# Working on JARVIS

This is a private repository; the owner decides what is merged. The rules every session follows are in `jarvis/CLAUDE.md`.

- Use existing, maintained projects before writing code; justify any custom code in `jarvis/docs/01_RESEARCH_AUDIT.md`.
- Never put a secret in the repository, a log or a message. Secrets live in `D:\jarvis-secrets` (Windows) or `~/.jarvis/secrets.env`.
- Every change comes with a test that fails on the old code. Run the suite you touched (see the README) and compare with the
  result before your change; say what could not be tested.
- Only the kernel decides what may run. Nothing in the face, a plugin or an agent may approve an action, change policy or lift the kill switch.
- Keep documents short: at most five files in `jarvis/docs/` (`01` to `05`); dated reports go to `jarvis/docs/reports/`.
- Regenerate `jarvis/docs/reports/FILE_MAP.md` and `MODELS.md` with `python jarvis/tools/make_docs.py` after adding or renaming files.
- The face (`jarvis/face`) has its own rules: see `jarvis/face/README.md` and `SECURITY.md`; `jarvis/tools/test_face_guard.py` must keep passing.
