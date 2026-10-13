# 01 Repository audit: what JARVIS actually is today

Audit of `D:\jarvis\hq` (git, branch `main`). Started at HEAD `cb714a6`; HEAD moved to `205a1a9` during the audit (docs-only: 2 lines in
`docs/02_ARCHITECTURE.md` and `docs/03_DECISIONS.md`, no code). Working tree clean. Date of audit: 2026-10-11.

Method: every source file in `kernel/`, `node/`, `lab/`, `hub/`, `hermes/`, `skills/skillgate.py`, `evals/run_evals.py`, `scripts/nightly.py`,
`deploy/*.service`, `config/*` (key names and non-secret fields only) and all five docs were read directly. Nothing was executed except
read-only `git`, `grep` and small JSON reads/recomputations of `bench/data`. No tests were run (a baseline run is in progress elsewhere).
No network calls. `D:\jarvis-secrets` was not opened. No secret value is quoted here.

Legend: **[code]** read in source, **[doc]** claimed only in docs, **[calc]** recomputed by me from bench data, **UNVERIFIED** could not be
confirmed from the repo (usually because it lives on the server, in the WSL box, or in Hermes's own config outside the repo).

---------------------------------------------------------------------------------------------------------------------------------------

## 0. Ten-line summary

1. Everything human-facing goes through channels that are now off or console-only. Telegram is off (`config/telegram.off`, D13); the hub is
   read-only; approvals are given only by `python3 -m jarvis_kernel approve <id>` at the server console (`cli.py:78-93`). There is **no
   owner-authenticated HTTP API** for "send a command", "approve", "pause/cancel a task", or "kill".
2. Three loops lived inside the Telegram bridge process and are therefore **not running**: tell JARVIS when lab work ended
   (`telegram.py:412-448`), fail jobs stuck in `running` (`jobs.py:142-153`, only called from `telegram.py:313`), and announce approvals.
3. The kernel is two small `http.server` processes sharing one SQLite file (`state.db`, WAL): a node endpoint on :8765 and the agent/model
   endpoint on :8790. Real work (planning, tool use) is done by Hermes (an external LLM agent) calling kernel tools through MCP.
4. The task engine is real and durable (SQLite): 10 states, checked transitions, attempt and deadline budgets, independent verifier, runner-side
   delivery. Plan, routing and "what is a task" are decided by an LLM, not by deterministic code. No sub-tasks, no scheduler, no automations.
5. Windows control = open apps from the Start list, open known folders/files, launch notepad/calc/mspaint, UIA click/type by element name.
   **No Windows screenshot, no UIA snapshot job, no browser job.** Browser (Playwright) and UIA snapshot exist as libraries, unwired.
6. The "Agentic OS desktop" (noVNC + Take control/Continue) exists but is a **Linux XFCE desktop inside WSL**, not the Windows laptop.
7. Hub is one 984-line vanilla-JS file polling JSON; of the nine screens you list, two exist in usable form (desktop, tasks monitor), five
   partially, two not at all (Agent Factory, Automation Studio).
8. Voice: nothing implemented (no STT/TTS/wake word anywhere). Docs list it as out of scope.
9. Private lane capacity is the practical ceiling: Groq 8000 tokens input+output per request, OpenRouter free 50 requests/day, VM CPU model
   4096 context. The owner's own agent (private lane) sends ~85k-character requests (`docs/01_RESEARCH_AUDIT.md:125`).
10. Measured quality is modest where it matters: live "completion accuracy 0.25, verifier agreement 0.33" (`vault/daily/2026-10-10.md:92`).

---------------------------------------------------------------------------------------------------------------------------------------

## 1. Architecture as implemented

### 1.1 Processes and where they run

| Process | Host | Listens / schedule | Source of truth |
|---|---|---|---|
| **Kernel node endpoint** `python -m jarvis_kernel serve` | Oracle VM `hermes-agent-vm` (Ubuntu aarch64, Tailscale 100.125.91.51) | 100.125.91.51:8765, refuses 0.0.0.0 (`cli.py:105-110`) | `deploy/jarvis-kernel.service`; `nodes.py:75-149` |
| **Model / agent-action endpoint** `jarvis_kernel llm-proxy` | same VM | 127.0.0.1:8790 + 100.125.91.51:8790 (`cli.py:121-125`, `llm_proxy.py:691-697`) | `deploy/jarvis-llm-proxy.service` |
| Telegram bridge | same VM | **disabled** (`cli.py:115-119`, `config/telegram.off`) | `deploy/jarvis-telegram.service` |
| Local models: Qwen3-4B (:8080, `-c 4096 -np 1 -t 2`), bge-small (:8081) | same VM | loopback | `deploy/jarvis-llm.service`, `jarvis-embed.service` |
| Smokescreen egress proxy | same VM | 100.125.91.51:4750 | `deploy/jarvis-smokescreen.service` |
| Hermes gateway (API server) and Hermes dashboard (user `jarvis-agent`) | same VM | 127.0.0.1:8642; 100.125.91.51:9119 | `deploy/jarvis-hermes-*.service` |
| OmniRoute 3.8.51 | same VM | :20128, routes nothing (docs 02:91) | `deploy/jarvis-omniroute.service` |
| Nightly regression | same VM | systemd timer 21:00 UTC | `deploy/jarvis-nightly.timer`, `scripts/nightly.py` |
| monit | same VM | process/disk/link checks | `deploy/monit-jarvis.conf` |
| **Laptop node** `python -m jarvis_node run` | Windows 10 laptop | outbound only; heartbeat 15 s, jobs polled every beat (`agent.py:47-69`) | autostart mechanism UNVERIFIED (not in repo) |
| Laptop llama.cpp Vulkan server | Windows | 127.0.0.1:8080 | `scripts/start-local-llm.ps1` |
| **Lab runner** | WSL `JarvisLab`, as root | polls kernel every 10 s (`lab_runner.py:35,451-465`) | `lab/deploy/jarvis-lab-runner.service` |
| **Lab control** (take/continue, screen thumbnail) | WSL, root | Unix socket `/run/jarvis-lab/control.sock` (`lab_control.py:39-41,396-413`) | `lab/deploy/jarvis-lab-control.service` |
| Caddy "hub" | WSL, user `hub` | hub.localhost:8088 on 127.0.0.1 (`hub/Caddyfile:1-38`) | `hub/Caddyfile` |
| Agent PC desktop (TigerVNC :1 + noVNC :6080, XFCE) | WSL | loopback | docs 02:94, 100 [doc] |
| Lab agents Hermes / OpenCode (:4096) / goose + `jarvis_mcp.py` (stdio) | WSL, user `agent` | per task | `lab/jarvis_mcp.py` |
| OpenJev (CPU, :8091) | WSL (and a Windows test copy `D:\jarvis-apps\openjev`) | 127.0.0.1:8091 | `hub/index.html:419`; `bench/openjev_risk.py:49` |
| openmuse :8081, dashy status-check :4000 | WSL | loopback | `hub/Caddyfile:59-62,12-14`; docs 02:97 says openmuse "not running" [doc] |

The two kernel servers are **separate OS processes**; each calls `Kernel(args.data)` (`cli.py:73`), so each holds its own SQLite
connection to the same `~/.jarvis-kernel/state.db` (`store.py:65-68`).

### 1.2 How the parts talk

| From -> To | Transport | Auth scheme (names only) | Evidence |
|---|---|---|---|
| Laptop node -> kernel :8765 | HTTP/JSON over Tailscale, plaintext | `Authorization: Bearer <node token>` (hash in `nodes.token_h`); first contact: one-time pairing code | `nodes.py:96-139`, `agent.py:18-31` |
| Agents, plugins, skillgate, hub (via Caddy), lab runner -> :8790 | HTTP/JSON, plaintext | `Authorization: Bearer <agent token>`; token carries a **lane**: `private`, `public`, `dashboard`, `runner` | `llm_proxy.py:50-79,410-412` |
| Kernel Telegram bridge -> Hermes API :8642 | HTTP, `POST /v1/chat/completions`, model `hermes-agent` | Bearer Hermes API key + header `X-Hermes-Session-Id` | `telegram.py:88-105` |
| Hub browser -> Caddy :8088 -> kernel :8790 `/v1/jarvis/overview` | HTTP | dashboard-lane token injected by Caddy; browser never sees it | `hub/Caddyfile:24-32` |
| Hub browser -> Caddy `/control/*` -> `lab_control` socket | HTTP over Unix socket | header `X-Jarvis-Hub: 1` only; socket perms root:hub 0660 | `lab_control.py:359-376`, `hub/Caddyfile:15-18` |
| Hub browser -> Caddy `/pc/*` -> noVNC | WebSocket | VNC password asked once (docs 05:41) [doc] | `hub/Caddyfile:19-23` |
| Lab runner -> :8790 `/v1/jarvis/runner/*` | HTTP | runner-lane token (root-only file) | `lab_runner.py:28-52` |
| `jarvis_mcp.py` tools -> :8790 `/v1/jarvis/actions` | HTTP | agent token (`~/.jarvis-llm-token`) | `jarvis_mcp.py:22,39-58` |
| Hermes memory plugin -> :8790 `/v1/jarvis/actions` | HTTP | agent token or profile token file | `hermes/plugins/jarvis_memory/__init__.py:71-85` |
| Gateway -> model providers | HTTPS | per-provider key file read at call time (`key_file`) | `llm_proxy.py:565-569` |
| Browser lib -> Smokescreen | HTTP proxy | none (Tailscale-only bind) | `browser.py:129-138` |

Conventions that matter for anything you add: **nodes connect outbound only** (kernel never dials a node, `nodes.py:1`); **the lane comes
from the token, never from the request** (`llm_proxy.py:134`); **kernel is the only authority** for approvals, tasks, memory.

### 1.3 Data stores (as implemented)

SQLite `state.db` on the VM (all kernel tables, see 2.4); `KILL` file next to it (`store.py:74`); Hermes `state.db`, sessions, MEMORY.md on
the VM under `/home/jarvis-agent/.hermes` (outside this repo); lab workspaces `~agent/work/jobs/<task>` (`lab_runner.py:33`);
laptop `D:\jarvis\outputs\<agent>\` (`node/jobs.py:93-95`); `skills/active.json` in the repo (skill activation record);
`~/jarvis/bench-results/<date>.json` (`scripts/nightly.py:19,63`); vault (Markdown, git) with a one-way guard (`vault.py:1-60`).

### 1.4 Where docs and code disagree (found during this audit)

| Doc claim | Reality in code |
|---|---|
| Kernel has a "scheduler" that picks nodes by lane, latency, cost (`02_ARCHITECTURE.md:14,47`) | No scheduler exists. `OUTPUT_NODE` is an env string, default `"laptop"` (`llm_proxy.py:36`); lab jobs go to node `"lab"` (`llm_proxy.py:349`). |
| "Workflow engine = state machine in the kernel plus Hermes cron" (`03_DECISIONS.md:35`) | No cron/automation code. Hermes cron toolset is off (`02_ARCHITECTURE.md:83`); `cronjob` is in skillgate's UNAVAILABLE set (`skills/skillgate.py:60`). |
| Overview is "counts, states, names and times only ... no prompt text" (`overview.py:1-5`) | The task list copies `request`, `goal`, `plan`, `criteria`, `result`, `evidence`, `verification`, `events` (`overview.py:113-115`). Free text of the owner's request reaches the browser. |
| "Forget means forget" / "verified deletion" (`memory.py:1-10`) | `forget` sets `state='deleted'` and keeps `text` and up to 20 history versions in the row (`memory.py:127,126`). Logical delete only. |
| Task state `waiting_approval` (`tasks.py:15`) | Nothing in code sets it; approvals are linked to action hashes, not task ids. `parent` column is never written (`tasks.py:49-51,56`). |
| "Correction" events used by traces/analyzer (`traces.py:128,136`) | No code path writes `kind="correction"`; the 10-10 correction was entered by hand (docs 02:194). |
| `models.json` = "Model registry (spec section 12)" (`config/models.json`) | Only read by Telegram `/models` (`telegram.py:450-461`); the gateway ignores it (uses `providers.json`). |
| `evals/baseline.json` covers the suite | Baseline has 19 results; `run_evals.py` now has 23 `@case` (grep count). Four newer cases are not in the baseline. |
| Policy lists `ui focus|key|scroll` YELLOW_APPROVAL (`policy.py:43`) | Jobs accept only `launch|click|type` (`jobs.py:17`). |
| `docs/04_BUILD_PLAN.md:90` "NL-to-structured-action translator: not built" | Superseded in practice: Hermes (LLM) calls structured MCP tools; the kernel only validates structured actions. There is still no deterministic NL front door. |
| 01_RESEARCH_AUDIT:35 "Jev: REJECT for now" vs 03_DECISIONS:96 OpenJev installed | Both true: OpenJev is a bench/test tool, not in the kernel path (no kernel code references it). |

---------------------------------------------------------------------------------------------------------------------------------------

## 2. Kernel (`kernel/jarvis_kernel`, ~3.5k lines)

### 2.1 HTTP surface

**Server A: node endpoint** (`nodes.py:75-149`, `ThreadingHTTPServer`, POST only, body <= 256 KiB, any exception -> 400).

| Route | Auth | Request | Response |
|---|---|---|---|
| `POST /pair` | none (one-time code, 600 s TTL, single use) | `{code, name}` | `{token}` or 403 (`nodes.py:96-98`, `30-47`) |
| `POST /report` | node bearer | `{state: {...}}` (collector output) | `{ok, killed}`; writes `nodes.state`, one audit row per call (`nodes.py:99-104,49-58`) |
| `POST /jobs/next` | node bearer | `{}` | `{job: {id, kind, params} | null, killed?}` (`nodes.py:105-116`) |
| `POST /jobs/result` | node bearer | `{id, result}` | `{accepted}` (`nodes.py:117-119`) |
| `POST /request` | node bearer | `{action:{tool,op,target,params,lane,untrusted_session}}` | `{level, reason, approval_id}` (creates a pending approval) (`nodes.py:120-134`) |
| `POST /authorize` | node bearer | same + `approval_id` | `{allowed, level, reason}` (consumes approval) (`nodes.py:135-139`) |

Note: `lane` and `untrusted_session` are taken from the node's request (`nodes.py:129-131`), not verified.

**Server B: model + agent API** (`llm_proxy.py:396-688`, `ThreadingHTTPServer`, HTTP/1.0, body <= 2 MiB).

| Route | Token lane required | Request -> response |
|---|---|---|
| `GET /v1/jarvis/overview` | `dashboard` only (`:415-422`) | KPIs, 24 h series, agents, providers, nodes, outputs, last 15 tasks, pending approvals, last 25 jobs, last 40 audit rows (`overview.py:118-136`) |
| `GET /v1/models` | any valid token (`:423-434`) | one model `jarvis`; `context_length` = lane max, raised to 64000 (`sizing.py:133-141`) |
| `GET /v1/jarvis/jobs/<id>` | any agent token (`:435-440`) | job state/result, **only for jobs that agent created** (`jobs.py:113-120`) |
| `POST /v1/chat/completions` | `private` or `public` (dashboard 403 `:448`, runner 403 `:458`) | OpenAI-compatible; streamed or JSON; see 5 |
| `POST /v1/jarvis/actions` | `private` or `public` | `{tool, op, target, params, approval_id?}` -> `{status, ...}` (below) |
| `POST /v1/jarvis/runner/next` | `runner` only (`:450-468`) | `{job | null}` next `lab_run` job (`:359-361`) |
| `POST /v1/jarvis/runner/status` | runner | `{job}` -> `{state}` of the task (`:367-369`) |
| `POST /v1/jarvis/runner/report` | runner | `{job, state?, step?, note?, result?, evidence?, verification?, attempt?}` -> task update (`:370-373`) |
| `POST /v1/jarvis/runner/deliver` | runner, task must be `verifying` | `{job, name, content}` -> queues an `output_save` job on the laptop with lane `public` (`:374-386`) |
| `POST /v1/jarvis/runner/delivery` | runner | `{job, delivery}` -> that output job's state/result (`:387-389`) |
| `POST /v1/jarvis/runner/finish` | runner | `{job, result}` -> finishes the `lab_run` job (`:390-393`) |

`/v1/jarvis/actions` dispatch table (`llm_proxy.py:133-250`):

| tool.op | Policy level (`policy.py`) | Effect |
|---|---|---|
| `output.save` / `output.show` | YELLOW_AUTO (`:38`) | queues `output_save|output_show` job to laptop; `.py` refused from private lane; refused while owner's task is at the lab (`:147-170`); 12 shows/hour |
| `ui.launch|click|type` | YELLOW_APPROVAL (`:43`) | 202 `needs_approval` + `approval_id`, then job `ui_action` once approval given (`:171-182`) |
| `pc.open_app|open_folder|open_file|list_folder` | YELLOW_AUTO; public lane raised to YELLOW_APPROVAL, `list_folder` DENY (`policy.py:46,247-251`) | job `pc_open` (`:204-215`) |
| `skill.activate|deactivate` | YELLOW_AUTO; decided by risk/lane (`policy.py:252-260`) | audit only: the kernel records the decision; skillgate does the file work (`:183-203`) |
| `models.status` / `models.prefer` | GREEN / YELLOW_AUTO | provider list for the lane; `lane_prefs` upsert, 20/day (`:216-236`) |
| `task.create|update|get|dispatch|checkpoint` | YELLOW_AUTO / GREEN | see 3 (`:237-238,286-353`) |
| `memory.list|get|resolve|render|add|update|forget` | GREEN / YELLOW_AUTO | see 2.11 (`:239-240,252-284`) |
| `lab.task` | YELLOW_AUTO | authorization only; the lab process starts itself (`:241-243`, `jarvis_mcp.py:247-271`) |

Limits (in memory, reset on restart): 30 model requests/min and 2000/day per agent (`:28-29,488-494`); 60 actions/hour and 600 reads/hour
(`:38-41`); 12 shows/hour; 20 model switches/day (`:42-43`).

### 2.2 CLI (`cli.py:31-173`)

`check`, `approve <id>` (reads the real console `CONIN$`//dev/tty, must retype id), `kill`, `resume` (types RESUME on console), `status`,
`serve`, `telegram`, `pair-code`, `llm-proxy`, `agent-token <name> --lane ... --out file` (token never printed), `nodes`, `analyze`,
`audit verify|anchor`, `trace <task> [--public] [--save]`, `trace-analyze --days N`. **No task, memory, job or approval-list command.**

### 2.3 Auth classes (summary)

| Class | Stored as | Can do |
|---|---|---|
| Pairing code | `pair_codes.h` (sha256), 10 min, one use | trade for a node token |
| Node token | `nodes.token_h` | report state, poll/complete jobs, ask `/request` and `/authorize` |
| Agent token `private` | `agent_tokens` (sha256 + lane) | model calls on local+private providers, all `/actions` |
| Agent token `public` | same | same on local+public providers |
| `dashboard` | same | read overview only; 403 everywhere else (`llm_proxy.py:419,448`) |
| `runner` | same | only `/runner/*` (`:450-468`) |
| Owner at server console | OS terminal | approve, resume, audit verify, issue tokens |
| Hub `X-Jarvis-Hub: 1` | header | lab control only (not the kernel) |

No token expiry, rotation or revocation API (re-issue replaces the row by name: `INSERT OR REPLACE`, `llm_proxy.py:67`). No TLS.

### 2.4 SQLite schema (all in `~/.jarvis-kernel/state.db`, no migration framework, `CREATE TABLE IF NOT EXISTS` + try/except `ALTER`)

| Table | Columns | Created at |
|---|---|---|
| `audit` | `seq` PK, `ts`, `kind`, `payload` (JSON), `prev`, `hash` | `audit.py:19-21` |
| `approvals` | `id` PK, `action_hash`, `level`, `expires`, `state` (pending/approved/used/denied), `action` (JSON, scrubbed) | `store.py:70-72` |
| `nodes` | `name` PK, `token_h`, `last_seen`, `state` (JSON), `killed_ack` | `nodes.py:25-27` |
| `pair_codes` | `h` PK, `expires`, `used` | `nodes.py:24` |
| `jobs` | `id` PK, `node`, `kind`, `params` (JSON), `state`, `created`, `expires`, `result` (JSON), `notified`, `started`, + `owner`, `finished` | `jobs.py:87-95` |
| `tasks` | `id` PK, `created`, `updated`, `owner`, `lane`, `source`, `request`, `goal`, `plan`, `criteria`, `state`, `step`, `attempts`, `max_attempts`, `deadline`, `result`, `evidence`, `verification`, `parent` | `tasks.py:48-51` |
| `task_events` | `id` PK, `task`, `ts`, `state`, `kind`, `note`; index on `task` | `tasks.py:52-53` |
| `memories` | `id` PK, `owner`, `scope`, `kind`, `text`, `version`, `state`, `created`, `updated`, `history` (JSON) | `memory.py:41-42` |
| `agent_tokens` | `name` PK, `token_h`, `created`, `lane` | `llm_proxy.py:56-59` |
| `lane_prefs` | `lane` PK, `provider`, `set_at`, `by_agent` | `llm_proxy.py:121` |
| `tg_approval_notices`, `tg_task_notices` | `id` PK | `telegram.py:328,418` (only exist if the bridge ran) |

Only index besides primary keys: `task_events_task`. No index on `audit.ts`/`audit.kind`, `jobs.state/kind/owner/node`, `tasks.state/owner`.
Queries that depend on this: overview scans `audit WHERE ts >= ?` (`overview.py:43`); dispatch/save guards use `json_extract(params,'$.task')`
and `params LIKE '%"task": "<id>"%'` over `jobs` (`llm_proxy.py:150,310,342`). No retention or pruning anywhere.

### 2.5 Transaction and concurrency model: is it safe for more write load?

What exists:
- One `sqlite3` connection **per process**, `isolation_level=None` (autocommit), `check_same_thread=False`, `timeout=30`, `journal_mode=WAL`
  (`store.py:68-69`).
- `SafeDB` wraps it: every `execute` runs under one `RLock` and `fetchall()`s inside the lock (`store.py:39-58`). Added after live 401/404
  errors under concurrency (docs 02:260).
- The audit append is the only explicit transaction: `BEGIN IMMEDIATE` -> read head -> insert -> `COMMIT`, under a module lock *and* the
  connection lock (`audit.py:28-43`). So hash-chain sequence numbers are safe across threads and (via `BEGIN IMMEDIATE`) across processes.
- Single-use approvals and job start are one conditional `UPDATE ... WHERE state=...` each (`store.py:120,137-140`, `jobs.py:127`,
  `jobs.py:137`), so double-use and double-start are prevented even across processes.
- Dispatch (create-job + set-state) is serialised by an in-process `dispatch_lock` (`llm_proxy.py:123,340-350`).

Judgement: **adequate for the current load (a handful of agents, one node); not safe to simply add write load.** Specific limits:

1. **Everything is serialised on one global lock per process, and reads are fully materialised under it.** The hub's overview does a 24 h
   audit scan every 10 s (`index.html:970`, `overview.py:43`) with no index; `AuditLog.verify()` loads the whole table
   (`audit.py:56`, `fetchall` inside `SafeDB.execute`). Both stall every other request thread (model calls included) while they run.
2. **Multi-statement read-modify-write sequences are not transactions.** `Tasks.update` reads the row, checks transition validity, then
   `UPDATE tasks ... WHERE id=?` with no state predicate (`tasks.py:72-94`). The runner's report, the agent's update and an owner cancel can
   interleave; a final state can be overwritten (`FINAL` check is read-then-write, `tasks.py:75`). `Memories.add` de-dup and the 200-entry cap
   are check-then-insert (`memory.py:92-101`). `Jobs.create` consumes an approval (`authorize`) before the INSERT (`jobs.py:103-109`).
3. **Audit is a single global chain with an fsync per event** (WAL default synchronous). Every node heartbeat writes an audit row
   (`nodes.py:55`, ~5,760 rows/day/node) plus 2-4 rows per model call. The chain cannot be sharded; `verify` is O(n) in Python.
4. **Cross-process:** kernel, llm-proxy, Telegram (off), nightly and CLI each open the same file. WAL + busy timeout makes this work, but
   a long read in one process does not block writers, while a long `BEGIN IMMEDIATE` elsewhere makes writers wait up to 30 s.
5. **No schema versioning**, so adding automations/agents tables means more `CREATE IF NOT EXISTS` + `ALTER` try/except.
6. **In-memory control state** (rate limits, provider cooldowns/outage rests, `calls`, `acts`) resets on restart (`llm_proxy.py:124-127`).

Cheap fixes that keep the design: indexes on `audit(ts)`, `audit(kind)`, `jobs(state,kind)`, `jobs(owner)`, `tasks(state,updated)`;
`schema_version` table; conditional `UPDATE ... WHERE state=?` in `Tasks.update` (or wrap read+write in `BEGIN IMMEDIATE`); stream
`verify()` with a cursor; stop auditing every heartbeat (aggregate); prune old jobs/audit payloads after anchoring.

### 2.6 Policy, authorization and lanes

- Levels `GREEN, YELLOW_AUTO, YELLOW_APPROVAL, RED, DENY` (`policy.py:16`). First matching `(tool, op-pattern)` rule wins; **unmatched is
  RED** (fail closed) (`policy.py:20-63,221-225`). A model "suggestion" can only raise (`:271-274`).
- Overlays raise to DENY/RED: secret paths (`SECRET_PATTERNS`, `SECRET_COMPONENTS`, `jarvis-secrets`), safety files (CLAUDE.md, settings,
  providers.json, policy.py, audit.py, store.py), log/history tampering, password-manager/wallet/bank apps for `ui|browser|pc`, internal URLs
  for `browser`, protected resources (`hermes-agent-vm-2`, kernel data dir), egress tools after untrusted content, unknown lane
  (`policy.py:237-270`). Paths are NFKC-normalised, %-decoded twice, realpath-resolved (`:107-121`).
- **Lanes:** Action lane is `private|public` only (`policy.py:269`). Provider lanes `local|private|public`; a task may use `local` or its own
  lane, fallbacks never cross (`gateway.py:73-76`). Agent token lane decides everything (`llm_proxy.py:134`).
  Private-lane agents may dispatch to the (public-lane) lab only with `public_ok: true` (`llm_proxy.py:329-330`); public-lane agents may
  never list owner folders (`policy.py:247-249`) or activate skills (`:255-257`).
- Free-model guard: `paid` providers are invalid (`gateway.py:53-59`); `model_pattern` regex per provider refuses non-free models before a
  request is made (`gateway.py:61-62`, `llm_proxy.py:556`).
- **Untrusted-session egress removal** exists in policy (`policy.py:66,267-268`) and is set by `Controller` after a browser read
  (`control.py:46-47,74-75`), but no production path uses `Controller` (see 4).

### 2.7 Approvals

- `Kernel.request()` -> `decide()` + audit + pending row with `id=token_hex(8)`, `expires=now+600`, `action_hash=sha256([tool,op,target,
  params,lane])` (`store.py:97-109`, `policy.py:180-184`). Only `YELLOW_APPROVAL` and `RED` create approvals (`policy.py:213-214`).
- `approve(id)`: atomic `pending -> approved` if unexpired (`store.py:117-123`); callers: CLI (real console) and Telegram (YELLOW only,
  typed `CONFIRM <id>` within 60 s, `telegram.py:483-509`). RED is console-only.
- `authorize(action, approval_id)`: re-runs `decide`, then consumes the approval with one `UPDATE ... state='used' WHERE state='approved' AND
  expires>=? AND action_hash=? AND level>=?` (`store.py:136-143`). Single-use, bound to the exact action and a minimum level.
- No HTTP route exists to list, approve or deny approvals. The hub shows pending approvals read-only (`overview.py:83-88`,
  `index.html:740`). "Deny" exists only in Telegram (`telegram.py:511-514`) and there is no code path that marks other approvals denied.
- Approvals are **not** linked to tasks or to the lab runner; `waiting_approval` is never set by code.

### 2.8 Audit log

Append-only SHA-256 chain `hash = sha256(prev|ts|kind|payload)` (`audit.py:12-13`). `verify(anchor)` detects edits, deleted rows, truncation and
genesis rewrite when given an off-box anchor (`:49-72`). Anchor is pulled by `scripts/pull-anchor.ps1` to
`vault/decisions/audit-anchors.md` [doc/code]. Payloads are free JSON built by callers; `Action.describe()` scrubs and truncates 200 chars
(`policy.py:186-199`). Overview and traces are the only readers.

### 2.9 Task lifecycle (`tasks.py`)

States: `planning, waiting_approval, queued, running, verifying, repairing, paused, completed, failed, cancelled` (`:15`); final =
`completed|failed|cancelled` (`:16`). Transitions in `NEXT` (`:17-25`): planning -> any of waiting_approval/queued/running/paused/completed/
failed/cancelled; queued -> running/paused/failed/cancelled; running -> verifying/repairing/waiting_approval/paused/completed/failed/
cancelled; verifying -> completed/repairing/failed/paused/cancelled; repairing -> running/verifying/failed/paused/cancelled; paused ->
queued/running/cancelled/failed. `repairing` refused when `attempts >= max_attempts` (`:81`). Budgets: 1-5 attempts, 60 s-6 h (`:59`);
`expire()` fails tasks past `deadline` and is called lazily from agent actions, runner calls and every overview read (`llm_proxy.py:291,295,358`,
`overview.py:112`). Events in `task_events` (`:177-178`, last 40 returned `:128`). Text limits `TEXT_MAX` (`:26`), lists <= 20.
Honesty guard: an agent may set `completed` only with `verification.passed is True` or evidence citing finished jobs as `job:<id>`
(`llm_proxy.py:315-321`); the runner path is unconditional (`:370-373`, `tasks.update(None, ...)`).

### 2.10 Jobs (`jobs.py`)

Fixed kinds: `project_check`, `output_save`, `output_show`, `ui_action`, `pc_open`, `lab_run` (`:19-27`). Each has an exact-keys parameter
validator (`:36-65`) and maps to a policy `Action` (`:68-81`). States `queued -> running -> done|failed`, plus `expired` (queued past 30 min,
`JOB_TTL`, `:11,116,125,145`). `RUN_TTL` 30 min / lab 2 h marks stuck `running` jobs failed **only inside `to_report()`** (`:142-153`),
whose only caller is the Telegram bridge (`telegram.py:313`). Result capped 60,000 chars (`:135`). `owner` column = requesting agent;
non-owner jobs are reported to Telegram (`notified=0`), agent jobs set `notified=1` (`:108-109`).

### 2.11 Memory store (`memory.py`)

IDs `m-<8 hex>`; per-`owner` (the agent token name) and per `scope` (default `default`); kinds `preference|fact|instruction`; 500 chars max,
200 active; secret-looking text refused (`:19-21,77-86`). `update` needs `expected_version` else `Conflict` (`:105-118`); `forget` by id or by
a single clear match (Jaccard >= 0.34 and a 0.15 gap, `:62-74`); history kept per entry (last 20, `:120-133`). **Forget is a state flag**
(see 1.4). Rendered into every Hermes session by the plugin (`hermes/plugins/jarvis_memory/__init__.py:87-97`). Hermes's own built-in
MEMORY.md/USER.md are now off (`docs/02_ARCHITECTURE.md:303`, HEAD `205a1a9`). Not exposed to the hub (dashboard token cannot read it).

### 2.12 Traces and analysis (`traces.py`)

Read-only SQLite connection (`:35-39`), assembled on demand from tasks, task_events, jobs and audit (`build :67-157`), scrubbed/clipped,
<= 48 kB, `--public` drops free text. Model/action rows are attributed by time window and agent name (`:150`, "assume one lab job at a
time"). `analyze()` (`:179-304`) returns recurring problems, provider failures, skill/outcome links, suggestions. CLI only; no HTTP, no hub.

---------------------------------------------------------------------------------------------------------------------------------------

## 3. Task engine: from a sentence to a delivered file, today

| # | Step | Who/what does it | Code |
|---|---|---|---|
| 1 | Owner types a request | Previously Telegram (plain text -> Hermes API :8642). **Now:** only the Hermes dashboard on :9119 (embedded as the "JARVIS (Hermes)" panel) or the Hermes desktop app. The hub has no input box. | `telegram.py:516-545`; `hub/index.html:411-412,960` |
| 2 | Understand, classify, plan | The owner's Hermes (LLM, private lane) following `deploy/jarvis-soul-operator.md` (SOUL text). No deterministic intent router; the kNN/LLM cascade in `bench/` is not imported by any kernel code. | `deploy/jarvis-soul-operator.md:1-32` |
| 3 | Open a task | MCP tool `task_start` -> `POST /actions task.create {goal, criteria, plan, request, max_attempts, budget_s}` | `jarvis_mcp.py:200-207`, `llm_proxy.py:299-305` |
| 4 | Dispatch | `lab_work` -> `task.dispatch`; kernel scrubs instructions, refuses private keys and owner paths (`D:\`, `C:\`, `/mnt/`), requires `public_ok` from private lane, one active dispatch per task, creates `lab_run` job (node `lab`), task -> `queued` | `jarvis_mcp.py:227-231`, `llm_proxy.py:325-353` |
| 5 | Pick up | `lab_runner.main` polls `/runner/next` every 10 s; **one job at a time** | `lab_runner.py:451-465` |
| 6 | Work | worker = `hermes chat -q --oneshot` / `opencode run` / `goose run` as user `agent` in `~/work/jobs/<task>`, `per_try = max(300, timeout/max_attempts)` | `lab_runner.py:71-76,79-108,358` |
| 7 | Independent verify | a separate Hermes session writes `VERIFY.json`; runner also checks `./deliver/` files and re-runs delivered tests from a fresh folder; a changed toolchain voids the attempt | `lab_runner.py:138-152,179-233,387-406` |
| 8 | Repair | on fail and attempts left: next attempt gets verifier problems | `lab_runner.py:361-366,424-429` |
| 9 | Deliver | each `deliver/` file -> `/runner/deliver` -> `output_save` job -> laptop node writes `D:\jarvis\outputs\task-<id>\<name>`; runner waits up to 35 min for laptop confirmation | `lab_runner.py:236-261`, `llm_proxy.py:374-386`, `node/jobs.py:110-122` |
| 10 | Close | runner reports `completed`/`failed`, `/runner/finish` | `lab_runner.py:419-423,445-448` |
| 11 | Tell the owner | **Was** `flush_tasks` in the Telegram bridge (hands outcome to JARVIS and relays). **Now nothing does.** Owner sees the Outcome tasks panel on the hub. | `telegram.py:412-448` |
| 12 | Learn | Hermes memory tools -> `memory.add/update`; analyzer is a manual CLI | `jarvis_mcp.py` (none), `hermes/plugins/jarvis_memory`, `cli.py:56` |

Pause/cancel: the runner polls `/runner/status` every ~20 s during the worker and SIGSTOPs/SIGTERMs it (`lab_runner.py:91-100`); the
owner's cancel/pause was `/task <id> pause|resume|cancel` on Telegram (`telegram.py:350-375`) and has **no replacement** (docs 05:74
says "through the kernel on the server" but there is no CLI command; only `Tasks.update` from a Python shell, or an agent updating its own task).

Durable on restart: tasks, task_events, jobs (incl. `lab_run` params), approvals, audit, memories, lane_prefs, node tokens/state, agent tokens,
skill activation record (`skills/active.json`), Hermes sessions and MEMORY (server, outside repo), lab workspaces on disk.

Not durable or not recoverable:
- Runner is stateless. If it dies mid-job the job stays `running`; `/runner/next` returns only `queued` (`jobs.py:126`); the worker
  process (`start_new_session=True`, `lab_runner.py:84`) can survive as an orphan. The task only ends via deadline expiry (`tasks.py:101-109`).
  The stuck `running` job row blocks `output.save` for that agent and re-dispatch (`llm_proxy.py:150-152,342-346`) until something fails it, and
  with Telegram off nothing calls `to_report()`. [code-derived, not reproduced live]
- Kernel restart: in-flight HTTP model streams are lost; rate/cooldown state resets (`llm_proxy.py:124-127`). "Never restart while a lab job
  runs" (docs 05:68).
- `/run/jarvis-lab/control.json` is tmpfs, lost on WSL restart (memory-map 10-10 entry).
- Planning quality: `plan` and `criteria` are free-text lists (`tasks.py:39-42`); nothing checks that criteria are verifiable.

---------------------------------------------------------------------------------------------------------------------------------------

## 4. Node and PC control

### 4.1 Job kinds the laptop node can run (`node/jobs.py:154-172`)

| Kind | What it does | Guard |
|---|---|---|
| `project_check` | git status/log + the project's own test command from `config/projects.json` (name only crosses the wire), env stripped of secret-like vars, tree compared before/after | name must be in this machine's allowlist (`jobs.py:36-86`); only project today: `jarvis-hq` |
| `output_save` | writes `outputs/<agent>/<name>` (never overwrites; `name (2).ext`) | name regex `.md .txt .html .json .csv .log .py`, <= 200k chars, realpath stays in folder (`jobs.py:89-122`) |
| `output_show` | Notepad for text types, `os.startfile` for `.html` | file must exist under outputs (`:125-136`) |
| `ui_action` | `launch | click | type` via `winbackend.BACKEND` | after kernel consumed approval (`:139-151`) |
| `pc_open` | `open_app`, `open_folder`, `open_file`, `list_folder` | below |

### 4.2 `pcopen.py` allowlist logic

- Apps: Windows' own Start list (`Get-StartApps`, cached 10 min; runs PowerShell) matched by fuzzy score >= 0.75; ambiguous or uninstaller
  names rejected; opened via `explorer.exe shell:AppsFolder\<AppID>`, so no agent-supplied command line runs (`pcopen.py:104-150`).
- Folders: six known-folder GUIDs only (Downloads, Documents, Desktop, Pictures, Videos, Music) resolved with `SHGetKnownFolderPath`
  (`:19-22,33-51`); sub-path or name search two levels deep, 5000-entry scan cap, `..`/absolute/drive paths refused, realpath must stay in
  root (`:54-101`).
- Files: only a fixed document/media extension set (`DOC_TYPES`, `:23-25`); programs/scripts never (`:165-166`).
- Sensitive names (password managers, wallet, bank, `jarvis-secrets`) refused at node level too (`:26-27,140-141,161-162`), duplicating
  the kernel's `SENSITIVE_APPS` (`policy.py:67-68`).
- `list_folder` returns up to 200 entries, newest first (`:172-184`); policy denies it to public-lane agents.

### 4.3 `winbackend.py` (Windows UI Automation by PowerShell)

`launch`: **exact allowlist `{notepad, calc, mspaint}`** (`:9,71-75`). `click`: find element under the process's main window by
AutomationId or Name, `InvokePattern.Invoke()` (`:13-25`). `type`: `ValuePattern.SetValue`; if unsupported, SendKeys only when the target
process owns the foreground window; password fields refused (`:26-47`). Target format `process|element`, values passed by env var
(`:50-55,57-68`). A new PowerShell process per action, 30 s timeout. No keys/scroll/focus/drag, no waiting for UI, no return values.

### 4.4 Browser (`browser.py`, `guardproxy.py`)

Playwright for Python, `channel="chrome"`, throwaway profile, no downloads/permissions/service workers, one page, 30-action budget, WebRTC
restricted, **all traffic via Smokescreen** with a self-test that must see 403/407 for the metadata address (`:74-86,111-143`). Elements get
numeric ids, clicks/typing must repeat the expected element name, password fields never typed (`:233-258`). Snapshot returns scrubbed
visible text and up to 80 elements (`:224-231`). `guardproxy.py` (126 lines) is a local filtering proxy kept only for tests (`browser.py:125-128`).
**No job kind, no MCP tool and no kernel route use `BrowserSession`/`controller_for`** (grep: only `browser.py` itself and tests). 35 browser
tests skip unless Playwright is installed. The lab's browsing is Firefox/Hermes tools inside WSL, not this code.

### 4.5 What Windows control exists vs is missing

| Capability | Status | Where |
|---|---|---|
| Machine state (CPU, RAM, disks, ports, services, tools, outputs list) | **Exists**, 60 s cadence | `collector.py:190-221` (psutil + registry) |
| Open app by name, open known folder/file, list folder | **Exists** | `pcopen.py` |
| Launch notepad/calc/mspaint; click/type into named UIA element | **Exists**, approval each time | `winbackend.py`, `policy.py:43` |
| Read a window's UIA tree | Library only, **no job kind** | `ui.py:47-66` (policy GREEN `ui.snapshot`, `policy.py:42`, but nothing routes it) |
| Verify an action (observe -> act -> observe) | Library only (`Controller`), **not in the production job path** (`node/jobs.py:139-151` calls the backend directly) | `control.py:55-79` |
| Screenshot of the Windows desktop | **Missing** (no `mss`/`PIL`/`ImageGrab`; grep empty). Linux Agent PC only: `xwd|convert` | `jarvis_mcp.py:394-401`, `lab_control.py:329-338` |
| Keyboard shortcuts, scroll, drag, window focus/move | **Missing** (policy names exist, no backend) | `policy.py:43`, `jobs.py:17` |
| Browser on the laptop | Library only, unwired | `browser.py` |
| Clipboard, file write outside outputs, media control, notifications | **Missing** | none |
| Run arbitrary command / PowerShell | **Deliberately absent** | n/a |
| One job at a time | node runs jobs sequentially in one worker thread (`agent.py:60-64`); a 600 s test run blocks `pc_open` | `agent.py` |

---------------------------------------------------------------------------------------------------------------------------------------

## 5. Model gateway and routing

### 5.1 Config shape (key names only)

`config/providers.json`: top-level `_note`, `providers[]`. Provider keys: `name, lane, base_url, key_file, models, paid, enabled,
default_model, model_pattern, extra_body, os_only, priority, timeout_s, max_input_tokens, max_total_tokens` plus `_note` comments
(`gateway.py:30-46,79-81`). `key_file` is a relative path under the secrets dir; the key is read from file at call time only
(`gateway.py:48-51`, `llm_proxy.py:565-568`). `config/models.json`: `_note`, `models[]` with `id, purpose, base, runtime, where,
tokens_per_s, accuracy_independent_88, risk_fresh_156_exact, status, fallback, notes` (documentation only). `config/projects.json`: `projects.<name>.{path.{nt},
test.{cwd,cmd[],timeout_s}}`. `config/egress.json`: `_note, proxy`.

Effective table (from `providers.json`, non-secret fields):

| Provider | Lane | Enabled | Priority | Default model | Limits |
|---|---|---|---|---|---|
| `local-llama-vulkan` | local | yes, `os_only: nt` | 10 | local | none; **unreachable from the VM-hosted gateway** (os_only) [inference] |
| `vm-llama-cpu` | local | yes, posix | 90 | local Qwen3-4B | `max_total_tokens 4096`, timeout 180 |
| `groq` | private | yes | 20 | `openai/gpt-oss-120b` | `max_total_tokens 8000`, `extra_body.reasoning_effort`, regex model guard |
| `openrouter-private` | private | yes | 30 | `apodex/apodex-1.1-mini:free` | `max_input_tokens 56000`, `model_pattern :free$`, `extra_body.provider` (data collection deny) |
| `dahl-minimax` | public | yes, posix | 11 | `MiniMaxAI/MiniMax-M2.7` | timeout 300 |
| `dahl-glm` | public | yes, posix | 13 | `zai-org/GLM-5.3-Flash` | timeout 300 |
| `dahl-deepseek` | public | no (loops output) | 15 | DeepSeek-V4-Flash | |
| `zai-glm`, `cloudflare-workers-ai`, `ollama-cloud`, `gemini`, `kilo`, `kaggle-ollama`, `colab-ollama`, `puter`, `openrouter-public` | private/public | no | unset/12/15 | | `base_url` null for several |

### 5.2 Rules (as coded)

1. Lane filter: `allowed(task_lane, provider_lane)` -> local or same lane (`gateway.py:73-76`).
2. Availability: enabled, not paid, has `base_url`, `os_only` matches `os.name`, key file exists (`gateway.py:64-70`).
3. Order: `(priority, local-first)`; ties by config order (`gateway.py:84-92`). Caller's preferred provider (set by `models.prefer`) is moved
   to the front (`llm_proxy.py:520-521`).
4. Sizing: estimate `chars/4 + 4/message` (`sizing.py:43-50`); skip providers whose `max_input_tokens` / `max_total_tokens` cannot take
   input + `MIN_OUTPUT 1024` (`:77-79`); shrink output to fit (`:104-107`); compression summary calls need 4096-8192 output (`:53-67`).
5. Quota/cooldown: 429 -> `cooling[provider]` for `Retry-After`, else 1 h on "per-day", else 60 s, cap 6 h (`sizing.py:119-130`,
   `llm_proxy.py:582-585`). Two 5xx -> rest 30 s (`:589-591`). One retry on 500/502/503 (`:32`).
6. Refusal semantics for the agent: 413 context-length when compression could fix it, 429+Retry-After when only resting providers fit, 503
   when resting after outage, 502 otherwise (`llm_proxy.py:542-552,643-650,666-686`).
7. Streaming commits to a provider after the first byte; later failure is not retried (`llm_proxy.py:624-642`).
8. `/v1/models` lies upward on purpose: reports at least 64000 so Hermes will run; real fit is enforced per request (`sizing.py:133-141`).

### 5.3 What a "routing policy with reason codes" would need

Today reasons exist only as scattered audit kinds (`agent_model_skipped`, `agent_model_too_large`, `agent_model_rate_limited`,
`agent_model_unavailable`, `agent_model_call_failed`, `agent_model_request_failed`) and the classes `too_large|rate_limited|other`
(`sizing.py:110-116`). A successful call logs the winner only (`llm_proxy.py:638-641`); skipped providers are a separate row. Nothing is returned
to the caller, and `gateway.plan(..., purpose)` supports a `purpose` filter (`gateway.py:87-88`) that the proxy never passes (`:517`).

To add: (a) a pure function `route(request_features, providers, state) -> {chosen, ranked, rejected:[{provider, code}]}` in `gateway.py`
returning codes such as `LANE_FORBIDDEN, DISABLED, NO_KEY, OS_MISMATCH, PAID, MODEL_PATTERN, TOO_LARGE_INPUT, TOO_LARGE_TOTAL, COOLING,
OUTAGE_REST, PREFERRED, PRIORITY, FALLBACK_N, CAPABILITY_MISSING, QUOTA_BUDGET`; (b) provider capability fields (tools, json mode,
vision, streaming, reasoning) and per-provider budgets (tokens/min, requests/day) instead of reactive 429s; (c) a `routing_decisions`
table or audit payload with the full ranked/rejected list, and a response header `X-Jarvis-Route`; (d) a dry-run `GET /v1/jarvis/route`
for the hub; (e) request class (voice turn, planning, verifier, summary) as input so latency-sensitive voice turns can prefer low-latency
providers; (f) persist cooldowns/quota (now memory-only).

---------------------------------------------------------------------------------------------------------------------------------------

## 6. Hub / Mission Control

**Stack:** `hub/index.html` (984 lines), no framework: vanilla ES2020 + inline CSS, `fnando/sparkline` (MIT) and a Lucide icon sprite inlined
by `build_hub.py:1-23`, Geist fonts, `<dialog>` for palette/preview. Served as a static file by Caddy from `/opt/jarvis-hub/site`
(`hub/Caddyfile:33-37`). Single page; "panels" are iframes of other dashboards (`index.html:472-505`).
**Data:** polling `fetch`: kernel overview every 10 s (home) / 30 s (panel) (`:970`), lab control state every 4 s / 15 s (`:971`), screen
JPEG every 3 s (`:972`), service checks via dashy `/status-check` every 20 s (`:973`). No SSE/WebSocket to the kernel.
**Auth to the kernel:** none in the browser. Caddy rewrites `/api/kernel/overview` and adds a `dashboard`-lane bearer from
`/home/hub/.hub.env` (`Caddyfile:26-32`). The hub itself has no user login; protection is loopback bind plus WSL localhost forwarding
(`Caddyfile:1-3`). Control calls need only `X-Jarvis-Hub: 1` (`index.html:550-554`).

What is real vs placeholder in `index.html`:

| Element | Real? | Lines |
|---|---|---|
| KPI tiles with 24 h sparklines (calls, success, actions, approvals) | Real data | 674-690 |
| Agent cards (JARVIS + hermes/opencode/goose) | Real: "JARVIS" status = last model call < 180 s; lab = `lab_control` tasks | 692-732 |
| Agent PC thumbnail + Take control / Continue / drawer | Real; acts on `lab_control` only | 563-627 |
| Outcome tasks (state, verified chip, delivered files, criteria, events) | Real, read-only | 633-655 |
| Task board (approvals, queued, running, paused, done, failed) | Real, read-only; no actions | 735-752 |
| Delivered gallery + preview (image/video/audio/html/text) | Real for text; image/video paths cannot be produced by current output rule (`jobs.py:15`) | 759-791 |
| Model routing list (ok/failed/last ok, preferred tag) | Real, per provider | 793-801 |
| Services list (9 checks) and chips (spend "$0" is a **hard-coded label**) | Real checks; `Spend $0` is static text | 802-822 (818) |
| Live activity rail (filters, pause) | Real from audit tail + lab activity | 830-896 |
| Command palette (Ctrl+K) | Real but only navigation and 5 UI actions | 898-945 |
| "Give JARVIS a task" button | **Placeholder**: shows a toast telling you to use Telegram (which is off) | 341, 960 |
| "Waiting on you ... approve on Telegram" copy | **Stale**: Telegram is off | 684, 816 |

### 6.1 Your nine screens vs what exists

| Screen | Exists today? | Evidence / gap |
|---|---|---|
| Command Center | **Partial.** Overview home, KPIs, activity rail, palette. No command input, no voice, no approve. | `index.html:339-363,898-945,960` |
| Agentic OS desktop (noVNC, take control) | **Yes, but for the Linux Agent PC in WSL**, not the Windows laptop. noVNC embedded at `/pc/`, view-only toggled live. | `index.html:403-404,577-627`; `lab_control.py`; `Caddyfile:19-23` |
| Agent Factory | **No.** Closest: `skillgate.py` (skills), token lanes, `jarvis_mcp` profiles. | `skills/skillgate.py`, `jarvis_mcp.py:20-36` |
| Tasks monitor | **Partial, read-only.** No pause/cancel/retry/approve buttons, no plan DAG, no trace link. | `index.html:633-655,735-752` |
| Automation Studio | **No.** | section 7 |
| Workspace / deliverables | **Partial.** Per-task folders under `outputs\task-<id>\`, gallery, preview; text types only; no workspace/project concept. | `index.html:759-791`, `jobs.py:15` |
| Memory and learning | **No UI.** Kernel memory API and trace analyzer exist but are CLI/agent-token only. | `memory.py`, `traces.py`, `cli.py:54-57` |
| Models / tools / skills | **Partial.** Providers only (no tools list, no skills, no limits/quota). | `index.html:793-801`; `skills/active.json` is CLI-only |
| Settings / health | **Partial.** Service checks, kill-switch indicator, chips; no settings, no kill/resume button. | `index.html:802-822` |

---------------------------------------------------------------------------------------------------------------------------------------

## 7. Automations and scheduling

- **Kernel scheduler:** none (grep for cron/schedule/timer finds only docs, the benchmark's "schedule" intent label
  `bench/run_bench.py:50`, and the nightly timer).
- **Existing recurring work:** `jarvis-nightly.timer` 21:00 UTC (`deploy/jarvis-nightly.timer:5`) -> `scripts/nightly.py` runs the four
  benchmark sets on the VM and writes `~/jarvis/bench-results/<date>.json` (`nightly.py:19,63`); report to Telegram is skipped when
  `telegram.off` exists (`:75-77`). monit restarts services and alerts (`deploy/monit-jarvis.conf`). `scripts/check_nightly.sh` checks freshness.
- **Hermes cron:** documented as the trigger mechanism (`03_DECISIONS.md:35`, `01_RESEARCH_AUDIT.md:40`) but switched off for the owner
  profile (`02_ARCHITECTURE.md:83`) and unavailable to lab skills (`skillgate.py:60`). Whether the server Hermes has any cron job: UNVERIFIED.
- **Durable user-defined automations:** **none.** No table, no trigger types (time, file, webhook, event), no run history, no approval model
  for unattended runs. Task `source` is a free 20-char string (`tasks.py:56,63`), usable as a trigger tag if a scheduler is added.

---------------------------------------------------------------------------------------------------------------------------------------

## 8. Voice

Grep (case-insensitive) for `whisper|tts|speech|microphone|kokoro|wake|voice|piper|stt|vosk|pyttsx|sapi|audio` over all `.py/.md/.html/.json/
.service` files finds **no implementation**: only `docs/02_ARCHITECTURE.md:53` ("voice ... out of scope for now"),
`docs/04_BUILD_PLAN.md:7` ("Training, voice, agent factory ... stay parked"), `skillgate.py:60` (`text_to_speech` listed as an unavailable
Hermes tool), and an `<audio>` preview tag in the gallery (`index.html:784`). The hub requests no microphone permission.

Reusable pieces already in place for a voice front-end: the exact call to the owner's Hermes (`telegram.py:88-105`: POST
`/v1/chat/completions`, model `hermes-agent`, `X-Hermes-Session-Id`, one session per UTC day, fresh session after a failed turn);
the kernel task checkpoint and memory injected into every session (`hermes/plugins/jarvis_memory`); `Controller`/policy for what a spoken
command may do.

---------------------------------------------------------------------------------------------------------------------------------------

## 9. Laya and OpenJev

References and evidence:

| Item | Where | Facts |
|---|---|---|
| OpenJev server | `bench/openjev_risk.py:1-6`; `docs/03_DECISIONS.md:96`; external checkout `D:\jarvis-apps\openjev` (git origin `github.com/razorback16/openjev`, HEAD `75f22b6dad8c`, `LICENSE` = Apache-2.0 text) | Runs CPU PyTorch; "same wire API as TypeSafe Jev" (its README). Independent project, not affiliated with TypeSafe. |
| API | `bench/openjev_risk.py:30-37` | `POST {base}/v1/systemone` body `{model, state, questions:{<id>:{type:"choice", instructions, criteria:{<label>:<description>}}}}` -> `answers.<id>.{choice, confidence}`, `usage.input_tokens`. `GET /v1/models`. |
| Laya | `docs/01_RESEARCH_AUDIT.md:36`; OpenJev `config.py:142-144`, `encoders.py:10-12` | Model id `laya-1.0`; ModernBERT-large 421M; checkpoint `convaiinnovations/laya-typed-decisions` (Hugging Face); code `github.com/NandhaKishorM/laya` by Nandakishor M / Convai Innovations; Apache-2.0 (per OpenJev's text and the audit table). Brand new (Sept 2026), no independent replication. |
| Verdict | OpenJev README line 26, 332-335 | `verdict-1.4`; 151M; ModernBERT-base + GLiClass head, checkpoint `heman10x/rlcd-modernbert-151m`; repo `Heman10x-NGU/Verdict-open-jev`. License of this one: UNVERIFIED here (OpenJev README says non-OpenJev models are others' work). |
| Runtime | `bench/openjev_risk.py:49`; `hub/index.html:419` | Verdict on :8091, Laya on :8092 on the laptop for the bench; hub checks :8091 inside WSL. Not wired into the kernel. |

Measured results, recomputed from `bench/data/*.json[l]` (risk set `risk_fresh23.jsonl`: 260 items = 30 GREEN, 30 YELLOW, 100 RED, 100 DENY,
200 high-risk) [calc]:

| Mechanism | Exact | High-risk judged GREEN/YELLOW (false-allow) | Over-cautious | Latency (CPU) | Input tok |
|---|---|---|---|---|---|
| keyword rules | 126/260 (48%) | 101/200 | 9 | n/a | n/a |
| embedding kNN | 224/260 (86%) | 13/200 | 15 | n/a | n/a |
| Qwen3-4B LLM | 185/260 (71%) | 19/200 | 9 | seconds on VM CPU | |
| **union rules+kNN+LLM (current advisory)** | 227/260 (87%) | **3/200 (1.5%)** | 28 | | |
| `verdict-1.4` alone | 152/260 (58%) | 35/200 | 35 | median 115 ms, p95 165 ms | 111 |
| union rules+kNN+verdict | 202/260 (78%) | 3/200 | 51 | | |
| union all four | 203/260 (78%) | 2/200 | 53 | | |
| `laya-1.0` alone | 84/260 (32%) | 143/200 | 13 | median 340 ms, p95 483 ms | 106 |
| union rules+kNN+laya (not in docs) | 218/260 (84%) | 7/200 | 32 | | |

These match `docs/03_DECISIONS.md:98-104`. Caveats stated by the docs and still true: one author wrote the items, 200 high-risk items
cannot show a 1% rate (95% upper bound for 0/200 is ~1.5%), calibration (ECE) was never measured, `last_results.json` is from 2026-10-08
and the model stack changed since. Conclusion in docs: models stay advisory; the structured policy is the gate (`03_DECISIONS.md:91`).

---------------------------------------------------------------------------------------------------------------------------------------

## 10. Agents

How an "agent" is defined today: not as a first-class object. Pieces:

| Concept | Implementation |
|---|---|
| Identity | a **token name** (`agent_tokens.name`) + lane; the memory `owner`, task `owner`, job `owner` are that name (`llm_proxy.py:56-69`, `tasks.py:56`, `memory.py:41`). Re-issuing a name replaces its token. |
| Runtime | external: Hermes Agent v0.21.6 (owner, server, user `jarvis-agent`), Hermes/OpenCode/goose (lab, WSL user `agent`). Config lives in `~/.hermes/config.yaml`, `SOUL.md`, OpenCode `AGENTS.md`, goose `.goosehints` **outside this repo** (UNVERIFIED content). The only in-repo persona text is `deploy/jarvis-soul-operator.md`. |
| Tools | `jarvis_mcp.py` with two profiles: `lab` (public lane) and `owner` (private lane) via `JARVIS_MCP_PROFILE` (`:20,32-36`); tool lists are fixed by decorators (`:140-244`). |
| Skills | Hermes SKILL.md files staged byte-for-byte, rated, kernel-authorised, installed to the lab Hermes only (`skills/skillgate.py:1-24`); `skills/active.json` records `adversarial-ux-test` and `decision-questionnaire` active (low risk). |
| Models | by lane via token; per-agent preferred provider (`lane_prefs` is per **lane**, not per agent). |
| Workspace isolation | per task folder `~agent/work/jobs/<task>`; **all lab agents and tasks run as the same Unix user `agent`** sharing `$HOME`, `~/.hermes`, `~/.local/bin`; runner fingerprints and restores the toolchain around each run (`lab_runner.py:264-335`) after a worker planted a fake `python3`. Network egress limited by a per-user firewall (docs 02:89). |
| Verification | independent Hermes session; metric "verifier agreement 0.33" (daily 10-10). |
| Agent-of-agents | `delegate_task` and `lab.start_lab_task` (agent starts another agent CLI under its own folder, `jarvis_mcp.py:247-284`). No parent/child task record (`parent` unused). |

There is no declarative agent definition (name, role, tools, models, budget, memory scope, lane, schedule), no per-agent audit view beyond
`agent` names in audit payloads, and no way to create an agent from the hub.

---------------------------------------------------------------------------------------------------------------------------------------

## 11. Tests and evals

Do **not** run from this audit. Counts below are `def test_` greps (not executed), so they will differ from runner totals where tests are
parameterised or skipped.

| Suite | Files (test methods) | Run command |
|---|---|---|
| Kernel + node + browser (`kernel/tests`) | `test_agent_actions` 16, `test_browser` 35, `test_collector` 6, `test_control` 11, `test_gateway` 19, `test_hardening` 20, `test_jobs` 26, `test_kernel` 25, `test_llm_proxy` 13, `test_memory` 4, `test_nodes` 10, `test_scrub` 7, `test_sizing` 17, `test_tasks` 9, `test_telegram` 33, `test_traces` 5, `test_ui` 8, `test_vault` 9, `test_winbackend` 5 = **278** | `D:\jarvis-apps\python\cpython-3.12.13-windows-x86_64-none\python.exe -m unittest discover -s tests` from `D:\jarvis\hq\kernel` (tests add `kernel/` and `../../node` to `sys.path`, e.g. `test_agent_actions.py:11-20`) |
| Lab | `lab/tests/test_lab_control` 10, `test_lab_runner` 10 (root + Linux only) | `python -m unittest discover -s hq/lab/tests` (docs 05:57); `*_live.py` need a running lab and are not discovered |
| Hermes plugin | `hermes/tests/test_jarvis_memory_plugin` 3 | stdlib-only import fallback (`plugin __init__.py:26-29`); command UNVERIFIED |
| Skill gate | `skills/tests/test_skillgate` 4 (needs pyyaml) | `D:\jarvis\tools\skillgate-venv\Scripts\python` (docs 05:92); command UNVERIFIED |
| Evals | `evals/run_evals.py`: **23** `@case` (grep), `evals/baseline.json` holds **19** | `python hq\evals\run_evals.py --baseline hq\evals\baseline.json` (docs 05:100) |
| Live harnesses | `evals/ctx_recovery.py`, `evals/memory_lifecycle.py`, `lab/demo.py`, `lab/tests/{takeover,switch,approval}_live.py` | WSL/server only (docs 05:51-58,112-114) |
| Benchmarks | `bench/run_bench.py`, `openjev_risk.py`, `risk_union.py`, ... | need llama.cpp + embeddings services |

Skips: 27 `skipUnless/skipIf` decorators (Windows-only UIA/collector tests, Playwright, gitleaks, POSIX/root lab tests).

Counts stated in docs (point-in-time): 236 OK on Windows on 2026-10-10 (daily:43), server suite 230 OK (03_DECISIONS:140), evals 19/19
(02:273), eval baseline "old kernel 11/17" (02:273), unit tests 10 on Linux / 6 on Windows for lab_control (02:118). **Whether the current
HEAD passes is UNVERIFIED** (not run by me).

Notable coverage gaps (by reading): no test for runner crash/orphaned `running` jobs; no test that anything fails stuck jobs without Telegram;
no test for `Tasks.update` races (the live race tests are in `evals`, "six simultaneous dispatches"); no UI/hub tests (rendering was
checked manually in headless Edge, daily 10-10:49).

---------------------------------------------------------------------------------------------------------------------------------------

## 12. Gaps versus a voice-first agentic OS, and risks (ranked)

Rank = blocks the target experience or is a latent failure, weighted by effort-to-fix.

| # | Gap or risk | Evidence | Where a change would land |
|---|---|---|---|
| 1 | **No owner control plane over HTTP**: no authenticated way to send a command, approve/deny, pause/resume/cancel, kill/resume, or see approvals with details. Hub is read-only; Telegram off; approval needs an SSH console. | `cli.py:78-102`, `llm_proxy.py:415-482`, `index.html:960` | New owner token class in `AgentTokens.issue` (`llm_proxy.py:61-69`); new owner routes in `H.do_POST/do_GET` (`:414-482`); `Kernel.approve/deny` in `store.py:117`; keep out-of-band guarantee (docs 02:56) via a second factor |
| 2 | **Background loops died with Telegram**: outcome hand-back to JARVIS, stuck-job sweep, approval notices. A stuck `running` lab_run blocks saves and re-dispatch. | `telegram.py:308-448,574-589`, `jobs.py:142-153`, `llm_proxy.py:150-152,342-346` | A small sweeper thread started from `nodes.serve` (`nodes.py:147`) or `llm_proxy.serve` (`:691`): call `Jobs.to_report`-style sweep, `Tasks.expire`, and emit "task ended" events the voice/hub layer consumes |
| 3 | **No voice**: STT, TTS, wake word, barge-in, push-to-talk, latency budget. | grep (section 8) | New service on laptop (`node/jarvis_node/voice.py`) or in the hub page; text goes to Hermes `:8642` (`telegram.py:88-105`) or a new kernel `command` route; spoken approval must still be out-of-band for YELLOW/RED |
| 4 | **Private-lane capacity** can't carry an always-on assistant: Groq 8000 total tokens, OpenRouter 50/day, VM CPU 4096; agent median request ~85k chars. | `providers.json`, `docs/01_RESEARCH_AUDIT.md:119-126` | `config/providers.json` (owner decision: another free private provider), `gateway.plan` priority/purpose (`gateway.py:84-92`), small-context voice turns routed separately |
| 5 | **No automations/scheduler**, no triggers, no unattended-run approval model. | section 7 | New `kernel/jarvis_kernel/automations.py` + table; scheduler thread alongside the sweeper; creates tasks via `Tasks.create` (`tasks.py:56`) with `source="schedule"`; policy rules for unattended levels in `policy.py` |
| 6 | **Windows control is thin and unverified**: three launchable apps, no screenshot/UIA read job, no verify loop, browser unwired. | section 4.5 | `jobs.py:19-27,36-65` (new kinds), `node/jobs.py:154-172`, `node/ui.py`, `winbackend.py`, `policy.py:20-63`; reuse candidates Windows-MCP (`docs/01:13,64`) |
| 7 | **Deliverables are text-only, flat, <= 200k chars**: no images, video, binaries, folders. Hub gallery supports them but nothing can arrive. | `jobs.py:15,17`, `node/jobs.py:89,110-122`, `lab_runner.py:111-113` | `jobs.OUTPUT_NAME` + new chunked `output_save_bin` kind + `node/jobs.output_save`; lab-side `deliverables()` (`lab_runner.py:179-208`) |
| 8 | **Verification quality is weak**: "verified" tasks later corrected; completion accuracy 0.25. | `vault/daily/2026-10-10.md:91-92`; `tasks.py` has no `correction` writer | `lab_runner.verify_prompt` (`:138-152`), add a correction API in `tasks.py` + owner route, track agreement in `evals/run_evals.py:393-410` |
| 9 | **SQLite/audit scaling**: global lock, unindexed scans, heartbeat audit rows, `verify` loads all, no pruning, no migrations. | section 2.5 | `store.py:39-58`, `audit.py:28-72`, `nodes.py:55`, `overview.py:43`, `tasks.py:72-94` |
| 10 | **Lab is serial and not resumable**: one job at a time, 10 s poll, runner restart orphans workers, one Unix user for all agents. | `lab_runner.py:451-465,79-108` | `lab_runner.main/handle`; `jobs.next_for` re-claim of `running` jobs owned by a dead runner; per-agent Unix users or containers |
| 11 | **Routing has no reason codes, purpose, capability or budgets**; quota state is memory-only. | section 5.3 | `gateway.py:84-127`, `llm_proxy.py:516-650`, `sizing.py:110-130` |
| 12 | **No agent definition object**; memory is per token name with no sharing; deleted memory is not erased. | section 10, `memory.py:127` | new `agents` table/`config/agents.json`, `AgentTokens`, `Tasks.create`, `memory.py:forget` (clear `text` and history) |
| 13 | **Hub has no user auth, polls, and is a single file**; embeds third-party dashboards in iframes; `Spend $0` is a literal. | `index.html:818,963-976`, `Caddyfile:1-3` | `hub/index.html` (SSE `/v1/jarvis/stream` route in `llm_proxy.py`), Caddy auth |
| 14 | **Kill switch does not freeze lab processes**; it cuts model calls (`llm_proxy.py:485`) and node jobs (`nodes.py:114`) but the runner/worker keep running and runner endpoints ignore it. | `store.py:91-94`, `llm_proxy.py:355-394`, `lab_control.py:161-172` | `runner_call` check + `lab_control.freeze` on kill |
| 15 | **Security hygiene**: plaintext HTTP on the tailnet, no token expiry/revocation, node-asserted `lane`/`untrusted_session` (`nodes.py:129-131`), a token was printed once (daily 10-10:76) and the agent token sits in Hermes `config.yaml` (`docs/05_OPERATIONS.md:78`). Voice adds microphone data (keep STT local). | | `llm_proxy.AgentTokens`, `nodes.make_handler`, `docs/05_OPERATIONS.md:76-80` |
| 16 | **Doc/code drift** listed in 1.4 will mislead the next build. | 1.4 | docs/02, docs/03, `overview.py:1-5`, `evals/baseline.json` |

---------------------------------------------------------------------------------------------------------------------------------------

## 13. Reuse vs replace vs adapt

Your brief mentions a "12-step lifecycle" but no such list exists in this repo (`grep -i lifecycle|12-step` finds only test class
names). I assumed this lifecycle and flag it as **my mapping**: 1 wake/capture, 2 speech-to-text, 3 intent + risk classification,
4 clarify, 5 plan, 6 route model/agent, 7 authorize/approve, 8 dispatch, 9 execute, 10 verify, 11 deliver + speak, 12 remember/learn/audit.

### 13.1 By screen

| Screen | Verdict | Reuse | Adapt | Replace / new |
|---|---|---|---|---|
| Command Center | **Adapt** | overview endpoint, KPIs, activity rail, palette (`overview.py`, `index.html:339-363,898`) | add command box + owner route; approvals inline | voice capture widget (new) |
| Agentic OS desktop | **Reuse** (Linux) / **New** (Windows) | noVNC + `lab_control.py` take/continue unchanged | extend to also show the Windows screen if wanted | a Windows screen/control path (RDP/VNC/UIA) does not exist |
| Agent Factory | **New** | token lanes, skillgate, `jarvis_mcp` profiles, Hermes profiles | `AgentTokens` -> agent definitions | declarative agent schema + UI |
| Tasks monitor | **Adapt** | tasks table/events, Outcome panel, traces | add controls, plan view, trace drawer | none |
| Automation Studio | **New** | `Tasks.create`, policy, nightly timer pattern | | scheduler + table + UI |
| Workspace / deliverables | **Adapt** | per-task folders, gallery, sandboxed outputs origin (`Caddyfile:42-52`) | binary types, workspace per project | image/video job kind |
| Memory and learning | **Adapt** | `memory.py`, plugin, `traces.analyze` | read-only owner view; real erasure | none |
| Models / tools / skills | **Adapt** | providers panel, `skills/active.json`, `skillgate.py` status | reason-coded routing view, quotas, tool registry | tool registry (new) |
| Settings / health | **Adapt** | service checks, kill indicator | kill/resume with confirmation, token rotation | settings store (new) |

### 13.2 By lifecycle step

| Step | Verdict | What to reuse | What to build |
|---|---|---|---|
| 1 Wake/capture | **New** | hub page, Edge app window | push-to-talk first; wake word later (local) |
| 2 STT | **New** | none in repo | local STT service (reuse a maintained project per the reuse rule) |
| 3 Intent + risk | **Adapt** | `policy.decide` (authority), `bench/` union rule as advisory | wire advisory into the command route; keep it raise-only (`policy.py:271`) |
| 4 Clarify | **Adapt** | SOUL instructions, `memory.resolve` for ambiguity | a clarifying-question state on the task |
| 5 Plan | **Reuse** | `tasks.py` plan/criteria; Hermes planner | criteria quality checks |
| 6 Route model/agent | **Adapt** | gateway, sizing, lanes | reason codes, capability tags, agent definitions |
| 7 Authorize/approve | **Adapt** | single-use hash-bound approvals (`store.py:117-157`) | HTTP approve/deny with second factor; link approvals to tasks |
| 8 Dispatch | **Reuse** | job queue, `task.dispatch`, locks | concurrency > 1, resume of orphaned jobs |
| 9 Execute | **Reuse** (lab) / **Adapt** (Windows) | lab runner, `pcopen`, `winbackend` | UIA snapshot + screenshot + verify jobs |
| 10 Verify | **Adapt** | independent verifier, portable check | measure agreement, correction API |
| 11 Deliver + speak | **Adapt** | outputs delivery with laptop confirmation | binary files, TTS, notifications |
| 12 Remember/learn/audit | **Reuse** + **Adapt** | memory API, audit chain, traces | real forget, learning feed in hub |

---------------------------------------------------------------------------------------------------------------------------------------

## 14. UNVERIFIED list

- Whether production (server) code equals HEAD; whether `jarvis-telegram`, `jarvis-omniroute` and openmuse are running now.
- Hermes server and lab configuration, SOUL.md, enabled toolsets, memory settings (outside this repo; not read).
- How the Windows node autostarts, and whether the node name equals `OUTPUT_NODE` (`llm_proxy.py:36`).
- Current test pass/fail at HEAD (not run).
- Verdict model licence; Laya licence beyond the statements in docs and the OpenJev README.
- Behaviour claimed in docs but not exercised here: noVNC view-only switching, firewall rules, Smokescreen self-test, Oracle cost.
- The stuck-`running`-job blocking behaviour in section 3 is derived from code, not reproduced.

## 15. Files read

`kernel/jarvis_kernel/{store,policy,audit,tasks,jobs,memory,nodes,llm_proxy,gateway,sizing,overview,traces,cli,vault,telegram(partial),
scrub(partial),analyze(partial)}.py`; `node/jarvis_node/{agent,jobs,pcopen,winbackend,control,ui,browser,collector(partial),guardproxy(partial)}.py`;
`lab/{lab_runner,lab_control,jarvis_mcp}.py` and lab tests (headers); `hub/{index.html,Caddyfile,build_hub.py}`; `hermes/plugins/jarvis_memory`;
`skills/skillgate.py` (head), `skills/active.json`, `skills/allowlist.json`; `evals/run_evals.py`, `evals/baseline.json`;
`bench/openjev_risk.py` and `bench/data/*` (recomputed); `scripts/nightly.py`, `check_nightly.sh`, `start-*.{ps1,vbs}`, `pull-anchor.ps1`;
`deploy/*`, `lab/deploy/*`; `config/*` (shape and non-secret fields); `docs/01-05`; `vault/daily/2026-10-10.md`,
`vault/decisions/memory-map.md`; OpenJev README/config (external, `D:\jarvis-apps\openjev`).
