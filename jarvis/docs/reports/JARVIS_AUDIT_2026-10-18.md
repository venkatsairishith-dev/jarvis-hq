# JARVIS implementation audit (2026-10-18)

Independent, read-only audit of the JARVIS repository against the owner's intended description ("JARVIS In Depth").
**Nothing was changed, committed or pushed.**

The evidence pack is in `evidence/audit-2026-10-18/`:
- six area reports with file:line references, truth tables and the commands used;
- the reproduction scripts;
- the test log at HEAD.

Every conclusion below points to one of those files, to a result file or to code.

---

## 1. Executive assessment

JARVIS today is a **well-engineered safety kernel and a large, well-tested body of components around it**. It is **not yet
a proven end-to-end assistant**.

- **The core controls hold under adversarial review.** These are:
  - approvals bound to the exact action, single use and expiring;
  - PIN lockout;
  - lanes taken from the token;
  - SSRF pinning;
  - a fail-closed policy;
  - the kill switch for new work.
- **The real end-to-end experience is unproven on current code:** voice, then action, then a verified result.
  - All live PC and voice evidence dates from 16 Oct and ran on code that has changed since.
  - All 4 recorded live missions failed.
  - The real microphone has never been recorded.
  - No phone channel exists while Telegram is off.
- **The benchmark headline is a pass rate over in-repo cases.**
  - 0 of 860 cases are live.
  - 202 cases are mocked by the console's own rule, and about 19 more use model fixtures.
  - The latest committed result (834/0, 9.81) does **not** reproduce at HEAD: the run there gave 833/1 and 9.12, because a
    critical timing case missed by 5 ms.
- **The audit found 3 High defects and about 30 Medium ones. None is Critical.**
  - Most are honesty and reliability gaps: verification that can never pass, two PC tasks at once, Stop that silently
    stops nothing, self-certified completion, and private data reaching public models.

**Overall:** the foundation is sound and worth keeping. The gap to the "Iron Man" experience is mainly integration,
live verification on the owner's hardware, and closing the defects below. It does not need new components.

---

## 2. Repository and commit inspected

| Item | Value |
|---|---|
| Repository | `/srv/jarvis/home/hq-neko` (Linux test server copy), branch `main` |
| HEAD | `88ec49a2` (Caretaker, phase 5). The documented baseline `112a9981` is **outdated**: 8 commits are newer |
| Working tree | Clean at start and end (`git status --porcelain` empty) |
| Remotes | `origin` is a local bundle with push disabled. GitHub `venkatsairishith-dev/jarvis-hq` `main` = `2e9d337b`; PR #5 (`combine-tools`, up to `88ec49a2`) is open and unmerged |
| Submodule | `jarvis/third_party/neko-tauri` at `b7b4fd70` |
| Machine | Linux test server (Xeon Gold 6244). **No Windows, microphone, GPU, laptop, Oracle VM, Hermes or WSL lab is reachable from here.** Every hardware claim was checked through code and recorded result files only |

---

## 3. Architecture and real execution flow

Details with file:line references are in each area report, section 1.

- **Kernel (Oracle VM)** (`jarvis/kernel/jarvis_kernel/`):
  - `llm_proxy.py` is one HTTP process for agents, the runner, the dashboard and the owner plane.
  - Lane always comes from the token.
  - `policy.decide` is first-match and fail-closed, and overlays can only raise the level.
  - `store.authorize` consumes approvals atomically and is bound to `Action.digest`.
  - `audit.py` holds the hash chain.
  - Also: `jobs.py`, `tasks.py`, `owner.py` (PIN, kill, owner routes) and `idempotency.py`.
- **There are three separate voice paths, not one:**
  1. "Hey Neko" (`jarvis/wake/wake_listener.py`) → widget `:48940` → `main_agent.run_task`. English only.
  2. Console push-to-talk (`jarvis/ui/src/lib/voice.ts`) → kernel owner command → Hermes. It bypasses N.E.K.O and main_agent.
  3. N.E.K.O's own browser microphone: never exercised end to end.
- **PC action path:**
  - `main_agent` → `desktop:run_pc_task` → `decide.py`: local decider via llama-swap, JSON schema, then `validate`/`complete_plan`.
  - → `coordinator.py` (lock, ≤ 600 s) → `core.Pc` → console `127.0.0.1:8799` → kernel `Owner.pc` → kernel job
    → laptop node (`jarvis/node/jarvis_node/jobs.py`, `winbackend.py`, `browserext.py`, `pcopen.py`)
    → result → `coordinator.verify`.
