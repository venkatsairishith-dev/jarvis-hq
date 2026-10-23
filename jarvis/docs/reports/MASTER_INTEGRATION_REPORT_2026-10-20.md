# Master integration report (2026-10-20)

Linux test server `/srv/jarvis/home/hq-neko`, branch `remediation-2026-10-18`, HEAD `88ec49a2` (unchanged). 344 uncommitted
lines in `git status --short` (341 at the start of this pass; earlier remediation work preserved), 0 staged, 0 stashed.
Nothing committed, pushed, deployed or published. No credentials, provider policies, security rules, model routing,
retention or production configuration changed. Caretaker off. Oracle VM and `hermes-agent-vm-2` untouched.

Status words: **VERIFIED** (executed here with real output), **PARTIALLY VERIFIED** (real for part, the rest stand-in or
mocked; says which), **IMPLEMENTED — NOT VERIFIED**, **BLOCKED** (needs a device/service named), **NOT IMPLEMENTED**.

## 1. Architecture (verified from the code)
- **Kernel** (`jarvis/kernel/jarvis_kernel`): authority for policy tiers, approvals bound to the action digest, kill
  switch, hash-chained audit, idempotency, jobs, automations (scheduler with run budgets, runs/day, auto-pause), memory.
  Unknown actions are RED: `_rule_level` fails closed.
- **N.E.K.O** (front end): main_agent (plan-first, closed catalogue, outcome judge), desktop plugin (PC planner,
  coordinator, missions, lock, OCR via kernel `diag` jobs), wake listener `jarvis/wake/wake_listener.py` (sherpa-onnx,
  en/hi/te, VAD, barge-in off by default), local speech server (Kokoro/Piper TTS, sherpa STT), widget state
  (`jarvis/widget/neko_state.py`).
- **Hermes**: a lab agent (with OpenCode, goose) reached only through the kernel; the owner's Hermes uses
  `jarvis/lab/jarvis_mcp.py`; memory plugin `jarvis/hermes/plugins/jarvis_memory`; skills only via `jarvis/skills/skillgate.py`.
- **Console** (`jarvis/console` + `jarvis/ui`): 127.0.0.1 only, master token, one-time launch code -> HttpOnly
  SameSite=Strict cookie + double submit; screens CommandCenter (approvals), Tasks, Automations, Connectors, Models
  (measured tokens per provider, 24 h), Memory, Bench, AgentFactory, Desktop, Workspace, Settings.
- **Workbench** (`plugin/plugins/workbench`, this and the previous pass): client projects, Prompt Generator, learning,
  skills lifecycle, briefing. 30 entries; execution, enabling and rollback and imports are owner-only (`confirm=true`) and
  excluded from main_agent's planner.
- **Telegram**: switched off (`config/telegram.off`), as the owner's standing rule says.

