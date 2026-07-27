import os
import base64
import shutil
import ctypes
import sys
import tempfile

import wgpu.utils
import wgpu.backends.wgpu_native
import numpy as np

from testutils import run_tests, can_use_wgpu_lib, is_ci
from pytest import mark, raises

is_win = sys.platform.startswith("win")


def test_get_wgpu_version():
    version = wgpu.backends.wgpu_native.__version__
    commit_sha = wgpu.backends.wgpu_native.__commit_sha__
    version_info = wgpu.backends.wgpu_native.version_info

    assert isinstance(version, str)
    assert len(version) > 1

    assert isinstance(version_info, tuple)
    assert all(isinstance(i, int) for i in version_info)
    assert len(version_info) == 4

    assert isinstance(commit_sha, str)
    assert len(commit_sha) > 0


def test_the_library_is_compiled_in():
    # This used to set WGPU_LIB_PATH and check that the loader picked it up:
    # the wheel shipped a prebuilt libwgpu_native.so alongside pure Python, and
    # you could point it elsewhere. wgpu-native is now statically linked into a
    # CPython extension, so there is no separate library to swap -- which is the
    # point, since a wheel and its native library can no longer disagree about a
    # version. What is checked is that the env var is genuinely inert.
    old = os.environ.get("WGPU_LIB_PATH")
    os.environ["WGPU_LIB_PATH"] = "foo/bar"
    try:
        path = wgpu.backends.wgpu_native.lib_path
        assert "foo/bar" not in path
        assert os.path.isfile(path)
        # It is the compiled extension itself, not a library beside it.
        assert os.path.dirname(path) == os.path.dirname(wgpu._native.__file__)
        assert os.path.basename(path).startswith("_wgpu.")
    finally:
        if old is None:
            os.environ.pop("WGPU_LIB_PATH")
        else:
            os.environ["WGPU_LIB_PATH"] = old


def _struct(name, mapping):
    """Build a C struct the way every descriptor field is built, and read it back."""
    from wgpu._runtime.api import get_api

    ptr, _keep = get_api().invoker.structs.new(name, mapping)
    return ptr


# The four tests below used to call _tuple_from_tuple_or_dict and friends --
# hand-written helpers that turned (10, 20) or {"width": 10} into a tuple. The
# rules they encoded are now the generated struct descriptors plus the runtime
# builder, which applies them to *every* struct rather than to the three that
# had a helper. So the same rules are checked, through the thing that enforces
# them.


@mark.skipif(not can_use_wgpu_lib, reason="Needs wgpu lib")
def test_struct_from_sequence_or_mapping():
    # A sequence fills the members in declaration order ...
    assert _struct("origin_3D", (1, 2, 3)).z == 3
    # ... and a mapping by name, in any order, in any of the three spellings.
    assert _struct("origin_3D", {"z": 3, "x": 1, "y": 2}).x == 1
    assert (
        _struct("extent_3D", {"depthOrArrayLayers": 4, "width": 1}).depthOrArrayLayers
        == 4
    )
    assert (
        _struct(
            "extent_3D", {"depth-or-array-layers": 4, "width": 1}
        ).depthOrArrayLayers
        == 4
    )

    with raises(TypeError):
        _struct("origin_3D", 42)  # neither a sequence nor a mapping
    with raises(ValueError):
        # More values than the struct has members
        _struct("origin_3D", (1, 2, 3, 4))
    with raises(ValueError):
        # Right number of values, wrong keyword
        _struct("origin_3D", {"x": 1, "y": 2, "w": 3})


@mark.skipif(not can_use_wgpu_lib, reason="Needs wgpu lib")
def test_struct_defaults_and_required_members():
    # An omitted member takes the default the Web IDL gives it ...
    e = _struct("extent_3D", {"width": 10})
    assert (e.width, e.height, e.depthOrArrayLayers) == (10, 1, 1)
    e = _struct("extent_3D", [10, 20])
    assert (e.width, e.height, e.depthOrArrayLayers) == (10, 20, 1)
    o = _struct("origin_3D", {"z": 30})
    assert (o.x, o.y, o.z) == (0, 0, 30)

    # ... but a member the IDL marks *required* has no default to fall back on,
    # and a zero would be a real value rather than an absence -- a zero-sized
    # texture, a black clear colour -- so it has to be an error.
    with raises(ValueError):
        _struct("extent_3D", {"height": 20, "depth_or_array_layers": 30})
    with raises(ValueError):
        _struct("extent_3D", ())
    with raises(ValueError):
        _struct("color", (0.1, 0.2, 0.3))


