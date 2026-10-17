# Neko research report (2026-10-16)

This report covers the research phase for Neko, a personal JARVIS-like desktop assistant. Six research agents worked in parallel, one per capability area, and a lead (the integrating engineer) compared their findings with what already runs on this machine. The full evidence, with a source URL for every claim, is in `evidence/`:

| File | Area |
|---|---|
| `evidence/00-baseline.md` | This machine and the existing application, inspected first |
| `evidence/01-assistants-and-desktop-shell.md` | Desktop assistants, JARVIS-like projects, local-first assistants, desktop shell and character UI |
| `evidence/02-voice.md` | Wake word, VAD, speech recognition, speech synthesis |
| `evidence/03-orchestration-research-memory.md` | Multi-agent frameworks, research agents with citations, memory, learning, evaluation, search backends |
| `evidence/04-computer-browser-mcp.md` | Browser and computer use, Windows automation, MCP |
| `evidence/05-documents-pptx-pdf.md` | PowerPoint and PDF generation, rendering and checking |
| `evidence/06-models-hardware-providers.md` | Models per hardware profile, inference engines, routing, remote providers, fine-tuning |

Later research (2026-10-17) is in `evidence/07` to `09`. See section 5.

**Labels used below:**
- **[V]** verified in source code, a licence file, a release page or an official document;
- **[C]** a project's own claim;
- **[L]** measured on this laptop;
- **[E]** estimate that needs a local benchmark;
- **[R]** our recommendation.

Nothing found during research was installed or executed.

## 1. The main conclusion: extend what exists, don't start again

The workspace is not empty. It already runs most of a JARVIS on this laptop (see `evidence/00-baseline.md`):

| Layer | What runs today |
|---|---|
| Base | **N.E.K.O** (Apache-2.0): character web UI, voice pipeline, 5-layer memory, plugin runtime, agent server with browser-use |
| Authority | **JARVIS kernel**: owner-PIN approvals, audit, kill switch, private/public lanes, MCP connectors |
| Orchestration | `main_agent`, a plan-then-run orchestrator |
| Local models | Local models through llama-swap, routed by a TypeSafe-Jev-style decision model |
| Privacy | A privacy gateway in front of the public model |
| Voice | Local int8 speech recognition (English, Hindi, Telugu) and TTS |
| Operations | A watchdog and an always-on-top widget |

**None of the alternatives offers a better base.** We looked at AIRI, Open-LLM-VTuber, OpenVoiceOS, Leon, Open Interpreter, Agent Zero, Jan, AnythingLLM, Open WebUI and the "JARVIS" repositories:

- None has a kernel-style approval authority.
- Several are archived or dormant (Rhasspy 3, Open Interpreter 01).
- Some need Docker or WSL (Agent Zero, OpenVoiceOS).
- Some carry restrictive licences: Open WebUI's branding clause, and non-commercial licences on isair/jarvis and MateEngine.

AIRI (MIT, Electron) is the closest complete companion, but adopting it would mean redoing every integration. **[R] Keep N.E.K.O plus JARVIS as the base, and add the missing capabilities as plugins, kernel jobs and small services.** (evidence 01, section "Conclusion")

## 2. Category findings and recommendations

### 2.1 Desktop shell and character (category 1, 3; core experience)
- **Upstream's desktop client is not available.** Its Electron client "N.E.K.O.-PC" is closed source [V] (build workflow comment; repository 404).
- **N.E.K.O.-Tauri (Apache-2.0) [V] is the only open shell that wraps the N.E.K.O page.** It provides a transparent, always-on-top window and Live2D-aware click-through, and needs no changes to N.E.K.O files.
- **Its risks:** 1 star, no releases, an open bug that can leave the desktop unclickable if the backend dies, an open Tauri bug with black transparent windows on Windows 10, and an idle cursor poll every 33 ms.
- **N.E.K.O already has an animated "cat form"** with awake, dozing and deep-sleep tiers and thinking/zzz bubbles [V src]. Every Neko state except "wake-word listening" already has a real signal (evidence 01 table).
- **[R] Order of work:**
  1. Build the state engine and the Neko icon in the existing widget server and window.
  2. Wire them to real signals.
  3. Only then try N.E.K.O.-Tauri, after a 30-minute transparency and CPU test on this PC.
  4. Fallback: a minimal Electron shell.

