"""Second builder: one bounded Claude Agent SDK run in its own git worktree (owner exception, expires 2026-10-31).

    D:\\jarvis\\tools\\sdk-venv\\Scripts\\python.exe jarvis\\builder\\run_builder.py --worktree D:\\jarvis\\hq-neko-sdk --task-file TASK.md [--model claude-sonnet-5-5] [--max-budget-usd 5] [--max-turns 40]

Rules enforced here, not left to the model:
- The API key is read from a file under D:\\jarvis-secrets by this process, handed only to the SDK process environment, and scrubbed from
  every line this script prints. It is never put in a prompt.
- Refuses to run after 2026-10-31 (UTC), or when this machine's own recorded spend would pass the ledger cap.
- Only inside the given worktree: file tools outside it, any mention of jarvis-secrets, git push/remote changes, and other-process
  commands (rm -rf, shutdown, ssh, curl to hosts) are denied by a PreToolUse hook.
- Nothing is merged or pushed: the run ends with its edits (and tests it ran) left in the worktree for the reviewer.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import re
import sys
from pathlib import Path

KEY_FILE = Path(r"D:\jarvis-secrets\claude.key")
LEDGER = Path(r"D:\jarvis\outputs\builder-spend.json")
WORKSPACE_ID = "wrkspc_016a2Nvdk5SmQsdF8qYKvTtR"   # not a secret; the key is not workspace-scoped, so every request names it
EXPIRES = dt.date(2026, 10, 31)
LEDGER_CAP_USD = 75.0   # this tool's own recorded spend; the owner's org-wide stop is $90 including spend made elsewhere
ALLOWED_MODELS = {"claude-sonnet-5-5", "claude-haiku-5-5"}   # owner: no Opus
DENY_BASH = re.compile(r"(jarvis-secrets|git\s+(push|remote|config)|\bssh\b|\bscp\b|\bcurl\b|\bwget\b|Invoke-WebRequest|\brm\s+-rf\b|"
                       r"Remove-Item\s+.*-Recurse|\bshutdown\b|\bformat\b|reg\s+(add|delete)|\bsc\s+(stop|delete)|taskkill)", re.I)


def today_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def check_allowed(today: dt.date, model: str, spent: float, budget: float) -> str | None:
    """Return why a run must be refused, or None."""
    if today > EXPIRES:
        return f"owner exception expired on {EXPIRES}"
    if model not in ALLOWED_MODELS:
        return f"model {model!r} not allowed (only {sorted(ALLOWED_MODELS)})"
    if spent + budget > LEDGER_CAP_USD:
        return f"ledger spend {spent:.2f} + this budget {budget:.2f} would pass the cap {LEDGER_CAP_USD:.2f}"
    return None


def read_spent() -> float:
    try:
        return float(sum(r.get("cost_usd", 0.0) for r in json.loads(LEDGER.read_text(encoding="utf-8"))))
    except (OSError, ValueError):
        return 0.0


def record_spend(task: str, model: str, cost: float, turns: int) -> None:
    try:
        rows = json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    rows.append({"when": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "task": task, "model": model,
                 "cost_usd": round(cost, 4), "turns": turns})
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(rows, indent=1), encoding="utf-8")


def deny_reason(tool: str, tool_input: dict, worktree: Path) -> str | None:
    """Pure policy for one tool call: why it must be denied, or None."""
    blob = json.dumps(tool_input, ensure_ascii=False)
    if "jarvis-secrets" in blob.lower().replace("\\\\", "\\"):
        return "the secrets folder is off limits"
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Read", "Glob", "Grep"):
        for k in ("file_path", "path", "notebook_path"):
            p = tool_input.get(k)
            if p:
                try:
                    Path(p).resolve().relative_to(worktree.resolve())
                except ValueError:
                    return f"{k} is outside the worktree"
    if tool == "Bash" and DENY_BASH.search(str(tool_input.get("command", ""))):
        return "that command is not allowed in the builder"
    if tool in ("WebFetch", "WebSearch"):
        return "no web access in the builder"
    return None


def scrub(text: str, key: str) -> str:
    return text.replace(key, "[KEY]") if key else text


async def run(args: argparse.Namespace) -> int:
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, ResultMessage, AssistantMessage, TextBlock, query

    worktree = Path(args.worktree).resolve()
    if not (worktree / ".git").exists():
        print("worktree is not a git worktree:", worktree)
        return 2
    why = check_allowed(today_utc(), args.model, read_spent(), args.max_budget_usd)
    if why:
        print("REFUSED:", why)
        return 3
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        print("REFUSED: empty key file")
        return 3

    async def pre_tool(input_data, tool_use_id, context):   # noqa: ARG001
        reason = deny_reason(input_data.get("tool_name", ""), input_data.get("tool_input") or {}, worktree)
        if reason:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}
        return {}

    options = ClaudeAgentOptions(
        model=args.model, cwd=str(worktree), max_turns=args.max_turns, max_budget_usd=args.max_budget_usd,
        permission_mode="acceptEdits", allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        disallowed_tools=["WebFetch", "WebSearch"], setting_sources=[],
        env={"ANTHROPIC_API_KEY": key, "ANTHROPIC_CUSTOM_HEADERS": f"anthropic-workspace-id: {WORKSPACE_ID}"}, hooks={"PreToolUse": [HookMatcher(hooks=[pre_tool])]},
        system_prompt=("You are a builder working in a git worktree. Make only the change the task describes, keep the repository's style, "
                       "run the stated tests, and finish with a short summary of files changed and test results. Never commit, push or "
                       "touch anything outside the worktree."))
    prompt = Path(args.task_file).read_text(encoding="utf-8")
    cost, turns = 0.0, 0
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, TextBlock):
                    print(scrub(b.text, key))
        elif isinstance(msg, ResultMessage):
            cost, turns = msg.total_cost_usd or 0.0, msg.num_turns
            print(f"--- result: {msg.subtype}, error={msg.is_error}, turns={turns}, cost=${cost:.4f}")
    record_spend(Path(args.task_file).name, args.model, cost, turns)
    print(f"ledger total now ${read_spent():.2f} of ${LEDGER_CAP_USD:.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--worktree", required=True)
    ap.add_argument("--task-file", required=True)
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--max-budget-usd", type=float, default=5.0)
    ap.add_argument("--max-turns", type=int, default=40)
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
