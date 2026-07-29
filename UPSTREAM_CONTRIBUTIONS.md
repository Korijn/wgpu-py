# Candidates to contribute upstream

The rewrite on `dev` replaced the hand-written binding with a generated one. Most
of that is not something upstream would want as a patch — it is a different
implementation, not a change to theirs. But porting the historical test suite
onto the generated stack surfaced a number of findings that apply to
`pygfx/wgpu-py` **as it stands today**, and a few pieces of tooling that are
useful independently of how the binding is produced.

This is the shortlist of those, prioritised. Everything here is deliberately
scoped so that it can be reviewed on its own: no item asks upstream to take on
the rewrite, adopt the generator, or commit to a direction.

All line references are against `pygfx/wgpu-py` at `9d59b27` (v0.32.0), which is
what this fork's `master` tracks. Each item was re-checked against that code
rather than inferred from the rewrite.

## Priority

| # | Item | Kind | Size | Risk |
| --- | --- | --- | --- | --- |
| 1 | Descriptor fields accepted and then dropped | Bug fix | S | low |
| 2 | `set_bind_group` allocates on every call | Performance | S | low |
| 3 | No test drives `then()` from a real wgpu-native callback | Test coverage | S | none |
| 4 | `test_basic_api` compares `co_varnames` | Test quality | XS | none |
| 5 | Public-API benchmark suite | Tooling | M | none |
| 6 | Flag strings reject numeric literals | Small feature | S | low |
| 7 | Wheels are built for targets nothing imports | CI | M | low |
| 8 | Nothing stops a call into wgpu-native's `unimplemented!()` | Robustness | M | medium |
| 9 | Unknown descriptor keys are silently ignored | Behaviour change | M | needs buy-in |

Items 1–4 are the ones worth opening first: each is a self-contained defect or
gap with a test, and none of them touch a design decision.

---

## 1. Descriptor fields accepted and then dropped

**What.** Two documented, spec-defined fields are in the public signature, pass
through unvalidated, and never reach wgpu-native:

* `GPUDevice.create_texture(texture_binding_view_dimension=...)` —
  `wgpu/backends/wgpu_native/_api.py:1479`. The value is stored in `tex_info`
  (`:1540`) and read back by `GPUTexture`, so it *looks* applied, but the
  descriptor built at `:1515` never chains `WGPUTextureBindingViewDimension`.
  The texture is created without it.
* `GPUCommandEncoder.begin_render_pass(max_draw_count=...)` —
  `wgpu/backends/wgpu_native/_api.py:3147`. Same shape: accepted, defaulted to
  `50000000` per the IDL, never chained as `WGPURenderPassMaxDrawCount`.

Both are `nextInChain` extension structs in `webgpu.h`, which is why they were
missed — every field that is a plain member of the descriptor is handled.

A third, adjacent case is explicit rather than silent: `create_texture` raises
`NotImplementedError` for `view_formats` (`:1501`), even though
`WGPUTextureDescriptor` has `viewFormatCount`/`viewFormats` as ordinary members
and the surrounding code already leaves them as `# not used:`.

**Why it matters.** A silently ignored argument is worse than an unsupported
one. In the rewrite, the equivalent `textureBindingViewDimension` mapping was
hand-written against a struct name that did not exist, so it raised `KeyError`
for every caller and no test noticed — the same class of gap, found only
because the generator derives the chain target from `webgpu.json` and a
mismatch fails the build.

**Proposed PR.** Chain both structs, with a test each that the value survives
the round trip. `view_formats` can go in the same PR or a follow-up; it is a
member array, so it is the more mechanical of the three.

**Risk.** Low. Both are additive: today's callers pass the default and get the
default behaviour.

## 2. `set_bind_group` allocates on every call

**What.** `wgpu/backends/wgpu_native/_api.py:2890` builds `offsets = list(...)`
and then `ffi.new("uint32_t []", offsets)` unconditionally, including the
overwhelmingly common call that passes no dynamic offsets at all. That is a
list construction plus a cffi allocation per call, on the method a render loop
calls the most.

**Why it matters.** In the rewrite's benchmark (`bindgen/tests/benchmark.py`,
results in `bindgen/REPLACEMENT_READINESS.md`) `set_bind_group` is the single
biggest per-call gap between the two implementations: 1.49 µs against 0.25 µs.
That figure is whole-stack and most of it belongs to other differences, so it
should not be quoted as the value of this one change — but the allocation is
real, it is on the hot path, and removing it for the no-offsets case is a
handful of lines.

**Proposed PR.** Early-out when `dynamic_offsets_data` is empty and the
start/length arguments are `None`: pass `0, ffi.NULL`. Keep the existing path
for everything else. Benchmark before/after in the PR body.

