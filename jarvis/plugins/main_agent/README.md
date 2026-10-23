# Main Agent

One task in (voice or text), one answer out. A planner splits the task across the sub-agents that fit, independent steps run
in parallel, and the combined result is spoken back by the face.

```
speech or text -> the face -> main_agent__run_task(task)
                      planner (the "agent" model: JARVIS_AGENT_BASE_URL / JARVIS_AGENT_MODEL) -> plan fixed up front, validated against the live catalogue
                      step kinds:  think    reasoning / summarising / writing from earlier results (LLM only)
                                   plugin   any running JARVIS plugin entry (web search, files, documents, research, ...)
                                   jarvis   JARVIS main agent + its lab sub-agents (code, PC files, browsing, long work), via jarvis_bridge
                      waves of independent steps in parallel -> one combined message
```

Browsing and desktop control are not steps of their own: they go through the `desktop` plugin and `jarvis`, so the JARVIS kernel
decides and the owner approves risky steps with the PIN.

## Entries
| Entry | Use |
|---|---|
| `run_task(task, mode?, source?)` | Plan and run in the background; the result arrives as a spoken message. A second request while a task is planned or runs gets "busy". `source="voice"` (a spoken request, which stops waiting after a few seconds) also says a failed or stopped plan in the chat. |
| `plan_task(task, mode?)` | Show the plan only. Runs nothing. |
| `run_status(run_id?)` | Each step, its outcome and whether the run can be resumed. |
| `stop_task()` | Stop the task being planned or run: planning is cancelled, no further step starts, and the step in flight is asked to stop (desktop PC tasks via `desktop:stop_pc_task`, JARVIS commands via `jarvis_bridge:cancel_task`; lab tasks JARVIS already started keep running until Stop all in the JARVIS console). Finished steps stay finished; a step stopped part-way that may change something is held. Answers `stopped: false` when nothing runs. This task only: not Stop all, not the kill switch. |
| `resume_run(run_id?, rerun?)` | Continue an interrupted, held, partial or failed run. Finished steps are kept. A step whose result is unknown and that may change something runs again only if its id is in `rerun`, which the user must confirm. |
| `pending_inputs()` | Questions held tasks are waiting on (newest first): run id, task and the question's public form (field names, labels, options; never a value). The chat polls this. |
| `answer_input(run_id, request_id, values)` | Answer one question. Checked against that question (required fields, choices, lengths); stale, expired, repeated or wrong-run answers are refused; then the held step reruns from its checkpoint with the answers. Secret fields stay in memory only, are handed to that one step and dropped. A spoken "the answer is …" takes the same path (never for secret questions). |
| `cancel_input(run_id, request_id)` | Decline a question; the run is cancelled. |
| `learning_stats()` | What the agent has learned from its own runs: success rate overall, per plan shape and per helper, the commonest error classes, whether the latest tasks beat the ones before, and helpers that fail more than they work. Counts and classes only. |
| `forget_learning()` | Erase the episode log and its salt (owner-only button with a confirmation). |

`mode` is `work` (default, tools), `chat` (no tools), `code` (JARVIS lab agents in the bound project) or `learn` (study
vault and documents).

## Safety design

**Untrusted content (2026-10-22).** A result is marked untrusted when its source is outside the owner's own data (a desktop-control step, a JARVIS/lab hand-over, any plugin that is not one of the owner's local ones, screen or disk text) or when it was built from an untrusted result (a reasoning step that summarised it). The plan is closed, so such text cannot add steps, and the planner lets an earlier result into only a few arguments (`body`, `content`, `message`, `note`, `summary`, `text`). A plugin step that may change something and takes untrusted text into one of those arguments is **held** ("needs your approval or answer ... would act on content from the web, the screen or another agent ... It would use: ..."). Nothing runs. You approve by resuming the run with that step in `rerun`; the approval is for exactly those arguments, so a changed result holds the step again. Not held: reads, reasoning, and the plugins that only write your own documents (`learn_vault`, `doc_tools`, `deliverables`). `MAIN_AGENT_UNTRUSTED_TO_ACTIONS=1` turns the hold off. Tests: `tests/test_untrusted_content.py`.
- **Plan first, then run.** The plan is made from the user's request only, before any tool output is seen. A web page or a file
  cannot add, change or redirect steps.
- **Closed catalogue.** Only sub-agents that are available right now can appear, with arguments checked against each entry's
  schema: undeclared arguments are dropped, and an argument of the wrong declared JSON type (text where the schema says
  integer, a number where it says string, `true` for an integer) refuses the whole plan. Stopped plugins, this plugin and
  the bridge are not offered as plain plugins.
- **No cycles, bounded work.** A step may depend only on earlier steps; at most `MAIN_AGENT_MAX_STEPS` steps, per-step and
  overall time limits, results capped.
- **One run at a time, for real.** The run is claimed before planning (no await between the check and the claim), and a run
  only ever releases its own claim. While it is planned, `main_agent_runs/active_run.state` says so (status views read it) and records how it ended if it never started (stopped, or why the plan failed).
