"""The hand-written part of the public API.

Everything here exists because the WebGPU spec and the C API genuinely differ
in *shape*, not just in spelling -- the generator emits a hook and this module
fills it in. Keeping the list short is the point: if this file grows, something
that could be derived is being written by hand instead.
"""

from __future__ import annotations

from wgpu._api.base import GPUObjectBase
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
    """``get_limits`` reports into an out-parameter; expose it as a dict.

    Keys are hyphenated (``max-bind-groups``), which is the spelling wgpu-py
    has always reported limits in -- and the one that reads back in unchanged,
    since the struct builder normalises hyphens, underscores and camelCase
    alike. The ``1D``/``2D``/``3D`` suffixes lowercase along with the rest.
    """
    return {
        key.replace("_", "-").lower(): value
        for key, value in obj._get("get_limits").items()
    }


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
    if (
        dynamic_offsets_data_start is not None
        or dynamic_offsets_data_length is not None
    ):
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


# -- buffer mapping ----------------------------------------------------------
#
# The mapped range is tracked here rather than asked of wgpu-native: it does not
# implement wgpuBufferGetMapState, and every offset and size has to be checked
# in Python regardless, because handing C an invalid range aborts the process
# instead of raising.

_UNMAPPED, _PENDING, _MAPPED = "unmapped", "pending", "mapped"


def _check_range(buffer, offset, size):
    """Resolve and validate a range against the buffer's mapped region."""
    start, end, mode = buffer._map_status
    if offset is None:
        offset = start if mode else 0
    else:
        offset = int(offset)
    if size is None:
        size = (end - offset) if mode else (buffer.size - offset)
    else:
        size = int(size)
    if offset < 0:
        raise ValueError("Mapped offset must not be smaller than zero.")
    if offset % 8:
        raise ValueError("Mapped offset must be a multiple of 8.")
    if size < 1:
        raise ValueError("Mapped size must be larger than zero.")
    if size % 4:
        raise ValueError("Mapped size must be a multiple of 4.")
    if offset + size > buffer.size:
        raise ValueError("Mapped range must not extend beyond total buffer size.")
    return offset, size


#: A read map that skips the flush below, for callers that have already
#: submitted and know there is nothing outstanding. Not part of WebGPU;
#: rendercanvas uses it on its present path, where the extra submit per frame
#: is pure cost.
_READ_NOSYNC = "READ_NOSYNC"


def buffer_map_async(self, mode, offset=0, size=None):
    """Map the buffer for reading or writing, asynchronously."""
    if self._map_status[2]:
        raise RuntimeError("Buffer is already mapped.")
    sync = mode != _READ_NOSYNC
    mode = _map_mode(mode)
    offset, size = _check_range(self, offset, size)
    if sync:
        _flush_queue_writes(self)
    promise = self._call("map_async", mode, offset, size)
    self._map_status = (offset, offset + size, mode)
    return promise


def _flush_queue_writes(buffer):
    """Make sure queued writes have landed before the buffer is mapped.

    ``queue.write_buffer``, and unmapping a buffer created with
    ``mapped_at_creation``, stage their data in wgpu-native rather than writing
    it through; the staged copies only run on the next queue submit. Mapping
    does not trigger one, so without this a read-back sees whatever was in the
    buffer before -- silently, and with plausible-looking stale bytes.
    See https://github.com/gfx-rs/wgpu-native/issues/305.

    By the spec, mapping observes everything already ordered on the queue, so
    an empty submit is what makes that true here. It costs one C call, on an
    operation that is already asynchronous and expensive -- and ``READ_NOSYNC``
    opts out for callers that have just submitted themselves.
    """
    device = buffer._device
    if device is not None:
        device.queue.submit([])


def _map_mode(mode):
    from wgpu._generated import apiflags

    if mode == _READ_NOSYNC:
        return apiflags.MapMode.READ
    value = apiflags.TO_INT["map_mode"][mode]
    if value not in (apiflags.MapMode.READ, apiflags.MapMode.WRITE):
        raise ValueError(f"Invalid map mode: {mode!r}")
    return value


def buffer_map_sync(self, mode, offset=0, size=None):
    """Blocking version of `map_async()`."""
    return buffer_map_async(self, mode, offset, size).sync_wait()


buffer_map = deprecated_sync_or_async("map")


def buffer_unmap(self):
    """Unmap the buffer, invalidating any views onto its mapped range."""
    if not self._map_status[2]:
        raise RuntimeError("Cannot unmap a buffer that is not mapped.")
    self._map_status = (0, 0, 0)
    # Release before unmapping: wgpu-native reclaims the memory these point
    # at, and a released view raises where a dangling one would quietly read
    # whatever landed there next. Views *derived* from these (slices) are not
    # reached, which is a limitation of memoryview, not a choice.
    views, self._mapped_views = self._mapped_views, []
    for view in views:
        try:
            view.release()
        except (BufferError, ValueError):
            pass  # still exported, or already released
    return self._call("unmap")


