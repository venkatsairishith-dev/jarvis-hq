# Audit area F/G: proactive assistance, multimodal awareness and interface truthfulness

- Repo: `/srv/jarvis/home/hq-neko`, commit `88ec49a2` (branch main). The working tree was clean before and after the audit; I modified nothing.
- Auditor's environment: a Linux test server with no Windows, microphone, GPU or VM access. Every hardware or Windows claim below was checked only against code and the result files and logs recorded in the repo.
- Evidence classes used:
  - **E2E**: a recorded live run on real hardware with a saved result file in the repo.
  - **E2E (doc only)**: a live run that the docs describe, but whose result file or screenshot is not in the repo.
  - **Integration**: real components wired together, with only the external boundaries faked.
  - **Unit/mocked**.
  - **None**.

---

## 1. Real execution flow (file:line)

### 1.1 Everything that acts without a user command

| # | Actor | Where it is scheduled | Installed or enabled by default? | What it does |
|---|---|---|---|---|
| A | **Desktop PC monitor** | `plugin/plugins/desktop/__init__.py:141` `@timer_interval(id="pc_monitor", seconds=60, auto_start=True)` → `_monitor_tick` (`:146-160`) → `monitor.Monitor.check()` (`plugin/plugins/desktop/monitor.py:154-193`) | On whenever the desktop plugin runs (`plugin.toml`: `enabled = true`, `auto_start = true`). Windows only (`win32evtlog`, `psutil`). | Reads only. Alerts are appended to `<DATA>/neko/monitor_alerts.jsonl` (`__init__.py:152-155`) and pushed with `ctx.push_message(visibility=["chat"], ai_behavior="respond", priority=6, coalesce_key=…)` (`:156-159`), so the character model speaks the alert. |
| B | **Watchdog** | `jarvis/scripts/watchdog.py` (one pass), scheduled by `install-watchdog.ps1:30` (`-RepetitionInterval 10 min`, per-user task `JarvisNekoWatchdog`) | Only when that install script is run. Docs say installed 2026-10-15 (05_OPERATIONS.md:252). | `observe()` (`:68-85`), then the pure `decide()` (`:37-57`), then either `neko-start.ps1` (`:142-153`) or `POST /plugin/<id>/start` (`:154-162`). Writes `watchdog_status.json` and `watchdog.log`. It notifies nobody: its only consumers are the widget's error state (`neko_state.py:45-49`) and the daily report. |
| C | **Daily report** | `jarvis/scripts/daily_report.py`, scheduled by `install-daily-report.ps1` (09:00, task `JarvisNekoDailyReport`) | Only when that script is run. Docs say installed 2026-10-15 (05_OPERATIONS.md:253). | Writes `<DATA>/logs/daily_report_YYYYMMDD.md` and keeps 14 (`:73-78`). There is no push, chat message or console notice; the owner must open the file. |
| D | **Kernel owner ticker** (automations, stuck jobs, task deadlines, outcome announcements, lessons) | `llm_proxy.serve()` → `start_ticker(H.owner_plane)` (`jarvis/kernel/jarvis_kernel/llm_proxy.py:1053-1054`) → a loop every 30 s (`owner.py:737-751`) → `Owner.tick()` (`owner.py:397-404`): `jobs.sweep()`, `tasks.expire()`, `automations.tick()`, `announce_finished()`, `capture_lessons()` | Always on while `jarvis-llm-proxy.service` runs on the Oracle VM (`deploy/jarvis-llm-proxy.service:9`). Automations are attached at `llm_proxy.py:1034-1036`. | Fires due automations and settles them. The details of the automations engine are in 1.2. |
| E | **Kernel automations** (definitions) | DB table `automations` (`automations.py:144-159`) | **None exist by default.** No code seeds a definition. The only shipped definition is `jarvis/caretaker/automation_preset.json`, a file that has to be POSTed, and it arrives paused (`"enabled": false`). | lab_task, node job (`JOB_KINDS_OK = {project_check, pc_open, ui_windows}`, `:31`) or a command to Hermes. |
| F | **File-arrival events** | The laptop node's heartbeat `watch.py` → kernel `nodes.py:100` inserts `watch_events` → `Automations.process_events` (`automations.py:505-534`) | The allowlist `jarvis/config/watches.json` contains only the throw-away `jarvis-test` folder (`D:\jarvis\cache\watch-test`). | Fires event-triggered automations. With no automations defined, nothing happens. |
| G | **Monit (VM)** | `jarvis/deploy/monit-jarvis.conf` (`set daemon 60`) → `jarvis/scripts/alert.py` | Docs say installed 2026-10-08 (01_RESEARCH_AUDIT.md:82), together with the drop-in `deploy/monit-override.conf`. | Restarts the kernel, llm, embed, smokescreen and tailscaled processes. Alerts with Telegram off: `audit monit_alert`, then an append to `~/jarvis/alerts.log` (`alert.py:24-29`). See defect D-1. |
| H | **Nightly decision regression (VM)** | `deploy/jarvis-nightly.timer` (`OnCalendar=*-*-* 21:00:00 UTC`, `Persistent=true`) → `jarvis-nightly.service` → `scripts/nightly.py` | Docs say installed around 2026-10-08 (04_BUILD_PLAN.md:86: "nightly regression timer (21:00 UTC)", with a first full run of 25 min). | Runs 4 bench sets and writes `~/jarvis/bench-results/<date>.json` with `flags`. With `config/telegram.off` present it sends nothing **and writes no audit entry**: the audit append is inside the Telegram branch (`nightly.py:71-76`). Monit's `check_nightly.sh` only checks that the file is less than 36 h old. |
| I | **Telegram bridge** | `deploy/jarvis-telegram.service` | **OFF.** `jarvis/config/telegram.off` exists. `telegram.disabled()` (`kernel/jarvis_kernel/telegram.py:58-62`) is checked by `cli.py:116-119` (refuses to start), `alert.py:24`, `nightly.py:71` and `owner.announce_finished` (`owner.py:437-438`). The monit block is commented out. | None. |
| J | **Outcome announcements** | `Owner.announce_finished` (`owner.py:433-465`), called on each tick | Active only while Telegram is off, which is the case now. | For a kernel task that ended **and has a `lab_run` job**: if Hermes is reachable, it queues an `owner_commands` row "outcome", and Hermes checks the task and replies in the console. Otherwise only `audit owner_task_ended` is written. |
| K | **Caretaker** | Through automation E: `automation_preset.json` (cron `17 3 * * 1` UTC, lab_task, agent hermes) → in the lab, `python3 -m jarvis.caretaker.run --config …` → kernel tasks with `source=caretaker` → lab sub-agent → PR | **Not installed and not runnable now.** See 1.3. | — |
| L | **N.E.K.O upstream proactive chat** (character speaks unprompted; news, video, music, meme and mini-game sources; vision chat from screen captures) | `main_logic/proactive_chat/*` (about 10k lines), `main_routers/proactive_router.py` presets `off`/`normal` | User setting in N.E.K.O-data on the laptop (unknown from here). Documented as "native" (05_OPERATIONS.md:220). | Never touches the kernel. See 1.4 and defect D-9. |

