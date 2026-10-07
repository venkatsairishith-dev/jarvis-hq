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

## Gate map and testable acceptance (after independent review)
- "Step 11" (the original session gate) is the gate after the design docs and the first-milestone definition. Nothing in P0 or later is written as code until you say go. Each later phase ends with its own gate.
- Failure rule: after two failed checks, stop. For any write to an external service (Oracle, GitHub, providers), never retry in a loop: report the failure and wait.
- Windows ESU: enroll only if the screen offers the free option. If it shows a price, stop and tell you. Confirm the account is a Microsoft administrator account first.
- OmniRoute is a trial with a keep or drop rule decided in P3; the diagram shows it as a candidate. Hermes is pinned to a tag but its aarch64 behaviour is unverified until P5.
- Secrets are files only, never environment variables, because variables can leak into logs and process listings. Tailscale uses a single-use, tagged auth key that is deleted after use. Earlier mentions of "files or environment variables" are superseded by this.
- Before P0 hardening touches the network, show you any change to the shared VCN, subnet or security lists and wait for approval, because hermes-agent-vm-2 shares them.
- Oracle: confirm the 4 OCPU / 24 GB allowance in the console and consider a compartment quota policy that blocks paid shapes and extra volumes. I propose it; you approve it.

First-milestone acceptance, rewritten as pass or fail tests:
1. Numbers: a script extracts every numeric token from the report and checks each against the collected state JSON. Zero unmatched tokens.
2. Audit log: editing a line, deleting the tail, and rewriting from genesis are each detected, using the off-box head hash.
3. Grants: forged GREEN, YELLOW and RED grants, a lowered risk floor, and a replayed approval are each refused (one test each).
4. Kill switch: the task stops within 5 s at p95 over 20 runs, including with the node on the other side of Tailscale.
5. Classification: at least 10 GREEN requests classify GREEN and at least 10 non-GREEN requests do not.
6. Node status: heartbeat every 15 s; offline after 45 s with no heartbeat.
7. Cost: Cost Analysis recorded at $0 for the month; no cloud model call appears in the log.

Measured figures only count as results. Latency, RAM and accuracy numbers in these docs are UNTESTED until the benchmark runs. With 200 items, 1% is 2 items, so the false-allow result is reported with a confidence interval, and the set is enlarged for the high-risk class before any claim.

## Status (updated 2026-10-07)
- P0 done. P1 (kernel v0.1) and P2 (laptop node) done and tested; see the daily note for the numbers.
- Results against the milestone tests: 50 of 50 unit tests pass on Windows (Python 3.14) and on the VM (ARM Linux, Python 3.12); kill switch stops a worker in a median 0.34 s and at most 0.44 s over 20 runs through Tailscale; audit edit, deleted row, tail truncation and genesis rewrite are each detected; forged, replayed, expired and wrong-action approvals are each refused; the report's numbers are all found in the collected state (checked by a script); node goes offline after 45 s without a heartbeat (observed live at 58 s) and returns on restart.
- Known limit: a process running as the same OS user can edit the kernel files directly. The kernel data dir is a protected resource in policy and every change is in the audit log, but the real fix is to run agents as a different OS user than the kernel. That is required before the Hermes bots (P5).
- The kernel service listens only on the Tailscale address (port 8765). The VM firewall opens that port on the tailscale0 interface only. The shared Oracle network is unchanged.

## Status update: P3, P4 and P6 (observe-first)
- P3 done except provider keys: local lane measured, gateway, scrubber, no-paid rule, token cap.
- P4 done: see 03_DECISIONS.md. Not done: calibration (ECE), a fresh confirmation set for the risk rule, a 300-item high-risk set.
- P5 (Hermes bots and the OS-user split) waits for the user's provider key files and a bot token.
- P6 started: read-only UI snapshots (accessibility tree), UI policy (observe GREEN; launch, click, type need approval; password managers, wallets and banking apps always denied), an authenticated /request and /authorize path from the node to the kernel, a controller that asks first, observes before and after, verifies, never repeats a failed action and has an action budget, and a Windows backend with an app allowlist and values passed by environment variable. Tested with 94 tests (5 Windows-only skipped on the VM). Not verified live: real typing. The keyboard fallback refused to type because Notepad was not the foreground window, which is the intended safe behaviour; classic Notepad exposes its text area without a settable value, so it needs the keyboard rung. Browser control (DOM rung) is not started.

## Status update: P6 browser control (2026-10-07 evening)
Built and tested: policy for the browser tool (open and snapshot GREEN; click, type, select, press need approval; download and upload RED; internal addresses and sensitive sites DENY), Playwright browser layer with the filtering proxy, element actions that must name the element they expect, password fields never typed into, secret-looking text refused. 150 tests pass with Playwright (30 in the browser file), 150 with 24 skipped on the VM. Verified live end to end: the laptop browser asked the real kernel on the VM over Tailscale, opened a public page, was refused for the metadata address, a router address, the Tailscale server, PayPal and a file path, needed approval for a click, and the kernel refused messaging after browsing. Not verified: a real approved click on a real site, logins (deliberately unsupported), pages that need JavaScript-heavy interaction, QUIC and WebRTC behaviour, DNS behaviour on a hostile network.

### Risk update: Oracle idle reclaim, measured (2026-10-07)
The VM''s 5-minute maximum CPU over 8 hours had a median of 3.6% and memory about 5% (Oracle monitoring, read-only). Oracle''s rule for Always Free A1 instances is that they may be reclaimed if CPU (95th percentile), network and memory all stay under 20% for 7 days. At today''s load the VM would qualify. Whether this applies to a Pay As You Go tenancy is not confirmed. Options, cheapest honest one first: give the VM real recurring work (for example a nightly regression run of the decision benchmark on the VM''s own CPU, which the evaluation framework needs anyway); or accept the risk and keep the build reproducible from the repos (it is: kernel, services and units are in jarvis-hq). A deliberate ballast process is not planned. Decision for the user if the risk matters.
VM reboot test passed: after sudo reboot the VM answered over Tailscale in 26 s and the kernel, Telegram bridge and tailscaled services were all active, with the firewall rule intact. The laptop was not rebooted.
