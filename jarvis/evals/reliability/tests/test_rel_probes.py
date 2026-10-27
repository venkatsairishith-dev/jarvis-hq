"""Responsiveness probes: thresholds, loads that must really be running, and the Node sampler's plumbing (fakes only)."""
import json
import subprocess

from jarvis.evals.reliability import model, probes

CAT = model.load()
WR = {g.id: g for g in CAT.capabilities["window_responsiveness"].goals}


class Sampler:
    def __init__(self, samples=(40, 50, 60, 70, 80), startup=None, raises=None):
        self.samples, self._startup, self.raises, self.calls = list(samples), startup or {"interactive_s": 2.0, "input_ignored_s": 0.4}, raises, []

    def input_latency_ms(self, n, page):
        self.calls.append(("input", n, page))
        if self.raises:
            raise self.raises
        return self.samples

    def click_to_paint_ms(self, n, page):
        self.calls.append(("click", n, page))
        return self.samples

    def startup(self):
        self.calls.append(("startup",))
        return self._startup


class FakeLoad(probes.Load):
    def __init__(self, ready=True, alive_at_end=True):
        self.name, self.ready, self.alive_flag, self.started, self.stopped = "local_model", ready, alive_at_end, False, False

    def start(self):
        self.started = True

    def wait_ready(self, timeout):
        return self.ready

    def alive(self):
        return self.alive_flag

    def stop(self):
        self.stopped = True


def patch_load(monkeypatch, load):
    monkeypatch.setattr(probes, "make_load", lambda kind, driver=None, opener=None: load if kind != "idle" else probes.Load())


def test_idle_input_latency_under_150ms_p90_passes():
    r = probes.run_probe(WR["wr-input-idle"], Sampler([40, 50, 60, 70, 80]), sleep=lambda s: None)
    assert r.ok and r.unit == "ms" and r.limit == 150 and r.value <= 80 and not r.invalid and "p90" in r.detail


def test_one_slow_keystroke_in_ten_is_the_p90_boundary():
    fast = [50] * 9 + [400]
    r = probes.run_probe(WR["wr-input-idle"], Sampler(fast), sleep=lambda s: None)
    assert r.ok and r.value == 85.0                                      # one outlier in ten moves the p90 only a tenth of the way to it
    slow = [50] * 8 + [400, 500]
    assert probes.run_probe(WR["wr-input-idle"], Sampler(slow), sleep=lambda s: None).ok is False


def test_a_load_that_ended_before_the_samples_were_taken_makes_the_repeat_invalid(monkeypatch):
    load = FakeLoad(alive_at_end=False)
    patch_load(monkeypatch, load)
    r = probes.run_probe(WR["wr-input-local-model"], Sampler(), sleep=lambda s: None)
    assert r.invalid == "the load ended before the samples were taken" and not r.ok and load.started and load.stopped


def test_a_load_that_never_produced_a_token_is_invalid(monkeypatch):
    load = FakeLoad(ready=False)
    patch_load(monkeypatch, load)
    r = probes.run_probe(WR["wr-input-local-model"], Sampler(), sleep=lambda s: None)
    assert "never started producing" in r.invalid and load.stopped


def test_with_the_load_running_the_samples_count(monkeypatch):
    load = FakeLoad()
    patch_load(monkeypatch, load)
    s = Sampler([100, 110, 120])
    r = probes.run_probe(WR["wr-input-local-model"], s, sleep=lambda s: None)
    assert r.ok and not r.invalid and s.calls == [("input", 20, "chat")] and load.stopped


def test_click_to_paint_uses_the_goals_page():
    s = Sampler()
    probes.run_probe(WR["wr-click-main-idle"], s, sleep=lambda s: None)
    assert s.calls == [("click", 20, "main")]


def test_startup_probe_values_and_limits():
    assert probes.run_probe(WR["wr-startup-interactive"], Sampler(), sleep=lambda s: None).ok               # 2.0 s < 3 s
    slow = Sampler(startup={"interactive_s": 3.4, "input_ignored_s": 1.6})
    r = probes.run_probe(WR["wr-startup-interactive"], slow, sleep=lambda s: None)
    assert not r.ok and r.value == 3.4 and r.unit == "s"
    assert not probes.run_probe(WR["wr-startup-input-ignored"], slow, sleep=lambda s: None).ok                 # the owner stared at a dead window for 1.6 s
    none = Sampler(startup={"interactive_s": 2.0, "input_ignored_s": None})
    assert probes.run_probe(WR["wr-startup-input-ignored"], none, sleep=lambda s: None).invalid


