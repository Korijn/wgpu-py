# bindgen — fully-automated wgpu-native bindings

This package generates the Python bindings to
[wgpu-native](https://github.com/gfx-rs/wgpu-native) with **no per-symbol manual
work**. Bumping the `wgpu-native` git submodule and re-running the pipeline is
the entire upgrade process.

## Architecture

```
wgpu-native/ (submodule, pinned)
├── ffi/webgpu-headers/webgpu.h    ← standard WebGPU C API  (generated from webgpu.yml)
├── ffi/webgpu-headers/webgpu.json ← machine-readable spec   (source for the high-level layer)
├── ffi/wgpu.h                     ← wgpu-native extensions
└── src/ + Cargo.toml              ← Rust sources → libwgpu_native.a (static)
        │
        ▼
   ┌─────────────────────── bindgen ───────────────────────┐
   │ headers.py   clean the C headers → cffi-parseable cdef │
   │ ffi_build.py compile a cffi API-mode extension,        │
   │              statically linked against libwgpu_native.a│
   └────────────────────────────────────────────────────────┘
        │
        ▼
   wgpu._native._wgpu   (compiled extension, ~228 wgpu* functions)
```

### Layering rule

`bindgen/` is **development-only** — it is not part of the wheel. Nothing under
`wgpu/` may import it, so everything the runtime needs (enum classes by spec
name, release functions, resolved defaults) is *generated into* `wgpu/_generated/`.
Two tests enforce this boundary, one statically and one by running the real call
path with `bindgen` made unimportable.

### Why this setup

* **cffi API (out-of-line) mode**, not ABI mode. The real headers are compiled
  via `set_source`, so the C compiler verifies every declaration and calls are
  real C calls (much faster than the previous ABI-mode implementation, which
  re-parsed a header at import time).
* **Static linking** of `libwgpu_native.a` into the extension → one
  self-contained binary per wheel, no separate shared library to locate.
* **Built from source** from the pinned submodule → reproducible and patchable,
  rather than downloading prebuilt release binaries.

## Building locally

```bash
# 1. Build the native static library (needs the Rust toolchain, >= 1.93)
cd wgpu-native && cargo build --release --lib && cd ..

# 2. Compile the cffi extension
python -m bindgen.ffi_build
```

## Testing

```bash
python -m pytest bindgen/tests --noconftest
```

`test_cdef_covers_full_surface` runs anywhere (no Rust needed);
`test_compiled_extension_is_functional` is skipped unless the static archive has
been built.

## Status

| Piece | State |
| --- | --- |
| Build `libwgpu_native.a` from submodule | ✅ done |
| cffi API-mode extension, static link | ✅ done |
| Generate full-surface cdef from headers | ✅ done (228 functions) |
| Generate enums / flags (`_generated/`) | ✅ done (54 enums, 5 flags) |
| Generate struct descriptors (`_generated/`) | ✅ done (80 structs, 292 members) |
| Generate constants (`_generated/`) | ✅ done (11 sentinels) |
| Runtime struct builder (mapping → cffi cdata) | ✅ done (`wgpu/_runtime/`) |
| Generate method table + `GPU*` classes | ✅ done (23 classes, 146 methods) |
| Runtime invoker (marshal args, wrap returns, release) | ✅ done (sync path) |
| Async future primitive (poll-driven, sniffio) | ✅ done (sync `.wait()` + `await`) |
| Array + raw-data (c_void) args | ✅ done |
| Buffer mapping → memoryview | ✅ done |
| Minimal compatibility shim (current wgpu-py API) | ⏳ in progress |
| cibuildwheel matrix (all OS/arch) | ⏳ planned |

## Validating against the existing test suite

The library built from the submodule can be driven by the *existing* (old)
wgpu-py implementation via `WGPU_LIB_PATH`, which validates the native build
against the full historical test suite:

```bash
cd wgpu-native && cargo build --release --lib && cd ..
export WGPU_LIB_PATH=$PWD/wgpu-native/target/release/libwgpu_native.so
export PYTHONPATH=$PWD          # so subprocess-based tests can import wgpu
python -m pytest tests -q
```

On Mesa lavapipe this passes **235 tests** (1 skipped), confirming the
from-source build is fully functional. A software driver is enough:

```bash
apt-get install -y mesa-vulkan-drivers   # lavapipe/llvmpipe
```

## End-to-end validation of the generated layer

Validated end-to-end against **Mesa lavapipe (llvmpipe)**: the async chain
`instance → request_adapter → request_device` resolves a real `GPUDevice` via
both `.wait()` and `await`, and a full data round-trip (`write_buffer` →
`copy_buffer_to_buffer` → `submit` → `map_async` → `get_mapped_range`) returns
the exact bytes written.

The high-level naming derivation is validated against the compiled extension:
408 enum constants, 292 struct fields, 31 bitflags, 146 object methods and 4
top-level functions all resolve with **zero** mismatches, so a submodule bump
that changes a convention fails generation instead of emitting wrong bindings.
