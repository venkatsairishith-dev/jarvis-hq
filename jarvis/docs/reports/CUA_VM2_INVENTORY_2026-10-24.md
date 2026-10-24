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
