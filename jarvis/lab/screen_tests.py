"""On-screen test runs (plan P6 step 7, item 15): run a test command on its own virtual display on the server, record the screen,
and keep the video only when the run fails, so the owner can replay exactly what went wrong.

    python3 screen_tests.py --out ~/jarvis/screen-runs --name ui-smoke [--keep always] [--live] -- npx playwright test --headed

How: Xvfb gives the command a private display (no TCP listener, so only local programs can draw on it); ffmpeg records it
(x11grab, H.264, 10 frames a second, about 1 MB a minute at 1280x800); the command runs with DISPLAY set and a time limit;
ffmpeg is stopped cleanly so the video is playable; the display is torn down. With --live, x11vnc shares the display
read-only on 127.0.0.1 for the console's live view (or noVNC) when x11vnc is installed; the owner watches, nothing listens
beyond loopback. Playwright's own trace and video (retain-on-failure) stay the test framework's job; this records the whole
screen, including dialogs and other windows a browser trace does not see.

Linux only (the Oracle VM, the Linux test image). The run never pushes or changes code: that is the fix loop's and the
push gate's job (jarvis_kernel/push_gate.py).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

DISPLAYS = range(90, 140)
FRAMERATE = 10
MAX_LOG = 200_000


class ScreenError(RuntimeError):
    pass


@dataclass
class RunResult:
    name: str
    exit_code: int
    passed: bool
    seconds: float
    video: str | None
    log: str
    live: str | None
    timed_out: bool


def free_display() -> int:
    for n in DISPLAYS:
        if not os.path.exists(f"/tmp/.X{n}-lock") and not os.path.exists(f"/tmp/.X11-unix/X{n}"):
            return n
    raise ScreenError("no free display number")


def _wait_for(path: str, proc: subprocess.Popen, seconds: float = 10.0) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if os.path.exists(path):
            return
        if proc.poll() is not None:
            raise ScreenError(f"{proc.args[0]} did not start")
        time.sleep(0.05)
    raise ScreenError(f"{proc.args[0]} did not start in time")


def _stop(proc: subprocess.Popen | None, sig=signal.SIGTERM, wait: float = 5.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.send_signal(sig)
        proc.wait(timeout=wait)
    except (subprocess.TimeoutExpired, ProcessLookupError):
        proc.kill()
        proc.wait(timeout=wait)


def run(cmd: list[str], out_dir: str | Path, name: str = "run", size: str = "1280x800", keep: str = "on-failure",
        timeout_s: int = 1800, live: bool = False, cwd: str | None = None) -> RunResult:
    if sys.platform != "linux":
        raise ScreenError("on-screen runs need Linux (Xvfb)")
    for tool in ("Xvfb", "ffmpeg"):
        if shutil.which(tool) is None:
            raise ScreenError(f"{tool} is not installed")
    if not cmd:
        raise ScreenError("no test command")
    if keep not in ("on-failure", "always", "never"):
        raise ScreenError("keep must be on-failure, always or never")
    w, h = (int(x) for x in size.split("x"))
    run_dir = Path(out_dir) / f"{time.strftime('%Y%m%d-%H%M%S')}-{''.join(c for c in name if c.isalnum() or c in '-_')[:40] or 'run'}"
    run_dir.mkdir(parents=True, exist_ok=False)
    video, log = run_dir / "screen.mp4", run_dir / "output.log"
    n = free_display()
    disp = f":{n}"
    xvfb = ff = vnc = None
    started = time.monotonic()
    timed_out, code, live_addr = False, -1, None
    try:
        xvfb = subprocess.Popen(["Xvfb", disp, "-screen", "0", f"{w}x{h}x24", "-nolisten", "tcp"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _wait_for(f"/tmp/.X11-unix/X{n}", xvfb)
        if keep != "never":
            # Fragmented MP4: the file is playable up to the last fragment even if ffmpeg is killed or the server stops.
            ff = subprocess.Popen(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "x11grab", "-video_size", f"{w}x{h}",
                                   "-framerate", str(FRAMERATE), "-i", disp, "-c:v", "libx264", "-preset", "veryfast",
                                   "-pix_fmt", "yuv420p", "-g", str(FRAMERATE), "-movflags", "+frag_keyframe+empty_moov",
                                   str(video)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _wait_for(str(video), ff, 10.0)               # recording has started before the test does
        if live and shutil.which("x11vnc"):
            port = 5900 + n
            vnc = subprocess.Popen(["x11vnc", "-display", disp, "-localhost", "-viewonly", "-shared", "-forever", "-nopw",
                                    "-rfbport", str(port), "-quiet"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            live_addr = f"vnc://127.0.0.1:{port} (view only)"
        env = {**os.environ, "DISPLAY": disp}
        env.pop("WAYLAND_DISPLAY", None)
        with open(log, "wb") as out:
            try:
                p = subprocess.run(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, timeout=timeout_s)
                code = p.returncode
            except subprocess.TimeoutExpired:
                timed_out, code = True, 124
            except OSError as e:
                out.write(f"could not start the command: {type(e).__name__}\n".encode())
                code = 127
    finally:
        _stop(ff, signal.SIGINT, wait=15.0)              # SIGINT: ffmpeg writes its last fragment and exits cleanly
        _stop(vnc)
        _stop(xvfb)
    if log.stat().st_size > MAX_LOG:                     # keep the end, where failures are
        data = log.read_bytes()[-MAX_LOG:]
        log.write_bytes(data)
    passed = code == 0
    kept = None
    if video.exists():
        if keep == "always" or (keep == "on-failure" and not passed):
            kept = str(video)
        else:
            video.unlink()
    result = RunResult(name, code, passed, round(time.monotonic() - started, 1), kept, str(log), live_addr, timed_out)
    (run_dir / "result.json").write_text(json.dumps(asdict(result), indent=1), encoding="utf-8")
    return result


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print("usage: screen_tests.py [options] -- <test command>", file=sys.stderr)
        return 2
    i = argv.index("--")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.expanduser("~/jarvis/screen-runs"))
    ap.add_argument("--name", default="run")
    ap.add_argument("--size", default="1280x800")
    ap.add_argument("--keep", default="on-failure", choices=("on-failure", "always", "never"))
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args(argv[:i])
    try:
        r = run(argv[i + 1:], args.out, args.name, args.size, args.keep, args.timeout, args.live)
    except ScreenError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(r), indent=1))
    return r.exit_code


if __name__ == "__main__":
    sys.exit(main())
