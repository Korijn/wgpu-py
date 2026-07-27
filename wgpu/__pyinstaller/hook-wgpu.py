# ruff: noqa: N999

# Init variables that PyInstaller will pick up.
hiddenimports = []
datas = []
binaries = []

# wgpu-native used to ship as a separate shared library under wgpu/resources,
# which PyInstaller had to be told to collect. It is now statically linked into
# the compiled ``wgpu._native._wgpu`` extension, and every module that reaches C
# imports that extension at module level -- so the import graph already carries
# it. collect_dynamic_libs would copy it a second time, to a path nothing
# imports from, and collect_data_files now finds nothing at all.

# The extension does import one thing PyInstaller cannot see: every cffi
# API-mode module pulls in the _cffi_backend runtime, and it does so from C, so
# it appears nowhere in the import graph. Without it the frozen app gets as far
# as importing wgpu._native and fails there.
hiddenimports += ["_cffi_backend"]

# Always include the wgpu-native backend. Since an import is not needed to
# load this (default) backend, PyInstaller does not see it by itself.
hiddenimports += ["wgpu.backends.auto", "wgpu.backends.wgpu_native"]
