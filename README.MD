# JARVIS

The owner's personal AI system: a face (avatar, chat, voice), a kernel that decides what may run, agents that do the work, and
local models and services that keep private data on the owner's PC. This repository is private
(github.com/venkatsairishith-dev/jarvis-hq).

```
the face (jarvis/face)  --chat-->  privacy gateway :48950  --> model provider (private values replaced)
  avatar, chat, voice   --voice->  local speech server :48930 (speech to text, text to speech)
                        --tools->  tool server :48970 (jarvis/bridge/face_tools.py)
                                       |-- jarvis_do, jarvis_task_result, ...  --> JARVIS console :8799 --> kernel (policy, PIN, audit)
                                       '-- <plugin>__<entry>   the plugins in jarvis/plugins (research, documents, desktop, ...)
```

The face decides nothing. Every action goes to the kernel, which checks policy, asks the owner for the PIN when a step is risky,
writes the audit log and honours the kill switch. Approvals are given in the JARVIS console, never in the face.

## Where things are

| Folder | What it is |
|---|---|
| `jarvis/face` | The face: Project AIRI's desktop app (Electron, Vue; Live2D, VRM, MMD), imported as JARVIS's own code and hardened. `README.md` lists what was changed, `SECURITY.md` is its security policy. |
| `jarvis/pluginapi` | The plugin API and host: loads `jarvis/plugins/*/plugin.toml`, runs lifecycle and timers, routes calls between plugins, offers every entry as an MCP tool. |
| `jarvis/plugins` | `main_agent` (plan, then run), `jarvis_bridge`, `desktop`, `research`, `deliverables`, `local_team`, `doc_tools`, `learn_vault`, `workbench`, `web_search`. |
| `jarvis/bridge` | The face's tool server and the read-only views. |
| `jarvis/kernel`, `jarvis/node`, `jarvis/console`, `jarvis/ui` | The kernel (the only authority), the laptop node, the owner console and its UI. |
| `jarvis/privacy`, `jarvis/egress` | The privacy gateway (redaction) and the outbound-request guard. |
| `jarvis/lab`, `jarvis/hermes`, `jarvis/hub`, `jarvis/coding` | The lab agents, Hermes plugins, the hub and the coding tools. |
| `jarvis/evals`, `jarvis/bench` | The benchmark (v2) and the evaluation suites, including `evals/reliability` (the 90% gate, `results/STATUS.md`) and `evals/ui` (the face's screenshot harness). |
| `jarvis/scout` | How JARVIS looks for ready-made tools/models/methods to reuse before building custom code; `scout/findings/*.md` are dated sweeps. |
| `jarvis/muse` | JARVIS's own background worker: briefs, ideas, finance, a heartbeat. |
| `jarvis/docs` | The five main documents (`01_RESEARCH_AUDIT.md` to `05_OPERATIONS.md`) and `reports/` (dated reports and evidence). |

Start reading with `jarvis/docs/05_OPERATIONS.md` ("Running JARVIS") and `jarvis/CLAUDE.md` (the rules every session follows).

## Run it (Windows laptop)

```
powershell -File jarvis\scripts\jarvis-start.ps1        # privacy gateway, speech, console, tool server, then the face
powershell -File jarvis\scripts\jarvis-stop.ps1
```

The first setup (Python environment, the face's build, moving data from the old layout) is in `05_OPERATIONS.md`.

## Tests

Each plugin suite runs on its own (several plugins have a `test_smoke.py`):

```
python -m pytest jarvis/plugins/main_agent/tests -q
python -m pytest jarvis/pluginapi jarvis/bridge jarvis/tools jarvis/persona jarvis/supervisor -q
python -m pytest jarvis/console/tests jarvis/caretaker/tests -q
cd jarvis/kernel && python -m unittest discover -s tests        # the kernel
python jarvis/evals/quality95/bench/bench.py                    # benchmark v2 (cases that need Windows or the owner's hardware are deferred)
pnpm -F @proj-airi/stage-tamagotchi exec vitest run             # the face (from jarvis/face)
```

Linux test image: `jarvis/scripts/test-container/`. Python pins: `jarvis/tools/requirements/`.

## Status

The face runs on the owner's real laptop (Windows 10 Pro). What was measured there, and what still needs a live check, is
in `jarvis/evals/reliability/results/STATUS.md` (updated after every phase) and the decision log `jarvis/docs/03_DECISIONS.md`
(D1 onward; read the newest entries first). As of D1-D80: none of the 15 reliability capabilities is confirmed at the 90%
bar yet, mainly because of provider capacity and features still awaiting a live run; the gate is honest about this rather
than claiming otherwise. Nothing here is claimed production-ready until `STATUS.md` says a capability passed at 90%+ with
zero unsafe actions. GitHub Actions is off for this repository on purpose; pushes happen as the owner's sessions work.

## Licence and credits

JARVIS's own code is the owner's. The face is built from Project AIRI (MIT, `jarvis/face/LICENSE`). Two pieces adapted from the
Apache-2.0 project N.E.K.O (the web search page parsers and the recall renderer) are credited in `NOTICE` and in the files, and
`LICENSE` (Apache-2.0) is kept for them. Other third-party components, their licences and pins are in
`jarvis/tools/registry.json`.