- **Memory for planning, only on a local model.** The planner gets a small block of recalled memory (a loopback service named by
  `JARVIS_MEMORY_RECALL_URL` that answers `POST /query_memory/<character>`; none is connected by default, and then plans are made
  without memory), top 8 by relevance, last written first among equals, 1,800 characters at most, source and date per entry;
  disputed, stale and archived entries are left out. It is sent only when the "agent" model is a verified LOCAL model
  (`JARVIS_LOCAL_LLM`, llama-swap on 127.0.0.1:8080). The JARVIS privacy gateway (127.0.0.1:48950) does NOT count: it forwards to
  Dahl/Groq and does not hide names, places, relations or health prose unless `JARVIS_PRIVACY_NAMES=1`. Any other endpoint gets
  no memory, and the run records why; a memory server error means planning without memory. The block sits between unforgeable
  `MEMORY-DATA` markers, every entry is neutralised (look-alike brackets, control characters, code fences), and memory never
  widens the catalogue. Because the planner may copy a memory into a step, every step of such a plan is marked
  memory-influenced: `jarvis` / `jarvis_bridge` and `web_search` steps fail with "Needs the owner" unless
  `MAIN_AGENT_MEMORY_TO_EXTERNAL_STEPS=1` (default off); a "think" step is a local-only model call.
- **Screen and disk text is marked and re-checked at every sink** (audit 2026-10-18, Low: OCR / file-name egress; helper
  `jarvis/plugins/_shared/screen_text.py`). A plugin result that says `screen_derived: true` (OCR, page or window text; the desktop
  plugin sets it) marks its step (`StepResult.screen`), and so does a think or plugin step that took such a result in. A "think"
  step names the result by a token; `_llm` puts the text in at the call, for the endpoint that call goes to: a verified local
  model gets it after the secret pass, any other endpoint (the privacy gateway URL included) only as `/redact` cleared it (names
  and the PII model confirmed ready), else the note `screen text withheld: no verified private model`. A plugin step gets it
  raw only if it acts on this PC and sends nothing on (`SCREEN_LOCAL_PLUGINS`); otherwise cleared or the note. The answer told to
  the owner goes through the face's chat model, whose endpoint cannot be verified from here, so it too carries only cleared text.
  Marked text is not written to the run file. Plugin errors are cleaned (file names become "a .xlsx file", secrets and e-mail
  addresses removed) before they become a step error. JARVIS steps carry only their own goal: an earlier
  result is never part of what they are sent, and no result can be put into research's `question`, a web `query` or a JARVIS
  `task` (only content / text / body / message / note / summary take `{{sN}}`). `JARVIS_SCREEN_TEXT_TO_MODEL` (off) is the owner's
  switch for all of it: off means no screen text reaches any model, local or not.
- **Failures stay local.** A failed step skips only what depends on it; the final message says what did not work.
- **Checkpoints.** Each run is saved after every step. After a crash or restart a running run is marked interrupted and
  the step in flight becomes `unknown`; nothing is re-run on its own.
- **JARVIS stays the authority** for what it does: its approvals and audit apply, and its detailed results are not returned
  unless `JARVIS_BRIDGE_SPEAK_REPLY=1`.

## Settings (environment variables)
`MAIN_AGENT_MAX_STEPS` (6), `MAIN_AGENT_PARALLEL` (3), `MAIN_AGENT_STEP_TIMEOUT` (240 s), `MAIN_AGENT_TOTAL_TIMEOUT` (600 s),
`MAIN_AGENT_STATE_DIR` (run checkpoints; default
`main_agent_runs` under `JARVIS_DATA_DIR`, or under `~/.jarvis-main-agent`).

## Needs
the "agent" model set up (`JARVIS_AGENT_MODEL`, and its key in the file named by `JARVIS_AGENT_KEY_FILE`, which the privacy gateway passes on
as the caller's own), and `jarvis_bridge` for JARVIS steps.

## Tests
`python -m pytest jarvis/plugins/main_agent/tests` (no model, no network).


## Learning from use (2026-10-22)
Every finished task adds one line to a private episode log (`main_agent_learning/episodes.jsonl`, next to the run files): the plan SHAPE (agent kinds and
plugin entries), each step's outcome with an error CLASS (rate_limit, no_match, timeout ...), seconds, and whether it asked a question or was held. It stores
**no request text, no goals, no arguments, no results and no error text**: the request is kept only as salted word hashes (digits and e-mail-like words dropped), so it can
say "this looks like that earlier request" but cannot be read back. Before planning, `Episodes.hints()` gives the planner one line with the shapes that worked or failed
for similar requests (shapes of helpers that exist right now, never words), as a preference: `parse_plan` still checks everything against the live catalogue.
Entries older than 180 days or beyond 2,000 are dropped on every write. Design and next stages: `jarvis/docs/reports/SELF_IMPROVEMENT_2026-10-22.md`. Tests: `tests/test_episodes.py`.

## Self-correction (2026-10-22)
After a **definite** failure (the step said it failed; not "unknown", not held, not waiting for you) the agent asks the planner **once** for a different plan, telling it only the failed
plan shape and the error class (never text), and runs that plan. It never does this when: the run was stopped; any step is unknown, held or waiting (it may have happened); a step that
changes something already succeeded (a new plan could do it twice); the failure is one a new plan cannot fix (rate limit, wrong key, a capability that does not exist, the owner);
the new plan has the same shape as the failed one; it already happened for this run (also after a resume); or `MAIN_AGENT_REPLAN=0`. Every step of the new plan passes the same checks as any
other (kernel approvals, the untrusted-content hold, the closed catalogue). The chat says "The first way did not work, so I tried another way." (or "...neither worked."); both attempts are
recorded as episodes, and `learning_stats` counts how often it was tried and rescued. Tests: `tests/test_self_correction.py` (10, each guard checked by mutation).
