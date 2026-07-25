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
        "_cache",
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
        self._cache = {}

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
        return (
            f"<wgpu.{self.__class__.__name__} "
            f"{self._label!r} at {hex(id(self))}>"
        )

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


def new_object(cls, handle, parent):
    """Wrap a handle a direct C call returned.

    The new object inherits its parent's event pump and holds a reference to
    it, so the ancestry that backs async work stays alive.
    """
    if not handle:
        return None
    return cls(handle, parent._pump, parent)
