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
    """Every ``_lib.wgpuX`` / ``_lib.WGPUX`` the extras module mentions."""
    return sorted(set(re.findall(r"_lib\.((?:wgpu|WGPU)\w+)", EXTRAS.read_text())))


def test_extras_reference_real_c_functions(lib):
    names = _referenced_c_names()
    assert names, "no C references found -- has extras.py moved?"
    missing = [n for n in names if not hasattr(lib, n)]
    assert not missing, (
        f"extras.py calls C names wgpu-native no longer provides: {missing}"
    )


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
    assert not called, (
        f"generated code binds unimplemented functions: {sorted(called)}"
    )
