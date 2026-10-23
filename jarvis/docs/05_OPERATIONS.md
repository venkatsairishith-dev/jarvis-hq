# 05 Operations: Oracle, cost, storage, secrets, hand-over

> **N.E.K.O notice (2026-10-23):** N.E.K.O, its widget and the "Hey Neko" wake listener were removed from the repository. Sections below that still mention them (Live checks, Robustness, "Hey Neko" and PC control, and some others) are dated records of that era; the current way to run JARVIS is the section "Running JARVIS: the face, the tool server and the local services".

> **Stale-section notice (2026-10-22):** sections written on 2026-10-07 to 2026-10-09 (Oracle state, storage, router and provider notes) predate the 2026-10-18 remediation and the 2026-10-22 P0 checks. Re-verify against `D:\jarvis\outputs\P0b_9ROUTER_VM_2026-10-22.md` before relying on them: for example, the VM is 2 CPU / ~11 GiB, OmniRoute 3.8.51 serves port 20128, and no 9router is installed on hermes-agent-vm.

## Oracle
- Region ap-hyderabad-1, one compartment (name in keys.json), Pay As You Go account used only inside Always Free.
- Free allowance (Oracle docs and community thread, 2026-10-07): 3,000 OCPU-hours and 18,000 GB-hours of Ampere A1 per month on a paid tenancy (4 OCPU / 24 GB), 200 GB block plus boot storage in total, 5 volume backups, 2 VCNs on Always Free tenancies, 10 TB outbound per month. Not confirmed in the console for this tenancy.
- Current state (2026-10-07): hermes-agent-vm (2 OCPU, 12 GB, 100 GB boot, Ubuntu 24.04 aarch64, public IP in the existing public subnet, new SSH key) is the main server. **hermes-agent-vm-2 is protected: never touch it, its boot volume, its network or its IPs.** Network hermes-vcn is shared, so it is never deleted. Storage is 200 of 200 GB.
- Budget `jarvis-1usd` with alert rule `spend-reached-1usd` at $1 actual spend. A budget alert warns; it does not cap spend.
- Use only Always Free-eligible resources. No paid shapes, GPUs or paid services. If something needs a paid resource, stop and tell you.
- Out-of-capacity: report it, never retry in a loop.
- Idle reclaim: A1 instances under 20% CPU, network and memory for 7 days may be reclaimed. Keep real work on the server.

### Oracle delete checklist (reusable)
1. List instances, boot volumes, block volumes, networks, reserved IPs, backups with names and sizes.
2. Offer a backup and wait for the answer.
3. Get a yes naming the exact resource.
4. Delete one resource at a time; refuse unless exactly one exact-name match.
5. Re-list and show the result. Never touch anything outside the named compartment.

### Cost check (reusable)
After each phase: Cost Analysis for the month, per service, must be $0. Billing data lags a day. Record the result in the daily note.

## Storage (Windows)
D: is NTFS, 75 GB, for every local AI file. C: has about 53 GB free and stays clean. Before and after every big download, record free space. Redirect before downloading: `OLLAMA_MODELS`, `HF_HOME`, pip cache, npm cache, TEMP. Installers and logs that land on C: are noted and trimmed. The OCI CLI lives in D:\jarvis\tools\oci-venv (0.8 GB).

## Secrets
- Windows: D:\jarvis-secrets, locked to the user (keys.json, oci\, ssh\). macOS: ~/.jarvis/secrets.env, mode 600.
- I use them through tools (OCI CLI config, ssh) and never open them to read values. Never print, log, commit or put them in a model context.
- If a secret ever shows in output or a transcript: rotate it, then tell you.

## Windows version
Windows 10 Pro 22H2 build 19045 under Boot Camp. Mainstream support ended 14 Oct 2025. Consumer ESU runs to 12 Oct 2027 if enrolled with a Microsoft administrator account (free with settings sync; otherwise $30 or 1,000 Rewards points, which is not free so do not pay). Source: Microsoft's ESU page.

## Hand-over
- End of each phase and before a reboot to macOS: vault/daily/<date>.md (done, checked, open, next).
- D:\jarvis\CLAUDE.md stays short: the Part 1 rules plus links to the memory map and the newest daily note.
- Reusable procedures live as project skills in D:\jarvis\.claude\skills, with a copy in jarvis-hq (`jarvis/skills/`): cost-check, phase-gate, vault-write, oracle-delete.
- Repos (updated 2026-10-17):
  - github.com/venkatsairishith-dev/jarvis-hq (private), branch `main`, now holds the N.E.K.O-based tree (N.E.K.O at the root, JARVIS under `jarvis/`). This was a fast-forward on 2026-10-17 from 789b485a (the old JARVIS main) to 112a9981.
  - GitHub Actions is off on jarvis-hq on purpose: N.E.K.O ships 19 workflows, and `health-check.yml` runs every 5 minutes.
  - Pushing is done only when the owner asks, with GitHub's device login. The Linux server copy (`~/hq-neko`) keeps its origin push URL disabled.
  - jarvis-vault (memory) is unchanged.
  - Local: D:\jarvis\hq-neko (branch `main`), D:\jarvis\hq (branch `jarvis-hq-legacy`, unused but kept) and D:\jarvis\vault.

## Lab operations (added 2026-10-10)
**Take control** (Windows): open the JARVIS Hub, go to Agent PC, press **Take control**, and work. The agents are frozen where they were, and windows they opened stay usable. Press **Continue task** to hand back; the agents carry on with what is left and look at the screen first. Activity shows each task's goal, time and last step.
- The VNC password is asked once per hub session.
- If the buttons say "Control service not answering", run in WSL as root: `systemctl status jarvis-lab-control`.

