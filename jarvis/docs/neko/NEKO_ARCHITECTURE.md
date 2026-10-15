# Neko architecture

Neko is not a new framework. It is N.E.K.O (character, voice, memory, plugins) under the JARVIS kernel (authority), with a small number of added services and plugins. The rule for every component is the same: reuse the existing part, and build only what the research report shows to be missing (`NEKO_RESEARCH_REPORT.md`, section 3).

## 1. Processes on this laptop (Windows)

All of these start at login (`JarvisNekoStack`, `neko-start.ps1`). The watchdog heals them every 10 minutes. Everything listens on 127.0.0.1 only.

| Port | Process | Role | Status |
|---|---|---|---|
| 48911 | N.E.K.O main (`launcher.py`) | Character UI, chat, voice session, settings | exists |
| 48912 | N.E.K.O memory server | 5-layer memory | exists |
| 48915 / 48916 | N.E.K.O agent server / plugin runtime | browser-use, computer-use, plugins (main_agent, local_team, doc_tools, learn_vault, jarvis_bridge; new: research, deliverables) | exists; plugins added |
| 48930 | Local speech server | STT (Parakeet, IndicConformer), TTS (Kokoro, Piper) | exists |
| 48940 | Widget server (becomes the **Neko state server**) | Aggregated state, icon window, quick task, push events | exists; extended |
| 48950 | Privacy gateway | Redacts before any public model; restores replies; provider fallback | exists; fallback added |
| 8080 | llama-swap | Local LLMs on demand (profile config) | exists; profile-generated |
| 8091 | OpenJev "Verdict" (owner's WSL service) | Typed decisions (route, depth, secret check) | exists (not managed by JARVIS) |
| 8799 | JARVIS console + kernel connection | Approvals (PIN), audit, connectors, providers | exists |

New processes are kept to a minimum. The research and deliverables work runs inside the existing plugin runtime. LibreOffice runs only when a deck is rendered or shown.

## 2. Request flow

```
"Hey Neko" (KWS) / click icon / push-to-talk hotkey / typed task
        │
        ▼
N.E.K.O session (VAD → STT 48930) ──► conversation model (Dahl via gateway 48950)
        │                                   │ tool/agent intent
        │                                   ▼
        │                       main_agent (coordinator, plan-then-run)
        │      ┌──────────────┬───────────┼──────────────┬──────────────┬───────────┐
        │   think          research     deliverables    browser        jarvis       local_team
        │ (model call)   (plugin, new)  (plugin, new)  (browser-use,  (kernel:      (code/math/
        │                     │              │          or kernel MCP)  pc, ui,      general,
        │                     │              │                          connectors)  Jev, gateway)
        │                     ▼              ▼                               │
        │              web_search,     python-pptx +                         ▼
        │              fetch, Dahl     OfficeCLI +                    approvals (PIN),
        │              synthesis,      LibreOffice QA;                audit, kill switch
        │              claim check     Edge page.pdf + checks
        ▼
TTS (48930) ◄── reply text            state server 48940 ◄── events from every box above
```

- **Coordinator:** `main_agent` makes one plan from the request and a closed catalogue of capabilities, then runs the steps under limits.
- **Roles are catalogue entries, not separate LLMs:**
  - research = `research.research_topic`
  - verification = the research plugin's claim checker, plus the QA steps inside deliverables
  - browser/search = browser and `web_search`
  - documents/presentations = `deliverables.*`
  - code = `local_team` code specialist
  - desktop = `jarvis` (kernel jobs)
  - QA = `deliverables.inspect_*`

## 3. State engine (new, in the widget/state server)

One state value is computed from real signals only (evidence 01, section 4) and pushed to the icon window over Server-Sent Events. Polling stays as a fallback.

| State | Source signal |
|---|---|
| error/offline | servers down (health probes), watchdog `gave_up_on`, kernel killed, session failed |
| needs_permission | kernel approvals waiting > 0 (console `/api/owner/state`) |
| speaking | N.E.K.O audio stream active (page event `neko-audio-stream-*`, reported by the icon page bridge) |
| working | a main_agent run is active (newest run file: state `running` and its current step) |
| thinking | the run is planning, or a user turn is waiting for the model's first token |
| listening | microphone capture active (session active, VAD speech) or push-to-talk held |
| waking | wake detected / session preparing |
| wake_listening | wake word enabled and its detector healthy |
| completed | the last run finished within the last N minutes and has an output file |
| sleeping | none of the above |

Priority is top to bottom: error beats everything, and sleeping is the default. Each state change carries `{state, since, detail, run_id?, output?}`. The icon UI animates per state. The icon uses no GPU-heavy Live2D while sleeping; that is the cat-form approach.

## 4. Voice

| Stage | Choice | Notes |
|---|---|---|
| Wake word | N.E.K.O sherpa-onnx KWS | Keywords from a file (`NEKO_WAKE_WORD_KEYWORDS_FILE`) with "Hey Neko" phone sequences. Runtime built locally or provisioned (owner approval needed for the build tools) |
| Push-to-talk | Global hotkey in the icon window | Real key-up needs a native shell (Tauri plugin); in the Edge window it works only while focused (documented limit) |
| VAD | Silero | — |
| STT | Parakeet (en), IndicConformer (hi/te), per utterance | — |
| TTS | Kokoro / Piper | Interrupt drops queued audio (N.E.K.O) |

## 5. Model layer

1. **Hardware detector** (`jarvis/local_models/hwdetect.py`, new). It reads CPU, RAM, disk and OS through `psutil` (falling back to WMI/CIM), and gets GPU devices and free VRAM from `llama-server --list-devices`, the runtime's own view. It assigns one of five profiles: `cpu8`, `cpu16`, `vram4`, `vram8plus`, `remote_only`.
2. **Profile configs:** `jarvis/local_models/profiles/<profile>.yaml` (llama-swap). The active file is the one the detector picked; the owner can override it.
3. **Memory guard:** before llama-swap loads a big model, a pre-load check compares the file size plus margin with available RAM. Exclusive groups keep one specialist on the GPU.
4. **Router:** keyword rules → script detection (Telugu or Devanagari goes past Verdict) → Verdict (English) → generalist one-word router → (planned) embedding kNN plus constrained-JSON tool choice. Remote is used only for planning, deep research and synthesis.
5. **Remote:** the privacy gateway keeps an ordered upstream list (Dahl first). On HTTP 429, a 5xx, a timeout or a quota error, it moves to the next enabled provider exactly once per request (no loops) and records which one answered. Keys stay in `D:\jarvis-secrets` and are referenced by name.

## 6. Deliverables pipeline

| Deliverable | Steps |
|---|---|
| PPTX | outline JSON (local or remote model) → python-pptx deck from a theme template (title, agenda, content, chart, table, quote, sources; notes and footnotes) → OfficeCLI transitions and entrance animations, then `validate` → LibreOffice headless to PDF → page PNGs (pdfplumber) → checks (text overflow, slide count, images present, file reopens) → open, or `soffice --show` |
| PDF | report JSON → HTML/CSS template (cover, TOC, sections, tables, figures, references) → Playwright with the installed Edge `page.pdf(tagged, outline, header/footer page numbers)` → pypdf and pdfplumber checks (text, links, fonts, outline, overflow) → page PNGs → open |

Outputs go to `D:\jarvis\outputs\<date>\`. Each run records its QA results next to the file.

## 7. Computer control

An **OS interface** (`jarvis/node/jarvis_node/osctl/`) defines `open_url`, `open_path`, `open_project`, `media_state`, `windows`, `focus` and `organise(plan)`. Windows uses the shell, UIA and SMTC; Linux has stubs (xdg-open, MPRIS) for later.

- **Owner-account browser work:** Playwright MCP in extension mode as a kernel connector, with the account check before use.
- **Everything else:** N.E.K.O browser-use in its own automation profile on D:.
- Every write action goes through the kernel policy tiers (`NEKO_SECURITY_MODEL.md`).

## 8. Memory and improvement

- **No new memory store.**
- **Feedback:** 👍/👎 plus an optional note on a finished run or deliverable. It is written to `D:\jarvis\N.E.K.O-data\neko\feedback.jsonl` and mirrored as N.E.K.O reinforce/dispute signals.
- **Evals:** `jarvis/evals` gains cases for routing, planning, citations and deliverable QA. The comparison against the last saved run is printed.
- **Versioned prompts and skills:** each lives in git, with a version id in the run record. Rollback is `git revert` or switching the config id.
- **No silent changes:** the model never changes core code or security rules.

## 9. Desktop shell

- **Phase 1:** the existing widget window (Edge app mode, always on top) becomes the Neko icon window with states.
- **Phase 2 (optional, after a measured test):** the N.E.K.O.-Tauri shell for a transparent overlay, a tray and true push-to-talk. Fallback: a minimal Electron shell.

## 10. Decisions taken by default (reversible)

| Decision | Reason | Reversal |
|---|---|---|
| Docs in `jarvis/docs/neko/` | Repo rule: at most 5 files in `docs/`; the root `docs/` is upstream's | Move the folder |
| LibreOffice portable on D: | Only free renderer and slideshow player; nothing on C: | Delete the folder |
| Search stays on the existing `web_search` plugin until the owner picks a backend | No paid service or card without approval | Add a key reference |
| Wake word stays off until the build tools are approved | The install is several GB on C: | — |
| N.E.K.O `mcp_adapter` and computer-use stay off for account and desktop work | Kernel-governed path instead (security model) | — |
