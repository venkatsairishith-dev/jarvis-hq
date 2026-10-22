# Integrating Hermes Agent, OpenCode, goose, openmuse and the OpenClaw/Grok Bot features into JARVIS's own codebase (as of 22 Oct 2026)

Method: on 2026-10-22 I shallow-cloned and read the four upstream repos at their current HEADs:
- NousResearch/hermes-agent `main` @944c3a43 (22 Oct 2026)
- anomalyco/opencode `dev` @f7d5b6ff (22 Oct 2026)
- block/goose → aaif-goose/goose `main` @6835dd45 (22 Oct 2026)
- CopilotKit/openmuse `main` @1ac68f39 (6 Oct 2026)

I read the JARVIS repo through `gh api` (branch `remediation-2026-10-18`). Library versions and licenses come from the PyPI JSON API, queried the same day.

Integration-form codes used below:
- **(a)** import as a Python library inside the JARVIS process
- **(b)** vendor the source in-tree and modify it
- **(c)** port specific features into JARVIS plugins
- **(d)** ship as a bundled, JARVIS-supervised child process that uses JARVIS's config, auth and audit

The decisive constraint is that JARVIS is pinned to `requires-python = "==3.11.*"`. The N.E.K.O base's pyproject also carries a `jarvis` dependency group that already pins `dbos==3.3.0`, `pdfplumber==0.11.10`, `pypdfium2==5.14.0` and `python-pptx`, and core deps include `pypdf>=6.19.0,<7` and `playwright>=1.58.0` ([jarvis-hq pyproject.toml](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/pyproject.toml)).

## Q1. Hermes Agent: language, license, modules, library use, version, and what JARVIS should import vs port

### Takeaway
Hermes Agent cannot be imported into JARVIS's Python 3.11 process. It is a ~1.0M-line Python codebase that **runs only on Python 3.14**. On Python 3.11–3.13 it installs with no dependencies and does not import, and it publishes no supported wheel.

The best "fully integrated" form is a hybrid:
- **(b-lite + d) for the runtime:** vendor the pinned upstream source into the JARVIS repo unmodified (git subtree under `jarvis/third_party/hermes-agent`, pinned to a release tag). JARVIS builds it into its own 3.14 venv and runs it as a supervised child: gateway/API server, ACP and cron. JARVIS generates its `config.yaml` and `.env`. All of JARVIS's changes live as Hermes plugins in JARVIS's own tree: memory provider (already exists), `pre_tool_call` policy hook to the kernel, and audit hook.
- **(c) for the parts JARVIS needs inside its own 3.11 process:** port skill format and staging, curator lifecycle, FTS5 session search, dangerous-command detection, pairing and heartbeat semantics.

### Cited Findings
**Identity, license, version**
- License is MIT, "Copyright (c) 2025 Nous Research" — [LICENSE](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE)
- Tags combine semver and calendar versions:
  - the latest semver release is **v0.22.4**, with canary `v0.22.4+canary.20261022`;
  - the calendar tags run to `v2026.9.24`.
  - Source: `git ls-remote --tags` on [github.com/NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/tags)
- JARVIS's own upgrade study gives the release history:
  - v0.22.0 (14 Oct);
  - v0.22.1 was "built from the wrong commit", so skip it;
  - v0.22.2 (15 Oct) is "the last version whose changes are documented";
  - v0.22.3/0.22.4 (21 Oct) form a 4,206-file roll-up whose notes are deferred to v0.23.0.
  - The study recommends staging v0.22.2.
  - Source: [HERMES_0_22_UPGRADE_2026-10-22.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/HERMES_0_22_UPGRADE_2026-10-22.md)
- JARVIS currently runs Hermes on the Oracle VM:
  - as user `jarvis-agent`, with the gateway API on 127.0.0.1:8642 and the dashboard on Tailscale only;
  - with a custom model provider pointing at the kernel's endpoint;
  - with only the memory and todo toolsets enabled, plus JARVIS's own memory provider plugin `jarvis/hermes/plugins/jarvis_memory`;
  - in "our 3.14 environment".
  - Source: [same doc](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/HERMES_0_22_UPGRADE_2026-10-22.md); [jarvis_memory/plugin.yaml](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/hermes/plugins/jarvis_memory/plugin.yaml). Systemd units `jarvis/deploy/jarvis-hermes-gateway.service` and `jarvis-hermes-serve.service` exist in the repo tree.

