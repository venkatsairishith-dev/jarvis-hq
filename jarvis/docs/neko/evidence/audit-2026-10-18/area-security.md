# Audit area H: security and operational reliability of the JARVIS kernel and its edges

- Repository: `/srv/jarvis/home/hq-neko`, commit `88ec49a2` (branch main, clean). Audit date: 2026-10-18.
- Mode: read-only. Nothing in the repository was changed.
  - Reproductions ran only against temporary kernels inside the `neko-test` container (temp dir, 127.0.0.1, fake upstream). No real service, server or network was used.
  - The scripts are in the scratchpad next to this file: `repro.py`, `repro2.py`, `repro3.py`, `repro4.py`.
- **Hardware limit.** This is a Linux test server. It has no Windows, microphone or GPU, and no access to the real VM, laptop node or lab. Every claim about the laptop node, the lab runner, the Windows UI, or the deployed systemd units is checked **only through code and recorded evidence**. None of them was observed running.
- **Evidence classes used below:**
  - **E2E-live**: a recorded live run on real hardware with a result file or log.
  - **E2E-manual**: a hand-recorded live observation without a saved result file.
  - **Integration**: real kernel components wired together on 127.0.0.1, with external boundaries faked.
  - **Unit/mocked**.
  - **None**.
- **About the bench.**
  - K and N bench cases (`jarvis/evals/neko95/bench/adapters/k.py`, `n.py`) are **integration**: real modules, temp kernel, servers on 127.0.0.1, fake MCP and upstream.
  - Latest full run: `jarvis/evals/neko95/results/bench-20261017-220721.json` (host Linux x86_64, 2026-10-17 22:07). Result: K 109 pass / 2 deferred (K-040 physical kill button, K-089 Windows Credential Manager); N 73/73 pass.

---

## 1. Real execution flow (with file:line)

### 1.1 Agent request → kernel (llm_proxy.py, one process serves agents, the runner, the dashboard and the owner plane)
1. **Accept the connection.**
   - `serve()` binds 127.0.0.1 plus the `--bind` addresses (llm_proxy.py:1050-1058).
   - The `llm-proxy` CLI passes `--bind` through unchecked (cli.py:126). Contrast `serve` for nodes, which refuses 0.0.0.0 (cli.py:108).
   - The deploy unit binds 127.0.0.1 and the Tailscale address 100.125.91.51 (deploy/jarvis-llm-proxy.service).
2. **Identify the token.**
   - `H._agent()` (llm_proxy.py:648-650) → `AgentTokens.identify()` compares SHA-256 hashes with `hmac.compare_digest` (llm_proxy.py:75-83).
   - The token row fixes `(name, lane)`. The lane is one of private/public/dashboard/runner/owner (llm_proxy.py:63-73).
3. **Route by path and lane.** Lane always comes from the token, never from the body.
   - `GET /v1/jarvis/owner/*` and `POST /v1/jarvis/owner/*` go to `_owner_call`. It requires lane `owner` and gives 401/403 otherwise, audited (llm_proxy.py:652-673, 703-705, 734-736).
   - `GET /v1/jarvis/overview` needs lane `dashboard` (706-713).
   - `GET /v1/models` accepts any valid token (714-725).
   - `GET /v1/jarvis/jobs/<id>` accepts any valid token, but `jobs.get_for` filters on `owner=<token name>` (726-731; jobs.py:218-225).
   - `POST` with a dashboard or owner token is refused (741-746).
   - A runner token works only on `/v1/jarvis/runner/*`, and runner paths work only with a runner token (747-765).
   - `POST /v1/jarvis/actions` → `idem.run("agent:"+name, …)` → `agent_action(agent, lane, body)` (766-781).
   - `POST /v1/chat/completions`:
     1. Kill check (784).
     2. Per-agent rate limit of 30/min and 2000/day, in memory (786-793).
     3. Body size limit of 2 MB (795-797).
     4. `_scrub_messages` (801; 86-148).
     5. `max_tokens` capped at 4096 (805-809).
     6. `gateway.plan(providers, lane)`, where the lane comes from the token (816).
     7. The lane preference is applied only inside that chain (819-820).
     8. Sizing filter (827-831).
     9. Fallback loop (853-957). It stops falling back once bytes are streamed (928).
4. **Agent actions** (`agent_action`, llm_proxy.py:302-447).
   - A per-agent hourly budget applies (308-314).
   - Each tool builds a fixed job kind via `jobs.action_for` and then either:
     - `kernel.request()`, which records a pending approval (store.py:138-151), or
     - `jobs.create(... approval_id)` → `kernel.authorize()`, the final gate. It raises `Killed`, re-decides policy, and atomically consumes the approval: `UPDATE … WHERE id=? AND state='approved' AND expires>=? AND action_hash=? AND level>=?` (store.py:217-249; jobs.py:197-216).
5. **Policy** (`policy.decide`, policy.py:324-398).
   - First matching rule wins. An unmatched action is RED (policy.py:317-321).
   - Overlays can only raise the level:
     - secrets paths → DENY;
     - safety files and log tampering → RED;
     - sensitive apps → DENY;
     - public lane on the PC → approval or DENY;
     - skills and connectors by the owner's risk class;
     - `webmcp_*` → DENY;
     - internal URLs → DENY;
     - protected resources → DENY;
     - untrusted session plus egress → DENY;
     - unknown lane → DENY.
   - The model's `suggested` level is clamped and applied with `max` (policy.py:394-397).
   - `owner_direct` lowers YELLOW_APPROVAL to YELLOW_AUTO only for ordinary UI/browser ops on the private lane without untrusted content, and it is re-raised by consequential words or keys (policy.py:328-336).
   - An approval binds to `Action.digest()` = sha256 of JSON of `[tool, op, target, params, lane, (owner_direct)]` (policy.py:270-273).
