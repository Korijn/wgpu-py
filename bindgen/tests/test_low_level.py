"""Prove the automated low-level binding end-to-end.

Requires the wgpu-native static library to be built first:

    cargo build --release --lib   # inside the wgpu-native/ submodule

The test then generates the cdef from the submodule headers, compiles the cffi
API-mode extension statically linked against the archive, and exercises a real
functional call. It is skipped (not failed) when the archive is absent, so it
stays friendly in environments without a Rust build.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bindgen import build_cdef
from bindgen import ffi_build, paths


def test_cdef_covers_full_surface():
    """The cleaned cdef must parse and mention every documented C function."""
    from cffi import FFI

    cdef = build_cdef(paths.FFI_DIR)
    FFI().cdef(cdef)  # raises if unparseable

    n_funcs = len(re.findall(r"\bwgpu[A-Z]\w*\s*\(", cdef))
    assert n_funcs >= 220, f"expected the full C surface, only found {n_funcs} funcs"


@pytest.mark.skipif(
    not paths.static_lib_path().exists(),
    reason="libwgpu_native static archive not built (run cargo build --release --lib)",
)
def test_compiled_extension_is_functional(tmp_path):
    """Compile the extension and make a real, GPU-free call into wgpu-native."""
    # Build under a flat module name so the test import doesn't collide with the
    # repo's real ``wgpu`` package on sys.path.
    ffi_build.compile_ffi(tmp_path, module_name="_wgpu_selftest", verbose=False)

    sys.path.insert(0, str(tmp_path))
    try:
        import _wgpu_selftest as m  # type: ignore
    finally:
        sys.path.pop(0)

    funcs = [n for n in dir(m.lib) if n.startswith("wgpu")]
    assert len(funcs) >= 220

    # Creating and releasing an Instance needs no adapter/GPU and proves the
    # statically-linked Rust code actually runs.
    instance = m.lib.wgpuCreateInstance(m.ffi.NULL)
    assert instance
    m.lib.wgpuInstanceRelease(instance)
