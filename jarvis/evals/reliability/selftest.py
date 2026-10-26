"""`runner.py --selftest`: does the harness itself work on THIS PC? Run it before a gate run and read every line.

The gate judges JARVIS by reading the PC with its own eyes (env.py, ps/envprobe.ps1, the Node drivers). Those were written without a Windows machine at
hand, so each primitive is exercised once here with something harmless and removed again: a file, a Notepad window, a Recycle Bin round trip (only the
item made here, matched by its token), a message box, a rendered picture, the tool server, the face chat and the responsiveness probe.
A FAIL names the primitive. A gate run before every line says PASS (or SKIP with a reason) would measure the harness, not JARVIS.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Callable

from . import drivers, probes, stats
from .env import EnvError, LocalEnv


def run(env: LocalEnv, driver: Any, args: Any, sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print) -> int:
    tok = "st" + hashlib.sha256(str(time.time()).encode()).hexdigest()[:6]
    work = f"{args.work_root.rstrip(env.sep)}{env.sep}selftest-{tok}"
    results: list[tuple[str, str, str]] = []
    windows = env.__class__.windows is not LocalEnv.windows

    def step(name: str, fn: Callable[[], str], needs_windows: bool = False) -> None:
        if needs_windows and not windows:
            results.append((name, "SKIP", "not a Windows environment"))
            return
        try:
            results.append((name, "PASS", fn() or ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", f"{type(e).__name__}: {str(e)[:160]}"))

    def files() -> str:
        env.mkdir(work)
        p = f"{work}{env.sep}a.txt"
        env.write_file(p, "hello " + tok)
        assert env.read_file(p) == "hello " + tok, "read back differs"
        assert env.sha(p), "no sha"
        assert env.list_dir(work, "*.txt"), "list_dir empty"
        return work

    def listing() -> str:
        w = env.windows()
        assert w, "no windows listed"
        return f"{len(w)} windows, e.g. {w[0].process}: {w[0].title[:40]}"

    def notepad() -> str:
        before = {w.hwnd for w in env.windows()}
        path = f"{work}{env.sep}pad-{tok}.txt"
        env.open_notepad_text(path, "selftest text " + tok)
        sleep(3)
        new = [w for w in env.windows() if w.hwnd not in before and "pad" in (w.title + w.process).lower()]
        assert new, "no new Notepad window after 3 s"
        text = env.window_text(new[0])
        assert tok in text, f"UI Automation did not return the typed text (got {len(text)} characters)"
        env.close_window(new[0])
        sleep(1.5)
        assert not any(w.hwnd == new[0].hwnd for w in env.windows()), "the Notepad window did not close"
        return new[0].title[:40]

    def bin_roundtrip() -> str:
        p = f"{work}{env.sep}bin-{tok}.txt"
        env.write_file(p, "to the bin")
        env.recycle_file(p)
        sleep(1)
        assert not env.exists(p), "the file is still there"
        assert any(tok in n for n in env.recycle_names()), "the file is not in the Recycle Bin list"
        removed = env.recycle_purge(tok)
        assert removed >= 1, "bin-purge removed nothing"
        assert not any(tok in n for n in env.recycle_names()), "the item is still in the bin after the purge"
        return f"recycled, listed and purged {removed} item(s)"

    def msgbox() -> str:
        before = {w.hwnd for w in env.windows()}
        env.message_box("Selftest " + tok, "Disk almost full " + tok)
        sleep(3)
        new = [w for w in env.windows() if w.hwnd not in before and tok in w.title]
        assert new, "the message box did not appear"
        assert "Disk almost full" in env.window_text(new[0]), "UI Automation did not return the box text"
        env.close_window(new[0])
        return "box shown, read and closed"

    def picture() -> str:
        before = {w.hwnd for w in env.windows()}
        path = f"{work}{env.sep}pic-{tok}.png"
        env.open_image_text(path, "SELFTEST " + tok)
        sleep(4)
        assert env.exists(path) and env.size(path) > 1000, "the picture was not written"
        new = [w for w in env.windows() if w.hwnd not in before]
        assert new, "the picture viewer did not open"
        for w in new:
            env.close_window(w)
        return f"{env.size(path)} bytes"

    def tool_server() -> str:
        r = drivers.McpClient(args.mcp_url).call("jarvis_ready")
        assert "ready" in r, f"jarvis_ready answered {str(r)[:80]}"
        return r.get("message", "")[:80]

    def face() -> str:
        obs = driver.run("Say hello in one short sentence.", 90.0)
        assert obs.status != "harness_error", obs.harness_error
        assert obs.final_text.strip(), f"no final message was read back from the face (store_found={obs.raw.get('store_found')}, tail errors {obs.errors})"
        return f"read back: {obs.final_text[:60]!r}; store_found={obs.raw.get('store_found')}"

    def probe() -> str:
        s = probes.NodeSampler(env=env).input_latency_ms(5, "chat")
        assert len(s) >= 3, f"only {len(s)} samples"
        return f"5 keys: p50 {stats.p50(s):.0f} ms"

    step("files", files)
    step("window list", listing, True)
    step("notepad open, read by UI Automation, close", notepad, True)
    step("recycle bin round trip (token only)", bin_roundtrip, True)
    step("message box read by UI Automation", msgbox, True)
    step("picture rendered and opened", picture, True)
    step("tool server (127.0.0.1:48970) answers", tool_server)
    if getattr(driver, "name", "") == "face":
        step("face chat: type, read the answer back", face)
        step("responsiveness probe runs", probe)
    try:
        env.remove_tree(work)
    except EnvError:
        pass
    width = max(len(n) for n, _, _ in results)
    for name, status, detail in results:
        log(f"{status:5} {name:{width}}  {detail}")
    bad = [n for n, s, _ in results if s == "FAIL"]
    log("\nSELFTEST: " + ("every primitive works; a gate run can be trusted to read the PC." if not bad else f"FIX FIRST: {', '.join(bad)}. Results from a gate run would not be trustworthy."))
    return 1 if bad else 0
