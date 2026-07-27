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


# -- lifetimes ---------------------------------------------------------------


def test_destroying_a_query_set_does_not_double_free(wgpu, device):
    """Destroy then release used to abort the process, not raise.

    ``wgpuQuerySetDestroy`` drops the wgpu-core resource, and the handle's own
    ``Drop`` drops it again when the release lands; wgpu-core panics on the
    vacant slot, and a Rust panic cannot unwind across FFI. If this regresses
    it takes the whole test run with it, so there is nothing subtle to assert
    -- reaching the end is the assertion.
    """
    import gc

    for _ in range(4):
        query_set = device.create_query_set(type=wgpu.QueryType.occlusion, count=2)
        query_set.destroy()
        query_set.destroy()  # idempotent, as the spec says
        del query_set
        gc.collect()


def test_the_double_free_is_still_the_one_wgpu_native_has():
    """The suppression above is derived from wgpu-native, not asserted here.

    If upstream fixes ``Destroy`` (its own source says it should), this set
    goes empty and the release starts happening again -- correctly, and with
    no edit. This test exists so that day is visible rather than silent.
    """
    assert paths.destroy_consumes_handle() == {"wgpuQuerySetDestroy"}


def test_finished_encoders_do_not_outlive_their_output(wgpu, device):
    """``finish()`` invalidates the encoder, so its product must not pin it."""
    import gc

    def encoders():
        return wgpu.diagnostics.object_counts.get_dict()["CommandEncoder"]["count"]

    gc.collect()
    before = encoders()
    buffers = []
    for _ in range(3):
        encoder = device.create_command_encoder()
        buffers.append(encoder.finish())
    del encoder
    gc.collect()
    assert encoders() == before
    assert all(b is not None for b in buffers)


def test_a_device_has_its_queue_from_the_start(wgpu, device):
    """Counted lazily, a device dropped without a ``.queue`` never adds one."""
    import gc

    def queues():
        return wgpu.diagnostics.object_counts.get_dict()["Queue"]["count"]

    gc.collect()
    before = queues()
    device2 = device.adapter.request_device_sync()
    assert queues() == before + 1
    assert device2.queue is device2.queue
    del device2
    gc.collect()
    assert queues() == before


# -- what counts as a spec object --------------------------------------------


def test_only_the_idl_says_which_handles_are_labelled_objects(wgpu):
    """``GPUObjectBase`` is a spec mixin, not "everything with a handle".

    The IDL spells out which interfaces include it, and the adapter is not one
    of them -- it has no label, and asking it for its device is a question with
    no answer. Both are still handles Python owns and releases.
    """
    from wgpu._api.base import GPUHandle, GPUObjectBase

    adapter = wgpu.gpu.request_adapter_sync()
    assert isinstance(adapter, GPUHandle)
    assert not isinstance(adapter, GPUObjectBase)
    assert not hasattr(adapter, "label")
    assert isinstance(adapter.request_device_sync(), GPUObjectBase)


def test_every_labelled_object_belongs_to_a_device(wgpu, device):
    """The property that the split above is what makes true."""
    from wgpu._api.base import GPUObjectBase

    for obj in (
        device,
        device.queue,
        device.create_buffer(size=16, usage="COPY_SRC"),
        device.create_command_encoder(),
    ):
        assert isinstance(obj, GPUObjectBase)
        assert obj._device is device


def test_unreleasable_object_types_get_no_class(wgpu):
    """A class nobody can hold an instance of is worse than no class.

    wgpu-native does not implement ``wgpuExternalTextureRelease`` -- external
    textures come from browser image sources, which do not exist here -- so a
    ``GPUExternalTexture`` could only ever be a name in the object counts that
    never moves off zero.
    """
    import wgpu._generated.classes as classes
    from wgpu._generated.objects import OBJECTS

    assert "external_texture" in OBJECTS  # still in the C spec
    assert not hasattr(classes, "GPUExternalTexture")
    assert "ExternalTexture" not in wgpu.diagnostics.object_counts.get_dict()


# -- required struct members -------------------------------------------------


def test_a_required_member_with_no_value_is_an_error(wgpu, device):
    """A zero is a value, not an absence, so it cannot stand in for one.

    The C spec has no notion of a required field -- every field of a C struct
    exists, zeroed -- so this comes from the Web IDL's ``required``. Without it
    an omitted width reaches wgpu-native as a zero-sized texture, which fails
    later and somewhere else, if it fails at all.
    """
    from wgpu._runtime.api import get_api

    build = get_api().invoker.structs.new
    for bad in ({"height": 20}, (), []):
        with pytest.raises(ValueError):
            build("extent_3D", bad)
    with pytest.raises(ValueError):
        build("color", (0.1, 0.2, 0.3))  # a is required too

    # A member with a default is a different thing, and still fills itself in.
    ptr, _keep = build("extent_3D", {"width": 10})
    assert (ptr.width, ptr.height, ptr.depthOrArrayLayers) == (10, 1, 1)


