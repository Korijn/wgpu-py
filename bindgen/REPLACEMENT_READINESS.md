# Replacement readiness

Can the generated implementation replace the classic one (`wgpu/_classes.py` +
`wgpu/backends/wgpu_native/`)? This is the gap analysis, measured rather than
estimated, so the decision to delete the old code is evidence-based.

**Short answer: not yet — but the hard part is done.** The engine (bindings,
marshalling, async, errors, lifetimes) is complete and validated against a real
driver. What remains is *breadth of the public surface*, which is mostly thin
adaptation over things that already exist.

## Measured coverage

| Area | Status |
| --- | --- |
| C API surface | ✅ 228 functions bound |
| Spec objects / methods | ✅ 23 objects, 146 methods generated |
| Enums / flags / structs / constants | ✅ 54 / 5 / 80 / 11 generated |
| Old public **methods** with no generated counterpart | ✅ only **5** (all conveniences: `read_mapped`, `write_mapped`, `create_buffer_with_data`, `read_buffer`, `read_texture`) — 4 already in the shim |
| Validation errors → Python exceptions | ✅ implemented (previously **aborted the process**) |
| Async (`.wait()` and `await`) | ✅ works on a real device |
| Buffer mapping, data round-trip | ✅ byte-exact on lavapipe |
| Object lifetimes / release | ✅ release-on-GC, ancestry kept alive |

Crucially, the capabilities behind the remaining gaps are **already generated**:

* canvas/presentation: `surface.configure / get_current_texture / present / get_capabilities / unconfigure`
* every old texture property is backed by a generated getter (`get_width`, `get_format`, …)
* `adapter.get_info / get_limits / get_features`, `query_set.get_count / get_type`,
  `device.get_limits / get_features / push_error_scope / pop_error_scope`

So the work left is wrapping, not implementing.

## Gaps to close before deleting the old code

1. **Properties (64)** across the mapped classes — `label`, `uid`, `size`,
   `usage`, `map_state`, texture dimensions, `device.limits/features/queue/adapter`,
   `adapter.info/limits/features/summary`, `query_set.count/type`. Almost all map
   to an existing generated getter, so these can be **generated** rather than
   hand-written.

2. **19 classes with no spec object**:
   * `GPU` — the `wgpu.gpu` entrypoint (`request_adapter_sync/async`,
     `get_preferred_canvas_format`, `enumerate_adapters_*`).
   * `GPUCanvasContext` (8 methods) — the rendercanvas integration, on top of the
     generated `surface` object. Needed by every example and by pygfx.
   * Error classes — `GPUError`, `GPUValidationError`, `GPUOutOfMemoryError`,
     `GPUInternalError` now exist in `wgpu/_runtime/errors.py`; `GPUPipelineError`
     and the public export names still need wiring.
   * Info objects: `GPUAdapterInfo`, `GPUCompilationInfo`, `GPUCompilationMessage`,
     `GPUDeviceLostInfo`.
   * `GPUPromise`, `DrawCancelled`, and the mixins (`GPUObjectBase`,
     `GPUCommandsMixin`, `GPUBindingCommandsMixin`, `GPUDebugCommandsMixin`,
     `GPURenderCommandsMixin`, `GPUPipelineBase`) — exported today, so anything
     importing them by name would break.

3. **Module-level surface (~130 names)** — `wgpu.gpu`, the ~60 struct classes,
   `enums`/`flags`/`structs` modules, `diagnostics`, `logger`,
   `get_default_device`, `preconfigure_default_device`, `resources`, `backends`,
   `classes`, `rendercanvas_context_hook`, `version_info`.

4. **String-enum semantics.** Classic enums are *strings*
   (`wgpu.TextureFormat.rgba8unorm == "rgba8unorm"`), while generated ones are
   `IntEnum`. The shim already *accepts* classic spellings, but code that reads
   `wgpu.TextureFormat.rgba8unorm` and expects a string — pygfx does this — would
   see an int. This is a deliberate API decision to make, not just a port.

5. **`wgpu-native` extras** (~12 public functions): `set_instance_extras`,
   `multi_draw_indirect*`, `create_statistics_query_set`,
   `begin/end_pipeline_statistics_query`, `write_timestamp`, `get_wgpu_instance`,
   push constants. All exist in the low-level lib (they come from `wgpu.h`), so
   these are wrappers.

6. **Peripheral**: `utils.compute_with_buffers`, the imgui helper, the
   PyInstaller hook, and the diagnostics subsystem.

## Acceptance gate

The existing test suite is the objective gate. Today it passes **235/236**
against the classic implementation driving our from-source library. The rule:

> Delete the classic implementation only once the historical `tests/` suite
> passes against the **generated** implementation.

That converts "are we compatible?" from a judgement call into a number, and it
protects downstream users (pygfx) from silent regressions.

## Suggested order

1. Generate properties from the spec getters (closes gap 1 mechanically).
2. Decide the string-vs-int enum question (gap 4) — it shapes everything else.
3. `GPU` entrypoint + `GPUCanvasContext` + module-level exports (gaps 2, 3).
4. Extras and peripherals (gaps 5, 6).
5. Run `tests/` against the new layer; fix until green; then delete.
