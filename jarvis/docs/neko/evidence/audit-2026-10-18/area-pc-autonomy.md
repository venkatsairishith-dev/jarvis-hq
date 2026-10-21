# Audit: B) computer and application control, C) goal-driven autonomy (missions, outcome tasks, lab)

Repo `/srv/jarvis/home/hq-neko`, commit `88ec49a2` (main, clean). Audit date 2026-10-18. Read-only: nothing in the repo
was changed. Reproduction scripts live outside the repo in `scratchpad/audit/repro/`.

**Environment limits.** This is a Linux test server. It has no Windows, no microphone, no GPU, no owner Chrome profiles,
no local decider model (llama-swap :8080), no kernel on Oracle, no Hermes and no WSL lab box. Every hardware or Windows
claim below was checked only through code and the result files recorded in the repo. "Verified working" is used only
where a recorded live run on real hardware exists **and** the code it ran has not changed since. No row in this area
meets that bar. See section 0.

Evidence classes used:
- **LIVE**: a recorded run on the real laptop, with a result file.
- **INTEG**: an integration test. Real components are wired together; external boundaries are faked.
- **UNIT**: a unit test, mocked or with fake agents.
- **NONE**: no evidence.

---

## 0. Headline: the live evidence predates the current code

- The live PC-control result files are `jarvis/evals/pc_control/results/*-20261016-*.json` and
  `jarvis/evals/neko95/results/mission-20261016-*.json`. Their names carry laptop (IST) times, all on **2026-10-16
  between 12:46 and 18:09 IST**.
- The commits that changed the code those runs exercised all came **after** 18:09 IST on 16 Oct:
  - `60324b11` (16 Oct 09:33 -0400 = 19:03 IST): coordinator, core, browser;
  - `e0817402` (16 Oct 23:05 IST): coordinator +98, decide +99, missions, browserext;
  - `a3ee6647` (17 Oct 05:37 -0400): decide +157, coordinator +28;
  - `175416f5` (17 Oct 05:37 -0400): core +73, `__init__` +30, node repair;
  - `8e29d3e7` (17 Oct 05:54 -0400): missions.
- **No live run has exercised the current decide.py, coordinator.py, missions.py, core.py or browserext.py.** Every
  later claim rests on unit and integration tests on Linux, with fake agents or a fake console.
- **No live mission has ever succeeded.** All 4 recorded missions failed, at 15:57, 16:20, 17:17 and 18:01 IST.
- **There is no live evidence at all** for any troubleshooter run through `run_pc_task`, any research step inside a
  PC task, or any lab code-fix task on an existing project.

---

## 1. Real execution flow (file:line)

### 1a. PC task: "open notepad and type hello"
1. N.E.K.O, then `main_agent`: the planner picks a capability from a closed catalogue (`plugin/plugins/main_agent/planner.py:20-40`, kinds think/plugin/browser/jarvis/computer). A `plugin` step runs `self.plugins.call_entry(step.ref, ...)` (`main_agent/__init__.py:248-249`), here `desktop:run_pc_task`.
2. `run_pc_task` (`plugin/plugins/desktop/__init__.py:636-656`):
   - starts missions on first use (`:639`);
   - builds `Coordinator(self._agents(loop), core.profiles())` (`:641`);
   - runs `c.run(request)` in a thread (`:644`).
3. `Coordinator.run` (`coordinator.py:271-357`):
   - `decide_fn`, which is `with_context`, then `decide.decide` (`coordinator.py:256-262`);
   - `clarify` returns a question (`:278`), `fallback` returns not_understood (`:282`), `answer` returns not_a_pc_task (`:286`), `stop` (`:290`);
   - otherwise it takes the lock (`acquire`, `:297`). Per step: budget check (`:307`), `AGENT[tool]`, `fn(step)` (`:324`), `verify()` (`:327`), at most one retry for transient errors on repeat-safe steps (`:332-336`), then a stop on approval, cancel or failure (`:338-347`);
   - `release` (`:351`), save the record (`:356`), `summary()` (`:370-399`).
4. `decide.decide` (`decide.py:724-745`):
   - stop words (`:729`), `follow_up` (`:731`), `fast_path` (`:735`, rules only);
   - otherwise `ask_model`: a POST to `http://127.0.0.1:8080/v1/chat/completions` (model alias `decider`) with a JSON schema (`:151-166`);
   - then `validate()` (`:387-555`) and `complete_plan()` (`:590-633`). Each step gets a textual `post` (`:553-554`, `POST` `:559-571`).
5. Agents (`__init__.py:406-623`). Every PC action is `pc(body)`, then `core.Pc.run` (`core.py:119-136`):
   - `POST /api/owner/pc` on the console (127.0.0.1:8799, `core.py:40-65`), with header `X-Jarvis-Console: 1`;
   - on `needs_approval`, it polls `/api/owner/state` for up to 300 s (`core.py:89-97`), then re-submits with `approval_id` (`:132`);
   - then it polls `/api/owner/job/<id>` every 0.5 s (`:99-113`).
6. The console's `_owner` proxy (`jarvis/console/jarvis_console/server.py:306-310`) sends the call through `kernel_client` (owner token from a file) to the kernel's `Owner.pc` (`jarvis/kernel/jarvis_kernel/owner.py:468-528`):
   - op mapping (`:478-506`);
   - for ui/browser/diag/repair/media it calls `kernel.request(action_for(..., owner_direct=True))` (`:509-519`), which can return `needs_approval`;
   - `jobs.create(self.node, kind, params, approval_id, owner_direct=True)` (`:520`).
