"""The classic-API compatibility shim, on top of the generated layer.

``test_buffer_roundtrip_like_classic_suite`` mirrors the semantics of the
historical ``tests/test_wgpu_native_buffer.py::test_buffer_init1``.
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
def compat():
    sys.path.insert(0, str(paths.REPO_ROOT))
    from wgpu import _compat

    return _compat


def test_classic_enum_and_flag_spellings(compat):
    from wgpu._generated import enums, flags

    # Flags: UPPER_CASE and "A|B" strings, as the classic API accepted.
    assert compat.enum_value(flags.BufferUsage, "COPY_SRC") == int(
        flags.BufferUsage.copy_src
    )
    assert compat.enum_value(flags.BufferUsage, "MAP_READ|COPY_DST") == int(
        flags.BufferUsage.map_read | flags.BufferUsage.copy_dst
    )
    # Enums: classic kebab-case and run-together spellings.
    assert compat.enum_value(enums.AddressMode, "clamp-to-edge") == int(
        enums.AddressMode.clamp_to_edge
    )
    assert compat.enum_value(enums.TextureFormat, "rgba8unorm") == int(
        enums.TextureFormat.RGBA8_unorm
    )
    # Members and ints pass through unchanged.
    assert compat.enum_value(flags.BufferUsage, flags.BufferUsage.vertex) == 32
    assert compat.enum_value(flags.BufferUsage, 4) == 4


def test_unknown_member_raises(compat):
    from wgpu._generated import flags

    with pytest.raises(ValueError):
        compat.enum_value(flags.BufferUsage, "NOT_A_USAGE")


@pytest.fixture(scope="module")
def device(compat):
    try:
        return compat.get_default_device()
    except RuntimeError as exc:
        pytest.skip(f"no adapter available: {exc}")


def test_buffer_roundtrip_like_classic_suite(compat, device):
    """Mirrors tests/test_wgpu_native_buffer.py::test_buffer_init1 semantics."""
    data1 = b"abcdefghijkl"
    buf = device.create_buffer_with_data(data=data1, usage="COPY_SRC")
    assert buf.size == len(data1)
    data2 = device.queue.read_buffer(buf)
    assert bytes(data2) == data1


def test_map_read_path(compat, device):
    data1 = b"0123456789ab"
    buf = device.create_buffer_with_data(data=data1, usage="MAP_READ")
    buf.map_sync("READ")
    assert bytes(buf.read_mapped()) == data1
    buf.unmap()


def test_queue_write_and_read(compat, device):
    buf = device.create_buffer(size=8, usage="COPY_DST|COPY_SRC")
    device.queue.write_buffer(buf, 0, b"12345678")
    assert bytes(device.queue.read_buffer(buf)) == b"12345678"
