"""The metaclass behind the generated enum and flag classes.

These are deliberately not ``enum.Enum``: a wgpu enum's members *are* plain
strings and a flag's members *are* plain ints, so they can be compared,
serialised and passed around with no unwrapping. The metaclass supplies what
that costs -- iteration, containment, indexing and a readable repr -- and makes
the classes immutable.
"""

import types


class EnumType(type):
    """Metaclass for wgpu enums and flags."""

    def __new__(cls, name, bases, dct):
        members = {
            key: (key if val is None else val)
            for key, val in dct.items()
            if not key.startswith("_")
        }
        for key, val in members.items():
            if not isinstance(val, (int, str)):
                raise TypeError(f"{name}.{key}: enum fields must be str or int.")
        dct.update(members)
        klass = super().__new__(cls, name, bases, dct)
        klass.__fields__ = tuple(members)
        klass.__members__ = types.MappingProxyType(members)  # enum.Enum compat
        for method in ("__dir__", "__iter__", "__getitem__", "__setattr__", "__repr__"):
            setattr(klass, method, types.MethodType(getattr(cls, method), klass))
        return klass

    def __dir__(cls):
        return cls.__fields__

    def __iter__(cls):
        """Supports ``list(TextureFormat)`` and ``x in TextureFormat``."""
        return iter([getattr(cls, key) for key in cls.__fields__])

    def __getitem__(cls, key):
        return cls.__dict__[key]

    def __repr__(cls):
        if not cls.__fields__:
            return f"<wgpu.{cls.__name__}>"
        options = []
        for key in cls.__fields__:
            val = cls[key]
            options.append(f"'{key}' ({val})" if isinstance(val, int) else f"'{val}'")
        return f"<wgpu.{cls.__name__} enum with options: {', '.join(options)}>"

    def __setattr__(cls, name, value):
        if name.startswith("_"):
            super().__setattr__(name, value)
        else:
            raise RuntimeError("Cannot set values on an enum.")
