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
declared rather than guessed — web-only names, four struct aliases, and six
fields whose *shape* differs. A spec bump that breaks a correspondence fails
the build (`test_spec_bridge.py`) instead of quietly emitting less.

Shape differences are themselves derived where the C spec already answers them.
When an IDL field has no C member of that name, `webgpu.json` usually says
where it went — an extension struct declares it `extends` this one, or a
member's own struct type carries the field — so the generator finds the target
instead of being told it:

| IDL field | derived as |
| --- | --- |
| `RenderPassDescriptor.maxDrawCount` | chain `WGPURenderPassMaxDrawCount` |
| `TextureDescriptor.textureBindingViewDimension` | chain `WGPUTextureBindingViewDimension` |
| `TexelCopyBufferInfo.{offset,bytesPerRow,rowsPerImage}` | nest under `layout` |

Only two shapes remain declared, because no declaration can settle them — they
depend on the runtime *value*: which slot of the binding-resource union a value
belongs in, and whether shader source is WGSL or SPIR-V (two extension structs
both offer `code`, and the rule refuses to guess between them).

This is not tidiness for its own sake. The hand-written version of
`textureBindingViewDimension` named a struct that does not exist, so the field
raised `KeyError` for every caller and no test noticed. A derived target cannot
drift from the spec that way.

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
| create_buffer | 9.65 us | **3.72 us** | **2.6x faster** |
| dispatch_workgroups | 1.37 us | **0.68 us** | **2.0x faster** |
| record_pass | 65.2 us | **53.9 us** | **1.2x faster** |
| create_bind_group_layout | 19.6 us | 20.6 us | 1.05x slower |
| write_buffer | 6.46 us | 8.93 us | 1.38x slower |
| property reads | 0.083 us | 0.162 us | 1.94x slower |

The hot path -- the calls a frame makes thousands of times -- is where the win
is. Startup is 12x because the library is statically linked: no download, no
runtime version probe, far less import work.

`create_bind_group_layout` is the one remaining case that still marshals a
descriptor at runtime, because it takes an array of structs; the technique that
fixed `create_buffer` would apply to it too.

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

* **Compiled descriptor bodies.** The interpreted struct builder walks a
  descriptor on every call -- look up the member, dispatch on its kind,
  normalise the key, apply the default -- and none of that depends on the
  values. For a struct whose members are each settable in one statement, the
  generator does the walk once and emits straight-line code. Measured against
  hand-written cffi doing the identical work (2.26 us), `create_buffer` went
  from 12.6 to 3.7 us, i.e. from 82% marshalling overhead to about 40%.
  Eleven methods qualify, including both `finish()` calls.
* **Handle arrays inline.** `submit()` runs once per frame; an array of object
  handles is one list comprehension, so it is emitted inline too. The wrinkle
  is that one IDL parameter becomes two C ones (count and pointer).

Not every method gets the deferred-error treatment. The rule is that a call
which *allocates* is not in the zero-allocation hot class, so it can afford to
check: the compiled descriptor bodies and the array calls report errors
immediately. That keeps `finish()` and `submit()` as the boundaries the
genuinely deferred errors surface at, which the rest of the API relies on.

The cases still slower than classic are cold or mid paths that run once per
resource rather than once per draw: the descriptors that still marshal at
runtime because they contain arrays or nested structs, and cached property
reads (a dict lookup rather than a plain attribute, 0.08 us each).

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

## What is generated, and what is not

Priority one is that this stays generated. The census, after deleting the
classic implementation:

| | lines |
| --- | ---: |
| **Generated** (committed output) | **6,123** |
| Binding runtime (`_api` + `_runtime`) | 2,834 |
| -- of which the salvaged promise implementation | 437 |
| wgpu-native extras (non-standard, no spec describes them) | 201 |
| Generator (development only, not shipped) | 2,903 |

The classic backend it replaces was ~5,500 hand-written lines in `_api.py`,
`_mappings.py`, `_helpers.py` and `_ffi.py` alone.

### The hand-written methods, audited

Five methods are not generated. Each is policy rather than mechanics:

