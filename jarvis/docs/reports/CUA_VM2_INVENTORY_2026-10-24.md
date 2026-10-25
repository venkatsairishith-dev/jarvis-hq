# Oracle inventory for the CUA plan, step 1 (read-only, 2026-10-24)

Run from the laptop with the OCI CLI (`D:\jarvis\tools\oci-venv`, reinstalled today because the folder was gone), **list/get commands only**, in the one named
compartment. Nothing was created, changed, stopped or deleted. No login to either VM. Owner approved this read-only look in the laptop session on 2026-10-24
(the rule "never touch hermes-agent-vm-2" otherwise stands). IDs below are shortened to their last 6 characters.

## 1. Instances (exactly two; `hermes-agent-vm-2` is ONE instance)

| Name | Shape | OCPU / RAM | State | Public IP | Private IP | Boot volume | Created |
|---|---|---|---|---|---|---|---|
| hermes-agent-vm (...s7e2rq) | VM.Standard.A1.Flex | 2 / 12 GB | RUNNING | yes | yes | 100 GB, available | 2026-10-07 |
| hermes-agent-vm-2 (...a56vja) | VM.Standard.A1.Flex | **1 / 6 GB** | RUNNING | yes | yes | 100 GB, available | 2026-09-11 |

Region ap-hyderabad-1, availability domain AD-1, compartment `venkatsairishith17`.

## 2. Volumes, backups, IPs, network

- Boot volumes: 2 (100 GB each, both AVAILABLE and attached) = 200 GB. Block volumes: none. **Always Free storage is 200 GB in total, so it is fully used.**
- Volume backups: **0 of 5** (no volume backups, no boot volume backups).
- Reserved public IPs: none (both VMs use ephemeral public IPs; a rebuild in place keeps the instance and its VNIC).
- VNICs: one per instance, both primary and attached (`...is26fa` for hermes-agent-vm, `...k7goza` for hermes-agent-vm-2).
- Network: one VCN `hermes-vcn` (...o75b7q) and one public subnet `hermes-public-subnet` (...nxhbma), shared by both VMs.

## 3. What runs on hermes-agent-vm-2 today

**Not visible read-only, so not guessed.** OCI metadata shows only that it is RUNNING. It is not on the owner's tailnet (`tailscale status` lists only the
laptop, another Windows PC, `jarvis-vm-a` and a workstation), and no login was made. To learn its services, cron jobs and data directories the owner would have
to allow an SSH look (or say what is on it). The plan's rule stands: nothing is wiped until the owner has chosen to back up or copy what is there.

## 4. Shape check

- hermes-agent-vm-2 is an Ampere A1 slice with **1 OCPU and 6 GB**, not the 1 GB micro. XFCE + Firefox + cua-driver need about 2 GB, so **it can run them**
  (6 GB leaves room; 1 OCPU is slow for Firefox but workable for an agent desktop).
- Always Free Ampere A1 total for the tenancy: 4 OCPU / 24 GB. Used: 2 + 1 = 3 OCPU and 12 + 6 = 18 GB. **Headroom: 1 OCPU / 6 GB.** Resizing VM2 up to 2 OCPU /
  12 GB would fit (3 → 4 OCPU, 18 → 24 GB) but leaves nothing spare; that would be a separate, named decision.
- Storage is the tight limit: 200 of 200 GB used. A boot-volume backup before a wipe would count against the 5-backup allowance (0 used), not the 200 GB.

## 5. Cost

`scripts/oci_cost.py` for this month: every service line 0, **TOTAL cost this month: 0.0** (USD and SGD).

## What this does not cover

Nothing destructive was done and none is approved yet. Still needed from the owner, as in the plan: the choice between backup and copying data, his edit of
`protected.json`, and a yes naming the exact instance before any rebuild.

## Backup (step 2, 2026-10-24, owner approved this exact backup in the laptop session)

One full boot-volume backup of `hermes-agent-vm-2` (exactly one instance by that name, exactly one attached boot volume) was taken with the OCI CLI:

| Item | Value |
|---|---|
| Name | jarvis-vm2-before-cua-2026-10-24 (...n3hkna) |
| State | AVAILABLE (about 30 seconds after the request) |
| Type | FULL |
| Size | 100 GB (10 GB unique data stored) |
| Source boot volume | ...aclwqa (the boot volume of hermes-agent-vm-2; checked equal) |
| Backups now | **1 of 5** |
| Cost this month | 0.0 (`oci_cost.py`) |

