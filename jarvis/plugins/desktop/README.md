# Desktop

JARVIS's hands on the Windows PC: websites and YouTube in the owner's existing Chrome profiles, apps through the keyboard,
mouse and accessibility tree, read-only PC checks, and a troubleshooter that backs up, fixes after approval and verifies.
Every action is a JARVIS kernel job: the kernel decides, and consequential actions wait for the owner's PIN in the
JARVIS console.

| Module | What it does |
|---|---|
| `decide.py` | Request -> plan. One simple command ("open notepad", "pause") is planned by rules in milliseconds; anything else goes to the small local model, whose JSON is forced by a grammar. Deterministic code then resolves the account, site and app against facts (Chrome profile registry, known sites, Start-menu apps) and checks every step. The plan is only a proposal. |
| `coordinator.py` | Runs one PC task: each step by the right agent (browser, desktop, troubleshooter, research), then checks the observed result against that step's success criterion. One PC task at a time, at most 600 s, one retry and only for a transient failure. Keeps a task record. |
| `missions.py` | Long goals with several phases ("research X, make slides, open them") as a durable DBOS workflow on SQLite: survives a restart, never repeats a side effect, re-plans at most twice when a check fails, stops on a loop, a budget (20 steps, 30 min) or the owner's word. |
| `skills.py` | Controlled self-improvement: JARVIS may propose new site names and example plans; only the owner promotes them, from the command line. |
| `core.py` | Talks to the JARVIS console's owner route, and `diagnose()`: what went wrong, from the error dialog, event log and crash records, checked against the files they name. |
| `__init__.py` entries | `run_pc_action`, `run_browser_action`, `open_website`, `play_youtube` and `run_troubleshooter` act on the PC, so they hold the same PC-control lock as a task or mission while they run and answer "another PC task is running" when it is busy; read-only entries (`check_pc`, `list_browser_profiles`, `recent_pc_alerts`, `read_window_text`, `find_on_screen`, status, stop) never take it. `read_window_text(app)` and `find_on_screen(app, text)` reach the OCR / vision rung through the kernel's `diag window_ocr` / `window_locate` jobs (unverified on real hardware); the troubleshooter falls back to OCR, labelled "from OCR (less reliable)" and never to apply a fix, when UI Automation gives no text. |
| `monitor.py` | Read-only timer: crashes, low disk, local services that stop answering, stuck tasks. |

## Safety rules
**The decider asks instead of acting** (`decide.validate`, rules applied after the model):
- vague: "research it", "open the other one", "make a pdf" with no topic -> it asks what;
- negated: "don't open youtube" is never planned; if nothing is left it asks what to do instead. A Chrome account the
  owner excluded ("but not in Satish's profile") is never used, even when the model picks it;
- a choice: "open notepad or wordpad" -> "Which one: notepad or wordpad?";
- two different accounts named for one request -> it asks which.

Requests with a correction ("open notepad, no wait, wordpad") never take the rule fast path; the model plans them and
the step the owner took back is dropped. A model field of the wrong type is a problem, not a crash.

**Only a boolean `ok: True` is success.** `{"ok": "false"}`, an HTML error page or a list from an agent is a failed
step. "Done" is reported only for steps whose check passed.

**A step the node only reported is not called checked.** A browser click, type or press, a window action, a key press or a check that
returns no data passes on the node's `ok`, with the uncertainty "reported ok, not observed: ..." unless the node supplied an
observation (the field read back, the window state read back, the foreground window, `typed_chars` equal to the request). The
summary then starts "Done, but not everything was observed". The plan is capped at 4 steps; only the whoami check the code
inserts for a named account may be a fifth, and any other overflow is a question to the owner, never a silent cut.

**A stop names what already happened.** A task stopped part-way says "Stopped." and then lists the checked steps that ran
before the stop (a window opened, a file saved), because those stay on the PC.