6. **Jobs and the node.**
   - Jobs wait in the `jobs` table.
   - The laptop node polls `POST /jobs/next` on the node endpoint (nodes.py:142-153). That endpoint is a separate process bound to the Tailscale address, and 0.0.0.0 is refused (cli.py:107-111).
   - While killed, the node gets no job (nodes.py:151-152; jobs.py:230).
   - `/report` returns `killed`, and the node stops the job in hand (nodes.py:141; node/jarvis_node/agent.py:85-93).
   - The node runs only fixed handlers (node/jarvis_node/jobs.py) and re-checks names and paths itself (pcopen.py:82-101, 137-184; jobs.py:103-113).
7. **Owner plane** (`Owner.handle`, owner.py:675-734).
   - Optional idempotency applies, but never to `approve` or to bodies that contain `pin` (owner.py:680-688).
   - PIN approval: `Owner.approve` (owner.py:265-303).
     1. The typed id is checked.
     2. A lockout counts wrong PINs and PIN checks still in flight (5 per 600 s, in memory).
     3. The PIN is checked with PBKDF2-SHA256 at 200k iterations and `compare_digest` (owner.py:47-59).
     4. The level must be YELLOW_APPROVAL.
     5. `Kernel.approve()` runs the atomic pending→approved update (store.py:159-166).
   - Kill: `Owner.kill` → `Kernel.kill` writes the KILL file (owner.py:392-394; store.py:121-123). Lifting it is console-only (cli.py:97-103).
8. **Console** (laptop, `jarvis/console/jarvis_console/server.py`).
   - Binds 127.0.0.1 (config.py:13).
   - The Host header must be 127.0.0.1 or localhost on its own port (server.py:85-89, 186-187).
   - POST and DELETE need a same-origin `Origin`, or no Origin plus `X-Jarvis-Console: 1` (server.py:91-99, 191-192).
   - `/api/owner/*` is proxied to the kernel with the owner token, which is read from a file on every call and never returned (server.py:306-310; kernel_client.py:25-56). `log_message` is silenced (server.py:81-82).

### 1.2 Kill switch: where it is checked
| Path | Check | file:line |
|---|---|---|
| Any authorization (jobs, tasks, memory, skills, connectors, models) | `authorize()` → `check_alive()` raises `Killed` | store.py:132-135, 219 |
| New approvals | `request()` does not create approvals while killed | store.py:143 |
| Model calls | 503 | llm_proxy.py:784-785 |
| Owner command to Hermes | 503 | owner.py:150-151 |
| Node job hand-out | `next_for` returns None; node endpoint answers `killed` | jobs.py:230-231; nodes.py:151-152 |
| Lab runner hand-out (`/runner/next`) | `next_for` (fixed after bench D-075) | jobs.py:228-231; llm_proxy.py:597-599 |
| Job already on the laptop | node kills the job in hand when `/report` says killed | node/jarvis_node/agent.py:85-93 |
| Automations | tick refuses | automations.py:438, 449 |
| Connectors | refused | connector_service.py:88, 190 |
| **Lab job already running** | **not checked.** `/runner/status` returns only the task state (llm_proxy.py:605-607). The runner stops only on cancelled/failed/completed or pause (lab/lab_runner.py:91-101). The running agent loses its model calls (503), but commands it already started keep running until the job timeout (≤ 5400 s). | — |

---

## 2. Capability truth table

Status vocabulary: Verified working / Partially working / Implemented, unverified / Mocked/simulated only / Missing / Blocked.

