# 07 Integration decisions

Date: 2026-10-11. Inputs: reports 01 to 06 in this folder (each carries its own evidence, pins and UNVERIFIED list). This file
records what was decided, why, and what the decisions are *not*. Status words used below: **decided** (a choice), **built** (code
in the repo, tested), **deployed** (running on the server or laptop). Nothing in this file claims more than its status word.

## 1. The shape of the system

```
 browser (React app, Edge)  <-- 127.0.0.1 only -->  JARVIS Console (Windows, Python, stdlib server)
   mic -> WAV (push to talk)                           |  voice: STT/TTS on this CPU (sherpa-onnx, Piper)
   text, approvals, screens                            |  holds the OWNER token (D:\jarvis-secrets), never sent to the browser
                                                       |  serves the built UI, previews files without running them
                                                       v
                           kernel owner plane  /v1/jarvis/owner/*   (Oracle VM, existing llm-proxy process)
                              command -> owner's Hermes -> kernel-gated tools -> lab / laptop node
                              approvals (YELLOW + PIN), task control, stop-all, kill, automations, agent definitions, memory
                              scheduler tick + sweeper (replaces the loops that lived in the Telegram bridge)
```

- **One authority.** The kernel decides every side effect. A model (local classifier, planner, Hermes) only proposes.
  The console and the UI hold no authority of their own; they hold an *owner token* that reaches only `/v1/jarvis/owner/*`.
- **Existing parts kept:** kernel task engine, jobs, policy, audit, lab runner and verifier, laptop node, Hermes, hub
  (noVNC take-control), gateway and lanes, evals. Nothing was rewritten; the kernel gained an owner plane, transactions
  and two tables (see 01 section 12 for the gaps this closes).
- **Why a console process on the laptop** (and not the browser talking to the kernel directly): the owner token must not
  live in a web page; voice models must run on the client; file previews and the take-control proxy need a local process.

## 2. Component decisions (reuse first)