7. Kernel jobs (`jobs.py:197-216`): `_check_params` (`:58-134`), `action_for` (`:137-180`), `kernel.authorize` (`policy.decide`, `policy.py:326-398`), then insert into `jobs`.
8. The laptop node polls `next_for` (`jobs.py:227-239`) and runs `run_job` (`jarvis/node/jarvis_node/jobs.py:229-295`):
   - `pc_open` goes to `pcopen.pc_open` (Start list via `Get-StartApps`, `pcopen.py:107-151`) plus `_verify_open` (new window by hwnd, `jobs.py:200-226`);
   - `ui_action` goes to `winbackend.BACKEND` (UIA via PowerShell; type = FOCUS, then TYPE, then READBACK, `winbackend.py:122-133`);
   - `browser_ext` goes to `browserext.run` (Playwright MCP extension; `open` without the extension is `chrome --profile-directory` + a foreground-window wait, `browserext.py:151-169`);
   - `diag`, `repair` and `media_state` go to their modules.
9. The result goes back through `jobs.finish` (`jobs.py:241-260`): the state is `done` only when `ok is True`. Then `Pc.wait_job`, the agent wrapper (`__init__.py:411-425`) and `coordinator.verify` (`coordinator.py:159-208`).

### 1b. Mission: `start_mission(goal)`
- `start_mission` (`__init__.py:101-107`) calls `missions.start` (`missions.py:244-250`, which runs `DBOS.start_workflow(run_mission)`).
- `run_mission` (`missions.py:137-219`):
  - `_plan` is a DBOS step that calls `E.plan_fn`, which is `decide.decide(...)`; any status other than `ok` becomes `[]` (`__init__.py:79-80`);
  - it takes the coordinator lock with a **30-min budget** (`:155`);
  - each step runs through `_act`, a DBOS step with an effects journal keyed `mid:sig:occurrence` (`:99-119`), and then `coord.verify`;
  - loop detection (`:127-133`); at most 2 re-plans (`:195-211`); budgets of 20 steps and 30 min (`:167-172`).
- Cancel (`missions.py:253-262`) sets a flag and calls `E.on_cancel`, which sets `stopped` on the active `Pc`.

### 1c. Troubleshooter step
- The `troubleshoot` agent (`__init__.py:557-568`) calls `find_process`, then `run_troubleshooter` (`:320-403`).
- `run_troubleshooter` makes these node calls: `diag window_text`, `diag app_errors`, `diag crashes`.
- It then runs `core.diagnose` (`core.py:194-252`, local regex checks):
  - JSON typo in a file the dialog names;
  - missing DLL or Python module;
  - port in use, disk full, refused local port.
- If a fix exists, it calls `repair edit_file` (YELLOW_APPROVAL; the PIN wait happens inside `Pc.run`), restarts the app and re-reads the window. Success is "a main window and no dialog" (`:383-391`).

### 1d. Outcome task and lab (the Hermes path)
1. `main_agent` `jarvis` step (`main_agent/__init__.py:271-283`), then `jarvis_bridge:delegate_task` (`jarvis_bridge/__init__.py:90-99`), then console `POST /api/owner/command`.
2. Then kernel `Owner.command` (`owner.py:139-168`). It needs `self.hermes`; otherwise it returns 503 "no Hermes API key on this server". `_run` calls `self.hermes(STATE_RULE + text)` and appends `_facts()`, the kernel record of what actually ran (`owner.py:170-190, 230-252`).
3. Hermes (remote, on the Oracle VM) calls kernel agent actions (`llm_proxy.py:301-...`):
   - `task create` (`:540-555`) says "Hands-on work ... goes to the lab ... You cannot run or test anything yourself";
   - `task dispatch`, which is `dispatch_to_lab` (`:499-538`). In the private lane it requires `public_ok=true`. It scrubs the text and **refuses any owner path** (`OWNER_PATH`, `:38`, `:511-514`). Only one active dispatch per task is allowed.
