r"""Undo, on the owner's explicit request only (D61): "undo that", "undo the last 3", "undo this task", or a click on Undo in the Timeline panel.

It takes journal steps (already chosen by the caller: exactly the ones the owner named, newest first, never more), and for each one runs its recipe as
a kernel job (`op: undo`, the node re-checks and does it: jarvis/node/jarvis_node/undo.py). The kernel's policy applies as to any owner-direct PC action:
no PIN for the ordinary ones, a refusal for secrets and sensitive names, and an approval wait is not entered here (a step that would need the PIN is
reported as such and left alone). Every attempt is journaled as a step of its own (tool `undo`), and a step that was undone is not offered again.

The answer says exactly what was and was not undone, in fixed words (no file names): status per step is
  undone   the recipe ran and its effect was checked          partly   some of a cleanup came back
  already  there was nothing left to do (the window was closed already)
  cannot   the recipe says it cannot be undone (typed text, a permanent delete, caches)
  refused  the node said no (it changed since, the place is taken, unsaved work, protected, ...)
  failed   the kernel or Windows did not do it                approval  it needs the owner's PIN first
"""
from __future__ import annotations

import time
from typing import Any, Callable

from jarvis.node.jarvis_node import journal as J
from jarvis.node.jarvis_node import undo as U

DONE = ("undone", "partly", "already")
JOB_TIMEOUT_S = 60.0


def bodies(recipe: dict) -> list[dict]:
    """The owner-route PC requests that run a recipe (one per thing to put back)."""
    k = (recipe or {}).get("kind")
    base = {"op": "undo"}
    if k == "close_window":
        return [dict(base, action="close_window", target=str(recipe["proc"]), path=str(recipe.get("pid") or 0), expect=str(recipe.get("opened_at") or 0))]
    if k == "recycle_new":
        return [dict(base, action="recycle", target=str(recipe["path"]), path="", expect=str(recipe.get("stamp") or ""))]
    if k == "restore_bin":
        return [dict(base, action="restore_bin", target=str(recipe["path"]), path="", expect="" if recipe.get("size") in (None, "") else str(recipe["size"]))]
    if k == "move_back":
        return [dict(base, action="move", target=str(recipe["dst"]), path=str(recipe["src"]), expect=str(recipe.get("stamp") or ""))]
    if k == "setting":
        return [dict(base, action="setting", target=str(recipe["name"]), path="" if recipe.get("previous") is None else str(recipe["previous"]),
                     expect=str(recipe.get("new") or ""))]
    if k == "cleanup":
        return [dict(base, action="restore_bin", target=str(i["path"]), path="", expect="" if i.get("size") in (None, "") else str(i["size"]))
                for i in recipe.get("items") or [] if isinstance(i, dict) and i.get("path")]
    if k == "moves":                       # a sort: one move back per file, each checked on its own (a file edited since stays where it is)
        return [dict(base, action="move", target=str(i["dst"]), path=str(i["src"]), expect=str(i.get("stamp") or ""))
                for i in reversed(recipe.get("items") or []) if isinstance(i, dict) and i.get("dst") and i.get("src")]
    return []


def _one(pc: Any, body: dict, what: str) -> tuple[str, str]:
    """(status, reason) of one job: done | already | approval | refused:<code> | failed."""
    try:
        res = pc.run(body, JOB_TIMEOUT_S, what)
    except Exception as e:  # noqa: BLE001 - the kernel refusing (secrets, a bank name) arrives as an exception with its own sentence
        msg = str(e)
        if "refused by policy" in msg or "touches a secrets path" in msg or "password manager" in msg:
            return "refused:protected", U.reason_text("protected")
        return "failed", "JARVIS could not reach the PC to do it"
    state = (res or {}).get("state")
    r = (res or {}).get("result") or {}
    if state == "waiting_for_permission":
        return "approval", "it needs your approval in the JARVIS console first (your PIN)"
    if state != "done":
        return "failed", "the PC did not do it (" + str(state or "no answer")[:30] + ")"
    if r.get("ok") is True and r.get("verified") is True:
        return ("already" if r.get("note") == "already closed" else "done"), ""
    code = str(r.get("refused") or "failed")
    return f"refused:{code}", U.reason_text(code)


