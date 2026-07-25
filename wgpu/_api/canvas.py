"""``GPUCanvasContext`` -- presenting rendered frames to a window.

The Web IDL models this as a property of an HTML canvas, so there is no C
object to generate from. wgpu-native instead exposes a *surface*, which is
generated; this class is the adapter between the two.
"""

from __future__ import annotations


class GPUCanvasContext:
    """The context through which a canvas is rendered to."""

    def __init__(self, present_info: dict, instance):
        self._present_info = dict(present_info or {})
        self._instance = instance
        self._surface = None
        self._config = None
        self._physical_size = (0, 0)

    # -- configuration -----------------------------------------------------

    def configure(
        self,
        *,
        device,
        format,
        usage=0x10,
        view_formats=(),
        color_space="srgb",
        tone_mapping=None,
        alpha_mode="opaque",
    ):
        """Configure how frames are presented to this canvas."""
        if format is None:
            format = self.get_preferred_format(device.adapter)
        self._config = {
            "device": device,
            "format": format,
            "usage": usage,
            "view_formats": list(view_formats),
            "alpha_mode": alpha_mode,
        }
        self._configure_surface()

    def get_configuration(self) -> dict | None:
        """The configuration passed to `configure()`, or None."""
        return dict(self._config) if self._config else None

    def unconfigure(self) -> None:
        """Stop presenting to this canvas."""
        if self._surface is not None and self._config is not None:
            self._surface.unconfigure()
        self._config = None

    def _configure_surface(self):
        surface, config = self._surface, self._config
        if surface is None or config is None:
            return
        width, height = self._physical_size
        surface.configure(
            {
                "device": config["device"],
                "format": config["format"],
                "usage": config["usage"],
                "view_formats": config["view_formats"],
                "alpha_mode": config["alpha_mode"],
                "width": max(width, 1),
                "height": max(height, 1),
            }
        )

    # -- presentation ------------------------------------------------------

    def get_current_texture(self):
        """The texture to render this frame into."""
        if self._config is None:
            raise RuntimeError("Canvas context must be configured before rendering.")
        return self._surface.get_current_texture()

    def present(self) -> None:
        """Present the rendered frame."""
        if self._surface is not None:
            self._surface.present()

    def get_preferred_format(self, adapter) -> str:
        """The texture format this canvas prefers on this adapter."""
        return "bgra8unorm"

    # -- sizing ------------------------------------------------------------

    @property
    def physical_size(self) -> tuple:
        """The canvas size in physical pixels."""
        return self._physical_size

    def set_physical_size(self, width: int, height: int) -> None:
        """Tell the context the canvas was resized, so the surface follows."""
        self._physical_size = (int(width), int(height))
        self._configure_surface()

    def __repr__(self):
        return f"<wgpu.GPUCanvasContext at {hex(id(self))}>"
