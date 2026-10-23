# JARVIS Bridge

Hands a task the face hears (voice or text) to **JARVIS's main agent**, which plans it, uses the right sub-agents and tools,
asks the owner for approval where needed and verifies the result. This plugin adds no agent logic; it is only the door.

```
voice -> the face (hears, understands, speaks)
           -> delegate_task -> JARVIS console 127.0.0.1:8799 -> kernel -> Hermes (main agent)
                                                                 -> task_start (goal + criteria)
                                                                 -> lab_work -> Hermes / OpenCode / goose (sub-agents)
                                                                 -> kernel verification, approvals, audit
```

## Entries
| Entry | Use |
|---|---|
| `delegate_task(task)` | Submit a task. Returns `command_id`. |
| `task_result(command_id?)` | Running, done or failed (latest the face task if no id). |
| `cancel_task(command_id)` | Cancel one command handed over: its answer is ignored and JARVIS is free again, and the lab tasks that command started are stopped too, with their queued or running jobs (the console's explicit `stop_tasks` option; the console's plain "Stop waiting" button does not do this). A task the kernel could not tie to that command is stopped only by Stop all in the JARVIS console. Never the kill switch. Used by main_agent's Stop. |
| `jarvis_status()` | Console and kernel reachable, kill switch, busy. |
| `pending_approvals()` | Read only: how many actions wait for the owner's approval, and what they are (no action parameters). |
| `connector_overview()` | Read only: JARVIS's connectors and whether each works (names and states; no endpoints or credentials). |

## Settings (environment variables)
- `JARVIS_CONSOLE_URL`: default `http://127.0.0.1:8799`. Must be plain http on a loopback address.
- `JARVIS_BRIDGE_SPEAK_REPLY`: default off. When off, `task_result` returns only the state and the details stay in the
  JARVIS console. Turn it on (`1`) only if the model provider you chose in the face may see your private results.

## Safety
- The plugin holds no credential; the JARVIS console holds the owner's token.
- Approvals for risky actions are given in JARVIS (console or kernel), never through the face. `pending_approvals` only
  shows that something is waiting.
- Needs the JARVIS console running (`start-console.ps1`) and JARVIS's agent reachable.
- The console accepts a write (delegate_task, cancel_task) only with its per-launch secret, which it puts in the page it serves. The bridge reads it from `GET /` before its first write and again if the console was restarted. That keeps blind and cross-site requests out; it is not a defence against a program running as you (the PIN remains the boundary for approvals).

## Tests
`uv run pytest plugin/plugins/jarvis_bridge/tests` (uses a fake console; no model is called).
