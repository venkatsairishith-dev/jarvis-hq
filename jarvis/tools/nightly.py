"""Nightly self-test (D71): while the owner sleeps or works, check that JARVIS still works, compare with the last night, write ONE content-free report,
and say so quietly only when something got worse. Local only. No model of Anthropic's is ever called; the only text sent to a local model is a fixed word.

    python jarvis/tools/nightly.py [--print] [--force] [--wait-idle-min N] [--no-heavy] [--no-models]
        [--inbox D:\\jarvis\\data\\muse\\inbox.jsonl] [--push-topic-file D:\\jarvis-secrets\\ntfy.topic]
    exit 0 = ran, nothing failing   1 = ran, at least one check failing (see the report)   3 = skipped, the owner is active   2 = refused (not on D:)

Started by the Windows Task Scheduler (docs/05_OPERATIONS.md, "Nightly self-test"; nothing is registered by this file). It uses the SAME inbox and push topic as the page-watch
runner (jarvis/muse), so a regression arrives the way a watch hit does: one line in the inbox with check NAMES, and on the phone only "JARVIS: 1 new notice".

What it runs (a check is a name and pass / warn / fail / skip, plus a number where there is one; never a text of the owner's):
  light, any time the owner has been still for --min-idle-s (default 120 s):
    doctor:*          the posture check (jarvis/tools/doctor.py), one line per area
    footprint:*       the D50 meter against the owner's budget: cpu, ram, vram, disk writes, nothing outside D:
    gateway, gateway2, provider:<name>   the privacy gateways' /health, and each provider that is cooling down (degraded)
    translation, tool_server, local_models   the translation service, the face's tool server, llama-swap: up or not
    reliability_goals   goals.json of the reliability gate is sound
    backup:*          the newest learned-state backup verifies (state_backup.py) and is not old
  heavy, only after --heavy-idle-s (default 600 s) of stillness (they load models or open windows of their own):
    specialist:<alias>   each local specialist model answers a fixed one-word prompt
    translation:answers  a fixed English word goes to Telugu and back
    window_responsiveness   input latency of the face window, raw DevTools input (no OS-level click or key), p90 under 150 ms
    selftest:*        the reliability harness's own primitives (jarvis/evals/reliability/selftest.py): files, a Notepad it opens and closes itself, the bin round trip
Never run: an attended goal, a goal, anything that clicks or types in the owner's apps, anything that needs the PIN.

Quiet: it lowers its own priority (BELOW_NORMAL and EcoQoS through the supervisor's throttle), runs one check at a time, gives every request a timeout, stops at a time box,
and stops the moment the owner comes back. The alert rule: a check that was fine and now fails, a provider that was fine and is now degraded, a D50 metric over its budget; the
same alert is not repeated the next night unless it got worse (a worse level, or a number more than 10% above the one the owner was told).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from jarvis import safe_mode  # noqa: E402
from jarvis.pluginapi.paths import data_dir  # noqa: E402

SCHEMA = 1
LEVELS = {"pass": 0, "warn": 1, "fail": 2}
RESPONSIVE_LIMIT_MS = 150.0           # PROTOCOL.md: p90 input latency and click-to-paint under 150 ms
WORSE_FACTOR = 1.10                   # a number counts as worse when it is more than 10% above the one the owner was told
KEEP_REPORTS = 30
GATEWAY = "http://127.0.0.1:48950"
GATEWAY2 = "http://127.0.0.1:48954"
TRANSLATE = "http://127.0.0.1:48960"
LOCAL_MODELS = "http://127.0.0.1:8080"
TOOL_SERVER = ("127.0.0.1", 48970)
BACKUP_MAX_AGE_DAYS = 8
FIXED_PROMPT = "Reply with the single word OK."
WORD = re.compile(r"[a-z][a-z0-9_]{0,39}")


class Unavailable(RuntimeError):
    """A check cannot run on this machine or right now (a service is down, a tool is missing): reported as skip or fail by the caller, never a crash."""


@dataclass
class Check:
    name: str
    status: str                       # pass | warn | fail | skip
    group: str = "light"
    value: float | None = None
    unit: str = ""
    limit: float | None = None
    note: str = ""                    # a fixed phrase written by this file, never text read from a service or the owner
    worse_by_value: bool = False

    def level(self) -> int:
        """How loud this check is for the alert rule: fail = 2; a degraded provider = 1; everything else is silent."""
        if self.status == "fail":
            return 2
        if self.status == "warn" and self.name.startswith("provider:"):
            return 1
        return 0


# ---- the real world, in one place so the tests can replace it ---------------------------------------------------------------------------------

@dataclass
class World:
    now: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    idle_ms: Callable[[], int | None] = field(default=None)
    get_json: Callable[[str, float], tuple[int, Any]] = field(default=None)
    post_json: Callable[[str, dict, float], tuple[int, Any]] = field(default=None)
    tcp: Callable[[str, int, float], bool] = field(default=None)
    doctor: Callable[[], list] = field(default=None)
    footprint: Callable[[], dict] = field(default=None)
    selftest: Callable[[], list[tuple[str, str]]] = field(default=None)
    responsiveness: Callable[[], list[float]] = field(default=None)
    specialist_aliases: Callable[[], tuple[list[str], set[str]]] = field(default=None)
    goals_problems: Callable[[], list[str]] = field(default=None)
    backups: Callable[[], dict] = field(default=None)
    lower_priority: Callable[[], None] = field(default=None)
    cpu_seconds: Callable[[], float] = field(default=None)

    def __post_init__(self):
        for name, fn in _DEFAULTS.items():
            if getattr(self, name) is None:
                setattr(self, name, fn)


def _idle_ms() -> int | None:
    """Milliseconds since the owner's last key or mouse move, from the node's own reader (jarvis_node.windows.input_state, GetLastInputInfo), or None."""
    if os.name != "nt":
        return None
    try:
        node = str(ROOT / "jarvis" / "node")
        if node not in sys.path:
            sys.path.insert(0, node)
        from jarvis_node import windows
        got = windows.input_state()
        return int(got["idle_ms"]) if isinstance(got.get("idle_ms"), int) else None
    except Exception:  # noqa: BLE001 - unknown idle time is a normal answer
        return None


def _get_json(url: str, timeout: float) -> tuple[int, Any]:
    import urllib.error
    from jarvis.plugins._shared import loopback_http
    try:
        with loopback_http.urlopen(url, timeout=timeout) as r:
            body = r.read(1_000_000)
            try:
                return r.status, json.loads(body.decode("utf-8", "replace"))
            except ValueError:
                return r.status, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:  # noqa: BLE001
        return 0, None


def _post_json(url: str, body: dict, timeout: float) -> tuple[int, Any]:
    import urllib.error
    import urllib.request
    from jarvis.plugins._shared import loopback_http
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with loopback_http.urlopen(req, timeout=timeout) as r:
            raw = r.read(2_000_000)
            try:
                return r.status, json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                return r.status, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:  # noqa: BLE001
        return 0, None


def _tcp(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _doctor() -> list:
    from jarvis.tools import doctor
    return doctor.run_all()


def _footprint() -> dict:
    try:
        from jarvis.tools import footprint
        return footprint.report_once(5.0, scan=True)
    except (ImportError, FileNotFoundError, NotImplementedError) as e:
        raise Unavailable(type(e).__name__) from None


def _selftest() -> list[tuple[str, str]]:
    """The harness's own primitives, as [(step name, PASS|FAIL|SKIP)]. The driver is the tool server's (mcp): no chat model, no face chat step."""
    import types
    from jarvis.evals.reliability import drivers, runner, selftest
    from jarvis.evals.reliability.env import LocalEnv, WindowsEnv
    work = runner.DEFAULT_WORK if os.name == "nt" else os.path.join(os.environ.get("TMPDIR", "/tmp"), "jarvis-reliability")
    args = types.SimpleNamespace(work_root=work, mcp_url=drivers.MCP_URL)
    env = WindowsEnv() if os.name == "nt" else LocalEnv()
    lines: list[str] = []
    selftest.run(env, drivers.McpDriver(drivers.McpClient(args.mcp_url)), args, log=lines.append)
    return parse_selftest(lines)


def parse_selftest(lines: list[str]) -> list[tuple[str, str]]:
    """[(step name, PASS|FAIL|SKIP)] from the harness's printout ("PASS  name   detail"). The detail (window titles, sizes, anything it read) is dropped here."""
    out = []
    for block in lines:
        for ln in str(block).splitlines():
            m = re.match(r"^(PASS|FAIL|SKIP) +(\S.*?)(?: {2,}.*)?$", ln.rstrip())
            if m:
                out.append((m.group(2).strip(), m.group(1)))
    return out


