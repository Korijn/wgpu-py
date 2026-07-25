"""Turn the real wgpu-native C headers into cffi-``cdef``-parseable declarations.

The headers we consume live in the ``wgpu-native`` git submodule and are the
authoritative, versioned source of the C API:

* ``wgpu-native/ffi/webgpu-headers/webgpu.h`` -- the standard WebGPU C API,
  itself generated from the machine-readable ``webgpu.yml`` spec.
* ``wgpu-native/ffi/wgpu.h`` -- wgpu-native's own extensions.

cffi's ``cdef`` accepts C declarations but *not* preprocessor directives or the
marker macros the headers sprinkle on every declaration. This module strips
exactly those, leaving clean declarations that pycparser (cffi's parser) can
read. Because we build in cffi *API mode*, the real headers are still compiled
via ``set_source`` and the C compiler verifies every declaration for us -- so
this cleaning only needs to be parseable, not semantically perfect.
"""

from __future__ import annotations

import re
from pathlib import Path

# Empty-in-C marker macros the headers attach to declarations. They carry no
# meaning for the ABI; to pycparser they are just stray identifiers, so blank
# them before parsing.
MARKER_MACROS = (
    "WGPU_OBJECT_ATTRIBUTE",
    "WGPU_ENUM_ATTRIBUTE",
    "WGPU_STRUCTURE_ATTRIBUTE",
    "WGPU_FUNCTION_ATTRIBUTE",
    "WGPU_VERTEX_ATTRIBUTE",
    "WGPU_NULLABLE",
    "WGPU_EXPORT",
)


def _marker_re() -> re.Pattern:
    return re.compile(r"\b(" + "|".join(MARKER_MACROS) + r")\b")


def clean_header(path: str | Path) -> str:
    """Return cffi-``cdef``-parseable declarations for a single C header.

    Steps:

    1. Blank the empty marker macros (see :data:`MARKER_MACROS`).
    2. Use cffi's own preprocessor to strip comments and pull object-like
       ``#define``\\ s out of the body.
    3. Walk the remaining ``#if``/``#endif`` with a stack, dropping only the
       ``__cplusplus`` / ``extern "C"`` blocks and keeping every real
       declaration (crucially, everything inside the ``#ifndef WGPU_H_`` and
       ``#ifndef WEBGPU_H_`` include guards).
    """
    # Imported lazily so importing this module never hard-requires cffi.
    from cffi.cparser import _preprocess as cffi_preprocess

    src = Path(path).read_text().replace("\r\n", "\n").replace("\\\n", "")
    src = _marker_re().sub(" ", src)
    cleaned, _macros = cffi_preprocess(src)  # strips comments + object #defines

    out: list[str] = []
    # One bool per open ``#if*`` block: True means "inside a block we drop".
    drop_stack: list[bool] = []
    for raw in cleaned.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            line = "#" + line[1:].lstrip()  # normalize "#   if" -> "#if"

        if line.startswith(("#ifdef", "#ifndef", "#if")):
            drop_stack.append("__cplusplus" in line)
            continue
        if line.startswith("#elif"):
            continue
        if line.startswith("#else"):
            if drop_stack:
                drop_stack[-1] = not drop_stack[-1]
            continue
        if line.startswith("#endif"):
            if drop_stack:
                drop_stack.pop()
            continue
        if line.startswith(("#include", "#define", "#pragma", "#error", "#undef")):
            continue

        if any(drop_stack):
            continue
        out.append(line)
    return "\n".join(out)


def build_cdef(ffi_dir: str | Path) -> str:
    """Assemble the combined ``cdef`` string for the whole C API surface.

    ``ffi_dir`` is ``wgpu-native/ffi``. ``webgpu.h`` must come first because it
    defines the base types that ``wgpu.h``'s extensions build on.
    """
    ffi_dir = Path(ffi_dir)
    webgpu = clean_header(ffi_dir / "webgpu-headers" / "webgpu.h")
    wgpu = clean_header(ffi_dir / "wgpu.h")
    header = (
        "/* Auto-generated from the wgpu-native submodule headers. Do not edit. */\n"
    )
    return (
        header + webgpu + "\n\n/* ---- wgpu-native extensions (wgpu.h) ---- */\n" + wgpu
    )
