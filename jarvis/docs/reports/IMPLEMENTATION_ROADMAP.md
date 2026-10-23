# Neko implementation roadmap and checklist

**Rules:**
- **Phase cycle:** each phase is built, tested on this laptop (Windows), and reported with what passed and what could not be tested.
- **Reversible decisions:** taken by default and recorded.
- **Owner-only items:** marked **OWNER**. These are credentials, installs on C:, paid services, the approval PIN and the YouTube automation choice.

The status legend is the one in `REQUIREMENTS.md`. This checklist is updated as work lands.

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

## Phase 2: research with citations — R1–R4, O2 ✅ (2026-10-16, D22)
- [x] `research` plugin: redacted question; local query plan; `web_search` plugin; SSRF-safe fetch with cache; BM25 passages with relevance cut; Dahl writes structured statements with sources through the gateway (local fallback); NLI claim check; labelled report
- [x] Budgets: quick and standard (queries, pages, passages, seconds); cache and deduplication; the run record is saved
- [x] Hands off to deliverables (`deliverable=report|presentation`). Live: PDF and deck made, QA passed
- [x] 12 tests with fakes; live runs (37–66 s; 7/16 to 11/17 statements confirmed, the rest labelled); `live_check.py` research check on the model path
- [ ] OWNER: choose a search backend (current AnySearch/DuckDuckGo, or add Tavily/Brave)