### 2.2 Voice (category 2)
- **Wake word [R]:** keep N.E.K.O's sherpa-onnx open-vocabulary keyword spotter. "Hey Neko" needs no training; it is entered as phone tokens (`HH EY1 N EH1 K OW0`, plus variants).
  - Alternatives fall short: openWakeWord's pretrained models are CC BY-NC-SA and the project has had no release since February 2024 [V]. Porcupine's free tier ended 2026-06-30 (secondary source) and needs an online key check.
  - Two blockers: the keywords are hard-coded to "Yui" (needs a small config change), and the native runtime `1.13.8+neko.kws2` is not built. Building it needs VS 2022 C++ tools and cmake, which are not installed.
- **VAD:** keep Silero (MIT). TEN VAD's licence has non-compete terms [V].
- **Speech recognition:** keep Parakeet-TDT 0.6B v3 (English) and IndicConformer int8 (Hindi, Telugu) [L: Telugu word recall 86%].
  - SraVaani-1.0 (MIT) reports Telugu WER 25.1 vs IndicConformer 26.4: not enough of a gain to justify a model 2–4x larger with no int8 build [V].
  - **Benchmark to run:** real-microphone Hindi, Telugu, English and code-mixed clips.
- **TTS:** keep Kokoro (English) and Piper (Hindi, Telugu). Indic Parler-TTS has no CPU speed data; Supertonic 3 is optional for Hindi.
  - N.E.K.O already has barge-in parts (browser echo cancellation, an interrupt that drops queued audio, echo-transcript filtering) [V src]. The time it takes to stop speaking still needs measuring.
- **Always-on microphone:** wake listening keeps Edge holding the microphone, so Windows 10 shows the mic indicator and cannot block it per app. Windows 10 support ended 2025-10-14 [V].

### 2.3 Orchestration, research, memory (categories 4, 5, 12)
- **[R] Keep the custom `main_agent`; do not put a framework under it.**
  - Frameworks compared: LangGraph, CrewAI, AutoGen (now in maintenance, replaced by Microsoft Agent Framework 1.0 [V]), PydanticAI, smolagents, Qwen-Agent, the OpenAI Agents SDK, OpenHands and Google ADK.
  - Each would add a second runtime and approval mechanism beside the kernel.
  - Each relies on free-form tool loops, which small models do badly: a 2026 preprint measured 75–79% correct tool calls for Qwen2.5-1.5B and 0.5% valid raw JSON [C].
  - Several had 2026 security advisories (LangGraph checkpoint RCE, AG2, CrewAI, gpt-researcher), and LiteLLM was compromised on PyPI on 2026-03-24 [V].
  - **Add to `main_agent`:**
    - typed run events, for a real timeline;
    - immediate cancel for read-only steps;
    - schema-constrained plans for local planners;
    - research as a normal catalogue entry.
- **Research agent [R]: write one bounded `research` plugin, borrowing patterns.**
  - Why not reuse: GPT-Researcher and Local Deep Research need Python 3.12 and pull 83–140 dependencies; LangChain's open_deep_research was archived 2026-08-21 [V]; STORM and MindSearch are stale; Tongyi DeepResearch is 18.6 GB.
  - **Pipeline:**
    1. Local sub-questions.
    2. Search: the existing `web_search` plugin and/or Tavily or Brave, by owner choice.
    3. Local fetch and passage ranking.
    4. Up to 2 rounds of local gap checks.
    5. Dahl synthesis with one citation per sentence, streamed.
    6. A local claim check per cited sentence.
    7. Stop at fixed limits, and say so.
