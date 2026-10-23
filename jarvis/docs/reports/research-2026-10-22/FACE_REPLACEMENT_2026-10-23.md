# Replacing N.E.K.O as JARVIS's base: findings and plan (2026-10-23)

The owner asked to replace N.E.K.O (Chinese-first, about 190,000 lines of Chinese in 2,764 inherited files) with another app, research
first.

**Progress (2026-10-23, later):** the owner chose to integrate AIRI as JARVIS's own code. Done: `jarvis/face` (AIRI v0.12.0,
made JARVIS's own; see its README) and the face tool server `jarvis/bridge/face_tools.py` (Phase A step 2). Next: the laptop
pilot (Phase B), then the plugin shim and memory move (Phase A steps 1 and 3), then Phase C.

## 1. How much of JARVIS depends on N.E.K.O (measured on this branch)

| Part | Size (non-test lines) | Depends on N.E.K.O? |
|---|---|---|
| JARVIS core: kernel, laptop node, privacy gateway, console, voice, muse, bridge, broker, push gate, persona, explain | about 72,000 | Barely: the persona installer, a voice helper (`main_logic.voice_input`), a few eval scripts and the privacy gateway's config reader |
| JARVIS plugins: desktop, research, deliverables, local_team, main_agent, jarvis_bridge, learn_vault, doc_tools, workbench | about 19,000 (plus 15,000 test lines) | Yes, but through a narrow surface: `NekoPluginBase`, `neko_plugin`, `plugin_entry`, `lifecycle`, `timer_interval`, `Ok`/`Err`/`SdkError`, `ui`, `tr`; `main_agent` also uses N.E.K.O's memory recall and LLM client |
| What N.E.K.O itself provides | the whole rest of the repo | Chat window, character card and avatar (Live2D/VRM/MMD), realtime voice session, 5-layer memory, proactive chat, settings, launcher, plugin host |

So a replacement must provide the chat and voice "face", a character, an avatar and memory, and host JARVIS's tools.
JARVIS's safety core does not move.

## 2. Candidates (checked against primary sources, 2026-10-23)

| App | Licence | Fit |
|---|---|---|
| **Project AIRI** (moeru-ai/airi) | MIT | Closest match: Live2D/VRM/MMD avatars, desktop pet, voice (VAD, wake word, interrupt), OpenAI-compatible chat and TTS, MCP via its launcher, plugin SDK, Windows (winget, Scoop). **But**: newest build is a beta (v0.12.0-beta.5); memory still "WIP"; Electron, not Tauri; 2 critical advisories (character card/chat XSS to RCE, Sep 2025; CI privilege escalation, Dec 2025); security policy is unfilled template text; telemetry not documented (a `deploy/openpanel` folder exists) |
| Open-LLM-VTuber | MIT (sample Live2D models under their own terms) | Python, sherpa-onnx/Piper/faster-whisper built in, barge-in, MCP; **going stale** (last release 2025-08, v1 frozen, long-term memory removed) |
| Open WebUI | Custom licence (BSD-3 plus a branding clause above 50 users) | Best pure chat-and-voice front: custom OpenAI URLs for chat, TTS and STT, native MCP, memory; **no avatar or desktop presence** |
| AnythingLLM | MIT | Good chat front with MCP; speech input only in the browser; telemetry on unless disabled |
| LobeHub, Cherry Studio, SillyTavern | Community licence / AGPL-3.0 | Excluded by licence |
| OpenClaw | MIT | An agent that runs tools itself: conflicts with the JARVIS kernel |
| JARVIS's own Tauri shell + console | Yours | Most control and no third-party risk; needs the avatar, chat and voice UI built (AIRI's MIT renderers can be reused) |

## 3. Recommendation

Do it in three phases, so JARVIS keeps working the whole time and every step is reversible.

**Phase A: make JARVIS independent of any face (no-regret, about a week).**
1. A thin plugin layer `jarvis/pluginapi/` with the same names the plugins use today. It maps to N.E.K.O while N.E.K.O is still in
   place, and to plain JARVIS hosting afterwards. Each plugin then changes one import line.
2. One kernel-gated JARVIS tool server over MCP. It exposes the desktop, research, deliverables and mission tools, so any MCP-capable
   face (AIRI, Open WebUI, Claude Code) calls JARVIS the same way, and the kernel still decides every action.
3. Memory: move `main_agent` from N.E.K.O's recall to the kernel's own memory store (`jarvis_kernel/memory.py`).

**Phase B: pilot the new face next to N.E.K.O (about a week).**
- **Primary choice: AIRI, pinned to a reviewed release.** Its own computer-use is OFF, so all actions go through the JARVIS MCP server and
  kernel. Chat goes through the privacy gateway, TTS through the local speech server. Telemetry and cloud sign-in are checked and off, and
  untrusted character cards are never imported (the XSS advisory).
- **Pass criteria:** the 18 persona cases, the voice path (speech-to-text through Parakeet needs testing; AIRI documents its own
  on-device STT), the desktop and research bench areas, and a week of daily use.
- **If AIRI fails** on security or stability, the fallback is JARVIS's own Tauri shell, with AIRI's MIT Live2D/VRM renderers lifted in and
  Open WebUI as a temporary chat and voice window.

**Phase C: cut over, then remove N.E.K.O (about one to two weeks).**
- Switch the launcher, run the full test and bench suites, and keep N.E.K.O installed but stopped for two weeks.
- Then delete N.E.K.O's code from the repository (after a tagged backup). That also makes the repository English, which was the
  original request.

## 4. What the owner gives up or must decide

- N.E.K.O features with no equal yet: the 5-layer memory (rebuilt on the kernel's store), proactive chat (becomes a JARVIS routine),
  the Steam workshop and N.E.K.O's own plugin market (not needed).
- AIRI's beta status and security history: acceptable only with its own tools off and everything routed through the kernel.
- Your current character (YUI) and memories: exported before the cutover; the JARVIS card moves over.
