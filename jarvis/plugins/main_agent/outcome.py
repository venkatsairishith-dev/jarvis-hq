"""What a plugin entry's result SAYS about its own outcome (re-review M11, 2026-10-18).

A plugin entry that returns Ok(...) has only said "I ran". Whether the thing it was asked to do worked is in the payload, and the
payloads differ: {"status": "failed"}, {"state": "denied"}, {"outcome": "the fix was not applied"}, {"recovered": False},
{"qa": {"passed": False}}, {"error": "..."}, {"refused": True}, a list of steps one of which did not pass, a value wrapped in
{"data": {...}}, or nothing at all. The old check read three keys and treated everything else as success.

The contract (fail closed, one place):
  * Evidence is collected from the payload and from every dict nested under `data` / `result` (a wrapper never hides a failure).
  * Words are matched against CLOSED vocabularies (below); a status word that is in none of them is "not recognised", which is
    NOT success for an entry with a registered schema.
  * Definite failure beats everything but a wait for the owner or a stop; "part of it ran" turns a failure into `unknown` (the
    caller holds a step that changes something and never retries it).
  * Success needs positive evidence: a status in the success vocabulary, `ok`/`success` true, or (SCHEMAS) the entry's own
    result key. A payload with no evidence either way is `unknown` for a registered write entry, for any entry that changes
    something, and for an empty or missing payload; it is success only for a registered read entry or an unregistered one that
    only reads and returned something.
  * SCHEMAS registers the entries this code can see. Another plugin's entry is judged on negative evidence and on the rules
    above; its own Err(...) remains its failure channel.

judge() never raises and never returns payload text other than short reason words taken from the payload's own fields; the caller
cleans the reason (paths, keys, e-mail) before it reaches a message.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

SUCCESS, FAILED, UNKNOWN, CANCELLED, WAITING = "success", "failed", "unknown", "cancelled", "waiting"
_PRIORITY = {WAITING: 5, CANCELLED: 4, FAILED: 3, UNKNOWN: 2, SUCCESS: 1}


@dataclass(frozen=True)
class Verdict:
    kind: str
    reason: str = ""
    partial: bool = False                  # part of it ran (a step passed, a fix was applied): the owner must hear it


@dataclass(frozen=True)
class Schema:
    kind: str = "write"                    # read | write | start (a background job: "started"/"queued" is success) | stop (stopping is the point)
    shape: tuple[str, ...] = ()            # a non-empty result key of this entry that is positive evidence on its own
    required_true: tuple[str, ...] = ()    # keys that must be true for success (recovered, ...)
    free_status: bool = False              # `status` is data (a learning status), not an outcome
    layers: tuple[str, ...] = ()           # result keys holding a nested dict that is read like the payload itself (e.g. login, qrcode)
    words: tuple[tuple[str, str], ...] = ()  # status words with a meaning only for this entry: (word, success|failed|waiting|unknown)
    post: tuple[tuple[str, Any], ...] = () # postconditions: a dotted path that, when present, must equal the value (logout: login.logged_in False)


def _w(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")


SUCCESS_WORDS = frozenset({"ok", "success", "succeeded", "successful", "completed", "complete", "done", "finished", "passed", "applied",
                           "verified", "fixed", "ready", "saved", "created", "idle", "continuing", "shutdown", "stopped_ok", "sent",
                           "already_running", "not_running", "logged_in", "confirmed", "diagnosed"})
STARTED_WORDS = frozenset({"started", "queued", "running", "in_progress", "pending", "accepted", "scheduled", "working", "planning", "resuming"})
STOPPED_WORDS = frozenset({"stopped", "cancelled", "canceled", "killed", "aborted_by_owner"})
FAILED_WORDS = frozenset({"failed", "failure", "error", "errored", "busy", "denied", "refused", "rejected", "not_understood",
                          "not_a_pc_task", "unavailable", "invalid", "aborted", "crashed", "expired", "forbidden", "not_found"})
UNKNOWN_WORDS = frozenset({"unknown", "partial", "partially", "incomplete", "interrupted", "held", "timed_out", "timeout", "stale",
                           "uncertain", "indeterminate"})
WAITING_WORDS = frozenset({"waiting_for_permission", "waiting", "needs_clarification", "needs_owner", "needs_approval",
                           "awaiting_approval", "pending_approval", "waiting_for_approval", "needs_input"})
READ_ONLY_WORDS = frozenset({"none"})              # mission_status with no mission: nothing to report, not a failure

# Booleans: false is a failure ...
_FALSE_FAILS = ("ok", "success", "succeeded", "passed", "qa_passed", "recovered", "applied", "completed", "saved", "created", "sent",
                "delivered", "opened", "shown", "presenting", "launched", "installed", "stopped_ok", "persisted")
# ... and true is a failure / a cancel / an unknown.
_TRUE_FAILED = ("failed", "refused", "denied", "rejected", "blocked", "busy", "error", "aborted", "crashed", "forbidden")
_TRUE_CANCELLED = ("cancelled", "canceled", "stopped", "killed")
_TRUE_UNKNOWN = ("timed_out", "timeout", "partial", "incomplete", "interrupted", "uncertain")

# `outcome` is a sentence (the troubleshooter's). First match wins; anything else is not recognised.
_OUTCOME = (
    (re.compile(r"^stopped by the owner"), CANCELLED),
    (re.compile(r"^(?:fix applied|applied)[, ]+but\b"), UNKNOWN),          # applied, then the check failed / could not run: undo exists
    (re.compile(r"waiting for (?:your|the owner|an? )"), WAITING),
    (re.compile(r"^not approved|\bdenied\b|\brefused\b|\bnot approved\b"), FAILED),
    (re.compile(r"\bwas not applied\b|\bnot applied\b|\bdid not (?:apply|work|start|finish)\b|\bcould not\b|\bfailed\b|\bunable\b"), FAILED),
    (re.compile(r"^fixed and verified|^diagnosed|^hypothesis only|^done\b|^completed\b|^succeeded\b|^success\b"), SUCCESS),
)

SCHEMAS: dict[str, Schema] = {}


def _reg(plugin: str, entries: str, **kw) -> None:
    for e in entries.split():
        SCHEMAS[f"{plugin}:{e}"] = Schema(**kw)


_reg("learn_vault", "vault_status list_notes read_note search_notes get_progress", kind="read")
_reg("learn_vault", "save_note", shape=("path",))
_reg("learn_vault", "record_progress", shape=("topic",), free_status=True)
_reg("doc_tools", "read_document list_outputs", kind="read")
_reg("doc_tools", "write_document", shape=("path",))
_reg("deliverables", "list_deliverables", kind="read")
_reg("deliverables", "make_presentation make_report regenerate", shape=("file",))
_reg("deliverables", "open_output", shape=("opened",))
_reg("deliverables", "show_in_folder", shape=("shown",))
_reg("deliverables", "start_slideshow", shape=("presenting",))
_reg("desktop", "check_pc list_browser_profiles recent_pc_alerts mission_status find_on_screen read_window_text", kind="read")
_reg("desktop", "open_website play_youtube run_browser_action run_pc_action")           # _brief: state + ok
_reg("desktop", "run_troubleshooter run_pc_task do_goal quick_command resume_mission")                         # outcome / status
_reg("desktop", "start_mission", kind="start", shape=("mission",))
_reg("desktop", "stop_pc_task", kind="stop", shape=("stopped_jobs",))
_reg("desktop", "stop_mission", kind="stop", shape=("mission",), required_true=("stopped",))
_reg("desktop", "stop_pc_job", kind="stop", shape=("id",))
_reg("desktop", "recover_pc_lock", required_true=("recovered",))
_reg("jarvis_bridge", "jarvis_status pending_approvals connector_overview task_result", kind="read")
_reg("jarvis_bridge", "delegate_task", kind="start", shape=("command_id",))
_reg("jarvis_bridge", "cancel_task", kind="stop", shape=("command_id",), required_true=("cancelled",))
_reg("web_search", "search search_summary", kind="read")
_reg("research", "research_topic", shape=("spec", "summary"))
_reg("local_team", "ask_local solve_with_team", shape=("answer", "plan", "text"))
_reg("local_team", "team_status", kind="read")
_reg("main_agent", "run_status plan_task", kind="read", free_status=True)       # the run's own state (held, failed ...) is the data read
_reg("main_agent", "run_task", kind="start", shape=("run_id",), words=(("answered", "success"),))   # a spoken answer resumes a held run
_reg("main_agent", "resume_run", kind="start", shape=("run_id",))
_reg("main_agent", "pending_inputs", kind="read")
_reg("main_agent", "learning_stats", kind="read")
_reg("main_agent", "forget_learning", kind="stop", words=(("forgotten", "success"),))
_reg("main_agent", "answer_input", kind="start", shape=("run_id",), words=(("answered", "success"),))
_reg("main_agent", "cancel_input", kind="stop", shape=("run_id",), words=(("cancelled", "success"),))
_reg("main_agent", "stop_task", kind="stop", shape=("message", "requested"))
# workbench (OUR entries). Its statuses say whether something is verified; only "validated" / "verified" count as success.
_reg("workbench", "project_status list_learned", kind="read")
_reg("workbench", "get_prompt", kind="read", free_status=True)                   # a prompt's status ("suggestion", "submitted") is data
_reg("workbench", "create_project set_provider_policy", shape=("project_id",))
_reg("workbench", "add_project_files", shape=("added",))
_reg("workbench", "analyze_project answer_questions", shape=("stats",))
_reg("workbench", "generate_prompt", shape=("id",), free_status=True)            # generated = saved as a suggestion, nothing ran
_reg("workbench", "edit_prompt regenerate_prompt", shape=("id", "version"))
_reg("workbench", "build_deliverables", words=(("verified", "success"), ("needs_review", "unknown"), ("partly_verified", "unknown"),
                                               ("blocked", "failed")))
_reg("workbench", "learn_task", words=(("validated", "success"), ("not_validated", "failed"), ("not_adopted", "failed"),
                                       ("invalid_workflow", "failed"), ("needs_examples", "waiting"), ("needs_a_workflow", "waiting"),
                                       ("needs_the_lab", "waiting")))
_reg("workbench", "execute_prompt run_learned_workflow", kind="start", shape=("ref", "command_id"), words=(("submitted", "success"),))
_reg("workbench", "check_workflow_output", kind="read", required_true=("verified",))
_reg("workbench", "rename_prompt", shape=("title",))
_reg("workbench", "duplicate_prompt", shape=("id", "version"))
_reg("workbench", "compare_prompt search_skills inspect_skill briefing", kind="read")
_reg("workbench", "export_prompt export_skill", shape=("file",))
_reg("workbench", "reuse_skill", kind="read", required_true=("ok",), post=(("checks.verified", True),))
_reg("workbench", "regress_skill", required_true=("passed",))
_reg("workbench", "disable_skill", shape=("status",), words=(("disabled", "success"),), post=(("status", "disabled"),))
_reg("workbench", "enable_skill", shape=("status",), words=(("active", "success"),), post=(("status", "active"),))
_reg("workbench", "rollback_skill", shape=("current",))
_reg("workbench", "import_skill", shape=("sha256",), free_status=True)            # imported = stored quarantined, nothing activated


def _layers(value: Any, depth: int = 0) -> list[dict]:
    """The payload and every dict nested under data / result, outermost first (a wrapper never hides a failure)."""
    if not isinstance(value, dict) or depth > 5:
        return []
    out = [value]
    for key in ("data", "result"):
        inner = value.get(key)
        if isinstance(inner, dict):
            out += _layers(inner, depth + 1)
    return out


def _dig(d: Any, path: str) -> tuple[bool, Any]:
    for part in path.split("."):
        if not isinstance(d, dict) or part not in d:
            return False, None
        d = d[part]
    return True, d


def _has(d: dict, key: str) -> bool:
    """The key is there with a real value (0 counts - stopped_jobs 0 is an answer; False, blank text, an empty container and NaN / infinity do not)."""
    if key not in d:
        return False
    v = d[key]
    if v is None or v is False or (isinstance(v, str) and not v.strip()) or (isinstance(v, (list, dict, tuple)) and not v):
        return False
    if isinstance(v, float) and not math.isfinite(v):         # NaN / infinity is not a count
        return False
    return True


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {} or v is False


def _first_reason(layers: list[dict]) -> str:
    for d in layers:
        for key in ("error", "reason", "summary", "message", "question", "how", "note", "outcome"):
            v = d.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()[:300]
    return ""


def _passed(layers: list[dict]) -> bool:
    for d in layers:
        steps = d.get("steps")
        if isinstance(steps, list) and any(isinstance(s, dict) and s.get("passed") is True for s in steps):
            return True
    return False


def judge(ref: str, value: Any, effect: str = "none") -> Verdict:
    """The outcome a result states, for the plugin entry `ref` ("plugin:entry"); effect is the step's ("none" reads,
    "non_idempotent" changes something). Never raises."""
    try:
        return _judge(str(ref or ""), value, effect)
    except Exception:  # noqa: BLE001 - a result this code cannot read is not a success
        return Verdict(UNKNOWN, "its result could not be read")


def _judge(ref: str, value: Any, effect: str) -> Verdict:
    schema = SCHEMAS.get(ref)
    kind = schema.kind if schema else ("read" if effect == "none" else "write")
    layers = _layers(value)
    for nested in (schema.layers if schema else ()):     # e.g. {"login": {...}, "qrcode": {...}}
        for d in list(layers):
            if isinstance(d.get(nested), dict):
                layers.append(d[nested])
    evidence: list[tuple[str, str]] = []                 # (verdict kind, why)
    positive = False

    # --- not a mapping: nothing to read keys from
    if not layers:
        if isinstance(value, str) and value.strip():
            for rx, k in _OUTCOME:
                if rx.search(value.strip().lower()):
                    return Verdict(k, value.strip()[:300]) if k != SUCCESS else Verdict(SUCCESS)
            return Verdict(UNKNOWN, "its result had no recognisable shape") if schema else Verdict(SUCCESS)
        if _empty(value) or schema is not None or effect == "non_idempotent":
            return Verdict(UNKNOWN, "it returned no result, so whether it worked is not known")
        return Verdict(SUCCESS)                          # an unregistered read returned a non-empty list / number

    is_start, is_stop = kind == "start", kind == "stop"
    for d in layers:
        # -- status / state
        for key in ("status", "state"):
            raw = d.get(key)
            if not isinstance(raw, str) or not raw.strip() or (schema and schema.free_status):
                continue
            w = _w(raw)
            override = dict(schema.words).get(w) if schema else None
            if override == "waiting" and kind != "read":
                evidence.append((WAITING, w))
            elif override == "waiting":
                positive = True
            elif override == "failed":
                evidence.append((FAILED, w))
            elif override == "success":
                positive = True
            elif override == "unknown":
                evidence.append((UNKNOWN, w))
            elif w in WAITING_WORDS:
                evidence.append((WAITING, w))
            elif w in FAILED_WORDS:
                evidence.append((FAILED, w))
            elif w in UNKNOWN_WORDS:
                evidence.append((UNKNOWN, w))
            elif w in STOPPED_WORDS:
                if is_stop:
                    positive = True
                else:
                    evidence.append((CANCELLED, w))
            elif w in STARTED_WORDS:
                if is_start or kind == "read":                  # a job just begun, or a status read of a job that is still going
                    positive = True
                else:
                    evidence.append((UNKNOWN, f"it is still {w.replace('_', ' ')}"))
            elif w in SUCCESS_WORDS or (kind == "read" and w in READ_ONLY_WORDS):
                positive = True
            elif schema is not None and kind != "read":
                evidence.append((UNKNOWN, f"a status that is not recognised ({w[:30]})"))
        # -- booleans and flags
        for key in _FALSE_FAILS:
            if d.get(key) is False:
                evidence.append((FAILED, f"{key} is false"))
            elif d.get(key) is True:
                positive = True
        for key in _TRUE_FAILED:
            if d.get(key) is True:
                evidence.append((FAILED, key))
        for key in _TRUE_CANCELLED:
            if d.get(key) is True and not is_stop:
                evidence.append((CANCELLED, key))
        for key in _TRUE_UNKNOWN:
            if d.get(key) is True:
                evidence.append((UNKNOWN, key))
        # -- error / refusal fields
        for key in ("error", "errors", "refused", "exception"):
            v = d.get(key)
            if not _empty(v) and v is not True:
                evidence.append((FAILED, key))
        qa = d.get("qa")
        if isinstance(qa, dict) and qa.get("passed") is False:
            evidence.append((FAILED, "its own checks did not pass"))
        # -- the troubleshooter's sentence
        out = d.get("outcome")
        if isinstance(out, str) and out.strip():
            low = out.strip().lower()
            for rx, k in _OUTCOME:
                if rx.search(low):
                    if k == SUCCESS:
                        positive = True
                    else:
                        evidence.append((k, out.strip()[:200]))
                    break
            else:
                evidence.append((UNKNOWN, "an outcome that is not recognised"))
        # -- work still going / left over
        if not _empty(d.get("still_running")):
            evidence.append((UNKNOWN, "something is still running"))
        steps = d.get("steps")
        if isinstance(steps, list) and kind != "read":
            bad = [s for s in steps if isinstance(s, dict) and s.get("passed") is False]
            if bad:
                evidence.append((UNKNOWN if any(isinstance(s, dict) and s.get("passed") is True for s in steps) else FAILED,
                                 "a step did not pass its check"))
        # -- the entry's own result key / required flag
        if schema:
            for key in schema.shape:
                if _has(d, key):
                    positive = True
            for key in schema.required_true:
                if d.get(key) is False:
                    evidence.append((FAILED, f"{key} is false"))
        q = d.get("question")
        if isinstance(q, str) and q.strip() and not positive:
            evidence.append((WAITING, "it asked a question"))

    if schema:
        for path, want in schema.post:
            found, got = _dig(layers[0], path)
            if found and got is not want and got != want:
                evidence.append((FAILED, f"{path} is {got!r}, not {want!r}"))
        for key in schema.required_true:
            if not any(d.get(key) is True for d in layers) and not any(k == FAILED for k, _ in evidence):
                evidence.append((UNKNOWN, f"it did not confirm {key}"))

    if evidence:
        kind_out = max((k for k, _ in evidence), key=lambda k: _PRIORITY[k])
        why = next(w for k, w in evidence if k == kind_out)
        reason = _first_reason(layers) if kind_out in (FAILED, WAITING, CANCELLED) else ""
        reason = reason if reason and not why.startswith(reason[:20]) else why
        partial = _passed(layers) or any(isinstance(d.get("backup"), (str, dict)) and d.get("backup") for d in layers)
        if kind_out in (FAILED, CANCELLED) and partial:
            return Verdict(UNKNOWN, reason, True)        # part of it ran before it stopped: the owner must hear that
        return Verdict(kind_out, reason, partial)

    if positive:
        return Verdict(SUCCESS)
    # no evidence either way
    if kind == "read" and schema is not None:
        return Verdict(SUCCESS)                          # a registered read entry: the shape of its data is the result
    if kind == "read" and effect == "none" and any(not _empty(v) for d in layers for v in d.values()):
        return Verdict(SUCCESS)                          # an unregistered entry that only reads and returned something
    return Verdict(UNKNOWN, "its result did not say whether it worked")
