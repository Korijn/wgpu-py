# Post-rewrite review

A full pass over the repository tree (excluding the `wgpu-native` submodule)
after the generated-bindings rewrite landed. It answers five questions: is the
tree organised sensibly, can consumers choose between the bare binding and the
IDL-aligned API, do the docs match the new architecture, are there leftover
files, and is there dead code.

Baseline at the time of review: `tests` + `bindgen/tests` are 378 passed /
1 skipped, and `ruff check` / `ruff format --check` are clean. Nothing below is
a test failure -- these are things the test suite cannot see.

Each item cites the file and line so it can be actioned without re-deriving it.

## Summary

| # | Finding | Kind |
| --- | --- | --- |
| 1 | `set_instance_extras` was dropped but is still documented and used by two examples | regression |
| 2 | `.readthedocs.yaml` cannot build the docs any more | broken infra |
| 3 | `.github/workflows/screenshots.yml` cannot install the package any more | broken infra |
| 4 | `tools/download_dxc.py` imports a deleted module | broken script |
| 5 | Neither layer is selectable or public | architecture |
| 6 | README / CONTRIBUTING / docs / issue template / changelog describe the old architecture | docs |
| 7 | `wgpu/_compat/` is an orphaned 242-line package | dead code |
| 8 | Dead symbols and two duplicated primitives in `wgpu/_coreutils.py` | dead code |
| 9 | Assorted smaller leftovers | cleanup |

Items 1-4 fail for a user or for CI. Items 5-9 do not fail, but they are the
difference between "the rewrite works" and "the rewrite is finished".

## 1. `set_instance_extras` is gone (regression)

Not a documentation problem. The function exists on `master` in
`wgpu/backends/wgpu_native/extras.py` and does not exist on the rewritten
branch. The async `request_device` went with it.

```
master: PipelineStatisticName, request_device_sync, request_device,
        multi_draw_indirect, multi_draw_indexed_indirect,
        multi_draw_indirect_count, multi_draw_indexed_indirect_count,
        create_statistics_query_set, begin_pipeline_statistics_query,
        end_pipeline_statistics_query, write_timestamp, set_instance_extras
branch: (same, minus request_device and set_instance_extras, plus
        enumerate_adapters)
```

Still referenced in three places:

* `docs/backends.rst:223-251` -- a full signature and parameter list, plus a
  worked usage example at `docs/backends.rst:243`.
* `examples/extras_dxc.py:14` -- `from wgpu.backends.wgpu_native.extras import
  set_instance_extras`, the entire point of the example.
* `examples/extras_debug.py:100` -- same import, inside `setup_demo()`.

Both examples carry `# run_example = false`, which is exactly why CI did not
catch this. That marker excludes them from the example test suite, so an
unimportable symbol in them is invisible.

This affects the DX12/DXC path (selecting the compiler, shader model, memory
budgets) and the RenderDoc debug-symbols workflow. Restoring it should come
with a test that at least imports the two examples, so the next removal is not
silent.

## 2. `.readthedocs.yaml` cannot build the docs

`docs/conf.py:27` does `import wgpu`. That now reaches the compiled extension:
`wgpu/__init__.py` imports `.classes` -> `_api` -> `_generated/apiclasses.py`,
which at line 33 does `from wgpu._native import ffi as _ffi, lib as _lib`.
Producing that extension needs the `wgpu-native` submodule and a Rust
toolchain.

`.readthedocs.yaml` declares neither. It has `build.tools.python` only, and no
`submodules:` section, so on Read the Docs the checkout has an empty
`wgpu-native/` and no `cargo`. The build fails at `import wgpu`.

Needs `build.tools.rust` and `submodules: include: all`. Worth checking the RTD
build-time limit too -- a cold `cargo build --release` of wgpu-native is not
quick.

## 3. `.github/workflows/screenshots.yml` cannot install the package

`.github/workflows/screenshots.yml:15` is a bare `uses: actions/checkout@v4`
with no `with:` block, so no submodule. Every job in `ci.yml` passes
`submodules: recursive` precisely because the build now compiles the submodule.
The subsequent `pip install -U -e .[tests,examples]` therefore runs
`tools/hatch_build.py` against an empty `wgpu-native/`.