4. Lab runner (`jarvis/lab/lab_runner.py`, root inside the **WSL lab box on the laptop**, `ENDPOINT` at `:28`):
   - `handle` (`:345-425`) runs the worker (`hermes`, `opencode` or `goose`) as user `agent` in an **empty workspace** `/home/agent/work/jobs/<task>`;
   - then a verifier session (also `hermes`) writes `VERIFY.json`;
   - the runner adds its own checks: deliverables rules (`:173-205`), `portable_check` (runs `python3 -m unittest` on delivered `test*.py` in a fresh folder, `:208-233`) and a toolchain tamper guard;
   - repair attempts follow, up to `max_attempts`;
   - delivery goes through `/runner/deliver`, which runs `output_save` jobs to `D:\jarvis\outputs\task-<id>\` (flat; `.py .md .txt .html .json .csv .log` only; at most 10 files).
5. Completion:
   - the runner reports `completed` itself after `verdict.passed`;
   - an agent may set `completed` only with `verification.passed` or by citing `job:<id>` of **any** done job it owns (`llm_proxy.py:572-579`).

### 1e. `project_check` (allowlisted test run)
- Kernel kind `project_check` maps to policy `tests.run`, which is GREEN (`jobs.py:29`, `policy.py:25`).
- It can be started only by:
  - the owner plane, `Owner.pc` op `project_check` (`owner.py:485-486`): the console API or a UI button;
  - an automation (`automations.py:31`, UI preset `jarvis/ui/src/screens/Automations.tsx:9`);
  - Telegram `/check` (`telegram.py:313`). **Telegram is off.**
- **No agent action** (`llm_proxy.agent_action`) and **no plugin entry** (desktop, main_agent, jarvis_bridge) can start it.
- The node runs `project_check` (`jarvis/node/jarvis_node/jobs.py:38-88`):
  - only projects in `jarvis/config/projects.json`. The only entry is `jarvis-hq` at `D:\jarvis\hq`, Windows path only;
  - `git rev-parse/status/rev-list/log` plus the configured test command, with secret-looking env vars removed;
  - the result is a summary plus a 5-line tail. `report_text` ends with "I changed nothing myself; any fix needs your approval." (`kernel jobs.py:352-370`).

### 1f. The requested scenario: "Inspect my project, identify why the build is failing, fix the cause, run the tests, and summarize the verified result."

| Hop | What really happens | Where it breaks or what it needs |
|---|---|---|
| N.E.K.O → main_agent | An LLM planner picks `desktop:run_pc_task` (work mode) or a `jarvis` step (code mode, "JARVIS lab agents in the bound project", `planner.py:30,38`) | No code binds a project ("bound project" appears only in prompt strings; grep finds no implementation) |
| → `run_pc_task` | `fast_path` returns None (the request has "and" and commas). The decider model is required. The model is **not** available here: `decide` returns `fallback`, "the local decision model did not answer (URLError)" (R4). | Needs the laptop and llama-swap |
| decider tool set | The 14 tools (R4) have **no build, test, code-edit or project tool**. Plausible model outputs and what `validate()` makes of them: `troubleshoot app=project` → ok; `troubleshoot app=""` → "Which app?"; `research` → web research; `answer` → "not a PC task" (R4) | Capability **missing** |
| troubleshoot | `find_process("project")` takes the first window whose title contains "project" (for example VS Code). The troubleshooter reads Windows error dialogs, the event log and WER crashes. `diagnose` knows only JSON typos, DLLs, Python modules, ports, disk and refused ports. A compiler or test failure is in none of these, so the result is "no error dialog, crash record or error event": a hypothesis, the step is not passed, and the task status is `failed` | It cannot run a build or tests, cannot read build output, cannot edit source code |
| → `jarvis` step / Hermes | `delegate_task` → `Owner.command` needs Hermes reachable (503 otherwise). Hermes has **no** project_check or test-running action. It can create a task and dispatch it to the lab. | Needs the Oracle VM (kernel, Hermes) |
| lab | The lab **cannot see the owner's project**. Owner paths are refused at dispatch. The workspace is empty. The private code would go to public-lane models only with `public_ok=true`. The lab could only clone a repo that its `gh` login can read; that is the caretaker pattern, which **has never run** (`jarvis/caretaker/history.jsonl` is empty). | Needs the WSL lab box on the laptop, network access and repo credentials |
| fix → project | Verified files are delivered **flat** to `D:\jarvis\outputs\task-<id>\`. They are not applied to the project. No code path patches the owner's working tree. | **Missing** |
| run the tests | `project_check` runs the allowlisted project's tests read-only, but only from the owner plane or an automation, and only for `jarvis-hq`. The lab's `portable_check` runs `python3 -m unittest` on delivered files only. | Partial, and not chained to the fix |
| verified summary | The kernel's `_facts()` appends what really ran ("NOT verified yet" when not verified). The coordinator summary is honest. | Works as an honesty guard, not as a capability |

**Verdict: Missing end to end.** The pieces exist separately:
- read-only test run for one allowlisted project (owner plane);
- lab code work on lab-local copies with LLM verification;
- the caretaker PR template for JARVIS's own tools (never run).

No path inspects an arbitrary owner project, fixes the cause in place, re-runs that project's tests and reports a verified
result. The honest behaviour expected today: the PC path fails or asks, and the Hermes path produces a lab task that
cannot see the project.

---

## 2. Capability truth table

| Capability | Status | Files and symbols | Evidence (test names or result files, dates) | Limitations | What remains unverified |
|---|---|---|---|---|---|
| Entry `run_pc_task` (request → plan → steps → verified summary) | Implemented, unverified (current code) | `desktop/__init__.py:636`, `coordinator.Coordinator.run` | LIVE (old code): `pc_control/results/coordinator-20261016-180855.json`, 12/12 G-cases (open site in a profile, notepad open/type/minimize/restore, disk, play, media, pause, ambiguous account, non-PC, whoami). UNIT: `desktop/tests/test_coordinator.py` (29 tests). Bench B (33 pass, 5 deferred), fake agents. | At most 4 steps per the docs, but 5 in practice (D-8); 600 s budget; no resume after an approval timeout (D-6) | Any live run on code after 16 Oct 18:09 IST (e0817402, a3ee6647, 175416f5) |
| Decider (local model + validate + complete_plan + clarification rules) | Partially working | `decide.py` `decide`, `validate`, `complete_plan`, `fast_path`, `follow_up`, `_choice`, `_corrected_away`, `_negated_step`, `accounts_clash` | LIVE decider accuracy (old code): `pc_control/results/decision-20261016-*.json`; progress report "95/95, dev 69/70, holdout 30/30" (16 Oct 16:38). UNIT/INTEG: bench A, 95 pass (real rules; model answers are recorded or hand-written, `adapters/a.py:1-10`); `test_decide_rules.py` (18). | The model only runs on the laptop. Plan p50 of 3.9–4.9 s misses the 3.5 s target. No tool for code, build or tests. | Accuracy of the current rules with the real model (157 lines of decide.py changed in a3ee6647 after the last live eval) |
| Clarification (ambiguous account, X-or-Y, negation, vague target, document with no topic) | Implemented, unverified live | `decide.py:396-441, 399, 404-406, 423-426, 545-552` | LIVE (old code): G10 "open my venkat account" asked (180855). UNIT: bench A cases, test_decide_rules. | In missions a clarification is lost and becomes "no plan for this goal" (D-5) | Live behaviour of the new rules |
| Coordinator lock, one PC task at a time | Partially working | `coordinator.acquire/_stale/_take_over/release` | UNIT: bench B lock_single, lock_race (8 threads), lock_stale, lock_released_after_crash; `test_coordinator.py` | The lock is not refreshed. Staleness uses the **caller's** budget, so a single task takes over a running mission's lock after 660 s (D-2, reproduced) | Windows `_alive` path (only the Linux psutil path is tested here) |
| Retries (one, transient errors, repeat-safe steps only) | Implemented, unverified live | `coordinator.py:36-45, 332-336` | UNIT: bench B retry_policy | none known | live |
| Post-condition verification | Partially working | `coordinator.verify:159-208`; node `_verify_open` (`node jobs.py:200-226`); winbackend READBACK and WINDOW_STATE; browserext `play`/`whoami` | LIVE (old code): open_app "a window for Notepad appeared", type read back, play "same id, position moving", whoami match. UNIT: bench B verify_step. | `verify()` passes browser click/type/press, window, press_key, check_pc and other tools on `ok: True` alone (R6). `open_website` "verified=window" means "any foreground Chrome window within 8 s", not the profile or URL (D-7). Research and open_output "window opened" checks can never pass (D-1). | Every check on current code, live |
| App discovery (Windows Start list) | Implemented, unverified (current); LIVE on old code | `node/pcopen.py:start_apps, find_app, pc_open` | LIVE (old code): `desktop-20261016-155621.json` B01 (launch allowlist), G02 open notepad. INTEG/UNIT: kernel tests use fake app lists (`apps=`) | Fuzzy match threshold 0.75; uninstallers excluded; SENSITIVE regex refuses names containing "bank", "wallet", "password" and similar | Real `Get-StartApps` across many installed apps |
| UIA actions (click, type, key, focus, scroll, min/max/restore, click_at) | Implemented, unverified (current); LIVE on old code | `node/winbackend.py` FIND/CLICK/TYPE/READBACK/WINDOW_STATE/PICK | LIVE: `desktop-20261016-155621.json` 21/21 (B01–B17, B19–B21; B18 "password fields never typed" not tested); `pc_control/results` W-uwp-app per progress report | Password fields refused via `IsPassword`; key syntax restricted; the UWP frame-title fallback refuses when two frames match | B18 live; bench B-034..B-038 deferred (`bench-20261017-220721.json`) |
| Chrome profile resolution (registry, aliases, masked accounts) | Partially working | `decide.resolve_account/account_from_text`, `node/browserext.resolve/build_registry`, `core.profiles` | LIVE (old code): G01, G10, G12 (180855). INTEG: `neko95/results/live-profiles-20261016-165202.json` 12/12 (headless Chrome, **4 fake profiles** on Linux) | The plugin and node keep two alias resolvers (decide.py's and browserext's, slightly different rules) | The real owner registry on current code |
| Playwright MCP extension (read, find, click, type, press, play, whoami, tabs) | Partially working | `node/browserext.py` `Session`, `act`, `play`, `whoami`, `ALLOWED_TOOLS` | LIVE (old code): G07 play (180855); `browser-20261016-144244.json` 19/19 on an isolated test page. Progress report: "C5 browser 6.0 (no saved Chrome token)" | Each profile needs its saved extension token (owner action). The password refusal is skipped when the target is an element ref (D-3). | Unattended connection; current code |
| Refusal lists (banking, wallets, password managers) | Partially working | `policy.SENSITIVE_APPS:86-87`, `policy.decide:352-353`; `node/jobs.SENSITIVE_PROC:164`; `pcopen.SENSITIVE:27-28` | Probe (section 5, P1): paypal, bankofamerica, netbanking.hdfcbank, bitwarden and 1password are DENY; **icicibank.com, chase.com, onlinesbi.sbi are GREEN** to open. UNIT: kernel policy tests; bench K | Name or regex based. `\bbank` misses "icicibank" and "hdfcbank" (www), and banks without the word (Chase, SBI) | Any curated domain list |
| Approvals (PIN, single use, bound to the action) | Implemented, unverified live (PC path) | `owner.approve:265-303`, `store.request/authorize` (APPROVAL_TTL 600 s), `core.Pc.wait_approval` (300 s) | UNIT/INTEG: bench B pc_approval (FakeConsole); bench N-001 (16 threads, one allowed), N-006 (PIN brute force); kernel tests | After 300 s the task ends with `waiting_for_permission`; `run_pc_task` takes no approval_id, so the remaining steps cannot resume (D-6) | Live PIN flow on current code |
| Troubleshooter (diagnose → backup → approved fix → restart → verify) | Partially working | `__init__.run_troubleshooter:320-403`; `core.diagnose:194-252`, `_system_causes`, `_number_typo_fix`, `_unique_edit`; `node/repair.py` | LIVE (old code): `diagnosis-20261016-155827.json` 17/17 (D01–D14 + T-port/T-disk/T-service on Neko Sample App). INTEG: bench E 56 pass (real diagnose on fixture files, real listeners and disk; fake console runs the real repair code). E-032..E-034 deferred | Only fixed fault classes (JSON number typo, port conflict via a JSON "port", missing DLL or module, disk, service down). It cannot handle build or test failures. | Through `run_pc_task` (no live troubleshoot step in any coordinator run); current core.py (+73 lines in 175416f5) |
| Monitor (crash, disk, service, stuck task) | Implemented, unverified (current) | `desktop/monitor.py`, `__init__._monitor_tick` | LIVE (old code): `neko95/results/monitor-20261016-151927.json` (crash after 47 s, disk, service, stuck). UNIT: `test_monitor.py` (5) | Stuck-task alerts cover single PC tasks only; missions do not write pc_tasks records | live on current code |
| Skills gate (proposals, regression gate, promote/rollback) | Implemented, unverified (current) | `desktop/skills.py` | LIVE (old code): `gate-20261016-164144.json` (refused a regressing proposal). UNIT: `test_skills.py` (13) | Promotion is CLI only | current code (cfc65bf9 changed skills.py by +164 after the live gate) |
| Missions: DBOS workflow, resume, effects journal | Mocked/simulated only (success path); live failed | `missions.py run_mission/_act/_loop/cancel`; `__init__._start_missions` | LIVE: **4 runs, 4 failures** (`neko95/results/mission-20261016-155724/162004/171703/180106.json`): "Event loop is closed" loop-stop; research timeout of 900 s (twice); and at 18:01 "presentation made but no window showing it appeared" plus QA failure. UNIT/INTEG: `test_missions.py` (14), including `test_resume_after_restart_skips_done_steps` (real killed child process, fake agents); bench D 82 pass; D-043 live mission deferred | Waiting for approval means "start the mission again", which is a new mission id with no journal, so done steps repeat (D-6). A policy refusal is treated as a failure and triggers re-plans (D-9). A clarification is lost (D-5). | Any successful mission on hardware |
| Mission budgets, re-plan, loop, cancel | Mocked/simulated only | `missions.py:30-33, 127-133, 167-211, 253-262` | UNIT: test_missions `test_replans_are_bounded`, loop tests; bench D | The 30-min mission budget is not honoured by the shared lock (D-2) | live |
| Kernel jobs (kinds, params, policy, kill switch, cancel, sweep) | Implemented, unverified live (INTEG on Linux) | `kernel/jobs.py` | INTEG: kernel `tests/test_jobs.py` Queue, Http (real kernel DB); bench N (73 pass, real store and audit) | none in scope | Kernel on Oracle with the real node |
| `project_check` (allowlisted git + tests, read-only) | Implemented, unverified on Windows (INTEG on Linux) | node `jobs.project_check`; kernel `jobs.report_text`; `config/projects.json` | INTEG: `tests.test_jobs.NodeRunner` (9 tests: real git repo, real unittest run, env scrub), run here: **OK** (section 5). No live result file for `jarvis-hq` found. | One project (`jarvis-hq`, nt path only). Owner plane or automation only; unreachable from N.E.K.O, main_agent, desktop or Hermes. The summary regex is unittest-specific (pytest output: last line only). | A real run on the laptop |
| Outcome tasks (states, transitions, budgets, checkpoint) | Implemented, unverified live (INTEG) | `kernel/tasks.py` | INTEG: kernel `tests/test_tasks.py`; bench D (automations, runner/kernel) | Agent completion proof can be any unrelated done job (D-4) | live with Hermes |
| `dispatch_to_lab` (scrub, public_ok, no owner paths, one active dispatch) | Implemented, unverified live (INTEG) | `llm_proxy.dispatch_to_lab:499-538` | INTEG: kernel tests; bench D-072..D-075 (lab job close, kill switch) | Lab sees no owner files by design | live |
| Lab runner (worker → independent verifier → repair → deliver) | Partially working | `jarvis/lab/lab_runner.py handle/deliverables/portable_check/deliver/guard_restore` | LIVE (one, older): `jarvis/evals/evidence/trace-9f98013ab16f.json`, a green-field "wordstat" tool, completed and verified after 1 attempt, 4 files delivered. Created at epoch 1791638231 (≈ 2026-10-10). INTEG: `lab/tests/test_repair_loop.py` (real runner, kernel and node delivery; scripted worker; needs the WSL lab box and user `agent`, so it cannot run here); `test_lab_runner.py` (12) | The verifier is an LLM (hermes) session in the same workspace. `portable_check` uses `python3 -m unittest` only (pytest-style tests give "no tests"). Delivery is flat, text files only, at most 10. Fixes are never applied to an owner project. | Lab code on current commits; fixing an existing failing project |
| Caretaker → lab → PR | Implemented, unverified | `jarvis/caretaker/*`, `policy.caretaker_problems` | `history.jsonl` is empty (no PR ever made) | Needs `gh auth` in the lab | everything live |
| Build-failure scenario (inspect → diagnose → fix → test → verified summary) | Missing | (none) | none | see 1f | - |
| Control "ladder" (API → DOM → CLI → OS API → UIA → vision → pixels) | Partially working (no automatic escalation) | `node/ui.py`, `browser.py`, `vision.py`, `winbackend.click_at`; `diag window_ocr/window_locate` | LIVE (old): W-uwp-app per progress report | The coordinator never escalates between rungs. The desktop plugin's `check_pc` enum leaves out `window_ocr`/`window_locate`, so vision is unreachable from `run_pc_task`. | Vision rung end to end |

---

## 3. Confirmed defects

| ID | Severity | Status | Defect | Evidence and reproduction |
|---|---|---|---|---|
| D-1 | **High** | Confirmed (code + LIVE symptom) | "A new window showing the file appeared" can never be observed. The node's `ui_windows` strips `hwnd` from each window (`node/jarvis_node/jobs.py:161`, keys title/process/foreground/minimized/w/h). The plugin's `research` and `open_output` agents build `before = {w.get("hwnd")}`, which is `{None}`, and test `w.get("hwnd") not in before` (`desktop/__init__.py:572, 589, 599`). That is always False, so `opened_verified` is always False. Every "research … make slides and open them" step fails `verify` (`coordinator.py:204-205`), and every "open it" (open_output) reports not opened. | R1: `ui_windows keys: ['foreground','h','minimized','process','title','w']`; `before set: {None}`, so a new PowerPoint window is not counted as new. Live symptom: `neko95/results/mission-20261016-180106.json` step 2, "presentation made but no window showing it appeared" (the .pptx existed). Unit tests mock the agents (`run_conversation.py:33-34` hard-codes `opened_verified`), so the bench cannot catch it. |
| D-2 | **High** | Confirmed (reproduced) | Two PC tasks can control the PC at once. The lock is never refreshed. `_stale` uses the **caller's** budget (`coordinator.py:98`): the coordinator uses 600 s and missions use 1800 s. After 660 s a `run_pc_task` takes over a live mission's lock. A single task whose research step legitimately runs up to 960 s, or a troubleshoot step up to 900 s, also passes `TOTAL_BUDGET_S+60`, so its lock can be taken mid-step. This breaks the "one PC task at a time, two agents never type into the same window" rule. | R2: lock held by a live pid with `at = now-700`. `acquire("t-single", budget=600)` succeeds: "holder now: t-single". With budget 1800 it is respected. |
| D-3 | **Medium** | Confirmed (reproduced) | The browser password-field refusal is skipped when the target is an element ref. In `browserext.act` (`:440-452`) the auth or password check sits inside `if not REF.match(target)`. `type` with a ref such as `e5`, which `run_browser_action` invites ("a ref from read"), types into a password box. The check also matches only the word "password" in the target, not PIN or OTP fields. | R3: `auth_required: True`; by name "Password" it is refused; by ref "e5": `{'ok': True, 'typed_chars': 7}` and `browser_type` was called. The kernel would apply YELLOW_AUTO (owner_direct type), so no approval is needed. |
| D-4 | **Medium** | Confirmed (repo's own test) | "Completed needs verification" can be met with unrelated evidence. `task update state=completed` accepts any `job:<id>` the agent owns that is `done` (`llm_proxy.py:572-579`), with no link to the task or its criteria. The kernel's own test asserts that saving `notes.md` with content "x" completes a task whose criterion is "tests pass" (`kernel/tests/test_tasks.py:296-300`). Mitigation: `_facts()` labels such a task "NOT verified yet" in the owner's reply. | Ran `tests.test_tasks.Honesty.test_an_agent_cannot_mark_a_task_completed_without_proof`: OK (section 5). That test's last assertion is the bypass. |
| D-5 | Low | Confirmed (reproduced) | A mission drops the decider's clarification question. `plan_fn` maps any status other than ok to `[]` (`__init__.py:79-80`), so the mission ends "not_understood: no plan for this goal" instead of asking. | R5: decide returns `clarify 'Open what? …'`; the mission gets `[]`. |
| D-6 | Medium | Confirmed (code) | There is no resume after an approval times out. `Pc` waits 300 s (`core.py:72`), then the task or mission ends with `waiting_for_permission`. The summary says "approve … then ask me to continue" (`coordinator.py:389-390`), but `run_pc_task` has no `approval_id` input and keeps no state for the remaining steps. A mission says "start the mission again" (`missions.py:236-237`): a new mission id means a new effects journal, so already-done non-idempotent steps (type, key, research) repeat. | Code reading: `run_pc_task` input schema `{"request"}` (`__init__.py:632`); `_act` key `f"{mid}:…"` (`missions.py:101`). |
| D-7 | Medium | Confirmed (code) | `open_website` verification overclaims. `open_plain` sets `verified="window"` when **any** foreground Chrome window appears within 8 s (`browserext.py:156-165`); `profile` is just the requested folder echoed back. `verify` then reports "Chrome window appeared in profile X" (`coordinator.py:167-170`) without checking the profile or URL. The only profile witness (`profile_name_matches`) turns a mismatch into an "uncertainty" note, not a failure. | R6 output: verify returns `True`, "Chrome window appeared in profile Profile 1", even with `profile_name_matches: False`. |
| D-8 | Low | Confirmed (reproduced) | A plan can have 5 steps, though the docs and guide say "max 4 steps". `complete_plan` returns `steps[:MAX_STEPS + 1]` (`decide.py:633`) after inserting a whoami step. | `repro_steps.py`: `MAX_STEPS = 4 … steps: 5 ['browser','open_website','open_app','type_text','window']`. |
| D-9 | Low | Confirmed (code), theoretical impact | A mission re-plans around a policy refusal. `Pc.submit` raises `DesktopError` on 403 "refused by policy" (`core.py:80-83`). `_act` turns that into `{"ok": False, error}` with no `state` (`missions.py:115-116`), so the mission re-plans "differently" up to 2 times (`:195-211`). The kernel still refuses each attempt, but this steers the model to look for another route to a refused target (for example a bank site that is not on the list, D-10). | Code reading. |
| D-10 | Medium | Confirmed (probe) | The banking refusal is a word regex, not a list. `policy.SENSITIVE_APPS` (`policy.py:86-87`) lets major banks through as GREEN `browser open`: `icicibank.com`, `chase.com`, `onlinesbi.sbi`. Typing and clicking on those pages is then YELLOW_AUTO, unless the words match `CONSEQUENTIAL`. The guide says "Banking … always refused." | P1 output in section 5. |
| D-11 | Low | Confirmed (code) | Many step types have no tool-specific post-condition. `verify` returns `True, "the action reported success"` for browser click/type/press, `window`, `press_key`, and `True` for any `check_pc` (`coordinator.py:188-189, 208`). Window and type states are checked at the node (WINDOW_STATE, READBACK), but browser click/type/press have no observed post-condition, despite `POST["browser"]` "the result was observed on the page". | R6 output. |
| D-12 | Informational | Confirmed (code) | The vision rung is unreachable from `run_pc_task`. `check_pc` enum (`__init__.py:299`) and `decide.ACTIONS["check_pc"]` leave out `window_ocr` and `window_locate`. | Code reading. |

Not defects, but notable:
- The lab runner imports `pwd.getpwnam("agent")` at module load (`lab_runner.py:31`), so its tests cannot run outside the lab box.
- The desktop plugin has no `plugin.meta.json` snapshot. The progress report warns that the host may list 0 entries until a rescan.

---

## 4. Doc-vs-code discrepancies

| Claim (source) | Code reality |
|---|---|
| "Check. Each step has a post-condition (the right window appeared, the video plays). Only checked steps count." (guide §3); "Every step carries a post-condition" (D28) | Each step carries a **text** post-condition. `verify()` checks it only for open_website (weakly, D-7), play, open_app, open_file, whoami, open_output, troubleshoot and research. Others pass on `ok: True` (D-11). The research and open_output window checks can never pass (D-1). |
| "coordinator: one PC task at a time, max 4 steps / 600 s" (guide flowchart) | 5 steps are possible (D-8). The lock can be taken over while a task or mission still runs (D-2). |
| "Act … lowest control level that works: API, then page DOM, then command line, then OS API, then the accessibility tree, then vision, then pixels" (guide §3) | Each tool is fixed to one mechanism, with no automatic escalation. Vision (`window_ocr/locate`) is not reachable from `run_pc_task` (D-12). |
| "Banking, wallets and password managers are always refused." (guide §9); "Every old refusal still wins: … banks" (D25) | Regex-based; major banks pass (D-10). The password-field refusal in the browser is bypassable by ref (D-3). |
| "Durable missions on DBOS: resume after a crash; … an interrupted send or typing step is never repeated." (guide §10) | True within one mission id. The documented recovery after an approval wait ("start the mission again") is a new id, so done steps do repeat (D-6). |
| "The desktop plugin waits for the approval and continues by itself." (D25) | Only for 300 s. After that the task ends, and `run_pc_task` has no continuation (D-6). |
| "Hermes + the lab … A separate verifier checks every criterion; the worker's own claim never counts." (guide §5) | The verifier is a second LLM session of the same `hermes` kind in the same workspace. The runner's own deterministic checks are deliverable rules, `portable_check` (unittest only) and the tamper guard. Separately, an agent can mark a task completed by citing an unrelated done job (D-4). |
| main_agent code mode: "Software work is done by JARVIS in the bound project (its lab agents have language-server queries)" (`planner.py:30,38`) | No project-binding code exists. The lab sees an empty workspace, and owner paths are refused at dispatch. No LSP integration was found in `jarvis/lab`. |
| `jarvis_bridge.delegate_task`: "writing or fixing code … JARVIS … verifies the result" | Fixes are delivered as flat files to `outputs\task-<id>`, never applied to a project. Hermes cannot run `project_check`. |
| "C4 Windows control 10.0", "C7 troubleshooting 10.0" (progress report scorecard) | Scored from 16 Oct laptop runs on code that has since changed in decide, coordinator, core and node (section 0). |
| "Bigger goals run as missions … re-plan at most twice" (guide §3) | Implemented. But the only live missions (4 of 4) failed, and bench D-043 (live mission) is deferred. |
| Guide §12: "834 / 0 pass / fail (26 wait for hardware)" | Matches `bench-20261017-220721.json` (pass 834, fail 0, error 0, deferred 26). For areas A, B, D, E and N all cases use real code with fake agents, a fake console or fake model answers (adapter docstrings), so they are not end-to-end. |

---

## 5. Commands run and real outputs

All commands were read-only, run with `nice -n 19 podman run --rm --cpus=1 --userns=keep-id -e HOME=/tmp -v ~/hq-neko:/src:O neko-test …`
unless noted otherwise.

**Desktop tests (UNIT/INTEG):**
```
python -m pytest plugin/plugins/desktop/tests/test_missions.py::test_resume_after_restart_skips_done_steps \
  plugin/plugins/desktop/tests/test_missions.py::test_an_interrupted_unsafe_step_is_not_repeated_but_a_safe_one_is \
  plugin/plugins/desktop/tests/test_coordinator.py -q -p no:cacheprovider
-> 31 passed in 8.64s
```
(The first attempt with `-p no:randomly` failed because `pytest.ini` passes `--randomly-seed`; it was rerun without that flag.)

**Kernel tests:**
```
cd jarvis/kernel && python -B -m unittest tests.test_tasks.Honesty.test_an_agent_cannot_mark_a_task_completed_without_proof
-> Ran 1 test in 1.174s  OK
cd jarvis/kernel && python -B -m unittest tests.test_jobs.NodeRunner
-> Ran 9 tests in 24.231s  OK
```

**Reproductions** (`scratchpad/audit/repro/repro_all.py`, mounted read-only, state in a temp dir):
```
=== R1: node ui_windows result has no hwnd; plugin 'new window' check depends on it
ui_windows keys: ['foreground', 'h', 'minimized', 'process', 'title', 'w']
before set: {None} | new PowerPoint window counted as new: False
=== R2: a single PC task takes over a running mission's lock after 660 s (budget of the CALLER is used)
single task acquired the lock; holder now: t-single
with the mission's 30-min budget the same lock is respected: another PC task is running (m-mission); stop it first or wai
=== R3: browser 'type' by element ref skips the password-field refusal
auth_required(snapshot): True
by name 'Password': refused -> Neko does not type into sign-in or password fields: please sign in you
by ref 'e5': {'ok': True, 'typed_chars': 7, 'ref': 'e5'} tools called: ['browser_type']
=== R4: the build-failure request through decide() (fast path; model down = fallback)
fast_path: None
decide (no local model on this server): fallback ['the local decision model did not answer (URLError)']
 model says [{'tool': 'troubleshoot', 'app': 'project'}] -> ok [('troubleshoot', 'project')]
 model says [{'tool': 'troubleshoot', 'app': ''}] -> clarify [] Which app?
 model says [{'tool': 'research', 'query': 'why is the build failing'}] -> ok [('research', 'why is the build failing')]
 model says [{'tool': 'check_pc', 'action': 'processes'}] -> ok [('check_pc', 'processes')]
 model says [{'tool': 'answer'}] -> ok [('answer', None)]
tools the decider can choose: ['answer', 'browser', 'check_pc', 'clarify', 'open_app', 'open_file', 'open_website', 'play_media', 'press_key', 'research', 'stop', 'troubleshoot', 'type_text', 'window']
=== R5: mission planner drops a clarification question (plan_fn returns [] for status != ok)
decide: clarify 'Open what? Nothing was made or opened in the last few minutes.' | what the mission gets: []
=== R6: coordinator.verify passes browser click/type, window, press_key and check_pc on ok=True alone
browser click -> (True, 'the action reported success', '')
window minimize -> (True, 'the action reported success', '')
press_key ctrl+s -> (True, 'the action reported success', '')
check_pc disk -> (True, 'disk read', '')
open_website with node verified=window (any foreground Chrome window): (True, 'Chrome window appeared in profile Profile 1', 'the profile button shows only a display name; the account itself is checked by whoami')
```
`repro_steps.py` (D-8):
```
MAX_STEPS = 4 | plan status: ok | steps: 5 ['browser', 'open_website', 'open_app', 'type_text', 'window']
```

**P1, policy probe.** Host python, `cd jarvis/kernel && PYTHONDONTWRITEBYTECODE=1 python3 -B -c …`, calling `policy.decide(action_for(...))`:
```
open https://www.paypal.com/ ('DENY', ...)          open https://www.icicibank.com/ ('GREEN', 'rule browser.open|snapshot|extract')
open https://netbanking.hdfcbank.com/ ('DENY', ...) open https://www.chase.com/ ('GREEN', ...)
open https://www.bankofamerica.com/ ('DENY', ...)   open https://vault.bitwarden.com/ ('DENY', ...)
open https://my.1password.com/ ('DENY', ...)        open https://www.onlinesbi.sbi/ ('GREEN', ...)
click Pay now YELLOW_APPROVAL | click Continue YELLOW_AUTO | type into search YELLOW_AUTO | press enter YELLOW_APPROVAL
ui type KeePassXC DENY | ui click Bitwarden DENY | ui key alt+f4 YELLOW_APPROVAL | ui click notepad Save YELLOW_AUTO
repair edit YELLOW_APPROVAL | tests run GREEN
```

**Evidence inventory** (host python, read-only JSON reads):
- `coordinator-20261016-180855.json`: G01–G12 all ok. No troubleshoot or research case.
- `desktop-20261016-155621.json`: 21/21. `not_tested`: B18 (password fields).
- `diagnosis-20261016-155827.json`: 17/17.
- `browser-20261016-144244.json`: 19/19 (test page).
- `mission-20261016-{155724,162004,171703,180106}.json`: statuses stopped (Event loop is closed ×3), cancelled (research timed out after 900 s), running (research timed out), failed ("presentation made but no window showing it appeared"; QA failed).
- `bench-20261017-220721.json`: A 95 pass; B 33 pass / 5 deferred (B-034..B-038); D 82 pass / 1 deferred (D-043 live mission); E 56 pass / 3 deferred (E-032..E-034); N 73 pass; total pass 834, fail 0, error 0, deferred 26.
- `evidence/trace-9f98013ab16f.json`: lab task completed and verified, 4 files delivered; created at epoch 1791638231 (`date -d @…` = Sat Oct 10 2026).
- `jarvis/caretaker/history.jsonl`: 0 lines.
- `git log`: the commits after the last live run that touch area code are 60324b11, e0817402, a3ee6647, 175416f5, 8e29d3e7 and cfc65bf9 (see section 0).