def undo_step(step: dict, pc: Any) -> dict:
    """Undo one journal step. {id, label, status, reason}. Never raises."""
    recipe = step.get("undo") or {}
    out = {"id": step["id"], "label": step.get("step_label", "a step"), "status": "failed", "reason": ""}
    if step.get("undone"):
        return dict(out, status="already", reason="it was undone before")
    kind = recipe.get("kind")
    if kind == "cannot_undo":
        return dict(out, status="cannot", reason=str(recipe.get("why") or U.reason_text("cannot")))
    todo = bodies(recipe)
    if not todo:
        return dict(out, status="failed", reason="there is no recipe to undo this with")
    results = [_one(pc, b, "undo: " + out["label"]) for b in todo]
    ok = [r for r in results if r[0] in ("done", "already")]
    if kind == "moves":
        left, more = len(results) - len(ok), int(recipe.get("more") or 0)
        extra = f" {more} more files were sorted than were recorded; they stay where they are." if more else ""
        if not left:
            return dict(out, status="undone" if not more else "partly", reason=f"{len(ok)} files are back where they were." + extra)
        why = next((r[1] for r in results if r[0] not in ("done", "already")), "")
        if ok:
            return dict(out, status="partly", reason=f"{len(ok)} of {len(results)} files are back; the rest: {why}." + extra)
        return dict(out, status=("approval" if results[0][0] == "approval" else "refused"), reason=why + extra)
    if kind == "cleanup":
        perm = recipe.get("permanent") or {}
        left = len(results) - len(ok)
        extra = (f" {int(perm.get('count') or 0)} files that were deleted for good (caches) cannot be restored." if perm.get("count") else "") + (
            f" {int(recipe.get('more') or 0)} more items are in the Recycle Bin: open it to restore them." if recipe.get("more") else "")
        if not left:
            return dict(out, status="undone", reason=f"{len(ok)} items are back from the Recycle Bin." + extra)
        if ok:
            why = next((r[1] for r in results if r[0] not in ("done", "already")), "")
            return dict(out, status="partly", reason=f"{len(ok)} of {len(results)} items are back; the rest: {why}." + extra)
        return dict(out, status=("approval" if results[0][0] == "approval" else "refused"), reason=results[0][1] + extra)
    status, reason = results[0]
    if status in ("done", "already"):
        return dict(out, status="undone" if status == "done" else "already", reason=reason or {"close_window": "the window is closed", "recycle_new": "it is in the Recycle Bin",
                                                                                   "restore_bin": "it is back", "move_back": "it is back where it was",
                                                                                   "setting": "the setting is back"}.get(kind, "done"))
    return dict(out, status="approval" if status == "approval" else ("refused" if status.startswith("refused") else "failed"), reason=reason)


def run(steps: list[dict], pc: Any, journal: "J.Journal | None", clock: Callable[[], float] = time.time) -> dict:
    """Undo `steps` (newest first, exactly these) and journal each attempt. The report:
    {ok, undone, not_undone, results: [{id, label, status, reason}], summary}."""
    results = []
    for step in steps:
        t0 = clock()
        r = undo_step(step, pc)
        results.append(r)
        if journal is not None and not step.get("undone"):                          # every attempt is a line of its own
            try:
                detail = {"undone": "undone", "partly": "partly undone", "already": "nothing left to do"}.get(r["status"], r["status"]) + (": " + r["reason"] if r["reason"] else "")
                journal.append(J.make_undo_entry(step, status=r["status"], detail=detail, duration_ms=int((clock() - t0) * 1000), ts=clock()))
            except Exception:  # noqa: BLE001 - the undo happened; a journal that cannot write must not hide it
                pass
    good = [r for r in results if r["status"] in DONE]
    bad = [r for r in results if r["status"] not in DONE]
    if not results:
        summary = "There was nothing to undo."
    else:
        parts = []
        if good:
            parts.append("Undone: " + "; ".join(f"{r['label']}" + (f" ({r['reason']})" if r["status"] == "partly" else "") for r in good) + ".")
        if bad:
            parts.append("Not undone: " + "; ".join(f"{r['label']} ({r['reason'] or r['status']})" for r in bad) + ".")
        summary = " ".join(parts)
    return {"ok": bool(good) and not bad, "undone": len(good), "not_undone": len(bad), "results": results, "summary": summary[:1500]}
