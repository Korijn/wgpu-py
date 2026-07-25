"""Features wgpu-native offers beyond the WebGPU specification.

Everything here is non-standard: it will not work on the web, and it is not
generated from ``webgpu.idl``, because the IDL does not describe it. The
functions are thin wrappers over ``wgpu.h``, which the FFI layer already binds.
"""

from __future__ import annotations

from wgpu._native import ffi as _ffi
from wgpu._native import lib as _lib
from wgpu._runtime.api import get_api


class PipelineStatisticName:
    """The statistics a statistics query set can record."""

    VertexShaderInvocations = "vertex-shader-invocations"
    ClipperInvocations = "clipper-invocations"
    ClipperPrimitivesOut = "clipper-primitives-out"
    FragmentShaderInvocations = "fragment-shader-invocations"
    ComputeShaderInvocations = "compute-shader-invocations"


_PIPELINE_STATISTICS = {
    PipelineStatisticName.VertexShaderInvocations: 0,
    PipelineStatisticName.ClipperInvocations: 1,
    PipelineStatisticName.ClipperPrimitivesOut: 2,
    PipelineStatisticName.FragmentShaderInvocations: 3,
    PipelineStatisticName.ComputeShaderInvocations: 4,
}


# -- timestamps and pipeline statistics --------------------------------------


def write_timestamp(encoder, query_set, query_index):
    """Write a timestamp into ``query_set`` at ``query_index``.

    Unlike the standard timestamp writes, this can be issued anywhere in a
    command encoder rather than only at a pass boundary.
    """
    _lib.wgpuCommandEncoderWriteTimestamp(
        encoder._handle, query_set._handle, int(query_index)
    )


def create_statistics_query_set(device, *, label="", count, statistics):
    """Create a query set that records pipeline statistics."""
    unknown = [s for s in statistics if s not in _PIPELINE_STATISTICS]
    if unknown:
        raise ValueError(f"Unknown pipeline statistics: {unknown}")
    values = [_PIPELINE_STATISTICS[s] for s in statistics]

    api = get_api()
    stats = _ffi.new("WGPUNativeQueryType[]", values)
    ext = _ffi.new("WGPUQuerySetDescriptorExtras *")
    ext.chain.sType = _lib.WGPUSType_QuerySetDescriptorExtras
    ext.pipelineStatistics = stats
    ext.pipelineStatisticCount = len(values)

    descriptor = _ffi.new("WGPUQuerySetDescriptor *")
    descriptor.type = _lib.WGPUNativeQueryType_PipelineStatistics
    descriptor.count = int(count)
    descriptor.nextInChain = _ffi.addressof(ext.chain)
    if label:
        data = _ffi.new("char[]", label.encode())
        descriptor.label.data = data
        descriptor.label.length = len(label.encode())
        _keep = data  # noqa: F841 - alive until the call returns
    handle = _lib.wgpuDeviceCreateQuerySet(device._handle, descriptor)
    api.errors.raise_if_error()
    return api.registry["query_set"](handle, device._pump, device)


def begin_pipeline_statistics_query(pass_encoder, query_set, query_index):
    """Start recording pipeline statistics into ``query_set``."""
    func = _statistics_func(pass_encoder, "BeginPipelineStatisticsQuery")
    func(pass_encoder._handle, query_set._handle, int(query_index))


def end_pipeline_statistics_query(pass_encoder):
    """Stop recording pipeline statistics."""
    func = _statistics_func(pass_encoder, "EndPipelineStatisticsQuery")
    func(pass_encoder._handle)


def _statistics_func(pass_encoder, suffix):
    kind = {
        "compute_pass_encoder": "ComputePassEncoder",
        "render_pass_encoder": "RenderPassEncoder",
    }.get(pass_encoder._spec_name)
    if kind is None:
        raise TypeError(
            f"pipeline statistics need a compute or render pass, "
            f"got {type(pass_encoder).__name__}"
        )
    return getattr(_lib, f"wgpu{kind}{suffix}")


# -- multi-draw --------------------------------------------------------------


def multi_draw_indirect(render_pass_encoder, buffer, *, offset=0, count):
    """Issue ``count`` indirect draws from one buffer, in a single call.

    This is the point of it: one call instead of ``count`` calls, so the draw
    parameters never make the trip through Python.
    """
    _lib.wgpuRenderPassEncoderMultiDrawIndirect(
        render_pass_encoder._handle, buffer._handle, int(offset), int(count)
    )


def multi_draw_indexed_indirect(render_pass_encoder, buffer, *, offset=0, count):
    """Indexed form of `multi_draw_indirect()`."""
    _lib.wgpuRenderPassEncoderMultiDrawIndexedIndirect(
        render_pass_encoder._handle, buffer._handle, int(offset), int(count)
    )


def multi_draw_indirect_count(
    render_pass_encoder, buffer, *, offset=0, count_buffer, count_buffer_offset=0,
    max_count,
):
    """Like `multi_draw_indirect()`, with the draw count read from the GPU."""
    _lib.wgpuRenderPassEncoderMultiDrawIndirectCount(
        render_pass_encoder._handle, buffer._handle, int(offset),
        count_buffer._handle, int(count_buffer_offset), int(max_count),
    )


def multi_draw_indexed_indirect_count(
    render_pass_encoder, buffer, *, offset=0, count_buffer, count_buffer_offset=0,
    max_count,
):
    """Indexed form of `multi_draw_indirect_count()`."""
    _lib.wgpuRenderPassEncoderMultiDrawIndexedIndirectCount(
        render_pass_encoder._handle, buffer._handle, int(offset),
        count_buffer._handle, int(count_buffer_offset), int(max_count),
    )


# -- misc --------------------------------------------------------------------


def enumerate_adapters(instance=None):
    """Every adapter wgpu-native can see, not just the one it would pick."""
    import wgpu

    api = get_api()
    inst = instance or wgpu.gpu._inst
    n = _lib.wgpuInstanceEnumerateAdapters(inst._handle, _ffi.NULL, _ffi.NULL)
    if not n:
        return []
    handles = _ffi.new("WGPUAdapter[]", n)
    _lib.wgpuInstanceEnumerateAdapters(inst._handle, _ffi.NULL, handles)
    cls = api.registry["adapter"]
    return [cls(handles[i], inst._pump, inst) for i in range(n)]


def request_device_sync(adapter, trace_path=None, **kwargs):
    """Request a device, optionally writing a wgpu-native API trace."""
    if trace_path:
        raise NotImplementedError("API tracing is not wired up yet")
    return adapter.request_device_sync(**kwargs)
