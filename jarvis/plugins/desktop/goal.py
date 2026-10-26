"""A goal for the PC, the D51 way: an ONLINE model plans (text only, through the privacy gateway), LOCAL models execute.

    goal --> plan_request -> privacy gateway (online chain, no tools, no images, redacted) -> JSON plan, at most 6 short steps
         --> planner.parse_plan against a catalogue of the LOCAL executors below (nothing else can be named)
         --> each step, in order: the local specialist for that step's task kind picks the exact action / arguments
             (decide.py + the coordinator for PC work; a small grammar-forced pick for the read-only helpers), and the JARVIS
             kernel still permits or gates every job (D27). A gated step stops the goal and waits for the owner's PIN.

What the online model never does: emit a tool call, write an argument, or receive a screenshot or window text. It sees the owner's goal
(redacted by the gateway) and the executor catalogue, once. Its steps carry a goal sentence and a one-line success check; the exact tool and
arguments come from the local model, and the success check is reported next to what the local executor really observed.

If the gateway is not ready, the online call fails, or the plan is unusable (not JSON, too many steps, an executor that does not exist), the
local model alone plans (decide.py, as run_pc_task always did) and the result says so. `plan_online_execute_local = false` in plugin.toml
(or JARVIS_PLAN_ONLINE_EXECUTE_LOCAL=0) turns the whole split off: the goal goes straight to the local planner.

Which model does which kind of work comes from the one table in jarvis/config/specialists.json (plugins/_shared/specialists.py).
This module has no I/O of its own beyond the two loopback calls (gateway, local model); the plugin hands it the hands (`Hands`).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from jarvis.plugins._shared import loopback_http, screen_text, specialists
from jarvis.plugins.main_agent import planner

from . import decide

FLAG = "plan_online_execute_local"
ENV_FLAG = "JARVIS_PLAN_ONLINE_EXECUTE_LOCAL"
MAX_STEPS = 6
MAX_GOAL = 1000
STEP_GOAL_CHARS = 200
CHECK_CHARS = 160
RESULT_CHARS = 300
ONLINE_PLAN = "online_plan_local_execution"      # result.planned_by: one plain word (result strings are cleaned by key, see screen_text)
LOCAL_ALONE = "local_alone"
LOCAL_KEY = "jarvis-local"                     # the placeholder key the gateway knows (it adds the real one itself)
ROUTE_HEADER = "X-Jarvis-Route"                # "online": the gateway puts the online chain first (a reasoning call, not a simple local one)
PLAN_TIMEOUT_S = 90.0
# D60: a plan is a short JSON object, decided not invented: temperature 0 and a small answer limit. A reasoning model spends the same limit on hidden
# thinking, so a plan that comes back empty or cut off is asked ONCE more with PLAN_RETRY_TOKENS before the goal falls back to the local planner.
PLAN_TEMPERATURE = 0
PLAN_MAX_TOKENS = int(os.environ.get("JARVIS_PLAN_MAX_TOKENS", "") or 500)
PLAN_RETRY_TOKENS = 1500
PICK_TIMEOUT_S = 60.0
DEFAULT_ONLINE_MODEL = "zai-org/GLM-5.3-Flash"
PLUGIN_TOML = Path(__file__).with_name("plugin.toml")


class GoalError(RuntimeError):
    """The online plan could not be had, or a local pick failed. The message holds none of the goal's text."""


# ---------------------------------------------------------------------------------------------------------------- the flag
def flag_on(toml_path: Path | str | None = None, env: dict | None = None) -> bool:
    """plan_online_execute_local: the environment wins (0/false/no/off), then [config] in plugin.toml, default ON."""
    env = os.environ if env is None else env
    raw = str(env.get(ENV_FLAG, "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    try:
        data = tomllib.loads(Path(toml_path or PLUGIN_TOML).read_text(encoding="utf-8"))
        value = (data.get("config") or {}).get(FLAG, True)
    except (OSError, ValueError):
        return True
    return value is not False


# ------------------------------------------------------------------------------------------------------- local executors
@dataclass(frozen=True)
class Executor:
    id: str
    kind: str                      # the task kind in jarvis/config/specialists.json: which local model does this
    entry: str                     # "plugin:entry" it calls, "" for the coordinator, "reader:<name>" for a face-side read
    description: str
    schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})


def _obj(props: dict, required: list | None = None) -> dict:
    return {"type": "object", "properties": props, **({"required": required} if required else {})}


