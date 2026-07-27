"""Route wgpu-native's own log messages into Python's ``logging``.

wgpu-native (and naga underneath it) says a great deal more than it returns.
A shader that fails validation, for instance, reports a one-line error through
the error callback and the detail -- which expression, which type, which line
-- through its log. Without this bridge that detail is simply lost, and the
only thing left is a message saying validation failed.

The traffic goes both ways: Python's log level decides how much wgpu-native
bothers to emit, so raising ``logging.getLogger("wgpu")`` to DEBUG turns on
Rust-side debug logging rather than just letting more of it through.
"""

from __future__ import annotations

import sys

from wgpu._coreutils import logger, logger_set_level_callbacks

#: Kept at module scope for the process lifetime: wgpu-native holds the
#: function pointer, and a collected callback would be a crash, not an error.
_callback = None


def install() -> None:
    """Connect wgpu-native's log to ``wgpu.logger``. Safe to call twice."""
    global _callback
    if _callback is not None:
        return

    from wgpu._native import ffi, lib

    levels = {
        lib.WGPULogLevel_Error: logger.error,
        lib.WGPULogLevel_Warn: logger.warning,
        lib.WGPULogLevel_Info: logger.info,
        lib.WGPULogLevel_Debug: logger.debug,
        lib.WGPULogLevel_Trace: logger.debug,
    }

    @ffi.callback("void(WGPULogLevel, WGPUStringView, void *)")
    def _log(level, message, userdata):
        # Called from Rust, which reclaims the message when this returns -- so
        # it is decoded here, not held. Nothing may propagate out of it: an
        # exception crossing back into Rust would abort the process.
        try:
            text = ffi.string(message.data, message.length).decode(errors="ignore")
            levels.get(level, logger.warning)(text)
        except Exception:  # pragma: no cover - shutdown, or a broken handler
            if not sys.is_finalizing():
                pass

    _callback = _log
    lib.wgpuSetLogCallback(_log, ffi.NULL)
    logger_set_level_callbacks.append(_set_native_level)
    _set_native_level(logger.level)


def _set_native_level(level: int) -> None:
    """Mirror a Python log level onto wgpu-native.

    Filtering on the Rust side rather than the Python side matters: the
    messages this suppresses are never formatted, and at DEBUG there are a lot
    of them.
    """
    from wgpu._native import lib

    for threshold, native in (
        (40, lib.WGPULogLevel_Error),
        (30, lib.WGPULogLevel_Warn),
        (20, lib.WGPULogLevel_Info),
        (10, lib.WGPULogLevel_Debug),
        (5, lib.WGPULogLevel_Trace),
    ):
        if level >= threshold:
            lib.wgpuSetLogLevel(native)
            return
    lib.wgpuSetLogLevel(lib.WGPULogLevel_Off)
