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

#: The machine-readable WebGPU **C** spec: source of truth for the FFI layer.
WEBGPU_JSON = WEBGPU_HEADERS_DIR / "webgpu.json"

#: The W3C WebGPU **Web IDL**: source of truth for the *public* API shape --
#: class/method names, enum string values, struct fields and their defaults.
#: Vendored (rather than taken from the submodule) because it tracks the W3C
#: spec, not wgpu-native.
WEBGPU_IDL = Path(__file__).resolve().parent / "resources" / "webgpu.idl"


#: wgpu-native's own list of C functions it declares but does not implement.
#: Calling one aborts the process (a Rust ``unimplemented!()`` cannot unwind
#: across FFI), so the generator reads this to emit a guard instead of a call.
UNIMPLEMENTED_RS = NATIVE_ROOT / "src" / "unimplemented.rs"


def unimplemented_functions() -> frozenset[str]:
    """The C functions wgpu-native declares but does not implement."""
    import re

    if not UNIMPLEMENTED_RS.exists():
        return frozenset()
    return frozenset(
        re.findall(r'pub extern "C" fn (wgpu\w+)', UNIMPLEMENTED_RS.read_text())
    )


#: wgpu-native's implementation of the C API, read to find the handles whose
#: ``Destroy`` already frees what their ``Drop`` will free again.
LIB_RS = NATIVE_ROOT / "src" / "lib.rs"


def _first_drop_call(body: str) -> str | None:
    import re

    match = re.search(r"\b(\w+_drop)\(", body)
    return match.group(1) if match else None


def destroy_consumes_handle() -> frozenset[str]:
    """The ``wgpuXDestroy`` functions that also free the handle for good.

    Destroying is meant to be quite different from releasing: the wgpu-core
    resource enters a destroyed state, while the handle stays valid, so
    ``destroy()`` followed by a release is the ordinary case. For query sets it
    is fatal. wgpu-native implements ``wgpuQuerySetDestroy`` by *dropping* the
    wgpu-core resource -- its own source says "FIXME: we shouldn't be using
    drop to implement this!" -- and then the handle's ``Drop`` impl drops it a
    second time when the release lands. wgpu-core panics on the vacant slot,
    and a Rust panic cannot unwind across FFI, so the process aborts.

    Rather than name query sets here, the pair is *found*: a ``Destroy`` and a
    ``Drop`` that call the same ``*_drop`` for the same object. A submodule
    bump that fixes this upstream, or breaks another object the same way, is
    then picked up without an edit -- and if the search ever matches nothing,
    that is the fix having landed, not the check silently lapsing.
    """
    import re

    if not LIB_RS.exists():
        return frozenset()
    source = LIB_RS.read_text()
    destroyed = {
        name: fn
        for name, body in re.findall(
            r'pub unsafe extern "C" fn wgpu(\w+)Destroy\b(.*?)\n\}', source, re.S
        )
        if (fn := _first_drop_call(body))
    }
    dropped = {
        name: fn
        for name, body in re.findall(
            r"impl Drop for WGPU(\w+)Impl \{(.*?)\n\}", source, re.S
        )
        if (fn := _first_drop_call(body))
    }
    return frozenset(
        f"wgpu{name}Destroy"
        for name, fn in destroyed.items()
        if dropped.get(name) == fn
    )


def native_version() -> str:
    """The wgpu-native version this build is pinned to.

    Read from the submodule's git tag: wgpu-native's Cargo version is a
    placeholder, and wgpuGetVersion() reports 0.0.0.0 unless the build sets it.
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            cwd=NATIVE_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return proc.stdout.strip().lstrip("v") or "unknown"


def native_commit_sha() -> str:
    """The wgpu-native commit this build is pinned to.

    With the library statically linked into the extension there is no file to
    inspect afterwards, so the exact commit is recorded at generation time --
    it is the only way to tell two builds of the same tag apart.
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=NATIVE_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return proc.stdout.strip() or "unknown"


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