_S = {"type": "string"}
EXECUTORS: dict[str, Executor] = {e.id: e for e in (
    Executor("pc", "ui_decide", "", "Operate this PC: open or focus an app or a website in Chrome, play music or video, type, press keys, "
             "check disk or network, troubleshoot an app. A local model chooses the exact action."),
    Executor("screen_read", "screen_read", "desktop:read_window_text", "Read the text of one open window (accessibility, OCR, local vision). "
             "Say which app in the goal.", _obj({"app": _S}, ["app"])),
    Executor("screen_locate", "screen_locate", "desktop:find_on_screen", "Find where a label or text is inside one window (does not click). "
             "Say the app and the text.", _obj({"app": _S, "text": _S}, ["app", "text"])),
    Executor("doc_read", "doc_read", "doc_reader:read_document", "OCR a scanned document or picture of a page. Say the full file path.",
             _obj({"path": _S, "max_pages": {"type": "integer"}}, ["path"])),
    Executor("deliverable_list", "chat_simple", "deliverables:list_deliverables", "List the decks and reports JARVIS made."),
    Executor("deliverable_open", "chat_simple", "deliverables:open_output", "Open a deck or report JARVIS made, by full path.",
             _obj({"path": _S}, ["path"])),
    Executor("recall", "chat_simple", "reader:recall", "Short notes about earlier days (memory). Read-only.", _obj({"days": {"type": "integer"}})),
    Executor("tasks", "chat_simple", "reader:tasks", "JARVIS's recent tasks and their state. Read-only.",
             _obj({"state": _S, "limit": {"type": "integer"}})),
)}


def catalogue(readers: Any = ()) -> list[planner.Capability]:
    """The executors the planner may name. The face-side readers (recall, tasks) are offered only when the host wired them."""
    out = []
    for ex in EXECUTORS.values():
        if ex.entry.startswith("reader:") and ex.entry.split(":", 1)[1] not in readers:
            continue
        # no schema here on purpose: the online model writes no arguments, so parse_plan has nothing to require of it (the local pick has the schema)
        out.append(planner.Capability("plugin", ex.id, f"[{ex.kind}] {ex.description}", None))
    return out


# ------------------------------------------------------------------------------------------------------------- the plan
def plan_messages(goal: str, caps: list[planner.Capability]) -> list[dict]:
    lines = "\n".join(f'- "{c.ref}": {c.description}' for c in caps)
    system = (
        "You plan work for a personal assistant on the owner's Windows PC. Answer with ONLY a JSON object, no prose:\n"
        '{"summary": "<one short sentence>", "steps": [{"id": "s1", "agent": "plugin", "plugin": "<executor id>", '
        '"goal": "<what this step must achieve, one short sentence>", "check": "<one line: how to know it worked>", "depends_on": []}]}\n'
        f"Rules: at most {MAX_STEPS} steps, the fewest that do the job (one is best); steps run one after another; use only the executors "
        "listed below; do NOT write tool calls, function names or arguments, a small model on the PC chooses the exact action; "
        "do not put passwords, keys or private numbers in a step. If no executor can do it, return "
        '{"summary": "cannot", "steps": []}.\n\nExecutors (the bracket is the kind of local model that runs it):\n' + lines)
    return [{"role": "system", "content": system}, {"role": "user", "content": screen_text.scrub_text(" ".join(goal.split())[:MAX_GOAL])[0]}]


def _gateway_base() -> str:
    try:
        port = int(os.environ.get("JARVIS_PRIVACY_PORT", "48950"))
    except ValueError:
        port = 48950
    return f"http://127.0.0.1:{port}"


def ask_online(messages: list[dict], post: Callable[[dict, dict], tuple[str, str]] | None = None, timeout: float = PLAN_TIMEOUT_S) -> tuple[str, str]:
    """(plan text, provider that answered) from the privacy gateway's online chain. The body holds messages and a few numbers: no `tools`,
    no `tool_choice`, no image part, no streaming. `post(body, headers)` is the test seam."""
    body = {"model": os.environ.get("JARVIS_AGENT_MODEL", DEFAULT_ONLINE_MODEL), "temperature": PLAN_TEMPERATURE, "max_tokens": PLAN_MAX_TOKENS, "messages": messages}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LOCAL_KEY}", ROUTE_HEADER: "online"}
    if post is not None:
        return post(body, headers)
    ready, why = screen_text.gateway_state()
    if not ready:
        raise GoalError(f"the privacy gateway is not ready ({why})")
    for attempt in (1, 2):
        req = urllib.request.Request(_gateway_base() + "/v1/chat/completions", data=json.dumps(body).encode(), headers=headers)
        try:
            with loopback_http.urlopen(req, timeout=timeout) as r:
                provider = r.headers.get("X-Jarvis-Provider", "") or "gateway"
                out = json.loads(r.read().decode("utf-8", "replace"))
            choice = out["choices"][0]
            content = choice["message"]["content"]
        except urllib.error.HTTPError as e:
            raise GoalError(f"the online model answered HTTP {e.code}") from None
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            raise GoalError(f"the online plan could not be read ({type(e).__name__})") from None
        empty = not isinstance(content, str) or not content.strip()
        if (empty or choice.get("finish_reason") == "length") and attempt == 1 and body["max_tokens"] < PLAN_RETRY_TOKENS:
            body = {**body, "max_tokens": PLAN_RETRY_TOKENS}         # cut off or all thinking: once more with room
            continue
        break
    if empty:
        raise GoalError("the online model returned no plan")
    return content, provider


