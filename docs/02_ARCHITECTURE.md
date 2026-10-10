# 02 Architecture

JARVIS is an agentic OS layer. It is not an operating system and does not run AIOS. A small deterministic kernel decides what is allowed. Models propose; the kernel disposes.

## Parts

```
Phone (Telegram, Obsidian)        Laptop node (Windows 10 only while booted)
        \                          - computer state collector (read-only first)
         \                         - local models, tool gateway for local tools
          v                        v
        +------------- KERNEL on hermes-agent-vm (Oracle, always on) -------------+
        | rules engine | context builder | audit log (hash-chained) | kill switch |
        | task state (SQLite) | node registry | model registry | scheduler         |
        +-----------------------------------------------------------------------+
                 |                      |                        |
           Hermes bots            model gateway             vault (git)
        (pinned, API keys only)  (OmniRoute trial)     inbox/ daily/ decisions/
```

- **Kernel (VM-A):** small Python service. Rules, context builder, audit log, kill switch. No LLM in the permission path. Fails closed.
- **Nodes:** laptop (Windows 10, sometimes macOS) and phone report in over Tailscale. Nodes connect outbound only and pair with a one-time code. The laptop is often off; anything that must keep running goes on Oracle.
- **hermes-agent-vm-2:** protected. JARVIS never touches it.
- **Bots:** Hermes agents on the VMs use API keys only, never login-based providers.

## Permissions (enforced outside the model)
- GREEN: automatic (inspect, read, run tests, gather state, create a branch).
- YELLOW: allowed under constraints or approval (install packages, change config, restart non-critical services, routine messages).
- RED: approval mandatory (money, deletions, credential changes, irreversible publication, sending sensitive data out).
- The kernel sets a risk floor from deterministic rules. A model can raise a risk level, never lower it. A model cannot grant itself a permission.
- After a session reads untrusted content (web, files, third-party text), its egress tools are removed until the task ends.
- Network egress is an allowlist. Secrets stay in the kernel and tools, never in a model context.

## Data lanes
- Private lane: local models, plus the plan's private-lane providers only. Public lane is for non-private research only. Fallbacks never cross lanes. The kernel stamps each task with a lane and the model gateway refuses a route outside it.

## Decision cascade
Minimum sufficient intelligence, per task class (see 03_DECISIONS.md): constant or rule, then embedding router with a simple classifier head, then a small local LLM with constrained JSON, then a larger model, then you. A state machine wraps the flow: observe, plan, route, act, verify, repair or record.

## Control ladder (computer use)
API, then DOM, then CLI, then OS API, then accessibility tree, then vision, then pixels. Use the lowest level that works. The state format copies Windows-MCP Snapshot.

## Memory
The vault is the long-term memory (see vault/decisions/memory-map.md). Structured state (hardware, services, models, tasks, events) lives in SQLite, not in embeddings. Embeddings are only for retrieval over vault text. Agents write only to inbox/, daily/, decisions/; the write path checks the folder in code, and a test must prove a write outside those folders is refused.

## Nodes and scheduling
Each node reports identity, hardware, free RAM, services, installed models and health. The scheduler picks a node by privacy lane, latency, resources and cost. A laptop that is off or in macOS is simply absent; work that needs it waits or goes elsewhere.

## Verification
Every action is followed by an observation and a check. Failure triggers a different strategy (never the same action again), then escalation. Every workflow has limits on attempts, time, actions and cost.

## Out of scope for now
Training and fine-tuning (design the interface only: a TrainingProvider with request fields model, task, dataset, method, expected gain, compute, evaluation), voice, agent factory, a fancy UI, extra nodes.

