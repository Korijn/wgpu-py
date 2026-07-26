"""
WebGPU for Python.
"""

# ruff: noqa: F401, F403

from ._coreutils import logger
from ._version import __version__, version_info
from ._diagnostics import DiagnosticsBase, diagnostics
from .flags import *
from .enums import *
from .structs import *
from .classes import *
from .utils.device import *  # get_default_device et al are top-level names
from . import utils
from . import resources

# There is only one backend now, statically linked, so this no longer selects
# anything -- but ``wgpu.backends.wgpu_native`` is how downstream code reaches
# wgpu-native's extras, and it has always resolved off a bare ``import wgpu``.
from . import backends

#: The API entrypoint, equivalent to the browser's ``navigator.gpu``.
gpu = GPU()  # noqa: F405
