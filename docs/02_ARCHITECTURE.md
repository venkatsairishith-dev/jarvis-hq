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