| Capability | Status | Files and symbols | Evidence (exact tests / result files, dates) | Limitations | What remains unverified |
|---|---|---|---|---|---|
| Risk tiers, fail-closed unknown, "a model can only raise risk" | Partially working | policy.py `RULES`, `_rule_level`, `decide` (317-398) | Integration/unit: `test_kernel.py::TestClassification`, `::TestGrants`. Bench K-001..K-033, K-090, K-110 pass (bench-20261017-220721.json). Ran myself: TestGrants passed (§5). | Unknown actions are RED (approvable at the console), not DENY. Words checked only in English. The owner_direct consequential-word list is a heuristic. | Policy against real Windows targets (real paths and real window titles). |
| Approval binding to the exact action, single use, expiry, level floor | Partially working | store.py `request` 138-151, `approve` 159-166, `authorize` 217-249; policy.py `Action.digest` 270-273 | Integration: `test_kernel.py::TestGrants::test_approval_bound_to_exact_action`, `test_approved_single_use_and_replay_refused`, `test_low_level_approval_cannot_cover_red`. `test_approvals.py` (all 12 passed in my run). `test_hardening.py::test_one_approval_allows_exactly_one_of_many_concurrent_callers`. Bench K-021..K-031, K-106, N-001..N-004, N-034, N-064. E2E-manual: `evidence_manual.json` S-real-pin "approval e2dcc0d3 via console PIN" (2026-10-16, no saved result file). | **Not bound to the requesting agent (defect D3).** `untrusted_session` is not part of the digest; policy re-decides at use, so this is acceptable. `lab_run` instructions and `connector_op` timeout/max_output are not bound (only YELLOW_AUTO kinds). | Live approval from the phone/console against the real VM, beyond the single manual row. |
| PIN approval over HTTP: YELLOW only, typed id, PIN, lockout | Partially working | owner.py `approve` 265-303, `_hash_pin`/`_check_pin` 47-59, `PIN_TRIES`/`PIN_WINDOW` 36-37 | Integration: `test_owner.py::Plane::test_http_approval_needs_pin_confirmation_and_yellow_level`, `::test_red_actions_are_console_only_and_wrong_pins_lock`, `::test_parallel_wrong_pins_cannot_exceed_the_limit` (ran: pass). `test_idempotency.py::test_approve_never_stores_a_body_with_a_pin`. Bench K-041..K-046, N-006, N-025 pass. E2E-manual: S-real-pin (2026-10-16). | The lockout is in process memory: a kernel restart resets it (theoretical, Low). It is global, not per client. The PIN can be guessed without a valid approval id: the PIN is checked before the approval is looked up, at 5 guesses per 10 min. The PIN travels as plain HTTP over Tailscale (WireGuard-encrypted). The progress report says the PIN was typed in chat and must be changed (NEKO_95_PROGRESS_REPORT.md:352, 453): still open. | Whether the PIN was rotated. |
| RED approvals console-only | Verified working (integration) | owner.py:296-298; cli.py approve path | `test_approvals.py::test_red_actions_are_not_approvable_over_http`, `test_owner.py::…red_actions_are_console_only…` (ran: pass); bench K-043. | — | — |
| Audit hash chain (edit, delete, truncation, rewrite) | Partially working | audit.py `append` 279-301, `verify` 307-330; cli.py `audit verify/anchor` 173-187; owner.py:641 `audit/verify` route | Integration: `test_kernel.py::TestAudit` incl. `test_tail_truncation_needs_anchor_and_is_detected` (ran: pass); `test_hardening.py::test_concurrent_audit_appends_keep_chain_valid`; bench K-047..K-053, N-007..N-009. Repro R7 (§5): deleting the last 5 rows → the owner route `audit/verify` says `ok: 21 events`; with the old anchor → `truncated`. | Truncation and rewrite are detected **only with an anchor**. The owner/console verify route passes none. The off-box anchor is a manual Windows script (`jarvis/scripts/pull-anchor.ps1`) that nothing schedules. The chain lives in the same SQLite file that the kernel user can rewrite (store.py:3-5 says so). | Whether any anchors were ever pulled. `vault/decisions/audit-anchors.md` is on the laptop and not in this checkout. |
| Off-box anchor export | Implemented, unverified | jarvis/scripts/pull-anchor.ps1 | None (no schedule, no recorded run in the repo) | Manual, needs the laptop (now off). | Everything. |
| Kill switch | Partially working | store.py 121-135; table in §1.2 | Integration: `test_kernel.py::TestKill` (ran: pass), `test_approvals.py::test_the_kill_switch_stops_a_pending_approval_from_being_used`, `test_llm_proxy.py::P::test_kill_switch_and_rate_limit` (ran: pass), `test_owner.py::test_kill_over_http_is_one_way`; bench K-034..K-039, N-019, N-020, N-045, N-063. E2E-manual: S-estop "kill switch engaged → PC action refused; lifted with RESUME" (2026-10-16). | Does not stop a lab job that is already running (§1.2). A file-based flag: the kernel OS user can delete the KILL file. Physical button K-040 deferred. | Lab-side behaviour on the real lab machine. Node behaviour on real Windows. |
| Idempotency keys | Partially working | idempotency.py `Idempotency.run` 376-413, `key_from` 355-362; owner.py:680-688; llm_proxy.py:777-778 | Integration: `test_idempotency.py` (12 tests, ran: pass) incl. `test_scopes_do_not_meet`, `test_a_repeat_while_the_first_still_runs_does_not_run_it_again`, `test_kept_for_24_hours_then_acts_again`. | Scope is per owner route or per agent name. **It keeps full replies for 24 h, including forgotten memory text (defect D2).** A crash between the claim and the update leaves no 'running' row (deleted on exception). A process kill leaves a 'running' row → 409 for 24 h (Low). Expired rows are pruned only when a later keyed request arrives. | Phone retry behaviour end to end. |
| Task final states and "completed needs proof" | **Partially working: self-certification bypass (defect D1)** | tasks.py `update` 72-106 (CAS, final stays final); llm_proxy.py `task_action` 575-590 | Integration: `test_tasks.py::Honesty::test_an_agent_cannot_mark_a_task_completed_without_proof` (ran: pass), N-038..N-044, N-065..N-072. Repro R1: an agent sets `verification={"passed":true}` in one update, then `completed` succeeds; the owner's `_facts` then prints "verified" (owner.py:177). | Proof is a stored flag the agent can write itself. Any `done` job of that agent (e.g. an unrelated `ui_windows`) counts as evidence. | Live lab verification honesty (the runner is trusted to report). |
| Agent tokens, lane from the token | Verified working (integration) | llm_proxy.py 52-83, 648-650, 741-756, 816 | `test_llm_proxy.py::P::test_token_lane_decides_the_providers` (ran: pass); `test_owner.py::Plane::test_only_an_owner_token_gets_in_and_it_gets_nothing_else` (ran: pass); bench K-088, K-093, K-094. | Token names are free text, and `issue` is INSERT OR REPLACE by name: re-issuing a name in another lane keeps that name's memories (see memory row). | — |
| Lanes / "fallback never crosses lanes" | Verified working (integration) for the kernel proxy | gateway.py `allowed` 92-95, `plan` 103-111; llm_proxy.py 816-831 | `test_llm_proxy.py::P::test_fallback_stays_in_the_private_lane_and_honours_the_free_model_guard` (ran: pass); `test_gateway.py`; bench J-area. | A `local` provider serves both lanes by design. | Real providers. |
| Paid-route refusal / free-model guard | Partially working | gateway.py `problems` 72-78, `model_ok` 80-81; llm_proxy.py 854-861 | Same test as above; repro R6. | The guard checks only `model`. **Agent-supplied body fields are forwarded unchanged** (`models`, `plugins`, `n`, `provider` unless extra_body sets it). R6: `models:["paid-vendor/…"]`, `plugins:[{"id":"web"}]`, `n:64` reached the upstream (defect D6). | Whether the OpenRouter account has credit (if it holds $0, the paid fallback fails). |
| Prompt scrubbing before model calls | Partially working | llm_proxy.py `_scrub_messages` 86-148; scrub.py | `test_llm_proxy.py::P::test_secrets_scrubbed_in_messages_and_tool_arguments` (ran: pass); `test_scrub.py` (ran: pass); bench K-054..K-058, K-095. Repro R5/R5d/R6. | Only `messages[].content`/`text`/`tool_calls.arguments` are scrubbed. `tools[].function.description` etc. are not (R6). **Groq keys (`gsk_…`) are not redacted** by the built-in pass or by gitleaks 8.30.1 unless written as `X_API_KEY=…` or `Bearer …` (R5d). Built-in pass misses `hf_`, `AIza`, `xox*`, `sk_live_` (gitleaks catches them when installed); PGP private key blocks are only redacted, not blocked. The deep pass fails open when gitleaks is missing (scrub.py:45-58). | Whether gitleaks is installed on the VM and reachable under `ProtectHome=read-only`. |
| Key-file handling (gateway) | Verified working (integration) | gateway.py `auth_headers` 58-70, `key_path` 53-56; llm_proxy.py 865-871 | `test_llm_proxy.py` (key never reaches the agent, line 87 assertion), bench J-cases. | A relative key path is not confined to the secrets dir (`..` is allowed); the config is owner-controlled. Provider error bodies are read and never logged (llm_proxy.py:881). | — |
| Request sizing / rate limits | Verified working (integration) | sizing.py; llm_proxy.py 786-793, 821-851 | `test_sizing.py`, `test_llm_proxy.py` | In-memory limits reset on restart. Malformed bodies (`max_tokens:"abc"`, a JSON array) crash the handler thread → connection dropped, traceback on stderr (R8; Low). | — |
| Caretaker guard / "lab can never push to main" | **Partially working: bypassable (defect D4)** | policy.py `caretaker_problems` 145-174; llm_proxy.py 516-522; caretaker/template.py | `test_caretaker_guard.py` (4 tests, ran: pass), `caretaker/tests/test_template.py` (ran: pass). Repro R4. | Text filter only. A clause containing "not/no/nothing/never" is skipped entirely, and `git -C . push` / `gh api …/merge` are not matched. The real control is GitHub branch protection, which is still a pending owner step (caretaker README step 5; guide "next steps"). The lab agent user holds a `repo`-scope GitHub login for all lab tasks (README step 4). | Branch protection state on GitHub; the lab token's scope. |
| MCP over HTTPS: SSRF, redirects, DNS pinning | Verified working (integration) | mcphttp.py `check_endpoint` 30-60, `_PinnedHTTPS` 63-72, redirect refusal 135-136; policy.py `internal_host` 212-233 | `test_mcphttp.py` (ran: pass); bench K-063..K-066, K-100..K-105. R9: NAT64/6to4/IPv4-mapped forms of internal IPv4 are classed as internal. | Resolves once and connects to that IP with SNI and certificate checks. | Real remote MCP servers. |
| Untrusted tool results / tool classification | Verified working (integration) | mcpwire.py `process_result` 187-237; connectors.py `set_tool` 179-201 (destructive/credential never enableable); connector_service.py `call_tool` 122-190 | Bench K-064..K-075, K-095..K-111; `test_connectors.py`, `test_webmcp.py` | "Untrusted" is a JSON flag plus a note: the model still reads the text, so it is marking, not isolation. The docstring at mcpwire.py:191 says the default cut is a head cut, while context_select.py says the default is "evidence" (doc drift). | Real stdio servers on Windows. |
| Memory scopes / lane isolation | Partially working | memory.py (owner = token name) 112-226; `PRAGMA secure_delete=ON` 109 | `test_memory.py` (ran: pass), `test_owner.py::Foundations::test_forget_erases_text_and_history`, bench F-area. | Scoped by **agent name, not lane**. Isolation holds only if a name is never reused across lanes. Forget wipes the row and WAL, **but the forget reply (with the text) is kept in `idempotency` for 24 h when a key was used (D2)**. | — |
| Vault write folders, control characters | Verified working (integration) | vault.py `_resolve` 16-34, `write` 37-58 | `test_vault.py` (ran: pass); bench K-060..K-062, K-108 | TOCTOU between `realpath` and `open` (symlink swapped after the check); `islink` is re-checked only on the final file, not on the parent folders (theoretical, Low; same-user attacker only). | Windows junctions on the real vault. |
| Context selection (untrusted marking) | Implemented (no authority role) | context_select.py | `test_context_select.py`; context bench results | Selection never authorizes (docstring 21-23); no untrusted marking lives here (it is in mcpwire). | — |
| Privacy gateway (redaction before public models) | Partially working | jarvis/privacy/gateway.py `redact_body` 58-83, `scrub_keys` 196-202, `serve` 376-379 (127.0.0.1) | `jarvis/privacy/test_privacy.py` (not run by me); `eval_pii.py` results not re-checked here. | Deep (PII model) redaction only for roles user/tool/function. **System and assistant content get patterns only** (DEEP_ROLES, gateway.py:46, 68). `tools`, `name` and other fields are not redacted. No Host/Origin check (unlike the console), so it is DNS-rebinding reachable (theoretical, Low). Fallback keys are scrubbed from error bodies. | Live Dahl traffic; whether N.E.K.O puts personal memory in system prompts. |
| Private→public data flow (lab dispatch) | **Partially working (defect D5)** | llm_proxy.py `dispatch_to_lab` 499-537 | Repro R10: a private agent with `public_ok:true` queued a lab job containing a synthetic name, address, phone and email. | The "may be shared" decision is a flag the private-lane model sets. The scrub removes secrets only, not PII. | — |
| Laptop node allowlists and path checks | Implemented, unverified on Windows (integration on Linux) | node/jarvis_node/jobs.py `project_check` 39-95 (projects allowlist, secret env removed), `_output_path` 103-113; pcopen.py `resolve` 82-101, `pc_open` 137-184 (DOC_TYPES allowlist; realpath confinement); guardproxy.py | `test_node_paths.py`, `test_neko_node.py`, `test_browser.py` (ran: pass); bench K-076..K-082, N-031, N-032 | `open_file` allows .html/.svg/.docx/.xls/.zip (macro documents rely on Office Protected View). `os.startfile` on Windows was not exercised. | All Windows behaviour (no Windows here). |
| Browser guard proxy and Smokescreen | Partially working | node/jarvis_node/guardproxy.py (resolves, refuses internal, connects to the checked IP); deploy/jarvis-smokescreen.service (deny ranges, Tailscale IP); hermes services set HTTP(S)_PROXY to it | Integration: bench K-081, K-082, N-032; `test_browser.py` | Smokescreen runs without an egress ACL file, so any tailnet peer can use it as an open public proxy. The owner's own Chrome profiles (`browser_ext`) are not behind the guard proxy; only the URL the request names is checked by policy. | Smokescreen is not run in this repo's tests at all (no evidence). |
| Console: loopback Host, same-origin POST/DELETE, PIN not logged | Verified working (integration) for the checks | console/jarvis_console/server.py 85-99, 186-192, 81-82; kernel_client.py | `jarvis/console/tests/test_console.py` (not run by me, 93 OK baseline per memory) | No authentication beyond loopback plus a custom header. **Any local process on the laptop can act as the owner** (owner_direct PC actions, commands, kill, stop_all). Only approvals need the PIN (theoretical, Medium). | Real browser behaviour. |
| llama-server flag guard | Verified working (unit, config lint) | jarvis/local_models/test_server_flags.py | Ran: pass (§5) | Static check of the YAML only. llama-swap itself runs without an API key and with permissive CORS (the docstring says so). | The real launched command line on the laptop. |

