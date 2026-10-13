# 05 Memory, database and automation scheduling

Date: 2026-10-11. Scope: read-only research for JARVIS (D:\jarvis\hq). No files in hq were changed except this report.

Evidence labels used below:
- **MEASURED** = run by me today on the owner's laptop (Windows 10, Intel i9-9980HK, 16 threads, 32 GB, NTFS, Python 3.12.13 at D:\jarvis-apps\python, SQLite 3.53.1). Scratch databases only, in the session temp dir.
- **SOURCE** = read in code or docs, with file:line or URL.
- **SECONDARY** = a summary by a small model of a page I fetched, or a vendor or blog claim. Verify before depending on it.
- **ANECDOTE** = one person's report.
- **UNVERIFIED** = not checked this session.

Limits of this research (stated up front):
- Reddit could not be searched. The WebSearch tool rejects `reddit.com` as a domain ("not accessible to our user agent") and WebFetch refused `old.reddit.com`. Section 8 uses GitHub issues, HN, forums and arXiv instead, and says so.
- The VM (Oracle ARM) was not touched, so every VM-specific number below is a laptop number or UNVERIFIED.
- The Hermes research clone (`D:\jarvis\research\hermes-agent-818c13be`, SHA `818c13be1dc4fd28987e1e881a9408224afd4535`, MIT) is partial: it has no `cron/` package and no `hermes_state.py`. Those two were read from GitHub raw at the same SHA through WebFetch, so they are SECONDARY (small-model summaries of the source).
- No code from any clone was executed. No new clone or download was made (research dir total: OpenJarvis 148 MB, Hermes 65 MB).

---

## 1. Summary and ranked recommendations

Short answers:

1. **SQLite is the right store at this scale, and SafeDB is correct for the one path it covers.** The daily note's open issue is real but narrower than it sounds: all transactions already go through one audited code path (`AuditLog.append`). What remains is (a) multi-statement read-modify-write sequences that are not transactions, (b) no way to put a state change and its audit row in one transaction, (c) one lock serialising reads behind fsync-ing writers. The minimal fix is a re-entrant `SafeDB.tx()` context manager (about 20 lines, tested below), not connection-per-thread.
2. **Do not add a vector database.** At under 100k notes, FTS5 plus (only if needed) a flat float32 scan beats any added service. Measured below: FTS5 queries take about 0.2 to 11 ms at 100k notes in a 56 MB database. The VM already has bge-small embedding at about 10 ms per text (docs/02_ARCHITECTURE.md:69).
3. **Adopt patterns from the memory frameworks, not dependencies.** The kernel's memory (id, version, verified forget) already matches or exceeds what mem0, Letta and Hermes built-in do for update and delete. The gaps are provenance, injection scanning, true deletion, and a "learn from correction" record.
4. **Learning loops: keep the existing trace analyzer + eval gate + skill gate as the pipeline.** OpenJarvis's orchestrator writes configs before it evaluates and never rolls back on reject, so its loop is not a safe template. Hermes's curator is a lifecycle manager, not a validator.
5. **Scheduling: one kernel table pair (`automations`, `automation_runs`) with a UNIQUE run key, fired by a `tick()` function.** Drive `tick()` from a systemd timer (the pattern `jarvis-nightly.timer` already uses), not a new daemon. Fired work goes into the existing `tasks` and `jobs` tables. File-arrival triggers must run on the laptop node.

### Ranked list (value / effort)

| # | Recommendation | Value | Effort | Type |
|---|----------------|-------|--------|------|
| 1 | `SafeDB.tx()` re-entrant IMMEDIATE transaction; audit and state change in one tx; CAS (`AND state=?`) on `Tasks.update` | High | S (about 40 lines + tests) | Own code, pattern |
| 2 | `automations` + `automation_runs` (UNIQUE run key) + `tick()`; fire into tasks/jobs | High | M | Own code, pattern from Hermes cron |
| 3 | Real deletion in `Memories.forget` (blank text and history texts, not just `state='deleted'`) | High (matches "forget means forget", docs/01_RESEARCH_AUDIT.md:114) | S | Own code |
| 4 | Memory provenance column + injection scan on write + only owner-stated entries in the system prompt | High (poisoning is the top persistent risk) | S | Pattern (Hermes `_scan_memory_content`, OWASP ASI06) |
| 5 | `tzdata` on the laptop Python; zoneinfo for all schedule maths | High (it is broken today, see 7.8) | XS | Add dependency (data only) |
| 6 | `croniter` for cron expressions (kernel is otherwise stdlib-only) | Medium | XS | Add dependency (MIT) |
| 7 | Laptop-node file watcher posting events with a dedupe key, plus reconcile scan at node start | Medium | M | Add dependency `watchdog` (Apache-2.0) on laptop only |
| 8 | `agent_defs` table keyed to existing `agent_tokens.name` | Medium | S | Own code |
| 9 | Read-only second connection for list/render/trace reads; `busy_timeout`, `foreign_keys`, `user_version` | Medium | S | Pattern (Hermes `hermes_state.py`) |
| 10 | FTS5 over lessons/corrections and vault chunks (not over the 200 preference entries) | Medium | S | Stdlib, no dependency |
| 11 | `corrections` record linked to memory id and later task outcomes ("learn from user corrections") | Medium | S | Own code, extends traces.py |
| 12 | Hermes cron: keep only for in-agent reminders; do not store owner automations there | Medium | XS (config) | Skip as store of record |
| 13 | sqlite-vec | Low now | S | Skip until >5k embedded notes and numpy is absent |
| 14 | mem0, Letta, Graphiti, Basic Memory, Chroma, LanceDB, Qdrant, Celery, Temporal, Prefect | Low | high | Skip |
| 15 | APScheduler 3.x | Low | S | Skip (tick() is smaller than adapting it; 4.x is pre-release) |

---

## 2. What exists today (read-only inspection)