| Need | Decision | Source (pin) | License (code / weights) | Status |
|---|---|---|---|---|
| Task engine, memory, approvals, audit | **Keep** the kernel; add owner plane, `tx()` + compare-and-set, real erasure | in repo | own | built |
| Scheduler | **Own ~400 lines** (`automations.py`): definitions in `state.db`, pure `tick(now)`, `UNIQUE(automation_id, run_key)`. Rejected APScheduler/Celery/Temporal (second daemon + store) and Hermes cron (outside kernel authorization/audit, no outcome verification; report 05 section 7) | docs/research/05 | own (cron matcher is checked against `croniter` 3.x, MIT, as a *test oracle* only) | built |
| Front end | **React 19 + TypeScript + Vite + Tailwind 4** in `hq/ui/`, as report 04 recommends; hub CSS tokens reused; the vanilla hub stays | npm packages pinned in `package-lock.json` | MIT | built (see status table in the final report) |
| Event vocabulary | **AG-UI-style** named events over SSE (pattern only, no SDK); the audit `seq` is the event id | report 04 | MIT (pattern) | built |
| noVNC / take control | **Keep** `lab/lab_control.py` + hub Caddy `/pc/`, `/control/`; embed from the console | in repo | own / MPL-2.0 noVNC | built |
| STT English | **Parakeet TDT 0.6B v3 int8 via sherpa-onnx** (RTF 0.11 to 0.14 measured). Fallback whisper.cpp small-q5_1 | sherpa-onnx `9db1af18` v1.13.8; HF `nvidia/parakeet-tdt-0.6b-v3` `541d1f99` | Apache-2.0 / **CC-BY-4.0 (attribution)** | built |
| STT Hindi, Telugu | **AI4Bharat IndicConformer int8 CTC via sherpa-onnx** (RTF 0.04 to 0.05 on synthetic speech; **not yet measured on real microphone speech**) | `parismitaglobalsolutions/indicconformer-sherpa-onnx` `9721eb71` | Apache-2.0 export / MIT weights | built |
| VAD | Silero v6 through sherpa-onnx | silero-vad `1e261b03` v6.2.3 | MIT | built |
| TTS English | **Kokoro v1.0 fp32** via sherpa-onnx (RTF about 0.5). int8 is 3x slower on this CPU: not used | `hexgrad/Kokoro-82M` `f3ff3571`; sherpa `kokoro-multi-lang-v1_0` | Apache-2.0 / Apache-2.0 (espeak-ng GPL-3.0 inside sherpa's TTS path) | built |
| TTS Hindi, Telugu | **Piper** voices `hi_IN-rohan-medium`, `te_IN-venkatesh-medium` (CC-BY-4.0) run as a **separate process** so the GPL-3.0 runtime is not linked into the console | `OHF-Voice/piper1-gpl` `6321ef99` v1.8.0 | GPL-3.0 runtime / per-voice | built |
| Wake word | openWakeWord `hey_jarvis`, **off by default, opt-in**, only after push-to-talk is proven. Its pretrained models are CC BY-NC-SA 4.0: personal use only | `dscripka/openWakeWord` `368c0371` | Apache-2.0 / **CC BY-NC-SA 4.0** | decided (not enabled) |
| Windows control | **Extend the node's own adapter** (fixed job kinds, kernel approval first, read-back verification). **Do not adopt** UFO, Browser Use or OpenHands as runtimes: each has its own loop and a model-judged confirmation, which would be a second permission authority (report 03) | adapt patterns from Windows-MCP `b74c507` v0.8.8 and UFO `a795552` v3.0.10 (both MIT) | MIT | built (see final report for exactly which operations) |
| UIA access | PowerShell UIA (existing `winbackend.py`) extended; `uiautomation` 2.0.29 (Apache-2.0) only if a persistent worker is needed | pip | Apache-2.0 | decided |
| Screenshots | `mss` (BSD) | pip | BSD-3 | built |
| Browser automation | Playwright for Python behind Smokescreen (existing `browser.py`), wired as a kernel job kind later | existing | Apache-2.0 | decided |
| Small decision model | **Keep** the project's kNN + logistic + Qwen3-4B union for risk; Laya/Verdict (OpenJev) stay *advisory*. Measured on the project's 260-item set: union 87% exact with 3/200 false-allow; Verdict 58% / 35 false-allow; Laya 32% / 143 false-allow. **Fine-tuning Laya is not done**: it does not meet the bar "measurably improves held-out performance without more unsafe selections" and the benchmark that would prove it does not exist yet | bench/ (re-verified in 01 section 9); OpenJev `75f22b6` | Apache-2.0 | decided |
| Local LLM | llama.cpp b11461 Vulkan, Qwen3-4B Q4_K_M (22.5 tok/s Vulkan, 9.3 CPU, measured). Trial Qwen3-4B-Instruct-2507 | existing | MIT / Apache-2.0 | decided |
| Memory search | SQLite FTS5 (available, 1 to 11 ms at 100k notes); **no vector DB** | report 05 | public domain | decided (not needed yet at 200 entries per owner) |

Rejected on the evidence: UFO as runtime (8 advisories 2026-08-10 to 09-22, pinned old deps, no password refusal); Windows-MCP
unfiltered (PowerShell/Registry tools, past unauthenticated HTTP advisory, telemetry on by default); Browser Use (telemetry on
by default, 40+ deps, 30% on Online-Mind2Web independent vs 89% self-reported); pyautogui (unmaintained since 2023);
AutoHotkey and dogtail (GPL); OmniParser icon-detect weights (AGPL); Open WebUI, LobeHub, Dyad `src/pro` (custom licenses);
OpenHands enterprise directories (PolyForm Free Trial).

## 3. Decisions on the questions the research left open

1. **May the browser approve actions?** Yes for `YELLOW_APPROVAL` only, and only with (a) the owner PIN, set once at the
   kernel console (`python3 -m jarvis_kernel set-approval-pin`), (b) the approval id typed again, (c) rate limit of 5 wrong
   PINs per 10 minutes. With no PIN set, HTTP approval is refused (fail closed). `RED` approvals and lifting the kill
   switch stay at the kernel console. Deny, cancel, pause, stop-all and kill need no PIN (safe direction). Reason: the
   directive requires approvals in the Command Center, while the owner's standing rule is that a model or a spoken
   sentence can never approve. A spoken "yes, approve" is never an approval; only the typed confirmation is.
2. **"DHAI".** The string does not occur anywhere in the repo or vault. The only integration matching the description
   (owner-allocated token pool, public lane only) is **Dahl** (`inference.dahl.global`, providers `dahl-minimax`,
   `dahl-glm`). All "DHAI" tests use Dahl, with public-lane content only. If the owner meant a different provider, the
   provider entry in `config/providers.json` is the only thing to change.
3. **Docs cap.** `CLAUDE.md` allows 5 files in `docs/`. The directive names `docs/research/` for seven documents; they are
   in that subfolder and the top level of `docs/` still holds its five files.
4. **Reddit.** Neither search nor fetch could read reddit.com from this environment (reports 05 and 06). Practitioner
   evidence comes from Hacker News, vendor forums, GitHub issues and arXiv, each labelled. No Reddit claim is made.
5. **Agent definitions run in the lab only (public lane).** A "specialist" is a lab agent with instructions, criteria and
   limits. The kernel enforces lane, attempts, time, runs per day, verification and delivery; it does not enforce the
   tool names inside the lab agent (the lab runner fixes those), and says so in the definition itself.

## 4. Threat decisions that shape the code

- Prompt injection can arrive through any page, file or screen an agent reads. Mitigations carried over from the kernel:
  untrusted-session egress removal, scrubbing, single-use hash-bound approvals, no agent holds an owner token.
- **UI-driven approval abuse:** an agent that could click the console window could approve its own request. The owner PIN
  is the answer (an agent never sees it), and policy keeps `ui` actions against the console/browser window denied.
- **Local file preview** never executes content: sandboxed iframe + `Content-Security-Policy: sandbox` + `nosniff`,
  extension allow-list, `outputs\` root only (realpath-checked).
- **Loopback exposure:** the console binds `127.0.0.1`, checks `Host` and `Origin`, and refuses other origins (DNS
  rebinding and cross-site POST).
- **Microphone:** audio is captured only while the button is held (or toggled) with a visible recording state, processed
  locally, and not stored unless the owner enables it.

## 5. Verification record (2026-10-11)
Every number below was produced by a process that exited with the status shown; counts are from the final tree.

**Automated**
| Suite | Result | Notes |
|---|---|---|
| Kernel (`kernel/tests`, Windows, 11 min) | 359 run, OK, 30 skipped, exit 0 | skips are Linux-only and Telegram-off cases. Baseline before this work: 278 |
| Kernel on the server, in place (Linux ARM) | OK, 0 failures | 349 run on the first deploy (38 skipped); after two follow-up syncs the full discovery was run again (39 skipped) before any restart |
| Evals (`evals/run_evals.py`) | 23/23, no regressions | |
| Console (`console/tests`) | 17 OK | one intermittent error was seen once while other suites ran; 3 clean reruns; the stream test's timeout was raised |
| Browser suite (Playwright on installed Edge) | 45 passed | 9 screens x 2 themes accessibility (axe, WCAG 2.1 AA, no serious/critical), keyboard, phone width, no-microphone, real voice path with a fake microphone, kill switch |
| Intent evaluation (62 held-out items) | see `bench/data/intent_results.json` | Qwen3-4B 0.85, rules 0.79 (optimistic), embeddings+LR 0.65, TF-IDF 0.48, Laya 0.39, Verdict 0.19 |
| gitleaks on the repo | no finding in anything added | two pre-existing randomly generated test strings in `bench/scrub_probe.py` (not touched) |

**Live, against the real system** (real console, real kernel on the server, real Hermes, real laptop node, real lab with Dahl models, public lane only)
1. *Voice-controlled computer operation.* A recorded request played through the browser's microphone path was transcribed locally (Parakeet, model load 4.2 s cold), shown in the box, sent, JARVIS answered "Opened Downloads", and the laptop node's own check found the Explorer window "Downloads" (`verified`). One earlier run reported `unconfirmed` because the folder window was already open; the check now accepts an existing window that is in front, with a note.
2. *File creation.* "Build vowelcount.py with tests and a README, verify it, deliver it" became a lab task, was independently verified on attempt 1 and delivered 4 files to `outputs\task-<id>\`. I ran the delivered tests from a clean folder myself: 14 tests OK, and the tool counted vowels correctly. Hermes created the same task three times for one request; the first one completed. A kernel guard now refuses a second open task with the same request (deployed, tested).
3. *Agent creation.* A sentence became a reviewable definition (criteria, limits, what the kernel enforces and what it does not), was activated and ran a bounded test through the lab: completed on attempt 1.
4. *Automation.* A one-minute automation was previewed (next runs and side effects), created, run now, fired by the scheduler on its own slot (both runs `done`, the node answered), then paused and deleted; the delete was read back.
5. *Speech response.* The reply is shown in writing and spoken by the local TTS, with Stop speaking and mute. The browser suite plays it with an audio element; the live run confirmed the TTS call counter, not what a human heard.
6. *Self-correction.* The kernel proposed one lesson from real history (task `5079247275a1`, completed after 3 attempts). It is a poor lesson (it copies the verifier's noise) from a task the owner's own check later corrected, which exposed a real gap: the gate now refuses origins that carry a `correction` event. The recovery attempts themselves (fail, repair, verify) are the lab runner's existing behaviour and were seen in that task's history; no new failure was injected today.
7. *Operator control.* While an agent's test task ran, "Take control" switched the Agent PC to owner mode and froze one task (its step and update time did not change for 20 s); "Continue" handed it back and the task completed.

**Defects the live run found, all fixed and redeployed:** `state` took 28 s on the server because of the deep scrubber (now 35 to 60 ms); `config/watches.json` had an invalid escape and was silently ignored (now valid, and loud); the console could not resolve `hub.localhost` from Python; PC buttons stayed disabled while a job waited for an offline node; the announcer would have asked JARVIS about every historical task on its first tick (now only tasks that end after start); duplicate tasks (above); verification of an already-open window (above).

**Not done or not proven, stated plainly**
- Wake word: decided, not built (weights are non-commercial).
- Hindi and Telugu speech were tested on synthetic speech only (TTS to STT round trip); real microphone accuracy is unmeasured.
- Browser approvals need a PIN that only the owner can set at the kernel console; the approval path is tested end to end against a throwaway kernel, not live.
- File-arrival triggers: the watcher and kernel side are tested; no live arrival was exercised.
- Laya was not fine-tuned (reasons in D15). No second run of the lab scenarios with a deliberately failing task.
- Reddit was unreachable from the tools; no Reddit evidence is claimed.
- The local small LLM server is not running on the laptop at present, so the intent hint uses its rules fallback.

## 6. Hardening verification record (2026-10-11, second pass: everyday use)
Scope: do not rebuild; use the system as the owner will, fix what breaks, and say what could not be proven. Every number comes from a process that exited with the status shown. "Live" means the real console, the real kernel on the server, the real Hermes, the real laptop node and the real lab (Dahl models, public lane); nothing was mocked. Telegram stayed off; `hermes-agent-vm-2` was never touched; no secret was read, printed or committed.

**State at the end of the pass**
- Repository: the commit is named in section 6.4; nothing was pushed. Server code was verified by SHA-256 against the working tree after every sync (`owner.py`, `llm_proxy.py`, `jobs.py`, `nodes.py`, `telegram.py`, the node agent); services were restarted only when no job was running. The laptop node and the console were restarted only as the components concerned.
- Rollback: each server sync kept the previous files in `~/downloads/sync<N>-<ts>/`; `kernel.old.<ts>` from the earlier deploy is still the full-tree fallback. The MCP tool description (`/opt/jarvis-mcp/jarvis_mcp.py`) has a `.bak-<ts>` beside it and takes effect at the next Hermes start.

### 6.1 Automated
| Suite | Result |
|---|---|
| Kernel, Windows (jarvis-venv, Python 3.12) | superseded by section 7: the first run of that day had one temp-folder cleanup error (an environment race, section 7.2); the two full runs of the third pass are 396 run, OK, 24 skipped, exit 0 each |
| Kernel, on the server in place (Linux ARM, before any restart) | 396 run, OK, 39 skipped, exit 0 at the final sync (before its restart); every earlier sync's full run was also OK (391 to 396 tests) |
| Console (`console/tests`) | 27 run, OK, exit 0 (scoring, Voice check endpoints, resident Piper worker, single-instance bind, POST-to-page refusal, intent circuit breaker, intent, console routes) |
| Evals (`evals/run_evals.py`) | 23/23, exit 0 |
| Browser suite (Playwright, installed Edge) | 51 passed, exit 0 (accessibility on every screen in both themes, keyboard, phone width, no microphone, fake-microphone voice path, interrupt and mute, microphone recovery, abandoned command, approvals, kill switch) |
| Self-correction runner, real `handle()` with a scripted worker (WSL) | 3 run, OK: a real failure is observed, repaired, re-verified and delivered; a task the owner later corrects cannot teach; a repair that never works ends failed with nothing delivered |
| gitleaks on the staged change | one finding, inspected: an invented test string that asserts a key is never returned; marked with the repo's inline allow, re-scan clean |

### 6.2 Live results
1. **Approvals.** A pending approval shows the exact tool, operation, target and full arguments (to 2,000 characters), why it asked, and the digest; denial from the real UI was final and read back from the kernel (screenshots 20 and 21 in `outputs\console-evidence`). Approving over HTTP needs the owner's PIN, which only the owner can set at the kernel console, so **the live approve-with-PIN run is not done**. It is proven end to end on a throwaway kernel (wrong PIN, typed id, single use, changed action refused, replay refused, expiry, audit chain) in kernel tests and in the browser.
2. **Voice (English; real engine; fake microphone fed the approved phrase).** Stop to transcript 0.9 s warm; transcript "Open my downloads folder."; the kernel recorded a `voice` command and a real `pc_open` job, the node verified the window, the reply was spoken, and the spoken text equalled the displayed text (107 KB WAV, 2.2 s). STT 0.33 to 0.47 s per 2.0 s clip (about 0.2 real-time factor, 2.1 to 2.6 CPU-seconds); English TTS (Kokoro) 1.8 to 1.9 s per sentence (7.3 to 8.7 CPU-seconds); Hindi and Telugu (Piper, resident worker) 0.07 to 0.11 s warm, against 5.9 to 8.4 s per sentence before. Memory: console 1.2 to 1.5 GB (Parakeet, Kokoro, Silero), Piper worker 0.29 GB, local reasoning-model server 1.7 GB; about 17 of 32 GB free throughout; speech runs on the CPU and the GPU stays free. Cold first call: STT 4.3 s, TTS 5.4 s. Installed: sherpa-onnx 1.13.8, piper-tts 1.8.0; model licences are in `docs/research/02` section 6 and in the `voicebench` output.
   - **Not proven:** Hindi and Telugu recognition from a real microphone and a real voice. The scoring tool (Settings, Voice check; word and character error rate per phrase) exists and is tested; the owner has to speak the phrases. No claim of production readiness for either language.
3. **Wake word.** Measured, not enabled (D16): 96 of 96 synthetic positives; 10 of 128 negatives fired, all containing "Jarvis", including JARVIS's own replies; English only; weights CC BY-NC-SA 4.0. No real-microphone false-activation rate exists. Push to talk stays.
4. **File-arrival automation** (disposable `D:\jarvis\cache\watch-test` only, the single entry in `config/watches.json`; no personal folder watched). Through the real UI: only that folder was offered; the preview stated trigger, target, action and side effects; the definition persisted; a `.txt` written and appended 300 ms later fired exactly one run, which returned a real window list (4 windows); a non-matching file and a node restart fired nothing; a file arriving while paused did not run and was not replayed on resume; the next file after resume ran; after the scheduler's service (`jarvis-llm-proxy`) restarted the definition survived and fired; delete was read back and a later file fired nothing. Evidence: screenshots 30 and 31.
5. **Self-correction.** A live lab task with criteria that cannot both hold ("exactly 10 bytes" and "the first 100 digits of pi"): attempt 1 was found failing by the independent verifier (100 bytes, not 10) and the finding was fed back; attempt 2 satisfied the first criterion and broke the second (a real oscillation); attempt 3's verifier wrote no verdict (classified `tool`); the retry budget ended it as `failed`. Nothing was delivered, no lesson was proposed, and the owner was told the task could not be met. Gap found: the final reason ("the verifier wrote no VERIFY.json") hides the substantive findings of attempts 1 and 2; they are in the task history, not in the headline. The repaired-and-verified path is proven with the real runner against a scripted worker (WSL test) and by an earlier natural live repair (3 attempts); the lesson gate for owner-corrected tasks has its own tests.
6. **Memory.** Through the real UI: add; correct (version moved, a stale correction refused with 409, the old text only in history); forget (gone from the list, history erased, a later change refused); the audit chain verified (12,483 events).
7. **Provider and tokens.** The endpoint is `inference.dahl.global` (authenticated, HTTP 200). *Corrected in section 7:* Dahl does document a key-balance endpoint (`GET /tokens/current`) and a 100M-token new-account gift into a pool that is allocated to keys; this pass first recorded, wrongly, that no balance endpoint existed. Its terms do not promise zero retention: public-lane content only (unchanged). Measured usage was blind for streamed calls (nested `usage` objects were not parsed) and is now recorded per provider. The limit that mattered today: the private lane's free providers (OpenRouter 50 requests a day, Groq about 8k tokens per request against the agent's 10k) were exhausted by testing, and commands now say so at once.
8. **Reliability.** Browser reload mid-command (command and answer survive); console restarted mid-command (the kernel finished it, the page reconnected, the owner token still held); laptop node stopped (the job stayed queued) and restored (the job completed and the window list showed); "Stop waiting" (cancelled, late answer ignored, the next command not refused as busy); kernel service restarted mid-command (the command failed honestly with "the kernel restarted before JARVIS answered", nothing stuck); console boundary (no token in any response; a foreign Origin, a missing header, a rebinding Host, API-plane and path-traversal attempts all refused; a POST to a page path is now 405).
9. **Local model recovery.** Qwen3-1.7B on Vulkan: 3 s cold start, 1.7 GB, about 230 ms per classification; killed hard, the console answers from its rules and voice is unaffected; the dead server is left alone for 30 s (a refused loopback connection costs 2 s on Windows).

### 6.3 Defects this pass found and fixed (all redeployed, each with a test)
Stale "approve on Telegram" text shown to the agent; measured token usage empty for every streamed call; an agent answering "already open" from memory with nothing run; a spoken request waiting up to 15 s for the node to look for work; a full agent conversation returned as a normal answer and repeated for the rest of the day; "Stop waiting" leaving the gate closed for up to 15 minutes; commands sitting "running" through the agent's 10-minute back-off when providers were resting; a second console silently starting beside the first (Windows port sharing); a POST to a page path answered with the page; a job held by a restarted node staying "running" for 30 minutes; `GET /watches` returning 500 on a kernel where no node had ever paired; punctuation handling in the Hindi and Telugu scorer; a stale schema assertion in a Windows-only collector test.

### 6.4 Open items, stated plainly
- **Owner only:** set the approval PIN (`python3 -m jarvis_kernel set-approval-pin`), then run `ui/e2e-live/approval.spec.ts` part B with `JARVIS_APPROVAL_PIN` set for that run; speak the Voice check phrases in English, Hindi and Telugu (Settings, Voice check).
- The private lane has no provider that can take the agent's request size and still has quota today; a larger free private-lane provider remains the owner's decision (open since the morning of 2026-10-11). The provider cool-down is held in memory and is rediscovered after a kernel restart by one failed request (which now ends the command at once instead of after 10 minutes).
- Agent PC takeover and resume, and delivery of lab artifacts, were exercised in the first pass and not repeated in this one.
- The Project JARVIS plan document could not be updated from here (no stored link); the plan note is D16.
- Commit: the one that contains this section (`git log -1 -- docs/research/07-integration-decisions.md`); nothing was pushed.

## 7. Final verification and provider audit (2026-10-11, third pass)
Scope: confirm the exact state, close the Windows test issue, audit the model providers from official sources, prepare the owner-only steps, re-run the regression. No application code was changed for the first two; one console defect was found and fixed in the last. Nothing was pushed, no secret was read or printed, no PIN was set, Telegram stayed off, `hermes-agent-vm-2` was never contacted, and no service was restarted while a job was running (the only restart was the laptop console, with no command running).

### 7.1 State found (read-only inspection)
- Working tree clean at `d190c3e`; three commits ahead of `origin/main` (`f6fdb3f`, `73f8a32`, `d190c3e`); nothing pushed.
- Server and laptop kernel trees byte-identical (22 files, SHA-256); `config/providers.json` identical on both; node agent identical.
- Server: `jarvis-llm-proxy`, `jarvis-kernel`, `jarvis-hermes-gateway` active; no queued or running job, no running owner command, no open task, no pending approval; the laptop node last seen seconds earlier; approval PIN not set; kill switch off.
- Laptop: exactly one process listening on 127.0.0.1:8799; one node process pair; no `llama-server` or Piper worker left over; `config/telegram.off` present on both machines and no Telegram process.

### 7.2 The `test_vault` temporary-folder error
- **First failure (earlier today):** one error in a full Windows run: `PermissionError [WinError 32]` while `tempfile` removed `D:\jarvis\cache\tmp\tmp…\vault`, in `test_secrets_scrubbed_and_keys_blocked` (a different test of the same class each time it recurs). That run had `TEMP` inside the project folder.
- **Run 1 of this pass** (`-X dev`, temp on `C:`): 396 run, OK, 24 skipped, exit 0.
- **Isolation experiments** (`test_vault.py` alone, repeated; the same interpreter and code):
  | Temp folder | Runs | Failures |
  |---|---|---|
  | `C:\Users\venkat\AppData\Local\Temp`, `-X dev`, ResourceWarning as error | 12 | 0 |
  | `D:\jarvis\cache\tmp` (inside the IDE-watched workspace) | 25 | **6** (always on the `vault` folder, which contains empty `.git` and `.obsidian` folders; test names varied) |
  | `D:\jarvis-apps\tmp` (same drive, outside the workspace) | 25 | 0 |
- **Run 2 of this pass** (temp on `D:\jarvis-apps\tmp`): 396 run, OK, 24 skipped, exit 0.
- **Conclusion:** a Windows handle race between the test's temporary folder and a process watching the workspace tree while `tempfile` deletes the folder. The holder was not identified by name; the evidence is that failures occur only inside the watched tree (6 of 25) and not outside it (0 of 25 and 0 of 12, plus two clean full runs). The vault module closes the handles it opens (it uses `with open`). It is created by where the tests put their temporary folder, not by an application defect. No test and no application file was changed for it; `docs/05_OPERATIONS.md` now says to run the Windows suite with `TEMP` outside `D:\jarvis`.
- **Skipped tests:** all 24 skips are `test_browser` ("playwright not installed in this Python"). The same module run in the browser venv: 35 run, OK, 0 skipped, exit 0.

### 7.3 One defect found by the verification run and fixed
A new console test (a POST to a page path) failed once with `ConnectionAbortedError [WinError 10053]`. Cause: the console turned some requests away (foreign Origin, rebinding Host, POST to a page path, unknown API route) without reading the request body; closing a socket that holds unread data resets the connection on Windows, so the client saw an abort instead of the refusal. Reproduced deterministically first (150 KB body, failed on the first request), then fixed by reading a bounded amount (1 MiB) of an unread body before the response is closed. Test added; console suite 28 OK three times running; the live console was restarted (no command running) and 10 of 10 large POSTs returned 405.

### 7.4 Provider audit
Full table with sources and uncertainty: `docs/research/08-owner-handoff.md` section 3. Findings that change earlier statements:
- **Corrected:** Dahl does publish a key-balance endpoint (`GET /tokens/current`) and documents the 100M-token gift as going to a pool that must be allocated to a key. Earlier text here and in D16 said no balance endpoint existed; that was wrong because the endpoint is not on the pricing page and the earlier probe tried other paths. One call (no model invoked) returned **6,720,752 tokens available on JARVIS's key**.
- **Confirmed by the owner (2026-10-11):** "DHAI" is Dahl: the owner named `dahl-credentials.txt` as its credentials. That file (309 bytes, `D:\jarvis-secrets`) was listed by name only and never opened; the providers read `providers/dahl.key`.
- **Unverified:** the pool balance, the ledger and any expiry attached to the gift (they need an account sign-in, which is what the unopened file presumably holds).
- **Public lane only:** Dahl's privacy policy records prompts, tool definitions and outputs, runs them on independent GPU operators and disclaims zero-retention and confidentiality. The allowance cannot serve the private lane.
- **The private lane has one usable provider:** Groq's free plan allows 8,000 tokens per minute against the owner's agent's 10,000 to 12,000-token request, so only OpenRouter's free model (50 requests a day under $10 of purchased credits, 1,000 at or above; resets 00:00 UTC) can serve it. Candidate additions and a recommendation are in the handoff.
- **Router:** private tasks run only on private or local providers and never fall back to a public one (several named tests, listed in 7.5, plus the `lane_enforcement` eval, all passing in the runs above).

### 7.5 Regression and security checks (this pass, with exit statuses)
| Check | Evidence |
|---|---|
| Kernel suite, Windows | run 1 and run 2: 396 run, OK, 24 skipped, exit 0 each |
| `test_browser` in the browser venv | 35 run, OK, exit 0 |
| Console suite | 28 run, OK, exit 0 (3 runs) |
| Evals | 23/23, exit 0 |
| Browser suite | 51 passed, exit 0 (re-run after the console fix) |
| Self-correction runner in WSL | 3 run, OK, exit 0 |
| Kill switch (stops pending approvals, blocks approved actions, one-way over HTTP, stops the scheduler and the node loop) | 18 named tests in run 1, all ok; `kill.spec` in the browser suite |
| Unapproved dangerous action cannot run | `test_action_without_approval_is_refused_and_backend_not_called`, `test_red_actions_are_not_approvable_over_http`, `test_approval_for_one_action_does_not_cover_another`; eval `approval` |
| Browser cannot obtain the owner token | `test_only_an_owner_token_gets_in_and_it_gets_nothing_else`; live case E of the reliability spec (earlier today); the owner token is not in any health, state or model response |
| Private content cannot reach the public lane by fallback | `test_fallback_stays_in_lane`, `test_private_task_never_planned_on_public`, `test_only_public_available_means_private_task_fails_closed`, `test_paid_or_bad_lane_provider_never_used` |
| A failed or cancelled command cannot leave the gate stuck | `test_stopping_the_wait_hands_the_gate_back_so_the_next_command_runs`, `test_a_running_command_fails_at_once_when_the_gateway_says_the_lane_is_resting`; live case D and F earlier today |
| Wake word disabled | no wake-word code path exists in the console or the UI (searched); `bench/wakeword/` is a measurement only |
| Telegram disabled | `telegram.off` on both machines, no process; the Telegram tests use fake APIs and never contact Telegram |
| Protected VM untouched | the kernel's protected set names it and refuses it (`test_hardening`, `test_telegram` refusal cases); no command of this pass connected to it or to its address (its name appeared only in source searches of the repository) |
| Secret scan | see section 7.6 |
| Oracle cost | $0.0 for the month (Cost Analysis script, 2026-10-11) |

### 7.6 Commit
The commit that contains this section (`git log -1 -- docs/research/07-integration-decisions.md`). Secret scan of the staged change: run before the commit, result recorded in that commit's message. Nothing pushed.

### 7.7 Limitations that remain
Real-microphone Hindi and Telugu accuracy; the live approve-with-PIN run; the private lane's capacity; Dahl's pool balance and expiry; the OpenRouter account's privacy toggles; the live voice-to-task spec has not been re-proven since the providers ran out of quota (earlier today it passed); agent takeover and artifact delivery were last exercised in the first pass.