---

## 3. Defects

Each defect has a severity and is marked **confirmed** (reproduced in a temp kernel in the container) or **theoretical**.

### D1. An agent can self-certify task verification and mark the task completed — **Medium, confirmed**
- **Code.**
  - llm_proxy.py:581-587 checks `cur.get("verification").passed is True`, where `cur` is the stored row.
  - llm_proxy.py:588-589 lets the same agent write `verification=p.get("verification")` in any update.
  - tasks.py:96 stores any dict.
  - `planning → completed` is an allowed transition (tasks.py:18).
- **Reproduction** (repro.py, R1), real HTTP handler on 127.0.0.1:
  1. A completion without proof gets `409 completed needs proof`.
  2. `{"verification":{"passed":true,"by":"me"}}` returns 200.
  3. `{"state":"completed"}` returns 200.
  4. DB row: `["completed", "{\"passed\": true, \"by\": \"me\"}"]`.
- **Impact.**
  - The honesty guard ("completion needs proof, not a claim") is bypassed.
  - Owner-facing text then says "verified": `Owner._facts`, owner.py:177.
  - Separately, any `done` job of that agent counts as proof (llm_proxy.py:583-584), with no link to the task.
- **Test gap.** `test_tasks.py::Honesty` does not try writing `verification` first.

