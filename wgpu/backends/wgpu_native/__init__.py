"""The wgpu-native backend.

There is no backend selection any more: wgpu-native *is* the implementation,
compiled into this package. This module exists so the historical import path
keeps working, and so wgpu-native's non-standard extras have a home that says
plainly they are not part of WebGPU.
"""

from wgpu._api import *  # noqa: F401,F403
from wgpu._api import __all__  # noqa: F401
from . import extras  # noqa: F401