All paths are under `D:\jarvis\hq\`.

### 2.1 Process and connection topology

- Six processes can open `state.db` (each builds its own `Kernel()` and so its own `SafeDB`): `jarvis-kernel.service` (nodes endpoint), `jarvis-llm-proxy.service` (:8790 gateway and actions), `jarvis-telegram.service` (currently off), `jarvis-nightly.service`, the console CLI (`kernel/jarvis_kernel/cli.py:73`), and `scripts/alert.py:24`. Sources: `deploy/*.service` ExecStart lines; `scripts/nightly.py:74`.
- Both HTTP servers are `ThreadingHTTPServer` (`kernel/jarvis_kernel/llm_proxy.py:694`, `nodes.py:149`): one thread per request, all sharing the process's single connection.
- Hermes (`hermes gateway run`, venv314, user jarvis-agent) is a separate process tree with its own SQLite files (`state.db`, `cron/executions.db`, `memory_store.db`).

So the real model is **several processes, each with many threads on one connection**. The daily note's "all threads share one database connection" describes one process. Across processes SQLite's own file locking is what protects data, and it does so correctly because of `BEGIN IMMEDIATE` + `timeout=30`.

### 2.2 Kernel store (SafeDB)

- `store.py:68`: `sqlite3.connect(..., timeout=30, isolation_level=None, check_same_thread=False)`; `store.py:69`: `PRAGMA journal_mode=WAL`. No `synchronous`, `foreign_keys`, `user_version` or `busy_timeout` pragma (timeout=30 sets busy timeout to 30 s).
- `store.py:23-36` `_Rows`: every statement is fully fetched under the lock, so a caller never holds a live cursor.
- `store.py:39-58` `SafeDB`: one `RLock`; `execute` = lock + execute + fetchall.
- `audit.py:9` module-level `_LOCK`; `audit.py:28-43` `append`: takes `_LOCK` and `db.lock`, then `BEGIN IMMEDIATE`, reads head, inserts, `COMMIT`; `ROLLBACK` on any exception. `BEGIN` is outside the `try`, correct.
- `store.py:75` `Kernel.lock` is a third lock. Order in use: `Kernel.lock -> db.lock` and `_LOCK -> db.lock`. I found no path that takes them in the opposite order, so no deadlock.
- Single-statement compare-and-set is used correctly for approvals: `store.py:119-121` (`approve`) and `store.py:136-140` (`authorize`, single-use consumption). `jobs.py:127` (`next_for`) and `jobs.py:137` (`finish`) are CAS on `state`.

### 2.3 Memory

- `memory.py:41`: `memories(id TEXT PRIMARY KEY, owner, scope, kind, text, version, state, created, updated, history)`. No index on `(owner, scope, state)`, no UNIQUE on `(owner, scope, text)`, no `source`/provenance column.
- `memory.py:93-100` `add`: duplicate check and the `MAX_ACTIVE` check read the table, then `INSERT` (check-then-insert, no transaction). Two simultaneous identical adds can both pass the check. Low probability, real.
- `memory.py:105-118` `update`: read, then `UPDATE ... WHERE version=?`; `rowcount != 1` becomes a `Conflict`. This is correct optimistic concurrency.
- `memory.py:120-133` `forget`: soft delete (`state='deleted'`), then re-reads. The **text and the history JSON (up to 20 prior texts) stay in the row**. This conflicts with the stated rule "FAISS and dense backends only soft-delete, which conflicts with 'forget means forget'" (`docs/01_RESEARCH_AUDIT.md:114`). Verification (`verified: self.get(...) is None`) checks the API view, not the bytes.
- `memory.py:77-86` `_check_text`: refuses secret-looking text via `scrub`. It does **not** look for instruction-like or injection text, and nothing records where an entry came from.
- `memory.py:53-74` `list`/`resolve`: N+1 reads (`list` calls `get` per id), word-overlap and Jaccard (threshold 0.34, margin 0.15). Fine for at most 200 entries per owner and scope.
- `hermes/plugins/jarvis_memory/__init__.py:87-97`: every session's system prompt block is `memory.render` + `task.checkpoint`. Whatever is in memory is therefore trusted system-prompt text at every session start.

### 2.4 Tasks, jobs, traces

- `tasks.py:72-99` `update`: reads the row (`_row`), validates the transition against the **read** state, then `UPDATE ... WHERE id=?` with no `AND state=?`. Two threads can both pass the check (TOCTOU). The transition table `NEXT` (tasks.py:17-25) is the only guard.
- `tasks.py:101-109` `expire` and `jobs.py:142-153` `to_report` already implement "stuck work becomes failed" with fixed TTLs (`JOB_TTL` 30 min, `RUN_TTL` 30 min, `LAB_RUN_TTL` 2 h, jobs.py:11-29).
- `jobs.py:19-27`: job kinds are a fixed whitelist; each kind's params are validated by `_check_params` (jobs.py:36-65). Any new automation must produce one of these, so policy and authorization stay in one place.
- `traces.py:35-39`: reads through a separate `mode=ro` connection. `traces.py:235-245`: analyzer correlates `skill_activate`/`lane_preference_set` events with before/after verified rates (last 5 vs next 5 tasks).
- `scripts/nightly.py:42-76` + `deploy/jarvis-nightly.timer`: `OnCalendar=*-*-* 21:00:00 UTC`, `Persistent=true`, `RandomizedDelaySec=300`; service is `Type=oneshot`. This is the only scheduled automation today.

### 2.5 Documented intent

- `docs/03_DECISIONS.md` D6: SQLite for state, markdown vault for memory, "no vector database until retrieval over the vault proves too slow or inaccurate with FastEmbed plus a flat index". D7: "a small state machine in the kernel plus the Hermes cron scheduler".
- `docs/01_RESEARCH_AUDIT.md:114`: OpenJarvis memory not used (needs the Rust extension; soft-delete backends).
- `docs/02_ARCHITECTURE.md:284-` (Context reliability, durable memory): kernel is the store of record; ids, versions, verified forget.

This report's recommendations are consistent with D6. They refine D7: Hermes cron stays, but the kernel owns the definitions (reason in 7.9).

---

## 3. SQLite at this scale

### 3.1 What the SQLite documentation guarantees (SOURCE: https://www.sqlite.org/wal.html, fetched 2026-10-11)

- WAL: "readers do not block writers and a writer does not block readers", but "there can only be one writer at a time".
- `journal_mode=WAL` is persistent in the file. All processes must be on the same host (no network filesystem), which holds: `state.db` is on the VM's local disk.
- Checkpoint starvation: a checkpoint cannot finish while any reader is always active; the WAL then grows without bound. The default auto-checkpoint is 1000 pages. At this scale this is not a risk, but a long-lived read cursor (for example a never-finished `verify()` loop) should be avoided.
- `synchronous=NORMAL` in WAL omits the WAL fsync per commit: "transactions are no longer durable and might rollback following a power failure"; the database stays consistent. The kernel uses the default FULL, which is the right choice for an audit log with an off-box anchor (the anchor lets you detect a rolled-back tail).
- Python (SOURCE: https://docs.python.org/3/library/sqlite3.html): `sqlite3.threadsafety` is 3 ("serialized") when SQLite is built thread-safe; `check_same_thread=False` only lifts the check, and "write operations may need to be serialized by the user". MEASURED on the laptop build: `threadsafety == 3`. The VM's system Python build is UNVERIFIED but Debian/Ubuntu builds are normally serialized.

### 3.2 What actually goes wrong with manual transactions on a shared connection

A SQLite connection has **one** transaction state, not one per thread. `isolation_level=None` plus `BEGIN` makes that state explicit and shared. Consequences, **MEASURED** by a two-thread repro (`scratchpad/exp1.py`):

| Scenario | Result |
|----------|--------|
| Thread A: `BEGIN IMMEDIATE`, insert, sleep, fails, `ROLLBACK`. Thread B (no lock): plain autocommit insert during A's window. | B reports `in_transaction=True` (it silently joined A's transaction). After A's rollback, **B's committed-looking row is gone** (`rows == []`). |
| Same, but A and B both hold one `RLock` around the whole transaction / statement. | B waits; B's row survives (`[('B-task-update',)]`). |

Four failure modes follow from the shared state:
1. A thread's statement joins another thread's open transaction and commits or rolls back with it.
2. One thread's `commit()` makes another thread's half-done work permanent.
3. A rollback after an error in one request throws away other requests' successful writes made in that window.
4. A live cursor in one thread interacts with another thread's commit/rollback on the same connection (the daily note's 401/404 from a lazy cursor; mechanism inferred, UNVERIFIED, but `_Rows` fully fetching under the lock removes the class).

**Current SafeDB removes all four for statements and for `AuditLog.append`**, because the lock spans BEGIN..COMMIT there. I found no other `BEGIN` in `kernel/jarvis_kernel/*.py`. nodes.py:28/33/45/54 call `db.commit()` which is a no-op in autocommit and takes the lock.

### 3.3 Residual risks (ranked)

| # | Risk | Where | Effect |
|---|------|-------|--------|
| 1 | No way to commit a state change and its audit row atomically | `tasks.py:94-98`, `memory.py:100-102`, `store.py:105-108` + `audit.append` | Crash between the two: state without audit event, or the reverse. The audit log is the claimed source of truth. |
| 2 | Read-modify-write without CAS | `tasks.py:72-99`; `memory.py:93-100` | Two threads or processes can both pass a check (illegal transition, duplicate or over-limit memory). |
| 3 | Nesting hazard | `audit.py:31` | If any caller ever wraps `append` in its own `BEGIN`, SQLite raises "cannot start a transaction within a transaction". The first person to add an outer transaction hits it. |
| 4 | Python locks do not cross processes | `audit._LOCK`, `Kernel.lock`, llm_proxy dispatch lock | Process B (telegram, nightly, CLI) is protected only by SQLite. Fine for CAS statements, not for multi-statement checks. |
| 5 | Reads serialised behind slow writers | `store.py:47-50` | MEASURED below: 4.7x fewer reads/s with FULL sync. Not a problem at human request rates. |
| 6 | `verify()` loads whole audit table under the lock | `audit.py:56` via `_Rows.fetchall` | O(n) memory and lock time as the chain grows. Use the read-only connection. |
| 7 | 30 s busy wait inside the Python lock | `store.py:68` + `:48` | A stuck cross-process writer makes every thread in this process block for up to 30 s. |
| 8 | Cursor `rowcount`/`lastrowid` are captured at statement time | `_Rows` | Correct. No issue. |

### 3.4 Measured: lock-per-connection vs split reader connections (scratchpad/exp3.py)

4 reader threads doing point reads plus 1 writer doing `BEGIN IMMEDIATE; INSERT; COMMIT`, 4 s per cell, 20k-row table, WAL, laptop NTFS.

| Mode | synchronous | reads/s | commits/s | commit p50 / p99 |
|------|-------------|---------|-----------|------------------|
| single connection + lock (today) | FULL (today's default) | 6,255 | 40 | 18.5 / 72 ms |
| writer + 4 `mode=ro` reader connections | FULL | 29,274 | 48 | 18.3 / 69 ms |
| single connection + lock | NORMAL | 43,434 | 2,188 | 0.04 / 9.2 ms |
| writer + 4 `mode=ro` readers | NORMAL | 22,242 | 2,052 | 0.32 / 0.79 ms |

Reading: with FULL sync the writer holds the lock for 18 ms per fsync and reads queue behind it (4.7x fewer). Absolute numbers are thousands per second, far above a personal system's tens of requests per minute. **Throughput is not a reason to change design. Correctness is.** The VM's block-storage fsync time will differ (UNVERIFIED).

### 3.5 Connection-per-thread vs SafeDB

| Property | One shared connection + RLock (SafeDB) | Connection per thread (WAL) |
|----------|----------------------------------------|-----------------------------|
| Transaction isolation between threads | By the lock only; every code path must remember it | By SQLite; threads cannot join each other's transaction |
| Writers | Serialised in Python (cheap) | Serialised by SQLite (`SQLITE_BUSY` after timeout; needs `BEGIN IMMEDIATE` discipline) |
| Reads during writes | Blocked by the lock | Concurrent (WAL) |
| Memory/file handles | 1 | N (threads x processes) |
| Failure mode | A missed lock = silent cross-thread corruption of logic | `database is locked` errors; deferred-transaction upgrade deadlock if a read tx later writes |
| Upstream practice | Hermes holographic memory (`plugins/memory/holographic/store.py:90-98`: "ONE connection and ONE re-entrant lock"), OpenJarvis traces (`traces/store.py:106-114`, comment: "WAL does not serialize operations on one connection, so use a lock too") | Hermes `hermes_state.py` (SECONDARY): one writer connection with a lock and `BEGIN IMMEDIATE`, plus a bounded pool of `mode=ro` readers |

The best-supported design in Hermes's own session store is the hybrid: **one locked writer connection plus read-only connections for reads**. That is what traces.py already does for its reads. Extending it to `list`/`render`/`checkpoint` is the safe direction. A full connection-per-thread rewrite is not justified.

### 3.6 Minimal fix (tested)

Add one re-entrant transaction context manager to `SafeDB` and use it in `AuditLog.append` and in every multi-statement write. Nested `tx()` uses a SAVEPOINT, so an inner failure rolls back only the inner part and an outer failure rolls back everything.

```python
@contextlib.contextmanager
def tx(self):
    with self.lock:                                   # RLock: same thread may nest
        self._c.execute("BEGIN IMMEDIATE" if self._d == 0 else f"SAVEPOINT s{self._d}")
        self._d += 1
        try:
            yield self
        except BaseException:
            self._d -= 1
            if self._d == 0:
                self._c.execute("ROLLBACK")
            else:
                self._c.execute(f"ROLLBACK TO s{self._d}"); self._c.execute(f"RELEASE s{self._d}")
            raise
        else:
            self._d -= 1
            self._c.execute("COMMIT" if self._d == 0 else f"RELEASE s{self._d}")
```

**MEASURED** (scratchpad/exp4.py): 8 threads x 300 transactions, each inserting a task row and calling a nested `audit()`; 20% raise after both writes. Result: `committed=1922 rolled_back=478 tasks=1922 audit=1922 max_seq=1922 consistent=True`. State and audit commit or roll back together, and audit sequence numbers have no gaps.

Companion changes (each small):
1. `AuditLog.append`: replace the manual BEGIN/COMMIT with `with self.db.tx():` (nesting-safe).
2. `Tasks.update`: add `AND state=?` to the UPDATE and check `rowcount`; raise `TaskError("state changed")` otherwise. No lock needed.
3. `Memories.add`: wrap the check and insert in `tx()`; add `CREATE UNIQUE INDEX ... ON memories(owner, scope, text) WHERE state='active'` so the guarantee also holds across processes.
4. Wrap `Tasks.create` (insert task + event) and `Jobs.create` (insert + audit) in `tx()`.
5. Pragmas at open: `PRAGMA foreign_keys=ON`, explicit `busy_timeout=30000`, `PRAGMA user_version` migration counter (replace the try/except `ALTER TABLE` pattern, `jobs.py:90-95`).
6. Read-only connection for `memory.list/render`, `tasks.checkpoint`, `audit.verify`. Use the same `file:...?mode=ro` form as `traces.py:39`.
7. Keep `synchronous=FULL` (default). Do not switch to NORMAL for the audit database.

---

## 4. Searchable memory: FTS5 and vectors

### 4.1 Availability (MEASURED)

On `D:\jarvis-apps\python\cpython-3.12.13-windows-x86_64-none\python.exe` run with `-I`:
- SQLite 3.53.1, `threadsafety=3`, compile options include `ENABLE_FTS5`, `ENABLE_FTS3`, `ENABLE_FTS4`, `ENABLE_MATH_FUNCTIONS`, `THREADSAFE=1`.
- FTS5 works, with `trigram` and `porter unicode61 remove_diacritics 2` tokenizers; `json_extract` works; `enable_load_extension` exists.
- `zoneinfo.ZoneInfo("Asia/Kolkata")` **fails** (`ZoneInfoNotFoundError`): this Python has no tz database. `tzdata`, `croniter`, `numpy`, `watchdog`, `apscheduler`, `sqlite_vec` are all absent from it.
- VM: FTS5 on the Ubuntu system Python is UNVERIFIED (Ubuntu's libsqlite3 normally enables FTS5; `json_extract` is already relied on at `llm_proxy.py:150`). Add a startup self-check that creates an in-memory FTS5 table and degrades to the current word-overlap search.

### 4.2 FTS5 facts (SOURCE: https://www.sqlite.org/fts5.html)

- External-content table + triggers is the supported pattern; the delete and update triggers must insert the `'delete'` command with the **old** values, and "the FTS5 table must be updated first". Creating triggers does not copy existing rows: run `INSERT INTO ft(ft) VALUES('rebuild')`.
- `bm25()` returns smaller (more negative) for better matches; `ORDER BY rank` is equivalent and faster. k1=1.2, b=0.75 are fixed; column weights are optional arguments.
- Tokenizers: `unicode61` (default), `porter` wraps another tokenizer (English only), `trigram` supports substring and indexed `LIKE`/`GLOB`, but terms under 3 characters match nothing.
- Maintenance: `merge` (bounded) and `optimize` (long), `integrity-check`. Hermes runs a bounded merge every 1000 writes (`hermes_state.py`, SECONDARY).

### 4.3 Measured FTS5 at 10k and 100k notes (scratchpad/exp2.py)

Synthetic notes: 45 words each (about 250 characters, like a memory entry), 20k-word vocabulary, external-content table with the three triggers, top-10 `bm25` with join to `notes`, median of 20 runs. Single run each, laptop NTFS.

| Notes | Tokenizer | Insert (incl. triggers) | DB size | median ms: 1 common term / rare term / 3-term OR |
|-------|-----------|--------------------------|---------|---------------------------------------------------|
| 10k | unicode61 | 3.8 s | 5.9 MB | 0.49 / 0.26 / 0.63 |
| 10k | porter unicode61 | 3.4 s | 5.9 MB | 0.26 / 0.25 / 0.68 |
| 10k | trigram | 7.6 s | 11.7 MB | 0.60 / 23.4 / 1.62 |
| 100k | unicode61 | 29.5 s | 56.3 MB | 1.1 / 1.0 / 10.8 |
| 100k | porter unicode61 | 17.4 s | 56.3 MB | 0.49 / 0.55 / 8.9 |
| 100k | trigram | 37.9 s | 115 MB | 0.81 / 105 / 10.2 |

Also checked at every size: after `UPDATE ... SET text=...` the new word is found, after `DELETE` it is gone (`upd_hit=1`, `after_delete=0`), so the triggers keep the index in sync. The trigram "rare" slowdown is because the 4-character query `w123` is a substring of many tokens; trigram costs 2x disk and is only worth it for substring search over paths and identifiers.

Conclusion: at 100k notes FTS5 answers in about a millisecond to ten milliseconds in a 56 MB file. It needs no service and no new dependency.

### 4.4 Vector options at under 100k notes

| Option | License | What it is | Evidence at this scale | Verdict |
|--------|---------|-----------|------------------------|---------|
| Flat scan over float32 BLOBs in a SQLite column | none | 384 dims x 4 B = 1,536 B per note: 154 MB at 100k | MEASURED pure Python (stdlib): 40 microseconds per vector, so 5k notes = 0.2 s, 20k = 0.8 s, 100k = about 4 s. With numpy it is tens of milliseconds (UNVERIFIED here: numpy absent). VM is 2 cores ARM, expect slower (UNVERIFIED). | **Default if semantic recall is needed.** Fits D6 ("FastEmbed plus a flat index"). |
| sqlite-vec 0.1.9 | MIT and Apache-2.0 dual (SOURCE: PyPI JSON and repo) | SQLite extension, brute-force KNN (SIMD AVX/NEON), pre-v1 ("expect breaking changes", README) | SECONDARY: author's benchmark on an M1 Mac mini 8 GB: 100k vectors, 384 dims below 75 ms (https://alexgarcia.xyz/blog/2024/sqlite-vec-stable-release/index.html); a competitor's benchmark (Marco Bambini, M1 Pro): 67.84 ms for 100k x 384 float32 (https://marcobambini.substack.com/p/the-state-of-vector-search-in-sqlite); a Recall-app issue reports about 185 ms at 100k for cosine until switched to normalised L2. Linux aarch64 wheel: UNVERIFIED (PyPI JSON listing I could read showed only old alpha wheels). | Skip until needed. It buys SQL-side filtering and no numpy, not speed. |
| sqlite-vss | MIT | Predecessor on Faiss | SOURCE: README says "not in active development"; 1 GB index cap, no UPDATE, Linux x86_64 and macOS x86_64 binaries only | **Skip (deprecated).** |
| Chroma | Apache-2.0 | Client or server vector DB | SECONDARY: README does not state its storage engine | Skip: second store, outside kernel audit and "forget means forget". |
| LanceDB | Apache-2.0 | Embedded Lance columnar format, has FTS | Claims in the billions (marketing). aarch64 wheels not confirmed | Skip. |
| Qdrant | Apache-2.0 | Rust server (Docker) | Needs its own service and no-auth by default (README note) | Skip. |

Cost to embed everything once: about 10 ms per text on the VM (docs/02_ARCHITECTURE.md:69) = about 17 minutes for 100k notes, one-off. The embedding model is bge-small-en-v1.5 (384 dims) via llama.cpp on 127.0.0.1:8081 (`deploy/jarvis-embed.service`).

### 4.5 Is FTS5 justified for the memory entries?

- **Preference/fact entries (at most 200 per owner and scope, `memory.py:20`)**: no. The existing word-overlap and Jaccard resolve is adequate and the entry count is bounded. The live failure on 2026-10-10 was an identity problem (Hermes substring match), already fixed by ids. FTS5 would add bm25 ranking and stemming, but not correctness.
- **Lessons, corrections, task summaries, and vault chunks (unbounded)**: yes. DDL in section 9.4.

---

## 5. Agent memory frameworks: patterns vs dependencies

### 5.1 What each one does (SOURCE unless marked)

| Project | License | Update by stable id | Correction without duplicates | Deletion | Provenance / history | Notes |
|---------|---------|----------------------|-------------------------------|----------|----------------------|-------|
| JARVIS kernel memory (`memory.py`) | own | Yes: `m-xxxxxxxx` + version; stale write = `Conflict` (memory.py:105-118) | Yes, by update(id, expected_version) | Soft delete; re-read verified; text and history remain (memory.py:127) | History kept per entry (20 versions); audit event per change; no source field | Owner-scoped per agent token |
| Hermes built-in `memory` tool | MIT | No: `old_text` is a substring locator (`tools/memory_tool.py:107-113`, 389-391); whole entry replaced | Fragile: a model that drops a label cannot find the entry (live test 2026-10-10) | Substring remove, ambiguity returns an error (`memory_tool_store.py:377-386`) | Plain files MEMORY.md/USER.md, `§` delimiter. A `.bak` after drift. Injection scan on add/replace (`memory_tool_store.py:28-31, 339`). | P0 data-loss issue #26045 (ANECDOTE, maintainers confirmed): `replace` silently clobbered 8 KB of externally written content; fixed by a drift guard (PR #30877). |
| Hermes holographic plugin | MIT | Integer `fact_id`; `update_fact(fact_id, content, trust_delta)` | `content TEXT UNIQUE` dedupes exact text | `remove` | `trust_score`, `retrieval_count`, `helpful_count`; FTS5 external-content table with insert/delete/update triggers (`store.py:12-61`) | The clearest local template for FTS5 + triggers; shares one connection + RLock (`store.py:90-98`) |
| mem0 (`mem0ai`) | Apache-2.0 | `memory_id`; caller supplies the new text, which "replaces the stored memory value" (docs.mem0.ai update page; SECONDARY) | **April 2026: ADD-only.** The migration doc says "Single-pass ADD-only (one LLM call, no UPDATE/DELETE)"; a changed fact "is stored alongside the old one" and retrieval ranks current info higher | Immutable memories cannot be updated, only deleted and re-added | Not documented in the pages I read | Needs an LLM (default OpenAI gpt-5-mini) and an embedding model; graph memory removed from OSS. Third-party critique: ADD-only conflates correction with retraction ("I never said I was vegetarian"), SECONDARY |
| Letta (`letta-ai/letta`) | Apache-2.0 | Named memory blocks (label, value, limit, read_only); `value` replaces the whole block | Last write wins: "the last write wins and overwrites all earlier changes" (docs.letta.com blocks page) | n/a | None | Server product. The repo says current source moved to `letta-ai/letta-code` |
| Zep / Graphiti | Apache-2.0 | Temporal graph; facts carry validity windows | Old facts are "invalidated, not deleted" | Invalidate, not erase | Temporal history preserved | Needs a graph DB (Neo4j, FalkorDB, Neptune; Kuzu marked deprecated) and an LLM with structured output |
| Basic Memory | **AGPL-3.0** | `permalink` in frontmatter; markdown files + SQLite index | Edit the file (`edit_note`) | `delete_note` | Files in git are the history | Fits an Obsidian vault; AGPL is a license decision, not a technical one |
| OpenJarvis memory | Apache-2.0 | `document id` + `source`; `replace_source` | Replace all docs of one source atomically | FAISS/dense only soft-delete (docs/01_RESEARCH_AUDIT.md:114) | `metadata` JSON | **Rust backend is mandatory, no Python fallback** (`tools/storage/sqlite.py:47`) |

### 5.2 Patterns worth taking (all as patterns; none as dependencies)

1. **Stable id + optimistic version + verified read-back.** Already built. Keep it; it is better than mem0's, Letta's and Hermes's built-in.
2. **History per entry, never silent overwrite.** Built (20 versions). The mem0 lesson to add: record *why* a change happened. Add `reason` and `source_event` to each history item ("owner said", "owner corrected task X").
3. **Retraction is not update.** Keep two operations: `update` (the fact changed) and `forget` (the fact was never true or must not be kept). The kernel already has both. Name them in the tool descriptions so models stop using update-to-empty.
4. **Supersession instead of deletion for time-varying facts** (Graphiti's validity window, mem0's keep-both). For JARVIS, add `valid_from`/`superseded_by` only for `kind='fact'`; `preference` and `instruction` stay single-current.
5. **Trust and feedback counters** (holographic `trust_score`, `helpful_count`). Optional; only useful once recall is automatic.
6. **Drift guard** (Hermes #26045): refuse a write when the stored state is not what the writer last read. The kernel's `expected_version` is the same guard, and since the file mirror is gone it cannot drift.

### 5.3 Deletion: what "verified" must mean

Today `forget` verifies that `get()` returns None. To match "forget means forget":
- In the same `tx()`: `UPDATE memories SET text='', history='[{"op":"forget","at":..}]', state='deleted'`. Keep the id, kind, scope, timestamps and the audit event (the audit payload already holds only id and owner, `memory.py:132`, so it does not leak the text).
- If FTS is added, the update trigger removes the old text from the index automatically (tested above).
- Copies still exist in WAL frames until a checkpoint, in backups, and in any session's already-loaded system prompt. State this limit in the tool result ("will not be loaded in new sessions").
- Hermes's own session store also holds conversation text containing the memory; `hermes sessions` retention is separate and not changed by kernel deletion. (UNVERIFIED how the owner's setup prunes it.)

### 5.4 Learning from user corrections

The kernel already marks a correction as an event kind on tasks (`traces.py:128`, `"corrected": any(e["kind"]=="correction")`) and the analyzer checks whether later same-category tasks avoided the problem (`traces.py:228-233`). What is missing is the link from a correction to a **durable lesson**:

1. A `corrections` row: `{id, task, memory_id?, skill?, what_was_wrong, what_is_right, category, created}`.
2. The planner reads open corrections for the category at task creation (as a short block like `render`).
3. A correction becomes a lesson that stays "provisional" until the analyzer's follow-up check shows later tasks of that category verified without correction (`traces.py:281-283` already produces the text "the fix has not shown up in real work yet").
4. Corrections on memory itself go through `update(id, expected_version)`; the correction row stores `memory_id` so the history entry can cite it.

### 5.5 Memory poisoning (failure modes)

| Source | Finding | Label |
|--------|---------|-------|
| MINJA, arXiv 2503.03704 (v1 5 Mar 2025, v5 12 Feb 2026; NeurIPS 2025) | An attacker needs only ordinary queries and observed outputs to plant records; any user can influence agent memory. Recommended: memory isolation, authentication, secure memory management, prompt filtering. Abstract has no success-rate numbers on the page I read. | Peer-reviewed research |
| eTAMP, arXiv 2604.02623 (3 Apr 2026) | One contaminated observation (a manipulated product page) poisons memory and triggers on later tasks on different sites. Attack success up to 32.5% on GPT-5-mini, 23.4% on GPT-5.2, 19.5% on GPT-OSS-120B; "more capable models are not more secure". Abstract names no defence. | Research (SECONDARY summary) |
| Unit 42, 9 Oct 2025 | Amazon Bedrock Agents with memory: a poisoned web page manipulated the session-summarisation step, so injected instructions were written to long-term memory and later exfiltrated booking data via URL parameters. Guardrails were off in the test. | Vendor PoC, reproducible description |
| OWASP ASI06 "Memory and Context Poisoning" | Persistence across sessions is what separates it from ordinary prompt injection. Numbering and dates differ across the secondary sources I found; check the official document before citing a number. | SECONDARY |
| OWASP Agent Memory Guard (community post, LangChain forum 2026-05-11; HN Show HN 2026-05-29 and 05-31, 3 and 4 points, no comments) | Middleware that scans memory reads and writes for injection patterns. A promotion by its author. License not stated in the post. | ANECDOTE / vendor |

How this maps to the kernel today:
- Strength: entries are owner-scoped by token (`memory.py:9`), length-capped (500), secret-scrubbed, and a lab/public-lane agent cannot write the owner's memory.
- **Gap 1 (provenance):** nothing records whether an entry came from "the owner said this", "the agent inferred this", or "text from a web page the agent read". The plugin renders all of them into the system prompt (`jarvis_memory/__init__.py:87-97`).
- **Gap 2 (content):** `_check_text` blocks secrets only. Hermes's own write path scans for injection or exfiltration patterns first (`tools/memory_tool_store.py:28-31`, strict scope, "memory enters the system prompt, so a poisoned entry persists across sessions").
- **Gap 3 (approval):** kernel approvals exist (`store.py:97-157`) but `memory.add` is allowed without one for any provenance.

Proposed controls (small):
1. Add `source` (`owner_said` | `agent_inferred` | `external_content`) and `origin_task` columns. The plugin passes the source from the turn context: only text the owner typed directly may be `owner_said`.
2. `render()` loads only `owner_said` entries unconditionally. `agent_inferred`/`external_content` entries are listed by id in a "pending review" count, not shown as instructions.
3. Reuse `scrub`-style detection with an instruction-pattern list for `kind='instruction'`; refuse and audit matches. (The Hermes list at `tools/skills_guard.py` is MIT and can be read as a source of patterns.)
4. Writes with `source != owner_said` need an approval (existing mechanism, 600 s TTL).
5. Memory text is data in the prompt, wrapped and labelled as quoted user preference, never concatenated into the instruction section.
6. Nightly: list new memory entries since last run in the nightly summary (`scripts/nightly.py` already sends a summary) so a poisoned write is seen within a day.

---

## 6. Learning loops

### 6.1 Hermes: skills lifecycle, not a regression-gated loop

- `agent/curator.py:1-8`: background skill maintenance, triggered by inactivity (no cron daemon). Invariants: touches only curator-managed skills, "never delete, only archive (recoverable)", pinned skills bypass transitions. Defaults: interval 7 days, min idle 2 h, stale after 14 days, archive after 30 days; LLM consolidation is opt-in (`DEFAULT_CONSOLIDATE = False` near the top of the file).
- `tools/skill_ledger.py:1-9`: every skill mutation is a JSONL entry with before/after manifests stored content-addressed (sha256), with single-edit rollback. The header says "TELEMETRY, NOT A GATE".
- `tools/skill_manager_tool.py:36,407`: agent-created skills go through `skills_guard.scan_skill` (a static security scan), not a behavioural test.
- `agent/learn_prompt.py`: `/learn` builds one prompt that makes the live agent author a skill with `skill_manage`, under house-style rules (description at most 60 characters, etc.).
- I found **no regression test or eval run before a Hermes skill becomes active** in the files I read. Validation is the security scan, the write-approval setting, and rollback after the fact.

Note: `curator_backup.py` also snapshots `cron/jobs.json` because consolidation rewrites cron `skills` references (line 5-6): skill changes can silently alter scheduled jobs. This is a concrete reason to keep owner automations out of Hermes's file.

### 6.2 OpenJarvis: trace to learning to optimisation

Schemas (SOURCE, `D:\jarvis\research\OpenJarvis`, ecbde4b3, Apache-2.0):
- `traces` table (`traces/store.py`): `trace_id UNIQUE, query, agent, model, engine, result, outcome (NULL|success|failure), feedback REAL, started_at, ended_at, total_tokens, total_latency_seconds, metadata JSON, messages JSON`; `trace_steps(trace_id, step_index, step_type, timestamp, duration_seconds, input, output, metadata)`.
- `traces_fts` external-content FTS5 over `(trace_id, query, result, agent)` with **only an AFTER INSERT trigger** (`traces/store.py:60-61`). There are no delete or update triggers, so the index goes stale on edits or deletions. Do not copy this.
- `LearningOrchestrator.run` (`learning/learning_orchestrator.py`): mine SFT/routing/agent pairs; baseline eval (line 146); **write evolved agent configs (line 159)**; optional LoRA; post-eval (line 170); mark `rejected` if improvement is below `min_improvement=0.02` (line 182); and "No eval gate — always accept" when no `eval_fn` is given (line 188). There is **no rollback call on reject**: `AgentConfigEvolver` has a versioned `rollback()` (`agent_evolver.py:268`), but the orchestrator never calls it. The loop applies the change first and judges it second.
- `SkillOptimizer` writes `learning/skills/<name>/optimized.toml` overlays and says "Plan 2B will measure the impact"; it does not measure.
- `scheduler` tables: see 7.3.

### 6.3 What JARVIS already has (and it is better gated)

| Stage | Component | Gate |
|-------|-----------|------|
| Record | `task_events`, `audit`, `jobs` (no second store) | Hash-chained audit with off-box anchor |
| Trace | `traces.build` (bounded 48 kB, scrubbed, `--public`) | Read-only connection |
| Analyze | `traces.analyze` (recurring problems, provider failures, skill-vs-outcome, before/after change rates) | Suggestions only, "never changes anything" |
| Validate | `evals/run_evals.py` (19 fixed cases, deterministic scorers, `--baseline` fails on any regression) | Real kernel code, fake providers |
| Activate | `skills/skillgate.py` (stage from pinned source, PROVENANCE.json, skills_guard findings can only raise risk, allowlist, kernel `skill.activate`, live smoke test with automatic rollback) | Kernel-authorized; medium risk needs approval |
| Observe | Analyzer compares verified rate before/after `skill_activate` events (n>=3, drop >0.2 triggers a regression suggestion) | Recommendation |

Gap: activation is gated by a smoke test and the owner, but the **regression suite is not an activation prerequisite** (the eval baseline is run on demand and by the analyzer afterwards). Closing it is small: `skill.activate` should refuse unless the last `run_evals.py --baseline` result is newer than the candidate and passes.

### 6.4 Concrete lesson schema (proposal)

```sql
CREATE TABLE lessons(
  id TEXT PRIMARY KEY,                -- 'l-' + 8 hex
  category TEXT,                      -- traces.CATEGORIES name
  kind TEXT CHECK(kind IN ('prompt_hint','criterion','skill_change','routing','memory_pointer')),
  text TEXT NOT NULL,                 -- <= 500 chars, scrubbed
  evidence TEXT,                      -- JSON: [{task, event_id}] that motivated it
  source TEXT,                        -- 'analyzer' | 'owner_correction' | 'agent'
  state TEXT DEFAULT 'proposed' CHECK(state IN ('proposed','validating','active','rejected','retired')),
  eval_baseline TEXT,                 -- run id / file of the eval run that passed
  eval_score_before REAL, eval_score_after REAL,
  activated_at REAL, retired_at REAL, version INT DEFAULT 1,
  created REAL, updated REAL
);
```
State changes only through the kernel (like `skill.activate`): `proposed -> validating` (run evals) `-> active` only if the eval run shows no regression and, for medium risk, an approval; `active -> retired` automatically if the analyzer's before/after drop rule fires.

---

## 7. Scheduling and automation

### 7.1 Requirements (from the brief)

Timezone-aware cron; once and recurring; durable across restart; duplicate-run prevention (idempotent run key); run history; run-now, pause, resume; retries; per-automation limits; event triggers (file arrival); smallest design reusing the existing `jobs` and `tasks` persistence and existing scheduler(s) rather than a second daemon.

### 7.2 Hermes cron (SOURCE: tools/cronjob_tools.py in the clone; `cron/*.py` at the pinned SHA via WebFetch, SECONDARY)

- Storage: JSON file `<HERMES_HOME>/cron/jobs.json`, atomic temp-file write + fsync + directory fsync, mode 0600; an in-process RLock plus an `flock` on `.jobs.lock` with a 30 s timeout. A shrink-merge restores jobs added by a concurrent writer (up to 5 attempts). Output per run: `cron/output/<job>/<timestamp>.md`, newest 50 kept.
- Job fields: `id` (12 hex), `name`, `prompt`, `skills`, `script`, `no_agent`, `context_from`, `workdir`, `model`, `provider`, `enabled_toolsets`, `schedule` (dict), `schedule_display`, `next_run_at`, `enabled`, `state`, `paused_at`, `paused_reason`, `repeat{times,completed}`, `last_run_at`, `last_status`, `last_error`, `last_delivery_error`, `last_failure`, `failure_streak`, `deliver`, `origin`, `created_at`, and claim fields `fire_claim{at,by}`, `pending_slot`.
- Schedules: natural phrases ("every monday 9am"), `every 30m`, 5+ field cron, ISO timestamps (naive are made aware in the configured Hermes timezone), `in 30m`. `croniter` is imported lazily. Cron is evaluated in the configured zone with DST fold handling (tries fold 0 and 1, takes the earliest strictly after base).
- Timezone: the user docs say jobs use the machine's local timezone (https://hermes-agent.nousresearch.com/docs/guides/cron-troubleshooting); the code has a configured zone with DST logic. Which wins per job is UNVERIFIED.
- Delivery: `deliver` target `local`, `origin`, `telegram`, `discord`, `slack`, `email`, `webhook`, ... or `platform:chat_id`; separate `failure_deliver`. Delivery failure is tracked apart from run failure (`last_status=delivery_failed`, `failure_streak` counts agent failures only).
- Ticker: `tick()` roughly every 60 s inside `hermes gateway run`, guarded by a file lock; jobs run in a bounded pool; recurring `next_run_at` is **advanced before execution**, so a crash gives at-most-once rather than a double run.
- Duplicate prevention: `claim_job_for_fire` takes a per-job fence and writes `fire_claim{at, by=<machine_id>:<uuid>}` with a TTL; a younger claim loses. Completion with a stale owner is discarded. `cron/executions.db` (SQLite) records every attempt (`status` claimed/running/completed/failed/unknown, `scheduled_instant`, `process_id`, `progress_at`, `delivery_outcome`); `completed_occurrence` skips a slot whose scheduled instant already completed. After a restart, rows owned by dead processes become `unknown` ("side effects may or may not have run"). Max 1000 terminal rows kept.
- Missed runs: a recurring job more than one period stale is **fast-forwarded and fired once**, not once per missed slot; `cron.catch_up_missed=false` skips instead. Grace is half the cadence clamped to 120 s to 7200 s; one-shots past 120 s are retired with a diagnostic file.
- Controls: `pause_job`, `resume_job` (an elapsed recurring `next_run_at` is kept so the catch-up policy applies), `trigger_job` ("run now" is queued for the **next tick**, the CLI `cron run` says so), `repeat`. Event path: `execute_job_for_event(job_ref, extra_prompt)` fires an existing job through the same claim code (`cronjob_tools.py:419-448`), used by the webhook adapter.
- Pluggable scheduler: `cron.provider` selects a `CronScheduler` provider (`plugins/cron_providers/__init__.py:1-9`); the built-in in-process scheduler is the fallback and cannot be removed.
- Practitioner signal: closed issue #120330 (opened 2026-09-23, closed 2026-09-28): `hermes cron run` from a CLI process ran the agent turn and then failed delivery because only the gateway holds the live adapter, and it overwrote `last_status` with `delivery_failed` on a healthy job. ANECDOTE with maintainer fix (PR #126156). Lesson for the kernel: **separate "run" from "delivery" in the run record.**

### 7.3 OpenJarvis scheduler (SOURCE: `src/openjarvis/scheduler/`)

- Two tables (`scheduler/store.py`): `scheduled_tasks(id, prompt, schedule_type cron|interval|once, schedule_value, context_mode, status, next_run, last_run, agent, tools, metadata)` and `task_run_logs(id, task_id, started_at, finished_at, success, result, error)`. One shared connection, `check_same_thread=False`, no lock in the store, no WAL, `INSERT OR REPLACE` for every update.
- Loop (`scheduler.py:191-205`): poll 60 s, run every due task **inline, sequentially**. `next_run` is computed **after** the run finishes (line 284-300), and for `interval` it is `now + seconds`, so schedules drift by run time.
- No claim, no run key, no coalescing, no catch-up policy: a crash mid-run leaves `next_run` in the past, so it re-runs on restart. `get_due_tasks` returns every active row due, including ones it already started if the loop is slow.
- Timezone: cron evaluated in `ZoneInfo(metadata.timezone)` with croniter, stored in UTC (`scheduler.py:310-334`). That part is fine.
- Verdict: a correct small example of the table shape, and a counter-example for duplicate prevention. Do not adopt as a dependency.

### 7.4 Other options

| Option | License | Timezone cron | One-shot | Durable restart | Dup-run prevention | Run history | Pause/resume/run-now | Retries | Event triggers | Weight |
|--------|---------|---------------|----------|-----------------|--------------------|-------------|----------------------|---------|----------------|--------|
| systemd timer (SOURCE: systemd.timer.xml, systemd.time.xml on GitHub main) | LGPL (UNVERIFIED) | `OnCalendar=` accepts `UTC`, local zone, or an IANA name suffix | `OnCalendar=` with a date | `Persistent=true` stores last trigger on disk and catches up one run after downtime | "if the unit to activate is already active ... it is not restarted" (no overlap), no run key | journald only | `systemctl start/stop`; no per-automation DB state | `Restart=` for services, not for timers | `.path` units for file arrival (UNVERIFIED this session) | Already used (nightly) |
| APScheduler 3.11.3 (PyPI, 2026-06-28; MIT) | MIT | Yes (pytz/zoneinfo) | `date` trigger | SQLAlchemyJobStore | `max_instances=1` default; **"Job stores must never be shared between schedulers"**; duplicates with several processes are documented in the 3.x FAQ and a Plotly forum thread | No built-in run history table | pause/resume/modify; `misfire_grace_time`, `coalesce` | no | no | 4.0 is alpha (`4.0.0a6` 2025-04-27; README: "pre-release ... may change in a backwards incompatible fashion without any migration pathway") |
| croniter 6.2.4 (PyPI; MIT; maintained under pallets-eco per its README) | MIT | "init with a TZ aware datetime" | n/a | n/a (a library) | n/a | n/a | n/a | n/a | n/a | Tiny; used by both Hermes and OpenJarvis |
| Celery beat | BSD-3 (UNVERIFIED this session) | yes | yes | needs broker | beat is a single process, a single point of failure (SECONDARY) | needs result backend | no | yes | no | Broker + workers + beat |
| Temporal | MIT | schedules (UNVERIFIED details) | yes | durable | workflow ids | yes | yes | yes | signals | Server + persistence + workers; the repo's own quick start runs a pre-built dev server. Overkill: docs/01_RESEARCH_AUDIT.md:86 already rejected workflow engines. |
| Prefect | Apache-2.0 (UNVERIFIED this session) | yes | yes | needs server | deployments | yes | yes | yes | automations | Server + DB |
| Windows Task Scheduler | OS | local time | yes | yes | "multiple instances" policy (page fetch returned 404, UNVERIFIED) | Event Log | yes | restart-on-fail settings (UNVERIFIED) | on logon, on event | Only to start the node at logon |
| watchdog (Apache-2.0, 3.9+) | Apache-2.0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | Windows uses `ReadDirectoryChangesW` with I/O completion ports; README warns CIFS shares need `PollingObserver`; README does not discuss duplicate events or partial writes | Library, laptop only |

Note on `ReadDirectoryChangesW`: it is a notification mechanism, not a log. Files that arrive while the watcher is not running are not reported. A reconcile scan at start is mandatory (7.7).

### 7.5 Recommendation: kernel-owned definitions, `tick()` function, systemd timer, tasks/jobs as the execution record

Principles (from the existing architecture):
- The kernel stays the single authority (docs/02_ARCHITECTURE.md:231). Definitions, run keys, history and audit live in `state.db`.
- Fire into what exists: `Tasks.create` (outcome task with budget, verification, checkpoint) and `Jobs.create` (fixed kinds, policy-authorized, laptop node pulls). No new execution engine.
- No new daemon. Keep the scheduling decision a pure function of the database and the clock: `tick(now)`.

Who calls `tick()`:
- **Primary: a systemd timer on the VM**, `OnCalendar=*:*:00` every minute, `AccuracySec=1s`, `Type=oneshot` running `python3 -m jarvis_kernel automations-tick`. Reasons: deploys restart `jarvis-kernel` and `jarvis-llm-proxy` often (daily note lists several redeploys per day); a oneshot has its own crash domain; the nightly timer already proves the pattern; nothing runs between ticks. Cost is one Python start per minute (about 0.1 s class, UNVERIFIED on the VM). `Persistent=true` is not needed: catch-up is in the database.
- Optional: the same function can also be called from an existing thread (for example once per request in `llm_proxy`, as `tasks.expire()` already is at lines 291-358) as a low-latency backstop. Because of the UNIQUE run key, double calls are harmless.

Where this uses Hermes cron: it does not store definitions there. Hermes cron remains for in-conversation reminders ("remind me at 5pm"), which the agent can create itself and which do not need kernel verification. If an automation needs an agent turn with delivery (private lane), the later step is: the kernel triggers a **pre-created, paused Hermes job by id** through the event entry point (`execute_job_for_event`, same claim code as manual runs), passing the run key as context. How the gateway exposes that endpoint (webhook platform route) is UNVERIFIED and should be tested in the lab first.

Why not put definitions in Hermes cron:
1. `jobs.json` is outside the kernel's authorization and audit.
2. A Hermes cron run is an agent turn with no outcome-task verification, no attempt budget, no `max_attempts`, no checkpoint.
3. Hermes's curator rewrites cron `skills` references during consolidation (`curator_backup.py:5-6`).
4. `execute_job_for_event` and `claim_job_for_fire` are exactly the right mechanics, but they are Hermes-internal and were still being fixed for delivery edge cases as of September 2026 (#120330).

### 7.6 `tick()` algorithm

```
tick(now):
  with db.tx():                                          # one IMMEDIATE tx, so two ticks serialise
    for a in SELECT * FROM automations WHERE enabled=1 AND state='scheduled' AND next_run <= now:
        slots = enumerate due slots from a.next_run up to now (cron/interval), cap 100
        late  = now - slots[-1]
        if a.trigger_kind == 'once' and late > a.grace_s:   -> run 'skipped_missed', state='completed'
        elif catchup == 'skip' and late > a.grace_s:        -> run 'skipped_missed' for each slot, advance
        elif catchup == 'one':  slots = [slots[-1]]         -> coalesce backlog into one run
        elif catchup == 'all':  keep up to max_catchup slots
        for s in slots:
            key = f"{a.id}:{iso_utc(s)}"                    # idempotent run key
            INSERT OR IGNORE INTO automation_runs(automation_id, run_key, scheduled_for, source, status)
            if rowcount == 0: continue                      # already fired by another tick or process
            if overlap == 'skip' and an earlier run's task/job is not FINAL: status='skipped_overlap'; continue
            if runs_today >= a.max_runs_per_day:             status='skipped_limit'; continue
            fire(a, key)    # Tasks.create(...) or Jobs.create(...), same tx; sets task_id / job_id; audit 'automation_fired'
        a.next_run = compute_next(a, after=max(slots) or now)  # advance BEFORE the work runs, as Hermes does
  after fire: settle(): copy final task/job state back to automation_runs.status, bump failure_streak, auto-pause at pause_after_failures
```

Properties:
- At-most-once per slot: the UNIQUE `(automation_id, run_key)` is the fence, and it holds across processes and restarts. A crash after the insert but before the task is created is detectable (`status='claimed'` with no `task_id`) and surfaced as `unknown`, as Hermes does, rather than silently re-run (side effects unknown).
- Catch-up is explicit per automation, default coalesce-to-one for recurring, grace-window skip for one-shots (Hermes defaults: half cadence, 120 s to 7200 s; one-shots 120 s).
- Manual run-now: `run_key = f"{id}:manual:{uuid}"`, `source='manual'`, never changes `next_run`. Hermes's mistake to avoid: do not make manual run wait for "the next tick" in the kernel; call `fire()` directly in the same request.
- Retries: use the task's `max_attempts` (1 to 5) and budget. The automation does not add a second retry layer; a failed run is one failure in `failure_streak`.
- Time: store `next_run` as UTC epoch. Compute with `zoneinfo` + `croniter` using a tz-aware base. Test DST gaps with `Europe/Berlin`/`America/New_York` fixtures (Asia/Kolkata has no DST, so it will not exercise this).

### 7.7 Where triggers must run

| Trigger | Runs on | Why |
|---------|---------|-----|
| cron / interval / once | VM (kernel `tick`) | Always on. Laptop sleeps (jobs expire after 30 min, `jobs.py:11`; the node is "simply absent", docs/02_ARCHITECTURE.md:47) |
| Work that needs the laptop (open app, save output to D:\) | Fired on the VM, queued as a `jobs` row for node `laptop`; the node pulls it within 15 s (`node/jarvis_node/agent.py:47-69`) | Same path as today's `output_save`, `pc_open` |
| File arrival in a laptop folder | **Laptop node**: `watchdog` (ReadDirectoryChangesW) in the node process | The VM has no filesystem access to D:\ and the lab cannot see it (docs/02_ARCHITECTURE.md:143-). Only a process on the laptop can observe it |
| File arrival in a VM folder | VM: a systemd `.path` unit (UNVERIFIED this session) or the same tick scanning with a size/mtime check | |
| Webhook / chat message | VM gateway (Hermes webhook adapter or kernel endpoint) | |

Event rules for the laptop watcher:
1. Debounce: report a file only after its size and mtime are unchanged for about 2 s (partial writes, editors that write twice; the watchdog README does not address either, so this is our job).
2. Dedupe key = `sha256(watch_id | relative path | size | mtime_ns)`; the kernel inserts it as the run key, so a duplicate event produces no second run.
3. **Reconcile at node start and every N minutes**: list the folder, compare with the kernel's `automation_runs` keys for that watch, and post any that are missing. This covers laptop-off periods, sleep and watcher failure.
4. The node posts only `{watch_id, relative_path, size, mtime_ns}` to the kernel (token-authenticated like `/jobs/result`); the kernel resolves watch id to an automation, applies policy, and creates the task. The node never decides what runs, matching the job whitelist model (jobs.py:19-27).
5. The folder to watch comes from the node's own allowlist file (like `config/projects.json`), never from a message.

### 7.8 Timezone prerequisite (MEASURED)

The laptop Python has no tz database: `ZoneInfo("Asia/Kolkata")` raised `ZoneInfoNotFoundError`. On Windows, `zoneinfo` needs the `tzdata` PyPI package (data only, Apache-2.0; UNVERIFIED license text this session, widely known). The VM likely has `/usr/share/zoneinfo` (UNVERIFIED). Install `tzdata` on any machine that evaluates schedules and add a startup check: `ZoneInfo(automation.tz)` must resolve or the automation is rejected at creation, not at fire time. The kernel (VM) is the only evaluator in the recommended design, so the laptop need not carry it unless the node shows local times.

### 7.9 Why this meets "smallest design"

New code: two tables, one `automations.py` (about 200 lines), one CLI subcommand, one timer unit, one node watcher module. Reused: `Tasks`, `Jobs` + `_check_params` whitelist, policy and approvals, audit, `traces.analyze` (automation runs appear as tasks, so success rates, corrections and failure classes come for free), the 15 s node poll, systemd. Not added: broker, server, second scheduler process, second store of definitions.

---

## 8. Practitioner and community reports (Reddit not reachable)

I could not read Reddit with these tools (see top). Instead, with labels:

| Topic | Source | Date | Label | What it says |
|-------|--------|------|-------|--------------|
| Memory write clobber | GitHub NousResearch/hermes-agent #26045 | opened 2026-05-15, closed 2026-05-23 | ANECDOTE confirmed by maintainer fix (P0 label, PR #30877) | `memory replace` rewrote MEMORY.md and lost about 8 KB of externally added content; `.bak` held only the post-write state. Production agent, no hardware claim. |
| Cron manual run vs delivery | hermes-agent #120330 | 2026-09-23 to 09-28 | ANECDOTE with fix (PR #126156) | CLI-run job executed then failed delivery, overwrote `last_status`. Run and delivery must be recorded separately. |
| APScheduler duplicates | Plotly community thread "Apscheduler is executing job multiple times even with --preload (gunicorn)" (https://community.plotly.com/t/apscheduler-is-executing-job-multiple-times-even-with-preload-gunicorn/83114) | date not captured | ANECDOTE | Three gunicorn workers ran an interval job 3x; `--preload` made it 4x because monkey-patching order started the scheduler in every worker. |
| APScheduler design limit | APScheduler 3.x FAQ (https://apscheduler.readthedocs.io/en/3.x/faq.html) | n/a | SOURCE | Sharing a persistent job store between processes causes duplicate or missed jobs; there is no inter-process synchronisation. |
| n8n double firing | community.n8n.io "double firing cron problem" (https://community.n8n.io/t/double-firing-cron-problem/297216) | 2026 (day not captured) | ANECDOTE | Two executions with different ids at the same timestamp indicated two scheduler processes registering triggers. |
| Duplicate-run guidance | oneuptime blog 2026-09-14, "Prevent Duplicate Cron Side Effects with Stable Business Keys"; cronitor guide | 2026 | SECONDARY (vendor blogs) | A lock reduces overlap, but a durable uniqueness rule in the database survives lock, process and scheduler; key the work by what it does, not by the execution. This is exactly the run-key design above. |
| Agent crash recovery | dev.to "Why your production agents can't forget" (claims to summarise Reddit builder communities) | 2026 | SECONDARY, attribution UNVERIFIED | Keep session state in durable storage; resume the interrupted step. Matches `Tasks.checkpoint`. |
| Memory poisoning, web | Unit 42 Bedrock PoC (https://unit42.paloaltonetworks.com/indirect-prompt-injection-poisons-ai-longterm-memory/) | 2025-10-09 | Vendor PoC | See 5.5. |
| Memory poisoning, research | arXiv 2503.03704 (MINJA), 2604.02623 (eTAMP) | 2025-03, 2026-04 | Research | See 5.5. |
| Token runaway | HN "Burned $250 in tokens on Day 1 with OpenClaw" (3 points, 1 comment) | 2026-02-26 | ANECDOTE | One data point supporting per-automation run and budget limits. |

Overall: the duplicate-run problem is well evidenced (docs and several forum reports) and has one robust answer (database-level uniqueness). Memory poisoning is evidenced in research and a vendor PoC, with success rates from the lab papers (not field data). Nothing here is hardware-specific except the sqlite-vec benchmarks (Apple M1 class, 8 to 16 GB).

---

## 9. Proposed schemas

All tables go in `state.db`. Creation follows the kernel's current style, but with `PRAGMA user_version` migrations.

### 9.1 `automations`

```sql
CREATE TABLE automations(
  id            TEXT PRIMARY KEY,                    -- 'a-' + 8 hex (same style as m-xxxxxxxx)
  owner         TEXT NOT NULL,                       -- agent name or 'owner'
  name          TEXT NOT NULL,
  agent         TEXT,                                -- agent_defs.name; NULL for a deterministic job
  action_kind   TEXT NOT NULL CHECK(action_kind IN ('task','job')),
  action        TEXT NOT NULL,                       -- JSON; task: {goal,criteria,plan,lane,max_attempts,budget_s};
                                                     --       job : {kind,params} validated by jobs._check_params
  trigger_kind  TEXT NOT NULL CHECK(trigger_kind IN ('cron','interval','once','event')),
  schedule      TEXT,                                -- cron expr | seconds | ISO-8601 with offset
  tz            TEXT NOT NULL DEFAULT 'UTC',         -- IANA, must resolve at create time
  node          TEXT NOT NULL DEFAULT 'server',      -- where an event is observed / the job runs: server|laptop|lab
  event_spec    TEXT,                                -- JSON: {watch_id, glob?, debounce_s}
  enabled       INTEGER NOT NULL DEFAULT 1,
  state         TEXT NOT NULL DEFAULT 'scheduled' CHECK(state IN ('scheduled','paused','completed','error')),
  paused_reason TEXT,
  next_run      REAL,                                -- UTC epoch; NULL when none
  catchup       TEXT NOT NULL DEFAULT 'one' CHECK(catchup IN ('skip','one','all')),
  grace_s       INTEGER NOT NULL DEFAULT 300,
  overlap       TEXT NOT NULL DEFAULT 'skip' CHECK(overlap IN ('skip','queue')),
  repeat_times  INTEGER,                             -- NULL = forever
  repeat_done   INTEGER NOT NULL DEFAULT 0,
  max_runs_per_day    INTEGER NOT NULL DEFAULT 24,
  pause_after_failures INTEGER NOT NULL DEFAULT 3,
  failure_streak INTEGER NOT NULL DEFAULT 0,
  expires       REAL,
  approval_id   TEXT,                                -- the approval that authorised creating it
  created_by    TEXT, created REAL, updated REAL,
  version       INTEGER NOT NULL DEFAULT 1           -- optimistic concurrency like memories
);
CREATE INDEX automations_due ON automations(enabled, state, next_run);
```

### 9.2 `automation_runs`

```sql
CREATE TABLE automation_runs(
  id            INTEGER PRIMARY KEY,
  automation_id TEXT NOT NULL REFERENCES automations(id),
  run_key       TEXT NOT NULL,                       -- 'a-xxxx:2026-10-12T03:00:00Z' | 'a-xxxx:manual:<uuid>' | 'a-xxxx:ev:<sha256>'
  scheduled_for REAL,
  fired_at      REAL,
  source        TEXT CHECK(source IN ('schedule','catchup','manual','event')),
  status        TEXT NOT NULL CHECK(status IN ('claimed','fired','done','failed','skipped_overlap','skipped_missed','skipped_limit','refused','unknown')),
  task_id       TEXT,                                -- tasks.id
  job_id        TEXT,                                -- jobs.id
  delivery      TEXT,                                -- kept apart from status (Hermes #120330)
  detail        TEXT,
  UNIQUE(automation_id, run_key)
);
CREATE INDEX automation_runs_a ON automation_runs(automation_id, id DESC);
```

### 9.3 `agent_defs` (extends existing `agent_tokens(name, token_h, created, lane)`, `llm_proxy.py:56-59`)

```sql
CREATE TABLE agent_defs(
  name         TEXT PRIMARY KEY,              -- same value as agent_tokens.name; the token itself stays in agent_tokens
  role         TEXT NOT NULL,                 -- planner | lab_worker | verifier | researcher | ...
  lane         TEXT NOT NULL DEFAULT 'private',
  runtime      TEXT NOT NULL,                 -- hermes | opencode | goose  (jobs.LAB_AGENTS)
  profile      TEXT,                          -- Hermes profile / HERMES_HOME name
  allowed_kinds TEXT NOT NULL DEFAULT '[]',   -- JSON list of jobs.KINDS this agent may request
  toolsets     TEXT NOT NULL DEFAULT '[]',
  memory_scope TEXT NOT NULL DEFAULT 'default',
  prompt_ref   TEXT,                          -- vault path + git sha, not the text
  model_pref   TEXT,                          -- provider name from providers.json, advisory
  max_attempts INTEGER NOT NULL DEFAULT 3,
  budget_s     INTEGER NOT NULL DEFAULT 3600,
  max_runs_per_day INTEGER NOT NULL DEFAULT 24,
  enabled      INTEGER NOT NULL DEFAULT 1,
  version      INTEGER NOT NULL DEFAULT 1,
  history      TEXT NOT NULL DEFAULT '[]',    -- like memories.history
  created REAL, updated REAL
);
```
An automation's `agent` must exist in `agent_defs` and be `enabled`; the automation's task `lane` cannot be wider than the agent's lane (existing rule: private-to-public dispatch needs an explicit "may be shared" flag, docs/02_ARCHITECTURE.md:143-).

### 9.4 Memory additions and FTS

```sql
-- on memories (migration via user_version)
ALTER TABLE memories ADD COLUMN source TEXT NOT NULL DEFAULT 'owner_said'
      CHECK(source IN ('owner_said','agent_inferred','external_content'));
ALTER TABLE memories ADD COLUMN origin_task TEXT;
ALTER TABLE memories ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 1;     -- 0 until the owner approves non-owner_said entries
CREATE INDEX memories_owner_scope ON memories(owner, scope, state);
CREATE UNIQUE INDEX memories_unique_active ON memories(owner, scope, text) WHERE state='active';

-- searchable lessons / corrections / notes (NOT the 200 preference entries)
CREATE TABLE notes(id TEXT PRIMARY KEY, kind TEXT, title TEXT, text TEXT, source TEXT, ref TEXT, state TEXT DEFAULT 'active', updated REAL);
CREATE VIRTUAL TABLE notes_fts USING fts5(title, text, content='notes', content_rowid='rowid', tokenize='porter unicode61 remove_diacritics 2');
CREATE TRIGGER notes_ai AFTER INSERT ON notes BEGIN
  INSERT INTO notes_fts(rowid,title,text) VALUES (new.rowid,new.title,new.text); END;
CREATE TRIGGER notes_ad AFTER DELETE ON notes BEGIN
  INSERT INTO notes_fts(notes_fts,rowid,title,text) VALUES('delete',old.rowid,old.title,old.text); END;
CREATE TRIGGER notes_au AFTER UPDATE OF title,text ON notes BEGIN
  INSERT INTO notes_fts(notes_fts,rowid,title,text) VALUES('delete',old.rowid,old.title,old.text);
  INSERT INTO notes_fts(rowid,title,text) VALUES (new.rowid,new.title,new.text); END;
-- query:  SELECT n.id, n.title FROM notes_fts f JOIN notes n ON n.rowid=f.rowid
--         WHERE notes_fts MATCH ? AND n.state='active' ORDER BY f.rank LIMIT 10;
```
Query hygiene (borrowed from Hermes retrieval, `plugins/memory/holographic/retrieval.py:192`): FTS5 ANDs multi-word queries and raises on operator characters, so quote each token and OR them, and catch `OperationalError`.
Vectors, if later needed: `ALTER TABLE notes ADD COLUMN emb BLOB` (float32, 1,536 B), loaded into a cached matrix; switch to sqlite-vec only past about 5k notes without numpy.
Delete is a real `DELETE` (or blanking) in one `tx()`; the trigger removes it from the index.

### 9.5 `corrections`

```sql
CREATE TABLE corrections(
  id TEXT PRIMARY KEY, task TEXT, memory_id TEXT, skill TEXT, category TEXT,
  wrong TEXT, right TEXT, state TEXT DEFAULT 'provisional' CHECK(state IN ('provisional','confirmed','retired')),
  confirmed_by_task TEXT, created REAL
);
```

---

## 10. Adopt as pattern vs add dependency vs skip

| Item | Decision | Reason |
|------|----------|--------|
| SafeDB `tx()` with SAVEPOINT nesting | Pattern, own code | 20 lines, tested; solves the open issue |
| Hermes reader-pool + one writer (`hermes_state.py`) | Pattern | Same shape as traces.py already |
| Hermes cron claim/fence, scheduled-instant dedupe, fast-forward, `unknown` after restart | Pattern | Proven; reimplemented with a UNIQUE key |
| Hermes cron as store of record for owner automations | Skip | Outside kernel authority and audit |
| Hermes cron for in-agent reminders | Keep | Already deployed; no change |
| Hermes holographic FTS5 schema and triggers | Pattern | Verified by my tests |
| Hermes built-in memory (substring locate) | Skip (already disabled) | No identity |
| mem0 | Skip (pattern: history of change reasons) | Needs LLM per write; ADD-only conflicts with "forget means forget" |
| Letta | Skip | Server product, last-write-wins blocks |
| Zep/Graphiti | Skip (pattern: validity window for facts) | Graph DB + LLM extraction |
| Basic Memory | Skip | AGPL, second index; vault already markdown |
| OpenJarvis scheduler/memory | Skip | No claims, Rust-mandatory memory |
| OpenJarvis `LearningOrchestrator` | Skip as template | Applies before evaluating, no rollback |
| OpenJarvis trace schema | Pattern (already adapted) | Field list only |
| `croniter` (MIT) | Add dependency | Tiny, used by Hermes and OpenJarvis; cron maths is easy to get wrong across DST |
| `tzdata` | Add dependency (data) | Required for zoneinfo on Windows |
| `watchdog` (Apache-2.0) | Add dependency, laptop node only | ReadDirectoryChangesW wrapper |
| APScheduler 3.x | Skip | Not multi-process safe, no run history; 4.x pre-release |
| Celery beat, Temporal, Prefect | Skip | Broker/server/DB; docs/01_RESEARCH_AUDIT.md:86 |
| sqlite-vec | Skip for now | Pre-v1; flat scan suffices; revisit past about 5k embedded notes without numpy |
| sqlite-vss | Skip | Deprecated |
| Chroma, LanceDB, Qdrant | Skip | Second store outside audit |
| OWASP Agent Memory Guard | Read only | Promotional post; pattern list can be checked against our own scanner |

---

## 11. Unverified or not done

- VM facts: FTS5 in the Ubuntu system Python, `/usr/share/zoneinfo`, SQLite version, fsync latency, sqlite3 `threadsafety`, numpy availability, tick process start cost.
- sqlite-vec was not installed or run; its figures are the authors' and a competitor's. A Linux aarch64 wheel for 0.1.9 was not confirmed.
- Hermes `cron/*.py` and `hermes_state.py` are summaries of the GitHub raw files at SHA 818c13be by a small model, not read by me line by line; the clone lacks those packages.
- Whether Hermes's per-job timezone follows the configured zone or the machine's (docs and code differ).
- How to call `execute_job_for_event` from the kernel (webhook route config) is untested.
- Windows Task Scheduler behaviour (Microsoft docs fetch returned 404), systemd `.path` units, Celery and Prefect licenses and details: not fetched this session.
- Reddit threads: not retrievable with the available tools.
- The mechanism behind the earlier live 401/404 (lazy cursor) is inferred from the daily note; my repro covers the transaction-joining failure mode, not cursor invalidation.
- All timing numbers are single runs on one laptop; relative sizes (lock vs split, tokenizer costs) are more trustworthy than absolute milliseconds.

## 12. Evidence files

Scratch scripts (not in hq): `C:\Users\venkat\AppData\Local\Temp\claude\d--jarvis\1b9a1699-227a-4978-933b-5bf113a23b99\scratchpad\` : `chk.py` (FTS5/threadsafety), `chk2.py` (zoneinfo/modules), `exp1.py` (transaction interleaving), `exp2.py` (FTS5 benchmark), `exp3.py` (lock vs readers), `exp4.py` (tx() stress), `exp5.py` (flat scan).

Primary URLs: https://www.sqlite.org/wal.html, https://www.sqlite.org/fts5.html, https://docs.python.org/3/library/sqlite3.html, https://github.com/asg017/sqlite-vec, https://github.com/asg017/sqlite-vss, https://alexgarcia.xyz/blog/2024/sqlite-vec-stable-release/index.html, https://marcobambini.substack.com/p/the-state-of-vector-search-in-sqlite, https://github.com/mem0ai/mem0, https://docs.mem0.ai/migration/oss-v2-to-v3.md, https://docs.letta.com/guides/agents/memory-blocks, https://github.com/getzep/graphiti, https://github.com/basicmachines-co/basic-memory, https://github.com/agronholm/apscheduler, https://apscheduler.readthedocs.io/en/3.x/userguide.html, https://github.com/pallets-eco/croniter, https://github.com/gorakhargosh/watchdog, https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.timer.xml, https://github.com/NousResearch/hermes-agent (issues 26045, 120330), https://arxiv.org/abs/2503.03704, https://arxiv.org/abs/2604.02623, https://unit42.paloaltonetworks.com/indirect-prompt-injection-poisons-ai-longterm-memory/.