- **Hands-on work:**
  - `main_agent` "jarvis" step → `jarvis_bridge` → console → kernel `Owner.command` → **Hermes on the VM**.
  - Then `task_start` / `lab_work` → `dispatch_to_lab` → `jarvis/lab/lab_runner.py` in the WSL box.
  - The runner runs worker → separate verifier session → its own deterministic checks → delivery of flat files to
    `D:\jarvis\outputs\task-<id>`.
- **Memory:** two systems that are **not connected**.
  - N.E.K.O memory (`memory/`, server `:48912`) goes into the conversation system prompt.
  - Kernel memory (`memory.py`) goes only into the owner's Hermes prompt.
  - The main_agent planner receives **no** memory.
- **Proactive:**
  - the desktop monitor (60 s plugin timer → chat `push_message`);
  - the watchdog and daily report (Windows tasks);
  - the kernel ticker (automations: **none defined by default**);
  - Monit and the nightly regression on the VM;
  - the caretaker (a paused preset file, never installed);
  - Telegram is **off**.

---

## 4. Capability truth table

Statuses:
- **Verified (integration):** real components wired together; only external boundaries faked.
- **Verified (live)** needs a recorded live run on unchanged code. No PC or voice capability meets that bar today.

| # | Capability | Status | Key files / symbols | Evidence | Main limitation | Unverified |
|---|---|---|---|---|---|---|
| 1 | Risk tiers, fail-closed, model can only raise risk | Partially working | `policy.py` `decide` 324-398 | `test_kernel.py::TestClassification/TestGrants`; bench K-001..033 | Unknown = RED (approvable), not DENY; English-only word lists | Real Windows targets |
| 2 | Exact-action approval, single use, expiry, level floor | Partially working | `store.py` 138-249, `policy.Action.digest` | `test_approvals.py` (12), `test_hardening.py`; K-021..031, N-001 | Not bound to the requesting agent (SEC-D3) | Live phone/console approval beyond one manual row |
| 3 | PIN approval, RED console-only, lockout | Verified (integration) | `owner.py` 265-303 | `test_owner.py::Plane::*`; K-041..046 | Lockout in memory, resets on restart | Whether the PIN was rotated |
| 4 | Kill switch | Partially working | `store.py` 121-135; checks listed in area-security §1.2 | `TestKill`, `test_llm_proxy::test_kill_switch...`; K-034..039; manual S-estop | Running lab job not stopped | Lab and real node |
| 5 | Audit hash chain | Partially working | `audit.py` 279-330 | `TestAudit` incl. truncation-with-anchor | Owner `audit/verify` uses no anchor; off-box anchor is manual (SEC-D8) | Any pulled anchor |
| 6 | Idempotency keys | Partially working | `idempotency.py` | `test_idempotency.py` (12) | No client sends keys; the console proxy drops headers; forget text kept 24 h (SEC-D2) | Phone retry |
| 7 | "Completed needs proof" | Partially working, bypassable | `llm_proxy.py` 575-590 | `test_tasks.py::Honesty` (its last assertion shows the bypass) | Agent can self-certify or cite an unrelated job (SEC-D1) | — |
| 8 | Lanes; fallback never crosses lanes (kernel proxy) | Verified (integration + live public lane) | `gateway.py` 92-111, `llm_proxy.py` 816 | `test_llm_proxy::test_fallback_stays_in_the_private_lane...`; `live-dahl-20261016-163134.json` JL-006 | Private→public via `public_ok` flag (SEC-D5); memory reaches Dahl (MEM-M3) | Private lane live |
| 9 | Prompt and secret scrubbing | Partially working | `llm_proxy._scrub_messages`, `scrub.py` | `test_scrub.py`; K-054..058 | Tool descriptions not scrubbed; Groq keys missed (SEC-D6, D7) | gitleaks on the VM |
| 10 | SSRF / remote MCP / untrusted results | Verified (integration) | `mcphttp.py`, `mcpwire.py`, `connectors.py` | `test_mcphttp.py`; K-063..075, K-100..105 | "Untrusted" is a label the model still reads | Real MCP servers |
| 11 | Console: loopback, same-origin, PIN not logged | Verified (integration) | `console/server.py` 85-99 | console 93 OK at HEAD | Any local process can act as the owner (SEC-D9) | Real browser |
| 12 | Wake word on the owner's voice | Blocked (hardware) | `wake_listener.make_detector` | none; G-017..019 deferred, `check: none` | No real recording exists | Everything |
| 13 | Wake state machine (record, VAD, cancel, stop) | Mocked/simulated only | `WakeLoop` | `test_wake_listener.py` (17), bench G (fakes) | Stop during planning is a no-op (VOI-D2) | Real mic |
| 14 | Speech-to-text en / hi / te | Implemented, unverified | speech server `:48930`, `voice.py` 86-92 | unit tests with a fake engine; latency doc-only | Wake path English only (VOI-D9) | WER on owner speech |
| 15 | Barge-in / interruption | Missing on the wake path; manual button in the console | `wake_listener.py` 323-326 | repro R3 (VOI) | Detections ignored while Neko speaks | Everything |
| 16 | Voice → action → spoken reply | Implemented, unverified | §3 path | none (no latency measured end to end) | Three disconnected paths; overlapping tasks break Stop (VOI-D1) | All |
| 17 | Decider + clarification rules | Partially working | `decide.py` | bench A 95 (recorded answers); live 16 Oct on old code | Current rules never run with the real model; no code/build/test tools | Live accuracy |
| 18 | PC actions (open, UIA type/click, window) | Implemented, unverified (current code) | node `pcopen.py`, `winbackend.py` | live 16 Oct (`desktop-20261016-155621.json` 21/21) on old code | One PC task at a time is not guaranteed (PC-D2) | Any live run on current code |
| 19 | Post-condition verification | Partially working | `coordinator.verify` 159-208 | bench B (fakes) | "Window opened" check can never pass (PC-D1); many steps pass on `ok: True` alone (PC-D11) | Live |
| 20 | Browser in owner profiles | Partially working | `browserext.py` | live 16 Oct (19/19 test page); live-profiles with 4 fake profiles | Password refusal bypassed by element ref (PC-D3); open-site check accepts any Chrome window (PC-D7) | Saved extension tokens |
| 21 | Refusal of banking / password managers | Partially working | `policy.SENSITIVE_APPS` 86-87 | policy probe (PC P1) | Regex: icicibank.com, chase.com, onlinesbi.sbi are GREEN (PC-D10) | — |
| 22 | Troubleshooter (diagnose → approved fix → verify) | Partially working | `run_troubleshooter`, `core.diagnose` | live 16 Oct diagnosis 17/17 (old code); bench E | Fixed fault classes only; cannot handle build or test failures | Through `run_pc_task` live |
| 23 | Missions (durable, journal, re-plan) | Mocked/simulated only (success); live failed | `missions.py` | `test_missions.py` (14); **4/4 live missions failed** (`mission-20261016-*`) | No resume after an approval wait (PC-D6); clarification lost (PC-D5) | Any successful live mission |
| 24 | Outcome tasks + lab (worker → verifier → delivery) | Partially working | `tasks.py`, `lab_runner.py` | one live green-field task (`evidence/trace-9f98013ab16f.json`, about 10 Oct) | Verifier is an LLM session; fixes delivered as flat files, never applied to a project | Lab on current code |
| 25 | "Fix my failing build" end to end | **Missing** | — | PC repro R4 | No path inspects, fixes in place, re-tests and reports (area-pc §1f) | — |
| 26 | `project_check` (allowlisted test run) | Implemented (integration on Linux) | node `project_check` | `test_jobs.NodeRunner` (9) | One project; reachable only from the console or an automation | Live on the laptop |
| 27 | Kernel memory (CAS, forget, secrets refused) | Verified (integration) | `memory.py` | `test_memory`; bench F | Isolation by token name, not lane (MEM-M2) | Live Hermes |
| 28 | Memory reaches the agent | Partially working | `memory.render` 296-305 | repro MEM R1 | Newest entries dropped above about 14 (MEM-M1); BM25 never feeds the prompt; planner gets no memory | — |
| 29 | N.E.K.O memory (facts, corrections, disputes) | Partially working | `memory/*` (upstream) | upstream unit suites; F-028/029/046-050 | Disputed items still injected (MEM-M6); no per-item delete | Live recall |
| 30 | Model routing / private-lane fallback | Partially working | `providers.json`, `gateway.py` | `test_llm_proxy`, `test_sizing`; live Dahl 12/12 | Owner's private lane has one usable provider (MEM-M4) | Private providers live |
| 31 | Local models / hardware profiles | Partially working | `llama-swap.yaml`, `profiles/*` | `test_server_flags.py`; decider 152/157 on **Linux CPU** only | Non-vram4 profiles have no `decider`/`vision` (MEM-M5) | Laptop GPU |
| 32 | Research with claim checking | Verified (live, public questions, 16 Oct) | `research/core.py`, `verify.py` | `live-research-dahl-*` 5/5 ×3; claims sets | Live runs used xsmall, not MiniCheck; loose pass rule (MEM-M10) | Laptop path with MiniCheck |
| 33 | Deliverables (deck/PDF + QA) | Verified (integration, Linux) | `deliverables/*` | bench I 31 pass | Windows open/print deferred | — |
| 34 | Agent-to-agent lab tasks | Implemented, unverified; defects | `jarvis_mcp.start_lab_task` | none | No verifier, no guard; PATH order (MEM-M8) | All |
| 35 | Proactive PC monitor | Partially working | `monitor.py` | live crash alert 47 s (`monitor-20261016-151927.json`); chat delivery unasserted | Crash loop spams (PRO-D2); one error hides all checks (PRO-D7) | Current code live |
| 36 | Watchdog / daily report | Partially working | `watchdog.py`, `daily_report.py` | `test_watchdog.py` (unit); drills doc-only | Desktop plugin and wake listener not watched (PRO-D6); report is a local file only | Installed tasks |
| 37 | Notifications to the owner's phone | **Missing** (Telegram off) | `config/telegram.off` | code | Nightly regression flags reach nobody (PRO-D3) | — |
| 38 | Automations / caretaker | Implemented; caretaker **Blocked** | `automations.py`, `jarvis/caretaker` | `test_automations.py` (22); caretaker tests (44); `history.jsonl` empty | None defined by default; caretaker guard bypassable (SEC-D4) | Any real run |
| 39 | Screenshot / UIA snapshot | Implemented, unverified | node `windows.py`, `ui.py` | unit; Windows tests skipped here | Screenshot has no sensitive-window refusal | Live capture |
| 40 | OCR / vision rung | Implemented, unreachable | node `vision.py`, kernel `diag window_ocr/locate` | none live; criticals never run | No planner or UI calls it (PRO-D10) | All |
| 41 | Console / icon show real state | Partially working | `owner.state`, `neko_state.decide` | console 93 OK; `test_neko_state` | The icon shows "sleeping" for failed/held runs and "Done" for failed QA (PRO-D4, D5) | Live UI |
| 42 | Phone access | Missing | — | code | Console is loopback on a laptop that is off | — |
| 43 | Service binding / exposure | Verified by code | area-voice-ui §1g | `test_server_flags.py`; host tests | `llm-proxy --bind` accepts 0.0.0.0; N.E.K.O monitor defaults to 0.0.0.0 (not started) | Live port scan |

