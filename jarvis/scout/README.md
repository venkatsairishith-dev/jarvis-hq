# Scout: find ready-made tools, skills, repos, models and methods for JARVIS, then integrate them (owner, 2026-10-26)

Owner's rule: do not build from scratch what already exists; find it, vet it, wire it in. Reuse-first is also in CLAUDE.md.

## Where to look (and what each is good for)
| Source | Good for |
|---|---|
| GitHub: topic pages (`computer-use`, `browser-automation`, `mcp-server`, `agent-skills`, `ocr`, `tts`, `asr`), trending, "awesome-*" lists | agents, drivers, MCP servers, skills |
| Hugging Face: trending models, tasks (image-text-to-text, ASR, TTS, OCR, translation), `GGUF` / `ONNX` filters | local specialist models |
| Official MCP registry and the Anthropic / OpenAI / Google skill and plugin directories | tools and skills with a manifest |
| arXiv + papers-with-code lists (GUI agents, UI grounding, OSWorld / WebArena / AndroidWorld leaderboards) | methods and which models score |
| r/LocalLLaMA, Hacker News, vendor release notes / changelogs | what actually runs on small hardware, real complaints |
| Social-media reels the owner saves | leads only, never proof |

## The vetting checklist (every candidate; unknown = say "not found", never guess)
1. **Source read today**: the vendor / repo page itself, URL and date quoted. Social posts, snippets and AI summaries are leads.
2. **Licence**: SPDX id for code AND weights, commercial use. GPL / NC / "community" licences are flagged, not silently accepted.
3. **Maintained**: last release/commit, open security advisories.
4. **Runs where JARVIS runs**: local at run time (no Claude calls ever, no mandatory cloud), fits D50 (20% CPU, 6554 MB RAM, 2048 MB VRAM, D: only) or runs on a provider chain behind the privacy gateway.
5. **Safety**: telemetry, network calls, install scripts, what it can touch; prompt-injection exposure; secrets handling. Supply chain: pin version + hash, own venv on D:.
6. **Beats what we have** on OUR task (or fills a gap): name the current component it replaces or extends and the measured/claimed difference.
7. **Integration plan**: files touched, kernel gate class (A/B/C) if it acts, tests with doubles, what only the laptop can verify.

## Outputs
`findings/YYYY-MM-DD.md` (ranked table: adopt / benchmark / idea / skip + evidence) → adopt items get an integration (code + tests + `tools/registry.json` entry + `01_RESEARCH_AUDIT.md` note + a decision). The protected servers (hermes-agent-vm, hermes-agent-vm-2), secrets and the kernel safety lists are not touched by a scouted tool.
