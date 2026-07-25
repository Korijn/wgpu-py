"""The hand-written part of the public API.

Everything here exists because the WebGPU spec and the C API genuinely differ
in *shape*, not just in spelling -- the generator emits a hook and this module
fills it in. Keeping the list short is the point: if this file grows, something
that could be derived is being written by hand instead.
"""

from __future__ import annotations

from wgpu._coreutils import logger
from wgpu._native import ffi as _ffi

_NULL = _ffi.NULL

# -- sync/async duality ------------------------------------------------------

_warned: set[str] = set()


def deprecated_sync_or_async(name: str):
    """The bare ``map`` / ``request_adapter`` form, kept for compatibility.

    wgpu-py used to expose only the blocking spelling; it now proxies to the
    ``_sync`` variant and warns once.
    """

    def proxy(self, *args, **kwargs):
        if name not in _warned:
            _warned.add(name)
            logger.warning(f"WGPU: {name}() is deprecated, use {name}_sync() instead.")
        return getattr(self, name + "_sync")(*args, **kwargs)

    proxy.__name__ = name
    proxy.__doc__ = f"Deprecated alias for ``{name}_sync()``."
    return proxy


# -- properties with no plain C getter ---------------------------------------


def _limits(obj) -> dict:
    """``get_limits`` reports into an out-parameter; expose it as a dict."""
    return dict(obj._get("get_limits"))


def _features(obj) -> set:
    return set(obj._get("get_features").get("features", ()))


adapter_limits = property(_limits, doc="The supported limits, as a dict.")
adapter_features = property(_features, doc="The supported features, as a set.")
device_limits = property(_limits, doc="The device's limits, as a dict.")
device_features = property(_features, doc="The device's features, as a set.")


@property
def adapter_info(self):
    """Information about the adapter: vendor, device, backend, driver."""
    from wgpu._api.info import GPUAdapterInfo

    return GPUAdapterInfo(self._get("get_info"))


@property
def device_adapter_info(self):
    """Information about the adapter this device came from."""
    from wgpu._api.info import GPUAdapterInfo

    return GPUAdapterInfo(self._get("get_adapter_info"))


@property
def buffer_map_state(self):
    """Whether the buffer is 'unmapped', 'pending' or 'mapped'."""
    return self._get("get_map_state")


@property
def texture_view_texture(self):
    """The texture this view was created from."""
    return self._parent


@property
def texture_texture_binding_view_dimension(self):
    """The view dimension this texture is bound as, if it was pinned."""
    return self._binding_view_dimension


@property
def device_queue(self):
    """The device's default queue. Created lazily and then cached."""
    queue = self._queue
    if queue is None:
        queue = self._queue = self._get("get_queue")
        queue._label = "default"
    return queue


@property
def device_lost(self):
    """A promise that resolves when the device is lost."""
    return self._lost_promise


@property
def device_onuncapturederror(self):
    """The handler called for errors not caught by an error scope."""
    return self._uncaptured_error_handler


@device_onuncapturederror.setter
def device_onuncapturederror(self, handler):
    self._uncaptured_error_handler = handler


# -- methods whose Python signature differs from the C one -------------------


def queue_write_buffer(self, buffer, buffer_offset, data, data_offset=0, size=None):
    """Write data into a buffer.

    The web API slices the source itself, so this takes ``data_offset``/``size``
    in *bytes* and hands wgpu-native only the resulting view.
    """
    view = data if isinstance(data, memoryview) else memoryview(data)
    if data_offset == 0 and size is None:
        # The common case: the whole source goes in, so there is nothing to
        # slice and no need to re-cast to bytes.
        return self._call("write_buffer", buffer, buffer_offset, view, view.nbytes)
    view = view.cast("B")
    if size is None:
        size = view.nbytes - data_offset
    chunk = view[data_offset : data_offset + size]
    if chunk.nbytes != size:
        raise ValueError(
            f"write_buffer: need {size} bytes from offset {data_offset}, "
            f"but the data has only {view.nbytes - data_offset}"
        )
    return self._call("write_buffer", buffer, buffer_offset, chunk, size)


def queue_write_texture(self, destination, data, data_layout, size):
    """Write data into a texture.

    C wants the byte count spelled out; the web API derives it from the data.
    """
    view = memoryview(data).cast("B")
    return self._call("write_texture", destination, view, view.nbytes, data_layout, size)


def binding_commands_set_bind_group(
    self,
    index,
    bind_group,
    dynamic_offsets_data=(),
    dynamic_offsets_data_start=None,
    dynamic_offsets_data_length=None,
):
    """Bind a bind group, optionally with a slice of dynamic offsets.

    The web API lets the caller pass a large offsets buffer plus a
    start/length window into it; C takes just the resulting array.
    """
    if not dynamic_offsets_data and dynamic_offsets_data_start is None:
        # The common case by far, and the one on the hot path: no dynamic
        # offsets, so there is nothing to marshal and this is one C call.
        return self._c_set_bind_group(
            self._handle, index, bind_group._handle if bind_group else _NULL, 0, _NULL
        )
    if dynamic_offsets_data_start is not None or dynamic_offsets_data_length is not None:
        if dynamic_offsets_data_start is None or dynamic_offsets_data_length is None:
            raise ValueError(
                "set_bind_group: pass both dynamic_offsets_data_start and "
                "dynamic_offsets_data_length, or neither"
            )
        if dynamic_offsets_data_start < 0 or dynamic_offsets_data_length < 0:
            raise ValueError("set_bind_group: dynamic offsets slice must be positive")
        start = dynamic_offsets_data_start
        dynamic_offsets_data = memoryview(dynamic_offsets_data).cast("I")[
            start : start + dynamic_offsets_data_length
        ]
    offsets = [int(i) for i in dynamic_offsets_data]
    return self._call("set_bind_group", index, bind_group, offsets)


def binding_commands_set_immediates(self, range_offset, data, data_offset=0, data_size=None):
    """Set immediate data, slicing the source the way the web API does."""
    view = memoryview(data).cast("B")
    if data_size is None:
        data_size = view.nbytes - data_offset
    chunk = view[data_offset : data_offset + data_size]
    return self._call("set_immediate_data", range_offset, chunk, chunk.nbytes)


# -- buffer mapping ----------------------------------------------------------
#
# The spec says an omitted ``size`` means "to the end of the buffer". C has a
# whole-size sentinel for this, but wgpu-native rejects it for mapping, so the
# size is resolved here, where the buffer's size is known.


def _map_range(buffer, offset, size):
    if size is None:
        size = buffer.size - offset
    return int(offset), int(size)


def buffer_map_async(self, mode, offset=0, size=None):
    """Map the buffer for reading or writing, asynchronously."""
    return self._call("map_async", mode, *_map_range(self, offset, size))


def buffer_map_sync(self, mode, offset=0, size=None):
    """Blocking version of `map_async()`."""
    return buffer_map_async(self, mode, offset, size).sync_wait()


buffer_map = deprecated_sync_or_async("map")


def buffer_get_mapped_range(self, offset=0, size=None):
    """A memoryview onto the mapped range. Invalid once the buffer is unmapped."""
    return self._call("get_mapped_range", *_map_range(self, offset, size))
