# The owner's 21 handwritten items: where each one stands

Updated 2026-10-23 (cloud session) from branch `claude/implementation-plan-designs-00v4r6` at the integration build's second
batch. The full design for every item is in `research-2026-10-22/JARVIS_FULL_INTEGRATION_PLAN.md`; every file is explained in
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
| 5 | "Find folder X"; learn a new task and keep it as a skill | PARTIAL | File search and undoable file operations **BUILT** (`jarvis/node/jarvis_node/filesearch.py`, `fileops.py`); routine recipes **BUILT** (`jarvis/routines/recipe.py`); learning-loop layers 1-3 and the skill gate exist | Decider tools for find/create/organise (P4 step 2); skill curator and trace recorder (P5, builder batch 3) |
| 6 | Research a project, plan it, hand it to a coding agent | PARTIAL | Research plugin with checked citations, main_agent planner, kernel lab dispatch | Rebuild the lab host (P2); OpenCode coding agent and worktree manager (P6) |
| 7 | Test by spawning multiple agents | TODO | Benchmark areas cover races and restarts | Worktree-per-worker, read-only tests, reviewer agent (P6) |
| 8 | What Clawdbot (OpenClaw) / Grokbot (xAI Grok Bot) do | PARTIAL | Kernel safety (tiers, PIN, audit, kill switch) is ahead of both; routines and Gmail/Calendar drafts **BUILT** | Phone channel and approvals, morning brief, chief-of-staff coordinator (P9) |
| 9 | Coordinate with ChatGPT / Gemini | TODO | Gemini left off by the owner; the router can translate formats | Second opinion through the APIs; JARVIS tools callable over MCP (P9) |
| 10-11 | API reasoning model controls agents running small local models | PARTIAL | `local_team`: a remote adviser plans through the privacy gateway (private values become placeholders) and local models act. A planner-only gateway instance (port 48952: Claude until 2026-10-31, then Dahl) was added 2026-10-22; its live check is still to be confirmed on the laptop. Router core **BUILT** (`jarvis_kernel/router/`) | Wire the router into the gateway and `llm_proxy` (P3) |
| 12 | Voice: the best GitHub repo | PARTIAL | sherpa-onnx wake word, Silero, Parakeet, IndicConformer, Kokoro, Piper | Live microphone test (OWNER), "Hey Jarvis", echo cancellation, Smart Turn (P8) |
| 13 | 10-20 local models, one at a time or combined | PARTIAL | 15 models pinned (`MODELS.md`), loaded on demand by llama-swap | Deep offline model (gpt-oss-20b) and the role roster (P3); D: has 31 GB free |
| 14 | Its own personality | TODO | N.E.K.O character system | JARVIS character card and voice (P8) |
| 15 | On-screen tests on the server, fix errors, push to git | PARTIAL | 860-case benchmark and test image on the Linux server; caretaker opens PRs | Xvfb + noVNC runs, fix loop, kernel-gated push to main (P6) |
| 16 | Explain how it works, each model, each file | DONE | `FILE_MAP.md` (436 files, all described), `MODELS.md`, `HOW_IT_WORKS.md`; `make_docs.py --check` keeps them current | Keep regenerating |
| 17 | Step-by-step guide on how to use it | DONE | `HOW_TO_USE.md` | Update as features switch on |
| 18 | Coordinate with a coding CLI | PARTIAL | Byte-for-byte Claude Code passthrough **BUILT** (`router/passthrough.py`); the Agent SDK builder is running on the API credit | OpenCode driven over ACP with kernel approvals (P6) |
| 19 | Switch API providers by itself and in the CLI | BUILT | Combos, key pools, quotas, circuit breaker and $0 budgets in `jarvis_kernel/router/` | Owner "switch to X" command and CLI endpoints (P3) |
| 20 | Its own VM doing browser research like openmuse | TODO / OWNER | Kernel and Hermes run on the Oracle VM (2 CPU / 11 GiB, within Always Free); the WSL agent computer was lost | Agent desktop on the VM (P2), after the owner checks both instances' shapes against the 2 OCPU / 12 GB free limit |
| 21 | Everything openmuse can do | BUILT | Page watches, finance CSV, durable task state, PDF forms, Gmail/Calendar drafts (no send path) **BUILT** in `jarvis/muse/` | Scheduler, inbox and UI wiring; browser profiles with takeover; no-network computer (P7) |

## Waiting on the owner

- Answer the laptop session's three questions: second kernel deploy, vendoring Hermes's `skills_guard.py`, and the Claude planner's
  expiry date (the Console showed the $100 credit expiring 2026-10-31).
- The live microphone test and "Hey Neko" recordings (`05_OPERATIONS.md`, "Hey Neko" section).
- Oracle console: the shapes of both instances against the free 2 OCPU / 12 GB total (never touch `hermes-agent-vm-2`).
- Merging the work into `main` when the phases are done.
