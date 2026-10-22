# Hermes Agent: from v0.21.6 to the 0.22 line, what breaks, what we can use (2026-10-22)

Read-only research. Nothing was installed or changed anywhere; the Oracle VM was not touched. Sources: the GitHub release notes of
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/releases) (v0.21.7, v0.22.0 to v0.22.4) and the source code of
v0.21.6 and v0.22.4 cloned to a scratch folder and compared. We run Hermes on the VM as user `jarvis-agent` in a sandbox: gateway (API on
127.0.0.1:8642) and dashboard (Tailscale address only), a custom model provider that points at the kernel's endpoint, only the memory and
todo tools, and our own memory provider plugin `jarvis/hermes/plugins/jarvis_memory`. Telegram is off.

## Which version

| Tag | Date | What it is |
|---|---|---|
| v0.21.7 | 12 Oct | roll-up of 204 PRs; updates follow tagged releases instead of `main` |
| **v0.22.0** "The Threshold Release" | 14 Oct | the big curated release (notes cover everything since 0.21.0) |
| v0.22.1 | 14 Oct | **built from the wrong commit** (shipped unreleased gateway work): skip it |
| **v0.22.2** | 15 Oct | the release 0.22.1 was meant to be; curated notes; fixes the gateway refusing to start under systemd after a reboot |
| v0.22.3 | 21 Oct | roll-up of about 620 PRs / 2,911 commits / 4,206 files; **its notes are deliberately not written until v0.23.0** |
| v0.22.4 | 21 Oct | 14 commits on top: small desktop and plugin fixes |

**Recommendation:** stage **v0.22.2** (the last version whose changes are documented). Take v0.22.3/0.22.4 only after v0.23.0's curated notes
exist, or if a specific fix in them is needed; we cannot review a 4,206-file roll-up. Never v0.22.1. (You said "v0.22": the newest tag is 0.22.4.)

## Checked against the real code (v0.21.6 vs v0.22.4)

| What we depend on | Result |
|---|---|
| Memory provider base class (`agent/memory_provider.py`) | Same four required methods (`name`, `is_available`, `initialize`, `get_tool_schemas`); only type hints and a longer docstring changed |
| Our plugin loads against the real base class of both versions | Yes: subclass OK, `register(ctx)` hands a new instance each time, no module-level mutable state (the new "multiplexing contract" requires exactly that) |
| Plugin discovery/loader (`plugins/memory/__init__.py`) | Same logic (plus a parse fallback so a long docstring cannot hide a provider) |
| Toolset policy: `memory` and `todo` toolsets, `hermes-api-server`, default-off list, platform tool resolution | Identical contents and logic. `todo_list` was already the tool name in 0.21.6. Only `onboarding` was added; `setup`, `start_chat`, `spotify` left |
| Dashboard command line (`hermes dashboard --no-open --host --port`) | Same options |
| Python | `>=3.11,<3.15` in both: our 3.14 environment stays valid |
| Config file format | No new top-level sections found; same migration steps |

## The "unable" parts: things that change or could stop working

