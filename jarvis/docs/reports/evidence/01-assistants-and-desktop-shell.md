# 01: Desktop assistants, local-first assistants and the desktop shell (2026-10-16)

Scope: (1) full desktop AI assistants and JARVIS-like projects, (3) low-resource local-first personal assistants, and the desktop shell question. The shell must give an always-available companion with an animated character icon and visible states (sleeping, wake-word listening, waking, listening, thinking, working, speaking, completed, needs permission, error/offline). It also needs click activation, push-to-talk and low idle CPU.

This was research only: nothing was installed, cloned or run. GitHub metadata comes from the GitHub REST API (`gh api repos/...`, read-only) on 2026-10-16.

**Evidence labels**
- **[V-src]**: verified from source code, a licence file or a workflow file (read through the API or from the local checkout at `D:\jarvis\hq-neko`).
- **[V-api]**: verified from GitHub API metadata, the release list or the issue tracker.
- **[C]**: the project's own claim (README, website, model card). Not verified.
- **[I]**: my inference from the evidence.
- **[U]**: unverified. Needs a measurement or test on this PC.

Machine context comes from `00-baseline.md`: Windows 10 19045, i9-9980HK, 32 GB RAM, Radeon Pro 5500M 4 GB shared with llama.cpp Vulkan, C: 22 GB free, D: 19 GB free.

---

## 1. Verdict in one paragraph

**Keep N.E.K.O as the base. No candidate is worth switching to.** None of them has the JARVIS kernel (owner-PIN approvals, audit, MCP connector allowlist) or N.E.K.O's memory and plugin stack, and the only comparable companion (AIRI) would mean redoing every integration.

**Shell: the official upstream N.E.K.O.-Tauri shell** (https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri, Apache-2.0). Use it at a pinned commit, plus a small local patch set (tray, global shortcut with press/release for push-to-talk, single instance, a click-through fail-safe). It is the only open-source desktop host that already speaks N.E.K.O's page contract. It attaches to an already-running backend on 127.0.0.1:48911. Its click-through hit test already knows N.E.K.O's Live2D, VRM, MMD and PNGTuber managers.

**The upstream Electron shell is not an option:** N.E.K.O.-PC is closed source [V-src].

**Animated character icon: N.E.K.O's own "cat form"** (return-ball cat with awake, dozing and deep-sleep tiers, thought and ZZZ bubbles, click to return). It already exists in the page and can be the icon [V-src]. What must be built:
- one **state bridge**: a single state enum fed by existing signals;
- a **state badge or ring overlay** on the cat;
- the shell patches.

**Fallback if the Rust/MSVC toolchain cannot fit on disk:** a minimal Electron host (no compile step). Its event-driven click-through on Windows is better, but it needs more custom code.

---

## 2. What N.E.K.O already provides for the desktop companion (local checkout, [V-src])

| Need | Already in `D:\jarvis\hq-neko` | Evidence |
|---|---|---|
| Desktop host contract | Pages detect host preload globals (`window.nekoChatWindow`, `window.nekoSubtitle`). The pet window loads `/`, chat windows load `/chat` or `/chat_full`, and subtitles load `/subtitle`. Cross-window state uses the `BroadcastChannel('neko_page_channel')` | `docs/frontend/index.md` lines 23-29; `docs/frontend/pages.md` line 43 |
| Electron host | **Closed source.** The CI checks out `Project-N-E-K-O/N.E.K.O.-PC`, and a comment calls it "闭源的 N.E.K.O.-PC" (the closed-source N.E.K.O.-PC). The public URL returns 404 | `.github/workflows/build-desktop.yml` lines 14, 1004, 1166; https://github.com/Project-N-E-K-O/N.E.K.O.-PC (404) |
| Small animated character with sleep states | "Cat form": after goodbye (manual, or automatic after 10 min idle), the model hides and a draggable, clickable cat appears. Tiers: `CAT1` awake (10 min), `CAT2` dozing (15 min), `CAT3` deep sleep (18 min). Click = return. A thinking bubble shows while audio plays, and a sleeping ZZZ bubble shows in CAT2/CAT3. Recording or voice sessions, running tasks and the chat grace window block auto-goodbye. The same code runs in the web page and in the Electron pet renderer | `docs/design/cat-idle-states-feature.md` sections 2, 3, 5.2, 8.1, 8.8, 8.9; assets `static/assets/cat-resources/appearance/dev_neko/idle/cat-idle-cat1.gif`, `static/assets/neko-idle/thought-items/sleeping-zzz.gif` |
| Page events usable as state signals | WebSocket message types handled in `static/app/app-websocket.js`: `session_preparing`, `session_started`, `user_transcript_preview`, `user_transcript`, `gemini_response`, `audio_chunk`, `audio_done`, `agent_status_update`, `agent_task_update`, `agent_notification`, `status` (error codes), `session_failed`, `session_ended_by_server`. CustomEvents: `neko:websocket-disconnected`, `neko:voice-session-started`, `neko-assistant-turn-start`, `neko-focus-thinking`, `neko-audio-stream-closed`, `neko:idle-return-ball-state`, `neko:session-ended-by-server` | grep of `static/app/*.js` (lines 3313-6102 of `app-websocket.js`; `app-buttons.js:2570`; `cross-window-broadcast-and-bridge.js:635`) |
| Backend error codes | `send_status` codes such as `CONNECTION_REFUSED`, `API_KEY_REJECTED`, `API_RATE_LIMIT_SESSION`, `MEMORY_SERVER_CRASHED`, `LLM_CONNECTION_FAILED` | `main_logic/core/lifecycle.py` lines 739-794 |
| Wake-word activation state machine | `ActivationState`: `disabled`, `preparing`, `waiting`, `verifying`, `replaying`, `active`, `unavailable`, `closed`. **No push of this state to the page was found** (searched `main_logic` and `main_routers`) | `main_logic/voice_input/activation/contracts.py` lines 10-20; `main_logic/voice_identity_service/activation_runtime.py` |
| Wake-word native runtime | Upstream CI builds and tests a Windows Python 3.11 runtime wheel (`wake-word-runtime.yml`, `windows-2022`). The newest artifact `wake-word-runtime-windows-py311-9cdc4df…` (17.9 MB, run 37173444856, 2026-10-04) **expires 2026-10-18** [V-api]. Downloading it needs a GitHub login. Its provenance and integrity must be checked before use. Not downloaded here | `gh api …/actions/runs/37173444856/artifacts` |
| Idle throttling | v0.9.0 notes: "Idle-state throttling across Live2D, VRM, MMD, and PNGTuber" [C] | https://github.com/Project-N-E-K-O/N.E.K.O/releases/tag/v0.9.0 |
| Desktop GPU cost | Issue #3059 (closed): the desktop (Electron) rendering of N.E.K.O uses much more GPU than the same page in a browser. #3341 (closed 2026-10-13): Live2D canvas initialised at screen size instead of viewport (4x the pixels), and physics stays at 60 Hz even at 30 fps drawing | https://github.com/Project-N-E-K-O/N.E.K.O/issues/3059, https://github.com/Project-N-E-K-O/N.E.K.O/issues/3341 |
| Current JARVIS widget | Edge `--app` window pinned top-most with `SetWindowPos(HWND_TOPMOST)`. It polls `/api/status` every 5 s, and each poll fans out 8 HTTP probes (one with a 6 s timeout). Read-only for approvals. Opaque, no tray, no global hotkey | `jarvis/scripts/open-widget.ps1`; `jarvis/widget/index.html:130`; `jarvis/widget/widget_server.py` lines 91-127 |

