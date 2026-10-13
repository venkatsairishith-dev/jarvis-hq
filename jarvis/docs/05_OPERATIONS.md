# 05 Operations: Oracle, cost, storage, secrets, hand-over

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
- Reusable procedures live as project skills in D:\jarvis\.claude\skills, with a copy in jarvis-hq: cost-check, phase-gate, vault-write, oracle-delete.
- Repos: github.com/venkatsairishith-dev/jarvis-hq (docs, skills) and jarvis-vault (memory). Local: D:\jarvis\hq (jarvis-hq) and D:\jarvis\vault.

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
  - `python hq\lab\tests\approval_live.py` (needs your /approve on Telegram)
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

## N.E.K.O base (added 2026-10-14)
**Where things are.** Repository: branch `neko-base`, worktree `D:\jarvis\hq-neko` (same git repository as `D:\jarvis\hq`, whose `main` still runs the live JARVIS services). Python environment `D:\jarvis\tools\neko-venv` (uv, Python 3.11), data `D:\jarvis\N.E.K.O-data`, logs in its `logs\`.
**Start and stop (Windows):** `powershell -File D:\jarvis\hq-neko\jarvis\scripts\neko-start.ps1` and `neko-stop.ps1`. UI: http://127.0.0.1:48911 . Start brings up memory 48912, main 48911, agent 48915 and plugin runtime 48916, rescans plugins and starts `jarvis_bridge` then `main_agent`.
**First run:** the web UI asks for the storage location (choose `D:\jarvis\N.E.K.O-data`), then add the model key in settings and switch the Agent master switch on. Keys are typed by the owner; nothing here reads them. Until the storage step is done, config endpoints answer 409 (limited mode).
**Environment rebuild:** from the repo root with `UV_PROJECT_ENVIRONMENT=D:\jarvis\tools\neko-venv`, `UV_CACHE_DIR=D:\jarvis\cache\uv`, `UV_PYTHON_INSTALL_DIR=D:\jarvis\tools\uv-python`: `D:\jarvis\tools\uv\uv.exe sync --frozen --python 3.11`. The editable install points at the repo it was synced from.
**Update from upstream:** in `D:\jarvis\hq-neko` on `neko-base`: `git fetch neko main`, `git merge neko/main`, resolve, run the plugin tests, then `uv sync --frozen`. The `neko` remote cannot be pushed to.
**Tests:** `python -m pytest plugin/plugins/main_agent/tests` and `.../jarvis_bridge/tests` (run separately: both ship a `test_smoke.py`). JARVIS suites run as before from `jarvis\kernel`, `jarvis\console`, `jarvis\node` (set `PYTHONPATH` to the console and kernel folders).
**Byte-exactness check for JARVIS files:** for each file in `git ls-tree -r main`, `git show main:<path>` and `jarvis/<path>` must hash the same (0 differ at b77ddc7b).
**Rollback:** nothing was removed. `git worktree remove --force D:/jarvis/hq-neko` and `git branch -D neko-base` return to the previous state; `main` and tag `pre-neko-base` are untouched.
**Cutover (live services to this tree):** `powershell -File D:\jarvis\hq-neko\jarvis\scripts\cutover-to-neko-base.ps1` is a dry run that checks the new tree (built UI, node path helper, start scripts), the connector allowlist, that the node resolves the same workspace folder (`D:\jarvis`, so `cache\` and `outputs\` do not move), and that JARVIS is idle. Add `-Apply` to back up both scheduled tasks, repoint `JarvisNode` (working directory) and `JarvisLab` (script), restart the node and console, verify node, kernel and console, and roll back by itself if a check fails. Manual rollback: `rollback-from-neko-base.ps1 -BackupDir <printed folder>`. Status at 2026-10-14: dry run passed; not applied (the live services still run from `D:\jarvis\hq`). The kernel on the server is not touched by either script.