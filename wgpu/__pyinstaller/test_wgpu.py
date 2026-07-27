script = """
# The script part
import sys
import importlib
import wgpu

# The test part
if "is_test" in sys.argv:
    included_modules = [
        "wgpu.backends.auto",
        "wgpu.backends.wgpu_native",
    ]
    excluded_modules = [
        "PySide6",
        "PyQt6",
    ]
    for module_name in included_modules:
        importlib.import_module(module_name)
    for module_name in excluded_modules:
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        raise RuntimeError(module_name + " is not supposed to be importable.")

    # Reach wgpu-native itself. It is statically linked into a cffi extension
    # whose own dependency on _cffi_backend is made from C and so appears
    # nowhere in the frozen import graph -- the one thing the hook has to add.
    import wgpu.backends.wgpu_native as native

    assert native.__version__, "wgpu-native did not report a version"
    assert wgpu.gpu is native.gpu
"""


def test_pyi_wgpu(pyi_builder):
    pyi_builder.test_source(script, app_args=["is_test"])
