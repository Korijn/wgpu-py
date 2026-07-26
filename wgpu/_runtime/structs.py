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

    def __init__(self, ffi, structs: dict, constants, enums, flags):
        self.ffi = ffi
        self.structs = structs
        self.constants = constants
        # Public value maps: strings in, C integers out (and back again).
        self.enums = enums
        self.flags = flags

    # -- public ------------------------------------------------------------

    def new(self, spec_name: str, mapping: dict | None = None):
        """Build a struct and return ``(pointer, keepalive)``.

        ``keepalive`` is a list of cffi objects that must outlive any use of the
        returned pointer (cffi frees owned memory when it is garbage collected).
        """
        keepalive: list[Any] = []
        ptr = self._build_into_new(spec_name, mapping or {}, keepalive)
        return ptr, keepalive

    def read(self, spec_name: str, ptr) -> dict:
        """Read a C struct back into a Python dict of public values.

        The inverse of :meth:`new`, driven by the same descriptors. Needed for
        the getters that report through an out-parameter -- ``adapter.limits``,
        ``adapter.info``, ``device.features`` -- where wgpu-native fills in a
        struct the caller allocated.
        """
        desc = self.structs[spec_name]
        out: dict = {}
        for mem in desc.members:
            value = getattr(ptr, mem.c, None)
            if mem.array:
                out[mem.py] = self._read_array(ptr, mem)
            elif mem.kind in ("string", "out_string"):
                # out_string is a WGPUStringView the implementation fills in --
                # adapter vendor/device/description arrive this way.
                out[mem.py] = _string(self.ffi, value)
            elif mem.kind == "enum":
                out[mem.py] = self.enums.FROM_INT.get(mem.ref, {}).get(
                    int(value), int(value)
                )
            elif mem.kind == "bitflag":
                out[mem.py] = int(value)
            elif mem.kind == "struct":
                child = value[0] if mem.pointer else value
                out[mem.py] = self.read(mem.ref, child)
            elif mem.kind == "prim":
                out[mem.py] = bool(value) if mem.ref == "bool" else value
            elif mem.kind == "object":
                out[mem.py] = value if value else None
        return out

    def _read_array(self, ptr, mem):
        n = getattr(ptr, mem.count_c, 0)
        data = getattr(ptr, mem.c)
        if not n or not data:
            return []
        if mem.kind == "enum":
            table = self.enums.FROM_INT.get(mem.ref, {})
            return [table.get(int(data[i]), int(data[i])) for i in range(n)]
        if mem.kind == "struct":
            return [self.read(mem.ref, data[i]) for i in range(n)]
        return [data[i] for i in range(n)]

    # -- internals ---------------------------------------------------------

    def _build_into_new(self, spec_name: str, mapping: dict, keep: list):
        desc = self.structs[spec_name]
        ptr = self.ffi.new(desc.c_name + " *")  # zero-initialised
        keep.append(ptr)
        self._fill(ptr, desc, mapping, keep)
        return ptr

    def _fill(self, ptr, desc, mapping, keep: list):
        if not hasattr(mapping, "keys"):
            # The spec lets the small geometric structs be written positionally
            # -- size=(64, 64, 1), color=(0, 0, 0, 1), origin=(0, 0) -- so a
            # sequence maps onto the leading members, in declaration order.
            values = list(mapping)
            if len(values) > len(desc.members):
                raise ValueError(
                    f"{desc.c_name}: got {len(values)} values but the struct has "
                    f"{len(desc.members)} fields"
                )
            mapping = {m.py: v for m, v in zip(desc.members, values)}
        elif any("-" in k for k in mapping):
            # wgpu-py has always accepted the WebGPU spec's hyphenated spelling
            # of struct fields ("max-bind-groups") alongside the Pythonic one.
            mapping = {k.replace("-", "_"): v for k, v in mapping.items()}
        if desc.adapters:
            from wgpu._api import adapt

            mapping = adapt.reshape(self, desc, mapping, keep)
        chain = mapping.pop("_chain", None) if "_chain" in mapping else None
        if chain is not None:
            self._chain(ptr, chain, keep)
        unknown = set(mapping) - {m.py for m in desc.members}
        if unknown:
            raise TypeError(f"{desc.c_name}: unexpected fields {sorted(unknown)}")
        for mem in desc.members:
            value = mapping.get(mem.py, _MISSING)
            if value is None:
                # An explicit None means "not specified", exactly like omitting
                # the field -- which is what ``undefined`` means on the web.
                value = _MISSING
            if value is _MISSING:
                value = self._default(mem)
                if value is _MISSING:
                    continue  # leave zero / NULL
            self._set_member(ptr, mem, value, keep)

    def _set_member(self, ptr, mem, value, keep: list):
        field = mem.c
        if mem.array:
            self._set_array(ptr, mem, value, keep)
        elif mem.kind == "enum":
            setattr(ptr, field, self.enums.TO_INT[mem.ref][value])
        elif mem.kind == "bitflag":
            setattr(ptr, field, self.flags.TO_INT[mem.ref][value])
        elif mem.kind == "prim":
            setattr(ptr, field, self._scalar(mem, value))
        elif mem.kind == "string":
            self._set_string(getattr(ptr, field), value, keep)
        elif mem.kind == "object":
            setattr(ptr, field, self._handle(value))
        elif mem.kind == "struct":
            self._set_struct(ptr, mem, value, keep)
        elif mem.kind == "callback":
            self._set_callback(ptr, mem, value, keep)
        elif mem.kind in ("c_void", "out_string"):
            return  # not built from plain input mappings
        else:  # pragma: no cover
            raise TypeError(f"cannot marshal member kind {mem.kind!r} ({mem.c})")

    def _chain(self, ptr, chain, keep: list):
        """Attach an extension struct to ``ptr``'s ``nextInChain``.

        C expresses several web-level fields as chained structs, each tagged
        with an ``sType`` so the implementation knows what it received.
        """
        spec_name, mapping = chain
        desc = self.structs[spec_name]
        child = self.ffi.new(desc.c_name + " *")
        keep.append(child)
        child.chain.sType = desc.s_type
        self._fill(child, desc, mapping, keep)
        ptr.nextInChain = self.ffi.addressof(child.chain)

    # -- per-kind helpers --------------------------------------------------

    def _scalar(self, mem, value):
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return value
        # allow passing enum/flag members by their generated member name
        raise TypeError(f"{mem.c}: expected number, got {type(value).__name__}")

    def _handle(self, value):
        """The C handle behind a public object, or NULL."""
        if value is None:
            return self.ffi.NULL
        return getattr(value, "_handle", value)

    def _set_string(self, view, value, keep: list):
        if value is None:
            view.data = self.ffi.NULL
            view.length = 0
            return
        data = self.ffi.new("char[]", value.encode("utf-8"))
        keep.append(data)
        view.data = data
        view.length = len(value.encode("utf-8"))

    def _set_callback(self, ptr, mem, value, keep: list):
        """Fill an embedded ``*CallbackInfo`` struct.

        ``value`` is either a cffi callback, or a mapping of the info struct's
        own fields (e.g. ``{"callback": cb, "mode": ...}``) for the infos that
        also carry a callback mode.
        """
        if value is None:
            return
        info = getattr(ptr, mem.c)
        fields = {f[0] for f in self.ffi.typeof(info).fields}
        if not isinstance(value, dict):
            value = {"callback": value}
        for name, item in value.items():
            if name not in fields:
                raise TypeError(f"{mem.c} has no field {name!r}")
            setattr(info, name, item)
            keep.append(item)

    def _set_struct(self, ptr, mem, value, keep: list):
        child = self._build_into_new(mem.ref, {} if value is None else value, keep)
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
                f"{elem_ctype}[{n}]", [self._handle(it) for it in items]
            )
        elif mem.kind == "enum":  # array of enums, given as public strings
            table = self.enums.TO_INT[mem.ref]
            arr = self.ffi.new(f"{elem_ctype}[{n}]", [table[it] for it in items])
        else:  # prim array
            arr = self.ffi.new(f"{elem_ctype}[{n}]", [int(it) for it in items])
        keep.append(arr)
        setattr(ptr, mem.c, arr)

    # -- defaults ----------------------------------------------------------

    def _default(self, mem):
        # Enum/bitflag defaults are already resolved to ints by the generator;
        # only the size-dependent ``constant.*`` sentinels stay symbolic.
        d = mem.default
        if d is None:
            return _MISSING
        if isinstance(d, (bool, int, float)):
            return d
        if isinstance(d, str):
            if d.startswith("constant."):
                return getattr(self.constants, d.split(".", 1)[1])
            if d.startswith("0x"):
                return int(d, 16)
        return _MISSING


def _string(ffi, view) -> str:
    """Decode a ``WGPUStringView`` (data + length, not NUL-terminated)."""
    if not view.data:
        return ""
    return ffi.string(view.data, view.length).decode("utf-8", "replace")