**Risk.** Low, but it is a hot path, so the PR should show that the existing
dynamic-offset tests still pass and add one that mixes both forms.

## 3. No test drives `then()` from a real wgpu-native callback

**What.** `tests/test_async.py` has 34 tests, of which roughly a dozen exercise
`then()`/`catch()` chaining — every one against a `GPUPromise` subclass defined
at the top of the file (`tests/test_async.py:22`) and resolved by hand. Not one
resolves a promise that wgpu-native actually completes.

**Why it matters.** That exact gap hid two simultaneous live bugs in the
rewrite: nothing drove the event queue when no one was awaiting (so a
fire-and-forget `then()` never fired), and `then()` constructed the chained
promise positionally, which put the user's callback in a subclass's second
constructor slot and left the handler unset. Both were invisible to sixteen
passing `then()` tests. Upstream's own `GPUPromise` subclass
(`wgpu/backends/wgpu_native/_api.py:692`) happens not to add constructor
arguments today, so the second bug is latent there rather than live — which is
precisely the kind of thing a test should pin before someone adds an argument.

**Proposed PR.** Two tests, straight ports of what the rewrite added: `then()`
on a real `buffer.map_async()` with nobody awaiting, asserting the callback
fires; and awaiting the promise that `then()` returns. Both marked
`skipif(not can_use_wgpu_lib)`. Optionally, a `_derive()` hook in
`wgpu/_async.py` so a subclass constructor is never called positionally across
the class boundary — but the tests are the contribution, the hook is a nicety.

**Risk.** None — test-only.

## 4. `test_basic_api` compares `co_varnames`

**What.** `tests/test_api.py` asserts that the sync and async forms of
`request_adapter` take the same arguments by comparing `__code__.co_varnames`
with a hardcoded subtraction of three known locals.

**Why it matters.** `co_varnames` includes local variables, so the assertion
depends on how each body happens to be written, and the exclusion list has to be
maintained as the bodies change. `inspect.signature(...).parameters` asks the
question the test is actually asking, and is stricter: it compares defaults and
parameter kinds too.

**Proposed PR.** Six lines. Ideally applied to both `request_adapter` and any
other sync/async pair checked the same way.

**Risk.** None — test-only, and strictly stronger than what it replaces.

## 5. Public-API benchmark suite

**What.** `bindgen/tests/benchmark.py` on `dev` times a fixed set of operations
through the public API only: device startup, `set_bind_group`,
`create_buffer`, `dispatch_workgroups`, `record_pass`, `write_buffer`,
`create_bind_group_layout`, property reads. It touches no implementation
detail, which is deliberate — it was written to run unmodified against a
pre-rewrite checkout so both sides could be compared on the same wgpu-native
build.

**Why it matters.** wgpu-py has no performance regression signal at all today.
The cases are chosen to separate the two costs that actually matter for a
render engine: per-call overhead on the hot path, and descriptor marshalling on
the cold path. Profiling against this harness is what showed that the
per-call converter loop intended as a fast path was itself the largest single
cost — the kind of finding that is invisible without a baseline.

**Proposed PR.** The file, a short README section on running it, and no CI
wiring initially. Upstream can decide later whether to gate on it.

**Risk.** None to the library. The discussion is whether upstream wants to own
the file, and whether it should live under `tests/` or a `benchmarks/` dir.

## 6. Flag strings reject numeric literals

**What.** `str_flag_to_int` (`wgpu/_coreutils.py:178`) splits on `|` and looks
each part up by name, raising `ValueError` for anything else. So
`usage="0xF"` or `usage="RED|0x2"` is rejected, even though the equivalent int
is accepted directly and that is how a mask arrives from a config file or from
a value copied out of the C headers.

**Why it matters.** Small, but it is a real inconsistency between the int and
string forms of the same argument, and the fix is to parse each part with
`int(part, 0)` when it is not a known name.

**Proposed PR.** ~10 lines in `str_flag_to_int` plus tests for `'0xF'`, `'15'`,
mixed `'RED|0x2'`, and that an unknown *name* still raises.

**Risk.** Low. It only widens what is accepted; the cache key is already the
string, so nothing about caching changes. Worth confirming upstream considers
this in scope rather than an invitation to pass stringly-typed masks.

## 7. Wheels are built for targets nothing imports

**What.** `.github/workflows/cd.yml` builds all wheels in one job and tests on
four: windows amd64, macos arm64, macos x86_64, linux amd64. Linux aarch64 is
built and published without anyone importing it. The smoke test itself
(`cd.yml`, "Install and test wheel") prints `_ffi.lib_path` and stops there.

