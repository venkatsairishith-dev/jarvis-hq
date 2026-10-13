"""Gated, verified UI control. Every action is: ask kernel -> observe -> act -> observe -> verify -> record.
The controller never decides permissions (the kernel does) and never repeats a failed action unchanged.
"""
import json
from dataclasses import dataclass


class RepeatError(Exception):
    """The same action already failed; change the strategy instead of retrying it."""


class BudgetError(Exception):
    pass


@dataclass
class Result:
    status: str          # refused | error | verified | unverified | mismatch
    detail: str = ""
    level: str = ""
    approval_id: str = ""


class Controller:
    def __init__(self, ask, backend: dict, snap, max_actions: int = 20, tool: str = "ui"):
        """ask(path, obj)->dict talks to the kernel. backend maps op -> fn(target, params). snap(process)->dict.
        tool is the policy tool name ("ui" or "browser"). Once a session has read untrusted content (web pages),
        every later action carries untrusted_session=True so the kernel removes egress tools."""
        self.ask, self.backend, self.snap, self.tool = ask, backend, snap, tool
        self.max_actions, self.used = max_actions, 0
        self.failed = set()
        self.untrusted = False

    @staticmethod
    def _key(op, target, params):
        return json.dumps([op, target, params or {}], sort_keys=True)

    def _action(self, op, target, params):
        return {"tool": self.tool, "op": op, "target": target, "params": params or {}, "untrusted_session": self.untrusted}

    def observe(self, process: str, **kw):
        d = self.ask("/authorize", {"action": self._action("snapshot", process, {})})
        if not d.get("allowed"):
            return Result("refused", d.get("reason", ""), d.get("level", ""))
        snap = self.snap(process, **kw)
        if self.tool == "browser":
            self.untrusted = True   # page text is untrusted input: from now on, egress tools are removed for this session
        return snap

    def request_approval(self, op, target, params=None):
        """Ask the kernel to create a pending approval. A human then approves it at the kernel console."""
        d = self.ask("/request", {"action": self._action(op, target, params)})
        return Result("pending" if d.get("approval_id") else "refused", d.get("reason", ""), d.get("level", ""), d.get("approval_id") or "")

    def do(self, op, target, params=None, approval_id=None, expect=None):
        key = self._key(op, target, params)
        if key in self.failed:
            raise RepeatError("this exact action already failed; choose a different strategy")
        if self.used >= self.max_actions:
            raise BudgetError("action budget used up")
        d = self.ask("/authorize", {"action": self._action(op, target, params), "approval_id": approval_id})
        if not d.get("allowed"):
            return Result("refused", d.get("reason", ""), d.get("level", ""))
        self.used += 1
        before = self.snap(target.split("|")[0]) if expect else None
        try:
            self.backend[op](target, params or {})
        except Exception as e:  # report the kind of failure, never its text (it could contain screen content)
            self.failed.add(key)
            return Result("error", type(e).__name__, d.get("level", ""))
        if not expect:
            return Result("unverified", "no expectation given", d.get("level", ""))
        after = self.snap(target.split("|")[0])
        if self.tool == "browser":
            self.untrusted = True
        if expect(before, after):
            return Result("verified", "", d.get("level", ""))
        self.failed.add(key)
        return Result("mismatch", "state did not change as expected", d.get("level", ""))
