"""WebGPU flags, also available from the root ``wgpu`` namespace.

Flags are integer bitmasks: zero or more values can be set at once. They can
also be given as strings, so ``BufferUsage.MAP_READ | BufferUsage.COPY_DST``
can be written ``"MAP_READ|COPY_DST"``.
"""

from wgpu._generated.apiflags import *  # noqa: F403
from wgpu._generated.apiflags import Flags, __all__  # noqa: F401
