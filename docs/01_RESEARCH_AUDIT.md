# 01 Research audit

Date: 2026-10-07. Baseline: the Project JARVIS plan (Plan and Toolbox tabs). Its research is prior work. I re-checked each pinned tool against GitHub, PyPI and docs on this date. Items marked [verify] come from a secondary source or memory and are not yet confirmed. Re-check versions and dates before installing anything; they move daily.

Classes: USE, REUSE, WRAP, ADAPT, FORK, REIMPLEMENT, RESEARCH_MORE, REJECT.

## Tools

| Tool | Class | Evidence (checked 2026-10-07) | Fit and risk |
|---|---|---|---|
| Hermes Agent (Nous) | USE, pinned | MIT. Release tag v2026.9.24 (v0.21.5). Windows PowerShell script install documented. Very high open-issue count (~47.7k, includes PRs) | Bot runtime on VMs. Pin the tag, do not track main. Expect churn. Aarch64 bug reports not read [verify] |
| OmniRoute | TRIAL (was USE) | v3.8.51. 597 open issues, 1 security advisory not read. Multi-arch Docker. Holds provider keys | Gateway on VM-A. Alternatives: LiteLLM proxy [verify its supply-chain history] or Hermes own provider fallback. Decide after a trial |
| Windows-MCP | USE, filtered | MIT, v0.8.7, Windows 10 confirmed, 23 open issues | Copy its Snapshot tool as the computer-state format. Never expose its PowerShell, Registry or file tools unfiltered |
| GoRules ZEN (zen-engine 2.1.2) | USE | PyPI has a manylinux_2_28_aarch64 wheel, so install on the ARM VM works. About 91k evaluations/s on one M3 core | Rules tier. Runtime on the VM is untested |
| semantic-router | OPTIONAL | 0.1.16 is the latest stable, pin >=0.1.15,<0.2 still valid. 0.1.16 requires litellm>=1.83.7; the plan says earlier litellm versions were backdoored [verify] | Default to a small FastEmbed + numpy cosine matcher (REIMPLEMENT, about 60 lines). semantic-router stays optional |
| FastEmbed (Qdrant) | USE | Active, ONNX, runs on CPU. bge-small 4 to 38 ms per query depending on the source | Embedding tier |
| Ollama | USE, after a test | Release marked Latest is v0.40.0 but v0.35.1 was published four days later, so check `ollama --version` after install. Vulkan on this Radeon is unmeasured | Local lane. Test llama.cpp Vulkan from D: first |
| Backlog.md | USE | Active | Task list in jarvis-hq |
| obsidian-git | USE, PC only | Active | Auto-commit on the PC only, plain git plus hooks elsewhere, so it does not clash with agent writes |
| Tailscale | USE | Active. Windows 10 support from general knowledge [verify] | Private network between nodes. Auth key goes in a file, not an env var in plaintext where avoidable |
| Obsidian | USE (PC and phone only) | n/a | Reading UI only. Nothing on the VMs depends on it |

## Reference projects (concepts only)

| Project | Class | Take | Avoid |
|---|---|---|---|
| AIOS | ADAPT (concept) | Kernel mediates scheduler, context, memory, tools, access | The code: research-grade, last release March 2025, licence unconfirmed. Do not run it |
| Microsoft UFO / UFO2 (MIT) | ADAPT (concept) | UIA-first control ladder, WebSocket node protocol | Treat the Toolbox "Skip" and the Agentic OS "worth a trial" as resolved: reference only, trial later in the computer-control phase |
| OpenClaw (MIT) | ADAPT (concept) | Trusted gateway, untrusted execution, pairing codes | Default public bind, public skill marketplace. CVE and exposure numbers are blog-only [verify] |
| Goose (Apache-2.0) | ADAPT (concept) | MCP-first tools, per-extension permission modes | n/a |
| OpenHands (MIT) | ADAPT (concept) | Per-task containers, schedule and webhook triggers | No-sandbox install. Enterprise licence terms [verify] |
| Open Interpreter | REJECT | Confirm-before-run idea only | Maintenance doubtful |
| Claude Code hooks | USE | A PreToolUse hook that runs outside the model and answers deny, ask or allow is the template for the GREEN/YELLOW/RED gate | n/a |
| NSSM | REJECT | n/a | Last stable release 2014. Use Task Scheduler plus a tray client, or WinSW (MIT) for a headless watcher |
| Jev (TypeSafe AI) | REJECT for now | n/a | Vendor-only benchmark; an independent audit reportedly found no stable latency or cost gain |
| Laya (Convai, Apache-2.0, ModernBERT-large, ~421M) | RESEARCH_MORE | Real, open. Fine-tuned 0.766, base ~0.36, 33 ms on a Tesla T4, no CPU figure | Brand new (Sept 2026), no independent replication, paper unread. Not in the safety path |

