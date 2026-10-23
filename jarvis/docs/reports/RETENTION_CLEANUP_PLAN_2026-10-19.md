# Retention audit and owner-reviewed cleanup plan (2026-10-19)

Nothing here deletes anything by itself. The sweep (`jarvis_kernel/retention.py`) runs at kernel start and about hourly with the
settings below; this file says what it would change, how to look first, and what it never touches.

## 1. How to look first (changes nothing)

```
python3 -m jarvis_kernel retention --show        # settings in force
python3 -m jarvis_kernel retention --dry-run     # COUNTS ONLY: rows that --run would change; rolled back; no audit row, no key file
python3 -m jarvis_kernel retention --run         # apply (audited as one retention_run row)
python3 -m jarvis_kernel retention --off         # nothing is removed until --on (also: environment JARVIS_RETENTION=off)
python3 -m jarvis_kernel retention --set projectfix_days=120   # windows are per store, see section 3
```
Sizes: this test server holds **no live kernel data** (no `~/.jarvis-kernel`, vault or outputs), so no real sizes can be quoted here
(BLOCKED: needs the laptop D:\ data and the Oracle VM). To measure on the real machines: `du -sh ~/.jarvis-kernel/* <vault> <outputs>`
(Linux) or `Get-ChildItem -Recurse | Measure-Object Length -Sum` (Windows), and `retention --dry-run` for row counts.

## 2. Inventory of everything that is written

"Private text" = the owner's words, files names, paths, hosts, replies. Modes are what the code produces; the kernel data folder is
now created 0700 with 0600 files (new this pass, `JARVIS_DATA_MODE=keep` turns it off; before, it followed the umask: 0775/0644 on this server).