---

## 5. Voice-to-action, end to end

- **What works (on paper and in unit tests):**
  - the wake state machine (record, VAD end, cancel, stop, gain, resample);
  - speech server routing en → Parakeet and hi/te → IndicConformer;
  - the wake → widget → main_agent hand-off;
  - the reply pushed into N.E.K.O chat for TTS.
- **What is not proven.** Not one recording of the owner's real voice exists in the repo. 7 critical voice cases are
  deferred with no implementation: recall at 0.5 m and 1.5 m, false wakes, voice cancel, barge-in, self-wake, WER.
  - Latency from speech end to text, and from command to spoken reply, was never measured. The only figure is
    keyword-end → detection, p50 0.215 s, on a synthetic clip (`wakelatency-20261016-180934.json`).
- **Real defects:**
  - **VOI-D1 (High):** overlapping tasks. Two `run_task` calls both start, and Stop then answers "Nothing is running".
  - **VOI-D2:** "Hey Neko, stop" during planning does nothing.
  - **VOI-D3:** the widget reports a stop as successful when nothing stopped. The G-028 fixture encodes a reply the real
    widget never gives.
  - **VOI-D4:** "task started" is announced before planning finishes, and later plan failures are never spoken.
  - **VOI-D9:** the wake path is English only.
  - Barge-in by voice is impossible on the wake path.
