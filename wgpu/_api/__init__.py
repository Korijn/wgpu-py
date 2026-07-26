"""The public wgpu API: generated classes plus the hand-written parts.

This module assembles the two halves. The generated classes in
``wgpu._generated`` cover everything the WebGPU spec describes; this package
supplies what the spec does not -- the entrypoint, the canvas context, the info
objects, the error types -- and attaches the conveniences wgpu-py adds on top.

The attachment step below is deliberately explicit: each line is a documented
difference from the web API, so the ways wgpu-py diverges from WebGPU can be
read off in one place instead of being hunted through a 4000-line backend.
"""

from wgpu._api import extras as _x
from wgpu._api import overrides as _ov
from wgpu._api.canvas import GPUCanvasContext
from wgpu._api.gpu import GPU
from wgpu._api.info import (
    GPUAdapterInfo,
    GPUCompilationInfo,
    GPUCompilationMessage,
    GPUDeviceLostInfo,
)
from wgpu._generated.apiclasses import *  # noqa: F403
from wgpu._generated.apiclasses import (
    GPUAdapter,
    GPUBuffer,
    GPUDevice,
    GPUObjectBase,
    GPUQueue,
    GPUTexture,
    GPUTextureView,
)
from wgpu._generated.apiclasses import __all__ as _generated_all
from wgpu._api.promise import GPUPromise
from wgpu._runtime.errors import (
    DrawCancelled,
    GPUError,
    GPUInternalError,
    GPUOutOfMemoryError,
    GPUPipelineError,
    GPUValidationError,
)

# -- wgpu-py additions to the WebGPU API -------------------------------------

GPUDevice.create_buffer = _ov.track_mapped_at_creation(GPUDevice.create_buffer)
GPUBuffer.read_mapped = _x.buffer_read_mapped
GPUBuffer.write_mapped = _x.buffer_write_mapped
GPUDevice.create_buffer_with_data = _x.device_create_buffer_with_data
GPUDevice.adapter = _x.device_adapter
GPUQueue.read_buffer = _x.queue_read_buffer
GPUQueue.read_texture = _x.queue_read_texture
GPUAdapter.summary = _x.adapter_summary
GPUTexture.size = _x.texture_size
GPUTextureView.size = _x.texture_view_size

__all__ = [
    *_generated_all,
    "GPU",
    "GPUAdapterInfo",
    "GPUCanvasContext",
    "GPUCompilationInfo",
    "GPUCompilationMessage",
    "GPUDeviceLostInfo",
    "GPUError",
    "GPUInternalError",
    "GPUOutOfMemoryError",
    "GPUPipelineError",
    "GPUPromise",
    "GPUValidationError",
    "DrawCancelled",
]
