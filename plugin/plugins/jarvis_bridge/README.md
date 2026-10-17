# JARVIS Bridge

Hands a task N.E.K.O hears (voice or text) to **JARVIS's main agent**, which plans it, uses the right sub-agents and tools,
asks the owner for approval where needed and verifies the result. This plugin adds no agent logic; it is only the door.

```
voice -> N.E.K.O (hears, understands, speaks)
           -> delegate_task -> JARVIS console 127.0.0.1:8799 -> kernel -> Hermes (main agent)
                                                                 -> task_start (goal + criteria)
                                                                 -> lab_work -> Hermes / OpenCode / goose (sub-agents)
                                                                 -> kernel verification, approvals, audit
```

## Entries
| Entry | Use |
|---|---|
| `delegate_task(task)` | Submit a task. Returns `command_id`. |
| `task_result(command_id?)` | Running, done or failed (latest N.E.K.O task if no id). |
| `jarvis_status()` | Console and kernel reachable, kill switch, busy. |
| `pending_approvals()` | Read only: how many actions wait for the owner's approval, and what they are (no action parameters). |
| `connector_overview()` | Read only: JARVIS's connectors and whether each works (names and states; no endpoints or credentials). |

## Settings (environment variables)
- `JARVIS_CONSOLE_URL`: default `http://127.0.0.1:8799`. Must be plain http on a loopback address.
- `JARVIS_BRIDGE_SPEAK_REPLY`: default off. When off, `task_result` returns only the state and the details stay in the
  JARVIS console. Turn it on (`1`) only if the model provider you chose in N.E.K.O may see your private results.

## Safety
- The plugin holds no credential; the JARVIS console holds the owner's token.
- Approvals for risky actions are given in JARVIS (console or kernel), never through N.E.K.O. `pending_approvals` only
  shows that something is waiting.
- Needs the JARVIS console running (`start-console.ps1`) and JARVIS's agent reachable.

## Tests
`uv run pytest plugin/plugins/jarvis_bridge/tests` (uses a fake console; no model is called).