- **Natural conversation** (speak, interrupt, correct, follow up without restarting) is **not available today**. Follow-ups
  exist only as `coordinator.previous()` within 10 minutes for PC tasks.

---

## 6. Computer control and autonomous missions

- **Live evidence predates the code.**
  - Every live PC result is from 16 Oct 12:46-18:09 IST.
  - decide.py, coordinator.py, missions.py, core.py and browserext.py changed afterwards (`60324b11`, `e0817402`,
    `a3ee6647`, `175416f5`, `8e29d3e7`).
  - Nothing in this area meets "Verified working".
- **Missions have never succeeded live.** All 4 failed:
  - event loop closed;
  - research timeouts (twice);
  - "presentation made but no window showing it appeared". That last one is PC-D1: the node strips `hwnd`
    (`node/jarvis_node/jobs.py:161`), and the plugin compares windows by `hwnd` (`desktop/__init__.py:589`).
- **The scenario "fix my failing build, run the tests, summarise the verified result" is Missing end to end:**
  - the decider has no build, test or code tool;
  - the troubleshooter knows only Windows dialog and event-log faults;
  - Hermes cannot run `project_check`;
  - the lab sees an empty workspace (owner paths are refused) and delivers flat files that are never applied to the project.
  - Honest behaviour today: the PC path fails or asks a question, and the Hermes path produces a lab task that cannot see
    the project.