@mark.skipif(not can_use_wgpu_lib, reason="Needs wgpu lib")
def test_struct_from_color():
    c = _struct("color", (0.1, 0.2, 0.3, 0.4))
    assert (c.r, c.g, c.b, c.a) == (0.1, 0.2, 0.3, 0.4)
    c = _struct("color", {"r": 0.1, "g": 0.2, "b": 0.3, "a": 0.4})
    assert (c.r, c.g, c.b, c.a) == (0.1, 0.2, 0.3, 0.4)

    with raises(ValueError):
        _struct("color", (0.1, 0.2, 0.3, 0.4, 0.5))
    with raises(ValueError):
        _struct("color", {"r": 0.1, "g": 0.2, "b": 0.3, "w": 0.4})


compute_shader_wgsl = """
@group(0)
@binding(0)
var<storage,read_write> out1: array<i32>;

@compute
@workgroup_size(1)
fn main(@builtin(global_invocation_id) index: vec3<u32>) {
    let i: u32 = index.x;
    out1[i] = 1000 + i32(i);
}
"""

compute_shader_glsl = """
#version 450 core

precision highp float;
precision highp int;

layout(local_size_x = 1, local_size_y = 1, local_size_z = 1) in;

layout(std430, binding=0) buffer BufferBinding { int out1[]; };

void main() {
    uvec3 index = gl_GlobalInvocationID;
    uint i = index.x;
    out1[i] = (1000 + int(i));
    return;
}
"""

compute_shader_spirv = base64.decodebytes(
    """
AwIjBwAAAQAcAAAAHAAAAAAAAAARAAIAAQAAAAoACwBTUFZfS0hSX3N0b3JhZ2VfYnVmZmVyX3N0
b3JhZ2VfY2xhc3MAAAAACwAGAAEAAABHTFNMLnN0ZC40NTAAAAAADgADAAAAAAABAAAADwAGAAUA
AAAQAAAAbWFpbgAAAAANAAAAEAAGABAAAAARAAAAAQAAAAEAAAABAAAABQAEAAkAAABvdXQxAAAA
AAUABAANAAAAaW5kZXgAAAAFAAQAEAAAAG1haW4AAAAARwAEAAQAAAAGAAAABAAAAEcABAAJAAAA
IgAAAAAAAABHAAQACQAAACEAAAAAAAAARwADAAoAAAACAAAASAAFAAoAAAAAAAAAIwAAAAAAAABH
AAQADQAAAAsAAAAcAAAAEwACAAIAAAAVAAQAAwAAACAAAAABAAAAHQADAAQAAAADAAAAFQAEAAYA
AAAgAAAAAAAAABcABAAFAAAABgAAAAMAAAArAAQAAwAAAAcAAAAAAAAAKwAEAAMAAAAIAAAAAQAA
AB4AAwAKAAAABAAAACAABAALAAAADAAAAAoAAAA7AAQACwAAAAkAAAAMAAAAIAAEAA4AAAABAAAA
BQAAADsABAAOAAAADQAAAAEAAAAhAAMAEQAAAAIAAAAgAAQAEgAAAAwAAAAEAAAAKwAEAAYAAAAT
AAAAAAAAACsABAADAAAAFQAAAOgDAAAgAAQAGAAAAAwAAAADAAAANgAFAAIAAAAQAAAAAAAAABEA
AAD4AAIADAAAAD0ABAAFAAAADwAAAA0AAABBAAUAEgAAABQAAAAJAAAAEwAAAPkAAgAWAAAA+AAC
ABYAAABRAAUABgAAABcAAAAPAAAAAAAAAHwABAADAAAAGQAAABcAAACAAAUAAwAAABoAAAAVAAAA
GQAAAEEABQAYAAAAGwAAABQAAAAXAAAAPgADABsAAAAaAAAA/QABADgAAQA=
""".encode()
)


def run_compute_shader(device, shader):
    """Minimal compute setup for the above shaders."""
    n = 16
    buffer = device.create_buffer(
        size=n * 4, usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_SRC
    )

    # Setup layout and bindings
    binding_layouts = [
        {
            "binding": 0,
            "visibility": wgpu.ShaderStage.COMPUTE,
            "buffer": {"type": wgpu.BufferBindingType.storage},
        },
    ]
    bindings = [
        {
            "binding": 0,
            "resource": {"buffer": buffer, "offset": 0, "size": buffer.size},
        },
    ]

    # Put everything together
    bind_group_layout = device.create_bind_group_layout(entries=binding_layouts)
    pipeline_layout = device.create_pipeline_layout(
        bind_group_layouts=[bind_group_layout]
    )
    bind_group = device.create_bind_group(layout=bind_group_layout, entries=bindings)

    # Create and run the pipeline
    compute_pipeline = device.create_compute_pipeline(
        layout=pipeline_layout,
        compute={"module": shader, "entry_point": "main"},
    )
    command_encoder = device.create_command_encoder()
    compute_pass = command_encoder.begin_compute_pass()
    compute_pass.set_pipeline(compute_pipeline)
    compute_pass.set_bind_group(0, bind_group)
    compute_pass.dispatch_workgroups(n, 1, 1)  # x y z
    compute_pass.end()
    device.queue.submit([command_encoder.finish()])

    # Read result
    out = device.queue.read_buffer(buffer).cast("i")
    result = out.tolist()
    assert result == [1000 + i for i in range(n)]


