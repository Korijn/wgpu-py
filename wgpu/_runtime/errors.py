"""Surface wgpu-native errors as Python exceptions.

Without a registered uncaptured-error callback, wgpu-native reacts to a
validation error by panicking, and a Rust panic cannot unwind through the FFI
boundary -- it **aborts the process**. So registering the callback is not a
nicety: it is what turns a hard crash into a catchable Python exception.

The callback itself cannot raise (it is called from Rust), so errors are
recorded here and re-raised by the runtime at the next API-call boundary. This
mirrors what the classic implementation does.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("wgpu")


class GPUError(Exception):
    """Base class for errors reported by wgpu-native."""

    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message


class GPUValidationError(GPUError):
    """The API was used incorrectly (bad usage flags, wrong sizes, ...)."""


class GPUOutOfMemoryError(GPUError, MemoryError):
    """The device ran out of memory."""


class GPUInternalError(GPUError):
    """An internal error inside wgpu-native."""


class GPUPipelineError(Exception):
    """A pipeline could not be created."""

    def __init__(self, message: str = "", reason: str = ""):
        super().__init__(message)
        self.message = message
        self.reason = reason


class DrawCancelled(Exception):
    """Raised to abandon a draw without it counting as a failure."""


#: ``WGPUErrorType`` value -> exception class.
_ERROR_TYPES = {
    2: GPUValidationError,
    3: GPUOutOfMemoryError,
    4: GPUInternalError,
}


def _clean(message: str) -> str:
    """Tidy a wgpu-native message without changing what it says.

    Only trailing whitespace goes -- wgpu-native pads its blank separator lines
    -- because the leading newline and the indentation of the source excerpt
    are part of how the message reads.
    """
    return "\n".join(line.rstrip() for line in message.rstrip().splitlines())


class ErrorSink:
    """Collects errors reported from C callbacks for later re-raising."""

    def __init__(self):
        self._pending: list[BaseException] = []

    def record(self, error_type: int, message: str) -> None:
        """Called from the C callback; must never raise."""
        try:
            cls = _ERROR_TYPES.get(int(error_type), GPUError)
            self._pending.append(cls(_clean(message) or cls.__name__))
        except BaseException:  # pragma: no cover - defensive, cannot propagate
            logger.exception("failed to record a wgpu error")

    def record_device_lost(self, reason: int, message: str) -> None:
        # Device loss is not necessarily an error (it happens on shutdown), so
        # it is logged rather than raised.
        logger.warning("wgpu device lost (reason %s): %s", reason, message.strip())

    def raise_if_error(self) -> None:
        """Raise the first pending error, discarding any that piled up behind it."""
        if not self._pending:
            return
        errors = self._pending
        self._pending = []
        first = errors[0]
        for extra in errors[1:]:
            logger.error("additional wgpu error: %s", extra)
        raise first

    def take(self) -> list[BaseException]:
        errors, self._pending = self._pending, []
        return errors

    def __bool__(self) -> bool:
        return bool(self._pending)
