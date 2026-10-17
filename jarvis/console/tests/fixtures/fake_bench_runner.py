"""A stand-in for bench.py in console tests: prints the same progress lines, slowly, and saves nothing.

    python fake_bench_runner.py <label> <cases> <seconds per case>
"""
import json
import sys
import time

label, n, delay = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
print(f"fake runner for {label}", flush=True)
for i in range(1, n + 1):
    print(f"@@start {i}/{n} F-{i:03d}", flush=True)
    time.sleep(delay)
    print(f"@@done {i}/{n} F-{i:03d} pass", flush=True)
print(json.dumps({"pass": n, "fail": 0, "error": 0, "deferred": 0}), flush=True)