@mark.skipif(not can_use_wgpu_lib, reason="Needs wgpu lib")
def test_compute_shader_wgsl():
    device = wgpu.utils.get_default_device()

    code = compute_shader_wgsl
    assert isinstance(code, str)

    shader = device.create_shader_module(code=code)
    assert shader.get_compilation_info_sync() == []

    run_compute_shader(device, shader)


@mark.skipif(not can_use_wgpu_lib, reason="Needs wgpu lib")
def test_compute_shader_glsl():
    device = wgpu.utils.get_default_device()

    code = compute_shader_glsl
    assert isinstance(code, str)

    shader = device.create_shader_module(label="simple comp", code=code)
    assert shader.get_compilation_info_sync() == []

    run_compute_shader(device, shader)


@mark.skipif(not can_use_wgpu_lib, reason="Needs wgpu lib")
@mark.skipif(is_ci and is_win, reason="Cannot use SpirV shader on dx12")
def test_compute_shader_spirv():
    device = wgpu.utils.get_default_device()

    code = compute_shader_spirv
    assert isinstance(code, bytes)

    shader = device.create_shader_module(code=code)
    assert shader.get_compilation_info_sync() == []

    run_compute_shader(device, shader)


@mark.skipif(not can_use_wgpu_lib, reason="Needs wgpu lib")
def test_compute_shader_invalid():
    device = wgpu.utils.get_default_device()

    code4 = type("CodeObject", (object,), {})

    with raises(TypeError):
        device.create_shader_module(code=code4)
    with raises(TypeError):
        device.create_shader_module(code={"not", "a", "shader"})
    with raises(ValueError):
        device.create_shader_module(code=b"bytes but no SpirV magic number")


@mark.skipif(not can_use_wgpu_lib, reason="Needs wgpu lib")
def test_logging():
    # Do *something* while we set the log level low
    device = wgpu.utils.get_default_device()

    wgpu.logger.setLevel("DEBUG")

    device.create_shader_module(code=compute_shader_wgsl)

    wgpu.logger.setLevel("WARNING")

    # yeah, would be nice to be able to capture the logs. But if we don't crash
    # and see from the coverage that we touched the logger integration code,
    # we're doing pretty good ...
    # (capsys does not work because it logs to the raw stderr)


@mark.skipif(not can_use_wgpu_lib, reason="Needs wgpu lib")
def test_wgpu_native_tracer():
    # API tracing used to write a replayable capture into a directory. wgpu
    # removed the feature (gfx-rs/wgpu#5974) and wgpu-native's ``trace`` cargo
    # feature is commented out until it returns, so there is nothing to enable
    # at any layer. Asking for a trace must therefore fail loudly rather than
    # silently write nothing -- and when upstream restores it, this is the
    # reminder to wire it back up.
    tempdir = os.path.join(tempfile.gettempdir(), "wgpu-tracer-test")
    shutil.rmtree(tempdir, ignore_errors=True)
    adapter = wgpu.utils.get_default_device().adapter

    with raises(NotImplementedError) as info:
        wgpu.backends.wgpu_native.request_device_sync(adapter, tempdir)
    assert "wgpu-native" in str(info.value)
    assert not os.path.isdir(tempdir)

    # Without a trace path it is just a device request, and still works.
    assert wgpu.backends.wgpu_native.request_device_sync(adapter) is not None


@mark.skipif(not can_use_wgpu_lib, reason="Needs wgpu lib")
def test_enumerate_adapters():
    # Get all available adapters
    adapters = wgpu.gpu.enumerate_adapters_sync()
    assert len(adapters) > 0

    # Check adapter summaries
    for adapter in adapters:
        assert isinstance(adapter.summary, str)
        assert "\n" not in adapter.summary
        assert len(adapter.summary.strip()) > 10

    # Check that we can get a device from each adapter
    for adapter in adapters:
        d = adapter.request_device_sync()
        assert isinstance(d, wgpu.backends.wgpu_native.GPUDevice)


