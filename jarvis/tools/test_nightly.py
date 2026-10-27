"""D71: the nightly self-test, with every service, the doctor, the meter and the harness replaced by fakes. What is under test is the part that is ours: when it runs,
what it writes (only names, statuses and numbers), what it compares, and the rule for the one quiet notice (a regression is told once; the same thing is not told
again the next night unless it got worse)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from jarvis.muse.notify import Inbox, Notifier, NtfyPush  # noqa: E402
from jarvis.tools import doctor as D  # noqa: E402
from jarvis.tools import nightly as N  # noqa: E402

DAY = 86400
T0 = time.mktime((2026, 10, 27, 3, 0, 0, 0, 0, -1))
OWNER_TEXT = "OWNERS-PRIVATE-SENTENCE about Priya's salary"


class Rig:
    """A fake PC. `state` is what the services say tonight; change it between nights."""

    def __init__(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.tmp = tmp_path
        self.folder = tmp_path / "nightly"
        self.clock = {"t": T0}
        self.idle = 3_600_000                                   # an hour of stillness, in ms
        self.calls: list[str] = []
        self.pushes: list[tuple[str, bytes]] = []
        self.gateway = {"ok": True, "ready": True, "degraded": [], "providers": ["dahl", "groq", "local"], "cooling_down": [],
                        "latency_p90_ms": {"dahl": {"total_ms": 900}}, "note": OWNER_TEXT}
        self.http = {"gateway2": 200, "translation": 200, "local_models": 200, "answers": 200, "chat": 200}
        self.doctor_findings = [D.Finding("listeners", "ok", f"detail with {OWNER_TEXT}"), D.Finding("secrets", "skip", "x"), D.Finding("docs", "warn", "FILE_MAP stale")]
        self.footprint_rows = {"cpu": ("OK", 4.0), "ram": ("OK", 2000.0), "vram": ("OK", 300.0), "disk_write": ("OK", 0.2), "outside_d": ("OK", 0.0)}
        self.tool_server = True
        self.selftest_rows = [("files", "PASS"), ("window list", "PASS"), ("notepad open, read by UI Automation, close", "PASS"), ("tool server (127.0.0.1:48970) answers", "PASS")]
        self.latency = [20.0, 25.0, 22.0, 30.0, 28.0]
        self.backup = {"count": 1, "problems": 0, "created_epoch": int(T0) - DAY}
        self.goal_problems: list[str] = []
        self.aliases = (["decider", "vision"], {"decider", "vision"})
        self.crash: str | None = None
        self.priority_calls = 0
        self.idle_sequence: list[int] | None = None
        self.topic = tmp_path / "ntfy.topic"
        self.topic.write_text("https://ntfy.invalid/abc123\n", encoding="utf-8")
        self.inbox_path = tmp_path / "muse" / "inbox.jsonl"

    # ---- the fakes
    def now(self):
        return self.clock["t"]

    def sleep(self, s):
        self.clock["t"] += s

    def idle_ms(self):
        if self.idle_sequence:
            return self.idle_sequence.pop(0) if len(self.idle_sequence) > 1 else self.idle_sequence[0]
        return self.idle

    def get_json(self, url, timeout):
        self.calls.append("GET " + url)
        if url.startswith(N.GATEWAY + "/health"):
            return 200, dict(self.gateway)
        if url.startswith(N.GATEWAY2):
            return self.http["gateway2"], {"ok": True, "ready": True, "providers": ["second"]} if self.http["gateway2"] == 200 else None
        if url.startswith(N.TRANSLATE):
            return self.http["translation"], {"ready": True}
        if url.startswith(N.LOCAL_MODELS):
            return self.http["local_models"], {}
        return 0, None

    def post_json(self, url, body, timeout):
        self.calls.append("POST " + url)
        if "translate" in url:
            return self.http["answers"], {"text": "శుభోదయం", "model": "indictrans2"} if self.http["answers"] == 200 else None
        return self.http["chat"], {"choices": [{"message": {"content": OWNER_TEXT}}]} if self.http["chat"] == 200 else None

    def tcp(self, host, port, timeout):
        return self.tool_server

    def doctor(self):
        self.calls.append("doctor")
        return list(self.doctor_findings)

    def footprint(self):
        self.calls.append("footprint")
        return {"verdicts": [{"metric": m, "status": st, "value": v, "limit": 20 if m == "cpu" else 1000, "unit": "u"} for m, (st, v) in self.footprint_rows.items()]}

    def selftest(self):
        self.calls.append("selftest")
        if self.crash == "selftest":
            raise RuntimeError(OWNER_TEXT)
        return list(self.selftest_rows)

    def responsiveness(self):
        self.calls.append("responsiveness")
        return list(self.latency)

    def world(self):
        return N.World(now=self.now, sleep=self.sleep, idle_ms=self.idle_ms, get_json=self.get_json, post_json=self.post_json, tcp=self.tcp, doctor=self.doctor,
                       footprint=self.footprint, selftest=self.selftest, responsiveness=self.responsiveness, specialist_aliases=lambda: self.aliases,
                       goals_problems=lambda: self.goal_problems, backups=lambda: dict(self.backup),
                       lower_priority=lambda: (self.calls.append("lower_priority"), setattr(self, "priority_calls", self.priority_calls + 1)),
                       cpu_seconds=lambda: 0.0)

    def notifier(self):
        def transport(url, body, headers):
            self.pushes.append((url, body))
            return 200
        return Notifier(Inbox(self.inbox_path), NtfyPush(self.topic, transport=transport))

    def night(self, **opts):
        o = N.Options(gap_s=0.0, **opts)
        return N.run_nightly(o, self.world(), self.folder, self.notifier())

    def next_day(self):
        self.clock["t"] += DAY

    def notices(self):
        return [r for r in Inbox(self.inbox_path)._rows() if "notice" in r]


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def status_of(rep, name):
    return next(c["status"] for c in rep["checks"] if c["name"] == name)


# ------------------------------------------------------------------------------------------------------------------------------ one night
def test_a_healthy_night_writes_one_report_and_says_nothing(rig):
    rep = rig.night()
    assert rep["status"] == "done" and rep["counts"]["fail"] == 0 and rep["alert"]["sent"] is False
    names = {c["name"] for c in rep["checks"]}
    assert {"doctor:listeners", "doctor:docs", "footprint:cpu", "footprint:outside_d", "gateway", "gateway2", "provider:dahl", "provider:groq", "translation", "tool_server",
            "local_models", "reliability_goals", "backup:latest_verifies", "backup:fresh", "specialist:decider", "specialist:vision", "translation:answers",
            "window_responsiveness", "selftest:files", "selftest:notepad open, read by UI Automation, close", "safe_mode"} <= names
    assert status_of(rep, "doctor:docs") == "warn" and status_of(rep, "window_responsiveness") == "pass"
    assert (rig.folder / f"nightly-{time.strftime('%Y-%m-%d', time.localtime(T0))}.json").exists() and (rig.folder / "latest.json").exists()
    assert rig.notices() == [] and rig.pushes == []
    assert sorted(p.name for p in rig.folder.iterdir()) == sorted([f"nightly-{time.strftime('%Y-%m-%d', time.localtime(T0))}.json", "latest.json", "state.json"])


def test_the_report_holds_names_statuses_and_numbers_never_text(rig):
    rig.gateway["degraded"] = ["pii_model_missing", OWNER_TEXT]                      # a service answering with free text: only fixed words are kept
    rig.gateway["providers"] = ["dahl", OWNER_TEXT]
    rig.crash = "selftest"
    rep = rig.night()
    raw = (rig.folder / "latest.json").read_text(encoding="utf-8") + (rig.folder / "state.json").read_text(encoding="utf-8")
    raw += "".join(json.dumps(r) for r in rig.notices())
    assert "OWNERS-PRIVATE" not in raw and "Priya" not in raw and "salary" not in raw
    assert "FILE_MAP" not in raw, "the doctor's findings are summarised by area, their sentences are not copied"
    assert "pii_model_missing" in raw
    assert status_of(rep, "selftest") == "fail" and [c["note"] for c in rep["checks"] if c["name"] == "selftest"] == ["the harness could not run (RuntimeError)"]
    for c in rep["checks"]:
        assert set(c) == {"name", "status", "group", "value", "unit", "limit", "note", "worse_by_value"}
        assert c["status"] in ("pass", "warn", "fail", "skip") and (c["value"] is None or isinstance(c["value"], (int, float)))


def test_it_lowers_its_own_priority_before_anything_else_and_gives_a_breath_between_checks(rig):
    rig.night()
    assert rig.calls[0] == "lower_priority" and rig.priority_calls == 1
    slept = []
    w = rig.world()
    w.sleep = lambda s: slept.append(s)
    rig.next_day()
    N.run_nightly(N.Options(gap_s=1.0), w, rig.folder, rig.notifier(), log=lambda s: None)
    assert len(slept) >= 10 and set(slept) == {1.0}


def test_a_check_that_crashes_is_a_failed_check_and_the_others_still_run(rig):
    def boom():
        raise ValueError(OWNER_TEXT)
    w = rig.world()
    w.doctor = boom
    rep = N.run_nightly(N.Options(gap_s=0.0), w, rig.folder, None)
    assert status_of(rep, "doctor") == "fail" and [c["note"] for c in rep["checks"] if c["name"] == "doctor"] == ["check crashed (ValueError)"]
    assert status_of(rep, "gateway") == "pass" and status_of(rep, "selftest:files") == "pass"
    assert OWNER_TEXT not in (rig.folder / "latest.json").read_text(encoding="utf-8")


def test_already_done_tonight_is_not_run_again_unless_forced(rig):
    rig.night()
    n = len(rig.calls)
    again = rig.night()
    assert again.get("already_done") is True and len(rig.calls) == n
    assert rig.night(force=True).get("already_done") is None and len(rig.calls) > n


# ------------------------------------------------------------------------------------------------------------------------------ the owner
def test_it_does_not_run_while_the_owner_is_active_and_runs_when_asked_again(rig):
    rig.idle = 10_000
    rep = rig.night()
    assert rep["status"] == "skipped_owner_active" and rep["checks"] == [] and "doctor" not in rig.calls and rig.notices() == []
    rig.idle = 3_600_000
    assert rig.night()["status"] == "done", "a skipped night is not a done night: the next try runs"


def test_it_can_wait_for_the_owner_to_be_still(rig):
    rig.idle_sequence = [5_000, 8_000, 400_000]
    rep = rig.night(wait_idle_min=10)
    assert rep["status"] == "done" and rig.clock["t"] >= T0 + 60
    rig2 = Rig(rig.tmp / "b")
    rig2.idle = 1_000
    rep2 = rig2.night(wait_idle_min=2)
    assert rep2["status"] == "skipped_owner_active" and rig2.clock["t"] <= T0 + 150, "it gives up at its limit"


def test_heavy_checks_need_a_longer_stillness_or_a_known_idle_time(rig):
    rig.idle = 300_000                                        # still for 5 minutes: enough for the light checks, not for models and windows
    rep = rig.night()
    assert status_of(rep, "gateway") == "pass" and "selftest" not in rig.calls and "responsiveness" not in rig.calls
    assert not any(c["group"] == "heavy" for c in rep["checks"]) and "not still long enough" in rep["heavy_skipped"]
    rig.next_day()
    rig.idle = None                                           # unknown (not Windows, or the reader failed)
    rep = rig.night()
    assert "selftest" not in rig.calls and rep["owner_idle_s"] is None and "unknown" in rep["heavy_skipped"]
    rig.next_day()
    rep = rig.night(heavy=False, assume_idle=True)
    assert "selftest" not in rig.calls and "heavy_skipped" not in rep


def test_it_stops_when_the_owner_comes_back_in_the_middle(rig):
    rig.idle_sequence = [3_600_000] * 14 + [5_000]            # still for the light checks and the first heavy ones, then he types
    rep = rig.night()
    assert rep["stopped"] == "the owner came back"
    skipped = [c for c in rep["checks"] if c["status"] == "skip" and c["note"] == "the owner came back"]
    assert skipped and "selftest" not in rig.calls


def test_it_stops_at_its_time_box(rig):
    w = rig.world()
    ticks = iter(range(100000))
    w.now = lambda: T0 + next(ticks) * 90.0                   # every look at the clock is a minute and a half later
    rep = N.run_nightly(N.Options(gap_s=0.0, max_min=5), w, rig.folder, None)
    assert rep["stopped"] == "time box used up" and any(c["note"] == "time box used up" for c in rep["checks"])


def test_nothing_heavy_runs_when_jarvis_is_already_over_its_cpu_share(rig):
    rig.footprint_rows["cpu"] = ("OVER", 31.0)
    rep = rig.night()
    assert "selftest" not in rig.calls and "responsiveness" not in rig.calls
    assert [c["note"] for c in rep["checks"] if c["name"] == "selftest"] == ["JARVIS is already over its CPU share"]


def test_the_models_are_asked_only_a_fixed_word_and_only_when_llama_swap_is_up(rig):
    bodies = []
    w = rig.world()
    w.post_json = lambda url, body, t: (bodies.append(body), (200, {"choices": [{}]}))[1]
    N.run_nightly(N.Options(gap_s=0.0), w, rig.folder, None)
    llm = [b for b in bodies if "messages" in b]
    assert [b["model"] for b in llm] == ["decider", "vision"] and all(b["messages"] == [{"role": "user", "content": N.FIXED_PROMPT}] and b["max_tokens"] == 4 for b in llm)
    rig2 = Rig(rig.tmp / "c")
    rig2.http["local_models"] = 0
    rep = rig2.night()
    assert status_of(rep, "local_models") == "fail" and [c["note"] for c in rep["checks"] if c["name"] == "specialists"] == ["llama-swap is not up"]
    rig3 = Rig(rig.tmp / "d")
    rig3.aliases = (["decider", "not-yet"], {"decider"})
    assert status_of(rig3.night(), "specialist:not-yet") == "skip"
    rig4 = Rig(rig.tmp / "e")
    rig4.night(models=False)
    assert not any(c.startswith("POST") and "chat" in c for c in rig4.calls)


# ------------------------------------------------------------------------------------------------------------------------------ the one quiet notice
def test_a_regression_is_one_notice_with_names_and_a_push_with_no_content(rig):
    rig.night()
    rig.next_day()
    rig.http["gateway2"] = 503
    rig.tool_server = False
    rep = rig.night()
    assert rep["alert"]["sent"] is True and sorted(rep["alert"]["names"]) == ["gateway2", "tool_server"] and rep["alert"]["worse"] == []
    n = rig.notices()
    assert len(n) == 1 and n[0]["notice"]["kind"] == "self_test" and "gateway2" in n[0]["notice"]["summary"] and "tool_server" in n[0]["notice"]["summary"]
    assert len(rig.pushes) == 1 and rig.pushes[0][1].decode() == "JARVIS: 1 new notice. Open JARVIS to read it.", "the phone is told that something waits, never what"
    assert any(d["name"] == "gateway2" and d["was"] == "pass" and d["now"] == "fail" for d in rep["deltas"])


def test_the_same_alert_is_not_repeated_the_next_night(rig):
    rig.night()
    rig.next_day()
    rig.http["gateway2"] = 503
    rig.night()
    for _ in range(3):
        rig.next_day()
        rep = rig.night()
        assert status_of(rep, "gateway2") == "fail" and rep["alert"]["names"] == [] and rep["alert"]["sent"] is False
    assert len(rig.notices()) == 1 and len(rig.pushes) == 1


def test_it_is_told_again_when_it_got_worse(rig):
    rig.footprint_rows["cpu"] = ("OVER", 25.0)
    first = rig.night()
    assert first["alert"]["names"] == ["footprint:cpu"], "over budget on the very first night is a regression too"
    rig.next_day()
    rig.footprint_rows["cpu"] = ("OVER", 26.0)                # 4% more: the same
    assert rig.night()["alert"]["names"] == []
    rig.next_day()
    rig.footprint_rows["cpu"] = ("OVER", 31.0)                # more than 10% above the 25 the owner was told
    rep = rig.night()
    assert rep["alert"]["names"] == ["footprint:cpu"] and rep["alert"]["worse"] == ["footprint:cpu"]
    rig.next_day()
    assert rig.night()["alert"]["names"] == [], "told 31, still 31"
    assert len(rig.notices()) == 2 and len(rig.pushes) == 2


def test_a_provider_that_degrades_is_told_once_and_again_only_if_it_gets_worse(rig):
    rig.night()
    rig.next_day()
    rig.gateway["cooling_down"] = ["groq"]
    rep = rig.night()
    assert status_of(rep, "provider:groq") == "warn" and rep["alert"]["names"] == ["provider:groq"]
    rig.next_day()
    assert rig.night()["alert"]["names"] == []
    rig.next_day()
    rig.gateway.update(ok=True, ready=False, degraded=["pii_model_missing"])        # now the whole gateway fails too: that is new, groq is unchanged
    rep = rig.night()
    assert rep["alert"]["names"] == ["gateway"]


def test_a_warning_that_is_not_a_provider_is_never_told(rig):
    rig.night()
    rig.next_day()
    rig.doctor_findings.append(D.Finding("listeners", "warn", "3 other ports"))
    rig.backup = {"count": 1, "problems": 0, "created_epoch": int(rig.clock["t"]) - 30 * DAY}
    rep = rig.night()
    assert status_of(rep, "doctor:listeners") == "warn" and status_of(rep, "backup:fresh") == "warn" and rep["alert"]["names"] == [] and rig.notices() == []


def test_a_problem_that_goes_away_and_comes_back_is_a_new_regression(rig):
    rig.night()
    rig.next_day()
    rig.http["translation"] = 0
    assert rig.night()["alert"]["names"] == ["translation"]
    rig.next_day()
    rig.http["translation"] = 200
    rep = rig.night()
    assert rep["alert"]["names"] == [] and status_of(rep, "translation") == "pass" and rig.notices().__len__() == 1, "recovery is quiet"
    rig.next_day()
    rig.http["translation"] = 0
    assert rig.night()["alert"]["names"] == ["translation"] and len(rig.notices()) == 2


def test_a_skipped_check_neither_raises_nor_clears_an_alert(rig):
    rig.night()
    rig.next_day()
    rig.latency = [300.0, 280.0, 350.0, 290.0, 310.0]
    assert rig.night()["alert"]["names"] == ["window_responsiveness"]
    rig.next_day()
    rig.idle = 300_000                                        # tonight the heavy checks do not run
    rep = rig.night()
    assert "window_responsiveness" not in {c["name"] for c in rep["checks"]}
    rig.next_day()
    rig.idle = 3_600_000
    assert rig.night()["alert"]["names"] == [], "still failing, same number: not told again"
    assert len(rig.notices()) == 1


def test_a_failing_selftest_step_is_named_in_the_notice(rig):
    rig.night()
    rig.next_day()
    rig.selftest_rows = [("files", "PASS"), ("notepad open, read by UI Automation, close", "FAIL"), ("window list", "SKIP")]
    rep = rig.night()
    assert rep["alert"]["names"] == ["selftest:notepad open, read by UI Automation, close"]
    assert status_of(rep, "selftest:window list") == "skip"


def test_the_same_nights_notice_is_filed_once_even_if_the_run_is_repeated(rig):
    rig.http["translation"] = 0
    rig.night()
    again = rig.night(force=True)                             # same date, same names: the inbox's own key drops the duplicate; state says it was told already
    assert again["alert"]["names"] == [] and len(rig.notices()) == 1 and len(rig.pushes) == 1


def test_the_inbox_is_the_watch_runners_and_nothing_is_sent_without_one(rig):
    rig.http["translation"] = 0
    rep = N.run_nightly(N.Options(gap_s=0.0), rig.world(), rig.folder, None)
    assert rep["alert"]["sent"] is False and rep["alert"]["names"] == ["translation"] and rig.pushes == []


def test_old_reports_are_pruned_and_a_damaged_state_file_is_ignored(rig):
    for i in range(N.KEEP_REPORTS + 5):
        rig.night(force=True)
        rig.next_day()
    assert len(list(rig.folder.glob("nightly-*.json"))) == N.KEEP_REPORTS
    (rig.folder / "state.json").write_text("{broken", encoding="utf-8")
    assert N.load_state(rig.folder) == {"version": 1, "last": {}, "alerted": {}}
    assert rig.night()["status"] == "done"


# ------------------------------------------------------------------------------------------------------------------------------ the rest
def test_safe_mode_is_shown_in_the_report_and_never_alerts(rig, monkeypatch):
    monkeypatch.setenv("JARVIS_SAFE_MODE", "1")
    rep = rig.night()
    assert rep["safe_mode"] is True and status_of(rep, "safe_mode") == "warn" and rep["alert"]["names"] == []


def test_the_report_goes_on_d_only_on_windows():
    with pytest.raises(PermissionError):
        N.nightly_dir({"JARVIS_NIGHTLY_DIR": r"C:\Users\me\nightly"}, windows=True)
    assert N.nightly_dir({"JARVIS_NIGHTLY_DIR": r"D:\jarvis\data\nightly"}, windows=True).as_posix().endswith("nightly")


def test_the_selftest_printout_is_read_without_its_details():
    lines = ["PASS  files                                          D:\\jarvis\\data\\reliability\\work\\selftest-abc",
             "FAIL  notepad open, read by UI Automation, close      AssertionError: no new Notepad window after 3 s",
             "SKIP  window list                                     not a Windows environment", "", "SELFTEST: FIX FIRST: notepad"]
    assert N.parse_selftest(["\n".join(lines)]) == [("files", "PASS"), ("notepad open, read by UI Automation, close", "FAIL"), ("window list", "SKIP")]


def test_command_line_exit_codes_and_output(rig, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_NIGHTLY_DIR", str(rig.folder))
    monkeypatch.setenv("JARVIS_DATA_DIR", str(rig.tmp / "data"))
    out = []
    args = ["--inbox", str(rig.inbox_path), "--print"]                      # no topic file: nothing may leave this machine in a test
    rig.idle = 5_000
    assert N.main(args, world=rig.world(), out=out.append) == 3
    rig.idle = 3_600_000
    assert N.main(args + ["--force"], world=rig.world(), out=out.append) == 0 and "status done" in out[-1]
    rig.http["translation"] = 0
    rig.next_day()
    assert N.main(args, world=rig.world(), out=out.append) == 1 and "FAIL  translation" in out[-1] and "told the owner about: translation" in out[-1]
    assert N.main(args, world=rig.world(), out=out.append) == 1 and out[-1].startswith("already done tonight")


def test_the_nightly_imports_nothing_that_acts_for_the_owner():
    """It reads the PC and asks local models a fixed word. Goals, approvals, the kernel and the desktop plugin are not importable from it, and it never builds a goal run."""
    import ast
    tree = ast.parse((ROOT / "jarvis" / "tools" / "nightly.py").read_text(encoding="utf-8"))
    mods, names, node_names = set(), set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mods.add(n.module or "")
            names |= {a.name for a in n.names}
            if n.module == "jarvis_node":
                node_names |= {a.name for a in n.names}
        elif isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
    assert not [m for m in mods if m.startswith(("jarvis.plugins.desktop", "jarvis_kernel", "jarvis.kernel", "jarvis.bridge"))], mods
    assert node_names == {"windows"}, "from the node it takes only the reader of the owner's idle time (GetLastInputInfo)"
    assert "Runner" not in names and "FaceDriver" not in names
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "") for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not called & {"run_one_goal", "approve", "jarvis_do", "type_text", "click", "send_keys"}, called