N.E.K.O upstream facts [V-api]:
- Apache-2.0, 3,063 stars, pushed 2026-10-15, 84 open issues.
- Latest stable release v0.9.0 (2026-08-20). Its Windows bundle is `N.E.K.O_v0.9.0.1_win.zip` at 1.27 GB.
- A nightly build exists (2026-10-02).
- Telemetry is on by default and is turned off with `DO_NOT_TRACK=1` [C] (README).

https://github.com/Project-N-E-K-O/N.E.K.O

---

## 3. Full desktop assistants and JARVIS-like projects

All metadata is [V-api] as of 2026-10-16 unless marked otherwise.

| Project | Licence (verified file) | Purpose | OS | Last release / push | Install complexity | Offline and mandatory APIs | MCP / sub-agents / tools / browser | Security, open issues | Reuse for Neko | Fit for this PC |
|---|---|---|---|---|---|---|---|---|---|---|
| **N.E.K.O** (base) https://github.com/Project-N-E-K-O/N.E.K.O | Apache-2.0 | Live2D/VRM companion, voice, memory, plugins, agent | Win/mac/Linux [C] | v0.9.0 2026-08-20; push 2026-10-15 | Already installed | Core voice expects a Realtime API, auxiliary needs ChatCompletion [C]; local wiring done by JARVIS | MCP adapter plugin (stopped), browser-use, computer-use, plugin SDK [V-src] | 84 open issues; telemetry on by default [C] | Base | Running today |
| **Open Interpreter** https://github.com/openinterpreter/openinterpreter | Apache-2.0 | Now a **Rust coding agent forked from Codex** ("A coding agent optimized for low-cost models") [C]. The Python version moved to the community fork `endolith/open-interpreter` [C] | Win/mac/Linux [C] | rust-v0.0.56 2026-10-07 | Installer script | OpenAI-compatible providers; local models not stated [C] | MCP, skills, hooks, permissions, OS sandbox [C] | 15 open issues | Nothing for the desktop shell. At most an optional coding sub-agent | Not an assistant UI |
| **01** https://github.com/openinterpreter/01 | AGPL-3.0 | Voice interface for desktop and ESP32 | – | **No push since 2024-11-01**; 78 open issues | – | – | – | Stale; AGPL | No | Reject (stale) |
| **OpenHands** https://github.com/OpenHands/OpenHands | MIT | AI software development ("Agent Canvas", Electron) | – | push 2026-10-15 | Heavy | – | Dev agent | Already assessed in `jarvis/docs/research/03-computer-control.md` | Patterns only | Not an assistant |
| **Leon** https://github.com/leon-ai/leon | MIT | Personal assistant; 2.0 is a "Developer Preview" on `develop`, and `master` holds the pre-agentic version [C] | Linux, macOS, Windows [C] | Only component pre-releases (tcp-server_v2.0.0 2026-02-19); push 2026-10-12 | Node ≥ 24, pnpm, web app on :5366 [C] | Local and remote providers [C]; no wake word stated | Skills → actions → tools; agent mode; computer use [C]; MCP not mentioned | 114 open issues; docs "not ready yet" [C] | No | Unstable, overlaps N.E.K.O |
| **OpenVoiceOS** https://github.com/OpenVoiceOS/ovos-core | Apache-2.0 | FOSS voice-assistant platform | Linux; Windows only through WSL2; macOS 15+ [C] (https://raw.githubusercontent.com/OpenVoiceOS/ovos-installer/main/README.md) | ovos-core 3.7.4a1 (alpha) 2026-10-08 | Ansible installer or Docker | Plugin STT/TTS/wake word | Personas, skills; no MCP noted | `ovos-shell` archived [V-api] | Wake-word ideas only | Poor on native Windows |
| **Rhasspy 3** https://github.com/rhasspy/rhasspy3 | MIT | Voice toolkit | – | **Archived**, last push 2023-12-26 | – | – | – | Archived | No | Reject |
| **Home Assistant Assist** https://www.home-assistant.io/voice_control/ | Apache-2.0 (core) | Smart-home voice | Needs an HA server; **no Windows desktop client mentioned** [C] | core push 2026-10-15 | HA server plus satellites | "can work locally" [C] | HA tools | – | Only as an MCP connector target | Not a desktop assistant |
| **Jan** https://github.com/janhq/jan | Apache-2.0 (LICENSE asks for attribution) | Offline ChatGPT alternative; Tauri app; llama.cpp engines incl. Vulkan; local API on :1337 [C] | Win 10+, mac, Linux [C] | v0.8.6 2026-10-09 | Installer | Fully local possible [C] | MCP [C]; assistants | **579 open issues**; no voice [C] | No (llama-swap already covers local models) | Runs, but redundant |
| **LM Studio** https://lmstudio.ai | **Proprietary** app ("valuable trade secrets", no reverse engineering); licence limited to personal and internal business use; some paid features. CLI `lms` is MIT | Local model runner | Win/mac/Linux | – | Installer | Local | – | Closed source | No (closed; conflicts with the reuse-first open-source rule) | – |
| **AnythingLLM** https://github.com/Mintplex-Labs/anything-llm | MIT | Local-first RAG, agents, desktop app | Mac/Win/Linux [C] | v1.18.0 2026-10-13 | Installer or Docker | llama.cpp, Ollama, LM Studio, cloud [C] | MCP-compatible, agents with web browsing, scheduled tasks [C] | **Telemetry on by default** (`DISABLE_TELEMETRY`) [C]; 325 open issues | Pattern only | Overlaps N.E.K.O memory and agents |
| **Open WebUI** https://github.com/open-webui/open-webui | "Open WebUI License": BSD-3 plus a **branding clause** forbidding removal of "Open WebUI" branding [V-src] | Chat UI | Server/web | v0.12.2 2026-10-14 | Docker/pip | Ollama/OpenAI | Tools, pipelines | Branding clause | No code copying (as already decided in research 04) | – |
| **Agent Zero** https://github.com/agent0ai/agent-zero | MIT [V-src] | General agent framework with an XFCE desktop canvas | Wherever Docker runs [C] | v2.14 2026-10-13 | **Docker effectively required** [C] | Configurable providers | Sub-agents, MCP, browser agent [C] | Advises isolation; CLI connector can reach the host [C]; 132 open issues | Pattern (sub-agent hierarchy) | Docker on Win10 with 19 GB free on D: is costly [I] |
| **Khoj** https://github.com/khoj-ai/khoj | AGPL-3.0 | "AI second brain", self-hostable | Web, desktop, phone [C] | 2.0.0-beta.28 2026-03-26; push 2026-08-02 | Docker [C] | Local or online LLMs [C] | Agents, automations; MCP not mentioned | AGPL | No | Redundant |
| **OpenJarvis** https://github.com/open-jarvis/OpenJarvis | Apache-2.0 | Stanford Hazy Research / SAIL "Personal AI, On Personal Devices"; Tauri desktop [C/I] | Win (PowerShell installer), mac, Linux, WSL2 [C] | desktop-v1.0.2 2026-05-25; push 2026-10-15 | Installer plus Ollama [C] | Local-first, cloud when needed [C] | Eight built-in agents incl. `native_openhands` [C] | 54 open issues | UI patterns (already assessed in research 04 §2.2) | Possible but redundant |
| **isair/jarvis** https://github.com/isair/jarvis | **Non-commercial "Jarvis AI Assistant License"**, derivatives under the same terms [V-src] | Private local desktop assistant: floating face, tray, Whisper, Piper, Ollama, MCP [C] | mac/Win/Linux, "developed primarily on macOS" [C] | v2.7.7 2026-10-14 | PyInstaller app | Local by default [C] | MCP [C] | Licence blocks code reuse | **Design reference only**: a PyQt6 `face_widget.py` with states `ASLEEP, IDLE (waiting for wake word), LISTENING, THINKING, SPEAKING, DICTATING` shared across processes through a temp state file [V-src] | – |
| **Priler/jarvis** https://github.com/Priler/jarvis | **CC BY-NC-SA 4.0** [V-src] | Offline Rust voice assistant, "WIP" [C] | – | push 2026-02-18 | – | Offline [C] | – | Non-commercial licence | No | – |
| **microsoft/JARVIS** https://github.com/microsoft/JARVIS | MIT | HuggingGPT research system (paper 2303.17580) | – | push 2025-07-29; **4,221 open issues** | – | – | – | Research code | No | Not an assistant |
| **goose** https://github.com/aaif-goose/goose | Apache-2.0 | MCP-native agent with a desktop app | – | v1.54.0 2026-10-08 | Installer | Providers | MCP-native | – | Agent pattern | Already noted in research 04 |
| **Cherry Studio** https://github.com/CherryHQ/cherry-studio | AGPL-3.0 | "AI productivity studio" (Msty-like) | Desktop | push 2026-10-15; 1,765 open issues | – | – | MCP, agents [C] | AGPL | No | Msty-like alternative only |

