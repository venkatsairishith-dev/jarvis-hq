# Third-party parts

Outside code that JARVIS carries or pins. Each one is pinned to an exact upstream commit.

| Part | Path | Upstream | Pin | Licence |
|---|---|---|---|---|
| Project AIRI (the face; imported as JARVIS's own code, not a subtree) | `jarvis/face` | https://github.com/moeru-ai/airi | tag `v0.12.0`, `3b0672e3668bd1ade1210e579818f379e4f8a224` | MIT (details in `jarvis/face/README.md`) |
| Hermes Agent (pinned, not vendored, see below) | installed from the tag | https://github.com/NousResearch/hermes-agent | tag `v0.22.2`, `d923a5eb487a17c0a68801bba9ef0f56c81040a8` | MIT |

Code adapted from the Apache-2.0 project N.E.K.O (the web search page parsers and the recall renderer) is credited in `NOTICE`
and in the header of each file.

## Hermes Agent (pinned, NOT vendored: a deliberate deviation from the plan, 2026-10-22)

The integration plan (P2 step 4) says to carry Hermes as a pristine subtree. Measured on 2026-10-22: tag `v0.22.2`
(commit `d923a5eb487a17c0a68801bba9ef0f56c81040a8`, MIT, "Copyright (c) 2025 Nous Research") is **204 MB in 18,605 files**.
A subtree would add all of that to this repository's history (and to every clone and push), so the pin is recorded here
instead and Hermes is installed from the tag into its own Python 3.14 venv on the VM or laptop (all 45 runtime
dependencies carry `python_version >= '3.14'` markers, so it cannot share the 3.11 environment). JARVIS code stays outside
Hermes, as Hermes plugins under `jarvis/hermes/plugins/` (`jarvis_memory`, `jarvis_policy`), which is what the plan wanted anyway.
Revisit if the owner prefers the subtree: `git subtree add --prefix=jarvis/third_party/hermes-agent --squash <url> v0.22.2`.
