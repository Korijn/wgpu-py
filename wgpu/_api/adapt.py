"""Reshape the few values whose Web and C forms genuinely differ.

Most of the public API maps onto C by renaming alone, which the generator does
ahead of time. A handful of fields do not: the web keeps a field inline where C
chains an extension struct, flattens where C nests, or passes a union where C
has a slot per type.

The first two are *derived*, not listed: webgpu.json declares which structs
extend which, and gives every member a type, so the generator can find where a
missing field went and record the target alongside the adapter. Adding a
chained extension upstream therefore needs no edit here. Only the shapes no
declaration covers -- where the choice depends on a runtime value -- are named
in ``bindgen.bridge.SHAPE_ADAPTED_FIELDS`` and written out below.

Each adapter takes the public mapping and returns a C-shaped one. Anything a
spec bump adds that is not handled here fails loudly at generation time rather
than being silently dropped.
"""

from __future__ import annotations


def reshape(builder, desc, mapping: dict, keep: list) -> dict:
    """Apply every adapter this struct declares, returning a C-shaped mapping."""
    out = dict(mapping)
    for field, adapter, target in desc.adapters:
        if adapter == "ignored":
            # Present on the web, meaningless natively (XR, video textures,
            # view swizzles). Accepted and dropped, so web code still runs.
            out.pop(field, None)
        elif adapter == "chain":
            _chain(out, field, target)
        elif adapter == "nest":
            _nest(out, field, target)
        else:
            _ADAPTERS[adapter](builder, out, keep)
    return out


def _chain(out: dict, field: str, target: str) -> None:
    """Move an inline field into the extension struct C expects it in."""
    value = out.pop(field, None)
    if value is None:
        return
    spec_name, mapping = out.get("_chain") or (target, {})
    if spec_name != target:
        raise ValueError(f"cannot chain both {spec_name} and {target}")
    mapping[field] = value
    out["_chain"] = (target, mapping)


def _nest(out: dict, field: str, target: str) -> None:
    """Move a field the web flattens into the member C nests it under."""
    if field not in out:
        return
    value = out.pop(field)
    if value is None:
        return
    inner = out.get(target)
    # The nested member may also have been given whole; merge, do not replace.
    out[target] = {**inner, field: value} if inner else {field: value}


def _bind_group_resource(builder, out: dict, keep: list) -> None:
    """``resource`` is a union on the web; C has a slot per resource kind.

    Accepts a buffer binding (``{"buffer": b, "offset": o, "size": s}``), or a
    sampler, texture view, or bare buffer object.
    """
    resource = out.pop("resource", None)
    if resource is None:
        return
    if isinstance(resource, dict):
        out["buffer"] = resource.get("buffer")
        out["offset"] = resource.get("offset", 0)
        size = resource.get("size")
        out["size"] = size if size is not None else _whole_size(resource.get("buffer"))
        return
    spec_name = getattr(resource, "_spec_name", "")
    if spec_name == "sampler":
        out["sampler"] = resource
    elif spec_name == "texture_view":
        out["texture_view"] = resource
    elif spec_name == "buffer":
        out["buffer"] = resource
        out["offset"] = 0
        out["size"] = _whole_size(resource)
    else:
        raise TypeError(
            f"bind group resource must be a buffer binding, sampler or texture "
            f"view, got {type(resource).__name__}"
        )


def _whole_size(buffer):
    return buffer.size if buffer is not None else 0


#: GLSL declares its stage nowhere the compiler can see, so wgpu-py has always
#: read it off the shader's label. Not a guess: without it the source cannot be
#: compiled at all, and the alternative is refusing GLSL outright.
_GLSL_STAGES = {"comp": "COMPUTE", "vert": "VERTEX", "frag": "FRAGMENT"}


def _shader_source(builder, out: dict, keep: list) -> None:
    """WGSL source is inline on the web; C chains a WGPUShaderSourceWGSL."""
    code = out.pop("code", None)
    if code is None:
        return
    if isinstance(code, str):
        if _looks_like_glsl(code):
            out["_chain"] = ("shader_source_GLSL", _glsl(code, out.get("label") or ""))
        else:
            out["_chain"] = ("shader_source_WGSL", {"code": code})
        return
    # Anything else is SPIR-V: a blob of 32-bit words, which C takes as a word
    # count plus a uint32 pointer rather than as a string.
    view = memoryview(code).cast("B")
    if view.nbytes % 4:
        raise ValueError(
            f"SPIR-V shader source must be a multiple of 4 bytes, got {view.nbytes}"
        )
    out["_chain"] = (
        "shader_source_SPIRV",
        {"code_size": view.nbytes // 4, "code": view.cast("I")},
    )


def _looks_like_glsl(code: str) -> bool:
    """GLSL opens with a ``#version`` line; WGSL has stage attributes."""
    if any(marker in code for marker in ("@compute", "@vertex", "@fragment")):
        return False
    return code.lstrip().startswith("#version ")


def _glsl(code: str, label: str) -> dict:
    from wgpu._generated import apiflags

    # WGPUShaderSourceGLSL is wgpu-native's own, so it is not in webgpu.json
    # and not generated. Registering it here keeps the cost on the GLSL path.
    from wgpu.backends.wgpu_native import native_structs  # noqa: F401

    label = label.lower()
    stage = next((v for k, v in _GLSL_STAGES.items() if k in label), None)
    if stage is None:
        raise ValueError(
            "A GLSL shader must say which stage it is for in its label -- one "
            f"of {sorted(_GLSL_STAGES)}. Got label {label!r}."
        )
    source = {"code": code, "stage": getattr(apiflags.ShaderStage, stage)}
    if stage == "VERTEX":
        # naga spells the vertex index the Vulkan way; GLSL code written for
        # OpenGL says gl_VertexID, so the two are bridged with a define.
        source["defines"] = [{"name": "gl_VertexID", "value": "gl_VertexIndex"}]
    return source


#: Adapters for shapes no declaration in either spec covers, because the
#: correct C form depends on the runtime value: which slot of a union a
#: resource belongs in, and whether shader source is WGSL or SPIR-V.
_ADAPTERS = {
    "bind_group_resource": _bind_group_resource,
    "shader_source": _shader_source,
}
