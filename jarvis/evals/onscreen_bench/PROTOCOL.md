# On-screen model benchmark (owner, 2026-10-25)

"Benchmark them with on-screen tests, not background." Every model JARVIS can use gets the same plain-language goals typed into the
face chat while the owner can watch the screen. No script drives the model. The result is read off the screen and the face's own log.

## Candidates
Pin one at a time with a one-line file `D:\jarvis-secrets\gateway-pin.txt` (not a secret; the gateway reads it per request, no restart):

| Pin line | What it tests |
|---|---|
| `dahl` (model as the face sends it) | Dahl GLM, then `dahl <minimax id>` for MiniMax (both MoE) |
| `groq` | gpt-oss-120b (MoE), 8K tokens/min cap: expect skips on big prompts |
| `cerebras` | gpt-oss-120b (MoE); only if `providers\cerebras.key` exists |
| `local` | Ling-3.0-tiny-heretic (MoE, local-chat); then `local <alias>` for Granite abliterated, once installed |

An empty or missing file means the normal chain. Delete the file when finished. A pinned run has NO failover, so a failure is that model's own.

## Goals (a clean chat for each, the same wording for every model)
1. Open Notepad and write a shopping list: milk, eggs, bread, rice.
2. What is on my screen right now?
3. Make a 3-slide deck about our week and show it to me. (expect the deliverables tool and the Preview panel)
4. Read the invoice `<fixture file name under the test fixtures>` and tell me the total. (a made-up fixture, never the owner's real document)
5. Two things at once: open Calculator and also tell me the weather in Paris. (several tool calls in one turn)

On-screen permission dialogs (Chrome extension approval and the like) JARVIS clicks itself. The kernel's PIN approvals for consequential
actions (send, delete, spend, install) stay the owner's (D27).

## Score per model and goal
done (looked at the screen: 1 or 0), tool rounds, wall seconds, errors (HTTP code, malformed tool JSON), wrong tool picked, invented facts, refusals.
Write one row per run into `results/onscreen-<date>.json`.

## Pick
Highest goals done; then fewest errors; then fastest. The winner goes first in the private and public provider order
(`jarvis/privacy/providers.json`, `jarvis/config/providers.json`); the next best stays as the failover, the local model stays last.
