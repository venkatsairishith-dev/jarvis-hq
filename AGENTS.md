# Start here

This repo's memory is plain markdown, not a service: nothing to run, nothing to authenticate to, readable by any
coding tool that opens this repo (Claude Code, Codex, Antigravity, a plain editor).

1. **`jarvis/CLAUDE.md`** — the rules every session follows. Read this first.
2. **`jarvis/docs/03_DECISIONS.md`** — every decision made so far, dated, in the owner's own words (D1 onward;
   newest entries are at the bottom). This is "what happened and why."
3. **`jarvis/docs/01_RESEARCH_AUDIT.md`** — why each piece of custom code exists instead of an off-the-shelf tool.
4. **`jarvis/evals/reliability/results/STATUS.md`** — live progress from the real laptop: what passed, what didn't,
   what's still open.
5. **`jarvis/scout/findings/`** — dated sweeps of ready-made tools considered for reuse.
6. **`README.MD`** (repo root) — the map of what's where.

Update `03_DECISIONS.md` (append, never rewrite history) when you make a decision worth remembering, and keep
`README.MD` / `jarvis/docs/reports/FILE_MAP.md` current when you add files (`python -m jarvis.tools.make_docs`).
Nothing here is a substitute for reading the actual code; it's the "why," not the "what."
