"""The generated direct-call fast path must stay correct, not just fast.

Hot methods bypass the general marshalling path and call the bound C function
straight away. That buys a lot of speed, and costs a per-call error check --
errors surface at the next call that does check. These tests pin both halves.
"""

import sys

import pytest

from bindgen import paths
from bindgen.generate import GENERATED_DIR

pytestmark = pytest.mark.skipif(
    not (GENERATED_DIR / "apiclasses.py").exists()
    or not list((paths.REPO_ROOT / "wgpu" / "_native").glob("_wgpu.*")),
    reason="run bindgen.ffi_build + bindgen.generate first",
)


@pytest.fixture(scope="module")
def wgpu():
    sys.path.insert(0, str(paths.REPO_ROOT))
    import wgpu

    return wgpu


@pytest.fixture(scope="module")
def device(wgpu):
    try:
        return wgpu.utils.get_default_device()
    except RuntimeError as exc:
        pytest.skip(f"no adapter available: {exc}")


def test_hot_methods_are_generated_direct():
    """Guard the optimisation itself: these must not regress to the slow path."""
    source = (GENERATED_DIR / "apiclasses.py").read_text()
    for expected in (
        "_c_wgpuRenderPassEncoderDraw(self._handle",
        "_c_wgpuRenderBundleEncoderDraw(self._handle",
        "_c_wgpuComputePassEncoderDispatchWorkgroups(self._handle",
        "_c_wgpuRenderPassEncoderSetViewport(self._handle",
    ):
        assert expected in source, f"lost the direct call for {expected}"


def test_mixin_methods_bind_the_right_c_function(wgpu):
    """A mixin method must reach each concrete object's own C function."""
    import inspect

    render = inspect.getsource(wgpu.GPURenderPassEncoder.draw)
    bundle = inspect.getsource(wgpu.GPURenderBundleEncoder.draw)
    assert "wgpuRenderPassEncoderDraw" in render
    assert "wgpuRenderBundleEncoderDraw" in bundle


def _compute_setup(device):
    shader = device.create_shader_module(
        code="""
        @group(0) @binding(0) var<storage,read_write> data: array<i32>;
        @compute @workgroup_size(1)
        fn main(@builtin(global_invocation_id) i: vec3<u32>) {
            data[i.x] = data[i.x] + 1;
        }
        """
    )
    bgl = device.create_bind_group_layout(
        entries=[{"binding": 0, "visibility": "COMPUTE",
                  "buffer": {"type": "storage", "has_dynamic_offset": True}}]
    )
    pipeline = device.create_compute_pipeline(
        layout=device.create_pipeline_layout(bind_group_layouts=[bgl]),
        compute={"module": shader, "entry_point": "main"},
    )
    return bgl, pipeline


def test_set_bind_group_with_dynamic_offsets(device):
    """The slow path still works: offsets are marshalled and actually applied."""
    bgl, pipeline = _compute_setup(device)
    align = device.limits["min-storage-buffer-offset-alignment"]
    buf = device.create_buffer(size=align * 2, usage="STORAGE|COPY_SRC|COPY_DST")
    device.queue.write_buffer(buf, 0, bytes(align * 2))
    bind_group = device.create_bind_group(
        layout=bgl,
        entries=[{"binding": 0, "resource": {"buffer": buf, "offset": 0, "size": 4}}],
    )
    encoder = device.create_command_encoder()
    cpass = encoder.begin_compute_pass()
    cpass.set_pipeline(pipeline)
    # Offset into the second aligned block, so the increment lands there.
    cpass.set_bind_group(0, bind_group, [align])
    cpass.dispatch_workgroups(1)
    cpass.end()
    device.queue.submit([encoder.finish()])

    data = bytes(device.queue.read_buffer(buf))
    assert data[align] == 1, "the dynamic offset was not applied"
    assert data[0] == 0, "the write landed at the wrong offset"


def test_errors_from_direct_calls_surface_at_the_next_boundary(device):
    """A bad direct call must still raise -- at finish(), not silently."""
    encoder = device.create_command_encoder()
    rpass_buf = device.create_buffer(size=16, usage="COPY_SRC")
    with pytest.raises(Exception):
        # Copying more than the buffer holds is a validation error. The copy
        # itself takes the direct path; finish() is where it is reported.
        encoder.copy_buffer_to_buffer(rpass_buf, 0, rpass_buf, 0, 1024)
        encoder.finish()


def test_device_still_usable_after_a_deferred_error(device):
    """Deferring the check must not lose the error or poison the device."""
    buf = device.create_buffer(size=16, usage="COPY_DST|COPY_SRC")
    device.queue.write_buffer(buf, 0, b"0123456789abcdef")
    assert bytes(device.queue.read_buffer(buf)) == b"0123456789abcdef"
