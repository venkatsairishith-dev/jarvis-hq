# Main Agent

One task in (voice or text), one answer out. A planner splits the task across the sub-agents that fit, independent steps run
in parallel, and the combined result is spoken back by N.E.K.O.

```
voice -> N.E.K.O -> run_task(task)
                      planner (N.E.K.O's "agent" model) -> plan fixed up front, validated against the live catalogue
                      step kinds:  think    reasoning / summarising / writing from earlier results (LLM only)
                                   plugin   any running N.E.K.O plugin entry (web search, files, music, ...)
                                   browser  N.E.K.O Browser Use
                                   jarvis   JARVIS main agent + its lab sub-agents (code, PC files, long work), via jarvis_bridge
                                   computer N.E.K.O Computer Use (off unless MAIN_AGENT_ALLOW_COMPUTER_USE=1)
                      waves of independent steps in parallel -> one combined message
```

## Entries
| Entry | Use |
|---|---|
| `run_task(task, mode?)` | Plan and run in the background; the result arrives as a spoken message. |
| `plan_task(task, mode?)` | Show the plan only. Runs nothing. |
| `run_status(run_id?)` | Each step, its outcome and whether the run can be resumed. |
| `stop_task()` | Stop after the current step. Finished steps stay finished. |
| `resume_run(run_id?, rerun?)` | Continue an interrupted, held, partial or failed run. Finished steps are kept. A step whose result is unknown and that may change something runs again only if its id is in `rerun`, which the user must confirm. |

`mode` is `work` (default, tools), `chat` (no tools), `code` (JARVIS lab agents in the bound project) or `learn` (study
vault and documents).

## Safety design
- **Plan first, then run.** The plan is made from the user's request only, before any tool output is seen. A web page or a file
  cannot add, change or redirect steps.
- **Closed catalogue.** Only sub-agents that are available right now can appear, with arguments checked against each entry's
  schema: undeclared arguments are dropped, and an argument of the wrong declared JSON type (text where the schema says
  integer, a number where it says string, `true` for an integer) refuses the whole plan. Stopped plugins, this plugin and
  the bridge are not offered as plain plugins.
- **No cycles, bounded work.** A step may depend only on earlier steps; at most `MAIN_AGENT_MAX_STEPS` steps, per-step and
  overall time limits, one run at a time, results capped.
- **Failures stay local.** A failed step skips only what depends on it; the final message says what did not work.
- **Checkpoints.** Each run is saved after every step. After a crash or restart a running run is marked interrupted and
  the step in flight becomes `unknown`; nothing is re-run on its own.
- **Desktop control is opt-in** and still needs N.E.K.O's own Computer Use switch.
- **JARVIS stays the authority** for what it does: its approvals and audit apply, and its detailed results are not returned
  unless `JARVIS_BRIDGE_SPEAK_REPLY=1`.

## Settings (environment variables)
`MAIN_AGENT_MAX_STEPS` (6), `MAIN_AGENT_PARALLEL` (3), `MAIN_AGENT_STEP_TIMEOUT` (240 s), `MAIN_AGENT_TOTAL_TIMEOUT` (600 s),
`MAIN_AGENT_ALLOW_COMPUTER_USE` (off), `MAIN_AGENT_STATE_DIR` (run checkpoints; default
`main_agent_runs` under `NEKO_STORAGE_SELECTED_ROOT`, or under `~/.neko-main-agent`).

## Needs
N.E.K.O's agent API key set (planning and reasoning use the "agent" model), the Agent master switch on, and `jarvis_bridge` for
JARVIS steps.

## Tests
`uv run pytest plugin/plugins/main_agent/tests` (no model, no network).