def buffer_get_mapped_range(self, offset=0, size=None):
    """A memoryview onto the mapped range. Invalid once the buffer is unmapped."""
    from wgpu._generated import apiflags

    offset, size = _check_range(self, offset, size)
    view = self._call("get_mapped_range", offset, size)
    if not self._map_status[2] & apiflags.MapMode.WRITE:
        # Mapped for reading only. wgpu-native hands back the memory either
        # way, but writing into it would be discarded on unmap rather than
        # uploaded, so the view says so instead of failing quietly.
        view = view.toreadonly()
    self._mapped_views.append(view)
    return view


@property
def buffer_map_state(self):
    """Whether the buffer is 'unmapped', 'pending' or 'mapped'."""
    return _MAPPED if self._map_status[2] else _UNMAPPED


def check_mapped_for(buffer, direction, offset, size):
    """Validate a read/write against the buffer's currently mapped range."""
    from wgpu._generated import apiflags

    start, end, mode = buffer._map_status
    wanted = apiflags.MapMode.READ if direction == "read" else apiflags.MapMode.WRITE
    if not mode:
        raise RuntimeError(f"Can only {direction} a buffer while it is mapped.")
    if not mode & wanted:
        raise RuntimeError(f"Can only {direction} a buffer mapped in {direction} mode.")
    offset, size = _check_range(buffer, offset, size)
    if offset < start or (offset + size) > end:
        raise ValueError("The range is not contained in the currently mapped range.")
    return offset, size


# -- shader compilation info -------------------------------------------------
#
# wgpu-native leaves wgpuShaderModuleGetCompilationInfo unimplemented, and a
# shader that fails to compile raises at create_shader_module() anyway, so a
# module you can call this on compiled cleanly. wgpu-py has always reported
# that as "no messages" rather than failing.


def shader_module_get_compilation_info_async(self):
    """The messages produced while compiling this shader module."""
    from wgpu._runtime.awaitable import completed

    return completed([])


def shader_module_get_compilation_info_sync(self):
    """The messages produced while compiling this shader module."""
    return []


shader_module_get_compilation_info = deprecated_sync_or_async("get_compilation_info")


def track_mapped_at_creation(generated_create_buffer):
    """Wrap ``create_buffer`` so ``mapped_at_creation`` records the range.

    A buffer created mapped is mapped for writing over its whole length, and
    the tracked range is what read_mapped/write_mapped/unmap validate against.
    """
    import functools

    @functools.wraps(generated_create_buffer)
    def create_buffer(self, **kwargs):
        buffer = generated_create_buffer(self, **kwargs)
        if kwargs.get("mapped_at_creation"):
            from wgpu._generated import apiflags

            buffer._map_status = (0, buffer.size, apiflags.MapMode.WRITE)
        return buffer

    return create_buffer


def check_clear_range(generated_clear_buffer):
    """Wrap ``clear_buffer`` to reject a bad range before recording it.

    ``clear_buffer`` is a command *recorded* into an encoder, so wgpu-native
    only rejects a misaligned offset at ``finish()`` -- a long way from the
    call that caused it. These four conditions are cheap to check here, and
    wgpu-py has always raised ValueError for them at the call itself.
    """
    import functools

    @functools.wraps(generated_clear_buffer)
    def clear_buffer(self, buffer, offset=0, size=None):
        offset = int(offset)
        if offset < 0 or offset % 4:
            raise ValueError("clear_buffer offset must be a multiple of 4, and >= 0")
        if size is None:
            if offset > buffer.size:
                raise ValueError("clear_buffer offset is past the end of the buffer")
        else:
            size = int(size)
            if size <= 0 or size % 4:
                raise ValueError("clear_buffer size must be a multiple of 4, and > 0")
            if offset + size > buffer.size:
                raise ValueError("clear_buffer range is past the end of the buffer")
        return generated_clear_buffer(self, buffer, offset, size)

    return clear_buffer


def device_poll(self, wait=False):
    """Run wgpu-native's pending work for this device.

    Not part of WebGPU, where the browser's event loop does this. It is the
    hook that lets a test (or a render loop that owns its own loop) push
    completion callbacks along without awaiting anything in particular.
    """
    from wgpu._native import ffi, lib

    lib.wgpuDevicePoll(self._handle, bool(wait), ffi.NULL)


def device_poll_wait(self):
    """Block until wgpu-native has finished this device's pending work."""
    device_poll(self, wait=True)


def retain_arguments(method):
    """Wrap a recording method so the objects it is given outlive the call.

    A command encoder takes ownership of what it records straight away, but a
    *bundle* encoder only reads its references again at ``finish()``. Drop the
    last Python reference to a pipeline in between and wgpu-core panics on a
    slot that no longer exists -- and a Rust panic cannot unwind through the
    FFI, so it takes the process with it rather than raising.

    Every object argument is retained rather than a listed few: which ones
    matter is a property of wgpu-core's internals, and guessing wrong here is
    a crash, while retaining one object too many until ``finish()`` is not.
    """
    import functools

    @functools.wraps(method)
    def recorded(self, *args, **kwargs):
        retained = self._retained
        if retained is None:
            retained = self._retained = set()
        for value in (*args, *kwargs.values()):
            if isinstance(value, GPUObjectBase):
                retained.add(value)
        return method(self, *args, **kwargs)

    return recorded


