"""Hatchling build hook: compile the wgpu-native extension into the wheel.

wgpu-py used to ship a *pure-Python* wheel with a prebuilt wgpu-native shared
library dropped in beside it, downloaded at build time. It now ships a real
CPython extension that statically links ``libwgpu_native.a``, so the wheel is
platform- and interpreter-specific and there is nothing to download: the
binding is compiled from the submodule that is pinned in git.

The two steps are:

1. ``cargo build --release --lib`` in the ``wgpu-native`` submodule, unless the
   static library is already there (so rebuilding the Python side does not pay
   for a Rust build).
2. ``cffi`` compiles ``wgpu/_native/_wgpu.*`` against the submodule headers and
   links that archive in.

Both are skipped when ``WGPU_PY_BUILD_NOARCH`` is set, which produces a
pure-Python artifact -- useful only for inspecting the packaged Python code.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

ROOT = Path(__file__).resolve().parent.parent


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        # https://hatch.pypa.io/latest/plugins/builder/wheel/#build-data
        if self.target_name != "wheel":
            return  # an sdist carries sources; it is compiled on the far side
        if _is_truthy(os.getenv("WGPU_PY_BUILD_NOARCH")):
            return

        sys.path.insert(0, str(ROOT))
        from bindgen import ffi_build, paths

        _build_static_lib(paths)
        self.app.display_info("compiling the wgpu-native extension")
        ffi_build.compile_ffi(ffi_build.NATIVE_DIR, verbose=False)

        # The extension links a C library and embeds the interpreter's ABI, so
        # the wheel is neither pure nor portable across Python versions.
        build_data["pure_python"] = False
        build_data["infer_tag"] = True


def _is_truthy(value: str | None) -> bool:
    return (value or "").lower() in ("1", "true", "yes", "on")


def _build_static_lib(paths) -> None:
    """Build ``libwgpu_native.a``, unless it is already there."""
    profile = os.getenv("WGPU_PY_BUILD_PROFILE", "release")
    if paths.static_lib_path(profile).exists():
        return
    if not (paths.NATIVE_ROOT / "Cargo.toml").exists():
        raise RuntimeError(
            f"{paths.NATIVE_ROOT} is empty. wgpu-py compiles its binding from "
            "the wgpu-native submodule:\n"
            "    git submodule update --init --recursive"
        )
    cmd = ["cargo", "build", "--lib"]
    if profile == "release":
        cmd.append("--release")
    subprocess.run(cmd, cwd=paths.NATIVE_ROOT, check=True)
