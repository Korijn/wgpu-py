"""Small Python-side objects that carry information rather than a GPU handle.

None of these map to a wgpu-native object, so none of them are generated: they
exist to give a nicer shape to values that the C API reports as plain structs.
"""

from __future__ import annotations


class GPUAdapterInfo(dict):
    """Information about an adapter: who made it, and what it runs on.

    A dict for backwards compatibility -- it has always been indexable -- with
    the spec's fields also exposed as attributes.
    """

    @property
    def vendor(self) -> str:
        """The adapter's vendor name."""
        return self.get("vendor", "")

    @property
    def architecture(self) -> str:
        """The adapter's architecture family."""
        return self.get("architecture", "")

    @property
    def device(self) -> str:
        """The adapter's device name."""
        return self.get("device", "")

    @property
    def description(self) -> str:
        """A human-readable description of the adapter."""
        return self.get("description", "")

    @property
    def is_fallback_adapter(self) -> bool:
        """Whether this is a (probably software) fallback adapter."""
        return bool(self.get("is_fallback_adapter", False))

    @property
    def subgroup_min_size(self) -> int:
        """The smallest supported subgroup size."""
        return self.get("subgroup_min_size", 0)

    @property
    def subgroup_max_size(self) -> int:
        """The largest supported subgroup size."""
        return self.get("subgroup_max_size", 0)

    @property
    def summary(self) -> str:
        """A one-line description, handy in logs and bug reports."""
        device = self.get("device") or self.get("description") or "unknown"
        return (
            f"{device} ({self.get('adapter_type', '')}) "
            f"via {self.get('backend_type', '')}"
        )


class GPUCompilationMessage:
    """One diagnostic emitted while compiling a shader module."""

    def __init__(self, info: dict):
        self._info = info

    @property
    def message(self) -> str:
        """The human-readable message."""
        return self._info.get("message", "")

    @property
    def type(self) -> str:
        """Whether this is an 'error', 'warning' or 'info'."""
        return self._info.get("type", "error")

    @property
    def line_num(self) -> int:
        """The 1-based line the message refers to."""
        return self._info.get("line_num", 0)

    @property
    def line_pos(self) -> int:
        """The 1-based column the message refers to."""
        return self._info.get("line_pos", 0)

    @property
    def offset(self) -> int:
        """The byte offset into the shader source."""
        return self._info.get("offset", 0)

    @property
    def length(self) -> int:
        """The length in bytes of the offending source range."""
        return self._info.get("length", 0)

    def __repr__(self):
        return f"<wgpu.GPUCompilationMessage {self.type} {self.message!r}>"


class GPUCompilationInfo:
    """The set of messages produced while compiling a shader module."""

    def __init__(self, messages=()):
        self._messages = [
            m if isinstance(m, GPUCompilationMessage) else GPUCompilationMessage(m)
            for m in messages
        ]

    @property
    def messages(self) -> list:
        """The individual `GPUCompilationMessage` objects."""
        return list(self._messages)

    def __repr__(self):
        return f"<wgpu.GPUCompilationInfo with {len(self._messages)} messages>"


class GPUDeviceLostInfo:
    """Why a device was lost."""

    def __init__(self, reason: str, message: str):
        self._reason = reason
        self._message = message

    @property
    def reason(self) -> str:
        """The reason the device was lost, e.g. 'destroyed'."""
        return self._reason

    @property
    def message(self) -> str:
        """A human-readable explanation."""
        return self._message

    def __repr__(self):
        return f"<wgpu.GPUDeviceLostInfo {self._reason}: {self._message!r}>"
