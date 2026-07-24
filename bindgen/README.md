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
| Generate object classes + method bodies | ⏳ next |
| Async future primitive (poll-driven, sniffio) | ⏳ next |
| Buffer mapping (memoryview) | ⏳ planned |
| Minimal compatibility shim (current wgpu-py API) | ⏳ planned |
| cibuildwheel matrix (all OS/arch) | ⏳ planned |

The high-level naming derivation is validated against the compiled extension:
408 enum constants, 292 struct fields, 31 bitflags, 146 object methods and 4
top-level functions all resolve with **zero** mismatches, so a submodule bump
that changes a convention fails generation instead of emitting wrong bindings.