Notes:
- **"JARVIS" projects with real traction.** By GitHub search for "jarvis in:name" sorted by stars [V-api], only microsoft/JARVIS (25.4k, research) and OpenJarvis (11.3k) have large, active followings. sukeesh/Jarvis (3.8k) is a Linux/macOS CLI, last pushed 2025-12-01. Priler/jarvis (3.0k) and isair/jarvis (2.1k) have non-commercial licences. None replaces N.E.K.O plus the kernel.
- Msty itself is closed-source freeware [I]. Its open alternatives (Cherry Studio, AGPL; LobeHub, custom licence per the API's `NOASSERTION`) are chat studios, not desktop companions.

---

## 4. Character, companion and desktop-pet projects

| Project | Licence | Desktop tech | Overlay features | Last release / push | Offline | MCP / agent | Reuse for Neko |
|---|---|---|---|---|---|---|---|
| **AIRI** https://github.com/moeru-ai/airi | MIT [V-api] | Electron desktop app "Stage Tamagotchi" [C]. Main window uses `transparentWindowConfig()`, `setWindowAlwaysOnTop(...)` and `setIgnoreMouseEvents(false)` [V-src: `apps/stage-tamagotchi/src/main/windows/main/index.ts`]; window folders include `desktop-overlay`, `widgets`, `spotlight`, `caption` [V-src] | Live2D and VRM, client-side speech recognition and talking detection [C] | v0.12.0-beta.5 2026-08-29; 50.4k stars; 236 open issues | WebGPU in browser, Ollama, native CUDA/Metal [C] | MCP only through a separate "MCP Launcher" [C] | **Pattern source (MIT, copy allowed with notice)** for Electron overlay windows if the Electron fallback is chosen. Switching to AIRI would drop N.E.K.O memory, plugins and every kernel integration. Not recommended |
| **Open-LLM-VTuber** https://github.com/Open-LLM-VTuber/Open-LLM-VTuber | Backend MIT, except Live2D sample models [V-src] | Frontend **Open-LLM-VTuber-Web** (Electron 31, React): pet mode = `transparent: true`, `setAlwaysOnTop(true,'screen-saver')`, `setIgnoreMouseEvents(true,{forward:true})`, tray menu [V-src: `src/main/window-manager.ts`, `menu-manager.ts`] | "transparent background desktop pet mode", global top-most, click-through [C, matches source] | Backend v1.2.1 2025-08-26 (push 2026-05-15); Web v1.2.1 2025-08-21 (push 2025-09-05) | "can run completely offline" with sherpa-onnx, Whisper, Ollama [C] | `mcp_servers.json` present [C] | **Frontend licence = "Open-LLM-VTuber License 1.0"** (Apache-2.0 plus extra conditions; commercial hosting or sale needs a licence) [V-src]. Design reference only. v1 is maintenance-only, and a v2 rewrite is planned [C] |
| **Desktop Mate** (Steam app 3301060) | Proprietary | Unity | Desktop mascot; developer says no official VRM loading planned (Steam post) [C] | – | – | – | No |
| **MateEngine** https://github.com/shinyflvre/Mate-Engine | "MateEngine Pro License v2.1": no public redistribution of modified versions [V-src] | Unity | VRM desktop pet | push 2026-01-20 | – | – | No (licence) |
| **VPet** https://github.com/LorisYounger/VPet | Apache-2.0 [V-api] | C# WPF desktop pet; 7.0k stars; push 2026-10-15 | Pet states and animations (Steam app) [C] | – | – | Mod and plugin system [C] | Would be a second, unrelated character stack; not N.E.K.O's avatar. No |
| **BongoCat** https://github.com/ayangweb/BongoCat | Apache-2.0 [V-api] | Now a **native Rust/gpui** app with its own Live2D renderer (crates `bongocat-overlay`, `bongocat-live2d-render`, `gpui-kit`) [V-src: `crates/bongocat-app/Cargo.toml`], not a web shell | Transparent keyboard-reactive cat | v2.3.1 2026-10-13; Windows exe 10.4 MB | – | – | Shows how small a native overlay can be; cannot host N.E.K.O's web avatar. No |
| **Clippy** https://github.com/felixrieseberg/clippy | MIT for the code; **Clippy art is Microsoft's** [V-src] | Electron plus node-llama-cpp (reference for `@electron/llm`) [C] | Retro chat window [C] | v0.4.3 2025-05-08 | Local [C] | – | Pattern only |
| **live2d-widget** https://github.com/stevenjoezhang/live2d-widget | GPL-3.0 | Web widget | – | push 2026-10-15 | – | – | No (GPL; N.E.K.O already renders Live2D) |
| **pixi-live2d-display** https://github.com/guansss/pixi-live2d-display / **three-vrm** https://github.com/pixiv/three-vrm | MIT / MIT | Renderers N.E.K.O already bundles [I from #3341] | – | 2024-08-20 / 2026-10-02 | – | – | Already in use |

---

## 5. Desktop shell options

### 5.1 Comparison

| Option | Licence | Transparent always-on-top overlay on Win10 | Click-through on transparent pixels | Tray | Global hotkey with **release** (hold-to-talk) | Build / install on this PC | Idle cost | Reuse vs custom |
|---|---|---|---|---|---|---|---|---|
| **A. Keep Edge `--app` window** (today) | – | **No transparency** (Chromium app mode has no transparent-window option [I]); top-most only through an external `SetWindowPos` (as `open-widget.ps1` does) [V-src] | No | No | No | Nothing new | One Edge renderer process plus a 5 s poll [V-src] | All in place, but cannot show a floating character |
| **B. N.E.K.O.-Tauri** https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri | Apache-2.0 [V-src] | Yes. `transparent: true, alwaysOnTop: true, decorations: false, shadow: false`; window sized to the **whole virtual desktop** [V-src: `tauri.conf.json`, `platform/windows.rs`] | Yes, through `set_ignore_cursor_events` plus an injected hit-test bridge that knows N.E.K.O's `live2dManager`, `vrmManager`, `mmdManager` and `pngtuberManager` and the chat overlay ids [V-src: `click_through.js`] | **Not implemented**. Tauri 2 core has it behind the `tray-icon` feature [C: https://v2.tauri.app/learn/system-tray/] | **Not implemented**. `tauri-plugin-global-shortcut` 2.4.1 events carry `state: 'Pressed' or 'Released'` [V-src: `plugins/global-shortcut/guest-js/index.ts`] | No prebuilt release. Needs Rust (MSVC) plus "Desktop development with C++" Build Tools [C: https://v2.tauri.app/start/prerequisites/]. Uses WebView2, which is supported on Win10 22H2 until at least Oct 2028 [C: Microsoft Edge lifecycle, reported by https://borncity.com/win/2025/08/12/windows-10-22h2-microsoft-edge-will-receive-updates-until-october-2028/] | A **cursor "radar" thread polls every 33 ms when idle (16 ms when active) and emits an event to the page at least every 100 ms**, forever [V-src: `radar.rs`] | **Highest reuse.** Official, already integrated with the N.E.K.O page; attaches to an existing backend on 48911 [C, README] |
| **C. Own minimal Electron host** | MIT (Electron) | Yes (`transparent`, `setAlwaysOnTop(level)`) [C: Electron docs] | Yes. `setIgnoreMouseEvents(true, {forward: true})` forwards mouse moves on **Windows** and macOS, so hit-testing is event-driven with no poll [C: https://www.electronjs.org/docs/latest/api/browser-window]. Transparent areas do not click through by themselves [C: https://www.electronjs.org/docs/latest/tutorial/custom-window-styles] | Built in | `globalShortcut` fires on **press only**; a conflict "will silently fail" [C: https://www.electronjs.org/docs/latest/api/global-shortcut]. Hold-to-talk needs `uiohook-napi` (MIT, prebuilt N-API, a global low-level keyboard hook) [V-api/V-src], or toggle-to-talk | No compile: Node 24 already present. Electron v44.7.2 Windows zip is 158 MB [V-api]. Windows 10+ supported [V-src: Electron README] | Event-driven; Chromium base memory (see 5.2) | **Most custom code.** The upstream host (N.E.K.O.-PC) is closed, so main, preload and the `window.nekoChatWindow` / `window.nekoSubtitle` contract would have to be re-written. Patterns from AIRI (MIT) are allowed |
| **D. Upstream release bundle** (`N.E.K.O_v0.9.0.1_win.zip`, 1.27 GB) | Shell is closed source [V-src] | Yes (it ships the Electron pet) [C] | Yes [C] | [U] | [U] | 1.27 GB download; bundles its own Python backend [V-api] | Issue #3059: high GPU in desktop mode [V-api] | Not inspectable, and it would run a second backend. No |
| **E. pywebview / PyQt in the existing Python venv** | BSD-3 / GPL or commercial (PyQt) | **pywebview: "transparent … Not supported on Windows"** [C: https://pywebview.flowrl.com/api/]. PyQt6 frameless translucent windows work (isair/jarvis does it) [V-src] | PyQt: per-window masks [I] | PyQt has a tray | Python `keyboard` (MIT) or `pynput` (LGPL) use global hooks [V-api] | PyQt6-WebEngine is a second Chromium (~100s of MB) [I] | – | Custom; PyQt licence (GPL) is awkward. No |
| **F. Pake** https://github.com/tw93/Pake | GPL-3.0 | No transparency option; has `--always-on-top`, `--show-system-tray`, `--activation-shortcut` [V-src: `docs/cli-usage.md`] | No | Yes | Press only [I] | Needs Rust as well | – | Could wrap the opaque widget only. No |

### 5.2 Measured facts and limits that decide the choice

- **RAM is not a differentiator.** In a CI benchmark of an empty app on Windows x64 (https://github.com/Elanis/web-to-desktop-framework-comparison) [C, third party]: Electron used ≈279 MB (release) and Tauri ≈307 MB (release), because WebView2 processes count. Bundles: Electron ≈385 MB vs Tauri ≈3 MB. Startup: Electron ≈153 ms vs Tauri ≈576 ms. Treat these as relative only.
- **Windows 10 transparency bug in Tauri.** Tauri issue **#15947 (open, 2026-08-30)**: on Windows 10, a transparent always-on-top window sporadically renders its transparent areas, or the desktop wallpaper, **black** when toggling `set_ignore_cursor_events` or when minimising and restoring. The reporter's use case is exactly a desktop pet. tao uses the legacy `DwmEnableBlurBehindWindow` hack [V-api: https://github.com/tauri-apps/tauri/issues/15947]. N.E.K.O.-Tauri toggles click-through constantly, so **this PC (Win10) is in the affected group** [I].
- **Click-through design difference.** Tauri on Windows cannot forward mouse moves while ignoring cursor events. N.E.K.O.-Tauri therefore polls the cursor (`radar.rs`) [V-src]. Related Tauri feature request #10564 (`set_ignore_cursor_events` for Webview) is still open [V-api]. Electron forwards moves (`forward: true`) [C, docs], and Open-LLM-VTuber-Web uses exactly that [V-src].
- **Tauri hold-to-talk on Windows.** global-hotkey issue #176: the Windows release detection "busy-spin[s] while waiting for release". It was closed 2026-05-01, and the current plugin depends on `global-hotkey 0.8` [V-api/V-src]. Whether the fix is in 0.8 is [U]: check CPU while the key is held.
- **N.E.K.O.-Tauri maturity** [V-api]:
  - Created 2026-07-27; 1 star; no releases; README says "实验性" (experimental) [C]. Default-branch HEAD is from 2026-07-29; PR #8 "managed native windows and chat handoffs" was closed 2026-10-14 (unmerged state [U]).
  - Open issues:
    - **#2**: after a backend start timeout or crash, the full-screen transparent window stops passing clicks through, so **the whole desktop cannot be clicked**;
    - #3: an extra console window appears, and closing it kills the backend;
    - #4: on exit it force-kills the backend, skipping the upstream shutdown;
    - #5: no single-instance guard;
    - #6: port 48911 is hard-coded;
    - #7: CSP is `null`.
  - Source: https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri/issues
- **Tauri 3 alpha exists** (tauri-v3.0.0-alpha.5, 2026-10-14) alongside 2.12.4 (2026-10-14) [V-api]. Pin Tauri 2.x.
- **Full-screen Live2D overlay GPU cost.** N.E.K.O #3059 (desktop mode uses much more GPU than the browser) and #3341 (canvas initialised at screen size) [V-api]. On a 4 GB Radeon that llama.cpp also uses, the Live2D model should not render full-time. The cat form (GIF) should be the resting presence [I]. Whether hiding the model in cat form fully stops its render loop is [U].

### 5.3 Shell decision

**Choose B (N.E.K.O.-Tauri) with a local patch set**, because:
- it is the only open-source host that already implements N.E.K.O's page contract;
- it is Apache-2.0 and upstream-maintained;
- it injects its bridges with `webview.eval` into the 48911 page, so **no N.E.K.O file has to change** [V-src: `lib.rs`].

Mitigate its four real risks:
1. **#2 desktop lock-out.** Add a native watchdog: if no hit-test heartbeat arrives for N seconds, or the backend health fails, call `set_ignore_cursor_events(false)` and shrink the window to the cat's bounds or hide it. Also add a tray item "Release mouse".
2. **Win10 black-transparency bug (#15947).** Test it on this PC first: a 30-minute soak with frequent toggles and minimise/restore. If it reproduces, try a **small window around the cat or avatar instead of a virtual-desktop-sized window**: fewer toggles and less GPU. If it still reproduces, switch to C.
3. **Idle polling.** Raise `IDLE_POLL_INTERVAL` and `IDLE_HEARTBEAT` (for example to 100 ms and 1 s) when no window is under the cursor, and stop the radar while the cat is in CAT3. Measure (see section 8).
4. **Toolchain disk.** Build with `CARGO_HOME`, `RUSTUP_HOME` and `CARGO_TARGET_DIR` on D:. The MSVC Build Tools install largely to C: [I]; the size is [U], so measure free space before and after. The same C++ toolchain also unblocks a local wake-word runtime build (`00-baseline.md`).

**Fallback C (minimal Electron host)** if #15947 reproduces or the toolchain does not fit. Copy window-manager patterns from AIRI (MIT, with notice). Use `globalShortcut` for toggle-to-talk, and add `uiohook-napi` only if hold-to-talk is required and the owner accepts a global keyboard hook.

---

## 6. Push-to-talk, click activation and tray on Windows 10

| Function | With B (Tauri) | With C (Electron) |
|---|---|---|
| Click activation | Click on the cat = existing N.E.K.O `*-return-click` chain [V-src: cat-idle doc §3, §5.3]. Click on the badge opens chat | Same page logic |
| Push-to-talk (hold) | `register('Ctrl+Alt+Space', e => e.state === 'Pressed' ? startMic() : stopMic())` with global-shortcut 2.4.1 [V-src]. `RegisterHotKey` only sees the registered chord, with no global keyboard hook [I] | Press-only `globalShortcut` → toggle-to-talk, or `uiohook-napi` hook for hold |
| Tray | `tray-icon` feature: menu (Show/Hide Neko, Mute mic, Release mouse, Pause wake word, Open JARVIS console, Quit); tooltip and icon follow the state [C, docs] | `Tray` built in |
| Single instance | `tauri-plugin-single-instance` 2.5.4 in plugins-workspace [V-src] (closes N.E.K.O.-Tauri #5) | `app.requestSingleInstanceLock()` [I] |
| Autostart | Keep the existing Task Scheduler `JarvisNekoStack` (decision in `jarvis/docs/03_DECISIONS.md`). The shell attaches to the running backend, so it needs no backend ownership | Same |

Calling `startMic()` / `stopMic()` from the shell means invoking the page's existing voice-session button logic (`app-buttons.js` dispatches `neko:voice-session-started`) through an injected function [V-src for the event; the exact entry point is [U]].

---

## 7. Visible states: signal sources and the wiring needed

There is one state enum. Display priority, highest first:

`error/offline > needs_permission > speaking > listening > waking > thinking > working > completed (transient 3 s) > wake_listening > sleeping`

| State | Existing signal (source) | Status |
|---|---|---|
| sleeping | Cat form tier `CAT2` / `CAT3` through `neko:idle-return-ball-state` (page) [V-src]; or no session, no task and no wake word | Present in page |
| wake-word listening | `ActivationState.WAITING` (backend) [V-src] | **Not pushed to the page; must expose** (a small endpoint or WS message). Wake word is **off** today (`00-baseline.md`) |
| waking | `ActivationState.VERIFYING/REPLAYING` (backend) or WS `session_preparing` (page) [V-src] | Page signal present; backend needs exposure |
| listening | WS `session_started`, `neko:voice-session-started`, `user_transcript_preview` [V-src] | Present |
| thinking | After `user_transcript` until the first `gemini_response`; `neko-focus-thinking`; `neko-assistant-turn-start` [V-src] | Present (semantics to be confirmed [U]) |
| working | WS `agent_task_update` / `agent_status_update`; main-agent run `state == running` (`widget_server.newest_run`) [V-src] | Present |
| speaking | WS `audio_chunk` … `audio_done` / `neko-audio-stream-closed` [V-src] | Present |
| completed | `audio_done` with no follow-up, or main-agent run finished (`widget_server`) [V-src] | Present; needs a 3 s transient |
| needs permission | Kernel approvals waiting: `widget_server.build_status().jarvis.approvals_waiting` from console `/api/owner/state` [V-src] | Present (read-only). Approving still needs the PIN in the console |
| error / offline | `neko:websocket-disconnected`, WS `session_failed`, `status` codes (`CONNECTION_REFUSED`, `API_KEY_REJECTED`, …), widget `servers` down, watchdog `gave_up_on`, kernel `killed` [V-src] | Present |

**Cheapest real wiring** (one owner of the state, no new repo):
1. **Page tap (~150 lines JS).** Injected by the shell exactly like `click_through.js` (`webview.eval` on the 48911 page). It listens to the CustomEvents above and wraps the WebSocket message handler. It writes the voice and chat sub-state to the shell (Tauri event) and to the widget server (`POST /api/state/page`, loopback, origin `127.0.0.1:48911` added to the allowlist).
2. **State reducer in `widget_server.py`** (already the JARVIS status aggregator). It merges the page sub-state with kernel approvals, main-agent runs, server health and the wake-word activation state into the enum above. It serves `GET /api/state` (one JSON) and **`GET /api/state/stream` (SSE)**, which replaces the 5 s polling fan-out for the badge. Health probes drop to 30-60 s.
3. **Badge overlay (~100 lines CSS/JS, injected).** A small ring and glyph anchored to the cat's or model's screen rect (use `getModelScreenBounds()`, already used by `click_through.js` [V-src]). Each state has a CSS animation, and `sleeping` uses a static frame (no animation).
4. **Tray icon** mirrors the same enum (10 small PNG/ICO variants) and its tooltip ("Neko: needs your approval").
5. The **existing Edge widget** subscribes to the same SSE. That makes every state testable **before any shell is built**: Phase 1 runs on the current stack.

States must come only from real signals; nothing is shown speculatively. Tests: replay recorded WS frames into the tap (jsdom/vitest), and unit-test the reducer in Python (`jarvis/widget` already has pure functions such as `build_status(fetch=…)` that can be faked).

---

## 8. What to reuse, what to build, how to test

**Reuse directly**
- N.E.K.O web UI, cat form, return chain and existing events [V-src].
- N.E.K.O.-Tauri: window, click-through bridge, screen-capture bridge, backend attach [V-src].
- Tauri official `tray-icon`, `tauri-plugin-global-shortcut` 2.4.1 and `tauri-plugin-single-instance` 2.5.4 [V-src].
- The JARVIS widget server as the state aggregator [V-src].

**Build (custom, small)**
1. Page tap.
2. State reducer plus SSE.
3. Badge overlay.
4. Tray menu and state icons.
5. PTT handler (Pressed/Released → mic start/stop).
6. Click-through fail-safe watchdog (fixes N.E.K.O.-Tauri #2).
7. Idle radar back-off.
8. Exposure of `ActivationState` to the page (backend, small).

Items 6, 7 and the tray belong upstream as PRs to N.E.K.O.-Tauri (reuse-first: contribute, do not fork into a new repo).

**Measure on this PC (Windows), before choosing B over C for good**
- **Idle CPU:** `Get-Counter '\Process(*)\% Processor Time'` over 10 minutes for `neko-tauri`, `msedgewebview2*` and the Python servers in three modes (cat CAT3, cat CAT1, model shown). Target [I]: total shell under 1% of one core when sleeping.
- **GPU:** Task Manager GPU engine for the webview while the model is shown versus the cat, with the llama.cpp Vulkan model loaded at the same time (4 GB VRAM).
- **#15947 soak:** 30 minutes of cursor in and out over the cat, plus minimise/restore. Watch for black transparent areas or a black wallpaper.
- **PTT hold:** hold the chord for 30 s and check that CPU does not plateau (global-hotkey #176).
- **Lock-out test:** kill the backend and confirm the desktop stays clickable (fail-safe).
- **Disk:** free space on C: and D: before and after installing Rust and the Build Tools.

---

## 9. Open risks and questions

1. **Win10 transparency bug (#15947)** may hit B on this exact OS. It is the main reason to keep C as a ready fallback [V-api].
2. **N.E.K.O.-Tauri is experimental** (1 star, 6 open issues incl. desktop lock-out, no tray, hotkey or single instance). Our patches must be small and offered upstream [V-api].
3. **Toolchain size on C:** (MSVC Build Tools) is [U]. C: has 22 GB free.
4. **GPU contention:** a full-screen Live2D overlay versus llama.cpp on 4 GB VRAM. Mitigation: cat form as the resting state, model on demand [I].
5. **Wake-word states** depend on the wake-word runtime, which is off. Upstream's CI-built Windows py311 runtime artifact **expires 2026-10-18** [V-api]. If it is to be used, the owner must decide quickly; a GitHub login is needed, and provenance must be checked. Otherwise build locally once the C++ toolchain exists.
6. **The upstream Electron shell (N.E.K.O.-PC) is closed source**, so features that exist only there (widget mode, cat window sensing through `desktop-window-sensing.js`) cannot be reused [V-src]. The cat form still works in the page, but desktop-window interactions (CAT1 window sensing) will be missing under Tauri [I].
7. **Hold-to-talk under Electron** requires a global keyboard hook (`uiohook-napi`). That is privacy- and antivirus-sensitive; prefer toggle-to-talk if C is used [I].
8. **CSP is null** in N.E.K.O.-Tauri (#7) and `withGlobalTauri: true` exposes `invoke` to the page. The capability file limits commands to click-through, open-window and screen capture [V-src: `capabilities/default.json`]. Do not add powerful commands (file system, shell) to the main-window capability, because the page also renders model and LLM output [I].
9. **Live2D Cubism Core** has its own proprietary licence terms (Open-LLM-VTuber notes that Live2D models are "licensed separately") [C]. Fine for personal use; recheck before any distribution [I].
10. **N.E.K.O telemetry** is on by default (`DO_NOT_TRACK=1`) [C]. Confirm it is set in the JARVIS start scripts [U].

---

## Sources

GitHub API metadata (licence, stars, pushed_at, releases, issues) was read with `gh api` on 2026-10-16 for every repository named above.

**N.E.K.O**
- https://github.com/Project-N-E-K-O
- https://github.com/Project-N-E-K-O/N.E.K.O
- https://github.com/Project-N-E-K-O/N.E.K.O/releases/tag/v0.9.0
- https://github.com/Project-N-E-K-O/N.E.K.O/issues/3059
- https://github.com/Project-N-E-K-O/N.E.K.O/issues/3341
- https://github.com/Project-N-E-K-O/N.E.K.O/issues/3339
- https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri (files `src-tauri/tauri.conf.json`, `Cargo.toml`, `src/lib.rs`, `src/radar.rs`, `src/platform/windows.rs`, `src/click_through.js`, `capabilities/default.json`; issues #2 to #7)

**Tauri, Electron and runtimes**
- https://github.com/tauri-apps/tauri/issues/15947
- https://github.com/tauri-apps/tauri/issues/10564
- https://github.com/tauri-apps/global-hotkey/issues/176
- https://github.com/tauri-apps/plugins-workspace (`plugins/global-shortcut/guest-js/index.ts`, `plugins/single-instance/Cargo.toml`)
- https://v2.tauri.app/plugin/global-shortcut/
- https://v2.tauri.app/learn/system-tray/
- https://v2.tauri.app/start/prerequisites/
- https://www.electronjs.org/docs/latest/api/browser-window
- https://www.electronjs.org/docs/latest/api/global-shortcut
- https://www.electronjs.org/docs/latest/tutorial/custom-window-styles
- https://github.com/electron/electron (README "Platform support"; release v44.7.2)
- https://github.com/Elanis/web-to-desktop-framework-comparison
- https://borncity.com/win/2025/08/12/windows-10-22h2-microsoft-edge-will-receive-updates-until-october-2028/
- https://pywebview.flowrl.com/api/
- https://github.com/tw93/Pake (`docs/cli-usage.md`)
- https://github.com/SnosMe/uiohook-napi

**Companions and desktop pets**
- https://github.com/moeru-ai/airi (`apps/stage-tamagotchi/src/main/windows/main/index.ts`)
- https://github.com/moeru-ai/airi/releases
- https://github.com/Open-LLM-VTuber/Open-LLM-VTuber (LICENSE)
- https://github.com/Open-LLM-VTuber/Open-LLM-VTuber-Web (LICENSE, `src/main/window-manager.ts`, `src/main/menu-manager.ts`)
- https://github.com/isair/jarvis (LICENSE, `requirements.txt`, `src/desktop_app/face_widget.py`)
- https://github.com/shinyflvre/Mate-Engine (LICENSE)
- https://steamcommunity.com/app/3301060/discussions/1/744882977527369520
- https://github.com/LorisYounger/VPet
- https://github.com/ayangweb/BongoCat (`crates/bongocat-app/Cargo.toml`)
- https://github.com/felixrieseberg/clippy (LICENSE)
- https://github.com/stevenjoezhang/live2d-widget
- https://github.com/guansss/pixi-live2d-display
- https://github.com/pixiv/three-vrm

**Assistants**
- https://github.com/openinterpreter/openinterpreter
- https://github.com/openinterpreter/01
- https://github.com/OpenHands/OpenHands
- https://github.com/leon-ai/leon
- https://github.com/OpenVoiceOS/ovos-core
- https://openvoiceos.github.io/ovos-technical-manual/
- https://raw.githubusercontent.com/OpenVoiceOS/ovos-installer/main/README.md
- https://github.com/rhasspy/rhasspy3
- https://www.home-assistant.io/voice_control/
- https://github.com/janhq/jan (LICENSE)
- https://lmstudio.ai
- https://lmstudio.ai/app-terms
- https://github.com/Mintplex-Labs/anything-llm
- https://github.com/open-webui/open-webui (LICENSE)
- https://github.com/agent0ai/agent-zero (LICENSE)
- https://github.com/khoj-ai/khoj
- https://github.com/open-jarvis/OpenJarvis
- https://github.com/Priler/jarvis (LICENSE)
- https://github.com/microsoft/JARVIS
- https://github.com/aaif-goose/goose
- https://github.com/CherryHQ/cherry-studio

**Local files** (`D:\jarvis\hq-neko`)
- `docs/frontend/index.md`, `docs/frontend/pages.md`
- `docs/design/cat-idle-states-feature.md`
- `.github/workflows/build-desktop.yml`, `.github/workflows/wake-word-runtime.yml`
- `static/app/app-websocket.js`, `static/app/app-buttons.js`, `static/app/app-character.js`
- `main_logic/core/lifecycle.py`, `main_logic/voice_input/activation/contracts.py`
- `jarvis/widget/widget_server.py`, `jarvis/widget/index.html`, `jarvis/scripts/open-widget.ps1`
- `jarvis/docs/reports/evidence/00-baseline.md`
