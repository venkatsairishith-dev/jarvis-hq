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

**Owner task control:** on Telegram, `/tasks` and `/task <id> [pause|resume|cancel]`. Each task's files land in `D:\jarvis\outputs\task-<id>\`.

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
