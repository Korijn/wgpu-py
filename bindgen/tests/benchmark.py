"""Benchmark the generated implementation against the historical one.

Nothing here touches an implementation detail, so the same file runs against a
pre-rewrite checkout too. Point PYTHONPATH at the checkout to compare, and give
both sides the same wgpu-native build -- then the Python layer is the only
variable:

    cargo build --release --lib --manifest-path wgpu-native/Cargo.toml
    lib=$PWD/wgpu-native/target/release/libwgpu_native.so

    PYTHONPATH=. python bindgen/tests/benchmark.py --label new
    PYTHONPATH=/path/to/pre-rewrite/checkout WGPU_LIB_PATH=$lib \\
        python bindgen/tests/benchmark.py --label classic

The pre-rewrite implementation loads that library at runtime; this one has the
matching static archive compiled in, so both end up in the same wgpu-native
code. Alternate the two and keep the best run of each: a single run of either
picks up whatever else the machine was doing.

Each case is timed with the same harness and reported as microseconds per
operation. The cases are chosen to separate the two things that actually
matter: per-call overhead on the hot path (command recording, which runs
thousands of times a frame) and descriptor marshalling on the cold path
(resource creation, which dominates startup).
"""

from __future__ import annotations

import argparse
import gc
import json
import time

#: (name, setup, operation) triples. Setup runs once; the operation is timed.
CASES: list = []


def case(name, count):
    def register(func):
        CASES.append((name, count, func))
        return func

    return register


def _timeit(func, count, settle=None) -> float:
    """Best-of-5 seconds per operation, with the GC quiesced.

    ``settle`` is called between repeats to let wgpu-native reclaim dropped
    resources. Without it, creating and discarding GPU objects in a loop gets
    steadily slower -- in *both* implementations, since the cost is inside
    wgpu-native -- and the benchmark would measure accumulated garbage rather
    than the Python layer.
    """
    gc.collect()
    gc.disable()
    try:
        best = float("inf")
        for _ in range(5):
            start = time.perf_counter()
            func(count)
            elapsed = time.perf_counter() - start
            best = min(best, elapsed)
            if settle is not None:
                gc.collect()
                settle()
    finally:
        gc.enable()
    return best / count


# ---- the cases -------------------------------------------------------------


def build_cases(wgpu, device):
    """Return ``[(name, count, callable)]`` for the given implementation."""
    cases = []

    def add(name, count, func):
        cases.append((name, count, func))

    # -- cold path: descriptor marshalling --------------------------------

    def create_buffer(n):
        for _ in range(n):
            device.create_buffer(size=256, usage="COPY_SRC|COPY_DST")

    add("create_buffer", 2000, create_buffer)

    def create_bind_group_layout(n):
        entries = [
            {"binding": 0, "visibility": "COMPUTE", "buffer": {"type": "storage"}}
        ]
        for _ in range(n):
            device.create_bind_group_layout(entries=entries)

    add("create_bind_group_layout", 1000, create_bind_group_layout)

    # -- hot path: command recording --------------------------------------

    shader = device.create_shader_module(
        code="""
        @group(0) @binding(0) var<storage,read_write> data: array<i32>;
        @compute @workgroup_size(1)
        fn main(@builtin(global_invocation_id) i: vec3<u32>) {
            data[i.x] = data[i.x] * 2;
        }
        """
    )
    buffer = device.create_buffer(size=1024, usage="STORAGE|COPY_SRC|COPY_DST")
    bgl = device.create_bind_group_layout(
        entries=[{"binding": 0, "visibility": "COMPUTE", "buffer": {"type": "storage"}}]
    )
    bind_group = device.create_bind_group(
        layout=bgl,
        entries=[
            {
                "binding": 0,
                "resource": {"buffer": buffer, "offset": 0, "size": buffer.size},
            }
        ],
    )
    pipeline = device.create_compute_pipeline(
        layout=device.create_pipeline_layout(bind_group_layouts=[bgl]),
        compute={"module": shader, "entry_point": "main"},
    )

    def dispatch_calls(n):
        encoder = device.create_command_encoder()
        cpass = encoder.begin_compute_pass()
        cpass.set_pipeline(pipeline)
        cpass.set_bind_group(0, bind_group)
        for _ in range(n):
            cpass.dispatch_workgroups(1)
        cpass.end()
        encoder.finish()

    add("dispatch_workgroups", 20000, dispatch_calls)

    def set_bind_group_calls(n):
        encoder = device.create_command_encoder()
        cpass = encoder.begin_compute_pass()
        cpass.set_pipeline(pipeline)
        for _ in range(n):
            cpass.set_bind_group(0, bind_group)
        cpass.end()
        encoder.finish()

    add("set_bind_group", 20000, set_bind_group_calls)

    def encoder_churn(n):
        # Submitting is what a real frame does, and it is also what lets
        # wgpu-native retire the command buffer instead of banking it.
        for _ in range(n):
            encoder = device.create_command_encoder()
            cpass = encoder.begin_compute_pass()
            cpass.set_pipeline(pipeline)
            cpass.end()
            device.queue.submit([encoder.finish()])

    add("record_pass", 500, encoder_churn)

    # -- data transfer -----------------------------------------------------

    payload = bytes(1024)

    def write_buffer(n):
        for _ in range(n):
            device.queue.write_buffer(buffer, 0, payload)

    add("write_buffer", 5000, write_buffer)

    # -- property reads ----------------------------------------------------

    def read_properties(n):
        for _ in range(n):
            _ = buffer.size
            _ = buffer.usage

    add("read_properties", 50000, read_properties)

    return cases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="write results to this file")
    parser.add_argument("--label", default="new")
    args = parser.parse_args()

    import wgpu

    t0 = time.perf_counter()
    if hasattr(wgpu.utils, "get_default_device"):
        device = wgpu.utils.get_default_device()
    else:  # pragma: no cover
        device = wgpu.gpu.request_adapter_sync().request_device_sync()
    startup = time.perf_counter() - t0

    def settle():
        """Give wgpu-native a chance to reclaim what the last repeat dropped."""
        try:
            device._pump()
        except AttributeError:  # the classic implementation polls on its own
            device._poll()

    results = {"startup_device": startup}
    for name, count, func in build_cases(wgpu, device):
        func(min(count, 50))  # warm up
        results[name] = _timeit(func, count, settle)
        print(f"{name:<28} {results[name] * 1e6:>10.3f} us/op")
    print(f"{'startup_device':<28} {startup * 1e3:>10.1f} ms")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"label": args.label, "results": results}, fh, indent=2)


if __name__ == "__main__":
    main()