- **Safety gaps in control:**
  - PC-D2 (High): two PC tasks can hold the PC at once, because the lock uses the caller's budget and is never refreshed;
  - PC-D3: typing into a password field by element ref is not refused;
  - PC-D10: major bank sites are GREEN to open;
  - PC-D6: no resume after a 300 s approval wait, and a re-started mission repeats done steps.

---

## 7. Memory, model routing and agents

- **Memory does not reach planning.**
  - The kernel's session memory block is oldest-first and cut at 1,600 characters, so newer preferences and corrections
    disappear after about 14 entries (MEM-M1, reproduced).
  - BM25 search, with hit@1 0.979 (46/47), is used only for "forget by content".
  - N.E.K.O memory and kernel memory are separate.
- **Privacy versus the rule "Dahl never gets private data":**
  - N.E.K.O's recalled memory sits in the system prompt, which the privacy gateway checks with patterns only (MEM-M3).
  - The memory server's summaries and corrections go to Dahl.
  - A private agent can send personal data to public lab models by setting `public_ok` (SEC-D5).
  - Images pass the gateway unredacted (PRO-D9, theoretical).
- **Routing:**
  - The public lane (Dahl MiniMax) is proven live: 12/12, lanes never crossed, on 16 Oct. The GLM fallback was not exercised.
  - The owner's private lane has only one provider that fits a Hermes turn, `openrouter-private` (MEM-M4).
  - Agent-supplied fields (`models`, `plugins`, `n`) are forwarded unchanged (SEC-D6).
- **Agents:**
  - main_agent planning and execution are unit-tested only, with a run race (VOI-D1 / MEM-M7).
  - The bridge → Hermes path is live **doc-only**.
  - Lab live runs are doc-only except one trace from about 10 Oct.
  - Agent-to-agent `start_lab_task` has no verifier, no guard, and a PATH order that undoes an earlier integrity fix (MEM-M8).

---

## 8. Proactive assistance and multimodal

- **Proactive:**
  - Only the PC monitor runs by default (when the desktop plugin runs).
  - No automations exist. The caretaker is a paused file, blocked on 6 owner steps.
  - With Telegram off there is **no way to reach the owner's phone**.
  - Nightly regression flags go nowhere and leave no audit row (PRO-D3).
  - Monit's fallback log is very likely unwritable (PRO-D1, needs the VM to confirm).
  - The full chain (detect → evidence → notify → propose → authorize → act) exists only as: monitor alert → owner says
    "check X" → troubleshooter → PIN → fix → verify. Its second half starts only when the owner replies, and its PIN-repair
    evidence file is not in the repo.
- **Multimodal:**
  - Screenshot, UIA snapshot and OCR/vision are kernel jobs under policy. But **nothing calls OCR or vision** (PRO-D10), and
    the live vision test bypasses the kernel.
  - `doc_tools` returns empty text for scanned PDFs without a warning (PRO-D8).
  - N.E.K.O's upstream screen-vision chat is outside the kernel and the lanes.
- **Interface truthfulness:**
  - The console shows real kernel state (tasks, approvals with PIN, cancel, Stop all, Kill), and the progress bar is
    honestly "time budget used".
  - The **Neko icon** shows "sleeping" after failed, held or partial runs, "Done ✓" for a deliverable that failed QA
    (PRO-D4, D5), and never shows kernel jobs.

---

## 9. Security and reliability findings

**No Critical defect was found.** Confirmed means reproduced on a throwaway kernel in a test container, or shown by code.

| ID | Severity | Status | Finding | Containment until fixed |
|---|---|---|---|---|
| SEC-D4 | Medium (**High** if the caretaker is enabled before `main` is protected) | Confirmed (guard bypass) | Caretaker text guard skips clauses with "not/no/nothing"; misses `git -C . push` and `gh api …/merge`. The lab holds a `repo`-scope login | Keep the caretaker paused; protect `main` on GitHub first |
| SEC-D1 / PC-D4 | Medium | Confirmed | Agent self-certifies `verification.passed` or cites any unrelated done job, then "completed"; the owner sees "verified" | Treat "verified" on agent-closed tasks as unverified |
| SEC-D5 | Medium | Confirmed | Private agent → public lab with `public_ok:true`; PII not redacted | Don't use lab dispatch for personal content |
| SEC-D6 | Medium | Confirmed (forwarding) | `models`/`plugins`/`n` and tool descriptions pass to providers unfiltered and unscrubbed | Keep $0 credit on OpenRouter |
| SEC-D9 | Medium | Theoretical | Any local process can drive the owner plane through the console (only approvals need the PIN) | — |
| MEM-M3 | Medium | Confirmed (code) | Recalled memory in the system role reaches Dahl with pattern-only redaction | Owner decision (see §15) |
| SEC-D7 | Low/Medium | Confirmed | Groq `gsk_` keys not redacted (built-in pass and gitleaks 8.30.1) | — |
| SEC-D8 | Low | Confirmed | Owner `audit/verify` cannot see truncation; off-box anchor manual and unscheduled | — |
| SEC-D2 / D3 / D10 | Low | Confirmed | Forgotten text kept 24 h in the idempotency table; approval not bound to the requester; running lab job not stopped by the kill switch; `--bind 0.0.0.0` accepted; malformed bodies drop the connection; lockout resets on restart | — |
| Reliability | High / Medium | Confirmed | PC-D1, PC-D2, VOI-D1 (High); PRO-D6 (desktop plugin and wake listener unsupervised); PRO-D3 (silent nightly regressions) | — |