## Hardening added after independent review (2026-10-07)
- **Approvals are out of band.** An approval is bound to a nonce and the hash of the exact action, shown to you on a channel no model can write to (kernel CLI or a paired device), and expires. Text a model or a web page produces can never count as approval. Forged GREEN, YELLOW or RED grants, a lowered risk floor, and replayed approvals are each refused and each has a test.
- **YELLOW is split.** YELLOW-auto: allowed automatically inside written limits (for example installing a pinned package into a sandbox). YELLOW-approval: needs an approval as above. Anything not listed as YELLOW-auto is YELLOW-approval.
- **Lanes come from the kernel, not a model.** Every provider is listed in the kernel config with its lane. A task's lane comes from deterministic source tags (where the data came from). Unknown means private. A model cannot change a lane. Some free tiers train on inputs, so a provider is public-lane unless its terms say otherwise.
- **Secret scrubber.** All vault writes and all model-bound context pass through a secret-pattern scrubber. The agent sandbox and the Windows-MCP filter deny access to D:\jarvis-secrets. Collected machine state goes to a cloud model only after an approval; by default it stays local. The collector skips secret-like paths and environment variables.
- **Protected resources are enforced in code.** The OCI wrapper loads D:\jarvis-secrets\protected.json (hermes-agent-vm-2, its boot volume and VNIC, and the shared subnet) and refuses any write to them. Names are matched by exact full-name equality, never by prefix. The shared VCN, subnet and security lists are not edited without your approval.
- **Audit log integrity.** Besides the hash chain, the head hash is copied off the box on a schedule (to the vault repo) so truncation or a rewritten chain is caught.
- **Cost cap per route.** The model gateway refuses any route marked paid. The default budget for every provider is $0. Providers are used only through keys with no card or billing attached.
- **Reviewer subagents run read-only with no access to D:\jarvis-secrets.**

## Browser guard (added after measuring Playwright)
The agent browser runs with a throwaway profile (deleted after the session), no downloads, no permissions, no popups, one page, and an action budget. All of its traffic is forced through a local filtering proxy that resolves every hostname itself, refuses loopback, private, link-local, metadata and Tailscale addresses and the addresses of internal-looking names, and connects only to the address it checked. This covers redirects, subresources and DNS names that point at private addresses; an in-page request hook was measured to miss redirect hops. The kernel's policy refuses internal URLs and password-manager, wallet and banking sites before the browser is asked. Page text is untrusted: it is scrubbed, hidden elements are not shown, and the first page read marks the session so the kernel removes egress tools. DNS rebinding does not apply to the proxied path because the proxy connects to the exact address it checked. Known limits: QUIC and WebRTC are handled by browser flags (an HTTP proxy disables QUIC; WebRTC is restricted to proxied UDP) and that has not been independently verified; a hostile network that tampers with DNS answers could still point a public name at a public attacker address, which this guard does not address.

## Always-on local services on the VM (added 2026-10-08)
The VM runs two small services bound to 127.0.0.1 only: an embedding service (bge-small, port 8081, about 10 ms per text) and a local Qwen3-4B (Q4_K_M, port 8080, 2 CPU threads, memory capped at 6 GB). Measured on the 2-core ARM VM: about 5.6 tokens/s generation and slow prompt processing, so the model is a last-resort private-lane fallback (provider priority 90, 180 s timeout) behind the laptop GPU model (10), Groq (20) and OpenRouter free models with data collection denied (30). Both services run from the pinned llama.cpp b11461 Ubuntu ARM64 build (checksum verified) as an unprivileged user with no new privileges and a read-only home. A nightly systemd timer (21:00 UTC) runs the decision-layer regression against these services and sends a summary to the owner's Telegram chat; any high-risk request judged GREEN or YELLOW, or a fall of more than 5 points in the hybrid's accuracy, is flagged first. This is real recurring work for the VM and also keeps it from looking idle, which matters for Oracle's reclaim rule.

## Browser egress checkpoint changed to Stripe Smokescreen (2026-10-08)
The agent browser no longer relies on my own local proxy. All of its traffic goes through Stripe Smokescreen v0.1.0 (MIT, github.com/stripe/smokescreen), running on the Oracle VM as service jarvis-smokescreen, bound to the VM's Tailscale address (100.125.91.51:4750) with a firewall rule on the tailscale0 interface only; the public address does not answer on that port. Smokescreen resolves each host itself and refuses non-global addresses plus the extra ranges configured in the unit (Tailscale 100.64.0.0/10, metadata and link-local 169.254.0.0/16, RFC 1918, loopback, IPv6 local ranges). Measured through it from the laptop: public sites 200; the metadata address, a router address, the Tailscale server, a hostname that resolves to loopback and the decimal-IP form of 127.0.0.1 all refused (407); a redirect from a PUBLIC site to the metadata address is stopped as well. Why on the VM: Smokescreen has no Windows build (it uses a Unix-only signal; main branch the same), and a checkpoint on a different machine from the browser cannot be loosened by a compromised laptop process. Side effects: browsing exits from Oracle's Hyderabad IP (some sites show captchas; your home IP is not shown to sites) and needs the VM and Tailscale to be up. The browser refuses to start unless the checkpoint is reachable AND refuses the metadata address in a self-test; there is no silent fallback. The old local proxy (guardproxy.py) remains only as an explicit local_guard option for tests that browse a local web server.

