"""Reshape the few values whose Web and C forms genuinely differ.

Most of the public API maps onto C by renaming alone, which the generator does
ahead of time. A handful of fields do not: the web passes a union where C has a
flat struct, keeps a field inline where C chains an extension struct, or nests
where C flattens. Those are listed in ``bindgen.bridge.SHAPE_ADAPTED_FIELDS``,
recorded per struct by the generator, and transformed here.

Each adapter takes the public mapping and returns a C-shaped one. Anything a
spec bump adds that is not handled here fails loudly at generation time rather
than being silently dropped.
"""

from __future__ import annotations


def reshape(builder, desc, mapping: dict, keep: list) -> dict:
    """Apply every adapter this struct declares, returning a C-shaped mapping."""
    out = dict(mapping)
    for field, adapter in desc.adapters:
        if adapter == "ignored":
            # Present on the web, meaningless natively (XR, video textures,
            # view swizzles). Accepted and dropped, so web code still runs.
            out.pop(field, None)
            continue
        _ADAPTERS[adapter](builder, out, keep)
    return out


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


def _shader_source(builder, out: dict, keep: list) -> None:
    """WGSL source is inline on the web; C chains a WGPUShaderSourceWGSL."""
    code = out.pop("code", None)
    if code is None:
        return
    if isinstance(code, str):
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


def _texel_copy_layout(builder, out: dict, keep: list) -> None:
    """The web flattens the layout fields; C nests them in ``layout``."""
    layout = dict(out.get("layout") or {})
    for field in ("offset", "bytes_per_row", "rows_per_image"):
        if field in out:
            value = out.pop(field)
            if value is not None:
                layout[field] = value
    if layout:
        out["layout"] = layout


def _max_draw_count(builder, out: dict, keep: list) -> None:
    """A plain field on the web; a chained struct in C."""
    value = out.pop("max_draw_count", None)
    if value is not None:
        out["_chain"] = ("render_pass_max_draw_count", {"max_draw_count": value})


def _texture_binding_view_dim(builder, out: dict, keep: list) -> None:
    """Pinning a texture's binding view dimension is a chained struct in C."""
    value = out.pop("texture_binding_view_dimension", None)
    if value is not None:
        out["_chain"] = (
            "texture_binding_view_dimension_descriptor",
            {"texture_binding_view_dimension": value},
        )


_ADAPTERS = {
    "bind_group_resource": _bind_group_resource,
    "shader_source": _shader_source,
    "texel_copy_layout": _texel_copy_layout,
    "max_draw_count": _max_draw_count,
    "texture_binding_view_dim": _texture_binding_view_dim,
}