## 2. Test results
Baseline (16:22Z, before this pass's edits): workbench 60 passed / 1 skipped; main_agent 490; hermes 6; lab 171 / 61 skipped;
skills 4; research 125 / 2 skipped; jarvis_bridge 31. (The baseline kernel/console logs captured the wrong lines; the
final run below has them.)

Final (all `nice -n 19` in the `neko-test` container; commands in the runbook):
| Suite | Result |
|---|---|
| kernel (`unittest discover`) | **Ran 1031, OK (22 skipped)**, rc 0 |
| console (`unittest discover`) | Ran 231, OK (3 skipped), rc 0 |
| console (`pytest tests`, also counts plain-function tests incl. the new phase tests) | **245 passed, 3 skipped** |
| workbench | **76 passed, 1 skipped** (the skip: upstream Harness² check without `HARNESS2_SRC`; run with the pinned checkout: 1 passed) |
| research (with NLI model) | 129 passed, 2 skipped |
| main_agent | 504 passed |
| jarvis_bridge | 31 passed |
| _shared | 174 passed, 2 skipped |
| doc_tools | 10 passed |
| deliverables | 36 passed |
| learn_vault | 18 passed |
| local_team | 49 passed |
| desktop | 465 passed |
| hermes | 6 passed |
| lab | 171 passed, 61 skipped (root-only lab-box tests) |
| skills | 4 passed |
| caretaker | 197 passed |
| `plugin/tests` (with network for the uvx ruff test) | **4302 passed, 5 skipped**, rc 0 |
| ruff (workbench, research `sources.py`/`fetch.py`) | clean |
| tsc (Workbench + main_agent panels; a control with a deliberate error gives 2 errors) | rc 0 |
| `check_plugin_metadata` (workbench, main_agent, research) | OK, 0 refused |

**Workbench count reconciliation:** 52 -> 57 -> 58 -> 59 -> 60 in the previous session as tests were added, 60 at this pass's
baseline, 76 now (skill lifecycle 8, briefing 2, prompts 2, intake 1, learning 3). The earlier lifekit failure
(`test_ambiguous_nearby_results...`) also fails on the clean base (6/6), lifekit is untouched; not re-run this pass.

## 3. Capability matrix
| Capability | Status | Evidence / what is missing |
|---|---|---|
| Kernel authority: approvals, kill switch, audit, fail-closed unknown actions | **VERIFIED** (tests) | kernel 1031 OK incl. `test_every_execution_route_refuses_during_a_kill`, `test_kill_switch_blocks_even_approved_actions`, `test_kill_switch_stops_the_scheduler...`; live kernel not exercised here |
| Workbench intake: chats, transcripts, PDF/Word/MD/CSV, isolation, injection quarantine | **VERIFIED** (synthetic data) | workbench tests; traversal, symlink, cross-project and injection tests |
| Requirement traceability: ID, normalized text, quote, source + location, confidence, basis, conflicts, validation status, sensitive-data flags | **VERIFIED** (synthetic) | `test_items_carry_basis_confidence_status_and_sensitive_flags` |
| Plan: scope, tasks, dependencies, milestones, risks, acceptance criteria, permissions, test plan | **VERIFIED** (synthetic) | same test; plan.md sections |
| Deliverables checked against requirements; invented figures caught | **PARTIALLY VERIFIED** | real local model (Qwen3-4B) previous pass: an English brief **verified**; invented figures flagged; Telugu/Hindi need hand checks; decks need the laptop |
| Prompt Generator: create, edit, save, version, regenerate, compare (with explanation), rename, duplicate, export, target/side effects, provenance | **VERIFIED** (tests) | `test_rename_duplicate_compare_export_and_provenance`, `test_copy_only_targets_are_not_executed` |
| Prompt execution through the kernel; kill switch / unreachable JARVIS refuse | **PARTIALLY VERIFIED** | gate logic with a fake bus (tests); the real kernel path needs the laptop console |
| Learning: research -> draft -> sandbox -> PPC -> validated skill | **PARTIALLY VERIFIED** | LIVE: real search (AnySearch via web_search's own function), real page fetch, NLI check (3 of 9 sentences supported, the rest marked unconfirmed); local-model drafts failed and were **refused** (not validated); a deliberately wrong workflow failed 0/2; the corrected one validated with the two real sources in its provenance and was reused on new input (general check passed). Unit tests: redraft with errors, wrong-input repair, pair composition |
| Skill lifecycle: inspect, reuse in sandbox, regression + quarantine, disable/enable, rollback, export, duplicates, untrusted import | **VERIFIED** (tests) | `test_skillhub.py` (8 tests incl. a malicious SKILL.md rated "reject" and never activated) |
| Harness² compatibility | **VERIFIED** | upstream demo via pinned, hash-checked checkout: passed (2 done, 0 failed) |
| GitHub research source (public API, no login) | **VERIFIED** (live) | real facts for google-research/harness2; offline tests for cleaning and failures |
| Web search from this server | **PARTIALLY VERIFIED** | AnySearch answers (HTTP 200); DuckDuckGo blocks this server (anti-bot 202); Reddit 403 (agent probe) |
| Briefing (facts with sources, suggestions marked, unread sources named) | **VERIFIED** (synthetic records) | `test_briefing.py`; no schedule created |
| Voice phases (idle, listening, transcribing, planning, researching, executing, awaiting approval, validating, speaking, completed, needs review, cancelled, failed) + real mic state | **IMPLEMENTED — server logic VERIFIED** | `jarvis/widget/neko_state.py` `voice_view`; 3 new tests; "listening" only with a healthy unmuted recording stream; never "completed" for unfinished runs |
| Wake word, STT, TTS, barge-in on a real microphone/speaker | **BLOCKED** | needs the Windows laptop (mic, speaker, sherpa models, speech server) |
| Desktop control (open apps, browser, files, OCR, verify) | **PARTIALLY VERIFIED** | desktop 465 tests with fakes; real Windows runtime BLOCKED |
| Build an app from a spoken request (code, preview) | **BLOCKED** | the route is main_agent code mode -> JARVIS lab agents; lab box + Hermes CLI not on this server; no preview tested |
| Audio intake (real recordings) | **BLOCKED** | no ffmpeg and no speech models on this server; tested with an injected transcriber |
| Memory: retrieval, correction, conflict, forget (real erasure + tombstone), agent and lane isolation | **VERIFIED** (existing kernel tests) | `test_memory.py`, `test_memory_retrieval.py` within kernel 1031 OK |
| Memory: per-fact citations; hypothesis / suggestion / model-summary kinds; client-scoped memory | **NOT IMPLEMENTED** | needs a kernel schema change: owner decision (section 7) |
| Mission Control (agents, jobs, approvals, schedules, usage, memory) | **PARTIALLY VERIFIED** | existing console screens cover most; Workbench skills live in the N.E.K.O panel; no unified skills/voice view in the console yet; provider-reported cost vs estimate not separated in a dedicated view |
| Multi-agent delegation with scope/budget/acceptance | **PARTIALLY VERIFIED** | main_agent plans + kernel lab dispatch (tests); Workbench prompts carry scope, tools, rules, acceptance; live lab BLOCKED |

## 4. What this pass changed (files)
New:
- `plugin/plugins/workbench/skillhub.py`: the skill lifecycle described in section 3.
- `plugin/plugins/workbench/tests/test_skillhub.py` and `test_briefing.py`.
- `plugin/plugins/research/sources.py`: the read-only GitHub adapter.
- `plugin/plugins/research/tests/test_sources.py`.
- `jarvis/docs/reports/RUNBOOK_WORKBENCH_2026-10-20.md` and this report.

Changed:

**Workbench:**
- `requirements.py`: annotations and sensitive-data flags.
- `planning.py`: scope, risks, acceptance criteria, permissions and test plan.
- `prompts.py`: runtimes, target info, provenance, rename, duplicate, compare and export.
- `service.py`: prompt operations, `reuse_skill` and `briefing`.
- `library.py`: duplicate check on save.
- `learning.py`:
  - GitHub repository sources;
  - a redraft carrying the validation errors;
  - examples passed to the drafter;
  - general checks for new data;
  - active-skill-only familiarity.
- `ppc.py`:
  - relative patch operations;
  - pair composition;
  - verdicts by behaviour only;
  - the input-name repair;
  - regex repairs.
- `localmodel.py`:
  - the model proposer;
  - the combined proposer;
  - a drafter with an example and the cases;
  - context-window budgets.
- `__init__.py`: 14 new entries, briefing included.
- `ui/panel.tsx`: the Briefing tab, prompt and skill controls.
- Test files and `plugin.meta.json`.

**Research:**
- `fetch.py`: an `accept_json` option, off by default, so existing callers are unchanged.
- `plugin.meta.json`.

**main_agent:**
- `planner.py`: three more owner-only entries excluded from plans.
- `outcome.py`: schemas for the new entries.
- `tests/test_outcome_registry.py`: examples for them.
- `plugin.meta.json`.

**Widget:**
- `jarvis/widget/neko_state.py`: `PHASES`, `mic_state`, `phase_of` and `voice_view`, added next to `decide`, which is unchanged.
- `widget_server.py`: publishes `voice` next to the existing state.
- `jarvis/console/tests/test_neko_state.py`: 3 tests.

## 5. Tools evaluated (exact revisions; nothing installed or run except the Harness² demo offline)
| Project | Revision | License | Verdict |
|---|---|---|---|
| google-research/harness2 | 1cfe254e8d25 | Apache-2.0 | method adapted (`ppc.py`); upstream executors not used (unrestricted network, Codex sandbox off) |
| garrytan/gbrain | 6e1086492dbd (2026-10-20) | MIT | adapt ideas only. It is a second memory system (Bun and Postgres, about 66 cron jobs, cloud model calls per note). Kernel memory already has forget-with-tombstone; citations are an owner decision |
| veltri-23/hermes-gbrain-memory | 41d128f346d4 | MIT file, copyright line names Nous Research (ambiguous) | reject: an exclusive memory provider would replace the kernel-backed one |
| NousResearch/hermes-agent skills + voice docs | 0319e113c960 | MIT | keep skillgate as the only install path; reviewed bump of its pin later. Voice options read (section 6) |
| iflytek/skillhub | 6c739b838ed5 | Apache-2.0 | reject: Java, Postgres, Redis and MinIO, built for teams |
| Panniantong/Agent-Reach | 94f06c1969df | MIT | adapt: GitHub adapter built; cookie or login channels (X, Reddit, LinkedIn) rejected; Jina and Exa not used (third parties) |
| thanhtantran/agent-reach-hermes | a5ce9e4bf428 | none (all rights reserved) | reject |
| nexu-io/open-design | 3b0df90f305c | Apache-2.0 | ideas only. It launches about 28 agent CLIs with permission bypasses, has a terminal, and telemetry is on by default |
| attentiondotnet/open-design | c3712b738866 | Apache-2.0 | reject: stale, unattributed copy |
| qdivan/hermes-studio, lysislab/hermes-studio | 1034313fcac9, c96ae3a55933 | BSL 1.1, non-commercial | reject: forks; bind 0.0.0.0; terminals; skip-permission agents |
| JasonNing96/hermes-studio | c4382cfe7fa4 | MIT | ideas only (approvals inbox, per-run cost, health strip). Auth is off by default and there is a LAN shell |
| "Hermes Apollo" / "Agent OS" (the three videos) | none public | — | Only on the creator's paid community (all three descriptions point to skool.com). No public repository found by GitHub search. Captions need a player token, so they were not read. Only titles, descriptions and chapters were checked |

## 6. Voice: what the videos and Hermes docs describe vs what JARVIS has
| Need | Official Hermes (docs, 0319e113) | JARVIS today |
|---|---|---|
| Wake word | openWakeWord "hey hermes" (local), sherpa, Porcupine (key) | sherpa-onnx keyword spotter "Hey Neko" (local) |
| STT | local faster-whisper; Groq / OpenAI / ElevenLabs (keys, cost) | sherpa-onnx Parakeet (en), IndicConformer (hi, te), local |
| Streaming STT | OpenAI / xAI / ElevenLabs only (cloud) | not implemented (file transcription per command) |
| End of speech / VAD | silence detection | Silero VAD + end-silence timer |
| TTS | Edge, Piper, NeuTTS, KittenTTS local; ElevenLabs, OpenAI, Gemini paid | Kokoro (en), Piper (hi, te), local |
| Barge-in | interrupt by voice | stop-word barge-in, off by default, unverified on hardware |
Gaps: streaming STT and full-duplex. Both of Hermes's paths for them are cloud and paid; nothing was added without your decision.

## 7. Owner decisions
1. **Kernel memory:** add per-fact citations and kinds (hypothesis, suggestion, model summary) and client-scoped memory. This is a schema migration.
2. **Laptop installs:** ffmpeg and openpyxl; any streaming STT provider (cloud and paid) or a local streaming model.
3. **External AI per client project:** use of an external provider (`set_provider_policy`).
4. **Briefing schedule:** a morning briefing as a kernel automation (not created).
5. **Console work:** add the Skills and Voice views to the console (front-end work, behind the existing auth), or keep them in the N.E.K.O panel.
6. **Skill gate pin:** review and bump skillgate's pinned hermes-agent commit.
7. **Earlier decisions:** see `COMPLETION_REMEDIATION_REPORT_2026-10-20.md` section 11 (gh login, lab proxy, kill-switch policy, PC lock, retention).

## 8. Resources and cost
- **Spent:** nothing. All local; the GitHub public API and AnySearch were used anonymously.
- **Live checks on this server:** the local model ran on 2 or 3 CPU threads at `nice 19`. A research run took about 23 minutes on that budget; it is faster on the laptop.
- **New daemons, schedules or credentials:** none.
- **Limits:**
  - The GitHub API allows 60 calls an hour unauthenticated.
  - AnySearch's anonymous limits are not documented.
  - A run reports a failure instead of retrying without bound.

## 9. Next steps
- **Laptop:** see the runbook's laptop-only steps:
  1. Workbench panel.
  2. A real audio file in en, te and hi.
  3. A prompt run, with the kill switch on and then off.
  4. A deck.
  5. A real workflow run, then `check_workflow_output`.
  6. Wake word and phases on the widget: does "listening" appear only while recording, and does the mute stop it?
- **Lab box:** a code task from a coding prompt, through JARVIS, ending with a preview checked in a browser.
- **Answers to section 7.**
