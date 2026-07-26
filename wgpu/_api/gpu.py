"""The ``wgpu.gpu`` entrypoint.

This is the equivalent of the browser's ``navigator.gpu``. The Web IDL has no C
counterpart for it -- on the web the browser owns the instance -- so it is
written by hand on top of the generated instance object.
"""

from __future__ import annotations

from wgpu._api import overrides
from wgpu._api.types import CanvasLike


class GPU:
    """The entrypoint: obtain an adapter, and from it a device."""

    def __init__(self):
        self._instance = None

    @property
    def _inst(self):
        """The wgpu-native instance, created on first use."""
        if self._instance is None:
            from wgpu._runtime.api import get_api

            self._instance = get_api().create_instance()
        return self._instance

    # -- adapters ----------------------------------------------------------

    def request_adapter_async(
        self,
        *,
        feature_level: str = "core",
        power_preference: str | None = None,
        force_fallback_adapter: bool = False,
        canvas: CanvasLike | None = None,
    ):
        """Request a `GPUAdapter`, the object representing a wgpu implementation.

        Arguments:
            feature_level (str): "core" (default) or "compatibility".
            power_preference (str): "high-performance" or "low-power".
            force_fallback_adapter (bool): prefer a (probably CPU) fallback.
            canvas: the canvas or context the adapter must be able to render to.
        """
        options = {
            "power_preference": power_preference,
            "force_fallback_adapter": force_fallback_adapter,
        }
        if canvas is not None:
            options["compatible_surface"] = _surface_of(canvas)
        return self._inst._call("request_adapter", options)

    def request_adapter_sync(
        self,
        *,
        feature_level: str = "core",
        power_preference: str | None = None,
        force_fallback_adapter: bool = False,
        canvas: CanvasLike | None = None,
    ):
        """Blocking version of `request_adapter_async()`.

        Provided by wgpu-py; the web API has no synchronous form.
        """
        return self.request_adapter_async(
            feature_level=feature_level,
            power_preference=power_preference,
            force_fallback_adapter=force_fallback_adapter,
            canvas=canvas,
        ).sync_wait()

    request_adapter = overrides.deprecated_sync_or_async("request_adapter")

    def enumerate_adapters_async(self):
        """List every adapter wgpu-native can see."""
        from wgpu._runtime.awaitable import completed

        return completed(self.enumerate_adapters_sync())

    def enumerate_adapters_sync(self) -> list:
        """List every adapter wgpu-native can see.

        Useful on multi-GPU machines, where ``request_adapter`` picks only one.
        """
        from wgpu._api.extras import enumerate_adapters

        return enumerate_adapters(self._inst)

    # -- canvas ------------------------------------------------------------

    def get_preferred_canvas_format(self) -> str:
        """The texture format a canvas should prefer on this platform."""
        return "bgra8unorm"

    def get_canvas_context(self, present_info: dict):
        """Get the `GPUCanvasContext` for a canvas."""
        from wgpu._api.canvas import GPUCanvasContext

        return GPUCanvasContext(present_info, self._inst)

    @property
    def wgsl_language_features(self) -> set:
        """The optional WGSL language features this implementation supports."""
        return set()

    def __repr__(self):
        return f"<wgpu.GPU at {hex(id(self))}>"


def _surface_of(canvas):
    """Get the wgpu-native surface backing a canvas or canvas context."""
    context = getattr(canvas, "_surface", None)
    if context is not None:
        return context
    get_context = getattr(canvas, "get_context", None)
    if get_context is not None:
        return getattr(get_context("wgpu"), "_surface", None)
    return None
