"""Hand-written runtime support for the generated high-level layer.

Small and dependency-light on purpose: the generated code declares *what* the
API looks like, and this package provides the *how* (struct marshalling, and
later the async future primitive and buffer mapping). Nothing here is
per-symbol, so submodule bumps do not touch it.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def get_struct_builder():
    """Return a process-wide :class:`StructBuilder` wired to the generated tables."""
    from wgpu import _native
    from wgpu._generated import constants, structs

    from .structs import StructBuilder

    return StructBuilder(_native.ffi, structs.STRUCTS, constants)
