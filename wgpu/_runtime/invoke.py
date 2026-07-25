"""Turn a generated ``Method`` descriptor + Python args into a live C call.

Split into a *marshalling* step (Python args -> tuple of cffi values) and the
*call + wrap* step, so marshalling can be unit-tested without a GPU.

Struct out-returns are the one shape not covered yet; that case raises a clear
``UnsupportedError`` rather than silently doing the wrong thing.
"""

from __future__ import annotations

from typing import Any

from .awaitable import WgpuFuture


class UnsupportedError(NotImplementedError):
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
        missing = len(method.args) - len(py_args)
        if missing > 0 and all(
            a.kind == "struct" and a.pointer == "mutable"
            for a in method.args[len(py_args) :]
        ):
            # Out-parameters are allocated by us, never supplied by the caller:
            # ``adapter.limits`` reaches get_limits() with no arguments at all.
            py_args = (*py_args, *(None,) * missing)
        elif len(py_args) != len(method.args):
            raise TypeError(
                f"{method.py}() takes {len(method.args)} args, got {len(py_args)}"
            )
        # The real C signature drives element types for array args, so the
        # marshaller never has to guess a ctype.
        c_sig = self.ffi.typeof(getattr(self.lib, method.c_func)).args
        c_args: list[Any] = []
        keep: list[Any] = []
        pos = 1  # index into c_sig; 0 is the object handle itself
        for arg, value in zip(method.args, py_args, strict=True):
            vals = self._marshal_one(arg, value, keep, c_sig, pos)
            c_args.extend(vals)
            pos += len(vals)
        return c_args, keep

    def _marshal_one(self, arg, value, keep: list, c_sig=None, pos=0) -> list:
        if arg.array:
            # C expands an array argument to (count, pointer), in that order.
            items = list(value or ())
            if not items:
                return [0, self.ffi.NULL]
            elem_ctype = self.ffi.getctype(c_sig[pos + 1].item)
            if arg.kind == "object":
                data = [self._unwrap(it) for it in items]
            elif arg.kind == "enum":
                table = self.enums.TO_INT[arg.ref]
                data = [table[it] for it in items]
            elif arg.kind in ("bitflag", "prim"):
                data = [int(it) for it in items]
            else:
                raise UnsupportedError(
                    f"array of {arg.kind!r} not supported ({arg.py})"
                )
            cdata = self.ffi.new(f"{elem_ctype}[{len(items)}]", data)
            keep.append(cdata)
            return [len(items), cdata]
        if arg.kind == "struct":
            if arg.pointer == "mutable":
                # An out-parameter: allocate it, let C fill it in, and read it
                # back after the call (see ``call``).
                ptr, k = self.structs.new(arg.ref, {})
                keep.append(k)
                keep.append(("out", arg.ref, ptr))
                return [ptr]
            if value is None and arg.optional:
                return [self.ffi.NULL]
            ptr, k = self.structs.new(arg.ref, value or {})
            keep.append(k)
            return [ptr]
        if arg.kind == "object":
            return [self._unwrap(value)]
        if arg.kind == "string":
            return [self._string_value(value, keep)]
        if arg.kind == "enum":
            # None means "undefined", which every WebGPU enum maps to zero.
            return [0 if value is None else self.enums.TO_INT[arg.ref][value]]
        if arg.kind == "bitflag":
            return [0 if value is None else self.flags.TO_INT[arg.ref][value]]
        if arg.kind == "prim":
            if value is None:
                # The only numeric argument WebGPU lets you omit is a size, and
                # omitting it means "the rest of the resource". C spells that as
                # an all-ones sentinel (WGPU_WHOLE_SIZE / WGPU_WHOLE_MAP_SIZE),
                # whose width depends on the parameter -- so take it from the
                # real C signature rather than hardcoding 32 vs 64 bits.
                return [(1 << (8 * self.ffi.sizeof(c_sig[pos]))) - 1]
            return [float(value) if _is_float(arg.ref) else int(value)]
        if arg.kind == "c_void":
            # Raw data: accept any buffer-like object (bytes, bytearray,
            # memoryview, array.array, numpy array, ...).
            if value is None:
                return [self.ffi.NULL]
            cdata = self.ffi.from_buffer(value, require_writable=False)
            keep.append(cdata)
            return [cdata]
        raise UnsupportedError(f"argument kind {arg.kind!r}")

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

    def wrap_return(self, method, c_ret, pump=None, parent=None, py_args=()):
        kind, ref = method.ret_kind, method.ret_ref
        if kind is None:
            return None
        if kind == "object":
            if not c_ret:  # NULL
                return None if method.ret_optional else c_ret
            return self._wrap_object(ref, c_ret, pump, parent)
        if kind == "c_void":
            # A raw pointer return is mapped memory (e.g. get_mapped_range):
            # expose it as a writable memoryview over the GPU-owned bytes. The
            # length comes from the call's own ``size`` argument.
            if not c_ret:
                return None
            size = self._size_arg(method, py_args)
            if size is None:
                raise UnsupportedError(
                    f"{method.c_func}: cannot size the returned pointer"
                )
            return memoryview(self.ffi.buffer(c_ret, size))
        if kind == "enum":
            return self.enums.FROM_INT.get(ref, {}).get(int(c_ret), int(c_ret))
        if kind == "bitflag":
            return int(c_ret)
        if kind == "prim":
            return bool(c_ret) if ref == "bool" else c_ret
        if kind == "struct":
            raise UnsupportedError(f"struct return not yet supported ({method.c_func})")
        return c_ret

    @staticmethod
    def _size_arg(method, py_args):
        for arg, value in zip(method.args, py_args, strict=False):
            if arg.py == "size":
                return int(value)
        return None

    def _wrap_object(self, ref, handle, pump=None, parent=None):
        cls = self.registry.get(ref)
        return cls(handle, pump, parent) if cls is not None else handle

    # -- the call ----------------------------------------------------------

    def call(self, method, caller, py_args):
        self_handle = caller._handle
        pump = caller._pump
        c_args, keep = self.marshal_args(method, py_args)
        cfunc = getattr(self.lib, method.c_func)
        if method.is_async:
            return self._call_async(
                method, cfunc, self_handle, c_args, keep, pump, caller
            )
        c_ret = cfunc(self_handle, *c_args)
        outs = [k for k in keep if isinstance(k, tuple) and k and k[0] == "out"]
        if outs:
            # Getters that report through an out-parameter (limits, features,
            # adapter info) return the filled struct, not the status code.
            _, ref, ptr = outs[0]
            return self.structs.read(ref, ptr[0])
        return self.wrap_return(method, c_ret, pump, caller, py_args)

    def _call_async(self, method, cfunc, self_handle, c_args, keep, pump, caller=None):
        if pump is None:
            raise RuntimeError(f"{method.py}() is async but no event pump is available")
        future = WgpuFuture(pump)
        info = self.ffi.new(method.callback_info_c + " *")
        info.mode = self.lib.WGPUCallbackMode_AllowProcessEvents

        cb_ctype = self.ffi.getctype(
            dict(self.ffi.typeof(info).item.fields)["callback"].type
        )
        result_ref = method.callback_result_ref
        success_status = 1  # every *Status enum uses 1 for success

        @self.ffi.callback(cb_ctype)
        def _cb(status, *rest):
            # Callback shape: (status, [result], message, ud1, ud2). When the op
            # yields an object the handle precedes the message; else status only.
            try:
                if int(status) != success_status:
                    msg = _message(self.ffi, rest)
                    future.set_error(RuntimeError(f"{method.py} failed: {msg}"))
                elif result_ref is not None:
                    future.set_result(
                        self._wrap_object(result_ref, rest[0], pump, caller)
                    )
                else:
                    future.set_result(int(status))
            except BaseException as exc:
                future.set_error(exc)

        info.callback = _cb
        # Keep everything the pending callback depends on alive until it fires
        # (including the caller, whose ancestry backs the event pump).
        future._keep = (info, _cb, keep, caller)
        cfunc(self_handle, *c_args, info[0])
        return future


def _message(ffi, rest) -> str:
    for item in rest:
        if isinstance(item, ffi.CData) and "StringView" in ffi.getctype(
            ffi.typeof(item)
        ):
            return (
                ffi.string(item.data, item.length).decode("utf-8", "replace")
                if item.data
                else ""
            )
    return ""


def _is_float(ref: str | None) -> bool:
    return bool(ref) and ref.startswith(("float", "nullable_float"))