Nothing was stopped, rebooted, detached, resized, wiped or deleted, and no security list, IP or `protected.json` was touched. The wipe and rebuild is a separate
step that needs the owner's yes naming the instance.

## Rebuild (step 3, 2026-10-24 21:14 to 21:15 laptop time; owner confirmed by name: "rebuild hermes-agent-vm-2")

`hermes-agent-vm-2` (...a56vja, exactly one match) was rebuilt **in place** with the OCI CLI (`compute instance update --source-details`: image
`Canonical-Ubuntu-24.04-aarch64-2026.09.18-0` (...3nmlua), boot volume 100 GB, old boot volume not preserved). Guards that passed first: one instance by that name,
one attached boot volume (...aclwqa), the backup `jarvis-vm2-before-cua-2026-10-24` (...n3hkna) AVAILABLE with that volume as its source.

| Check | Result |
|---|---|
| Instance state | STOPPING, STOPPED, RUNNING again after about 90 seconds |
| Shape | VM.Standard.A1.Flex, 1 OCPU / 6 GB, unchanged |
| VNIC | the same one; it still has a public IP |
| Boot volumes | new ...kvsrna (100 GB, AVAILABLE) for vm-2; hermes-agent-vm's ...idxkja (100 GB, AVAILABLE) untouched; the old ...aclwqa is TERMINATED. Still 2 x 100 GB in use |
| Backup | jarvis-vm2-before-cua-2026-10-24 (...n3hkna) AVAILABLE, count 1 of 5 |
| TCP port 22 from the laptop | reachable (plain connect, no login) |
| Cost this month | 0.0 (`oci_cost.py`) |

Not touched: hermes-agent-vm, any security list, VCN, subnet or IP. The owner deleted `D:\jarvis-secrets\protected.json` before the rebuild (the whole file, not only the
vm-2 entries), so the protection of every resource it listed is off until it is recreated; hermes-agent-vm's entries should be restored. No login to the VM and nothing
installed yet; the CUA install is the next step.

## Free-limit check (2026-10-24, read-only: limits service, usage-api, SSH to hermes-agent-vm for four numbers)

| Number | Value |
|---|---|
| A1 core limit in the tenancy (AD-1) | 250, used 3, available 247 |
| A1 memory limit (GB) | 1666, used 18, available 1648 |
| Usage now | hermes-agent-vm 2 OCPU / 12 GB + hermes-agent-vm-2 1 OCPU / 6 GB = 3 OCPU / 18 GB |
| This month so far (usage-api) | Standard - A1: 1542.4 OCPU-hours; A1 memory: 9254.6 GB-hours; **every compute line costs 0**; month total 0.0 (`oci_cost.py` also 0.0) |

What it means: the limits service shows the account's Pay-As-You-Go ceilings (250 / 1666), not the Always Free pool, so it cannot settle the 4 OCPU / 24 GB
versus 2 / 12 question by itself. The evidence that does: the Always Free allowance is counted in hours (3,000 OCPU-hours and 18,000 GB-hours a month, which
is 4 OCPU / 24 GB around the clock), and 3 OCPU / 18 GB all month is 2,232 OCPU-hours and 13,392 GB-hours, inside it, and Oracle billed 0 for the A1 lines on
the servers' real running time. Nothing shows a 2 / 12 pool. The current 3 / 18 is not above the limit found. Account type (Always Free only versus upgraded) was not
available from a read-only call and is not claimed.

### Could hermes-agent-vm run on 1 OCPU / 6 GB? (measured over SSH, names and sizes only)

- Memory: 11,927 MB total, **5,731 MB used**, 479 MB free, 5,944 MB buffers/cache, 6,195 MB available, no swap.
- Biggest process: `llama-server` at about 6.4 GB resident (a second, small one 89 MB); `omniroute` 516 MB; `hermes` 307 MB and 173 MB; journal 131 MB; others under 125 MB.
- Load average 0.00; uptime 16 days; disk 13 GB used of 96 GB; 41 services running.
- Judgement: **it does not fit in 6 GB with a safety margin.** Used memory is already 5.7 GB, with the big model server on its own near 6 GB resident. Shrinking would
  need the model server to be smaller or moved, or it would run out of memory. The CPU would be fine (load 0.00), memory is the limit.

### Resize of hermes-agent-vm to 1 OCPU / 6 GB: steps and risks (written down, NOT run, needs the owner's yes naming hermes-agent-vm)

