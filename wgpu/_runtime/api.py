"""Wires the generated tables + runtime into a live, singleton API facade."""

from __future__ import annotations

from functools import lru_cache


class Api:
    def __init__(self):
        from wgpu import _native
        from wgpu._generated import classes, constants, enums, flags, objects, structs

        from .invoke import Invoker
        from .structs import StructBuilder

        self.ffi = _native.ffi
        self.lib = _native.lib
        self.objects = objects.OBJECTS

        # spec object-name -> generated Python class, for wrapping return values
        self.registry = {
            cls._spec_name: cls
            for cls in vars(classes).values()
            if isinstance(cls, type) and getattr(cls, "_spec_name", "")
        }

        builder = StructBuilder(self.ffi, structs.STRUCTS, enums, flags, constants)
        self.invoker = Invoker(self.ffi, self.lib, builder, self.registry, enums, flags)

        # method lookup: (spec_object, method_py) -> Method descriptor
        self._methods = {
            (name, m.py): m for name, ot in self.objects.items() for m in ot.methods
        }

    def invoke(self, caller, method_py: str, args):
        method = self._methods[(caller._spec_name, method_py)]
        return self.invoker.call(method, caller, args)

    def release(self, spec_object: str, handle):
        from bindgen import naming

        cfunc = naming.c_object_lifecycle(spec_object, "Release")
        getattr(self.lib, cfunc)(handle)

    def create_instance(self, descriptor=None):
        """Entry point: create the root :class:`GPUInstance`."""
        ptr = self.ffi.NULL
        if descriptor:
            ptr, _keep = self.invoker.structs.new("instance_descriptor", descriptor)
        handle = self.lib.wgpuCreateInstance(ptr)
        # The instance's pump drives every downstream async op; child objects
        # inherit it as they are created.
        pump = lambda: self.lib.wgpuInstanceProcessEvents(handle)  # noqa: E731
        return self.registry["instance"](handle, pump)


@lru_cache(maxsize=1)
def get_api() -> Api:
    return Api()
