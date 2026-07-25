"""WebGPU enums, also available from the root ``wgpu`` namespace.

Enums are choices: exactly one value applies. The values are strings, so
``wgpu.TextureFormat.rgba8unorm`` and ``"rgba8unorm"`` are interchangeable.
"""

from wgpu._generated.apienums import *  # noqa: F401,F403
from wgpu._generated.apienums import Enum, __all__  # noqa: F401