- **Memory [R]:** no second memory system. mem0, Letta, Graphiti, LangMem and cognee each bring their own store and extractor; some add telemetry or a graph database. Instead:
  - feedback goes into N.E.K.O's reinforce/dispute signals;
  - one owner-visible fact per finished run;
  - web content never flows into memory automatically, to avoid memory poisoning;
  - skills use the SKILL.md format behind the existing skill gate;
  - `jarvis/evals` gets planner and citation cases;
  - GEPA (optional) tunes prompts offline, only when evals show no regression.
- **Search backends (checked 2026-10-16):**

  | Backend | Free tier |
  |---|---|
  | Tavily | 1,000 credits a month, no card |
  | Brave | $5 credit a month, card required |
  | Exa | $10 a month |
  | Jina Reader | 20 requests a minute, no key |
  | SearXNG | self-hosted, needs WSL or Docker |

### 2.4 Computer and browser control, MCP (categories 6, 7, 8)
- **The owner's signed-in Google session is in Chrome's Default profile (Chrome 155).**
  - Since Chrome 136, remote-debugging flags are ignored on the default user-data directory [V], and Playwright calls automating it "not supported" [V].
  - **[R] Supported route:** Playwright MCP in **extension mode**: the owner installs the extension, and each connection is approved in Chrome. The agent only sees its own tab group.
  - Playwright MCP is "not a security boundary" [V], so every click or typing step goes through kernel approval (YELLOW_APPROVAL).
  - Before acting in the account, read the signed-in e-mail and refuse on mismatch.
  - **Avoid:** browser-use "real profile" mode copies cookies into `%TEMP%` [V src].
- **Song playback [R]:**
  1. Find the song through the YouTube Data API or search.
  2. Open the link in the owner's browser.
  3. Verify through Windows' media-session API (SMTC): Chrome reports "Playing", the title matches, and the position advances.

  YouTube's terms forbid "automated means" of access, so opening a link is the low-risk path. **Owner decision.**
- **Desktop [R]:** extend the JARVIS laptop node with UI Automation (`uiautomation`), `mss` screenshots and SMTC behind one OS interface (Linux: AT-SPI, xdotool, MPRIS).
  - N.E.K.O's built-in computer-use can press any hotkey, which can lead to arbitrary OS actions: use it only as a last resort, with approval per step.
  - Organising files: plan, approve the exact move list, keep an undo journal.
- **MCP:**
  - Spec 2026-07-28; the official registry is still "preview" with minimal moderation, and a look-alike server was found in it [V].
  - **[R] Minimal set:** time; Playwright MCP extension mode; filesystem read-only on `D:\jarvis\outputs`; fetch, with results marked untrusted; GitHub read-only; one search MCP or direct API.
  - **Rejected:** Desktop Commander (the model can change its own blocklist [V]), Windows-MCP PowerShell/Registry tools, third-party gateways.
  - N.E.K.O's `mcp_adapter` speaks protocol 2024-11-05 with no per-tool approvals. Keep it off, and route MCP through the kernel's connector governance (fingerprints, argument-bound approvals).

### 2.5 PowerPoint and PDF (categories 9, 10)

**Generator capabilities**

| Tool | Finding |
|---|---|
| PptxGenJS | No animations or transitions: maintainer statement, PR #1431 unmerged [V] |
| python-pptx | No animation API [V]; hand-written timing XML is fragile [C] |
| **OfficeCLI** (Apache-2.0, single exe, v1.0.157, 2026-10-12) | The only maintained open tool found that writes PowerPoint transitions, morph and entrance animations, and claims schema validation [C] |
| Marp, Slidev | Export images by default |
| Presenton | Uses python-pptx without animations, and sends telemetry by default |
| PPTAgent | Linux only |

