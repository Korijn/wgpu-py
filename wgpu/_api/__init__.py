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
    GPUCommandEncoder,
    GPUDevice,
    GPUObjectBase,
    GPUQueue,
    GPURenderBundleEncoder,
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
GPUCommandEncoder.begin_render_pass = _ov.drop_inapplicable_aspect_ops(
    GPUCommandEncoder.begin_render_pass
)
GPUCommandEncoder.clear_buffer = _ov.check_clear_range(GPUCommandEncoder.clear_buffer)
GPUCommandEncoder.copy_texture_to_buffer = _ov.check_copy_alignment(
    GPUCommandEncoder.copy_texture_to_buffer
)
GPUBuffer.read_mapped = _x.buffer_read_mapped
GPUBuffer.write_mapped = _x.buffer_write_mapped
GPUDevice.create_buffer_with_data = _x.device_create_buffer_with_data
GPUDevice.adapter = _x.device_adapter
GPUQueue.read_buffer = _x.queue_read_buffer
GPUQueue.read_texture = _x.queue_read_texture
GPUAdapter.summary = _x.adapter_summary
GPUDevice._poll = _ov.device_poll
GPUDevice._poll_wait = _ov.device_poll_wait
GPUTexture.size = _x.texture_size
GPUTexture._nbytes = _x.texture_nbytes
GPUTextureView.size = _x.texture_view_size
GPUTextureView.texture = _x.texture_view_texture
# Alone among the encoders, this one reads its arguments again at finish().
_ov.retain_on(GPURenderBundleEncoder)

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
    # The IDL's own base interface: every GPU object is one, and downstream
    # code isinstance-checks against it.
    "GPUObjectBase",
    "GPUOutOfMemoryError",
    "GPUPipelineError",
    "GPUPromise",
    "GPUValidationError",
    "DrawCancelled",
]