Separately, this workflow still filters `pull_request: branches: [main]`
(lines 6-7). `ci.yml` deliberately removed that filter, with a comment
explaining that this repo is a fork whose trunk is not `main` and that the
filter silently disabled CI. The same reasoning applies here.

## 4. `tools/download_dxc.py` imports a deleted module

`tools/download_dxc.py:11`:

```python
from download_wgpu_native import RESOURCE_DIR, download_file, get_os_string
```

`tools/download_wgpu_native.py` was deleted in the rewrite -- correctly, since
nothing downloads a prebuilt library any more. The DXC script cannot be
imported at all now, and `examples/extras_dxc.py:4` tells users to run it.

Either give it its own `RESOURCE_DIR` / download helpers, or drop it along with
the resource-directory concept (see item 9).

## 5. Consumers cannot choose between the bare binding and the IDL API

Both layers exist and are cleanly separated internally. Neither is selectable,
and neither is public.

`wgpu/_generated/` already splits along the right seam:

| layer | modules | derived from |
| --- | --- | --- |
| C-shaped | `objects.py`, `classes.py`, `structs.py`, `enums.py`, `flags.py`, `constants.py` | `webgpu.json` (wgpu-native) |
| IDL-shaped | `apiclasses.py`, `apistructs.py`, `apienums.py`, `apiflags.py` | `webgpu.idl` (W3C) |

But `wgpu/__init__.py` eagerly does `from .classes import *`, so importing
anything under `wgpu` executes the whole public API:

```
$ python -c "import wgpu._native, sys; print('wgpu._generated.apiclasses' in sys.modules)"
True
```

Reaching for the mid-level layer instead does not help either:
`wgpu/_runtime/api.py:14` has `Api.__init__` import `apiclasses`, `apienums`
and `apiflags` to build its class registry, so the low-level facade pulls the
high-level layer in by construction.

Everything involved is under a `_` name, so there is no supported entry point
in either direction. The pieces are there; what is missing is a public name for
the low-level layer, a stated stability contract for it, and a lazy root
`__init__`.

This is also the prerequisite for ever splitting the compiled extension into
its own distribution: the runtime contract between the layers is already just
two names (`ffi` and `lib`, imported in ten modules), but it is not written
down anywhere as a contract.

## 6. Documentation describes the pre-rewrite architecture

None of the prose has been updated. Grouped by file:

**`README.md:125-127`** -- the development install still says `pip install -e .`
"will also download the upstream wgpu-native binaries", points at
`python tools/download_wgpu_native.py` (deleted), `python codegen` (now
`bindgen`), and offers `WGPU_LIB_PATH` (no longer read anywhere in the tree).

**`CONTRIBUTING.md:123`** -- links `codegen/README.md`, which does not exist;
the file is `bindgen/README.md`. **`CONTRIBUTING.md:134`** -- `pytest -v
codegen` should be `pytest -v bindgen/tests`.

**`docs/start.rst`**
* line 10 -- "Pypy is supported". The wheels are `cp311`-`cp313` only; the
  package is now a CPython extension.
* line 27 -- "The wheels that pip installs include the prebuilt binaries of
  wgpu-native". They contain a statically linked extension.
* line 32 -- `WGPU_LIB_PATH`, no longer read.
* line 63 -- "manylinux_2_24". `pyproject.toml` pins `manylinux_2_28`.

**`docs/backends.rst`**
* lines 14, 36 -- `import wgpu.backends.wgpu_natve`, misspelled (pre-existing).
* line 23 -- "a backend is automatically selected in the first call to
  `request_adapter`". There is no backend selection any more.
