"""Conveniences wgpu-py provides that the WebGPU spec does not.

Everything here is a documented *addition* to the web API -- helpers that have
proven too useful to leave out, plus a few wgpu-native features with no web
counterpart. They are attached to the generated classes in ``wgpu._api``.
"""

from __future__ import annotations


# -- GPUBuffer ---------------------------------------------------------------


def buffer_read_mapped(self, buffer_offset=None, size=None, *, copy=True):
    """Read from a mapped buffer.

    With ``copy=True`` (the default) you get an independent memoryview that
    stays valid after ``unmap()``. With ``copy=False`` you get a view onto the
    mapped memory itself: faster, but invalid once the buffer is unmapped.
    """
    from wgpu._api.overrides import check_mapped_for

    offset, size = check_mapped_for(self, "read", buffer_offset, size)
    view = self.get_mapped_range(offset, size)
    return memoryview(bytearray(view)) if copy else view


def buffer_write_mapped(self, data, buffer_offset=None):
    """Write into a mapped buffer."""
    from wgpu._api.overrides import check_mapped_for

    src = memoryview(data).cast("B")
    # The mapped range must be 4-aligned even when the data is not; the extra
    # bytes are simply not written.
    size = (src.nbytes + 3) & ~3
    offset, size = check_mapped_for(self, "write", buffer_offset, size)
    self.get_mapped_range(offset, size)[: src.nbytes] = src


# -- GPUDevice ---------------------------------------------------------------


def device_create_buffer_with_data(self, *, label="", data, usage):
    """Create a buffer and fill it with ``data`` in one step.

    The buffer is created mapped, written, and unmapped; its size is the data's
    size rounded up to a multiple of 4, as wgpu-native requires.
    """
    src = memoryview(data).cast("B")
    # wgpu-native requires both buffer sizes and mapped ranges to be a multiple
    # of 4, so round up and leave the padding bytes zeroed.
    size = (src.nbytes + 3) & ~3
    buffer = self.create_buffer(
        label=label, size=size, usage=usage, mapped_at_creation=True
    )
    buffer.write_mapped(src)
    buffer.unmap()
    return buffer


@property
def device_adapter(self):
    """The adapter this device was created from."""
    return self._parent


# -- GPUQueue ----------------------------------------------------------------


def queue_read_buffer(self, buffer, buffer_offset=0, size=None):
    """Read back a buffer's contents.

    Copies through a staging buffer, so the source buffer needs only
    ``COPY_SRC``; the result is an independent ``bytearray``.
    """
    from wgpu._generated import apiflags as flags

    if not size:
        size = buffer.size - buffer_offset
    if not (0 <= buffer_offset < buffer.size):
        raise ValueError("Invalid buffer_offset")
    if size > buffer.size - buffer_offset:
        raise ValueError("Invalid size")
    device = self._parent
    staging = device.create_buffer(
        size=size, usage=flags.BufferUsage.COPY_DST | flags.BufferUsage.MAP_READ
    )
    encoder = device.create_command_encoder()
    encoder.copy_buffer_to_buffer(buffer, buffer_offset, staging, 0, size)
    self.submit([encoder.finish()])
    staging.map_sync("READ")
    try:
        return memoryview(bytearray(staging.get_mapped_range(0, size)))
    finally:
        staging.unmap()
        staging.destroy()


def queue_read_texture(self, source, data_layout, size):
    """Read back a region of a texture, as a ``bytearray``."""
    from wgpu._generated import apiflags as flags

    device = self._parent
    bytes_per_row = data_layout["bytes_per_row"]
    rows_per_image = data_layout.get("rows_per_image") or size[1]
    nbytes = bytes_per_row * rows_per_image * size[2]
    staging = device.create_buffer(
        size=nbytes, usage=flags.BufferUsage.COPY_DST | flags.BufferUsage.MAP_READ
    )
    encoder = device.create_command_encoder()
    encoder.copy_texture_to_buffer(
        source,
        {"buffer": staging, "offset": 0, "bytes_per_row": bytes_per_row,
         "rows_per_image": rows_per_image},
        size,
    )
    self.submit([encoder.finish()])
    staging.map_sync("READ")
    try:
        return memoryview(bytearray(staging.get_mapped_range(0, nbytes)))
    finally:
        staging.unmap()
        staging.destroy()


# -- GPUAdapter / textures ---------------------------------------------------


@property
def adapter_summary(self):
    """A one-line description of the adapter, for logs and bug reports."""
    return self.info.summary


@property
def texture_size(self):
    """The texture's size as ``(width, height, depth_or_array_layers)``."""
    return (self.width, self.height, self.depth_or_array_layers)


@property
def texture_view_texture(self):
    """The texture this view was created from.

    Not in the Web IDL, which gives ``GPUTextureView`` no attributes at all.
    The view holds its texture as its parent regardless, to keep it alive.
    """
    return self._parent


@property
def texture_view_size(self):
    """The size of the texture this view was created from."""
    return self._parent.size


def enumerate_adapters(instance):
    """Every adapter wgpu-native can see, as `GPUAdapter` objects."""
    from wgpu.backends.wgpu_native.extras import enumerate_adapters as _enumerate

    return _enumerate(instance)