def release_arguments(finish):
    """Wrap ``finish`` so the retained objects are let go once it has run."""
    import functools

    @functools.wraps(finish)
    def wrapper(self, **kwargs):
        try:
            return finish(self, **kwargs)
        finally:
            self._retained = None

    return wrapper


def retain_on(cls) -> None:
    """Apply :func:`retain_arguments` to everything ``cls`` records.

    Every public method is wrapped rather than a hand-kept list: one that
    takes no objects simply retains nothing, so the wrapping cannot be wrong
    in the direction that crashes.
    """
    for name, value in list(vars(cls).items()):
        if name.startswith("_") or not callable(value):
            continue
        if name == "finish":
            setattr(cls, name, release_arguments(value))
        else:
            setattr(cls, name, retain_arguments(value))
    for base in cls.__mro__[1:]:
        for name, value in list(vars(base).items()):
            if name.startswith("_") or not callable(value) or name in vars(cls):
                continue
            setattr(cls, name, retain_arguments(value))


#: wgpu-native requires a texture-to-buffer copy's rows to be 256-byte aligned.
_COPY_BYTES_PER_ROW_ALIGNMENT = 256


def check_copy_alignment(generated_copy_texture_to_buffer):
    """Wrap ``copy_texture_to_buffer`` to reject a bad destination up front.

    Same reasoning as ``clear_buffer``: this records a command, so wgpu-native
    would not object until ``finish()``. The alignment rule catches nearly
    everyone once, and a ValueError naming the number is a far better answer
    than a validation error arriving three calls later.
    """
    import functools

    @functools.wraps(generated_copy_texture_to_buffer)
    def copy_texture_to_buffer(self, source, destination, copy_size):
        bytes_per_row = (destination or {}).get("bytes_per_row")
        if bytes_per_row is not None:
            bytes_per_row = int(bytes_per_row)
            if bytes_per_row % _COPY_BYTES_PER_ROW_ALIGNMENT:
                raise ValueError(
                    f"bytes_per_row ({bytes_per_row}) must be a multiple of "
                    f"{_COPY_BYTES_PER_ROW_ALIGNMENT}"
                )
        texture = (source or {}).get("texture")
        if texture is not None and texture._spec_name == "texture_view":
            raise ValueError("copy source must be a texture, not a texture view")
        return generated_copy_texture_to_buffer(self, source, destination, copy_size)

    return copy_texture_to_buffer


#: The depth-stencil attachment keys that only apply when the attached texture
#: actually has that aspect, and it is not read-only.
_ASPECT_KEYS = {
    "depth": ("depth_load_op", "depth_store_op", "depth_clear_value"),
    "stencil": ("stencil_load_op", "stencil_store_op", "stencil_clear_value"),
}


def drop_inapplicable_aspect_ops(generated_begin_render_pass):
    """Wrap ``begin_render_pass`` to drop ops the attachment has no aspect for.

    By the spec, ``stencil_load_op``/``stencil_store_op`` are only allowed when
    the depth-stencil texture has a stencil aspect that is not read-only, and
    likewise for depth. wgpu-py used to *require* all four regardless, so a lot
    of existing code passes them unconditionally and would now fail validation.

    Those keys are therefore dropped rather than forwarded, and saying so is a
    warning rather than an error -- once per device per key, because this sits
    in front of a per-frame call and a warning every frame is just noise. The
    read-only flags stay, since they are what makes the aspect inapplicable.
    """
    import functools

    @functools.wraps(generated_begin_render_pass)
    def begin_render_pass(self, *, depth_stencil_attachment=None, **kwargs):
        if depth_stencil_attachment:
            depth_stencil_attachment = _prune_aspect_ops(
                self._device, dict(depth_stencil_attachment)
            )
        return generated_begin_render_pass(
            self, depth_stencil_attachment=depth_stencil_attachment, **kwargs
        )

    return begin_render_pass


def _prune_aspect_ops(device, attachment: dict) -> dict:
    # Every depth format has "depth" in its name and every stencil format has
    # "stencil" in its name, so the format string answers this directly.
    view = attachment.get("view")
    format = getattr(getattr(view, "texture", None), "format", "") or ""
    warned = device._warned_aspect_keys if device is not None else None
    if warned is None and device is not None:
        warned = device._warned_aspect_keys = set()
    for aspect, keys in _ASPECT_KEYS.items():
        if aspect in format and not attachment.get(f"{aspect}_read_only", False):
            continue
        for key in keys:
            if attachment.pop(key, None) is None:
                continue
            if warned is not None and key not in warned:
                warned.add(key)
                logger.warning(f"Unexpected key {key} in depth_stencil_attachment")
    return attachment