@dataclass
class GoalPlan:
    summary: str
    steps: list[dict]              # {n, id, executor, kind, goal, check}
    provider: str = ""


def parse_online_plan(text: str, caps: list[planner.Capability]) -> GoalPlan:
    """planner.parse_plan against the executor catalogue, then the extras the planner does not keep (the success check). Raises planner.PlanError."""
    plan = planner.parse_plan(text, caps, MAX_STEPS)
    raw = planner._extract_json(text).get("steps") or []
    checks = {str(s.get("id")): s.get("check") for s in raw if isinstance(s, dict)}
    steps = []
    for n, s in enumerate(plan.steps, 1):
        ex = EXECUTORS[s.ref]
        steps.append({"n": n, "id": s.id, "executor": ex.id, "kind": ex.kind, "goal": s.goal[:STEP_GOAL_CHARS],
                      "check": " ".join(str(checks.get(s.id) or "").split())[:CHECK_CHARS]})
    return GoalPlan(plan.summary, steps)


# ---------------------------------------------------------------------------------------- local picks of exact arguments
def _local_chat(body: dict, post: Callable[[dict], str] | None, timeout: float = PICK_TIMEOUT_S) -> str:
    if post is not None:
        return post(body)
    req = urllib.request.Request(decide.LOCAL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with loopback_http.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8", "replace"))
        content = out["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
        raise GoalError(f"the local model did not answer ({type(e).__name__})") from None
    return content if isinstance(content, str) else ""


def _same_text(needle: str, haystack: str) -> bool:
    norm = lambda s: re.sub(r"[\s\\/]+", "", str(s)).lower()           # noqa: E731
    return bool(norm(needle)) and norm(needle) in norm(haystack)


def pick_args(ex: Executor, spec: specialists.Specialist, step_goal: str, request: str, post: Callable[[dict], str] | None = None) -> dict:
    """The exact arguments of a read-only helper, chosen by the local specialist (alias, then its fallback), grammar-forced to the entry's
    schema. Nothing is guessed: a missing required value, a wrong type, or a `path` the owner never wrote is a GoalError."""
    props = ex.schema.get("properties") or {}
    if not props:
        return {}
    schema = {"type": "object", "properties": props, "required": list(ex.schema.get("required") or []), "additionalProperties": False}
    system = (f"Fill in the arguments of the tool '{ex.entry}' as JSON. Copy values from the owner's request; never invent one. "
              "Answer ONLY with the JSON object.")
    user = f"Owner's request: {request[:MAX_GOAL]}\nThis step: {step_goal}"
    last: Exception | None = None
    for alias in spec.aliases:
        body = {"model": alias, "temperature": 0, "max_tokens": spec.max_tokens or 300, "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {"type": "json_schema", "json_schema": {"name": "args", "schema": schema}},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            data = json.loads(planner.strip_reasoning(_local_chat(body, post)) or "null")
        except GoalError as e:
            last = e
            continue                                       # the model is down or missing: its fallback alias is next
        except ValueError:
            last = GoalError("the local model's arguments were not valid JSON")
            continue
        if not isinstance(data, dict):
            last = GoalError("the local model's arguments had the wrong shape")
            continue
        args = {k: v for k, v in data.items() if k in props and v not in ("", None)}
        missing = [k for k in schema["required"] if k not in args]
        if missing:
            raise GoalError(f"the request does not say {', '.join(missing)}")
        if any(not planner._type_ok(v, props[k]) for k, v in args.items()):
            raise GoalError("the local model gave an argument of the wrong type")
        if "path" in args and not _same_text(args["path"], request):
            raise GoalError("the file path is not one the owner wrote, so it was not used")
        if any(isinstance(v, str) and (re.search(r"[\r\n\0]", v) or len(v) > 300) for v in args.values()):
            raise GoalError("an argument was not one plain short line")
        return args
    raise last or GoalError("no local model answered")


# ----------------------------------------------------------------------------------------------------------------- running
@dataclass
class Hands:
    """What the plugin lends the runner. All are async except the two model calls, which run in a worker thread."""
    run_pc: Callable[[str, Any], Awaitable[dict]]                  # (step goal, decide.Plan) -> the coordinator's record
    call: Callable[[str, dict], Awaitable[tuple[bool, dict, str]]]  # (entry ref, args) -> (ok, value, error text)
    ask_online: Callable[[list[dict]], tuple[str, str]] = ask_online
    decide_fn: Callable[[str, str], decide.Plan] | None = None     # (step goal, model alias) -> decide.Plan
    local_post: Callable[[dict], str] | None = None
    readers: dict = field(default_factory=dict)                    # {"recall": fn(days=3) -> str, "tasks": fn(state, limit) -> list}
    on_progress: Callable[[dict], None] | None = None              # D60: {state: planning|running|done|failed|waiting, n, total, executor}; content-free, never raises into the goal


def _short(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return " ".join(str(value or "").split())[:RESULT_CHARS]


def _brief_value(v: dict) -> str:
    keep = {k: v[k] for k in ("text", "lines", "found", "x", "y", "note", "text_note", "summary", "message", "files", "count", "deliverables",
                              "opened", "page_count", "pages") if k in v and v[k] not in (None, "", [])}
    return _short(keep or {k: v[k] for k in list(v)[:4]})


_WAITING = re.compile(r"(?i)approval|permission|\bPIN\b")


class GoalRunner:
    def __init__(self, hands: Hands, table: dict | None = None):
        self.h = hands
        self.table = specialists.load() if table is None else table
        self.decide_fn = hands.decide_fn or (lambda goal, model: decide.decide(goal, _profiles(), model=model, post=hands.local_post))

    def _note(self, state: str, n: int = 0, total: int = 0, executor: str = "") -> None:
        """D60: tell the host which step is running (for the face's progress line). Fixed words and numbers only; a broken callback is ignored."""
        if self.h.on_progress is not None:
            try:
                self.h.on_progress({"state": state, "n": n, "total": total, "executor": executor})
            except Exception:                                   # noqa: BLE001 - progress must never fail a goal
                pass

    # ---- the whole goal
    async def run(self, goal: str, enabled: bool = True) -> dict:
        goal = " ".join(str(goal or "").split())[:MAX_GOAL]
        if not goal:
            return {"status": "failed", "planned_by": "none", "summary": "Say what you want done on the PC.", "steps": []}
        if not enabled:
            return await self._local_alone(goal, "the online-plan option is off")
        caps = catalogue(self.h.readers)
        self._note("planning")
        try:
            text, provider = await asyncio.to_thread(self.h.ask_online, plan_messages(goal, caps))
            plan = parse_online_plan(text, caps)
            plan.provider = provider
        except GoalError as e:
            return await self._local_alone(goal, str(e))
        except planner.PlanError as e:
            return await self._local_alone(goal, f"the online plan was not usable ({e})")
        return await self._execute(goal, plan)

    async def _local_alone(self, goal: str, why: str) -> dict:
        """The local model plans by itself (decide.py, as run_pc_task always did) and says so."""
        spec = specialists.resolve("ui_decide", self.table)
        self._note("running", 1, 1, "pc")
        plan = await self._decide(goal, spec)
        rec = await self.h.run_pc(goal, plan)
        step = self._pc_step({"n": 1, "executor": "pc", "kind": "ui_decide", "goal": goal, "check": ""}, rec, spec.alias)
        out = self._result([step], LOCAL_ALONE, f"Planned by the local model alone: {why}.", rec)
        out["reason"] = why[:200]
        return out

    async def _decide(self, goal: str, spec: specialists.Specialist) -> decide.Plan:
        plan = None
        for alias in spec.aliases:
            plan = await asyncio.to_thread(self.decide_fn, goal, alias)
            if plan.status != "fallback" or not plan.problems or "did not answer" not in " ".join(plan.problems):
                return plan
        return plan

    async def _execute(self, goal: str, plan: GoalPlan) -> dict:
        done: list[dict] = []
        last_rec: dict | None = None
        for s in plan.steps:
            ex, spec = EXECUTORS[s["executor"]], specialists.resolve(EXECUTORS[s["executor"]].kind, self.table)
            self._note("running", s["n"], len(plan.steps), ex.id)
            if ex.entry == "":
                dplan = await self._decide(s["goal"], spec)
                last_rec = rec = await self.h.run_pc(s["goal"], dplan)
                step = self._pc_step(s, rec, spec.alias)
            else:
                step = await self._helper_step(s, ex, spec, goal)
            done.append(step)
            if step["status"] != "completed":
                break
        for s in plan.steps[len(done):]:
            done.append({**s, "status": "not_run", "model": "", "result": "an earlier step did not finish"})
        head = f"Planned by {plan.provider or 'the online model'} (text only), executed by local models. {plan.summary}".strip()
        out = self._result(done, ONLINE_PLAN, head, last_rec)
        out["provider"] = plan.provider or "gateway"
        self._note({"completed": "done", "waiting_for_permission": "waiting"}.get(out["status"], "failed"), len(done), len(plan.steps))
        return out

    def _pc_step(self, s: dict, rec: dict, alias: str) -> dict:
        status = {"completed": "completed", "waiting_for_permission": "waiting_for_permission", "needs_clarification": "needs_clarification",
                  "cancelled": "cancelled", "stopped": "cancelled"}.get(rec.get("status"), "failed")
        steps = rec.get("steps") or []
        observed = "; ".join(str(x.get("checked")) for x in steps if x.get("passed") and x.get("checked"))
        text = observed or rec.get("question") or rec.get("how") or rec.get("error") or rec.get("route") or rec.get("status") or ""
        out = {**s, "status": status, "model": alias, "result": _short(text), "done_text": _short(observed)}
        if rec.get("approval_id"):
            out["approval_id"] = rec["approval_id"]
        out["verified"] = bool(steps) and all(x.get("passed") for x in steps) and not rec.get("uncertain")
        return out

    async def _helper_step(self, s: dict, ex: Executor, spec: specialists.Specialist, request: str) -> dict:
        base = {**s, "model": spec.alias}
        try:
            args = await asyncio.to_thread(pick_args, ex, specialists.resolve("chat_simple", self.table), s["goal"], request, self.h.local_post)   # arguments are simple text work
            if ex.entry.startswith("reader:"):
                fn = self.h.readers[ex.entry.split(":", 1)[1]]
                value = await asyncio.to_thread(lambda: fn(**args))
                ok, val, err = True, ({"text": value} if not isinstance(value, dict) else value), ""
                if isinstance(value, list):
                    val = {"count": len(value), "text": _short(value)}
            else:
                ok, val, err = await self.h.call(ex.entry, args)
        except GoalError as e:
            return {**base, "status": "failed", "result": str(e)[:RESULT_CHARS]}
        except Exception as e:  # noqa: BLE001 - a failed helper is a failed step
            return {**base, "status": "failed", "result": f"{type(e).__name__}"}
        if ok and val.get("ok") is not False:
            return {**base, "status": "completed", "result": _brief_value(val), "verified": False}
        msg = _short(err or val.get("error") or val.get("note") or "the step did not succeed")
        waiting = val.get("state") == "waiting_for_permission" or bool(_WAITING.search(msg))
        return {**base, "status": "waiting_for_permission" if waiting else "failed", "result": msg}

    def _result(self, steps: list[dict], planned_by: str, head: str, rec: dict | None) -> dict:
        bad = next((s for s in steps if s["status"] not in ("completed", "not_run")), None)
        status = "completed" if bad is None else bad["status"]
        for s in steps:
            if s.get("check"):
                s["note"] = "success check from the plan: " + s["check"]          # next to what the executor observed, not instead of it
        if bad is None:
            # a helper's own text (a window's words, a document) stays in the step's `result`, which is cleaned as screen text; the summary has none of it
            tail = " ".join(f"Step {s['n']}: {s['done_text']}." if s.get("done_text") else f"Step {s['n']} ({s['executor']}) done." for s in steps)
            summary = f"{head} Done. {tail}".strip()
        elif status == "waiting_for_permission":
            aid = bad.get("approval_id") or (rec or {}).get("approval_id")
            summary = (f"{head} Step {bad['n']} needs your approval first. Approve it in the JARVIS console with your PIN"
                       + (f" (approval {aid})" if aid else "") + ", then ask me to continue; the later steps have not run.")
        elif status == "needs_clarification":
            summary = f"{head} {bad['result']}"
        else:
            summary = f"{head} I could not finish: step {bad['n']} ({bad['executor']}): {bad['result']}"
        steps = [{k: v for k, v in s.items() if k not in ("done_text", "check")} for s in steps]
        out = {"status": status, "planned_by": planned_by, "summary": summary[:1500], "steps": steps}
        aid = bad.get("approval_id") if bad else None
        if aid or (rec or {}).get("approval_id") and status == "waiting_for_permission":
            out["approval_id"] = aid or rec.get("approval_id")
        return out


def _profiles() -> list:
    from . import core
    return core.profiles()
