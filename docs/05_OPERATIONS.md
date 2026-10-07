# 05 Operations: Oracle, cost, storage, secrets, hand-over

## Oracle
- Region ap-hyderabad-1, one compartment (name in keys.json), Pay As You Go account used only inside Always Free.
- Free allowance (Oracle docs and community thread, 2026-10-07): 3,000 OCPU-hours and 18,000 GB-hours of Ampere A1 per month on a paid tenancy (4 OCPU / 24 GB), 200 GB block plus boot storage in total, 5 volume backups, 2 VCNs on Always Free tenancies, 10 TB outbound per month. Not confirmed in the console for this tenancy.
- Current state (2026-10-07): hermes-agent-vm (2 OCPU, 12 GB, 100 GB boot, Ubuntu 24.04 aarch64, public IP in the existing public subnet, new SSH key) is the main server. **hermes-agent-vm-2 is protected: never touch it, its boot volume, its network or its IPs.** Network hermes-vcn is shared, so it is never deleted. Storage is 200 of 200 GB.
- Budget `jarvis-1usd` with alert rule `spend-reached-1usd` at $1 actual spend. A budget alert warns; it does not cap spend.
- Use only Always Free-eligible resources. No paid shapes, GPUs or paid services. If something needs a paid resource, stop and tell you.
- Out-of-capacity: report it, never retry in a loop.
- Idle reclaim: A1 instances under 20% CPU, network and memory for 7 days may be reclaimed. Keep real work on the server.

### Oracle delete checklist (reusable)
1. List instances, boot volumes, block volumes, networks, reserved IPs, backups with names and sizes.
2. Offer a backup and wait for the answer.
3. Get a yes naming the exact resource.
4. Delete one resource at a time; refuse unless exactly one exact-name match.
5. Re-list and show the result. Never touch anything outside the named compartment.

### Cost check (reusable)
After each phase: Cost Analysis for the month, per service, must be $0. Billing data lags a day. Record the result in the daily note.

## Storage (Windows)
D: is NTFS, 75 GB, for every local AI file. C: has about 53 GB free and stays clean. Before and after every big download, record free space. Redirect before downloading: `OLLAMA_MODELS`, `HF_HOME`, pip cache, npm cache, TEMP. Installers and logs that land on C: are noted and trimmed. The OCI CLI lives in D:\jarvis\tools\oci-venv (0.8 GB).

## Secrets
- Windows: D:\jarvis-secrets, locked to the user (keys.json, oci\, ssh\). macOS: ~/.jarvis/secrets.env, mode 600.
- I use them through tools (OCI CLI config, ssh) and never open them to read values. Never print, log, commit or put them in a model context.
- If a secret ever shows in output or a transcript: rotate it, then tell you.

## Windows version
Windows 10 Pro 22H2 build 19045 under Boot Camp. Mainstream support ended 14 Oct 2025. Consumer ESU runs to 12 Oct 2027 if enrolled with a Microsoft administrator account (free with settings sync; otherwise $30 or 1,000 Rewards points, which is not free so do not pay). Source: Microsoft's ESU page.

## Hand-over
- End of each phase and before a reboot to macOS: vault/daily/<date>.md (done, checked, open, next).
- D:\jarvis\CLAUDE.md stays short: the Part 1 rules plus links to the memory map and the newest daily note.
- Reusable procedures live as project skills in D:\jarvis\.claude\skills, with a copy in jarvis-hq: cost-check, phase-gate, vault-write, oracle-delete.
- Repos: github.com/venkatsairishith-dev/jarvis-hq (docs, skills) and jarvis-vault (memory). Local: D:\jarvis\hq (jarvis-hq) and D:\jarvis\vault.
