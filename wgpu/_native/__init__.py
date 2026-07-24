"""Low-level cffi binding to wgpu-native (compiled from the submodule).

The compiled ``_wgpu`` extension is a build artifact (git-ignored); it exposes
``lib`` (all wgpu* C functions) and ``ffi`` (the cffi FFI instance).
"""

from ._wgpu import ffi, lib  # noqa: F401  (built by `python -m bindgen.ffi_build`)
