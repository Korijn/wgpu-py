"""Bridge the two specs: W3C Web IDL names <-> wgpu-native C-spec names.

The public API is shaped by ``webgpu.idl`` while the FFI layer is shaped by
``webgpu.json``. They describe the same objects but spell them differently:

======================  ======================  ======================
concept                 IDL                     C spec (webgpu.json)
======================  ======================  ======================
class / object          ``GPUDevice``           ``device``
method                  ``createBuffer``        ``create_buffer``
struct                  ``BufferDescriptor``    ``buffer_descriptor``
struct field            ``mappedAtCreation``    ``mapped_at_creation``
enum type               ``TextureFormat``       ``texture_format``
enum value              ``"rgba8unorm"``        ``RGBA8_unorm``
enum value              ``"2d-array"``          ``2D_array``
======================  ======================  ======================

Every correspondence here is derived by rule, never by a hand-maintained table,
so bumping either spec cannot silently desynchronise them. What *is* recorded
by hand is the small set of names that genuinely exist on only one side (see
:data:`IDL_ONLY_ENUMS`); anything else that fails to match is reported by
:func:`report`, which the test suite asserts on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import paths
from .idl import get_idl_parser

#: Enums that the Web API defines but the C API has no counterpart for, because
#: the concept does not exist at the C level (canvas presentation is handled by
#: the surface API) or is expressed differently (``auto`` layout is a null
#: pointer in C; pipeline-error reasons come from the error callback).
IDL_ONLY_ENUMS = {
    "AutoLayoutMode",
    "CanvasAlphaMode",
    "CanvasToneMappingMode",
    "PipelineErrorReason",
}

#: IDL classes that are not wgpu-native objects. They are either pure Python
#: constructs (errors, info bags, mixins) or sit on top of a *different* C
#: object -- ``GPUCanvasContext`` wraps a ``surface``, which the IDL does not
#: model at all because on the web the canvas plays that role.
IDL_ONLY_CLASSES = {
    "GPU",
    "GPUAdapterInfo",
    "GPUCanvasContext",
    "GPUCompilationInfo",
    "GPUCompilationMessage",
    "GPUDeviceLostInfo",
    "GPUError",
    "GPUInternalError",
    "GPUOutOfMemoryError",
    "GPUPipelineError",
    "GPUValidationError",
    "GPUObjectBase",
    "GPUBindingCommandsMixin",
    "GPUCommandsMixin",
    "GPUDebugCommandsMixin",
    "GPURenderCommandsMixin",
    "GPUPipelineBase",
}


#: Structs the Web API defines that wgpu-native has no equivalent for. Most are
#: about the browser's image/canvas pipeline, which does not exist natively.
IDL_ONLY_STRUCTS = {
    "ExternalTextureDescriptor",
    "CopyExternalImageDestInfo",
    "CopyExternalImageSourceInfo",
    "PipelineErrorInit",
    "UncapturedErrorEventInit",
    "CanvasToneMapping",
    "CanvasConfiguration",
    "Origin2D",  # C only has origin_3D; the 2D form is a web convenience
    "BufferBinding",  # part of the BindGroupEntry.resource union, see below
    "ShaderModuleCompilationHint",  # accepted and ignored, as on the web
}

#: Structs that exist on both sides under names too different for the
#: normalising rule to see. Kept explicit (and asserted by the tests) rather
#: than pattern-matched, because guessing here would be worse than declaring.
STRUCT_ALIASES = {
    "ProgrammableStage": "compute_state",
    "ComputePassTimestampWrites": "pass_timestamp_writes",
    "RenderPassTimestampWrites": "pass_timestamp_writes",
    "RenderPassLayout": "render_bundle_encoder_descriptor",
}

#: Struct fields whose *shape* differs between the two specs, so no name can
#: connect them: the runtime has to transform the value. Each entry names the
#: adapter in ``wgpu._api.adapt`` that performs the transform, and exists so an
#: unhandled mismatch is a hard failure rather than a silently dropped field.
SHAPE_ADAPTED_FIELDS = {
    # IDL passes a union (buffer | {buffer,offset,size} | sampler | textureView);
    # C has one flat struct with a slot per resource kind. No declaration in
    # either spec says which slot a given value belongs in -- that is read off
    # the Python object's type at runtime.
    ("BindGroupEntry", "resource"): "bind_group_resource",
    # `code` chains, and the chaining itself is derivable -- but *two* extension
    # structs offer a `code` member (WGSL and SPIR-V), so which one applies is
    # decided by the value's Python type, not by the specs.
    ("ShaderModuleDescriptor", "code"): "shader_source",
    ("ShaderModuleDescriptor", "compilationHints"): "ignored",
    # Web-only: XR presentation, texture-view swizzling, video textures.
    ("RequestAdapterOptions", "xrCompatible"): "ignored",
    ("TextureViewDescriptor", "swizzle"): "ignored",
    ("BindGroupLayoutEntry", "externalTexture"): "ignored",
}
# Everything else that once lived here -- maxDrawCount, textureBindingViewDimension
# and the three TexelCopyBufferInfo layout fields -- is now derived by
# _derive_adapter() below, from declarations webgpu.json already makes.

#: Methods that only make sense on the web (they take browser image sources).
IDL_ONLY_METHODS = {
    ("GPUDevice", "importExternalTexture"),
    ("GPUQueue", "copyExternalImageToTexture"),
}


def _key(name: str) -> str:
    """Normalise a name so the two spellings of the same thing collide.

    ``"2d-array"`` and ``"2D_array"`` both become ``2darray``; ``"rgba8unorm"``
    and ``"RGBA8_unorm"`` both become ``rgba8unorm``.
    """
    return name.lower().replace("_", "").replace("-", "")


def camel_to_snake(name: str) -> str:
    """``createBindGroupLayout`` -> ``create_bind_group_layout``.

    Digits stay attached to the word they follow (``copyBufferToTexture`` and
    ``draw3`` behave), and runs of capitals are kept together so ``toGPUBuffer``
    does not explode into single letters.
    """
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper():
            prev = name[i - 1] if i else ""
            nxt = name[i + 1] if i + 1 < len(name) else ""
            starts_word = prev and (not prev.isupper() or (nxt and nxt.islower()))
            if starts_word:
                out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out).lstrip("_")


def _derive_adapter(spec_structs: dict, c_struct: str, field: str):
    """Work out where a field with no matching C member actually goes.

    webgpu.json says more than "here are the members". It declares which
    structs are extensions and what they ``extends``, and it gives every member
    a type -- so when the IDL has a field the C struct lacks, the C spec itself
    usually says where it went:

    * an extension struct that extends this one and carries the field
      (``maxDrawCount`` -> ``WGPURenderPassMaxDrawCount``); or
    * a member of this struct whose own struct type carries the field
      (``bytesPerRow`` -> the nested ``layout``).

    Returns ``(adapter, target)`` when exactly one candidate exists, else None
    -- ambiguity is not resolved by guessing, it is declared in
    :data:`SHAPE_ADAPTED_FIELDS` and handled at runtime.
    """
    wanted = _key(field)

    chain = [
        st["name"]
        for st in spec_structs.values()
        if st.get("type") == "extension"
        and c_struct in (st.get("extends") or ())
        and wanted in {_key(m["name"]) for m in st.get("members", ())}
    ]
    nest = []
    for mem in spec_structs.get(c_struct, {}).get("members", ()):
        type_ = mem.get("type", "")
        if not type_.startswith("struct."):
            continue
        inner = spec_structs.get(type_.split(".", 1)[1], {})
        if wanted in {_key(m["name"]) for m in inner.get("members", ())}:
            nest.append(mem["name"])

    if len(chain) == 1 and not nest:
        return "chain", chain[0]
    if len(nest) == 1 and not chain:
        return "nest", nest[0]
    return None


@dataclass
class Bridge:
    """Resolved correspondences between the IDL and the C spec."""

    idl: object
    spec: dict

    #: IDL enum class name -> C spec enum name, e.g. ``TextureFormat`` ->
    #: ``texture_format``.
    enum_types: dict[str, str] = field(default_factory=dict)
    #: (IDL enum class, IDL string value) -> C spec entry name.
    enum_values: dict[tuple[str, str], str] = field(default_factory=dict)
    #: IDL flag class name -> C spec bitflag name.
    flag_types: dict[str, str] = field(default_factory=dict)
    #: IDL struct name -> C spec struct name.
    structs: dict[str, str] = field(default_factory=dict)
    #: (IDL struct, IDL field) -> C spec member name.
    struct_fields: dict[tuple[str, str], str] = field(default_factory=dict)
    #: IDL class name -> C spec object name, e.g. ``GPUDevice`` -> ``device``.
    classes: dict[str, str] = field(default_factory=dict)
    #: (IDL class, IDL method) -> C spec method name.
    methods: dict[tuple[str, str], str] = field(default_factory=dict)
    #: (IDL struct, IDL field) -> ``(adapter, target)``. The adapter names the
    #: runtime reshaping in :mod:`wgpu._api.adapt`; the target is the thing it
    #: acts on -- the extension struct to chain or the member to nest under --
    #: and is empty for adapters that need no target.
    shape_adapted: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    #: Subset of ``shape_adapted`` that was derived from webgpu.json rather
    #: than declared in :data:`SHAPE_ADAPTED_FIELDS`. Reported by the generator.
    derived_adapters: dict[tuple[str, str], tuple[str, str]] = field(
        default_factory=dict
    )

    #: Things present in the IDL with no C counterpart, as
    #: ``(category, name)`` pairs. Expected to be empty apart from the
    #: documented IDL-only sets.
    unmatched: list[tuple[str, str]] = field(default_factory=list)


def build() -> Bridge:
    """Resolve every IDL name against the C spec."""
    idl = get_idl_parser()
    spec = json.loads(paths.WEBGPU_JSON.read_text())
    b = Bridge(idl=idl, spec=spec)

    spec_enums = {e["name"]: e for e in spec["enums"]}
    spec_flags = {f["name"]: f for f in spec["bitflags"]}
    spec_structs = {s["name"]: s for s in spec["structs"]}
    spec_objects = {o["name"]: o for o in spec["objects"]}

    enums_by_key = {_key(n): n for n in spec_enums}
    flags_by_key = {_key(n): n for n in spec_flags}
    structs_by_key = {_key(n): n for n in spec_structs}
    objects_by_key = {_key(n): n for n in spec_objects}

    # -- enums ------------------------------------------------------------
    for idl_name, members in idl.enums.items():
        if idl_name in IDL_ONLY_ENUMS:
            continue
        spec_name = enums_by_key.get(_key(idl_name))
        if spec_name is None:
            b.unmatched.append(("enum", idl_name))
            continue
        b.enum_types[idl_name] = spec_name
        entries = {
            _key(e["name"]): e["name"]
            for e in spec_enums[spec_name]["entries"]
            if e is not None
        }
        for value in members.values():
            entry = entries.get(_key(value))
            if entry is None:
                b.unmatched.append(("enum-value", f"{idl_name}.{value}"))
            else:
                b.enum_values[(idl_name, value)] = entry

    # -- flags ------------------------------------------------------------
    for idl_name in idl.flags:
        # The IDL calls it ``ColorWrite``; the C spec ``color_write_mask``.
        spec_name = flags_by_key.get(_key(idl_name)) or flags_by_key.get(
            _key(idl_name + "_mask")
        )
        if spec_name is None:
            b.unmatched.append(("flag", idl_name))
        else:
            b.flag_types[idl_name] = spec_name

    # -- structs ----------------------------------------------------------
    for idl_name, fields in idl.structs.items():
        if idl_name in IDL_ONLY_STRUCTS:
            continue
        spec_name = STRUCT_ALIASES.get(idl_name) or structs_by_key.get(_key(idl_name))
        if spec_name is None:
            b.unmatched.append(("struct", idl_name))
            continue
        assert spec_name in spec_structs, f"alias points at unknown struct {spec_name}"
        b.structs[idl_name] = spec_name
        members = {
            _key(m["name"]): m["name"]
            for m in spec_structs[spec_name].get("members", [])
        }
        for fname in fields:
            declared = SHAPE_ADAPTED_FIELDS.get((idl_name, fname))
            if declared is not None:
                b.shape_adapted[(idl_name, fname)] = (declared, "")
                continue
            member = members.get(_key(fname))
            if member is not None:
                b.struct_fields[(idl_name, fname)] = member
                continue
            # No member of that name -- but the C spec may say where it went.
            derived = _derive_adapter(spec_structs, spec_name, fname)
            if derived is None:
                b.unmatched.append(("struct-field", f"{idl_name}.{fname}"))
            else:
                b.shape_adapted[(idl_name, fname)] = derived
                b.derived_adapters[(idl_name, fname)] = derived

    # -- classes & methods -------------------------------------------------
    for idl_name, interface in idl.classes.items():
        if idl_name in IDL_ONLY_CLASSES:
            continue
        spec_name = objects_by_key.get(_key(idl_name[3:]))  # strip the GPU prefix
        if spec_name is None:
            b.unmatched.append(("class", idl_name))
            continue
        b.classes[idl_name] = spec_name
        meths = {_key(m["name"]) for m in spec_objects[spec_name].get("methods", [])}
        for fname in interface.functions:
            if (idl_name, fname) in IDL_ONLY_METHODS:
                continue
            snake = camel_to_snake(fname)
            if _key(snake) in meths:
                b.methods[(idl_name, fname)] = snake
            else:
                b.unmatched.append(("method", f"{idl_name}.{fname}"))
    return b


def report() -> str:
    """Human-readable summary, used by the generator and by the tests."""
    b = build()
    lines = [
        f"enums        {len(b.enum_types):>4} types, {len(b.enum_values)} values",
        f"flags        {len(b.flag_types):>4} types",
        f"structs      {len(b.structs):>4} types, {len(b.struct_fields)} fields",
        f"classes      {len(b.classes):>4} types, {len(b.methods)} methods",
        f"shape-adapted{len(b.shape_adapted):>4} struct fields",
    ]
    if b.unmatched:
        lines.append(f"UNMATCHED ({len(b.unmatched)}):")
        lines += [f"  {cat:<12} {name}" for cat, name in b.unmatched]
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
