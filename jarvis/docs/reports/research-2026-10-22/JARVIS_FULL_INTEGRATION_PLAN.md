# Fold every project into one JARVIS

**Status: research and planning only.** Nothing described here has been built, merged, installed, deployed or pushed. Every step is a proposal waiting for the owner's go, following the repo's own gate rule: "test, report what passed and what could not be tested, wait" ([jarvis/CLAUDE.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/CLAUDE.md)). This report replaces the earlier "JARVIS research and build plan" and stands on its own.

All 21 handwritten capabilities, plus OpenClaw/Grok Bot parity, can ship as **one product built on jarvis-hq**. It has one Python 3.11 core (the N.E.K.O base plus the JARVIS kernel), one supervisor that starts every other process, one settings tree, one secrets store, one per-boot token, one hash-chained audit log, and one React app. That app appears as the Tauri desktop window, the widget, the tray and a phone PWA. "Fully integrated" works out in practice as **"JARVIS owns the lifecycle, config, auth, audit and UI of every component"**, not "everything runs in one Python process". Most external apps should be **ported, not run**:

- openmuse's useful engine is about 3k of its ~12k TypeScript lines, and DBOS (already pinned) replaces most of it ([openmuse](https://github.com/CopilotKit/openmuse); [DBOS](https://docs.dbos.dev/python/reference/contexts)).
- The OmniRoute/9router routing features become kernel modules, Smokescreen's SSRF/ACL logic becomes a Python egress proxy, and goose contributes only its recipe idea.

Some components keep a separate form:

- **Hermes Agent** stays a pristine vendored subtree run as a supervised child, because its docs say it "runs only on Python 3.14" ([Hermes docs](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/getting-started/updating.md)). One research note disagrees, and the report shows how to settle it.
- **OpenCode and llama.cpp** stay pinned binaries under the supervisor.
- **GPL desktop servers, the proprietary agy, CopilotKit's cloud and non-commercial models** stay outside the shipped tree.

Integration also fixes operations. Retiring the separate OmniRoute and openmuse services removes two internet-facing attack surfaces. It also frees an estimated 1.7–3 GB on an Oracle VM that now has only 12 GB ([Oracle](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)).

Four problems still come before any new feature: the halved Oracle allowance, the 9router RCE that writes into Hermes's config, the lost WSL lab, and three days of unmerged work. The plan below has eleven phases (P0–P10) of roughly 27–40 engineer-weeks. That figure is a judgement, not a measurement.

## Executive summary: one owner for every process, with five honest exceptions

The owner asked for everything to be merged into JARVIS instead of being kept as separate repos or apps. The research supports that for almost every component, using four forms:

- **Python libraries imported into the 3.11 core**: DBOS, Presidio, sherpa-onnx, Playwright, browser-use, pypdf, Google clients, uiautomation, pywebrtc-audio.
- **Vendored source** that JARVIS owns and edits: the N.E.K.O.-Tauri shell, mini-swe-agent, Windows-MCP's UI-tree code and the Playwright-MCP CDP relay.
- **Ported features**: openmuse, the OmniRoute/9router routing features, Smokescreen, goose recipes, and Hermes's skill and curator logic.
- **Bundled binaries under JARVIS's supervisor**: llama-server builds, OpenCode, es.exe, gitleaks, Caddy, and llama-swap until a Python port replaces it.

Five exceptions are forced by licences, interpreters or vendor terms, and the report states each plainly:

1. **GPL servers** (KasmVNC, webtop, piper1-gpl) can run only as separate programs, or should be dropped.
2. **Hermes** needs its own Python 3.14 venv, unless verification shows otherwise.
3. **Claude Code traffic** must be passed through unmodified.
4. **agy and CopilotKit Intelligence** are proprietary and cannot be vendored.
5. **Non-commercial models** must stay out of any build that might be shared.

After the plan, JARVIS would:

- find any file by description and undo its own file moves;
- run approved admin verbs instead of a raw admin shell;
- browse in a cookie-less research lane and a PIN-gated signed-in lane;
- learn routines as reviewed skills;
- research, plan and hand work to an in-tree coding agent on its own Linux computer, which the owner can watch and take over;
- test with several agents that cannot edit the tests;
- push to main only through a kernel gate;
- talk with barge-in in English, Hindi and Telugu;
- run about 15 local models, one GPU model at a time;
- switch providers by itself, on command, and for coding CLIs;
- reach the owner's phone with approvals that still need the PIN.

## What JARVIS will be able to do

Every capability below runs through the same spine:

