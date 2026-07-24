"""Filesystem locations for the wgpu-native submodule and its build artifacts."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NATIVE_ROOT = REPO_ROOT / "wgpu-native"
FFI_DIR = NATIVE_ROOT / "ffi"
WEBGPU_HEADERS_DIR = FFI_DIR / "webgpu-headers"

#: Directories to pass to the C compiler as ``-I`` include paths.
INCLUDE_DIRS = (str(FFI_DIR), str(WEBGPU_HEADERS_DIR))

#: The machine-readable WebGPU spec (source of truth for the high-level layer).
WEBGPU_JSON = WEBGPU_HEADERS_DIR / "webgpu.json"


def static_lib_path(profile: str = "release") -> Path:
    """Path to the compiled ``libwgpu_native`` static archive."""
    if sys.platform.startswith("win"):
        name = "wgpu_native.lib"
    else:
        name = "libwgpu_native.a"
    return NATIVE_ROOT / "target" / profile / name


def native_static_libs() -> list[str]:
    """System libraries Rust's staticlib must be linked against, per platform.

    These come from ``rustc --print native-static-libs`` for wgpu-native and are
    required when statically linking the archive into the CPython extension.
    """
    if sys.platform.startswith("linux"):
        return ["dl", "gcc_s", "util", "rt", "pthread", "m", "c"]
    if sys.platform.startswith("darwin"):
        # wgpu-native links these system frameworks/libs on macOS.
        return ["System", "c", "m"]
    # Windows links these via the MSVC toolchain; handled with extra_link_args.
    return []