Full reproductions are in `evidence/audit-2026-10-18/area-security.md` §3 and §5 (scripts `repro/security_repro*.py`).

---

## 10. Test and benchmark reconciliation

Totals from different commits are never combined here. Source: `evidence/audit-2026-10-18/area-tests.md`.

| Suite | Run | Code it ran on | Result | Type |
|---|---|---|---|---|
| Benchmark v2 | `bench-20261017-102655.json` | `4e32e583` | 833/1/0/26 of 860, scorecard 9.69 (H-042 failed) | 658 integration / 202 mocked / 0 live |
| Benchmark v2 | `bench-20261017-220721.json` (latest committed) | `15d62f50` | 834/0/0/26, scorecard 9.81 (MiniCheck made H-042 pass) | same |
| Benchmark v2 | **audit run at HEAD** (`head-test-run-88ec49a2.txt`, not saved to results) | `88ec49a2` | **833/1/0/26, scorecard 9.12**: J-018 at 505 ms vs a 500 ms critical limit caps routing, security and speed at 6.0 | same |
| v1 official score | `score-20261016-154951-server-rescore.json` | `9f278eee` (laptop live files from 16 Oct) | 7.61; C8 4.0, C1 and C9 5.0 | live Windows + hand-entered rows; does not reflect HEAD |
| Kernel unittest | audit run | `88ec49a2` | 574 tests, OK, 16 skipped (538 at `bbe769cc`, 570 at `15d62f50`) | unit/integration |
| Console unittest | audit run | `88ec49a2` | 93 tests, OK, 3 skipped | integration |
| Plugin suites, local_models, wake, tools, caretaker | audit run | `88ec49a2` | all pass: desktop 115, research 40, main_agent 73, deliverables 36, local_team 31, caretaker 44, tools 52, ... | unit/integration |
| plugin/tests, tests/unit, UI e2e | not re-run | — | docs say 4289 pass / 8 fail; 41981 pass / 4 fail; UI e2e 5/5. **There is no artifact in the repo** | — |
| pc_control live suite | `pc_control/results/suite-20261016-144424.json` | `7dd88fb1` | 224 pass / 2 fail / 1 not tested of 229 | live Windows, old code |

**What the scores mean:**
- A v2 dimension score is 10 × passed/executed, with caps.
- **9.81 is the highest this server can ever produce**: three dimensions are capped at 9 by deferred critical cases.
- All 26 deferred cases have `check: "none"` and defer on **every** OS. The docs' claim that they "run instead of
  deferring on Windows" is false: no implementation exists.

---

## 11. Confirmed defects by severity

- **Critical:** none found.
- **High:**
  - **PC-D1:** the "window opened" check can never pass (`hwnd` stripped).
  - **PC-D2:** two PC tasks can control the PC at once (stale-lock takeover).
  - **VOI-D1:** overlapping `run_task` calls; Stop silently stops nothing.
- **Medium:**
  - SEC-D1/PC-D4, SEC-D4, SEC-D5, SEC-D6, SEC-D9 (theoretical);
  - PC-D3, PC-D6, PC-D7, PC-D10;
  - VOI-D2, D3, D4, D5, D7;
  - MEM-M1, M3, M4, M5, M6, M8;
  - PRO-D1 (theoretical), D3, D4, D5, D6, D9 (theoretical), D10.
- **Low:**
  - SEC-D2, D3, D7 (Low/Medium), D8, D10 items;
  - PC-D5, D8, D9, D11;
  - VOI-D6, D8, D9, D10, D11;
  - MEM-M2, M9, M10, M11, M12;
  - PRO-D2, D5b, D7, D8.
- **Informational:** PC-D12, VOI-D12/D13, MEM-M13, PRO-D11, SEC-D10 items.

