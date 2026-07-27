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

#: The API entrypoint, equivalent to the browser's ``navigator.gpu``.
gpu = GPU()  # noqa: F405

# There is only one backend now, statically linked, so this no longer selects
# anything -- but ``wgpu.backends.wgpu_native`` is how downstream code reaches
# wgpu-native's extras, and it has always resolved off a bare ``import wgpu``.
# It comes after ``gpu`` because the backend module re-exports that very
# object, and importing it first would find the name not yet bound.
from . import backends  # noqa: E402
