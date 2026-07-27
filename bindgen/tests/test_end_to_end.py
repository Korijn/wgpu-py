"""Real end-to-end validation of the generated API against a live driver.

Runs only when an adapter is available (e.g. Mesa lavapipe/llvmpipe in CI or
locally). Exercises the async future chain and several sync method shapes
without touching wgpu-native functions that are still ``unimplemented``.
"""

import sys

import pytest

from bindgen import paths
from bindgen.generate import GENERATED_DIR

pytestmark = pytest.mark.skipif(
    not (GENERATED_DIR / "classes.py").exists()
    or not list((paths.REPO_ROOT / "wgpu" / "_native").glob("_wgpu.*")),
    reason="run bindgen.ffi_build + bindgen.generate first",
)


@pytest.fixture(scope="module")
def device():
    sys.path.insert(0, str(paths.REPO_ROOT))
    from wgpu._runtime.api import get_api

    instance = get_api().create_instance()
    future = instance.request_adapter()
    adapter = future.sync_wait()
    if adapter is None or not adapter._handle:
        pytest.skip("no adapter available (install a Vulkan driver, e.g. lavapipe)")
    return adapter.request_device_sync()


def test_async_chain_yields_device(device):
    assert type(device).__name__ == "GPUDevice"
    assert device._handle
    # The pump propagated from the instance down the async chain.
    assert device._pump is not None


def test_sync_object_returns(device):
    queue = device.queue
    assert type(queue).__name__ == "GPUQueue"
    encoder = device.create_command_encoder()  # optional descriptor omitted
    assert type(encoder).__name__ == "GPUCommandEncoder"


def test_create_buffer_struct_arg(device):
    """The descriptor is flattened into keyword arguments by the generator."""
    buf = device.create_buffer(label="vbuf", usage="COPY_SRC|COPY_DST", size=256)
    assert type(buf).__name__ == "GPUBuffer"
    assert buf.label == "vbuf"
    assert buf.size == 256  # scalar return


def test_gpu_data_roundtrip(device):
    """write -> copy -> submit -> map -> read, all through the generated API.

    Exercises struct args, raw-data (c_void) args, array args (submit), the
    async future, buffer mapping as a memoryview, and object lifetimes.
    """

    data = bytes(range(16))

    queue = device.queue
    src = device.create_buffer(label="src", usage="COPY_SRC|COPY_DST", size=16)
    dst = device.create_buffer(label="dst", usage="COPY_DST|MAP_READ", size=16)

    queue.write_buffer(src, 0, data)  # c_void data arg
    encoder = device.create_command_encoder()
    encoder.copy_buffer_to_buffer(src, 0, dst, 0, 16)
    queue.submit([encoder.finish()])  # array arg -> (count, ptr)

    assert dst.map_async("READ", 0, 16).sync_wait() == 1
    view = dst.get_mapped_range(0, 16)  # c_void return -> memoryview
    assert isinstance(view, memoryview)
    assert bytes(view) == data
    dst.unmap()


def test_await_path_matches_sync(device):
    import asyncio

    from wgpu._runtime.api import get_api

    async def main():
        adapter = await get_api().create_instance().request_adapter()
        return adapter

    adapter = asyncio.run(main())
    assert type(adapter).__name__ == "GPUAdapter"


def test_derived_chain_adapter(device):
    """A field C expresses as a chained extension struct, derived not declared.

    Regression: this was hand-written against a struct name that does not
    exist, so every caller got a KeyError. Nothing pointed it out, because
    nothing called it -- hence this test.
    """
    texture = device.create_texture(
        size=(4, 4, 1),
        format="rgba8unorm",
        usage="TEXTURE_BINDING",
        texture_binding_view_dimension="2d",
    )
    assert type(texture).__name__ == "GPUTexture"
    assert texture.size == (4, 4, 1)


def test_derived_nest_adapter(device):
    """The web flattens the texel-copy layout fields; C nests them."""
    texture = device.create_texture(
        size=(64, 4, 1), format="rgba8unorm", usage="COPY_DST|COPY_SRC"
    )
    data = bytes(range(256)) * 4
    device.queue.write_texture(
        {"texture": texture},
        data,
        {"offset": 0, "bytes_per_row": 256, "rows_per_image": 4},
        (64, 4, 1),
    )
    read = device.queue.read_texture(
        {"texture": texture},
        {"offset": 0, "bytes_per_row": 256, "rows_per_image": 4},
        (64, 4, 1),
    )
    assert bytes(read) == data
