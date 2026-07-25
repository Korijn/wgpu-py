"""Base class for all generated wgpu object wrappers."""

from __future__ import annotations


class GPUObjectBase:
    """Wraps a wgpu-native object handle and dispatches its methods.

    Subclasses (generated) set ``_spec_name`` and define one method per C
    method; the bodies all funnel through :meth:`_invoke`, so there is no
    per-method hand code. The underlying handle is released when the Python
    wrapper is garbage-collected.
    """

    _spec_name: str = ""

    __slots__ = ("__weakref__", "_handle", "_parent", "_pump")

    def __init__(self, handle, pump=None, parent=None):
        self._handle = handle
        # Drives wgpu-native's event loop for async ops; inherited from the
        # object that created this one (ultimately the instance).
        self._pump = pump
        # Strong reference to the creating object, so an ancestor (in
        # particular the instance backing ``_pump``) cannot be garbage
        # collected -- and released -- while a descendant is still alive.
        self._parent = parent

    def _invoke(self, method_name: str, *args):
        from .api import get_api

        return get_api().invoke(self, method_name, args)

    def _release(self):
        """Release the underlying handle exactly once (idempotent)."""
        handle = getattr(self, "_handle", None)
        if not handle:
            return
        self._handle = None  # prevent double-free before the C call
        try:
            from .api import get_api

            get_api().release(self._spec_name, handle)
        except Exception:
            pass  # interpreter shutdown / api gone

    def __del__(self):
        self._release()

    def __repr__(self):
        return f"<{type(self).__name__} at {self._handle}>"