| method | why it cannot be derived |
| --- | --- |
| `setBindGroup` | the IDL overloads it with a window over an *array*, not a buffer |
| `mapAsync`, `getMappedRange`, `unmap` | need Python-side map state: wgpu-native leaves `GetMapState` unimplemented, and handing C a bad range aborts the process |
| `getCompilationInfo` | also unimplemented in C; reporting "no messages" is a decision |

Three more used to be on that list -- `writeBuffer`, `writeTexture` and
`setImmediates` -- and are now derived, because they share one shape: the web
API passes a buffer plus an optional `(dataOffset, size)` window where C wants
a pointer and a byte count. One rule replaced three functions, so the next spec
revision that adds a data-taking method gets it free.

The same went for the struct *shape* adapters: five of seven were replaced by
two rules read off `webgpu.json` (see above), leaving only the two that depend
on a runtime value. `wgpu/_api/adapt.py` shrank from five bespoke functions to
two generic mechanisms plus those two.

The wgpu-native extras stay hand-written on purpose. Deriving module-level
function names from a C header would be a naming heuristic rather than
something either spec states, and it would auto-expose a large surface nobody
asked for. They do carry the generated layer's guarantee: `test_extras_are_pinned.py`
checks every C function they reference still exists, and that neither they nor
the generated code ever wrap one of wgpu-native's unimplemented functions --
which would abort the process rather than raise.

## Porting the historical suite

In progress. **214 of 233 tests pass**, with no errors and no crashes -- from a
suite that would not even collect. `tests_mem` now collects and largely runs
too, where before it could not be imported at all.

It has earned its keep repeatedly, finding bugs nothing else did:

* **`await buffer.map_async()` hung forever.** wgpu-native only runs completion
  callbacks while its event queue is processed, and only `sync_wait()` drove
  that. The awaiting task now pumps between naps.
* **35 C functions abort the process.** wgpu-native declares the full WebGPU
  surface but leaves 35 functions `unimplemented!()`, and a Rust panic cannot
  unwind across FFI. Four test files died outright. The generator now reads
  wgpu-native's own `unimplemented.rs` and emits a guard.
* **Invalid buffer ranges aborted rather than raising.** The mapped range is
  tracked in Python and every offset and size validated before it reaches C --
  which is also how `map_state` is answered, since the C getter is unimplemented.
* **Omitted nested structs lost their defaults.** An omitted multisample state
  reached wgpu-native with `count: 0`. The IDL says exactly which nested
  structs default to `{}`, and that now rides in the descriptor.
* **A zero size aborted the process.** `set_vertex_buffer(slot, buf, 0, 0)`
  reached C as a zero-length binding; a falsy size means "the rest".
* **Adapter info came back empty.** `out_string` members were never read, so
  `summary` reported `unknown | 6 | 3`.
* **Every omitted string was sent as `""`.** webgpu.h defines a zeroed
  `WGPUStringView` as the *empty string*; "not specified" is
  `{NULL, WGPU_STRLEN}`. A pipeline with no explicit `entry_point` therefore
  failed with "Unable to find entry point ''". Both the interpreted builder
  and the compiled bodies now write the sentinel.
* **Mapping a buffer read back stale bytes.** `queue.write_buffer`, and
  unmapping a buffer created with `mapped_at_creation`, stage their data;
  the staged copies only run on the next submit, which mapping does not
  trigger. The classic backend did the same empty submit, citing
  wgpu-native#305.
* **A render bundle encoder did not keep its arguments alive.** It reads them
  again at `finish()`, and wgpu-core panics on a released slot -- which aborts
  the process rather than raising. `bindgen/tests` reproduces the abort.
* **wgpu-native's log went nowhere.** naga explains *why* a shader failed
  through the log while the error callback only says that it did, so the
  useful half of every shader error was being discarded.
* **`write_timestamp` always called the command-encoder function**, so passing
  a pass encoder raised a ctype error; the statistics query set allocated the
  wrong ctype outright. Neither had a test near it.
* **A dead hook silently dropped a property.** `HAND_WRITTEN_ATTRS` named
  `GPUTextureView.texture`, which the IDL does not declare, so it suppressed
  nothing and the replacement was never attached. That is now a build error.

