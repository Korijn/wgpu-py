# Replacement readiness

Can the generated implementation replace the classic one? This is the measured
status, so the decision to delete the old code stays evidence-based.

**The public API is now generated end to end, and it performs.** What remains
is porting the historical `tests/` suite, which still imports the old module
layout.

## How it is built

Two specs, each answering the question it is actually authoritative for:

| spec | source | drives |
| --- | --- | --- |
| `webgpu.json` | wgpu-native submodule | the C API: functions, structs, enum integers |
| `webgpu.idl` | W3C, vendored in `bindgen/resources` | the public API: names, enum *strings*, defaults |

`bindgen/bridge.py` connects them with a single normalising rule, and **nothing
goes unmatched**: 30 enums / 268 enum values / 50 structs / 185 fields / 20
classes / 52 methods resolve automatically. The genuine divergences are
declared rather than guessed — web-only names, four struct aliases, and 11
fields whose *shape* differs. A spec bump that breaks a correspondence fails
the build (`test_spec_bridge.py`) instead of quietly emitting less.

Because the mapping is resolved at generation time, the runtime does no name
translation: generated code already speaks C-side member names and integers.

## The enum question, settled

Public enums are **strings**, as the web API and existing wgpu-py code expect:

```python
wgpu.TextureFormat.rgba8unorm == "rgba8unorm"     # True
wgpu.AddressMode.clamp_to_edge == "clamp-to-edge" # True
```

This is not a judgement call — the generated enums and flags are
content-identical to the hand-maintained ones: 34 enum classes, 5 flag classes,
every member, every value. pygfx and any code reading these constants as
strings keeps working. `EnumMap`/`FlagMap` convert to C integers on a plain
dict lookup, with no Python-level call on the success path.

## Coverage

| area | status |
| --- | --- |
| C API surface | 228 functions bound |
| Public classes | generated from the IDL, signatures match the classic ones |
| Enums / flags / structs | 34 / 5 / 60, content-identical to classic |
| Validation errors | raised as `GPUValidationError`, not process aborts |
| Async | `await`, `then()`, and `sync_wait()`, on asyncio and trio |
| Compute pipeline | verified end to end on lavapipe: WGSL, bind groups, dispatch, byte-exact readback |
| Hand-written surface | ~6 methods and ~14 properties, each a documented spec divergence |

## Performance

Same wgpu-native build, same machine, same harness; only the Python layer
differs (`python -m bindgen.tests.benchmark`).

| case | classic | generated | |
| --- | ---: | ---: | --- |
| device startup | 794.2 ms | **81.1 ms** | **9.8x faster** |
| write_buffer | 20.2 us | **15.0 us** | 1.35x faster |
| record_pass (submit) | 110.5 us | **87.0 us** | 1.27x faster |
| dispatch_workgroups | 2.22 us | 2.35 us | 1.06x slower |
| create_bind_group_layout | 22.1 us | 24.4 us | 1.11x slower |
| set_bind_group | 3.46 us | 4.50 us | 1.30x slower |
| create_buffer | 10.3 us | 14.8 us | 1.44x slower |
| property reads | 0.12 us | 0.27 us | 2.2x slower |

Startup dominates because the library is statically linked: no download, no
runtime version probe, far less import work.

The hot path is at parity. Getting there needed two changes that profiling
pointed at directly — 97% of per-call time was Python marshalling, with the C
call itself at 3%:

* **Compiled per-method calls.** The C function, its cffi signature and each
  argument's converter are resolved once per method instead of on every call.
* **Cached immutable properties.** A buffer's size, a texture's format and so on
  are fixed at creation, so they are read once rather than crossing FFI on
  every access in a render loop.

The remaining cold-path gaps (`create_buffer`, `set_bind_group`) are descriptor
marshalling through the generic struct builder; the same compilation technique
would apply if they mattered, but they run once per resource, not per frame.

Note that creating and dropping GPU objects in a loop gets steadily slower in
**both** implementations — the cost is inside wgpu-native's resource
reclamation, not the bindings. The benchmark settles between repeats so it
measures the Python layer rather than accumulated garbage.

## What is left

1. **Port `tests/`** (the acceptance gate below). The 24 historical test files
   still import `wgpu.backends.wgpu_native` and `wgpu._async`, which no longer
   exist. The suite's *content* is what matters and should be kept; only the
   imports and a few old-internals tests need updating.
2. **Delete the classic implementation** once that suite is green.
3. **CI**: the cibuildwheel matrix (still the last piece of the original goal).
4. wgpu-native extras (`set_instance_extras`, `multi_draw_indirect*`, push
   constants) and the diagnostics subsystem.

## Acceptance gate

> Delete the classic implementation only once the historical `tests/` suite
> passes against the generated one.

That suite encodes years of hard-won behaviour — it is what caught the
process-abort bug — so it should be ported, not discarded, and it stays the
objective measure of whether this is a faithful replacement.