| Store | Where | Private text | Retention today | Docs vs reality |
|---|---|---|---|---|
| approvals (card text, reason) | state.db | full action text while pending; reason can quote a host | used/denied/expired -> stub after 24 h; `reason` now stubbed too unless a fixed phrase | 05_OPERATIONS said "24 h", true; `reason` was NOT covered before this pass |
| lab_share_pending | state.db | text approved for the public lab | deleted when used/denied/expired | true |
| idempotency | state.db | kept replies | deleted after 24 h | true |
| jobs (params, result) | state.db | file names, content, results | final + 14 d or beyond newest 2000 -> ids/numbers/lengths | true |
| tasks, task_events | state.db | request, goal, plan, results, notes | final + 30 d or beyond newest 300 | true |
| owner_commands | state.db | what the owner typed, replies | finished + 30 d or beyond newest 500 | true |
| projectfix.data | state.db | lab instructions, file names, test output, outcome | **new:** finished (done/failed) + 60 d -> scalars/ids kept, text -> length | was unbounded; docs said "not covered" |
| connector_calls | state.db | names, lane, timing, digest of arguments (no argument text) | **new:** args_digest cleared after 90 d | was unbounded |
| automations (+ runs) | state.db | name, command/prompt, watched path, run detail | **new:** completed/expired + 90 d -> text removed; live automations untouched | was unbounded |
| agent_defs | state.db | purpose, instructions, criteria | **new:** terminated + 90 d -> text removed; clone of such a row is refused; active/draft/paused untouched; edit history holds only version/time/op | was unbounded |
| memories | state.db | the owner's memory entries | none by design: the owner's own store with its own forget (secure_delete + WAL truncate) | by design |
| audit | state.db | metadata only since this series; OLD rows still hold task goals, memory text, pc.open_* paths | none: append-only hash chain, rewriting would break it and the anchors | stated in 05_OPERATIONS |
| audit anchors | audit-anchors.jsonl (0600) | seq + hash only | none, small | n/a |
| KILL file | data folder | a timestamp | removed on resume | n/a |
| traces | not stored: built on demand from tasks/jobs/audit; `trace --save` writes a JSON file in a folder the owner names | scrubbed, clipped text | owner deletes the saved file; the sources are covered above | docs correct |
| state.db-wal / -shm | data folder | recent pages | checkpoint(TRUNCATE) after each run that changed rows; secure_delete on | true |
| alerts.log | data folder | service names, events | none (small, appended) | now inside the 0700 folder |
| Telegram state / notice tables | state file, `tg_*_notices` | chat offsets, ids | none, no text | Telegram is off by owner decision |
| vault inbox/daily/decisions | the owner's vault | notes agents wrote | none: the owner's own notes | by design |
| outputs dirs, node outputs | laptop D:\jarvis\outputs | delivered files | none: the owner's files | by design |
| desktop / main_agent run files, widget neko_state, `pc_operations.json`, `activity.json` | laptop data dir `neko\` | step text, window titles, app names | NOT AUDITED in depth on this server (Windows-only); they are overwritten per run (state) or appended (activity log); no sweep | needs a laptop check |
| lab activity/task logs | lab host `~/lab/` | prompts, outputs | NOT covered by the kernel sweep; the lab owns them | LAB engineer |
| eval artifacts, backups/snapshots, crash reports | repo / owner backups | may hold canary or real text | none; never touched | see section 5 |

## 3. What the sweep changes, exactly

Windows (all settable with `--set`): approvals_grace_hours 24, jobs_days 14, tasks_days 30, commands_days 30, projectfix_days 60,
connector_days 90, definitions_days 90; keep-newest caps jobs 2000, tasks 300, commands 500. Only **finished** things are touched:
pending/approved cards, running jobs/commands, open tasks, live automations, non-terminated agents keep their text. Each change is
idempotent (a stub is never stubbed again), audited as one `retention_run` row, safe on old schemas (a missing table counts as zero),
and off with `--off`/`JARVIS_RETENTION=off`.

## 4. Salted-hash threat model (what a stub's hash prefix does and does not protect)

Before this pass a stub kept, per text field, `sha256(salt|text)[:12]` with the salt stored in the same database, and a 12-character
prefix of the approval digest (an unsalted hash of the action).
- *Database file stolen* (a copied state.db, a backup): the thief has the salt, so he can test guesses offline at billions per second.
  Anything guessable (a name, a PIN-like number, yes/no, a phone number, a short command) was recoverable in seconds. Long, unpredictable
  text was not.
- *Whole disk / same-account process / root*: the same.
Now: prefixes are `HMAC-SHA256(key, text)[:12]` with a random 32-byte key in `retention.key` (0600), a separate file from state.db;
text shorter than 24 characters gets **no prefix at all**; the digest prefix kept on a stub is dropped when the removed text was shorter
than 24 characters; with no readable key no prefix is written. Result: a stolen state.db alone cannot be used to test guesses
(strictly better). A whole-disk thief has the key, but for short text there is nothing left to test against. **Not protected:** medium-
length, low-entropy text (24+ characters of predictable wording) against someone with the key; the kept length (`chars`) of every
field; the digest prefix of a long action; old stubs written before this pass (their salted prefixes stay, and the old salt row stays in
`owner_config`). Owner option for old stubs: `DELETE FROM owner_config WHERE key='retention_salt'` makes them untestable from the database
alone (nothing reads them; the cost is that old prefixes can no longer be matched to a known text). Keep `retention.key` OUT of copies
you hand to anyone, and out of cloud backups of state.db, or the separation is lost. `secure_delete` and the WAL truncate do not reach
filesystem snapshots, off-box backups or SSD wear-levelling.

## 5. What is never deleted, and what is left for the owner

Never by the sweep: user data (vault, outputs, memories), audit rows and anchors, backups, snapshots, crash reports, eval artifacts, the
repository. Owner-reviewed manual list (nothing automatic): (1) old audit rows in any copy that left the machine; (2) off-box backups
of state.db made before this pass (they still hold full cards); (3) the laptop's `neko\` run and activity files (needs a look on the
laptop); (4) the lab's `~/lab/` logs; (5) saved traces. Order: `--dry-run`, review counts, make a backup you are willing to keep or
destroy on purpose, `--run`, verify with `python3 -m jarvis_kernel audit verify`.