### D2. Forgotten memory text is kept for 24 h in the idempotency table — **Low, confirmed**
- **Code.**
  - `Memories.forget` returns `{"forgotten": {..."text"...}}` (memory.py:221).
  - `Idempotency.run` stores every reply below 400 for 24 h (idempotency.py:408-410), for agents (llm_proxy.py:777) and for the owner's `memory` route (owner.py:680-688).
- **Reproduction** (R2): after `forget` with `Idempotency-Key: forget-0001`, the memory row is `["", "deleted"]`, but 1 row in `idempotency.reply` contains the synthetic text.
- **Impact.** "Forget is real erasure" (guide; memory.py:201) is false for 24 h when a key is used.
  - Other replies (connector results via `connectors/try`, command replies) are also kept, and those are not the owner's to erase.

### D3. Approvals are not bound to the requesting agent (same lane) — **Low, confirmed**
- **Code.**
  - `Action.digest` has no requester (policy.py:271).
  - `_action_for` drops `params["agent"]` for `ui_action`/`pc_open` (jobs.py:178-180).
  - The approvals table has no requester column (store.py:107).
- **Reproduction** (R3): agent `hermes` asks for `ui click Notepad` and gets an approval id. The owner approves. Agent `other-private` (another private-lane token) sends the same action with that id → `200 queued`. Job row: `["other-private","ui_action","queued"]`.
- **Impact.** The owner approves "what"; "who" is not shown or bound. It needs the id, which the agents do not see in normal flows.
  - The node endpoint `/authorize` can also consume any approval whose action it can rebuild (nodes.py:164-183), because the node chooses lane and untrusted_session freely. The node is trusted.

