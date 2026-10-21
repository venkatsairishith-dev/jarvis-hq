# Audit: D) persistent memory and context, E) model and agent orchestration

- Repo: /srv/jarvis/home/hq-neko, commit 88ec49a2, branch main, clean. Audit date 2026-10-18. Read-only.
- Claim checked: "JARVIS In Depth" (scratchpad/jarvis-guide/jarvis-in-depth.html), sections 3, 5, 6, 8.
- Where I could look: the Linux test server (Xeon Gold 6244, no GPU, microphone, Windows, WSL lab, Oracle VM or Hermes). Everything about the laptop, the VM, Hermes and the WSL lab can only be judged from code and the files recorded in the repo. Several "live" claims exist only as prose in jarvis/docs/*.md, with no result file. They are labelled **doc-only** below.
- Evidence classes used: **E2E-live** (a recorded live run with a result file), **integration** (real components wired together, outside boundaries faked), **unit/mocked**, **doc-only** (prose claim, no artifact), **none**.

---

## 1. Real execution flow (file:line)

### 1a. Owner request through N.E.K.O, main_agent and JARVIS
1. **The N.E.K.O session starts and memory goes into the system prompt.**
   - `main_logic/core/lifecycle.py:1592-1610` fetches `GET /new_dialog/{name}` from the memory server (port 48912).
   - `lifecycle.py:2229-2241` appends the result to `initial_prompt`.
   - `main_logic/omni_offline_client/_streaming.py:144-152` sends that as the **SystemMessage**.
   - The text contains the persona, reflections and recent history (`app/memory_server/routes.py:7453`, ~7570-7790).
   - Separately, the `recall_memory` tool (`main_logic/core/tool_calling.py:314,371` → `POST /query_memory`, `routes.py:7149` → `memory/hybrid_recall.py:1091`) returns memory as tool output.
2. **The conversation model call** goes to N.E.K.O's configured model URLs. On the laptop these point at the privacy gateway 127.0.0.1:48950. That is a runtime config (`core_config.json`), not in the repo, and is checked only by `jarvis/scripts/live_check.py:170-183`.
   - The gateway runs the PII model only on `user`/`tool`/`function` roles (`jarvis/privacy/gateway.py:46,68`).
   - The **system prompt, which holds the recalled memory, gets regex patterns only.**
   - The request then goes to Dahl (`gateway.py:162-176` load; chain at `:284`). The fallback is Groq, if its key file exists.
3. **main_agent** (`plugin/plugins/main_agent/__init__.py:356 run_task`):
   - Builds the catalogue (`:157`, `planner.py:137`) and plans (`__init__.py:207` → `planner.planner_messages` `planner.py:188-208`).
   - The planner prompt contains **only the catalogue and the task text. No memory of any kind is retrieved for planning.**
   - Plans are validated by `parse_plan` (`planner.py:264-317`), then run in waves by `executor.run_plan` (`executor.py:96-196`).
   - The LLM is N.E.K.O's "agent" slot (`__init__.py:180-205`).
4. **Jarvis step** (`__init__.py:271-294`):
   - `jarvis_bridge:delegate_task` → `client.py:71` `POST /api/owner/command` on the console (`jarvis/console/jarvis_console/server.py:306`) → kernel `/v1/jarvis/owner/command` → `owner.py:139 command()`.
   - `command()` checks scrub, kill switch, lane wait and the busy lock, then spawns `_run` (`owner.py:230`).
   - `_run` calls the **owner's Hermes on the VM** (`llm_proxy.py:258-261`, `telegram.hermes_agent`) with `STATE_RULE + text`, then appends the kernel's own record (`owner.py:170 _facts`).
   - main_agent polls `jarvis_bridge:task_result` every 5 s. By default the reply is withheld from N.E.K.O (`client.py:38-43`).
5. **The owner's Hermes:**
   - Its model calls go to the kernel proxy `/v1/chat/completions` (`llm_proxy.py:734-965`), private lane fixed by the token (`llm_proxy.py:54-86`).
   - Its memory is the `jarvis_memory` provider plugin (`jarvis/hermes/plugins/jarvis_memory/__init__.py:87-101`). At every session start and every compaction it injects `memory render` plus `task checkpoint` into the system prompt.
   - Its tools come from `jarvis/lab/jarvis_mcp.py` (owner profile), and every tool call is a kernel action (`llm_proxy.py:302-447`).
6. **Hands-on work goes to the lab:**
   - `task_start` → `create_task` (`llm_proxy.py:539`); `lab_work` → `dispatch_to_lab` (`llm_proxy.py:499-537`).
   - That path is scrubbed, has owner-path and caretaker guards and one active job per task, and creates a `lab_run` job.
   - The WSL `lab_runner.py:352 handle()` runs worker → separate verifier session (`:388`, `verify_prompt :138`) → runner's own deliverable and portable checks (`:403-405`) → repair loop → delivery to the laptop.

### 1b. PC path (desktop plugin)
- `coordinator.Coordinator.with_context` (`plugin/plugins/desktop/coordinator.py:256-262`) calls `previous()` (`:217-249`).
  - `previous()` reads only the newest task record from the last 10 min and takes file, url and app from passed steps only.
  - It passes that record to `decide.decide` (`decide.py:724-745`).
  - "open it" is resolved without a model (`decide.follow_up :641-658`). Other follow-ups get the previous request text as context when `FOLLOW_CUE` matches.
- `decide.ask_model` (`decide.py:151-166`) also appends the owner-promoted examples from the skills registry (`overlay() :282-291`) to the decider's system prompt.
  - This, plus `previous()`, is the only "memory" that reaches the PC planner.

### 1c. Kernel memory call sites (who actually reads it)
| Read path | Caller | Feeds a model? |
|---|---|---|
| `Memories.render` (`memory.py:296`) | Hermes plugin `_record()` (`jarvis_memory/__init__.py:89`) via `memory_action` (`llm_proxy.py:465`) | **Yes**: Hermes system prompt, every session and compaction |
| `Memories.list(query)` | `jarvis_memory_list` tool | Yes, when the model calls the tool |
| `Memories.resolve` (BM25) | `memory_action` resolve (`llm_proxy.py:463`) and forget-by-content (`:472-476`) | **No.** No Hermes tool calls `resolve`. BM25 only decides forget-by-content and the eval. |
| `Tasks.checkpoint` (`tasks.py:157`) | Hermes plugin `_record()` (`:90`) | Yes |
| lesson gate (`memory.py:228-294`) | `owner.py:409-426` (candidates), `owner.py:621-634` (console) | Activated lessons render like other entries |

**Nothing in N.E.K.O, main_agent, research, local_team or the desktop decider reads kernel memory.** The two memory systems are not connected.

---

## 2. Capability truth table

| Capability | Status | Files / symbols | Evidence | Limitations | Unverified |
|---|---|---|---|---|---|
| Kernel memory: ids, versions, CAS update, conflict | **Verified working (integration)** | `memory.py:166-198`; `llm_proxy.py:449-484` | bench F-001…F-012, F-027, F-035-F-037, F-041, F-044 pass (bench-20261017-220721.json, Linux). kernel `tests.test_memory`: I ran it, OK | Single SQLite DB on the VM | Live Hermes use since 2026-10-11 (doc-only) |
| Kernel memory: forget = real erasure | **Verified working (integration)** | `memory.py:200-222` (secure_delete, WAL truncate) | F-007, F-008 (disk check), F-034 | `erased` is false when another reader holds the WAL; then old pages may remain | — |
| Kernel memory: secrets refused | Verified (integration) | `memory.py:155-164` | F-017, F-018 | Uses the scrubber's patterns plus gitleaks | — |
| Kernel memory: per-agent isolation | Verified for distinct names; **Partially** for lanes | `llm_proxy.py:54-86,449-484` | F-015, F-026, F-039, F-040 | **Isolation is by token name, not lane.** Re-issuing a name in another lane keeps its memories (defect M-2) | — |
| Kernel memory: retrieval (BM25 resolve) | Verified (unit/eval) | `memory.py:49-152` | I ran `memory_retrieval.py`: hit@1 0.979 (46/47), MRR 0.989, unique_wrong 0; F-031 | 47-item fixture written by the developer. **resolve() never feeds a prompt.** | Real-world queries |
| Kernel memory reaches Hermes prompt | **Partially working** | `memory.py:296-305`, `jarvis_memory/__init__.py:87-101` | F-013 checks only size and the "… N more" line; hermes test_jarvis_memory_plugin (unit) | **Oldest-first 1600-char cut drops the newest entries** (defect M-1, reproduced) | Live Hermes behaviour (F-030 deferred) |
| Lessons (self-improvement) gate | Verified (integration) | `memory.py:225-294`, `owner.py:409-426` | F-020-F-022, F-045 | Duplicate/contradiction check is Jaccard ≥0.6 only | Live use |
| Task checkpoint for fresh/compacted sessions | Verified (integration) | `tasks.py:157-202`, plugin `_record` | bench A-080 cited in code; `tests.test_tasks` | 3 tasks, 900 chars | Hermes compaction live (doc-only, 2026-10-11) |
| Hermes USER.md / MEMORY.md | **Replaced/disabled by design** | Config on the VM (not in repo) | 02_ARCHITECTURE.md:207-209 (USER.md live test, 2026-10-10: remove failed); D14 | Hermes config lives on the VM | Whether the VM config really has `memory.provider: jarvis_memory` and USER.md off |
| Vault (jarvis-vault) write rules | **Implemented, unwired** | `jarvis_kernel/vault.py:16-58` | `tests/test_vault.py`, bench K adapter (unit) | **No runtime caller.** The vault-write skill (`jarvis/skills/vault-write/SKILL.md`) asks the model to follow the rules and "push", so enforcement depends on the model | Vault repo is not in this repo |
| coordinator.previous() follow-ups | Verified (unit/integration) | `coordinator.py:217-262`, `decide.py:641-658` | bench B/D cases (Linux) | Newest record only; broad `FOLLOW_CUE` ("it/that/this/too") | Live on Windows (deferred) |
| N.E.K.O 5-layer memory (facts, recent, reflections, persona) | Implemented upstream; **unit-tested upstream** | `memory/*`, `app/memory_server/*` | Upstream unit suites (~108 memory test files; doc says 41,937 upstream pass on laptop 2026-10-15, doc-only); bench F-028, F-029, F-046-F-050 (Linux, in-process) | See below | Live recall quality on the laptop |
| N.E.K.O reinforce/dispute ("disputed sinks") | **Partially working** | `memory/evidence.py`, `persona/rendering.py:317-395`, `reflection/surfacing.py:174-202` | F-048 (sink ordering) | Disputed persona/pending reflections are **still injected** while they fit the 2000-token budget. They are only sorted lower; archived after 14 sub-zero days (defect M-6) | — |
| N.E.K.O stale marking | **Partially working** | `memory/temporal.py:268`, `persona/rendering.py:759-822` | F-047 | Applied only to confirmed reflections at render time. Facts, persona, pending reflections and recall output never get the flag | — |
| N.E.K.O contradictions/corrections | Implemented (LLM + trust arbitration) | `fact_dedup.py:1194-1482`, `persona/corrections.py:56` | F-029, F-046 | Decisions are LLM-made, and the correction LLM is Dahl (public) | Live |
| N.E.K.O per-item delete of private memory | **Missing** | memory_router.py has no per-fact delete | — | Only scoped (visit/group) forget and whole-character delete exist. Archives keep text; archived facts stay recallable | — |
| N.E.K.O memory provenance | **Partially** | `facts.py:4059-4108` | — | No message, conversation or turn id; recall output has no source | — |
| Memory into the main_agent plan | **Missing** | `planner.py:188-208` | code | Planner sees the task and catalogue only | — |
| Plugin `ctx.query_memory` | **Broken (returns placeholder)** | `plugin/server/application/messages/memory_query_service.py:43` → `routes.py:2028-2047` | code | Deprecated endpoint always says "memory unavailable" | — |
| Kernel model gateway: lanes never crossed | **Verified (E2E-live, Linux, public lane)** | `gateway.py:92-111`, `llm_proxy.py:816` | live-dahl-20261016-163134.json JL-006 (private task → 503, 0 Dahl calls); 12/12 pass | — | Private lane live (Groq/OpenRouter): doc-only |
| Fallback within lane, 5xx retry once, 429 bench, outage rest | Verified (integration, fake providers) | `llm_proxy.py:874-965`, `sizing.py:classify/cooldown_s` | `tests.test_llm_proxy`, `test_sizing` (I ran test_sizing: OK); bench J | Fallback stops once bytes stream; a broken stream is not retried | Real 429 behaviour of the live providers |
| Request sizing, 413 compress, summary checks | Verified (integration) | `sizing.py`, `llm_proxy.py:821-857, 909-930` | test_sizing; docs (2026-10-11 live, doc-only) | Estimate is chars/4 | — |
| Private lane for the owner's Hermes | **Partially working (single provider)** | providers.json; `llm_proxy.py:278-296` | My repro R3 | Only `openrouter-private` (`:free` model) fits a ~12k-token owner request. Groq (8000 total) and vm-llama-cpu (4096) never fit (defect M-4) | Live availability of that free model |
| Public lane (Dahl MiniMax → GLM) | **Verified (E2E-live, Linux, 2026-10-16)** | providers.json `dahl-minimax` p11, `dahl-glm` p13 | live-dahl (5/5 correct, p50 4.07 s, streaming, tool calls, key never echoed) | All 5 audited calls were served by dahl-minimax. **GLM fallback was not exercised live** in that file | GLM fallback under 429 |
| Privacy gateway (N.E.K.O → Dahl, Groq fallback) | Integration-tested; live on laptop **doc-only** | `jarvis/privacy/gateway.py` | `test_privacy.py` (28 tests); D19 "measured live" 2026-10-15 (doc-only); bench J-routing | System role pattern-only (M-3); names and places are not redacted by default | Live redaction rate on real conversations |
| Local models: llama-swap vram4 config | Implemented; laptop **doc-only** | `local_models/llama-swap.yaml` | test_server_flags.py (flags); doc: laptop vram4, 3439 MiB free | One model on the GPU, ttl 300 | Laptop runtime |
| Hardware profiles cpu8/cpu16/vram8plus/remote_only | **Partially working** | `hwdetect.py:102-124`, `profiles/*.yaml`, `local-models.ps1:16-24` | test_hwdetect (fake readings) | **No `decider` or `vision` alias in any non-vram4 profile; remote_only starts no models** (defect M-5) | Never run on such hardware (NEKO_REQUIREMENTS M2 says so) |
| PC decider Qwen3-4B-Instruct-2507 | **Verified on the Linux CPU only** | `decide.py:22,151-166` | models-20261017-060424.json: 152/157 (Xeon Gold 6244, llama.cpp b11669 CPU, 6 threads); re-runs 19:18-20:53 with b11714 also 152 | Guide says "Laptop GPU"; GPU behaviour with `-c 2048` is not in any result file | Laptop GPU accuracy and latency |
| local_team router (rules → Jev → Ling) | Unit-tested; live doc-only | `local_team/core.py:133-181` | test_core (21); D19/D20 numbers (doc-only) | Jev service in WSL (8091); falls back to Ling, then "general" | Live |
| main_agent planner/executor | **Verified (unit/mocked)** | `planner.py`, `executor.py` | I ran test_planner + test_executor: pass; doc says the panel ran a task end to end "two of two" (2026-10-15, doc-only) | No memory; one run at a time, with a race (M-7) | Live E2E with a real model |
| jarvis_bridge → console → kernel → owner's Hermes | Unit (bridge) + **doc-only** live | `client.py`, `owner.py:139-260` | test_client (9); live_check 31/31-57/57 (doc-only, 2026-10-14/15) | Reply hidden by default; `_facts` lists all tasks updated in the window, not only this command's | Live |
| Lab: dispatch → worker → independent verifier → delivery | Integration (test_lab_runner, test_repair_loop); live **doc-only** | `lab_runner.py:352-431` | 02_ARCHITECTURE.md:190-205 (tasks 9f98…, 84f6…, dates ~2026-10-10/11, doc-only) | Verifier is the same Hermes on the same Dahl lane (session independence only) | No lab result files in the repo |
| Agent-to-agent `start_lab_task` / `lab_task_result` | **Implemented, unverified; defects** | `jarvis_mcp.py:268-312` | none | Kernel only authorizes and audits a char count. No job, no verification. PATH puts `~/.local/bin` first (M-8). Zombie child reads as "running" (M-9) | Everything |
| Research plugin (search, fetch, write, claim check) | **Verified (E2E-live, Linux, public questions)** | `research/core.py:208-336`, `verify.py` | live-research-dahl 2026-10-16 16:41 / 18:26 / 18:40: 5/5 pass each; supported share 0.416 → 0.382 → 0.723 | Pass rule is lax (M-10). Live runs used xsmall NLI, **not MiniCheck**. Writer and planner were Dahl via the kernel proxy, not the laptop path (local planner + gateway writer) | Laptop path; MiniCheck in production (file not yet on the laptop) |
| Deliverables (deck/PDF + QA) | Integration-tested on Linux | `deliverables/qa.py`, `deck.py`, `report.py` | bench area I: 31 pass, 2 deferred (I-017/I-018 need Windows) | — | Windows open/slideshow |

---

## 3. Confirmed defects

**M-1 (Medium, confirmed, reproduced). The kernel's session memory block drops the newest entries.**
- `Memories.list` orders by `created` ascending (`memory.py:122`). `render()` (`:296-305`) keeps lines until 1600 chars, then appends "… N more".
- Above roughly 14-16 typical entries, every newer preference is missing from Hermes's system prompt. That includes a correction saved as a new entry, and every activated lesson, since lessons are newest.
- BM25 `resolve` is not used to pick what is rendered.
- Repro (R1, my run): 30 entries plus one `NEWEST` entry → 14 lines rendered, `newest entry in session block: False`. `resolve('aisle seats flight')` ranks NEWEST first, but nothing calls resolve for the prompt.
- Bench F-013 checks only the size and the "N more" line, so it cannot catch this.

**M-2 (Low, confirmed, reproduced). Memory and task ownership is keyed by the token NAME, not the lane.**
- `AgentTokens.issue` (`llm_proxy.py:65-76`) does `INSERT OR REPLACE` by name. Re-issuing a name with a different lane keeps all memories under that name. The CLI's default lane is `private` (`cli.py:49`).
- Repro (R2): private entry added as `agentx`; re-issued as public → the public token lists "Private fact: the PAN card is in the blue folder".
- Needs an admin CLI action, so this is not remotely exploitable.
- It does contradict the guide's "a public-lane agent can't reach your private memory" as a structural guarantee: there is no lane column on memories.

**M-3 (Medium, confirmed by code; documented trade-off). Recalled N.E.K.O memory reaches Dahl with pattern-only redaction.**
- `/new_dialog` memory is placed in the system prompt (`lifecycle.py:2229-2241`). The privacy gateway runs GLiNER and Jev only on user/tool/function roles (`gateway.py:46,68`; D20 "Rules around the model pass").
- A secret with no cue word that was stored in memory and recalled into the system prompt therefore goes to Dahl in clear.
- Names and places are never redacted by default (D19).
- The memory server's own summary, correction and fact-extraction LLM tiers also go to Dahl via the gateway (05_OPERATIONS.md:251; upstream `docs/architecture/memory-system.md:197-198`). So the owner's conversation content is processed by a public-lane provider.
- This contradicts the guide's "Public-lane models (Dahl) never see private data" and CLAUDE.md's "Dahl never gets private data", except under D19's narrow reading ("private *values*").

**M-4 (Medium, confirmed, reproduced). The owner's private lane effectively has one provider.**
- The owner's Hermes request is about 10.6-12k tokens (`OWNER_REQUEST_TOKENS`, `llm_proxy.py:212`).
- R3 on the real providers.json, private lane:
  - groq `max_total_tokens` 8000 → never fits;
  - vm-llama-cpu 4096 → never fits;
  - local-llama-vulkan is `os_only: nt` (laptop), but the kernel runs on the VM.
- Only `openrouter-private` (a single `:free` model) can serve owner commands. "Fallback inside the same lane" is therefore not available for the owner's agent. The "private fallback Qwen3-4B on CPU" in the guide cannot take a Hermes turn; it only serves small `gateway.call` prompts.

**M-5 (Medium, confirmed by code). Non-vram4 hardware profiles break the PC decider and vision.**
- `decide.DEFAULT_MODEL = "decider"` (`decide.py:22`). Vision uses the alias `vision`.
- Only `llama-swap.yaml` (vram4) defines these aliases (`:45,:52`). `profiles/cpu8.yaml`, `cpu16.yaml` and `vram8plus.yaml` define neither, and `remote_only` starts no local models (`local-models.ps1:22`).
- On any other machine every decider call fails, and `decide()` returns `fallback`.
- On the laptop, detection picks vram4 only with ≥3000 MiB free VRAM. Measured free was 3439 MiB (05_OPERATIONS.md:368), a ~440 MiB margin, and D28 notes the owner's apps hold ~1.2 GB. A detection run while other apps hold more VRAM would select cpu16 and break the decider.
- Theoretical on the laptop; certain on other hardware. No test asserts that each profile has the aliases callers use.

**M-6 (Medium, confirmed by code; N.E.K.O upstream). "Disputed facts sink" orders entries but does not exclude them.**
- `/new_dialog` persona rendering sorts by evidence score and trims by budget (`memory/persona/rendering.py:314-326,334-395`), with no `score<0` filter.
- Pending reflections are filtered by status only (`reflection/surfacing.py:174-184`).
- A disputed item stays in the system prompt until it has 14 sub-zero days (`evidence.py:254`, `evidence_loops.py:908-1034`).
- Disputing a reflection does not suppress its source facts in `recall_memory` (`hybrid_recall.py:1018-1028`).

**M-7 (Medium, theoretical). main_agent `run_task` race.**
- `if self._active is not None` (`__init__.py:360`) is checked before `await self._make_plan(...)` (up to 2×120 s). `_active` is set only in `_start` (`:324-331`).
- Two concurrent calls can both pass the check and start two runs. The second overwrites `self._cancel`, so `stop_task` can stop only the second, and the first run's `finally` clears `_active` while the second still runs.
- `resume_run` has the same pattern (`:386`).
- Depends on the plugin runtime dispatching entries concurrently. Not reproduced: it needs the plugin SDK.

**M-8 (Medium, confirmed by code). The lab-integrity fix was not applied to `start_lab_task`.**
- `lab_runner.agent_env` puts `~/.local/bin` LAST because a worker once planted `~/.local/bin/python3` (`lab_runner.py:54-58`, D14).
- `jarvis_mcp.start_lab_task` prepends it FIRST: `PATH=~/.local/bin:$PATH` (`jarvis_mcp.py:279`).
- It also runs with no fingerprint guard and no verifier, and the kernel only authorizes `("lab","task")` with a char count (`llm_proxy.py:440-442`).
- Agent-to-agent sub-tasks are therefore unguarded and unverified.

**M-9 (Low, confirmed, reproduced in principle). `lab_task_result` can report "running" forever.**
- `start_lab_task` discards the `Popen` (`jarvis_mcp.py:288-292`). `lab_task_result` decides "running" by `os.path.exists(f"/proc/{pid}")` (`:302`).
- An exited, unreaped child is a zombie whose /proc entry still exists. R4: a discarded `Popen(["true"])` after 1 s → `exists: True state: Z`.
- It is reaped only when the same process creates another Popen.

**M-10 (Low, confirmed). The live research pass rule is too lax to back "checked" claims.**
- `live_research_dahl.py:86-92` passes a case when the answer word appears anywhere, including the "could not be confirmed" section, there are no fabricated URLs, and ≥1 citation exists.
- The 16:41 and 18:26 runs "pass 5/5" with only 41.6% and 38.2% of cited sentences supported.
- The live runs used the xsmall NLI checker, not MiniCheck.

**M-11 (Low, confirmed by code). Research searches a private question unredacted when the privacy gateway is down.**
- `research/__init__.py:116-120`: `redact = None` → note "the question was searched as typed". This fails open to the search providers (DuckDuckGo/Tavily).

**M-12 (Low, confirmed by code; upstream). N.E.K.O memory robustness gaps.**
- (a) Plugin `ctx.query_memory` always gets a placeholder: `memory_query_service.py:43` → deprecated `routes.py:2028-2047`.
- (b) The JARVIS counter hardening (`evidence._int_counter`, F-050) is not used in `evidence_loops.py:977,1015` (`int(r.get('sub_zero_days',0) or 0)`), `reflection/evidence_flow.py:175,180` or `persona/facts.py:880,886`. One damaged counter aborts that character's archive sweep.
- (c) There is no per-item delete for the owner's private N.E.K.O memory. Archives keep text, and archived facts remain in the BM25 recall pool (`hybrid_recall.py:1155-1186`).
- (d) The WeChat plugin pulls the owner's private `/new_dialog` (`plugin/plugins/wechat_integration/__init__.py:740`), which is a privacy risk if it is enabled.

**M-13 (Informational).** Smaller confirmed issues:
- `owner._facts` (`owner.py:170-180`) lists every task updated during the turn, from any agent, not just this command's.
- A worker timeout does not force `passed=False` if the verifier passes (`lab_runner.py:393-396`).
- `llm_proxy` `int(body["max_tokens"])` on a non-numeric value raises inside the handler (`llm_proxy.py:807-809`), which drops the connection instead of returning 400.
- `FOLLOW_CUE` matches very common words (`coordinator.py:213`), so the previous request is passed to the decider as context more often than the comment suggests.

---

## 4. Doc vs code discrepancies

1. **Guide §8 "Search uses BM25 … right memory first in 97.9%"** is true for `resolve()` (I reproduced 46/47). But resolve only drives forget-by-content. What the agent actually sees is `render()`, oldest-first and truncated (M-1).
2. **Guide §4/§8 "a public-lane agent can't reach your private memory"**: holds only because names differ. There is no lane on memory rows (M-2).
3. **Guide §4 and CLAUDE.md "Dahl never gets private data"** vs D19/D20 and code: Dahl receives the full conversation, the recalled memory (system role, pattern-only) and the memory server's summarisation and correction traffic, minus PII placeholders (M-3).
4. **Guide §6 "Private fallback Qwen3-4B on CPU (Oracle)"**: it cannot fit an owner Hermes turn (4096 tokens); Groq cannot either. The private lane is single-provider for the owner (M-4).
5. **Guide §6 "PC decider Qwen3-4B-Instruct-2507 (152/157) — Laptop GPU"**: the 152/157 was measured on the Linux Xeon CPU (models-20261017-*.json). No laptop GPU measurement of this model is in the repo.
6. **Guide §6 "Hardware profiles … picked automatically"**: the non-vram4 profiles lack the `decider` and `vision` aliases, and remote_only has no local models (M-5). NEKO_REQUIREMENTS.md:43 admits "not run on such hardware".
7. **Guide §8 "disputed facts sink, stale states marked"**: disputed items still render; stale is computed only for confirmed reflections, never persisted, and not applied to facts or persona (M-6, §2).
8. **Guide §8 "The vault: agents may write only to inbox/, daily/, decisions/"** and **02_ARCHITECTURE.md:44 "the write path checks the folder in code"**: `vault.py` has no runtime caller. Agents follow a SKILL.md that also says "push".
9. **Guide §6 "Conversation … Dahl public lane: MiniMax-M2.7, then GLM-5.3-Flash"**: the privacy gateway (N.E.K.O's path) falls back Dahl → Groq, not → GLM (`privacy/providers.json`). MiniMax → GLM exists only in the kernel's public lane (lab and research harness).
10. **Guide §5 "research … check every cited sentence with the claim checker" with MiniCheck as the new checker**: every live research run in the repo (2026-10-16) used xsmall. MiniCheck is not on the laptop yet (D30).
11. **Guide §5 "A separate verifier checks every criterion"**: true for `lab_work` (runner). Not true for `start_lab_task` agent-to-agent tasks (M-8).
12. **02_ARCHITECTURE.md:262 "Preferences live in Hermes memory"** (Telegram continuity) vs D14 and 02_ARCHITECTURE.md:302-305 "the kernel is JARVIS's only memory". Internal inconsistency.
13. **README.MD:602/703 refers to `app/memory_server.py`**: it is the package `app/memory_server/` (upstream doc drift).
14. **Live claims with no artifact in the repo**:
    - `live_check.py` 31/31…57/57 (05_OPERATIONS.md:230-299);
    - the lab full-loop and robustness runs (02_ARCHITECTURE.md:190-205);
    - the Hermes USER.md test;
    - privacy gateway "measured live";
    - the main_agent panel "two of two".

    These remain doc-only.

---

## 5. Commands run and real outputs

1. Repro script (my file, outside the repo), test container, overlay mount:
   `nice -n 19 podman run --rm --cpus=1 --userns=keep-id -e HOME=/tmp -v ~/hq-neko:/src:O -v <scratchpad>/audit-mma:/w:ro neko-test sh -c "cd /src && python -B /w/repro.py"`
   ```
   == R1 render(): which entries reach the session prompt
   rendered lines: 14 | chars: 1561
   first: [m-44094ead v1] (preference) Preference number 00: the owner likes opt
   last: ... 18 more (list them with jarvis_memory_list)
   newest entry in session block: False
   resolve('aisle seats flight') top: NEWEST: the owner now prefers aisle seats on every flight
   == R2 memory is keyed by token NAME, not lane: re-issuing a name in another lane keeps its memories
   old token still valid: False | new token -> ('agentx', 'public')
   public-lane token now reads: ['Private fact: the PAN card is in the blu']
   issue() default lane: private
   == R3 which configured providers can take the owner's agent request (~12k tokens + 1024 out)
   private lane:
       ('local-llama-vulkan', 'local', 10, 'nt', None, None, True)
       ('groq', 'private', 20, None, None, 8000, False)
       ('openrouter-private', 'private', 30, None, 56000, None, True)
       ('vm-llama-cpu', 'local', 90, 'posix', None, 4096, False)
      lane_context_length: 56000
   public lane:
       ('local-llama-vulkan', 'local', 10, 'nt', None, None, True)
       ('dahl-minimax', 'public', 11, 'posix', None, None, True)
       ('dahl-glm', 'public', 13, 'posix', None, None, True)
       ('vm-llama-cpu', 'local', 90, 'posix', None, 4096, False)
      lane_context_length: 3072
   == R4 subprocess zombie: ...
   exists: True state: Z
   ```
   local-llama-vulkan is unavailable on the VM (`os_only nt`). The kernel runs on the VM.
2. `python -B jarvis/evals/memory_retrieval.py` (container):
   `{"queries": 47, "memories": 47, "hit@1": 0.979, "mrr": 0.989, "unique_right": 25, "unique_wrong": 0, "misses": ["latest time I can have coffee"]}`
3. `cd jarvis/kernel && python -B -m unittest tests.test_memory tests.test_memory_retrieval tests.test_sizing` → `Ran 32 tests in 107.498s OK`.
4. `python -m pytest plugin/plugins/main_agent/tests/test_planner.py plugin/plugins/main_agent/tests/test_executor.py plugin/plugins/jarvis_bridge/tests/test_client.py jarvis/hermes/tests/test_jarvis_memory_plugin.py jarvis/lab/tests/test_lab_runner.py -q` → `68 passed, 10 skipped in 10.63s`. These are unit/mocked tests; they prove no live behaviour.
5. Read-only inspection of result files (python json reads on the host):
   - bench-20261017-220721.json: Linux x86_64, `{'pass': 834, 'fail': 0, 'error': 0, 'deferred': 26}`; area F 49 pass, 1 deferred (F-030 live lifecycle).
   - live-dahl-20261016-163134.json: providers dahl-glm and dahl-minimax, 12/12; all audited calls by dahl-minimax.
   - live-research-dahl-*: 5/5 each; supported_share 0.416, 0.382, 0.723.
   - live-plans-dahl-20261016-171332.json: Dahl as substitute decider. plan_cases 6/9, dev 40/70, holdout 20/30, holdout2 31/40.
   - models-20261017-060424.json: Xeon Gold 6244 CPU, b11669 CPU build; qwen3-4b-2507 152/157, qwen3-1.7b 130/157, qwen3.5-4b 146/157.
6. Spot checks of the subagent's N.E.K.O findings (sed/grep): `routes.py:2028-2047` placeholder; `evidence_loops.py:977,1015` raw `int()`; `persona/rendering.py:317-326` sort only; `lifecycle.py:2229-2241` system-prompt concatenation. All confirmed.

Not done, on purpose: no full suites (another job runs them), no services, no network, no secrets folders opened. Hardware, VM, Hermes and WSL-lab behaviour is unverifiable here.