### 1.2 Automations engine (kernel), checked in code
- **Creation.** `normalize()` validates every definition (`automations.py:162-217`). A node job may only be one of the 3 unattended kinds, and policy must say neither DENY nor approval (`:240-251`). A lab task needs a goal, instructions and criteria, and no owner paths (`:252-265`).
- **`tick()`** (`:445-503`):
  1. If the kill switch is on, it does nothing.
  2. Otherwise it selects the due rows and computes the slots.
  3. Catch-up policy: `skip`, `one` or `all`.
  4. It advances `next_run` **before** firing.
  5. Each slot is claimed through `UNIQUE(automation_id, run_key)` (`_claim`, `:383-386`).
  6. Daily-limit and overlap checks.
  7. `_fire()` (`:388-432`) moves the run from `claimed` to `firing` (the fence), then calls `jobs.create`, `owner.command` or `tasks.create` plus `dispatch`.
- After firing, `tick()` runs `process_events`, `_retry_claimed` (a run left `claimed`/`firing` for more than 15 min becomes `unknown`) and `settle()` (copies the task, job or command end state).
- `_note_failure` (`:589-595`) auto-pauses an automation after N failures and writes **only an audit entry**. Nobody is notified.
- Owner routes: `owner.py:642-660` (`GET/POST automations…`, preview, pause, resume, run, delete with confirmation). Policy row: `policy.py:60` (`automation create|…|run` YELLOW_AUTO).

### 1.3 Caretaker: what must happen before it runs at all
1. Owner POSTs `automation_preset.json` to `/v1/jarvis/owner/automations`. It is created **paused** (`owner.py:655` passes `enabled=bool(b.get("enabled", True))` and the preset has `"enabled": false`).
2. Owner presses Resume.
3. The caretaker token is issued: `agent-token caretaker --lane public`.
4. `~/.config/jarvis-caretaker/config.json` is created in the lab.
5. A GitHub login with `repo` scope exists in the lab.
6. `main` is protected on GitHub.
7. The lab, Hermes and the VM kernel are all up.

None of this can be done from this server. The CLAUDE.md rule here is also "never push"; the caretaker's PRs go to the private GitHub repo from the lab, not from this server. Every kernel dispatch is checked by `policy.caretaker_problems` (`policy.py:145`, called at `llm_proxy.py:518`). `jarvis/caretaker/history.jsonl` has **0 lines**, so no caretaker PR has ever been recorded.

### 1.4 Does any path run the full chain (detect, gather evidence, notify, propose a fix, wait for authorization, act)?

**The closest path is the PC monitor plus the troubleshooter. It works only if the owner replies.**

1. **Detect.** `monitor.py:157-169` reads a crash from the Application log after the bookmark.
2. **Notify.** `__init__.py:156` calls `push_message`, and the character speaks: "X crashed. Say 'check X' and I'll look at why." (`monitor.py:169`).
3. *Gap 1:* the chain stops here until the owner speaks. No evidence is gathered before the notification; the alert is only the program name.
4. The owner's request reaches `run_pc_task`. The decider has to map it to the `troubleshoot` tool (`decide.py` tools). *Unverified:* whether "check X.exe" maps to `troubleshoot`; `check_pc` is also a candidate.
5. **Gather evidence.** `run_troubleshooter` (`__init__.py:320-400`) runs three kernel jobs: `diag window_text`, `diag app_errors` and `diag crashes` (all GREEN).
6. **Diagnose.** `core.diagnose` labels the result verified or hypothesis.
7. **Propose and wait for authorization.** It sends a `repair edit_file` kernel job. The kernel answers `needs_approval`. `Pc.wait_approval` polls `/api/owner/state` for up to 300 s (`core.py:88-96`), while the icon shows needs_permission. The owner approves with the PIN in the console.
8. **Act.** The resubmit carries the `approval_id`; the node backs up, edits and verifies on disk.
9. **Verify.** It relaunches the app and reads `window_text` again (`:375-391`).
10. **Report.** The result goes back to the chat.

Evidence for this chain:

| Leg | Evidence |
|---|---|
| Detect and notify | **E2E** on the Windows laptop: `jarvis/evals/neko95/results/monitor-20261016-151927.json`, row `PR-crash-alert`: "alerted after 47 s (timer 60 s)". The test checks the **jsonl log**, not that `push_message` reached the chat (`jarvis/tests/neko_monitor_live.py:66`). |
| Fix with approval | `evidence_manual.json` row `T-real-pin-approved-repair` ("held, owner approved with PIN, fixed, verified, rolled back"). Its source `guided-owner-test.json` is **not in the repo**, so this is E2E (doc only). `jarvis/tests/neko_fix_verify.py` explicitly **simulates** the approval ("no kernel, no PIN") and has no saved result. |