**[R] PPTX pipeline:**
1. **Build:** python-pptx with topic-selected theme templates: native charts and tables, notes, source footnotes, a Sources slide.
2. **Animate:** OfficeCLI adds transitions and entrance animations, then runs `validate`.
3. **Check:** LibreOffice 26.8 portable on D: renders PPTX to PDF; pdfplumber turns pages into PNGs; overflow checks and a visual review follow.
4. **Present:** `soffice --show` starts the slideshow.

Limits:
- Morph plays only in PowerPoint 2019/365.
- The quality check shows LibreOffice's rendering, not PowerPoint's.
- A static render cannot prove that animations play.
- PowerPoint Viewer was retired in 2018 [V].

**[R] PDF pipeline:** HTML/CSS templates printed by Playwright through the installed Edge, using `page.pdf(tagged=True, outline=True)` with footer page numbers. pypdf and pdfplumber then check text, links, fonts, outline and overflow, and produce page images. The only new install is pdfplumber.
- Avoid PyMuPDF (AGPL) and wkhtmltopdf (archived).
- Typst or the WeasyPrint exe are upgrade paths for PDF/UA.

### 2.6 Models, hardware and providers (categories 11, 13)
- **Engine:** stay on llama.cpp + llama-swap [L].
  - vLLM is not suitable here (no Windows, no Vulkan) [V].
  - Ollama would duplicate llama-swap, and its flash-attention default is risky on this RDNA1 card, where flash attention gave garbage [L].
- **Models:** the newest small families are Apache-2.0: Qwen3.5 (0.8B to 35B-A3B), Gemma 4 (E2B to 31B) and Granite 4.x [V]. **Qwen3.5-4B Q4_K_M** (2.74 GB, vendor BFCL-V4 50.3 / TAU2 79.9 [C]) is the router-LLM candidate. It must pass a Vulkan correctness check, because its Gated DeltaNet layers were missing from Vulkan in March 2026 [V].
- **Router [R]:** the D20 design plus four additions:
  1. hardware profile detection that writes the llama-swap config;
  2. script detection for Telugu and Devanagari;
  3. a multilingual embedding kNN tier that also picks the top-5 candidate tools;
  4. a small constrained-JSON LLM that only chooses among those 5.

  Verdict (ModernBERT) is English-trained [V], so it must not judge Telugu or Hindi input.
- **Remote fallback [R]:** Dahl, then Groq (no retention by default), then OpenRouter free models (20 requests a minute, 50 a day), then Cloudflare Workers AI (10,000 neurons a day). Always through the privacy gateway.
  - **Avoid for personal data:** Gemini's free tier (its terms allow training and human review [V]).
  - **Not available:** GitHub Models (retired 2026-07-30), Cerebras (trial only), Hugging Face Inference Providers (no free credits).
  - **No key rotation within a provider:** extra keys are forbidden or pooled per account. On a 429, move to the next provider.
- **Fine-tuning:** not now. Encoder/SetFit intent models on MASSIVE (CC-BY-4.0, includes te-IN and hi-IN) are a better first step than LoRA on an LLM. Kaggle T4 GPUs lack bf16 [V], and Kaggle's quota could not be confirmed on an official page.

## 3. Reuse summary

