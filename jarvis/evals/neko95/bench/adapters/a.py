"""A: intent understanding and planning, against the real deciders.

* plugin.plugins.desktop.decide: the deterministic parts run as they are (fast path for one-command requests, follow-ups,
  stop words, validate() and the rule completion that repairs a model plan). The local decision model (an LLM on the
  owner's laptop) is NOT available on this server: where a case needs it, the model's answer is a recorded or
  hand-written plan, handed to the real code exactly where the model's JSON would arrive (decide(post=...) or
  validate(raw)). Each such case says so in its scenario.
* plugin.plugins.desktop.coordinator: what context a follow-up gets, and that a plan that asks or fails runs nothing.
* plugin.plugins.main_agent.planner / the plugin: strict parsing of an LLM-made plan (model replies are fixtures).
* jarvis_kernel.tasks: the durable task record a fresh session re-plans from.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import types
from pathlib import Path

from plugin.plugins.desktop import coordinator as C
from plugin.plugins.desktop import decide as D

PROFILES = [{"profile": "Default", "name": "Venkat", "account": "ven***", "default": True, "say": ["default", "venkat"]},
            {"profile": "Profile 1", "name": "Venkat", "account": "ris***", "default": False, "say": ["venkat", "venkatamv"]},
            {"profile": "Profile 2", "name": "Satish", "account": "sat***", "default": False, "say": ["satish", "satishk"]}]


class _ModelAsked(Exception):
    pass


def _no_model(body):
    raise _ModelAsked("the model was asked")


def _fake_model(plan, seen: list | None = None):
    """Stands in for the laptop's decision model: records what it was asked and returns a fixed plan (the model's JSON)."""
    def post(body):
        if seen is not None:
            seen.append(body["messages"][-1]["content"])
        return plan if isinstance(plan, str) else json.dumps(plan)
    return post


def _view(plan) -> str:
    steps = [{k: v for k, v in s.items() if k != "post" and v not in ("", None)} for s in plan.steps]
    q = f" q={plan.question!r}" if plan.question else ""
    pr = f" problems={plan.problems}" if plan.problems else ""
    return f"{plan.status} model={plan.model or '-'}{q}{pr} steps={json.dumps(steps)}"[:420]


def _check(plan, exp: dict) -> list[str]:
    """Compare a decide.Plan with measurable expectations; returns what differs (empty = pass)."""
    bad = []
    tools = [s["tool"] for s in plan.steps]
    if "status" in exp and plan.status != exp["status"]:
        bad.append(f"status {plan.status} != {exp['status']}")
    if "tools" in exp and tools != exp["tools"]:
        bad.append(f"tools {tools} != {exp['tools']}")
    if "has_tool" in exp and exp["has_tool"] not in tools:
        bad.append(f"no {exp['has_tool']} step")
    if "not_tools" in exp and set(tools) & set(exp["not_tools"]):
        bad.append(f"unwanted steps {sorted(set(tools) & set(exp['not_tools']))}")
    if "question_contains" in exp and exp["question_contains"].lower() not in (plan.question or "").lower():
        bad.append(f"question {plan.question!r} lacks {exp['question_contains']!r}")
    if "question_all" in exp and not all(w.lower() in (plan.question or "").lower() for w in exp["question_all"]):
        bad.append(f"question {plan.question!r} lacks one of {exp['question_all']}")
    if exp.get("no_steps") and plan.steps and plan.status == "ok":
        bad.append("steps would run")
    if exp.get("all_post") and not all(str(s.get("post") or "").strip() for s in plan.steps):
        bad.append("a step has no post-condition")
    if "problems_contains" in exp and not any(exp["problems_contains"].lower() in p.lower() for p in plan.problems):
        bad.append(f"problems {plan.problems} lack {exp['problems_contains']!r}")
    if "max_steps" in exp and len(plan.steps) > exp["max_steps"]:
        bad.append(f"{len(plan.steps)} steps > {exp['max_steps']}")
    if "model" in exp and plan.model != exp["model"]:
        bad.append(f"model {plan.model} != {exp['model']}")
    for want in exp.get("steps", []):
        i = want["i"]
        if i >= len(plan.steps):
            bad.append(f"no step {i}")
            continue
        got = plan.steps[i].get(want["key"])
        if "eq" in want and got != want["eq"]:
            bad.append(f"step {i} {want['key']}={got!r} != {want['eq']!r}")
        if "contains" in want and want["contains"].lower() not in str(got or "").lower():
            bad.append(f"step {i} {want['key']}={got!r} lacks {want['contains']!r}")
        if want.get("truthy") and not got:
            bad.append(f"step {i} {want['key']} is empty")
    for word in exp.get("never_in_steps", []):            # e.g. an account name must never be typed or searched
        for s in plan.steps:
            for k, v in s.items():
                if k in ("query", "text", "target") and word.lower() in str(v).lower():
                    bad.append(f"{word!r} in step {s['tool']}.{k}={v!r}")
    return bad


# ---------------------------------------------------------------- routing: rules vs the model
def fast_path_takes(a):
    """Each request is one simple command: planned by rules (model never asked), with a post-condition per step."""
    out = []
    for req, tool, val in a["requests"]:
        try:
            p = D.decide(req, PROFILES, post=_no_model)
        except _ModelAsked:
            return False, f"{req!r}: not planned by rules (the model was asked)"
        s = p.steps[0] if p.steps else {}
        ok = (p.model == "rule" and s.get("tool") == tool and any(val.lower() in str(v).lower() for k, v in s.items() if k != "post")
              and all(x.get("post") for x in p.steps))
        if not ok:
            return False, f"{req!r}: {_view(p)}"
        out.append(f"{req!r}->{tool}")
    return True, "; ".join(out)


def fast_path_declines(a):
    """Each request must NOT be planned by the one-command rules: fast_path() returns None and decide() asks the model."""
    out = []
    for req in a["requests"]:
        fp = D.fast_path(req, PROFILES)
        if fp is not None:
            return False, f"fast path took {req!r}: {_view(fp)}"
        seen: list = []
        D.decide(req, PROFILES, post=_fake_model({"steps": [{"tool": "answer"}]}, seen))
        if not seen:
            return False, f"{req!r}: the model was not asked"
        out.append(req)
    return True, f"{len(out)} requests went to the model: {out}"


def fast_path_speed(a):
    """p95 of decide() on fast-path requests, model never asked."""
    reqs, n, limit = a["requests"], int(a.get("n", 200)), float(a["p95_ms"])
    times = []
    for i in range(n):
        t0 = time.perf_counter()
        p = D.decide(reqs[i % len(reqs)], PROFILES, post=_no_model)
        times.append((time.perf_counter() - t0) * 1000)
        if p.model != "rule":
            return False, f"{reqs[i % len(reqs)]!r} not planned by rules"
    times.sort()
    p95 = times[int(0.95 * (len(times) - 1))]
    return p95 <= limit, f"p95 {p95:.2f} ms, max {times[-1]:.2f} ms over {n} calls (limit {limit} ms)"


def stop_words(a):
    out = []
    for req in a["requests"]:
        p = D.decide(req, PROFILES, post=_no_model)
        if [s["tool"] for s in p.steps] != ["stop"] or p.model != "rule":
            return False, f"{req!r}: {_view(p)}"
        out.append(req)
    return True, f"stop without the model for {out}"


def follow_up(a):
    """'open it' refers only to what the last task verifiably produced; the model is never asked."""
    try:
        p = D.decide(a["request"], PROFILES, last=a.get("last"), post=_no_model)
    except _ModelAsked:
        return (a.get("expect_model") is True), "the model was asked"
    if a.get("expect_model"):
        return False, f"rule answered a non-follow-up: {_view(p)}"
    bad = _check(p, a["expect"])
    if "path" in a and (not p.steps or p.steps[0].get("path") != a["path"]):
        bad.append("path differs")
    return not bad, "; ".join(bad) or _view(p)


# ---------------------------------------------------------------- the model's plan through the deterministic layer
def validate_plan(a):
    """A recorded or hand-written model plan (the JSON the laptop model returns) -> decide.validate(), the real rules."""
    variants = a.get("variants") or [{"request": a["request"], "model_plan": a["model_plan"], "expect": a["expect"]}]
    seen = []
    for v in variants:
        raw = v["model_plan"] if isinstance(v["model_plan"], str) else json.dumps(v["model_plan"])
        p = D.validate(raw, PROFILES, v["request"])
        bad = _check(p, v["expect"])
        if bad:
            return False, f"{v['request']!r}: {'; '.join(bad)} | {_view(p)}"
        seen.append(f"{v['request']!r}: {_view(p)[:160]}")
    return True, " || ".join(seen)[:500]


def decide_with_model(a):
    """decide() end to end with the model answer as a fixture: routing, what the model was asked, and the final plan."""
    seen: list = []
    p = D.decide(a["request"], PROFILES, context=a.get("context", ""), post=_fake_model(a["model_plan"], seen))
    bad = _check(p, a.get("expect", {}))
    if a.get("asked_contains") and not any(a["asked_contains"] in s for s in seen):
        bad.append(f"model was asked {seen!r}, lacking {a['asked_contains']!r}")
    return not bad, "; ".join(bad) or _view(p)


def _record(tasks: Path, request: str, at: float, steps: list[dict]):
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(at))}-aaaaaa.json").write_text(
        json.dumps({"request": request, "started": at - 5, "finished": at, "status": "completed", "steps": steps}), encoding="utf-8")


def coordinator_context(a):
    """The coordinator gives the model the previous request only when this one reads like a follow-up (the model is
    replaced by a recorder at decide.ask_model; nothing else is mocked)."""
    asked: list = []
    real = D.ask_model

    def fake(request, model=D.DEFAULT_MODEL, url=D.LOCAL, timeout=60.0, post=None):
        asked.append(request)
        return json.dumps(a["model_plan"]), 0.01

    with tempfile.TemporaryDirectory() as t:
        tasks, now = Path(t) / "tasks", time.time()
        _record(tasks, a["previous"], now - 30, a.get("previous_steps", []))
        D.ask_model = fake
        try:
            c = C.Coordinator({}, PROFILES, tasks_dir=tasks, lock_path=Path(t) / "lock")
            p = c.decide_fn(a["request"])
        finally:
            D.ask_model = real
    with_ctx = any("Previous request:" in s for s in asked)
    bad = []
    if a["expect_context"] != with_ctx:
        bad.append(f"context given={with_ctx}, wanted {a['expect_context']} (asked {asked})")
    bad += _check(p, a.get("expect", {}))
    return not bad, "; ".join(bad) or f"context given={with_ctx}; asked={asked[:1]}; {_view(p)}"


def coordinator_follow_memory(a):
    """'open it' through the coordinator: only a recent, CHECKED result of the last task can be opened."""
    with tempfile.TemporaryDirectory() as t:
        tasks = Path(t) / "tasks"
        _record(tasks, a["previous"], time.time() - float(a.get("age_s", 30)), a["previous_steps"])
        c = C.Coordinator({}, PROFILES, tasks_dir=tasks, lock_path=Path(t) / "lock")
        real = D.ask_model
        D.ask_model = lambda *x, **k: (_ for _ in ()).throw(_ModelAsked("model asked"))
        try:
            p = c.decide_fn(a["request"])
        finally:
            D.ask_model = real
    bad = _check(p, a["expect"])
    return not bad, "; ".join(bad) or _view(p)


def coordinator_runs_nothing(a):
    """A plan that asks, falls back or is not a PC task ends the task before any agent acts."""
    called = []
    agents = {t: (lambda s, t=t: called.append(t) or {"ok": True}) for t in D.TOOLS}
    raw = a["model_plan"] if isinstance(a["model_plan"], str) else json.dumps(a["model_plan"])
    with tempfile.TemporaryDirectory() as tmp:
        c = C.Coordinator(agents, PROFILES, tasks_dir=Path(tmp) / "t", lock_path=Path(tmp) / "lock",
                          decide_fn=lambda r: D.decide(r, PROFILES, post=lambda b: raw) if a.get("model_up", True)
                          else D.decide(r, PROFILES, post=lambda b: (_ for _ in ()).throw(D.DecisionError("the local decision model did not answer (URLError)"))))
        rec = c.run(a["request"])
    ok = rec["status"] == a["expect_status"] and not called
    return ok, f"status {rec['status']}, agents called {called}, question={rec.get('question')!r}, error={rec.get('error')!r}"


# ---------------------------------------------------------------- main agent planner (strict parsing)
def _caps(spec):
    from plugin.plugins.main_agent.planner import Capability
    out = []
    for c in spec or ["think", "web_search:search", "learn_vault:save_note"]:
        if c == "web_search:search":
            out.append(Capability("plugin", c, "search", {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}))
        elif c == "learn_vault:save_note":
            out.append(Capability("plugin", c, "save", {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                                                        "required": ["path", "content"]}))
        elif ":" in c:
            out.append(Capability("plugin", c, "x", None))
        else:
            out.append(Capability(c, c, c))
    return out


def planner_parse(a):
    from plugin.plugins.main_agent.planner import PlanError, parse_plan
    text = a["text"] if isinstance(a["text"], str) else json.dumps(a["text"])
    try:
        plan = parse_plan(text, _caps(a.get("caps")), int(a.get("max_steps", 6)))
    except PlanError as e:
        want = a.get("error_contains")
        return bool(want) and want.lower() in str(e).lower(), f"refused: {e}"
    if a.get("error_contains"):
        return False, f"accepted: {[(s.id, s.kind, s.ref, s.args) for s in plan.steps]}"
    got = [(s.id, s.kind, s.ref or s.kind, s.effect) for s in plan.steps]
    bad = []
    if "kinds" in a and [s.kind for s in plan.steps] != a["kinds"]:
        bad.append(f"kinds {[s.kind for s in plan.steps]} != {a['kinds']}")
    for sid, keys in (a.get("args_keys") or {}).items():
        st = next(s for s in plan.steps if s.id == sid)
        if sorted(st.args) != sorted(keys):
            bad.append(f"{sid} args {sorted(st.args)} != {sorted(keys)}")
    return not bad, "; ".join(bad) or f"accepted {got}"


def planner_effects(a):
    from plugin.plugins.main_agent.planner import effect_of
    wrong = [(ref, effect_of("plugin", ref), want) for ref, want in a["refs"].items() if effect_of("plugin", ref) != want]
    return not wrong, (f"misclassified (ref, got, want): {wrong}" if wrong else f"{len(a['refs'])} entries classified as expected")


def planner_catalogue(a):
    from plugin.plugins.main_agent.planner import PlanError, build_catalogue, filter_for_task, parse_plan
    plugins = [{"id": "web_search", "status": "running", "entries": [{"id": "search"}]},
               {"id": "learn_vault", "status": "running", "entries": [{"id": "save_note"}]},
               {"id": "main_agent", "status": "running", "entries": [{"id": "run_task"}]}]
    caps = build_catalogue(plugins, browser_ready=True, jarvis_ready=True, computer_ready=True, allow_computer=False, mode=a.get("mode", "work"))
    caps = filter_for_task(caps, a["task"])
    refs = sorted(c.ref for c in caps)
    bad = [r for r in a.get("absent", []) if r in refs] + [f"missing {r}" for r in a.get("present", []) if r not in refs]
    if a.get("plan"):
        try:
            parse_plan(json.dumps(a["plan"]), caps, 6)
            bad.append("a plan naming a helper outside the catalogue was accepted")
        except PlanError:
            pass
    return not bad, f"offered {refs}" + (f"; problems {bad}" if bad else "")


def _main_agent(replies):
    """The main agent plugin object with its model replaced by fixed replies (no host, no network)."""
    from plugin.plugins import main_agent as ma
    from plugin.plugins.main_agent.store import RunStore
    p = object.__new__(ma.MainAgentPlugin)
    p._runs, p._active, p._cancel = {}, None, None
    p._store = RunStore(Path(tempfile.mkdtemp()) / "runs")
    p.logger = logging.getLogger("bench")
    p.pushed, p.llm_calls = [], 0
    p.push_message = lambda **kw: p.pushed.append(kw)

    async def catalogue(mode="work"):
        return _caps(None)

    async def llm(messages, **kw):
        p.llm_calls += 1
        return replies[min(p.llm_calls - 1, len(replies) - 1)]

    p._catalogue, p._llm = catalogue, llm
    return p


def planner_retry(a):
    """One corrective retry of a refused plan, never more; the second refusal reaches the user as a plain message."""
    p = _main_agent([r if isinstance(r, str) else json.dumps(r) for r in a["replies"]])
    res = asyncio.run(p.plan_task(task=a["task"]))
    ok_type = type(res).__name__
    msg = str(getattr(getattr(res, "error", None), "message", None) or getattr(res, "error", "") or getattr(res, "value", ""))[:200]
    good = ok_type == a["expect_type"] and p.llm_calls == a["expect_calls"] and (a.get("message_contains", "") in msg)
    return good, f"{ok_type} after {p.llm_calls} model calls: {msg}"


# ---------------------------------------------------------------- kernel tasks: the durable record a re-plan starts from
def task_context(a):
    from jarvis_kernel.store import Kernel
    from jarvis_kernel.tasks import TaskError, Tasks
    tmp = tempfile.TemporaryDirectory()
    k = Kernel(os.path.join(tmp.name, "d"))
    try:
        T = Tasks(k)
        tid = T.create("hermes", "private", a["request"], a["goal"], a.get("plan"), a.get("criteria"), max_attempts=a.get("max_attempts", 3))
        refused = []
        for u in a.get("updates", []):
            try:
                T.update("hermes", tid, **u)
            except TaskError as e:
                refused.append(f"{u.get('state') or 'update'}: {e}")
        cp = T.checkpoint("hermes")
        c = cp["tasks"][0]
        bad = []
        for key, want in (a.get("expect") or {}).items():
            got = c.get(key)
            if isinstance(want, list):
                if not all(any(w.lower() in str(g).lower() for g in got) for w in want):
                    bad.append(f"{key}={got} lacks {want}")
            elif str(want).lower() not in str(got).lower():
                bad.append(f"{key}={got!r} lacks {want!r}")
        if len(refused) != int(a.get("expect_refused", 0)):
            bad.append(f"refused {refused}")
        for w in a.get("text_contains", []):
            if w.lower() not in cp["text"].lower():
                bad.append(f"text lacks {w!r}")
        return not bad, "; ".join(bad) or f"state {c['state']}, attempts {c['attempts']}, next {c['next']!r}, refused {refused}"
    finally:
        k.db.close()
        tmp.cleanup()
