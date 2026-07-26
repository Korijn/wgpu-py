"""Wires the generated tables + runtime into a live, singleton API facade."""

from __future__ import annotations

from functools import lru_cache

from .errors import ErrorSink


class Api:
    def __init__(self):
        from wgpu import _native
        from wgpu._generated import apiclasses, apienums, apiflags, classes
        from wgpu._generated import constants, objects, structs

        from .invoke import Invoker
        from .structs import StructBuilder

        self.ffi = _native.ffi
        self.lib = _native.lib
        self.objects = objects.OBJECTS

        # spec object-name -> Python class, for wrapping returned handles. Most
        # come from the IDL-derived classes; ``classes`` covers the objects the
        # IDL does not model (the instance, the surface).
        self.registry = {
            cls._spec_name: cls
            for module in (classes, apiclasses)
            for cls in vars(module).values()
            if isinstance(cls, type) and getattr(cls, "_spec_name", "")
        }

        builder = StructBuilder(
            self.ffi, structs.STRUCTS, constants, apienums, apiflags
        )
        self.invoker = Invoker(
            self.ffi, self.lib, builder, self.registry, apienums, apiflags
        )

        # method lookup: (spec_object, method_py) -> Method descriptor
        self._methods = {
            (name, m.py): m for name, ot in self.objects.items() for m in ot.methods
        }

        self.errors = ErrorSink()
        self._error_callbacks = self._build_error_callbacks()
        # Generated methods that build their descriptor inline call C directly,
        # so they check for errors themselves rather than via invoke().
        from wgpu._api import base

        base.publish_error_tracker(self.errors)

    # -- error plumbing ----------------------------------------------------

    def _build_error_callbacks(self) -> dict:
        """Create the C callbacks that feed :attr:`errors`.

        Kept alive for the process lifetime: wgpu-native holds these pointers
        for as long as the device exists.
        """
        ffi, sink = self.ffi, self.errors

        def _text(view):
            if not view.data:
                return ""
            return ffi.string(view.data, view.length).decode("utf-8", "replace")

        @ffi.callback(
            "void(WGPUDevice const *, WGPUErrorType, WGPUStringView, void *, void *)"
        )
        def uncaptured_error(device, error_type, message, ud1, ud2):
            sink.record(error_type, _text(message))

        @ffi.callback(
            "void(WGPUDevice const *, WGPUDeviceLostReason, WGPUStringView, void *, void *)"
        )
        def device_lost(device, reason, message, ud1, ud2):
            sink.record_device_lost(reason, _text(message))

        return {"uncaptured_error": uncaptured_error, "device_lost": device_lost}

    def device_descriptor_with_error_handling(self, descriptor=None) -> dict:
        """Add the error/device-lost callbacks to a device descriptor.

        Without these, wgpu-native panics on a validation error, and that panic
        aborts the process instead of raising something catchable.
        """
        descriptor = dict(descriptor or {})
        descriptor.setdefault(
            "uncaptured_error_callback_info",
            {"callback": self._error_callbacks["uncaptured_error"]},
        )
        descriptor.setdefault(
            "device_lost_callback_info",
            {
                "callback": self._error_callbacks["device_lost"],
                "mode": self.lib.WGPUCallbackMode_AllowProcessEvents,
            },
        )
        return descriptor

    def invoke(self, caller, method_py: str, args):
        method = self._methods[(caller._spec_name, method_py)]
        if method.c_func == "wgpuAdapterRequestDevice":
            args = (self.device_descriptor_with_error_handling(*args[:1]),)
        result = self.invoker.call(method, caller, args)
        # wgpu-native reports errors asynchronously via the callback above; the
        # classic implementation surfaces them at the next call boundary too.
        self.errors.raise_if_error()
        return result

    def release(self, spec_object: str, handle):
        getattr(self.lib, self.objects[spec_object].release_func)(handle)

    def create_instance(self, descriptor=None):
        """Entry point: create the root :class:`GPUInstance`."""
        ptr = self.ffi.NULL
        if descriptor:
            ptr, _keep = self.invoker.structs.new("instance_descriptor", descriptor)
        handle = self.lib.wgpuCreateInstance(ptr)
        if not handle:
            raise RuntimeError("wgpuCreateInstance failed to create an instance")
        # The instance's pump drives every downstream async op; child objects
        # inherit it as they are created.
        pump = lambda: self.lib.wgpuInstanceProcessEvents(handle)
        return self.registry["instance"](handle, pump)


@lru_cache(maxsize=1)
def get_api() -> Api:
    return Api()