def _responsiveness() -> list[float]:
    from jarvis.evals.reliability import probes
    try:
        return probes.NodeSampler().input_latency_ms(5, "chat")
    except (RuntimeError, OSError, FileNotFoundError) as e:
        raise Unavailable(type(e).__name__) from None


def _specialist_aliases() -> tuple[list[str], set[str]]:
    from jarvis.plugins._shared import specialists
    table = specialists.load()
    aliases: list[str] = []
    for kind in specialists.KINDS:
        s = table.get(kind)
        if s and s.where == "local" and s.alias and s.alias not in aliases:
            aliases.append(s.alias)
    return aliases, specialists.swap_aliases()


def _goals_problems() -> list[str]:
    from jarvis.evals.reliability import model
    return model.validate(model.load())


def _backups() -> dict:
    from jarvis.tools import state_backup as SB
    root = SB.backups_root()
    rows = SB.list_backups(root)
    if not rows:
        return {"count": 0}
    newest = rows[0]
    _m, problems = SB.verify_backup(root / newest["id"])
    return {"count": len(rows), "problems": len(problems), "created_epoch": int((_m or {}).get("created_epoch") or 0)}


def _lower_priority() -> None:
    if os.name == "nt":
        from jarvis.supervisor import jobobject as J
        J.throttle_pid(os.getpid())               # BELOW_NORMAL and EcoQoS, the same call the governor makes for every JARVIS process
    else:
        try:
            os.nice(10)
        except OSError:
            pass