**Stop reaches every PC operation, and "stopped" means its worker ended.** Each task, mission, single-action entry and read-only
check is one registered `coordinator.Operation` while it runs (id, kind, its Pc objects, a stop flag, its worker threads, its
lock). `stop_pc_task` sets every flag, cancels every node job (once), denies the approval a Pc waits on (it never runs; the wait
wakes at once) and waits a bounded time for the workers: `stopped_jobs`/`stopped` list only operations whose workers really
ended, the rest are `still_running` (also logged and in the operation's events) and keep their PC lock. The lock is released
exactly once, by the entry or by the last worker, whichever ends last; a cancelled job the node never confirmed keeps it too.
A second task is refused with the holder named (kind, age, last sign of life, "stop requested ... not ended"); a worker with a
live heartbeat but no progress for 120 s becomes `stuck` and is asked to stop. `recover_pc_lock` (owner only) frees such a lock
only if the stop was requested and the kernel confirms every node job it had ended (or the holder's process is gone).

**Pending approvals are denied, not left approvable.** An approval a PC job waits on, or that an operation left pending when it ended
waiting for the owner, is tracked per operation. Stop (`stop_pc_task`) and an operation's cancel deny it through the kernel's owner
route (`approvals_denied` in the answer); `missions.cancel` denies the approval named in the mission's own record. An approval another
agent or the owner created is never touched; one already approved, used or expired is tolerated (nothing to deny); one the console could
not be asked about stays tracked and is reported (`approvals_unreachable`). A denied approval can never be approved or used. If the owner
approved at the very moment of the cancel, the operation's stop flag is checked before the approved action is sent, so it does not run
(the already-approved card then simply expires: a decided approval cannot be taken back).

**The registry is what the widget and the wake word read.** While any operation is registered the plugin rewrites
`plugins/pc_operations.json` every 5 s (`coordinator.publish`): the widget's `pc.busy` is true exactly then, however long the current step
takes (research can be silent for 16 minutes), so a spoken "stop" reaches `main_agent:stop_task` -> `desktop:stop_pc_task`. The file
counts as stale only when it stops being refreshed (the plugin process is gone). The older `activity.json` stays the icon's signal.

**Tasks and missions keep the lock until the node confirms (re-review H1, M7-M9, 2026-10-19).** A task or mission ends with
`coordinator.settle(hold)`, not `release(hold)`: the lock goes to the Operation, which frees it exactly once, when the entry, the
workers and every unconfirmed node job are over (a reaper polls the kernel; capped at 900 s, logged). No further step starts while
a job of the same task is unconfirmed. If the console stops answering while a job is followed, `Pc.wait_job` keeps the job in
`unconfirmed_jobs`, retries with a bounded backoff, and reports it `unconfirmed` (never done, never stopped); the reaper confirms
it when the console is back. "Not stopped" + "a request is about to be sent" is one step under `Pc.gate` and `recover()` reads
under the same gate, so a worker either already sent (its job id is named at once) or can never send. An unreadable live lock file
is "cannot tell" (Busy), never stale. A lock file that cannot be deleted at release is retried, the Hold stays registered, the
operation shows `release_failed` and `recover_pc_lock` removes it; it is never reported released.

**The skill gate:** a proposal may only add sites (new names, public https addresses) and example plans; it cannot change tools,
permissions or prompt rules. Its regression suite must run at least one case, lose no case the active version passes,
and not score lower. A suite of zero cases never passes. Every version is a file `versions/v<N>.json` with its sha256
in `registry.json`; version files are never overwritten, a damaged registry is refused instead of reset, and promote
and rollback are logged in `audit.log`.

**Diagnosis** (`core.diagnose`): a cause is "verified" only when the named file really has the fault; otherwise it is
a hypothesis. A fix is offered only when its text is unique in the file (widened to whole lines if needed).
Wrong-typed inputs are dropped, not trusted. Screen text stays on the laptop unless `JARVIS_SCREEN_TEXT_TO_MODEL=1`.

**Screen and disk text (changed 2026-10-18, review Low "OCR / file-name egress").** `JARVIS_SCREEN_TEXT_TO_MODEL=1` no longer
means "the pattern-scrubbed text goes to the model". It means "the text may be OFFERED to a model after it is cleared": the
secret pass runs first (a private key withholds everything), then the privacy gateway's `/redact` must hide names, places and
private details and its own answer must confirm names and the PII model were active (the research plugin's contract). A gateway
that is down, degraded, old or unreadable, or any error at all, means the text is withheld with the note
`screen text withheld: no verified private model`; the raw text is never the fallback. This applies to `read_window_text` text,
`run_browser_action` page text, the dialog words, library and module names and the window title in the troubleshooter's report
(one helper: `plugin/plugins/_shared/screen_text.py`). Results that carry such text say `screen_derived: true`, so the main agent
re-checks them at its own sinks. A file or folder is never named in anything the model sees: the report says "a .json file"
(the repair job itself still gets the real path), titles, rows, errors, step details and the icon's status line are cleaned of file
names, secrets and e-mail addresses, and an unexpected exception is reported by its class only. Offline owners who want the text
without a gateway have no switch for it: that would be a new, separate decision.

## Fencing, orphaned jobs and escalation (completion A, 2026-10-19)
- Every job request made under a PC lock carries `holder = {token, task, seq}` (token = the lock file's token, `seq` = that holder's
  request counter). Plugin side: `Pc.fence` checks, under the send gate and before EVERY request, that the lock file still names
  this holder; a stale executor sends nothing and is stopped. Kernel side (`Owner.pc`): when it can read the lock file
  (`JARVIS_PC_LOCK`, else `JARVIS_DATA_DIR/plugins/pc_task.lock`) a request whose token is not the file's is refused
  (HTTP 409) and no job is made; a `(token, seq)` seen before gets the job it already made (duplicate delivery). If the kernel runs
  on another machine than the lock file, only the plugin-side fence and the duplicate rule apply.
- A holder that crashed leaves its job ids in its heartbeat file. A new task (`acquire`) and the owner's recovery cancel those
  node jobs and wait (10 s) for the kernel to confirm each ended before the lock changes hands; otherwise they are refused
  ("job(s) ... not confirmed ended"). Job id and heartbeat are written right after the request returns; a crash in that gap loses
  the id (the node's own `fail_orphans` then fails it when the node reports idle).
- The node remembers a cancel that arrives before a job begins (so it starts cancelled) and checks for a cancel before its first
  side effect and between a repair's backup and its write.
- A lock released after 900 s with a job the node never confirmed is recorded as an alert (shown in Stop's and recovery's answers
  and in `pc_operations.json` for an hour). **Owner decision:** the safer alternative is to NOT release in that case (hold until the
  owner runs recover); it is the single line `break` -> keep looping in `Operation._reap` (and dropping `REAP_MAX_S`). Current
  behaviour kept: a console that never returns frees the lock after 15 minutes with the alert.

## Owner commands
```
D:\jarvis\tools\agent-venv\Scripts\python.exe -m plugin.plugins.desktop.skills status|promote v3|rollback
```

## Tests
`python -m pytest plugin/plugins/desktop/tests -q -p no:cacheprovider` (no model, no Windows needed). On Linux run it
in the jarvis-test container:
```
podman run --rm --userns=keep-id -e HOME=/tmp -v ~/hq-jarvis:/src:O jarvis-test sh -c "cd /src && python -m pytest plugin/plugins/desktop/tests -q -p no:cacheprovider"
```


## Learning from what worked (2026-10-22)
`learn.py` finds PC tasks that the model planned, that completed, where **every step passed and was observed on screen** (no "reported ok, not observed"), seen **three times** with the same plan and
only safe-to-teach tools (open app/site/file, play media, window, press key, read-only checks; no typed text, no clicks, no repairs, no research), and **stages** them as examples through the existing skill
gate. Requests that look private (an address, a long number, password/token/key/pin/otp/card ...) or contain a line break are never taught. At most 3 examples per proposal, one proposal a day, never one
already taught or already proposed. It cannot promote: only you do, after the regression gate passed for that exact file.
`python -m plugin.plugins.desktop.skills candidates` lists them; `... propose-mined` stages one version. Tests: `tests/test_learn.py` (23, each rule checked by mutation).


## Where a goal goes (audit 2026-10-25, D51) and the split
**The path a goal took before this change, file by file.** The face's chat model calls the MCP tool `jarvis_do(task)`.
1. `jarvis/bridge/face_tools.py` `FaceTools.do` -> `JarvisClient.delegate` (`jarvis/plugins/jarvis_bridge/client.py`): `POST /api/owner/command {text, source: "face"}` to the console on 127.0.0.1:8799.
2. The console forwards it to the kernel's owner plane: `jarvis/kernel/jarvis_kernel/owner.py` `Owner.command` scrubs it, checks the kill switch and the provider rest, records it (`owner_commands`, audit) and
   hands it to `self.hermes` (the Hermes agent on the SERVER) in `_run`.
3. On the server, Hermes (an online model, through the privacy gateway chain) both REASONS and FORMS every tool call (`pc_action`, ...). A malformed tool-call JSON (HTTP 400), a PIN-gated choice, or no view of the screen
   are failures of that model doing the executor's job.
4. Each tool call comes back to the kernel as a job: `Owner.pc` (`owner.py`) validates it against `jobs.py` (PC_OPS, UI_OPS, BROWSER_OPS, DIAG_OPS, REPAIR_OPS) and policy (D27), and the laptop node (`jarvis/node/jarvis_node`: `ui.py`, `vision.py`, `jobs.py`) only RUNS
   the job and reports what it observed.
So no code on the laptop was ever asked "what is the goal": the local executors below were reachable only by the owner's own `run_pc_task` call or a main_agent plan, never from `jarvis_do`.

**What the laptop already had:** `decide.py` (the small local model turns a request into steps with a grammar, rules resolve the names), `coordinator.py` (the PC lock, runs and verifies each step; `Coordinator.run(request, plan=None)`),
`missions.py` (durable multi-phase), `node/.../vision.py` + `ui.py` (accessibility tree, then OCR, then the local vision model), `plugins/doc_reader` (LightOnOCR), `plugins/main_agent/planner.py` (`parse_plan` against a catalogue).

**The split (`goal.py`, entry `desktop:do_goal`, in the chat tool list as `desktop__do_goal`):**
1. `plan_messages` asks the privacy gateway (`http://127.0.0.1:48950/v1/chat/completions`, header `X-Jarvis-Route: online` so the gateway does not answer it with the local model first) for a JSON plan: at most 6 short steps, each with a goal sentence and a one-line
   success check, naming only executors of the catalogue. The request is messages only: no `tools`, no `tool_choice`, no image, no stream; the gateway redacts it as for every online call. The online model writes no tool name and no argument.
2. `planner.parse_plan` validates the plan against the catalogue (`pc`, `screen_read`, `screen_locate`, `doc_read`, `deliverable_list`, `deliverable_open`, plus `recall` and `tasks` when the face lent them). Arguments the model wrote anyway are dropped.
3. Each step runs in order, locally. `pc`: `decide.decide` (alias of task kind `ui_decide`) makes the exact steps and `DesktopPlugin._run_task` runs them through the coordinator, so the PC lock, verification and the kernel's gate apply as for `run_pc_task`.
   Other executors: a grammar-forced pick of the arguments by the `chat_simple` specialist (a `path` must be one the owner wrote), then the plugin entry (`read_window_text`, `find_on_screen`, `doc_reader:read_document`, `deliverables:*`).
4. A step the kernel gates (`waiting_for_permission`, or an approval message from a helper) stops the goal: the later steps show `not_run`, the answer names the approval, and the owner approves with the PIN and asks again.
   A failed step also stops the goal. The plan's success check is reported as a note next to what the executor observed (`verified`), never in place of it.
5. The model for each kind of work comes from `jarvis/config/specialists.json` (`plugins/_shared/specialists.py`: kind -> alias, fallback alias, max tokens; `reason` = the online provider). A missing file means built-in defaults; a wrong file is an error;
   aliases the llama-swap file does not define yet are listed by `specialists.not_installed()` (today `local-chat`), not failed. Today `ui_decide` is passed to the decider and its fallback is tried when the first does not answer; `doc_reader` and `vision.py` still
   hard-code their alias and a test checks that the table and those constants name the same model.
6. **Fallback:** if the gateway is not ready, the online call fails (HTTP error, 400 bad tool JSON, timeout), or the plan is unusable (not JSON, 0 or more than 6 steps, an executor that does not exist), the local model plans the whole goal alone (`decide.py`, as
   `run_pc_task`) and the result says `planned_by: local_alone` with a `reason`.
7. **Flag:** `plan_online_execute_local` in `[config]` of this plugin's `plugin.toml` (default true); `JARVIS_PLAN_ONLINE_EXECUTE_LOCAL=0` turns it off for a run. Off: step 6 directly, no online request.
Privacy: the goal is secret-scrubbed, then redacted by the gateway. No window text, screenshot or document text goes online: the plan is made once, from the goal, and nothing the executors read is sent back (the answer is built on the laptop). Helper text stays in the
step's `result`, which `screen_text.cleaned` releases only under the screen-text rule; the `summary` never carries it.
Not done here: the server's Hermes and the kernel are unchanged (`jarvis_do` still reaches Hermes for everything that is not a PC, screen or document goal); the chat model picks `desktop__do_goal` for those from its description.


## Write and save, VS Code, solve what is on screen (D54, 2026-10-26)
"A save is part of the request": no PIN for the ordinary steps; the words that gate (pay, send, delete, install, sign in, accept ...) still gate, also inside typed text and file names.
New decider tools (`decide.py`, finished from the owner's words by `composite.py`, so the small model is not relied on): `open_in_vscode`, `type_in_editor`, `save_as`. All three plans fit the 4-step cap.
1. **Notepad:** "open Notepad and type X, save it as NAME in FOLDER" -> `open_app`, `type_text`, `save_as`. A save is added only when the request says to save (or to create a file); a bare ctrl+s from the model is replaced by `save_as`.
   Default folder Documents, default name `JARVIS note <date time>.txt`. Folders: Documents, Desktop, Downloads, outputs, a project of `config/projects.json`, and folders that already exist inside them; anything else is a question, never a job.
2. **VS Code:** "open VS Code in FOLDER, create FILE and type CODE" is planned by rules (`decide.composite_rule`) -> `open_in_vscode` (node: `pc.open_code`), `type_in_editor` (`ui.key ctrl+n`, then `ui.type_focused` = paste, text longer than 3,900 characters in pieces), `save_as`. The saved file is read back from disk: that is the check, because VS Code's editor cannot be read.
3. **Solve what is on screen:** `goal.solve_request` recognises "solve ... on screen ... type ..."; `GoalRunner._solve` runs read (`DesktopPlugin._goal_read_window`: the named app or the front window that is not JARVIS's own and not the editor; accessibility tree, then OCR) -> reason (`screen_text.release_for_request`, then `ask_online` with `X-Jarvis-Route: online`, no tools, no image, answer text only, `CANNOT_SOLVE` = nothing typed) -> type (`_goal_pick_editor`, the coordinator, `save_as` if asked). Gateway or redaction down: stop, nothing sent, nothing typed.
Verification per step (`coordinator.verify`): `save_as` passes only when the node found the file on disk; it fails when the text read back differs from the typed text, and says "reported ok, not observed" for what could not be compared (a window title that did not show the name, an unreadable file). `type_in_editor` is "not observed" in VS Code by design.
Node side: `node/jarvis_node/savefile.py` (folders, names, free name, read-back, Replace guard, VS Code launch) and `winbackend.SAVE_AS` / `TYPE_FOCUSED`. Tests: `tests/test_write_and_save.py` (fake Windows end to end, real kernel policy), `tests/test_solve_on_screen.py` (fake gateway), `node/tests/test_savefile.py`, `kernel/tests/test_save_ops.py`, `_shared/tests/test_release_for_request.py`.

## Any task, the simple-task catalogue and the shared decision engine (D54 / D54b, 2026-10-26)
- **One rule:** the owner's goal covers every ordinary step it needs (kernel effect classes A / B / C; B only with his words; C always the PIN). The grant is computed from HIS goal text only and rides in every job (`Pc.goal`); a dialog's text can only add risk. `drive.py` is the observe - decide - act loop for a task nothing else covers (tool `drive`); results are verified by effect.
- **Catalogue:** `jarvis/config/simple_tasks.json` (read by `simple_tasks.py`; `decide.py` asks it after the fast path). Add a task = add an entry with an existing handler. `python -c "from jarvis.plugins.desktop import simple_tasks as s; print(s.problems())"` lists what is wrong with the file. Tool `simple_task` runs a catalogue task; `coordinator.verify` passes it only on what its handler verified (`facts`), fails it on `expected_missing`.
- **Deleting** follows his words (Recycle Bin by default, permanent only on permanent words, broad cleanup only from the safe list); see D54 in `docs/03_DECISIONS.md`.
- **Shared engine:** `jarvis/plugins/_shared/decider.py` (intent routing, dialog class - raise-only, specialist, folder / file / app resolution). `drive.py` asks it about each dialog before a click; `simple_tasks.plan_for` asks it for a vague folder. Eval: `jarvis/evals/pc_control/eval_decider.py`.
- **Tests (fakes only):** `tests/fakewin.py` (a fake Windows: apps, dialogs, a Recycle Bin, a PowerShell, a scheduler, with the REAL kernel, node file / settings code and plugin agents), `test_simple_tasks.py` (the file and the matching), `test_simple_tasks_run.py` (every task run, the general-rule scenarios, class C, injection), `test_decider_uses.py`, `test_goal_review.py`.

**Rules first (D60, `quick.py`):** a whole message that is ONE plain command ("open notepad", "pause", "volume up", "minimize excel", "open youtube", "play despacito", "open notepad and write hello", "type hello into notepad") is planned by rules (`decide.fast_path` plus a small compound table), checked by `decide.validate` like a model's plan, and run by the same coordinator and kernel jobs, with no online plan and no chat model. "What time is it" and "what is the date" are answered from the clock. Two doors: `do_goal` tries the rules before it asks the online model (`planned_by: rules`), and `quick_command` (called by the face before it asks a chat model; `handled: false` at once for anything else; not in the chat model's tool list). `JARVIS_QUICK_PATH=0` turns the router off. `quick.register_normaliser(fn)` is where the Tenglish verb lexicon plugs in once the translate module exists. A screenshot has a kernel job but no step in this plugin, so it is not a rule yet. The goal's progress (`planning`, `running` step n of m with a fixed executor id, `waiting`) is written to `plugins/goal_progress.json` for the face's HUD line (`face_tools.read_goal_progress`: a state word, two counts, one fixed id, never text).
The plan call is small and deterministic: temperature 0, `max_tokens` 500 (`JARVIS_PLAN_MAX_TOKENS`), and asked once more with 1,500 when the answer comes back empty or cut off.

## What JARVIS did, and Undo (D61)
Every step the plugin runs (a task, a goal, a mission, a single action, a read) is one line in a local, append-only journal (`D:\jarvis\data\journal\`, `jarvis/node/jarvis_node/journal.py`; this plugin's side is `journal_client.py`): the owner's words, a fixed step label, the class (the kernel's effect class: A ordinary and local, B changes something that was there or a setting, C a delete, a repair or held for the PIN), verified / unconfirmed / failed, the time it took, before and after pictures of the one window it acted on (kept 7 days; off with `journal_screenshots = false` in `plugin.toml`), and an undo recipe. `undoer.py` runs a recipe as a kernel job (`op: undo`), only on the owner's explicit request, exactly what he named, and says what was and was not undone; the entry is `undo_steps`. The face's Timeline panel and the chat tool `jarvis_timeline` read it (`jarvis/bridge/timeline.py`). Typed text is never in the journal; a failing or missing journal never stops an action. The file and setting jobs of the D54 tasks are journaled by what they did (one line each, with its undo recipe; `pc()` calls `journal_client.before_job` / `after_job`). Tests: `tests/test_journal_client.py`, `tests/test_journal_effects.py`, `tests/test_undoer.py`; the decision and what the laptop must check on screen: D61 in `docs/03_DECISIONS.md`.


## The browser agent and the free deck generators (D64, 2026-10-26)
"Make a deck / slides / a presentation about X" is a `web_task` step (`decide.deck_rule`, no small model): JARVIS opens a FREE AI presentation website in the owner's own Chrome, uses his EXISTING signed-in session, generates the deck, waits for it by condition, exports it into the quarantine folder (`outputs\downloads\<host>\`), reads the file (python-pptx / pypdf: type, size, slide or page count), copies it to `outputs\deliverables` for the Preview panel and reports only what was read. The built-in deck and report makers (`plugins/deliverables`) are no longer on the owner's surface (chat tools, persona, `research` has no file output); the plugin stays on disk, dormant.
- **Engine** `browser_agent.py` (+ `browser_snapshot.py`, `browser_backend.py`, `browser_planner.py`): observe (accessibility snapshot first, local vision only when a control has no name, page text never to an online model), act (click, fill, select, press, scroll, hover, drag, upload from an allowed folder, tabs, iframes, dialogs, cookie banners by a known list), verify by effect, and a recovery ladder (retry once, other locators, scroll into view, close the overlay, reload, go back, re-plan with the decision engine `web_step`, the next site, then ask the owner with a form that shows what blocked it). CAPTCHA, 2FA, e-mail confirmation, payment and robot walls are NEVER passed: the engine stops, shows the page state and asks the owner to do that one step.
- **Playbooks** `browser_playbooks.py`, `config/site_playbooks/<host>.make_deck.json` (shipped, `verified_live: false` until the laptop has run them) and a learned store beside the plugin data: semantic locators, replay, repair (the old name stays as a synonym, the repair is recorded), `learn` ("learn this site"), no credential or typed secret is ever saved.
- **Flow** `ppt_flow.py` (`DeckFlow`), shortlist `config/ppt_sites.json` (vendor words, source and date read), wiring `web_runner.py`. A site whose terms forbid automation or that blocks it is `excluded` and never tried; the next site on the list is used automatically when one fails or hits its free limit.
- **Grants** (kernel `sitegrants.py`, `config/trusted_sites.json`, console only): sign-in with the existing session, sign-up, accepting terms and downloads are done only on a host the owner granted with a PIN-confirmed card. Without one the flow stops and says what it waits for.
- **Tests** (fakes only: a fake Chrome behind the real kernel and node): `tests/test_browser_agent_core.py`, `test_browser_playbooks.py`, `test_ppt_flow.py`, `test_browser_signup.py`, `test_web_task.py`; node: `node/tests/test_browser_agent_node.py`; kernel: `kernel/tests/test_site_grants.py`. Full design, the shortlist and what is unverified: `docs/03_DECISIONS.md` D64.


## The human-like control loop and the problem ladder (D65)
`pcagent.py` runs ANY desktop task the way a careful person would: look (`perceive.py`: windows and focus, the active window's accessibility tree, OCR and the vision model only when the tree is thin, with the cascade's agreement rule), decide ONE next action (the local model, `drive.ACTS`), act through kernel jobs only (`pcport.py` is the Port that sends them), check the EFFECT (`closed_loop.py`), and remember (a short working memory; `lessons.py`). When something goes wrong it climbs `ladder.py`'s rungs: (a) the wrong or no window, (b) an unexpected dialog (UAC never touched, only class A answered), (c) a missing control, (d) a slow or hung app, (e) typed text that did not land, (f) no effect twice, (g) a loop, (h) a resource problem. It pauses when the owner uses the mouse or keyboard (`humanfeel.py`), keeps a content-free live state (`taskstate.py`, `pc_task_state.json`) that the voice hook (`taskvoice.py`) answers "what are you doing / why did you stop / what went wrong" from, and takes "try another way / skip this step / stop" by voice. `JARVIS_PC_AGENT=0` switches back to the older `drive.Driver` loop. Details, the tests of every rung and what the laptop must verify: D65 in `docs/03_DECISIONS.md`.

| Module | What it does |
|---|---|
| `pcagent.py` | the loop and the rungs; result shape = `drive.Driver.run` plus `problems`, `ask`, `lessons_used` |
| `perceive.py` | `Port`, `Observation`, `observe`, `cascade_read`, `locate` |
| `ladder.py` | recognising a problem; button, key and menu tables; UAC / resource / not-responding patterns |
| `lessons.py` | `(app, problem) -> route that worked`, closed vocabularies, 300 entries / 64 KB |
| `taskstate.py`, `taskvoice.py` | the live state file, the controls, the owner's phrases and the answers |
| `humanfeel.py` | waits by condition, `OwnerWatch`, the pause for the owner |
| `pcport.py` | the real Port: one kernel job per call |

Tests (fake desktop, no Windows): `tests/fake_desktop.py` with `test_pcagent_ladder.py`; the real kernel behind a fake PC: `test_pcagent_wiring.py`; the wiring of the D59 levers: `test_coordinator_wiring.py`, `test_run_pc_action_loop.py`, `test_ocr_entries.py`.