**Why it matters.** The failure mode of a compiled binding is not the build, it
is the import on someone else's machine — which is the one thing an untested
wheel never checks. Free arm64 Linux runners now exist, so this is no longer a
QEMU-shaped problem.

**Proposed PR.** Add linux aarch64 to the test matrix; have the smoke test
round-trip data through the GPU rather than only reporting a path; assert the
platform tag on each wheel before upload (an unrepaired linux wheel is tagged
`linux_x86_64`, which PyPI rejects — after the tag is cut).

**Risk.** Low, but it is CI: the PR should be prepared to iterate on runner
availability, and each of the three changes is independently droppable.

**Note.** The rest of this fork's `cd.yml` changes are not applicable — they
exist because the fork compiles wgpu-native from a submodule rather than
downloading a prebuilt library.

## 8. Nothing stops a call into wgpu-native's `unimplemented!()`

**What.** wgpu-native declares the full WebGPU C surface but leaves a set of
functions as `unimplemented!()`. A Rust panic cannot unwind across FFI, so
calling one aborts the interpreter — no exception, no traceback. Upstream
handles the two cases it knows about by hand (`create_*_pipeline_async`, via
`_CREATE_PIPELINE_ASYNC_IS_IMPLEMENTED` at
`wgpu/backends/wgpu_native/_api.py:1398`), but there is no check
that the rest of the wrapped surface avoids the list, and no signal when a
wgpu-native bump moves a function onto or off it.

**Why it matters.** Four historical test files died this way during the port,
each as a bare `SIGABRT`. The rewrite reads wgpu-native's own
`unimplemented.rs` at generation time and emits a `NotImplementedError` guard,
so the list is never maintained by hand and a release that implements one turns
the guard back into a real call.

**Proposed PR.** Upstream does not vendor the Rust sources, so the list has to
come from somewhere: fetch `unimplemented.rs` at codegen time alongside the
headers, and have the codegen check assert that no `# H:` annotated call is in
it. That is the same mechanism as the existing header check in
`codegen/wgpu_native_patcher.py`, applied to a second input.

**Risk.** Medium — it adds a network-fetched input to codegen, and upstream may
prefer a pinned list checked at review time. Worth opening as an issue first to
agree on the mechanism before writing it.

## 9. Unknown descriptor keys are silently ignored

**What.** Descriptors are plain dicts read field by field. Required fields are
indexed, so a typo raises `KeyError`; optional ones go through `.get()`, so a
typo does nothing at all. `create_texture(mip_level_counts=4)` — plural — is
accepted and ignored.

**Why it matters.** This is the single most common way to lose an afternoon
with an API of this shape, and it is invisible: the call succeeds and the
object is wrong. The rewrite raises `ValueError` for an unknown struct field
because the generated builder knows the full field set; upstream's hand-written
readers do not, so this needs a per-call-family answer rather than one switch.

**Risk / process.** This is a behaviour change that could break working code
which happens to pass a stray key, so it needs upstream buy-in before any code
is written, and should land one call family at a time behind a clear changelog
entry. Open as an issue, not a PR.

---

## Deliberately not proposed

Listed so the reasoning is on record, and so nobody re-derives it later.

* **The generator itself** (`bindgen/`, `wgpu/_generated/`, `wgpu/_runtime/`).
  This is a replacement, not a patch. Upstream's `codegen/` produces a
  hand-maintained binding it has years of investment in; proposing a swap is a
  direction conversation, not a pull request. If there is ever appetite for it,
  `bindgen/REPLACEMENT_READINESS.md` is the document to start from.
* **wgpu-native as a submodule, built from source and statically linked.** The
  reason startup is 12x faster in the rewrite, and completely incompatible with
  upstream's download-a-prebuilt-library model. Not a piece that can be taken
  in isolation.
* **Generated enums, flags and structs.** Content-identical to upstream's
  hand-maintained ones — there is nothing to fix.
* **The per-device poll thread, the error sink, mapped-range validation,
  adapter-info string reads, `layout="auto"`, hyphenated and positional struct
  keys.** All of these are the rewrite catching up to behaviour upstream
  already has. They read like fixes in the fork's history because they were
  fixes *to the fork*.
* **CI branch-filter changes.** `pull_request` is filtered to `main` upstream,
  which is correct for upstream — the fork widened it only because `dev` is its
  integration branch.
* **`docs/conf.py` changes.** The fork escapes the synthetic `(**parameters)`
  signature as a literal, because docutils reads the unescaped `**` as a strong
  start-string with no end and `.readthedocs.yaml` sets `fail_on_warning: true`.
  Upstream has the identical line (`docs/conf.py:119`) and a green docs build,
  so something about the fork's context differs and the fix may be a no-op
  there. Not worth proposing without first reproducing the warning against
  upstream's checkout.
