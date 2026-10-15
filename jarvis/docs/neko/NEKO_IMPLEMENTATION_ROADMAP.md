# Neko implementation roadmap and checklist

**Rules:**
- **Phase cycle:** each phase is built, tested on this laptop (Windows), and reported with what passed and what could not be tested.
- **Reversible decisions:** taken by default and recorded.
- **Owner-only items:** marked **OWNER**. These are credentials, installs on C:, paid services, the approval PIN and the YouTube automation choice.

The status legend is the one in `NEKO_REQUIREMENTS.md`. This checklist is updated as work lands.

## Phase 0: research and design ✅
- [x] Workspace baseline (`evidence/00-baseline.md`)
- [x] Six parallel research reports with sources (`evidence/01`–`06`)
- [x] Research report, requirements, architecture, model matrix, security model, this roadmap

## Phase 1: deliverables (PDF and PPTX) — requirements D1–D6, X7 ✅ (2026-10-16, D21)
Why first: it is the largest missing capability with no owner dependency, and it is fully testable here.
- [x] pdfplumber 0.11.10 and python-pptx 1.0.2 in `neko-venv`
- [x] OfficeCLI v1.0.152 (SHA256SUMS) and LibreOffice 26.2.6 (sha256 and GPG) on D:. D: went from 19.1 to 17.4 GB free
- [x] `make_report`: cover, contents, badges, tables, charts, references; tagged, bookmarks, links, fonts embedded (live: PASS)
- [x] `make_presentation`: 3 themes, 10 layouts, native chart and table, notes, footnotes, Sources slide; OfficeCLI transitions and animations; schema-valid; LibreOffice render; checks (live: PASS)
- [x] `open_output` / `show_in_folder` / `start_slideshow` / `regenerate` (slideshow: launched full screen through the plugin; watching it play is left to the owner)
- [x] 16 unit tests; `run_demo.py`; `live_check.py` (2 new checks; 60 of 60 total, `--no-model`)
- [ ] OWNER: watch one slideshow and open a deck in PowerPoint for the web to confirm the animations play

## Phase 2: research with citations — R1–R4, O2
- [ ] `research` plugin: bounded plan, then search (`web_search` plugin; Tavily/Brave if the owner adds a key), then fetch, then rank passages, then synthesise with per-sentence citations (Dahl via gateway, streamed), then claim check (Jev/HHEM), then a labelled report
- [ ] Budgets: maximum queries, pages, seconds and characters; cache and deduplication
- [ ] Hand off to deliverables: "research X and make a PPT/PDF"
- [ ] Tests with a fake search and fetch; one live run (one Dahl synthesis)

## Phase 3: Neko states and icon — X1, X2, X5, X6, X7, X8
- [ ] State engine in the widget server: priority rules, SSE `/api/events`, unit tests per state
- [ ] Neko icon window: animated states (CSS/SVG), click to activate, a Stop button (kernel kill switch), completed actions (open, folder, regenerate), the exact approval text
- [ ] Page bridge: speaking and listening signals from N.E.K.O page events
- [ ] Live check: forced states (start a run, then working; an approval waiting, then needs_permission; a server down, then error)

## Phase 4: models, hardware and providers — M1–M3, M5, M7–M10
- [ ] `hwdetect.py` (psutil + `llama-server --list-devices`) with profile choice; tests with fake readings
- [ ] Per-profile llama-swap configs; RAM guard before loading large models
- [ ] Gateway ordered fallback (429, 5xx, timeout, quota); one hop per request; the answering provider recorded; tests with fake upstreams
- [ ] Benchmarks: Qwen3.5-4B Vulkan correctness and speed; 100-item router/tool set; over-refusal set
- [ ] **OWNER:** Groq / OpenRouter / Cloudflare keys (optional)

## Phase 5: voice — X3, X4, V3, V4
- [ ] `wake_word_keywords()` reads `NEKO_WAKE_WORD_KEYWORDS_FILE` (default unchanged; 2 new tests)
- [ ] "Hey Neko" keyword file (3 pronunciations)
- [ ] **OWNER:** approve VS 2022 Build Tools + cmake (several GB on C:) to build the KWS runtime, or provision upstream's artifact (GitHub login)
- [ ] Push-to-talk (window hotkey; global after the Tauri shell)
- [ ] Microphone end to end; stop-speaking latency measurement (B3)

## Phase 6: computer control — C2–C6
- [ ] OS interface in the JARVIS node: open_url, open_path, open_project (VS Code), media_state (SMTC), organise(plan) with an undo journal
- [ ] Policy entries for new jobs (tiers as in the security model)
- [ ] Song flow: search, open link, verify through SMTC
- [ ] **OWNER:** install the Playwright MCP extension in Chrome, then the kernel connector and the account check
- [ ] **OWNER:** the YouTube in-page automation decision

## Phase 7: memory, feedback and evals — L2–L4
- [ ] Feedback control on runs and deliverables, then `feedback.jsonl` plus N.E.K.O reinforce/dispute
- [ ] Eval cases: routing, planning, citations, deliverable QA; comparison with the previous run
- [ ] Prompt and skill version ids in run records; rollback documented

## Phase 8: desktop shell (optional) — X1, X4
- [ ] 30-minute test of N.E.K.O.-Tauri on this PC (transparency, CPU, crash fail-safe); adopt or fall back to minimal Electron
- [ ] **OWNER:** Rust and C++ build tools (on C:)

## Done at every phase
- [ ] `live_check.py` extended and green; unit tests green; gitleaks clean; commit (not pushed)
- [ ] `05_OPERATIONS.md` and `03_DECISIONS.md` updated; free disk space reported
