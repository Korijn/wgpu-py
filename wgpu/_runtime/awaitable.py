"""Poll-driven completion primitive, awaitable without an event-loop tie-in.

wgpu-native's async ops complete only while the app pumps ``processEvents``/
``WaitAny`` (callback mode ``AllowProcessEvents``), so completion is fully under
our control -- no background threads. That lets one object be consumed three
ways:

* ``fut.wait()``   -- synchronous, blocks the thread, **zero dependencies**.
* ``await fut``    -- yields to whatever event loop is running (asyncio/trio).
* ``fut.is_ready`` / ``fut.result`` -- manual polling.

Both driven paths currently spin on the pump (``wgpuInstanceProcessEvents``).
wgpu-native also offers ``wgpuInstanceWaitAny``, which blocks inside C until a
future resolves; adopting it for :meth:`WgpuFuture.wait` would remove the spin,
but needs the ``WGPUFuture`` handle that the async call returns to be captured
first. Left as a follow-up so the current behaviour stays simple and correct.

The only loop-specific bit is *how* to yield during ``await``; asyncio and trio
have incompatible checkpoints, so we detect the running loop once per await via
``sniffio`` (a neutral detector, imported lazily so sync users never pay for
it). An explicit override via :func:`configure_async_backend` skips detection
entirely.
"""

from __future__ import annotations

from typing import Any, Callable

_MISSING = object()

# Optional explicit async backend: a zero-arg coroutine function that yields
# control to the running loop (e.g. ``lambda: asyncio.sleep(0)``).
_async_checkpoint: Callable[[], Any] | None = None


def configure_async_backend(backend) -> None:
    """Pin the async backend to skip per-await detection.

    ``backend`` may be ``"asyncio"``, ``"trio"``, or a callable returning an
    awaitable that yields control to the current loop.
    """
    global _async_checkpoint
    if callable(backend):
        _async_checkpoint = backend
    elif backend == "asyncio":
        import asyncio

        _async_checkpoint = lambda: asyncio.sleep(0)
    elif backend == "trio":
        import trio

        _async_checkpoint = lambda: trio.sleep(0)
    else:
        raise ValueError(f"unknown async backend {backend!r}")


def _detect_checkpoint():
    """Resolve a ``() -> awaitable`` that yields to the running loop."""
    if _async_checkpoint is not None:
        return _async_checkpoint
    import sniffio

    lib = sniffio.current_async_library()
    if lib == "asyncio":
        import asyncio

        return lambda: asyncio.sleep(0)
    if lib == "trio":
        import trio

        return lambda: trio.sleep(0)
    raise RuntimeError(
        f"unsupported async library {lib!r}; use configure_async_backend"
    )


class WgpuFuture:
    """Result of an async wgpu operation; pump-driven, loop-agnostic."""

    def __init__(self, pump: Callable[[], None]):
        # ``pump`` drives wgpu-native once (e.g. instance.process_events()).
        self._pump = pump
        self._result: Any = _MISSING
        self._error: BaseException | None = None

    # -- filled by the C callback -----------------------------------------

    def set_result(self, value) -> None:
        if self._result is _MISSING and self._error is None:
            self._result = value

    def set_error(self, exc: BaseException) -> None:
        if self._result is _MISSING and self._error is None:
            self._error = exc

    @property
    def is_ready(self) -> bool:
        return self._result is not _MISSING or self._error is not None

    def result(self):
        if not self.is_ready:
            raise RuntimeError("future is not ready")
        if self._error is not None:
            raise self._error
        return self._result

    # -- sync consumption --------------------------------------------------

    def wait(self):
        """Block the current thread until the operation completes.

        Spins on the pump; see the module docstring on ``wgpuInstanceWaitAny``.
        """
        while not self.is_ready:
            self._pump()
        return self.result()

    # -- async consumption -------------------------------------------------

    def __await__(self):
        if self.is_ready:
            return self.result()
        checkpoint = _detect_checkpoint()  # detected once per await
        while not self.is_ready:
            self._pump()
            yield from checkpoint().__await__()
        return self.result()
