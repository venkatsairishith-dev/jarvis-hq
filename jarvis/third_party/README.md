# Third-party parts

Outside code that JARVIS carries in the repository (a git subtree, squashed). Each one is pinned to an exact upstream commit.

| Part | Path | Upstream | Pin | Licence |
|---|---|---|---|---|
| N.E.K.O.-Tauri (desktop shell) | `jarvis/desktop/shell` | https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri | `b7b4fd70692489ca94457bc95f968e36ed61c4c4` (branch `main`, 2026-07-29) | Apache-2.0 |

## N.E.K.O.-Tauri

- **What it is:** the official open-source Tauri 2 shell for N.E.K.O. It shows the 48911 page in a transparent,
  always-on-top window. It also has a click-through bridge that knows the Live2D, VRM, MMD and PNGTuber managers.
  Upstream calls it experimental. Its own lock has tauri 2.11.2, tao 0.35.3 and wry 0.55.1 (`src-tauri/Cargo.lock`).
- **Why:** it will give JARVIS a tray, a global push-to-talk key (press and release), a single instance and a
  click-through fail-safe. The plan is in `jarvis/docs/neko/evidence/01-assistants-and-desktop-shell.md` (sections
  5.3, 6 and 8). The patches are listed in `PATCHES.md` (still in jarvis/third_party/; they apply to jarvis/desktop/shell).
- **Pin:** the default-branch HEAD on 2026-10-17. Evidence 01 names no commit and says HEAD dates from 2026-07-29,
  which matches.
- **Licence check:** `LICENSE` in the checkout is the Apache License, Version 2.0 (sha256 `0d99b64baf1323c3a7f70a28075b14d573df6d773aa2d33d827053cc775062d8`).
  `NOTICE` credits "Project N.E.K.O. Team" and "Hongzhi Wen". Keep both files with any copy.

## Status

Added and pinned only. It has **not been built or run**. Rust, the MSVC Build Tools and WebView2 are a Windows
laptop job. Put `CARGO_HOME`, `RUSTUP_HOME` and `CARGO_TARGET_DIR` on D: (evidence 01, section 5.3). The Win10
transparency soak (Tauri #15947), the idle-CPU check and the push-to-talk hold check wait for the laptop.

## Update it (since 2026-10-22 a subtree, no longer a submodule)

```
git subtree pull --prefix=jarvis/desktop/shell --squash https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri <new-commit>
```

After an update, check that `LICENSE` is still Apache-2.0 and write the new commit in the table above.
`jarvis/tools/test_python_deps.py` fails if the two differ. Re-apply the patches in `PATCHES.md` (still in jarvis/third_party/; they apply to jarvis/desktop/shell), and offer them
upstream as pull requests rather than keeping a fork.

## Hermes Agent (pinned, NOT vendored: a deliberate deviation from the plan, 2026-10-22)

The integration plan (P2 step 4) says to carry Hermes as a pristine subtree. Measured on 2026-10-22: tag `v0.22.2`
(commit `d923a5eb487a17c0a68801bba9ef0f56c81040a8`, MIT, "Copyright (c) 2025 Nous Research") is **204 MB in 18,605 files**.
A subtree would add all of that to this repository's history (and to every clone and push), so the pin is recorded here
instead and Hermes is installed from the tag into its own Python 3.14 venv on the VM or laptop (all 45 runtime
dependencies carry `python_version >= '3.14'` markers, so it cannot share the 3.11 environment). JARVIS code stays outside
Hermes, as Hermes plugins under `jarvis/hermes/plugins/` (`jarvis_memory`, `jarvis_policy`), which is what the plan wanted anyway.
Revisit if the owner prefers the subtree: `git subtree add --prefix=jarvis/third_party/hermes-agent --squash <url> v0.22.2`.
