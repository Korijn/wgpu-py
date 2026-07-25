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


# Fallbacks used only when cargo cannot be queried (e.g. building from an
# sdist without a Rust toolchain). The authoritative list comes from rustc.
_FALLBACK_STATIC_LIBS = {
    "linux": ["dl", "gcc_s", "util", "rt", "pthread", "m", "c"],
    "darwin": ["c", "m"],
    "win32": ["ws2_32", "userenv", "bcrypt", "ntdll", "advapi32", "kernel32"],
}


#: Apple frameworks wgpu-native needs, used only if rustc cannot be queried.
_FALLBACK_FRAMEWORKS = ("Metal", "QuartzCore", "CoreFoundation", "Foundation")


def native_link_spec(profile: str = "release") -> tuple[list[str], list[str]]:
    """Return ``(libraries, extra_link_args)`` for linking the Rust staticlib.

    Asks rustc directly (``--print native-static-libs``) rather than hardcoding
    per-platform lists, so a new target -- or an upstream dependency change --
    needs no edits here. Apple ``-framework X`` pairs are kept as link args
    (passing ``X`` as a plain library would emit a bogus ``-lX``). Falls back to
    known-good lists when cargo is unavailable (e.g. building from an sdist).
    """
    spec = _query_native_link_spec(profile)
    if spec is not None:
        return spec
    libs: list[str] = []
    for key, value in _FALLBACK_STATIC_LIBS.items():
        if sys.platform.startswith(key):
            libs = list(value)
            break
    args: list[str] = []
    if sys.platform.startswith("darwin"):
        for framework in _FALLBACK_FRAMEWORKS:
            args += ["-framework", framework]
    return libs, args


def native_static_libs(profile: str = "release") -> list[str]:
    """Just the plain libraries; see :func:`native_link_spec`."""
    return native_link_spec(profile)[0]


def _query_native_link_spec(profile: str) -> tuple[list[str], list[str]] | None:
    """Parse ``native-static-libs`` out of a rustc build of wgpu-native."""
    import re
    import subprocess

    cmd = ["cargo", "rustc", "--lib", "--quiet"]
    if profile == "release":
        cmd.append("--release")
    cmd += ["--", "--print", "native-static-libs"]
    try:
        proc = subprocess.run(
            cmd, cwd=NATIVE_ROOT, capture_output=True, text=True, timeout=1800
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"native-static-libs:\s*(.+)", proc.stderr)
    if not match:
        return None

    libs: list[str] = []
    args: list[str] = []
    tokens = match.group(1).split()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token == "-framework" and index < len(tokens):
            framework = tokens[index]
            index += 1
            if ["-framework", framework] not in [
                args[i : i + 2] for i in range(0, len(args), 2)
            ]:
                args += ["-framework", framework]
            continue
        if token.startswith("-l"):
            token = token[2:].split("=")[-1]  # -lstatic=foo -> foo
        elif token.endswith(".lib"):
            token = token[: -len(".lib")]
        elif token.startswith("-"):
            continue  # some other linker flag we do not need to forward
        if token and token not in libs:
            libs.append(token)
    if not libs and not args:
        return None
    return libs, args