## Jobs: "Check my project" from the phone (added 2026-10-08)
Telegram /check <project> asks the kernel to queue a fixed job (kind project_check) for the laptop node. The kernel authorizes it first (policy tests.run, GREEN) and refuses if the kill switch is on. The node polls /jobs/next with its own token every 15 s, runs the job in a background thread (heartbeats never stall) and posts the result to /jobs/result; the Telegram bridge reports each finished or expired job exactly once. Jobs wait at most 30 minutes if the laptop is off. Safety: a job message carries only a project NAME; the node maps names to folders and test commands from its own allowlist (config/projects.json, which the owner edits), so nothing in a message is ever executed or used as a path. The runner uses read-only git commands plus the project's own test command, strips secret-looking environment variables from the test run, writes no bytecode, scrubs the output tail for secrets, and compares the git working tree before and after so the report says exactly what the test run changed. Running a project's tests executes that project's code, so only projects the owner lists can be checked. Measured live: queued, picked up in 20 s, 206 tests passed in 283 s on the laptop, report delivered; audit trail job_queued, job_started, job_finished, job_reported.

## Agent sandbox foundations (added 2026-10-08, for a "do anything" agent runtime)
Any agent runtime runs as the unprivileged OS user jarvis-agent (no sudo, nologin shell, own home and workspace). Measured: it cannot read the provider keys, the Telegram token, the kernel database or SSH keys, and cannot write the kernel's folders; the kernel data folder is now 700 and its database 600. A per-user firewall chain (JARVIS_AGENT_OUT, iptables owner match, saved) lets that user reach only the Smokescreen checkpoint and the kernel on the Tailscale address and the local model services and agent model endpoint on 127.0.0.1; IPv6 is refused; measured: direct internet, DNS, Groq, the other VM's subnet and the metadata address are all blocked, while web through the checkpoint works and the metadata address stays refused there. The agent reaches models only through the kernel's OpenAI-compatible endpoint (service jarvis-llm-proxy, 127.0.0.1:8790), authenticated with its own token (file readable only by jarvis-agent, never printed); the endpoint adds the provider key itself and applies the gateway rules (private lane only, free-model guard, priority and same-lane fallback before any byte is streamed, deep secret scrubbing of every message and tool-call argument, 4096 output-token cap, 30 requests a minute and 2000 a day, kill switch, audit with counts only). Measured as the agent user: a reply, a 33-chunk stream, and 401 without the token.

## Agent runtime: Hermes Agent (added 2026-10-08)
Hermes Agent v0.21.6 (NousResearch, MIT, pinned to commit 818c13be) runs as jarvis-agent inside the sandbox above, from its own Python 3.14 environment (installed with uv; 45 of its 48 dependencies need Python 3.14). Two services: jarvis-hermes-gateway (its API server on 127.0.0.1:8642, bearer key) and jarvis-hermes-serve (the backend for the Hermes desktop app, on the Tailscale address 100.125.91.51:9119 only, username and scrypt-hashed password; measured: unreachable on the public IP). Both are systemd units with ProtectSystem=strict, writes only in the agent's home, 2 GB memory cap, and web traffic forced through Smokescreen.
- Models: Hermes is configured with one "custom" provider, the kernel's agent endpoint (127.0.0.1:8790). It holds only its agent token, never a provider key. The endpoint strips the agent's own reasoning settings (the provider entry decides) and logs prompt size and tool names per call.
- Tools: shell, file, code execution, browser, computer use, delegation, cron, web, search, vision, media and connector toolsets are off; the API platform gets only memory and todo. Measured: the model is offered memory, tool_search, tool_describe and tool_call, and tool_search finds no shell, Python, file or web tool (the catalogue holds only todo). Hermes's own source restricts tool_call to the session's scope. A model reply claiming it "can run Python" was false.
- Limits seen: the Groq free tier allows 8,000 tokens a minute; one Hermes turn is about 13,000 to 17,000 characters (tool schemas plus history), so the fourth turn of a session hit HTTP 429 and the endpoint fell back inside the private lane (OpenRouter free, data collection denied). Long tasks will lean on the fallback.
- Not yet connected: Telegram (the owner bridge stays the only bot) and real tools. The plan is a kernel-gated MCP server so every Hermes action goes through policy, approvals and audit.

