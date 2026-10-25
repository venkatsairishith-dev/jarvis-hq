"""Before/after size of the owner agent's private-lane request (2026-10-25). Local counting only: no model call, no server, no secret.

    python jarvis\\evals\\private_prompt_size.py

Why: the owner's console agent (Hermes, private lane) sends ~23-31k characters per call; Groq's gpt-oss-120b, the only private-safe free provider with room, takes
8000 tokens for input AND answer, and the proxy keeps 1024 for the answer, so a request may carry at most 6976 ESTIMATED tokens (sizing.estimate_tokens: characters / 4
plus 4 per message). Real Groq counts are ~5 characters per token, so the estimate is ~20% high; the proxy still decides on the estimate.

What is measured here, from the code in this repo: the tool schemas the MCP tool server (lab/jarvis_mcp.py) offers in profile "owner" and in "owner-compact", in
the same JSON form the proxy sizes. What is taken from the kernel's audit log on hermes-agent-vm (agent_model_call events of agent "hermes", private lane, Groq,
2026-10-25 08:13-08:50, read-only; the log keeps sizes and counts, never the text): the total characters, message count and Groq's real prompt tokens of real
calls. The "after" rows subtract what the compact profile saves in tool schemas from those totals; a conversation capped at MAX_TURNS also stops growing.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "kernel"))
from jarvis_kernel import sizing  # noqa: E402

MCP_FILE = HERE.parent / "lab" / "jarvis_mcp.py"
GROQ_TOTAL, MIN_OUTPUT = 8000, sizing.MIN_OUTPUT
LIMIT = GROQ_TOTAL - MIN_OUTPUT            # largest estimate the proxy sends to Groq: 6976
# What Hermes sends for each MCP tool is a little larger than the MCP schema measured here. Measured from the tool list Hermes stored on the server (sessions
# state.db, system_prompts, names and sizes only): the 13 tools both lists have, 8243 characters in Hermes' form (an older tool-server version) against 7876 here.
HERMES_FORM_RATIO = 8243 / 7876
UTILITY_TOOLS_CHARS = 1063                 # list_prompts + get_prompt + list_resources + read_resource, as Hermes adds them (tools.resources/prompts: false drops them)
# (label, prompt characters, messages incl. system, Groq's real prompt tokens or None), from the audit log (see above)
CAPTURED = [
    ("fresh conversation, 1 turn", 22882, 2, 4407),
    ("2 turns", 25893, 4, 5190),
    ("3 turns", 27140, 6, 5459),
    ("task c-de74d8ecbb7a (no usage: no provider took it)", 31395, None, None),
]


def tools_json(profile: str) -> tuple[int, int]:
    """(number of tools, characters of the tool list as the proxy sizes it) for an MCP profile, from the repo's jarvis_mcp.py."""
    with tempfile.TemporaryDirectory() as home:
        Path(home, ".jarvis-llm-token").write_text("made-up-not-a-token")
        os.environ.update({"HOME": home, "USERPROFILE": home, "JARVIS_MCP_PROFILE": profile, "JARVIS_ACTIVITY": os.path.join(home, "a.jsonl")})
        spec = importlib.util.spec_from_file_location("jarvis_mcp_" + profile.replace("-", "_"), MCP_FILE)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        listed = asyncio.run(m.mcp.list_tools())
    tools = []
    for t in listed:
        d = t.model_dump(by_alias=True, exclude_none=True)
        tools.append({"type": "function", "function": {"name": "mcp__jarvis__" + d["name"], "description": d.get("description", ""), "parameters": d.get("inputSchema", {})}})
    return len(tools), len(json.dumps(tools, ensure_ascii=False))


def est(chars: int, msgs: int | None) -> int:
    return int(chars / sizing.CHARS_PER_TOKEN) + 4 * (msgs or 6)


def main() -> int:
    n_owner, c_owner = tools_json("owner")
    n_comp, c_comp = tools_json("owner-compact")
    saved_mcp = c_owner - c_comp
    saved = round(saved_mcp * HERMES_FORM_RATIO)
    print(f"tool schemas (MCP form), profile owner: {n_owner} tools, {c_owner} chars;  owner-compact: {n_comp} tools, {c_comp} chars;  saved {saved_mcp}")
    print(f"as Hermes sends them (x{HERMES_FORM_RATIO:.2f}): saved about {saved} chars ({saved // 4} est tokens); with tools.resources/prompts off a further {UTILITY_TOOLS_CHARS}")
    print(f"limit the proxy applies to Groq: estimate <= {LIMIT} (8000 total - {MIN_OUTPUT} kept for the answer)\n")
    print(f"{'request':52} {'chars':>7} {'est':>6} {'real':>6}   {'after: chars':>12} {'est':>6} {'+ utility off':>13} {'est':>6}")
    for label, chars, msgs, real in CAPTURED:
        a = chars - saved
        b = a - UTILITY_TOOLS_CHARS
        print(f"{label:52} {chars:7} {est(chars, msgs):6} {real or '-':>6}   {a:12} {est(a, msgs):6} {b:13} {est(b, msgs):6}"
              f"   {'fits' if est(b, msgs) <= LIMIT else 'TOO BIG'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
