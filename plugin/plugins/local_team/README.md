# Local Team

Small specialist models on this PC share a task. A public reasoning model can advise on hard tasks, but it never sees
private values.

| Entry | What it does | Leaves the PC? |
|---|---|---|
| `ask_local` | One local specialist answers: `code` (Qwen2.5-Coder 1.5B), `math` (Qwen2.5-Math 1.5B), `general` (Ling-3.0-tiny, 1.3B active). `auto` picks one by keyword rules, then by Ling as a one-word router (11 of 12 right in a test; SmolLM2-360M got 5). | No |
| `solve_with_team` | Dahl's MiniMax writes a short plan through the privacy gateway (it sees `[PASSWORD_1]`, not the password), then the local specialist writes the answer with the real values. With no gateway it skips the adviser and the local model reasons on its own. | Only the redacted text |
| `team_status` | Which local models are available or loaded, and whether the privacy gateway is up. | No |

The models are served by llama-swap at `http://127.0.0.1:8080/v1` (`jarvis/local_models/llama-swap.yaml`,
started by `jarvis/scripts/local-models.ps1`). A model loads on first use and unloads after 5 idle minutes.

**Safety rule (core.py, tested):** the adviser call is refused unless N.E.K.O's agent model URL is the privacy gateway
(127.0.0.1:48950).

Tests: `pytest plugin/plugins/local_team/tests` (no model needed).
