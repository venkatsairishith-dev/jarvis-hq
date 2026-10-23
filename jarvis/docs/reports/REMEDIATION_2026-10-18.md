# JARVIS remediation report (2026-10-18)

Follow-up to `JARVIS_AUDIT_2026-10-18.md` (audited commit `88ec49a2`; evidence in `evidence/audit-2026-10-18/`).

> **Follow-up (same day):** an independent review of this tree found 3 High and ~18 Medium issues. Their fixes, the final-tree test results and the open owner decision (benchmark case K-106) are in `REMEDIATION_FOLLOWUP_2026-10-18.md`. Where the two reports differ, the follow-up is current.

**State of the work:** all changes are **uncommitted** in the working tree on the local branch `remediation-2026-10-18`
(based on `88ec49a2`: 132 tracked files changed, 8,220 lines added and 759 removed, plus 45 new files including the audit
evidence and result files). Nothing was committed, pushed, merged or deployed. GitHub Actions and the caretaker were not
touched; the caretaker stays paused. Two rounds of fixes are recorded here: round 1 (sections 2 and 3) and round 2 (section 2b,
the audit's remaining items).

**What this report does not claim:** that JARVIS is fully operational. Every number below comes from the Linux test server
(Xeon, no Windows, no microphone, no GPU, no laptop, no Oracle VM, no lab box). Nothing was verified on real hardware. The
laptop procedure is in `LAPTOP_VERIFICATION_2026-10.md`, and every step in it is PENDING.

---

## 1. Baseline and final results

| Suite | Baseline (audit run at `88ec49a2` unless marked) | Final (this tree, run C/D; **superseded by `REMEDIATION_FOLLOWUP_2026-10-18.md`**) | Type |
|---|---|---|---|
| Kernel unittest | 574 OK, 16 skipped | **717 OK**, 16 skipped | unit / integration (real kernel in temp dirs, fakes at the edges) |
| Console unittest | 93 OK, 3 skipped | **127 OK**, 3 skipped | integration |
| Plugin suites | desktop 115, main_agent 73, doc_tools 9, jarvis_bridge 20, research 40 | desktop **256**, main_agent **128**, doc_tools **10**, jarvis_bridge **27**, research **46**; deliverables 36, learn_vault 18, lifekit 319 (+1 skipped), local_team 31, wechat 12 | unit / integration |
| local_models / wake / tools / caretaker / lab / bench tests | 12 / 17 / 52 / 44 / not run / not run | **19 / 48 / 53 / 44 / 27 passed + 13 skipped / 14** | unit (the lab repair loop and `lab_runner` live-box tests skip off the lab box) |
| `jarvis/privacy` tests | *not run by the audit*; 28 ran, 11 fail with a stubbed presidio (an earlier measurement; the stub, not the code, caused the failures: the follow-up ran the real presidio) | 35 ran, 12 fail (all 12 need the real presidio, which is not in the image; includes the new key-shape test) | unit |
| `plugin/tests` (N.E.K.O) | *not run by the audit*; 4289 pass / 8 fail (plugin metadata snapshots) from the earlier progress report | 4289 pass / 8 fail (the same 8) | upstream unit |
| `tests/unit` (N.E.K.O) | *not run by the audit*; 41,981 pass / 4 fail from the earlier progress report | **42,017 pass / 4 fail** (the same 4: cloudsave read-only dir, knowledge-subsystem race, voice-identity timing, built-in plugin metadata) | upstream unit |
| Benchmark v2 (860 cases) | **833 pass, 1 fail (J-018, 505 ms vs 500 ms), 0 error, 26 deferred**; scorecard 9.12 | **834 pass, 0 fail, 0 error, 26 deferred**; scorecard **9.81** (`bench-20261018-134602.json`, `scorecard-v2-20261018-134603.json`) | 658 integration / 202 mocked / 0 live (console labelling rule) |
| Claim checker (MiniCheck default) | 0/46, 0/18, 9/200 false acceptances; VitaminC recall 0.71 | identical | real local model, fixed sets |
| UI | tsc, vitest 5, bench e2e 5 | tsc OK, vitest **21 passed** (4 files), build OK, bench e2e **5/5**; console e2e on Linux Chrome 45 pass / 2 fail (see 1a) | unit + browser |

- Benchmark definitions, case files and thresholds were **not changed**. Benchmark *setup helpers* were adapted three times: `_lab_task`
  (D) and `_dispatched` (N) now take the owner's approval step before a private lab dispatch (section 4), and the fake console in
  `adapters/e.py` now serves a page with the per-launch console secret like the real console (see 1a).
- **9.81 is the highest this server can score.** A v2 "10" means the in-repo cases pass. It is not proof that JARVIS works on the owner's machine: 0 of 860 cases are live, 202 are mocked.
- **Timing variance (J-018, 500 ms limit):** it failed at the baseline (505 ms). Round 2 removed most of the cause (section 2b, item 6) and it passed in
  every final run, but the margin depends on CPU and load. Do not treat 834/0 as a guaranteed property.
- **Not run:** the 26 deferred cases (no implementation exists for them), the UI live specs, and every Windows / microphone / GPU / Chrome-profile /
  Oracle VM / lab-box check.

### 1a. What the last passes showed
Logs: `evidence/remediation-2026-10-18/final-run.md` (run A/B, round 1) and `final-run-round2.md` (run C/D, round 2).

- **Regressions caught and fixed by the final passes (so they are not hidden):**
  1. Round 1: `tests/unit/test_recall_render_token_budget` failed because the planner-memory module rendered its own lines. It now renders through N.E.K.O's shared recall renderer.
  2. Round 2: the full benchmark showed **five troubleshooter cases failing** (E-026/027/028/044/045, scorecard 9.12). The cause: the desktop client now reads the console's per-launch secret (SEC-D9) before any write, and the benchmark's fake console had no page. The fake console now behaves like the real one. Area E went back to 56/56. A real regression in how the pieces fit was caught by the benchmark, as it should.
- **Console UI e2e (Linux Chrome in the container; the shipped config is Windows-only):** 45 passed, 2 failed, both Linux-environment: the voice engine is absent so `/api/voice/speak` answers 503, and the `Alt+0` shortcut does not fire in Linux Chrome (it fails the same way on the audited UI). A connectors form that uses `cmd.exe` is Windows-only and did not run. Not run: the live specs.
- **`plugin/tests`:** the 8 failures are the plugin metadata snapshots (the same at the baseline); the planner/desktop changes add `main_agent`, `jarvis_bridge`, `doc_tools` and `desktop` to the ones that need a laptop rebuild.

---

## 2. Defects fixed

Every item below was reproduced first (audit repro script or an equivalent) and has regression tests that **fail on the old
code** (checked by restoring the HEAD file inside the container overlay) and pass now. IDs refer to the audit.

### P0

| ID | Root cause | Fix | Tests |
|---|---|---|---|
| **PC-D2** two PC tasks controlled the PC at once | The lock was judged stale by the *caller's* budget (+60 s) and never refreshed, so a task took over a live mission's lock after 660 s | `coordinator.py`: the lock records the holder's pid, its own budget and a token; a heartbeat thread refreshes a private beat file every 10 s; stale only if the holder is dead or silent beyond **its own** budget + grace. Coordinator and missions share it and check `still_held()` before every step. A timed-out agent call is cancelled, and `run_pc_task` has its own stop flag | `test_pc_lock.py` (13), 2 in `test_missions.py` |
| **VOI-D1 / MEM-M7** overlapping voice/typed tasks; Stop silently stops nothing | `_active` was checked before the long planning `await` and cleared unconditionally by whichever run ended first | `main_agent/__init__.py`: one authoritative claim taken **before** planning, released only by its owner; a second request gets the honest "busy" answer; `resume_run` claims too | `test_run_control.py` (16) |
| **VOI-D2/D3/D4/D8** Stop during planning did nothing; widget said "stopped" when nothing stopped; "task started" before planning ended; cancel during submit still started the task | The wake listener read the newest run *file* (none exists while planning); the widget ignored the entry's answer | planning marker, truthful `stop_main_task` answer (`stopped` true/false/unknown), plan failures spoken in chat, submit-cancel stops the task it started | widget (7), wake (4), neko_state (1) |
| **Cancellation chain** Stop reached only the step boundary | Cancelling the wait did not cancel the remote step | `executor.py` interrupts the step in flight; a step that really ends within 1 s of Stop is reported as done; desktop steps call `stop_pc_task`; jarvis steps call the new `jarvis_bridge:cancel_task` for that one command id. Stop (this task), Stop all, and Kill stay separate | `test_run_control.py`, `test_executor.py`, bridge (3) |
| **SEC-D5** private → public lab sharing trusted a model-set `public_ok` flag | The flag was treated as authorization | `llm_proxy.dispatch_to_lab`: a private-lane dispatch now needs the **owner's PIN approval** (`lab.share`, YELLOW_APPROVAL) bound to the digest of the exact scrubbed text; used once; audit rows hold only counts and digests | `PrivateToPublic` (5) |
| **MEM-M3** recalled memory reached Dahl with patterns only | The gateway ran its PII model on user/tool/function roles only | `privacy/gateway.py`: every role (system, assistant, reasoning, refusal) gets the deep pass; the memory server's own summary/correction/extraction calls are covered whatever their role | `GatewayRoles` and others |
| **PRO-D9** images passed the gateway unredacted | Only text parts were redacted | images, audio, files and unknown parts are replaced by a text note ("an image was withheld for privacy"); POSTs to anything other than chat/completions are refused (403) | 3 gateway tests |
| **SEC-D6/D7** agent fields forwarded unchanged; Groq/HF/Google/Slack/Stripe keys and PGP blocks not redacted | `payload = dict(body, …)` and missing patterns | allowlist of standard chat fields; tool descriptions/parameters scrubbed; malformed bodies get 400; built-in scrubber covers the missing key shapes and blocks PGP private keys | llm_proxy (2), scrub (3) |
| **MEM-M8** agent-to-agent lab tasks unguarded | The prompt was never part of an approval; `~/.local/bin` first on PATH | `lab.subtask` action carries the whole prompt and is approval-bound for the private lane; PATH order restored | `test_jarvis_mcp.py` (4) |

### P1

| ID | Root cause | Fix | Tests |
|---|---|---|---|
| **PC-D1** "new window opened" could never pass | The node's `ui_windows` stripped `hwnd`, the plugin compared by `hwnd` | the node returns an opaque, keyed-HMAC window `id` (+ `id_epoch`, `ids_complete`); the raw handle still never leaves the node; success only for exactly one new window whose title shows the file name; missing/stale/ambiguous ids fail safe | `test_new_window.py` (15), adapter (1) |
| **SEC-D1 / PC-D4** an agent could mark its own task verified | Any agent could write `verification` and cite any finished job | agent writes of `verification` are refused (403); completion needs a verdict recorded by `lab-runner`/`owner`, or cited jobs that are the agent's own, created for this task, finished OK after the task began; the runner's verdict must cover every criterion; repeated completion gets a clean 409; the kernel never sets `passed` on evidence alone | `VerifiedCompletion` (9) |
| **MEM-M1** newest memories were dropped from the agent's prompt | `render()` was oldest-first and cut at 1600 chars | `memory.py`: newest/most important first, strict cap, explicit omitted count, provenance on every line, conflicting similar entries marked (never merged); optional task query ranks by BM25 | `test_memory_render.py` (9), plugin (2) |
| **MEM-M2** memory keyed by token *name*, not lane | no lane column | rows carry the writer's lane; every read filters by the reader's lane; old rows migrate to private | `Lanes` (2) |
| **Planner memory** the planner received none | planner prompt = task + catalogue | `main_agent/memory_context.py`: top-8 recall from the memory server, ranked by relevance then newest, hard 1,800-char cap, rendered through N.E.K.O's shared recall renderer (token ceilings), provenance and "may be outdated" marks; **only when the planner's endpoint is the privacy gateway or a local model**; fails closed | `test_memory_context.py` (15) |
| **SEC-D2** forgotten text kept 24 h in the idempotency table | stored replies | the forget reply carries only id/kind/version, and stored replies mentioning the id are redacted in the same transaction | 4 |

### P2 (Phase 3) and others

| ID | Fix |
|---|---|
| **SEC-D4** caretaker guard bypasses ("…and nothing else", `git -C . push`, `gh api …/merge`) | `policy.caretaker_problems` rewritten: negation never exempts a command; commands read the way a shell would; the **only** push allowed is `git push [-u] origin caretaker/<id>`; `gh` allowlisted to PR create/view/list/diff/checks/status, repo view/clone, runs; REST/GraphQL merge, protection and refs endpoints refused. 40 adversarial variants tested. **The caretaker stays paused** |
| **PC-D10** bank sites opened GREEN | `jarvis/config/sensitive_sites.json` (~250 domains) matched on registrable domain/subdomain and host labels, never URL paths; kernel `policy` and node `pcopen`/`jobs`/`diag`/`repair` share the check; file is a safety component (console-only to change). *Pages merely about banks stay allowed* |
| **SEC-D8** audit truncation invisible to the owner check | kernel exports a head anchor to `<data>/audit-anchors.jsonl` (≤ every 15 min); owner/CLI verify uses it. Copying the file off the box is still manual |
| **SEC-D10** | `llm-proxy`/`serve` refuse all-interface binds (`0.0.0.0`, `0`, `[::]`, `*`); the PIN lockout survives a kernel restart (fail closed) |
| **PRO-D3/D1** silent nightly alerts, unwritable Monit fallback | audit rows always written (`nightly_run`, `nightly_regression_failed`); fallback log moved into the writable kernel data dir |
| **PRO-D6/VOI-D5** nobody watched the desktop plugin or wake listener | watchdog heals both (3/hour, then `gave_up_on`), never restarts the listener the owner turned off; the listener retries a failed microphone with backoff and says why on `/status`; the icon shows a stopped desktop plugin as an error |
| **PRO-D2/D7/D11** monitor | crash alerts obey the 30-min quiet period; one failing collector no longer hides the others; stuck-task state is pruned |
| **PRO-D5/D5b/D8** | a deliverable that failed QA shows "Needs your check", not "Done ✓"; file-arrival automations honour `expires`; scanned PDFs say "no text layer" |
| **PC-D3** typing into password fields by element ref | refs are resolved against a fresh snapshot; password/PIN/OTP/CVV fields refused on any page |
| **PC-D7 (partial)** `open_website` accepted any Chrome window | only a window that changed after the launch counts; otherwise "unconfirmed" |
| **PC-D5/D6/D9** missions | clarifying questions are kept, `resume_mission` continues under the same id and journal, policy refusals end the mission honestly |
| **Live mission failures** | see section 3 |
| **Entry bug found while testing** | a Phase 2 edit had placed a helper between `@plugin_entry(id="run_pc_task")` and its function, making the entry unreachable; fixed, with a test that every desktop entry is an async function named after its id |
| **N.E.K.O guard** | `test_recall_render_token_budget` requires every recall reader to render through the shared renderer; the planner-memory module now does, and is registered as a known client |

---

## 2b. Round 2: the audit's remaining items (fixed, with tests that fail on the old code)

| Item | Root cause | Fix |
|---|---|---|
| **SEC-D3** an approval could be spent by another agent | the approval and its digest had no requester | the requester is stored and in the digest; the card says "asked by <agent>"; the node `/authorize` must name it. Owner-direct approvals are unchanged |
| **SEC-D10** the kill switch did not stop a running lab job | `/runner/status` carried only the task state | status carries `killed`/`cancel_requested`; the runner checks every 5 s and kills the worker's whole process tree; the task ends "stopped by the kill switch", nothing delivered |
| **Lab tasks of a cancelled command kept running** | tasks were not linked to the command that started them | `task_commands` link; `command/cancel` with `stop_tasks: true` (used by `jarvis_bridge:cancel_task`) cancels that command's tasks and lab jobs. The plain "Stop waiting" button still stops no tasks |
| **Automations sent file names to the public lab** | the trigger text was copied into the lab instructions | the lab sees only "a new file arrived" unless the owner set `share_filename: true` when creating the automation |
| **SEC-D9** any local process could drive the console | a constant header `X-Jarvis-Console: 1` | a random per-launch secret in the served page, required on every POST/DELETE (forged-Origin hole closed). **A process of the same user that can GET the page can still read it:** this blocks blind and cross-site requests, DNS rebinding and sandboxed processes. The PIN is still the boundary for approvals. Clients updated: the UI, `jarvis_bridge`, the desktop plugin client, the four live scripts, the laptop procedure |
| **J-018** the 500 ms relay | the deep scrub started the `gitleaks` program for every request (about 500 ms) | `gitleaks_gate.py` skips it only for plain ASCII text with none of gitleaks' own 244 trigger keywords and no encoded-looking runs; fail closed on any doubt. Short keyword-free requests now take about 350 ms cold. Most real prompts contain a keyword and still pay about 0.5 s |
| **Single-action PC entries skipped the PC lock** | `run_pc_action`, `run_browser_action`, `open_website`, `play_youtube`, `run_troubleshooter` never took it | they take the same heartbeat lock and answer "another PC task is running" when busy |
| **PC-D8** 5-step plans | `complete_plan` sliced to cap+1 | the cap is enforced; only the code's own inserted account check may exceed it; otherwise the decider asks |
| **PC-D11** steps passed on `ok: True` alone | no observation | click/key steps now carry "reported ok, not observed"; the summary says "Done, but not everything was observed"; browser typing that fell short fails; window state and typed text are read back by the node |
| **PRO-D10** OCR / vision unreachable | no caller | owner-callable `read_window_text` and `find_on_screen` entries through kernel jobs (policy, audit, scrubbed output); OCR as a labelled, never-auto-fix fallback in the troubleshooter. The decider is untouched |
| **PC-D7 remainder** | profile unproven | the doubt text now says what was and was not observed; L-004 still completes with its warning (the bench case is unchanged) |
| **Computer-use step could not be stopped** | the agent-server call answered at once, so the step "succeeded" while the desktop was still controlled | the step follows the one task and cancels exactly that task on Stop (never `end_all`) |
| **Leaked run claim** | a claim not released if something raised before the run started | released on that path |
| **VOI-D9** wake path English only | `transcribe` was called with the default language | `--lang en/hi/te` (saved choice, shown on `/status`, `POST /lang` with the same guards as `/pause`). No automatic language detection |
| **Barge-in by voice** | detections ignored while Neko speaks | **opt-in** `--barge-in` (default off): only a stricter wake hit plus a whole-utterance stop phrase acts; it never submits a task from that audio. A real `POST /interrupt` on the speech server cancels in-flight synthesis. **Unverified on hardware, and audio already received by the page keeps playing** (N.E.K.O has no HTTP route to stop it) |
| **VOI-D6** 60 s push-to-talk limit | forced English and dropped the text | Command Center and Settings use the chosen language and keep the transcript |
| **VOI-D11** an optional server made the icon "error" forever | llama-swap was expected even when not installed | watched only when installed and not in `remote_only` |
| **MEM-M5** other hardware profiles had no `decider` | missing aliases | `cpu8` Qwen3-1.7B; `cpu16`/`vram8plus` Qwen3-4B-Instruct-2507 (+ UI-Venus vision on `cpu16`/`vram8plus`); a test checks every profile defines every alias. **Never run on that hardware class** |
| **MEM-M11** research searched unredacted when the gateway was down | fail open | fails closed with a clear message (an explicit offline parameter exists for tests) |
| **MEM-M10** the live research eval was too lax | the answer could sit in "could not be confirmed" | stricter judge (confirmed sentence + citation + supported share), unit-tested; cannot run live here |
| **MEM-M12** damaged counters | raw `int()` in six more N.E.K.O memory files | the safe `_int_counter` helper everywhere |

---

## 3. Why the four recorded live missions failed (evidence, not guesses)

From `results/mission-20261016-*.json` and the commit history:

1. **155724** `"RuntimeError: Event loop is closed"` on three research steps. The research call ran on the plugin-startup loop, which the host closes after startup (documented in the 16 Oct progress report). Fixed since then by starting missions on first use; now also: a closed or stopped loop raises `StepUnavailable` at once, and an unavailable step ends the mission instead of being re-planned twice.
2. **162004 and 171703** research timed out after 900 s. The commit `df020727` records a nested-plugin-call deadlock fixed in N.E.K.O core. A timed-out step used to be re-planned into a second 15-minute research; cancelling could not interrupt the wait. Both fixed in missions, with the recorded error string as a test.
3. **180106** "presentation made but no window showing it appeared" = PC-D1 (fixed). The re-plan then researched again; now an existing passed deck is opened instead. **Not explained by the evidence:** step 1's "the action did not succeed" (no error text recorded) and the second deck's QA failure. The repo does not contain their cause.

No live mission has succeeded since; none has been run on the current code.

## 3a. The build-failure scenario

`jarvis/kernel/jarvis_kernel/projectfix.py`, started from the owner plane (`POST /v1/jarvis/owner/project_fix`, `…/advance`, `GET project_fix`): an outcome task with four criteria → the real `project_check` job gives the failing output → the lab fix is shared only with the owner's PIN → the kernel checks the returned patch (same commit, existing text files, ≤ 5 files, no test files, every file named in the root cause) → a `project_apply` job (YELLOW_APPROVAL, patch digest) proves the patch in a scratch clone (fails without, passes with, every file needed), backs up and applies it → the kernel re-runs the tests → "completed" only if the tests exit 0, the root cause is named and the tree changed in exactly the patch files. Nothing is committed.

- **Integration test** (`test_projectfix.py`, 20 tests, including the real owner HTTP routes): real kernel, real node `project_check`/`project_apply`, real undo. **The lab model is a scripted stand-in.**
- Honest failures covered: a fix that does not make the tests pass, an unrelated file changed, test edits, no approval, denied approval, project not allowlisted, lab unavailable, no `lab_clone`, tests already passing, kill switch.
- **Where it stops here:** there is no lab box, so the real lab leg did not run. The only allowlisted project (`jarvis-hq`) has a Windows path and no `lab_clone`. It has never touched the owner's real projects.

---

## 4. Behaviour changes you will notice

1. Every `lab_work` from your own JARVIS now waits for your **PIN** (a "lab.share" card shows what will be shared). JARVIS tells you the id and stops.
2. Applying a project patch is a "project.apply_patch" approval.
3. Stop during planning works; a second voice/typed task while one is active gets "still working".
4. The Neko icon shows an amber "Needs your check" for failed QA and unfinished tasks.
5. Images never reach a public model; the privacy gateway refuses any non-chat endpoint.
6. ~250 financial sites and apps are refused for JARVIS to drive (you can still use them yourself). `bankrate.com`/`tradingview.com` are refused by design (fail closed).
7. The agent's memory block shows fewer, richer lines (about 9–10 instead of 14).
8. The planner may now see a few relevant memories (only through the gateway or a local model).

**Changed expectations in existing tests (each encoded a defect or a removed behaviour):** `test_tasks.Honesty` (completion with an unrelated job), `test_privacy.GatewayRoles` (system/assistant roles were deliberately pattern-only), `test_scrub.Deep` (built-in pass "misses" Slack/Google/Stripe), `test_tasks.API/Delivery/Race` and `test_idempotency` (private dispatch now needs approval first), `test_widget_server` (the widget sends `source: "widget"`), `test_neko_state` (unfinished runs show `attention`), the bench setup helpers `_lab_task` (D) and `_dispatched` (N) in `adapters/d.py`/`n.py` (they now take the owner's approval step first; the case files, expected results and thresholds are untouched), and `tests/unit/test_recall_render_token_budget.py` (one known-client entry added, as that test instructs).

---

## 5. Security implications

- **Strengthened:** lane isolation at the gateway, the kernel and memory; no agent-supplied flag authorizes anything; verification is no longer self-certifiable; the PC cannot be driven by two tasks; supervision, alerting and anchors close silent-failure paths.
- **Added surface:** three owner-only routes (`project_fix`), a `project_apply` job kind (YELLOW_APPROVAL), `lab.share` and `lab.subtask` actions, an audit-anchor file, `sensitive_sites.json`. All are owner-plane, loopback/Tailscale, token-gated; none can approve itself.
- **Not weakened:** approvals, the kill switch, audit hash chain, idempotency, lanes, benchmark definitions.

---

## 6. Remaining defects and limits (not fixed)

| Item | Why it is not fixed |
|---|---|
| Console secret: a same-user process that can GET the page can read it (SEC-D9) | inherent to a loopback web console; the real boundary is the PIN |
| Audio the page already received keeps playing after a barge-in | N.E.K.O has no HTTP route to stop playback; adding one is upstream code (your decision) |
| Hindi / Telugu cancel and stop words on the normal wake path are English only | needs hardware tuning |
| Barge-in, wake language, `read_window_text`, `find_on_screen`, profile deciders, single-entry locks, kill of a running lab worker: all untested on real hardware | needs the laptop / lab box |
| Orphaned agent turns (a task created after you stopped waiting) cannot be tied to a command | the kernel never sees the agent session id; "Stop all" covers it |
| Your private lane has one usable provider for a Hermes turn (MEM-M4) | provider / config choice |
| Private → public sharing, planner memory through the gateway, and the other items in section 9 | your decisions |
| N.E.K.O keeps disputed/archived memory in the prompt ("sinks" it); no per-item delete of private memory (MEM-M6) | upstream design; changing it is upstream code |
| The N.E.K.O plugin `ctx.query_memory` returns a placeholder; the WeChat plugin reads private `/new_dialog` | upstream; disable WeChat if unused |
| PC-D7 versus bench L-004 (an unproven profile completes with a warning) | the bench definition may not be changed |
| The 26 deferred cases have no checks and defer on every OS | no hardware here |
| Telegram is off, so there is still no way to reach you on your phone | your decision |
| `fetch_models.py --profile` does not fetch the decider/vision files for the new profiles (use the lock fetcher); the lock stores Qwen3.5 files under `models/gguf` while older profiles read `models/llm`; `decide.py` / `vision.py` do not yet say "no vision model in this profile" | small follow-ups |
| `_counter` in `memory/evidence.py` does not catch `OverflowError`; a few `float()` casts in `refinement.py` / `promotion_merge.py` | small follow-ups |
| `Presidio`-dependent privacy tests (12) | the library is not in the image |
| The audit-anchor copy off the box is still manual; `J-018` still depends on CPU and load for keyword-containing prompts | |
| The v1 official score (7.61) is unchanged and reflects 16 Oct laptop runs | needs live Windows runs |

---

## 7. Hardware-only and external verification still pending

See `LAPTOP_VERIFICATION_2026-10.md` (12 sections, all PENDING). In short: wake word and speech with the real microphone;
the decider on the real GPU; a real PC action and a new-window check on Windows; cancel/Stop on real runs; the lab box and
Oracle VM (kernel must be **redeployed** with this tree, together with `jarvis_mcp.py`, `lab_runner.py`, the console and the Hermes memory plugin; the desktop plugin, `jarvis_bridge` and the console must be updated **together**, because writes now need the per-launch console secret);
PIN approval cards in the console; the privacy-gateway image test; the build-fix procedure with a real lab; Windows
`_alive`/window ids/`msvcrt` lock; Presidio-dependent privacy tests (12 fail here only because presidio is not installed).

## 8. Manual actions for you (priority order)

1. **Review this tree before anything is committed.** Say "commit" and I will commit it in separate, reviewed commits (no push without your say-so).
2. **Protect `main` on GitHub** (require PR review, no force pushes) *before* the caretaker or any lab GitHub login is used. The caretaker guard is only a second gate.
3. **Decide the questions in section 9.**
4. **Redeploy** the kernel, `jarvis_mcp.py`, `lab_runner.py`, the console and the Hermes memory plugin to the Oracle VM / lab box (the new kill-switch check lives in `lab_runner.py`); then run the VM checks listed in `LAPTOP_VERIFICATION_2026-10.md` section 0.
5. When home: run the laptop procedure in order (sections 1, 2 and 5 first).
6. Rebuild the plugin metadata snapshots (the 8 that already fail: desktop, learn_vault, local_team, doc_tools, main_agent, research, jarvis_bridge, deliverables).
7. Confirm N.E.K.O's vision/proactive-chat provider URL points at port 48950.
8. Add a `lab_clone` to a *test* project before trying the build-fix procedure.
9. Still open from before: revoke the Dahl test key, change the approval PIN, revoke the GitHub CLI login if not wanted.

## 9. Decisions only you can make

1. May the planner see memory at all when its model is the *gateway* (still reaching Dahl, redacted)? Narrowing to local models only is a one-line change (`private_endpoints()`).
2. Accept that every private → public lab share needs your PIN?
3. (Done in round 2 as the safer default) the single-action PC entries now take the PC lock; say if you want that undone.
4. PC-D7: should an unproven profile fail (needs bench L-004 changed) or complete with a warning (today)?
5. `bankrate.com`/`tradingview.com` refused: keep?
6. Raise the agent memory block cap (1,600 chars) given M-4's single private provider?
7. Lab sharing of failing project output with the public lab: allow, per approval (as built)?

## 10. Documentation reconciliation

README header, `04_BUILD_PLAN`, `05_OPERATIONS`, D30 and the progress report were brought in line with the artifacts; historical
results keep their dates and commits. Details in section 11.

## 11. Evidence

- Area reports and repro scripts: `evidence/audit-2026-10-18/`.
- Final-run logs: `evidence/remediation-2026-10-18/final-run.md` (round 1) and `final-run-round2.md` (round 2).
- Laptop checks for round 2: `LAPTOP_VERIFICATION_2026-10.md` section 11b (14 checks, all PENDING).
- Per-agent before/after reproductions are in the engineers' scratchpad reports and are summarised in section 2 above; each fix names its regression tests.
- Result files: `jarvis/evals/neko95/results/bench-20261018-*.json` and `scorecard-v2-20261018-*.json` (untracked, labelled with the tree they ran on).
- Secret scan (gitleaks, whole `git diff HEAD` plus all new text files, 2.47 MB, 2026-10-18): 3 findings, all obviously fake test fixtures
  (two placeholder console secrets in `jarvis_bridge/tests/test_client.py`, one fake key marker in `desktop/tests/test_ocr_entries.py`). No real secret.
