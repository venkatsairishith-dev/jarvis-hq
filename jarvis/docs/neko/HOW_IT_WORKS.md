# How JARVIS works

A plain walk-through of the whole system for the owner: what happens between "Hey Neko, do X" and the result, which part does
what, and why it is safe. Per-file detail is in `FILE_MAP.md`, per-model detail in `MODELS.md`, day-to-day use in
`HOW_TO_USE.md`, and where each of the owner's 21 items stands in `OWNER_NOTES_STATUS.md`.

## 1. The shape of it

```
 you ──voice / text / icon──▶ N.E.K.O (character, chat, memory, voice)        laptop, 127.0.0.1 only
                                 │ a real task
                                 ▼
                           main_agent (plans, then runs steps under limits)
              ┌───────────────┬──┴─────────────┬───────────────┬────────────────┐
           research       deliverables       desktop          local_team        jarvis_bridge
        (cited, checked)  (decks, PDFs)   (PC control)   (small local models)   (hand to JARVIS)
              │                │                │                 │                  │
              └──── every model call: privacy gateway ───┐        │                  │
                     (private values → placeholders)     │        │                  │
                                                          ▼        ▼                  ▼
                                 planner model (Claude/Dahl)   llama-swap        JARVIS kernel  ◀── Oracle VM, over Tailscale
                                                               (local models)    (policy, PIN approvals,
                                                                                  audit chain, kill switch)
                                                                                        │ permitted jobs only
                                                                                        ▼
                                                                                  laptop node (does the PC work)
```

## 2. One request, step by step

Example: "Hey Neko, find the folder with my resume and open it."

1. **Hearing.** The wake listener (sherpa-onnx keyword spotting) hears "Hey Neko". Silero VAD finds where your sentence ends,
   and Parakeet (English) or IndicConformer (Hindi, Telugu) turns it into text. Audio is never saved.
2. **Deciding what kind of request it is.** N.E.K.O keeps small talk itself and passes real tasks on. Rules and the Verdict
   classifier pick the route; the local decider model (Qwen3-4B-2507) turns a PC request into concrete steps. Vague, negated or
   "X or Y" requests become a question to you instead of a guess.
3. **Planning bigger work.** For several-step work `main_agent` asks the planner model. The call goes through the **privacy
   gateway**, which replaces names, addresses, keys and similar with placeholders before anything leaves the laptop and puts them
   back in the answer. A private task never goes to a public provider; that lane rule lives in the kernel, not in the model.
4. **Acting.** Each PC step becomes a **kernel job**. The kernel classifies it: GREEN runs, YELLOW waits for your PIN in the
   console, RED is refused over HTTP. Banks, wallets and password managers are always refused. Every decision is appended to a
   hash-chained audit log (editing or deleting a line is detectable). The kill switch stops everything.
5. **Doing.** The laptop node runs only what the kernel permitted (UI Automation first, keyboard as a fallback) and checks the
   result on screen before reporting success.
6. **Answering.** The reply is spoken by Kokoro (English) or Piper (Hindi, Telugu), and the Neko icon shows "done" with Open file
   and Show in folder.

## 3. The parts, by job

| Job | Part | Where |
|---|---|---|
| Talking, memory, character | N.E.K.O (the base app) | `app/`, `main_logic/`, `memory/` |
| Planning and running tasks | `main_agent` plugin | `plugin/plugins/main_agent/` |
| PC control and missions | `desktop` plugin, laptop node | `plugin/plugins/desktop/`, `jarvis/node/` |
| Research with checked citations | `research` plugin (MiniCheck claim checker) | `plugin/plugins/research/` |
| Decks and PDF reports | `deliverables` plugin | `plugin/plugins/deliverables/` |
| Small local specialists | `local_team` plugin, llama-swap | `plugin/plugins/local_team/`, `jarvis/local_models/` |
| Authority (policy, approvals, audit, kill switch) | the kernel | `jarvis/kernel/` (runs on the Oracle VM) |
| Privacy before any remote model | privacy gateway | `jarvis/privacy/` |
| Approvals screen, models, connectors | JARVIS console | `jarvis/console/`, `jarvis/ui/` |
| Voice | wake listener, local speech server | `jarvis/wake/`, `jarvis/scripts/local_speech_server.py` |
| Hermes agents on the VM, checked by the kernel | `jarvis_policy` Hermes plugin | `jarvis/hermes/plugins/` |
| JARVIS's personality (card, installer, persona cases) | N.E.K.O character card | `jarvis/persona/` |
| Morning brief, page-watch runner, notifications | openmuse features | `jarvis/muse/` |
| Safety posture check | `jarvis doctor` | `jarvis/tools/doctor.py` |

## 4. The integration build (what is being added)

The owner asked for every outside project to become part of JARVIS rather than a separate app. That work is landing in these
folders; most of it is built and tested but not switched on yet (see `OWNER_NOTES_STATUS.md`):

- `jarvis/supervisor/`: one owner for every process. Starts them in order, checks each listens only on loopback or Tailscale,
  renders their configs without secrets, and on Windows a Job Object ends every child if the supervisor dies.
- `jarvis/kernel/jarvis_kernel/router/`: the OmniRoute/9router features inside the kernel: ordered fallbacks per lane, several
  keys per provider, quotas, a circuit breaker, $0 budgets by default, format translation, and a byte-for-byte path for Claude Code.
- `jarvis/egress/`: the Smokescreen behaviour in Python. Every web request is resolved once, refused if it points at a private,
  Tailscale or cloud-metadata address, and connected to the address that was checked.
- `jarvis/muse/`: openmuse's features without its cloud: page watches with a background runner and a local notice inbox (the phone
  only gets a content-free "new notices" ping), finance CSV, a durable task state machine that never repeats
  an action whose outcome is uncertain, PDF forms filled only after approval, and Gmail/Calendar drafts with no way to send.
- `jarvis/node/jarvis_node/fileops.py`, `filesearch.py`: find, create, move and organise your files with an undo journal; deletes go
  to a JARVIS trash folder, never straight to gone.
- `jarvis/routines/`: goose-style routine recipes with parameters, a tool allow-list and exact replay.

## 5. Why it is safe

- **The kernel is the only authority.** Models suggest; the kernel decides. A model cannot grant itself a permission, lower a risk
  level or approve its own action, and a replayed or edited approval is refused.
- **Secrets are files, never text.** Keys live in `D:\jarvis-secrets` and are read by code at run time only; they are never shown to
  a model, printed, logged or committed. Claude Code is blocked from reading that folder.
- **Everything listens on loopback or Tailscale only.** Nothing is exposed to the internet.
- **Every action is audited** in a hash-chained log whose head is copied off the server.
- **Untrusted text stays data.** Web pages and files can carry instructions for models; research strips them, the decider asks
  instead of guessing, and the planned taint flag will hold actions that follow a web read until you approve.
- **Costs stay at $0** unless you approve an exception (today: the Anthropic API credit until its expiry, capped at $90).
