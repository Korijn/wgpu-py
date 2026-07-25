"""The hand-written part of the public API.

Everything here exists because the WebGPU spec and the C API genuinely differ
in *shape*, not just in spelling -- the generator emits a hook and this module
fills it in. Keeping the list short is the point: if this file grows, something
that could be derived is being written by hand instead.
"""

from __future__ import annotations

from wgpu._coreutils import logger

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
    view = memoryview(data).cast("B")
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
