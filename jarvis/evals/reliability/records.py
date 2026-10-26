"""Plain records shared by the drivers, the checks and the classifier."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Observation:
    """What a driver saw while JARVIS handled one goal. It is only what JARVIS (or the face) SHOWED; the PC is read separately (env.py)."""
    final_text: str = ""                 # the last assistant message the owner would read
    tool_calls: list[str] = field(default_factory=list)
    ask_form: dict | None = None         # an ask form (ask_form.py) seen in a tool result or message
    asked_in_prose: bool = False         # the last message is a question, with no form
    approval_pending: bool = False       # a PIN card was waiting when the run ended
    approval_ids: list[str] = field(default_factory=list)
    approved: bool = False               # the owner approved it during an attended run
    status: str = "done"                 # done | timeout | failed | harness_error
    seconds: float = 0.0
    requests: int = 0                    # model requests made for this goal
    errors: list[str] = field(default_factory=list)
    command_id: str = ""
    harness_error: str = ""              # the driver itself could not run (face not reachable, server down): the run is INVALID, not a failure
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    cause: str = ""                      # a short code for the ranked failure causes when ok is False

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail[:200], "cause": self.cause}


@dataclass
class Baseline:
    """The PC just before the goal was typed: what already existed must not count as an effect."""
    t0: float = 0.0
    windows: set[tuple[int, str]] = field(default_factory=set)
    window_list: list[Any] = field(default_factory=list)
    files: dict[str, str | None] = field(default_factory=dict)     # path -> sha256 recorded when setup wrote it
    bin: set[str] = field(default_factory=set)
    listing: dict[str, set[str]] = field(default_factory=dict)