**Every other proactive path stops before "propose" and "act":**
- **Watchdog.** Detects and acts (restart) without asking, which is by design for restarts. It notifies only through a status file the widget reads and the daily report summarizes.
- **Daily report.** Detection plus a file. No notification.
- **Monit.** Detects and acts (restart). With Telegram off, the notification is an audit row plus a log file that probably cannot be written (D-1).
- **Nightly regression.** Detects. Nobody is notified while Telegram is off; there is not even an audit row (D-3).
- **Automation auto-pause.** Audit row only.
- **Caretaker.** Designed for the full chain (detect, log, kernel task, lab fix, PR for the owner to merge), but never run (blocked, see 1.3).

### 1.5 Multimodal flow

| Capability | Path | Goes through a kernel job and policy? |
|---|---|---|
| Screenshot | Console `Desktop.tsx:94` `op:"screenshot"` → console `/api/owner/pc` → `Owner.pc` (`owner.py:487-488`) → kernel job kind `screenshot` (`jobs.py:38,113,168-169`; policy `("ui","screenshot",YELLOW_AUTO)` `policy.py:43`, approval floor for the public lane or agents `policy.py:359`) → node `jobs.run_job` (`node/jobs.py:273-274`) → `screenshot()` (`node/jobs.py:187-197`) → `windows.screenshot_png` (`windows.py:121-158`, the whole virtual desktop through GDI BitBlt) | **Yes.** The result carries only `saved` (the file name), `folder`, width, height and byte count; the PNG never goes inline. Also reachable through a voice intent (`console/intent.py`). It is **not** used by the desktop plugin or the main agent; D27 says "Screenshots are never taken by this flow". |
| UIA snapshot | `Owner.pc op=snapshot` → kind `ui_snapshot` (`jobs.py:37,111,166`) → `node/jobs.py:167-184` → `ui.snapshot()` (`ui.py:53-72`, PowerShell UIAutomationClient, password fields skipped, scrubbed). The desktop plugin uses it in `type_text` (`desktop/__init__.py:516`) and `diag window_text` in the troubleshooter. | **Yes.** |
| OCR (Windows.Media.Ocr) and vision (UI-Venus-1.5-2B) | `Owner.pc op=diag check=window_ocr|window_locate` → kind `diag` (`jobs.py:22-23,85-88,154-155`; GREEN, `policy.py:70`; public lane needs approval, `policy.py:337`) → `node/diag.py:121-131` → `vision.look()` (`vision.py:199-228`). Steps: PrintWindow capture of one window at 2× (`:85-95`), then OCR (`:98-114`), then `find_text` (`:122-142`), then, only for `window_locate` with no OCR hit, `ground_vision()` against llama-swap `vision-ground` → `vision-ui-venus-2b` (`local_models/llama-swap.yaml`, mmproj on CPU). The PNG is deleted (`:224-228`); OCR text goes to the local file `outputs\neko-local`. | **Yes, when it is called. But nothing calls it.** The desktop decider's `check_pc` enum is `disk, network, processes, crashes, app_info, services, media` (`decide.py:40-42`), and no plugin, main agent, console screen or Hermes skill sends `window_ocr`/`window_locate` (repo-wide grep: only kernel validation, policy, the node and `jarvis/tests/neko_vision_live.py`). The live vision test calls `vision.look()` **directly in the node's Python, bypassing the kernel** (`neko_vision_live.py:21,53,90,114`). `vision.describe()` has no production caller at all. |
| RapidOCR | `deps/rapidocr_pillow` (N.E.K.O galgame group) | **Not used by JARVIS.** The registry says so: "not used by JARVIS (Windows OCR chosen)". |
| Document reading | `doc_tools.read_document` (`plugin/plugins/doc_tools/core.py:103-125`): pypdf (**not pdfplumber**), python-docx, bs4, text. Path allowlist plus refusals. | **No.** It is a local plugin entry, outside the kernel (by design). A scanned or image-only PDF returns empty text with no "no text layer / no OCR" note (D-8). |
| Deliverables QA | `deliverables/qa.py`: pdfplumber `words_off_page` (`:50-59`), LibreOffice render (`:86-88`), pypdfium2 page PNGs and a contact sheet (`:19-48`, `:95`, `:173`). | **No** (local plugin). The contact sheet is never looked at by a vision model: QA is deterministic. |
| N.E.K.O image understanding | Upstream proactive "vision" mode: the frontend screen capture → `main_logic/proactive_chat/sources.py:310-360` → the configured `vision_model` provider (e.g. `config/api_providers.json`). | **No kernel or lane involvement.** If the conversation path runs through the privacy gateway, `redact_content` passes `image_url` parts through **unredacted** (`jarvis/privacy/gateway.py:52-54`) (D-9). |

### 1.6 Interface truthfulness

**Console** (`jarvis/console` → kernel owner plane, `kernel_client.py:35-56`):
- `Owner.state()` (`owner.py:101-117`) returns the real kernel `tasks.recent(30)` (state, step, attempts, verification, evidence, events), `pending_approvals()`, `approval_history()`, the last 25 `jobs` (state), `commands`, `killed` and `busy`.
- Task control: `Tasks.tsx:21` posts `task {id, op}` with pause, resume or cancel.
- Stop-all: `App.tsx:87` (`owner.py:330-347`).
- Job cancel: `owner.py:530-533`, `:720`.
- **Verdict: real kernel state.** This is consistent with the docs.

**N.E.K.O jarvis_bridge panel** (`plugin/plugins/jarvis_bridge/__init__.py:35-62`):
- Reads console `/api/health` and `/api/owner/state` (approvals) and `/api/owner/commands` (only its own source).
- **Real kernel state**, but it shows commands and approvals only, not kernel tasks or jobs.
- It never grants approvals.

**Neko icon / state server** (`jarvis/widget/widget_server.py:184-197` → `neko_state.decide`, `neko_state.py:40-91`). Signals and their sources:

