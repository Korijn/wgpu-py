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
differs. Best of three runs each (`python -m bindgen.tests.benchmark`).

| case | classic | generated | |
| --- | ---: | ---: | --- |
| device startup | 635.1 ms | **52.9 ms** | **12.0x faster** |
| set_bind_group | 1.49 us | **0.25 us** | **6.1x faster** |
| dispatch_workgroups | 1.37 us | **0.68 us** | **2.0x faster** |
| create_bind_group_layout | 19.6 us | 20.6 us | 1.05x slower |
| create_buffer | 9.65 us | 10.9 us | 1.13x slower |
| record_pass | 65.2 us | 75.6 us | 1.16x slower |
| write_buffer | 6.46 us | 9.04 us | 1.40x slower |
| property reads | 0.083 us | 0.162 us | 1.94x slower |

The hot path -- the calls a frame makes thousands of times -- is where the win
is. Startup is 12x because the library is statically linked: no download, no
runtime version probe, far less import work.

### How the hot path got there

Profiling was unambiguous: a raw cffi call costs 0.303 us, and the API was
spending 1.4 us to make it. Worse, the largest single cost was the "fast path"
that was supposed to help -- a per-call loop of argument converters that
duplicated conversions cffi already does. Three changes closed it:

* **Direct call bodies.** For methods whose arguments need no allocation, the
  generator emits a body that calls an already-bound C function, with the
  argument expressions inlined. `draw()` is now one Python frame and one C
  call, landing within 10% of the raw-cffi floor.
* **Per-class mixin overrides.** `draw` is declared once on a mixin but backed
  by a different C function per object, so each concrete class gets its own
  bound override rather than sharing a generic dispatch.
* **`set_bind_group` fast case.** Its signature cannot be generated (the web
  API slices a dynamic-offsets buffer), but the overwhelmingly common call
  passes no offsets at all, and that case is a single C call.

The cost, chosen deliberately: direct calls do not check for a pending
wgpu-native error. Errors still surface, at the next call that does check --
`finish()` or `submit()`, both of which take a descriptor or an array and so
use the general path. That is where the classic implementation reported them
too, and `test_direct_calls.py` pins it.

The cases still slower than classic are all cold or mid paths that run once
per resource rather than once per draw: descriptor marshalling through the
generic struct builder, and cached property reads (a dict lookup rather than a
plain attribute, 0.08 us each). The same generation technique would apply if
profiling ever showed they mattered.

Note that creating and dropping GPU objects in a loop gets steadily slower in
**both** implementations -- the cost is inside wgpu-native's resource
reclamation, not the bindings. The benchmark settles between repeats so it
measures the Python layer rather than accumulated garbage.

### What would push it further

Per-call tuning is close to done: we are near the floor for *any* Python
binding, and a hand-written C extension might buy 2x more at the cost of the
"everything is generated" property. The remaining order of magnitude is
structural -- stop making one Python call per GPU command:

* **render bundles**, already generated, record once and replay with one call;
* **`multi_draw_indirect`** (a wgpu-native extra, not yet wrapped);
* **batched setters** that take an array of draw parameters and loop in C.

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