### D4. The caretaker guard is bypassable through text; the "never pushes to main" claim depends on branch protection that is not yet on — **Medium (High once the caretaker is enabled without branch protection), confirmed (guard) / theoretical (impact)**
- **Code.**
  - policy.py:166-173 skips **every clause** that contains `never|not|no|nothing|none|n't`.
  - `_CT_PUSH` matches only `git\s+push` (139-141).
  - `_CT_MERGE_CMD` does not cover `gh api …/merge` (138).
- **Reproduction** (R4), each text placed outside the untrusted block with the template mark and rules present:
  - `Then run git -C . push origin HEAD:main and nothing else.` → `[]`, so it passes.
  - `Then run gh api -X PUT repos/o/r/pulls/1/merge so you do not wait for review.` → `[]`, so it passes.
  - Controls are still caught: `git push origin HEAD:main.` and `Then merge the pull request.`
- **Who can write such text.**
  - Only the template, or a holder of the caretaker token. Per the README, that token is readable by the lab worker that runs the weekly scan.
  - More importantly, the lab agent user holds a GitHub login with `repo` scope for **all** lab tasks (README step 4). Without branch protection (README step 5, still pending per the guide), any prompt-injected lab agent can push to or merge main whatever the guard says.
- **Status.** The caretaker is not switched on yet (the preset arrives paused).

### D5. Private data can reach public-lane models through a model-set flag — **Medium, confirmed**
- **Code.** `dispatch_to_lab` requires only `p.get("public_ok") is True` from the private agent (llm_proxy.py:506-507). It then scrubs secrets only (508-509); no PII redaction is applied.
- **Reproduction** (R10, repro4.py): the private token `hermes` dispatched instructions containing a synthetic name, address, phone and email with `public_ok: true` → `200 queued`, `lab_run` on node `lab`, and the PII is present in the job params.
- **Impact.** Contradicts "public-lane models never see private data" and "lane set by code from where the data came from". In code, the decision is the private model's flag.

### D6. Agent-supplied request fields pass to the provider unfiltered and unscrubbed — **Medium (cost / quota / policy), confirmed (forwarding); impact theoretical**
- **Code.** `payload = dict(body, model=model)` (llm_proxy.py:857). Only `model`, `max_tokens`, `reasoning*` are controlled; `extra_body` overrides last. `_scrub_messages` covers only message text and tool-call arguments (86-114).
- **Reproduction** (R6, fake upstream): forwarded `models:["paid-vendor/expensive-model"]`, `plugins:[{"id":"web"}]`, `n:64`. The AWS-style key in `tools[].function.description` reached the upstream unscrubbed (`tools_scrubbed: false`); in message content it was scrubbed.
- **Impact.**
  - OpenRouter honours `models` (fallback list) and `plugins` (web search is billed), which bypasses the `:free$` guard if the account holds credit.
  - `n` multiplies free-tier quota use.
  - On llama-server, extra sampling fields such as `n_predict` may override the cap (not tested).

### D7. Groq API keys are not redacted by either scrub pass — **Low/Medium, confirmed**
- **Reproduction** (R5d), gitleaks 8.30.1 in the image: `gsk_` + 52 characters in plain text or after `GROQ_KEY ` → not redacted, no findings. It is caught only as `Bearer …` or `NAME_API_KEY=…`.
- **Impact.** Groq is the private lane's main provider and the privacy gateway's fallback, and the project's own evals look for `gsk_` leaks (evals/pc_control/run_suite.py:96).
- **Also.** The built-in pass (used for the audit chain, and everywhere when gitleaks is missing) misses `hf_`, `AIza`, `xox*`, `sk_live_`, and PGP private key blocks (R5).

### D8. The owner-facing audit verification cannot detect truncation; the off-box anchor is manual — **Low (Medium for the claim), confirmed**
- **Code.** owner.py:641 calls `verify()` with no anchor. audit.py:307-330 detects truncation and rewrite only with an anchor. `pull-anchor.ps1` is manual, on the laptop, and not scheduled.
- **Reproduction** (R7): deleting the last 5 audit rows → owner route `{"ok": true, "message": "ok: 21 events"}`. With the pre-deletion anchor → `truncated: have 21, anchor says 26`.

### D9. The console trusts every local process — **Medium, theoretical**
- Any process on the laptop can POST `http://127.0.0.1:8799/api/owner/...` with `X-Jarvis-Console: 1` and no Origin (server.py:91-94). That covers N.E.K.O plugins, MCP servers started by node connectors, and malware.
- It gets the owner plane: owner_direct PC/UI/browser actions (YELLOW_AUTO for ordinary clicks and typing), Hermes commands, stop_all and kill.
- Approvals still need the PIN. A browser page cannot do this, because of CORS preflight on the custom header and the Host check.

### D10. Smaller items — **Low / Informational**
- **Lab job keeps running under the kill switch** (§1.2) — Low, theoretical.
- **`llm-proxy --bind` accepts `0.0.0.0`** (cli.py:126 has no check, unlike cli.py:108) — Low, confirmed by code reading.
- **Malformed chat bodies drop the connection** with a traceback on stderr (`max_tokens:"abc"`, a JSON array) (llm_proxy.py:801, 807) — Low, confirmed (R8).
- **In-memory PIN lockout and rate limits reset on restart** (owner.py:70; llm_proxy.py:273-276) — Low, theoretical.
- **Memory isolation is by token name.** `AgentTokens.issue` is INSERT OR REPLACE by name (llm_proxy.py:71), so re-issuing `hermes` as public would hand it the private memories — Low, theoretical (operator action).
- **Privacy gateway has no Host/Origin checks** (DNS rebinding), and system/assistant roles get pattern-only redaction (gateway.py:46, 68) — Low/Medium, theoretical.
- **Smokescreen runs without an egress ACL** on the Tailscale IP; the Hermes dashboard binds the Tailscale IP (deploy units) — Informational.
- **Vault TOCTOU** between `realpath` and `open` (vault.py:24-50) — Low, theoretical (same-user attacker).
- **`untrusted_session` is not in the approval digest.** Policy re-decides at use, so it is safe — Informational.