@mark.skipif(not can_use_wgpu_lib, reason="Needs wgpu lib")
def test_adapter_destroy():
    adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
    # The handle used to be called _internal; releasing it clears it either way.
    assert adapter._handle is not None
    adapter.__del__()
    assert adapter._handle is None


@mark.skipif(not can_use_wgpu_lib, reason="Needs wgpu lib")
def test_adapter_by_name():
    ori = os.environ.get("WGPUPY_WGPU_ADAPTER_NAME", None)
    try:
        os.environ["WGPUPY_WGPU_ADAPTER_NAME"] = " "

        promise = wgpu.gpu.request_adapter_async()
        adapter = promise.sync_wait()
        assert adapter
        assert promise._title == "adapter by name"

    finally:
        if ori is None:
            os.environ.pop("WGPUPY_WGPU_ADAPTER_NAME")
        else:
            os.environ["WGPUPY_WGPU_ADAPTER_NAME"] = ori


@mark.skipif(not can_use_wgpu_lib, reason="Needs wgpu lib")
def test_any_buffer_protocol_object_can_be_uploaded():
    # This used to test get_memoryview_and_address(), a helper that turned
    # buffer-like data into a (memoryview, address) pair to hand to C -- taking
    # some care, because a readonly buffer cannot go through ctypes without a
    # copy. cffi's from_buffer does that job now, for readonly buffers too, so
    # the helper is gone. The guarantee it existed for is not: anything
    # supporting the buffer protocol can be uploaded, without a copy.
    device = wgpu.utils.get_default_device()

    readonly_array = np.array([1, 2, 3, 4], dtype=np.int32)
    readonly_array.flags.writeable = False

    for data in [
        b"bytes are readonly, but we can still upload them",
        bytearray(b"a bytearray works too"),
        (ctypes.c_float * 100)(),
        np.array([1, 2, 3, 4], dtype=np.int32),
        readonly_array,
        memoryview(b"and a memoryview of one"),
    ]:
        nbytes = memoryview(data).nbytes
        # Round up: write_buffer needs a multiple of 4.
        size = (nbytes + 3) // 4 * 4
        buffer = device.create_buffer(
            size=size, usage=wgpu.BufferUsage.COPY_DST | wgpu.BufferUsage.COPY_SRC
        )
        device.queue.write_buffer(buffer, 0, data, 0, nbytes - nbytes % 4)
        device.queue.submit([])


def are_features_wgpu_legal(features):
    """Returns true if the list of features is legal. Determining whether a specific
    set of features is implemented on a particular device would make the tests fragile,
    so we only verify that the names are legal feature names."""
    adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
    try:
        adapter.request_device_sync(required_features=features)
        return True
    except RuntimeError as e:
        assert "Unsupported features were requested" in str(e)
        return True
    except KeyError:
        return False


def test_features_are_legal():
    # A standard feature.  Probably exists
    assert are_features_wgpu_legal(["shader-f16"])
    # A common extension feature
    assert are_features_wgpu_legal(["vertex-writable-storage"])
    # An uncommon extension feature.  Certainly not on a mac.
    assert are_features_wgpu_legal(["pipeline-statistics-query"])
    assert are_features_wgpu_legal(
        ["immediates", "vertex-writable-storage", "depth-clip-control"]
    )
    # We can also use underscore
    assert are_features_wgpu_legal(["immediates", "vertex_writable_storage"])
    # We can also use camel case
    assert are_features_wgpu_legal(["Immediates", "VertexWritableStorage"])


def test_features_are_illegal():
    # writable is misspelled
    assert not are_features_wgpu_legal(["vertex-writeable-storage"])
    assert not are_features_wgpu_legal(["my-made-up-feature"])


def are_limits_wgpu_legal(limits):
    """Returns true if the list of features is legal. Determining whether a specific
    set of features is implemented on a particular device would make the tests fragile,
    so we only verify that the names are legal feature names."""
    adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
    try:
        adapter.request_device_sync(required_limits=limits)
        return True
    except RuntimeError as e:
        assert "Unsupported features were requested" in str(e)
        return True
    except KeyError:
        return False


def test_limits_are_legal():
    # A standard feature.  Probably exists
    assert are_limits_wgpu_legal({"max-bind-groups": 8})
    # Two common extension features
    assert are_limits_wgpu_legal({"max-immediate-size": 128})
    # We can also use underscore
    assert are_limits_wgpu_legal({"max_bind_groups": 8, "max_immediate_size": 128})
    # We can also use camel case
    assert are_limits_wgpu_legal({"maxBindGroups": 8, "maxImmediateSize": 128})


def test_limits_are_not_legal():
    assert not are_limits_wgpu_legal({"max-bind-group": 8})


if __name__ == "__main__":
    run_tests(globals())
