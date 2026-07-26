"""Base classes behind every generated public ``GPU*`` class.

The generated classes contain signatures and one-line bodies; all of the actual
behaviour -- dispatch into the FFI layer, object identity, labels, lifetimes,
sync/async duality -- lives here, in code that does not change when a spec is
bumped.
"""

from __future__ import annotations

import itertools

from wgpu._runtime.api import get_api

_uid_counter = itertools.count(1)


class Mixin:
    """Base for the WebGPU mixins (commands, debug commands, ...).

    They carry methods shared by several object types and are never
    instantiated on their own, so dispatch uses the concrete instance's
    ``_spec_name``.
    """

    __slots__ = ()


class GPUObjectBase(Mixin):
    """A handle to an object owned by wgpu-native.

    Instances are created by the runtime when a C call returns a handle, never
    by user code. The handle is released when the last Python reference goes
    away; ``_parent`` keeps the owning object (and thus the event pump that
    drives async work) alive for as long as this object needs it.
    """

    __slots__ = (
        "__weakref__",
        "_handle",
        "_parent",
        "_pump",
        "_label",
        "_uid",
        # Cached/attached state for the few properties that are not plain
        # getters: the device's queue, its loss promise and error handler, and
        # a texture's pinned binding-view dimension.
        "_queue",
        "_lost_promise",
        "_uncaptured_error_handler",
        "_binding_view_dimension",
        # Warnings that are issued once per device rather than once per call,
        # because they sit in front of per-frame calls.
        "_warned_aspect_keys",
        "_cache",
        # A buffer's mapped range: (start, end, mode). wgpu-native does not
        # implement the getter, and the range has to be validated in Python
        # anyway, since passing a bad one to C aborts the process.
        "_map_status",
    )

    _spec_name = ""

    def __init__(self, handle, pump=None, parent=None, label=""):
        self._handle = handle
        self._pump = pump
        self._parent = parent
        self._label = label
        self._uid = next(_uid_counter)
        self._queue = None
        self._lost_promise = None
        self._uncaptured_error_handler = None
        self._binding_view_dimension = None
        self._warned_aspect_keys = None
        self._cache = {}
        self._map_status = (0, 0, 0)

    # -- identity ----------------------------------------------------------

    @property
    def label(self) -> str:
        """A human-readable name for this object, for debugging."""
        return self._label

    @property
    def uid(self) -> int:
        """A process-unique id, handy when logging or diffing object graphs."""
        return self._uid

    def __repr__(self):
        return f"<wgpu.{self.__class__.__name__} {self._label!r} at {hex(id(self))}>"

    @property
    def _device(self):
        """The device this object ultimately came from, or ``None``.

        Every handle is created either by a device or by something a device
        created, and each one keeps its creator as ``_parent`` to hold it
        alive -- so the device is always reachable by walking up. wgpu-py has
        exposed this since the beginning, and per-device state (the
        once-per-device warnings, for one) needs somewhere to live.
        """
        obj = self
        while obj is not None:
            if obj._spec_name == "device":
                return obj
            obj = obj._parent
        return None

    # -- dispatch ----------------------------------------------------------
    #
    # Three entry points, matching the three shapes the generator emits. Each
    # is a single call into the invoker; nothing is translated here, because the
    # generated code already speaks the C layer's vocabulary.

    def _call(self, spec_method: str, *args):
        return get_api().invoke(self, spec_method, args)

    def _call_desc(self, spec_method: str, descriptor: dict):
        result = get_api().invoke(self, spec_method, (descriptor,))
        if isinstance(result, GPUObjectBase):
            result._label = descriptor.get("label") or ""
        return result

    def _get(self, spec_method: str):
        return get_api().invoke(self, spec_method, ())

    def _get_cached(self, spec_method: str):
        """Like :meth:`_get`, for values that cannot change after creation.

        A buffer's size, a texture's format and so on are fixed by the
        descriptor they were made from, so reading them need not cross the FFI
        boundary more than once -- and they are read constantly in render loops.
        """
        cache = self._cache
        try:
            return cache[spec_method]
        except KeyError:
            value = cache[spec_method] = get_api().invoke(self, spec_method, ())
            return value

    @staticmethod
    def _promise(future):
        """The ``*_async`` form: hand back the awaitable as-is."""
        return future

    @staticmethod
    def _await(promise):
        """The ``*_sync`` form: pump the event queue until the result lands."""
        return promise.sync_wait()

    # -- lifetime ----------------------------------------------------------

    def _release(self):
        handle, self._handle = self._handle, None
        if handle is not None and self._spec_name:
            get_api().release(self._spec_name, handle)

    def __del__(self):
        try:
            self._release()
        except Exception:  # pragma: no cover - interpreter shutdown
            pass


def new_object(cls, handle, parent, label=""):
    """Wrap a handle a direct C call returned.

    The new object inherits its parent's event pump and holds a reference to
    it, so the ancestry that backs async work stays alive. The label is kept
    Python-side, as it always has been: wgpu-native does not hand it back.
    """
    if not handle:
        return None
    return cls(handle, parent._pump, parent, label)


#: The one live error tracker, published by ``Api`` at construction so the
#: generated bodies can reach it without going through the invoker. There is
#: exactly one ``Api`` (it is ``lru_cache``d), so this is not a shortcut around
#: state that could plausibly be plural.
_errors = None


def publish_error_tracker(tracker) -> None:
    global _errors
    _errors = tracker


def raise_if_error() -> None:
    """Surface any error wgpu-native reported since the last check.

    Methods that build a descriptor call this, so they keep reporting errors at
    exactly the boundary they always have. The genuinely hot methods -- the
    per-draw setters -- deliberately do not; their errors surface here instead,
    at the next ``finish()`` or ``submit()``.
    """
    if _errors is not None and _errors:
        _errors.raise_if_error()


def unimplemented(c_func: str):
    """Refuse to call a C function wgpu-native does not implement.

    wgpu-native declares the whole WebGPU surface but leaves some functions as
    ``unimplemented!()``. A Rust panic cannot unwind across FFI, so calling one
    aborts the interpreter -- an exception is strictly better. The generator
    reads wgpu-native's own list, so if a release implements one of these the
    guard disappears on the next regeneration.
    """
    raise NotImplementedError(
        f"{c_func} is declared by wgpu-native but not implemented by it."
    )


def slice_data(data, offset=0, size=None):
    """A byte view of ``data``, optionally windowed.

    The web API lets a caller hand over a large buffer plus a window into it,
    where C takes only a pointer and a length. Used by the generated methods
    that pass raw bytes -- queue writes and immediate data.
    """
    view = data if isinstance(data, memoryview) else memoryview(data)
    if not offset and size is None:
        return view  # the whole thing, with no re-cast
    view = view.cast("B")
    if size is None:
        size = view.nbytes - offset
    chunk = view[offset : offset + size]
    if chunk.nbytes != size:
        raise ValueError(
            f"need {size} bytes from offset {offset}, but the data has only "
            f"{view.nbytes - offset}"
        )
    return chunk
