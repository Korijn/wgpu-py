"""The marshalling rules that are easy to get subtly, silently wrong.

Every case here failed at some point against real wgpu-native, and every one
of them failed *quietly*: a pipeline that could not find its entry point, a
buffer that read back as zeros, a property that was never attached. They are
pinned individually because a single end-to-end test would only say that
something broke, not which rule.
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
    return wgpu.utils.get_default_device()


# -- strings -----------------------------------------------------------------


def test_omitted_string_is_null_not_empty(device):
    """A zeroed WGPUStringView means the empty string, so it must be written.

    With ``{NULL, 0}`` on the wire, wgpu-native looks for an entry point
    *named* ``""`` and the pipeline fails to build. The module here has a
    single entry point and does not name it, which is the whole point.
    """
    module = device.create_shader_module(
        code="@compute @workgroup_size(1) fn main() {}"
    )
    layout = device.create_pipeline_layout(bind_group_layouts=[])
    pipeline = device.create_compute_pipeline(layout=layout, compute={"module": module})
    assert pipeline is not None


def test_explicit_entry_point_still_works(device):
    module = device.create_shader_module(
        code="@compute @workgroup_size(1) fn main() {}"
    )
    layout = device.create_pipeline_layout(bind_group_layouts=[])
    pipeline = device.create_compute_pipeline(
        layout=layout, compute={"module": module, "entry_point": "main"}
    )
    assert pipeline is not None


def test_label_survives_a_multibyte_round_trip(device):
    buffer = device.create_buffer(size=16, usage="COPY_SRC", label="buf-é☃")
    assert buffer.label == "buf-é☃"


def test_async_creation_keeps_its_label(wgpu):
    """The object arrives via a promise, so nothing stamps it on the way out."""
    adapter = wgpu.gpu.request_adapter_sync()
    device = adapter.request_device_sync(label="spam")
    assert device.label == "spam"


# -- tri-state booleans ------------------------------------------------------


def test_optional_bool_takes_python_bools(wgpu):
    from wgpu._generated.apienums import TO_INT

    table = TO_INT["optional_bool"]
    assert table[True] == table["true"]
    assert table[False] == table["false"]
    assert table[None] == table["undefined"]


def test_optional_bool_aliases_stay_out_of_the_reverse_map(wgpu):
    from wgpu._generated.apienums import FROM_INT, TO_INT

    reverse = FROM_INT["optional_bool"]
    assert reverse[TO_INT["optional_bool"]["true"]] == "true"
    assert set(reverse.values()) == {"false", "true", "undefined"}


def test_depth_write_enabled_accepts_a_bool(device):
    """``depthWriteEnabled`` is an enum in C and a boolean in the Web IDL."""
    module = device.create_shader_module(
        code="""
        @vertex fn vs() -> @builtin(position) vec4<f32> {
            return vec4<f32>(0.0, 0.0, 0.0, 1.0);
        }
        """
    )
    pipeline = device.create_render_pipeline(
        layout=device.create_pipeline_layout(bind_group_layouts=[]),
        vertex={"module": module},
        depth_stencil={
            "format": "depth32float",
            "depth_write_enabled": True,
            "depth_compare": "less",
        },
    )
    assert pipeline is not None


def test_glsl_source_is_recognised_and_staged(device):
    """GLSL says nowhere what stage it is; wgpu-py reads that off the label."""
    module = device.create_shader_module(
        label="a comp shader",
        code="""
        #version 450 core
        layout(local_size_x = 1) in;
        layout(std430, binding=0) buffer B { int out1[]; };
        void main() { out1[gl_GlobalInvocationID.x] = 1; }
        """,
    )
    assert module is not None


def test_glsl_without_a_stage_in_the_label_says_so(device, wgpu):
    with pytest.raises(ValueError, match="stage"):
        device.create_shader_module(
            label="mystery",
            code="#version 450 core\nvoid main() {}",
        )


def test_wgsl_is_not_mistaken_for_glsl(device):
    module = device.create_shader_module(
        label="comp", code="@compute @workgroup_size(1) fn main() {}"
    )
    assert module is not None


# -- names -------------------------------------------------------------------


def test_enum_names_accept_three_spellings(wgpu):
    from wgpu._generated.apienums import TO_INT

    table = TO_INT["feature_name"]
    expected = table["vertex-writable-storage"]
    assert table["vertex_writable_storage"] == expected
    assert table["VertexWritableStorage"] == expected


def test_an_unknown_name_is_both_a_value_and_a_key_error(wgpu):
    """Historical code catches KeyError; current code catches ValueError."""
    from wgpu._generated.apienums import TO_INT

    with pytest.raises(KeyError):
        TO_INT["feature_name"]["no-such-feature"]
    with pytest.raises(ValueError):
        TO_INT["feature_name"]["no-such-feature"]


def test_the_message_is_not_repr_quoted(wgpu):
    """KeyError renders with repr, which would mangle these messages."""
    from wgpu._generated.apienums import TO_INT

    with pytest.raises(ValueError) as excinfo:
        TO_INT["feature_name"]["no-such-feature"]
    assert not str(excinfo.value).startswith("'")


def test_limits_are_reported_hyphenated(device):
    limits = device.limits
    assert "max-bind-groups" in limits
    assert all("_" not in key for key in limits)


def test_limits_read_back_in(device, wgpu):
    """The reported spelling has to be one the API accepts again."""
    adapter = wgpu.gpu.request_adapter_sync()
    key = "max-bind-groups"
    device2 = adapter.request_device_sync(required_limits={key: 4})
    assert device2.limits[key] == 4


# -- objects -----------------------------------------------------------------


def test_texture_view_knows_its_texture(device):
    texture = device.create_texture(
        size=(4, 4, 1), format="rgba8unorm", usage="TEXTURE_BINDING"
    )
    view = texture.create_view()
    assert view.texture is texture
    assert view.size == texture.size


def test_device_is_reachable_from_anything_it_made(device):
    buffer = device.create_buffer(size=16, usage="COPY_SRC")
    encoder = device.create_command_encoder()
    assert buffer._device is device
    assert encoder._device is device
    assert device._device is device


def test_repr_shows_the_label_only_when_there_is_one(device):
    labelled = device.create_buffer(size=16, usage="COPY_SRC", label="spam")
    plain = device.create_buffer(size=16, usage="COPY_SRC")
    assert "object 'spam' at" in repr(labelled)
    assert "object at" in repr(plain)


# -- queue ordering ----------------------------------------------------------


def test_queued_writes_are_visible_once_mapped(device):
    """Staged writes only land on submit; mapping has to force one."""
    buffer = device.create_buffer(size=16, usage="MAP_READ|COPY_DST")
    device.queue.write_buffer(buffer, 0, b"0123456789abcdef")
    buffer.map_sync("read")
    assert bytes(buffer.read_mapped()) == b"0123456789abcdef"
    buffer.unmap()


def test_mapped_at_creation_survives_a_remap(device):
    buffer = device.create_buffer(size=12, usage="MAP_READ", mapped_at_creation=True)
    buffer.write_mapped(b"abcdefghijkl")
    buffer.unmap()
    buffer.map_sync("read")
    assert bytes(buffer.read_mapped()) == b"abcdefghijkl"
    buffer.unmap()


def test_a_read_mapping_is_not_writable(device):
    buffer = device.create_buffer(size=16, usage="MAP_READ|COPY_DST")
    buffer.map_sync("read")
    view = buffer.read_mapped(copy=False)
    with pytest.raises(TypeError):
        view[0] = 1
    buffer.unmap()


def test_a_write_mapping_is_writable(device):
    buffer = device.create_buffer(size=16, usage="MAP_WRITE|COPY_SRC")
    buffer.map_sync("write")
    buffer.get_mapped_range()[0] = 1
    buffer.unmap()


# -- ranges ------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [(10,), (-10,), (12, 30), (12, 64)],
    ids=["offset-misaligned", "offset-negative", "size-misaligned", "size-too-big"],
)
def test_clear_buffer_rejects_a_bad_range_at_the_call(device, args):
    """Not at finish(), which is where wgpu-native would report it."""
    buffer = device.create_buffer(size=52, usage="COPY_DST|COPY_SRC")
    encoder = device.create_command_encoder()
    with pytest.raises(ValueError):
        encoder.clear_buffer(buffer, *args)


def test_mapped_views_are_released_on_unmap(device):
    """A dangling view onto reclaimed memory reads garbage; a released one raises."""
    buffer = device.create_buffer(size=16, usage="MAP_READ|COPY_DST")
    device.queue.write_buffer(buffer, 0, b"0123456789abcdef")
    buffer.map_sync("read")
    view = buffer.read_mapped(copy=False)
    assert view[0] == ord("0")
    buffer.unmap()
    with pytest.raises(ValueError):
        view[0]


def test_a_copied_read_survives_unmap(device):
    buffer = device.create_buffer(size=16, usage="MAP_READ|COPY_DST")
    device.queue.write_buffer(buffer, 0, b"0123456789abcdef")
    buffer.map_sync("read")
    data = buffer.read_mapped()
    buffer.unmap()
    assert bytes(data) == b"0123456789abcdef"


def test_read_texture_pads_the_row_stride(device):
    """wgpu-native only copies 256-byte rows; read_texture asks for what you want."""
    width, height = 5, 3  # 5 * 4 = 20 bytes per row, nowhere near 256
    texture = device.create_texture(
        size=(width, height, 1),
        format="rgba8unorm",
        usage="COPY_DST|COPY_SRC",
    )
    data = bytes(range(width * height * 4))
    device.queue.write_texture(
        {"texture": texture},
        data,
        {"bytes_per_row": width * 4, "rows_per_image": height},
        (width, height, 1),
    )
    out = device.queue.read_texture(
        {"texture": texture},
        {"bytes_per_row": width * 4, "rows_per_image": height},
        (width, height, 1),
    )
    assert bytes(out) == data


def test_copy_texture_to_buffer_rejects_an_unaligned_row(device):
    texture = device.create_texture(
        size=(5, 3, 1), format="rgba8unorm", usage="COPY_SRC"
    )
    buffer = device.create_buffer(size=4096, usage="COPY_DST")
    encoder = device.create_command_encoder()
    with pytest.raises(ValueError, match="256"):
        encoder.copy_texture_to_buffer(
            {"texture": texture},
            {"buffer": buffer, "offset": 0, "bytes_per_row": 20},
            (5, 3, 1),
        )


def test_clear_buffer_accepts_a_good_range(device):
    buffer = device.create_buffer(size=52, usage="COPY_DST|COPY_SRC")
    device.queue.write_buffer(buffer, 0, bytes(range(52)))
    encoder = device.create_command_encoder()
    encoder.clear_buffer(buffer, 8, 12)
    device.queue.submit([encoder.finish()])
    result = bytes(device.queue.read_buffer(buffer))
    assert result == bytes(range(8)) + bytes(12) + bytes(range(20, 52))


# -- depth/stencil attachments ----------------------------------------------


def test_stencil_ops_are_dropped_for_a_depth_only_target(wgpu, device):
    """wgpu-py used to require all four ops, so a lot of code still passes them."""
    warnings = []
    original = wgpu.logger.warning
    wgpu.logger.warning = warnings.append
    try:
        texture = device.create_texture(
            size=(64, 64, 1), format="depth32float", usage="RENDER_ATTACHMENT"
        )
        attachment = dict(
            view=texture.create_view(),
            depth_clear_value=0.1,
            depth_load_op="clear",
            depth_store_op="store",
            stencil_load_op="clear",
            stencil_store_op="store",
        )
        encoder = device.create_command_encoder()
        encoder.begin_render_pass(
            color_attachments=[], depth_stencil_attachment=attachment
        ).end()
        assert len(warnings) == 2
        # Once per device, not once per call: this is a per-frame path.
        encoder.begin_render_pass(
            color_attachments=[], depth_stencil_attachment=attachment
        ).end()
        assert len(warnings) == 2
    finally:
        wgpu.logger.warning = original


def test_stencil_ops_are_kept_for_a_depth_stencil_target(wgpu, device):
    warnings = []
    original = wgpu.logger.warning
    wgpu.logger.warning = warnings.append
    try:
        texture = device.create_texture(
            size=(64, 64, 1), format="depth24plus-stencil8", usage="RENDER_ATTACHMENT"
        )
        encoder = device.create_command_encoder()
        encoder.begin_render_pass(
            color_attachments=[],
            depth_stencil_attachment=dict(
                view=texture.create_view(),
                depth_clear_value=0.1,
                depth_load_op="clear",
                depth_store_op="store",
                stencil_load_op="clear",
                stencil_store_op="store",
            ),
        ).end()
        assert not warnings
    finally:
        wgpu.logger.warning = original


# -- wgpu-native's own voice -------------------------------------------------


def test_native_log_reaches_the_python_logger(wgpu, device, caplog):
    """The detail behind a shader error arrives through the log, not the error.

    wgpu-native reports "validation failed"; naga explains *why* through its
    log. Without the bridge the explanation is simply dropped.
    """
    import logging

    code = """
        struct Varyings {
            @builtin(position) position : vec4<f32>,
            @location(0) uv : vec2<f32>,
        };
        @vertex
        fn fs_main(in: Varyings) -> @location(0) vec4<f32> {
            return vec3<f32>(1.0, 0.0, 1.0);
        }
    """
    with caplog.at_level(logging.INFO, logger="wgpu"):
        with pytest.raises(wgpu.GPUError):
            device.create_shader_module(code=code)
    assert caplog.records, "wgpu-native said nothing -- is the log bridge installed?"
    assert "is expected" in caplog.records[0].msg


def test_the_log_level_is_pushed_down_to_native(wgpu):
    """Filtering in Rust means the suppressed messages are never formatted."""
    from wgpu._runtime import logbridge

    assert logbridge._callback is not None
    from wgpu._coreutils import logger_set_level_callbacks

    assert logbridge._set_native_level in logger_set_level_callbacks


# -- version metadata --------------------------------------------------------


def test_the_pinned_wgpu_native_commit_is_recorded(wgpu):
    """A static link leaves no library to inspect, so it is recorded instead."""
    import wgpu.backends.wgpu_native as native

    assert len(native.__commit_sha__) >= 7
    assert len(native.version_info) == 4
    assert all(isinstance(part, int) for part in native.version_info)
    assert native.lib_path.endswith((".so", ".pyd", ".dylib"))


def test_the_diagnostics_report_runs(wgpu):
    import wgpu.backends.wgpu_native

    text = wgpu.diagnostics.get_report()
    assert "wgpu_native_info" in text


# -- lifetimes ---------------------------------------------------------------


def test_a_bundle_encoder_outlives_its_arguments(device):
    """Dropping a pipeline before finish() used to abort the process.

    A command encoder takes ownership straight away; a bundle encoder reads
    its references again at finish(). wgpu-core panics on a released slot, and
    a Rust panic cannot unwind through the FFI -- so this is a crash, not an
    exception, and no test can catch it after the fact.
    """
    import gc

    module = device.create_shader_module(
        code="""
        @vertex fn vs() -> @builtin(position) vec4<f32> {
            return vec4<f32>(0.0, 0.0, 0.0, 1.0);
        }
        @fragment fn fs() -> @location(0) vec4<f32> {
            return vec4<f32>(1.0, 0.0, 0.0, 1.0);
        }
        """
    )
    pipeline = device.create_render_pipeline(
        layout=device.create_pipeline_layout(bind_group_layouts=[]),
        vertex={"module": module},
        fragment={"module": module, "targets": [{"format": "rgba8unorm"}]},
    )
    encoder = device.create_render_bundle_encoder(color_formats=["rgba8unorm"])
    encoder.set_pipeline(pipeline)
    encoder.draw(3)
    del pipeline
    gc.collect()
    assert encoder.finish() is not None


def test_object_counts_follow_creation_and_release(wgpu, device):
    import gc

    def buffers():
        return wgpu.diagnostics.object_counts.get_dict()["Buffer"]["count"]

    before = buffers()
    held = [device.create_buffer(size=16, usage="COPY_SRC") for _ in range(3)]
    assert buffers() == before + 3
    held.clear()
    gc.collect()
    assert buffers() == before


def test_native_counts_cover_the_same_objects(wgpu, device):
    """The two reports exist to be compared, so they must be comparable.

    The counts themselves are not asserted against each other: wgpu-core
    recycles slots and Python collects on its own schedule, so at any instant
    either side can legitimately lead. What has to hold is that the names line
    up, which is what makes a real divergence visible.
    """
    native = wgpu.diagnostics.wgpu_native_counts.get_dict()
    python = wgpu.diagnostics.object_counts.get_dict()
    shared = {"Buffer", "Texture", "RenderPipeline", "ShaderModule", "total"}
    assert shared <= set(native)
    assert shared <= set(python)


def test_native_counts_survive_a_dropped_instance(wgpu):
    """The report must not hold -- or dangle on -- an instance handle."""
    import gc

    gc.collect()
    assert isinstance(wgpu.diagnostics.wgpu_native_counts.get_dict(), dict)


# -- the hooks themselves ----------------------------------------------------


def test_hand_written_hooks_all_match_the_idl():
    """A hook naming a member the IDL dropped suppresses nothing, silently."""
    from bindgen import bridge, genapi

    b = bridge.build()
    for cls_name, member in genapi.HAND_WRITTEN_ATTRS:
        assert member in b.idl.classes[cls_name].attributes
    for cls_name, member in genapi.HAND_WRITTEN:
        assert member in b.idl.classes[cls_name].functions
