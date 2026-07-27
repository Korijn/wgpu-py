"""Struct descriptors for wgpu-native's own extension structs.

``webgpu.json`` describes the WebGPU spec, and everything in it is generated.
wgpu-native's *own* extensions -- GLSL shader sources, push constants, and the
rest of the ``*Extras`` family -- are declared only in ``wgpu.h``, which is a C
header rather than a machine-readable spec, so they cannot be generated from
the same source.

They are spelled out here rather than marshalled by hand: written as the same
descriptors the generator emits, they go through the same struct builder and
the same chaining machinery, so nothing about *how* to marshal is duplicated.
Only the shape is, and ``bindgen/tests/test_extras_are_pinned.py`` checks each
one against the compiled header, so a submodule bump that changes a field
fails the build rather than corrupting memory.
"""

from __future__ import annotations

from wgpu._generated.structs import STRUCTS, Member, StructDescriptor
from wgpu._native import lib as _lib


def _member(py, c, kind, **kwargs):
    return Member(
        py=py,
        c=c,
        kind=kind,
        ref=kwargs.get("ref"),
        pointer=kwargs.get("pointer"),
        optional=kwargs.get("optional", False),
        default=kwargs.get("default"),
        array=kwargs.get("array", False),
        count_c=kwargs.get("count_c"),
    )


#: Descriptors keyed by the name the chain mechanism uses, matching the naming
#: the generator would have given them.
NATIVE_STRUCTS = {
    # A GLSL define, as a name/value pair.
    "shader_define": StructDescriptor(
        c_name="WGPUShaderDefine",
        category="standalone",
        members=(
            _member("name", "name", "string"),
            _member("value", "value", "string"),
        ),
    ),
    # GLSL source. Unlike WGSL, GLSL does not say in the source which stage it
    # compiles for, so the stage is a field.
    "shader_source_GLSL": StructDescriptor(
        c_name="WGPUShaderSourceGLSL",
        category="extension",
        s_type=int(_lib.WGPUSType_ShaderSourceGLSL),
        members=(
            _member("stage", "stage", "bitflag", ref="shader_stage"),
            _member("code", "code", "string"),
            _member(
                "defines",
                "defines",
                "struct",
                ref="shader_define",
                array=True,
                count_c="defineCount",
            ),
        ),
    ),
}


def install() -> None:
    """Add these to the generated table, so the runtime treats them alike."""
    for name, descriptor in NATIVE_STRUCTS.items():
        STRUCTS.setdefault(name, descriptor)


install()