def _cpu_seconds() -> float:
    t = os.times()
    return t.user + t.system + t.children_user + t.children_system


_DEFAULTS = {"idle_ms": _idle_ms, "get_json": _get_json, "post_json": _post_json, "tcp": _tcp, "doctor": _doctor, "footprint": _footprint,
             "selftest": _selftest, "responsiveness": _responsiveness, "specialist_aliases": _specialist_aliases, "goals_problems": _goals_problems,
             "backups": _backups, "lower_priority": _lower_priority, "cpu_seconds": _cpu_seconds}


# ---- the checks ----------------------------------------------------------------------------------------------------------------------------------

def check_safe_mode(w: World) -> list[Check]:
    on = safe_mode.active()
    return [Check("safe_mode", "warn" if on else "pass", note="safe mode is on: JARVIS runs on its defaults" if on else "")]


def check_doctor(w: World) -> list[Check]:
    worst: dict[str, tuple[int, int]] = {}
    rank = {"ok": 0, "skip": 0, "warn": 1, "fail": 2}
    for f in w.doctor():
        lv, n = worst.get(f.check, (0, 0))
        r = rank.get(f.level, 2)
        worst[f.check] = (max(lv, r), n + (1 if r else 0))
    return [Check(f"doctor:{name}", ("pass", "warn", "fail")[lv], value=float(n), unit="findings") for name, (lv, n) in sorted(worst.items())]


