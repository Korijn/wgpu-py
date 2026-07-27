"""Fields the IDL and C spec shape differently should be derived, not declared.

When an IDL field has no C member of that name, webgpu.json usually already
says where it went: an extension struct declares it ``extends`` this one, or a
member's own struct type carries the field. Deriving that beats listing it --
a hand-written list can name a struct that does not exist (this is not
hypothetical; ``texture_binding_view_dimension`` did, and silently raised
KeyError for every caller), whereas a derived target is read off the spec.

These tests pin both halves: that the rule still fires where we expect, and
that it stays conservative enough not to fire where it should not.
"""

import pytest

from bindgen import bridge


@pytest.fixture(scope="module")
def b():
    return bridge.build()


#: What the two rules should find, given the current submodule.
EXPECTED = {
    ("RenderPassDescriptor", "maxDrawCount"): ("chain", "render_pass_max_draw_count"),
    ("TextureDescriptor", "textureBindingViewDimension"): (
        "chain",
        "texture_binding_view_dimension",
    ),
    ("TexelCopyBufferInfo", "offset"): ("nest", "layout"),
    ("TexelCopyBufferInfo", "bytesPerRow"): ("nest", "layout"),
    ("TexelCopyBufferInfo", "rowsPerImage"): ("nest", "layout"),
}


def test_the_expected_fields_are_derived(b):
    assert b.derived_adapters == EXPECTED


def test_derived_targets_name_real_structs(b):
    """A derived chain target must be a struct that exists and can chain."""
    structs = {s["name"]: s for s in b.spec["structs"]}
    for (idl_struct, field), (adapter, target) in b.derived_adapters.items():
        where = f"{idl_struct}.{field}"
        if adapter == "chain":
            assert target in structs, f"{where}: no such struct {target}"
            assert structs[target].get("type") == "extension", (
                f"{where}: {target} is not chainable"
            )
        elif adapter == "nest":
            c_struct = structs[b.structs[idl_struct]]
            members = {m["name"]: m for m in c_struct.get("members", ())}
            assert target in members, f"{where}: no member {target}"
            assert members[target]["type"].startswith("struct."), (
                f"{where}: {target} is not a struct member"
            )


def test_derivation_never_overrides_a_direct_match(b):
    """A field that matches a C member by name is never reshaped instead."""
    assert not set(b.struct_fields) & set(b.shape_adapted)


def test_declared_adapters_are_only_the_value_dependent_ones(b):
    """Anything the specs *can* answer should be derived, not listed.

    The two that remain need a runtime value to resolve: which slot of the
    binding-resource union applies, and whether shader source is WGSL or
    SPIR-V. ``ignored`` fields have no C counterpart at all.
    """
    declared = {
        key: value
        for key, value in b.shape_adapted.items()
        if key not in b.derived_adapters and value[0] != "ignored"
    }
    assert set(declared.values()) == {
        ("bind_group_resource", ""),
        ("shader_source", ""),
    }


def test_ambiguity_is_not_guessed(b):
    """Two extension structs offer `code`, so the rule must decline to pick."""
    structs = {s["name"]: s for s in b.spec["structs"]}
    assert bridge._derive_adapter(structs, "shader_module_descriptor", "code") is None


def test_a_field_with_nowhere_to_go_is_not_invented(b):
    structs = {s["name"]: s for s in b.spec["structs"]}
    assert bridge._derive_adapter(structs, "buffer_descriptor", "notAThing") is None
