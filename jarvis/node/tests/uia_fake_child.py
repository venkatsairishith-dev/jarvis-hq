"""A real child process that speaks the UI Automation worker's protocol over a fake desktop, with faults it can be told to have (D68). Not a test file.

It runs the worker's own pieces (protect_stdout, Ops, serve; or main() itself when FAKE_UIA_VIA_MAIN=1) with fakeuia.FakeBackend as the library, plus extra
operations for the tests:
  crash  - die at once (exit 7) without answering       hang   - never answer (sleeps)          slow {s}  - answer after s seconds
  flood  - write a line far longer than the node accepts, no newline                           garble    - write a line that is not JSON
  big {n} - answer with n bytes of text                 noise  - print to stdout and to fd 1 (what a chatty library does), then answer
  pid    - the process id                               log    - the fake desktop's log        deaf      - from now on never answer a ping
Startup behaviour, from the environment: FAKE_UIA_START = ok | missing (ready:false, exit 3) | silent (never ready) | crash (exit before ready) | badproto.
FAKE_UIA_STARTS names a file that gets one line per start; FAKE_UIA_GRACE is the end-of-input grace in seconds.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fakeuia  # noqa: E402
import uia_worker as W  # noqa: E402


def main():
    log = os.environ.get("FAKE_UIA_STARTS")
    if log:
        with open(log, "a") as fh:
            fh.write(f"{os.getpid()}\n")
    mode = os.environ.get("FAKE_UIA_START", "ok")
    grace = float(os.environ.get("FAKE_UIA_GRACE", "3"))
    if mode == "crash":
        os._exit(9)
    if mode == "silent":
        time.sleep(120)
        return 0
    backend = fakeuia.FakeBackend()
    if mode == "missing":
        def loader():
            raise ImportError("no uiautomation here")
        return W.main(loader=loader, grace_s=grace)
    via_main = os.environ.get("FAKE_UIA_VIA_MAIN") == "1"
    out = None if via_main else W.protect_stdout()
    if mode == "badproto":
        out.write(b'{"ready":true,"proto":99}\n')
        time.sleep(120)
        return 0
    state = {"deaf": False}

    def deaf(a):
        state["deaf"] = True
        return {}

    def hang(a):
        time.sleep(120)
        return {}

    def flood(a):
        out.write(b"x" * (W.MAX_RESPONSE * 3))                  # no newline: the node must give up on the line, not wait for its end
        return {}

    def garble(a):
        out.write(b"this is not json at all\n")
        return {}

    def noise(a):
        print("a library warning on stdout")
        sys.stdout.flush()
        os.write(1, b"another one straight to fd 1\n")
        return {"noisy": True}

    ops = {"crash": lambda a: os._exit(7), "hang": hang, "slow": lambda a: (time.sleep(float(a.get("s", 0.2))), {"slept": True})[1],
           "big": lambda a: {"text": "y" * int(a.get("n", 10))}, "pid": lambda a: {"pid": os.getpid()},
           "log": lambda a: {"log": [list(x) for x in backend.d.log]}, "deaf": deaf, "noise": noise,
           "state": lambda a: {"state": fakeuia.desktop_state(backend.d)},
           "setup": lambda a: fakeuia.apply_setup(backend.d, a["name"], a.get("value")) or {}}
    if via_main:                                                  # the worker's own main(): it protects stdout itself
        return W.main(loader=lambda: (backend, "fake 1.0"), extra_ops=ops, grace_s=grace)
    ops.update(flood=flood, garble=garble)
    wops = W.Ops(backend)
    wops.table.update(ops)
    orig = wops.table["ping"]
    wops.table["ping"] = lambda a: (time.sleep(120) if state["deaf"] else orig(a))
    W.serve(wops, sys.stdin.buffer, out, lib="fake 1.0", grace_s=grace)
    return 0


if __name__ == "__main__":
    sys.exit(main())
