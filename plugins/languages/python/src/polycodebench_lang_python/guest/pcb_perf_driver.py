"""One fresh-process performance iteration (the paired run algorithm lives in Prompt 13).

Usage: python pcb_perf_driver.py --candidate-dir DIR --module NAME --workload FILE
                                 --scale N --seed S --output FILE

The workload file (trusted overlay) defines ``prepare(scale, seed)``, ``run(module, data)`` and
``verify(data, result)``. Only ``run`` is inside the timed region; output verification happens
afterwards and is never excluded from the candidate's computation. Memory is the process peak
resident set (``ru_maxrss``); the driver executes in a fresh interpreter per iteration so one
iteration's allocations never leak into the next.

Exit status: 0 verified, 3 output rejected by the verifier, 4 candidate raised, 2 usage/harness.
"""

import argparse
import gc
import importlib
import importlib.util
import json
import os
import resource
import sys
import time


def _load_workload(path):
    spec = importlib.util.spec_from_file_location("pcb_workload", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv):
    parser = argparse.ArgumentParser(prog="pcb_perf_driver")
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--module", required=True)
    parser.add_argument("--workload", required=True)
    parser.add_argument("--scale", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv[1:])
    sys.path.insert(0, os.path.abspath(args.candidate_dir))
    record = {
        "schema": "pcb-perf-iteration-v1",
        "metric_ids": ["elapsed_ns", "peak_rss_kb"],
        "memory_metric": "process_peak_rss_ru_maxrss_kb",
        "scale": args.scale,
        "seed": args.seed,
    }
    status = 2
    try:
        workload = _load_workload(args.workload)
        data = workload.prepare(args.scale, args.seed)
        module = importlib.import_module(args.module)
        record["rss_before_kb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        gc.collect()
        gc.disable()
        started = time.perf_counter_ns()
        try:
            result = workload.run(module, data)
        finally:
            finished = time.perf_counter_ns()
            gc.enable()
        record["elapsed_ns"] = finished - started
        record["peak_rss_kb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        record["verified"] = bool(workload.verify(data, result))
        status = 0 if record["verified"] else 3
    except Exception as error:  # candidate or workload failure is evidence, not a crash
        record["error"] = type(error).__name__
        record["verified"] = False
        status = 4
    parent = os.path.dirname(args.output)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(record, handle, sort_keys=True, separators=(",", ":"))
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
