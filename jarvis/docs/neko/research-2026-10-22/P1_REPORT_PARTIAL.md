# P1 report (partial), 2026-10-22

Branch: claude/implementation-plan-designs-00v4r6. Nothing merged to main. Oracle untouched in this phase; Anthropic API credit used only by the SDK builder (ledger: D:\jarvis\outputs\builder-spend.json, $0.15 at time of writing).

## Done (tests pass; run on this Windows laptop)

| Plan step | What | Evidence |
|---|---|---|
| 2 | `jarvis/tools/licence_gate.py` + tests | 6 passed; real registry passes in personal and shared profile (0 shipped entries yet, 21 licences still "unknown": `--report` lists them) |
| 3 | `jarvis/tools/make_notices.py` + tests (built by the SDK builder, reviewed by me) | 5 passed (11 with the gate's tests) |
| 4 (part) | `jarvis/supervisor/binds.py`: refuses 0.0.0.0, ::, public and unlisted addresses | 21 supervisor tests passed; acceptance "a deliberate 0.0.0.0 bind fails the assertion" met at unit level |
| 5 | `jarvis/supervisor/boot_token.py` (fresh 64-hex token per boot, overwritten each boot) and `render.py` (refuses secret-looking values, allows only `file:` references, `scan_dir` stand-in for a gitleaks scan) | covered by the same 21 tests; secrets guard hook was respected (fake secrets built at run time in tests) |
| 8 | Hermes Python verification, check 1: pinned tag v0.22.2 `pyproject.toml` has `requires-python >=3.11,<3.15` but all 45 runtime dependencies carry `python_version >= '3.14'` markers | confirms: on 3.11-3.13 Hermes installs with no dependencies. Design stands: Hermes in its own 3.14 venv, supervised. Checks 2 (scratch 3.11 install) and 3 (`uv lock` as workspace member) were **not run**; check 1 already decides the design. |
| 9 | `cdp_relay.py` added to SAFETY_COMPONENTS; `router`, `egress`, `supervisor` added to JARVIS_SAFETY_FILES (RED only inside JARVIS paths, so another project's `router/` is not caught) | new test in `test_caretaker_guard.py`; caretaker-guard and policy-mutation tests 25 passed; full kernel suite 1057 tests OK, 60 skipped |
| (builder) | `jarvis/sdk_builder/run_builder.py`: Agent SDK runner with expiry (2026-10-31), model allow-list (sonnet-5-5, haiku-5-5), spend ledger cap, worktree-only paths, no secrets path, no git push | 5 policy tests passed; run 1 succeeded ($0.15, 10 turns) |

Note for deployment: the SAFETY_COMPONENTS change is in the repo only. The kernel on hermes-agent-vm still runs the old policy until it is deployed (a deploy needs the owner's go because it changes the live kernel).

## Not done in P1

- Step 1: registry fields are defined by the gate but not populated for any entry.
- Step 4: the rest of the supervisor (manifest, health, Windows Job Object, systemd rendering). Acceptance "starting the app launches one process tree; killing the shell leaves zero orphans" is untested.
- Step 6: audit record with component version and sha256 (touches the live kernel audit chain; needs care and a deploy).
- Step 7: converting the neko-tauri submodule to a subtree.

## Could not be done / blocked

- The auto-mode classifier blocked builder run 2 (it was allowed for run 1). Not retried or bypassed; the renderer task was written by hand.
- OmniRoute bind change (separate item) stays unapplied by decision: tailscale0-only firewall, plan retires it in P3.

## Credit

SDK builder spend recorded: $0.15 of the 2026-10-31 exception (cap on this tool $75; owner stop at $90 org spend including other usage).
