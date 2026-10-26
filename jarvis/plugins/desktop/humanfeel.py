"""The human feel of PC control (D65). Feel, not evasion: JARVIS acts openly, and nothing here hides what it does or tries to look like a person to a website.

  * waits by CONDITION, not by a fixed sleep: look again soon, then less often, until the thing is there or a budget is used up (`wait_for`)
  * a short beat before the first action in a window and between two actions, so the owner can follow what is happening (`beat`)
  * the owner comes first: when he touches the mouse or keyboard, or the foreground window changes because HE changed it, JARVIS pauses and says so
    ("I will wait until you are done") and carries on, from a fresh look at the screen, once he has been still for a few seconds (`OwnerWatch`)
  * a "what I am doing" line for the face's strip (taskstate.py writes it) and an always-available Stop (stop_pc_task, and the voice control in taskstate.py)

The pointer path (a short eased curve for click_at and drag) and the typing pace (small pieces with a beat between them, a cap, one fast paste beyond it) live
in the node's PowerShell (node/jarvis_node/winbackend.py: SMOOTH_MOVE and the TYPE fallback), because that is where the pointer and keys are. This file is the pure
side: time, input readings and decisions, so every rule is tested without a PC. Standard library only.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

BEAT_S = 0.35                     # the pause a person takes between looking and acting
FIRST_POLL_S = 0.25
POLL_GROWTH = 1.6
MAX_POLL_S = 2.0
OWNER_QUIET_MS = 3000             # the owner has to be still this long before JARVIS carries on
OWNER_GRACE_S = 1.2               # input within this time after JARVIS's own action is JARVIS's (synthetic input counts in Windows' idle timer)
CURSOR_SLACK_PX = 12              # the pointer is where JARVIS left it, give or take (DPI rounding, a window that moved under it)
OWNER_WAIT_MAX_S = 180.0          # after this the task asks instead of waiting for ever


def wait_for(check: Callable[[], Any], *, timeout: float, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Any] = time.sleep,
             first: float = FIRST_POLL_S, growth: float = POLL_GROWTH, cap: float = MAX_POLL_S, stop: Callable[[], bool] | None = None) -> tuple[Any, float]:
    """Wait until check() is truthy: poll soon, then a little less often. Returns (what check returned, seconds waited); the value is falsy when the
    time ran out or `stop()` said so. Never waits longer than `timeout`."""
    t0 = clock()
    gap = first
    while True:
        got = check()
        if got:
            return got, round(clock() - t0, 3)
        left = timeout - (clock() - t0)
        if left <= 0 or (stop is not None and stop()):
            return got, round(clock() - t0, 3)
        sleep(min(gap, left))
        gap = min(cap, gap * growth)


def beat(sleep: Callable[[float], Any] = time.sleep, seconds: float = BEAT_S) -> None:
    sleep(seconds)


@dataclass
class OwnerReading:
    idle_ms: int | None = None                    # ms since the last keyboard or mouse input of ANY kind (the node's input_state)
    cursor: tuple[int, int] | None = None
    front: str = ""                               # the foreground window's process, lower case


class OwnerWatch:
    """Decides whether the OWNER is using the PC. JARVIS's own synthetic input also resets Windows' idle timer, so the rule compares the last input with the
    time JARVIS's own last action ended: input that is MORE RECENT than that (by a grace) is the owner's. The pointer is a second witness: JARVIS leaves it where
    its last click put it, so a pointer that has moved far from there while JARVIS was not acting was moved by a person. Both witnesses are needed to be
    wrong at once for JARVIS to mistake its own input for the owner's, and an unknown reading (a PC that does not report idle time) is "not the owner"."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.acted_at: float | None = None
        self.cursor_after: tuple[int, int] | None = None
        self.front_after: str = ""

    def acted(self, reading: OwnerReading | None = None) -> None:
        """Call right after each of JARVIS's own actions (and after it re-read the screen, with that reading)."""
        self.acted_at = self.clock()
        self.cursor_after = reading.cursor if reading is not None else None
        self.front_after = reading.front if reading is not None else ""

    def owner_input(self, r: OwnerReading) -> bool:
        if r.idle_ms is None or self.acted_at is None:
            return False
        since_us = self.clock() - self.acted_at
        recent = r.idle_ms / 1000.0 < since_us - OWNER_GRACE_S           # an input after our action ended
        moved = bool(r.cursor and self.cursor_after and max(abs(r.cursor[0] - self.cursor_after[0]), abs(r.cursor[1] - self.cursor_after[1])) > CURSOR_SLACK_PX)
        return recent or (moved and r.idle_ms < 2500)

    @staticmethod
    def during(first: OwnerReading, second: OwnerReading, seconds_between: float, slack_s: float = 0.05) -> bool:
        """Two readings with NOTHING of JARVIS's in between (it just looked, took a beat, and looked again): if the second one's last input is more recent than the
        gap between them, somebody touched the keyboard or mouse in that gap, and it was not JARVIS. This is the sure test; the single-reading rule above only catches
        an owner who keeps going for longer than the grace."""
        return second.idle_ms is not None and second.idle_ms / 1000.0 < seconds_between - slack_s

    def took_over(self, r: OwnerReading) -> bool:
        """The owner took over: input of his since JARVIS's last action. A foreground change with NO input is the app's own doing (a dialog, a splash screen)
        and belongs to the problem ladder, not to this rule; input alone is enough to wait, because he may be typing into the very window."""
        return self.owner_input(r)

    def quiet(self, r: OwnerReading) -> bool:
        """He has been still long enough for JARVIS to carry on."""
        return r.idle_ms is None or r.idle_ms >= OWNER_QUIET_MS


def wait_for_owner(read: Callable[[], OwnerReading], watch: OwnerWatch, *, say: Callable[[str], Any], clock: Callable[[], float] = time.monotonic,
                   sleep: Callable[[float], Any] = time.sleep, stop: Callable[[], bool] | None = None, max_s: float = OWNER_WAIT_MAX_S) -> str:
    """Pause until the owner is quiet. Returns 'resumed', 'stopped' (Stop arrived) or 'gave_up' (he kept using the PC for max_s: the task asks instead)."""
    say("I will wait until you are done.")
    t0 = clock()
    while True:
        if stop is not None and stop():
            return "stopped"
        r = read()
        if watch.quiet(r):
            watch.acted_at = clock()                  # his idleness is measured from now: JARVIS has not touched anything
            watch.cursor_after, watch.front_after = r.cursor, r.front
            return "resumed"
        if clock() - t0 >= max_s:
            return "gave_up"
        sleep(1.0)
