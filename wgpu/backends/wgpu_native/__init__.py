"""The wgpu-native backend.

There is no backend selection any more: wgpu-native *is* the implementation,
compiled into this package. This module exists so the historical import path
keeps working, and so wgpu-native's non-standard extras have a home that says
plainly they are not part of WebGPU.
"""

from wgpu._api import *  # noqa: F401,F403
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

#: Kept for compatibility: wgpu-native is statically linked into the extension
#: now, so there is no separate library file to point at.
lib_path = None
