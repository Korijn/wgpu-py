"""The shipped runtime must never import the generator.

``bindgen`` is a development-only package: it is not part of the wheel. If any
module under ``wgpu/`` imports it, an installed wheel breaks at runtime. This
guards that boundary both statically and by executing the real call path with
``bindgen`` made unimportable.
"""

import builtins
import re
import subprocess
import sys
import textwrap

import pytest

from bindgen import paths
from bindgen.generate import GENERATED_DIR


def test_no_source_level_bindgen_imports():
    """Static check: no shipped module imports the development-only generator."""
    offenders = []
    # Match real import statements only: the generated files name the generator
    # in their "do not edit" banner, which is documentation, not a dependency.
    pattern = re.compile(r"^\s*(from|import)\s+bindgen\b", re.MULTILINE)
    for sub in ("_runtime", "_generated", "_api", "_native"):
        for path in (paths.REPO_ROOT / "wgpu" / sub).rglob("*.py"):
            if pattern.search(path.read_text()):
                offenders.append(str(path.relative_to(paths.REPO_ROOT)))
    assert not offenders, f"runtime modules must not import the generator: {offenders}"


@pytest.mark.skipif(
    not (GENERATED_DIR / "classes.py").exists()
    or not list((paths.REPO_ROOT / "wgpu" / "_native").glob("_wgpu.*")),
    reason="run bindgen.ffi_build + bindgen.generate first",
)
def test_runtime_works_without_bindgen_installed():
    """Execute the real path in a subprocess where importing bindgen fails."""
    code = textwrap.dedent(
        f"""
        import builtins, sys
        sys.path.insert(0, {str(paths.REPO_ROOT)!r})
        _real = builtins.__import__
        def _guard(name, *a, **k):
            if name == "bindgen" or name.startswith("bindgen."):
                raise ImportError("simulating an installed wheel")
            return _real(name, *a, **k)
        builtins.__import__ = _guard

        from wgpu._runtime.api import get_api
        from wgpu._generated import apiflags as flags
        api = get_api()
        instance = api.create_instance()
        adapter = instance.request_adapter().sync_wait()
        if adapter is None or not adapter._handle:
            print("SKIP")           # no driver in this environment
        else:
            device = adapter.request_device_sync()
            buf = device.create_buffer(
                label="x", size=16, usage=flags.BufferUsage.MAP_READ
            )
            device.queue            # object return wrapping
            del buf                 # release() via the generated release_func
            print("OK")
        """
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    if out == "SKIP":
        pytest.skip("no adapter available")
    assert out == "OK", proc.stdout + proc.stderr


def test_guard_helper_is_sane():
    """Sanity: the import guard used above really does block bindgen."""
    real = builtins.__import__
    try:

        def guard(name, *a, **k):
            if name.startswith("bindgen"):
                raise ImportError("blocked")
            return real(name, *a, **k)

        builtins.__import__ = guard
        with pytest.raises(ImportError):
            __import__("bindgen")
    finally:
        builtins.__import__ = real