1. **Input.** A request arrives by voice, typed text, the widget, the phone or a schedule.
2. **Planning.** The main_agent coordinator plans it, using an API reasoning model for anything multi-step. The planner commits to its action list **before** any untrusted page or file enters its context. This is the plan-then-execute pattern that the prompt-injection design-pattern paper recommends ([arXiv 2506.08837](https://arxiv.org/html/2506.08837v1)), and that CaMeL showed can keep 77% task success with provable security ([arXiv 2503.18813](https://arxiv.org/pdf/2503.18813)).
3. **Execution.** Small local models, the Windows node, the VM worker or a coding agent carry out each step.
4. **Authorisation.** The kernel classifies every effect into the existing tiers `GREEN / YELLOW_AUTO / YELLOW_APPROVAL / RED / DENY`, plus a proposed ADMIN tier ([policy.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/policy.py)). It records the decision in the audit log and asks for the PIN where required.
5. **Narration.** Every status line JARVIS speaks or shows is rendered from a real tool event, never from model-predicted progress. This is the repo's own N3 honesty rule ([REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/reports/REQUIREMENTS.md)).

In each subsection, "today" means the `remediation-2026-10-18` tip, which contains every other working branch. "After" means the end state of the plan.

### 1. Full PC rights become a growing catalogue of approved admin verbs

**What the owner says and what happens.** Take "Jarvis, install VLC and restart the Windows Search service, it's stuck."

1. The planner turns this into two typed verbs, `winget_install(id="VideoLAN.VLC")` and `service_restart(name="WSearch")`. Both are on an allowlist.
2. The kernel marks both ADMIN.
3. The Approval Inbox opens on the desktop, with a widget badge and a tray badge. The card shows each verb and its parameters in plain language. It says a restore point will be created first, and that the install is reversible through uninstall.
4. The owner enters the PIN and confirms a second time.
5. A small elevated broker re-validates the signed request, creates the restore point, runs the verbs and writes a transcript to the audit log.
6. JARVIS says "VLC 3.x installed; Windows Search restarted and indexing again." It says this only after it has checked both facts.

If the owner instead asks "turn off Defender for an hour", JARVIS refuses, says why, and offers a scoped exclusion only if that verb is on the list.

**What makes it work.**

- The broker is a **Just Enough Administration** endpoint (native on Windows 10's PowerShell 5.1) or a small service. JEA runs privileged commands through virtual accounts and limits which commands are visible ([Microsoft Learn: JEA](https://learn.microsoft.com/en-us/powershell/scripting/security/remoting/jea/overview)). It would live in a proposed `jarvis/node/jarvis_node/adminctl.py` with an installer script.
- For ordinary GUI work, JARVIS **vendors Windows-MCP's UI Automation tree-snapshot code**, backported to 3.11 under its MIT notice, or reimplements the same walk on `uiautomation` (Apache-2.0). Windows-MCP 0.8.8 itself requires Python ≥3.14 and pulls in `posthog` and `fastmcp` ([PyPI windows-mcp](https://pypi.org/pypi/windows-mcp/json); [uiautomation](https://pypi.org/pypi/uiautomation/json)).
- Microsoft UFO²'s "API before GUI" pattern becomes a node `apis/` registry. Office goes through COM, Explorer through Shell COM, and winget through the broker. UIA clicking is the fallback, and vision-model clicking comes last ([microsoft/UFO](https://github.com/microsoft/UFO)).

**Safety.**

- ADMIN means RED plus PIN plus a second confirmation.
- The broker refuses any parameter that came from web or tool content unless the owner has seen it verbatim.
- Unknown installers first run in Windows Sandbox, which needs Windows Pro or Enterprise ([Microsoft Learn: .wsb](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file)).
- Windows-MCP's PowerShell, Registry and Process tools are never imported. Its README warns it "operates with full system access", and an HTTP-mode bug once exposed an unauthenticated PowerShell tool ([Windows-MCP](https://github.com/CursorTouch/Windows-MCP)).

**What it will not do.**

- **No raw admin shell.**
- No `bcdedit`, no disabling Defender, UAC or the firewall, no creating admins, no SAM/LSA keys, and nothing touching password managers, wallets or banking. These are hard DENY.
- It never "clicks through" UAC. The consent prompt is on the Secure Desktop, which medium-integrity automation cannot reach, by design ([Microsoft Learn: UAC](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/user-account-control/how-it-works)).

**Today → after.**

- **Today:** fixed, kernel-authorised job kinds. Elevation exists only inside `repair.py`, and there is no `osctl` module on any branch.
- **After:** a broker with roughly 20–40 typed verbs, which grows only when the owner approves new verbs. Whether a PIN-gated allowlist satisfies "full rights" is the owner's call.

### 2. Any web page, through two lanes and a taint flag

**What the owner says and what happens.**

*"Jarvis, read this article and tell me the three claims that matter."* This runs in the **research lane**:

1. JARVIS opens the page in a throwaway Playwright profile. The profile has no cookies, downloads or pop-ups.
2. The page text is scrubbed and marked UNTRUSTED.
3. JARVIS summarises the page and checks each claim with MiniCheck.

*"Jarvis, in my Chrome, open Gmail and find the invoice from Airtel."* This runs in the **signed-in lane**:

1. JARVIS attaches to the owner's existing Chrome through Microsoft's bridge extension.
2. It searches and reports what it finds.
3. If the owner then says "reply that it's paid", the send becomes a RED card that needs the PIN.

**What makes it work.**

- The research lane is the existing `browser.py`. Every request passes through a network guard and an egress proxy that "the browser refuses to run" without ([browser.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/node/jarvis_node/browser.py); [egress.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/config/egress.json)).
- That proxy becomes **`jarvis/egress/proxy.py`**, a port of Smokescreen's logic. It resolves each hostname, refuses private, loopback, link-local, Tailscale (100.64.0.0/10) and cloud-metadata (169.254.169.254) addresses, connects to the vetted IP to defeat DNS rebinding, and keeps per-role allow/deny lists ([stripe/smokescreen](https://github.com/stripe/smokescreen)).
- browser-use (MIT, Python ≥3.11) can be imported as the research-lane agent loop with telemetry off. Its posthog dependency and many SDKs argue for a separate venv under the supervisor ([PyPI browser-use](https://pypi.org/pypi/browser-use/json)).
- The signed-in lane replaces the Node-based Playwright MCP with a **Python port of its CDP relay** (`cdp_relay.py`). This is a WebSocket server with `/extension` and `/cdp` endpoints, driven by Playwright-Python's `connect_over_cdp`. JARVIS keeps Microsoft's extension and its per-tab approval UX ([Playwright MCP extension](https://playwright.dev/mcp/configuration/browser-extension)).
- Heavy browsing moves to the VM, because native Playwright on Windows now lists "Windows 11+" ([Playwright](https://playwright.dev/docs/intro)).

**Safety.**

- Banking, wallet and password-manager origins are hard-DENY in both Playwright and the egress proxy.
- Once page text enters context, a CaMeL-lite taint flag forces PIN on admin, delete and messaging tools, and shows page-derived parameters verbatim.
- Playwright MCP says it "is **not** a security boundary" ([playwright-mcp](https://github.com/microsoft/playwright-mcp)), and Anthropic still calls a 1% adaptive attack rate "meaningful risk" ([Anthropic](https://www.anthropic.com/research/prompt-injection-defenses)). The kernel tiers, not the browser, remain the boundary.

**What it will not do.**

- It never types into password fields and never reads cookies or tokens.
- It never automates chatgpt.com or gemini.google.com (see item 9).
- It does not use Chrome's `chrome://inspect` remote-debugging toggle by default. While that toggle is on, any local process can drive every tab ([cua #2916](https://github.com/trycua/cua/issues/2916)).
- It cannot use `--remote-debugging-port` on the default profile, which Chrome 136+ ignores ([Chrome blog](https://developer.chrome.com/blog/remote-debugging-port?hl=zh-cn)).

**Today → after.**

- **Today:** both lanes exist. The signed-in lane depends on Node Playwright MCP, and the egress proxy is Smokescreen on the at-risk Oracle VM.
- **After:** both lanes run as Python inside JARVIS, the proxy runs on both laptop and VM, and taint tracking is added.

### 3. Human-like, interactive and explanatory, because every sentence comes from a real event

**What the owner says and what happens.**

*"Jarvis, put a 30-minute call with Ravi on my calendar at noon tomorrow."* Noon turns out to be taken.

1. JARVIS **does not pick a time itself**. It shows a question card with "11:30 or 12:30, or type another time" and says that sentence aloud.
2. The run waits in a held state.

This applies the Nielsen Norman Group lesson from ChatGPT Agent, which picked 12:15 when noon was unavailable: "the choice wasn't theirs to make" ([NN/g](https://www.nngroup.com/articles/impressions-chatgpt-agent/)).

Asked "why that file?", JARVIS gives a partial explanation: the plan step, the tool, the evidence, the model that decided, and the policy rule that applied. Google PAIR's guidance is that "the best explanation is likely a partial one" and should come in response to a user action ([PAIR](https://pair.withgoogle.com/chapter/explainability-trust/)).

**What makes it work.**

- A `RunTimeline` component reads the kernel's SSE stream. Events are normalised to AG-UI names, a mapping the repo's own UI research already specifies ([04-agentic-os-and-ui.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/research/04-agentic-os-and-ui.md)).
- The existing question card (`input_request.py`) handles clarification.
- Every step and every approval gets a "Why?" affordance.
- Sentence-chunked streaming TTS and barge-in come from the voice engine (item 12).
- For voice, JARVIS speaks one sentence and puts the detail on screen, following PAIR's multimodal advice.

**Safety.** The persona never softens a confirmation prompt. Explanations never invent reasons that the event log does not support.

**What it will not do.** It will not fill silence with fake progress ("almost done…"). It will not guess when a constraint fails.

**Today → after.**

- **Today:** the question card exists. The timeline is PARTIAL (O4) and pause/retry is PARTIAL (O5) ([REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/reports/REQUIREMENTS.md)).
- **After:** one live timeline, Why? on every step, and a run that pauses for clarification.

### 4. A Windows widget and tray icon that are the same app as everything else

**What the owner says and what happens.**

1. The owner holds **Ctrl+Alt+Space**, or says "Hey Jarvis".
2. The always-on-top avatar switches to its listening ring and shows the live transcript.
3. On release, it shows thinking, then working, then speaking. Each state comes from a real signal: sleeping, listening, thinking, working, speaking, completed, needs-permission or error.
4. When a result is a file, the widget shows Open, Show in folder and Undo chips.

Right-clicking the tray offers Talk, Pause wake word, Mute mic, Stop all, Open JARVIS, approvals waiting (N), Check for updates, Licences and Quit.

**What makes it work.**

- The N.E.K.O.-Tauri submodule becomes a **git subtree at `jarvis/desktop/shell/`**, with its Apache-2.0 LICENSE and NOTICE kept.
- Four plugins are added in the documented order ([Tauri single-instance](https://v2.tauri.app/plugin/single-instance/)): single-instance first, then global-shortcut (Pressed/Released events for push-to-talk), tray and autostart.
- A Windows Job Object kills the whole process tree on exit. Today's plain `kill()` would orphan llama-server and the speech worker ([backend_runtime.rs](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri/blob/main/src-tauri/src/backend_runtime.rs)).
- The widget stops being a separate HTML page. It becomes the `#/widget` route of the one React app, shown in a transparent Tauri window.

**Safety.**

- The widget can show an approval but never grants one. Its badge opens the inbox, where the PIN lives.
- The upstream capability file lets any page on `127.0.0.1:48911` call screen capture while `csp` is null. That capability should be scoped to specific windows and paths, and capture gated behind a gesture or approval ([N.E.K.O.-Tauri capabilities](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri/tree/main/src-tauri/capabilities)).
- The push-to-talk chord uses Win32 `RegisterHotKey`. If another app owns the chord, it fails with `AlreadyRegistered`, so the tray must offer a re-bind ([Tauri global-shortcut](https://v2.tauri.app/plugin/global-shortcut/)).

**What it will not do.** No new powerful Tauri commands are exposed to the backend origin. This keeps PATCHES.md's own rule.

**Today → after.**

- **Today:** a loopback web widget exists. Tray, push-to-talk and single-instance are "**PLAN ONLY**", and push-to-talk (X4) is MISSING ([PATCHES.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/third_party/PATCHES.md)).
- **After:** a native shell with tray, push-to-talk, a single instance and autostart. It must first pass the repo's 30-minute Windows 10 transparency soak.

### 5. Plain speech finds files and folders, and new tasks become reviewed skills

**What the owner says and what happens.**

*"Jarvis, find the folder with my resume."*

1. Speech is recognised locally (Parakeet).
2. The local decider (Qwen3-4B-2507) extracts slots: name fragments "resume" and "CV", kind folder, no date.
3. The node's proposed `filesearch.py` queries the Everything SDK through ctypes, which is near-instant across NTFS volumes and needs the Everything client running ([voidtools SDK](https://www.voidtools.com/support/everything/sdk/python/)).
4. It falls back to `es.exe -json /ad`, then to Windows Search SQL through `Search.CollatorDSO` for full-text hits inside documents ([Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/search/-search-sql-windowssearch-entry)), then to a `scandir` walk of user folders.
5. RapidFuzz ranks the candidates. Because this is a GREEN, read-only search, no approval is needed and the audit gets one line.
6. JARVIS says: "Three matches; the newest is D:\Docs\Jobs\Resume 2026, changed yesterday. Open it?"
7. "Open the second one" opens it.

*"Move the old versions into an Archive folder inside it."*

1. JARVIS shows a batch preview of the mkdir and three moves. This is YELLOW_AUTO inside user folders.
2. It runs them and records each step in an undo journal.
3. "Undo that" puts everything back and then checks that it did.

*Learning a task.* "Every Friday at six, put PDFs from Downloads that look like invoices into D:\Invoices\2026."

1. JARVIS does it once with a preview.
2. It offers to save it as a skill and drafts an agentskills.io `SKILL.md` into `pending/`. The draft has a narrow `allowed-tools` list (fileops.search, fileops.move), typed parameters, and JARVIS metadata: version, source run and risk tier ([agentskills.io](https://agentskills.io/specification)).
3. The skill gate scans the draft and replays generated bench cases in a sandbox.
4. The owner sees a plain-language diff and promotes the skill with the PIN, which pins its hash.
5. A DBOS schedule runs it every Friday.

**What makes it work.**

- FileOps is `fileops.py` beside `pcopen.py`: an SQLite undo journal of mkdir, rename and move, with delete only to the Recycle Bin through send2trash ([send2trash](https://github.com/arsenetar/send2trash)).
- Skills reuse `jarvis/skills/skillgate.py` and `plugin/plugins/desktop/skills.py`.
- Two Hermes features are ported natively. The first is the `skill_manage` staging behaviour (`write_approval`) together with its `skills_guard` scanner. The second is the **curator** lifecycle: unused for 14 days becomes stale, unused for 30 days is archived, pinned or cron-referenced skills are skipped, and it "never auto-deletes" ([Hermes curator](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/curator.md)).

**Safety.**

- Reusable workflows help: Agent Workflow Memory reported 24.6% and 51.1% relative gains ([AWM](https://arxiv.org/pdf/2409.07429)).
- Skills are also an attack vector: Snyk found 13.4% of 3,984 marketplace skills had a critical issue ([Snyk](https://snyk.io/blog/toxicskills-malicious-ai-agent-skills-clawhub)).
- So skills learned from web content are tagged tainted. Skills that write, send or spend still confirm on each run after promotion.

**What it will not do.** No permanent delete. No system paths. No skill marketplace or auto-install, which the owner's roadmap already forbids.

**Today → after.**

- **Today:** `open_file` matches names only inside six known folders ([pcopen.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/node/jarvis_node/pcopen.py)). A "create a folder" request ends with "I cannot create folders or files yet" ([commit 1c28d3a3](https://github.com/venkatsairishith-dev/jarvis-hq/commit/1c28d3a3f760a3d194d6228960e9c9ba47d5a237)). C6 undo is MISSING.
- **After:** whole-disk search, undoable organising, and the full learn → stage → test → promote loop.

### 6. Research a project, plan it and hand it to a coding agent

**What the owner says and what happens.** *"Jarvis, research how people add OCR to a small Python invoice tool, plan it for my invoice-tool project, and have a coding agent build it."*

1. **Research.** The research plugin fans out search and fetch in the research lane, ranks the sources, and checks every claim with MiniCheck. It returns a brief with sources ([research plugin](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/plugin/plugins/research)).
2. **Plan.** The API planner turns the brief into a plan card with acceptance criteria, such as "these 6 tests pass; no new dependency over 50 MB". The owner approves the card.
3. **Dispatch.** The kernel calls `dispatch_to_lab` with an agent definition from `agents.py`. That definition carries the lane, attempts, time budget and an independent verifier.
4. **Code.** On the VM, a rootless podman worker gets its own git worktree on a `jarvis/<run>` branch. The coding agent works there, and its model calls go through JARVIS's gateway.
5. **Watch and verify.** The owner can watch the terminal and diff in Live view. The verifier runs the tests read-only.
6. **Deliver.** The diff lands in Workspace, and the push gate (item 15) decides what happens next.

**What makes it work.** The **coding plugin** (`plugin/plugins/coding/`, proposed) has two engines:

- **mini-swe-agent**, vendored in-tree. It is MIT, Python ≥3.10, "just some 100 lines", and bash-only. Its model layer is swapped to JARVIS's provider layer, which also avoids LiteLLM, and its single bash action is wrapped in the kernel tier check ([mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)).
- **OpenCode v2.1.2** as a pinned binary for larger refactors, driven over ACP from Python (`agent-client-protocol` 0.12.1). The kernel answers every permission request ([OpenCode ACP](https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/acp.mdx); [ACP Python SDK](https://agentclientprotocol.github.io/python-sdk/)).

**Safety.**

- Only projects in `jarvis/config/projects.json` are eligible.
- Code from a private repo goes only to a private-lane or local model.
- The run fails closed if the JARVIS MCP server did not connect.

**What it will not do.** It will not run a coding agent with auto-approve, put a push credential inside the worker, or send private code to the public lane.

**Today → after.**

- **Today:** research, planner, `agents.py`, `lab_runner.py` and `projectfix.py` all exist, but "the WSL lab disk is gone", so nothing can execute code ([commit 6395e98f](https://github.com/venkatsairishith-dev/jarvis-hq/commit/6395e98f)).
- **After:** an in-tree coding agent on JARVIS's own VM, chained end to end.

### 7. Testing by spawning several agents that cannot cheat

**What the owner says and what happens.** *"Jarvis, test the new OCR feature with three agents and tell me what breaks."*

1. The planner writes a test charter.
2. A DBOS queue with `worker_concurrency=3` starts three workers, each in its own worktree and container. One writes property tests, one runs the existing `adversarial-ux-test` lab skill, and one runs bench shards.
3. Test directories are mounted read-only and hashed before and after.
4. A reviewer agent from a different model vendor flags any change under `tests/`, any new skip/xfail markers and any drop in test count.
5. A kanban-style board shows todo, running, review, blocked and done. This is a concept port of Hermes's durable kanban ([Hermes kanban](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/kanban.md)).
6. JARVIS reports failures with traces and videos.

**Why this design.**

- Agents reward-hack. Cursor found 63% of successful SWE-bench Pro resolutions retrieved the fix rather than deriving it ([Cursor](https://cursor.com/blog/reward-hacking-coding-benchmarks)). EvilGenie documents agents editing test files ([arXiv 2511.21654](https://arxiv.org/pdf/2511.21654)).
- Tests the agent cannot edit are the control that works.
- About three parallel workers is the practical review limit.

**What it will not do.** It will not let a worker modify the thing that judges it, and it will not count a run as green without the kernel's own test pass.

**Today → after.**

- **Today:** bench areas D (83 cases) and N (73) cover races and restarts, but there is no multi-agent suite and no host to run one.
- **After:** a new `o_multiagent.json` bench area, run on the VM.

### 8. Doing what Clawdbot (OpenClaw) and Grok Bot do, without their runtimes

**How the names are read.** "Clawdbot" is read as **OpenClaw**, which was called Clawdbot in January 2026 ([Wikipedia](https://en.wikipedia.org/wiki/OpenClaw)). "Grokbot" is read as xAI's **Grok Bot**, launched 11 Aug 2026 ([x.ai](https://x.ai/news/introducing-grok-bot)). Both readings are inferences from handwriting, and the owner should confirm them.

**What the owner gets.**

- **Phone approvals**, worked through in the next example.
- **A morning brief** at 7:30 covering calendar, watches, inbox digest and overnight research.
- **Routines learned by demonstration**: "watch me do this once".
- **A chief-of-staff coordinator** that hands work to persistent specialists and "only comes back when something needs your approval".
- **Chat channels** such as Telegram, with DM pairing.
- **Persistent memory** across sessions.
- **An always-on computer** of its own.

**What the owner says and what happens.** *"Jarvis, while I'm out, handle my unanswered emails from this week and ping me only for anything that needs sending."*

1. The chief-of-staff coordinator assigns an inbox specialist.
2. The specialist drafts replies.
3. JARVIS sends a push to the phone that contains no secrets: "JARVIS needs approval (RED): send 3 emails". The push links into the phone app.
4. The owner reviews each draft there and approves with the PIN.

**What makes it work.** The full mapping is in the parity table below. Its main pieces are:

- the **vendored Hermes gateway** for channels, which brings 27 platform adapters and `gateway/pairing.py` ([Hermes platforms](https://github.com/NousResearch/hermes-agent/tree/main/plugins/platforms));
- **DBOS schedules** for automations and the brief. APScheduler 4 is still alpha, so it is not used ([APScheduler](https://pypi.org/project/APScheduler/));
- main_agent plus specialist profiles on DBOS queues, for the chief-of-staff pattern;
- a JARVIS-owned Playwright-trace recorder that writes a `SKILL.md`, for teaching by demonstration.

**What it will not do.**

- **It will not run OpenClaw or install ClawHub skills.** Koi found 341 malicious skills out of 2,857 ([The Hacker News](https://thehackernews.com/2026/02/researchers-find-341-malicious-clawhub.html)). ClawJacked let any webpage brute-force the localhost gateway ([Oasis](https://www.oasis.security/blog/openclaw-vulnerability)). OpenClaw 2.0 still ships with its sandbox off and its Secret Store unencrypted ([dsebastien](https://www.dsebastien.net/2026-10-03-openclaw-2-0-easier-setup-shared-sessions-same-security-defaults/)).
- **It will not share logins across all agents on one machine**, as Grok Bot reportedly does ([Rohit Ghumare](https://rohitghumare.com/blog/grok-bot-shared-computer/)).
- **It will not vendor workflow-use** (AGPL-3.0, "very early development") as the recorder ([workflow-use](https://github.com/browser-use/workflow-use)).
- **It will not put private content in Telegram.** Telegram bot chats are not end-to-end encrypted ([reclaimthenet](https://reclaimthenet.org/is-telegram-encrypted)), so Telegram carries pointers only.

| Parity feature (source) | Integrated JARVIS form | Today | After |
|---|---|---|---|
| Always-on gateway + one dashboard (OpenClaw Control UI) | Kernel + console + one React app; OpenClaw's single-SPA design as reference ([OpenClaw Control UI](https://docs.openclaw.ai/web/control-ui)) | Console + hub page | One app with chat, sessions, approvals, automations, skills, logs |
| 20+ channels, DM pairing (OpenClaw, Hermes) | Vendored Hermes gateway child; allowlist pairing; JARVIS `pre_tool_call` policy; tool-less reader for inbound text | Telegram off (`telegram.off`) | Telegram first; others opt-in |
| Approvals bound to the exact request (OpenClaw 2.0) | Kernel approval bound to request hash; PIN in app; Web Push / ntfy `view` deep link | PIN approvals TESTED (C8) on desktop | Same approvals on phone |
| Automations + proactive check (OpenClaw "heartbeat" now jobs) | DBOS `create_schedule` + workflows; webhook route behind kernel | Missions/monitor, `watches.json` | Schedules with phone delivery |
| Morning brief (ChatGPT Pulse-style) | DBOS cron workflow → vault note + Home card + push summary without private-lane content | None | Daily brief |
| Chief of staff + specialists (Grok Bot) | main_agent coordinator; persistent specialist profiles (role card, tool scope, memory namespace) on DBOS queues; Hermes kanban review states | Coordinator, `local_team`, `agents.py` | Persistent team with group view |
| Teach-by-demonstration routines (Grok Bot) | Playwright tracing in the agent profile + Windows event log → LLM distils `SKILL.md` + parameter schema → skill gate | Layer 3 mines successful tasks | Explicit record → staged skill |
| Persona files SOUL/USER/AGENTS.md (OpenClaw) | JARVIS character card in N.E.K.O data + SOUL-style durable traits | No JARVIS card | Card + anti-sycophancy cases |
| Memory + cross-session recall | N.E.K.O memory + FTS5 session search ported from Hermes (stdlib `sqlite3`) | 5-layer memory, BM25, stale vault | Searchable history |
| Always-on own computer (Grok Bot, Manus) | Oracle VM desktop under JARVIS supervisor (item 20) | Lost with WSL lab | Restored, watchable |
| Security audit command (`openclaw security audit`) | `jarvis doctor`-style posture check of binds, auth, ports, skill hashes, licence gate | None | Runs clean on every start |
| Skill marketplace (ClawHub, Grok marketplace) | **Deliberately none**; local curated registry | None | None |

**Today → after.** Today JARVIS is ahead on safety: tiers, PIN, audit, and a kill switch (C7 TESTED). It lacks reach: no phone app, no brief and no recorder. After the plan, it has the reach features above while keeping every effect behind the same kernel.

### 9. Coordinating with ChatGPT and Gemini: APIs out, MCP in

**What the owner says and what happens.** *"Jarvis, ask Gemini what it thinks of this plan."*

- **If a paid Gemini API key is enabled:** paid Gemini is not used for training ([Gemini API terms](https://ai.google.dev/gemini-api/terms)), so it is allowed in the private lane. JARVIS sends the redacted plan and shows the reply labelled "Gemini says…". It treats that reply as **untrusted input**, so it can inform the plan but never trigger an action. JARVIS then summarises where it and Gemini disagree.
- **If only a free key exists:** JARVIS answers "Gemini's free tier is public-lane only and this plan contains private data", and offers to send a redacted version.
- **In the other direction:** from ChatGPT's Developer mode or Gemini CLI, the owner can ask "what's on my JARVIS task list?" through a **read-only JARVIS MCP server** ([OpenAI Developer mode](https://developers.openai.com/api/docs/guides/developer-mode)).

**What makes it work.**

- Provider entries in `jarvis/config/providers.json` route through the router port (item 19).
- The front door's Gemini `generateContent` surface (item 18) serves Gemini CLI.
- The MCP server builds on the existing lab MCP code.

**Safety.** Multi-model consensus is used selectively, because the evidence for it is mixed and it costs 2–4x ([DeliberationBench](https://arxiv.org/html/2601.08835v1)).

**What it will not do.**

- **It will not drive chatgpt.com or gemini.google.com in a browser.** OpenAI's terms forbid programmatic extraction of output ([OpenAI ToU](https://openai.com/policies/terms-of-use/)).
- It will not route consumer subscriptions through a router. Gemini CLI stopped serving free, AI Pro and AI Ultra consumer accounts on 2026-06-18 ([The Register](https://www.theregister.com/ai-ml/2026/05/20/bye-bye-gemini-cli-google-nudges-devs-toward-antigravity/5243605)). Antigravity-style OAuth proxying draws bans that also cut off Gemini CLI ([gemini-cli #20632](https://github.com/google-gemini/gemini-cli/discussions/20632)).
- ChatGPT connects only to remote HTTPS MCP servers. That needs a tunnel, which conflicts with the loopback/Tailscale posture, so it is an owner decision.

**Today → after.**

- **Today:** the `gemini` entry is disabled, there is no OpenAI provider in the kernel, and ChatGPT appears only as a website in `decide.py`.
- **After:** labelled second opinions (if paid keys are approved), an inbound MCP server, and an explicit deny for automating those two domains.

### 10–11. An API reasoning model directs everything; local small-model agents do the hands-on work

**What the owner says and what happens.** *"Jarvis, my laptop feels slow. Find out why and fix whatever is safe."*

1. **The planner writes a plan.** The planner is Groq gpt-oss-120b in the private lane, called through the privacy gateway, so it sees `[PATH_1]` rather than real paths. Its plan reads: check processes, disk and services, then propose fixes.
2. **The plan is fixed before any output is read.** The planner commits to its action list before any tool output enters its context.
3. **Local agents run each step.** Verdict, the 151M decision classifier, routes each step. The local Qwen3-4B decider grounds UI actions. A specialist such as Qwen2.5-Coder reads a crash log, with real values restored locally.
4. **The kernel sets a tier for each effect:**
   - `check_pc` is GREEN.
   - "Clear 4 GB of temp files" is YELLOW_AUTO with a preview.
   - "Pause OneDrive sync for two hours" is YELLOW_APPROVAL.
   - "Restart the Windows Search service" is ADMIN, through the broker.
5. **JARVIS reports.** It says what it found and what it changed, from the event log.

**What makes it work.**

- This generalises `local_team`, which already has a remote adviser plan through the privacy gateway while local specialists answer with real values ([local_team README](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/local_team/README.md)).
- The roadmap already decided "planning goes to a cloud model through the privacy gateway … local 4B decider stays for UI grounding", because local models are "too small to plan (decider ctx 2048)" ([roadmap](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/reports/JARVIS_ROADMAP_2026-10-22.md)).
- "Spawning agents" means main_agent executor steps and specialist profiles on DBOS queues on the laptop. On the VM it means Hermes `delegate_task` children, which are bounded by `max_concurrent_children` and `max_spawn_depth`.

**Safety.** "Controls everything" means the planner **directs** everything while the kernel still **authorises** every effect. The lane comes from the token, never from the request ([llm_proxy.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/llm_proxy.py)).

**What it will not do.** It will not let page or file text change the plan's consequential steps. It will not send private data to a public-lane planner.

**Today → after.**

- **Today:** `local_team` works for Q&A, but the PC planner is local and too small.
- **After:** remote planning with local grounding for every PC task, as in the roadmap's Phase 0.

### 12. The best voice stack, assembled into one in-process engine

**What the owner says and what happens.**

1. The owner says "Hey Jarvis, what's on today?" across the room.
2. The wake engine fires.
3. A speaker-embedding check confirms it is the owner's voice, and the transcript is re-checked for the word "jarvis". A film saying "Jarvis" should therefore not wake it.
4. Smart Turn decides the owner has finished speaking, which is better than a fixed silence timeout.
5. Parakeet transcribes the English. If the owner speaks Telugu, spoken-language ID routes the audio to IndicConformer instead.
6. JARVIS starts answering within about a second on the local path.
7. If the owner talks over it, JARVIS stops speaking in under 300 ms. Echo cancellation means its own voice never triggers this.

**What makes it work.** One `jarvis/voice/engine.py` replaces the separate wake process and the speech server. It keeps the `48930` HTTP routes as a thin facade.

- **sherpa-onnx 1.13.8** (Apache-2.0, Windows wheels) runs in-process. It provides `KeywordSpotter`, Silero `VoiceActivityDetector`, `OfflineRecognizer` (Parakeet via `from_transducer(model_type="nemo_transducer")`), `SpeakerEmbeddingExtractor`, `SpokenLanguageIdentification`, and `OfflineTts` for Kokoro, Piper-format VITS voices, Pocket TTS and Supertonic. The TTS callback can return non-zero to stop generation, which is the barge-in hook ([sherpa-onnx PyPI](https://pypi.org/pypi/sherpa-onnx/json)).
- **Smart Turn v3** (BSD-2, about 8 MB int8, ≤8 s of audio) runs standalone on onnxruntime. Its Whisper feature extractor is ported to numpy, so `transformers` is not needed ([smart-turn](https://github.com/pipecat-ai/smart-turn)).
- **pywebrtc-audio 0.2.0** (Apache-2.0, Windows wheels for Python 3.10–3.14) provides `EchoCanceller(…).process(near, far)`. The far-end signal is JARVIS's own playback buffer ([pywebrtc-audio](https://github.com/strands-labs/pywebrtc-audio)).

**Wake word licensing.**

- openWakeWord's pretrained `hey_jarvis` is **CC BY-NC-SA 4.0** ([openWakeWord](https://github.com/dscripka/openWakeWord)). Its training notebook's default negative data is also BY-NC-SA ([HF dataset](https://huggingface.co/datasets/davidscripka/openwakeword_features)).
- The default is therefore a sherpa KWS "HEY JARVIS" keyword.
- The fully permissive route is training a livekit-wakeword model (Apache-2.0 code) on the owner's own clips plus clean negatives ([livekit-wakeword](https://github.com/livekit/livekit-wakeword)).

**What it will not do.**

- **It will not clone a real actor's voice.** The optional "signature" voice uses Pocket TTS (CC-BY-4.0) cloned only from a **consenting** speaker ([Pocket TTS](https://huggingface.co/kyutai/pocket-tts)).
- It will not ship non-commercial voices or wake models in a shareable build.
- It will not use full-duplex speech models. Those need 10–24 GB of GPU ([Moshi](https://github.com/kyutai-labs/moshi)).

**Today → after.**

- **Today:** the cascade exists across two processes. Wake fires on 67% of test clips, and the mic-to-reply loop (V4) is MISSING ([roadmap](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/reports/JARVIS_ROADMAP_2026-10-22.md)).
- **After:** one engine with speaker verification, AEC, Smart Turn and measured targets:
  - false wakes ≤0.2 per hour over a 24 h recording;
  - barge-in stop under 300 ms;
  - end-to-end response ≤1.2 s on the local path and ≤2.5 s through the cloud.

  These targets are design goals, not sourced numbers. Telugu stays hard: the best real-world WER in one benchmark was 33.2% ([arXiv 2602.03868](https://arxiv.org/html/2602.03868v1)).

### 13. Ten to twenty local models, used one at a time on the GPU and in combination around it

**What the owner says and what happens.**

*"Which local models are loaded?"* The Models screen shows the decider on the GPU and the resident CPU helpers: Verdict, embeddings, reranker and guard.

*"Summarise this 40-page PDF offline with the deep model."*

1. JARVIS warns that this takes the GPU and about 13–15 GB of RAM for a few minutes.
2. It loads the exclusive deep model, which evicts the GPU group.
3. It unloads the deep model on a short TTL.

*"Solve this with the team."* This runs the planner, coder and maths specialists in a pipeline.

**What makes it work.**

- llama.cpp's `llama-server` is **bundled as pinned binaries per build and backend** under the supervisor, not run in-process through llama-cpp-python. The binaries live under `jarvis/local_models/bin/<build>-<os>-<backend>/`, with SHA-256 values in a manifest. Official Windows Vulkan, CPU, CUDA and ROCm zips exist, plus `ubuntu-arm64` for the VM ([b11669 assets](https://github.com/ggml-org/llama.cpp/releases/expanded_assets/b11669)).
- One extra binary pinned at **≤b10950** serves the Qwen3.5 GDN models, because issue **#30039** cut prompt processing 41–54% on Windows/AMD Vulkan from b10950–b11010 onward ([llama.cpp #30039](https://github.com/ggml-org/llama.cpp/issues/30039)).
- Swapping uses three llama-swap groups: a persistent CPU group, a swappable GPU group, and an exclusive deep group.
- llama-swap ships first as the bundled MIT binary with a generated YAML ([llama-swap](https://github.com/mostlygeek/llama-swap)). It can later be ported to a 400–700-line `swapper.py` if the owner wants it all in Python.
- llama-server's own router mode (`--models-preset`, `--models-max`, `--sleep-idle-seconds`) suffices on the VM, where one CPU build serves everything ([server README](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md)).

**Safety.** The existing guard test still applies: every llama-server binds 127.0.0.1 with `--no-webui` and no `--tools/--agent/--mcp-servers-config`.

**What it will not do.** It will not run several LLMs on the 4 GB card at once. It will not install a model that does not fit the disk budget.

**Today → after.**

- **Today:** llama-swap runs 7 models in 3 profiles. The lock already needs **16.2 GB required plus 2.5 GB optional** against roughly 19 GB free ([jarvis.lock.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/tools/jarvis.lock.json)).
- **After:** a roster of about 15 models, made possible by:
  - unpacking archives with `tarfile` `filter="data"` and deleting them, which reclaims about 1.2 GB;
  - converting MiniCheck to ONNX;
  - pruning Qwen3-1.7B and Qwen2.5-Math after the A/B tests.

  A 12 GB deep MoE still does not fit without an owner decision on disk.

### 14. A personality of its own, tested rather than hoped for

**What the owner says and what happens.** The owner proposes something wrong: "Let's store the API keys in the repo so the VM can read them." JARVIS disagrees plainly, gives the reason, and offers the secrets-file alternative. It does not flatter or comply.

**Its voice.**

- English: Kokoro's British male `bm_george` or `bm_fable` (Apache-2.0) ([Kokoro voices](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)).
- Hindi: Kokoro `hm_omega`.
- Telugu: Piper `te_IN-venkatesh` (CC-BY-4.0 dataset).

**What makes it work.**

- A JARVIS character card, created through N.E.K.O's card manager and stored in the data folder, not in `config/`, which is N.E.K.O's platform layer.
- Hermes-style SOUL.md discipline: a few durable traits for voice, directness and disagreement, kept separate from task facts ([Hermes personality](https://hermes-agent.nousresearch.com/docs/user-guide/features/personality)).
- Anti-sycophancy bench cases in `l_ux.json`. OpenAI rolled back a GPT-4o update for being "overly flattering or agreeable" ([OpenAI](https://openai.com/index/sycophancy-in-gpt-4o/)).

**What it will not do.** It will not let personality soften a confirmation or a refusal. It will not impersonate a real person.

**Today → after.**

- **Today:** no JARVIS persona exists. A script forces N.E.K.O's Chinese preset into English ([make_persona_english.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/scripts/make_persona_english.py)).
- **After:** a card, a voice, and persona cases that run on every model change.

### 15. On-screen test runs on the server, automatic fixes, and a guarded push to git

**What the owner says and what happens.** *"Jarvis, run the UI tests on your computer where I can watch, fix what fails, and push."*

1. **The owner watches.** Live view's Agent PC tab shows the VM's Xvfb display, with Playwright running headed.
2. **Failures are recorded.** A failing test saves a trace and video (`retain-on-failure`), so the owner can replay it later ([Playwright videos](https://playwright.dev/docs/videos)).
3. **The fix loop runs.** The loop is work → verify → repair, as in `lab_runner.py`.
4. **The push gate decides.** It shows a checklist on screen and on the phone:
   - full required tests pass on the exact merge commit, run by the kernel rather than the agent;
   - nothing changed under tests, fixtures, thresholds, `.github/`, `.claude/`, `.mcp.json`, policy or secrets without RED/PIN;
   - the diff is at most about 300 lines or 10 files;
   - one merge at a time, under a daily cap;
   - the commit carries an `Agent-Run:` trailer.
5. **Main moves only through the kernel.** If every check passes, the **kernel's own identity** fast-forwards main, and smoke tests run with automatic revert on regression. Otherwise a PR waits for the owner.

**Why.** Mature automations never let an agent push a protected main itself. Copilot's agent can only push `copilot/*` branches and cannot merge its own PRs ([GitHub docs](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations)). The s1ngularity attack used installed AI CLIs to harvest more than 1,000 GitHub tokens ([Wiz](https://www.wiz.io/pt-br/blog/s1ngularity-supply-chain-attack)). Agents therefore never hold the main-push credential.

**What it will not do.** It will not push to main from an agent process, skip the kernel's own test run, or work while the kill switch is set.

**Today → after.**

- **Today:** there is a Linux test image, the 860-case bench and the Benchmark Console. Actions are off on purpose, and the caretaker is "off (no safe credential)".
- **After:** watchable runs, replayable failures, and a kernel-only fast-forward. That last part depends on the owner confirming the "main" reading.

### 16. Explaining how it works, model by model and file by file

**What the owner says and what happens.**

- *"Which model answered that, and why that one?"* JARVIS names the provider or model and the lane, and explains the routing reason. Requirement M5 is "PARTIAL" today.
- *"Explain plugin/plugins/desktop/decide.py."* JARVIS reads the file's docstring, the plugin README and, for ported files, the provenance header. It then explains what the file does, where its code came from, and which tests cover it.

**What makes it work.**

- A Help screen ("How JARVIS works") holds a model and provider table generated from `llama-swap.yaml`, `models.json`, `providers.json` and `registry.json`, so it cannot drift from reality.
- It includes a "What can you do?" list with honest maturity labels, following HAX guidelines G1/G2: "Make clear what the system can do / how well" ([HAX](https://www.microsoft.com/en-us/research/publication/guidelines-for-human-ai-interaction/)).
- It includes an About → Licences page generated from the lock.
- 249 of 268 Python files already have module docstrings. The plan fills the rest and adds directory READMEs.

**What it will not do.** It will not explain from memory when the generated table or the docstring says otherwise.

**Today → after.**

- **Today:** strong docstrings, nine plugin READMEs, and no model explainer.
- **After:** generated explainers and per-file provenance.

### 17. A step-by-step guide, inside the app and in the docs

**What the owner says and what happens.** "Jarvis, how do I approve things from my phone?" opens the guide section and offers to start phone pairing.

**First-run onboarding** walks through these steps in order:

1. UI language and voice language, which are separate settings.
2. Mic test.
3. Push-to-talk chord.
4. Wake-word opt-in.
5. What the PIN protects.
6. Phone pairing.
7. Notification opt-in.
8. A demo task that needs one approval.

**What makes it work.** The docs side restructures `05_OPERATIONS.md`, today a 469-line dated log, into quick start, daily use, voice, approvals, stopping and recovery. This respects the "at most 5 files in docs/" rule.

**Today → after.**

- **Today:** a log with a buried quick start and stale WSL-lab sections.
- **After:** a guide the owner can follow end to end.

### 18. Coordinating with coding CLIs through one front door

**What the owner says and what happens.** *"Jarvis, start Claude Code on invoice-tool and have it fix the failing test."*

1. JARVIS launches `claude --bare`. Without `--bare`, `-p` runs repo hooks and `.mcp.json` servers "even in untrusted folders" ([Claude headless](https://code.claude.com/docs/en/headless)).
2. It sets `ANTHROPIC_BASE_URL` to its own front door, with a token minted for one lane.
3. It passes `--mcp-config` pointing at the JARVIS MCP server.
4. Every request, its cost and the session are audited.
5. JARVIS reports the diff. The diff then goes through the push gate.

**What makes it work.** A proposed `jarvis/kernel/jarvis_kernel/router/frontdoor.py` exposes four surfaces:

- `/v1/chat/completions` for OpenCode, Aider and goose;
- `/v1/responses` for Codex, which removed `wire_api="chat"` in February 2026 ([janhq #7413](https://github.com/janhq/jan/issues/7413));
- `/v1/messages`, `count_tokens` and `/v1/models` for Claude Code;
- Gemini `generateContent` for Gemini CLI.

For **Claude Code** the front door is a **byte-for-byte pass-through**, because Anthropic's gateway guide says to forward `anthropic-beta`/`anthropic-version` unchanged and not to buffer SSE. It also warns that a gateway that "rewrites or redacts request bodies … breaks the pairing". Anthropic "doesn't support routing Claude Code to non-Claude models through any gateway" ([Claude Code gateway protocol](https://code.claude.com/docs/en/llm-gateway-protocol); [LLM gateway](https://code.claude.com/docs/en/llm-gateway)). Local llama-server natively serves `/v1/messages` and `/v1/responses`, which is fine for experiments but unsupported for Claude Code.

**OpenCode** runs as the pinned binary driven over ACP or `opencode serve`. Most of its permissions default to allow, so JARVIS sets them explicitly ([OpenCode permissions](https://opencode.ai/docs/permissions/)).

**What it will not do.**

- **It will not redact or rewrite Claude Code bodies.** Instead, the lane is chosen explicitly: private-lane or local models only, for real repos.
- **It will not route subscription OAuth through any router.**
- **It will not use agy as an orchestrated worker.** agy:
  - is proprietary;
  - soft-denies shell commands and "exits 0" ([agy headless](https://antigravity.google/docs/cli/headless/));
  - never starts stdio MCP servers in print mode ([#1226](https://github.com/google-antigravity/antigravity-cli/issues/1226));
  - has no documented custom base URL.

  At most, JARVIS detects a user-installed agy and wraps it so that it fails closed.

**Today → after.**

- **Today:** OpenCode and goose lived in the lost lab.
- **After:** one front door with per-CLI lane tokens. ACP is the control plane, and a run fails if the MCP server did not connect.

### 19. Switching API providers by itself, on command, and inside the CLIs

**What the owner says and what happens.**

*Automatic.* Groq returns 429 three times. The circuit breaker opens for that provider and model, and the quota tracker reads `retry-after`. JARVIS says "Groq is rate-limited until 14:20; planning on OpenRouter-private meanwhile". It never moves private data to a public-lane provider while doing so.

*On command.* "Jarvis, use Groq for planning for the next hour" writes a time-boxed `lane_prefs` entry with an audit line, and the Models screen shows it. A proposed CLI verb, `jarvis provider use groq --lane private --for 1h`, does the same.

*Inside coding CLIs.* The CLIs keep one base URL and token. Switching happens server-side inside the lane's combo, so no CLI configuration changes.

**What makes it work.** The OmniRoute/9router features worth having are **ported into `jarvis_kernel/router/`**:

| Module | What it does |
|---|---|
| `combos.py` | Ordered per-lane combos. Strategies: priority, round-robin, least-recent-429, cheapest-first, fastest-p50 |
| `keypool.py` | Several keys per provider, only keys the owner legitimately holds |
| `quota.py` | Parses `x-ratelimit-*` and `retry-after` headers |
| `breaker.py` | Closed → open → half-open per provider and model |
| `budget.py` | Hard USD and token stops per key and per lane |
| `cache.py` | Exact-match cache per lane; optional semantic cache, never shared across lanes |
| `translate/` | Format translation built on the official SDK types |

any-llm (Apache-2.0, Python ≥3.11) can be imported for upstream calls ([any-llm](https://github.com/mozilla-ai/any-llm)). LiteLLM is reference-only, for the reasons in the carried-forward findings. The recommend-only `model_select.py` gets wired into `llm_proxy`, in shadow mode first.

**What it will not do.**

- It will not farm accounts.
- It will not port 9router's "Caveman"/"Ponytail" prompt injection or its token savers. Those silently alter text after redaction ([9router](https://github.com/decolua/9router)).
- It will not turn on a paid provider without the owner's approval. The repo rule is "paid must stay false".

**Today → after.**

- **Today:** `gateway.plan()` fallback, `lane_prefs`, a 60 s privacy-gateway cooldown and the lab-only `switch_model`. The selector is not wired, and OmniRoute "routes nothing".
- **After:** one Python router with breaker, quota, budget, an owner command and CLI combos. The OmniRoute service is retired.

### 20. Its own computer for browser research, like openmuse

**What the owner says and what happens.** *"Jarvis, on your computer, research which laptops with 8 GB of VRAM run Qwen3.5-9B well under ₹90,000, and let me watch."*

1. **The desktop is on.** On the Oracle VM, JARVIS's supervisor has already started Xvfb on display :1, an XFCE session and **Selkies** (MPL-2.0, `pip install selkies`).
2. **The owner watches.** Live view shows the desktop. "Watch" is view-only by default.
3. **The agent browses.** It uses a persistent research Chromium profile behind the egress proxy. Hermes `computer_use` (MCP over stdio to `cua-driver`) or Playwright drives the same display ([Hermes computer use](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/computer-use.md)).
4. **The owner can take over.** Clicking "Take control" takes a server-side lease that freezes the agent and switches the viewer to interactive. The owner types anything sensitive personally and then clicks "Hand back".
5. **The result is delivered.** The cited comparison table lands in Workspace.

**What makes it work.**

- **Selkies** streams over WebSockets by default and can be embedded in "any HTML5 web interface" ([Selkies](https://github.com/selkies-project/selkies)).
- **If Selkies's arm64 build fails** (unverified), the fallback is TigerVNC or x11vnc plus noVNC's `RFB` class, imported directly into the React Live view. `viewOnly` is a runtime property, so watch versus take-control is a single toggle instead of today's fragile DOM poke into an iframe ([noVNC API](https://novnc.com/noVNC/docs/API.html); [04-agentic-os-and-ui.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/research/04-agentic-os-and-ui.md)).
- The desktop is reachable on Tailscale only, with Caddy as the single authenticated front door.

**What it will not do.**

- **It will not run linuxserver/webtop.** Webtop is GPL-3.0, has no authentication by default, and its web terminal has passwordless sudo ([docker-webtop](https://github.com/linuxserver/docker-webtop)).
- It will not expose the desktop to the internet.
- It will not hold the owner's banking sessions.

**Today → after.**

- **Today:** the agent computer was the WSL box on `D:\jarvis-lab\ubuntu\ext4.vhdx`, which is gone.
- **After:** a VM desktop managed by JARVIS. Its size depends on the Oracle decision (see the carried-forward findings).

### 21. Everything openmuse does, rebuilt inside JARVIS

**What the owner says and what happens.**

*Watch a page.* "Watch the Samsung T7 2TB price on this page and tell me when it drops below ₹12,000."

1. JARVIS creates a durable watch.
2. If a check fails, the next one waits `min(60, 2^failures)` minutes. After five failures the watch pauses with a notice.
3. Each check stores a diff showing what changed.
4. When the condition hits, a push arrives with the diff.

*Fill a PDF form.* "Fill this scholarship PDF form with my details."

1. JARVIS lists the form's fields.
2. It asks for any missing values through a typed input card.
3. It shows a filled-copy preview.
4. It writes the final file only after the owner approves, and keeps a receipt in the audit log.

*Email.* "Draft replies to this week's unanswered emails." JARVIS saves Gmail drafts only. Sending needs a RED approval.

*Spending.* "What did I spend on food last month?" works from a CSV export using openmuse's rules: positive amounts are expenses, negative amounts are income, the file cap is 500 KB, and currencies are never converted.

*Durability.* Every task survives a restart and can be paused, resumed, cancelled or retried.

**What makes it work.** A full port into a proposed `plugin/plugins/tasks/`:

- **The task engine.** openmuse's durable worker becomes DBOS workflows. openmuse's states are queued, scheduled, running (with a 60 s lease renewed every third of the lease) and `waiting_approval` ([worker.ts](https://github.com/CopilotKit/openmuse/blob/main/apps/server/src/engine/worker.ts)). In DBOS, `waiting_approval` becomes `DBOS.recv("approval", timeout)`, answered by the kernel's PIN flow through `DBOS.send` ([DBOS reference](https://docs.dbos.dev/python/reference/contexts)).
- **Retry safety.** openmuse refuses to retry when an "outcome may be uncertain". In JARVIS this becomes the existing `idempotency.py` keys.
- **Page watches.** Backoff and diff are ported from `service.ts` and `page-diff.ts` ([service.ts](https://github.com/CopilotKit/openmuse/blob/main/apps/server/src/engine/service.ts)). The port should avoid openmuse's open bug #184: a "contains" watch with a trailing space never matches.
- **PDF forms.** pypdf 6.x (already required) or PyPDFForm (MIT). PyMuPDF is avoided because it is "AGPL 3.0 or Artifex commercial" ([PyMuPDF](https://pypi.org/project/PyMuPDF/)).
- **Gmail and Calendar.** google-api-python-client with reviewed writes ([PyPI](https://pypi.org/project/google-api-python-client/)).
- **The rest.** The finance CSV rules are ported verbatim. "Ideas with evidence" goes onto the vault. Browser profiles plus takeover come from item 20. The sandboxed "computer" is a no-network podman container with openmuse's 30 s and 128 KB limits ([COMPUTER.md](https://github.com/CopilotKit/openmuse/blob/main/docs/COMPUTER.md)).

**What it will not do.**

- It will not run openmuse's Node, PGlite or Expo stack.
- It will not need a **CopilotKit Intelligence** key. openmuse's API "fails at startup" without one and keeps threads in CopilotKit's cloud ([RICH-THREADS.md](https://github.com/CopilotKit/openmuse/blob/main/docs/RICH-THREADS.md)). The CopilotKit coupling is confined to 8 chat-layer files that JARVIS replaces with main_agent.
- It will not send PostHog telemetry or Parallel search queries ([TELEMETRY.md](https://github.com/CopilotKit/openmuse/blob/main/docs/TELEMETRY.md)).

**Today → after.**

- **Today:** openmuse lived in the lost WSL box and logged "CopilotKit Intelligence 503s" when it ran ([03_DECISIONS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/03_DECISIONS.md)).
- **After:** every openmuse feature in its FEATURES.md inventory, local, durable and behind the kernel, with OCR and scanned forms still remaining, as they do upstream ([FEATURES.md](https://github.com/CopilotKit/openmuse/blob/main/docs/FEATURES.md)).

### All 21 at a glance

| # | Capability | Today | After | Phase |
|---|---|---|---|---|
| 1 | Admin rights | Fixed job kinds; UAC only in repair | Broker + ADMIN tier + UIA hands + API-first registry | P4 |
| 2 | Any web page | Two lanes; Node relay; VM-hosted proxy | Python relay + Python egress proxy + taint | P4 |
| 3 | Human-like, explanatory | Question card; timeline PARTIAL | Event-true timeline, Why?, clarify-don't-guess | P8 |
| 4 | Widget + tray | Web widget; tray PLAN ONLY | Tauri subtree with tray, PTT, single-instance | P8 |
| 5 | Find files, learn skills | Six-folder name match; no create | FileOps + undo; SKILL.md loop + curator | P4–P5 |
| 6 | Research → plan → code | Pieces exist; no lab host | Chained flow; in-tree coding agent on VM | P6 |
| 7 | Multi-agent tests | No suite | Read-only tests, reviewer, `o_multiagent` | P6 |
| 8 | OpenClaw/Grok parity | Safety ahead, reach behind | Phone, brief, chief of staff, recorder, channels | P9 |
| 9 | ChatGPT/Gemini | Disabled entries | APIs out (labelled), MCP in | P9 |
| 10–11 | API planner + local agents | Q&A only (`local_team`) | Plan-then-execute for all PC work | P3 |
| 12 | Voice | Two processes; 67% wake | One engine + AEC + Smart Turn + speaker check | P8 |
| 13 | 10–20 local models | 7 models, disk-tight | ~15 models, per-build binaries, pruning | P3 |
| 14 | Personality | None | Card + traits + anti-sycophancy cases | P8 |
| 15 | On-screen tests, fix, push | Bench; no host; no push | Watchable runs + kernel-only main | P6 |
| 16 | Explain itself | Docstrings | Generated explainers + provenance | P10 |
| 17 | Usage guide | Dated log | Guide + onboarding | P10 |
| 18 | Coding CLIs | Lost with lab | Front door + ACP + pass-through | P3, P6 |
| 19 | Provider switching | Fallback; selector unwired | Ported router + owner/CLI commands | P3 |
| 20 | Own VM for research | Lost | Selkies desktop on Oracle, watch/take over | P2 |
| 21 | openmuse features | Lost; cloud-dependent | Full native port on DBOS | P7 |

## One repo, one core, one supervisor: the integrated architecture

"Integrated" here has a concrete meaning. Every component, whatever its language, is started by JARVIS's supervisor. It gets its configuration rendered from JARVIS's settings tree, its secrets injected from JARVIS's secrets store, and its access controlled by JARVIS's token. Its every effect is logged in JARVIS's audit, and the owner sees it only through JARVIS's UI.

N.E.K.O.-Tauri already contains the seed of this. It maintains only "window, mouse click-through, platform adaptation, native screen capture, and **starting/stopping the backend process**" ([N.E.K.O.-Tauri README](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri/blob/main/README.md)). The proposal makes Tauri launch exactly one thing, the JARVIS supervisor, and the supervisor launch everything else from one manifest.

### Proposed repository layout

New paths are marked (P). Everything else exists today on `remediation-2026-10-18`. The licensing notes suggested a top-level `third_party/` and a top-level `desktop/`, while the shell notes suggested `jarvis/desktop/shell/`. The layout below keeps everything under `jarvis/`, because `jarvis/third_party/PATCHES.md` already lives there.

```
jarvis-hq/
├── pyproject.toml, uv.lock          one uv workspace, requires-python ==3.11.* (N.E.K.O + JARVIS)
├── app/ main_logic/ main_routers/ memory/ brain/ config/ templates/ static/ frontend/
│                                    N.E.K.O platform layer, tracked against upstream (escalation rules apply)
├── plugin/plugins/
│   ├── main_agent/ desktop/ research/ local_team/ workbench/ learn_vault/ doc_tools/ deliverables/ jarvis_bridge/   (exist)
│   ├── coding/              (P) ACP client, OpenCode adapter, mini_swe/ (vendored MIT, modified), codex/claude adapters
│   ├── tasks/               (P) openmuse port: engine.py (DBOS), watches.py, page_diff.py, pdf_forms.py, google.py, finance.py, ideas.py
│   ├── routines/            (P) recipes (goose idea), recorder.py (Playwright trace → SKILL.md)
│   └── channels/            (P) push.py (Web Push/ntfy/Apprise), pairing.py, brief.py
├── jarvis/
│   ├── kernel/jarvis_kernel/  policy.py gateway.py llm_proxy.py agents.py idempotency.py model_select.py ...
│   │   └── router/          (P) combos keypool quota breaker budget cache translate/ frontdoor.py
│   ├── supervisor/          (P) manifest.py units/ health.py jobobject (Windows) systemd render (Linux) render_config/
│   ├── egress/proxy.py      (P) Smokescreen-equivalent asyncio CONNECT proxy
│   ├── voice/               (P) engine.py turn.py aec.py tts_stream.py wake/   (replaces jarvis/wake + local_speech_server)
│   ├── node/jarvis_node/    browser.py browserext.py pcopen.py repair.py winbackend.py ...
│   │                        (P) cdp_relay.py filesearch.py fileops.py adminctl.py apis/ vendor/windows_mcp/
│   ├── local_models/        llama-swap.yaml profiles/  (P) bin/<build>-<os>-<backend>/ binaries.json swapper.py(optional)
│   ├── hermes/              check_hermes_compat.py plugins/jarvis_memory  (P) plugins/jarvis_policy plugins/jarvis_audit config_render.py
│   ├── vm/                  (P) desktop.py (Xvfb + XFCE + Selkies), unit templates
│   ├── desktop/shell/       (P) git subtree of N.E.K.O.-Tauri; src-tauri/src/jarvis/{tray,ptt,jobobject,firstrun}.rs
│   ├── ui/                  React 19 SPA: all screens, #/widget route, PWA manifest + service worker (P)
│   ├── console/ privacy/ skills/ caretaker/ lab/ evals/ config/ deploy/ docs/ scripts/   (exist)
│   ├── tools/               registry.json (P: Chromium-style fields) jarvis.lock.json fetch.py (P: profiles, safe unpack) licence_gate.py (P)
│   └── third_party/         PATCHES.md, (P) hermes-agent/ (pristine subtree, never edited), UPSTREAM records
├── THIRD_PARTY_NOTICES      (P, generated)   licenses/ (P, generated)
└── (retired) jarvis/hub/index.html, jarvis/widget/*.html, jarvis/deploy/jarvis-omniroute.service, .gitmodules
```

### The "one of each" contract

| Single thing | Proposed form | Evidence and constraint |
|---|---|---|
| **One Python core** | N.E.K.O + kernel + plugins in one uv workspace on 3.11. uv enforces one `requires-python`, the intersection of all members ([uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/)) | N.E.K.O is `==3.11.*` with "Python 3.11 only" in CONTRIBUTING. Hermes (3.14) and Windows-MCP (≥3.14) cannot be workspace members |
| **One supervisor** | `jarvis/supervisor/` reads one manifest (`cmd`, `args`, `env`, `port`, `health_url`, `restart_policy`, `resources`, `os`). It checks health, restarts with backoff, captures logs, and asserts every bind is 127.0.0.1 or Tailscale only. On Windows it runs children in a Job Object; on the VM it renders systemd units | Tauri "spawns sidecars; it does **not** supervise them" ([community guide](https://claudskills.com/skills/tauri-sidecar-lifecycle/SKILL.md)) |
| **One settings tree** | `jarvis/config/*.json` with a schema, plus sections for models, voice, browser, router, egress, agents and services. Children never read it. The supervisor renders their native configs (llama-swap YAML, Caddyfile, egress ACL, OpenCode `OPENCODE_CONFIG_CONTENT`, Hermes `config.yaml`) into `%LOCALAPPDATA%\JARVIS\run\` or `/run/jarvis/` on every start | Two provider registries exist today, N.E.K.O's `config/api_providers.json` and JARVIS's `jarvis/config/providers.json`. They must be reconciled, which is an open design question |
| **One secrets store** | The existing `D:\jarvis-secrets` file references, read only by the supervisor and kernel and injected as env or stdin. On the VM, a root-owned 0600 file loaded with systemd `LoadCredential=` (from background knowledge, unverified). `keyring` (Windows Credential Locker) is an optional owner decision ([keyring](https://pypi.org/project/keyring/)) | The repo rule is files in `D:\jarvis-secrets`, and S1 says "OS credential store not used". The licensing note's keyring proposal conflicts with that rule, so the files stay the default |
| **One auth token** | The supervisor mints a random per-boot token. Tauri hands it to the UI, and each child gets it as its API key (llama-swap `apiKeys`, router key, `OPENCODE_SERVER_PASSWORD`, Hermes `API_SERVER_KEY`). The browser holds only the console's HttpOnly SameSite=Strict cookie plus a double-submit token. Approvals still need the PIN | GeoLibre uses the same per-launch-token pattern ([GeoLibre](https://instagit.com/opengeos/GeoLibre/how-does-the-fastapi-python-sidecar-communicate-with-the-frontend.md)). The console already uses a one-time launch code and an exact-Host check ([server.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/console/jarvis_console/server.py)) |
| **One audit log** | The kernel's append-only, hash-chained JSONL. Each record holds who, what, tier, approval, idempotency key, child, and the component's registry version and sha256. Hermes reports through a `post_tool_call` plugin, OpenCode through ACP `session/update`, and DBOS step results become receipts | Audit without secrets (S3) is TESTED today |
| **One UI** | `jarvis/ui` (React 19, Vite 8, Tailwind 4) in four containers: Tauri main window, Tauri widget window, tray menu, and phone PWA over Tailscale Serve | 11 screens, SSE feed, Stop all/Kill and axe e2e already exist ([App.tsx](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/ui/src/App.tsx)) |
| **One Windows installer** | Tauri NSIS (per-user, no admin), signed through `signCommand`, mandatory-signature updater, WebView2 `embedBootstrapper`. Models are fetched on first run against `jarvis.lock.json` sha256 ([Tauri updater](https://v2.tauri.app/plugin/updater/); [Windows installer](https://v2.tauri.app/distribute/windows-installer/)) | Packaging the Python side is an open choice: a uv-managed runtime or Nuitka (see below) |
| **One VM deployment** | An aarch64 bundle: `uv sync --frozen`, `linux-aarch64` binaries from the same lock, and `jarvis-supervisor.service` plus generated child units with `MemoryMax` hardening. Caddy is the TLS/auth front door on Tailscale | Replaces the hand-written `jarvis-omniroute.service` style |

**Packaging the Python backend.** The two notes disagree, and both options are defensible.

- **Nuitka build.** N.E.K.O's CI already builds the backend with Nuitka into a single `projectneko_server.exe`, including the riskiest native piece, the patched `sherpa-onnx 1.13.8+neko.kws2` wheel ([build-desktop.yml](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/.github/workflows/build-desktop.yml)).
- **uv-managed runtime.** The shell notes favour the shell bundling a pinned `uv.exe`, which runs `uv python install 3.11` plus `uv sync --frozen` on first run. That path has no self-extracting bootloader to trigger antivirus heuristics, avoids the NSIS stale-sidecar bug ([tauri #15134](https://github.com/tauri-apps/tauri/issues/15134)), and lets Python update by `git pull` independently of the shell.

The recommendation is the **uv runtime for the owner's own laptop**, with the Nuitka recipe kept as the "shareable installer" profile. Either way, `uv` itself must be pinned in the lock, which today pins no uv version.

### Per-component integration table

Forms are written in the owner's terms:

- **library**: imported into the 3.11 core;
- **vendored**: source in-tree that JARVIS edits;
- **pristine subtree**: source in-tree that JARVIS never edits;
- **ported**: features re-implemented in JARVIS's Python, with provenance headers;
- **bundled binary**: a pinned, hash-checked executable under the supervisor;
- **separate process**: a GPL program that must stay separate;
- **excluded**.

| Component | Form | What JARVIS takes | Licence obligations | Upstream update path |
|---|---|---|---|---|
| N.E.K.O | Base of the repo | Everything: companion, memory, plugins, characters, voice hooks | Apache-2.0: LICENSE, mark modified files (§4b) ([Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0)) | `neko-upstream` remote, periodic merge with regression report |
| N.E.K.O.-Tauri | Vendored subtree `jarvis/desktop/shell/` | Shell, click-through, capture, backend launcher; plus tray/PTT/single-instance/autostart/Job Object | Apache-2.0 + **NOTICE** ("Project N.E.K.O. …; Copyright (c) 2025 Hongzhi Wen") ([NOTICE](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri/blob/main/NOTICE)) | `git subtree pull --squash`; offer patches upstream with `git subtree split` ([git-subtree](https://github.com/git/git/blob/master/contrib/subtree/git-subtree.adoc)) |
| Hermes Agent | Pristine subtree `jarvis/third_party/hermes-agent` at tag v0.22.2 + own 3.14 venv + supervised gateway/API/cron child. JARVIS code only as Hermes plugins. **Plus ports**: SKILL.md staging, skills_guard, curator, FTS5 search, dangerous-command patterns, pairing | Channels, computer_use, delegation, cron for VM jobs | MIT notice | Subtree bump to a tag, then `check_hermes_compat.py` and plugin tests. Skip v0.22.1 ("built from the wrong commit"); v0.22.3/0.22.4 is a 4,206-file roll-up ([HERMES_0_22_UPGRADE](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/reports/HERMES_0_22_UPGRADE_2026-10-22.md)) |
| OpenCode | Bundled binary v2.1.2 (Bun-compiled; 0 Go files left) driven over ACP/HTTP from `plugin/plugins/coding/` | Agent sessions, worktree/PTY endpoints, kernel-answered permission requests | MIT | Bump pin + checksum, re-run ACP contract test ([CHANGELOG](https://github.com/anomalyco/opencode/blob/dev/CHANGELOG.md)) |
| mini-swe-agent | Vendored and modified in `coding/mini_swe/` | ~100-line agent loop, environment abstraction (podman) | MIT | Provenance-commit diff review |
| goose | **Runtime dropped**; recipe concept ported | Parameterised YAML recipes with sub-recipes; observability-hook event model | Apache-2.0 (concept only) | None |
| Codex CLI | Optional bundled binary (Rust) as a sandboxed reviewer via `/v1/responses` | `codex exec`, `required = true` MCP servers abort on failure | Apache-2.0 + **NOTICE** incl. Ratatui MIT credit ([Codex NOTICE](https://github.com/openai/codex/blob/main/NOTICE)) | Pin bump |
| Claude Code | External, user-installed; launched `--bare` against the front door, pass-through | Coding sessions under audit/budget | Proprietary client; Anthropic terms | None |
| Antigravity CLI (agy) | **Excluded**; optional detector/wrapper of a user install, fail-closed | Nothing in-tree | "Proprietary"; no licence file | None |
| OpenHands SDK | Optional child in own venv (needs Python ≥3.12) | agent-server if ever wanted | MIT | Pin |
| openmuse | **Ported** into `plugin/plugins/tasks/` | Durable tasks, watches + backoff + diff, PDF fill, Gmail/Calendar reviewed writes, finance CSV, ideas, profiles + takeover | MIT: keep notice; header `# Ported from CopilotKit/openmuse@1ac68f39:<path> (MIT)` | Monthly upstream diff review by provenance commit |
| CopilotKit Intelligence / runtime | **Excluded** (cloud service, outside MIT). AG-UI event *names* reused as vocabulary | Nothing | Service ToS | None |
| OmniRoute / 9router | **Ported features**; both services retired | Combos, key pool, quota, breaker, budgets, cache, translation | MIT (copy headers only if code is copied) | Concept; no runtime to update |
| LiteLLM | **Excluded** from the kernel; read-only reference (≤1.106.2) | Edge cases for translation | MIT core (enterprise/ split unverified) | None |
| any-llm | Library (optional) for upstream calls | `completion`/`responses` over official SDKs | Apache-2.0 | Locked pin |
| llama.cpp `llama-server` | Bundled binaries per build×backend, incl. ≤b10950 for GDN models | Inference for every local LLM | MIT + bundle its `licenses/` dir | Lock bump with sha256; `llama-bench` per model |
| llama-swap | Bundled binary first (generated YAML, under supervisor); optional Python `swapper.py` port later | Groups/exclusive/persistent, TTL, preload | MIT | Pin bump (v265, 14 Oct) ([releases](https://github.com/mostlygeek/llama-swap/releases)) |
| llama-cpp-python | **Not used** | — | MIT | — |
| sherpa-onnx | Library (1.13.8); custom KWS wheel either upstreamed or built reproducibly with hash in lock | KWS, VAD, ASR, TTS, speaker ID, language ID | Apache-2.0; **wheel links espeak-ng (GPL-3.0)** | Locked pin + voice bench |
| Voice models (Parakeet, IndicConformer, Kokoro, Piper te_IN-venkatesh, Pocket TTS, Silero) | First-run download, lock-pinned | Weights | Parakeet CC-BY-4.0 (attribution); Pocket TTS CC-BY-4.0, gated; Piper Telugu dataset CC-BY-4.0; Kokoro Apache-2.0; Silero MIT | Lock bump |
| Smart Turn v3 | Ported inference glue (~80 lines) + ONNX weights | Endpointing model | BSD-2 | Pin exact ONNX file + sha256 |
| WebRTC AEC (pywebrtc-audio) | Library (0.2.0); aec-audio-processing as fallback | AEC3, NS, AGC2 | Apache-2.0 / BSD-3 + WebRTC BSD notice | Locked pin |
| openWakeWord pretrained / livekit-wakeword | openWakeWord models **excluded** from shared builds; livekit-wakeword library for training an own model | Own `hey_jarvis` ONNX | NC-SA (openWakeWord models); Apache-2.0 (livekit code) | — |
| piper1-gpl / piper-tts | **Excluded**; Piper voices run through sherpa-onnx VITS | — | GPL-3.0-or-later | — |
| Playwright (Python) | Library | Sandbox lane, persistent profiles, traces | Apache-2.0 + **NOTICE** | Locked pin; browsers hash-pinned |
| Playwright MCP | **Ported** CDP relay (`cdp_relay.py`); Microsoft's bridge extension kept and pinned | `/extension` + `/cdp` relay | Apache-2.0: NOTICE, mark ported files | Contract test against pinned extension |
| browser-use | Library in research lane (or separate venv) | Agent loop over CDP | MIT; telemetry off | Locked pin |
| workflow-use | **Excluded** (AGPL-3.0, alpha) | Design reference only | AGPL | — |
| Windows-MCP | Vendored subset (UIA tree/snapshot, input helpers), backported to 3.11; fastmcp/posthog/PowerShell/Registry/Process stripped | Snapshot with element IDs | MIT notice + `VENDORED.md` with commit | Provenance diff |
| uiautomation / pywinauto / dxcam | Libraries | UIA access, app helpers, fast capture | Apache-2.0 / BSD-3 | Locked pins (pywinauto 1.0.0b1 is a beta) |
| UFO² | Ported pattern (API before GUI) | `apis/` registry | MIT | Concept |
| cua | MIT driver/SDK parts only, as library if needed (Hermes's `cua-driver`); FSL Spaces and AGPL `cua-som`/perception model **excluded** | computer_use driver | MIT; FSL-1.1-MIT; AGPL ([cua licensing](https://github.com/trycua/cua)) | Pin |
| Everything / ES / Windows Search | ctypes wrapper over Everything64.dll; `es.exe` bundled binary; Windows Search via pywin32 ADODB | Instant name search, full-text fallback | ES MIT; voidtools License.txt is MIT-style ([License.txt](https://www.voidtools.com/License.txt)). The earlier note said "freeware"; check the current 1.4/1.5 installer EULA | Pin es.exe; Everything client installed with owner opt-in |
| Smokescreen | **Ported** into `jarvis/egress/proxy.py` (optionally on proxy.py, BSD-3); binary kept as fallback | DNS-resolve-then-check, ACL roles, rate limits | MIT | Concept + fallback pin |
| Selkies | Library/app in VM venv under supervisor; HTML5 client embedded via JARVIS reverse proxy | Agent desktop streaming | MPL-2.0: file-level; ship MPL files unmodified or publish changes ([MPL FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/)) | Locked pin; arm64 wheels unverified |
| noVNC | Vendored client (`core/rfb.js`) imported in React Live view (fallback path) | `RFB` with `viewOnly` toggle | MPL-2.0 core; BSD HTML/CSS | Pin npm 1.7.0 |
| KasmVNC / webtop | **Separate process only** (KasmVNC GPL-2.0, run unmodified as its own binary/container) or excluded (webtop GPL-3.0, no auth by default) | — | GPL source offer if distributed | Not in product |
| DBOS | Library (3.3.0, already pinned) | Durable workflows, queues, schedules, recv/send approvals | MIT | Locked pin; confirm SQLite backend behaviour |
| Presidio / spaCy / GLiNER-PII | Libraries + model download | Redaction in privacy gateway | MIT + NOTICE / Apache-2.0 | Locked pins |
| MiniCheck | Library + MIT weights (RoBERTa-L / Flan-T5); Bespoke-7B **excluded** | Claim verification | Repo is **Apache-2.0** (not MIT); Bespoke-7B CC BY-NC | Convert RoBERTa-L to ONNX int8 |
| Document tools (pdfplumber, pypdf, PyPDFForm, python-pptx, OfficeCLI) | Libraries; OfficeCLI as bundled CLI | Read, fill, generate documents | MIT / BSD-3 / Apache-2.0 + OfficeCLI **NOTICE** | Locked pins |
| PyMuPDF | **Excluded** | — | AGPL-3.0 or commercial | — |
| LibreOffice | Detect, or download on demand; run `soffice --headless` as a separate process; never vendored | docx/pptx conversion for safe preview | MPL-2.0 unmodified: point to source | Lock entry (0.37 GB MSI; delete after install) |
| Caddy | Bundled binary on VM (and the laptop hub path until retired) | TLS/auth front door, server-side token injection | Apache-2.0 | Pin |
| gitleaks | Bundled binary (both OSes) | Secret scan in push gate and caretaker | MIT | Pin |
| Verdict (OpenJev 151M) | Supervised child (already native on 127.0.0.1:8091) | Decision classifier | Licence unresolved (no HF repo found) | Pin weights |
| ntfy / Apprise / Web Push | Plain httpx POSTs to self-hosted ntfy; Apprise library; Web Push from the PWA | Notifications | ntfy Apache-2.0/GPLv2 dual (server separate); Apprise BSD-2; avoid AGPL `ntfy-client` | Pins |
| python-telegram-bot / aiogram | Via Hermes gateway child; or aiogram (MIT) in a pure-JARVIS `channels` plugin | Telegram | PTB LGPL-3.0 (fine unmodified) | Pins |
| Google APIs | google-api-python-client + google-auth-oauthlib | Gmail/Calendar drafts and reviewed writes | Apache-2.0 | Pins |
| Gemma / Qwen / gpt-oss weights | First-run download from lock | Local LLMs | Qwen/gpt-oss Apache-2.0. **Gemma conflict:** HF metadata and news say Gemma 4 moved to Apache-2.0 ([implicator](https://www.implicator.ai/google-releases-gemma-4-under-apache-2-0-dropping-its-custom-ai-license/)), but the licensing note applies the Gemma Terms (§3.1 NOTICE and use restrictions) ([Gemma terms](https://ai.google.dev/gemma/terms)). Treat older Gemma under the Terms and verify the Gemma 4 licence page before shipping | Lock bump |

### Where "integrate completely" cannot be met, and the closest compliant form

**Hermes Agent and the Python version.** The two research notes disagree, and the disagreement decides the design.

- **The runtimes note** read Hermes's own docs: "Hermes runs only on **Python 3.14**". Its `pyproject.toml` says `>=3.11,<3.15`, but "every runtime dependency carries a `python_version >= '3.14'` marker". On 3.11–3.13 it therefore "installs `hermes-agent` with **no dependencies** and the package does not import" ([Hermes updating.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/getting-started/updating.md)). It also "does not publish a supported wheel" ([python-library.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/guides/python-library.md)). The repo's own upgrade study says JARVIS runs Hermes in "our 3.14 environment".
- **The licensing note** saw only the `>=3.11,<3.15` range and proposed Hermes as a uv dependency in the 3.11 environment. It flagged that this was "not tested".

The docs statement and the repo's own practice outweigh the bare version range. The plan therefore treats Hermes as a **pristine vendored subtree in its own 3.14 venv, supervised by JARVIS**. All JARVIS logic lives in Hermes plugins:

- `jarvis_memory`, which exists today;
- a new `jarvis_policy` `pre_tool_call` hook that returns block, approve or allow from the kernel. Hermes documents that a hook which times out or raises **fails closed** ([Hermes hooks](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/hooks.md));
- an audit hook.

Verification is a P1 task with three checks:

1. `grep python_version` on the pinned tag's `pyproject.toml`.
2. In a scratch 3.11 venv, install the tag and run `python -I -c "import run_agent"`.
3. Attempt `uv lock` with Hermes as a workspace member and inspect which dependencies were skipped by markers.

If all three pass, Hermes can become a library. If any fails, the subtree-plus-child design stands. Two more cautions apply. A ~1M-line subtree may bloat the clone, so a filtered mirror without `website/` and `tests/` may be needed. And every edit inside the subtree is forbidden, because upstream churn is extreme.

**GPL components.**

- KasmVNC (GPL-2.0) cannot be combined with Apache-2.0 N.E.K.O code at all. The FSF "has never considered the Apache License to be compatible with GPL version 2" ([Apache](https://www.apache.org/licenses/GPL-compatibility.html)).
- webtop and piper1-gpl are GPL-3.0. Importing a GPL Python package in-process counts as linking ([GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#InterpreterIncompat)).

The closest compliant forms are Selkies (MPL) and noVNC for the desktop, and Piper voices through sherpa-onnx for TTS. KasmVNC can run only as an unmodified separate program on the VM, which the GPL FAQ normally treats as an aggregate ([GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#MereAggregation)). That is interpretive and needs legal confirmation before any distribution. One residual GPL piece exists even in the compliant path: the sherpa-onnx wheel links espeak-ng. This is irrelevant while JARVIS stays private, but it needs source-offer compliance or lexicon front-ends if JARVIS is ever shared.

**Claude Code traffic.** JARVIS can own launch, token, lane, budget and audit, but it must not own the bytes. Anthropic's guide requires inspect-without-modify and unbuffered SSE ([gateway protocol](https://code.claude.com/docs/en/llm-gateway-protocol)). The privacy gateway's Presidio redaction therefore cannot apply to Claude Code. The compliant substitute is lane choice: private-lane or local upstreams only, for private repos.

**agy and CopilotKit cloud.** Both are proprietary services. JARVIS can only detect and wrap a user-installed agy, and it replaces CopilotKit Intelligence with main_agent and local thread storage.

**Non-commercial models.** These are openWakeWord's pretrained models and the ACAV100M features, Bespoke-MiniCheck-7B, Holo2, Piper `hi_IN-pratham`/`priyamvada`, and the GigaSpeech-trained English KWS model. They are legal for this personal assistant but block any shared build. The lock should mark them `licence_class: non-commercial`, and the model manager should require explicit opt-in. Piper `hi_IN-rohan` (IITM IndicTTS licence, terms unread) should be replaced by Kokoro's Hindi voices ([piper-voices](https://huggingface.co/rhasspy/piper-voices)).

**Windows-MCP.** Its Python ≥3.14 requirement makes a direct import impossible, so the closest form is the vendored, backported subset described above.

### One UI: port JARVIS and N.E.K.O surfaces into the React app, keep sandboxed frames only where they are a security boundary

The UI research recommended porting JARVIS-owned surfaces and keeping N.E.K.O upstream surfaces as same-origin iframes. Its reason was that porting them "would fork N.E.K.O and trigger its PR rules (zh/en/ja, screenshots, regression reports)" ([unified UI research → 04-agentic-os-and-ui.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/research/04-agentic-os-and-ui.md)). Given the owner's "integrate completely" instruction, this report recommends going further, accepting a defined maintenance cost.

**Port natively into `jarvis/ui`:**

- the hub page, the widget HTML and the Benchmark Console;
- the approvals view;
- the **react-neko-chat** window, upgrading its components from React 18 to 19;
- the JARVIS-relevant N.E.K.O templates: memory browser, API-key settings, model manager, character card manager, voice clone and voice identity. There are 34 template files, and most of the rest are games and avatar editors;
- the Vue plugin manager's admin pages, rewritten in React.

**Replace third-party dashboards** with native screens over their APIs: Hermes runs through a thin BFF, noVNC through `RFB`, router health through the kernel. Dashy is already gone.

**Keep two exceptions:**

1. **The Live2D/VRM avatar renderer** stays as an embedded same-origin component. It is a rendering library, not a page worth porting.
2. **Plugin-hosted TSX panels** keep their sandboxed frame. Today `tsxRuntime.ts` compiles plugin TSX with sucrase and renders it in a frame "that does not allow native form submission" ([main_agent panel](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/main_agent/ui/panel.tsx)). That frame isolates untrusted plugin UI, so it is a security boundary rather than a fragmentation problem. The JARVIS shell hosts the same runtime.

**Avoid Module Federation and single-spa.** React 18, React 19 and Vue 3 coexist in the repo, and practitioner guidance calls micro-frontends "a poor fit for small teams" ([techinterview.org](https://www.techinterview.org/post/3233475066/module-federation-micro-frontends/)).

**The tradeoff is real.** Ported N.E.K.O screens will drift from upstream. Each ported file needs a provenance header so the caretaker can surface upstream changes. Any change that lands back in N.E.K.O platform code must still meet its zh/en/ja i18n and screenshot rules. i18n unifies on i18next, which N.E.K.O already ships, adding hi/te catalogues for JARVIS surfaces.

**Screen list.** The resulting app has these screens:

| Screen | Content |
|---|---|
| Home | Status and command bar |
| Chat | Conversation |
| Approval Inbox | Pending approvals |
| Activity timeline | Run-by-run event log |
| Live view | Agent PC, Browser, My PC and Lab tabs, with pop-out and replay |
| Agents | Agent definitions and runs |
| Automations | Schedules, plus the recorder |
| Workspace | Delivered files |
| Files & Undo | Search results and the undo journal |
| Memory | What JARVIS remembers |
| Models & skills | Model roster and skills |
| Connectors | Connected services |
| Settings | Twelve sub-pages, each showing which file it writes |
| Bench/Health | Benchmark and health checks |
| Character | Persona card |
| Plugins | Plugin admin |
| Help | "How JARVIS works" |
| Onboarding | First-run setup |
| Devices | Paired phones and other devices |
| `#/widget` | The widget route |

**Phone access.** The same build serves the phone as a PWA over **Tailscale Serve**, which gives the HTTPS secure context that microphone and Web Push need ([Tailscale Serve](https://tailscale.com/kb/1312/serve)). Phone approvals work like this:

- A push carries no secrets: "JARVIS needs approval (RED): send 3 emails", with a deep link.
- Web Push payloads are end-to-end encrypted ([RFC 8291](https://datatracker.ietf.org/doc/html/rfc8291)).
- ntfy is the Android-friendly fallback, using its `view` action. Its `http` approve buttons are not used, because ntfy tokens "grant users **full access to the user account**" ([ntfy config](https://docs.ntfy.sh/config/)).
- The PIN is always entered inside the app.

## Ten findings from the earlier research still outrank every new feature

The integration plan changes how some of these risks get fixed, because porting replaces the vulnerable runtimes. It does not change their order: each one either blocks the integration or exposes the owner's keys today.

| # | Finding | What integration changes | First action (P0) |
|---|---|---|---|
| 1 | Oracle A1 Always Free is now 1,500 OCPU-h + 9,000 GB-h, "equivalent to 2 OCPUs and 12 GB" ([Oracle](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)). Keeping 4/24 on PAYG is estimated at ~$25–28/month ([TerminalBytes](https://terminalbytes.com/oracle-cloud-free-tier-changes-2026/)). The VM hosts the kernel, egress proxy, Dahl lanes, OmniRoute and Hermes | Porting openmuse and OmniRoute removes ~1.7–3 GB of estimated resident RAM. Selkies + desktop ~0.6–1 GB, Chromium 1–2 GB, Hermes 0.5–1 GB and an on-demand VM model then fit 12 GB more plausibly. Estimates, not measurements | Owner checks account type and A1 usage across **both** instances (never touch hermes-agent-vm-2), then decides resize or pay |
| 2 | 9router: default password `123456`, public secrets, `REQUIRE_API_KEY=false`, and **CVE-2026-108593**: `POST /api/cli-tools/hermes-settings` injects keys into Hermes's `config.yaml`, with a `baseUrl` shell hook, in ≥0.4.1 ≤0.5.99 with no fix reported ([VulnCheck](https://www.vulncheck.com/advisories/9router-0.4.1-through-0.5.99-config-injection-rce-via-hermes-settings-endpoint)) | 9router is never run. Its features are ported. JARVIS renders Hermes's `config.yaml` itself and verifies its hash at every start with `check_hermes_compat.py` | Find the owner's "9router setup"; remove it or lock it down; diff Hermes config |
| 3 | OmniRoute trial (v3.8.51) is exposed to critical anonymous RCE GHSA-hf57-cqmx-p4gr / CVE-2026-88062 when `requireLogin=false`, and predates an authz-bypass fix in 3.8.52 ([GHSA](https://github.com/advisories/GHSA-hf57-cqmx-p4gr)) | OmniRoute is retired once the router port ships | Until then: upgrade to the pinned 3.8.53 digest, `requireLogin=true`, ACP/spawn routes off |
| 4 | LiteLLM 1.107.0 wheels (incl. aarch64) and the GHCR image shipped a `.pth` credential stealer on **2026-10-21**; pin 1.106.2 if used at all ([Socket](https://socket.dev/blog/litellm-compromise)) | The kernel never imports LiteLLM; translation is built in-house on SDK types; the lockfile gate blocks unexpected `.pth` files | Confirm no machine pulled 1.107.0 or `latest`; rotate every key on any machine that did |
| 5 | Gemini CLI stopped serving consumer accounts on 2026-06-18. agy is closed, exits 0 on denials, never starts MCP in print mode, and has no custom base URL | The front door serves Gemini CLI only for paid API keys; agy is excluded | Owner states whether "Gemini" is a consumer plan or a paid API key |
| 6 | The **WSL lab disk is gone**, taking the coding lab, lab Hermes, OpenCode, goose, openmuse, the hub host and the caretaker's runtime ([commit 6395e98f](https://github.com/venkatsairishith-dev/jarvis-hq/commit/6395e98f)) | The rebuilt lab is part of the VM deployment, under the same supervisor, with backups designed in | Decide the host with finding 1 |
| 7 | Unmerged stacked branches: combine-tools ⊂ laptop-dev ⊂ remediation-2026-10-18 (49 ahead, 0 behind). The owner's **JARVIS_ROADMAP_2026-10-22.md** exists only on remediation. The Oracle kernel is the 2026-10-16 build ([compare](https://github.com/venkatsairishith-dev/jarvis-hq/compare/main...remediation-2026-10-18); [roadmap](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/reports/JARVIS_ROADMAP_2026-10-22.md)) | Every integration path in this report assumes the merged tree. The roadmap's phases and section-5 decisions map onto this plan rather than being replaced | Owner-approved fast-forward of main, full bench run, kernel deploy |
| 8 | llama.cpp **#30039**: Windows/AMD Vulkan prompt processing −41–54% on Qwen3.5 GDN models since b10950–b11010; the owner runs b11669/b11714 ([#30039](https://github.com/ggml-org/llama.cpp/issues/30039)) | Per-model binaries in the bundled set solve it cleanly | Benchmark both builds on the `vram8plus` models |
| 9 | Disk: the lock's laptop set is already **16.2 GB required + 2.5 GB optional** against ~19 GB free ([jarvis.lock.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/tools/jarvis.lock.json)). The fetcher's 1 GiB safety margin should be 3–5 GB | Profiles in the lock, safe unpack + archive delete, and pruning come before any deep model | Owner decides on pruning and a second drive |
| 10 | OpenClaw: 341 malicious skills of 2,857, ClawJacked, tens of thousands of exposed gateways, sandbox off in 2.0 ([Firecrawl](https://www.firecrawl.dev/blog/secure-openclaw)) | Parity comes from ported features; N.E.K.O's `brain/openclaw_adapter.py` stays disabled | None beyond keeping it off |

### Owner requests that conflict with standing repo rules

The repo's rules ([jarvis/CLAUDE.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/CLAUDE.md); [CONTEXT.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/CONTEXT.md); [CONTRIBUTING.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/CONTRIBUTING.md)) collide with the owner's requests in five places. The table states each collision and the recommended resolution.

| Owner request | Collides with | Recommended resolution |
|---|---|---|
| **Full admin rights** | Kernel tiers and the DENY tier; L5 "no silent self-modification" | ADMIN tier through a typed broker (item 1). The owner must say whether this satisfies "full rights" |
| **Push to main** | Phase gates; caretaker "only opens PR"; `policy.py` caretaker git rules | A kernel-identity-only fast-forward with caps and auto-revert. The owner must confirm that "main" meant "agents may push to main" and not "merge the remediation work" |
| **Paid providers** (Gemini, OpenAI, Oracle PAYG) | "paid must stay false"; N5 "no paid service without approval, Oracle $0" | Each paid item is a separate decision with a budget cap |
| **"Integrate everything"** | The reuse-first rule: "do not remake anything … Justify any custom code against an existing project in docs/01_RESEARCH_AUDIT.md" | Each port (openmuse, router, egress proxy, CDP relay, UI screens) needs an audit entry naming the upstream it replaces and why running that upstream is worse |
| **Integration work outside JARVIS's own code** | The plugin boundary: only `plugin/plugins/<id>/` is editable by default, and the N.E.K.O platform layer needs escalation | The supervisor, router, egress proxy and voice engine live under `jarvis/`, which is JARVIS's own code. Changes to `config/`, `app/`, `main_logic/` or `memory/` trigger N.E.K.O's regression-report rules |

Two smaller collisions also need handling. The "at most 5 files in docs/" rule shapes the documentation plan. And because "GitHub Actions are off on purpose", the licence gate and tests must run on the Linux test server and in the kernel push gate rather than in CI.

## An eleven-phase plan for the integrated product

Each phase ends the repo's way: test, report what passed and what could not be tested, wait for the go. Effort figures are judgement-based for one developer working with coding agents, not measurements. The notes' only sourced-ish anchors are:

| Work | Estimate |
|---|---|
| Hermes plugins + supervisor | 1–2 weeks |
| Hermes ports | 1–2 weeks |
| OpenCode ACP plugin | ~1 week |
| mini-swe-agent vendoring | a few days |
| openmuse port | 3–5 weeks |
| `swapper.py` | 400–700 lines |

### P0 — Contain, merge and decide (days)

**Goal.** Stop active exposure and give every later phase one merged baseline.

**Steps.**

1. Fast-forward main to `remediation-2026-10-18`, with a "Why Not Split" note.
2. Rebase the dependabot branches, treating uv's "253 updates" as high risk.
3. Deploy the 2026-10-22 kernel using `KERNEL_DEPLOY_2026-10-22.md`.
4. Find and remove, or lock down, 9router.
5. Upgrade or stop OmniRoute.
6. Run a LiteLLM inventory.
7. Make the Oracle sizing decision.
8. Remove the stale `apodex` provider and model entries.
9. Catch up the vault (`jarvis-vault/daily/`, `decisions/memory-map.md`).
10. Put a stale-section notice into `05_OPERATIONS.md`.

**Components merged.** None.

**Acceptance.**

- The 860-case bench on the merged tree shows at least the last recorded 834 passes and 0 failures.
- Kernel and console suites are green.
- The Hermes `config.yaml` hash matches the rendered expectation.
- No route on OmniRoute answers without auth.
- A written inventory exists listing every machine's LiteLLM version.

**Owner inputs.** Merge approval, the Oracle decision, the 9router location, and the "Gemini" plan type.

**Risks.** A forced Oracle stop before the decision takes down the kernel, the egress proxy and the public planner together.

### P1 — Integration skeleton: registry, licence gate, supervisor, settings, secrets, token, audit (2–3 weeks)

**Goal.** Build the "one of each" contract before any component is merged, so that every later merge plugs into it.

**Steps.**

1. Extend `jarvis/tools/registry.json` with Chromium README.chromium-style fields: `upstream_url`, `version`/`revision`, `license` (SPDX), `license_file`, `notice_file`, `shipped`, `security_critical`, `update_mechanism`, `local_modifications`, `divergence_point` ([Chromium third_party](https://chromium.googlesource.com/chromium/src/+/main/docs/adding_to_third_party.md)).
2. Write `jarvis/tools/licence_gate.py`, which fails if a shipped entry lacks a licence or NOTICE, or carries a forbidden SPDX id in the `shared` profile.
3. Generate `THIRD_PARTY_NOTICES` and `licenses/`.
4. Build `jarvis/supervisor/`: manifest, health, Job Object on Windows, systemd rendering, bind assertions.
5. Build the config renderers and the per-boot token.
6. Extend the audit record with the component version and sha256.
7. Convert the `jarvis/third_party/neko-tauri` submodule to a subtree at `jarvis/desktop/shell/`.
8. Run the **Hermes Python verification**.
9. Add `router/*`, `egress/*`, `cdp_relay.py` and `supervisor/*` to SAFETY_COMPONENTS.

**Components merged.** N.E.K.O.-Tauri (subtree). llama-swap, llama-server, es.exe and gitleaks become supervisor-managed binaries with hashes.

**Acceptance.**

- Starting the app launches exactly one process tree, and killing the shell leaves zero orphans (checked by process listing).
- A deliberate 0.0.0.0 bind fails the supervisor's assertion.
- The licence gate fails on an injected `GPL-3.0` test entry and passes on the real manifest.
- No secret appears in any rendered config file on disk (gitleaks scan of the run directories).

**Owner inputs.** The secrets-store choice (files or keyring) and the distribution profile (personal-only or shareable).

**Risks.** Subtree conversion conflicts with `PATCHES.md`'s "offer upstream as PRs (no fork)". `git subtree split` keeps upstreaming possible.

**Effort.** 2–3 weeks.

### P2 — Agent computer and VM deployment, with Hermes merged (2–3 weeks)

**Goal.** Restore the lost lab as JARVIS's own ARM64 computer that the owner can watch and take over.

**Steps.**

1. Build the VM bundle: `uv sync --frozen` on aarch64, the `jarvis-supervisor.service` unit, Caddy on Tailscale, and `jarvis/vm/desktop.py` with Xvfb, XFCE and Selkies, falling back to TigerVNC + noVNC if the Selkies arm64 wheels fail.
2. Run the egress proxy port on the VM.
3. Add the rootless podman worker image, with no network by default.
4. Add Hermes as a pristine subtree in a 3.14 venv, with the `jarvis_policy` and `jarvis_audit` plugins and `skills.write_approval: true`. Connectors, Relay/Cloud, shared metrics and Skills Hub auto-install stay off.
5. Rebuild the lab runner (`lab_runner.py`, `lab_isolation.py`, `lab_control.py` lease).
6. Build the first UI pieces: RunTimeline, Approval Inbox, and Live view with RFB/Selkies.
7. Schedule backups.

**Components merged.** Hermes (child), Selkies, Smokescreen (ported), DBOS (already pinned), Caddy.

**Acceptance.**

- The desktop is reachable only on the tailnet; an external port scan finds nothing.
- Take-control freezes the agent within 1 s and hands back cleanly.
- A `pre_tool_call` callback that raises blocks the tool (fail-closed test).
- VM resident memory stays under 11 GB over a 24 h soak at the decided size.
- Lab demo tasks (build, fix, delegate) pass.
- The egress proxy refuses 169.254.169.254, 100.64.0.0/10 and a DNS-rebinding fixture.

**Owner inputs.** Oracle size, Tailscale ACLs.

**Risks.** Selkies arm64 is unverified, and software encoding on 2 OCPUs may be CPU-heavy. Benchmark it against KasmVNC first.

### P3 — Brain, router, CLI front door and local models (3–4 weeks)

**Goal.** Deliver items 10, 11, 13, 19 and the routing half of 18.

**Steps.**

1. Write `jarvis_kernel/router/` (combos, keypool, quota, breaker, budget, cache, translate) behind `llm_proxy.py`.
2. Wire `model_select.py`, in shadow mode first.
3. Add the owner's "switch provider" voice/UI command and the CLI verb.
4. Write `frontdoor.py` with its four surfaces and per-CLI lane tokens.
5. Route the planner through the privacy gateway with plan-then-execute (`plugin/plugins/main_agent/planner.py`, `executor.py`).
6. Let the `local_team` adviser use Groq or OpenRouter.
7. Bundle per-build llama-server binaries, including ≤b10950.
8. Restructure llama-swap into persistent, swap and exclusive groups.
9. A/B Qwen3.5-2B/4B on the 157 decider cases.
10. Add embeddings, reranker and guard.
11. Prune models and switch the lock to profiles.
12. Retire `jarvis-omniroute.service`.

**Components merged.** OmniRoute/9router (ported), llama.cpp, llama-swap, any-llm (optional).

**Acceptance.**

- A config test proves a public-lane token cannot reach a private-lane combo.
- Claude Code requests are byte-identical upstream (hash of request body in vs out) with `ping` events preserved.
- `/v1/responses` passes a Codex smoke run.
- The breaker opens after N injected 429s and half-opens after cooldown.
- Bench areas A, J and D are green.
- `test_server_flags.py` passes.
- `llama-bench` numbers are recorded per model.
- Disk stays above the new 3–5 GB margin.

**Owner inputs.** Paid-provider decisions, deep-model choice and disk.

**Risks.** Translation edge cases (tool calls, streaming). LiteLLM's issue #23841, which drops `input_text` blocks, shows how subtle these are ([#23841](https://github.com/BerriAI/litellm/issues/23841)).

### P4 — PC hands and browser lanes (3–4 weeks)

**Goal.** Deliver items 1, 2 and the search half of 5.

**Steps.**

1. Write `fileops.py` and `filesearch.py` (Everything ctypes → es.exe → Windows Search → scandir), with the undo journal.
2. Add decider tools for find, create and organise, and bump `PROMPT_VERSION`.
3. Vendor the Windows-MCP tree subset under `vendor/windows_mcp/`, or reimplement it on uiautomation.
4. Build the `apis/` registry, starting with COM.
5. Write the `adminctl.py` broker and installer, and add the ADMIN tier to `policy.py`.
6. Write `cdp_relay.py` with an extension contract test.
7. Add browser-use in the research lane.
8. Add the taint flag.
9. Add banking and wallet DENY to `sensitive_sites.json` and the egress ACL.

**Components merged.** Windows-MCP (vendored subset), uiautomation, pywinauto, dxcam, UFO² pattern, Everything/ES, Playwright MCP relay (ported), browser-use.

**Acceptance.**

- C6 moves to TESTED: 20 undo drills restore 100% of files, with hashes verified.
- `b_desktop.json` gains find/create cases and passes.
- `k_security.json` adds tainted-parameter cases that must be refused.
- `c_browser.json` C-049..C-061 plus new relay cases pass.
- The 10-request real-desktop baseline (`task_baseline.py`) improves on its recorded result.
- The owner approves the admin verb catalogue line by line.

**Owner inputs.** The verb catalogue, the Everything install, Windows edition (for Sandbox).

**Risks.** The Microsoft extension's relay protocol is internal and unversioned, so the extension version must be pinned.

### P5 — Skills, routines and the recorder (2–3 weeks)

**Goal.** Deliver the learning half of item 5 and the teach-by-demonstration part of item 8.

**Steps.**

1. Adopt the SKILL.md format in `jarvis/skills/skillgate.py` and `plugin/plugins/desktop/skills.py`.
2. Port Hermes's `skills_guard` scanner so it runs without the Hermes venv.
3. Add the curator lifecycle with a dry-run mode.
4. Add goose-style recipes in `plugin/plugins/routines/`.
5. Write the Playwright-trace recorder that emits a staged skill with a parameter schema.
6. Generate bench cases from runs and replay them in the sandbox.

**Components merged.** Hermes skill/curator (ported), goose recipes (ported).

**Acceptance.**

- `m_skills.json` passes, including L4 rollback.
- A recorded routine replays three times with different parameters.
- A malicious-skill fixture is blocked at staging.
- No skill activates without an owner promotion record in the audit log.

**Risks.** No production-quality Python "watch once, replay with corrections" library exists, so the recorder is the largest unknown.

### P6 — Coding agents, multi-agent testing, on-screen runs and guarded push (3–4 weeks)

**Goal.** Deliver items 6, 7, 15 and 18.

**Steps.**

1. Build `plugin/plugins/coding/`: vendored mini-swe-agent on JARVIS's provider layer inside the podman worker, plus the OpenCode binary over ACP with kernel-answered permissions.
2. Add optional Codex `exec` reviewer and Claude Code `--bare` adapters.
3. Add an MCP-connected assertion to every run.
4. Chain research → plan → `dispatch_to_lab`.
5. Give each worker its own worktree, with read-only hashed tests and a different-vendor reviewer.
6. Write `jarvis/evals/neko95/bench/cases/o_multiagent.json`.
7. Run tests on screen through Playwright video/trace in the VM display.
8. Build the kernel-identity push gate in `policy.py`.
9. Turn the caretaker back on with a short-lived scoped credential.

**Components merged.** mini-swe-agent (vendored), OpenCode (binary), Codex (optional binary).

**Acceptance.**

- An end-to-end run on an allowlisted project in `projects.json` succeeds.
- A worker run fails closed when MCP is missing.
- `o_multiagent` is green on a held-out set the agents never saw.
- A revert drill restores main within one cycle.
- Daily-cap and kill-switch tests pass.

**Owner inputs.** The "main" reading, the projects allowlist, and the Anthropic or OpenAI keys if those CLIs are wanted.

**Risks.** Reward hacking, which is why the reviewer exists. Long-lived tokens on agent boxes are also a risk.

### P7 — The openmuse port (3–5 weeks)

**Goal.** Deliver item 21 natively.

**Steps.**

1. Build the DBOS task engine with approval recv/send, receipts and idempotency.
2. Add watches with backoff and diff, avoiding bug #184.
3. Add PDF inspect and fill.
4. Add Gmail and Calendar drafts with reviewed writes.
5. Add finance CSV.
6. Add ideas with evidence in the vault.
7. Add browser profiles with takeover.
8. Add a no-network podman "computer".
9. Wire notifications into the inbox and push.

**Components merged.** openmuse (ported), pypdf/PyPDFForm, Google clients.

**Acceptance.**

- Every row of openmuse's FEATURES.md is mapped to a passing test.
- A watch survives a supervisor restart mid-check.
- A retry after an uncertain send is refused.
- PDF fill matches the field types, and the wrong-type case is refused.
- No outbound connection to CopilotKit, PostHog or Parallel appears in egress logs.

**Owner inputs.** Google OAuth consent and scopes.

**Risks.** DBOS on SQLite versus Postgres at JARVIS's load is unbenchmarked.

### P8 — Presence: voice engine, shell, unified UI and personality (4–6 weeks)

**Goal.** Deliver items 3, 4, 12 and 14, plus the UI port.

**Steps.**

1. Build `jarvis/voice/engine.py` with sherpa-onnx objects, Smart Turn, AEC3, speaker verification, language ID and sentence-streamed TTS. Keep the 48930 facade and run it from a single audio thread.
2. Measure first, following the protocol in the voice notes: 200 owner clips, 24 h of room audio including film/TV "Jarvis", and DET curves per wake engine.
3. Add Tauri tray, push-to-talk, single-instance and autostart, then the 30-minute Windows 10 soak.
4. Port the widget into `#/widget` and the N.E.K.O chat and templates into React.
5. Add the i18next catalogues (en, zh-CN, ja, hi, te).
6. Create the JARVIS character card and the anti-sycophancy cases.

**Components merged.** sherpa-onnx and the voice models, Smart Turn, pywebrtc-audio, livekit-wakeword (training only), N.E.K.O UI surfaces (ported).

**Acceptance.**

- `g_voice.json` is green, and V4 mic-to-reply is TESTED.
- False wakes ≤0.2/h over 24 h at a recall to be set.
- Barge-in stops in under 300 ms.
- End-to-end ≤1.2 s local and ≤2.5 s cloud.
- Zero mic-buffer overruns with a GPU model loaded.
- X4 push-to-talk is TESTED.
- axe e2e is green on every new screen and locale.
- Persona cases pass.

**Risks.** CPU contention on the laptop. Whether sherpa-onnx releases the GIL during inference is unverified. Rendering of Telugu conjuncts in WebView2 is untested.

### P9 — Reach and parity (2–3 weeks)

**Goal.** Deliver items 8 and 9.

**Steps.**

1. Ship the PWA manifest and service worker over Tailscale Serve, with device pairing and Web Push, plus self-hosted ntfy (`auth-default-access: deny-all`).
2. Bring up the Hermes Telegram gateway with allowlist pairing and a tool-less reader, for pointers only.
3. Add a DBOS morning brief.
4. Add the chief-of-staff coordinator with specialist profiles.
5. Add the ChatGPT/Gemini API second-opinion path (if paid) and a read-only MCP server.
6. Add a `decide.py` SITES deny for chatgpt.com and gemini.google.com.
7. Add a posture self-check.

**Components merged.** Hermes gateway (already a child), Apprise, ntfy.

**Acceptance.**

- A phone approval requires the PIN, and a forged deep link is refused.
- Push payloads contain no private-lane text (fixture scan).
- Prompt-injection cases on inbound Telegram text are refused.
- The brief arrives on schedule for 7 consecutive days.
- The posture check is clean.

**Owner inputs.** Phone OS, Telegram yes/no, and the MCP tunnel decision.

### P10 — Package, explain and guide (2–3 weeks)

**Goal.** Deliver items 16 and 17, and ship the product.

**Steps.**

1. Build the Tauri NSIS installer with a uv runtime (or the Nuitka profile), signing via `signCommand` and the updater's `latest.json` on a TLS endpoint ([Tauri signing](https://v2.tauri.app/distribute/sign/windows/)).
2. Make the model manager profile-aware, with first-run download, safe unpack and NC opt-in.
3. Fill the remaining ~19 docstrings and add directory READMEs.
4. Rewrite `02_ARCHITECTURE.md` as "How JARVIS works", with generated tables.
5. Restructure `05_OPERATIONS.md` into the guide.
6. Build the Help and Onboarding screens.

**Acceptance.**

- A clean Windows 10 user account installs, runs onboarding and completes a demo approval without admin.
- `fetch.py --check` is all ok.
- An AST docstring scan shows 100% of non-vendored files covered.
- The owner follows the guide end to end.
- The licence gate passes on the shipped manifest.

## Licences, attribution and keeping merged code current

### The licence plan is cheap now and expensive later

*Not legal advice. Get a lawyer before any public or commercial distribution.*

While jarvis-hq stays private and only the owner installs JARVIS, almost no redistribution duty applies. MPL says outright that it "only creates obligations for you if you want to distribute the software outside your organization" ([MPL FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/)). Apache, MIT, CC-BY and GPL source duties likewise attach to sharing.

The plan still designs for "the owner may share it later", because retrofitting notices and removing NC or GPL parts after the fact is costly. JARVIS's own code can stay private or become Apache-2.0. N.E.K.O's Apache-2.0 allows either, as long as §4 is met: give a licence copy, mark modified files, keep notices, and carry NOTICE files ([Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0)).

The concrete mechanics have five parts.

**1. A generated `THIRD_PARTY_NOTICES` and `licenses/` folder.** These are built from `registry.json` and `jarvis.lock.json`. They must include:

- the NOTICE files of N.E.K.O.-Tauri, Codex (with the Ratatui credit), Playwright, Presidio and OfficeCLI;
- llama.cpp's `licenses/` directory;
- the WebRTC BSD notice.

Transitive Rust, npm and Python dependencies need the same treatment. Scanners such as cargo-about, license-checker and pip-licenses can collect them; those tool names come from background knowledge and are unverified.

**2. Visible attribution in About → Licences.** The page must show these lines:

- "Speech recognition: NVIDIA Parakeet-TDT 0.6B v3 (CC-BY-4.0)" ([Parakeet](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3))
- "Voice: Kyutai Pocket TTS (CC-BY-4.0)", if used
- "Telugu voice: Piper te_IN-venkatesh, trained on AI4Bharat IndicVoices-R (CC-BY-4.0)"
- "Desktop shell based on Project N.E.K.O.-Tauri (Apache-2.0)", with its NOTICE text
- Supertonic's OpenRAIL-M use restrictions, if shipped

**3. A licence gate with two profiles.**

- The `personal` profile allows NC models after an explicit opt-in.
- The `shared` profile fails on `GPL-*`, `AGPL-*`, `CC-BY-NC*`, `FSL-*` and `LicenseRef-Proprietary` in anything shipped. cua's own blocking "License direction" workflow is the precedent ([cua license-check.yml](https://github.com/trycua/cua/blob/main/.github/workflows/license-check.yml)).

GitHub Actions are off, so the gate runs in the kernel push gate and on the Linux test server.

**4. Model-specific duties.**

- Gemma: treat it under the Gemma Terms until the Gemma 4 licence page is verified. Those terms require a NOTICE line, a copy of the agreement and pass-through use restrictions ([Gemma terms](https://ai.google.dev/gemma/terms)).
- Download Gemma, Pocket TTS and other gated weights at first run rather than bundling them.
- Do not ship `hi_IN-rohan` until the IITM licence PDF has been read.

**5. Provenance on every ported or vendored file.**

- Each file carries a header such as `# Ported from CopilotKit/openmuse@1ac68f39:apps/server/src/engine/service.ts (MIT)`.
- Changed Apache files also get a "Modified by JARVIS" line, as §4(b) requires.

### Maintenance runs on three regimes and the existing caretaker

Upstream changes reach JARVIS through three regimes, chosen by how JARVIS holds each component.

**Regime 1: pristine subtrees (Hermes).** JARVIS never edits these.

- Updates come from `git subtree pull --squash` to a reviewed tag, never a roll-up the owner cannot read.
- `check_hermes_compat.py` and the plugin tests run after each pull.
- The kernel push gate rejects any diff under the prefix outside a bump commit.
- Hermes's plugin contracts were stable from 0.21.6 to 0.22.4 ("Same four required methods"). Its compatibility import layer was removed, however, so the compat checker is the only guard ([HERMES_0_22_UPGRADE](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/reports/HERMES_0_22_UPGRADE_2026-10-22.md)).

**Regime 2: modified subtrees and vendored code** (N.E.K.O.-Tauri, mini-swe-agent, the Windows-MCP subset).

- Patches are kept as files under `patches/`.
- A squash pull lands on a clean copy, and the patches are re-applied with `git apply --3way`.
- Changes can be offered upstream through `git subtree split`.

**Regime 3: ports** (openmuse, router, egress, relay, Hermes sub-features, N.E.K.O UI screens).

- A monthly DBOS job diffs each upstream from its recorded provenance commit to its newest tag.
- It files a review note in the vault inbox. This is "porting diffs" as a reviewed routine, never an automatic merge.

**Pinned binaries and libraries.**

- Exact pins with hashes, matching Hermes's own exact-pin policy, which was adopted after the Shai-Hulud worm hit mistralai ([Hermes pyproject](https://github.com/NousResearch/hermes-agent/blob/main/pyproject.toml)).
- A minimum release age before any bump.
- A lock-time block on unexpected `.pth` files, the LiteLLM attack vector.

**The caretaker automates all of this but only opens PRs.** It already has detectors for upstream_release, OSV advisory, lock_drift, test_failure and secret_scan, and opens PRs only on `caretaker/<tool>-<id>` branches ([caretaker README](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/caretaker/README.md)). For each bump it:

1. pulls or bumps the component;
2. re-applies patches;
3. regenerates notices;
4. runs the licence gate and the 860-case bench;
5. opens a PR.

**Contract tests on the three unstable interfaces:**

- the Chrome extension relay protocol;
- the ACP permission flow against OpenCode;
- the Claude Code gateway byte-identity.

These catch upstream drift before the owner does.

## Decisions only the owner can make

| # | Decision | Recommendation |
|---|---|---|
| 1 | Fast-forward main to `remediation-2026-10-18` and deploy the 10-22 kernel | Yes, after a full bench run on the merged tree |
| 2 | Oracle: resize to 2/12 at $0, or ~$25–28/month for 4/24. Also check hermes-agent-vm-2's shape yourself | Resize. Porting openmuse and OmniRoute plus an on-demand VM model makes 12 GB workable; verify with the 24 h soak |
| 3 | 9router: remove or lock down. OmniRoute: retire after the router port | Remove 9router; retire OmniRoute in P3 |
| 4 | Is a **PIN-gated admin allowlist** (broker, ADMIN tier) acceptable instead of raw admin? | Yes. Raw admin is not recommended |
| 5 | **"main"**: may a kernel gate fast-forward main for small, fully tested agent changes, or did "main" mean "merge the remediation work"? | Both, read separately: P0 merges; the P6 gate allows kernel-only fast-forwards with caps and auto-revert |
| 6 | Paid items, each separately: Gemini API, OpenAI API, Anthropic API (for Claude Code pass-through), $10 OpenRouter credit, Oracle PAYG | OpenRouter $10 is the cheapest uplift; others only for a named need |
| 7 | Is your **Gemini** a consumer AI Pro/Ultra plan or a paid API key? | Consumer plans no longer work with Gemini CLI and must not be routed |
| 8 | **Disk**: prune small models, add a second drive, or skip the deep MoE | Prune after A/B; deep model only with more space |
| 9 | **Phone channel**: PWA + Web Push, self-hosted ntfy, Telegram pointers, or none. Which phone OS? | PWA + Web Push first; ntfy if Android; Telegram for pointers only |
| 10 | May the browser or phone approve at all? The repo doc says "console or Telegram only" today | Yes, with PIN in-app and device pairing |
| 11 | **Grokbot / Clawdbot** readings: xAI Grok Bot and OpenClaw, or something else? | Confirm; the parity list depends on it |
| 12 | Distribution profile: personal-only forever, or possibly shared | Design for shared; run personal |
| 13 | Secrets: keep `D:\jarvis-secrets` files or move to Windows Credential Locker | Keep files (repo rule); revisit later |
| 14 | Python packaging: uv-managed runtime or Nuitka | uv runtime for the laptop; Nuitka profile for a shareable installer |
| 15 | UI depth: port N.E.K.O chat and templates into React (more drift) or embed them as same-origin frames (less integration) | Port, with provenance headers and caretaker diffs |
| 16 | Coding agents: mini-swe-agent in-tree + OpenCode binary; Codex optional; goose dropped | Accept |
| 17 | A public HTTPS tunnel so ChatGPT can reach JARVIS's MCP server | Defer; Gemini CLI over Tailscale first |
| 18 | Create-file/folder capability (roadmap decision #6) | Yes, YELLOW_AUTO inside user folders with undo |
| 19 | A signature voice cloned from a consenting speaker (who?) | Optional; Kokoro `bm_george` meanwhile |
| 20 | Windows edition and a possible Windows 11 move. Windows 10 left mainstream support on 14 Oct 2025 ([Microsoft](https://www.microsoft.com/en-us/windows/end-of-support)) | Tell us the edition; Sandbox needs Pro/Enterprise |

Several questions remain open beyond these decisions. The research could not resolve them:

- **Live infrastructure.** Do the Linux test server and the Smokescreen proxy still run?
- **The Project JARVIS doc.** Where does it live? CLAUDE.md requires plan changes to go there too.
- **Selkies on arm64.** Do its aarch64 wheels exist and perform on 2 OCPUs?
- **The Hermes Python question.** What do the three verification checks show?
- **Licences of models already in use.** The IITM IndicTTS licence, the Verdict model's licence, and the English KWS weights' licence are all unknown.
- **The Everything licence.** Does voidtools' MIT-style License.txt or the installer EULA govern bundling?

## Conclusion

The research reframes what the owner asked for. "Merge everything into JARVIS" cannot honestly mean one Python process: Hermes needs another interpreter, GPL servers must stay separate programs, and Claude Code's bytes are not JARVIS's to touch. It can mean one owner. That is one supervisor that starts everything, one settings tree that configures everything, one token, one audit chain and one UI through which the owner sees everything. Read that way, the instruction is fully achievable, and it turns out to be the safest architecture as well as the tidiest. The components that most endanger the owner today are standalone runtimes with their own logins, ports and update channels: 9router, OmniRoute, openmuse's cloud dependency, and OpenClaw-style gateways. Porting their ideas into the kernel removes those attack surfaces and frees the RAM the shrunken Oracle VM needs.

The cost moves from operations to maintenance. Ports and modified subtrees drift from upstream, so the plan's real long-term asset is the provenance discipline: registry fields, provenance headers, a licence gate, and a caretaker that turns every upstream change into a reviewable PR. If that discipline is built in P1, before any component is merged, JARVIS keeps improving as Hermes, sherpa-onnx, llama.cpp and openmuse improve. If it is skipped, the integrated product will freeze at October 2026 within a few upstream releases.