| Signal | Source |
|---|---|
| Servers | Health probes |
| Kernel reachable, authorized, killed | Console `/api/health` |
| Approvals | Console `/api/owner/state` (real kernel) |
| Thinking | Privacy gateway `inflight` |
| Speaking (estimate), listening | Speech server `/activity` |
| Working / completed | The **main_agent's own run files** (`main_agent_runs/*.json`, `widget_server.py:74-89`), not kernel tasks |
| PC steps | Desktop plugin `activity.json` |
| Ready | Newest deliverable `qa.json` |
| Wake | Wake listener `/status` |

Gaps in the icon (confirmed, see D-4, D-5, D-6):
- Kernel tasks, node jobs and automations never drive "working".
- Failed, held, partial, cancelled and interrupted main-agent runs show as **sleeping**.
- A deliverable that **failed QA** shows as **completed ✓**.
- A stopped desktop plugin is not an error state.

---

## 2. Capability truth table

| Capability | Status | Files and symbols | Evidence (exact) | Limitations | What remains unverified |
|---|---|---|---|---|---|
| PC monitor: crash alert | **Partially working** | `monitor.py` `crash_events`, `Monitor.check`; `desktop/__init__.py` `pc_monitor`, `_monitor_tick` | E2E: `results/monitor-20261016-151927.json` `PR-crash-alert` pass, 47 s, canary announced once; also the scored `score-20261016-161950-interim.json` C10 = 10.0. Unit: `test_monitor.py::test_crashes_after_the_bookmark_only_and_once_each` (passed here). | No quiet period for a crash-looping program (D-2). The alert is the name only, with no evidence gathered. | Chat delivery (`push_message`) was never asserted; the test reads the jsonl. Behaviour after a plugin restart. |
| PC monitor: disk, service-down and stuck-task alerts | **Partially working** | `Monitor.check` disk, service and stuck branches | E2E rows `PR-disk-alert`, `PR-service-down-alert`, `PR-stuck-task-alert` in the same file. **But** these rows build `M.Monitor(...)` directly in the test process with real readings or real sockets (`neko_monitor_live.py:79-113`), not through the plugin's timer. `PR-no-false-alarms` pass: 0 non-canary alerts in 24 h. Unit: 4 tests passed here. | One exception in the crash collector skips every other check in that tick (D-7). The desktop plugin itself is not watched (D-6). | That the 60 s plugin timer delivers disk, service or stuck alerts to the chat. |
| `recent_pc_alerts` entry | Implemented, unverified | `desktop/__init__.py:162-184` | None (no test found) | — | Everything |
| Watchdog self-heal | **Partially working** | `scripts/watchdog.py` `decide`, `observe`, `main`; `install-watchdog.ps1` | Unit: `jarvis/console/tests/test_watchdog.py` (8 tests passed here). E2E (doc only): 05_OPERATIONS.md:252, "killed speech server back in 22 s, stopped plugin in 1 s"; no result file in the repo. | Does not check or heal the **desktop** plugin (the monitor's host) or the wake listener (D-6). Notifies nobody. | The live drills (no saved artefact); that the scheduled task is still installed. |
| Daily report | Implemented, unverified | `scripts/daily_report.py`, `install-daily-report.ps1` | None in the repo (no test, no sample report). Docs: 05_OPERATIONS.md:253 (2026-10-15). | Writes a file only; no notification. | That it runs at 09:00 and what it contains. |
| Kernel automations (cron, interval, once) | **Partially working** | `kernel/jarvis_kernel/automations.py` `Automations.tick`, `_fire`, `settle`, `parse_cron`, `next_cron`; `owner.py:642-660`; `start_ticker` | Unit/integration (real SQLite kernel, faked dispatch): `kernel/tests/test_automations.py` 22 tests OK (run here). E2E (doc only): `docs/research/07-integration-decisions.md:109` (1-minute automation fired on its slot; screenshots not in repo). | **No automations exist by default.** Auto-pause and failures are audit-only. | The current live VM state (list of definitions, recent runs). |
| File-arrival automations | **Partially working** | `node/watch.py`, `kernel/nodes.py:100`, `Automations.process_events` | E2E (doc only): `07-integration-decisions.md:148` (screenshots 30 and 31 in `D:\jarvis\outputs\console-evidence`, not in the repo). Unit in `test_automations.py`. | Ignores `expires` (D-5b, reproduced). Only the `jarvis-test` folder is allowed. | — |
| Telegram bridge | **Blocked (switched off by owner)** | `config/telegram.off`; `telegram.disabled()`; `cli.py:116` | Code: every sender checks `disabled()`. | No push channel to the owner's phone exists at all. | — |
| Monit alerts (VM) | Implemented, unverified (as configured now) | `deploy/monit-jarvis.conf`, `monit-override.conf`, `scripts/alert.py` | E2E (doc only, **before** Telegram was off): 01_RESEARCH_AUDIT.md:82 drill on 2026-10-08 (alert 43 s, restart 60 s). No evidence for the Telegram-off fallback. | The fallback write to `~/jarvis/alerts.log` is very likely blocked by `ProtectHome=read-only` (D-1). | Whether `monit_alert` audit rows appear on the VM; whether `alerts.log` is written. |
| Nightly decision regression (VM) | **Partially working** | `deploy/jarvis-nightly.timer`/`.service`, `scripts/nightly.py`, `check_nightly.sh` | E2E (doc only): 04_BUILD_PLAN.md:86, first VM run 25 min (around 2026-10-08). No result JSON in the repo. | Regression flags reach no one while Telegram is off; there is not even an audit row (D-3). The units point at the old `~/jarvis/scripts` layout, and cutover is "Open" (04_BUILD_PLAN.md:94). | That the timer still fires; the latest flags. |
| Outcome announcements | Implemented, unverified | `owner.announce_finished` | Unit coverage in `kernel/tests/test_owner.py` (not run individually here). | Only tasks with a `lab_run` job. | Live. |
| Caretaker | **Blocked** | `jarvis/caretaker/*`, `automation_preset.json`, `policy.caretaker_problems` | Unit/integration: `jarvis/caretaker/tests/test_run.py::test_automation_preset_installs_disabled_and_does_not_fire` passed here. Other caretaker tests use faked HTTP and kernel. `history.jsonl` is empty. | Needs the owner's steps in 1.3 and a GitHub login in the lab. | Any real scan, task, PR or merge. |
| End-to-end proactive chain (detect → … → act) | **Partially working (owner-initiated second half)** | See 1.4 | Detect and notify: E2E (monitor json). Repair: E2E (doc only, `T-real-pin-approved-repair`, source file not in repo). | No path proposes a fix by itself; the owner must say "check X". | That "check X" routes to the troubleshooter. |
| Screenshot (desktop) | Implemented, unverified | `node/windows.py` `screenshot_png`, `node/jobs.py` `screenshot`; kernel `jobs.py` kind `screenshot`; `policy.py:43,359` | Unit: `test_windows_adapter.NodeSide.test_screenshot_is_written_locally_and_not_returned`, `test_png_encoder_makes_a_decodable_image`, `Validation.test_policy_levels`, `test_a_lab_agent_cannot_look_at_the_desktop_without_approval` (passed here). The real-desktop test `RealDesktop.test_real_screenshot_is_a_valid_png_of_the_screen` was **skipped** ("needs Windows"). The live spec `e2e-live/live.spec.ts:49` is deliberately excluded (05_OPERATIONS.md:229). | Captures the whole desktop with no sensitive-window refusal; the file persists in `outputs\screens`. | Any real capture. |
| UIA snapshot | **Partially working** | `node/ui.py` `snapshot`; `node/jobs.py` `ui_snapshot` | Manual evidence `PE-dialog-text-read` pass ("MessageBox, Find and Save? task dialog texts read through UIA", source "suite B08/B15/B17", `evidence_manual.json`). Unit: `test_snapshot_refuses_password_managers`. | — | Saved artefact for the UIA read. |
| OCR on one window (Windows.Media.Ocr) | Implemented, unverified | `node/vision.py` `capture`, `ocr`, `find_text`, `look`; `node/diag.py:121-131` | Unit: `test_neko_node.Vision.test_find_text_forgives_ocr_digits_and_refuses_ambiguity` (passed here). Live critical `PE-vision-dialog` **never run into a saved file** (QUALITY_95_PROGRESS_REPORT.md:443; `score-20261016-161950-interim.json` C9 `critical_missing`). No `results/vision-*.json` exists. | **Not reachable from any planner or UI** (D-10). | Everything live. |
| Vision model (UI-Venus-1.5-2B via llama-swap) | Implemented, unverified | `vision.ask_vision`, `ground_vision`, `describe`; `local_models/llama-swap.yaml` `vision-ui-venus-2b` | None live. `PE-vision-grounding` and `PE-observation-vs-inference` not run (same sources). The yaml comment records 2026-10-16 GPU failures and a CPU projector, with no saved run. | Same as OCR. `describe()` has no caller. | Everything. |
| RapidOCR | **Missing** for JARVIS (N.E.K.O-only dependency) | `deps/rapidocr_pillow` | Registry: "not used by JARVIS". | — | — |
| Document reading (`doc_tools`) | Implemented, unverified (live) | `doc_tools/core.py` `read_document` (pypdf, python-docx, bs4) | Unit: `doc_tools/tests/test_core.py::test_pdf_and_html_and_docx_are_read_as_text` and refusal tests (not re-run here). 05_OPERATIONS.md:227 says live_check covers document write and read-back. | No OCR for scanned PDFs; empty text with no warning (D-8). Not a kernel job. | — |
| Deliverables QA (pdfplumber, render, previews) | Implemented; unit-tested | `deliverables/qa.py` | `deliverables/tests/test_core.py` (e.g. `test_qa_on_a_damaged_pdf_says_not_passed`; not re-run here). | No vision check of the contact sheet. | — |
| N.E.K.O image understanding (proactive vision chat) | Implemented upstream, unverified for JARVIS | `main_logic/proactive_chat/sources.py`, `main_routers/proactive_router.py` | None for JARVIS. | Outside the kernel and lanes; images pass the privacy gateway unredacted (D-9). | Whether it is enabled on the laptop and which provider it uses. |
| Console shows real kernel state | **Partially working** (code-verified real source) | `owner.state`, console proxy, `ui/src/screens/Tasks.tsx` | Code. E2E (doc only): `e2e-live/evidence.spec.ts` screenshots outside the repo. | — | Live UI. |
| Neko icon states from real signals | **Partially working** | `widget/neko_state.py` `decide`; `widget_server.py` `neko_state`, `build_status`, `newest_run`, `newest_deliverable` | Unit: `jarvis/console/tests/test_neko_state.py` (9 tests passed here). E2E (doc only): 05_OPERATIONS.md:360 ("sleeping → speaking → completed … live_check 66 of 66"). | Failed and held runs hidden; failed QA shown as Done; kernel jobs and tasks invisible; the desktop plugin is not part of error (D-4, D-5, D-6). | — |
| jarvis_bridge panel | Implemented, unverified | `jarvis_bridge/__init__.py` `dashboard` | `jarvis_bridge/tests` (not run here); live_check checks the entries. | Commands and approvals only. | — |

---

## 3. Confirmed defects

| ID | Severity | Status | Title |
|---|---|---|---|
| D-1 | **Medium** | Theoretical (strong; needs the VM to confirm) | With Telegram off, Monit's alert fallback cannot write `~/jarvis/alerts.log` |
| D-2 | Low | **Confirmed** (reproduced) | A crash-looping program is announced on every 60 s tick; the 30-minute quiet period never applies to crashes |
| D-3 | **Medium** | **Confirmed** by code | Nightly regression flags reach nobody and leave no audit trace while Telegram is off |
| D-4 | **Medium** | **Confirmed** (reproduced) | The Neko icon shows "sleeping" after a failed, held, partial, cancelled or interrupted task |
| D-5 | Medium | **Confirmed** (reproduced) | A deliverable that failed QA is shown as "Done ✓ Ready" |
| D-5b | Low | **Confirmed** (reproduced) | File-arrival automations ignore `expires` |
| D-6 | **Medium** | **Confirmed** by code | Nothing watches the desktop plugin that hosts the proactive monitor |
| D-7 | Low | **Confirmed** (reproduced) | One collector error suppresses all other monitor checks in that tick |
| D-8 | Low | Confirmed by code | `read_document` returns empty text for scanned or image-only PDFs with no warning |
| D-9 | Medium (privacy) | Theoretical (depends on the laptop's N.E.K.O settings) | Images pass the privacy gateway unredacted; N.E.K.O's upstream vision chat is outside the lanes |
| D-10 | Medium (claim vs reality) | **Confirmed** by code | The OCR and vision rung of the perception ladder is not reachable from any planner or UI |
| D-11 | Informational | — | Assorted notes |

### D-1 With Telegram off, Monit's alert fallback cannot write `~/jarvis/alerts.log`
- **Severity:** Medium. **Status:** theoretical but strong; it needs the VM to confirm.
- **Evidence:**
  - `monit-override.conf` sets `ProtectHome=read-only` and `ReadWritePaths=/home/ubuntu/.jarvis-kernel` for monit.service. Docs say this was installed on 2026-10-08 (01_RESEARCH_AUDIT.md:82).
  - Processes that monit execs inherit that mount namespace.
  - `alert.py:24-29` (Telegram off) appends to `os.path.join(ROOT, "alerts.log")` = `/home/ubuntu/jarvis/alerts.log`, which is outside the writable path, so the write would raise `OSError` (read-only file system).
  - The `monit_alert` audit row is written first (`alert.py:23`), so that part survives.
  - D13 (03_DECISIONS.md:153) claims alerts go to `~/jarvis/alerts.log`.
- **Reproduction (VM, read-only check):**

  ```
  systemctl show monit -p ProtectHome -p ReadWritePaths
  ls -l ~/jarvis/alerts.log
  ```

  Then check whether a recent `monit_alert` audit row has a matching `alerts.log` line.
- **Impact:** VM failures notify no one. The only trace is the audit chain, which the owner sees only if he reads the console event feed.

### D-2 A crash-looping program is announced on every tick
- **Severity:** Low. **Status:** confirmed (reproduced).
- **Evidence:**
  - `monitor.py:168` pops `active["crash|<app>"]` before `_emit`.
  - `_emit` (`:145-152`) applies the quiet period only when `active` is set.
  - So the comment "still rate-limited below" is false. It also contradicts the docstring ("one fault speaks once", `:10`) and D28 ("One alert per fault, then a 30-minute quiet period").
- **Reproduction:** `repro_area_fg.py` R2 gives `[1, 1, 1, 1, 1]`: alerts per 60 s check for one program crashing once a minute.
- **Impact:** chat and voice spam while an app crash-loops.

### D-3 Nightly regression flags reach nobody and leave no audit trace while Telegram is off
- **Severity:** Medium. **Status:** confirmed by code.
- **Evidence:**
  - `nightly.py:71-76`: the audit append `nightly_run` and the send both sit inside `if … not _tg.disabled()`.
  - Flags go only to `~/jarvis/bench-results/<date>.json` and the journal.
  - Monit's `check_nightly.sh` checks freshness only, never the flags.
- **Reproduction (code):** `sed -n 60,76p jarvis/scripts/nightly.py`.
- **Impact:** a safety regression (union false-allow above 3%) is silent.

### D-4 The Neko icon shows "sleeping" after a failed, held, partial, cancelled or interrupted task
- **Severity:** Medium. **Status:** confirmed (reproduced).
- **Evidence:**
  - main_agent run states: `executor.run_state` returns done, partial, held, failed or cancelled; `store.py:109` adds interrupted.
  - `neko_state.decide` handles only `running`/`planning` (working) and `done|succeeded|finished|completed` (completed) (`neko_state.py:65,78`).
  - `held` means "a step with side effects may or may not have happened". It is the state the owner most needs to see.
  - This conflicts with D23's "no state is shown without a signal behind it" and with the docstring "completed/failed show through the task" (`neko_state.py:25`).
- **Reproduction:** `repro_area_fg.py` R1: failed, held, partial, cancelled and interrupted all give `state=sleeping`, detail "Click me or type a task".

### D-5 A deliverable that failed QA is shown as "Done ✓ Ready"
- **Severity:** Medium. **Status:** confirmed (reproduced).
- **Evidence:**
  - `deliverables/core.py:31,49` writes `qa.json` whether or not QA passed.
  - `newest_deliverable` (`widget_server.py:156-165`) passes `passed`.
  - `decide` returns `completed` regardless (`neko_state.py:80-86`).
  - `neko.html` never reads `passed` (grep finds no match). Its titles map completed to "Done" with badge "✓" (`neko.html:138-141`).
- **Reproduction:** R5 gives `completed Ready: deck.pptx passed: False`.

### D-5b File-arrival automations ignore `expires`
- **Severity:** Low. **Status:** confirmed (reproduced).
- **Evidence:**
  - Expiry is enforced only inside `tick()` for rows with a due `next_run` (`automations.py:473-475`).
  - Event automations have `next_run = NULL` and are fired by `process_events` (`:505-534`), which never checks `expires`.
- **Reproduction:** `repro_event_expiry.py` (real kernel SQLite in a temp dir). One hour after both definitions expired, the event automation is still `state=scheduled` with run `fired`; the interval one is `completed` with no run.

### D-6 Nothing watches the desktop plugin that hosts the proactive monitor
- **Severity:** Medium. **Status:** confirmed by code.
- **Evidence:**
  - `watchdog.py:32` `PLUGINS` omits `desktop`.
  - `live_check.py:199-210` `EXPECTED` omits `desktop`.
  - `neko_state.decide` builds its error state from `CORE` servers only, not plugins (`neko_state.py:19,43-46`).
  - Only the old status page (`widget_server.py:29`) lists it.
  - So if the desktop plugin stops, the 60 s monitor stops silently: no heal, no icon error, no daily-report failure.
  - The wake listener (48945) is also not healed by the watchdog.
- **Reproduction:** R6 gives `'desktop' in watchdog.PLUGINS: False`.

### D-7 One collector error suppresses all other monitor checks in that tick
- **Severity:** Low. **Status:** confirmed (reproduced with a faked collector).
- **Evidence:**
  - `Monitor.check` (`monitor.py:154-193`) has no per-collector try/except.
  - `_monitor_tick` (`__init__.py:146-160`) has none either, and the state is not saved on an exception.
  - An Event Log query error therefore also hides a low-disk or service-down alert.
- **Reproduction:** R3: `check() raised OSError`, so the C: 1.0 GB (floor 12) alert never appears.

### D-8 `read_document` returns empty text for scanned or image-only PDFs with no warning
- **Severity:** Low. **Status:** confirmed by code.
- **Evidence:** `doc_tools/core.py:77-89` uses pypdf `extract_text() or ""`. There is no OCR fallback and no flag, so the model gets `chars_total: 0` and may say the document is empty.

### D-9 Images pass the privacy gateway unredacted; N.E.K.O's upstream vision chat is outside the lanes
- **Severity:** Medium (privacy). **Status:** theoretical; it depends on the laptop's N.E.K.O settings.
- **Evidence:**
  - `jarvis/privacy/gateway.py:52-54` redacts only `text` parts. `image_url` parts are forwarded as-is.
  - N.E.K.O's proactive vision chat (`main_logic/proactive_chat/sources.py:310-360`) sends compressed screen captures to the configured `vision_model`.
  - The "normal" preset turns `proactiveVisionChatEnabled` on (`proactive_router.py:107-110`).
  - If the owner enables screen sharing with a public-lane vision provider, screen pixels (possibly private data) leave the PC. This conflicts with "Dahl (public model) never gets private data".
  - There is no test in `jarvis/privacy/test_privacy.py` for image parts.
- **Related, informational:** monitor alerts use `ai_behavior="respond"`, so crashed program names and stuck-task request text are fed into the conversation model. If that model is Dahl (public lane), this is private PC metadata crossing lanes, behind the gateway's text PII redaction only.

### D-10 The OCR and vision rung of the perception ladder is not reachable from any planner or UI
- **Severity:** Medium (claim vs reality). **Status:** confirmed by code.
- **Evidence:**
  - Kernel kinds `diag window_ocr` and `window_locate` exist, but no caller sends them: not `decide.py` (`check_pc` enum `:42`), not `desktop/__init__.py`, `main_agent`, `ui/src` or `jarvis/hermes`.
  - The only user is `jarvis/tests/neko_vision_live.py`, which calls `vision.look()` directly, outside the kernel.
  - The guide says "Act … accessibility tree, then vision, then pixels", and D28 says "Perception. UI Automation first. Then Windows' built-in OCR … Then UI-Venus".
- **Reproduction:**

  ```
  grep -rn "window_ocr\|window_locate" --include=*.py --include=*.ts --include=*.tsx .
  ```

  The only hits are kernel `jobs.py` and `policy.py`, node `diag.py`, and the tests.

### D-11 Informational
- `Monitor.check` keeps `pending`, `active` and `last_alert` keys for every stuck task id forever, so the state file grows without bound.
- If the Application log is empty (`newest()` returns 0), the bookmark stays 0 and crash checks never start.
- `screenshot_png` captures the whole desktop with no password-manager or bank refusal (the snapshot and OCR paths have one). It is YELLOW_AUTO for the owner, and the file stays in `outputs\screens`.
- `neko_state` shows error "kernel not reachable" whenever the console's `/api/health` probe (6 s timeout) fails while the console itself answers. A slow health call therefore flips the icon to error.

---

## 4. Doc-vs-code discrepancies

| Doc claim | Code reality |
|---|---|
| 05_OPERATIONS.md:77: "**monit** restarts `jarvis-telegram` within about 2 minutes" | The monit block is commented out (`monit-jarvis.conf`, "Telegram switched off"); D13 agrees with the code. Stale line. |
| 05_OPERATIONS.md:252: the watchdog "checks the six servers, the four plugins" | `watchdog.py:27-32`: 9 servers and 8 plugins (desktop not included). |
| `widget_server.py:7` docstring: "the six servers, the four plugins" | 6 servers, 9 plugins. |
| D13 (03_DECISIONS.md:153): Monit alerts go "to `~/jarvis/alerts.log`" | Very likely unwritable under the documented `monit-override.conf` (D-1). |
| `nightly.py:1-3` docstring and `monit-jarvis.conf:1` header: "sends a short summary to the owner's Telegram chat" / "tells the owner on Telegram" | Telegram is off; nothing is sent (D-3). |
| `monitor.py:10` and D28: "One alert per fault, then a 30-minute quiet period" | Not true for crashes (D-2). |
| D23 / 05_OPERATIONS.md:340-358 state table: "no state is shown without a signal behind it"; "completed: a run or deliverable finished" | Failed or held runs are hidden (sleeping), failed QA shows as completed, kernel jobs and tasks never show (D-4, D-5). The doc table also omits `wake_listening`. |
| Guide §3 "Act … then the accessibility tree, then vision, then pixels"; D28 "Perception … UIA first, then OCR, then UI-Venus" | The OCR and vision rung exists only as a kernel op plus a node library, with no caller (D-10). The progress report itself admits the 3 vision criticals were never run (QUALITY_95_PROGRESS_REPORT.md:22,443). |
| Guide §1: "a watchdog heals it every 10 min" | It does not heal the desktop plugin (the monitor) or the wake listener (D-6). |
| Guide §5 / desktop: "a proactive monitor (crashes, low disk, services down)" | Accurate, but its live disk, service and stuck rows tested the class directly, not the plugin timer. Chat delivery is unasserted. |
| Guide §5 jarvis_bridge: "shows pending approvals and connectors" | Accurate. It also shows only its own commands, not kernel tasks. |
| Guide §6 "Vision (last resort) UI-Venus-1.5-2B, Laptop" | The model is configured in llama-swap, but there is no saved live run and no production caller. |
| Guide §11 caretaker "Once a week (installed paused)" | It is a preset **file**, not installed anywhere; it needs the steps in 1.3. `history.jsonl` is empty. |
| Registry entry RapidOCR, `jarvis_files: plugin/plugins/_shared/rapidocr (N.E.K.O)` | Exists; correctly marked "not used by JARVIS". The task brief's "OCR (Windows OCR or rapidocr)": only Windows OCR is wired. |
| doc_tools described with PDF reading; the brief names pdfplumber | `doc_tools` uses **pypdf**. pdfplumber is used only by deliverables QA. |
| `jarvis/caretaker/README.md`: "`history.jsonl` … on each PR branch" | Consistent: the file is empty on main. |
| Registry UI-Venus: "no revision or sha256 in repo" | `jarvis.lock.json:815-836` pins revision `42af0157…`. The registry wording is about upstream, and the lock is fine. Minor wording. |

---

## 5. Commands run and real outputs

All container runs used the overlay mount `:O`, so nothing was written to the repo. `git status --short` afterwards printed nothing (clean). HEAD stayed `88ec49a2`.

**1. Selected existing tests (pytest):**

```
nice -n 19 podman run --rm --cpus=1 --userns=keep-id -e HOME=/tmp -v ~/hq-neko:/src:O neko-test sh -c "cd /src && python -m pytest plugin/plugins/desktop/tests/test_monitor.py jarvis/console/tests/test_neko_state.py jarvis/console/tests/test_watchdog.py 'jarvis/caretaker/tests/test_run.py::test_automation_preset_installs_disabled_and_does_not_fire' -q -p no:cacheprovider; cd /src/jarvis/kernel && python -B -m unittest tests.test_automations"
```

```
.......................                                                  [100%]
23 passed in 1.51s
----------------------------------------------------------------------
Ran 22 tests in 0.128s
OK
```

The 23 pytest tests are: 5 monitor, 9 neko_state, 8 watchdog and 1 caretaker preset. The 22 unittest tests are the kernel automations tests.

**2. Perception and screenshot tests (unittest):**

```
... -v ~/hq-neko:/src:O neko-test sh -c "cd /src/jarvis/kernel && python -B -m unittest -v tests.test_windows_adapter; python -B -m unittest -v tests.test_neko_node | grep …"
```

```
test_lists_real_windows_with_titles_and_processes ... skipped 'Win32 observation needs Windows'
test_real_screenshot_is_a_valid_png_of_the_screen ... skipped 'Win32 observation needs Windows'
Ran 11 tests in 1.379s
OK (skipped=2)
test_find_text_forgives_ocr_digits_and_refuses_ambiguity (tests.test_neko_node.Vision...) ... ok
Ran 25 tests in 3.100s
OK (skipped=3)
```

**3. Reproductions** (scripts in the scratchpad: `/tmp/claude-1002/-srv-jarvis-home-hq-neko/f5cf48e8-19a9-50b8-a560-a1752bd6083e/scratchpad/repro/repro_area_fg.py` and `/tmp/claude-1002/-srv-jarvis-home-hq-neko/f5cf48e8-19a9-50b8-a560-a1752bd6083e/scratchpad/repro/repro_event_expiry.py`, mounted read-only):

```
podman run ... -v ~/hq-neko:/src:O -v <scratchpad>/repro:/repro:ro neko-test sh -c "cd /src && python -B /repro/repro_area_fg.py"
```

```
R1 run state=done         -> icon state=completed  detail='Done: demo task'
R1 run state=failed       -> icon state=sleeping   detail='Click me or type a task (wake word off)'
R1 run state=held         -> icon state=sleeping   detail='Click me or type a task (wake word off)'
R1 run state=partial      -> icon state=sleeping   detail='Click me or type a task (wake word off)'
R1 run state=cancelled    -> icon state=sleeping   detail='Click me or type a task (wake word off)'
R1 run state=interrupted  -> icon state=sleeping   detail='Click me or type a task (wake word off)'
R2 crash alerts per 60 s check for one program crashing once a minute: [1, 1, 1, 1, 1] (QUIET_S = 1800 )
R3 check() raised OSError -> the low-disk alert (C: 1.0 GB < 12 GB) was never produced
R4 kernel has running jobs but no main-agent run -> sleeping
R5 deliverable qa.passed=False -> completed Ready: deck.pptx passed: False
R6 'desktop' in watchdog.PLUGINS: False | decide() with only desktop down -> ([], [])
```

R4 is illustrative: `decide()` takes no kernel jobs or tasks input at all.

```
... python -B /repro/repro_event_expiry.py
```

```
R7 tick: {'fired': [], 'events': ['a-efde3f03:ev:e1']}
R7 event    expired=True state=scheduled runs=['fired']
R7 interval expired=True state=completed runs=[]
```

**4. Evidence lookups:**
- `ls jarvis/evals/neko95/results` gives 97 files. There is one `monitor-20261016-151927.json` and **no** `vision-*.json`.
- `score-20261016-161950-interim.json` C9_perception: `"critical_missing": ["PE-vision-dialog","PE-vision-grounding","PE-observation-vs-inference"]`, score 5.0. C10_proactivity: score 10.0 (6/6).
- `wc -l jarvis/caretaker/history.jsonl` gives `0`.
- `cat jarvis/config/telegram.off`: "Telegram is switched off by the owner (2026-10-10 …)".
- A repo-wide grep for `window_ocr|window_locate` finds only kernel `jobs.py`/`policy.py`, node `diag.py`, `kernel/tests/test_neko_pc.py` and `jarvis/tests/neko_vision_live.py`.

**Not done (out of reach on this server):** any Windows, UIA, OCR, vision, screenshot or microphone run; any VM check (monit, nightly timer, audit rows, `alerts.log`); the laptop's N.E.K.O proactive and vision settings. No suites were run in full.