def test_required_comes_from_the_idl_not_from_a_list():
    """Nothing names ``width`` here; the vendored IDL does."""
    from wgpu._generated.structs import STRUCTS

    required = {m.py for m in STRUCTS["extent_3D"].members if m.required}
    assert required == {"width"}
    assert {m.py for m in STRUCTS["origin_3D"].members if m.required} == set()
    assert {m.py for m in STRUCTS["color"].members if m.required} == {
        "r",
        "g",
        "b",
        "a",
    }
    # A label is never required, on any descriptor.
    assert not any(
        m.required for desc in STRUCTS.values() for m in desc.members if m.py == "label"
    )


def test_an_enum_map_remembers_its_canonical_spellings(wgpu):
    """The alternates are cached into the map, so the originals need keeping.

    Without this the "expected ..." message grows every time someone spells a
    name a different way, and nothing can ask what the real values are.
    """
    from wgpu._generated.apienums import TO_INT

    table = TO_INT["texture_format"]
    before = set(table.spellings)
    # An underscored spelling resolves, and is cached into the map itself ...
    assert table["depth24plus_stencil8"] == table["depth24plus-stencil8"]
    assert "depth24plus_stencil8" in table
    # ... but the canonical set is unchanged by that.
    assert set(table.spellings) == before
    assert "depth24plus_stencil8" not in before


# -- wgpu-py's own extras ----------------------------------------------------


def test_adapter_can_be_pinned_by_name(wgpu, monkeypatch):
    """``WGPUPY_WGPU_ADAPTER_NAME`` is how CI picks the software renderer."""
    summary = wgpu.gpu.request_adapter_sync().summary
    fragment = summary.split("|")[0].strip().split()[0]

    monkeypatch.setenv("WGPUPY_WGPU_ADAPTER_NAME", fragment)
    promise = wgpu.gpu.request_adapter_async()
    assert promise._title == "adapter by name"
    assert fragment in promise.sync_wait().summary

    monkeypatch.setenv("WGPUPY_WGPU_ADAPTER_NAME", "no-such-adapter-anywhere")
    with pytest.raises(ValueError):
        wgpu.gpu.request_adapter_sync()


def test_api_tracing_refuses_rather_than_writing_nothing(wgpu):
    """wgpu removed tracing upstream; wgpu-native's cargo feature is disabled."""
    import wgpu.backends.wgpu_native as native

    adapter = wgpu.gpu.request_adapter_sync()
    with pytest.raises(NotImplementedError) as info:
        native.request_device_sync(adapter, "/tmp/some-trace-dir")
    assert "wgpu-native" in str(info.value)


def test_flags_accept_an_integer_literal_string():
    """ "0xF" is a flag value wgpu-py has always taken, and the examples use it.

    The named members are the documented spelling, but a mask written out as a
    number -- from a config file, or copied from the C headers -- reached the
    flag map as a string and was rejected, which broke the triangle example.
    """
    from wgpu._generated.apiflags import TO_INT
    from wgpu._runtime.errors import InvalidValueError

    mask = TO_INT["color_write_mask"]
    assert mask["0xF"] == mask["ALL"]
    assert mask["15"] == mask["ALL"]
    assert mask["RED|0x2"] == mask["RED"] | mask["GREEN"]
    with pytest.raises(InvalidValueError):
        mask["not_a_flag"]


def test_web_idl_defaults_reach_every_member(device):
    """A default the IDL states must survive the trip into the descriptor.

    The two specs spell a member differently -- webgpu.json says ``sample_type``
    where the C header says ``sampleType`` -- and the generator has to key its
    IDL lookups by the C spelling. Getting that wrong loses the default for
    every *multi-word* member and only those, which is silent: the member goes
    out as a zero, and for these particular ones a zero means "not used", so
    wgpu-native rejects the whole entry.
    """
    from wgpu._generated.structs import STRUCTS

    expected = {
        # (struct, member): the Web IDL's stated default, as a C value.
        ("texture_binding_layout", "sample_type"): "float",
        ("texture_binding_layout", "view_dimension"): "2d",
        ("storage_texture_binding_layout", "view_dimension"): "2d",
        ("sampler_binding_layout", "type"): "filtering",
        ("buffer_binding_layout", "type"): "uniform",
    }
    from wgpu._generated import apienums

    for (struct, member), spelling in expected.items():
        mem = next(m for m in STRUCTS[struct].members if m.py == member)
        want = apienums.TO_INT[mem.ref][spelling]
        assert mem.default == want, (
            f"{struct}.{member} defaults to {mem.default!r}, "
            f"but the IDL says {spelling!r} ({want})"
        )


def test_a_bare_binding_layout_is_accepted(device):
    """Each of the four layout kinds must work with no members given.

    Left without its IDL default, ``texture={}`` reaches wgpu-native as
    "binding not used" for all four kinds at once, and wgpu-native answers a
    Rust panic that cannot unwind -- so the process aborts rather than raising.
    """
    for kind in ("buffer", "sampler", "texture"):
        layout = device.create_bind_group_layout(
            entries=[{"binding": 0, "visibility": "FRAGMENT", kind: {}}]
        )
        assert layout is not None, kind
