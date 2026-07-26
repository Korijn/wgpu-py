"""wgpu-native's extras are hand-written, but they must not rot silently.

The extras wrap functions from ``wgpu.h`` that neither spec describes, so they
cannot be generated without inventing an API from a C header. What they *can*
have is the guarantee the generated layer has: every C function they call is
checked to exist, so a submodule bump that renames or drops one fails here
rather than at a user's call site.
"""

import re
import sys

import pytest

from bindgen import paths
from bindgen.generate import GENERATED_DIR

pytestmark = pytest.mark.skipif(
    not list((paths.REPO_ROOT / "wgpu" / "_native").glob("_wgpu.*")),
    reason="run bindgen.ffi_build first",
)

EXTRAS = paths.REPO_ROOT / "wgpu" / "backends" / "wgpu_native" / "extras.py"


@pytest.fixture(scope="module")
def lib():
    sys.path.insert(0, str(paths.REPO_ROOT))
    from wgpu._native import lib

    return lib


def _referenced_c_names():
    """Every C name the extras module reaches for, however it spells it.

    Both forms count: ``_lib.wgpuFoo`` written out, and ``"wgpuFoo"`` as a
    string that ``getattr`` resolves later -- one function is dispatched
    through a table, and a name that only the table knows would otherwise slip
    past this check entirely.
    """
    source = EXTRAS.read_text()
    names = set(re.findall(r"_lib\.((?:wgpu|WGPU)\w+)", source))
    names |= set(re.findall(r"[\"']((?:wgpu|WGPU)\w+)[\"']", source))
    return sorted(names)


def _referenced_ctypes():
    """Every C type name the extras module hands to ``_ffi.new``."""
    source = EXTRAS.read_text()
    return sorted(set(re.findall(r"_ffi\.new\(\s*[\"']([^\"']+)[\"']", source)))


def test_extras_reference_real_c_functions(lib):
    names = _referenced_c_names()
    assert names, "no C references found -- has extras.py moved?"
    missing = [n for n in names if not hasattr(lib, n)]
    assert not missing, (
        f"extras.py calls C names wgpu-native no longer provides: {missing}"
    )


def test_extras_allocate_real_c_types():
    """A ctype only exists as a string until something allocates one."""
    sys.path.insert(0, str(paths.REPO_ROOT))
    from wgpu._native import ffi

    unknown = []
    for ctype in _referenced_ctypes():
        try:
            ffi.typeof(ctype.replace("[]", "[1]"))
        except Exception:
            unknown.append(ctype)
    assert not unknown, (
        f"extras.py allocates C types wgpu-native no longer provides: {unknown}"
    )


def test_pipeline_statistic_names_match_the_header(lib):
    """``PipelineStatisticName`` is hand-written; wgpu.h is the truth.

    It is spelled out because the Web IDL knows nothing about pipeline
    statistics, but the values are wgpu-native's, so the two can drift. The
    names are compared, not just the count: a reordering would keep the count.
    """
    sys.path.insert(0, str(paths.REPO_ROOT))
    from wgpu.backends.wgpu_native.extras import (
        _PIPELINE_STATISTICS,
        PipelineStatisticName,
    )

    header = (paths.FFI_DIR / "wgpu.h").read_text()
    in_header = {
        n
        for n in re.findall(r"WGPUPipelineStatisticName_(\w+)", header)
        if n != "Force32"
    }
    declared = {
        name for name in vars(PipelineStatisticName) if not name.startswith("_")
    }
    assert declared == in_header
    for name in declared:
        value = getattr(lib, f"WGPUPipelineStatisticName_{name}")
        assert _PIPELINE_STATISTICS[getattr(PipelineStatisticName, name)] == int(value)


def test_native_struct_descriptors_match_the_header():
    """The hand-written descriptors for wgpu-native's own extension structs.

    These cannot be generated -- wgpu.h is a C header, not a machine-readable
    spec -- so they are written out, and this is what keeps them honest. A
    field that has moved or been renamed would otherwise write to the wrong
    offset, which corrupts memory rather than raising.
    """
    sys.path.insert(0, str(paths.REPO_ROOT))
    from wgpu._native import ffi
    from wgpu.backends.wgpu_native.native_structs import NATIVE_STRUCTS

    for name, descriptor in NATIVE_STRUCTS.items():
        fields = dict(ffi.typeof(descriptor.c_name).fields)
        for member in descriptor.members:
            assert member.c in fields, f"{name}.{member.c} is not in {fields}"
            if member.count_c:
                assert member.count_c in fields, f"{name}.{member.count_c}"
        if descriptor.category == "extension":
            assert "chain" in fields, f"{name} chains but has no chain field"
            assert descriptor.s_type, f"{name} chains but has no sType"


def test_extras_do_not_call_unimplemented_functions():
    """Calling one of these aborts the process, so they must never be wrapped."""
    unimplemented = paths.unimplemented_functions()
    called = set(_referenced_c_names()) & unimplemented
    assert not called, (
        f"extras.py wraps functions wgpu-native declares but does not "
        f"implement, which would abort the process: {sorted(called)}"
    )


def test_generated_layer_never_calls_unimplemented_functions():
    """The same guarantee for the generated classes, which bind C directly."""
    source = (GENERATED_DIR / "apiclasses.py").read_text()
    bound = set(re.findall(r"_c_(\w+) = _lib\.", source))
    called = bound & paths.unimplemented_functions()
    assert not called, f"generated code binds unimplemented functions: {sorted(called)}"
