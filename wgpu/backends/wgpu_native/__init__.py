"""The wgpu-native backend.

There is no backend selection any more: wgpu-native *is* the implementation,
compiled into this package. This module exists so the historical import path
keeps working, and so wgpu-native's non-standard extras have a home that says
plainly they are not part of WebGPU.
"""

from wgpu._api import *  # noqa: F403
from wgpu._api import __all__  # noqa: F401

from . import extras  # noqa: F401
from .extras import request_device_sync  # noqa: F401


def _wgpu_native_version() -> str:
    """The version of the wgpu-native library compiled into this package."""
    from wgpu._native import lib

    from wgpu._generated.constants import WGPU_NATIVE_VERSION

    packed = lib.wgpuGetVersion()
    if packed:
        return ".".join(str((packed >> shift) & 0xFF) for shift in (24, 16, 8, 0))
    # wgpu-native only fills that in when the build sets it; fall back to the
    # release recorded at generation time.
    return WGPU_NATIVE_VERSION


#: The version of wgpu-native this package was built against.
__version__ = _wgpu_native_version()

#: The same version as a tuple of four ints, e.g. ``(29, 0, 1, 1)``.
version_info = tuple(
    int(part) if part.isdigit() else 0 for part in (__version__.split(".") + ["0"] * 4)
)[:4]

#: The version of the library actually loaded. There is only one now -- it is
#: compiled in -- so it cannot disagree with the expected version, which is
#: exactly the failure mode the two-value comparison used to catch.
lib_version_info = version_info


def _commit_sha() -> str:
    from wgpu._generated.constants import WGPU_NATIVE_COMMIT_SHA

    return WGPU_NATIVE_COMMIT_SHA


#: The wgpu-native commit compiled into this package, recorded when the
#: bindings were generated: a static link leaves nothing to inspect afterwards.
__commit_sha__ = _commit_sha()


def _lib_path() -> str:
    """Where the wgpu-native code actually lives: inside our own extension."""
    from wgpu._native import _wgpu

    return getattr(_wgpu, "__file__", "") or ""


#: wgpu-native is statically linked into the compiled extension rather than
#: loaded from a separate shared library, so this points at the extension.
lib_path = _lib_path()
