# Neko 9.5: architecture (2026-10-16)

Everything here runs on Windows (the laptop), except the JARVIS kernel, which runs on the Oracle server hermes-agent-vm. The design rule: **the model proposes, deterministic code checks, the kernel permits, and only observation decides "done".**

## Request flow

```
voice ("Hey Neko", 48945) / chat (N.E.K.O 48911)
        |
   N.E.K.O main agent ── picks a plugin entry ──> desktop plugin (48916)
        |                                              |
        |                       run_pc_task (one request)   start_mission (a goal with phases)
        |                              |                               |
        |                       Coordinator (coordinator.py)   Mission workflow (missions.py, DBOS on SQLite)
        |                              |   \________ same agents, same verify() ________/
        |                       decide.py: local decider (Qwen3-4B-Instruct-2507 via llama-swap 8080)
        |                         -> JSON-schema plan -> validate(): accounts, sites, keys from the owner's words
        |                         -> complete_plan(): measured omissions closed (typing step, account check, open site)
        |                         -> each step gets a post-condition ("post")
        |                              |
        |                       agents: browser / desktop / troubleshooter / research / open_output
        |                              |
        |                       core.Pc -> JARVIS kernel (Oracle) -> approval rules, PIN for consequential actions
        |                              |
        |                       node on the PC (jarvis_node): UIA, Playwright extension in the owner's Chrome, diag, repair
        |                              |
        |                       verify(step, observed result) -> passed / checked / uncertainty -> honest summary
        |
   proactive monitor (monitor.py, plugin timer 60 s) -> push_message to the chat; recent_pc_alerts entry
```

## Components added or changed in this phase

| Piece | File | What it does | Reused project |
|---|---|---|---|
| Plan completion and post-conditions | `plugin/plugins/desktop/decide.py` | Adds what the owner said but the model left out: a typing step, an account check before acting on a named account, "go to SITE" as opening the site. Removes what is wrong: typing the account name into a page, a duplicate inspect before the troubleshooter, a document with no topic (asks instead). Attaches a post-condition to every step. | none (deterministic rules) |
| "Active window" target | `decide.ACTIVE`, `__init__._agents.find_process` | "the error on the screen" means a window whose title reads like an error, else the foreground window; never Neko's own windows | — |
| Preconditions with recovery | `__init__._agents.need_proc` | A step that needs an app window opens the app first and records that as a recovery | — |
| Account check in a new tab | `jarvis/node/jarvis_node/browserext.py` `whoami()` | Reads the signed-in account in a tab of its own, then closes it; the page being worked on is not navigated away | Playwright MCP (extension mode) |
| Long missions | `plugin/plugins/desktop/missions.py` | Durable workflow (resumes after a crash or restart). Side-effect journal: an interrupted unsafe step is never repeated. Re-plans after a failed check (≤ 2). Budgets: 20 steps, 30 min. Loop and ping-pong detection. Cancellation reaches the running PC job. | **DBOS Transact** (MIT) on SQLite |
| Opened-file evidence | `__init__._agents.opened_window` | "Opened" only when a new window showing the file's name appears | — |
| Follow-ups | `coordinator.previous()` | The last task (within 10 min), what it asked and what it verifiably produced, is passed to the decider for "open it" and "use … instead" | — |
| Proactive monitor | `plugin/plugins/desktop/monitor.py` | Crashes (Application log 1000/1002/1026 after a bookmark), disk floors with hysteresis, local services down twice in a row, stuck tasks. One alert per fault, 30-minute quiet period. | pywin32 EvtQuery, psutil, the N.E.K.O SDK's `timer_interval` and `push_message` |
| Decider footprint | `jarvis/local_models/llama-swap.yaml` | Context 4096 → 2048. The owner's own apps hold about 1.2 GB of the 4 GB, so the model spilled into shared memory; 2048 is enough (prompt plus answer is about 1,400 tokens) and spills about 0.5 GB less | llama.cpp / llama-swap |

## Safety properties kept

- Every PC action is still a kernel job. Missions, the monitor and follow-ups add **no** new permission path.
- `open_output` goes through the deliverables plugin, which refuses anything outside its own output folder.
- The monitor only reads: event records (program name only, never the event text), disk, ports and task files.
- Nothing is repeated after an interruption unless repeating it is harmless (open, read, check). Typing, keys, research and repairs stop and ask instead.
- "Done" means the step's check passed on observed state. Otherwise the summary says what failed or what is uncertain.

## Update 2026-10-17 (Linux test server; D30)

The flow above is unchanged. Bugs found by the new benchmark cases were fixed. Each fix has a test that fails on the old code (commits a3ee6647 and 8e29d3e7).

- **`decide.py` asks instead of acting.** These are rules that run after the model. The prompt, the schema and what is sent to the model are unchanged.
  - **Vague target:** "research it" or "the other one" → a question (A-070/071).
  - **A choice:** "open notepad or wordpad" → "Which one: notepad or wordpad?" (A-072).
  - **A negated action:** "don't open youtube" is never planned. If nothing is left, Neko asks what to do instead (A-073).
  - **In-sentence corrections:** they leave the fast path, and the app the owner corrected away is dropped (A-074/075).
  - **A negated account:** "but not in Satish's profile" is never used, even when the model picks it. Neko asks which account (A-076).
  - **The owner's words beat the model's** for the deliverable type and for play or pause (A-083/086).
  - **"write a report on X"** is research, not typing into an app called X (A-084).
  - **Unverified with the owner:** the excluded-account, negated-action and "X or Y" rules now ask instead of acting. The owner should confirm that this is the behaviour he wants.
- **`coordinator.py`.**
  - Only a boolean `ok` of `True` is success (E-049). A result that is not an object is a failed step (E-047).
  - A stop after finished steps says what already happened: "Stopped. Before the stop: …" (L-034).
  - If the newest task record is damaged, "open it" asks. It no longer reaches back to an older task (A-088).
- **`missions.py`.** A planner or re-plan that raises now ends the mission as "failed", with the error class. Finished steps stay on record, and the PC lock is released (D-066). Before, the mission stayed "queued" or "running" for good.
- **Kernel automations.**
  - A cancelled job now settles its run (D-078). Before, the run stayed "fired" and every later slot was skipped as an overlap.
  - File (event) triggers default to overlap "queue", so each file gets its run. "skip" is still available, and the preview then says that such files are skipped.
- **Not verified:** none of this ran on Windows or with the laptop's GPU decider. Offline, all 140 dev/holdout/holdout2 cases went through `decide()` with no model, and no line changed (a3ee6647).
