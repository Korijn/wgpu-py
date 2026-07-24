"""Validate the generated high-level enums/flags against the compiled lib.

Skipped unless the low-level extension has been built and the generator has run
(``python -m bindgen.ffi_build`` then ``python -m bindgen.generate``).
"""

import importlib.util
from pathlib import Path

import pytest

from bindgen import naming, paths
from bindgen.generate import GENERATED_DIR, load_spec

pytestmark = pytest.mark.skipif(
    not (GENERATED_DIR / "enums.py").exists()
    or not (paths.REPO_ROOT / "wgpu" / "_native").glob("_wgpu.*"),
    reason="run bindgen.ffi_build + bindgen.generate first",
)


def _load(name: str):
    """Import a generated module by path (avoids the heavy wgpu package)."""
    spec = importlib.util.spec_from_file_location(name, GENERATED_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lib():
    from bindgen.ffi_build import NATIVE_DIR, load_compiled

    return load_compiled(NATIVE_DIR).lib


def test_enums_match_lib():
    enums = _load("enums")
    lib = _lib()
    spec = load_spec()
    checked = 0
    for item in spec["enums"]:
        py_cls = getattr(enums, naming.py_enum_name(item["name"]))
        for entry in item["entries"]:
            if entry is None:
                continue
            c_val = int(getattr(lib, naming.c_enum_value(item["name"], entry["name"])))
            # every real value must be present in the generated IntEnum
            assert c_val in [int(m) for m in py_cls], (
                f"{py_cls.__name__} missing value {c_val}"
            )
            checked += 1
    assert checked > 400


def test_flags_are_intflag_and_combine():
    flags = _load("flags")
    import enum

    assert issubclass(flags.BufferUsage, enum.IntFlag)
    combo = flags.BufferUsage.map_read | flags.BufferUsage.copy_dst
    assert int(combo) == int(flags.BufferUsage.map_read) + int(flags.BufferUsage.copy_dst)
    # ColorWriteMask.all is the OR of its components
    assert int(flags.ColorWriteMask.all) == 15


def test_known_values():
    enums = _load("enums")
    # Spot-check a few stable, well-known WebGPU enum values.
    assert int(enums.TextureFormat.undefined) == 0
    assert int(enums.PrimitiveTopology.undefined) == 0
    assert int(enums.FeatureName.depth_clip_control) >= 1