def check_footprint(w: World) -> list[Check]:
    try:
        rep = w.footprint()
    except Unavailable as e:
        return [Check("footprint", "skip", note=f"cannot be measured here ({e})")]
    out = []
    for v in rep.get("verdicts", []):
        st = {"OK": "pass", "OVER": "fail", "CHECK": "warn", "N/A": "skip"}.get(v.get("status"), "skip")
        val = v.get("value")
        out.append(Check(f"footprint:{v.get('metric')}", st, value=float(val) if isinstance(val, (int, float)) else None,
                         unit=str(v.get("unit", ""))[:30], limit=float(v["limit"]) if isinstance(v.get("limit"), (int, float)) else None,
                         worse_by_value=True, note="over the D50 budget" if st == "fail" else ""))
    return out or [Check("footprint", "skip", note="no verdicts")]


def _gateway(w: World, name: str, url: str, with_providers: bool) -> list[Check]:
    code, h = w.get_json(url + "/health", 5.0)
    if code != 200 or not isinstance(h, dict):
        return [Check(name, "fail", note="not reachable" if code == 0 else f"http {code}")]
    out = []
    why = [x for x in (h.get("degraded") or []) if isinstance(x, str) and WORD.fullmatch(x)]
    ready = h.get("ready") is not False and not why
    out.append(Check(name, "pass" if ready else "fail", note="" if ready else "degraded: " + ", ".join(why[:4]) if why else "not ready"))
    if with_providers:
        cooling = {x for x in (h.get("cooling_down") or []) if isinstance(x, str)}
        p90 = h.get("latency_p90_ms") if isinstance(h.get("latency_p90_ms"), dict) else {}
        for p in h.get("providers") or []:
            if not isinstance(p, str) or not WORD.fullmatch(p.lower()):
                continue
            tot = (p90.get(p) or {}).get("total_ms") if isinstance(p90.get(p), dict) else None
            out.append(Check(f"provider:{p}", "warn" if p in cooling else "pass", value=float(tot) if isinstance(tot, (int, float)) else None, unit="ms p90 total",
                             note="cooling down after errors" if p in cooling else ""))
    return out


def check_gateway(w: World) -> list[Check]:
    return _gateway(w, "gateway", GATEWAY, True)


def check_gateway2(w: World) -> list[Check]:
    return _gateway(w, "gateway2", GATEWAY2, False)


def check_translation(w: World) -> list[Check]:
    code, h = w.get_json(TRANSLATE + "/health", 5.0)
    return [Check("translation", "pass" if code == 200 else "fail", note="" if code == 200 else ("not reachable" if code == 0 else f"http {code}"))]


def check_tool_server(w: World) -> list[Check]:
    ok = w.tcp(*TOOL_SERVER, 3.0)
    return [Check("tool_server", "pass" if ok else "fail", note="" if ok else "not reachable")]


def check_local_models(w: World) -> list[Check]:
    code, _h = w.get_json(LOCAL_MODELS + "/health", 5.0)
    return [Check("local_models", "pass" if code == 200 else "fail", note="" if code == 200 else ("not reachable" if code == 0 else f"http {code}"))]


def check_goals(w: World) -> list[Check]:
    try:
        n = len(w.goals_problems())
    except Exception as e:  # noqa: BLE001
        return [Check("reliability_goals", "fail", note=f"cannot be read ({type(e).__name__})")]
    return [Check("reliability_goals", "pass" if n == 0 else "fail", value=float(n), unit="problems")]


def check_backup(w: World) -> list[Check]:
    try:
        b = w.backups()
    except Exception as e:  # noqa: BLE001
        return [Check("backup:latest_verifies", "skip", note=f"cannot be read ({type(e).__name__})")]
    if not b.get("count"):
        return [Check("backup:latest_verifies", "skip", note="no backup yet")]
    age = (w.now() - b["created_epoch"]) / 86400 if b.get("created_epoch") else None
    return [Check("backup:latest_verifies", "pass" if b.get("problems", 1) == 0 else "fail", value=float(b.get("problems", 0)), unit="problems"),
            Check("backup:fresh", "skip" if age is None else ("pass" if age <= BACKUP_MAX_AGE_DAYS else "warn"), value=None if age is None else round(age, 1), unit="days old",
                  limit=float(BACKUP_MAX_AGE_DAYS))]