Every ID is defined, with code references and a reproduction, in the matching area report under
`evidence/audit-2026-10-18/`. The prefixes map as follows:

| Prefix | Area report |
|---|---|
| SEC | security |
| PC | pc-autonomy |
| VOI | voice-ui (D-numbers there) |
| MEM | memory-models-agents |
| PRO | proactive-multimodal |

---

## 12. Unverified claims and missing evidence

- **No live run on current code** for:
  - PC control;
  - the decider with the real model;
  - missions;
  - the troubleshooter through `run_pc_task`;
  - the lab;
  - Hermes;
  - the privacy gateway on real traffic.
- **No real microphone recording, speech-to-text WER, barge-in or end-to-end voice latency.**
- **Doc-only live claims (no artifact in the repo):**
  - `live_check` 31/31-66/66;
  - lab full-loop and robustness runs;
  - the privacy gateway "measured live";
  - the main_agent panel "two of two";
  - the watchdog drills;
  - the PIN-approved repair (`guided-owner-test.json` is absent).
- **Laptop GPU numbers.** The decider's 152/157 was measured on the Linux CPU, not on the laptop GPU.
- **No artifact for these suite totals:** kernel, plugin/tests, tests/unit, UI e2e.
- **VM state:**
  - which automations exist;
  - whether the nightly timer still fires;
  - whether the Monit fallback log is writable;
  - whether anchors were ever pulled;
  - whether gitleaks is reachable under `ProtectHome`.
- **GitHub state:** branch protection on `main` (not set, per the docs); the lab's GitHub login and its scope.
- **Doc statements now stale or wrong:**
  - README header, 04_BUILD_PLAN and 05_OPERATIONS still quote 833/1, 9.69 and kernel 538.
  - D30 still says "xsmall kept".
  - The progress report still lists H-042 as open.
  - The 87eeaf48 commit message says "all suites green" while its own report lists known failures.
  - "0 false wakes" (D26, PC_CONTROL_TEST_REPORT, REQUIREMENTS) was corrected elsewhere to 1 in 5.5 h.
  - The watchdog doc says 6 servers / 4 plugins; the code checks 9 servers / 8 plugins.
  - Monit "restarts Telegram" is commented out.
- **Guide page ("JARVIS In Depth") overstatements:**
  - "kernel 570" (it is 574 at HEAD);
  - "834/0, 9.81" (does not reproduce at HEAD);
  - "every replaced part is labelled fake/mock";
  - "Dahl never sees private data";
  - "banking always refused";
  - "lowest control level that works, … vision, pixels";
  - "usable from a phone";
  - "watchdog heals it";
  - "a public-lane agent can't reach private memory" (structurally, isolation is by name only).

---

## 13. Biggest obstacles to the intended JARVIS experience

1. **No proof on the real machine.**
   - The owner's laptop, microphone, GPU and Chrome profiles have not run the current code.
   - Every voice and PC capability is "implemented, unverified".
   - The 26 hardware cases have no checks to run.
2. **The voice experience is not a conversation:**
   - three disconnected voice paths;
   - English-only wake path;
   - no barge-in;
   - Stop is unreliable while planning or when runs overlap;
   - no measured latency.
3. **Goals do not complete:**
   - no live mission has succeeded;
   - the "window opened" check cannot pass;
   - there is no path to fix code in the owner's project and re-test it.
4. **No channel to reach the owner away from the laptop.** Telegram is off, and the console, approvals and Bench are
   loopback on the laptop.
5. **Honesty and privacy guarantees are weaker than stated:**
   - self-certified completion;
   - icon states that hide failures;
   - mocked cases counted as integration;
   - a score that does not reproduce;
   - private data reaching public models through memory, `public_ok` and images.

---

## 14. Evidence references

All evidence paths are relative to `jarvis/docs/reports/evidence/audit-2026-10-18/`.

- **Area reports** (each has a flow with file:line, a truth table, defects with reproductions, doc-vs-code tables, and
  commands with real outputs):
  - `area-voice-ui.md`
  - `area-pc-autonomy.md`
  - `area-security.md`
  - `area-memory-models-agents.md`
  - `area-proactive-multimodal.md`
  - `area-tests.md`
- **Reproductions:** `repro/*.py`. Run them inside the `neko-test` container with the repo mounted `:O` and the scripts
  mounted read-only. They use temp kernels and fakes only; there are no real services or network calls.
- **Test run at HEAD:**
  - `head-test-run-88ec49a2.txt`: commit `88ec49a2`, clean, 2026-10-18 04:55-05:15 UTC;
  - `repro/head_test_run.sh`: the script that produced it.
