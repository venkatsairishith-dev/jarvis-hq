# P0b: 9router / OmniRoute check on hermes-agent-vm, 2026-10-22

Read-only, over SSH to hermes-agent-vm (100.125.91.51) with the repo's key path (key never read or printed). hermes-agent-vm-2 was not contacted. Nothing was stopped, changed, uninstalled or reconfigured. No secret values were printed (flag names and true/false only).

## Headline

- **9router is not installed on hermes-agent-vm.** Not as an npm global, not in docker (docker is not installed), not as a systemd unit, and a filesystem search for `9router*` / `.9router` found nothing. The CVE-2026-108593 / CVE-2026-49352 exposure for 9router does not apply to this VM. Its real location is still unknown (not on the laptop either, see P0 report).
- **What does run on port 20128 is OmniRoute 3.8.51** (`jarvis-omniroute.service`, user `jarvis-router`, node v24, started 2026-10-09). Mis-labelled as 9router in the cloud session's premise.
- **No sign of Hermes config tampering.**

## a. Install

- Unit: `jarvis-omniroute.service`, ExecStart `/home/jarvis-router/app/node_modules/.bin/omniroute serve`, package version 3.8.51, process started Fri 2026-10-09 13:51 UTC. Env file modified 2026-10-09 13:51 (mode 600).
- No 9router package or folder anywhere.

## b. Listening address and firewall

- `ss`: omniroute listens on **0.0.0.0:20128** (not loopback), plus 127.0.0.1:20131 and 127.0.0.1:20132. The unit description says "loopback only"; the actual bind is wider than described.
- Host firewall (iptables): INPUT policy ACCEPT but the chain ends with `REJECT`; 20128 is accepted **only on `tailscale0`**; SSH 22 is open to all; other accepted Tailscale ports 8765, 8790, 9119, 4750. nftables shows the same tailscale0-only accept for 20128. So 20128 is not reachable from the public internet through the host firewall. **Not checked:** the Oracle cloud security list (shared with hermes-agent-vm-2, needs your approval to even look at) and Tailscale ACLs.
- Recommendation (needs your OK): set the bind to 127.0.0.1 or the Tailscale address so it matches its own description.

## c. OmniRoute settings (names and status only)

| Setting | Status |
|---|---|
| REQUIRE_API_KEY | true |
| ALLOW_API_KEY_REVEAL | false |
| JWT_SECRET, API_KEY_SECRET | set (values not read) |
| INITIAL_PASSWORD | set; **not the default 123456** |
| CLOUD_URL / cloud sync, ENABLE_REQUEST_LOGS | not present in the env file |

Provider API keys stored in OmniRoute's data dir (DATA_DIR) were not listed; the owner would need to rotate those if OmniRoute were ever judged compromised. No evidence of that.

## d. Hermes config tamper check

- Only one Hermes config exists: `/home/jarvis-agent/.hermes/config.yaml` (owner jarvis-agent, mode 600, **modified 2026-10-10 21:57 UTC**; the OmniRoute install was 2026-10-09). There is no `~/.hermes` for user ubuntu.
- Top-level keys: agent, compression, gateway, mcp_servers, memory, model, platform_toolsets, telemetry, tools.
- Entries scanned for shell metacharacters (`; & | backtick $ < > newline backslash`): `model.base_url`, `mcp_servers.jarvis.command`, `mcp_servers.jarvis.args[0]`. **0 flagged**, no hook or shell keys.
- `hermes-settings` lines in the `jarvis-omniroute` journal: **0**. (That is the OmniRoute journal only; 9router's access log does not exist here.)

## e. OmniRoute

Running, version 3.8.51, port 20128 (0.0.0.0, Tailscale-only through the firewall), API key required. `requireLogin` was not read from its database (it lives in the app data dir, which I did not open).

## f. VM size

`nproc` 2; `free -g` total 11 GiB (5 used, 5 buff/cache, 5 available); matches the docs' 2 OCPU / 12 GB. **OCI CLI query not done**: `oci` is not on this laptop's PATH, so the shape from OCI itself is unconfirmed. Disk and shape decision (plan decision #2) still the owner's.

## Recommended next steps (none done; waiting for your OK)

1. Locate the 9router that the plan refers to (it is not here). If it exists elsewhere, stop and back it up before anything else.
2. Bind OmniRoute to loopback or the Tailscale IP (small config change, with a backup of its env file first).
3. Retire OmniRoute after the router port (plan P3), as already planned; until then it is acceptable behind Tailscale with the API key required.
4. No key rotation is needed on current evidence.