| Capability | Reuse | Build (small, justified) |
|---|---|---|
| Character, chat, memory, plugins | N.E.K.O | — |
| Authority, approvals, audit, MCP governance | JARVIS kernel | New policy entries (open URL/project, organise files, media state) |
| Wake word | N.E.K.O sherpa-onnx KWS | Keyword-file config; runtime build or provisioning |
| VAD, STT, TTS | Silero, Parakeet, IndicConformer, Kokoro, Piper (installed) | Push-to-talk hotkey; stop-speaking timing test |
| Desktop states and icon | N.E.K.O cat form, widget server | State engine (aggregator + push), icon UI, tray |
| Orchestration | `main_agent` | Typed events, cancel, pause/retry, constrained planning |
| Research | `web_search` plugin, browser-use, Dahl via gateway | `research` plugin (bounded, cited, verified) |
| PPTX | python-pptx, OfficeCLI, LibreOffice | Theme templates, deck builder, QA checks |
| PDF | Playwright + Edge, pypdf, pdfplumber | HTML templates, report builder, QA checks |
| Browser (owner account) | Playwright MCP extension mode | Kernel connector entry, account check |
| Desktop control | JARVIS laptop node, `uiautomation`, `mss`, SMTC | OS interface, media verification |
| Models | llama.cpp, llama-swap, Ling, Qwen, Verdict, GLiNER | Hardware detector, profile configs, benchmark harness |
| Providers | Privacy gateway, kernel providers | Ordered fallback with 429/quota detection |
| Memory and learning | N.E.K.O memory, learn_vault, jarvis/evals | Feedback capture, eval cases, versioned prompts with rollback |

## 4. Open questions that need the owner

1. **Search backend:** keep the free AnySearch/DuckDuckGo path, or add Tavily (no card) and/or Brave (card)?
2. **YouTube:** open-link playback only, or allow in-page clicking despite the "automated means" clause?
3. **Browser extension:** install the Playwright MCP extension in Chrome (an owner action), so Neko can use the signed-in account.
4. **Wake word build:** allow installing the VS 2022 C++ Build Tools and cmake (several GB, mostly on C:, which has 22 GB free), or try upstream's prebuilt runtime artifact? That artifact expires 2026-10-18 and needs a GitHub login.
5. **Approval PIN:** set the owner PIN in the JARVIS console. Without it, approval flows can be tested only up to "waiting".
6. **Remote providers:** add Groq and/or OpenRouter keys? Keys go into `D:\jarvis-secrets`, never into chat.

## 5. Update 2026-10-17

This report is kept as written on 2026-10-16. Three later web-research reports, each with links, were checked on the Linux test server (D30):

| File | Area |
|---|---|
| `evidence/07-small-models-2026-10-17.md` | Small models as of 2026-10-17 (decider, routing, vision, embeddings, claim checking, speech), with ranked shortlists, licences and pinned file hashes |
| `evidence/08-runtimes-agents-infra-2026-10-17.md` | Inference runtimes, browser and computer use, orchestration and safety, memory, evaluation; ranked low-risk adoptions and what is not worth adopting |
| `evidence/09-research-reliability-2026-10-17.md` | Claim decomposition, grounding checkers, qualifiers and units, source quality, calibration, prompt injection in pages, evaluation datasets |

What changed in the recommendations above:
- **Models (2.6).**
  - Qwen3.5-4B was named the router-LLM candidate here. In a CPU comparison of 12 pinned models through the real decider (`decide.py`), it scored 146/157. The current decider, Qwen3-4B-Instruct-2507, scored 152/157 and is kept.
  - No sub-2B model is good enough to decide alone (`NEKO_95_MODEL_BENCHMARK.md`).
- **Engine (2.6).** Still llama.cpp + llama-swap. A test now keeps every llama-server a plain loopback model server: no agent, tool or MCP flags, and no web UI.
- **Claim checking (2.3).**
  - The NLI checker stays `nli-deberta-v3-xsmall`, plus deterministic guards: units, qualifiers, comparators, hedges, and the removal of sentences addressed to AI assistants.
  - The larger NLI models accepted more false claims on VitaminC: 16/200 and 21/200, against 11/200.
  - The checkers named in evidence 09 (FactCG, MiniCheck, HHEM) are deferred until one fits CPU latency here.
- **Frameworks (2.3, 2.4).** No change. Evidence 08 again rejects model-driven agent frameworks that bypass the kernel's policy.
- **Open questions (section 4).** Two are settled, both on 2026-10-16 (`PC_CONTROL_TEST_REPORT.md`):
  - the Playwright extension is installed in all six Chrome profiles;
  - the approval PIN is set. It was typed in chat once, so changing it is still an owner action.
