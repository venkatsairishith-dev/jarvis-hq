# Vendored: Hermes Agent `skills_guard.py`

- **What:** the static scanner Hermes Agent runs on skills before install (`tools/skills_guard.py`, scanner version `skills-guard-v10`).
- **Source:** https://github.com/NousResearch/hermes-agent, tag `v0.22.2`, commit `d923a5eb487a17c0a68801bba9ef0f56c81040a8`.
- **Licence:** MIT, "Copyright (c) 2025 Nous Research" (`LICENSE` in this folder, copied unchanged).
- **File:** `skills_guard.py` is copied **verbatim**, sha256 `2b39ae73585709c51af62cbf8c3f23c5330ca1e297ac01453e9328996e281562`. Local modifications: none.
- **Why vendored:** the plan (P5 step 2) wants the scanner to run without the Hermes venv (Hermes needs Python 3.14; JARVIS runs 3.11). The file imports only the
  standard library (`re, fnmatch, hashlib, json, contextlib, dataclasses, datetime, pathlib, typing`).
- **Reviewed 2026-10-22 before vendoring:** no `eval`/`exec`/`open`/subprocess/network calls (every "dangerous" word in the file is inside a detection regex, i.e. data);
  its only write is the optional scan cache in `scan_skill_cached`.
- **Rule for JARVIS callers:** call `scan_skill_cached(..., cache_dir=<a folder you choose>)`. Its default cache folder is `<skill's parent>/.scan-cache`, which would write
  next to the skills; always pass `cache_dir`.
- **Tests:** `jarvis/skills/tests/test_hermes_skills_guard*.py` are Hermes's own tests with imports pointed at this path and three small changes marked `JARVIS port`
  (explicit UTF-8 on a Windows write, a symlink-privilege skip, and a skip for the un-vendored `plugin_guard`). `test_vendored_guard_acceptance.py` is the JARVIS acceptance test.
- **Updating:** replace the file from a newer tag, re-run the tests, update the sha256 and tag here. Never edit `skills_guard.py` in place; put JARVIS logic in `skillgate.py`.