def check_specialists(w: World) -> list[Check]:
    try:
        aliases, installed = w.specialist_aliases()
    except Exception as e:  # noqa: BLE001
        return [Check("specialists", "fail", "heavy", note=f"the table cannot be read ({type(e).__name__})")]
    out = []
    for a in aliases:
        name = f"specialist:{a}"
        if installed and a not in installed:
            out.append(Check(name, "skip", "heavy", note="not installed in llama-swap yet"))
            continue
        t0 = w.now()
        body = {"model": a, "messages": [{"role": "user", "content": FIXED_PROMPT}], "max_tokens": 4, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}
        code, ans = w.post_json(LOCAL_MODELS + "/v1/chat/completions", body, 120.0)
        ok = code == 200 and isinstance(ans, dict) and bool(ans.get("choices"))
        out.append(Check(name, "pass" if ok else "fail", "heavy", value=round(w.now() - t0, 1), unit="s", note="" if ok else ("no answer" if code == 0 else f"http {code}"),
                         worse_by_value=False))
        if code == 0:                                   # llama-swap is gone: the rest would only wait for their timeouts
            out += [Check(f"specialist:{x}", "skip", "heavy", note="llama-swap did not answer") for x in aliases[aliases.index(a) + 1:]]
            break
    return out


def check_translation_answers(w: World) -> list[Check]:
    code, ans = w.post_json(TRANSLATE + "/v1/translate", {"text": "Good morning", "src": "en", "tgt": "te"}, 60.0)
    ok = code == 200 and isinstance(ans, dict) and bool(str(ans.get("text") or "").strip())
    if code == 0:
        return [Check("translation:answers", "skip", "heavy", note="service not reachable")]
    return [Check("translation:answers", "pass" if ok else "fail", "heavy", note="" if ok else f"http {code}")]


def check_responsiveness(w: World) -> list[Check]:
    try:
        samples = w.responsiveness()
    except Unavailable as e:
        return [Check("window_responsiveness", "skip", "heavy", note=f"the face cannot be probed ({e})")]
    if len(samples) < 3:
        return [Check("window_responsiveness", "skip", "heavy", note="too few samples")]
    s = sorted(samples)
    p90 = s[min(len(s) - 1, int(round(0.9 * (len(s) - 1))))]
    return [Check("window_responsiveness", "pass" if p90 < RESPONSIVE_LIMIT_MS else "fail", "heavy", value=round(p90, 1), unit="ms p90", limit=RESPONSIVE_LIMIT_MS, worse_by_value=True)]


def check_selftest(w: World) -> list[Check]:
    try:
        rows = w.selftest()
    except Exception as e:  # noqa: BLE001
        return [Check("selftest", "fail", "heavy", note=f"the harness could not run ({type(e).__name__})")]
    st = {"PASS": "pass", "FAIL": "fail", "SKIP": "skip"}
    return [Check(f"selftest:{name}", st[s], "heavy") for name, s in rows] or [Check("selftest", "skip", "heavy", note="no steps")]


LIGHT = [check_safe_mode, check_doctor, check_footprint, check_gateway, check_gateway2, check_translation, check_tool_server, check_local_models, check_goals, check_backup]
HEAVY = [("specialists", check_specialists), ("translation_answers", check_translation_answers), ("responsiveness", check_responsiveness), ("selftest", check_selftest)]


# ---- state, comparison, alert ----------------------------------------------------------------------------------------------------------------------

