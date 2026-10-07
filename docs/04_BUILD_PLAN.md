# 04 Build plan, first milestone and risks

## Rules for every phase
Plan first (steps, files, commands, checks, inputs). Do one step, run a check, show the real output. If a check fails twice, stop. At the end: a fresh subagent reviews the diff, I report what passed and what could not be tested, write vault/daily/<date>.md, and wait for your go. Inputs are listed at the start of each phase and asked for only then, as files or environment variables; never pasted in chat.

## Ordering (dependency graph)
Foundations, then kernel on the VM, then the laptop node that reports to it, then the model gateway and local lane, then the decision cascade and its benchmark, then Hermes bots, then computer control. Training, voice, agent factory and extra nodes stay parked.

| Phase | Runs on | Deliverable | Inputs I will need | Main checks |
|---|---|---|---|---|
| P0 Foundations (mostly done) | Windows, VM | Repos, vault, memory map, Oracle server, $1 budget. Still to do: harden the VM (firewall, updates, Tailscale), redirect caches to D:, enroll in free Windows 10 ESU | Tailscale login (you, in a browser). Auth key file `D:\jarvis-secrets\tailscale_authkey.txt` if used | Port scan from outside shows only intended ports; free space before and after; Cost Analysis is $0 |
| P1 Kernel v0.1 | VM (Python), tested from Windows | Policy engine GREEN/YELLOW/RED, hash-chained audit log, kill switch, SQLite state, `jarvis` CLI | None | Unit tests for each rule; tamper test on the audit chain; kill switch stops a running task; a model-granted permission is refused |
| P2 Laptop node | Windows 10 | Read-only computer state collector (hardware, disks, processes, services, ports, installed AI tools) reporting to the kernel over Tailscale, outbound only | None beyond Tailscale on the laptop | Collector output matches `systeminfo` and Get-Process; node shows offline when the laptop sleeps |
| P3 Model gateway and local lane | Windows, VM | llama.cpp Vulkan test on D:, model registry, gateway with lane enforcement, OmniRoute trial | Provider keys in `D:\jarvis-secrets\providers.env`: GLM_API_KEY, CLOUDFLARE_ACCOUNT_ID plus token, Groq, OpenRouter, Gemini, Kilo, OLLAMA_API_KEY (names from the old plan, confirm when we get there). Only the ones you choose to use, free tiers only | Private task cannot route to a public provider; fallback never crosses lanes; Vulkan test result recorded |
| P4 Decision cascade and benchmark | VM and laptop | The 200-item set, the five mechanisms compared, a chosen default | You review and correct labels | Thresholds in 03_DECISIONS.md |
| P5 Hermes bots and vault write path | VM | Pinned Hermes, vault write skill that refuses writes outside inbox/daily/decisions | Bot channel credentials as files | Write test outside the allowed folders is refused; bots restart cleanly |
| P6 Computer control | Windows | Filtered Windows-MCP, approvals, verification step after each action | None | Each action followed by an observed state check |

Reboots: P0 to P4 need no macOS. Reboot to macOS only for macOS-specific setup (the Mac secrets file and Tailscale there). Before any reboot I write the daily note, because JARVIS's laptop node goes offline then.

## First milestone: JARVIS Core v0.1 (smallest useful)
Scope: P1 plus P2. The kernel and one read-only node.
Demo: "Analyze my computer." The CLI asks the kernel; the kernel asks the laptop node for state; the node returns real data; the kernel classifies the request GREEN, builds a verified report (what it saw, what it could not see), logs every step, and recommends actions without executing any RED or YELLOW one.
Acceptance:
1. Every number in the report comes from collected state, with its source.
2. The audit log verifies as an unbroken hash chain, and editing a line is detected.
3. A forged "grant YELLOW" from a model output is refused.
4. The kill switch stops the task within seconds.
5. The whole demo costs $0 and uses no cloud model.
Not in v0.1: LLMs, Hermes, the tray app, any write action on the laptop.

## Risk register
| Risk | Likelihood | Effect | Mitigation |
|---|---|---|---|
| Oracle reclaims an idle A1 instance (7 days under 20% CPU, network and memory) | Medium | Lose the main server | Keep real work running; watch utilization; keep the plan reproducible from the repos |
| Oracle charges you | Low | Money | Only Always Free-eligible resources, $1 budget alert, Cost Analysis check each phase |
| Storage cap | Certain | Cannot add volumes | 200 of 200 GB used; no new volumes |
| Out of ARM capacity | Medium | Cannot rebuild | Tell you; no retry loop |
| Windows 10 security updates stop | Medium | Exposed laptop | Enroll in free ESU before 12 Oct 2027; keep the laptop off the public internet; plan Windows 11 or Linux later |
| Radeon unusable for inference | Medium | CPU-only local models | Small models by default |
| Prompt injection through files or web | High | Data loss or leaks | Policy outside the model, egress removal after untrusted reads, allowlists |
| Leaked secrets | Medium | Account takeover | Files only, never in context, never committed |
| Hermes churn | Medium | Breakage | Pinned tag, upgrade by hand |
| Overbuilding | High | Time | Gates; five docs; v0.1 has no LLMs |
