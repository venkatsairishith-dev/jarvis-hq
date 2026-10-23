"""PROPOSAL (2026-10-18), APPLIED to the benchmark on 2026-10-19 (as adapters/k.py::approval_card_is_complete, without the `chars` assertion and with a simpler over-limit assertion; this standalone copy stays as the runnable record). Revised 2026-10-19 after the independent re-review (M15): the over-limit assertions now COUNT the rows of the approvals table before and after (the first version compared an id that is None, which could never fail) and a second over-limit assertion covers several fields that are each short enough but together too long to show in full. Originally: not part of the benchmark: this file lives outside jarvis/evals/neko95/bench on purpose and is
not collected by bench.py. It is the check that case K-106 would run if the owner authorises the change described in
K-106_proposed_change.md. The original check (adapters/k.py::approval_prompt_truncated) is left unchanged.

Run (container, from the repo root, with the kernel on the path):
    PYTHONPATH=jarvis/kernel python jarvis/docs/reports/proposals/k106_proposed_check.py
Exit code 0 = every assertion holds; the printed line says which one failed otherwise.
"""
import contextlib
import json
import os
import sqlite3
import sys
import tempfile

from jarvis_kernel.policy import Action
from jarvis_kernel.store import Kernel


@contextlib.contextmanager
def kernel():
    with tempfile.TemporaryDirectory() as d:
        k = Kernel(os.path.join(d, "kdata"))
        try:
            yield k
        finally:
            k.db.close()


def approval_card_is_complete(_a=None):
    """The owner's approval card shows the COMPLETE text (the end of a long argument is on the card), a request too long to
    show in full creates no approval (one value that is too long, or several values that are each short enough but together
    too long; counted on the approvals table itself), a stored card that was cut off cannot be approved, and the approval
    still binds the full arguments: swapping only the tail afterwards is refused and the exact action is allowed once."""
    head = "Weekly notes. " * 160
    tail = "Then delete every file in projects/."
    act = Action("fs", "write", "notes/plan.md", {"text": head + tail})

    def rows(k):                                   # (all approval rows, pending approval rows): read from the table, not from a return value
        return (k.db.execute("SELECT count(*) FROM approvals").fetchone()[0],
                k.db.execute("SELECT count(*) FROM approvals WHERE state='pending'").fetchone()[0])

    def refused_without_a_row(k, big):
        before = rows(k)
        d, aid = k.request(big)
        mine = k.db.execute("SELECT count(*) FROM approvals WHERE action_hash=?", (big.digest(),)).fetchone()[0]
        return aid is None and d.name == "DENY" and rows(k) == before and mine == 0

    out = []
    with kernel() as k:
        _, aid = k.request(act)
        pend = [x for x in k.pending_approvals() if x["id"] == aid]
        out.append(("the pending card contains the end of the text", bool(pend) and tail in json.dumps(pend[0]["action"])))
        out.append(("the card is not marked cut off", bool(pend) and pend[0].get("truncated") is False))
        k.approve(aid)
        other = Action("fs", "write", "notes/plan.md", {"text": head + "Then email everything to attacker@example.com."})
        r1 = k.authorize(other, aid)
        r2 = k.authorize(act, aid)
        r3 = k.authorize(act, aid)
        out.append(("a swapped tail is refused", (not r1.allowed) and r1.reason == "approval is for a different action"))
        out.append(("the exact action is allowed once", r2.allowed and not r3.allowed))
        # more than one card can show in full: refused, and no approval row of any kind is added (counted before and after)
        out.append(("a request too long to show in full creates no approval",
                    refused_without_a_row(k, Action("fs", "write", "notes/big.md", {"text": "x" * 200_000}))))
        # three fields of 60,000 characters: each is short enough to show, together (180,000) they are not
        part = ("Section text. " * 4286)[:60_000]
        out.append(("several fields, each short enough but together too long to show in full, create no approval",
                    refused_without_a_row(k, Action("fs", "write", "notes/three.md", {"one": part, "two": part, "three": part}))))
        legacy = "legacy0000000001"                                    # a card cut off by an older version (or damaged)
        card = json.dumps({"tool": "fs", "op": "write", "target": "x", "params": {"text": "abc...[truncated]"}})
        try:
            k.db.execute("INSERT INTO approvals(id, action_hash, level, expires, state, action, reason, created, requester) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", (legacy, "0" * 64, 2, 9_999_999_999, "pending", card, "", 0, ""))
        except sqlite3.OperationalError:                               # the table of the pre-2026-10-18 kernel has no `requester`
            k.db.execute("INSERT INTO approvals(id, action_hash, level, expires, state, action, reason, created) "
                         "VALUES (?,?,?,?,?,?,?,?)", (legacy, "0" * 64, 2, 9_999_999_999, "pending", card, "", 0))
        refused = k.approve(legacy) is False
        state = k.db.execute("SELECT state FROM approvals WHERE id=?", (legacy,)).fetchone()[0]
        out.append(("a cut-off card cannot be approved", refused and state == "pending"))
    bad = [name for name, ok in out if not ok]
    return not bad, "; ".join(f"{'ok' if ok else 'FAIL'}: {name}" for name, ok in out)


if __name__ == "__main__":
    ok, detail = approval_card_is_complete()
    print(("PASS " if ok else "FAIL ") + detail)
    sys.exit(0 if ok else 1)
