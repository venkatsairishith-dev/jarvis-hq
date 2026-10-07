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
