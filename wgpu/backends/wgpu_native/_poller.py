"""A thread that drives wgpu-native's event queue while work is outstanding.

wgpu-native only runs completion callbacks while its queue is being processed,
so *something* has to process it. When a promise is awaited, the awaiting task
does that itself. Nothing else can:

* ``then()`` is fire-and-forget by definition -- the caller has gone back to
  its own work, and if nobody drives the queue the callback simply never runs;
* a timer on the event loop would do, but there is no portable way to start
  one. wgpu-py supports trio, where every task needs a nursery that only the
  caller's own code can open, so a detached background task cannot be spawned.

Which leaves a thread -- and a thread is the better shape anyway, because
``wgpuDevicePoll(wait=True)`` blocks in C until the GPU has something to
report. No timer, no polling interval, no wasted wakeups: the thread sleeps in
the driver and returns the instant there is work.

It polls only while at least one :class:`PollToken` is alive, so an idle device
costs nothing. Inspired by Servo's wgpu-polling thread:

* https://github.com/sagudev/servo/blob/main/components/webgpu/poll_thread.rs
* https://github.com/servo/servo/pull/32266
* https://bugzilla.mozilla.org/show_bug.cgi?id=1870699
"""

import atexit
import threading

is_shutting_down = False


@atexit.register
def mark_shutdown():
    global is_shutting_down
    is_shutting_down = True


class PollToken:
    """A claim on the poll thread, obtained via ``PollThread.get_token()``.

    The thread keeps polling for as long as the token is active -- that is,
    alive and not yet marked done. Dropping it is enough, so a token that is
    lost along with an abandoned promise stops the polling rather than pinning
    the thread awake forever.
    """

    def __init__(self, id, ids):
        self._id = id
        self._ids = ids

    def set_done(self):
        """Release this token's claim on the poll thread."""
        self._ids.discard(self._id)

    def is_done(self):
        return self._id not in self._ids

    def __del__(self):
        self.set_done()


class PollThread(threading.Thread):
    """Polls a device, but only while there is something to wait for."""

    def __init__(self, poll_func):
        super().__init__(name="wgpu-poller")
        self._poll_func = poll_func
        self._token_ids = set()  # add and discard are atomic under the GIL
        self._token_count = 0
        self._token_id_lock = threading.Lock()
        self._event = threading.Event()
        self._shutdown = False
        self.daemon = True  # never hold up interpreter shutdown

    def get_token(self):
        """Wake the poll thread and claim a :class:`PollToken`.

        The thread polls until the token's ``set_done()`` is called or it is
        garbage collected.
        """
        if self._shutdown:
            raise RuntimeError("Cannot use PollThread because it has stopped.")

        with self._token_id_lock:
            self._token_count += 1
            token_id = self._token_count

        # Add the id *before* waking the thread, so it cannot see an empty set
        # and go straight back to sleep.
        self._token_ids.add(token_id)
        token = PollToken(token_id, self._token_ids)
        self._event.set()

        return token

    def stop(self):
        """Stop polling, and wait briefly for the thread to notice.

        Callers must do this *before* the device handle is released: the thread
        holds that handle raw, and polling a released device would abort the
        process rather than raise.
        """
        self._shutdown = True
        self._poll_func = lambda _: None
        self._token_ids.clear()
        self._event.set()
        # Python 3.13 can hang joining a thread during shutdown, and 3.14 does
        # not allow it at all.
        if not is_shutting_down:
            self.join(timeout=1)

    def run(self):
        """Sleep until there is a token, then poll until there are none."""
        event = self._event
        token_ids = self._token_ids

        while not self._shutdown:
            event.wait()  # sleeps until a token is claimed
            event.clear()
            # One non-blocking pass first: the work may already be done, and a
            # blocking poll with nothing outstanding would wait for nothing.
            self._poll_func(False)
            while token_ids:
                self._poll_func(True)  # blocks in the driver
