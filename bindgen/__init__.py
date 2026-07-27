"""Fully-automated binding generator for wgpu-native.

The pipeline has two independently-testable halves:

1. **Low-level binding** (:mod:`bindgen.headers`, :mod:`bindgen.ffi_build`):
   clean the wgpu-native submodule headers and compile a cffi API-mode
   extension statically linked against ``libwgpu_native.a``. This exposes the
   entire C API (~228 functions) with zero per-symbol manual work.

2. **High-level Pythonic layer** (planned): generated from the machine-readable
   ``webgpu.json`` spec (objects -> classes, methods, enums, flags, structs),
   replacing the hand-written API of the previous cffi ABI-mode implementation.

Nothing here is hand-maintained per symbol: bumping the ``wgpu-native``
submodule and re-running the generator is the entire upgrade process.
"""

from .headers import build_cdef, clean_header

__all__ = ["build_cdef", "clean_header"]