## Lab lane: test agents on the laptop, models on Colab (added 2026-10-09)
Owner rules: Colab hosts models only; every agent and test runs on the laptop under D:. Agent tokens are now bound to a lane: the owner's Hermes token stays private; the "lab" token is public and can never reach a private provider (tested). The model endpoint also listens on the Tailscale address (firewall: tailscale0 only) so agents in the laptop's WSL box use it while holding only the lab token.
- WSL box JarvisLab (Ubuntu 24.04 on D:\jarvis-lab): default user agent without sudo; Windows drives not mounted, Windows programs blocked, no Windows PATH; a boot-time firewall for that user allows the internet and the JARVIS endpoint only (Windows host, home network, other Tailscale peers and the VM's other ports refused, measured). Agents: Hermes Agent (pinned, with terminal, file, code, web and delegation), OpenCode 1.18.35, goose 1.54.0 (release digests checked).
- Colab model: Ollama on the owner's free T4 (gpt-oss:20b, plus a 16,384-token-context variant gpt-oss-16k), reached through ngrok. ngrok's IP allow-list is a paid feature; a free-plan deny rule refuses every caller except the VM's public address (measured: Colab and the laptop get 403). The provider colab-ollama is public lane because ngrok sees the traffic. The ngrok URL changes when the runtime restarts.
- OmniRoute 3.8.51 runs on the VM (dedicated user, loopback only, login and API key required) but routes nothing yet: OpenCode's free tier answers "can only be used from within OpenCode", so it was switched off; Pollinations needs a free key.

## One window for everything (added 2026-10-09)
The JARVIS Hub (desktop shortcut "JARVIS Hub", an Edge app window on http://hub.localhost:8088) shows every dashboard as a panel: Agent PC, openmuse, Hermes lab, OpenCode, Hermes main (server), OmniRoute, plus live status of the JARVIS model endpoint, the GPU model server and OpenJev. Caddy in the WSL box (user hub, loopback, port 8088, forwarded to this laptop only) gives each one its own *.localhost name. The agent PC is a real XFCE desktop in the WSL box (TigerVNC display :1, loopback), viewable in the hub (noVNC). The native Remote Desktop route (xrdp on localhost:3390) was removed on 2026-10-10: xrdp 0.9.24 is end-of-life with unpatched CVEs. The WSL agent user is blocked from the hub proxy and the status checker so neither can be used to get around its firewall (measured). The server's Hermes now serves its full dashboard on 100.125.91.51:9119; OmniRoute's dashboard is reachable on the Tailscale address with its login.

## Agent control (added 2026-10-09, owner defaults)
Lab agents (Hermes, OpenCode, goose) get a small MCP tool server (hq/lab/jarvis_mcp.py, official MCP Python SDK 2.3.0) inside the WSL box. Each tool only forwards to the kernel through the model endpoint (POST /v1/jarvis/actions, the lab's public-lane token); the kernel's policy, single-use approvals, limits and audit decide. Tools: save_output and show_output (laptop node writes only inside D:\jarvis\outputs\<agent>\, never overwrites, opens text in Notepad and .html in the browser; YELLOW_AUTO; 12 shows per hour), pc_action (launch allow-listed apps, click, type through the existing Windows UI layer; YELLOW_APPROVAL every time: the owner gets a Telegram message and approves with /approve then CONFIRM; password managers, wallets and banking always refused), models_status and switch_model (same lane only, 20 switches per day; the endpoint puts the preferred provider first), start_lab_task and lab_task_result (hand work to another lab agent). Agent jobs are untrusted sessions, only their agent can read the result, and they are not reported to the owner's Telegram. Measured end to end: a save landed in D:\jarvis\outputs\lab in 4 s, the show opened Notepad on the owner's desktop, an escaping name was refused. Not yet: openmuse tasks (openmuse not running); OmniRoute has no active provider, so model failover happens in the JARVIS gateway.

## The Agent PC is the agents' own computer (added 2026-10-09)
The XFCE desktop in the WSL box (display :1) is the agents' computer; the owner watches it in the hub or in Remote Desktop and can take over. Lab Hermes has its computer_use toolset on, driven by cua-driver 0.21.0 (installed pinned through Hermes's package manager; telemetry off by default): health check passed for screen capture, AT-SPI and input on display :1. Firefox 157 from Mozilla's signed apt repository, with an enterprise policy that turns telemetry and first-run screens off. Accessibility works after putting the desktop, the driver and Hermes on the agent user's systemd session bus (970 elements read from a Wikipedia page, versus 1 before). Measured gaps in cua-driver's Linux support (it calls it alpha): typing into web pages through AT-SPI leaves them empty, and element frames are offset by the window position. So the JARVIS tool server adds desktop_open, desktop_windows, desktop_click, desktop_type, desktop_key and desktop_screenshot on xdotool and xwd; measured: a Wikipedia search typed and submitted on the Agent PC. These act only inside the lab box. Each agent's own instruction file (Hermes SOUL.md, OpenCode AGENTS.md, goose .goosehints) says it has this computer. Correction 2026-10-10: goose's computercontroller extension is now off, because goose 1.47+ has no screen tools on Linux but still claims a screenshot tool.

## Take control and Continue task (added 2026-10-10)
The owner watches the Agent PC in the hub without disturbing the agents and can take it over at any moment.

**Hub controls.** The Agent PC panel's header shows the state ("2 agents working · you are watching" or "You have control · agents paused") and three buttons: Take control, Continue task, and Activity.
- **Activity** is a drawer listing each task (goal, running time, working or paused, last step) and the latest 14 actions.
- **Watching** uses noVNC's own View Only switch, flipped live inside the screen. noVNC is served on the hub's own address (/pc/), so switching never reconnects or asks for the VNC password again.
- **Shared viewing:** Xvnc runs with -AlwaysShared -AcceptSetDesktopSize=0. Several viewers never kick each other off, and none can resize the agents' 1600x900 screen. Logging in to watch never stops an agent.

**lab_control.py** (WSL box) does the work:
- It runs as root and listens only on the Unix socket /run/jarvis-lab/control.sock (root:hub 0660; Caddy route /control/*; every request needs the header X-Jarvis-Hub: 1). There is no TCP port for Windows' localhost forwarding to expose, and the agent user cannot open the socket (measured).
- **Take control** freezes (SIGSTOP) every agent task of the agent user with everything it runs, whether or not the child has its own session (Hermes starts commands that way). Agent tasks are recognised by program and subcommand (hermes chat, opencode run, goose run), never by prompt text. Anything driving the screen (cua-driver, xdotool) is frozen wherever it runs.
- Windows the agents opened (Firefox, terminals, file manager) keep running for the owner. Programs the owner starts from the desktop are never touched. A task that starts while the owner has control is frozen within 2 seconds.
- **Continue task** unfreezes them. On its next call each agent that was running learns, once, that "the owner had control of your computer from X to Y… look again before you continue with what is left". Agents started later never get that note. While the owner has control, the desktop tools answer "waiting".
- Activity data comes from ~agent/lab/activity.jsonl: the JARVIS tool server logs each call and, since today, the laptop's answer (save_output:done and so on). It also uses Hermes's log. Root reads both without following links, and only files the agent user owns. Last steps are matched by process for JARVIS tools and by Hermes session for Hermes's own tools.

**Measured:**
- Unit tests: 10 on Linux; 6 run on Windows, where 4 Linux-only tests skip.
- With dummy tasks: frozen (T) while a "window" child stayed running (S), a task started during the takeover was frozen, Continue resumed everything, and the note was given once, only to tasks that were running.
- Hub buttons in headless Edge: same screen session, View Only flipped live, no reload.

**Also changed:** xrdp is off. Version 0.9.24 is end-of-life with unpatched CVEs, including a 9.8 in its VNC module, so the Agent PC is viewed in the hub only. Screen blanking and the screensaver are off.

## JARVIS acts on the owner's requests (added 2026-10-10)
A plain Telegram message ("open my Downloads folder", "open Spotify") now goes to the owner's own Hermes on the server: its API server on 127.0.0.1:8642, one ongoing session. It runs in the background, so /kill, /approve and job reports keep working while it thinks. /ask stays a quick model answer without tools.

Hermes gets the JARVIS tool server in its owner profile (/opt/jarvis-mcp, root-owned; its toolset is allowed on the API platform):
- save_output and show_output;
- pc_action;
- open_app, open_folder, open_file and list_folder;
- models_status and switch_model.

Opening things on the laptop is a new job kind, pc_open:
- **Apps** come from Windows' own Start list and open through shell:AppsFolder\<AppID>, so no agent-written command line runs.
- **Folders** are the known folders (Downloads, Documents, Desktop, Pictures, Videos, Music), resolved by Windows. Anything inside one is found by name, two levels deep, and cannot escape it.
- **Files** open only if they are document or media types, never programs or scripts.
- **Never opened:** password managers, wallets, banking apps and the secrets folder.

Policy:
- The owner's private lane runs at once.
- A lab agent (public lane) needs the owner's approval every time and can never list the owner's folders, since the names would reach a public model.

## Outcome tasks: plan, execute, verify, repair (added 2026-10-10, operator slice 1)
The owner says WHAT they want; JARVIS works out how. It opens a task in the kernel with:
- a goal;
- checkable acceptance criteria;
- a plan;
- a budget (1-5 attempts, a time limit).

Hands-on work is dispatched to the lab.

**Task states** are explicit and checked: planning, waiting_approval, queued, running, verifying, repairing, paused, completed, failed, cancelled. A task past its time limit fails. Retries stop at the attempt budget. Final states stay final.

**The lab runner** (`hq/lab/lab_runner.py`) runs as root in the lab box with its own runner token. That token can only fetch lab jobs and report on their task; the agents never see it. For each job:
1. A worker agent runs as the unprivileged agent user in its own folder.
2. A separate verifier session checks every criterion itself and writes VERIFY.json. The worker's own claim never counts.
3. On failure, a repair attempt gets the verifier's findings.
4. Evidence and the verdict go to the kernel.

The runner follows the owner's pause and cancel. Root reads agent files only without following links. A failed status request (network blip, kernel restart) never stops the work.

**Delivery.** The lab cannot see the owner's computer, so delivery is the runner's job, after verification:
1. The worker puts the files the owner should get in `./deliver/` in its workspace.
2. The verifier checks those copies. It never judges the laptop.
3. After a pass, the runner sends each file through the kernel (`/runner/deliver`, allowed only while the task is verifying). The laptop node writes it to `D:\jarvis\outputs\task-<id>\`.
4. The runner waits until the laptop confirms each file. Only then is the task completed, with evidence naming each laptop path and its `job:<id>`.
5. If the laptop does not confirm, the task fails: "verified, but NOT delivered".

If the request asks for delivery and `./deliver/` is empty, the runner fails the check itself. Each task has its own folder, so files from older tasks are never mistaken for this task's results.

**Honesty guards:**
- The kernel refuses "completed" from an agent unless an independent verification passed, or the evidence cites finished jobs (`job:<id>`).
- Every Telegram reply carries the kernel's own record of the tasks touched in that turn ("planning, NOT verified yet").
- Dispatch is refused while the task already has lab work, so there is no duplicate work. While the lab works, the agent may only pause, cancel or add notes.
- Dispatch is refused if the instructions or criteria name the owner's paths (`D:\`, `C:\`, `/mnt/`). The lab cannot see them, and work like that stalls or "verifies" stale files.
- The owner's agent cannot save files while its own task is at the lab, so no unverified copy races the verified delivery. It cannot deliver `.py` code at all, because it cannot run code. Code reaches the owner only through a verified lab task.

**Owner control on Telegram, with no model in between:**
- `/tasks` lists tasks.
- `/task <id>` shows one.
- `/task <id> pause|resume|cancel` changes it. The runner freezes or stops the worker within about 20 s.

**Lanes:**
- The lab runs on public-lane models.
- Dispatch from the private lane needs an explicit "may be shared" flag.
- Instructions are scrubbed, and private keys are refused.

**When lab work ends,** the Telegram bridge gives the outcome to JARVIS in the owner's conversation. JARVIS checks it against the criteria and tells the owner.

**First live runs (2026-10-10):**
- Before the guards, JARVIS wrote code itself and claimed "all tests pass". Checking its files showed the tests erroring and output "212.0 None".
- After the guards, the same request went to the lab. The verifier rejected attempt 1 (no files in the workspace), and attempt 2 repaired it.
- Found and fixed live: a duplicate dispatch (two extra jobs were cancelled), and a runner that killed work on a network blip.
- Task 5079247275a1 (temperature converter) was reported completed, but nothing reached the laptop. The verifier could not see the laptop, and it cited stale files from an earlier try. The code itself was right (12 tests, CLI checked by hand). A correction event is on its record, and runner-side delivery replaced the old design.
- Cancel test: the owner cancelled a running task, and the runner stopped the worker 21 s later.
- Full loop (task 9f98013ab16f, wordstat): the request went through the Telegram bridge. A dispatch naming `D:\` paths was refused; the next one was accepted. The lab built the tool, the independent check passed attempt 1, and 4 files reached `outputs\task-9f98013ab16f\`, each confirmed by the laptop. On Windows: 13/13 tests pass and the CLI is correct.
- Robustness (task 84f6f4708cd5, FizzBuzz), all in one run:
  - pause froze the worker (process state T) and resume continued it;
  - a 60 s outage of the kernel endpoint did not stop the work;
  - a killed worker (exit 137) went straight to the independent check;
  - the task completed and was delivered.
- Found by the same tests, and fixed:
  - Simultaneous dispatches made 2 jobs. The check-and-create step is now under one lock; six at once give one job.
  - A valid token got a 401 under concurrent requests, from a lazy cursor on the shared database connection.
  - Delivered tests hard-coded the lab folder and failed on the laptop. The runner now refuses lab paths and runs delivered tests from a fresh folder.
- JARVIS's single Telegram session grew to about 200k characters. The models rejected it and turns hung for 15 minutes. Sessions now last one day, and a failed turn starts a new one. Hermes compaction is capped at 30k tokens; its summary step still fails with a reasoning model.
- Memory (Hermes USER.md):
  - saved a preference, recalled it in a new session, replaced it on correction, and applied the corrected version;
  - removing it on request failed, because JARVIS looked for a label it had dropped when saving. The test entry was cleared by hand.

## Mission control (the hub's overview, added 2026-10-10)
The hub's first page is now the Agent OS dashboard. It follows the owner's references: the Contentstack Agent OS dashboard, the "agentic OS dashboard" articles, and dashboard design practice. It shows:
- **Top bar:** live KPI chips.
- **KPI tiles:** model calls, success rate, agent actions and approvals waiting, each with a 24-hour sparkline.
- **Agent cards:** JARVIS and the three lab agents, with status, current step, elapsed time and heartbeat.
- **The Agent PC:** a live thumbnail with Take control and Continue task.
- **Task board:**
  - Waiting on you: approvals.
  - Queued and Running: kernel jobs and lab tasks.
  - Paused, Done and Failed.
- **Delivered to you:** a gallery of D:\jarvis\outputs, with previews.
- **Model routing and services.**
- **Live activity rail:** filters and pause.
- **Ctrl+K command palette.**

Data sources, all read-only:
- **Kernel:** GET /v1/jarvis/overview, served only to a new "dashboard" lane token that cannot call models or act. Caddy adds the token, so the browser never holds it. The response has counts, states, names and times only; the fields are whitelisted, with no prompt text and no tool arguments (tested).
- **lab_control:** tasks, a screen thumbnail and the activity log.
- **Outputs:** D:\jarvis\outputs is mounted read-only for the hub user only and served on its own origin (outputs.localhost) with a sandbox CSP. An agent-written HTML file can never script the hub.

Design and assets: Lucide icons (ISC), the Geist font (OFL) and @fnando/sparkline (MIT), all vendored and sanitised. Dashboard projects with their own approval and audit stores (Mission Control, Paperclip) were not adopted, because the kernel stays the single authority.

## Lab models: Dahl (added 2026-10-10; replaces the Kaggle and Colab model servers)
**Why Kaggle and Colab are parked.** Serving a model from a notebook through a tunnel breaks Colab's rules (explicit) and reportedly Kaggle's ("server farming"), and there are account-ban reports. A Kaggle run was also cancelled without an error. The prepared server code (hq/kaggle/model-server) stays, at 64K context with the tunnel limited to three paths.

**Dahl.** The lab lane now uses Dahl (inference.dahl.global, run by FROMZERO OÜ, Estonia): an OpenAI-compatible API on the Gonka network whose terms allow agents and API automation.
- It is a prepaid promotional pool on the owner's one account: no card billing, never topped up. The key holds an allocation from that pool, which caps what a leak could spend.
- Requests run on independent GPU operators who may see them, so it is **public lane only**. A config test refuses any dahl*, gonka*, kaggle*, colab* or omniroute* provider outside the public lane.

**One provider entry per model**, so a busy model (HTTP 429) falls through to the next and agents can switch_model between them:
- dahl-minimax (MiniMax-M2.7) first.
- dahl-glm (GLM-5.3-Flash) second.
- dahl-deepseek (DeepSeek-V4-Flash) off: its text replies loop until max_tokens (measured), which burns the pool.

Measured from the server: all three list, answer and make correct tool calls, and stream with the first chunk in 0.5-0.8 s. MiniMax's reasoning arrives in <think> tags and is kept on purpose, because MiniMax says removing it from the history hurts its agent work.

**Agent settings.**
- Lab agents use a 64K window: Hermes's minimum, and Hermes cannot probe the window through the endpoint. OpenCode is set to 65536 context and 8192 output; goose has GOOSE_CONTEXT_LIMIT=65536.
- The endpoint retries a provider's 5xx once before falling back. (Ollama answers an unparsable gpt-oss tool call with HTTP 500.)

## Request sizing, traces, evals and the skill gate (added 2026-10-10, OpenJarvis integration)
**Gateway request sizing** (`jarvis_kernel/sizing.py`, port 8790 stays the only model path):
- Each provider carries its **measured** limit in `providers.json`: `max_input_tokens`, or `max_total_tokens` (input plus reserved answer).
- Before a call, the gateway estimates the request and skips providers it cannot fit. For input-plus-output limits it shrinks the answer budget to what is left (at least 1,024 tokens).
- If nothing in the lane fits, the agent gets one fast **413 `context_length_exceeded`**. Hermes treats that as "compress"; no provider is called.
- A provider's 413 or "context length" 400 counts as *too large*: no retry on the same provider.
- A 429 benches that provider for its `Retry-After`, or 1 h for a used-up daily quota. When every provider that fits is benched, the agent gets **429 with Retry-After**, never a 502.
- `/v1/models` reports the lane's `context_length` (private lane: 56000), so Hermes compacts in time.

**Kernel database:** the one SQLite connection is wrapped (`SafeDB`). Each statement holds one re-entrant lock and is read in full, and an audit transaction holds the same lock from `BEGIN` to `COMMIT`. This fixed live 401 and 404 errors under concurrent requests.

**Telegram continuity:** a new Hermes session (each day, or after a failed turn) starts with the kernel's card of open and recent tasks. Preferences live in Hermes memory.

**Task traces** (`jarvis_kernel/traces.py`, `python -m jarvis_kernel trace <id> [--public] [--save dir]`, `trace-analyze --days N`):
- A read-only view built from tasks, task_events, jobs and the audit log, through its own read-only connection. There is no second task store.
- Free text is scrubbed and clipped. Traces are capped at 48 kB, and `--public` drops all free text.
- The analyzer reports recurring problems, provider failures, refused actions and corrected tasks, with suggestions. It never changes anything.

**Eval suite** (`hq/evals/run_evals.py`):
- 19 fixed cases over the real kernel and gateway code, with fake providers that behave like the measured ones.
- Deterministic scorers check the resulting state, never an agent's claim.
- `--baseline evals/baseline.json` fails on any regression. `--traces <data dir>` adds live metrics: task completion accuracy, verifier agreement, attempts.
- The kernel before this work scored 11/17, with 6 regressions caught. The current kernel scores 19/19.

**Skill gate** (`hq/skills/skillgate.py`):
1. Stages Hermes optional skills from the pinned source (`818c13be`) byte-for-byte. Scripts are never copied.
2. Writes `PROVENANCE.json`: repo, commit, licence, SHA-256 and git blob per file, tool mapping onto the JARVIS registry, dependencies, purpose, data sensitivity, permissions, risk and findings.
3. Runs Hermes's own `skills_guard` in the lab; its findings can only raise the risk.
4. Activates only allowlisted skills with passed checks, through the kernel (`skill.activate`): low risk is automatic and audited, medium needs the owner's approval, anything else is denied.
5. Installs into the lab's `~/.hermes/skills/jarvis-staged/` and runs a live smoke test. A failure is rolled back automatically.

The lab Hermes has its skill tools on, with `skills.write_approval: true`, so the agent cannot change a skill without approval.
