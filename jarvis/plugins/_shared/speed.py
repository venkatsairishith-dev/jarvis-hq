"""Small speed levers that need no new infrastructure (D59, lever e). Each is a pure helper a caller adds to a request it already makes.

  decision_body(body)          thinking OFF for decisions (pick a tool, fill arguments, yes/no): no hidden reasoning tokens to wait for.
                               Local llama.cpp models get chat_template_kwargs.enable_thinking=false (what goal.pick_args already sends);
                               a provider that takes a reasoning-effort switch gets "low" ONLY if the caller says it does (providers differ
                               and a wrong field is a 400, so the list is explicit, never guessed).
  with_prompt_cache(body)      llama-server keeps the evaluated prompt prefix between requests when `cache_prompt` is true: the static system
                               prompt is then read once, not on every turn.
  static_first(static, dynamic) builds the messages with ONE byte-identical system message (persona, tool rules) and every changing piece
                               (time, task, memory) in the user message, because a prefix cache only helps while the prefix does not change.
  PrefixWatch                  records the hash of the static prefix of each request and counts how often it changed: a "static" prompt that
                               changes every turn (a timestamp slipped into it) shows up as churn, with the first differing line.
  run_dag(tasks, run)          runs independent steps AT THE SAME TIME (a web lookup and a model call), but anything that touches the PC runs
                               one at a time (the PC lock allows one task; two UI actions at once would fight over the mouse).

Not here: the request-size diet (D52) and "no second request beside a turn" (D52) are done in the face; `evals/reliability/latency.py` counts
requests per goal so a regression shows up in the gate.
Standard library only.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable

# providers whose chat endpoint accepts {"reasoning_effort": "low"} (verified per provider by the laptop session; D49 lists Groq as using it)
REASONING_EFFORT_PROVIDERS = frozenset({"groq"})


def decision_body(body: dict, *, local: bool, provider: str = "", effort_providers: Iterable[str] = REASONING_EFFORT_PROVIDERS) -> dict:
    """A copy of `body` with thinking turned off for a DECISION request. Never overrides a value the caller already set."""
    out = dict(body)
    if local:
        kw = dict(out.get("chat_template_kwargs") or {})
        kw.setdefault("enable_thinking", False)
        out["chat_template_kwargs"] = kw
    elif provider and provider.lower() in {p.lower() for p in effort_providers}:
        out.setdefault("reasoning_effort", "low")
    return out


def with_prompt_cache(body: dict) -> dict:
    """llama-server: reuse the evaluated prefix of the previous request (`cache_prompt`). Harmless when ignored."""
    out = dict(body)
    out.setdefault("cache_prompt", True)
    return out


def static_first(static_system: str, dynamic_parts: Iterable[str], user_text: str) -> list[dict]:
    """[system(static)], then ONE user message holding the changing context followed by the owner's words."""
    dyn = "\n".join(p.strip() for p in dynamic_parts if p and p.strip())
    return [{"role": "system", "content": static_system},
            {"role": "user", "content": (dyn + "\n\n" if dyn else "") + user_text}]


def prefix_hash(messages: list[dict]) -> str:
    """Hash of the leading system messages (the part a prefix cache can reuse)."""
    h = hashlib.sha256()
    for m in messages:
        if m.get("role") != "system":
            break
        h.update(str(m.get("content", "")).encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


@dataclass
class PrefixWatch:
    """Counts how often the static prefix changes between requests."""
    last_hash: str = ""
    last_text: str = ""
    requests: int = 0
    changes: int = 0
    first_difference: str = ""

    def record(self, messages: list[dict]) -> bool:
        """True when this request's prefix equals the previous one's (a cache hit is possible)."""
        text = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
        h = prefix_hash(messages)
        self.requests += 1
        same = self.last_hash in ("", h)
        if self.last_hash and not same:
            self.changes += 1
            if not self.first_difference:
                a, b = self.last_text.splitlines(), text.splitlines()
                self.first_difference = next((y[:80] for x, y in zip(a, b) if x != y), (b[len(a)][:80] if len(b) > len(a) else "length changed"))
        self.last_hash, self.last_text = h, text
        return same

    @property
    def churn(self) -> float:
        return self.changes / max(1, self.requests - 1)


@dataclass(frozen=True)
class Task:
    id: str
    deps: tuple[str, ...] = ()
    pc: bool = False               # touches the screen, keyboard, mouse or windows: never concurrent with another pc task


async def run_dag(tasks: list[Task], run: Callable[[Task, dict], Awaitable[Any]], *, pc_lock: asyncio.Lock | None = None) -> dict[str, Any]:
    """Run every task after its dependencies. Independent tasks overlap; pc tasks take turns. `run(task, results_so_far)` returns the result.
    A failed task fails its dependents (they are not run: their result is the exception) and the others carry on. Returns {id: result|Exception}."""
    lock = pc_lock or asyncio.Lock()
    by_id = {t.id: t for t in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("duplicate task id")
    for t in tasks:
        for d in t.deps:
            if d not in by_id:
                raise ValueError(f"task {t.id} depends on unknown task {d}")
    results: dict[str, Any] = {}
    futures: dict[str, asyncio.Future] = {}

    async def go(t: Task) -> None:
        for d in t.deps:
            await futures[d]
            if isinstance(results.get(d), BaseException):
                results[t.id] = RuntimeError(f"dependency {d} failed")
                return
        try:
            if t.pc:
                async with lock:
                    results[t.id] = await run(t, results)
            else:
                results[t.id] = await run(t, results)
        except Exception as e:  # noqa: BLE001 - one failed step must not cancel the independent ones
            results[t.id] = e

    loop = asyncio.get_running_loop()
    for t in tasks:
        futures[t.id] = loop.create_future()

    async def wrapper(t: Task) -> None:
        try:
            await go(t)
        finally:
            futures[t.id].set_result(None)

    await asyncio.gather(*(wrapper(t) for t in tasks))
    return results


def cycle_free(tasks: list[Task]) -> bool:
    """True when the dependency graph has no cycle (run_dag would wait forever on one)."""
    deps = {t.id: set(t.deps) for t in tasks}
    done: set[str] = set()
    while deps:
        ready = [k for k, v in deps.items() if v <= done]
        if not ready:
            return False
        for k in ready:
            done.add(k)
            deps.pop(k)
    return True
