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
| `run_task(task, mode?, source?)` | Plan and run in the background; the result arrives as a spoken message. A second request while a task is planned or runs gets "busy". `source="widget"` (set by the desktop widget, which stops waiting after a few seconds) also says a failed or stopped plan in the chat. |
| `plan_task(task, mode?)` | Show the plan only. Runs nothing. |
| `run_status(run_id?)` | Each step, its outcome and whether the run can be resumed. |
| `stop_task()` | Stop the task being planned or run: planning is cancelled, no further step starts, and the step in flight is asked to stop (desktop PC tasks via `desktop:stop_pc_task`, JARVIS commands via `jarvis_bridge:cancel_task`; lab tasks JARVIS already started keep running until Stop all in the JARVIS console). Finished steps stay finished; a step stopped part-way that may change something is held. Answers `stopped: false` when nothing runs. This task only: not Stop all, not the kill switch. |
| `resume_run(run_id?, rerun?)` | Continue an interrupted, held, partial or failed run. Finished steps are kept. A step whose result is unknown and that may change something runs again only if its id is in `rerun`, which the user must confirm. |
| `pending_inputs()` | Questions held tasks are waiting on (newest first): run id, task and the question's public form (field names, labels, options; never a value). The chat polls this. |
| `answer_input(run_id, request_id, values)` | Answer one question. Checked against that question (required fields, choices, lengths); stale, expired, repeated or wrong-run answers are refused; then the held step reruns from its checkpoint with the answers. Secret fields stay in memory only, are handed to that one step and dropped. A spoken "the answer is …" from the widget takes the same path (never for secret questions). |
| `cancel_input(run_id, request_id)` | Decline a question; the run is cancelled. |

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
  overall time limits, results capped.
- **One run at a time, for real.** The run is claimed before planning (no await between the check and the claim), and a run
  only ever releases its own claim. While it is planned, `main_agent_runs/active_run.state` says so (the widget, the Neko
  icon and "Hey Neko, stop" read it) and records how it ended if it never started (stopped, or why the plan failed).
- **Memory for planning, only on a local model.** The planner gets a small block from N.E.K.O's memory (`/query_memory`, top 8
  by relevance, last written first among equals, 1,800 characters at most, source and date per entry; disputed, stale and
  archived entries are left out) only when the "agent" model is a verified LOCAL model (`JARVIS_LOCAL_LLM`, llama-swap on
  127.0.0.1:8080). The JARVIS privacy gateway (127.0.0.1:48950) does NOT count: it forwards to Dahl/Groq and does not hide
  names, places, relations or health prose unless `JARVIS_PRIVACY_NAMES=1`. Any other endpoint gets no memory, and the run
  records why; a memory server error means planning without memory. The block sits between unforgeable `MEMORY-DATA` markers,
  every entry is neutralised (look-alike brackets, control characters, code fences), and memory never widens the catalogue.
  Because the planner may copy a memory into a step, every step of such a plan is marked memory-influenced: browser and
  computer steps run only while the agent slot is still a verified local model; `jarvis` / `jarvis_bridge` and `web_search`
  steps fail with "Needs the owner" unless `MAIN_AGENT_MEMORY_TO_EXTERNAL_STEPS=1` (default off); a "think" step is a
  local-only model call.
- **Screen and disk text is marked and re-checked at every sink** (audit 2026-10-18, Low: OCR / file-name egress; helper
  `plugin/plugins/_shared/screen_text.py`). A plugin result that says `screen_derived: true` (OCR, page or window text; the desktop
  plugin sets it) marks its step (`StepResult.screen`), and so does a think or plugin step that took such a result in. A "think"
  step names the result by a token; `_llm` puts the text in at the call, for the endpoint that call goes to: a verified local
  model gets it after the secret pass, any other endpoint (the privacy gateway URL included) only as `/redact` cleared it (names
  and the PII model confirmed ready), else the note `screen text withheld: no verified private model`. A plugin step gets it
  raw only if it acts on this PC and sends nothing on (`SCREEN_LOCAL_PLUGINS`); otherwise cleared or the note. The answer told to
  the owner goes through N.E.K.O's chat model, whose endpoint cannot be verified from here, so it too carries only cleared text.
  Marked text is not written to the run file. Plugin errors are cleaned (file names become "a .xlsx file", secrets and e-mail
  addresses removed) before they become a step error. browser / computer / JARVIS steps carry only their own goal: an earlier
  result is never part of what they are sent, and no result can be put into research's `question`, a web `query` or a JARVIS
  `task` (only content / text / body / message / note / summary take `{{sN}}`). `NEKO_SCREEN_TEXT_TO_MODEL` (off) is the owner's
  switch for all of it: off means no screen text reaches any model, local or not.
- **Failures stay local.** A failed step skips only what depends on it; the final message says what did not work.
- **Checkpoints.** Each run is saved after every step. After a crash or restart a running run is marked interrupted and
  the step in flight becomes `unknown`; nothing is re-run on its own.
- **Desktop control is opt-in** and still needs N.E.K.O's own Computer Use switch. The computer-use step follows its one task
  (`GET /tasks/<id>`) and Stop or a timeout cancels that one task (`POST /tasks/<id>/cancel`); the agent server's `end_all`
  is never used from here, and desktop control never starts while Stop is set. A start that returns no task id is reported
  as one that cannot be followed or stopped from here.
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