Conveniences that had gone missing are back, all descriptor-driven rather than
per-struct: positional struct values (`size=(64, 64, 1)`), every field spelling
the specs use (`max_bind_groups` / `max-bind-groups` / `maxBindGroups`),
mappings for key/value arrays (pipeline constants, including numeric `@id`
keys), underscored enum spellings, `layout="auto"`, SPIR-V shader source, and
the container protocol on enums (`"low-power" in wgpu.PowerPreference`).

Performance was re-measured after all of this: unchanged on the hot path, and
`create_buffer` improved to 9.3 us because field-name normalisation only runs
when a key does not already match.

Object counts are now tracked so that `wgpu.diagnostics.object_counts` and
`wgpu_native_counts` can be compared -- which is how a leaked handle is found.
Counting costs about 3% on object creation, because it is a class-attribute
load and one item store rather than a dict keyed by class name; the names are
recovered by walking the class tree, which only the diagnostic does.

### Remaining failures, by file

| file | failing | what they are |
| --- | ---: | --- |
| `test_wgpu_native_basics.py` | 8 | probe `_api` / `_ffi` / `_helpers` / `_internal` |
| `test_api.py` | 7 | classic construction, backend registration, `wgpu._classes` |
| others (3 files) | 4 | `_mappings`, backend-qualified `repr`, API tracing |

What is left is now almost entirely tests reaching for classic internals that
no longer exist -- they need porting, not implementation changes. The one real
gap is API tracing, which is unimplemented and says so.

Two behaviours were deliberately *not* matched. `repr` says
`<wgpu.GPUDevice object 'x' at ...>` rather than naming
`wgpu.backends.wgpu_native`: the classes really do live in `wgpu`, and a repr
that names a module you cannot import them from is worse than a test that
needs porting. And limits are reported hyphenated, as they always were, but
`GPUObjectBase.__init__` takes the new argument list rather than the classic
one.

## Packaging

The classic wheel was pure Python with a prebuilt `libwgpu_native.so`
downloaded at build time and dropped in beside it. This one *is* a CPython
extension with wgpu-native statically linked, so:

* there is nothing to download, at build time or install time -- the binary is
  compiled from the submodule commit that is pinned in git, so the wheel and
  its native library cannot disagree about a version;
* a wheel is specific to a platform, architecture **and** interpreter, so the
  matrix is real: cp311/312/313 x {linux x86_64, linux aarch64, macOS arm64,
  macOS x86_64, windows amd64}, driven by cibuildwheel (`.github/workflows/cd.yml`);
* `tools/hatch_build.py` runs `cargo build --release` and then cffi, and marks
  the wheel non-pure so hatchling infers the right tag.

The sdist carries the wgpu-native sources too, so a source build works given a
Rust toolchain; cargo still fetches the crate dependencies. Verified locally on
linux x86_64: a wheel built from the sdist alone, installed into a clean
virtualenv outside the repo, creates a device and a buffer on lavapipe. The
extension is ~17 MiB uncompressed, ~5 MiB in the wheel -- comparable to the
shared library the classic wheel shipped.

CI also asserts that `wgpu/_generated` matches a fresh generator run, which is
the check that keeps "fully generated" true rather than aspirational.

## What is left

1. Port the ~19 remaining tests that reach for deleted internals.
2. Push constants, and API tracing.
3. `tests_mem` collects and runs, but aggressive release ordering still aborts
   the process in some combinations -- a real lifetime bug, not a test artefact.
4. Descriptors that still marshal at runtime (arrays and nested structs) could
   use the same compiled bodies as the flat ones.

wgpu-native's own extension structs (`WGPUShaderSourceGLSL` and the `*Extras`
family) are declared only in `wgpu.h`, so they cannot come from `webgpu.json`.
They are written out in `wgpu/backends/wgpu_native/native_structs.py` as the
same descriptors the generator emits -- so the marshalling stays generic and
only the shape is hand-written -- and pinned against the compiled header.
Teaching the generator to read them out of `wgpu.h` would remove even that.

## Acceptance gate

> Delete the classic implementation only once the historical `tests/` suite
> passes against the generated one.

That suite encodes years of hard-won behaviour — it is what caught the
process-abort bug — so it should be ported, not discarded, and it stays the
objective measure of whether this is a faithful replacement.
