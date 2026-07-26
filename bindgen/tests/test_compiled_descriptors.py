"""Methods that build their descriptor inline must still behave identically.

The interpreted builder walks a struct descriptor on every call. For structs
whose members are all settable in one statement, the generator does that walk
once and emits straight-line code instead. That skips the runtime builder
entirely, so the behaviour it used to provide -- labels, defaults, enum
validation, error timing -- is asserted here rather than assumed.
"""

import re
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
def device():
    sys.path.insert(0, str(paths.REPO_ROOT))
    import wgpu

    adapter = wgpu.gpu.request_adapter_sync()
    if adapter is None:
        pytest.skip("no adapter available")
    return adapter.request_device_sync()


@pytest.fixture(scope="module")
def source():
    return (GENERATED_DIR / "apiclasses.py").read_text()


def test_create_buffer_is_actually_compiled(source):
    """Guard the premise: if this stops compiling, the rest proves nothing."""
    body = source.split("def create_buffer(")[1].split("def ")[0]
    assert "_ffi.new" in body and "_call_desc" not in body


def test_label_survives(device):
    """wgpu-native never hands the label back, so it is kept Python-side."""
    assert (
        device.create_buffer(label="vbuf", size=256, usage="COPY_DST").label == "vbuf"
    )
    assert device.create_buffer(size=256, usage="COPY_DST").label == ""


def test_multibyte_label(device):
    """The StringView length is in bytes, not characters."""
    label = "bü≈ffer"
    assert device.create_buffer(label=label, size=64, usage="COPY_DST").label == label


def test_defaults_match_the_interpreted_builder(device):
    """An omitted field takes the spec default, not a zeroed one."""
    # max_anisotropy defaults to 1; a zero would be invalid and wgpu-native
    # would complain, so a clean create is the assertion.
    assert device.create_sampler().__class__.__name__ == "GPUSampler"
    assert device.create_query_set(type="occlusion", count=4).count == 4


def test_required_members_are_rejected_when_explicitly_none(device):
    """``size=None`` must raise, not build a zero-sized buffer.

    Omitting a required field is caught by the keyword signature, which gives
    it no default. Passing ``None`` explicitly is not: it means "unspecified",
    and unspecified plus no default is exactly what ``required`` forbids. The
    compiled bodies used to fall through that case and send C a zero, which
    wgpu-native reads as a real value -- a silent divergence from the
    interpreted builder, which has raised here since it learned ``required``.
    """
    from wgpu._runtime.errors import InvalidValueError

    with pytest.raises(InvalidValueError, match="'size' is required"):
        device.create_buffer(size=None, usage="COPY_DST")
    with pytest.raises(InvalidValueError, match="'usage' is required"):
        device.create_buffer(size=8, usage=None)


def test_every_compiled_required_member_has_a_guard(source):
    """The rule above, checked against the descriptors rather than by example.

    Any future struct that gains a required member gets the guard for free;
    this fails if the emitter ever stops writing one, without needing a
    sample value for every other member of that struct.
    """
    from wgpu._generated.structs import STRUCTS

    by_c_name = {d.c_name: d for d in STRUCTS.values()}
    checked = 0
    for chunk in source.split("\n    def ")[1:]:
        match = re.search(r'_ffi\.new\("(\w+) \*"\)', chunk)
        if match is None:
            continue  # not a compiled descriptor body
        desc = by_c_name.get(match.group(1))
        if desc is None:
            continue
        for mem in desc.members:
            if not mem.required or mem.default is not None:
                continue
            assert "raise InvalidValueError" in chunk and mem.py in chunk, (
                f"{chunk.split('(')[0]}: required member {mem.py!r} has no guard"
            )
            checked += 1
    assert checked, "no compiled body has a required member -- test proves nothing"


def test_enum_values_are_still_validated(device):
    with pytest.raises(ValueError, match="Invalid value for FilterMode"):
        device.create_sampler(mag_filter="bogus")


def test_enum_accepts_string_and_member(device):
    import wgpu

    assert device.create_sampler(mag_filter="linear")
    assert device.create_sampler(mag_filter=wgpu.FilterMode.linear)


def test_flags_accept_string_and_int(device):
    import wgpu

    assert device.create_buffer(size=64, usage="COPY_SRC|COPY_DST")
    assert device.create_buffer(
        size=64, usage=wgpu.BufferUsage.COPY_SRC | wgpu.BufferUsage.COPY_DST
    )


def test_errors_are_reported_at_the_call(device):
    """Unlike the per-draw setters, these check before returning.

    finish() is a compiled method *and* the boundary the rest of the API
    reports errors at, so deferring here would move every deferred error one
    step further away.
    """
    import wgpu

    with pytest.raises(wgpu.GPUError):
        device.create_buffer(size=256, usage="MAP_READ|MAP_WRITE")
    # ...and the device is still usable afterwards.
    assert device.create_buffer(size=64, usage="COPY_DST")


def test_finish_still_reports_a_deferred_error(device):
    """A bad recorded command surfaces at finish(), as it always has."""
    import wgpu

    encoder = device.create_command_encoder()
    src = device.create_buffer(size=64, usage="COPY_SRC")
    dst = device.create_buffer(size=64, usage="COPY_SRC")  # not COPY_DST
    encoder.copy_buffer_to_buffer(src, 0, dst, 0, 64)
    with pytest.raises(wgpu.GPUError):
        encoder.finish()


def test_submit_is_compiled(source):
    body = source.split("def submit(")[1].split("def ")[0]
    assert "_c_wgpuQueueSubmit" in body


def test_submit_array_shapes(device):
    """One IDL parameter becomes a C count plus pointer; empty means NULL."""
    device.queue.submit([])
    device.queue.submit([device.create_command_encoder().finish()])
    device.queue.submit([device.create_command_encoder().finish() for _ in range(3)])
    # Any sequence, not just a list -- the annotation says Sequence.
    device.queue.submit(
        tuple(device.create_command_encoder().finish() for _ in range(2))
    )


def test_submit_still_reports_errors(device):
    """submit() allocates, so it checks rather than defers -- and it is the
    boundary the genuinely deferred errors surface at."""
    import wgpu

    encoder = device.create_command_encoder()
    cpass = encoder.begin_compute_pass()
    # dispatch without a pipeline: a per-draw setter, so the error is deferred.
    cpass.dispatch_workgroups(1)
    cpass.end()
    with pytest.raises(wgpu.GPUError):
        device.queue.submit([encoder.finish()])