**Python version and packaging (the blocker for option a)**
- The docs say: "Hermes runs only on **Python 3.14**". `pyproject.toml` declares `>=3.11,<3.15`, but "every runtime dependency carries a `python_version >= '3.14'` marker". So "`pip install .` … built on Python 3.11–3.13 installs `hermes-agent` with **no dependencies** and the package does not import." — [docs: updating.md, "Python 3.14 and older interpreters"](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/getting-started/updating.md)
- Every direct dependency is exact-pinned (`==X.Y.Z`). This was "tightened on 2026-05-12 in response to the Mini Shai-Hulud worm hitting mistralai 2.4.6" — [pyproject.toml](https://github.com/NousResearch/hermes-agent/blob/main/pyproject.toml)
- `pyproject.toml` `version = "0.0.0"`. "Hermes does not publish a supported wheel or source distribution for `requirements.txt` installs". Library use means cloning, then `source ./activate`, then running your app from that activated checkout — [docs: Using Hermes as a Python Library](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/guides/python-library.md)
- Size (my count at HEAD): about **1,014,744 lines of non-test Python**. The docs cite "~25,000 tests across ~1,250 files" — [docs: architecture.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/architecture.md)

**Library API (exists, but only in a 3.14 Hermes venv)**
- `from run_agent import AIAgent`
- Constructor: `AIAgent(model=..., quiet_mode=True, enabled_toolsets=[...], disabled_toolsets=[...], ephemeral_system_prompt=..., save_trajectories=...)`
- Methods: `agent.chat(msg)` returns a string. `agent.run_conversation(user_message, task_id=, conversation_history=, system_message=)` returns `{final_response, messages}`.
- The docs say "Always set `quiet_mode=True` when embedding".
- `batch_runner.py` handles parallel prompts.
- Source: [python-library.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/guides/python-library.md)

**Module structure** — [architecture.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/architecture.md)
- **Entry points:** CLI (`cli.py`), Gateway (`gateway/run.py`), ACP (`acp_adapter/`), Batch Runner, API Server, Python Library.
- **Agent loop:** `run_agent.py` is the AIAgent facade. The loop itself is in `agent/conversation_loop.py` and `agent/turn_*.py`.
  - Prompt builder, provider resolution and three API modes (chat_completions, codex_responses, anthropic).
  - `model_tools.py` handles dispatch, and `tools/registry.py` holds the registry: "70+ tools, 28 toolsets".
- **Session storage:** SQLite + FTS5 (`hermes_state.py` plus about 80 `hermes_state_*.py` siblings at the repo root).
- **Tools (one file per tool):**
  - `approval.py` (dangerous command detection), `terminal_tool.py`, `file_tools.py`, `web_tools.py`, `browser_tool.py`, `code_execution_tool.py`
  - `delegate_tool.py` (sub-agent delegation), `mcp_tool.py` (MCP client), `skills_*`, `skill_manager_tool.py`
  - `environments/` (terminal backends)
- **Terminal backends** in `tools/environments/`: local, docker (+ `docker_egress`), ssh, modal/managed_modal, daytona, singularity, vercel_sandbox. Directory listing at HEAD.
- **Gateway:** `gateway/run.py`, `session.py`, `delivery.py`, `pairing.py` ("DM pairing authorization"), `hooks.py`.
- **Platform plugins:** the docs list telegram, discord, slack, whatsapp, matrix, mattermost, email, sms, dingtalk, feishu, wecom, irc, line, teams, google_chat, buzz, ntfy, photon, raft, simplex, whatsapp_cloud, signal, weixin, bluebubbles, qqbot and yuanbao. HEAD also contains `a2a`.
- **Other directories:** `cron/` (scheduler), `plugins/memory/`, `plugins/context_engine/`, `skills/`, `optional-skills/`.
- **Optional extras** — [pyproject.toml](https://github.com/NousResearch/hermes-agent/blob/main/pyproject.toml):
  - `messaging` = python-telegram-bot[webhooks]==22.8, discord.py 2.7.1, slack-bolt 1.30.0
  - `acp = ["agent-client-protocol==0.9.0"]`
  - `mcp==2.3.0`
  - `voice` (faster-whisper 1.2.1), `wake` (pyopen-wakeword, sherpa-onnx, pvporcupine), `piper`
  - `modal`, `daytona`, `vercel`

**Features JARVIS cares about (official docs at HEAD)**
- **Skills / `skill_manage` / write approval:**
  - Skills are SKILL.md files compatible with agentskills.io.
  - `skill_manage` supports create, patch, rewrite and delete.
  - `skills.write_approval: true` stages writes under `~/.hermes/pending/skills/`.
  - The linter "warns but never blocks".
  - Source: [Hermes docs: Skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills) (via prior note `skills_coordination_personality.md`)
- **Curator:**
  - Background maintenance of **agent-created** skills.
  - Deterministic transitions: unused 14 days becomes `stale`, unused 30 days is archived to `~/.hermes/skills/.archive/`.
  - Pinned skills and cron-referenced skills are skipped.
  - It "never auto-deletes".
  - An LLM review pass is spawned as a background `AIAgent` fork when `interval_hours` (default 7 days) has passed and the agent has been idle for `min_idle_hours` (default 2).
  - `hermes curator run --dry-run` is available.
  - Source: [docs: curator.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/curator.md)
- **Heartbeat vs cron:**
  - `/heartbeat every 10m <prompt>` re-enters the current session when idle, one per session.
  - `hermes cron` is "a fresh isolated session per tick", "fully durable scheduler".
  - Cron flow: load due jobs from jobs.json, create a fresh AIAgent, inject attached skills, run, then deliver to the target platform.
  - Source: [docs: heartbeat.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/heartbeat.md); [architecture.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/architecture.md). Croniter 6.0.0 is a core dependency — [pyproject.toml](https://github.com/NousResearch/hermes-agent/blob/main/pyproject.toml)
- **Kanban:**
  - "a durable task board, shared across all your Hermes profiles", stored in `~/.hermes/kanban.db`.
  - "every worker is a full OS process with its own identity".
  - Toolset: `kanban_create`, `kanban_complete`, `kanban_request_review`, `kanban_block` and others.
  - `delegate_task` children are bounded by `delegation.max_concurrent_children` and `max_spawn_depth`.
  - Source: [docs: kanban.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/kanban.md)
- **Computer use:**
  - The `computer_use` toolset "speaks MCP over stdio to `cua-driver`".
  - Background control on macOS, Windows and Linux with any tool-capable model.
  - Source: [docs: computer-use.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/computer-use.md)
- **API server:** "exposes hermes-agent as an OpenAI-compatible HTTP endpoint". Enable with `API_SERVER_ENABLED=true` and `API_SERVER_KEY` — [docs: api-server.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/api-server.md)
- **The plugin hook that makes kernel-governed integration possible — `pre_tool_call`:**
  - It fires before every tool execution, built-in or plugin.
  - Possible return values:
    - `{"action":"block","message":...}`
    - `{"action":"approve","message":...,"rule_key":...}`, which escalates to the human-approval gate; "denial, timeout, or gate error fails closed"
    - `{"action":"modify","args":{...}}`
  - A callback that times out or raises also **fails closed**.
  - Source: [docs: hooks.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/hooks.md)
- **Module sizes relevant for porting** (`wc -l` at HEAD):
  - `tools/approval*.py`: about 4,200 lines (`approval_detection.py` alone is 1,572)
  - `gateway/pairing.py`: 660
  - `cron/`: 20+ `scheduler_*.py` modules
- **Known risk:** 9router's CVE-2026-108593 can inject keys into Hermes `config.yaml` (a shell hook via `baseUrl`) — [VulnCheck](https://www.vulncheck.com/advisories/9router-0.4.1-through-0.5.99-config-injection-rce-via-hermes-settings-endpoint) (from the prior report)

### Inferences
- **Option (a) in-process import: rejected.** It needs Python 3.14, while JARVIS/N.E.K.O is pinned to `==3.11.*` with 24,000+ unit tests and native deps. There is no wheel, and the code is about 1M lines. Making JARVIS itself 3.14 would make import possible. That would be a whole-platform migration, though, and N.E.K.O's CONTRIBUTING says "Python 3.11 only". Flag it as a long-term option, not a plan step.
- **Option (b) vendor-and-modify the whole tree: rejected as a modification target.** Upstream churn is extreme (one roll-up touched 4,206 files), so any in-tree edits would conflict on every pull.
- **Recommended for the runtime: "vendored pristine + supervised child" (b-lite + d).** This meets the owner's "not a separate repo/app" rule:
  1. `git subtree add --prefix=jarvis/third_party/hermes-agent … v0.22.2 --squash`. Never edit inside that prefix. Record the tag and commit in `jarvis/third_party/PATCHES.md`, which already exists.
  2. A JARVIS build step creates a 3.14 venv from the vendored `pm/lock.json`/`uv.lock`. A JARVIS supervisor (systemd units already exist) starts the gateway, API server and cron with `HERMES_HOME` under JARVIS's data dir.
  3. JARVIS generates `config.yaml` and `.env` from its own config. The model provider is the kernel endpoint, and secrets come from the kernel's secret broker. JARVIS verifies `config.yaml` by hash at start, reusing `jarvis/hermes/check_hermes_compat.py`.
  4. JARVIS code lives as Hermes plugins under `jarvis/hermes/plugins/`:
     - `jarvis_memory` (exists);
     - a new **`jarvis_policy`** `pre_tool_call` hook that calls the kernel tier/PIN API and returns block, approve or allow. It fails closed by design;
     - a `post_tool_call` or audit hook that appends to the kernel's hash-chained audit.
  5. The owner never sees "Hermes" as a separate app. JARVIS's UI and Telegram front it.
- **Recommended native ports (c), into JARVIS 3.11 plugins, with upstream file provenance recorded:**

| Hermes feature | Port into | Notes |
|---|---|---|
| SKILL.md format + `skill_manage` staging (`write_approval`) + `skills_guard` scan | `jarvis/skills/skillgate.py`, `plugin/plugins/desktop/skills.py` | skillgate already runs Hermes's `skills_guard`. Port the scanner logic so it runs without the Hermes venv |
| Curator lifecycle (14d stale / 30d archive, pin, never delete, dry-run) | learn_vault / skill gate | Deterministic part only. The LLM consolidation pass goes through the kernel as a proposal |
| FTS5 session search | N.E.K.O memory layer | SQLite FTS5 is in Python's stdlib `sqlite3` |
| Dangerous-command detection (`tools/approval_detection.py`, `threat_patterns.py`) | kernel tier classifier | Pure-Python patterns. Port as data plus tests |
| DM pairing (`gateway/pairing.py`) | JARVIS channel layer (Q5) | Allowlist-only |
| Heartbeat vs cron semantics, cron "fresh session + attached skills + deliver" | DBOS schedules (Q4/Q5) | |
| Kanban board (durable tasks + handoffs + review states) | main_agent coordinator on DBOS queues | Concept port, not code |

- Hermes features to keep **off** in the child: Connectors/Nous tool gateway, Relay/Hermes Cloud, shared metrics, and Skills Hub auto-install. This repeats the repo's own upgrade study.

### Gaps
- I did not verify whether `run_agent.AIAgent` can be driven through Hermes's ACP server by a Python 3.11 ACP client for every feature, such as cron and kanban. Those features likely need the gateway, not ACP.
- I did not measure Hermes's 3.14 venv size or RAM on ARM (the prior note estimates ~0.5–1 GB).
- I did not check whether `optional-skills/` contains content under non-MIT licenses.

## Q2. OpenCode: architecture, license, `serve` API/SDK, ACP; embed vs sidecar; Python-native coding-agent alternatives

### Takeaway
OpenCode is now a pure TypeScript/Bun monorepo, MIT-licensed, at **v2.1.2 (21 Oct 2026)**. It has 4,269 TS files and **no Go code remains**. It is built into a compiled single executable, and its SDK is TypeScript-only (`@opencode/sdk`). Its agent core cannot be embedded in Python.

If OpenCode is kept, JARVIS should treat it as **(d) a bundled, pinned binary sidecar**:
- spawned by JARVIS over ACP (Python `agent-client-protocol` SDK), or over `opencode serve`'s OpenAPI 3.1 HTTP API, from which a Python client can be generated;
- with permission requests answered by the kernel through the V2 `permission.request`/`session.permission.reply` endpoints.

For a Python-native coding worker, **mini-swe-agent (MIT, v2.4.6, py>=3.10, ~100-line agent class, bash-only)** is the best fit for (a)/(b). **OpenHands SDK (MIT, 1.57.0) requires Python >=3.12**, so under JARVIS's 3.11 pin it would have to be a child process too.

### Cited Findings
**OpenCode**
- `LICENSE`: "MIT License, Copyright (c) 2025 opencode". Root `package.json` `"version": "2.1.2"`, `packageManager: bun@1.4.2`. Tags v2.1.0–v2.1.2 — [anomalyco/opencode](https://github.com/anomalyco/opencode)
- CHANGELOG "Release notes for OpenCode V2". v2.1.2 dated 2026-10-21. It adds `OPENCODE_DISABLE_FREE_MODELS` and `experimental.location_idle_timeout` — [CHANGELOG.md](https://github.com/anomalyco/opencode/blob/dev/CHANGELOG.md)
- Architecture, from my scan of the tree:
  - workspace packages include `cli`, `core`, `server`, `protocol`, `sdk`, `tui`, `desktop`, `app`, `plugin` and `codemode`;
  - **0 `.go` files, 4,269 `.ts` files**;
  - `packages/cli/script/build.ts` compiles release executables (`compileExecutable`);
  - the only other SDK is `sdks/vscode`.
  - Source: [repo tree](https://github.com/anomalyco/opencode/tree/dev/packages)
- SDK: `@opencode/sdk` v2.1.2, MIT, TS/ESM (`exports: ./src/index.ts`). No Python SDK is in the repo — [packages/sdk/package.json](https://github.com/anomalyco/opencode/blob/dev/packages/sdk/package.json)
- **`opencode serve`:**
  - `opencode serve [--port 4096] [--hostname 127.0.0.1] [--mdns] [--cors]`;
  - `OPENCODE_SERVER_PASSWORD` turns on HTTP basic auth (username `opencode` or `OPENCODE_SERVER_USERNAME`);
  - the TUI is itself a client of this server;
  - the server "publishes an OpenAPI 3.1 spec" at `http://<host>:<port>/doc`, used to generate the SDK.
  - Source: [docs/server.mdx](https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/server.mdx) (also [opencode.ai/docs/server](https://opencode.ai/docs/server/))
- **V2 HTTP API:** "Current endpoint count: 139", regenerated 2026-09-13 from `packages/protocol/openapi.json`. Relevant endpoints include:
  - `GET /api/permission/request` (`permission.request.list`)
  - `POST /api/session/{id}/permission` (non-blocking evaluation)
  - `POST /api/session/{id}/permission/{requestID}/reply` (field renamed to `decision`)
  - `GET /api/session/{id}/inbox`
  - PTY and worktree groups
  - Source: [V2_HTTP_API_AUDIT.md](https://github.com/anomalyco/opencode/blob/dev/V2_HTTP_API_AUDIT.md)
- **ACP:** "`opencode acp` … starts OpenCode as an ACP-compatible subprocess that communicates with your editor over JSON-RPC via stdio". Implementation is in `packages/cli/src/acp/` (agent.ts, sessions.ts, catalog.ts) — [docs/acp.mdx](https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/acp.mdx)
- Prior-note facts, still valid:
  - `OPENCODE_CONFIG_CONTENT`, `OPENCODE_PERMISSION` (inline JSON), `OPENCODE_DISABLE_AUTOUPDATE`;
  - permission keys use allow/ask/deny, and **most default to allow**.
  - Source: [opencode.ai/docs/cli](https://opencode.ai/docs/cli/); [opencode.ai/docs/permissions](https://opencode.ai/docs/permissions/)

**ACP from Python**
- `agent-client-protocol` 0.12.1 (16 Aug 2026), requires Python >=3.10,<3.15 — [PyPI](https://pypi.org/project/agent-client-protocol/)
- The SDK exposes `spawn_agent_process` / `spawn_client_process` for programmatic launch, `acp.client` async base classes, `acp.helpers` (builders for content, tool calls, permissions), and `acp.contrib` ("permission brokers", "tool call trackers") — [ACP Python SDK docs](https://agentclientprotocol.github.io/python-sdk/)
- Hermes pins `agent-client-protocol==0.9.0` for its own ACP adapter — [hermes pyproject.toml](https://github.com/NousResearch/hermes-agent/blob/main/pyproject.toml)

**Python-native alternatives** (PyPI JSON API, 2026-10-22)

| Project | Version / date | Python | License | Embeddable? |
|---|---|---|---|---|
| mini-swe-agent | 2.4.6 / 2026-07-23 | >=3.10 | MIT | Yes: `DefaultAgent(LitellmModel(...), LocalEnvironment()).run(task)`; "Just some 100 lines of python"; bash-only, no tool-calling interface, linear history; docker/podman by swapping `subprocess.run` for `docker exec`; claims >74% SWE-bench Verified ([GitHub](https://github.com/SWE-agent/mini-swe-agent); [PyPI](https://pypi.org/project/mini-swe-agent/)) |
| openhands-sdk | 1.57.0 / 2026-10-21 | **>=3.12** | MIT ([repo](https://github.com/OpenHands/software-agent-sdk)) | Yes on 3.12+: `LLM` → `Agent(tools=[TerminalTool, FileEditorTool, TaskTrackerTool])` → `Conversation(...).send_message(); run()`. Packages: sdk, tools, workspace, agent-server ([PyPI](https://pypi.org/project/openhands-sdk/)) |
| smolagents | 1.26.0 / 2026-05-29 | >=3.10 | Apache-2.0 (**unverified this session**) | General agent framework, not coding-specialised ([PyPI](https://pypi.org/project/smolagents/)) |
| aider-chat | 0.86.2 / 2026-02-12 | >=3.10,**<3.13** | Apache (classifier) | Python API "not officially supported" ([aider scripting](https://aider.chat/docs/scripting.html)) |
| SWE-agent | PyPI `sweagent` is 0.0.1 (2024) | — | MIT | Installed from source; superseded in practice by mini-swe-agent from the same team ([PyPI](https://pypi.org/project/sweagent/)) |
| pydantic-ai | 2.59.0 / 2026-10-21 | >=3.11 | MIT | Agent framework that could host a custom coding toolset ([PyPI](https://pypi.org/project/pydantic-ai/)) |

### Inferences
- **OpenCode: (d) bundled sidecar.** The integration would look like this:
  - Ship the pinned compiled binary (v2.1.2), with its checksum, inside JARVIS's install. Start it per task inside the rootless-podman worker container with `OPENCODE_DISABLE_AUTOUPDATE=1`.
  - Pass `OPENCODE_CONFIG_CONTENT`, generated by JARVIS: provider = kernel/router `baseURL`, MCP = JARVIS MCP server, explicit permissions with shell/edit set to "ask".
  - Drive it from a new JARVIS plugin (e.g., `plugin/plugins/coding/`) using the Python ACP client. ACP `request_permission` maps directly onto the kernel GREEN/YELLOW/RED and PIN flow.
  - Alternatively use `opencode serve` on loopback with a random `OPENCODE_SERVER_PASSWORD`, and generate a Python client from `/doc` (e.g., `openapi-python-client`).
  - Audit every ACP `session/update` into the hash chain.
- **Python-native coding lane: vendor mini-swe-agent (b).** It is small, MIT and 3.10+ compatible, so it can be vendored into `plugin/plugins/coding/mini_swe/` and modified:
  - replace LitellmModel with JARVIS's provider layer (avoid LiteLLM given the Oct 2026 compromise noted in the prior report);
  - wrap its single bash action in the kernel tier check;
  - run it inside the podman worker.
  - This gives a fully in-tree coding agent that JARVIS owns. OpenCode stays an optional heavier sidecar for large refactors.
- **OpenHands SDK is the strongest Python SDK but is ruled out in-process by its >=3.12 requirement.** If wanted, run its `agent-server` as a (d) child in its own venv. Aider's `<3.13` cap and unsupported API make it a poor library choice.

### Gaps
- I did not read OpenCode's `packages/protocol/openapi.json` endpoint-by-endpoint, or confirm which V2 endpoints are marked stable versus experimental beyond the audit file.
- I did not run `openapi-python-client` against the spec, so whether a generated client works is untested.
- I did not verify smolagents' license, or mini-swe-agent's exact LOC and current confirmation/approval hooks.

## Q3. goose: Rust, license, recipes, extensions, embeddability, redundancy

### Takeaway
goose is a Rust (Apache-2.0) agent now governed by the **Agentic AI Foundation (AAIF) at the Linux Foundation**. Its repo moved to `aaif-goose/goose`. Workspace version is 1.56.0, the latest stable tag is v1.55.0, and v2 RC tags exist. Its only Python surface is **`goose-sdk` 0.1.0a12 (alpha)**, UniFFI bindings that expose **providers and completions, not the agent loop**. The agent is reachable only as a process (CLI, `goose serve`/ACP).

Once JARVIS has Hermes (general agent) plus a Python coding lane plus optionally OpenCode, goose is **redundant as a runtime**. Port only its **recipe concept** (c): parameterised YAML task templates with sub-recipes.

### Cited Findings
- `LICENSE` is Apache License 2.0. `Cargo.toml` has workspace `version = "1.56.0"`, `rust-version = "1.94.1"`, `authors = ["AAIF <ai-oss-tools@block.xyz>"]`, `repository = "https://github.com/aaif-goose/goose"` — [Cargo.toml](https://github.com/aaif-goose/goose/blob/main/Cargo.toml)
- README:
  - "goose is part of the Agentic AI Foundation (AAIF) at the Linux Foundation";
  - "A native desktop app … A full CLI … An API to embed it anywhere. Built in Rust";
  - "15+ providers"; "70+ extensions via MCP";
  - Claude/ChatGPT/Gemini subscriptions usable via ACP providers.
  - Source: [README](https://github.com/aaif-goose/goose)
- Tags: latest semver `v1.55.0`, plus `v2-rc.1` and `v2.0.0-rc-04-27-0` — [tags](https://github.com/block/goose/tags)
- Crates: `goose`, `goose-agent`, `goose-cli`, `goose-mcp`, `goose-providers`, `goose-acp-macros`, `goose-sdk`, `goose-local-inference`, `goose-roaming` and others. The UI includes `goose-acp` and `goose-acp-client` — [crates/](https://github.com/aaif-goose/goose/tree/main/crates)
- goose-sdk: "With `--features uniffi` the crate compiles to native bindings for Python and Kotlin … callers construct providers, stream provider completions, perform non-streaming completion". "The PyPI package is published as `goose-sdk` and imports as `goose`" — [crates/goose-sdk/README.md](https://github.com/aaif-goose/goose/blob/main/crates/goose-sdk/README.md). PyPI `goose-sdk` 0.1.0a12 (2026-10-13), Apache-2.0 — [PyPI](https://pypi.org/project/goose-sdk/)
- Recipes docs: `recipe-reference.md`, `subrecipes.md`, `session-recipes.md` — [documentation/docs/guides/recipes](https://github.com/aaif-goose/goose/tree/main/documentation/docs/guides/recipes)
- CLI facts from the prior note: `goose run --recipe`, `--output-format stream-json`, `--with-extension` — [goose docs: running tasks](https://goose-docs.ai/docs/guides/running-tasks)

### Inferences
- Embedding goose's agent in Python would mean writing new Rust FFI for `goose-agent`. That is not worth it.
- Keeping it as a (d) sidecar adds a third agent runtime (Rust toolchain, its own config and provider stack) with no capability JARVIS lacks.
- **Recommendation: drop the goose runtime.** Port two ideas instead:
  1. **Recipes** as a JARVIS plugin artifact: YAML with `title`, `instructions`, typed `parameters`, `extensions`/tool scopes and `sub_recipes`. JARVIS runs these through main_agent and DBOS workflows, and each recipe is promoted through the skill gate.
  2. **The `ObservabilityHook` event model** (request start, response start, end with usage), applied to JARVIS's provider layer.
- If the owner wants a second independent coding agent for cross-checking, OpenCode (sidecar) plus mini-swe-agent (in-tree) already gives two.

### Gaps
- I did not read the recipe schema in full or the goose v2 RC changes.
- I did not confirm whether `goose acp` is the current command name for ACP.

## Q4. openmuse: which features to port, and the Python libraries for each; removing CopilotKit Intelligence

### Takeaway
openmuse is small, about 12k lines of TS across `apps/`. MIT allows porting with attribution. Its most valuable code is self-contained:
- a lease-based durable task worker;
- page watches with exponential backoff;
- a 282-line PDF fill using pdf-lib (text and checkbox only);
- an 860-line Google integration;
- a 100-line finance CSV analyser.

All of these map cleanly onto **Python libraries JARVIS already pins or can add on 3.11**:
- DBOS 3.3.0 for durable tasks, approvals via `recv`/`send`, schedules and durable sleep;
- pypdf 6.x or PyPDFForm for forms;
- google-api-python-client with google-auth-oauthlib;
- Playwright persistent contexts with noVNC for profiles and takeover.

**Recommendation: (c) port all of it natively and run no openmuse process.** The CopilotKit Intelligence dependency then disappears entirely, because it is confined to the chat/agent layer (8 server files) that JARVIS replaces with main_agent.

### Cited Findings
**openmuse facts**
- MIT. `package.json` version 0.1.0. Node >=22. Last commit 6 Oct 2026 ("perf(server): scope refreshIdeas status queries (#109)") — [openmuse](https://github.com/CopilotKit/openmuse)
- Dependencies: `@copilotkit/runtime` 1.70.1, `@ag-ui/*` 0.0.59, `@tanstack/ai*`, `@e2b/desktop` 2.4.0, `@electric-sql/pglite`, `@modelcontextprotocol/sdk ^1.30.0`, `pdf-lib ^1.17.1`, `pg`, `hono`, `parse5`, `zod`. Worker: `playwright 1.62.1` — [package.json](https://github.com/CopilotKit/openmuse/blob/main/package.json)
- Code layout (`wc -l`):
  - `engine/service.ts` 1,199, `engine/worker.ts` 257, `engine/page-diff.ts` 85, `engine/finance.ts` 100, `db.ts` 180;
  - `packages/integrations/src/google.ts` 860, `pdf.ts` 282, `vault.ts` 56;
  - about 12,046 TS lines under `apps/`.
  - Source: [apps/server/src/engine](https://github.com/CopilotKit/openmuse/tree/main/apps/server/src/engine)
- **Durable task worker:**
  - Statuses: `queued`, `scheduled` (with `nextRunAt`), `running` (with `leaseId`/`leaseUntil`) and `waiting_approval` (linked to an action `awaiting_review` with `expiresAt`, which expires to `expired`).
  - The default lease is 60 s, renewed every leaseMs/3. A `LostLeaseError` aborts the run.
  - Controls are `pause|resume|cancel|retry`. Retry refuses if a reviewed action's "outcome may be uncertain".
  - Storage is one `records(owner, kind, id, data jsonb, updated_at)` table with compare-and-swap.
  - Source: [engine/worker.ts](https://github.com/CopilotKit/openmuse/blob/main/apps/server/src/engine/worker.ts); [engine/service.ts](https://github.com/CopilotKit/openmuse/blob/main/apps/server/src/engine/service.ts); [db.ts](https://github.com/CopilotKit/openmuse/blob/main/apps/server/src/db.ts)
- **Page-watch backoff:** on failure, `nextCheckAt = now + min(60, 2**failures) minutes`. After 5 failures the watch is paused ("Watch paused after repeated failures"). Alerts are keyed per failure streak. On success the next check is `intervalMinutes` later — [engine/service.ts ~L825–850, L1121](https://github.com/CopilotKit/openmuse/blob/main/apps/server/src/engine/service.ts). "Show what changed" diff is in [engine/page-diff.ts](https://github.com/CopilotKit/openmuse/blob/main/apps/server/src/engine/page-diff.ts). Open bug #184: a "contains" watch with a trailing space never matches — [issues](https://github.com/CopilotKit/openmuse/issues)
- **PDF:** `inspectPdf` lists fields as text, checkbox or unsupported. `fillPdf` uses `field.setText` / `check()` / `uncheck()` and fails on wrong types — [packages/integrations/src/pdf.ts](https://github.com/CopilotKit/openmuse/blob/main/packages/integrations/src/pdf.ts). FEATURES.md lists "OCR/scanned forms and additional PDF field types" as remaining — [docs/FEATURES.md](https://github.com/CopilotKit/openmuse/blob/main/docs/FEATURES.md)
- **Finance:** "CSV amounts use positive expenses and negative income. No currency conversion is inferred". 500 KB cap, BOM handling, strict quoted-field parser — [engine/finance.ts](https://github.com/CopilotKit/openmuse/blob/main/apps/server/src/engine/finance.ts)
- **Feature inventory (FEATURES.md):**

| Feature | Current state |
|---|---|
| Goals/Tracking | "recurring watches, observations, retry/backoff, pause and cancellation" |
| Browser | "Persistent Chromium, public page reads, snapshots, console takeover, PDF downloads" |
| Gmail/Calendar | "complete threads; saved drafts; calendar/event CRUD with reviewed versions" |
| PDF job | "Durable import, typed input request, filled-copy preview, reviewed reply, receipt" |
| Notifications | in-app inbox only, with "APNs/FCM/device push" remaining |
| OpenBot | "Disabled adapter" |

  Source: [docs/FEATURES.md](https://github.com/CopilotKit/openmuse/blob/main/docs/FEATURES.md)
- **CopilotKit coupling:** `@copilotkit` is imported in 8 server files: `agent.ts`, `app.ts`, `engine/model.ts`, `engine/conversation.ts`, `engine/tanstack-agent.ts`, `jev/tools.ts`, `computer-tools.ts` and `demo/model.ts`. It is absent from `engine/worker.ts`, `service.ts`, `finance.ts`, `page-diff.ts` and the integrations package (my grep at HEAD).
- The Intelligence key is mandatory and chat threads are stored in CopilotKit cloud. "not included in this repository's MIT license" (issue #73/#179 open) — [docs/RICH-THREADS.md](https://github.com/CopilotKit/openmuse/blob/main/docs/RICH-THREADS.md). Telemetry is on by default — [docs/TELEMETRY.md](https://github.com/CopilotKit/openmuse/blob/main/docs/TELEMETRY.md) (both via prior note)

**Python libraries** (PyPI JSON, 2026-10-22)

| openmuse feature | Python implementation | Version / license | Notes |
|---|---|---|---|
| Durable tasks, approvals, receipts, pause/resume/cancel/retry | **DBOS** | 3.3.0 (2026-10-15), MIT; **already pinned** in JARVIS `jarvis` group for `plugin/plugins/desktop/missions.py` | `DBOS.recv(topic, timeout_seconds)` / `DBOS.send(...)` for approval waits. `set_event`/`get_event` for status. `DBOS.sleep` is durable. `SetWorkflowTimeout`. `cancel_workflow`/`resume_workflow`/`fork_workflow(start_step)`. `register_queue(global_concurrency, worker_concurrency, limiter)`. `create_schedule(cron)` with pause/resume/backfill. `Debouncer` ([DBOS Python reference](https://docs.dbos.dev/python/reference/contexts); [PyPI](https://pypi.org/project/dbos/)) |
| Page tracking + diff + backoff | httpx/Playwright fetch + stdlib `difflib`; schedules via DBOS | — | Optional: changedetection.io 0.60.9, **Apache-2.0**, Python >=3.11, full REST API, uses Apprise. App-shaped (pip-installable server), not a library ([GitHub](https://github.com/dgtlmoon/changedetection.io); [PyPI](https://pypi.org/project/changedetection.io/)) |
| PDF form inspect/fill | **pypdf** 6.21.2 (BSD-3; JARVIS already requires `>=6.19,<7`) or **PyPDFForm** 6.0.1 (MIT, py>=3.11) | — | Avoid **PyMuPDF 2.0: "Dual Licensed - GNU AFFERO GPL 3.0 or Artifex commercial"**. pdfplumber 0.11.10 (MIT, already pinned) for reading ([PyPI pypdf](https://pypi.org/project/pypdf/); [PyPDFForm](https://pypi.org/project/PyPDFForm/); [PyMuPDF](https://pypi.org/project/PyMuPDF/)) |
| Gmail & Calendar with reviewed writes | google-api-python-client 2.201.0 + google-auth-oauthlib 1.5.0 | Apache-2.0 | Drafts first; send/modify is a YELLOW/RED kernel action ([PyPI](https://pypi.org/project/google-api-python-client/)) |
| Finance CSV | stdlib `csv` + `decimal` | — | Port the 100-line rules verbatim |
| Ideas with evidence | data model port onto vault/DBOS | — | openmuse issues #34/#35 (evidence provenance/freshness) show what the model is missing |
| Browser profiles + takeover console | Playwright Python 1.64.0 (Apache-2.0) `launch_persistent_context` per profile; noVNC/x11vnc or KasmVNC for takeover | — | JARVIS already depends on `playwright>=1.58.0` |
| Sandboxed Linux "computer" | rootless podman container (no network, time and output caps, mirroring openmuse's 30 s / 128 KB defaults) | — | [docs/COMPUTER.md](https://github.com/CopilotKit/openmuse/blob/main/docs/COMPUTER.md) |

### Inferences
- **Port, don't run.** The pieces JARVIS lacks are all in about 3k lines of CopilotKit-free TS. A Python port onto DBOS is shorter, because DBOS supplies leases, recovery, timeouts, queues and schedules that openmuse hand-rolls. A Python port also needs no Node 24, pnpm, PGlite or the 1.5–2.5 GB RAM footprint.
- Map openmuse's statuses onto DBOS:
  - `waiting_approval` becomes a workflow step that calls `DBOS.recv("approval", timeout)`. The kernel's PIN approval calls `DBOS.send(wf_id, decision, "approval")`.
  - A "receipt" is a step result written to the hash-chained audit.
  - "uncertain outcome" retry protection becomes an idempotency key per reviewed action. JARVIS already has `jarvis/kernel/jarvis_kernel/idempotency.py`.
- The CopilotKit Intelligence dependency is removed by construction: chat goes through N.E.K.O/main_agent and threads live in JARVIS memory. No telemetry, and no Parallel search unless the router lane allows it.
- License hygiene: keep openmuse's MIT notice in a `THIRD_PARTY_NOTICES` entry for ported files, plus a provenance header naming the upstream path and commit `1ac68f39`.

### Gaps
- I did not read `google.ts` in detail (scopes, draft versus send flow) or the Ideas/JEV module.
- I did not benchmark DBOS on SQLite versus Postgres for JARVIS's load. DBOS's SQLite support should be confirmed against the version pinned.
- PyPDFForm's handling of XFA and radio fields was not verified.

## Q5. OpenClaw / Grok Bot features to port natively, and the Python libraries

### Takeaway
None of OpenClaw (Node, sandbox-off defaults) or Grok Bot (hosted, closed) should be run. Their feature ideas port onto a small set of Python libraries. Most of the plumbing already exists inside the vendored Hermes runtime, which has 27 platform adapters including Telegram, Signal, email, Matrix, ntfy and a2a.

Split the work as follows:
- **Inbound channels** stay in the supervised Hermes gateway, with JARVIS's `pre_tool_call` policy and pairing allowlist.
- **Outbound notifications and approvals** go through ntfy or Apprise called directly from JARVIS.
- **Heartbeat, automations and morning brief** become DBOS schedules in JARVIS. Not APScheduler: 4.0 is still alpha.
- **Chief-of-staff** is main_agent plus persistent specialist profiles over DBOS queues.
- **Teach-by-demonstration** is a JARVIS-owned Playwright-trace recorder that emits a SKILL.md through the skill gate. workflow-use is AGPL-3.0 and "very early development".

### Cited Findings
- Hermes's bundled platform plugins (directory at HEAD): a2a, bluebubbles, buzz, dingtalk, discord, email, feishu, google_chat, irc, line, matrix, mattermost, ntfy, photon, qqbot, raft, signal, simplex, slack, sms, teams, telegram, wecom, weixin, whatsapp, whatsapp_cloud, yuanbao — [plugins/platforms](https://github.com/NousResearch/hermes-agent/tree/main/plugins/platforms). DM pairing is in `gateway/pairing.py` — [architecture.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/architecture.md)
- python-telegram-bot 22.8 (2026-06-12), **LGPL-3.0-only**, py>=3.10. Hermes pins the same version — [PyPI](https://pypi.org/project/python-telegram-bot/). aiogram 3.31.0 is an MIT alternative, py>=3.10,<3.15 — [PyPI](https://pypi.org/project/aiogram/)
- Apprise 2.0.1 (2026-10-03), BSD-2-Clause — [PyPI](https://pypi.org/project/apprise/). changedetection.io uses Apprise for notifications — [changedetection.io](https://github.com/dgtlmoon/changedetection.io)
- ntfy: "dual licensed under the Apache License 2.0 and the GPLv2", self-hostable, open-source Android (Play/F-Droid) and iOS apps — [binwiederhier/ntfy](https://github.com/binwiederhier/ntfy). Its publish docs include an "Action buttons" section — [docs.ntfy.sh/publish](https://docs.ntfy.sh/publish/) (I saw the section heading but did not read its content). The PyPI `ntfy-client` 0.0.6 is AGPL-3.0, so avoid it and use plain httpx POSTs — [PyPI](https://pypi.org/project/ntfy-client/)
- APScheduler: stable 3.11.3 (2026-06-28, MIT). The 4.x line is still `4.0.0a6` (alpha) — [PyPI](https://pypi.org/project/APScheduler/). DBOS `create_schedule(schedule_name, workflow_fn, schedule=<cron>, cron_timezone, automatic_backfill, queue_name)` with `pause_schedule`/`resume_schedule`/`trigger_schedule` — [DBOS reference](https://docs.dbos.dev/python/reference/contexts)
- OpenClaw "Automations": heartbeat retired into ordinary jobs, with a default "Proactive check" every 30 min — [docs: heartbeat](https://docs.openclaw.ai/gateway/heartbeat) (via prior note). Hermes heartbeat = in-session idle re-entry, cron = fresh session plus delivery — [heartbeat.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/heartbeat.md)
- Grok Bot: "watch a workflow once, save the steps as a routine, accept corrections"; chief-of-staff Bot over specialists; "only come back when something needs your approval" — [x.ai news](https://x.ai/news/introducing-grok-bot) (via prior note)
- workflow-use (browser-use): record once and replay deterministically, with Browser Use fallback. Output is `.workflow.json`. Recorder is an `extension/` folder. **AGPL-3.0**, "very early development … don't recommend using this in production"; self-healing is roadmap only — [browser-use/workflow-use](https://github.com/browser-use/workflow-use). PyPI `workflow-use` 0.2.11, py>=3.11 — [PyPI](https://pypi.org/project/workflow-use/)
- browser-use 0.13.11 (2026-10-07), MIT, py>=3.11 — [PyPI](https://pypi.org/project/browser-use/). Playwright Python 1.64.0, Apache-2.0 — [PyPI](https://pypi.org/project/playwright/)
- Hermes kanban, a durable multi-agent board with review states and per-worker OS processes, is a reference for a chief-of-staff and specialists design — [kanban.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/kanban.md)

### Inferences
**Port map** (all JARVIS 3.11 plugins unless noted)

| Feature | JARVIS form | Libraries |
|---|---|---|
| Channels gateway (Telegram first) | (d) Hermes gateway child with allowlist pairing, a JARVIS `pre_tool_call` policy, a tool-less reader for inbound text. Or, for a pure-JARVIS path, (a) python-telegram-bot 22.8 / aiogram in a `channels` plugin | PTB is LGPL: fine for unmodified library use. aiogram (MIT) if vendoring/modifying is wanted |
| Push + actionable approvals | (a) direct ntfy HTTP publish with action buttons that hit a kernel approval URL bound to the request hash. Apprise for fan-out to email/Matrix/etc. | ntfy self-hosted on the Tailscale VM; Apprise 2.0.1 |
| Heartbeat / automations / webhooks | (a) DBOS `create_schedule` + workflows; a FastAPI webhook route behind the kernel (JARVIS already uses FastAPI) | DBOS 3.3.0 (already pinned); do not add APScheduler |
| Morning brief | DBOS cron workflow at a fixed time: research + calendar + watches + inbox digest → vault note + Telegram/ntfy | google-api-python-client, existing research plugin |
| Chief-of-staff coordinator | Extend main_agent: persistent specialist "profiles" (role card, tool scope, memory namespace), with tasks on DBOS queues (`worker_concurrency` caps) and review states copied from Hermes kanban | DBOS queues |
| Teach-by-demonstration recorder | JARVIS-owned recorder: Playwright tracing/codegen in the agent browser profile (and a desktop event log on Windows) → LLM distils into SKILL.md + parameter schema → skill gate (sandbox replay, owner promotion) | Playwright 1.64; do **not** vendor workflow-use (AGPL, alpha). Read it only as a design reference |

### Gaps
- The content of ntfy action buttons (http action semantics, auth) was not read this session.
- I did not verify whether Hermes's `a2a` platform plugin implements Google/LF A2A or something else.
- No Python library was found that implements Grok-Bot-style "watch once, replay with corrections" at production quality. This is a custom build.

## Q6. Effort, maintenance trade-offs, and keeping upstream improvements

### Takeaway
Use three different upstream-sync regimes:
1. **Pristine vendored subtree plus out-of-tree plugins for Hermes.** Cheap to update: re-pull the tag, run the compat checker and tests.
2. **Pinned binary plus generated config for OpenCode.** Trivial to update and audit.
3. **Port-with-provenance for openmuse, goose recipes, mini-swe-agent and Hermes sub-features.** Every ported file records its upstream path and commit, and a scheduled JARVIS job diffs upstream since that commit for owner review.

The expensive and fragile choice, editing inside a vendored Hermes tree, should be avoided.

### Cited Findings
- Hermes release cadence: v0.22.3 was "a roll-up of about 620 PRs / 2,911 commits / 4,206 files", so its notes are deferred. JARVIS's own study judged "we cannot review a 4,206-file roll-up" — [HERMES_0_22_UPGRADE](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/HERMES_0_22_UPGRADE_2026-10-22.md)
- Hermes plugin contracts used by JARVIS were stable across 0.21.6 → 0.22.4:
  - memory provider base class "Same four required methods";
  - plugin loader "Same logic";
  - but the "Plugin compatibility import layer removed (#126164)".
  - Source: [same doc](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/HERMES_0_22_UPGRADE_2026-10-22.md)
- JARVIS already has `jarvis/third_party/PATCHES.md` (patch plan for neko-tauri, "offer upstream as PRs (no fork)"), `jarvis/hermes/check_hermes_compat.py` and a reuse-first rule ("Justify any custom code against an existing project in docs/01_RESEARCH_AUDIT.md") — [jarvis/CLAUDE.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/CLAUDE.md) (via prior repo note)
- Hermes's exact-pin supply-chain policy — [pyproject.toml](https://github.com/NousResearch/hermes-agent/blob/main/pyproject.toml). OpenCode ships compiled executables — [build.ts](https://github.com/anomalyco/opencode/blob/dev/packages/cli/script/build.ts)
- License obligations:
  - MIT (Hermes, OpenCode, openmuse, mini-swe-agent, DBOS) requires keeping the copyright and permission notice.
  - Apache-2.0 (goose, changedetection.io, Playwright, Google clients) also requires a NOTICE and marking of modified files.
  - AGPL (PyMuPDF, workflow-use, ntfy-client) should be excluded from an in-tree build unless the owner accepts AGPL obligations.
  - Sources: LICENSE files and PyPI entries cited above.

### Inferences
**Decision matrix** (effort figures are my rough estimates, not sourced)

| Runtime | Form | What JARVIS takes | Initial effort | Ongoing maintenance |
|---|---|---|---|---|
| Hermes Agent | **(b-lite + d)**: pristine `git subtree` at a tag, own 3.14 venv, JARVIS-supervised; **plus (c)** ports | Runtime: gateway (channels), API server, cron, computer_use (cua-driver), delegation/kanban for VM jobs. Ports: skill format + staging, curator lifecycle, FTS5 search, dangerous-command patterns, pairing | Medium: 1–2 weeks for the policy/audit plugin, config generator, supervisor and build step. Ports: 1–2 weeks | Low–medium. `git subtree pull --squash` to a new tag, then `check_hermes_compat.py` and JARVIS's plugin tests. Never patch the subtree, and upstream any fixes |
| OpenCode | **(d)** pinned binary sidecar driven over ACP/HTTP from a `coding` plugin | Agent sessions, permission requests (answered by the kernel), worktrees/PTY endpoints | Low–medium: about 1 week for the ACP client plugin and config generation | Low: bump the binary pin, re-run the ACP contract test. V2 API audit marks endpoint stability |
| Python coding lane | **(b)** vendor mini-swe-agent | Agent loop, environment abstraction (podman) | Low: a few days | Low: small code, diff upstream by commit |
| goose | **drop runtime; (c)** port recipe format and observability-hook idea | Recipe YAML schema, sub-recipes | Low: days | None (concept port) |
| openmuse | **(c)** full Python port onto DBOS / pypdf / Google clients / Playwright | Task engine semantics, watches+backoff+diff, PDF fill, Gmail/Calendar reviewed writes, finance CSV, ideas-with-evidence, browser profiles + takeover | Medium–high: 3–5 weeks for all features. Watches + PDF + finance alone ~1 week | Low: upstream is an alpha; review its diffs monthly by provenance commit |
| OpenClaw / Grok Bot ideas | **(c)** native features | Automations, morning brief, approval push, chief-of-staff, recorder | Medium: recorder is the largest unknown | Low |

- **Mechanics for "keep upstream improvements":**
  - **Subtree.** `git subtree add/pull --prefix=jarvis/third_party/hermes-agent https://github.com/NousResearch/hermes-agent <tag> --squash`. One commit per bump, with the tag and commit recorded in `PATCHES.md`. CI fails if any diff touches the prefix outside a bump commit.
  - **Provenance headers on ported files.** Each carries `# Ported from <repo>@<commit>:<path> (MIT)`. A monthly DBOS job runs `git diff <commit>..<new-tag> -- <paths>` on the upstream clones and files an owner review note in the vault inbox. That is "porting diffs" as a reviewed routine, not an automatic merge.
  - **Pins.** Exact pins plus hashes for every new Python dependency, matching Hermes's own policy. Use a minimum release age for bumps, given the LiteLLM and Shai-Hulud incidents.
- **Python-version strategy:** keep JARVIS on 3.11. Run the only components that need newer Python (Hermes 3.14; OpenHands SDK 3.12+ if ever wanted) as supervised children from in-tree sources. Revisit only if N.E.K.O upstream moves to 3.14.

### Gaps
- No upstream statement was found on API stability guarantees for Hermes's plugin hook surface across 0.23. JARVIS's compat checker is the only guard.
- The effort estimates are judgement calls, not measured.
- I did not check whether `git subtree` of a ~1M-LOC Python repo (plus its website and tests) bloats the jarvis-hq clone unacceptably. A sparse subtree, or excluding `website/` and `tests/` via a filtered mirror, may be needed. Untested.
