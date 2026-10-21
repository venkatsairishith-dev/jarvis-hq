# Runbook: Workbench, learned skills, research sources (2026-10-20)

Secrets are never written here; `<placeholders>` stand for values you keep in your own secret store.

## Components and where they run
| Component | Where | Starts with | Notes |
|---|---|---|---|
| Workbench plugin (`plugin/plugins/workbench`) | inside N.E.K.O's plugin host (laptop) | N.E.K.O start (`auto_start = true`) | No own service or port. Data folder: `WORKBENCH_DIR`, default `D:\jarvis\workbench`. |
| Research plugin + GitHub source adapter (`plugin/plugins/research/sources.py`) | N.E.K.O plugin host | N.E.K.O start | Public GitHub API, no token; 60 API calls an hour from one IP. |
| Local model for Workbench | llama-swap on the laptop, `http://127.0.0.1:8080/v1` | `jarvis\scripts\local-models.ps1` | `WORKBENCH_MODEL` (default `decider-qwen3-4b-2507`), `WORKBENCH_MODEL_CTX` (default 2048, the llama-swap setting). |
| Speech for audio intake | `jarvis\scripts\local_speech_server.py`, `http://127.0.0.1:48930` | `neko-start.ps1` | en / hi / te models (sherpa-onnx). Non-WAV audio needs ffmpeg (`WORKBENCH_FFMPEG` or PATH). |
| Kernel, console, approvals, kill switch | laptop / server as before | unchanged | Workbench reaches JARVIS only through `jarvis_bridge`. |

## Health checks
- Plugin: N.E.K.O plugin manager -> Workbench panel loads; the Learn tab shows the local model status.
- Local model: `curl --noproxy "*" http://127.0.0.1:8080/v1/models` returns a model list.
- Speech: `curl --noproxy "*" http://127.0.0.1:48930/health` (or the speech server's readiness route) answers.
- JARVIS gate: `jarvis_bridge` "Is JARVIS ready?" says ready; while the kill switch is on, Workbench runs nothing.
- Metadata snapshot: `python jarvis/scripts/check_plugin_metadata.py workbench main_agent research` prints OK.

## Tests (Linux test image, no network needed except where noted)
```
podman run --rm --network none --userns=keep-id --cpus=2 -e HOME=/tmp -v ~/hq-neko:/src:O neko-test \
  sh -c "cd /src && python -m pytest plugin/plugins/workbench/tests plugin/plugins/research/tests plugin/plugins/main_agent/tests -q -p no:cacheprovider"
# Harness² compatibility (needs a checkout of google-research/harness2 at 1cfe254e):
HARNESS2_SRC=<checkout> python -m pytest plugin/plugins/workbench/tests/test_learning.py -k harness2
```
The research tests need `RESEARCH_NLI_DIR` for the claim-checker tests (otherwise they skip).

## Inspecting logs and records safely
- Project log: `<WORKBENCH_DIR>/projects/<id>/log.jsonl` holds events and counts only, scrubbed; no file contents.
- Learning records: `<WORKBENCH_DIR>/library/learning/learn-*.json` (stages, evidence table, ledger).
- Skill usage: `<WORKBENCH_DIR>/library/workflows/<name>/usage.jsonl`; lifecycle: `state.json`.
- Prompt runs: `<prompts dir>/<id>/index.json` -> `executions` (state "submitted" / "refused" / "failed", `verified: false`
  until the result is checked).
- Never paste a client's `sources/` files or `requirements.json` into a chat with a public model.

## Disabling things
| What | How | Effect |
|---|---|---|
| One learned skill | panel -> Learn -> Disable (or `disable_skill`) | refused for sandbox reuse and real runs; stays stored |
| A skill that started failing | panel -> Re-test (`regress_skill`) | a failure quarantines it; enabling again needs a passing re-test |
| A bad skill version | panel -> `rollback_skill` (owner confirm) | "current" moves to an older validated version |
| GitHub lookups in learning | call `learn_task` with `research: false` | no GitHub or web research |
| The whole Workbench | N.E.K.O plugin manager -> disable Workbench, or `plugin.toml` `[plugin_runtime] enabled = false` | entries and panel gone; data folder untouched |
| Everything JARVIS runs | the kill switch (console) | Workbench execution entries refuse ("nothing was run") |

## Rolling back the code
Everything from this pass is uncommitted in `remediation-2026-10-18`. To remove it before a commit:
- New files: `plugin/plugins/workbench/` (whole folder), `plugin/plugins/research/sources.py`, `plugin/plugins/research/tests/test_sources.py`,
  `jarvis/docs/neko/WORKBENCH_2026-10-20.md`, `MASTER_INTEGRATION_REPORT_2026-10-20.md`, this runbook.
- Small edits to undo by hand (each is one block): `plugin/plugins/research/fetch.py` (`accept_json` parameter),
  `plugin/plugins/main_agent/planner.py` (`PLANNER_EXCLUDED_ENTRIES`), `plugin/plugins/main_agent/outcome.py` (the
  `workbench` schemas), `plugin/plugins/main_agent/tests/test_outcome_registry.py` (workbench in `OURS` + examples),
  `plugin/plugins/main_agent/TOOL_INVENTORY.md`, `jarvis/docs/01_RESEARCH_AUDIT.md` (Workbench section).
- Then regenerate the main_agent and research `plugin.meta.json` with the project's generator.
Data in `WORKBENCH_DIR` is never deleted by any of this.

## Laptop-only steps (cannot be done on the Linux test server)
1. Start N.E.K.O; open Workbench; confirm the three tabs load and the Learn tab shows "local model: ready".
2. Client audio: put a short WAV (or MP3 with ffmpeg installed) of a synthetic call in Documents; create a project, add it
   with language `en` (then `te`, then `hi`); Analyze. Expected: segments with `00:00:00`-style times; requirements cite them.
3. Prompt run: generate a prompt, Run -> confirm; JARVIS console shows the command; result checked with task_result.
4. Kill switch on -> Run again: the panel says "refused ... nothing was run".
5. Decks: a presentation deliverable through `deliverables:make_presentation` (OfficeCLI + LibreOffice are on the laptop).
6. Learned workflow real run: `run_learned_workflow` on a real (non-client) file; then `check_workflow_output` on the file
   JARVIS reports.

## Operations that need your approval
- Installing ffmpeg or openpyxl on the laptop; downloading any speech model.
- `set_provider_policy` to let one client project use an external AI provider.
- Running any prompt or learned workflow for real; enabling or rolling back a skill; importing a SKILL.md.
- Any change to kernel memory (citations, purge with tombstones): a schema migration of the kernel database.
- Bumping `jarvis/skills/skillgate.py`'s pinned hermes-agent commit.