1. **Plugin compatibility import layer removed** (#126164). Our plugin does not use it (imports only `agent.memory_provider`, with a fallback),
   but any helper or MCP script that imports Hermes internals must be re-checked: `/opt/jarvis-mcp/jarvis_mcp.py` is a standalone MCP server and should be fine.
2. **Sessions auto-prune is ON by default in 0.22.0/0.22.2** (ended sessions idle 90 days are deleted at start). 0.22.3 turns it back to opt-in.
   It fits our privacy aim, but decide on purpose: set `sessions.auto_prune` explicitly either way.
3. **Reasoning is replayed by default** on providers that can read it, across resume and compaction. Our kernel endpoint forwards to Groq (8,000 tokens/minute on the
   free tier; one Hermes turn is already 13-17k characters) and OpenRouter. Replayed reasoning makes requests bigger, and the proxy's reasoning-field handling
   (`llm_proxy.py` drops fields and never lets reasoning stand in for content) must be checked with a real multi-turn session. **Not measured yet: needs a staged run.**
4. **Memory providers moved to catalog plugins** (Honcho, Mem0 ...): not ours; ByteRover and RetainDB stay bundled. Auto-migration only touches those.
5. **Removed:** `hermes sync` (Skill Sync), the bundled tirith scanner (its checks are now built in). Our `skills/skillgate.py` must not call either; grep before upgrading.
6. **Windows desktop is MSIX-only** (Windows 11 22H2+; NSIS/MSI gone). We use the CLI/dashboard on Linux, so this does not touch us; the WSL lab is gone anyway.
7. **One gateway per machine, shared by all profiles**: `hermes update` folds per-profile gateways in. We have a single profile; check that `jarvis-hermes-gateway.service` still starts the same process after the update.
8. **Updates now follow tagged releases** by default for source installs (`hermes update --set-channel main` for every commit). We pin to a tag/commit, which stays valid ("pinned installs check out only the pin").
9. **Tool names:** `todo`→`todo_list`, `cronjob`→`cronjob_manage`, `process`→`process_manage` (old names still work). We allow whole toolsets, not tool names, so no change.
10. **Known 0.22.2 issue:** sessions created under the 0.22.1 gateway cannot be deleted. We never ran 0.22.1.

## New things we must keep OFF (outbound or powerful)

- **Connectors (Nous tool gateway):** on first use Hermes creates a free connectors-only identity and talks to Nous-hosted apps. Our `connections` toolset stays off and the sandbox firewall only allows Smokescreen and the kernel, so it cannot reach them: verify `manage_connections` is not offered.
- **Shared metrics** (`telemetry.shared_metrics`): default off, asks once per profile; answer "No thanks" and check the setting.
- **Bot Screen, computer use, cron, kanban, delegation, local-model installer, password-manager browser login, Relay / Hermes Cloud:** all stay off; none is in our two allowed toolsets.
- **Nous free tier / onboarding (`nous/welcome`):** desktop first-run only; irrelevant to the headless gateway.

## Usable parts worth adopting (ranked for JARVIS)

1. **Leaner prompts**: about 2.1k fewer tokens per request and a roughly halved tool schema (rarely used tools load via `tool_search`): directly eases the Groq 8k tokens/minute limit that made our fourth turn hit HTTP 429.
2. **Compaction on real token counts, and bad summaries cannot wipe context**; a stall watchdog (`agent.turn_liveness.timeout_s`, 600 s) frees stuck turns; `state.db` lock errors name the holder.
3. **`config.yaml` survives a bad write** (falls back to the last good copy) and `hermes config get` masks secrets; credentials, `state.db` and snapshots are owner-only at rest; child processes no longer inherit adapter secrets.
4. **Gateway fix**: starts after a reboot again under systemd (v0.22.2). Our unit uses `Restart=on-failure`, which is exactly the setup that could refuse to start.
5. **`post_compaction` plugin hook** (0.22.3): our memory provider could save the task checkpoint right after compaction, next to the existing `on_pre_compress`.
6. **`mcp-serve` (Hermes as an MCP server)** and **MCP progress notifications**: a possible bridge between Hermes memory/skills and our kernel connector framework (kernel-gated, as the plan already says).
7. **`hermes-pii` plugin** (catalog): compare with our privacy gateway before trusting either alone; **`plugins.isolation: host`** runs third-party plugins in a separate process with only that profile's secrets (not needed while we run only our own plugin).
8. **Subagent steering and `/stop` reaching background children**: useful if delegation is ever enabled in the lab.

## Staged upgrade plan (the owner runs it; the VM is not touched by me)

1. **Back up**: `hermes backup` and copy `/home/jarvis-agent/.hermes` (config.yaml, state.db, memory) to a dated folder; note the current venv path `/home/jarvis-agent/venv314`.
2. **Install the candidate beside the old one**, not over it: a new venv with uv at the tag (`v0.22.2`), checking the commit hash against the release page. Do not use the installer one-liner on the live user.
3. **Run our own tests** against it: `jarvis/hermes/tests/test_jarvis_memory_plugin.py`, and the loader check (`python jarvis/hermes/check_hermes_compat.py PATH_TO_CHECKOUT`: it loads Hermes's real base class and our plugin, and checks the contract).
4. **Start it on a spare port with a copy of the config** (`HERMES_HOME` copy, API port 8643), not on 8642. Verify, measured like the 0.21.6 install was: the model is offered only `memory`, `tool_search`, `tool_describe`, `tool_call`; `tool_search` finds no shell/file/web tool; `manage_connections` is absent; no direct internet (only Smokescreen and the kernel).
5. **Send a 6-turn session** through the kernel endpoint with both versions and compare request sizes (reasoning replay) and Groq 429s; set `sessions.auto_prune` on purpose.
6. **Switch**: change `ExecStart` in `jarvis-hermes-gateway.service` and `jarvis-hermes-serve.service` to the new venv, restart those two units only (never while a lab job runs), watch `hermes status`.
7. **Rollback** = point `ExecStart` back at `venv314`; the backup restores config and state.
8. After a successful run, update `jarvis/tools/registry.json` (pinned_version, commit), `jarvis/docs/01_RESEARCH_AUDIT.md` and `02_ARCHITECTURE.md`.

## Not verified here

Reasoning-replay size effect (needs a live multi-turn run), whether our `/opt/jarvis-mcp` script imports any Hermes module, anything inside v0.22.3's undocumented 4,206 files,
and behaviour on Python 3.14 (the real Hermes needs 3.14; the test image has 3.11, so only interface and policy code was compared).
