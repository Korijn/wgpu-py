"""Build the low-level cffi extension for wgpu-native (out-of-line / API mode).

This compiles a real CPython extension that:

* ``#include``\\ s the actual wgpu-native headers (so the C compiler verifies
  every declaration for us), and
* statically links ``libwgpu_native.a`` plus its native system dependencies,

producing a single self-contained binary. Every ``wgpuXxx`` C function is then
reachable from Python as ``lib.wgpuXxx(...)`` with real C-call performance --
no per-call header parsing as in cffi's ABI mode.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import paths
from .headers import build_cdef

#: Dotted name of the compiled extension module.
MODULE_NAME = "wgpu._native._wgpu"


def make_ffi(profile: str = "release", module_name: str = MODULE_NAME):
    """Construct and return a configured, un-compiled ``cffi.FFI``."""
    from cffi import FFI

    ffi = FFI()
    ffi.cdef(build_cdef(paths.FFI_DIR))

    static_lib = paths.static_lib_path(profile)
    if not static_lib.exists():
        raise FileNotFoundError(
            f"{static_lib} not found. Build it first:\n"
            f"    cargo build --release --lib   # in the wgpu-native submodule"
        )

    extra_link_args: list[str] = []
    frameworks: list[str] = []
    if sys.platform.startswith("darwin"):
        # wgpu-native's Metal backend needs these Apple frameworks at link time.
        for fw in ("Metal", "QuartzCore", "CoreFoundation", "Foundation"):
            frameworks += ["-framework", fw]
        extra_link_args += frameworks

    ffi.set_source(
        module_name,
        '#include "wgpu.h"\n',
        include_dirs=list(paths.INCLUDE_DIRS),
        extra_objects=[str(static_lib)],
        libraries=paths.native_static_libs(),
        extra_link_args=extra_link_args,
    )
    return ffi


def compile_ffi(
    out_dir: str | Path,
    profile: str = "release",
    module_name: str = MODULE_NAME,
    verbose: bool = True,
) -> str:
    """Compile the extension into ``out_dir`` and return the resulting path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ffi = make_ffi(profile, module_name=module_name)
    return ffi.compile(tmpdir=str(out_dir), verbose=verbose)


if __name__ == "__main__":
    target = paths.REPO_ROOT / "wgpu" / "_native"
    print(compile_ffi(target))
