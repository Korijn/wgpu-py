import sys
import logging
import subprocess
from dataclasses import fields

import wgpu

from pytest import raises, mark
from testutils import run_tests, can_use_wgpu_lib


def test_basic_api():
    import wgpu

    assert isinstance(wgpu.__version__, str)
    assert isinstance(wgpu.version_info, tuple)
    assert isinstance(wgpu.gpu, wgpu.GPU)

    # Entrypoint funcs
    assert wgpu.gpu.request_adapter_sync
    assert wgpu.gpu.request_adapter_async

    # The two forms must take the same arguments. This used to compare
    # co_varnames, which also picks up local variables -- so it depended on how
    # each body happened to be written. The signature is what the test was
    # after, and asking for it directly is both stricter (it compares defaults
    # and kinds too) and immune to a refactor of either body.
    import inspect

    sig1 = inspect.signature(wgpu.GPU.request_adapter_sync)
    sig2 = inspect.signature(wgpu.GPU.request_adapter_async)
    assert sig1.parameters == sig2.parameters

    assert repr(wgpu.classes.GPU()).startswith(
        "<wgpu.GPU "
    )  # does not include _classes


def test_api_subpackages_are_there():
    code = "import wgpu; x = [wgpu.resources, wgpu.utils, wgpu.backends]; print('ok')"
    result = subprocess.run(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    out = result.stdout.rstrip()
    assert out.endswith("ok")
    assert "traceback" not in out.lower()


def test_logging():
    level = [-1]

    def set_level_test(lvl):
        level[0] = lvl

    wgpu._coreutils.logger_set_level_callbacks.append(set_level_test)
    logger = logging.getLogger("wgpu")
    logger.setLevel("ERROR")
    assert level[0] == 40
    logger.setLevel("INFO")
    assert level[0] == 20
    logger.setLevel("DEBUG")
    assert level[0] == 10
    logger.setLevel(5)
    assert level[0] == 5  # "trace" in wgpu-native
    logger.setLevel(0)
    assert level[0] == 0  # off
    # Reset
    logger.setLevel("WARNING")
    assert level[0] == 30


def test_enums_and_flags_and_structs():
    # Enums are str
    assert isinstance(wgpu.BufferBindingType.storage, str)

    # Enum groups show their values
    assert "storage" in repr(wgpu.BufferBindingType)

    # Flags are ints
    assert isinstance(wgpu.BufferUsage.STORAGE, int)

    # Flag groups show their field names (in uppercase)
    assert "STORAGE" in repr(wgpu.BufferUsage)

    # Structs are dataclasses
    assert issubclass(wgpu.structs.DeviceDescriptor, wgpu.structs.Struct)
    assert isinstance(wgpu.structs.DeviceDescriptor.label, str)
    assert wgpu.structs.DeviceDescriptor.required_features == ()

    # Structs show their field names ... in the instance
    instance = wgpu.structs.DeviceDescriptor()
    for field in fields(wgpu.structs.DeviceDescriptor):
        assert field.name in repr(instance)

    # Structs have nice repr
    r = repr(instance)
    assert "DeviceDescriptor" in r
    assert "label" in r
    assert "required_features: Sequence[enums.FeatureNameEnum] = ()" in r


@mark.skipif(not can_use_wgpu_lib, reason="Needs wgpu lib")
def test_base_wgpu_api():
    # This used to build an adapter, a queue and a device out of nothing, by
    # passing their features and limits to the constructor. There is no longer
    # anything to pass: an object *is* a handle wgpu-native owns, and its
    # features and limits are read back from it. So the same properties are
    # checked, on real objects.
    adapter = wgpu.gpu.request_adapter_sync()
    device = adapter.request_device_sync(label="device08")
    queue = device.queue

    assert queue._device is device

    assert isinstance(adapter.features, set)
    assert isinstance(adapter.limits, dict)
    assert isinstance(device.features, set)
    assert set(device.limits.keys())

    assert isinstance(device, wgpu.GPUObjectBase)
    assert device.label == "device08"
    assert hex(id(device)) in repr(device)
    assert device.label in repr(device)

    # Every object gets a distinct, increasing id
    assert device.uid < queue.uid


@mark.skipif(not can_use_wgpu_lib, reason="Needs wgpu lib")
def test_backend_is_selected_automatically():
    # Test this in a subprocess to have a clean wgpu with no backend imported yet
    code = "import wgpu; print(wgpu.gpu.request_adapter_sync())"
    result = subprocess.run(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    out = result.stdout.rstrip()
    assert "GPUAdapter object at" in out
    assert "traceback" not in out.lower()


def test_that_we_know_how_our_api_differs():
    # The differences from the web API used to be collected by an ``apidiff``
    # decorator and reported through its docstring. They are now the explicit
    # attachment block at the bottom of wgpu/_api/__init__.py -- same purpose,
    # one place to read them off, except that the lines *are* the wiring rather
    # than a description of it, so they cannot fall out of date.
    import inspect

    from wgpu import _api

    source = inspect.getsource(_api)
    assert "GPUBuffer.read_mapped" in source
    assert "GPUDevice.create_buffer_with_data" in source
    assert _api.__doc__ and "differ" in _api.__doc__


def test_that_all_docstrings_are_there():
    for class_name, cls in wgpu.classes.__dict__.items():
        if class_name.startswith("_"):
            continue
        assert isinstance(cls, type)
        assert cls.__doc__, f"No docstring on {cls.__name__}"
        for name, attr in cls.__dict__.items():
            if not (callable(attr) or isinstance(attr, property)):
                continue
            if name.startswith("_"):
                continue
            func = attr.fget if isinstance(attr, property) else attr
            assert func.__doc__, f"No docstring on {func.__name__}"


def get_output_from_subprocess(code):
    cmd = [sys.executable, "-c", code]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.stdout.decode(errors="ignore")


def test_do_not_import_utils_submodules():
    # OK: use something from root package
    code = "import wgpu; print(wgpu.__version__)"
    out = get_output_from_subprocess(code)
    assert "Error" not in out
    assert wgpu.__version__ in out

    # OK: wgpu.utils itself is always there
    code = "import wgpu; print(wgpu.utils)"
    out = get_output_from_subprocess(code)
    assert "Error" not in out
    assert "module 'wgpu.utils' from " in out

    # OK: wgpu.utils itself is always there
    code = "import wgpu; print(wgpu.utils.get_default_device)"
    out = get_output_from_subprocess(code)
    assert "Error" not in out
    assert "get_default_device" in out

    # Also, no numpy
    code = "import sys, wgpu.utils; print('numpy' in sys.modules)"
    out = get_output_from_subprocess(code)
    assert out.strip().endswith("False"), out


def test_there_is_exactly_one_backend():
    # wgpu-py used to pick a backend at runtime, and ``_register_backend``
    # guarded that choice: the wrong shape of object, or a second registration,
    # was a RuntimeError. There is nothing left to choose -- wgpu-native is
    # compiled into the package -- so what is checked now is that the import
    # path downstream code uses still resolves, and that the entrypoint is the
    # real one rather than something that could be swapped underneath it.
    import wgpu.backends.wgpu_native

    assert wgpu.backends.wgpu_native is sys.modules["wgpu.backends.wgpu_native"]
    assert isinstance(wgpu.gpu, wgpu.GPU)
    assert wgpu.backends.wgpu_native.GPUDevice is wgpu.GPUDevice


if __name__ == "__main__":
    run_tests(globals())