## Conflicts in the old plan, resolved
- UFO: Skip in the Toolbox, "worth a trial" in Agentic OS. Resolved above.
- n8n: "added later" in the stack, Skip in the Toolbox. Resolved: Skip. The kernel and Hermes cron cover triggers.
- headroom: Trial in the Toolbox, listed as skip in the Plan tab. Resolved: not used until the token-diet phase measures a need.
- Oracle sizing: the plan said 2 OCPU / 12 GB. That is the Always Free-only figure. The Pay As You Go figure is 3,000 OCPU-hours and 18,000 GB-hours free per month (4 OCPU / 24 GB), and storage stays capped at 200 GB. Both from Oracle's Always Free page and a community thread.
- Windows: the plan assumes Windows 11. This laptop runs Windows 10 22H2 (build 19045). Hermes Desktop MSIX needs Windows 11, so use the script install. Consumer security updates run to 12 Oct 2027 only if enrolled (free via settings sync, otherwise $30 or 1,000 Rewards points).

## Open items
Hermes aarch64 issues, the OmniRoute advisory, Vulkan on the Radeon, OpenClaw CVE counts, AIOS and OpenHands enterprise licence terms.

## Added 2026-10-07: browser control
| Tool | Class | Evidence | Fit and risk |
|---|---|---|---|
| Playwright for Python 1.63.0 | USE, pinned | Apache-2.0, Microsoft, requires Python 3.10+, installed and tested on Python 3.14 in a venv on D: (0.2 GB). Drives the installed Chrome (channel chrome), so no browser download | DOM rung of the control ladder. Its request hook does NOT see redirect hops (measured), so the safety guarantee comes from a local filtering proxy, not from Playwright |
| Own filtering proxy (guardproxy.py, about 100 lines, stdlib) | REIMPLEMENT | No maintained small library found that resolves, checks and pins the address per connection; a general proxy (mitmproxy, squid) is much larger than needed | Forces all browser traffic, loopback included, through one checkpoint |

## Reuse audit of the custom code, 2026-10-08 (user rule: use existing maintained GitHub projects, do not remake)
Five read-only audits with live GitHub data (gh api: licence, last release or push, issues). Facts marked unverified in the subagent reports are unverified here too.

