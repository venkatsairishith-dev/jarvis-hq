"""A real, throwaway JARVIS for browser tests: kernel owner plane + console + real voice models, with seeded records.

Not a mock: the same kernel code the server runs, in a temp data dir, with a scripted stand-in only for the one thing that
cannot run here (the owner's Hermes agent). Prints one JSON line with the ports, then serves until killed.
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "kernel"))
from jarvis_console.config import Config
from jarvis_console.server import make_server
from jarvis_kernel.llm_proxy import make_handler
from jarvis_kernel.memory import Memories
from jarvis_kernel.policy import Action
from jarvis_kernel.store import Kernel
from jarvis_kernel.tasks import Tasks

PORT = int(os.environ.get("E2E_CONSOLE_PORT", "8811"))
UI = os.environ.get("E2E_UI_DIR", os.path.abspath(os.path.join(HERE, "..", "..", "ui", "dist")))
KERNEL_FILE = os.environ.get("E2E_STATE_FILE")


def main():
    tmp = tempfile.mkdtemp(prefix="jarvis-e2e-")
    k = Kernel(os.path.join(tmp, "kernel"))
    log = []

    def hermes(text):
        log.append(text)
        time.sleep(0.4)
        if "fail" in text.lower():
            raise RuntimeError("agent HTTP 500; the next message starts a fresh conversation")
        return "Done. I opened your Downloads folder. Nothing else was changed."

    H, tokens = make_handler(k, [], owner_hermes=hermes)
    plane = H.owner_plane
    ksrv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=ksrv.serve_forever, daemon=True).start()
    token = tokens.issue("console", "owner")
    tokfile = os.path.join(tmp, "owner.token")
    with open(tokfile, "w") as fh:
        fh.write(token)
    plane.set_pin("correct horse")

    # seeded, real records
    T = Tasks(k)
    done = T.create("hermes", "private", "make a word counter", "Build a word-count command line tool in Python", ["write wordstat.py", "write tests"],
                    ["the tool counts words correctly", "tests pass from a clean folder"])
    T.update("hermes", done, state="running", attempt=True)
    T.update(None, done, state="verifying")
    T.update(None, done, state="completed", result="Delivered wordstat.py and test_wordstat.py", verification={"passed": True, "checks": ["tests exit 0"]},
             evidence=["job:abc123def456"])
    running = T.create("hermes", "private", "research the best open source speech models", "Compare local speech-to-text options and write a report",
                       ["compare 3 options", "cite sources"])
    T.update("hermes", running, state="running", step="reading model cards", attempt=True)
    k.request(Action("ui", "click", "notepad|Save", lane="private", untrusted_session=True))      # a real pending YELLOW approval
    Memories(k).add("hermes", "I prefer dark themes and concise answers", "preference")
    Memories(k).add("hermes", "My projects live under D:\\jarvis", "fact")

    out = os.path.join(tmp, "outputs")
    os.makedirs(os.path.join(out, f"task-{done}"))
    with open(os.path.join(out, f"task-{done}", "wordstat.py"), "w") as fh:
        fh.write("import sys\nprint(len(sys.stdin.read().split()))\n")
    with open(os.path.join(out, f"task-{done}", "report.html"), "w") as fh:
        fh.write("<h1>Report</h1><p>Delivered.</p><script>document.title='PWNED'</script>")

    cfg = Config(port=PORT, kernel_url=f"http://127.0.0.1:{ksrv.server_address[1]}", token_file=tokfile, outputs_dir=out, ui_dir=UI,
                 hub_url="http://127.0.0.1:9", local_llm_url="http://127.0.0.1:9")
    srv = make_server(cfg)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    info = {"console": PORT, "kernel": ksrv.server_address[1], "tmp": tmp, "tasks": {"done": done, "running": running}}
    print(json.dumps(info), flush=True)
    if KERNEL_FILE:
        with open(KERNEL_FILE, "w") as fh:
            json.dump(info, fh)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
