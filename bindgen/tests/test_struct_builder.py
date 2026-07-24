"""Exercise the runtime struct builder against real compiled structs."""

import importlib.util
import sys
from pathlib import Path

import pytest

from bindgen import paths
from bindgen.generate import GENERATED_DIR

pytestmark = pytest.mark.skipif(
    not (GENERATED_DIR / "structs.py").exists()
    or not list((paths.REPO_ROOT / "wgpu" / "_native").glob("_wgpu.*")),
    reason="run bindgen.ffi_build + bindgen.generate first",
)


def _load(name: str):
    modname = f"_gen_{name}"
    spec = importlib.util.spec_from_file_location(modname, GENERATED_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def builder():
    sys.path.insert(0, str(paths.REPO_ROOT))
    import wgpu._native as native

    from wgpu._runtime.structs import StructBuilder

    structs = _load("structs").STRUCTS
    enums = _load("enums")
    flags = _load("flags")
    constants = _load("constants")
    return StructBuilder(native.ffi, structs, enums, flags, constants), native.ffi, flags


def test_scalar_string_and_flags(builder):
    b, ffi, flags = builder
    ptr, _keep = b.new(
        "buffer_descriptor",
        {
            "label": "vertices",
            "usage": flags.BufferUsage.vertex | flags.BufferUsage.copy_dst,
            "size": 4096,
            "mapped_at_creation": False,
        },
    )
    assert ffi.string(ptr.label.data, ptr.label.length) == b"vertices"
    assert ptr.usage == int(flags.BufferUsage.vertex | flags.BufferUsage.copy_dst)
    assert ptr.size == 4096
    assert ptr.mappedAtCreation == 0


def test_default_applied_when_omitted(builder):
    b, ffi, _flags = builder
    # bind_group_entry.size defaults to constant.whole_size (uint64 max).
    ptr, _keep = b.new("bind_group_entry", {"binding": 0, "offset": 0})
    assert ptr.size == (1 << 64) - 1


def test_array_and_object_and_nested(builder):
    b, ffi, _flags = builder
    fake_layout = ffi.cast("WGPUBindGroupLayout", 0x1234)
    fake_buffer = ffi.cast("WGPUBuffer", 0x5678)
    ptr, _keep = b.new(
        "bind_group_descriptor",
        {
            "label": "bg",
            "layout": fake_layout,
            "entries": [
                {"binding": 0, "buffer": fake_buffer, "offset": 0, "size": 256},
                {"binding": 1, "buffer": fake_buffer, "offset": 256, "size": 256},
            ],
        },
    )
    assert ptr.layout == fake_layout
    assert ptr.entryCount == 2
    assert ptr.entries[0].binding == 0
    assert ptr.entries[1].binding == 1
    assert ptr.entries[1].offset == 256
    assert ptr.entries[0].buffer == fake_buffer


def test_unknown_field_rejected(builder):
    b, _ffi, _flags = builder
    with pytest.raises(TypeError):
        b.new("buffer_descriptor", {"nonexistent": 1})