def nightly_dir(env=None, windows: bool | None = None) -> Path:
    env = os.environ if env is None else env
    root = Path((env.get("JARVIS_NIGHTLY_DIR") or "").strip() or str(data_dir() / "nightly"))
    if (os.name == "nt" if windows is None else windows):
        from jarvis.supervisor.budget import is_on_d
        if not is_on_d(root):
            raise PermissionError(f"the nightly report goes on D: only (D50); {root} is not on D:")
    return root


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def load_state(folder: Path) -> dict:
    try:
        d = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("last"), dict) and isinstance(d.get("alerted"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"version": 1, "last": {}, "alerted": {}}


def _level_of(status: str, name: str) -> int:
    return Check(name, status).level()


def decide_alert(checks: list[Check], state: dict) -> tuple[list[str], list[str]]:
    """(names to tell the owner about, the subset that got worse). Pure: it reads `state` and changes nothing.
    New: it was fine (or never seen) at its last known run. Repeat: it was already bad; told again only if worse than what the owner was told."""
    new, worse = [], []
    for c in checks:
        if c.status == "skip":
            continue
        lv = c.level()
        if lv == 0:
            continue
        prev = state["last"].get(c.name)
        prev_bad = bool(prev) and _level_of(prev.get("status", "pass"), c.name) > 0
        told = state["alerted"].get(c.name)
        if not prev_bad or told is None:
            new.append(c.name)
            continue
        got_worse = lv > int(told.get("level", 0)) or (c.worse_by_value and isinstance(c.value, (int, float)) and isinstance(told.get("value"), (int, float))
                                                       and c.value > told["value"] * WORSE_FACTOR + 1e-9)
        if got_worse:
            new.append(c.name)
            worse.append(c.name)
    return new, worse


def deltas(checks: list[Check], state: dict) -> list[dict]:
    out = []
    for c in checks:
        if c.status == "skip":
            continue
        prev = state["last"].get(c.name)
        if prev is None:
            out.append({"name": c.name, "was": None, "now": c.status, "value_was": None, "value_now": c.value})
        elif prev.get("status") != c.status or (isinstance(c.value, (int, float)) and isinstance(prev.get("value"), (int, float)) and c.value != prev["value"] and c.worse_by_value):
            out.append({"name": c.name, "was": prev.get("status"), "now": c.status, "value_was": prev.get("value"), "value_now": c.value})
    return out


def apply_state(checks: list[Check], state: dict, told: list[str], date: str) -> dict:
    for c in checks:
        if c.status == "skip":
            continue
        state["last"][c.name] = {"status": c.status, "value": c.value, "date": date}
        if c.level() == 0:
            state["alerted"].pop(c.name, None)
        elif c.name in told:
            state["alerted"][c.name] = {"level": c.level(), "value": c.value, "date": date}
    state["last_date"] = date
    return state


def make_notice(date: str, names: list[str], worse: list[str], report_path: Path) -> dict:
    shown = ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else "")
    sig = hashlib.sha256(("|".join(sorted(names)) + "#" + "|".join(sorted(worse))).encode()).hexdigest()[:10]
    return {"kind": "self_test", "watch": "nightly self-test", "key": f"nightly-{date}-{sig}",
            "summary": f"Nightly self-test: {len(names)} check(s) need a look: {shown}" + (f" ({len(worse)} got worse)" if worse else ""),
            "path": str(report_path)}


# ---- the run -------------------------------------------------------------------------------------------------------------------------------

@dataclass
class Options:
    min_idle_s: float = 120.0
    heavy_idle_s: float = 600.0
    wait_idle_min: float = 0.0
    max_min: float = 20.0
    heavy: bool = True
    models: bool = True
    force: bool = False
    assume_idle: bool = False
    gap_s: float = 1.0                         # a breath between two checks