- **Result files cited:** `jarvis/evals/neko95/results/`:
  - `bench-20261017-102655.json`, `bench-20261017-220721.json`;
  - `score-20261016-154951-server-rescore.json`;
  - `mission-20261016-*.json`, `monitor-20261016-151927.json`;
  - `live-dahl-20261016-163134.json`, `live-research-dahl-*.json`;
  - `wakelatency-20261016-180934.json`;
  - `models-20261017-*.json`.

  Also in `jarvis/evals/pc_control/results/`: `*-20261016-*.json`.

---

## 15. Questions that need the owner's decision

1. **Phone channel.** With Telegram off, JARVIS cannot reach you or take approvals away from the laptop. Should a phone
   channel be re-enabled (Telegram, or another one), or is laptop-only intended for now?
2. **Public models and private context.**
   - Today the conversation, recalled memory and memory maintenance go to Dahl (public lane), with pattern or PII
     redaction only.
   - Should that stay (D19's reading), or must conversation and memory move to private-lane or local models?
   - Should private → public lab dispatch require your PIN?
3. **GitHub protection.** May branch protection on `jarvis-hq` `main` (require PR review, no force push) be set before the
   caretaker or any lab GitHub login is used? It is the real control behind "never pushes to main".
4. **The decider's "ask instead of act" rules** (negation, "X or Y", excluded account, vague target). Keep them as they
   are? This was already pending.
5. **Live verification window.** When the laptop is back, should the first step be a short, recorded live run of the
   current code before any new work? That covers voice, PC tasks, a mission and the 26 hardware cases, which first need
   checks written for them.

*Audit only. No feature, fix, roadmap or next-phase plan is proposed here.*

---

## Addendum, 2026-10-19: status after remediation (the findings above are the audit as written and are NOT edited)

This addendum summarises where the evidence stands; the per-finding status, root causes, tests and numbers are in
`REMEDIATION_2026-10-18.md` (rounds 1 and 2) and `REMEDIATION_FOLLOWUP_2026-10-18.md` (sections 1b–1d, 4, 5, 6b). All results are Linux-container,
server-synthetic; **no Windows, microphone, GPU, lab-box, Oracle VM or live-desktop check was run**, and branch protection on `main` is unverified.

- **Fixed in code with regression tests that failed before the fix** (not live-verified): the three High findings of the audit and the Medium
  findings that could be fixed on the server, including requester-bound approvals, PIN lockout, complete approval cards (with the aggregate size
  limit and a mutation-tested K-106 checker), kill-switch re-checks between steps and stream chunks, lane-aware idempotency, memory lanes, audit
  records without memory text, the privacy gateway whitelist with fail-closed 503 when the GLiNER model is missing, screen-text and file-name
  egress controls, the PC lock (exclusive claim; tasks and missions keep it until the node confirms; atomic submit), Stop reaching single PC
  entries, control-surface authentication for the widget, wake and speech servers, canonical host checks for the financial-site list, and the
  lab verdict channel (verifier's final answer as authority, runner-owned test harness, sealed `VERIFY.json`, one verdict per job/attempt).
- **Reduced but NOT closed:** the lab verifier's same-user boundary (a process that inherits the verifier's stdout, or tested code that writes a
  consistent fake report to the harness pipe, can still vouch for criteria the tests do not cover; closing it needs a separate OS user or container);
  names and street addresses in redaction (best-effort; GLiNER absent on the server, 9 tests unrun).
- **Open (listed with evidence in `REMEDIATION_FOLLOWUP_2026-10-18.md` section 6b):** invisible Unicode on approval cards; the media filter is a
  blacklist; the console/widget secret is served to any loopback client and GET routes are open; private-text retention in approvals, jobs, tasks and
  lab tables; the caretaker text guard (13 of 30 paraphrases pass; only branch protection can stop a lab agent that follows such text);
  the planner-memory sink guard is a two-plugin denylist; step outcomes that use fields other than `status/state/ok`; partly uncleaned plugin
  results; urllib honouring proxy settings; voice stop after `activity.json` goes stale and English-only stop words.
- **Benchmark:** v2 reads 834 pass / 0 fail / 0 error / 26 deferred, scorecard 9.81, after the owner authorised the change of case K-106 (only); the
  original K-106 case and checker are preserved in `proposals/`. 0 of 860 cases are live and 202 are mocked: 9.81 is the ceiling for this server,
  not proof that JARVIS works on the owner's machine. The official v1 score is unchanged (7.61, from 16 Oct laptop runs).
- **Not run (environment unavailable):** every Windows, microphone, GPU, lab-box, Oracle VM and live-desktop check; see
  `LAPTOP_VERIFICATION_2026-10.md` and `MODEL_PLAN_LAPTOPS_2026-10.md` section 8.

