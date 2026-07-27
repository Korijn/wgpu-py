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

from pathlib import Path

from . import paths
from .headers import build_cdef

#: Name of the compiled extension module. It is built directly into
#: :data:`NATIVE_DIR` (``wgpu/_native/``), where ``wgpu/_native/__init__.py``
#: re-exports its ``ffi`` and ``lib``.
MODULE_NAME = "_wgpu"


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

    # Both come from rustc, so new targets need no changes here (on macOS the
    # link args carry the "-framework Metal" style pairs).
    libraries, extra_link_args = paths.native_link_spec(profile)

    ffi.set_source(
        module_name,
        '#include "wgpu.h"\n',
        include_dirs=list(paths.INCLUDE_DIRS),
        extra_objects=[str(static_lib)],
        libraries=libraries,
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


def load_compiled(build_dir: str | Path, stem: str = "_wgpu"):
    """Import a compiled extension directly by file path.

    Loading by path (rather than by dotted name) lets the generator use the
    freshly-built low-level module without importing the heavyweight ``wgpu``
    package. Returns the imported module (with ``.lib`` and ``.ffi``).
    """
    import importlib.util

    build_dir = Path(build_dir)
    matches = sorted(build_dir.glob(f"{stem}.*.so")) + sorted(
        build_dir.glob(f"{stem}.pyd")
    )
    if not matches:
        raise FileNotFoundError(f"no compiled {stem} extension in {build_dir}")
    spec = importlib.util.spec_from_file_location(stem, matches[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NATIVE_DIR = paths.REPO_ROOT / "wgpu" / "_native"


if __name__ == "__main__":
    print(compile_ffi(NATIVE_DIR, module_name="_wgpu"))
