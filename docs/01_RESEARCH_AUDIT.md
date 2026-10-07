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
