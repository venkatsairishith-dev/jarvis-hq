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
