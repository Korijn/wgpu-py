"""The promise wgpu-native operations resolve into.

wgpu-native reports completion by invoking a callback while the instance's
event queue is being pumped, so a pending promise is only resolved from inside
``wgpuInstanceProcessEvents``. That is what ``_sync_wait`` drives here; every
other way of consuming a promise -- ``await``, ``then()`` -- comes from the
loop-agnostic base in ``wgpu._api.promise``.
"""

from __future__ import annotations

from wgpu._api.promise import (
    GPUPromise,
    async_sleep,
    get_backoff_time_generator,
)


class WgpuPromise(GPUPromise):
    """A promise resolved by pumping wgpu-native's event queue."""

    def __init__(self, title, pump, handler=None, **kwargs):
        super().__init__(title, handler, **kwargs)
        self._pump = pump

    def _sync_wait(self):
        # Pump until the callback fires. The backoff keeps a long wait from
        # spinning a core, while staying responsive for the common fast case.
        backoff = get_backoff_time_generator()
        import time

        while self._state == "pending":
            self._pump()
            if self._state != "pending":
                break
            time.sleep(next(backoff))

    def __await__(self):
        """Await the result, pumping wgpu-native's queue while we wait.

        The base implementation naps until something else resolves the promise.
        Nothing else would: wgpu-native only runs completion callbacks while
        its event queue is being processed, so the awaiting task has to drive
        that itself. It yields to the event loop between polls, so other tasks
        still run -- this waits, it does not block.
        """

        async def awaiter():
            if self._state == "pending":
                sleep_gen = get_backoff_time_generator()
                while self._state == "pending":
                    self._pump()
                    if self._state != "pending":
                        break
                    await async_sleep(next(sleep_gen))
            return self._resolve()

        return awaiter().__await__()


#: Kept as the historical name used inside the runtime.
WgpuFuture = WgpuPromise


def completed(value):
    """A promise that is already resolved with ``value``."""
    promise = GPUPromise("completed", None)
    promise._set_input(value)
    return promise