**Lab models (Dahl).** Lab tasks only, public lane, never private data.
- The key lives in D:\jarvis-secrets\providers\dahl.key (copied from the owner's dahl-credentials.txt without printing). The server's copy is ~/.jarvis/secrets/providers/dahl.key (600).
- HTTP 402 means the key's allocation is used up. Allocate more from the free pool on the Dahl account page; **never top up** (that is a purchase).
- HTTP 429 means the model is busy: the endpoint falls back to the next Dahl model.
- Check the models with GET https://inference.dahl.global/v1/models (key needed). Health: /v1/status (no key; also shown on the hub).
- After a key change, copy it to the server the same way and restart jarvis-llm-proxy.

**Tests (laptop):**
- Demo suite in WSL as agent: `cd ~/lab && python3 demo.py --timeout 900`. 6 tasks x 3 agents; the "desktop" task is checked against the laptop's own confirmations.
- On Windows, after the suite (they pause or switch the lab's agents):
  - `python hq\lab\tests\takeover_live.py`
  - `python hq\lab\tests\switch_live.py`
  - `python hq\lab\tests\approval_live.py` (needs an approval; it was written for Telegram /approve, which is OFF since D13: approve through the kernel console or owner plane on the server instead; this live script was not re-run after Telegram was switched off: NOT RUN)
- Unit tests: `python -m unittest discover -s hq/lab/tests` and the kernel suite.

**Kaggle and Colab model servers are parked** (rules on tunnels). If ever restarted on purpose, push with a hard session limit (`kaggle kernels push -p DIR -t <seconds>`; the CLI cannot cancel a run). Stop it in the Kaggle UI when done.

**Deploying the kernel** (Windows → server):
1. Pack `hq/kernel` and copy it to the server.
2. Unpack it and move the live `~/jarvis/kernel` to `kernel.old.<ts>`.
3. Run the tests **in place**. In the unpack folder they fail, because there is no `config/` beside them.
4. If the tests fail, the new code goes to `~/downloads/kernel.failed.<ts>` and the old one comes back. The failing tests are printed from `~/downloads/deploy-tests.log`.
5. Restart only `jarvis-llm-proxy`, `jarvis-kernel` and `jarvis-telegram`.

Never restart while a lab job runs: a runner report sent during the restart is lost. Check the active jobs first.

**Lab runner:** install `hq/lab/lab_runner.py` to `/opt/jarvis-hub/lab_runner.py` (root 644, with a `.bak-<ts>` copy). Then run `systemctl restart jarvis-lab-runner` in WSL as root.

**monit** restarts `jarvis-telegram` within about 2 minutes if it stops. Its HTTP interface is off, so `monit unmonitor` does not work. To test with a second bridge, don't stop the service; let it handle everything after the first turn.

**Owner task control:** Telegram is off (D13). Use Mission Control to watch tasks; pause/resume/cancel through the kernel on the server. Each task's files land in `D:\jarvis\outputs\task-<id>\`.

**If a token is ever printed:**
1. Run `python3 -m jarvis_kernel agent-token <name> --lane <lane> --out <tmpfile>`.
2. Swap the new token into its users. For `hermes`, that is the Hermes `config.yaml` `model.api_key` and `/home/jarvis-agent/.jarvis-llm-token`.
3. Restart the services that read it.
4. Confirm that the old token gets 401.

**WSL:** .wslconfig keeps the lab running when idle (instanceIdleTimeout=-1, vmIdleTimeout=-1, effective after the next `wsl --shutdown`). The logon task JarvisLab starts it.

## Corrections after independent review (2026-10-07)
- The 4 OCPU / 24 GB, 10 TB egress and VCN-count figures are from Oracle's docs page and a community thread. They are NOT confirmed in the console for this tenancy [verify]. A Pay As You Go account bills anything over the free allowance. The $1 budget alert is a tripwire, not a cap.
- Protected resources: D:\jarvis-secrets\protected.json lists the OCIDs of hermes-agent-vm-2, its boot volume and its VNIC, and the shared subnet. Any Oracle script must load it and refuse to write to those. Names are matched exactly, never by prefix.
- Delete checklist: also verify the OCID is not in protected.json.
- ESU: free only if enrollment shows no price. Source: Microsoft's ESU page (the Q&A thread said 13 Oct 2026; the Microsoft page says 12 Oct 2027) [verify in the Windows settings screen].
- Secrets: files only, no environment variables. The Windows D: partition is visible from macOS under Boot Camp, so D:\jarvis-secrets is user-locked on Windows and is not copied to macOS.

## Skills, traces and evals (added 2026-10-10)
**Skills** (Windows; Python with pyyaml: `D:\jarvis\tools\skillgate-venv\Scripts\python`):
- `skillgate.py scan | stage <category/name> | guard <name> | activate <name> [--approval-id ID] | deactivate <name> | status`
- Medium-risk skills print an approval id. Telegram is off (D13), so approve it at the kernel console on the server (`python3 -m jarvis_kernel approve <id>`), then rerun with `--approval-id`.
- The kernel token is in `D:\jarvis-secrets\kernel\skill-operator.token`.
- Active now: `adversarial-ux-test`. Staged, waiting for approval: `code-wiki`, `grill-me`.

**Traces** (server): `cd ~/jarvis/kernel && python3 -m jarvis_kernel trace <task> [--public]`, or `trace-analyze --days 7`.

**Evals** (Windows): `python hq\evals\run_evals.py --baseline hq\evals\baseline.json`. Add `--kernel <dir>` to test another checkout. Run it before every kernel deploy.

**Measured provider limits:** if a provider changes plan or model, measure again and update `max_input_tokens` / `max_total_tokens` in `providers.json`.

## Context, memory and integrity (added 2026-10-11)
**Rollback:**
- Kernel: `kernel.old.<ts>` on the server.
- Hermes config: `~/.hermes/config.yaml.bak-memory-<ts>` and `.bak-ctx-<ts>` (as jarvis-agent).
- Plugin: remove `~/.hermes/plugins/jarvis_memory` and set `memory.provider: ""`.
- Lab runner: `/opt/jarvis-hub/lab_runner.py.bak-<ts>`.
- Restart only the services concerned: `jarvis-llm-proxy`, `jarvis-kernel`, `jarvis-hermes-gateway`, `jarvis-hermes-serve`; in WSL, `jarvis-lab-runner`.

**Tests outside the owner's data:**
- `hq/evals/ctx_recovery.py`: real Hermes plus fake providers. Runs in the WSL lab as root with a throwaway `HERMES_HOME`. The server firewall rightly blocks the Hermes user from other local ports.
- `hq/evals/memory_lifecycle.py`: real gateway. Runs in the disposable `jtest` profile with its own `memtest` token; the owner's memory is never touched.

**Quarantine:** files a lab task planted or changed go to `/root/quarantine/<time>-<task>` in WSL, where root can review them.

## JARVIS Console (added 2026-10-11)
**Run (Windows):** `D:\jarvis\tools\jarvis-venv\Scripts\python.exe -m jarvis_console` with `PYTHONPATH=D:\jarvis\hq\console`, then open http://127.0.0.1:8799/ . Build the UI once with `npm install && npm run build` in `hq/ui` (Node 24). Voice needs `D:\jarvis\models\voice` and `D:\jarvis\tools\piper-venv`.

**One-time setup on the server (kernel console, `ssh -t`):**
1. Issue the owner token: `python3 -m jarvis_kernel agent-token console --lane owner --out /tmp/owner.token`, copy it to `D:\jarvis-secrets\kernel\owner.token` with `scp` (never print it), then delete the server copy.
2. Set the approval PIN: `python3 -m jarvis_kernel set-approval-pin` (typed at the terminal; 6 to 64 characters). Until it is set the UI cannot approve anything.
3. Restart only `jarvis-llm-proxy` (check no lab job is running first).

**Rotate the owner token:** repeat step 1 (it replaces the row) and restart nothing else; the old token then gets 401.

**Tests:**
- Kernel: `cd hq\kernel; python -m unittest discover -s tests` (about 11 minutes; use the venv Python so the time-zone tests run).
- Console: `cd hq\console; python -m unittest discover -s tests` with `PYTHONPATH=hq\console;hq\kernel`.
- UI: `cd hq\ui; npm run build; npx playwright test` (starts a throwaway kernel and console from `console/tests/e2e_server.py`; uses installed Edge; needs the voice models for the voice project).
- Intent evaluation: `python bench\run_intent_eval.py --embed=URL --qwen=URL|model --verdict=URL --laya=URL` (servers optional; results in `bench/data/intent_results.json`).

**Rollback:** the kernel deploy keeps `~/jarvis/kernel.old.<ts>`; swap it back and restart `jarvis-llm-proxy` and `jarvis-kernel`. The new tables are additive; an older kernel ignores them.

## Everyday use, added 2026-10-11 (hardening pass)
**Restarting the console (Windows):** `powershell -ExecutionPolicy Bypass -File D:\jarvis\hq\scripts\start-console.ps1` after stopping the `pythonw ... -m jarvis_console` pair. A second start now fails to bind and exits instead of leaving a duplicate (each holds about 1.3 GB of speech models). Never run the script from a call that waits on its output pipe: the console inherits it.
**Restarting the laptop node:** `Stop-ScheduledTask JarvisNode; Start-ScheduledTask JarvisNode`. The task also restarts itself up to 5 times, 1 minute apart. The node asks for jobs every 3 s and reports every 15 s; a job it held when it died is failed at its next idle report ("the node restarted or lost this job"), not after the 30 minute sweep.
**The owner's agent says it is busy or rate limited:** a command is refused immediately, with the time to try again, when every model provider of the owner's lane is resting after a free-tier limit. Check the Models screen (measured tokens, cooling providers). "Stop waiting" hands the gate back and starts a fresh agent conversation.
**Voice check (owner, once per language):** Settings → Voice check. Speak the listed phrases in English, Hindi and Telugu; the screen scores each against the phrase (word and character error rate) and saves the session under `D:\jarvis\cache\voice-checks\`. Until that is done Hindi and Telugu recognition is measured on synthetic speech only. The wake word is off; `bench/wakeword/` reproduces the measurements.
**The first approval:** run `python3 -m jarvis_kernel set-approval-pin` at the kernel console (SSH to the server), then restart nothing. Until then the Command Center can show, deny and cancel, but not approve.
**Live checks (against the real system, not part of the default suite):** `cd hq\ui; npx playwright test -c playwright.live.config.ts <spec>`. Specs: `filetrigger` (disposable `D:\jarvis\cache\watch-test` folder only), `voice-task`, `memory`, `reliability` (restarts the console and the node; sends 5 short commands), `selfcorrect` (one lab task), `approval` (part B needs the PIN in `JARVIS_APPROVAL_PIN` for that one run).
**Running the Windows test suites:** use the jarvis-venv Python (`D:\jarvis\tools\jarvis-venv\Scripts\python.exe`) and keep `TEMP` and `TMP` outside `D:\jarvis` (the default `C:` temp folder, or `D:\jarvis-apps\tmp`). With the temp folder inside the watched project tree, `test_vault` fails about one run in four with WinError 32 while `tempfile` removes its folder (measured 6 of 25; 0 of 25 outside). The 24 `test_browser` tests skip in that venv; run them with `D:\jarvis\tools\browser-venv\Scripts\python.exe -m unittest discover -s tests -p test_browser.py`.

## Connectors (added 2026-10-11)
**Open:** Connectors screen, Alt+0, or `#/connectors`. **Add one:** Add connector, template or form, create the secret file it names under `D:\jarvis-secrets\connectors\`, Test connection, classify and switch on the tools you want. Details and states: `docs/research/09-connectors.md`.
**The local allowlist** is `config/connectors.local.json` (git-ignored; written only by the console). Delete it to make every local connector "not configured" at once.
**Install a server** into its own folder, never into the kernel's or console's Python: `D:\jarvis\tools\mcp-venv` holds `mcp-server-time`; GitHub's binary goes in `D:\jarvis\tools\github-mcp-server\`.
**Agents** get `connector_list`, `connector_call` and `connector_result` from the tool file `/opt/jarvis-mcp/jarvis_mcp.py` on the server; a changed copy is picked up at the next Hermes start.
**Stuck or noisy connector:** Disable it (read back from the kernel), Disconnect, or Remove. The kill switch refuses every connector call.
**Tests:** `kernel/tests/test_connectors.py` (32+ cases with a fake MCP server in `kernel/tests/fixtures/`), `kernel/tests/test_mcphttp.py`, `console/tests/test_connectors_api.py`, `ui/e2e/connectors.spec.ts`.

## Running JARVIS: the face, the tool server and the local services (rewritten 2026-10-23)
**Where things are.** The repository is JARVIS only (the N.E.K.O application was removed, see D33). On the laptop the checkout is `D:\jarvis\hq`; data is `D:\jarvis\data` (`JARVIS_DATA_DIR`); the last N.E.K.O tree is the branch `backup/neko-final-2026-10-23`.
**Start and stop (Windows):** `powershell -File D:\jarvis\hq\jarvis\scripts\jarvis-start.ps1` and `jarvis-stop.ps1`. The start script brings up, each only if it is not already listening: the privacy gateway (48950), the second-opinion gateway (48954), the page-watch runner, llama-swap (8080), the local speech server (48930), the JARVIS console (8799), the face's tool server (48970) and then the face window. `-NoFace` starts the services only.
**First setup of the Python environment (agent-venv):** `uv venv D:\jarvis\tools\agent-venv --python 3.11`, then `uv pip install --python D:\jarvis\tools\agent-venv\Scripts\python.exe -r jarvis\tools\requirements\agent-venv.txt` (with `UV_PYTHON_INSTALL_DIR`, `UV_CACHE_DIR` on D:). Without uv (the laptop has none): `python -m venv D:\\jarvis\\tools\\agent-venv`, then `agent-venv\\Scripts\\python.exe -m pip install -r jarvis\\tools\\requirements\\agent-venv.txt`. The speech server keeps `jarvis-venv`, the privacy gateway `privacy-venv`.
**Move the data folder once:** `python jarvis\scripts\migrate_data_dir.py` (dry run) and `--apply` copy `D:\jarvis\N.E.K.O-data\neko` to `D:\jarvis\data\plugins`, `muse`, `main_agent_runs` and `logs` as they are, and the outputs folders `outputs\neko\*` to `outputs\*`. It never deletes the old folder.
**Scheduled tasks:** `install-autostart.ps1` (task `JarvisStack`, runs jarvis-start.ps1 at login), `install-watchdog.ps1` (`JarvisWatchdog`, every 10 minutes), `install-daily-report.ps1` (`JarvisDailyReport`). Remove the old `JarvisNekoStack`, `JarvisNekoWatchdog` and `JarvisNekoDailyReport` with their `-Remove` switches from the old checkout first.
**Laptop migration, 2026-10-23 (done by the laptop session):** backup in `D:\\jarvis\\backup\\neko-migration-2026-10-23` (git bundle of the old checkout, data folders, scheduled-task XML); data copied to `D:\\jarvis\\data` (133 files, 56 already there, nothing deleted); new checkout `D:\\jarvis\\hq`, the old `hq-neko` kept for rollback; `agent-venv` built with venv and pip; `JarvisStack`, `JarvisWatchdog`, `JarvisDailyReport` installed and the three `JarvisNeko*` tasks disabled. `JarvisNode` and `JarvisMorningBrief` still point at the old checkout until the owner moves them; the `JarvisWidget` task has no purpose any more. `live_check.py --no-model` passed 52 of 53 there; the one failure was `JARVIS_AGENT_MODEL` / `JARVIS_AGENT_KEY_FILE` not being set (the owner's choice). On Windows the `workbench` suite has 4 failures (two are the symlink privilege), `deliverables` 1 (needs playwright) and `_shared` 2 (the 32,767-character environment limit); not yet analysed.
**The face** (`jarvis/face`, README there): build `pnpm install && pnpm run build:win` (Node 22, pnpm 11; the Live2D core and sample avatars are downloaded at build time and checked against `downloads.lock.json`), install, and start. Settings > Providers > OpenAI Compatible: the privacy gateway is already the default chat endpoint; paste the provider key the gateway forwards (it stays in the face's own settings). Speech needs no key. Tools: the face connects to `http://127.0.0.1:48970/mcp` on its own (a new config starts with it). Memories the model suggests wait for your approval in Settings > Memory.
**What each piece may do.** The face decides nothing: chat goes through the privacy gateway, speech to the local speech server, every action to the tool server, which hands it to the console and the kernel. Approvals are given in the JARVIS console (http://127.0.0.1:8799/) with your PIN, never in the face. `--plugins none|a,b` on `python -m jarvis.bridge.face_tools` narrows which plugins' entries the face can call (default: all but `jarvis_bridge`).
**The planner's model:** `JARVIS_AGENT_BASE_URL` (default the privacy gateway), `JARVIS_AGENT_MODEL`, `JARVIS_AGENT_KEY_FILE` (a file, never the key). `JARVIS_MEMORY_RECALL_URL` names an optional loopback service that answers `POST /query_memory/<character>`; without it plans are made without memory.
**Tests:** each plugin suite separately (`python -m pytest jarvis/plugins/<plugin>/tests`: several ship a `test_smoke.py`), `jarvis/pluginapi`, `jarvis/bridge`, the console, kernel and node suites as before, `jarvis/tools/test_face_guard.py` for the face's protections, and the face's own `pnpm -F @proj-airi/stage-tamagotchi exec vitest run`.
**Models and the key:** Dahl (`https://inference.dahl.global/v1`, `MiniMaxAI/MiniMax-M2.7`) stays the public-lane provider behind the privacy gateway, with the caller's own key. Nothing from JARVIS's private lane is sent to it; `jarvis_bridge` returns only state unless `JARVIS_BRIDGE_SPEAK_REPLY=1`.
**Voice:** the face hears and speaks through the local speech server (`jarvis\scripts\local_speech_server.py`: Parakeet and IndicConformer int8 for speech to text, Kokoro and Piper for text to speech, all on this PC). In the face set speech-to-text to the OpenAI-compatible provider at `http://127.0.0.1:48930/v1`; the defaults are already these. Not measured on the laptop yet (pilot).
**Records from the N.E.K.O era** (the verification run of 2026-10-14, the model slots in N.E.K.O's config, its voice set-up) were removed from this page with the code they described; they remain in `reports/` and in git history.
## Cyrene-Agent's feature list, mapped (added 2026-10-14)
Source of the list: Playa-Cyrene/Cyrene-Agent (MIT, Electron/TypeScript). What was reused is the design, not code: it is a different stack and a character-specific app. N.E.K.O already covers most of the list natively; the plugins below fill the real gaps. The character (Cyrene, from Honkai: Star Rail) and its Live2D model are not part of this and were not copied.
| Cyrene feature | Where it lives here | State |
|---|---|---|
| Daily Chat (character, memory, no tools) | N.E.K.O's own chat and persona; Main Agent `mode=chat` exposes no tool | native |
| Work (search, browser, files, documents, lifestyle) | Main Agent `mode=work` over web_search, Browser Use, lifekit, music, `doc_tools`, JARVIS | built |
| Code (bound project, language-server queries, restricted exec, approvals) | Main Agent `mode=code`: JARVIS lab agents only (OpenCode has language-server queries); the kernel approves every risky action; projects are the ones JARVIS already allows | built (via JARVIS) |
| Learn (Obsidian vault, notes, exercises, progress) | `learn_vault` plugin plus `mode=learn` (vault and documents only) | built |
| Personalized memory (layered, worldbook, lifecycle) | N.E.K.O's five-layer memory with BM25 and optional embeddings; JARVIS memory | native |
| Voice (TTS, ASR, calls) | local speech server (Kokoro, Piper) and N.E.K.O's faster-whisper | built, see the voice section |
| Tool ecosystem (search, files, documents, music, MCP) | bundled plugins, `doc_tools`, `mcp_adapter`, JARVIS connectors | built |
| Multi-vendor model compatibility | N.E.K.O's provider profiles and custom API slots (Dahl in use) | native |
| Plugin system with SDK | N.E.K.O's plugin SDK and CLI | native |
| Multi-platform (Feishu, WeChat iLink, QQ) | `wechat_integration` exists in N.E.K.O (off); Feishu and QQ are not built | **not built** |
| Proactive chat | N.E.K.O proactive chat and `proactive_controller` | native |
**Modes** (`run_task` and `plan_task` take `mode`): a mode only narrows the live catalogue, never adds to it, so a plan can still name nothing that is not running and allowed. chat: think only. work: everything running except the study vault. code: think and JARVIS. learn: think, `learn_vault`, `doc_tools`.
**`doc_tools`:** `read_document` (PDF, Word, Markdown, text, CSV, JSON, HTML) only from Documents, Downloads, Desktop, `D:\jarvis\outputs` and the study vault (override `DOC_TOOLS_READ_ROOTS`); refuses credential-looking names, `.ssh`, AppData, system folders and any `jarvis-secrets` folder; 25 MB limit. `write_document` (Markdown, text, Word) only to `D:\jarvis\outputs\neko\` (override `DOC_TOOLS_OUT_DIR`), never overwriting.
**`learn_vault`:** the vault is `D:\jarvis\learn-vault` (override `LEARN_VAULT_DIR`), separate from JARVIS's memory vault whose write rules (inbox, daily, decisions) are unchanged. Paths stay inside the vault, only `.md`, no hidden folders, a note is never overwritten (create fails if present, append adds), progress is kept in `_progress/` which the note tools cannot write to.
**Passing results between steps:** a plan may write `{{s1}}` in a content-type argument (content, text, body, message, note, summary) of a step that depends on `s1`; the data fills the value and can never add or redirect a step.
**Planner hardening from live use (Dahl):** reasoning text is stripped before parsing; a refused plan gets one corrective retry that lists the real helpers; entry timeouts are 300 s because the model takes 10 to 60 s per call.
**Not built, on purpose:** Feishu and QQ channels (external accounts and outward messaging, owner decision), the DMAE worldbook, the music window and desktop pet shell. Planner quality is model-dependent: a plan can be valid and still be thinner than asked (the validator checks safety, not wisdom).
## Live checks (added 2026-10-14; rewritten 2026-10-23)
**Services and plugins:** `D:\jarvis\tools\agent-venv\Scripts\python.exe jarvis\scripts\live_check.py` (flags `--no-asr`, `--no-model`). It checks that the speech server, the privacy gateway and the console answer, that the face's tool server answers over MCP and offers the JARVIS tools and the plugins' entries, that every plugin loads and runs (the plugins are run in the checking process with the plugin host, against the real services), that the agent model goes through the privacy gateway, that the key stays in a file, documents and the study vault (writes, reads, refuses secrets and outside folders), text to speech and speech to text in English, Hindi and Telugu, the local team, the deliverables, research and the planner (those two call a model; `--no-model` skips them), the watchdog's last report, and the JARVIS side (kernel reachable and authorized, kill switch off, audit chain, node online, the bridge entries). `preflight.py` wraps it with the scheduled tasks and the disk check; `daily_report.py` writes the one-page report. These checks need the running services: on a machine without them most of them fail by design.
## Robustness, modes, approvals view, autostart (added 2026-10-14)
**What the main agent now does when a step goes wrong** (ideas taken from Cyrene-Agent, written for this plan-then-run design). A step ends in one of four outcomes: done, failed, **unknown** (the call may or may not have happened, for example a timeout or a crash mid-call) or skipped. A step that writes (anything that is not a read: send, save, write, create, delete, move, run) is never retried and a step of unknown outcome is **held**: the run stops there, says what it did not retry, and does not go on to steps that depend on it. A read-only step is retried once. Nothing is run twice without the owner's say.
**Checkpoint and resume:** every finished step is saved to `D:\jarvis\N.E.K.O-data\main_agent_runs`. If the app stops mid-run, the run shows as *interrupted* on the next start. `resume_run` (button in the main-agent panel, or ask her to resume) continues from the saved steps; a held step is only run again if its id is passed in `rerun`, which the owner confirms.
**Two clocks:** time spent waiting for the owner (a JARVIS approval pending) is counted separately and added to the step's deadline, so a slow approval does not make a step time out; the panel shows "waited" next to "worked".
**Modes:** chat, work, code and learn narrow which helpers the planner may use (the catalogue is closed in every mode); learn adds a note-saving step after thinking.
**Approvals and connectors inside N.E.K.O's UI:** the JARVIS panel in the plugin manager shows what is waiting for approval (what, level, time left, reason, never the parameters) and the connected services (name, status, lanes, tool count, never endpoints or references). It is read-only: approving needs the PIN and stays in the JARVIS console, and the bridge has no approve call.
**Autostart:** `jarvis\scripts\install-autostart.ps1` creates one per-user scheduled task `JarvisNekoStack` that runs `neko-start.ps1` 30 seconds after login (no administrator rights; `-Status` shows it, `-Remove` deletes only that task). The `JarvisNode` task is untouched. It was installed on 2026-10-14.
**Voice, what was found:** typed questions with spoken answers work end to end (text input mode, local speech out). The microphone path does **not** work with Dahl: N.E.K.O's audio input mode always opens a realtime session, and Dahl offers no realtime model (the session is refused with `API_KEY_REJECTED`). Speaking to her by microphone needs a realtime-capable provider; this is a provider limit, not a defect in the stack. Local speech recognition is faster-whisper `base`, which is weak on Hindi and Telugu (English is fine). Speech out in Hindi and Telugu is chosen by the script of the text.
**Dahl's MiniMax-M2.7 reasoning:** it puts its reasoning in the reply text inside `<think>` tags on every reply. `always_inlines_thinking` (config/providers.py) makes N.E.K.O's streaming path withhold it before the screen and the speech. Caveat found live: turns recorded *before* the fix kept their reasoning text in her recent history, and the model imitates what is in its history, writing the reasoning as plain untagged text. The stored test turns for the YUI persona are backed up in `D:\jarvis-apps\tmp\yui-memory-before-clean`; removing those entries (recent.json, time_indexed.db) is an owner decision that is still open.**Cutover applied 2026-10-14 (20:00):** `cutover-to-neko-base.ps1 -Apply` moved JarvisNode (working directory), JarvisLab (script) and the console to `D:\jarvis\hq-neko\jarvis`; the kernel was not touched. Its own checks passed (node online, kernel reachable and authorized, one console listener) and `live_check.py` then passed 41 of 41. Task backup: `D:\jarvis-apps\backups\tasks-pre-neko-20261014-200013`; rollback: `rollback-from-neko-base.ps1 -BackupDir <that folder>`. `D:\jarvis\hq` (branch jarvis-hq-legacy) is now unused but kept.
**Reasoning leak, final cause:** with tools in the request Dahl keeps `<think>` but drops `</think>`, leaving three or more newlines before the answer. The stream stripper's `open_tag_only` mode and `strip_thinking_segments` end the reasoning at that gap. After cleaning the stored test turns, a typed question gave a clean reply with 4.4 s of spoken audio.
**Same usage as actual N.E.K.O (checked 2026-10-14):** the tree is upstream 3ffcf7b9 with only five upstream files changed (`config/providers.py`, `main_logic/omni_offline_client/_streaming.py`, `utils/llm_client/thinking.py`, `utils/config_manager/core_config.py`, one test exclusion) plus added plugins, two helper modules and `jarvis/`. Upstream's own entry point is `launcher.py` (it starts and watches the memory, main and agent servers, reuses ones already running, and records a good boot); `neko-start.ps1` now runs exactly that instead of starting the three modules by hand, then adds only what is ours: the local speech server, the four plugins and the JARVIS console. `neko-stop.ps1` ends the launcher and its whole process tree. Same ports, same UI at http://127.0.0.1:48911/, same plugin manager; the launcher keeps the data root on `D:\jarvis\N.E.K.O-data` and writes only small state files under `%LOCALAPPDATA%\N.E.K.O`. Its Playwright browser check finds the installed Chromium (revision 1243) and downloads nothing.
**Memory vectors (added 2026-10-14):** the optional embedding model was missing, so memory search ran on keywords only. It was fetched with upstream's own scripts\prepare_embedding_model.py (public Hugging Face profile, int8 variant only, about 265 MB) into D:\jarvis\N.E.K.O-data\embedding_models\local-text-retrieval-v1, which the runtime prefers over a bundled copy. After a restart the memory server logs EmbeddingService: ready (int8, 256d). Re-run the same script to update it; deleting that folder returns to keyword-only search. The fp32 variant was not downloaded (this CPU has AVX2 and AVX-VNNI).
**Light int8 speech models (added 2026-10-14):** measured on this laptop (i9-9980HK, 8 cores, no usable GPU). Whisper int8 (what N.E.K.O used): `base` 0.13x real time but wrong on Hindi (Latin letters) and Telugu (Tamil script); `small` fixes Hindi at 0.45x but still fails Telugu (one run looped for 88 s); `tiny` is worse. JARVIS's own int8 recognizers (Parakeet-TDT 0.6B int8 for English, IndicConformer int8 for Hindi and Telugu, already installed under `D:\jarvis\models\voice`) run at 0.04x to 0.1x real time warm and transcribed all three test sentences correctly. N.E.K.O now uses them: `local_speech_server.py` also serves `POST /v1/audio/transcriptions` (OpenAI form, multipart or raw WAV, 12 MB limit, loopback only), and `neko-start.ps1` sets `NEKO_WHISPER_ENDPOINT` to it, so the existing recognizer worker (`main_logic/asr_client/workers/faster_whisper.py`, one small opt-in class, `_EndpointModel`) sends each utterance there instead of loading Whisper in-process. The worker keeps its own turn handling, pooling and hallucination filter. Remove the env line to go back to Whisper `base`. Speech out: Kokoro (English) 0.35x real time, Piper (Hindi 0.9x, Telugu 1.2x on a cold request). Upstream files now changed: six (the five listed above plus this worker); the recognizer change is covered by `tests/unit/test_asr_local_endpoint.py`. Dahl's three models were also measured (GLM-5.3-Flash 18.7 tok/s but slower in N.E.K.O's real path, MiniMax-M2.7 11 tok/s, DeepSeek-V4-Flash fast but loops); the conversation stays on MiniMax-M2.7 for now.
**Speech recognition accuracy on a real voice note (2026-10-14):** a 5 min 19 s conversational Telugu WhatsApp voice note (Telangana Telugu with many English words) was scored against a human reference with `jarvis\scripts\asr_score.py` (romanization-tolerant: Telugu script is transliterated, then compared as a phonetic string; absolute numbers are approximate, the ranking is what matters). Cause found: `Voice.transcribe` glued all speech segments into ONE stream, and the CTC/transducer models drop most of a long recording that way. Decoding each utterance on its own and joining the texts moved it from 173 of 661 reference words to 625, word recall 61% to 86%, character error 74% to 29%, 11 s for the 5 min (about 29 times real time). Also changed: VAD silence gap 0.6 s to 0.2 s, longest utterance 15 s, 0.1 s padding per utterance. Measured and not adopted (no gain): high-pass, denoise, loudness normalisation, 0.25 s padding, threshold 0.35 or 0.65. Known trade-off: very short gaps can split a word, so a clean synthetic clip lost one letter group (ఈరోజు heard as ఈ రజు). What is left is mostly English loanword spellings and dialect, which only a better model or a vocabulary list would fix. The regression is covered by `test_each_utterance_is_decoded_as_its_own_stream` (verified to fail on the old code). Long recordings go through the API in pieces of at most 90 s.
**Pitfall found by the full test run (2026-10-15):** running the upstream unit tests with `uv` on PATH makes `uv` create an empty `.venv` inside the repository. Upstream's `launcher.py` re-executes into a project `.venv` whenever one exists, so the next start failed with `No module named 'fastapi'` and the stack stayed down. Run uv-dependent tests with `UV_PROJECT_ENVIRONMENT=D:\jarvis\tools\neko-venv` so it never makes its own; `neko-start.ps1` now also removes an empty project `.venv` (and refuses to start, with a message, if one holds packages). Full upstream unit run on this laptop: 41,876 passed, 17 failed: 14 reproduce on untouched upstream (missing `httpx` in spawned workers, Windows symlink privilege, Node TAP format), 2 are timing flakes under 4-way parallel load that pass 3 of 3 serially, 1 was ours (the async-blocking gate, fixed in `ed28116f`).
**Desktop app (added 2026-10-15):** upstream's Electron desktop client is a separate project and is not in this repository, so the app window is Microsoft Edge in app mode (own window, no tabs or address bar, taskbar icon). `jarvis\scripts\open-desktop-app.ps1` opens it (`-Target neko` for her at http://127.0.0.1:48911/, `-Target jarvis` for the console at http://127.0.0.1:8799/) and starts the servers first if they are not running. Shortcuts: `D:\jarvis\JARVIS (N.E.K.O).lnk` and `D:\jarvis\JARVIS Console.lnk` (copy or pin them to the desktop, taskbar or Start menu). Closing the window leaves the servers running; the login task already starts them.
**Everything English (2026-10-15):** the app UI was already English (`NEKO_LANGUAGE=en`; the language picker keeps native names such as 日本語 on purpose). What still produced Chinese was her own data. (1) The "sharp-tongued junior" personality preset ships in Chinese and its prompt said "may use multiple languages ... including English, 日本語", so replies came out as English with Chinese tails, and the Chinese lines in her chat history were then imitated. `jarvis\scripts\make_persona_english.py` (dry run by default, `--apply` makes a never-overwritten backup under `N.E.K.O-data\backups\english-*`) translated the preset's profile, changed that language line to "Speak English only", translated the Chinese lines already in her recent chat, outbox and history rows, and dropped the Chinese fragments of the anti-repeat index. Stop the servers first. (2) `config\api_providers.json`: 46 provider names and descriptions on the API settings page translated (ids, voice labels and aliases are functional and untouched; the Doubao name keeps the pinned 火山方舟 as a bilingual proper noun). (3) The greeting path leaked her reasoning ("The gap is about 1 hour since...") because it built its own stripper that knew only the Qwen case; it now uses the same MiniMax-aware stripper as chat (`main_logic/proactive_chat/generation.py`, covered by two tests). Verified live: three typed turns, 0 Chinese characters in what she said and in the whole chat window; `live_check.py` has three new checks that keep the persona and memory English (38 of 38 offline, 43 of 43 full before them).
**Limits, on purpose:** upstream's source comments, server log lines and developer pages stay Chinese (translating them would fork us from upstream for good). Two default texts pinned by upstream tests (`状态预览`, `串过门的人`) are replaced by the English translation at load. The key `档案名` ("profile name") is read by the code, so a memory fact labelled with it stays Chinese. The memory server re-derives her persona facts from the character card at every start and dropped the 7 translated preset facts (her personality prompt lives in the card's persona override, which is English and in effect). Re-applying the original preset from the UI would bring the Chinese back; run `make_persona_english.py --apply` again after doing that.
**Follow-up fixes to "everything English" (2026-10-15):** (1) The live prompt is rebuilt from upstream's code every time (`get_persona_prompt_guidance(preset_id)`), so editing the saved prompt text had no effect; the shared English rule in `config/prompts/prompts_chara.py` now says "Speak English only". (2) `utils/persona_presets.py` has English profiles for all four active presets (`_PROFILE_EN`), used when the language is English, so re-applying a preset no longer brings Chinese back. (3) The card-to-memory sync labels the internal key `档案名` as "Profile name" in English (pinned to the card's own locale so the sync stays idempotent). (4) Untagged reasoning: the opening greeting sometimes printed her reasoning with no `<think>` tags, only a blank gap before the real line. The opt-in MiniMax stripper (`open_tag_only`) now also recognises that by its opener ("The user wants me to...", "Looking at the recent...", "The gap is...") plus the gap, and releases any text that only looks like reasoning unchanged if no gap arrives, so a real reply cannot be lost. (5) `make_persona_english.py --apply --drop-leaked-reasoning` removes the reasoning that had already leaked into her history, which she imitates (it uses the stripper's own opener pattern; run it with the N.E.K.O Python, servers stopped). Not changed on purpose: the Step voice labels (they are voice ids), two template default texts that an upstream convention test pins to the Chinese locale (the page shows the English translation), and Chinese source comments and log lines. A busy memory server (settling the previous chat through a slow model API) can make the next chat start time out once; it recovers by itself.
**Error sweep (2026-10-15):** `jarvis\scripts\log_digest.py [--since HH:MM]` groups the warnings and errors in the N.E.K.O logs by pattern. What it found, and what was done: (1) the small helper model calls (emotion enrichment, suggested-reply chips, topic search, history summary) have budgets sized for a model that answers directly (6 to 30 s, 128 to 600 output tokens); Dahl's MiniMax reasons inline at about 11 tokens a second, so they timed out or came back as cut-off JSON (`reply_not_json_object`). Opt-in scales `NEKO_AUX_LLM_TIMEOUT_SCALE` (3) and `NEKO_AUX_LLM_TOKEN_SCALE` (4), set in `neko-start.ps1`, multiply those budgets (default 1 = upstream unchanged; tests in `tests/unit/test_aux_llm_scales.py`). (2) A new chat waits on the memory server's settle lock until the previous chat's summary and fact extraction finish (by design, for consistent reads); upstream gave up after 5 s. `NEKO_MEMORY_FETCH_TIMEOUT_S` (150) now lets it wait. Measured: back-to-back conversations went from mostly failing to 3 of 4 succeeding. **Known limit:** the browser enforces its own 15 s start limit (`static/app/app-buttons.js`), so a chat started within about a minute of closing the previous one can still show a start error; retry after a minute works. Not changed because it is upstream's coupled browser and server timeout. (3) Tried and rejected: moving the emotion, summary, correction and agent slots to GLM-5.3-Flash and DeepSeek-V4-Flash. Dahl rations those two (5 parallel calls: all 429), MiniMax took 5 of 5; the slots stay on MiniMax. (4) Harmless and left alone: "Plugin router is already started" (every plugin start), Steamworks init (no Steam), TTS sentence-boundary notes, and the built-in computer-use connectivity ping timing out (that feature is off; JARVIS does PC control).
**Self-healing watchdog (2026-10-15):** `jarvis\scripts\watchdog.py` runs every 10 minutes as the per-user scheduled task `JarvisNekoWatchdog` (`install-watchdog.ps1`, `-Status`, `-Remove`). It makes no model calls. It checks the six servers, the four plugins and the JARVIS kernel; it starts what is down (servers through `neko-start.ps1`, a stopped plugin through the plugin runtime) and never touches the JARVIS kernel, kill switch or approvals. At most 3 attempts per hour per problem, then it stops and records `gave_up_on` in `<data>\logs\watchdog_status.json` (log: `watchdog.log`). Proven by breaking things on purpose: a killed speech server was back in 22 s and a stopped plugin in 1 s. First version hung because it read the start script's output through a pipe that the servers it launched kept open; it now logs to a file. `--dry-run` shows what it would do.
**Daily report and test runner (2026-10-15):** `jarvis\scripts\daily_report.py` (task `JarvisNekoDailyReport`, 09:00, `install-daily-report.ps1`) writes `<data>\logs\daily_report_YYYYMMDD.md`: the no-model live checks, what the watchdog repaired in 24 hours, the top warnings and errors, and free disk space; it keeps 14 days and makes no model calls. `live_check.py` also checks that the watchdog reported in the last 25 minutes, and its browser step keeps Edge's throw-away profiles on D: (they went to C:). `jarvis\scripts\run-upstream-tests.ps1` runs N.E.K.O's unit tests correctly on this laptop: it points uv at the existing environment (`UV_PROJECT_ENVIRONMENT`, `UV_NO_SYNC`), sets `NODE_OPTIONS=--test-reporter=tap` for Node 24, and uses throw-away data; with that, 12 of the 14 failures that had looked like "environment" pass. Two remain (avatar tests needing the Windows symlink privilege, i.e. Developer Mode) plus one upstream bug (`test_pr3078_review_evidence::test_c1`, a missing method in upstream's own hot-swap code).
**Panel buttons were dead in a real browser (found 2026-10-15 by driving the UI):** the main-agent and JARVIS panels submitted through the UI kit's `ActionForm`, a native `<form>`, and the plugin manager's panel frame is sandboxed without form submission, so "Plan and run" and "Hand over" did nothing (console: "Blocked form submission ... 'allow-forms' permission is not set"). Compile and render checks could not see it. Both panels now use inputs plus a `Button` calling `props.api.call`, the pattern upstream's bundled panels use; the main-agent panel also refreshes itself every 4 s while a task is active. `live_check.py` presses each panel's button with an empty task and requires the "Type the task first" toast. The planner no longer offers the web-browser helper unless the request points at the web (a link, a site, "latest", "news", "price", a form); a small planner model was sending plain questions to the browser, which failed. Result: the real panel runs a task end to end (two of two).
**Full upstream unit run with the correct environment (2026-10-15, run-upstream-tests.ps1, 34 minutes): 41,937 passed, 3 failed, 219 skipped.** The 3 are the known ones: two avatar tests that need the Windows symlink privilege and `test_pr3078_review_evidence::test_c1` (a missing method in upstream's own hot-swap code). An earlier run that lost a worker had been disturbed by plugin reloads and restarts I made while it ran; do not restart or reload the stack during a full run. `live_check.py`: 50 of 50 (full, including the model call).
**Privacy gateway (added 2026-10-15):**
- What it does: N.E.K.O's five model URLs point at `http://127.0.0.1:48950/v1`. Passwords, keys, card and phone numbers, e-mail, Aadhaar and PAN leave the PC as `[PASSWORD_1]`-style placeholders and come back restored.
- Run, stop and watch: `neko-start.ps1` starts it first and `neko-stop.ps1` stops it. The watchdog heals it. Its log is `<data>\logs\privacy.err.log` (requests and counts, never values).
- Check: `POST /redact {"text": ...}` shows what Dahl would receive. `GET /health` gives counts by kind.
- Undo: copy `D:\jarvis-apps\tmp\core_config.before-privacy-gateway-20261015-230616.json` back over `<data>\config\core_config.json` with the servers stopped.
- Tests:
  - `D:\jarvis\tools\privacy-venv\Scripts\python.exe -m unittest jarvis/privacy/test_privacy.py` (13, with a fake upstream).
  - `jarvis\privacy\demo_live.py` makes one live call (one small Dahl request).
- Environment: `D:\jarvis\tools\privacy-venv` (Python 3.11, presidio 2.2.364, spaCy 3.8.16, en_core_web_sm 3.8.0, 255 MB).

**Local models (added 2026-10-15):**
- Serving: `jarvis\scripts\local-models.ps1` (`-Stop`) runs llama-swap v261 on `127.0.0.1:8080` with `jarvis\local_models\llama-swap.yaml`. `neko-start.ps1` starts it, `neko-stop.ps1` stops it, and the watchdog heals it.
- Models: kept in `D:\jarvis\models\llm` (6.5 GB). `jarvis\scripts\fetch_models.py` downloads them with resume and a free-space check, and verifies each against Hugging Face's SHA-256; `--check` only verifies.
- Speeds through llama-server on the Radeon Pro 5500M (Vulkan; measured on that Windows laptop only, macOS Metal and NVIDIA CUDA are UNVERIFIED):
  - Ling-3.0-tiny: 31 tok/s.
  - Qwen2.5-Coder-1.5B: 80 tok/s.
  - Qwen2.5-Math-1.5B: 96 tok/s.
- Must keep `-fa off`: Vulkan flash attention gives garbage on this GPU.
- Ling reasons before answering unless the request sets `chat_template_kwargs: {"enable_thinking": false}`.
- Quick check: `python jarvis\local_models\smoke.py` (local only).
- Plugin `local_team`: tools `ask_local`, `solve_with_team` and `team_status` (see its README).
- Live check: `live_check.py` has 7 new checks:
  - 3 for the privacy gateway.
  - 3 local-team checks with no quota (status, maths 391, code).
  - 1 team run with the redacted adviser, model path only.
- Results: 55 of 55 (`--no-model`); local team 4 of 4, including the adviser run.
- Gotchas:
  - Port 8091 on this PC belongs to another service ("Verdict"); leave it alone.
  - `Start-Process -Wait` waits for every process the script starts, so it hangs on start scripts.
**Jev-style decision models (added 2026-10-16, D20):**
- The owner's OpenJev "Verdict" service (WSL, `http://127.0.0.1:8091/v1/systemone`; set `JARVIS_JEV_URL` and `JARVIS_JEV_MODEL` to change it) is used for three decisions:
  - which local specialist gets a task;
  - whether MiniMax is needed;
  - whether a value the PII model found is a secret.
- JARVIS does not start or stop that service. When it is down, routing uses Ling, MiniMax is asked, and the gateway hides every candidate. `team_status` shows `decision_model`, and the gateway's `/health` shows `pii_model` and `checks` (`jev_unreachable`).
- PII model files:
  - Location: `D:\jarvis\models\pii\gliner-pii-small-v1.0` (revision d21aad5b, int8 ONNX, SHA-256 checked).
  - The base model was measured (D20) and removed.
- Privacy environment: now 3.45 GB, with PyTorch CPU 2.8.0, gliner 0.2.29 and onnxruntime 1.31.
- Quality check: `D:\jarvis\tools\privacy-venv\Scripts\python.exe jarvis\privacy\eval_pii.py`. Local only; exit 0 means 12 of 12 hidden and at most 3 of 12 flagged.
- Tests:
  - Privacy: 22, with no services needed.
  - local_team: 21.
  - `live_check.py`: 57 of 57 (`--no-model`). Local team: 5 of 5, including one MiniMax run.
- Known limit: the model's labels are rough (a passcode can show up as `[USERNAME_2]`). Only the value is hidden, and the reply is restored locally.
**Deliverables: PPTX decks and PDF reports (added 2026-10-16, D21):**
- Plugin `deliverables`:
  - `make_presentation`, `make_report`, `open_output`, `show_in_folder`, `start_slideshow`, `regenerate`, `list_deliverables`.
  - Outputs go to `D:\jarvis\outputs\neko\deliverables\<time>-<title>\`: the file, `spec.json`, `qa.json` and `previews\` (page PNGs and a contact sheet).
- Tools:

  | Tool | Location | Licence | Verified | Notes |
  |---|---|---|---|---|
  | OfficeCLI v1.0.152 | `D:\jarvis\tools\officecli\v1.0.152` | Apache-2.0 | SHA256SUMS match | 22 Sep build, chosen because it is over 2 weeks old. Auto-update is off (`officecli config autoUpdate false`; the plugin also sets `OFFICECLI_SKIP_UPDATE=1`). It contacted its update server once on the first run, before that was set |
  | LibreOffice 26.2.6 | `D:\jarvis\tools\libreoffice\26.2.6` | MPL-2.0 | sha256 match; GPG "Good signature" from the LibreOffice Build Team | 1.5 GB. Administrative extract (`msiexec /a`): no system registration and no file associations. Private profile in `D:\jarvis\cache\lo-profile`; first-run dialogs are pre-set off by the plugin |
  | python-pptx 1.0.2, pdfplumber 0.11.10 | `neko-venv` | — | — | Installed with `uv pip`. **A future `uv sync` removes them: reinstall with the same pins** |

- **Gotchas found:**
  - OfficeCLI batches are all-or-nothing: one bad command undoes all.
  - Tables cannot be animated.
  - python-pptx writes chart axis ids that fail the schema; the builder fixes them.
  - OfficeCLI leaves a background "resident" process holding the file; the plugin closes it.
  - LibreOffice's first-run "Welcome" dialog blocked the first visible slideshow.
- **Checks:**
  - `python -m plugin.plugins.deliverables.tests.run_demo` builds the demo deck and report.
  - `live_check.py` builds both through the plugin runtime, then deletes the folders it made.
  - Measured here: deck 11 slides, 11 transitions, animations on 9 slides, schema-valid, ~10 s; report 5 pages, tagged, 5 source links, fonts embedded, ~5 s.
- **Not verified:**
  - Playback in PowerPoint (not installed).
  - Whether LibreOffice advances slides on its own after animations. In one show the deck was on slide 2 after 15 s while the PC was in use, so a click cannot be ruled out. The owner should watch one run.
  - Telugu/Hindi text in decks and reports.
**Research with citations (added 2026-10-16, D22):**
- **Plugin:** `research.research_topic(question, depth=quick|standard, deliverable=none|report|presentation)`. It searches with N.E.K.O's `web_search` plugin (now started at login), reads pages within limits, has Dahl write through the privacy gateway (or the local generalist), and checks every cited statement with a local NLI model.
- **Where results go:**
  - The run record (queries, sources, passages, the writer's draft, check results) is saved as `research.json` next to the deliverable, or in `D:\jarvis\outputs\neko\research\`.
  - Page cache: `D:\jarvis\cache\research` (24 h).
- **NLI model:** `D:\jarvis\models\nli\nli-deberta-v3-xsmall` (int8 ONNX, 87 MB, Apache-2.0, revision a150876, sha256 checked).
- **Live results (quick depth, 2026-10-16):** 37–66 s per run. Between 7 of 16 and 11 of 17 cited statements were confirmed; the rest are listed as unconfirmed. About 3–6 of 8–10 pages refuse scripted reads (HTTP 403, or pages built by scripts).
- **Fixed during live runs:**
  - The planner dropped "AI" from the question; the owner's own words are now always the first query.
  - MiniMax ignored inline `[S#]` markers; it now gets a structured `sources` field per statement, and the code adds the markers.
  - Text after the JSON broke parsing.
  - The privacy filter turned "AI" into `[API_KEY_1]`; model-found secrets must now be at least 4 characters, and placeholders are never searched.
- **Checks:** `live_check.py` has a research test that runs only on the model path (it uses the web and one Dahl call).
**Neko icon and states (added 2026-10-16, D23):**
- **The window:** `jarvis\scripts\open-widget.ps1` opens the Neko icon by default (always on top, bottom right). `-Page status` gives the old detailed widget. The login task `JarvisWidget` therefore shows the icon from the next login.
- **What it offers:** click the cat to type a task; Talk opens N.E.K.O for voice. When something finishes it shows Open, Folder and Make again; while working, "Stop after this step"; when approval is needed, a link to the console.
- **States:** one state, from real signals only (`jarvis/widget/neko_state.py`). In priority order:

  | State | Signal |
  |---|---|
  | error | a server is down, the kernel is unreachable or killed, or the watchdog gave up |
  | needs_permission | kernel approvals are waiting (shows the bound action) |
  | speaking | estimated: the speech server synthesized a reply less than that reply's length ago |
  | working | a main-agent run is active (shows its step) |
  | thinking | a model call is in flight through the privacy gateway |
  | listening | the speech server transcribed something in the last 4 s |
  | completed | a run or deliverable finished in the last 3 minutes |
  | sleeping | none of the above |

- **New endpoints:**
  - Widget server: `GET /neko`, `GET /api/neko`, `GET /api/events` (server-sent events, checked every second), `POST /api/action` (open, folder, regenerate or stop; paths limited to the deliverables folder).
  - Speech server: `GET /activity`.
  - Gateway `/health`: adds `inflight` and `last_call_age_s`.
- **Measured live:** sleeping → speaking (on a real synthesis) → completed (on a real deliverable), all pushed by the event stream. `live_check.py`: 4 icon checks, 66 of 66.
- **Limits:**
  - "Listening" means "just transcribed", not "microphone open": N.E.K.O's page does not report its microphone state.
  - "Speaking" is estimated from the synthesized length.
  - Wake listening shows only once the wake word is built (phase 5).
  - The Edge window has no transparent overlay or global push-to-talk (phase 8 shell).
**Hardware profiles, memory guard and provider fallback (added 2026-10-16, D24):**
- **Hardware detector:** `python jarvis\local_models\hwdetect.py` reads the CPU (name, cores, AVX2), RAM, disks, OS and GPUs as llama.cpp sees them. It picks a profile (remote_only, cpu8, cpu16, vram4, vram8plus) and saves `<data>\neko\hardware.json`.
  - This laptop: vram4 (Radeon Pro 5500M, 3439 MiB free).
  - Override with `NEKO_MODEL_PROFILE`.
- **Launching models:** `local-models.ps1` starts llama-swap with that profile's config: vram4 uses `llama-swap.yaml`, the others `local_models\profiles\<profile>.yaml`.
- **Downloading a profile's models:** `fetch_models.py --profile <name>`. The Qwen3.5 2B, 4B and 9B files were checked to exist on Hugging Face (unsloth, Apache-2.0).
- **Memory guard:** before `local_team` loads a model that is not already loaded, it needs the model's RAM (general 5.5 GB, code 1.8, math 1.6, router 3.5) plus 1.5 GB in reserve. Otherwise it refuses with a plain message. Override per model with `NEKO_MODEL_RAM_GB_<NAME>`.
- **Provider fallback in the privacy gateway (`jarvis\privacy\providers.json`, no secrets in it):**
  - Enabled providers are tried in order, and every request is redacted first.
  - The first provider (Dahl) always gets the caller's key and model.
  - A fallback needs `enabled`, a `model` and a key file name under `D:\jarvis-secrets`.
  - Each provider is tried once per request, and only on 429, 5xx or a timeout. A 429 makes that provider sit out for 60 s.
  - Responses carry `X-Neko-Provider`; `/health` lists `providers`, `answered_by` and `cooling_down`.
  - Today only Dahl is enabled, so the behaviour is unchanged until the owner adds a key.
  - Tests: 4 fallback tests with fake providers (27 privacy tests in all). One live call through Dahl after the change was correct.
**Evaluations and feedback (added 2026-10-16, D25):**
- **Evaluations:** `python jarvis\evals\component_evals.py` scores routing (16 requests), the adviser decision (12), the claim checker (8), privacy redaction (24) and the deliverable checks.
  - It saves `D:\jarvis\outputs\neko\evals\<time>.json` and compares with the previous run.
  - Exit 1 and a "WORSE:" list mean a regression. Local only.
- **Baseline (2026-10-16):** routing 0.938 (~1.1 s each), adviser decision 0.917, claims 1.0, secrets hidden 1.0 with 0.25 of ordinary messages flagged, deck and report checks pass.
- **Feedback:** the 👍/👎 buttons on the Neko icon's Done state write `<data>\neko\feedback.jsonl` (rating, the file, an optional note).
  - Read it when changing prompts or models. Nothing changes automatically.
  - Prompt and model changes go through git and must not make `component_evals.py` worse.

## PC control (added 2026-10-16, D27) and the "Hey Neko" wake word (D26; removed 2026-10-23, D34)
**Wake word (Windows, neko-venv):** `neko-start.ps1` starts `jarvis\wake\wake_listener.py` (status http://127.0.0.1:48945/status); `-NoWake` skips it, `neko-stop.ps1` stops it.
- [Status 2026-10-19: wake, ASR, TTS and spoken stop are tested on Linux only with synthetic audio and fakes; the live microphone path is NOT RUN (needs the owner's laptop); see `reports/CAPABILITY_STATUS_2026-10-19.md`.] Say "Hey Neko", wait for the chime, then the command. "Cancel" / "never mind" cancels; "Hey Neko, stop" stops a running task. POST /pause turns the microphone off for it; /resume turns it back on.
- No audio is saved; `<data>\logs\wake_listener.log` holds states and timings only.
- Tuning: `NEKO_WAKE_WORD_THRESHOLD` (default 0.25), `NEKO_WAKE_WORD_KEYWORDS_FILE` (one phone line per row, e.g. `HH EY1 N EH1 K OW0 @hey_neko`).
- Checks: `python jarvis\wake\eval_wake.py` (synthetic voices), `jarvis\wake\loopback_test.py` (plays clips through the speakers, dry run), `command_cut_check.py`.
- **Owner's spoken test:** with the stack running, say "Hey Neko, what is the time?" five times from where you usually sit, and "Hey Nico" twice. Then read `counts` and `audio` at the status URL: detections, and the loudest second (`max_s_dbfs`). If it misses, lower the threshold to 0.2 or speak closer; if it false-wakes, raise it to 0.3.
- Rebuilding the runtime (only after a sherpa-onnx change): `scripts\wake_word\build_wake_word_runtime.ps1` with `$env:UCRTContentRoot='C:\Program Files (x86)\Windows Kits\10\'` for that shell; output on D:.

**Approval PIN (owner only, once):** in a Windows terminal on the laptop, run
`ssh -t -i D:\jarvis-secrets\ssh\jarvis_vm_ed25519 ubuntu@100.125.91.51 "cd ~/jarvis/kernel && python3 -m jarvis_kernel set-approval-pin"`
and type the PIN there (6 to 64 characters; it is stored as a salted hash). The JARVIS console at http://127.0.0.1:8799 then shows "Approval PIN: set" and approves YELLOW actions with it. RED stays at the kernel console. Never put the PIN in a file, a chat or a log.

**Playwright extension (owner, per Chrome profile):** in the Default profile and in the VenkatAMV profile (Profile 1), open `https://chromewebstore.google.com/detail/mmlmfjhmonkocbjadbfplnigmagldckm` (Microsoft's Playwright extension; check the publisher is Microsoft) and add it.
- Without the extension, Neko can only open pages there (`chrome --profile-directory`).
- Optional: copy the connection token the extension shows into `D:\jarvis-secrets\playwright\extension-token.txt`. Without it, Chrome asks you to allow each connection.
- The profile registry is `<data>\neko\browser_profiles.json`: aliases and masked accounts, rebuilt from Chrome's profile list when missing. "Venkat" fits four profiles, so Neko asks which one; "VenkatAMV" is Profile 1.

**Repairs:** backups are in `<data>\repair-backups\<id>` (the file and its manifest). "Undo" is `repair restore <id>` (it runs at once and is itself undoable).
- Sample app for the checks: `jarvis\tests\neko_sample_app\build.ps1` builds it with a broken config and a Start-menu shortcut "Neko Tests\Neko Sample App"; `-Remove` deletes both.

**Cancel:** `POST /api/owner/job/cancel {"id": ...}` (console), or the plugin's `stop_pc_job`. The node kills what the job started within about 3 s.

## Linux test server and Benchmark Console (added 2026-10-17, D29, D30)
**Linux test server (owner travelling):** the relative's machine, used only as a headless test bench.
- Rules: nothing outside `/srv/jarvis`, no sudo, no reboot, low priority (nice 19, capped CPUs). There is no VM (VT-x is off in the BIOS).
- Tests run in the rootless podman image `neko-test` (recipe: `jarvis/scripts/neko-test/`; rebuild with `~/neko-test/sync-and-build.sh` whenever `uv.lock` changes).
- Kernel, then benchmark v2:
  ```
  nice -n 19 podman run --rm --userns=keep-id -e HOME=/tmp -v ~/hq-neko:/src:O neko-test sh -c "cd /src/jarvis/kernel && python -B -m unittest discover -s tests"
  nice -n 19 podman run --rm --userns=keep-id -e HOME=/tmp -v ~/hq-neko:/src:O neko-test sh -c "cd /src && python jarvis/evals/quality95/bench/bench.py"
  ```
- `:O` is a throw-away overlay: files written inside it are not kept. Run as a normal user (`--userns=keep-id`); as root, permission tests fail.
- The research (H) cases and the research plugin tests need the claim-checker model mounted and `RESEARCH_NLI_DIR` set.
- Windows-only cases (UI Automation, Chrome profiles, microphone) are DEFERRED here, never simulated.
- Baseline, run 2026-10-17 morning on `4e32e583` (historical; later runs: 2026-10-17 `15d62f50` 834/0/0/26, the 2026-10-18 remediation tree, round 2: kernel 717 OK, console 127 OK, benchmark 834/0/0/26 (9.81), and after the independent review's fixes: kernel 866 OK, console 176 OK, benchmark 833/1/0/26 (9.62), then 834/0/0/26 (9.81) after the owner's K-106 change of 2026-10-19; details in `reports/REMEDIATION_2026-10-18.md`):
  - kernel 538 tests OK (16 skipped, Windows);
  - console 93 OK (3 skipped);
  - benchmark v2 860 cases: 833 pass, 1 fail (H-042), 0 errors, 26 deferred (`results/bench-20261017-102655.json`);
  - plugin/tests 4289 passed, 8 failed (the plugin metadata snapshots, rebuilt on the laptop);
  - tests/unit 41981 passed, 4 failed (Linux-only, known).
- Windows tests are unchanged: see "JARVIS Console" above, `run-upstream-tests.ps1`, and the laptop checklist in the progress report.

**Benchmark Console:** the Bench screen of the existing console at http://127.0.0.1:8799 (routes `/api/bench/*`; no new port, loopback only).
- Tabs: Dashboard, Run, Challenge, Models, Security.
- Dashboard: pass, fail and deferred per area from the saved result files, with sample sizes and evidence labels (live, integration, mocked). The official v1 score is shown separately.
- Run: one job at a time, built only from allow-lists (groups, existing case ids, pinned model names, fixed sets). Argument lists, no shell, nice'd. Cancel stops the whole process group. Failed cases can be re-run.
- Challenge: runs a typed request through the real `decide.py`, coordinator and kernel policy in a sandbox (synthetic profiles, simulated observations; rules only unless a local model answers) and grades it. The result is untrusted: it is saved under `jarvis/evals/quality95/results/challenges/` (gitignored, local only) and never becomes a benchmark expectation.
- Models: lists only the entries of `bench/model_candidates.json`. The screen never downloads a model.
- DELETE is accepted only under `/api/bench/`, with the same same-origin check as POST.
- Start it as usual (Windows: `jarvis\scripts\start-console.ps1`).

**Model comparison (CPU):** `python jarvis/evals/quality95/bench/model_compare.py --models qwen3-4b-2507,qwen3-1.7b`.
- Only pinned candidates (repo, revision, file, SHA-256). `--fetch` downloads them and checks the hash.
- Results: `results/models-*.json`. CPU numbers are this server's; they are not a forecast for the laptop GPU.

**Claim checker evaluation:** `RESEARCH_NLI_DIR=<model dir> python jarvis/evals/quality95/bench/claims_eval.py --set claims_v2` (sets in `plugin/plugins/research/tests/fixtures/`: `claims_v2`, `nli_pairs`, `vitaminc_sample`). Results: `results/claims-*.json`.

**llama-server flag guard:** `python -m pytest jarvis/local_models/test_server_flags.py` (9 tests). Every model server binds 127.0.0.1 with `--no-webui`; no command carries `--tools`, `--agent`, MCP flags, `--props` or a plain `--api-key`; llama-swap listens on 127.0.0.1:8080; `model_compare.py` starts its server the same way. Run it after any change to `llama-swap.yaml` or `local_models/profiles/`.

## Caretaker (added 2026-10-17, D30)
**Caretaker** (`jarvis/caretaker/`, README there): a weekly lab task checks every tool in `jarvis/tools/registry.json` and `jarvis.lock.json`: upstream releases (GitHub, Hugging Face, Node.js), OSV advisories (package names and versions only), lock drift (`fetch.py`'s check), failed benchmark cases and gitleaks. Each new finding of medium severity or higher becomes one kernel task (Idempotency-Key = the finding id). The lab sub-agent fixes it on a `caretaker/<tool>-<id>` branch, records the old pin in the PR body and in `jarvis/caretaker/history.jsonl`, runs the mapped tests and opens a pull request to `main` for the owner (a draft, or "could not fix", when tests fail). It never pushes to `main` and never merges; the kernel refuses any caretaker dispatch that is not the fixed template. Logs: `<data root>/logs/caretaker/<date>/findings.jsonl`, `runs.jsonl`, and `caretaker_run` in the kernel audit chain. To switch it on, install `jarvis/caretaker/automation_preset.json` in the console (it arrives paused) and press Resume. The lab needs the caretaker's kernel token (`agent-token caretaker --lane public`), a config file and a GitHub login with `repo` scope; protect `main` on GitHub. Look first without changing anything: `python -m jarvis.caretaker.run --dry-run`. **Branch protection on `main` cannot be enabled on this GitHub plan (verified 2026-10-19), so the lab agent must never hold the owner's GitHub token: give it a credential for a separate fork/mirror that cannot write to `jarvis-hq` (jarvis/caretaker/README.md, "Safe deployment" has the design and the read-only verification commands).**

## Control surfaces and Stop (added 2026-10-18, follow-up to the independent review; details in `reports/REMEDIATION_FOLLOWUP_2026-10-18.md`)
- The widget (`48940`), the wake status server (`48945`) and the speech server's `/interrupt` now need a per-launch secret on every state-changing route (`X-Jarvis-Control`), an exact loopback Host and an allow-listed or absent Origin. The secret is published to a 0600 token file (`wake.token`, `widget.token`, `speech.token`) in `%LOCALAPPDATA%\jarvis\run` (Windows) or `$XDG_RUNTIME_DIR/jarvis` / `~/.jarvis/run`. **Restart the widget, the wake listener and the speech server together.** A process of the same OS user can still read the token file; the PIN stays the boundary for approvals.
- Stop (`desktop:stop_pc_task`, the Stop button, a spoken "stop") now reaches every PC operation, including single actions (`run_pc_action`, `run_browser_action`, `open_website`, `play_youtube`, `run_troubleshooter`). It reports `still_running` for a worker that has not ended and keeps the PC lock until it has. A busy answer names the holder. `desktop:recover_pc_lock` (owner only) frees a lock only when the holder was asked to stop and everything it started is confirmed ended.
- The privacy gateway refuses model calls (503, `/health` `ready=false`) when the GLiNER model is missing; `JARVIS_PRIVACY_REQUIRE_DEEP=0` opts out.
- Planner memory goes only to a verified local model; plans that used memory cannot hand steps to JARVIS or `web_search` unless `MAIN_AGENT_MEMORY_TO_EXTERNAL_STEPS=1`.
- Branch protection on `main`: **verified NOT enabled on 2026-10-19** (read-only: `branches/main` shows `protected:false`; the protection and rulesets APIs answer 403 "Upgrade to GitHub Pro or make this repository public", so it cannot be enabled on this plan for a private repo). The login stored on this server (`GH_CONFIG_DIR=/srv/jarvis/home/.config/gh-jarvis`, scopes gist, read:org, repo, workflow, admin on the repo) could push to `main`: never give it to the lab or the caretaker. Safe design and the verification commands: `jarvis/caretaker/README.md`.
- Lab verdict (third pass, 2026-10-19): the verifier's final answer must end with one `JARVIS-VERDICT: {json}` line and the delivered tests are run by a runner-owned harness; deploy `lab_runner.py` and the kernel together (an old runner's passes are demoted by the new kernel) and check once on the lab box that the installed Hermes prints its final answer, and only that, on stdout. The same-user residual is documented in `jarvis/caretaker/README.md` ("How the lab's verdict is protected"); it is reduced, not closed.
- PC lock (third pass): a cancelled task or mission keeps the PC lock until the kernel confirms its node job ended (`desktop:recover_pc_lock` only under its documented conditions); the reaper still releases after 900 s if the node never confirms.

## Console sign-in, retention and approvals (added 2026-10-19, fourth pass; details in `reports/REMEDIATION_PASS4_2026-10-19.md`)
- **Console, widget, wake status and speech servers:** a bare `GET /` no longer returns any secret; it returns a login page (401). First-party programs read a master token from a 0600 file (`<name>.token` in the per-user run folder: `%LOCALAPPDATA%\jarvis\run` or `$XDG_RUNTIME_DIR/jarvis` / `~/.jarvis/run`); browsers sign in with a one-time launch code (valid 60 s, single use) that `jarvis/scripts/open_console.py` (or the widget's "open console" link, or `jarvis-auth.ps1` on Windows) puts in the URL fragment, which becomes an HttpOnly SameSite=Strict cookie. Typing the console address into a browser therefore shows the login page: open it with the launcher. A restart ends open browser sessions. A process of the same OS user that can read the token file or the browser profile can still sign in.
- **Retention** (`jarvis_kernel/retention.py`): used, denied or expired approvals are reduced to a stub after 24 hours (tool, op, lane, requester, digest prefix, per-field lengths and salted hash prefixes; undecided cards keep their full text); lab-share text is deleted when used, denied or expired; final jobs after 14 days (or beyond the newest 2,000), final tasks and owner commands after 30 days (or beyond the newest 300/500) keep ids, states and lengths only; kept idempotency replies after 24 hours. Runs at kernel start, about hourly, and with `python3 -m jarvis_kernel retention --run`; `retention --show`, `--off`/`--on`, `--set tasks_days=60`, or `JARVIS_RETENTION=off`. Old audit rows cannot be rewritten without breaking the hash chain (new rows are metadata-only). Not covered: `projectfix.data`, `memories` (own forget), connector call rows, automations, agent definitions, traces, approval `reason` text.
- **Approval cards** now show every invisible/format/control character as `<U+XXXX>` and carry `invisible_chars`/`has_invisible`; hostile characters (tag, bidi, zero-width, soft hyphen, control) block approval (409), legitimate Hindi/Telugu ZWJ/ZWNJ and emoji joiners stay approvable with a notice, warnings need an extra acknowledgement on top of the PIN and typed id. The owner approve route and `Kernel.approve` refuse while the kill switch is on; an approval granted during a kill is withdrawn at resume.
- **Stop:** a cancelled or ended PC operation/mission/task denies its own still-pending approvals; the spoken stop sees PC work from the operation registry snapshot (`reports/pc_operations.json`) and accepts whole-utterance Hindi, Telugu and Hinglish stop phrases; the stop answer reports `requested/stopped/still_running` honestly.
- **Caretaker:** the dispatch text must match a fixed template (allow-list); see the README. Do not switch the caretaker on until a limited credential exists.
- **Lab verdict isolation** (`jarvis/lab/lab_isolation.py`): delivered code runs as `jarvis-sandbox` (one process per test id), the verifier as `jarvis-verify`; `verified` needs mode `full` and the owner's statement `JARVIS_LAB_VERIFIER_TOOLS=sandboxed`; every other mode ends "completed, NOT verified". Create the two users on the lab box and copy `lab_isolation.py` next to `lab_runner.py` (README, "Deploying the isolation boundary").

