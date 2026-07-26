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


class EnumMap(dict):
    """Maps a WebGPU enum string (or a raw int) to its C integer."""

    __slots__ = ("name",)

    def __init__(self, name: str, mapping: dict):
        super().__init__(mapping)
        self.name = name

    def __missing__(self, key):
        if isinstance(key, int) and not isinstance(key, bool):
            return key  # already a C value
        if isinstance(key, str) and "_" in key:
            # wgpu-py has always taken "vertex_writable_storage" for the spec's
            # "vertex-writable-storage". Cached so it only converts once.
            hyphenated = key.replace("_", "-")
            if dict.__contains__(self, hyphenated):
                value = self[hyphenated] = dict.__getitem__(self, hyphenated)
                return value
        options = ", ".join(repr(k) for k in self if isinstance(k, str))
        raise ValueError(f"Invalid value for {self.name}: {key!r}. Expected {options}.")


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
                try:
                    value |= dict.__getitem__(self, part.upper())
                except KeyError:
                    raise ValueError(
                        f"Invalid flag for {self.name}: {part!r}"
                    ) from None
            self._cache[key] = value
            return value
        raise ValueError(f"Invalid value for {self.name}: {key!r}")
