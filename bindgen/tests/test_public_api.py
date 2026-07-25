"""The public API must keep the shape existing wgpu-py code depends on.

These are the compatibility guarantees that matter to downstream users (pygfx
above all): enums are strings, flags are ints, both accept the historical
spellings, and the classic conveniences still work.
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


def test_enums_are_strings(wgpu):
    assert wgpu.TextureFormat.rgba8unorm == "rgba8unorm"
    assert isinstance(wgpu.BufferBindingType.storage, str)
    # The hyphenated web spellings, not the C spec's underscored ones.
    assert wgpu.AddressMode.clamp_to_edge == "clamp-to-edge"
    assert wgpu.TextureViewDimension.d2_array == "2d-array"


def test_flags_are_ints(wgpu):
    assert isinstance(wgpu.BufferUsage.STORAGE, int)
    assert wgpu.BufferUsage.MAP_READ | wgpu.BufferUsage.COPY_DST == 9


def test_flags_accept_strings(wgpu):
    from wgpu._generated import apiflags

    table = apiflags.TO_INT["buffer_usage"]
    assert table["MAP_READ|COPY_DST"] == 9
    assert table["map_read | copy_dst"] == 9  # case and spacing are forgiving
    assert table[9] == 9  # an already-resolved value passes through


def test_invalid_enum_value_says_what_was_expected(wgpu):
    from wgpu._generated import apienums

    with pytest.raises(ValueError) as excinfo:
        apienums.TO_INT["texture_format"]["not-a-format"]
    assert "TextureFormat" in str(excinfo.value)
    assert "rgba8unorm" in str(excinfo.value)


def test_structs_are_mappings(wgpu):
    desc = wgpu.structs.BufferDescriptor(size=8, usage=1)
    assert dict(**desc)["size"] == 8
    assert "BufferDescriptor" in repr(desc)


def test_object_classes_are_exported(wgpu):
    for name in ("GPU", "GPUDevice", "GPUBuffer", "GPUCanvasContext", "GPUError"):
        assert hasattr(wgpu, name), name
    assert isinstance(wgpu.gpu, wgpu.GPU)