def run_nightly(opts: Options, w: World, folder: Path, notifier: Any = None, log: Callable[[str], None] = lambda s: None) -> dict:
    t_start = w.now()
    date = time.strftime("%Y-%m-%d", time.localtime(t_start))
    report_path = folder / f"nightly-{date}.json"
    try:
        today = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        today = None
    if today and today.get("status") == "done" and not opts.force:
        return dict(today, already_done=True)
    state = load_state(folder)
    w.lower_priority()
    cpu0, wall0 = w.cpu_seconds(), time.monotonic()

    def idle_s() -> float | None:
        if opts.assume_idle:
            return 1e9
        v = w.idle_ms()
        return None if v is None else v / 1000.0

    # wait for the owner to be still (the Task Scheduler may also be told to run only when idle; this is the second guard)
    deadline = t_start + opts.wait_idle_min * 60
    idle = idle_s()
    while idle is not None and idle < opts.min_idle_s and w.now() < deadline:
        w.sleep(30)
        idle = idle_s()
    report = {"schema": SCHEMA, "date": date, "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t_start)), "safe_mode": safe_mode.active(),
              "owner_idle_s": None if idle is None or opts.assume_idle else round(min(idle, 1e6), 1),
              "assumed_idle": bool(opts.assume_idle), "status": "done", "checks": [], "deltas": [], "alert": {"sent": False, "names": [], "worse": []}}
    if idle is not None and idle < opts.min_idle_s:
        report.update(status="skipped_owner_active", note="the owner was active; nothing was run")
        _atomic_json(report_path, report)
        return report
    heavy_ok = opts.heavy and (idle is not None and idle >= opts.heavy_idle_s)
    if opts.heavy and not heavy_ok:
        report["heavy_skipped"] = "owner idle time unknown" if idle is None else "the owner was not still long enough"
    checks: list[Check] = []
    t_box = t_start + opts.max_min * 60
    aborted = ""

    def still_ok() -> bool:
        nonlocal aborted
        if w.now() > t_box:
            aborted = "time box used up"
        else:
            cur = idle_s()
            if cur is not None and cur < opts.min_idle_s:
                aborted = "the owner came back"
        return not aborted

    def run_one(fn: Callable[[World], list[Check]], label: str, group: str) -> None:
        if not still_ok():
            checks.append(Check(label, "skip", group, note=aborted))
            return
        try:
            got = fn(w)
        except Exception as e:  # noqa: BLE001 - one broken check never hides the others
            got = [Check(label, "fail", group, note=f"check crashed ({type(e).__name__})")]
        checks.extend(got)
        log(f"{label}: " + ", ".join(f"{c.name}={c.status}" for c in got)[:200])
        w.sleep(opts.gap_s)

    for fn in LIGHT:
        run_one(fn, fn.__name__.replace("check_", ""), "light")
    over_cpu = any(c.name == "footprint:cpu" and c.status == "fail" for c in checks)
    if heavy_ok:
        for label, fn in HEAVY:
            if label == "specialists" and not opts.models:
                continue
            if over_cpu:
                checks.append(Check(label, "skip", "heavy", note="JARVIS is already over its CPU share"))
            elif label == "specialists" and any(c.name == "local_models" and c.status == "fail" for c in checks):
                checks.append(Check(label, "skip", "heavy", note="llama-swap is not up"))
            else:
                run_one(fn, label, "heavy")
    # compare, decide, tell
    names, worse = decide_alert(checks, state)
    report["checks"] = [asdict(c) for c in checks]
    report["deltas"] = deltas(checks, state)
    cnt = {k: sum(1 for c in checks if c.status == k) for k in ("pass", "warn", "fail", "skip")}
    report["counts"] = cnt
    if aborted:
        report["stopped"] = aborted
    wall = max(time.monotonic() - wall0, 1e-6)
    cpu = max(w.cpu_seconds() - cpu0, 0.0)
    report["resources"] = {"seconds": round(wall, 1), "cpu_seconds": round(cpu, 2), "cpu_pct_of_machine": round(100.0 * cpu / (wall * (os.cpu_count() or 1)), 2)}
    if names:
        notice = make_notice(date, names, worse, report_path)
        if notifier is not None:
            notifier.deliver([notice])
            report["alert"] = {"sent": True, "names": names, "worse": worse}
        else:
            report["alert"] = {"sent": False, "names": names, "worse": worse, "note": "no inbox given"}
    state = apply_state(checks, state, names, date)
    _atomic_json(report_path, report)
    _atomic_json(folder / "latest.json", report)
    _atomic_json(folder / "state.json", state)
    prune_reports(folder)
    return report


