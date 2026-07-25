"""Thin compatibility layer presenting the classic wgpu-py API.

Everything real lives in the generated layer (``wgpu._generated``) and the small
runtime (``wgpu._runtime``); this module only adapts *ergonomics*:

* keyword arguments instead of descriptor dicts,
* classic enum/flag spellings (``"clamp-to-edge"``, ``BufferUsage.COPY_SRC``),
* a handful of convenience methods that are not in the C API at all
  (``create_buffer_with_data``, ``queue.read_buffer``, ``map_sync``, ...).

It is deliberately small and hand-written: bumping the wgpu-native submodule
regenerates the layers underneath without touching this file.
"""

from __future__ import annotations

from wgpu._generated import enums, flags
from wgpu._runtime.api import get_api

__all__ = ["enum_value", "get_default_device", "request_adapter_sync"]


# ---- classic enum/flag spellings -------------------------------------------

def _norm(name: str) -> str:
    """``"clamp-to-edge"``, ``"CLAMP_TO_EDGE"`` and ``clamp_to_edge`` all match."""
    return name.replace("-", "").replace("_", "").lower()


_lookup_cache: dict[type, dict[str, object]] = {}


def enum_value(cls, value):
    """Resolve ``value`` (member, int, or classic string) for enum/flag ``cls``."""
    if isinstance(value, cls):
        return int(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        table = _lookup_cache.get(cls)
        if table is None:
            table = {_norm(m.name): int(m) for m in cls}
            _lookup_cache[cls] = table
        total = 0
        for part in value.split("|"):  # flags may be "MAP_READ|COPY_DST"
            key = _norm(part.strip())
            if key not in table:
                raise ValueError(f"{cls.__name__} has no member {part!r}")
            total |= table[key]
        return total
    raise TypeError(f"cannot interpret {value!r} as {cls.__name__}")


# ---- wrappers ---------------------------------------------------------------

class _Wrapper:
    """Base for the classic-API wrappers around generated objects."""

    def __init__(self, inner):
        self._inner = inner

    @property
    def _internal(self):  # classic attribute name for the raw handle
        return self._inner._handle

    def __repr__(self):
        return f"<wgpu.{type(self).__name__} {self._inner._handle}>"


class GPUBuffer(_Wrapper):
    def __init__(self, inner, size: int, usage: int, device=None):
        super().__init__(inner)
        self._size = size
        self._usage = usage
        self._device = device

    @property
    def size(self) -> int:
        return self._size

    @property
    def usage(self) -> int:
        return self._usage

    def map_sync(self, mode="READ", offset: int = 0, size: int | None = None) -> None:
        size = self._size - offset if size is None else size
        if self._device is not None:
            self._device._poll()  # flush pending device work first
        status = self._inner.map_async(
            enum_value(flags.MapMode, mode), offset, size
        ).wait()
        if status != 1:
            raise RuntimeError(f"failed to map buffer (status {status})")
        if self._device is not None:
            self._device._poll()
        self._mapped = (offset, size)

    def read_mapped(self, offset: int | None = None, size: int | None = None):
        moffset, msize = getattr(self, "_mapped", (0, self._size))
        offset = moffset if offset is None else offset
        size = msize if size is None else size
        return self._inner.get_mapped_range(offset, size)

    def write_mapped(self, data, offset: int | None = None) -> None:
        moffset, msize = getattr(self, "_mapped", (0, self._size))
        offset = moffset if offset is None else offset
        view = self._inner.get_mapped_range(offset, len(data))
        view[:] = bytes(data)

    def unmap(self) -> None:
        self._inner.unmap()


class GPUQueue(_Wrapper):
    def __init__(self, inner, device):
        super().__init__(inner)
        self._device = device

    def write_buffer(self, buffer, buffer_offset, data, data_offset=0, size=None):
        mv = memoryview(data).cast("B")
        if size is None:
            size = len(mv) - data_offset
        self._inner.write_buffer(
            buffer._inner, buffer_offset, mv[data_offset : data_offset + size], size
        )

    def submit(self, command_buffers):
        self._inner.submit([cb._inner if isinstance(cb, _Wrapper) else cb for cb in command_buffers])

    def read_buffer(self, buffer, buffer_offset: int = 0, size: int | None = None):
        """Copy buffer contents to the CPU and return them as a memoryview."""
        size = buffer.size - buffer_offset if size is None else size
        BU = flags.BufferUsage
        staging = self._device.create_buffer(
            size=size, usage=BU.copy_dst | BU.map_read
        )
        encoder = self._device.create_command_encoder()
        encoder.copy_buffer_to_buffer(buffer, buffer_offset, staging, 0, size)
        self.submit([encoder.finish()])
        self._device._poll()
        staging.map_sync("READ", 0, size)
        data = bytearray(staging.read_mapped())  # copy before unmapping
        staging.unmap()
        return memoryview(data)


class GPUCommandEncoder(_Wrapper):
    def copy_buffer_to_buffer(self, source, source_offset, destination, destination_offset, size):
        self._inner.copy_buffer_to_buffer(
            source._inner, source_offset, destination._inner, destination_offset, size
        )

    def finish(self, *, label: str = ""):
        return _Wrapper(self._inner.finish({"label": label}))


class GPUDevice(_Wrapper):
    def __init__(self, inner):
        super().__init__(inner)
        self._queue = GPUQueue(inner.get_queue(), self)

    @property
    def queue(self) -> GPUQueue:
        return self._queue

    def _poll(self, wait: bool = True) -> None:
        """Flush pending device work (``wgpuDevicePoll``, a wgpu-native extra).

        The standard webgpu.h has no equivalent, so this reaches into the
        low-level lib directly -- one of the few things the shim does by hand.
        """
        api = get_api()
        api.lib.wgpuDevicePoll(self._inner._handle, bool(wait), api.ffi.NULL)

    def create_buffer(self, *, label: str = "", size: int, usage, mapped_at_creation: bool = False):
        usage = enum_value(flags.BufferUsage, usage)
        inner = self._inner.create_buffer(
            {
                "label": label,
                "size": size,
                "usage": usage,
                "mapped_at_creation": mapped_at_creation,
            }
        )
        return GPUBuffer(inner, size, usage, self)

    def create_buffer_with_data(self, *, label: str = "", data, usage):
        mv = memoryview(data).cast("B")
        size = (mv.nbytes + 3) & ~3  # buffer sizes are rounded up to a multiple of 4
        buf = self.create_buffer(
            label=label, size=size, usage=usage, mapped_at_creation=True
        )
        buf.write_mapped(mv, 0)
        buf.unmap()
        # Unmapping only queues the staging upload; it becomes visible to
        # subsequent reads once the queue is flushed.
        self.queue.submit([])
        self._poll()
        return buf

    def create_command_encoder(self, *, label: str = ""):
        return GPUCommandEncoder(self._inner.create_command_encoder({"label": label}))


class GPUAdapter(_Wrapper):
    def request_device_sync(self, *, label: str = "", **_ignored) -> GPUDevice:
        return GPUDevice(self._inner.request_device({"label": label}).wait())


def request_adapter_sync(*, power_preference=None, force_fallback_adapter=False, **_ignored):
    """Classic entrypoint: synchronously request an adapter."""
    options: dict = {"force_fallback_adapter": bool(force_fallback_adapter)}
    if power_preference is not None:
        options["power_preference"] = enum_value(enums.PowerPreference, power_preference)
    instance = get_api().create_instance()
    adapter = instance.request_adapter(options).wait()
    if adapter is None or not adapter._handle:
        raise RuntimeError("no adapter available")
    return GPUAdapter(adapter)


_default_device: GPUDevice | None = None


def get_default_device() -> GPUDevice:
    global _default_device
    if _default_device is None:
        _default_device = request_adapter_sync().request_device_sync()
    return _default_device
