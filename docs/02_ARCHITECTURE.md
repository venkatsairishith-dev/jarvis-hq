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