* line 43 -- "the wgpu-native DLL is shipped with wgpu-py".
* lines 47-57 -- documents `request_device_sync(adapter, trace_path, ...)`.
  Tracing was removed upstream (gfx-rs/wgpu#5974) and wgpu-native's `trace`
  cargo feature is commented out; the implementation now says so rather than
  silently writing nothing.
* lines 223-251 -- `set_instance_extras`, see item 1.

**`docs/guide.rst`**
* line 239 -- still tells users `adapter.request_device_sync()` takes a
  directory path and writes an API trace.
* line 250 -- the PyInstaller hook "ensures that the wgpu-native DLL is
  included". It now adds `_cffi_backend`; see `wgpu/__pyinstaller/hook-wgpu.py`.

**`docs/wgpu.rst:173`** -- "The GPURenderBundle and GPURenderBundleEncoder can
be used to record commands to be used multiple times, but this is not yet
implemented in wgpu-py." They are generated and covered by
`tests/test_wgpu_native_render.py`, `tests/test_set_immediates.py` and
`tests/test_wgpu_vertex_instance.py`.

**`docs/utils.rst:46-49`** -- documents `wgpu.utils.BaseEnum` as the "Base class
for flags and enums". It is not the base of anything any more; see item 8.

**`docs/conf.py:50`** -- `wgpu.structs._use_sphinx_repr = True`. Nothing reads
that attribute, so the struct-repr tweak silently does nothing.

**`.github/ISSUE_TEMPLATE/update-wgpu.md`** -- the canonical "how to update
wgpu" checklist, entirely pre-rewrite: it links `codegen/README.md`, and its
steps reference `idlparser.py`, `hparser.py`, `tools/download_wgpu_native.py`,
`wgpu_native/_api.py` and FIXME comments in `_classes.py`. The whole point of
the rewrite is that this process is now "bump the submodule, re-run the
generator, review the `_generated/` diff" -- and this template is where a
contributor looks first.

**`CHANGELOG.md`** -- the `[unreleased]` section covers only the pre-rewrite
wgpu-native 29.0.0.0 bump and `DrawCancelled`. Nothing about generated
bindings, binary wheels, static linking, the removal of `WGPU_LIB_PATH`, the
interpreter support change, or the removal of tracing.

**`bindgen/README.md`**
* The status table still says "Minimal compatibility shim -- in progress" (it
  is dead code, item 7) and "cibuildwheel matrix -- planned" (it shipped).
* The architecture diagram shows only `headers.py` and `ffi_build.py`, omitting
  `genapi.py`, `idl.py`, `bridge.py`, `naming.py` and `generate.py` -- which is
  most of what bindgen does and all of the high-level generation.
* The "Validating against the existing test suite" section instructs the reader
  to drive the library through the classic implementation via `WGPU_LIB_PATH`.
  That implementation no longer exists in this repository.
* The testing section says `pytest bindgen/tests --noconftest`; `ci.yml` runs
  `pytest -v bindgen/tests`.

**`bindgen/REPLACEMENT_READINESS.md`** -- worth keeping for its design
rationale, but it is framed as a pre-deletion decision document and ends on an
"Acceptance gate" for a deletion that has happened. Its performance table also
now conflicts with the measured comparison posted on PR #1: it claims
`create_bind_group_layout` at 1.25x, property reads at 1.1x and "Every case is
now faster than the implementation it replaces", where six interleaved rounds
put both of those at parity (0.96x and 0.94x). Either reconcile the numbers or
mark the section as superseded.

## 7. `wgpu/_compat/` is orphaned

`wgpu/_compat/__init__.py` is 242 lines implementing a classic-API shim
(`GPUBuffer`, `GPUQueue`, `GPUDevice`, `GPUAdapter` wrappers, `enum_value`,
`get_default_device`, `request_adapter_sync`).

Nothing imports it. It was added in `c77e8be` as a transitional step and
superseded when the generated public API landed; it was never removed.

## 8. Dead code and duplicated primitives in `wgpu/_coreutils.py`

| symbol | line | status |
| --- | ---: | --- |
| `get_header_filename` | 29 | no callers |
| `get_library_filename` | 52 | no callers |
| `ApiDiff` | 204 | no callers -- the classic codegen's API-diff mechanism, replaced by the explicit attachment block in `wgpu/_api/__init__.py` |
| `_resource_files` (`ExitStack` + `atexit`) | 17 | only feeds the two dead getters above |
| `error_message_hash` | 98 | referenced only by `tests/test_util_core.py` |
| `str_flag_to_int`, `_flag_cache` | 178 | referenced only by `tests/test_util_core.py` |

The last two are dead code kept alive by its own test. `tests/test_util_core.py`
imports all three symbols directly from `wgpu._coreutils` and exercises nothing
the library itself calls.

Two primitives exist twice:

**`EnumType`** -- `wgpu/_coreutils.py:111` and `wgpu/_runtime/enumbase.py:13`,
near-identical implementations. The generated enums use the `_runtime` one,
which leaves `wgpu.utils.BaseEnum` (`_coreutils.py:165`) an orphan:

```
>>> issubclass(wgpu.TextureFormat, wgpu.utils.BaseEnum)
False
>>> wgpu.utils.BaseEnum.__subclasses__()
[]
```

`docs/utils.rst:49` still documents it as the base class for flags and enums.

**`ArrayLike` / `CanvasLike`** -- `wgpu/_coreutils.py:23,26` and
`wgpu/_api/types.py:8,9`. Generated code imports the `_api/types.py` pair;
`wgpu/utils/device.py:6` imports the `_coreutils` one.

Elsewhere:

* `wgpu/_runtime/__init__.py:15` -- `get_struct_builder()`, an `lru_cache`d
  factory with no callers. `Api.__init__` constructs its own `StructBuilder`.
* `wgpu/_generated/apiclasses.py:24-30` -- the generator emits a `TYPE_CHECKING`
  block that re-imports `apistructs` (already imported at line 20) and imports
  `GPUHandle` / `GPUObjectBase` from the module being generated. Harmless, but
  it is the emitter producing noise.

## 9. Leftovers and smaller items

* **`wgpu/resources/`** is now an empty package -- only `__init__.py`. It is
  kept alive by `wgpu/__init__.py:16` (`from . import resources`) and an
  assertion in `tests/test_api.py:40`. Its two accessors in `_coreutils` are
  dead (item 8) and the only other consumer was `download_dxc.py` (item 4).
* **`wgpu/backends/auto.py`** still implements runtime backend selection,
  including an `emscripten` branch, while the sibling `backends/__init__.py`
  docstring states that backend selection no longer exists.
* **`.gitignore:3-6`** still ignores `wgpu/resources/*.dll`, `*.so`, `*.dylib`
  and `commit-sha`; nothing writes those. **`.gitignore:136`**
  (`bindgen/**/__pycache__/`) is redundant with the global `__pycache__/`.
  `docs/gallery/`, `docs/_static/*.whl` and `docs/sg_execution_times.rst` are
  sphinx-gallery leftovers with no gallery in the tree.
* **`ci.yml:244-247`** tests py3.14 and pypy3.11, neither of which the wheel
  matrix (`cp311-*  cp312-*  cp313-*`) covers. A cffi API-mode extension
  statically linking Rust is a materially different proposition on PyPy; worth
  deciding deliberately rather than inheriting.
* **Two generator entry points.** `python -m bindgen.generate` then
  `python -m bindgen.genapi`, which `ci.yml` has to run in sequence. A single
  `python -m bindgen` would be simpler and would match the `python codegen` the
  docs still describe.
* **`examples/wgpu-examples.ipynb`** is 178 KB with stored cell outputs
  committed (pre-existing, not from the rewrite).
* **`examples/README.md`** links to a `#developers` anchor in the root README;
  the section is now "Contributing" / "Development install".

## What holds up well

Worth recording, since a review that only lists problems misrepresents the
state of the tree.

* The `bindgen/` vs `wgpu/` split is enforced, not merely intended: two tests
  check the boundary, one statically and one by making `bindgen` unimportable
  and running the real call path.
* `wgpu/_generated/` splitting C-shaped from IDL-shaped output is the right
  seam, and it is already load-bearing.
* `_runtime/` (mechanics) versus `_api/` (policy) is a clean division, and
  `wgpu/_api/__init__.py` listing every divergence from WebGPU in one readable
  block is a genuinely good decision -- the ways wgpu-py differs from the web
  API can be read off in one place.
* The runtime coupling to C is two names, `ffi` and `lib`. No build-time
  artifacts leak into the runtime.
* CI asserting that `wgpu/_generated/` matches a fresh generator run is what
  keeps "fully generated" true rather than aspirational.