1. Decide what happens to `llama-server` first (stop it, use a smaller model, or run it elsewhere); without that the VM is out of memory after the resize.
2. Take a full boot-volume backup of hermes-agent-vm (uses 1 more of the 5 free backups: 2 of 5), wait for AVAILABLE.
3. Stop the services in order: jarvis-llm-proxy, jarvis-kernel (audit chain stays valid), hermes agents, the Telegram bot, omniroute, llama-server; note their states.
4. `oci compute instance update --instance-id <hermes-agent-vm> --shape-config '{"ocpus":1,"memoryInGBs":6}'`: for the A1 flex shape this restarts the instance
   (a few minutes of downtime for the main server, the kernel and every agent).
5. After RUNNING: check shape, public IP unchanged, Tailscale up, start services in reverse order, `live_check`, audit chain verify.
6. Blockers and risks: the `protected.json` entry for hermes-agent-vm (the file was deleted on 2026-10-24; if it is recreated, the entry must be lifted for this one
   step); out-of-memory kills if step 1 is skipped; downtime of the kernel; resizing back up needs free A1 capacity in the region.

Recommendation: **do not shrink hermes-agent-vm.** The tenancy is inside the free hours at 3 / 18, it is billed 0, and the main server needs its memory. If the owner
still wants it, do step 1 first and take the backup before anything else.

### Free-limit check, addendum: hours used this month (read-only, 2026-10-24 16:26 UTC)

usage-api, this month to date (568.4 of 744 hours elapsed; 175.6 hours left):

| | Used so far | Cost |
|---|---|---|
| A1 OCPU hours | **1,546.2** | 0.0 (SGD line 0.0, USD line 0.0) |
| A1 memory GB hours | **9,277.1** | 0.0 |

- Oracle's Always Free reference page (read today) says all tenancies get the first **1,500 OCPU hours and 9,000 GB hours** a month free on VM.Standard.A1.Flex, "for Always
  Free tenancies equivalent to 2 OCPUs and 12 GB", and that an upgraded account is charged only for usage above that. Oracle's Ampere pricing snippet (search result,
  page itself returned 403) says 3,000 OCPU hours and 18,000 GB hours. The two Oracle pages disagree; I could not open the price list, so the dollar rates below are
  NOT verified.
- **The 1,500 / 9,000 pool is already used up** (1,546 and 9,277, so 46 OCPU-hours and 277 GB-hours over). The cloud estimate of about 1,400 / 8,500 was a little low. Cost
  still shows 0.0, which fits either a 3,000 / 18,000 pool or billing that is not shown yet; I cannot tell which from the CLI.
- Account type: `docs/05_OPERATIONS.md` calls it a Pay-As-You-Go account used only inside Always Free (so upgraded). The limits service (250 cores / 1,666 GB) agrees with
  an upgraded tenancy. I found no read-only call that states the type, and I did not guess what Oracle does for a non-upgraded tenancy over its limit.

Projection to 31 October 23:59 UTC if nothing changes (3 OCPU / 18 GB = 3 and 18 per hour):

| Case | OCPU hours at month end | GB hours at month end | Over 1,500 / 9,000 by |
|---|---|---|---|
| (a) stay at 3 / 18 | 1,546 + 527 = about 2,073 | 9,277 + 3,161 = about 12,438 | 573 OCPU-h, 3,438 GB-h |
| (b) hermes-agent-vm to 1 / 6 now (total 2 / 12) | 1,546 + 351 = about 1,897 | 9,277 + 2,107 = about 11,384 | 397 OCPU-h, 2,384 GB-h |

Cost estimate (ESTIMATE ONLY, rates not verified: the only figures found are an old Arm Developer post, 2021, $0.01 per OCPU-hour and $0.0015 per GB-hour): (a) about
$10.9, (b) about $7.5, and even a resize today does not bring October back under 1,500 / 9,000. If the real free pool is 3,000 / 18,000 the cost for both cases is 0.
Not a quote; Oracle's invoice decides.

Ways to stop adding hours without resizing (each needs the owner's yes naming the instance): stop hermes-agent-vm-2 until needed (its rebuild is done; a stopped
instance uses no OCPU hours), or resize one of them. The resize plan for hermes-agent-vm is in the section above; it needs the memory step first.

