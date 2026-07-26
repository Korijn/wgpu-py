"""
Test creation of offscreen canvas windows.
"""

import gc
import weakref

import wgpu
import pytest
from testutils import can_use_glfw, can_use_wgpu_lib, create_and_release, is_pypy
import testutils  # noqa: F401 - sometimes used in debugging


if not can_use_wgpu_lib:
    pytest.skip("Skipping tests that need wgpu lib", allow_module_level=True)


DEVICE = wgpu.utils.get_default_device()


def make_draw_func_for_canvas(canvas):
    """Create a draw function for the given canvas,
    so that we can really present something to a canvas being tested.
    """
    ctx = canvas.get_context("wgpu")
    ctx.configure(device=DEVICE, format=None)

    def draw():
        ctx = canvas.get_context("wgpu")
        command_encoder = DEVICE.create_command_encoder()
        current_texture_view = ctx.get_current_texture().create_view()
        render_pass = command_encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": current_texture_view,
                    "resolve_target": None,
                    "clear_value": (1, 1, 1, 1),
                    "load_op": wgpu.LoadOp.clear,
                    "store_op": wgpu.StoreOp.store,
                }
            ],
        )
        render_pass.end()
        DEVICE.queue.submit([command_encoder.finish()])

    return draw


@create_and_release
def test_release_canvas_context(n):
    # Test with offscreen canvases. A context is created, but not a wgpu-native surface.

    # Note: the offscreen canvas keeps the render-texture alive, since it
    # is used to e.g. download the resulting image, and who knows how the
    # user want to use the result. The context does drop its ref to the
    # textures, which is why we don't see textures in the measurements.

    from rendercanvas.offscreen import RenderCanvas

    yield {
        "expected_counts_after_create": {},
        "ignore": {"CommandBuffer", "Buffer"},
    }

    canvases = weakref.WeakSet()
    for i in range(n):
        c = RenderCanvas()
        canvases.add(c)
        c.request_draw(make_draw_func_for_canvas(c))
        c.draw()
        yield c.get_context("wgpu")

    del c
    gc.collect()
    if is_pypy:
        gc.collect()  # Need a bit more on pypy :)
        gc.collect()

    # Check that the canvas objects are really deleted
    assert not canvases


@create_and_release
def test_release_surface(n):
    # A surface needs a real window to wrap, so an offscreen canvas never makes
    # one -- see the note above. wgpu-core does not track surfaces either, so
    # there is no native count to compare against.
    yield {
        "expected_counts_after_create": {"Surface": (n, 0)},
    }
    if not can_use_glfw:
        pytest.skip("Need glfw for a real window to make a surface from")

    from rendercanvas.glfw import RenderCanvas

    canvases = []
    for i in range(n):
        canvas = RenderCanvas()
        canvases.append(canvas)
        yield canvas.get_context("wgpu")._surface


TEST_FUNCS = [test_release_canvas_context, test_release_surface]


if __name__ == "__main__":
    # testutils.TEST_ITERS = 40  # Uncomment for a mem-usage test run

    for func in TEST_FUNCS:
        print(func.__name__ + " ...")
        try:
            func()
        except pytest.skip.Exception:
            print("  skipped")
    print("done")
