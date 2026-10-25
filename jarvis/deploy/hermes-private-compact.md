# Smaller request for the owner's Hermes on the private lane (2026-10-25, NOT deployed)

**Why.** The owner's console agent (Hermes, private lane) sends 23-31k characters per call. Groq's `openai/gpt-oss-120b`, the only private-safe free provider with room,
takes 8000 tokens for input and answer together; the proxy keeps 1024 for the answer, so a request may carry at most 6976 *estimated* tokens (characters / 4 + 4 per
message). Task c-de74d8ecbb7a sent 31,395 characters (estimate 7,872) and no provider could take it; the owner's command then waited five minutes. Real Groq counts are
about 5 characters per token (a 22,882-character call counted 4,407), so the estimate is ~20% high; the proxy still decides on the estimate.

**Where the characters are** (kernel audit `agent_model_call`, Hermes' stored system prompt and tool list; sizes and counts only):

| part | characters | note |
|---|---|---|
| system prompt | ~7,150 | Hermes' own text ~4,800 (steering, parallel calls, finishing) + `SOUL.md` operator section ~2,300 |
| tools | ~14,000 | 25 tools: 20 from the JARVIS tool server (16 + 4 utility tools Hermes adds for MCP resources/prompts), 4 `jarvis_memory_*`, `todo_list` |
| conversation | 100 per first turn, then +900 to +1,500 per turn | one Hermes session per day, so it only grows |

**What this change does** (all in the repo, none of it on the server yet):

1. `lab/jarvis_mcp.py`: profile `owner-compact`. Same tools, same arguments, same kernel checks; only the ten an owner command used (`task_start`, `task_get`,
   `task_update`, `lab_work`, `list_folder`, `open_folder`, `open_file`, `open_app`, `pc_action`, `save_output`) and a shorter `lab_work` and `task_update` description.
   Left out: `connector_*`, `show_output`, `models_status`, `switch_model` (the console has its own typed model switch). Saves ~3,300 characters.
2. `kernel/telegram.py` + `kernel/llm_proxy.py`: the owner console's Hermes conversation is replaced after 3 turns (`CONSOLE_AGENT_MAX_TURNS`), so history stays under
   ~5,000 characters. Durable facts live in Hermes memory and the kernel's task records, not in the conversation.
3. `evals/private_prompt_size.py` replays the captured sizes. Result (estimated tokens, limit 6,976): 1 turn 5,728 -> 4,647; 2 turns 6,489 -> 5,408; 3 turns 6,809 -> 5,728;
   the c-de74 call 7,872 -> 6,792.

**To deploy (owner decision; every step is reversible):** copy `lab/jarvis_mcp.py` to `/opt/jarvis-mcp/jarvis_mcp.py`, and `kernel/jarvis_kernel/telegram.py` and
`llm_proxy.py` into the kernel (keep a `.bak`); in the owner Hermes `config.yaml`:

```yaml
mcp_servers:
  jarvis:
    env:
      JARVIS_MCP_PROFILE: owner-compact      # was: owner
    tools:
      resources: false                       # drops list_resources / read_resource (Hermes' utility tools): ~560 characters
      prompts: false                         # drops list_prompts / get_prompt: ~500 characters
# optional, a behaviour change: remove `todo` from platform_toolsets.api_server to drop todo_list (~1,300 characters)
```

Restart `jarvis-llm-proxy` and the Hermes API service (no lab job running). Roll back: put the three files and `config.yaml` back and restart. Hermes' own memory tools
(`jarvis_memory_*`, ~1,500 characters) and `SOUL.md` are unchanged; a shorter operator section (~900 characters instead of ~2,300) is possible later.
