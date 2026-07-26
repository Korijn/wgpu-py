"""Value maps used to turn public API values into the integers C wants.

Both maps are plain ``dict`` subclasses, so the common path -- a valid value --
is a single C-level dict lookup with no Python-level function call. The only
custom code runs on ``__missing__``, i.e. when the value is wrong and we are
about to raise anyway.

They accept more than the canonical spelling on purpose, because the classic
wgpu-py API did: an already-resolved integer passes through, and flags accept
``"MAP_READ|COPY_DST"`` as well as ``MapMode.READ | MapMode.WRITE``.
"""

from __future__ import annotations

from wgpu._runtime.errors import InvalidValueError


def spec_spelling(key: str) -> str:
    """``vertex_writable_storage`` / ``VertexWritableStorage`` -> hyphenated.

    One rule covers the three spellings wgpu-py has always accepted for the
    same name: the spec's own ``hyphen-case``, the ``snake_case`` that reads
    naturally in Python, and the ``CamelCase`` that matches wgpu-native's own
    header. Only reached on a miss, so the common path never pays for it.
    """
    out = []
    for i, ch in enumerate(key):
        if ch.isupper() and i and key[i - 1] not in "_-":
            out.append("-")
        out.append(ch.lower())
    return "".join(out).replace("_", "-")


class EnumMap(dict):
    """Maps a WebGPU enum string (or a raw int) to its C integer."""

    __slots__ = ("name",)

    def __init__(self, name: str, mapping: dict):
        super().__init__(mapping)
        self.name = name

    def __missing__(self, key):
        if isinstance(key, int) and not isinstance(key, bool):
            return key  # already a C value
        if isinstance(key, str):
            # wgpu-py has always taken "vertex_writable_storage" and
            # "VertexWritableStorage" for the spec's "vertex-writable-storage".
            # Cached under the original spelling, so it converts only once.
            hyphenated = spec_spelling(key)
            if dict.__contains__(self, hyphenated):
                value = self[key] = dict.__getitem__(self, hyphenated)
                return value
        options = ", ".join(repr(k) for k in self if isinstance(k, str))
        raise InvalidValueError(
            f"Invalid value for {self.name}: {key!r}. Expected {options}."
        )


class FlagMap(dict):
    """Maps a flag name, ``'A|B'`` string, or int to its integer value."""

    __slots__ = ("name", "_cache")

    def __init__(self, name: str, mapping: dict):
        super().__init__(mapping)
        self.name = name
        # Combined strings are resolved once and then hit the fast path.
        self._cache: dict[str, int] = {}

    def __missing__(self, key):
        if isinstance(key, int) and not isinstance(key, bool):
            return key
        if isinstance(key, str):
            try:
                return self._cache[key]
            except KeyError:
                pass
            value = 0
            for part in key.split("|"):
                part = part.strip()
                if not part:
                    continue
                # dict.get, not dict.__getitem__: the latter would come back
                # through __missing__ and recurse on an unknown name.
                one = dict.get(self, part.upper())
                if one is None:
                    raise InvalidValueError(
                        f"Invalid flag for {self.name}: {part!r}"
                    )
                value |= one
            self._cache[key] = value
            return value
        raise InvalidValueError(f"Invalid value for {self.name}: {key!r}")
