"""Turn a generated ``Method`` descriptor + Python args into a live C call.

Split into a *marshalling* step (Python args -> tuple of cffi values) and the
*call + wrap* step, so marshalling can be unit-tested without a GPU. Array and
raw-pointer (``c_void``) arguments, and struct out-returns, are the handful of
cases not yet covered; they raise a clear ``NotImplementedError`` rather than
silently doing the wrong thing.
"""

from __future__ import annotations

from typing import Any

from .awaitable import WgpuFuture


class Unsupported(NotImplementedError):
    """Raised for method shapes the invoker does not marshal yet."""


class Invoker:
    def __init__(self, ffi, lib, struct_builder, registry, enums, flags):
        self.ffi = ffi
        self.lib = lib
        self.structs = struct_builder
        self.registry = registry  # spec object-name -> Python class
        self.enums = enums
        self.flags = flags

    # -- argument marshalling (unit-testable, no C call) -------------------

    def marshal_args(self, method, py_args) -> tuple[list, list]:
        """Return ``(c_args, keepalive)`` for the fixed (non-callback) args."""
        if len(py_args) != len(method.args):
            raise TypeError(
                f"{method.py}() takes {len(method.args)} args, got {len(py_args)}"
            )
        c_args: list[Any] = []
        keep: list[Any] = []
        for arg, value in zip(method.args, py_args):
            c_args.extend(self._marshal_one(arg, value, keep))
        return c_args, keep

    def _marshal_one(self, arg, value, keep: list) -> list:
        if arg.array:
            raise Unsupported(f"array argument {arg.py!r} not yet supported")
        if arg.kind == "struct":
            if value is None and arg.optional:
                return [self.ffi.NULL]
            ptr, k = self.structs.new(arg.ref, value or {})
            keep.append(k)
            return [ptr]
        if arg.kind == "object":
            return [self._unwrap(value)]
        if arg.kind == "string":
            return [self._string_value(value, keep)]
        if arg.kind in ("enum", "bitflag", "prim"):
            return [int(value)]
        if arg.kind == "c_void":
            raise Unsupported(f"raw-pointer argument {arg.py!r} not yet supported")
        raise Unsupported(f"argument kind {arg.kind!r}")

    def _unwrap(self, value):
        if value is None:
            return self.ffi.NULL
        return getattr(value, "_handle", value)  # class instance or raw handle

    def _string_value(self, value, keep: list):
        sv = self.ffi.new("WGPUStringView *")
        if value is None:
            sv.data, sv.length = self.ffi.NULL, 0
        else:
            data = self.ffi.new("char[]", value.encode("utf-8"))
            keep.append(data)
            sv.data, sv.length = data, len(value.encode("utf-8"))
        keep.append(sv)
        return sv[0]  # WGPUStringView is passed by value

    # -- return wrapping ---------------------------------------------------

    def wrap_return(self, method, c_ret):
        kind, ref = method.ret_kind, method.ret_ref
        if kind is None:
            return None
        if kind == "object":
            if not c_ret:  # NULL
                return None if method.ret_optional else c_ret
            return self._wrap_object(ref, c_ret)
        if kind == "enum":
            return getattr(self.enums, _py(ref))(c_ret)
        if kind == "bitflag":
            return getattr(self.flags, _py(ref))(c_ret)
        if kind == "prim":
            return bool(c_ret) if ref == "bool" else c_ret
        if kind in ("struct", "c_void"):
            raise Unsupported(f"{kind} return not yet supported ({method.c_func})")
        return c_ret

    def _wrap_object(self, ref, handle):
        cls = self.registry.get(ref)
        return cls(handle) if cls is not None else handle

    # -- the call ----------------------------------------------------------

    def call(self, method, self_handle, py_args, pump=None):
        c_args, keep = self.marshal_args(method, py_args)
        cfunc = getattr(self.lib, method.c_func)
        if method.is_async:
            return self._call_async(method, cfunc, self_handle, c_args, keep, pump)
        c_ret = cfunc(self_handle, *c_args)
        return self.wrap_return(method, c_ret)

    def _call_async(self, method, cfunc, self_handle, c_args, keep, pump):
        if pump is None:
            raise RuntimeError(f"{method.py}() is async but no pump was provided")
        future = WgpuFuture(pump)
        info = self.ffi.new(method.callback_info_c + " *")
        info.mode = self.lib.WGPUCallbackMode_AllowProcessEvents

        cb_ctype = self.ffi.getctype(
            dict(self.ffi.typeof(info).item.fields)["callback"].type
        )

        @self.ffi.callback(cb_ctype)
        def _cb(status, *rest):
            # Callback shape: (status, [result], message, ud1, ud2). When the op
            # produces an object (request_adapter/device, pipeline-async) the
            # result handle precedes the message; otherwise there is only status.
            try:
                if len(rest) >= 3:  # (result, message, ud1, ud2)
                    future.set_result(rest[0])
                else:  # (message, ud1, ud2)
                    future.set_result(int(status))
            except BaseException as exc:  # noqa: BLE001
                future.set_error(exc)

        info.callback = _cb
        future._keep = (info, _cb, keep)  # keep alive until fired
        cfunc(self_handle, *c_args, info[0])
        return future


def _py(spec_name: str) -> str:
    from bindgen import naming

    return naming.py_enum_name(spec_name)
