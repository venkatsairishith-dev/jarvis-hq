# The owner's 21 handwritten items: where each one stands

Updated 2026-10-23 (cloud session) from branch `claude/implementation-plan-designs-00v4r6` after the second kernel deploy
(audit fields and the Hermes policy endpoint are live on the VM). The full design for every item is in `research-2026-10-22/JARVIS_FULL_INTEGRATION_PLAN.md`; every file is explained in
`FILE_MAP.md`. Update this page when an item moves.

| Status | Meaning |
|---|---|
| **DONE** | Built, tested and in use |
| **BUILT** | Code merged with passing tests, but not yet switched on (not wired into the app, not deployed, or not exercised live) |
| **PARTIAL** | Part works today; the rest is planned |
| **TODO** | Not started; the plan phase is named |
| **OWNER** | Waiting on something only the owner can do |

| # | Item (owner's words) | Status | Today | Next (plan phase) |
|---|---|---|---|---|
| 1 | Full PC rights (admin) | TODO | PC actions go through the kernel's tiers and PIN; elevation only inside `repair.py` | PIN-gated admin broker with a fixed verb list (P4 step 5); the owner approves the verb list |
| 2 | Can go through any pages | PARTIAL | Sandboxed browser and signed-in Chrome lanes; banks and wallets always refused. Python egress proxy **BUILT** (`jarvis/egress/proxy.py`), Smokescreen still live | Switch `config/egress.json` to the Python proxy; CDP relay; taint flag (P4) |
| 3 | Human-like, interactive, explaining | PARTIAL | N.E.K.O companion, question cards for unclear requests | Narration from real run events, explanatory mode, barge-in (P8) |
| 4 | Windows widget and tray icon | PARTIAL | Neko icon widget with states; Tauri shell imported as a subtree (`jarvis/desktop/shell/`) | Tray, global push-to-talk, single instance (P8) |
| 5 | "Find folder X"; learn a new task and keep it as a skill | PARTIAL | File search and undoable file operations **BUILT** (`jarvis/node/jarvis_node/filesearch.py`, `fileops.py`); routine recipes **BUILT** (`jarvis/routines/recipe.py`); Hermes's skill safety scanner vendored (`skills_guard.py`, MIT); learning-loop layers 1-3 and the skill gate exist; skill curator (owner promotion checked against the audit log) merged on the laptop | Decider tools for find/create/organise (P4 step 2); trace recorder (P5) |
| 6 | Research a project, plan it, hand it to a coding agent | PARTIAL | Research plugin with checked citations, main_agent planner, kernel lab dispatch | Rebuild the lab host (P2); OpenCode coding agent and worktree manager (P6) |
| 7 | Test by spawning multiple agents | TODO | Benchmark areas cover races and restarts | Worktree-per-worker, read-only tests, reviewer agent (P6) |
| 8 | What Clawdbot (OpenClaw) / Grokbot (xAI Grok Bot) do | PARTIAL | Kernel safety (tiers, PIN, audit, kill switch) is ahead of both; routines and Gmail/Calendar drafts **BUILT**; posture self-check like `openclaw security audit` **BUILT** (`jarvis/tools/doctor.py`); morning brief into the vault **BUILT** (`jarvis/muse/brief.py`) | Schedule the brief at 07:30 and add calendar events; phone channel and approvals; chief-of-staff coordinator (P9) |
| 9 | Coordinate with ChatGPT / Gemini | PARTIAL | Gemini left off by the owner; the router can translate formats. Automating chatgpt.com, chat.openai.com, gemini.google.com or claude.ai (reading, clicking, typing) is refused by the kernel and on the laptop, as their terms require; opening them for you still works | Second opinion through the APIs; JARVIS tools callable over MCP (P9) |
| 10-11 | API reasoning model controls agents running small local models | PARTIAL | `local_team`: a remote adviser plans through the privacy gateway (private values become placeholders) and local models act. A planner-only gateway instance (port 48952: Claude until 2026-10-31, then Dahl) was added 2026-10-22; its live check is still to be confirmed on the laptop. Router core **BUILT** (`jarvis_kernel/router/`) | Wire the router into the gateway and `llm_proxy` (P3) |
| 12 | Voice: the best GitHub repo | PARTIAL | sherpa-onnx wake word, Silero, Parakeet, IndicConformer, Kokoro, Piper | Live microphone test (OWNER), "Hey Jarvis", echo cancellation, Smart Turn (P8) |
| 13 | 10-20 local models, one at a time or combined | PARTIAL | 15 models pinned (`MODELS.md`), loaded on demand by llama-swap | Deep offline model (gpt-oss-20b) and the role roster (P3); D: has 31 GB free |
| 14 | Its own personality | BUILT | JARVIS character card **BUILT** (`jarvis/persona/jarvis_card.json`: calm, direct, no flattery, never approves, outside text is data), installer that replaces N.E.K.O's "act without considering dangers" default prompt for this card only, and 18 anti-sycophancy cases with a runner | Install it on the laptop (N.E.K.O stopped), run the cases on the planner and local models, pick the British voice by ear (P8) |
| 15 | On-screen tests on the server, fix errors, push to git | PARTIAL | 860-case benchmark and test image on the Linux server; caretaker opens PRs | Xvfb + noVNC runs, fix loop, kernel-gated push to main (P6) |
| 16 | Explain how it works, each model, each file | DONE | `FILE_MAP.md` (471 files, all described), `MODELS.md`, `HOW_IT_WORKS.md`; `make_docs.py --check` keeps them current | Keep regenerating |
| 17 | Step-by-step guide on how to use it | DONE | `HOW_TO_USE.md` | Update as features switch on |
| 18 | Coordinate with a coding CLI | PARTIAL | Byte-for-byte Claude Code passthrough **BUILT** (`router/passthrough.py`); the Agent SDK builder is running on the API credit | OpenCode driven over ACP with kernel approvals (P6) |
| 19 | Switch API providers by itself and in the CLI | BUILT | Combos, key pools, quotas, circuit breaker, $0 budgets and a lane-safe response cache in `jarvis_kernel/router/` (on the VM, not wired in yet) | Owner "switch to X" command and CLI endpoints (P3) |
| 20 | Its own VM doing browser research like openmuse | TODO / OWNER | Kernel and Hermes run on the Oracle VM (2 CPU / 11 GiB, within Always Free); the WSL agent computer was lost | Agent desktop on the VM (P2), after the owner checks both instances' shapes against the 2 OCPU / 12 GB free limit. Candidate: Cua Spaces for Hermes (`trycua/hermes-cua-spaces`, MIT, plugin 0.4.0 at `82bb8d3`): a fresh throwaway machine per job, watchable live; local Spaces only (no paid cua.ai cloud), pinned install (not `curl \| sh`), telemetry off, egress through the JARVIS proxy, kernel hook kept. Untested: arm64 on the Oracle VM and the Hermes upgrade it needs |
| 21 | Everything openmuse can do | BUILT | Page watches, finance CSV, durable task state, PDF forms, Gmail/Calendar drafts (no send path) **BUILT** in `jarvis/muse/`; the watch runner (checks on schedule, survives restarts, fetches only through the egress guard) and notifications (local inbox plus content-free phone pings over ntfy) **BUILT** (`runner.py`, `notify.py`); ideas with evidence **BUILT** (`ideas.py`); parity table below | Start the runner from the supervisor, show the inbox in the console, owner creates an ntfy topic; browser profiles with takeover; no-network computer (P7) |

