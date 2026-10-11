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