| My code | Verdict | Existing project | Notes and effort |
|---|---|---|---|
| Network guard proxy (guardproxy.py) | REPLACE | stripe/smokescreen (MIT, pushed 2026-10-07, v0.1.0): resolves hosts, denies private ranges by default, extra --deny-range for Tailscale and metadata | No Windows release asset (Linux ARM64 and amd64 only): Go build or another route needed (unverified). About 1 day |
| Secret scrubber (scrub.py) | WRAP | gitleaks/gitleaks (MIT, v8.30.1, Windows x64 and Linux ARM64 builds) as a second pass behind the existing scrub() API | My scrubber covers 7 key types. Avoid trufflehog (AGPL, sends found secrets to providers), detect-secrets (no release since 2024-05), Presidio (PII-focused). 0.5 to 1 day |
| State collector (collector.py, PowerShell) | REPLACE the PowerShell part | giampaolo/psutil (CPU, RAM, disks, processes, ports, services) | Keep tool-version probe, GPU and env paths. Removes the PowerShell launches. osquery is the heavier option (Windows ARM64 unverified). 0.5 day |
| Browser tools (browser.py) | WRAP | microsoft/playwright-mcp (Apache-2.0, 38k stars, v0.0.83, 3 open issues, --isolated profile, proxy flag) | README: not a security boundary, origin lists do not cover redirects, so it must run behind the proxy. Keep the element-name check, scrubbing and no password typing as a thin shim. 2 to 3 days. browser-use, Stagehand, Steel are agent loops; Skyvern is AGPL |
| Benchmark runner (run_bench.py) | REPLACE | promptfoo/promptfoo (MIT) or UKGovernmentBEIS/inspect_ai (MIT) | Keep dataset and cascade as a custom provider. 1 to 2 days. The real weakness is the data (200 own items, 20 high-risk test items) |
| Windows UI control (ui.py, winbackend.py) | LEAVE for now | uiautomation or pywinauto, or CursorTouch/Windows-MCP (MIT, v0.8.7) with only Snapshot, Click, Type | Not urgent. 1 to 2 days when needed |
| Permission policy (policy.py, store.py) | KEEP | OPA, Cedar, Casbin, OpenFGA, Cerbos, agentgateway all maintained | None gives single-use approvals bound to an action hash, a risk floor models can only raise, or the path and address normalisation. Cedar migration would save little |
| Audit log (audit.py) | KEEP | immudb, Tessera, Trillian, Rekor | All separate servers; mine is 72 lines on SQLite. Optional: publish the anchor as a signed note via Sigstore (about 1 day) |
| Model gateway (gateway.py) | KEEP | litellm, bifrost, Portkey, new-api, OmniRoute | None enforces lanes a model cannot override plus the free-model guard. Optional wrap: bifrost (Go, Apache-2.0) only if many providers are needed |
| Telegram bridge (telegram.py) | KEEP | python-telegram-bot (GPL-3.0), aiogram (MIT, Python cap below 3.15), pyTelegramBotAPI (GPL-2.0); Hermes, OpenClaw, goose, claude-code-telegram | A library replaces only about 40 lines; the owner-only, CONFIRM, kill-switch and audit logic is kernel-specific; whole projects let the model drive tools. Cheap hardening: token-never-in-errors test, getMe check, close file handles |
| Action controller (control.py) | KEEP | mcp-gateway-registry gates access per tool only | No maintained project gates every action through a separate policy server with before and after checks, no repeats and a budget |
| Node registry, report builder, vault guard | KEEP | none found | Optional: Tailscale peer-identity check; OS-level read-only protection for the vault guard |
| Agent runtime | ADOPTED 2026-10-08 (user decision), pinned | NousResearch/hermes-agent (MIT, v0.21.6, commit 818c13be, native Windows, built-in Telegram gateway) | Concerns stand (hooks fail open, its SECURITY.md says only OS-level isolation is a boundary, fast-moving 0.x), so the boundary is ours: OS user jarvis-agent, per-user egress lock, key-free model endpoint, shell/file/code/browser/web toolsets off. See "Agent runtime" in 02_ARCHITECTURE.md |

