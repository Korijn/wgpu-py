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
    adapter = future.wait()
    if adapter is None or not adapter._handle:
        pytest.skip("no adapter available (install a Vulkan driver, e.g. lavapipe)")
    return adapter.request_device().wait()


def test_async_chain_yields_device(device):
    assert type(device).__name__ == "GPUDevice"
    assert device._handle
    # The pump propagated from the instance down the async chain.
    assert device._pump is not None


def test_sync_object_returns(device):
    queue = device.get_queue()
    assert type(queue).__name__ == "GPUQueue"
    encoder = device.create_command_encoder()  # optional descriptor omitted
    assert type(encoder).__name__ == "GPUCommandEncoder"


def test_create_buffer_struct_arg(device):
    from wgpu._generated import flags

    buf = device.create_buffer(
        {"label": "vbuf", "usage": flags.BufferUsage.copy_src | flags.BufferUsage.copy_dst, "size": 256}
    )
    assert type(buf).__name__ == "GPUBuffer"
    assert buf.get_size() == 256  # scalar return


def test_await_path_matches_sync(device):
    import asyncio

    from wgpu._runtime.api import get_api

    async def main():
        adapter = await get_api().create_instance().request_adapter()
        return adapter

    adapter = asyncio.run(main())
    assert type(adapter).__name__ == "GPUAdapter"