def test_a_sampler_that_cannot_run_is_invalid_never_a_pass():
    r = probes.run_probe(WR["wr-input-idle"], Sampler(raises=RuntimeError("cannot reach the face at http://127.0.0.1:9333")), sleep=lambda s: None)
    assert r.invalid.startswith("the probe could not run") and not r.ok
    assert probes.run_probe(WR["wr-input-idle"], Sampler(samples=[]), sleep=lambda s: None).invalid == "the probe returned no samples"


def test_stream_load_marks_ready_on_the_first_token_and_stops():
    chunks = [b'data: {"choices":[{"delta":{"content":"The"}}]}\n', b"\n", b'data: {"choices":[{"delta":{"content":" light"}}]}\n', b"data: [DONE]\n"]

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter(chunks)
    seen = {}

    def opener(req, timeout=None):
        seen["url"], seen["headers"], seen["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
        return Resp()
    load = probes.StreamLoad("local_model", online=False, opener=opener)
    load.start()
    assert load.wait_ready(5)
    load.stop()
    assert seen["url"].endswith("/v1/chat/completions") and seen["body"]["stream"] is True and "X-jarvis-route" not in seen["headers"]
    online = probes.StreamLoad("online_stream", online=True, opener=opener)
    online.start()
    online.wait_ready(5)
    assert seen["headers"]["X-jarvis-route"] == "online"


def test_stream_load_that_cannot_connect_reports_the_class_only():
    def opener(req, timeout=None):
        raise ConnectionRefusedError("secret address")
    load = probes.StreamLoad("local_model", opener=opener)
    load.start()
    assert not load.wait_ready(0.3) and load.error == "ConnectionRefusedError" and not load.alive()


def test_node_sampler_reads_the_probe_json_and_reports_a_harness_error():
    def run_ok(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout='noise\n{"mode":"input","page":"chat","samples":[12.5,14,15]}\n', stderr="")
    assert probes.NodeSampler(runner=run_ok).input_latency_ms(3, "chat") == [12.5, 14.0, 15.0]

    def run_bad(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 2, stdout='{"harness_error":"cannot reach the face at http://127.0.0.1:9333"}\n', stderr="")
    try:
        probes.NodeSampler(runner=run_bad).click_to_paint_ms(3, "main")
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "cannot reach the face" in str(e)


def test_goals_json_has_a_startup_command_for_the_face_copied_from_the_bench():
    cfg = CAT.probes["startup"]
    assert "--remote-debugging-port=9333" in " ".join(cfg["launch_cmd"]) and cfg["window_title"]


class HangingProbe:
    """D81 root cause: a node probe process that never exits on its own - exactly the shape of bug results/STATUS.md's Phase 3 already recorded live
    ("connectOverCDP itself returned after 20 s and was wrong" against an assumed bound). Before the fix, `NodeSampler.startup()`'s own
    `while probe.poll() is None` loop had no deadline at all and would spin on this forever."""

    def __init__(self):
        self.killed = False

    def poll(self):
        return None

    def kill(self):
        self.killed = True

    def communicate(self, timeout=None):
        return "", ""


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_the_startup_probes_own_process_is_killed_instead_of_waited_on_forever():
    clk = Clock()
    hang = HangingProbe()
    s = probes.NodeSampler(cfg={"startup": {"timeout_s": 5}}, clock=clk.now, sleep=clk.sleep, popen=lambda *a, **k: hang)
    with __import__("pytest").raises(RuntimeError, match="did not exit"):
        s.startup()
    assert hang.killed
    assert clk.t >= 5 + probes.STARTUP_GRACE_S          # the loop advanced by a BOUNDED amount, never forever


def test_a_hung_startup_probe_makes_run_probe_invalid_not_a_hang():
    clk = Clock()
    hang = HangingProbe()
    s = probes.NodeSampler(cfg={"startup": {"timeout_s": 5}}, clock=clk.now, sleep=clk.sleep, popen=lambda *a, **k: hang)
    r = probes.run_probe(WR["wr-startup-interactive"], s, sleep=lambda s: None)
    assert r.invalid.startswith("the probe could not run") and "did not exit" in r.invalid and not r.ok


def test_a_startup_probe_that_exits_in_time_is_unaffected_by_the_new_deadline():
    class QuickProbe:
        def __init__(self):
            self.polled = 0

        def poll(self):
            self.polled += 1
            return 0 if self.polled > 2 else None

        def communicate(self, timeout=None):
            return '{"t_first_handled_ms": 900, "t_page_ms": 500}\n', ""
    clk = Clock()
    s = probes.NodeSampler(cfg={"startup": {"timeout_s": 5}}, clock=clk.now, sleep=clk.sleep, popen=lambda *a, **k: QuickProbe())
    got = s.startup()
    assert got["interactive_s"] == 0.9 and clk.t < 1.0          # finished fast; the deadline never had to fire
