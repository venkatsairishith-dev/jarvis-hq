# 00 — Baseline: this machine and what already exists (2026-10-16)

Everything here was observed on the development machine on 2026-10-16 (commands in the session log). "Tested" means a recorded test or live check passed with its output and exit status; "present" means the code exists but was not exercised here.

## Machine

| Item | Value |
|---|---|
| OS | Windows 10 Pro 10.0.19045 |
| CPU | Intel Core i9-9980HK, 8 cores / 16 threads |
| RAM | 31.9 GB (17.1 GB free at the time) |
| GPU | AMD Radeon Pro 5500M, 4 GB (RDNA1). llama.cpp Vulkan works, but only with `-fa off` |
| Audio | AMD High Definition Audio Device, Apple Audio Device (MacBook Pro hardware) |
| Disk | C: 22.1 GB free of 176 GB; D: 19.1 GB free of 75 GB (project rule: large files on D:) |
| Apps | Edge, Chrome, VS Code (user install). **No PowerPoint, no LibreOffice.** |
| Runtimes | Node 24.19, Git, WSL (the owner's OpenJev service runs there), Python 3.11 venvs on D: (`neko-venv`, `jarvis-venv`, `privacy-venv`), uv at `D:\jarvis\tools\uv` |

## The application today

The repository root is **N.E.K.O** (Project-N-E-K-O/N.E.K.O, Apache-2.0, upstream 3ffcf7b9). The JARVIS project lives under `jarvis/` (decision D18). Its pieces:
- the companion web UI with a Live2D/VRM character;
- a memory server;
- an agent server with browser-use;
- a plugin runtime;
- the JARVIS kernel (authority, approvals, audit, connectors);
- local models and a privacy gateway.

Everything starts at login (`JarvisNekoStack`) and is self-healed every 10 minutes (`JarvisNekoWatchdog`).

| Requirement area | What exists | Status |
|---|---|---|
| Character UI and states | N.E.K.O web UI (Live2D/VRM) at 48911; Edge app-mode window (`open-desktop-app.ps1`); always-on-top status widget (`jarvis/widget`, 48940) | UI tested (live check: loads in English, no script errors). No Neko icon or state machine with sleeping, waking or thinking states |
| Click activation | Widget quick-task box; N.E.K.O chat; main-agent panel | Tested |
| Wake word | N.E.K.O sherpa-onnx keyword spotting (`main_logic/voice_input/wake_word/`). Default keywords are "Yui"/"悠宜"; it needs a custom native runtime ("1.13.8+neko.kws2") and `NEKO_WAKE_WORD_MODEL_DIR` | **Off.** The runtime build needs a C++ toolchain. openWakeWord "hey_jarvis" was benchmarked on synthetic audio only (`jarvis/bench/wakeword`) |
| Push-to-talk / mic | N.E.K.O voice pipeline mode (ASR → text model → TTS) | **Mic → reply never tested end to end.** The realtime voice-to-voice mode has no provider (Dahl is text only) |
| Speech recognition | Local int8 Parakeet-TDT 0.6B (en) and IndicConformer (hi, te) through `local_speech_server.py` (48930) | Tested. Telugu voice note: 86% word recall |
| Speech output | Kokoro (en), Piper (hi, te) through the same server | Tested (live check) |
| Model routing | `local_team`: keyword rules → Jev decision model (OpenJev, 8091) → Ling; llama-swap (8080) loads on demand and unloads after TTL | Tested (D19, D20) |
| Local models | Ling-3.0-tiny 31 tok/s, Qwen2.5-Coder-1.5B 80, Qwen2.5-Math-1.5B 96 (Vulkan) | Benchmarked |
| Remote models | Dahl MiniMax-M2.7 (~11 tok/s) through the privacy gateway (48950). GLM and DeepSeek are rationed | Tested |
| Hardware detector | none | Missing |
| Memory budget and model manager | llama-swap TTL and one specialist on the GPU at a time | Partial: no RAM budget or hardware profiles |
| Provider fallback, multiple keys | JARVIS kernel providers (`jarvis/config/providers.json`, lanes, kill switch); N.E.K.O config slots | Partial: no per-provider key rotation or quota detection in the N.E.K.O path |
| Orchestrator | `main_agent` plugin: plan-then-run, closed catalogue, held steps, checkpoints/resume, sub-agent kinds (think, plugin, browser, jarvis, computer); JARVIS lab agents | Tested (71 tests, live panel runs) |
| Task timeline UI | main-agent panel (auto-refresh); widget shows the newest run | Partial: no pause or retry; cancel exists as `stop_task` |
| Research with citations | `web_search` plugin (AnySearch/Baidu/DuckDuckGo, **stopped**); browser-use helper | Missing: no evidence model, citation tracking or verification |
| MCP | JARVIS connectors (D17: allowlist, kernel-gated); N.E.K.O `mcp_adapter` plugin (**stopped**) | Present; registry UI partial (JARVIS console) |
| Computer control | browser-use ready (`/browser_use/availability`); computer-use ready (MiniMax); JARVIS laptop node (`pcopen.py` opens apps and files); `app_launcher` plugin (**stopped**) | Present. Real-profile Google, song playback and VS Code flows not verified |
| PPTX | none | Missing |
| PDF | none. `doc_tools` writes md/txt/docx only | Missing |
| Memory and learning | N.E.K.O 5-layer memory; `learn_vault`; JARVIS evals (`jarvis/evals`) | Present. No feedback capture or prompt/skill evals loop |
| Security | JARVIS kernel: owner PIN approvals (PIN **not set yet**), audit, kill switch, lanes; secrets as file references in `D:\jarvis-secrets` (not the OS credential store); privacy gateway; Telegram off | Tested, except the owner-only PIN |

## Missing information and limits found

- The owner PIN is not set, so approval flows can only be tested up to "waiting for approval".
- No PowerPoint or LibreOffice: PPTX rendering checks and slideshow playback need a viewer or renderer installed (decided in the PPTX section).
- The wake-word native runtime is not built.
- The microphone path has never run end to end on this PC.
- Disk: D: has 19.1 GB free. Every install must be checked against that.