Recommendation: nothing has been billed, so do not panic, but because the account is upgraded and the pool is used, treat further A1 hours as potentially billable: the
cheapest safe step today is to **stop hermes-agent-vm-2** (it has nothing running yet) until the CUA install is ready, and plan the 2 / 12 total for November 1 at
the latest (it must include solving the main server's 5.7 GB memory use). That is the owner's decision.

## Resize to 2 / 12 (2026-10-24 evening, owner approved each resize by name in the laptop session)

| Step | Result |
|---|---|
| Split | hermes-agent-vm 1 OCPU / 8 GB, hermes-agent-vm-2 1 OCPU / 4 GB (total 2 OCPU / 12 GB, the free-only size) |
| hermes-agent-vm-2 (...a56vja) | 6 GB to 4 GB, about 2 minutes (22:00), RUNNING, same VNIC and public IP, port 22 reachable |
| Backup of the main server first | jarvis-main-before-resize-2026-10-24 (...qz72rq), FULL, AVAILABLE, source ...idxkja; backups now **2 of 5** |
| Services stopped in order (all were enabled and active) | monit, jarvis-llm-proxy, jarvis-kernel, jarvis-hermes-gateway, jarvis-hermes-serve, jarvis-omniroute, jarvis-embed, jarvis-llm, jarvis-smokescreen; audit chain verified first (42,635 events ok) |
| hermes-agent-vm (...s7e2rq) | 2 OCPU / 12 GB to 1 OCPU / 8 GB, 22:02 to 22:04 (about 2 minutes), same VNIC and public IP, Tailscale up |
| After the restart | all ten services active again by themselves (enabled units), audit chain verifies (42,638 events), `live_check.py` 57 of 57 |
| Memory afterwards | 7,913 MB total; right after boot 716 MB used; with the model loaded 4,520 MB used, 3,393 MB available, `llama-server` about 5.3 GB resident |
| Limits service | A1 cores used **2** (248 available), memory used **12 GB** (1,654 available) |
| usage-api COST | A1 lines still the month's earlier quantities (1,546 OCPU-h, 9,277 GB-h), amounts 0.0 SGD and 0.0 USD, total 0; the data lags, look again tomorrow for any October overage |

Downtime of the main server (kernel, Telegram bot, Hermes): from the first service stop at 22:02:11 until the instance was back at about 22:04:30 and all services active: about 3 minutes.
Unhealthy afterwards: nothing found. Nothing else on Oracle was changed (no security list, IP, VCN or subnet).

`protected.json`: the owner deleted the file. A DRAFT with the ids (instances, VNICs, current boot volumes, VCN, subnet, default security list, last 6 characters shown here:
s7e2rq, is26fa, idxkja, a56vja, k7goza, kvsrna, o75b7q, nxhbma, xmhemq) is at `D:\jarvis\cache\protected.draft.json`. It is a draft in my own guess of the structure; the owner reviews it and moves it
into `D:\jarvis-secrets` himself.

## Agent PC install on hermes-agent-vm-2 (2026-10-24 evening; owner said in the laptop session: "reset whole server new one" and confirmed a rebuild with a new key)

**Second rebuild (22:22 to 22:24):** the SSH key the owner thought was on the VM was not known to the session, so hermes-agent-vm-2 (...a56vja, 1 OCPU / 4 GB, empty) was rebuilt in
place again with a NEW key pair made on the laptop (private key in `D:\jarvis\keys-vm2`, ACL: the owner and SYSTEM only; only the public key went into the VM's metadata,
appended to the existing key). Same image, 100 GB, no preserved old volume (new boot volume ...uu6crq; ...kvsrna terminated), same shape, VNIC and public IP, port 22 reachable.
The VM's host key changed with each reinstall: the stale `known_hosts` line for this IP was removed, the new ED25519 fingerprint
`SHA256:rrtQzyvrq3SWX4XTZhnkMw96A2Pb2nPZHt17blPYWTU` was shown to the owner and accepted on first connect. Backups: still 2 of 5. hermes-agent-vm untouched.

**Read-only look:** aarch64, Ubuntu 24.04.5 LTS, disk 96 GB (2 GB used), RAM 3,897 MB, user ubuntu with sudo.

**Provisioning:** only the 8 lab files were copied to /tmp/jarvis-lab (no key, token or config). `provision_vm2.sh --plan` was read, then run for real
(the first attempt stopped on a held apt lock from the first boot; the second ran clean). The Mozilla key fingerprint check passed (script step 2 completed).

| Check | Result |
|---|---|
| jarvis-desktop | active |
| jarvis-lab-control | **crash-looping**: `lab_control.py` line 410 `grp.getgrnam("hub")` fails with KeyError, the group `hub` does not exist; `provision_vm2.sh` does not create it (gap in the script, not fixed here) |
| Listening sockets | 22 on all addresses; VNC 5901 on 127.0.0.1 and ::1 only; **111 (rpcbind) on 0.0.0.0** (Ubuntu default, blocked by the firewall; needs `systemctl disable --now rpcbind rpcbind.socket` or accept) |
| ufw | active, 22/tcp only (IPv4 and IPv6), default deny incoming |
| Users agent, jarvis-sandbox, jarvis-verify | exist, each its own group, no sudo |
| Firefox | Mozilla build 158.0.1 from /usr/lib/firefox (apt repo), no snap installed |
| check_lab_deploy.py | RESULT: OK. WARN: sandbox user has no login shell; runner token not found (expected, not created); verifier tools not sandboxed (JARVIS_LAB_VERIFIER_TOOLS unset); sandbox user can create user namespaces |
| Free memory after the install | 3,897 MB total, 682 MB used, 1,081 MB free, 3,214 MB available |

**cua-driver:** not installed (as instructed). The WSL box that had it is gone (`wsl -l` shows no distributions), so how it was installed could not be read; the architecture
doc says "pinned through Hermes's package manager". A Linux arm64 build of 0.21.0 was not checked online here.

**By hand / next:** (1) fix the `hub` group gap (`groupadd hub` before starting jarvis-lab-control, decide who is in it) and decide about rpcbind; (2) the owner runs `sudo tailscale up` on
the VM and logs in; (3) check the cua-driver arm64 build, store the runner token.

### Fixed provision script applied (2026-10-24 17:19 UTC)

Cloud fix `d4736e2` (system group and account `hub`, no login, no sudo; rpcbind disabled) was copied alone to the VM (`provision_vm2.sh` only; the other lab files were already in
/opt/jarvis-hub), `--plan` was read (it showed `groupadd --system hub`, `useradd ... --shell /usr/sbin/nologin hub`, `systemctl disable --now rpcbind.socket / rpcbind.service`), then run
for real. It is idempotent and finished `RESULT: OK`.

| Check | Result |
|---|---|
| jarvis-lab-control, jarvis-desktop | both **active**; lab-control up since 17:19:14, `NRestarts` 228 before and 228 after 45 seconds (that number is left from the earlier crash loop; it no longer grows) |
| Listening | 22 on all addresses (IPv4 and IPv6); VNC 5901 on 127.0.0.1 and ::1 only; port 111 **gone** |
| `hub` | uid 997, gid 988, shell /usr/sbin/nologin, not allowed to run sudo |
| Lab socket | `/run/jarvis-lab/control.sock`, owner root, group hub, mode `srw-rw----` (0660) |
| check_lab_deploy.py | RESULT: OK; the same four WARN lines as before (sandbox user has no login shell, `/etc/jarvis-lab/runner.token` not found, verifier tools not sandboxed, sandbox user can create user namespaces); no FAIL |
| Free memory | 3,897 MB total, 640 MB used, 1,082 MB free, 3,256 MB available |

Not done on purpose: cua-driver (the vendor installer is a pipe to a shell; plan in `01_RESEARCH_AUDIT.md`), Tailscale (owner runs `sudo tailscale up`), the runner token, anything on Oracle or hermes-agent-vm.

### Tailscale on hermes-agent-vm-2 (2026-10-24, owner logged in himself)

`provision_vm2_tailscale.sh` (cloud commit eba8883 or newer) was copied alone, its `--plan` read (Tailscale's own signed repo `pkgs.tailscale.com/stable/ubuntu noble`, the firewall unit, nothing else), then run.
Tailscale 1.104.2 installed from that repo. `sudo tailscale up --hostname=jarvis-vm2 --shields-up --accept-dns=false --accept-routes=false` (no --ssh, no auth key) printed a login link that was shown only to
the owner in the laptop session (not written to any file; the temporary log files on the VM were deleted). The first link failed on the VM side (`http 410: auth path not found` although the
browser said success); a second command and link worked. A stray offline `jarvis-vm2` entry may exist in the Tailscale admin console from the first try.

| Check | Result |
|---|---|
| `tailscale status` | jarvis-vm2 online in the owner's tailnet (same tailnet as jarvis-vm-a, the main server) |
| `tailscale ip -4` | 100.79.254.82 |
| tailscaled, jarvis-agent-firewall | both active |
| Ping to the main server (100.125.91.51) as user ubuntu | works (61 ms) |
| Same ping as user `agent` | **fails** (blocked by the firewall unit) |
| shields-up | on (no other tailnet device can open connections to the VM) |

Owner does in the Tailscale admin console: turn key expiry **off** for jarvis-vm2 (a server must not be logged out every few months); optionally add a tag and an ACL with no rule that has the VM as the source; remove a duplicate offline jarvis-vm2 entry if there is one.
Left: cua-driver (vendor installer is a pipe to a shell, not run; plan in `01_RESEARCH_AUDIT.md`), the runner token.

### cua-driver on hermes-agent-vm-2 (2026-10-24, owner said yes to this exact run after reading the plan)

`provision_vm2_cua.sh` (cloud commit 3782a46) was copied alone; `--plan` read (hosts null route for the PostHog hosts, libxi6 and at-spi2-core, pinned linux-arm64 0.34.0 tarball
checked against the pinned hash AND the vendor's SHA256SUMS, telemetry disabled as `agent`, service installed but not enabled, doctor read-only), then run. Exit 0, both hash checks passed.
The local run of `jarvis/lab/tests/test_provision_vm2.py` failed 6 of 13 on the laptop only because `bash` there resolves to the empty WSL (no distribution installed), not because of the scripts.

| Check | Result |
|---|---|
| `cua-driver telemetry status` | Telemetry: disabled (source: persisted); installation ID not created |
| `cua-driver doctor` (as agent, DISPLAY=:1) | all [ok]: binary cua-driver 0.34.0 (aarch64-linux), install dir /opt/cua-driver/0.34.0, home /home/agent/.cua-driver, telemetry disabled, display server X11, X11 connection (4 visible top-level windows), AT-SPI reachable |
| Does the arm64 build run | **yes** (vendor docs only mention x86_64; the binary runs and the doctor passes) |
| `systemctl is-enabled cua-driver` | `static` (the unit has no [Install] section: it cannot be enabled with `systemctl enable`; it is not started at boot) |
| `systemctl is-active cua-driver` | inactive |
| /etc/hosts | one marked block null-routing the 5 PostHog hosts (0.0.0.0) |
| Free memory | 3,897 MB total, 689 MB used, 3,207 MB available |

Not done on purpose: the service was NOT enabled or started; the owner decides after reading doctor. Next: how the lab runs it for the Agent PC (a start line instead of `enable`), the runner token, the owner's tag/ACL and key-expiry choices in Tailscale.

### cua-driver service started on hermes-agent-vm-2 (2026-10-24 17:51 UTC, owner said yes in the laptop session)

Cloud commit 71881e5 added an `[Install]` section; the idempotent script was re-run (hashes re-checked, hosts block unchanged, unit rewritten): `is-enabled` read **disabled** (no longer `static`). The unit runs as `agent`:
`cua-driver telemetry disable` before start, then `cua-driver serve --permission-mode standard --no-overlay`, after `jarvis-desktop.service`, `Restart=on-failure`. Then `systemctl enable --now cua-driver`.

| Check | Result |
|---|---|
| is-enabled / is-active | enabled / active; NRestarts 0 |
| Listeners | no new TCP listener (diff of `ss -ltn` before and after is empty); the driver listens on a unix socket only: `/home/agent/.cache/cua-driver/cua-driver.sock` |
| Connections | no TCP connection by the driver at the time of the check; no connection to PostHog |
| Telemetry | `Telemetry: disabled (source: persisted)` |
| Other services | jarvis-lab-control, jarvis-desktop, tailscaled, jarvis-agent-firewall all active |
| Memory | 3,897 MB total, 676 MB used, 3,220 MB available |

Note for the owner: on start the daemon printed "cua-driver v0.38.0 is available (you have v0.34.0). Update with: cua-driver update". So it does look for newer versions (an outbound request to the vendor at start); no
update was run and the pinned 0.34.0 stays. If no outbound check is wanted, the allowed outbound hosts for the agent user need to exclude the vendor's update URL or the egress list must be checked.

### Lab runner token on hermes-agent-vm-2 (2026-10-25, owner lifted the "never copy from the secrets folder" rule for this one file in the laptop session)

The owner issued the token on the laptop (kernel token, lane `runner`, 43 bytes). The session checked only that the file exists and its size, never opened or printed it. It was transferred by path with scp to a temp file on the VM, installed
as `/etc/jarvis-lab/runner.token` (root:root, mode 600, 43 bytes, same size) in `/etc/jarvis-lab` (root:root, 700), and the temp copy was shredded. No token text is in any file, log or report.

| Check | Result |
|---|---|
| `check_lab_deploy.py` | RESULT: OK; the runner-token WARN is gone (now "OK ... root 600"); the other three WARN lines are unchanged (sandbox no login shell, verifier tools not sandboxed, user namespaces) |
| `jarvis-lab-runner` | disabled and inactive; not started or enabled (the check does not require it; the owner decides) |
| Kernel proxy on the main server | `jarvis-llm-proxy` active, listening on 100.125.91.51:8790 (tailnet address) and 127.0.0.1:8790; VM2 (user ubuntu) can open TCP 8790 and gets HTTP 404 on `/` (a live server, expected) |
| Lock-down | the `agent` account is still blocked from reaching the tailnet (8790 closed for it) |

Next (owner decision): start the lab runner on VM2 and see it report to the kernel; set `JARVIS_LAB_VERIFIER_TOOLS` only after the agent CLI's tool execution is confirmed to run as another user.

### Update check off, and the trivial lab task (2026-10-25, laptop session)

**A. Update check.** Cloud commit 5c2c66d (`provision_vm2_cua.sh`) was copied alone, `--plan` read, then run (idempotent). Result on cua-driver **0.34.0**:

| Check | Result |
|---|---|
| `cua-driver config set update_check_enabled false` | **does not exist in 0.34**: "Unknown config key: update_check_enabled"; the available keys are capture_mode, max_image_dimension, version, platform, agent_cursor.enabled, cursor.motion.* (the script reports this as not fatal) |
| Unit environment `CUA_DRIVER_RS_UPDATE_CHECK=false` | set in the unit (`systemctl show -p Environment`) |
| After `systemctl restart cua-driver` | active, NRestarts 0, "listening on /home/agent/.cache/cua-driver/cua-driver.sock"; the "v0.38.0 is available" banner is **gone** (1 line in the 17:51 start, 0 lines since the restart at 19:57:01), so the environment variable works on 0.34 even though the config key does not |
| Connections | no TCP connection by cua-driver (`ss -tnp`), telemetry still disabled, jarvis-lab-control active |

**B. One trivial lab task: not run; what is missing.** The owner said yes (laptop session). Before starting anything on VM2 the session looked for the simple documented path and found none that is safe to use as is: `demo.py` does not use the kernel (it runs agents locally in the WSL-era box); tasks reach the lab only through an
agent's `lab_work` action (agent token, lane private or public) or the owner plane's `agents/test` and `project_fix`; there is no single CLI like "submit this lab task". Using it would mean minting an agent token on the main server or driving the
owner plane by hand, which is improvising on the main server. So `jarvis-lab-runner` was **not** started (still disabled and inactive), no task was created, and the main server was not touched.
What would settle it: a small documented command (for example `python -m jarvis_kernel lab-task --text ... --criteria ...` or an owner-plane route that creates a lab task with a runner-lane job), or the owner choosing the agent-definition test (`POST owner/agents/test`) as the trivial task.

### Lab smoke test via `agents/test`: stopped at the runtime check (2026-10-25, laptop session)

Owner chose the existing owner route `POST owner/agents/test` as the trivial task. Before creating the throw-away agent or starting anything, the runtime it would name was checked on hermes-agent-vm-2 (read-only, as root):

| Check | Result |
|---|---|
| `hermes`, `opencode`, `goose` on PATH or in `/home/agent/.local/bin` | **none installed** (`/home/agent/.local/bin` does not exist) |
| `/home/agent/.hermes` (agent CLI config and model credential) | does not exist |
| What the runner starts | `lab_runner.py`: `LOCAL_BIN/hermes chat ...`, `opencode run ...` (LOCAL_BIN = `~/.local/bin` of the agent user); the agent definition's runtime must be one of those |

So no agent definition `lab-smoke-test` was created, `jarvis-lab-runner` was not started (still disabled and inactive), no task was made, and nothing on the main server was called. Installing an agent CLI means a model credential on the VM
(the plan says the VM gets no model key, Telegram token or kernel database) and the lab Hermes install, which is a separate owner decision: which agent CLI, from where (pinned), and which model access (through the privacy gateway or the kernel's llm-proxy over the tailnet, never a key file on the VM).

### Lab agent installed on hermes-agent-vm-2 (2026-10-25, owner said yes in the laptop session after reading the plan)

`provision_vm2_agent.sh` (pinned: uv 0.13.5, Hermes Agent v0.21.6 at commit 818c13be, Python 3.14.7, three hash-pinned patched wheels) ran clean on the second attempt. First attempt: `uv sync --no-editable` failed because Hermes refuses a built wheel
("Building wheels or sdists for hermes-agent is not supported"); the main server also uses an editable install, so the flag was removed (the source is root-owned, so the agent still cannot change it). Everything is idempotent.

| Check | Result |
|---|---|
| `hermes --version` as `agent` and as `jarvis-verify` | Hermes Agent (2026.9.24), install directory /opt/hermes-agent/src, method git |
| Agent writes to the install | refused (Permission denied on /opt/hermes-agent/src and /venv); /opt/hermes-agent is root:root 755 |
| Patched wheels | httpcore2 2.12.0, httpx2 2.12.0, PyJWT 2.13.0 replaced by 2.14.0 (as on the main server) |
| Configs | `~/.hermes/config.yaml` for agent and jarvis-verify, mode 0600 in their own homes; model base URL http://127.0.0.1:18790/v1, dummy key `jarvis-local`, no real key on the VM |
| Model forwarder | installed (`/opt/jarvis-hub/model_forwarder.py`, unit file), **not enabled or started**: no `/etc/jarvis-lab/model.token` yet |
| Listeners | unchanged (22, VNC on loopback, tailscale's own ports); nothing on 18790 yet |
| Services | jarvis-lab-control, jarvis-desktop, cua-driver active; free memory 3,180 MB available of 3,897 MB |

Left: the owner makes the kernel token (public lane, like the lab agents) and it is copied by path to /etc/jarvis-lab/model.token (root 600); then `systemctl enable --now jarvis-model-forwarder`, the runner start and the smoke test (`agents/test`).

### Model token on hermes-agent-vm-2 and the smoke test: stopped at a 401 (2026-10-25, laptop session)

The owner ran `python -m jarvis_kernel agent-token lab-vm2-agent --lane public --out D:\jarvis-secrets\lab-vm2-agent.token` on the laptop and said yes (laptop session) to copying that one file. It was copied by path only (43 bytes, never opened or printed) and installed as `/etc/jarvis-lab/model.token`
(root:root 600; temp copy shredded); `jarvis-model-forwarder` was enabled and started.

| Check | Result |
|---|---|
| Forwarder | active, listening on 127.0.0.1:18790 only; no other new port |
| Agent user reads the token | no ("not readable") |
| `GET /v1/other`, `GET /v1/jarvis/owner/state` through the forwarder | 404 both (path allowlist works) |
| `GET /v1/models` through the forwarder | **401** from the kernel: the token is not accepted |

Cause (read-only check on the main server): the kernel's token table in `~/.jarvis-kernel/state.db` on hermes-agent-vm lists these names and lanes: lab (public), hub (dashboard), lab-runner (runner), hermes (private), skill-operator (private), memtest (public), console (owner), approval-test (public). **There is no `lab-vm2-agent`.**
`python -m jarvis_kernel agent-token` writes into the data directory of the machine it runs on (default `~/.jarvis-kernel`, or `$JARVIS_DATA`); run on the laptop it made a token in the laptop's own, separate kernel database, which the main server does not know.
The same is almost certainly true of the `lab-runner` token issued on the laptop earlier (`check_lab_deploy.py` only checks the file's owner and mode, not that the kernel accepts the token).
Not done, to avoid improvising on the main server: minting tokens there. The smoke test (agent definition, runner start, `agents/test`) was **not run**; the runner stays disabled and inactive.

Fix (the owner's yes needed for the main server): run the same command on hermes-agent-vm itself (`python3 -m jarvis_kernel agent-token lab-vm2-agent --lane public --out <path on the server>`, and for the runner `lab-runner` already exists there, so its token must be re-issued or the existing file reused),
then move the token files to VM2 without printing them (for example `ssh server 'cat file' | ssh vm2 'sudo tee /etc/jarvis-lab/model.token'` through a pipe, never to disk on the laptop), root 600.
The useless model.token on VM2 is harmless (the kernel answers 401) and is replaced by the fix.