Rejected with reasons: litellm (PyPI 1.82.7 and 1.82.8 were malicious on 2026-03-24 per BerriAI/litellm issue #24518, plus 2026 critical advisories: it would sit beside every provider key), trufflehog (AGPL), Skyvern (AGPL), new-api (AGPL), RouteLLM (stale), Advocate (archived), protectai/llm-guard (archived), Invariant (stale). Optional extra signal for injection detection: Llama Prompt-Guard-2 (Llama licence, gated weights, ARM64 inference unverified).

### Reuse audit, progress (2026-10-08)
- DONE: secret scrubber now wraps gitleaks 8.30.1 (MIT; Windows x64 and Linux ARM64 builds verified against the release checksums file) as a second pass on outbound paths (model calls, vault writes, Telegram replies). Measured on 14 made-up secrets in real formats: built-in pass 9 of 14, gitleaks 11 of 14, together 14 of 14, no false positive on plain text. If gitleaks is missing the built-in pass still applies.
- DONE: state collector now uses psutil 7.2.2 (BSD-3-Clause) plus the Windows registry instead of PowerShell. It starts no helper program for the core data (collection 2.2 s, was 4.4 s), so no console can flash. Cross-checked against systeminfo, Get-Process, Get-PSDrive, Get-NetTCPConnection and Get-Service: RAM, top processes, disks, 18 listening ports and 105 running services all agree. Tool versions are probed hidden and cached for 30 minutes. Memory is now reported as "available" (includes reusable cache) rather than "free".
- NEXT, in order: Smokescreen in place of my proxy (needs a Windows build route: unverified), then Playwright MCP behind it, then promptfoo or inspect_ai for the benchmark. Kept as written, with reasons above: policy, audit log, gateway, Telegram bridge, controller, node registry, report builder, vault guard.
- DONE: network guard now uses Stripe Smokescreen v0.1.0 (MIT; release checksum verified; Linux ARM64 build on the VM). Windows build route: none (compile fails: syscall.SIGUSR2 undefined on Windows, same on main; no upstream issue), so it runs on the VM and the laptop browser reaches it over Tailscale; documented in 02_ARCHITECTURE.md. My guardproxy.py stays only as a test option (local_guard=True) and is a candidate for deletion once the browser tests can use a local Smokescreen. Go 1.27.1 (checksum verified) and its build cache are on D: for the failed Windows attempt and can be deleted.
- DONE (small, from the Telegram audit): start-up getMe check, token-free errors verified for connection reset, garbage reply and HTTP 500, token file handles closed.
- REMAINING from the audit: Playwright MCP wrap (2 to 3 days), promptfoo or inspect_ai for the benchmark runner (1 to 2 days). Both are replacements of working code with modest benefit; they will be done only if the user wants the benchmark reported in a standard format or wants MCP-compatible browser tools.
- DONE (2026-10-08): server monitoring uses Monit 5.33.0 from Ubuntu's own repository (AGPL-3+ with OpenSSL exception; used as an unmodified standalone daemon, so no obligations reach our code) instead of custom monitoring code. It watches the kernel, Telegram bridge, local model, embedding service, Smokescreen and tailscaled, the tailscale0 link, disk (85%), memory (92%) and the freshness of the nightly results, restarts dead services and tells the owner on Telegram through a 40-line alert script. Failure drill: embedding service stopped at 15:36:08, alert at 15:36:51, restarted 15:37:51, recovery alert 15:38:51. Ubuntu's hardened monit.service unit hides /home and drops the capability to switch user, so a small documented drop-in (deploy/monit-override.conf) grants read-only home, the kernel data folder as writable, and CAP_SETUID and CAP_SETGID.
- NOT CHOSEN: Prometheus with Alertmanager, Netdata, Uptime Kuma (all heavier than needed for one small VM).
- DONE (2026-10-08): agent runtime is Hermes Agent v0.21.6 (user: "use hermes as our repo"). Used as published, not forked. Its "Open Muse / Grok Bot style" peers were checked on GitHub the same day and not adopted: CopilotKit/openmuse (MIT, 3 weeks old, alpha, needs a hosted CopilotKit project key), milind-soni/OpenMausBot (Apache-2.0, 2 months old, 365 open issues, Electron plus cloud or local VM per bot), elie222/rakazo (Apache-2.0, needs Docker, which is root-equivalent on the VM), feder-cr/invisible_dots (MIT, built to evade anti-bot detection, needs x86 virtualisation: rejected). Revisit openmuse when it leaves alpha if a visible "agent computer" is wanted.
- 2026-10-09 (user requests): OmniRoute 3.8.51 installed on the VM (critical ACP RCE advisory fixed in 3.8.49; login required, loopback only; HOST/HOSTNAME are ignored on Linux, the bind variable is OMNIROUTE_SERVER_HOST). The article's "free forever" OpenCode route is refused upstream ("OpenCode's free tier can only be used from within OpenCode"), so it is off; keyless "web" providers in its catalogue scrape consumer chat sites and are not used. Lab agents adopted as published: OpenCode 1.18.35 (MIT), goose 1.54.0 (Apache-2.0), Hermes Agent v0.21.6. Colab model server: Ollama 0.40.1 plus the ngrok agent from ngrok's signed apt repository; colab-mcp (Google, Apache-2.0) for notebook control. Custom, justified: lab/demo.py, a 5-task acceptance check with independent checkers, because Terminal-Bench (the standard terminal-agent benchmark) needs Docker, which the WSL box does not have.
- Custom, justified (2026-10-08): the job queue (kernel/jarvis_kernel/jobs.py, about 90 lines on the existing SQLite). Workflow engines exist (Temporal, n8n, Celery, Hatchet) but each needs its own server and database and would sit outside the kernel's authorization and audit; the need today is one fixed job kind for one node. Revisit if the number of job kinds or nodes grows. The work itself is done by existing tools: git and the project's own test runner.
- Custom, justified (2026-10-09): hq/hub/index.html, a single static page (no build, no external files) that shows every dashboard in one window with panels that stay loaded. Tried first: Dashy 4.7.0 (MIT), whose workspace view opened as an empty page with unlabeled icons and reloads each panel; Homarr and Organizr need Docker or PHP and do not keep panels alive. Dashy stays as the status-check service behind the hub. Routing: Caddy 2.11.7 (Apache-2.0, release digest checked) gives each dashboard a *.localhost name so logins work inside panels, supplies OpenCode's login, and lets OmniRoute be framed only by the hub. Agent PC native window: xrdp 0.9.24 (Ubuntu package) into the existing VNC desktop, opened with Windows' own Remote Desktop (removed 2026-10-10, see below).

**Custom, justified (2026-10-10): hq/lab/lab_control.py** (about 330 lines, with 10 unit tests). It provides "Take control" and "Continue task" for the Agent PC.
- *Why not existing tools:* screen-sharing tools (TigerVNC, noVNC, Apache Guacamole, xrdp) share or hand over a screen, but none can pause the programs driving it and resume them later. The agent runtimes (Hermes, OpenCode, goose) offer no external pause API.
- *What it reuses:* the kernel's POSIX job control (SIGSTOP and SIGCONT). The watch-only switch is noVNC's own View Only setting, flipped live.
- *Revisit if:* Hermes or cua-driver gain an owner-takeover mode.

**Removed (2026-10-10): xrdp.** It is end-of-life (0.9.x) with unpatched CVEs on Ubuntu 24.04, and the hub's noVNC covers the need.

**Lab models (2026-10-10): Dahl.** It is used as a service through its OpenAI-compatible API, with no code. One provider entry per model, and only in the public lane.
- Kaggle and Colab model serving is parked because of those platforms' rules on tunnels.
- The known-issues review behind the 2026-10-10 fixes is summarised in 03_DECISIONS.md, D12.

## OpenJarvis compatibility report (2026-10-10)
**Source:** https://github.com/open-jarvis/OpenJarvis, pinned at `ecbde4b32091e2a8b02e8acfa41cb5f95e4da560`. Apache-2.0 (LICENSE file; no NOTICE file, no per-file headers). Checked out read-only at `D:\jarvis\research\OpenJarvis`.
**Rule followed:** improve JARVIS, never replace it. Hermes, the kernel, the task store, the lab runner, the laptop node, Telegram and Mission Control all stay. No OpenJarvis runtime is installed: `pip install` would bring the whole runtime, including python-telegram-bot, posthog, ddgs and a Rust extension.

| Subsystem | What OpenJarvis has (files) | Decision for JARVIS | Why |
|---|---|---|---|
| **Skill import** | `skills/importer.py`, `sources/hermes.py`, `parser.py`, `types.py`, `tool_translator.py`, `security.py` | **Vendored** `parser.py`, `types.py` and `tool_translator.py` (stdlib + pyyaml) into `hq/skills/vendor/openjarvis/`. The rest of the gate is new: `hq/skills/skillgate.py`. | The importer tracks HEAD (no pinned commit), stores no hashes or licence, re-serialises SKILL.md (so hashes no longer match upstream), installs straight into active skills with no staging or rollback, and its capability check almost never fires for Hermes skills. |
| **Skill optimisation and benchmark** | `learning/agents/skill_optimizer.py` (DSPy/GEPA), `evals/skill_benchmark.py` | Not used. | Needs optional heavy dependencies and an Ollama model, and writes overlays automatically. |
| **Traces** | `core/types.py` Trace/TraceStep, `traces/store.py`, `collector.py`, `analyzer.py` | **Pattern adapted.** `jarvis_kernel/traces.py` builds a read-only view from the kernel's own tasks, task_events, jobs and audit. | OpenJarvis keeps its own `traces.db` holding raw prompts and tool output, with no redaction and no size limit. Its `outcome` field is never set in production. |
| **Trace → skill discovery** | `learning/agents/skill_discovery.py` | Idea only: the analyzer reports recurring problems. | It counts tool-name sequences and writes manifests that cannot run. |
| **Evals** | `evals/core/*` (EvalRecord, Scorer, RunSummary), `learning/spec_search/gate/regression.py` | **Pattern adapted.** `hq/evals/run_evals.py`: fixed cases, deterministic scorers, regression check against a baseline. | Its datasets download from HuggingFace, its judges default to cloud `gpt-5-mini`, and its trackers send data out. None of that is allowed here. |
| **Context-length errors** | `engine/_base.py` `CONTEXT_LENGTH_MARKERS`, `looks_like_context_length_error`, `estimate_prompt_tokens` | **Reused directly** in `jarvis_kernel/sizing.py`, with markers added (Groq 413 and llama.cpp wording, as measured) and the estimate adapted to 3.5 chars per token. | Small, pure functions. OpenJarvis itself never handles 413 and does not size requests. |
| **Model catalog** | `intelligence/model_catalog.py` | Not used. Limits were measured instead (below). | Hand-written, not kept current, some values wrong, and used only for display. |
| **Compaction** | `agents/hybrid/mini_swe_agent.py` (untested), `agents/loop_guard.py` (counts messages) | Ideas only: bound the summary input and output, and keep a fallback. | Hermes has its own compressor; the fix was to give it correct limits. |
| **Memory** | `memory/*`, sqlite/bm25 backends | Not used. | It needs the Rust extension. FAISS and dense backends only soft-delete, which conflicts with "forget means forget". Hermes memory already works (tested). |
| **Security and sandbox** | `security/*`, `sandbox/*` | Not used. | Scanning silently switches off without the Rust module. Capability policy is open by default. The subprocess sandbox fails on Windows. The kernel policy and gitleaks stay. |

**Licence duties:** the vendored files keep the Apache-2.0 text, with `NOTICE.md` listing the one modified line. Adapted files name their source and commit in their docstrings.

**Measured provider limits (2026-10-10, private lane, now in `providers.json`):**
- **Groq `gpt-oss-120b`:** 8,000 tokens per request, *input plus reserved output* ("Requested 8085" was refused). JARVIS reserved up to 4,096 output tokens, so its prompts were capped at about 3,900.
- **llama.cpp on the server:** `n_ctx` 4,096.
- **OpenRouter free:** about 56k input tokens, but only **50 requests a day** ("free-models-per-day"). It ran out today.

**Risks still open:**
- The private lane has no free provider with high capacity. JARVIS's median request is 85k characters. This needs the owner's decision on another free provider.
- Hermes's summariser can still fail when only OpenRouter can take its input and the quota is used up. It now fails fast (429 with Retry-After), and the next turn starts a fresh session with the task card.
