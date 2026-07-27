"""Exercise the runtime invoker + generated classes without needing a GPU.

Covers argument marshalling, return wrapping, the create_instance entry point,
and the real release lifecycle (wgpuInstanceRelease) -- all GPU-free.
"""

import sys

import pytest

from bindgen import paths
from bindgen.generate import GENERATED_DIR

pytestmark = pytest.mark.skipif(
    not (GENERATED_DIR / "classes.py").exists()
    or not list((paths.REPO_ROOT / "wgpu" / "_native").glob("_wgpu.*")),
    reason="run bindgen.ffi_build + bindgen.generate first",
)


@pytest.fixture(scope="module")
def api():
    sys.path.insert(0, str(paths.REPO_ROOT))
    from wgpu._runtime.api import get_api

    return get_api()


def test_marshal_struct_arg_builds_pointer(api):
    method = api._methods[("device", "create_buffer")]
    c_args, _keep = api.invoker.marshal_args(method, [{"label": "vb", "size": 128}])
    assert len(c_args) == 1
    desc = c_args[0]
    assert desc.size == 128
    assert api.ffi.string(desc.label.data, desc.label.length) == b"vb"


def test_marshal_scalar_args(api):
    method = api._methods[("render_pass_encoder", "draw")]
    c_args, _keep = api.invoker.marshal_args(method, [3, 1, 0, 0])
    assert c_args == [3, 1, 0, 0]


def test_return_wrapping_enum(api):
    method = api._methods[("buffer", "get_map_state")]
    from wgpu._generated import enums

    # Enum returns come back as the public *string*, not the C integer.
    wrapped = api.invoker.wrap_return(method, int(enums.BufferMapState.unmapped))
    assert wrapped == "unmapped"


def test_create_instance_and_release(api):
    inst = api.create_instance()
    assert type(inst).__name__ == "GPUInstance"
    assert inst._handle
    # Releasing triggers wgpuInstanceRelease (a real C call); idempotent, so a
    # subsequent GC-driven __del__ is a safe no-op.
    inst._release()
    assert inst._handle is None
    inst._release()  # no double-free


def test_signatures_are_introspectable(api):
    import inspect

    sig = inspect.signature(api.registry["device"].create_buffer)
    # Descriptor-taking methods are flattened into keyword arguments, so the
    # signature shows the descriptor's fields rather than "descriptor".
    assert list(sig.parameters) == [
        "self",
        "label",
        "size",
        "usage",
        "mapped_at_creation",
    ]
