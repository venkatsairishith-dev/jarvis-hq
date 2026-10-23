# Kernel deploy to the Oracle server: what is waiting, and how (2026-10-22)

The kernel on the Oracle server still runs the 2026-10-16 deploy. Everything below has been built and tested here but is **not
deployed**: nobody has touched the Oracle VM. The deploy is the owner's step (it needs his PIN/console and a moment when no lab job
runs). This page is the checklist; the procedure itself is "Deploying the kernel" in `05_OPERATIONS.md`.

## What changes (jarvis/kernel/jarvis_kernel, 22 files, +4,173 −228 against the commit `88ec49a2` that main points to)

| Area | Files | What it is |
|---|---|---|
| Permissions and approvals | `policy.py` (+838), `owner.py` (+306), `store.py` (+190), `idempotency.py`, `jobs.py` | The hardening passes of 2026-10-18 to 2026-10-20: stricter classification, approval cards that cannot be approved when truncated, duplicate-run protection. Reports: `REMEDIATION_*`, `COMPLETION_REMEDIATION_REPORT_2026-10-20.md`. |
| Model route | `llm_proxy.py` (+486), `agents.py`, `scrub.py`, `connector_service.py` | Provider fallback, cooldowns, token sizing, scrubber fixes. The routing rules (lanes, paid refusal) are unchanged. |
| Memory and tasks | `memory.py` (+246), `tasks.py` (new), `automations.py`, `nodes.py` | Task records and memory handling. |
| Privacy retention | `retention.py` (new), `audit.py`, `store.py` | Removes old private text from the kernel's stores (see "Read this first"). |
| Lab / caretaker | `projectfix.py`, `visible.py`, `gitleaks_gate.py` (new) | Lab agent verification and the secret-scan gate. |
| Model selector | `model_select.py`, `capability_probe.py` (new) | Recommend-only; no route changes; safe to ship. |
| Kernel database | `store.py`, `cli.py` | `secure_delete=ON` on every connection; the database is closed when the CLI returns. |

Test status on this code (neko-test image, Linux, network off): kernel suite **1,056 OK (27 skipped)**; console 251 passed; main_agent
526; desktop 466; shared plugins 175; bridge 31; research 128. On Windows 24 kernel tests fail for known Windows-only reasons
(file locking, `pwd`, file modes); the server is Linux, so those do not apply there.

## Read this first: two effects that cannot be undone

1. **The first start purges old private text.** `retention.py` runs at kernel start. Approval cards older than 24 h after use, finished
   jobs older than 14 days, tasks and commands older than 30 days lose their text (stubs with digests stay; audit rows are never
   touched, the hash chain stays valid). Settings: `python3 -m jarvis_kernel retention --show`; turn it off before deploying with
   `retention --off` (or `JARVIS_RETENTION=off`) if you want to look first; `--on` later.
2. **The first retention run also runs `VACUUM` once** on `state.db` (clears old text left in free space). It rewrites the file: expect a
   few seconds of delay at that start, and make sure the disk has room for a second copy of `state.db`.

The new tables are additive; an older kernel ignores them (rollback below).

## Before you deploy
- [ ] No lab job is running (the doc: a runner report sent during the restart is lost).
- [ ] Copy of the live database: `cp ~/.jarvis-kernel/state.db ~/downloads/state.db.<date>` (or wherever `JARVIS_DATA` points).
- [ ] Run the kernel suite once more from the exact tree you pack: `cd jarvis/kernel && python -B -m unittest discover -s tests` (about 11 minutes; expect OK, 27 skipped).
- [ ] Decide on retention (above).

## Deploy
Follow "Deploying the kernel" in `05_OPERATIONS.md` (pack `jarvis/kernel`, unpack on the server, move the live kernel to
`kernel.old.<ts>`, run the tests in place, restart only `jarvis-llm-proxy`, `jarvis-kernel`, `jarvis-telegram` (Telegram stays off:
do not enable it)). If the tests fail the script restores the old kernel by itself.

## After you deploy (5 minutes)
- [ ] `python3 -m jarvis_kernel audit` prints the chain head and verifies (compare with the last anchor).
- [ ] The console (laptop, port 8799) shows "kernel reachable" and `live_check.py` passes the kernel items.
- [ ] A harmless PC task from the chat still works ("open Calculator").
- [ ] `python3 -m jarvis_kernel retention --show` shows the settings you chose; the audit has one `retention_run` row (with `vacuumed`).
- [ ] The token for the console still works (the kernel client now sends it only to loopback, a Tailscale address or https).

## Rollback
Swap `~/jarvis/kernel.old.<ts>` back, restart `jarvis-llm-proxy` and `jarvis-kernel`. Text removed by retention does not come back;
restore `state.db` from the copy above if you need it.
