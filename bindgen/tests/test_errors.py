"""wgpu-native errors must become Python exceptions, not process aborts.

Without a registered uncaptured-error callback, a validation error makes
wgpu-native panic, and that panic cannot unwind across the FFI boundary -- it
aborts the interpreter. These tests pin the behaviour that replaced it.
"""

import subprocess
import sys
import textwrap

import pytest

from bindgen import paths
from bindgen.generate import GENERATED_DIR

pytestmark = pytest.mark.skipif(
    not (GENERATED_DIR / "classes.py").exists()
    or not list((paths.REPO_ROOT / "wgpu" / "_native").glob("_wgpu.*")),
    reason="run bindgen.ffi_build + bindgen.generate first",
)


@pytest.fixture(scope="module")
def compat():
    sys.path.insert(0, str(paths.REPO_ROOT))
    from wgpu import _compat

    return _compat


@pytest.fixture(scope="module")
def device(compat):
    try:
        return compat.get_default_device()
    except RuntimeError as exc:
        pytest.skip(f"no adapter available: {exc}")


def test_validation_error_raises(device):
    from wgpu._runtime.errors import GPUValidationError

    with pytest.raises(GPUValidationError) as excinfo:
        device.create_buffer(size=16, usage=0)  # invalid usage flags
    assert "usage" in str(excinfo.value).lower()


def test_device_survives_a_validation_error(device):
    """An error must not poison the device: valid work still succeeds after it."""
    from wgpu._runtime.errors import GPUValidationError

    with pytest.raises(GPUValidationError):
        device.create_buffer(size=16, usage=0)

    buf = device.create_buffer(size=16, usage="COPY_DST|COPY_SRC")
    device.queue.write_buffer(buf, 0, b"0123456789abcdef")
    assert bytes(device.queue.read_buffer(buf)) == b"0123456789abcdef"


def test_error_does_not_abort_the_process():
    """The whole point: the interpreter must stay alive (exit code 0, not SIGABRT)."""
    code = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(paths.REPO_ROOT)!r})
        from wgpu import _compat
        from wgpu._runtime.errors import GPUValidationError
        try:
            device = _compat.get_default_device()
        except RuntimeError:
            print("SKIP"); raise SystemExit(0)
        try:
            device.create_buffer(size=16, usage=0)
        except GPUValidationError:
            print("RAISED")
        else:
            print("NO-ERROR")
        """
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    # A Rust abort would show up as a negative return code (SIGABRT).
    assert proc.returncode == 0, (
        f"process died ({proc.returncode}): {proc.stderr[-500:]}"
    )
    out = proc.stdout.strip()
    if out == "SKIP":
        pytest.skip("no adapter available")
    assert out == "RAISED", proc.stdout + proc.stderr
