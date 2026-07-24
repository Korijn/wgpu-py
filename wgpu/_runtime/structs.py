"""Turn a Python mapping into a filled cffi struct, driven by the generated
struct descriptors.

This is the hand-written *runtime* keystone of the high-level layer: the
generated ``wgpu/_generated/structs.py`` says *what* each struct looks like;
this module knows *how* to marshal Python values into the C representation
(primitives, enums/flags, ``WGPUStringView`` strings, nested structs, arrays,
object handles, defaults, optionals).

It deliberately depends only on a cffi ``ffi`` and the generated descriptor /
enum / flag / constant tables -- no wgpu-specific hand-written per-struct code,
so bumping the submodule needs no changes here.
"""

from __future__ import annotations

from typing import Any

_MISSING = object()


class StructBuilder:
    """Marshals Python mappings into cffi structs using generated descriptors.

    Parameters are injected (rather than imported) so the builder is unit
    testable against a freshly-built extension without importing the package.
    """

    def __init__(self, ffi, structs: dict, enums, flags, constants):
        self.ffi = ffi
        self.structs = structs
        self.enums = enums
        self.flags = flags
        self.constants = constants

    # -- public ------------------------------------------------------------

    def new(self, spec_name: str, mapping: dict | None = None):
        """Build a struct and return ``(pointer, keepalive)``.

        ``keepalive`` is a list of cffi objects that must outlive any use of the
        returned pointer (cffi frees owned memory when it is garbage collected).
        """
        keepalive: list[Any] = []
        ptr = self._build_into_new(spec_name, mapping or {}, keepalive)
        return ptr, keepalive

    # -- internals ---------------------------------------------------------

    def _build_into_new(self, spec_name: str, mapping: dict, keep: list):
        desc = self.structs[spec_name]
        ptr = self.ffi.new(desc.c_name + " *")  # zero-initialised
        keep.append(ptr)
        self._fill(ptr, desc, mapping, keep)
        return ptr

    def _fill(self, ptr, desc, mapping: dict, keep: list):
        unknown = set(mapping) - {m.py for m in desc.members}
        if unknown:
            raise TypeError(f"{desc.c_name}: unexpected fields {sorted(unknown)}")
        for mem in desc.members:
            value = mapping.get(mem.py, _MISSING)
            if value is _MISSING:
                value = self._default(mem)
                if value is _MISSING:
                    continue  # leave zero / NULL
            elif value is None and mem.optional:
                continue
            self._set_member(ptr, mem, value, keep)

    def _set_member(self, ptr, mem, value, keep: list):
        field = mem.c
        if mem.array:
            self._set_array(ptr, mem, value, keep)
        elif mem.kind in ("prim", "enum", "bitflag"):
            setattr(ptr, field, self._scalar(mem, value))
        elif mem.kind == "string":
            self._set_string(getattr(ptr, field), value, keep)
        elif mem.kind == "object":
            setattr(ptr, field, value if value is not None else self.ffi.NULL)
        elif mem.kind == "struct":
            self._set_struct(ptr, mem, value, keep)
        elif mem.kind in ("callback", "c_void", "out_string"):
            return  # not built from plain input mappings
        else:  # pragma: no cover
            raise TypeError(f"cannot marshal member kind {mem.kind!r} ({mem.c})")

    # -- per-kind helpers --------------------------------------------------

    def _scalar(self, mem, value):
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return value
        # allow passing enum/flag members by their generated member name
        raise TypeError(f"{mem.c}: expected number, got {type(value).__name__}")

    def _set_string(self, view, value, keep: list):
        if value is None:
            view.data = self.ffi.NULL
            view.length = 0
            return
        data = self.ffi.new("char[]", value.encode("utf-8"))
        keep.append(data)
        view.data = data
        view.length = len(value.encode("utf-8"))

    def _set_struct(self, ptr, mem, value, keep: list):
        child = self._build_into_new(mem.ref, value or {}, keep)
        if mem.pointer:  # field is a pointer to the struct
            setattr(ptr, mem.c, child)
        else:  # field embeds the struct by value
            setattr(ptr, mem.c, child[0])

    def _set_array(self, ptr, mem, value, keep: list):
        items = list(value)
        n = len(items)
        setattr(ptr, mem.count_c, n)
        if n == 0:
            setattr(ptr, mem.c, self.ffi.NULL)
            return
        # Element ctype comes straight from the real C field (a pointer type),
        # so enum/object/prim/struct arrays are all handled uniformly.
        struct_type = self.ffi.typeof(ptr).item
        field_type = dict(struct_type.fields)[mem.c].type
        elem_ctype = self.ffi.getctype(field_type.item)

        if mem.kind == "struct":  # array of struct *values*
            arr = self.ffi.new(f"{elem_ctype}[{n}]")
            child_desc = self.structs[mem.ref]
            for i, item in enumerate(items):
                self._fill(self.ffi.addressof(arr, i), child_desc, item or {}, keep)
        elif mem.kind == "object":  # array of handles
            arr = self.ffi.new(
                f"{elem_ctype}[{n}]", [it or self.ffi.NULL for it in items]
            )
        else:  # enum / prim array
            arr = self.ffi.new(f"{elem_ctype}[{n}]", [int(it) for it in items])
        keep.append(arr)
        setattr(ptr, mem.c, arr)

    # -- defaults ----------------------------------------------------------

    def _default(self, mem):
        d = mem.default
        if d is None:
            return _MISSING
        if isinstance(d, bool) or isinstance(d, (int, float)):
            return d
        if isinstance(d, str):
            if d.startswith("constant."):
                return getattr(self.constants, d.split(".", 1)[1])
            if d.startswith("0x"):
                return int(d, 16)
            # else: an enum/flag member name for this member's type
            if mem.kind == "enum":
                cls = getattr(self.enums, _py_type_name(mem.ref))
                return int(getattr(cls, _member(d)))
            if mem.kind == "bitflag":
                cls = getattr(self.flags, _py_type_name(mem.ref))
                return int(getattr(cls, _member(d)))
        return _MISSING


def _py_type_name(spec_name: str) -> str:
    from bindgen import naming

    return naming.py_enum_name(spec_name)


def _member(name: str) -> str:
    from bindgen import naming

    return naming.py_enum_member(name)