---

## 4. Doc-vs-code discrepancies

| Claim (source) | Code reality |
|---|---|
| "Audit log … its head is copied off the server so tampering is caught" (guide, Safety) | The copy is a manual Windows script (`jarvis/scripts/pull-anchor.ps1`) and nothing schedules it. The owner/console `audit/verify` uses no anchor, so truncation passes as "ok" (D8). |
| "Lanes … set by code from where the data came from (unknown means private) … public-lane models (Dahl) never see private data" (guide) | In the kernel the lane is the **token's** lane (llm_proxy.py:302-303, 816). Private→public lab dispatch is allowed when the private model sets `public_ok: true`, with secrets scrubbed but not PII (D5). No data-provenance labelling exists in the kernel. |
| "It can never push to or merge main; the kernel refuses such a task" (guide, caretaker) | The kernel refuses only text that its regexes catch, and they are bypassable (D4). The lab user has a `repo`-scope login. The docs themselves (caretaker README, 05_OPERATIONS.md:451) list branch protection as a separate owner step that is not yet done. |
| "Forget is real erasure" (guide, memory.py:201) | The forgotten text stays in `idempotency.reply` for 24 h when a key was used (D2). |
| "Kernel memory: scoped per agent and lane" (guide) | Scoped by agent (token name) and scope string only; no lane column (memory.py:104-123). |
| "Kill switch. Stops all new work, including lab jobs" (guide) | True for *new* work. A lab job already running is not told about the kill (§1.2). The page wording is accurate only for "new". |
| "A scrubber cleans everything sent to a model" (guide, Secrets) | Only message text and tool-call arguments. Tool descriptions and other fields pass through (D6). Groq keys are missed (D7). |
| "Agent browsing goes through Smokescreen" (guide) | True for the Hermes services on the VM (deploy units set HTTP(S)_PROXY). The laptop agent browser uses the node's own `GuardProxy`. The owner's Chrome profiles (`browser_ext`) are not proxied. Smokescreen is not covered by any test in the repo. |
| "Approvals are bound to … a hash of the exact action" (guide; 03_DECISIONS D16) | True for the action. Not bound to the requesting agent (D3). |
| mcpwire.py:191 docstring: default cut is "the first max_chars characters" | context_select.py:1-3 and D29: the default is "evidence". Internal doc drift. |
| 03_DECISIONS D15 "Approvals over HTTP are YELLOW only … with no PIN the route refuses" | Matches the code (owner.py:279-298). |

---

## 5. Commands run and their real outputs

All commands ran with `nice -n 19` and `--cpus=1`, with the repo mounted `:O` (a throw-away overlay) and the scratchpad mounted `:ro`.

1. **Targeted kernel tests:**
   ```
   podman run --rm --cpus=1 --userns=keep-id -e HOME=/tmp -v ~/hq-neko:/src:O neko-test sh -c "cd /src && python -m pytest -q -p no:cacheprovider \
    jarvis/kernel/tests/test_kernel.py::TestGrants jarvis/kernel/tests/test_kernel.py::TestAudit jarvis/kernel/tests/test_kernel.py::TestKill \
    jarvis/kernel/tests/test_approvals.py jarvis/kernel/tests/test_owner.py::Plane::test_red_actions_are_console_only_and_wrong_pins_lock \
    jarvis/kernel/tests/test_owner.py::Plane::test_parallel_wrong_pins_cannot_exceed_the_limit jarvis/kernel/tests/test_owner.py::Plane::test_only_an_owner_token_gets_in_and_it_gets_nothing_else \
    jarvis/kernel/tests/test_caretaker_guard.py jarvis/kernel/tests/test_idempotency.py \
    jarvis/kernel/tests/test_llm_proxy.py::P::test_token_lane_decides_the_providers jarvis/kernel/tests/test_llm_proxy.py::P::test_fallback_stays_in_the_private_lane_and_honours_the_free_model_guard jarvis/kernel/tests/test_llm_proxy.py::P::test_kill_switch_and_rate_limit jarvis/kernel/tests/test_llm_proxy.py::P::test_secrets_scrubbed_in_messages_and_tool_arguments \
    jarvis/kernel/tests/test_tasks.py::Honesty::test_an_agent_cannot_mark_a_task_completed_without_proof jarvis/local_models/test_server_flags.py"
   ```
   Output: `63 passed, 7 subtests passed in 44.64s`

2. **Node, SSRF, vault, scrub, memory and caretaker template tests:**
   ```
   ... python -m pytest -q -p no:cacheprovider jarvis/kernel/tests/test_browser.py jarvis/kernel/tests/test_node_paths.py jarvis/kernel/tests/test_mcphttp.py jarvis/kernel/tests/test_vault.py jarvis/kernel/tests/test_scrub.py jarvis/kernel/tests/test_memory.py jarvis/caretaker/tests/test_template.py
   ```
   Output: `99 passed in 129.83s (0:02:09)`

