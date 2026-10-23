"""A throwaway console for the Bench screen's browser test: the real console and a real (temporary) kernel owner plane,
real benchmark result files copied into a temp results folder, the real decide/coordinator/policy code for the challenge
lab (no model: rules only), and a tiny fake runner instead of the benchmark (so a run can be started and cancelled
without running 572 cases). Prints one JSON line with the port, then serves until killed.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "kernel"))
from jarvis_console import bench as benchmod
from jarvis_console.config import Config
from jarvis_console.server import make_server
from jarvis_kernel.llm_proxy import make_handler
from jarvis_kernel.store import Kernel

HQ = os.path.abspath(os.path.join(HERE, "..", ".."))
PORT = int(os.environ.get("E2E_CONSOLE_PORT", "8812"))
UI = os.environ.get("E2E_UI_DIR", os.path.join(HQ, "ui", "dist"))
REAL_RESULTS = os.path.join(HQ, "evals", "quality95", "results")
FAKE = os.path.join(HERE, "fixtures", "fake_bench_runner.py")
FIXTURES = ["bench-20261016-173117.json", "bench-20261016-181824.json", "bench-20261017-031703.json", "score-20261016-150635-baseline.json",
            "score-20261016-154951-server-rescore.json", "score-20261016-161950-interim.json", "scorecard-v2-20261017-031704.json",
            "context-holdout-20261017-023635.json", "context-holdout2-20261017-045353.json", "live-dahl-20261016-163134.json",
            "live-research-dahl-20261016-184005.json", "live-profiles-20261016-165202.json"]


def main():
    tmp = tempfile.mkdtemp(prefix="jarvis-e2e-bench-")
    results = os.path.join(tmp, "results")
    os.makedirs(results)
    for n in FIXTURES:
        shutil.copy(os.path.join(REAL_RESULTS, n), results)
    k = Kernel(os.path.join(tmp, "kernel"))
    H, tokens = make_handler(k, [], owner_hermes=lambda text: "Done.")
    ksrv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=ksrv.serve_forever, daemon=True).start()
    tokfile = os.path.join(tmp, "owner.token")
    with open(tokfile, "w") as fh:
        fh.write(tokens.issue("console", "owner"))
    cfg = Config(port=PORT, kernel_url=f"http://127.0.0.1:{ksrv.server_address[1]}", token_file=tokfile, outputs_dir=os.path.join(tmp, "outputs"),
                 ui_dir=UI, hub_url="http://127.0.0.1:9", local_llm_url="http://127.0.0.1:9", intent_llm_url="http://127.0.0.1:9",
                 voice_dir=os.path.join(tmp, "voice"), bench_results_dir=results, model_dir=os.path.join(tmp, "models"))
    delay = os.environ.get("E2E_FAKE_DELAY", "0.4")

    def command(kind, spec):                       # 30 fake cases per step, 0.4 s each: long enough to watch and cancel
        return [sys.executable, "-u", FAKE, spec.get("area") or "all", "30", delay]
    srv = make_server(cfg, bench=benchmod.Bench(cfg, command=command))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import control_auth                      # the throwaway console publishes its master token like main() does (JARVIS_RUN_DIR)
    control_auth.publish("console", srv.console_secret)
    print(json.dumps({"console": PORT, "tmp": tmp}), flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
