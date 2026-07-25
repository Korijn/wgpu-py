"""Low-level cffi binding to wgpu-native (compiled from the submodule).

The compiled ``_wgpu`` extension is a build artifact (git-ignored); it exposes
``lib`` (all wgpu* C functions) and ``ffi`` (the cffi FFI instance).
"""

try:
    from ._wgpu import ffi, lib  # noqa: F401
except ImportError as err:  # pragma: no cover - depends on build state
    raise ImportError(
        "The compiled wgpu-native extension is missing. Build it with:\n"
        "    git submodule update --init --recursive\n"
        "    cd wgpu-native && cargo build --release --lib && cd ..\n"
        "    python -m bindgen.ffi_build\n"
        f"(original error: {err})"
    ) from err
