"""WebGPU structs, also available from the root ``wgpu`` namespace.

These dataclasses exist for typing, autocompletion and docs. Anywhere a struct
is accepted, a plain dict with the same fields works too.
"""

from wgpu._generated.apistructs import *  # noqa: F403
from wgpu._generated.apistructs import __all__  # noqa: F401
from wgpu._api.struct import Struct  # noqa: F401
