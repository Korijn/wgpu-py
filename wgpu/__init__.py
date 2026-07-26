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