def prune_reports(folder: Path, keep: int = KEEP_REPORTS) -> None:
    files = sorted(p for p in folder.glob("nightly-????-??-??.json"))
    for old in files[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass


def render(rep: dict) -> str:
    lines = [f"JARVIS nightly self-test {rep['date']}   status {rep['status']}   safe mode {'ON' if rep.get('safe_mode') else 'off'}"
             + (f"   owner idle {rep['owner_idle_s']:.0f} s" if rep.get("owner_idle_s") is not None else "")]
    for c in rep.get("checks", []):
        val = "" if c.get("value") is None else f" {c['value']}" + (f" {c['unit']}" if c.get("unit") else "") + (f" / {c['limit']}" if c.get("limit") is not None else "")
        lines.append(f"  {c['status'].upper():5} {c['name']:<34}{val}" + (f"   ({c['note']})" if c.get("note") else ""))
    for d in rep.get("deltas", []):
        if d["was"] is not None:                                  # the first night everything is new; only a real change is worth a line
            lines.append(f"  changed: {d['name']}  {d['was']} -> {d['now']}")
    if rep.get("counts"):
        lines.append("  " + ", ".join(f"{n} {k}" for k, n in rep["counts"].items()))
    if rep.get("alert", {}).get("names"):
        lines.append("  told the owner about: " + ", ".join(rep["alert"]["names"]) + ("" if rep["alert"]["sent"] else " (no inbox given: not sent)"))
    if rep.get("heavy_skipped"):
        lines.append("  heavy checks skipped: " + rep["heavy_skipped"])
    if rep.get("stopped"):
        lines.append("  stopped early: " + rep["stopped"])
    return "\n".join(lines)


def main(argv=None, world: World | None = None, out: Callable[[str], None] = print) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inbox", default="", help="the inbox file of the page-watch runner (default <data>\\muse\\inbox.jsonl)")
    ap.add_argument("--push-topic-file", default="", help="the ntfy topic file (default: the secrets folder's ntfy.topic if it exists); read only by the existing push code")
    ap.add_argument("--min-idle-s", type=float, default=120.0)
    ap.add_argument("--heavy-idle-s", type=float, default=600.0)
    ap.add_argument("--wait-idle-min", type=float, default=0.0, help="wait this long for the owner to be still before giving up")
    ap.add_argument("--max-min", type=float, default=20.0, help="time box in minutes")
    ap.add_argument("--no-heavy", action="store_true", help="only the light checks")
    ap.add_argument("--no-models", action="store_true", help="do not ask the specialist models")
    ap.add_argument("--force", action="store_true", help="run again even if tonight's report is already done")
    ap.add_argument("--assume-idle", action="store_true", help="treat the owner as away (tests, or a PC nobody is using)")
    ap.add_argument("--print", action="store_true", help="print the report")
    args = ap.parse_args(argv)
    try:
        folder = nightly_dir()
    except PermissionError as e:
        out("refused: " + str(e))
        return 2
    from jarvis.muse.notify import Inbox, Notifier, NtfyPush
    inbox = Path(args.inbox) if args.inbox else data_dir() / "muse" / "inbox.jsonl"
    topic = Path(args.push_topic_file) if args.push_topic_file else None
    if topic is None:
        from jarvis.tools.doctor import default_secrets_dir
        cand = default_secrets_dir() / "ntfy.topic"
        topic = cand if cand.exists() else None                        # existence only: the file is read by NtfyPush when a ping goes out
    notifier = Notifier(Inbox(inbox), NtfyPush(topic) if topic else None)
    opts = Options(min_idle_s=args.min_idle_s, heavy_idle_s=args.heavy_idle_s, wait_idle_min=args.wait_idle_min, max_min=args.max_min, heavy=not args.no_heavy,
                   models=not args.no_models, force=args.force, assume_idle=args.assume_idle)
    rep = run_nightly(opts, world or World(), folder, notifier, log=lambda s: None)
    if args.print or rep.get("already_done"):
        out(("already done tonight (use --force to run again)\n" if rep.get("already_done") else "") + render(rep))
    for e in notifier.push_errors:
        out("push: " + e)
    if rep["status"] != "done":
        return 3
    return 1 if rep.get("counts", {}).get("fail") else 0


if __name__ == "__main__":
    sys.exit(main())