## openmuse parity (item 21)

Each row of openmuse's own feature inventory (`docs/FEATURES.md` at commit 1ac68f3) against JARVIS. "Tests" are the files that
pin the behaviour; a row is only DONE when it is also switched on.

| openmuse area | JARVIS today | Tests | Missing |
|---|---|---|---|
| Chat / delegated work, durable tasks, confirmed outcomes | N.E.K.O chat + main_agent; `muse/taskstate.py` (leases, approval waits, no blind retry after an uncertain action) **BUILT** | `test_taskstate*.py` | Wire taskstate to main_agent and the kernel PIN flow (DBOS in the plan) |
| Ideas / personal context | `muse/ideas.py` (mail and goal rules, evidence, accept/dismiss, no duplicate tasks) **BUILT**; memory is N.E.K.O's | `test_ideas.py` | A Gmail reader to feed it; goals store; console screen |
| Goals / tracking: watches, retry/backoff, pause | `muse/watch.py`, `muse/runner.py` **BUILT** | `test_watch*.py`, `test_runner.py` | Supervisor starts the runner; console screen for watches |
| Browser: persistent profile, page reads, takeover | Owner's Chrome profiles via the Playwright extension (live); sandboxed agent browser | `jarvis/kernel/tests/test_browser.py`, `test_neko_node.py` | Takeover view and the agent's own computer (item 20) |
| Linux computer (no network, bounded commands) | Not built | none | podman container, 30 s / 128 KB limits (plan P7 step 8) |
| Gmail / Calendar drafts with reviewed writes | `muse/google_drafts.py` (no send path) **BUILT** | `test_google_drafts*.py` | Google OAuth on the laptop (owner input) |
| PDF job: import, typed input, preview, reviewed reply, receipt | `muse/pdfforms.py` **BUILT** | `test_pdfforms.py` | Input card and preview in the console |
| Generated results: reports, finance CSV | `deliverables` plugin (decks, PDFs, live); `muse/finance.py` **BUILT** | deliverables tests, `test_finance*.py` | Finance in the console |
| Notifications: inbox, change alerts, restart reconciliation | `muse/notify.py` (inbox, dedupe, content-free ntfy) **BUILT**; daily brief `muse/brief.py` | `test_notify.py`, `test_brief.py` | Owner's ntfy topic; inbox screen |
| Connectors catalogue | Kernel connectors (local MCP servers, per-tool risk) live | `jarvis/kernel/tests/test_connectors.py` | Nothing for parity |
| OpenBot adapter | Not applicable (disabled upstream too) | none | none |

## Waiting on the owner

- The Claude planner stays until 2026-10-31 (the promo credit's end on the Console); the Agent SDK builder is set to 2026-11-05.
  Check the Console before 31 Oct: spend after the credit ends is billed to the card (within the $50 monthly limit).
- The live microphone test and "Hey Neko" recordings (`05_OPERATIONS.md`, "Hey Neko" section).
- Oracle console: the shapes of both instances against the free 2 OCPU / 12 GB total (never touch `hermes-agent-vm-2`).
- Merging the work into `main` when the phases are done.
