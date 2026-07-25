"""The two specs must stay connected, and the public API must stay faithful.

The Web IDL shapes the public API; webgpu.json shapes the FFI. If a bump to
either leaves a name unmatched, the generated bindings would silently lose it,
so the bridge's own report is asserted here.
"""

import pytest

from bindgen import bridge


@pytest.fixture(scope="module")
def b():
    return bridge.build()


def test_every_idl_name_resolves(b):
    """Nothing may go unmatched: each divergence is declared, not discovered."""
    assert b.unmatched == [], (
        "the specs have drifted; declare each case in bindgen.bridge "
        f"(IDL_ONLY_*, STRUCT_ALIASES, SHAPE_ADAPTED_FIELDS): {b.unmatched}"
    )


def test_the_bridge_actually_covers_the_api(b):
    """Guard against 'no unmatched names' being true because nothing matched."""
    assert len(b.enum_types) >= 30
    assert len(b.enum_values) >= 250
    assert len(b.structs) >= 45
    assert len(b.classes) == 20
    assert len(b.methods) >= 50


def test_camel_to_snake():
    assert bridge.camel_to_snake("createBindGroupLayout") == "create_bind_group_layout"
    assert bridge.camel_to_snake("mapAsync") == "map_async"
    assert bridge.camel_to_snake("maxDrawCount") == "max_draw_count"


def test_normalisation_joins_the_two_spellings():
    # The C spec's RGBA8_unorm and the web's rgba8unorm are the same value.
    assert bridge._key("RGBA8_unorm") == bridge._key("rgba8unorm")
    assert bridge._key("2D_array") == bridge._key("2d-array")
    assert bridge._key("clamp_to_edge") == bridge._key("clamp-to-edge")