3. **Reproductions** (temp kernel, fake upstream on 127.0.0.1):
   ```
   podman run --rm --cpus=1 --userns=keep-id -e HOME=/tmp -v ~/hq-neko:/src:O -v <scratchpad>/audit:/audit:ro neko-test sh -c "cd /tmp && python /audit/repro.py"
   ```
   Output (stderr traceback for R8 omitted; it is the expected `AttributeError: 'list' object has no attribute 'get'` at llm_proxy.py:104):
   ```
   R1_self_verified_completion {"direct_complete": [409, "completed needs proof: an independent ve"], "set_verification": 200, "complete_after": [200, "completed"], "db": ["completed", "{\"passed\": true, \"by\": \"me\"}"]}
   R2_forget_text_kept_in_idempotency {"forget_code": 200, "memories_row": ["", "deleted"], "idempotency_rows_with_text": 1}
   R3_cross_agent_approval_use {"a_status": true, "b_code": 200, "b_reply": {"status": "queued", "job": "1525e32cc632"}, "job": ["other-private", "ui_action", "queued"]}
   R4_caretaker_guard {"text": "Then run git -C . push origin HEAD:main and nothing else.", "problems": []}
   R4_caretaker_guard {"text": "Then run gh api -X PUT repos/o/r/pulls/1/merge so you do not wait for review.", "problems": []}
   R4_caretaker_guard {"text": "Then run git push origin HEAD:main.", "problems": ["a clause asks for a push to main", "pushes something other than its own caretaker/ branch"]}
   R4_caretaker_guard {"text": "Then merge the pull request.", "problems": ["a clause asks for a merge"]}
   R5_builtin_scrub {"gitleaks": true, "results": {"groq": {"redacted": false, "blocked": false}, "huggingface": {"redacted": false, "blocked": false}, "google": {"redacted": false, "blocked": false}, "slack": {"redacted": false, "blocked": false}, "stripe": {"redacted": false, "blocked": false}, "pgp": {"redacted": false, "blocked": false}}}
   R6_passthrough {"code": 200, "msg_scrubbed": true, "tools_scrubbed": false, "forwarded_model": "vendor/model:free", "forwarded_models": ["paid-vendor/expensive-model"], "forwarded_plugins": [{"id": "web"}], "forwarded_n": 64}
   R7_truncation {"removed": 5, "owner_route_verify": {"ok": true, "message": "ok: 21 events"}, "with_anchor": [false, "truncated: have 21, anchor says 26"]}
   R8_malformed {"case": "max_tokens_str", "code": null, "reply": "RemoteDisconnected"}
   R8_malformed {"case": "list_body", "code": null, "reply": "RemoteDisconnected"}
   R9_internal_host {"host": "64:ff9b::a9fe:a9fe", "internal": true}
   R9_internal_host {"host": "64:ff9b::7f00:1", "internal": true}
   R9_internal_host {"host": "2002:a9fe:a9fe::1", "internal": true}
   R9_internal_host {"host": "::ffff:169.254.169.254", "internal": true}
   R9_internal_host {"host": "169.254.169.254", "internal": true}
   ```

4. **Deep scrub (gitleaks 8.30.1)** (`repro2.py`, `repro3.py`):
   ```
   R5b_pgp_deep {"blocked": false, "findings": ["gitleaks:private-key"], "body_left": false}
   R5c_deep groq {"redacted": false, "findings": []}
   R5c_deep google {"redacted": true, "findings": ["gitleaks:gcp-api-key"]}
   R5c_deep slack {"redacted": true, "findings": ["gitleaks:slack-bot-token"]}
   R5c_deep stripe {"redacted": true, "findings": ["gitleaks:stripe-access-token"]}
   R5d groq 'config value g' {"redacted": false, "findings": []}
   R5d groq 'GROQ_KEY gsk_u' {"redacted": false, "findings": []}
   R5d groq 'Authorization:' {"redacted": true, "findings": ["bearer"]}
   R5d huggingface_letters 'config value h' {"redacted": true, "findings": ["gitleaks:huggingface-access-token"]}
   ```

5. **Private→public dispatch** (`repro4.py`):
   ```
   R10_private_to_public_lab {"code": 200, "status": "queued", "lab_job_node": "lab", "pii_in_lab_job": true}
   ```

6. **Recorded evidence read (not re-run):**
   - `jarvis/evals/neko95/results/bench-20261017-220721.json`: counts `{'pass': 834, 'fail': 0, 'error': 0, 'deferred': 26}`. K: 109 pass + 2 deferred (K-040, K-089). N: 73 pass.
   - `jarvis/evals/neko95/evidence_manual.json` (hand-recorded, 2026-10-16, no result files): S-real-pin pass, S-estop pass, T-real-pin-approved-repair pass, S-no-false-completion **fail** ("needs the full acceptance re-run").

### Suggested fixes (not applied; audit only)
- **D1:** never accept `verification` from an agent token. Only the runner or the owner may set it. Require evidence jobs to be linked to the task.
- **D2:** do not store replies of `memory forget` (or store a redacted reply). Prune by TTL on a timer.
- **D3:** store the requester in the approval row, include it in the digest, and show it on the approval card.
- **D4:** turn on GitHub branch protection before enabling the caretaker. Give the lab a fine-grained token limited to `caretaker/*` pushes. Do not let negation skip a whole clause; match `push` with any `git` options and `gh api … merge`.
- **D5:** run the privacy redactor on lab dispatch, or require owner approval for private→public dispatch.
- **D6:** forward an allowlist of OpenAI fields only. Scrub `tools` too.
- **D7:** add `gsk_[A-Za-z0-9]{40,}` (and the others above) to the built-in pass, plus a gitleaks custom rule.
- **D8:** schedule the anchor export from the VM to an off-box store. Have `audit/verify` use the latest anchor.
- **D9:** have the console require a per-session secret (cookie plus header) that only its own page knows.
