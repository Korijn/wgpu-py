"""Base class for the generated struct dataclasses.

Structs subclass ``Mapping`` so they can be splatted as ``**struct`` and passed
anywhere a plain dict is accepted -- which is everywhere, since the runtime
struct builder only ever asks for items.
"""

from collections.abc import Mapping


class Struct(Mapping):
    def __repr__(self):
        return self._repr()

    def _repr(self, indent=""):
        new_indent = indent + "    "
        lines = [f"wgpu.{self.__class__.__name__}("]
        for name, type in self.__annotations__.items():
            if type.startswith(("flags.", "enums.")):
                type = type.split(".")[-1]
            val = self[name]
            valr = val._repr(new_indent) if isinstance(val, Struct) else repr(val)
            lines.append(f"{new_indent}{name}: {type} = {valr}")
        lines.append(f"{indent})")
        return "\n".join(lines)

    def __getitem__(self, key):
        return self.__dict__[key]

    def __iter__(self):
        return iter(self.__dict__)

    def __len__(self):
        return len(self.__dict__)

    def get(self, key, default=None):
        """Like ``dict.get``, but a stored ``None`` also yields the default."""
        val = self.__dict__[key]  # unknown keys are still an error
        return default if val is None else val