## Phase 3: Neko states and icon — X1, X2, X5, X6, X7, X8 ✅ (2026-10-16, D23)
- [x] State engine (`neko_state.py`): priority rules from real signals; SSE `/api/events`; 6 unit tests
- [x] Neko icon window: animated states (CSS/SVG, light and dark), click to give a task, Stop after this step, Open / Folder / Make again, the exact approval text and a console link; checked headless in all states
- [x] Signals: gateway in-flight calls (thinking), speech-server activity (speaking estimate, listening), run files, approvals, health
- [x] Live: sleeping → speaking → completed pushed by the event stream; `live_check.py` 4 icon checks (66/66)
- [ ] N.E.K.O page bridge for the exact microphone and playback state (needs the shell's script injection, phase 8)

## Phase 4: models, hardware and providers — M1–M3, M5, M7–M10 ✅ (2026-10-16, D24)
- [x] `hwdetect.py` (psutil, registry/AVX2, `llama-server --list-devices`); this laptop is vram4; 3 tests with fake readings
- [x] Per-profile llama-swap configs (cpu8, cpu16, vram8plus; vram4 is the tuned file); `fetch_models.py --profile`; memory guard in `local_team` (2 tests)
- [x] Gateway ordered fallback: one try per provider, cooldown on 429, `X-Neko-Provider`; 4 tests with fake providers; live call still correct
- [x] Benchmark: Qwen3.5-4B correct on Vulkan, 10.7 tok/s, not adopted here. Router scorecard in `neko_evals.py` (16 items)
- [ ] Larger router/tool set (100 items) and an over-refusal set (later)
- [x] Groq first fallback (openai/gpt-oss-120b, free tier, own key file): live-verified through a temporary gateway with a fake Dahl 429 (2026-10-16)
- [x] Tavily optional secondary search (key file only; primary stays web_search); merged, de-duplicated, tracked; 5 tests
- [ ] **OWNER:** Tavily key (free, no card) to run `search.compare()`; OpenRouter / Cloudflare keys (optional)

## Phase 5: voice — X3, X4, V3, V4 (built 2026-10-16, D26; the owner's spoken test is open)
- [x] KWS runtime 1.13.8+neko.kws2 built with upstream's script (Build Tools were already installed; C: untouched); model provisioned on D:
- [x] `jarvis/wake/wake_listener.py`: mic → detector → VAD → local ASR → task → back to listening; cancel/stop/pause; status 48945; icon states
- [x] Keyword lines configurable (`NEKO_WAKE_WORD_KEYWORDS_FILE`), 4 pronunciations; 16 loop tests; offline eval (`eval_wake.py`)
- [x] Live microphone: 10 min, 0 false wakes; loopback 2/3 then 0/3 at low speaker level (honest: marginal at ~10 dB SNR)
- [x] One real voice command from a file reached the main agent (run succeeded, icon showed "Done")
- [ ] **OWNER:** say "Hey Neko, what is the time?" a few times at normal distance; report wakes/misses (see 05_OPERATIONS)
- [ ] Push-to-talk (Phase 8 finding: RegisterHotKey + key-state polling, no global hook)
- [ ] Stop-speaking latency measurement (B3)

## Phase 6: computer control — C2–C6 (built 2026-10-16, D27)
- [x] Kernel: `browser_ext`, `diag`, `repair`, `media_state`; ui focus/key/scroll/click_at; `owner_direct`; approval round trip; job cancel
- [x] Node: browserext (profile registry, Playwright MCP extension, chrome --profile-directory), diag, repair (backup/undo), media (SMTC), cancel
- [x] `desktop` plugin: open_website, play_youtube, run_browser_action, run_pc_action, check_pc, run_troubleshooter, stop_pc_job, list_browser_profiles
- [x] Sample app with a reproducible error (`jarvis/tests/neko_sample_app`)
- [x] Deployed to hermes-agent-vm (473 server tests OK in place; proxy + kernel restarted; Telegram still off; rollback copy kept)
- [x] Live on this laptop (`jarvis/tests/neko_pc_live.py`, 2026-10-16): 1 open in default profile PASS; 2 switch profile PARTIAL
      (opens in Profile 1, "Venkat" asks which one; account check needs the extension); 3 YouTube PARTIAL (opens the search; playing
      needs the extension; nothing claimed); 4 error dialog PASS (text read locally, not sent to the server); 5 diagnosis PASS
      (verified cause, fix proposed, held for approval, denied in the test, nothing changed); 6 risky actions PASS (Buy now, alt+f4
      held and denied); 7 no leaks PASS; 8 cancel PASS (queued job). Fix → restart → verify → undo: PASS with the approval SIMULATED
      (`neko_fix_verify.py`); the live approved run waits for the owner's PIN
- [x] **OWNER:** install the Playwright extension in the Default and Profile 1 Chrome profiles. Done for all six profiles on 2026-10-16, with each account verified 6/6 (`PC_CONTROL_TEST_REPORT.md`)
- [ ] **OWNER:** save a token per Chrome profile for unattended connection (`save_playwright_token.ps1`)
- [x] **OWNER:** set the approval PIN, then the live approval test. Done on 2026-10-16: a protected repair was approved with the real PIN, verified and rolled back (`PC_CONTROL_TEST_REPORT.md`)

## Phase 7: memory, feedback and evals — L2–L4 (part 1 ✅ 2026-10-16, D25)
- [x] Feedback 👍/👎 on deliverables in the Neko icon, then `feedback.jsonl` (validated; tested)
- [x] `neko_evals.py`: routing, adviser decision, claims, PII, deliverables; saved runs; regression = exit 1; baseline recorded
- [ ] Mirror feedback into N.E.K.O reinforce/dispute signals (needs a look at N.E.K.O's memory API)
- [ ] Prompt and skill version ids in run records (git is the version store today)

## Phase 8: desktop shell (optional) — X1, X4 — DEFERRED (owner, 2026-10-16: no Rust yet; keep on the roadmap)
- [x] Investigated in the current stack (2026-10-16), nothing installed:
  - Transparent pet window: N.E.K.O's own Electron shell (N.E.K.O.-PC, upstream, separate repo) is the reuse-first route;
    N.E.K.O's frontend already has desktop-window hooks. Cost: Electron on D: (~250 MB), Node 24 is present.
  - The current Edge app-mode icon can be made semi-transparent and click-through as a whole window (SetLayeredWindowAttributes,
    WS_EX_LAYERED/TRANSPARENT from open-widget.ps1), but not shaped per pixel.
  - Hold-to-talk without a global keyboard hook: RegisterHotKey for the press, then poll GetAsyncKeyState for that one key
    until release; the wake listener records while it is held (no other keystroke is ever seen).
- [ ] Prototype hold-to-talk in the wake listener; try the click-through mode on the icon
- [ ] Decide between N.E.K.O.-PC (Electron) and the deferred Rust/Tauri shell
- [ ] **OWNER:** Rust and C++ build tools (on C:) — only if the Tauri route is chosen

## Phase 9: benchmark v2 and hardening — Linux test server, owner away (2026-10-16/17, D29, D30)
Built and tested in the rootless test container on the relative's server: headless, nice 19, CPU-capped. Committed locally, never pushed. No Windows, GPU or microphone run.
- [x] Benchmark v2 (`jarvis/evals/neko95/bench`): 572 cases, then 860. Rubric v2 written before any case ran, and unchanged
- [x] Final run (`results/bench-20261017-102655.json`): 833 pass, 1 fail (H-042), 26 deferred. Scorecard v2 9.69. The official v1 score stays 7.61
- [x] About 60 product bugs found by the new cases, each fixed with a test that fails on the old code (D30; `QUALITY_95_PROGRESS_REPORT.md`, "Mission 2026-10-17")
- [x] Decider asks instead of acting on vague, negated, "X or Y" and corrected requests, and on an excluded account (`decide.py`)
- [x] Security fixes: adviser URL (J-068), skill gate (M-024/029/030), key scrubbing and key-file fallbacks, kill switch for lab jobs, strict `ok is True`, vault names, connector checks on Linux/macOS, llama-server flag guard (`SECURITY_MODEL.md`)
- [x] Research checker guards (units, qualifiers, comparators, hedges, injection) and `claims_eval.py`. claims_v2 false acceptance 2/46, VitaminC 10/200
- [x] CPU comparison of 12 pinned models (`model_compare.py`): Qwen3-4B-Instruct-2507 kept at 152/157
- [x] Benchmark Console: Bench screen of the loopback console (Dashboard, Run, Challenge, Models, Security). Phone e2e 5/5
- [ ] **OWNER:** confirm the new ask-instead-of-act behaviour (excluded account, negated action, "X or Y")
- [ ] Open bugs: H-042 (a past figure is confirmed as current) and J-018 (500 ms proxy relay; flaky at 1 CPU under load)

### Next: the laptop and Windows (when the owner is home)
The commands are in `QUALITY_95_PROGRESS_REPORT.md` ("Laptop-return checklist" and "Laptop checklist additions").
- [ ] Bring the commits over, then run the Windows-only suites: 16 kernel skips, desktop with the `msvcrt` skills lock, and `jarvis/local_models`
- [ ] v1 live re-runs (`neko_safety_live.py`, `run_plans.py --repeat 5`), then `score.py`. v1 stays 7.61 until then
- [ ] `bench.py` on Windows: the Windows-only cases run instead of deferring
- [ ] Decider on the real GPU: `model_compare.py --models qwen3-4b-2507,qwen3-1.7b` with the Vulkan build
- [ ] Plugin metadata snapshots: rebuild (`rebuild-plugin-metadata.ps1`); 8 `plugin/tests` failures wait for it
- [ ] **OWNER:** microphone cases G-036 (a 44.1 kHz-only microphone) and G-037 (pause with the real microphone); owner voice recording and tuning
- [ ] **OWNER:** Chrome profiles: save each profile's Playwright extension token (`save_playwright_token.ps1`)
- [ ] **OWNER:** revoke the Dahl test key and delete the server's copy; change the approval PIN
- [ ] Fix H-042 and J-018

## Done at every phase
- [ ] `live_check.py` extended and green; unit tests green; gitleaks clean; commit (not pushed)
- [ ] `05_OPERATIONS.md` and `03_DECISIONS.md` updated; free disk space reported
