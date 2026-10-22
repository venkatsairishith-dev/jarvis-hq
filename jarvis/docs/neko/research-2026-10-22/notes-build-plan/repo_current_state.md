# Current state of the owner's repositories (jarvis-hq, jarvis-vault), mapped to the owner's requirements

Method: read-only. Branch list via the GitHub MCP tool; then a read-only `git clone` of both repos into the session scratchpad (no branch, commit, push, issue or comment was made on GitHub) to compare branches and read files at the tip of each branch. File paths below are repo-relative. Unless marked otherwise, file citations point at the `remediation-2026-10-18` tip (c50dc2f1), which contains every other non-dependabot branch (see Q1). No secret values were read or copied; provider files were printed with key fields redacted.

URL shorthand used in citations:
- R = https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/
- M = https://github.com/venkatsairishith-dev/jarvis-hq/blob/main/
- C = https://github.com/venkatsairishith-dev/jarvis-hq/commit/
- V = https://github.com/venkatsairishith-dev/jarvis-vault/blob/main/

## Q1. Branches: what is merged into main, what is ahead, what remediation-2026-10-18 contains

### Takeaway
Nothing has been merged into main since 2026-10-17 (main tip 2e9d337b). The three working branches form one straight line: combine-tools (5 ahead) ⊂ laptop-dev (32 ahead) ⊂ remediation-2026-10-18 (49 ahead), all with merge-base = main tip and 0 behind. remediation-2026-10-18 is the most complete tree (578 files changed vs main, +254,837/-1,972 lines). The four dependabot branches are 1 ahead / 1 behind main and unmerged.

### Cited Findings
- main tip 2e9d337b (2026-10-17 18:35): "Docs: README header, core docs, Neko docs and plugin READMEs brought up to the 2026-10-17 state" — [C 2e9d337b](https://github.com/venkatsairishith-dev/jarvis-hq/commit/2e9d337ba349db58748e3b4b9c65bf407ab5f7f1)
- Branch tips (GitHub list_branches): combine-tools 88ec49a2, laptop-dev 1c28d3a3, remediation-2026-10-18 c50dc2f1, main 2e9d337b; dependabot: docs/vue bump 6d9cd977, frontend/plugin-manager 147867f0, frontend/react-neko-chat (katex) 4a6c2092, uv "253 updates" 726f181a — [jarvis-hq branches](https://github.com/venkatsairishith-dev/jarvis-hq/branches)
- `git merge-base` of each working branch with main = 2e9d337b; `rev-list --left-right --count`: combine-tools 5/0, laptop-dev 32/0, remediation 49/0. `merge-base --is-ancestor`: combine-tools is an ancestor of laptop-dev; laptop-dev is an ancestor of remediation. — [compare main...remediation-2026-10-18](https://github.com/venkatsairishith-dev/jarvis-hq/compare/main...remediation-2026-10-18)
- combine-tools (5 commits, 2026-10-17): phase 1 tool inventory of 88 projects; phase 2 one lock for every download, verified fetcher, locked Python groups, N.E.K.O.-Tauri pinned; phase 3 MiniCheck claim checker, llama.cpp b11714, idempotency keys, webmcp deny, BM25 memory; phase 4 full run 834/0/0/26 of 860, scorecard v2 9.81; phase 5 Caretaker — [C 88ec49a2](https://github.com/venkatsairishith-dev/jarvis-hq/commit/88ec49a2ba056f85ae5bbcfc65509852b58ad4f0)
- laptop-dev adds (2026-10-21/22): "Chat question card for held JARVIS tasks, and a recommend-only model selector (phase 1)" (5c90a450); "Remediation passes 1-4, Workbench, audits, evidence, benchmark results and reports (2026-10-18 to 2026-10-20)" (3b0d2655, squashed); Windows test fixes; retention secure_delete; "openrouter-private uses gemma-4-31b-it:free" (47023e08); merge of remediation d071bb7e (rate-limit retry); dev-test/dev-status scripts; "Start the Jev decision model (OpenJev Verdict) natively" (6395e98f); "Desktop decider refuses 'create a folder' plainly" (1c28d3a3) — [C 1c28d3a3](https://github.com/venkatsairishith-dev/jarvis-hq/commit/1c28d3a3f760a3d194d6228960e9c9ba47d5a237)
- remediation-2026-10-18 adds on top of laptop-dev (17 commits, 2026-10-21/22): kernel token only to loopback/Tailscale/https hosts (f587583a); kernel deploy checklist (ecdc7747); wake listener ignores commands under 0.4 s (3b280da9); "Roadmap: from this project to an actual JARVIS" (83cce189); main_agent holds a plugin step acting on untrusted content until owner approves (1a8b445c); desktop baseline runner, 10 plain requests (066a4957); visible testing launcher (2546acf9); planner knows date/time (8696a417); console health "Not set up" (71154eaa); Hermes 0.22 upgrade report + compat checker (4a319874); Learning loop layers 1-3: episode log/hints/stats (809de440), bounded self-correction (4843df8c), stage examples mined from successful PC tasks through the skill gate (c50dc2f1) — [C c50dc2f1](https://github.com/venkatsairishith-dev/jarvis-hq/commit/c50dc2f15dcd8b5dc379fc1ef148f27f9d991072)
- Existence matrix (git cat-file per branch): only on remediation: `plugin/plugins/main_agent/episodes.py`, `jarvis/evals/neko95/task_baseline.py`, `jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md`, `jarvis/hermes/check_hermes_compat.py`. On laptop-dev+remediation (not main/combine-tools): `plugin/plugins/workbench/`, `jarvis/kernel/jarvis_kernel/model_select.py`, `projectfix.py`, `visible.py`, `jarvis/lab/lab_isolation.py`, `plugin/plugins/main_agent/input_request.py`, `jarvis/scripts/start-verdict.ps1`, `dev-test.ps1`. On combine-tools and later (not main): `jarvis/caretaker/`, `jarvis/tools/registry.json`, `jarvis/kernel/jarvis_kernel/idempotency.py`, `jarvis/third_party/PATCHES.md`. On all incl. main: `jarvis/kernel/jarvis_kernel/agents.py`, `jarvis/widget/neko.html` — [R tree](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis)
- Directories with most changed files main→remediation: jarvis/kernel/tests (50), evals results (25), plugin/plugins/workbench (24), jarvis/scripts (24), jarvis/kernel/jarvis_kernel (24), jarvis/docs/neko (18), jarvis/console/tests (17), desktop tests (16), main_agent (11), jarvis/node/jarvis_node (10), jarvis/lab/tests (10), jarvis/caretaker (9) — [compare](https://github.com/venkatsairishith-dev/jarvis-hq/compare/main...remediation-2026-10-18)
- Commits on laptop-dev/remediation are co-authored by "Claude Sonnet 5.5"; main's by "Claude Opus 5.5" — [C 6395e98f](https://github.com/venkatsairishith-dev/jarvis-hq/commit/6395e98f)

### Inferences
- A plan should treat `remediation-2026-10-18` as the effective baseline and include a first step "merge remediation (fast-forward possible, 0 behind) into main" — a fast-forward is possible because main has no commits the branch lacks. The PR would exceed 20 counted files, so CONTRIBUTING's "Why Not Split" section applies (see Q19).
- Dependabot branches are each 1 behind main (based on main's parent), so they need a rebase before merging; the uv one (253 updates) is high risk.

### Gaps
- No open PRs were checked (not requested); whether the owner intends remediation → main via PR or direct push is not recorded in the repo.

## Q2. Requirement (1): admin/PC control, file search, folder creation

### Takeaway
PC control exists as fixed, kernel-authorized job kinds (open app/file/folder, window ops, typing, keys, read-only checks, repair) — but `jarvis/node/jarvis_node/osctl` does NOT exist on any branch, there is no create-file/create-folder capability (the decider now explicitly refuses it), no general file-search tool beyond name-matching inside known folders, and admin/elevation exists only inside repair (UAC).

### Cited Findings
- `jarvis/node/jarvis_node/` contains: agent.py, browser.py, browserext.py, collector.py, connectors.py, control.py, diag.py, guardproxy.py, jobs.py, mcpclient.py, media.py, paths.py, pcopen.py, repair.py, runtime.py, sensitive.py, ui.py, vision.py, watch.py, winbackend.py, windows.py — no `osctl` (0 osctl files on main, combine-tools, laptop-dev, remediation) — [R jarvis/node/jarvis_node](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/node/jarvis_node)
- Desktop decider tool set (decide.py `TOOLS`): open_website, play_media, browser (read/find/click/type/press/tabs/whoami), open_app, window (focus/minimize/maximize/restore/inspect), type_text, press_key, open_file ("find and open a file or folder (args: name, folder)"), check_pc (disk/network/processes/crashes/app_info/services/media), troubleshoot, research, answer, clarify, stop; MAX_STEPS = 4; PROMPT_VERSION = 3 — [R plugin/plugins/desktop/decide.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/desktop/decide.py)
- Commit 1c28d3a3: "There is no operation that creates a folder or a file. The request now ends with 'I cannot create folders or files yet ... nothing was changed' without a model call." (decide.py +8, tests/test_decide_rules.py +15) — [C 1c28d3a3](https://github.com/venkatsairishith-dev/jarvis-hq/commit/1c28d3a3f760a3d194d6228960e9c9ba47d5a237)
- pcopen.py: apps from Get-StartApps via shell:AppsFolder; folders limited to known folders (Downloads, Documents, Desktop, Pictures, Videos, Music) and "anything inside one is matched by name and must stay inside it"; files open only if documents/media; custom code justified vs Windows-MCP's App tool — [R jarvis/node/jarvis_node/pcopen.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/node/jarvis_node/pcopen.py)
- Kernel policy tiers: `NAMES = ["GREEN", "YELLOW_AUTO", "YELLOW_APPROVAL", "RED", "DENY"]`, `RULES`, `EGRESS_TOOLS`, `SENSITIVE_APPS`, `WRITE_OPS` regex (write|edit|delete|remove|rename|move…), `ORDINARY_OPS` for ui (launch, focus, click, type, key, scroll, …), `RISKY_KEYS`, caretaker git/gh rules — [R jarvis/kernel/jarvis_kernel/policy.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/policy.py)
- Only UAC/elevation references in node are in repair.py (lines 9-10, 183) — [R jarvis/node/jarvis_node/repair.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/node/jarvis_node/repair.py)
- NEKO_REQUIREMENTS: C1 open website PRESENT; C2 signed-in Google account TESTED live 6/6; C3 play song TESTED live; C4 open project in VS Code PRESENT not verified; C5 find/open generated PDF/PPTX PARTIAL; **C6 "Create folders and organise selected files, with an undo journal" MISSING**; C7 kill switch TESTED; C8 confirm before high-impact TESTED with real PIN — [R jarvis/docs/neko/NEKO_REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/NEKO_REQUIREMENTS.md)
- Roadmap: "Computer control … Not verified on the current tree; no create-file/folder capability"; decision: 10-task real-desktop baseline, compare node vs Windows-MCP (MIT, UIA) behind kernel allow-list; "File/folder creation needs a kernel approval rule: design it, deploy it deliberately"; owner decision #6 "Allow a create-file/folder capability with a kernel approval rule (phase 3)" — [R JARVIS_ROADMAP_2026-10-22.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md)
- Baseline tool: `python jarvis\evals\neko95\task_baseline.py` (10 plain requests, judged by real screen, results to `jarvis/evals/neko95/results/baseline-<time>.json`) — [R task_baseline.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/evals/neko95/task_baseline.py)
- Desktop plugin files: coordinator.py, core.py, decide.py, learn.py, missions.py, monitor.py, skills.py, config.schema.json, plugin.toml — [R plugin/plugins/desktop](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/plugin/plugins/desktop)
- Related docs: PC_AGENT_RESEARCH.md, PC_CONTROL_BENCHMARK.md, PC_CONTROL_OPERATIONS.md, PC_CONTROL_TEST_REPORT.md — [R jarvis/docs/neko](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/docs/neko)

### Inferences
- "osctl" is a name the plan should not assume; new OS ops would go into node job kinds (`jarvis/node/jarvis_node/jobs.py` + a new module beside `pcopen.py`), kernel `policy.py` rules, and desktop `decide.py` TOOLS/schema (bumping PROMPT_VERSION), plus bench cases in `jarvis/evals/neko95/bench/cases/b_desktop.json`.
- File search beyond "name inside a known folder" (e.g. full-disk/indexed search) is absent.

### Gaps
- Did not read jobs.py end to end to enumerate every node job kind; the list above is from decide.py's TOOLS and pcopen.py.

## Q3. Requirement (2): browser control

### Takeaway
Two browser paths exist: a sandboxed Playwright browser (throwaway profile, network guard, hidden-text filtering) and control of the owner's existing Chrome profiles via an extension bridge with an allow-listed tool set; benchmark area C covers browser security.

### Cited Findings
- browser.py: "Browser control over the DOM … Playwright + the installed Chrome": throwaway profile per session, no downloads/permissions/popups, every request through a network guard (no localhost/private/Tailscale addresses), page text scrubbed and UNTRUSTED, hidden text not shown, password fields never typed into, clicks must name the expected element — [R jarvis/node/jarvis_node/browser.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/node/jarvis_node/browser.py)
- browserext.py: "The owner's EXISTING Chrome profiles for Neko (2026-10-16)"; registry of aliases + masked account; no cookie/password/token read; `ALLOWED_TOOLS = {"browser_tabs", "browser_navigate", "browser_snapshot", "browser_find", "browser_click", "browser_type", …}` — [R jarvis/node/jarvis_node/browserext.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/node/jarvis_node/browserext.py)
- Egress proxy for the agent browser: Stripe Smokescreen on the Oracle VM over Tailscale; "The browser refuses to run if this proxy is unreachable" — [R jarvis/config/egress.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/config/egress.json)
- Scripts: `install_playwright_extension.ps1`, `allow_playwright_connection.ps1`, `save_playwright_token.ps1` — [R jarvis/scripts](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/scripts)
- Bench area file `c_browser.json`; main commit 874bd85f added C-049..C-061 (approval spoofing, element swap, exfiltration, 3 MB lines, token in URL) — [C 874bd85f](https://github.com/venkatsairishith-dev/jarvis-hq/commit/874bd85f600b6c93826fef700912de4071df2949)
- Desktop decider's SITES map includes chatgpt.com, gmail, calendar, drive, etc. (open as websites, not API) — [R decide.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/desktop/decide.py)

### Inferences
- Browser control is comparatively mature; gaps are live verification on the current tree and form-submitting/egress actions (gated as EGRESS_TOOLS by policy).

### Gaps
- Whether the Smokescreen egress proxy is still running (Oracle VM state) is not evidenced in the repo.

## Q4. Requirement (3): personality / character card config

### Takeaway
Personality comes from N.E.K.O's own character system (`config/characters/*.json`, `config/character_defaults.py`, `config/character_fields.py`, `config/prompts`) plus a JARVIS script that forces the persona to English; there is no JARVIS-specific character card ("JARVIS" persona) in the repo.

### Cited Findings
- `config/` holds `character_defaults.py`, `character_fields.py`, `characters/` (en, es, ja, ko, pt, ru, zh-CN, zh-TW .json), `prompts/`, `voice_wake_word.py`, `api_providers.json` — [R config](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/config)
- No match for "jarvis" in `config/characters/*` or `config/character_defaults.py` (grep, case-insensitive) — [R config/characters](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/config/characters)
- make_persona_english.py: "the 'sharp-tongued junior' personality preset ships in Chinese … the model then mixes Chinese into English replies"; rewrites preset profile fields, persona-override language line, memory persona, stored Chinese replies; data root default `D:\jarvis\N.E.K.O-data` — [R jarvis/scripts/make_persona_english.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/scripts/make_persona_english.py)

### Inferences
- A "JARVIS" character card would be a new entry/preset in N.E.K.O's character config (config/ is N.E.K.O's platform layer — editing it falls under CONTRIBUTING's high-risk/regression-report rules if it touches main_logic/app), or better, created through N.E.K.O's settings UI and stored in the data folder, not code.

### Gaps
- Did not read the character JSON schema in detail (fields available for tone, voice, wake word).

## Q5. Requirement (4): widget / tray (jarvis/widget, neko-tauri submodule, PATCHES.md)

### Takeaway
A loopback web widget exists (`jarvis/widget`: index.html, neko.html, neko_state.py, widget_server.py; 1,225 lines). The native shell is a pinned N.E.K.O.-Tauri submodule; the tray, global push-to-talk, single-instance and click-through fail-safe patches are PLAN ONLY (not written). Push-to-talk is MISSING per requirements.

### Cited Findings
- `.gitmodules`: submodule `jarvis/third_party/neko-tauri` → https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri, pinned at commit b7b4fd70 — [R .gitmodules](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/.gitmodules)
- PATCHES.md: "**PLAN ONLY.** None of these patches is written, built or tested yet." Patches: 1 Tray (Show/Hide, Mute mic, Release mouse, Pause wake word, Open console, Quit; state-following icon), 2 Global push-to-talk (`tauri-plugin-global-shortcut` 2.4.1, draft Ctrl+Alt+Space), 3 Single instance (`tauri-plugin-single-instance` 2.5.4), 4 Click-through fail-safe; rules: no new powerful commands, pin Tauri 2.x, run 30-min Win10 transparency soak else fallback to minimal Electron; offer upstream as PRs (no fork) — [R jarvis/third_party/PATCHES.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/third_party/PATCHES.md)
- widget_server.py: "Backend of the JARVIS desktop widget. Windows; standard library only; loopback only; no model calls"; read-only for approvals (PIN in console); one-time launch code auth; wake listener uses `X-Jarvis-Control` — [R jarvis/widget/widget_server.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/widget/widget_server.py)
- Scripts: `install-widget.ps1`, `open-widget.ps1` — [R jarvis/scripts](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/scripts)
- Requirements: X1 always-available icon PARTIAL; X2 click activates TESTED; X3 "Hey Neko" PARTIAL; **X4 push-to-talk MISSING**; X6 clickable fallback TESTED; X7 Open file/Show in folder/Regenerate PARTIAL; X8 needs-permission PRESENT; X9 no LLM while sleeping TESTED (TTL unload) — [R NEKO_REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/NEKO_REQUIREMENTS.md)

### Inferences
- Tray + push-to-talk work is fully specified in PATCHES.md and can be lifted directly into a plan phase; the reuse-first rule means upstream PRs to N.E.K.O.-Tauri rather than a fork.

### Gaps
- Submodule content (Tauri version at b7b4fd70) not inspected (submodule not checked out).

## Q6. Requirement (5): skills (jarvis/skills, desktop skills, skillgate)

### Takeaway
Two skill systems exist: (a) `jarvis/skills/skillgate.py` stages/vets/activates Hermes skills for the lab (2 active lab skills, 6 staged); (b) `plugin/plugins/desktop/skills.py` versioned decider changes with a regression gate, promote and rollback. Remediation adds layer 3 of a learning loop that stages examples from successful PC tasks through the existing gate.

### Cited Findings
- skillgate.py: commands scan/stage/guard/activate/deactivate/status; staging copies files byte-for-byte (scripts never), records provenance, runs Hermes's skills_guard; activation needs allowlist + checks + low/medium rating + kernel authorization (`skill.activate`: low auto+audited, medium owner approval); skills go only to the lab Hermes — [R jarvis/skills/skillgate.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/skills/skillgate.py)
- active.json: lab skills `adversarial-ux-test` (activated 2026-10-10) and `decision-questionnaire` (2026-10-11); backups under `/home/agent/.hermes/skills/...` (WSL lab paths) — [R jarvis/skills/active.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/skills/active.json)
- `jarvis/skills/`: allowlist.json, cost-check, oracle-delete, phase-gate, vault-write, staging (adversarial-ux-test, code-wiki, decision-questionnaire, duckduckgo-search, godmode, grill-me), vendor/openjarvis, tests — [R jarvis/skills](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/skills)
- desktop/skills.py: "Controlled self-improvement for Neko's decision layer (2026-10-16)"; functions check, propose, load, active, gate, promote, rollback — [R plugin/plugins/desktop/skills.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/desktop/skills.py)
- main commit cfc65bf9: a regression gate that ran 0 cases passed (M-024, fixed); damaged registry/overwrite (M-029) and concurrent proposals (M-030) fixed — [C cfc65bf9](https://github.com/venkatsairishith-dev/jarvis-hq/commit/cfc65bf94ba6bc4f212438b18afe9279ddfbd032)
- SELF_IMPROVEMENT_2026-10-22.md: learning loop "do a task → record an EPISODE → measure → find weak spots → propose a change → promote (owner, after a passing gate)"; Layer 1 built (`plugin/plugins/main_agent/episodes.py`, no request text stored, salted word hashes) — [R SELF_IMPROVEMENT_2026-10-22.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/SELF_IMPROVEMENT_2026-10-22.md)
- Layer 3 commit: "stage examples mined from PC tasks that worked, through the existing skill gate" — [C c50dc2f1](https://github.com/venkatsairishith-dev/jarvis-hq/commit/c50dc2f15dcd8b5dc379fc1ef148f27f9d991072)
- Workbench plugin "Learning unfamiliar tasks: research, plan, sandbox test, Propose-Probe-Compose refinement, skill + lessons" (`learn_task`, `list_learned`, `run_learned_workflow`, `check_workflow_output`; skillhub.py) — [R plugin/plugins/workbench/README.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/workbench/README.md)
- Requirement L4 versioned prompts/skills/configs with rollback PARTIAL; L5 no silent self-modification TESTED — [R NEKO_REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/NEKO_REQUIREMENTS.md)

### Inferences
- Lab skills depend on the WSL lab Hermes, whose disk is gone (Q16), so skillgate activation targets are currently unavailable; desktop skills and main_agent learning loop are local and usable.

### Gaps
- Layers 4-5 of the learning loop are described in SELF_IMPROVEMENT doc but not read in detail.

## Q7. Requirement (6): research → plan → coding agent

### Takeaway
All three parts exist but are separate: research plugin (search/fetch/rank/verify with NLI/MiniCheck), main_agent plan-first planner/executor over 155 plugin entries, and coding via kernel lab dispatch (lab_runner with independent verifier, agents.py definitions, projectfix.py) and the caretaker (lab sub-agent that may only open a PR). The coding path ran in the WSL lab, which no longer exists; caretaker is off (no safe credential).

### Cited Findings
- research plugin files: core.py, fetch.py, rank.py, search.py, sources.py, verify.py (MiniCheck referenced in verify.py) — [R plugin/plugins/research](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/plugin/plugins/research)
- main_agent files: planner.py, executor.py, outcome.py, store.py, episodes.py, input_request.py, memory_context.py, TOOL_INVENTORY.md; TOOL_INVENTORY: "all 155 `@plugin_entry` in `plugin/plugins/*`"; effect decided by code (`planner.effect_of`), unknown on non_idempotent step = HELD; owner-only entries excluded via `planner.PLANNER_EXCLUDED_ENTRIES` — [R plugin/plugins/main_agent/TOOL_INVENTORY.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/main_agent/TOOL_INVENTORY.md)
- Requirement O1 coordinator TESTED (plan-then-run); O2 roles PARTIAL; O3 least-privilege PARTIAL; O4 timeline PARTIAL; O5 pause/cancel PARTIAL; O6 budgets PARTIAL — [R NEKO_REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/NEKO_REQUIREMENTS.md)
- Kernel lab dispatch: `dispatch_to_lab(agent, lane, target, p, approval_id=None)` in llm_proxy.py (line ~690) — [R jarvis/kernel/jarvis_kernel/llm_proxy.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/llm_proxy.py)
- agents.py: agent definitions from a sentence ("a Python software engineer that writes code, tests it and fixes failures"); kernel enforces lane, lab dispatch path, attempts, time budget, runs/day, acceptance criteria checked by independent verifier; tool names/instructions not enforced by kernel — [R jarvis/kernel/jarvis_kernel/agents.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/agents.py)
- projectfix.py: "Fix a failing build in an allowlisted project, verified by the kernel (2026-10-18)": task → inspect via node project_check → fix via lab sub-task (owner approves sharing text with public lab) — [R jarvis/kernel/jarvis_kernel/projectfix.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/projectfix.py)
- lab_runner.py: runs as root in the WSL lab box; work (Hermes/OpenCode/goose as unprivileged user) → verify (separate verifier session) → repair; lab_isolation.py: delivered code runs as dedicated `jarvis-sandbox` identity — [R jarvis/lab/lab_runner.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/lab/lab_runner.py)
- Caretaker README: deterministic detectors (upstream_release, advisory via OSV, lock_drift, test_failure, secret_scan); hands medium+ findings (max 5/run) to the lab as kernel tasks with Idempotency-Key; kernel refuses caretaker dispatch failing `policy.caretaker_problems`; sub-agent branches `caretaker/<tool>-<id>` from origin/main, only opens PR — [R jarvis/caretaker/README.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/caretaker/README.md)
- Roadmap: "Lab box and Hermes CLI blocked; caretaker off (no safe credential)"; planner: "planning goes to a cloud model through the privacy gateway … local 4B decider stays for UI grounding"; "Local models too small to plan (decider ctx 2048); cloud planner rate-limited" — [R JARVIS_ROADMAP_2026-10-22.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md)
- Workbench "Client projects: raw materials → source-grounded requirements → plan → checked deliverables" — [R plugin/plugins/workbench/README.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/workbench/README.md)

### Inferences
- The missing piece for "research → plan → code" is a working code-execution sandbox on the laptop (replacing the WSL lab) and a chained flow: research output → main_agent plan → lab/agent dispatch. The kernel parts (agents.py, dispatch_to_lab, verifier) can be reused unchanged if a new lab host is provisioned.

### Gaps
- No evidence of a replacement lab host (new WSL distro, VM, or container) on any branch.

## Q8. Requirement (7): multi-agent tests

### Takeaway
Multi-agent behaviour is tested indirectly: main_agent run-control/outcome tests, kernel lab/task race tests, lab demo task "delegate", and bench areas D (long-horizon, 83 cases) and N (reliability, 73 cases). There is no dedicated multi-agent (agent-to-agent coordination) test suite.

### Cited Findings
- main_agent tests: test_claim_races, test_computer_step, test_executor, test_hostile_results, test_input_request, test_outcome_*, test_planner, test_run_control, test_screen_egress, test_self_correction, test_side_effect_matrix, test_stop_report, test_untrusted_content, test_episodes, … — [R plugin/plugins/main_agent/tests](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/plugin/plugins/main_agent/tests)
- lab demo tasks: build, fix, data, delegate, refactor, desktop ("tasks an agent with shell, files and sub-agents should pass"; runs inside WSL box) — [R jarvis/lab/demo.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/lab/demo.py)
- Bench case files: a_intent, b_desktop (38), c_browser, d_longhorizon (83), e_repair, f_memory, g_voice, h_research, i_documents, j_routing, k_security, l_ux, m_skills, n_reliability (73), x_context — [R jarvis/evals/neko95/bench/cases](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/evals/neko95/bench/cases)
- main commit f801bbfe: D-044..D-083 and N-034..N-073 cover dependencies, idempotency, restart/SIGKILL recovery, cancellation reaching child programs, races across threads and processes — [C f801bbfe](https://github.com/venkatsairishith-dev/jarvis-hq/commit/f801bbfef8c1795f0789f1cc4fceed507e49445c)
- jarvis/kernel/tests: 67 entries — [R jarvis/kernel/tests](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/kernel/tests)

### Inferences
- A "multi-agent tests" phase can extend bench area D/N with a new area (e.g., planner + research + coding sub-agent chains) using the existing bench.py adapter pattern.

### Gaps
- Did not open individual adapters to confirm which cases exercise more than one agent at once.

## Q9. Requirement (8): ChatGPT / Gemini integration

### Takeaway
JARVIS's kernel gateway has a `gemini` provider entry (public lane, research only) that is disabled with `base_url: null`; there is no OpenAI/ChatGPT provider in the JARVIS kernel. N.E.K.O's own `config/api_providers.json` does list openai, gemini and claude among its core/assist providers (keys entered in N.E.K.O settings). The privacy gateway notes Gemini's free tier is excluded for personal data.

### Cited Findings
- jarvis/config/providers.json: `gemini` lane public, models ["research"], enabled false, base_url null; no openai/chatgpt entry — [R jarvis/config/providers.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/config/providers.json)
- config/api_providers.json sections: core_api_providers [free, qwen, qwen_intl, openai, step, gemini, glm, grok]; assist_api_providers [free, qwen, qwen_intl, openai, glm, step, silicon, gemini, kimi, kimi_code, deepseek, doubao, minimax, minimax_intl, mimo, claude, grok, openrouter, orcarouter, requesty, vllm_omni]; also default_models, api_key_registry, native_tts_voice_providers [step, free, gemini, free_intl, grok, mimo] — [R config/api_providers.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/config/api_providers.json)
- privacy/providers.json note: "Gemini's free tier is excluded for personal data (its terms allow training on it)" — [R jarvis/privacy/providers.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/privacy/providers.json)
- ChatGPT appears only as a website in decide.py SITES and as an injection phrase in research/workbench safety regexes — [R plugin/plugins/research/verify.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/research/verify.py)

### Inferences
- Adding OpenAI (API) or Gemini to JARVIS = a providers.json entry (lane, base_url, key_file name, enabled false until key file + live call) plus model_pattern; the "paid must stay false" rule means a paid OpenAI API key conflicts with current policy unless the owner changes it. Using a ChatGPT/Gemini subscription (not API) would need something like 9Router's OAuth routing, which the selector doc flags as possibly breaking provider terms.

### Gaps
- Not checked whether the owner holds OpenAI/Gemini API keys (would be in D:\jarvis-secrets; correctly not in repo).

## Q10. Requirement (9): remote reasoning model controlling local small models

### Takeaway
This is already built as `local_team`: a public adviser (Dahl MiniMax) plans through the privacy gateway (redacted placeholders), local specialists (Qwen2.5-Coder 1.5B, Qwen2.5-Math 1.5B, Ling-3.0-tiny) answer with real values, routing via OpenJev "Verdict" (now native on 127.0.0.1:8091). llama-swap profiles cpu8/cpu16/vram8plus define the local model set.

### Cited Findings
- local_team README: entries `ask_local`, `solve_with_team` ("Dahl's MiniMax writes a short plan through the privacy gateway (it sees [PASSWORD_1], not the password), then the local specialist writes the answer"), `team_status`; Verdict at `JARVIS_JEV_URL` default `http://127.0.0.1:8091/v1/systemone`; llama-swap at `http://127.0.0.1:8080/v1`, 5-minute idle unload; adviser refused unless agent URL is exactly `127.0.0.1:48950`/`localhost:48950` — [R plugin/plugins/local_team/README.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/local_team/README.md)
- Privacy gateway: local OpenAI-compatible proxy in front of Dahl, port 48950, Presidio + spaCy redaction, whitelist of forwarded fields, images/audio/files withheld, placeholders restored in replies incl. streaming — [R jarvis/privacy/gateway.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/privacy/gateway.py)
- start-verdict.ps1: "It was a WSL service; the WSL lab disk is gone, so this runs it natively"; weights heman10x/rlcd-modernbert-151m (~600 MB) — [R jarvis/scripts/start-verdict.ps1](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/scripts/start-verdict.ps1)
- llama-swap.yaml models: ling-tiny, qwen-coder-1.5b, qwen-math-1.5b, decider-qwen3-1.7b, decider-qwen3-4b, decider-qwen3-4b-2507, vision-ui-venus-2b; group specialists. Profile vram8plus: qwen3.5-9b, qwen-coder-1.5b, qwen-math-1.5b, decider-qwen3-4b-2507, vision-ui-venus-2b — [R jarvis/local_models/llama-swap.yaml](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/local_models/llama-swap.yaml); [R profiles/vram8plus.yaml](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/local_models/profiles/vram8plus.yaml)
- Guard test: every llama-server binds 127.0.0.1 with --no-webui, no --tools/--agent/--mcp-servers-config flags (test_server_flags.py) — [C 3c903413](https://github.com/venkatsairishith-dev/jarvis-hq/commit/3c903413e090052dc0e457f44b23b0dbf6aca689)
- 12-model CPU comparison: qwen3-4b-2507 152/157 correct (kept as decider); qwen3-1.7b 130/157 — [C 13e48d59](https://github.com/venkatsairishith-dev/jarvis-hq/commit/13e48d5998159d4a05135e944abc3fc5c87dd61a)
- Requirements M1-M4, M6, M7 TESTED; M5 show provider/model PARTIAL; M8 multiple keys PARTIAL; M9 disable each provider PARTIAL — [R NEKO_REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/NEKO_REQUIREMENTS.md)

### Inferences
- The "remote brain + local hands" pattern is implemented for Q&A-style tasks; extending it to the main_agent planner (remote plans, local decider grounds PC actions) is the roadmap's Phase 0 item "Planner through the gateway".

### Gaps
- The adviser is hard-wired to Dahl via the gateway's first provider; whether local_team can use Groq/OpenRouter as adviser was not verified in code.

## Q11. Requirement (10): voice (jarvis/wake, speech server)

### Takeaway
A full local cascade exists: "Hey Neko" wake (N.E.K.O's sherpa-onnx KWS) → Silero VAD → local STT (Parakeet en, IndicConformer hi/te) → main_agent task via widget server; TTS via Kokoro (en) and Piper (hi/te) behind a local OpenAI-compatible speech server. End-to-end mic loop and latency are unproven; wake hit rate is low for the owner's voice.

### Cited Findings
- wake_listener.py: mic 16 kHz → SherpaWakeWordDetector → chime → record until 1 s silence (Silero VAD, ≤12 s) → local speech recognition 127.0.0.1:48930 → main-agent task via widget server 127.0.0.1:48940/api/task — [R jarvis/wake/wake_listener.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/wake/wake_listener.py)
- local_speech_server.py: POST /v1/audio/speech (PCM16 24 kHz or WAV; voices en|hi|te), POST /v1/audio/transcriptions (Parakeet en, IndicConformer hi/te) — [R jarvis/scripts/local_speech_server.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/scripts/local_speech_server.py)
- jarvis/wake also has eval_owner.py, eval_wake.py, wake_latency.py, command_cut_check.py, loopback_test.py — [R jarvis/wake](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/wake)
- Commits: wake threshold 0.20 (ab579a06); ignore commands with < 0.4 s speech (3b280da9) — [C 3b280da9](https://github.com/venkatsairishith-dev/jarvis-hq/commit/3b280da9)
- Requirements: V1 offline STT PARTIAL; V2 local TTS TESTED (rate not exposed); V3 interrupt PRESENT not measured; **V4 mic end to end MISSING** (G-036/G-037 deferred) — [R NEKO_REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/NEKO_REQUIREMENTS.md)
- Roadmap: wake fired "only some of the time (67% of repo test clips)"; barge-in off; no streaming STT/full duplex; decision: record 200 owner clips + 1 h room audio, compare sherpa KWS / livekit-wakeword / openWakeWord at fixed FAR; sentence-level TTS; measure wake-to-first-audio — [R JARVIS_ROADMAP_2026-10-22.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md)

### Inferences
- Voice work should be measurement-first (owner recordings) rather than new code, per the roadmap's own decision.

### Gaps
- No recorded wake-to-first-audio latency anywhere in the repo.

## Q12. Requirement (11): server tests (jarvis/scripts/neko-test, evals/neko95/bench)

### Takeaway
A Linux test server image (`jarvis/scripts/neko-test/Containerfile` + `sync-and-build.sh`) runs the benchmark v2 (`jarvis/evals/neko95/bench/bench.py`, 860 cases in 15 areas) plus unit suites; the Benchmark Console drives runs from a phone. Latest full result: 834/0/0/26 on combine-tools; on main 833 pass / 1 fail (H-042) / 26 deferred.

### Cited Findings
- `jarvis/scripts/neko-test/`: Containerfile, sync-and-build.sh — [R jarvis/scripts/neko-test](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/scripts/neko-test)
- `jarvis/evals/neko95/bench/`: bench.py, adapters, cases, claims_eval.py, context_bench.py, model_compare.py, model_candidates.json, rubric_v2.json, scorecard.py, live_dahl.py, live_profiles.py, … ; `jarvis/evals/neko95/`: score.py, run_unit.py, run_plans.py, run_gate_live.py, task_baseline.py (remediation) — [R jarvis/evals/neko95](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/evals/neko95)
- Final main run: 833 pass / 1 fail (H-042) / 0 error / 26 deferred on 860; scorecard v2 9.69; kernel 538 OK; console 93 OK; UI e2e 5/5 — [C bbe769cc](https://github.com/venkatsairishith-dev/jarvis-hq/commit/bbe769ccd12defd1320991a78a9b26e52924d2e2)
- combine-tools phase 4: 834/0/0/26 of 860, scorecard v2 9.81, all suites green — [C 87eeaf48](https://github.com/venkatsairishith-dev/jarvis-hq/commit/87eeaf48)
- Benchmark Console (jarvis_console/bench.py, /api/bench/*): run/watch/cancel/compare, challenge lab, allow-listed groups, loopback-only — [C c49165cf](https://github.com/venkatsairishith-dev/jarvis-hq/commit/c49165cf072b8207f0ef6ff36c964784663c496d)
- Laptop-side: dev-test.ps1, dev-status.ps1, test-visible.ps1/.cmd, live_check.py ("live_check: 69 of 69" on 2026-10-22) — [C 6395e98f](https://github.com/venkatsairishith-dev/jarvis-hq/commit/6395e98f)
- Roadmap caveat: "Almost every 'works' claim in our own docs is server-synthetic (Linux image, CPU, fakes)" — [R JARVIS_ROADMAP_2026-10-22.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md)
- 05_OPERATIONS section "Linux test server and Benchmark Console (added 2026-10-17, D29, D30)" (line 413) — [R jarvis/docs/05_OPERATIONS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/05_OPERATIONS.md)
- GitHub Actions are off on purpose (README header) — [M README.MD](https://github.com/venkatsairishith-dev/jarvis-hq/blob/main/README.MD)

### Inferences
- No full benchmark result on the remediation tip is recorded in commit messages after 2026-10-17; a plan should start with a re-run on the merged tree.

### Gaps
- Whether the Linux test server is still provisioned (host, access) is not in the repo.

## Q13. Requirement (12): docs per file

### Takeaway
Per-file documentation is already strong: 249 of 268 non-test Python files under jarvis/ and the JARVIS plugins have a module docstring; all 9 JARVIS plugins have a README. Missing docstrings are mostly `__main__.py` files, vendored rapidocr helpers in `_shared`, and audit repro scripts.

### Cited Findings
- AST scan (remediation tip): 268 non-test .py files in jarvis/ + plugins desktop, research, deliverables, local_team, main_agent, jarvis_bridge, learn_vault, doc_tools, workbench, _shared; 249 have module docstrings. Missing (non-__init__): jarvis/node/jarvis_node/__main__.py, jarvis/kernel/jarvis_kernel/__main__.py, jarvis/console/jarvis_console/__main__.py, jarvis/scripts/oci_cost.py, jarvis/bench/wakeword/score.py, plugin/plugins/_shared/rapidocr/* (8 files), 5 audit repro scripts under jarvis/docs/neko/evidence/audit-2026-10-18/repro/ — [R jarvis tree](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis)
- READMEs present for desktop, research, deliverables, local_team, main_agent, jarvis_bridge, learn_vault, doc_tools, workbench; also jarvis/caretaker/README.md and jarvis/third_party/README.md — [R plugin/plugins](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/plugin/plugins)
- Docs limit: "Docs: at most 5 files in docs/" (jarvis/CLAUDE.md); jarvis/docs holds exactly 01-05 plus subfolders neko/ and research/ — [R jarvis/CLAUDE.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/CLAUDE.md)

### Inferences
- "Docs per file" is best met by docstrings in each file (already the convention) and per-plugin READMEs, not new docs/ files (the 5-file limit). Remaining work is small: ~19 files.

### Gaps
- No README for jarvis/kernel, jarvis/node, jarvis/console, jarvis/lab, jarvis/wake, jarvis/widget, jarvis/privacy, jarvis/local_models as directories (only module docstrings).

## Q14. Requirement (13): usage guide (05_OPERATIONS.md)

### Takeaway
05_OPERATIONS.md (469 lines) is an accreted operations log by date rather than a task-oriented user guide; it does contain "Everyday use", "Using JARVIS the way you use N.E.K.O", "Hey Neko and PC control", console and caretaker sections.

### Cited Findings
- Headings: Oracle; Storage (Windows); Secrets; Windows version; Hand-over; Lab operations (2026-10-10); Skills, traces and evals; Context, memory and integrity; JARVIS Console (2026-10-11); Everyday use (2026-10-11); Connectors; N.E.K.O base (2026-10-14); Using JARVIS the way you use N.E.K.O; N.E.K.O verification record; N.E.K.O's models: Dahl for now; Voice for N.E.K.O: local, no cloud; Cyrene-Agent's feature list, mapped; Live checks; Robustness, modes, approvals view, autostart; "Hey Neko" and PC control (2026-10-16); Linux test server and Benchmark Console (2026-10-17); Caretaker; Control surfaces and Stop (2026-10-18); Console sign-in, retention and approvals (2026-10-19) — [R jarvis/docs/05_OPERATIONS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/05_OPERATIONS.md)
- Separate run guides in docs/neko: PC_CONTROL_OPERATIONS.md, RUNBOOK_WORKBENCH_2026-10-20.md, TEST_VISIBLE_2026-10-22.md, LAPTOP_VERIFICATION_2026-10.md, KERNEL_DEPLOY_2026-10-22.md — [R jarvis/docs/neko](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/docs/neko)
- Start/stop scripts: neko-start.ps1, neko-stop.ps1 (calls start-verdict.ps1), start-console.ps1, start-node.ps1, local-models.ps1, open-widget.ps1 — [R jarvis/scripts](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/scripts)

### Inferences
- A usage guide can be a restructured section of 05_OPERATIONS.md (to respect the 5-file rule) with a "daily use" quick start at the top; 05 has lab sections that are now stale (WSL lab gone).

### Gaps
- None material.

## Q15. Requirement (14): provider switching (gateway fallback, lane_prefs, OmniRoute/9Router)

### Takeaway
Switching exists at three levels: kernel `gateway.plan()` orders providers per lane/purpose with fallback; `llm_proxy` keeps a `lane_prefs` table (an agent's preferred provider per lane, settable via the lab MCP tool `switch_model`); the privacy gateway has its own ordered fallback list with 60 s cooldown. A recommend-only `model_select.py` is not wired into the live route. OmniRoute was installed on the VM as a trial but routes nothing; 9Router was only read and not recommended for install.

### Cited Findings
- gateway.py: `plan(providers, task_lane, purpose=None, prefer_local=True)`, `probe(...)`, `call(kernel, providers, task_lane, prompt, purpose=None, model="default", …)` — [R jarvis/kernel/jarvis_kernel/gateway.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/gateway.py)
- llm_proxy.py: `CREATE TABLE IF NOT EXISTS lane_prefs(lane TEXT PRIMARY KEY, provider TEXT, set_at REAL, by_agent TEXT)`; `preferred(lane)`; `lane_wait(lane)` ("rate limited, back at HH:MM"); `agent_action` ("Lane comes from the token, never from the request") — [R llm_proxy.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/llm_proxy.py)
- jarvis_mcp.py `switch_model(provider)` tool — [R jarvis/lab/jarvis_mcp.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/lab/jarvis_mcp.py)
- main commit cfc65bf9: "a key file gone after planning falls back to the next provider instead of dropping the connection (J-054). gateway.call/probe the same (J-055)"; privacy gateway scrubs a fallback's key echo (J-062) — [C cfc65bf9](https://github.com/venkatsairishith-dev/jarvis-hq/commit/cfc65bf94ba6bc4f212438b18afe9279ddfbd032)
- MODEL_SELECTOR_2026-10-20.md: shadow replay and capability probe built; "Wiring the selector into `llm_proxy` is still not done"; 9Router (v0.5.104, MIT) concerns: default dashboard password 123456, REQUIRE_API_KEY false by default, stores credentials in ~/.9router outside secrets rule, mitm component, subscription routing may break provider terms; recommendation "do not install on this server … put it behind our gateway as one more provider" — [R MODEL_SELECTOR_2026-10-20.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/MODEL_SELECTOR_2026-10-20.md)
- OmniRoute: 01_RESEARCH_AUDIT "TRIAL (was USE) v3.8.51 … Holds provider keys"; 02_ARCHITECTURE "OmniRoute 3.8.51 runs on the VM … but routes nothing yet"; audit verdict for gateway.py: KEEP ("None enforces lanes a model cannot override plus the free-model guard") — [R 01_RESEARCH_AUDIT.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/01_RESEARCH_AUDIT.md); [R 02_ARCHITECTURE.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/02_ARCHITECTURE.md)
- Tool registry entry "OmniRoute (model gateway, trial)" referencing jarvis/deploy/jarvis-omniroute.service — [R jarvis/tools/registry.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/tools/registry.json)
- Retry on rate limit/timeout in main_agent (d071bb7e) — [C d071bb7e](https://github.com/venkatsairishith-dev/jarvis-hq/commit/d071bb7e)

### Inferences
- User-facing "switch provider" (owner picks a provider/model from UI or voice) is not present; the pieces (lane_prefs, model_select, console Models screen `jarvis/ui/src/screens/Models.tsx`) exist to build it without a new gateway.

### Gaps
- Did not verify whether the console Models screen can write lane_prefs.

## Q16. Requirement (15): agent VM / openmuse / hub (jarvis/hub, jarvis/lab)

### Takeaway
The "agent computer" (WSL box JarvisLab with Agent PC/VNC, lab Hermes, OpenCode, goose, openmuse, and the hub's Caddy) lived on `D:\jarvis-lab\ubuntu\ext4.vhdx`; per laptop-dev (2026-10-21/22) "the WSL lab disk is gone". Code for hub (static page + Caddyfile) and lab (runner, isolation, control, MCP, demo) remains in the repo but has no host.

### Cited Findings
- 6395e98f: "The WSL lab disk is gone, so Verdict now runs from D:\jarvis-apps\openjev on 127.0.0.1:8091 (CPU)" — [C 6395e98f](https://github.com/venkatsairishith-dev/jarvis-hq/commit/6395e98f)
- HERMES_0_22_UPGRADE: "the WSL lab is gone anyway" — [R HERMES_0_22_UPGRADE_2026-10-22.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/HERMES_0_22_UPGRADE_2026-10-22.md)
- jarvis/hub: Caddyfile, assets, build_hub.py, index.html; hub service list includes openmuse ("Personal-agent app: chat, tasks, approvals and its own browser", check http://127.0.0.1:8081, group "Lab (this laptop)") — [R jarvis/hub/index.html](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/hub/index.html)
- lab_control.py: owner takes the Agent PC from agents (SIGSTOP) via Unix socket /run/jarvis-lab/control.sock reached by Caddy as hub.localhost:8088/control/ — [R jarvis/lab/lab_control.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/lab/lab_control.py)
- Vault memory map: "WSL box JarvisLab, disk D:\jarvis-lab\ubuntu\ext4.vhdx (Ubuntu 24.04; user agent)"; hub at /opt/jarvis-hub in the WSL box; openmuse data at ~agent/apps/openmuse/.openmuse (PGlite) and CopilotKit cloud project "jarvis-lab" — [V decisions/memory-map.md](https://github.com/venkatsairishith-dev/jarvis-vault/blob/main/decisions/memory-map.md)
- 03_DECISIONS: "openmuse: CopilotKit Intelligence 503s; MCP SDK 1.30 advisory in its dependencies" — [R 03_DECISIONS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/03_DECISIONS.md)
- Oracle: hermes-agent-vm is the main server; NEVER touch hermes-agent-vm-2 — [R jarvis/CLAUDE.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/CLAUDE.md)
- KERNEL_DEPLOY_2026-10-22: kernel on Oracle is the 2026-10-16 build; 2026-10-22 fixes undeployed (per roadmap table) — [R JARVIS_ROADMAP_2026-10-22.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md)

### Inferences
- Rebuilding the agent VM is a prerequisite for requirements 6, 7 (lab-based tests), skills activation and caretaker. The lab code assumes Linux root + Unix users (lab_isolation's `jarvis-sandbox` identity), so a replacement must be a Linux host (new WSL distro, Hyper-V VM, or the Oracle VM within Always Free limits).
- openmuse depends on a CopilotKit cloud project; reuse-first suggests re-installing rather than rebuilding.

### Gaps
- No commit records a replacement lab host or a decision on whether to rebuild the WSL box. The vault was not updated after 2026-10-09, so the memory map still describes the WSL lab as present.

## Q17. Providers currently configured (names, lanes, enabled flags; no secrets)

### Takeaway
JARVIS kernel gateway (`jarvis/config/providers.json`, 16 entries): enabled = local-llama-vulkan (local, Windows), vm-llama-cpu (local, posix), groq (private, gpt-oss-120b), openrouter-private (private, gemma-4-31b-it:free, data_collection deny), dahl-glm and dahl-minimax (public, posix). Disabled: zai-glm, cloudflare-workers-ai, ollama-cloud, gemini, kilo, dahl-deepseek, kaggle-ollama, colab-ollama, puter, openrouter-public. Privacy gateway (`jarvis/privacy/providers.json`): dahl enabled, groq enabled (inactive until key file exists), openrouter and cloudflare disabled.

### Cited Findings
- Kernel providers (name / lane / enabled / priority / default model): local-llama-vulkan local true 10 (os nt, 127.0.0.1:8080, key_optional); vm-llama-cpu local true 90 (posix, timeout 180 s, max 4096 tokens); groq private true 20 openai/gpt-oss-120b (pattern gpt-oss-120b|20b|qwen3.8-27b, reasoning_effort low, max 8000 tokens); openrouter-private private true 30 google/gemma-4-31b-it:free (pattern `:free$`); zai-glm private false; cloudflare-workers-ai private false; ollama-cloud private false; gemini public false (research only); kilo public false; dahl-deepseek public false 15 (note: replies loop, retest before enabling); dahl-glm public true 13 zai-org/GLM-5.3-Flash; dahl-minimax public true 11 MiniMaxAI/MiniMax-M2.7; kaggle-ollama public false 12 gpt-oss:20b; colab-ollama public false 15; puter public false 15 gpt-5.4-nano; openrouter-public public false apodex/apodex-1.1-mini:free — [R jarvis/config/providers.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/config/providers.json)
- Rules in that file: "No secrets here. key_file is relative to the machine's secrets folder … A provider stays enabled=false until its key file exists AND a live call has worked. paid must stay false. Lanes: local, private, public." Dahl: "PUBLIC LANE ONLY, never private data"; "Prepaid promotional pool … never top up"; HTTP 402 = allocation used up — [R jarvis/config/providers.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/config/providers.json)
- Privacy gateway providers: dahl (enabled; uses caller's own key), groq (enabled; owner's first fallback 2026-10-16; inactive until `providers\groq.key` exists; 30 req/min, 1,000/day, 8K tokens/min), openrouter (disabled, model empty), cloudflare (disabled); cooldown_seconds 60 — [R jarvis/privacy/providers.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/privacy/providers.json)
- Model registry `jarvis/config/models.json` (updated 2026-10-08): qwen3-4b-q4km-laptop (production), qwen3-1.7b (fallback), qwen3-4b-q4km-vm (fallback, 5.6 tok/s), bge-small-en-v1.5 (embeddings), groq-gpt-oss-120b (production), openrouter-apodex-mini-free (fallback) — [R jarvis/config/models.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/config/models.json)
- The apodex OpenRouter model is "no longer listed" (2026-10-20 check), so openrouter-public and models.json's fallback entry are stale — [R MODEL_SELECTOR_2026-10-20.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/MODEL_SELECTOR_2026-10-20.md)
- Architecture: a config test refuses any dahl*, gonka*, kaggle*, colab* or omniroute* provider outside the public lane — [R 02_ARCHITECTURE.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/02_ARCHITECTURE.md)
- Other config: `jarvis/config/model_catalog.json`, `measured_capabilities.json` (decider-qwen3-4b-2507 measured), `sensitive_sites.json`, `projects.json`, `watches.json`, `telegram.off` (Telegram off by owner decision) — [R jarvis/config](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/config)

### Inferences
- "Enabled" in the file does not mean usable: groq/openrouter need key files on the machine; the roadmap says the owner must still "Connect OpenRouter/NVIDIA for the planner". dahl-* entries are posix-only (VM/server), so on the Windows laptop the enabled cloud options are groq and openrouter-private.

### Gaps
- Whether key files exist on the laptop/VM cannot be determined from the repo (by design).

## Q18. vault (jarvis-vault) state

### Takeaway
jarvis-vault has 36 commits, last on 2026-10-09; it contains daily notes for 2026-10-07/08/09, decisions/memory-map.md and decisions/audit-anchors.md, and empty inbox/. It is stale relative to the repo (no notes for 2026-10-10..22).

### Cited Findings
- Files: daily/2026-10-07.md, daily/2026-10-08.md, daily/2026-10-09.md, decisions/audit-anchors.md, decisions/memory-map.md, inbox/.gitkeep; latest commit 49cd063 2026-10-09 "Daily note: first agent task on the Agent PC" — [V jarvis-vault](https://github.com/venkatsairishith-dev/jarvis-vault)
- Memory map rules: "agents write only in inbox/, daily/ and decisions/"; jarvis-hq "Docs: 5 files max"; "Backlog.md … One file"; secrets "Not in any repo" — [V decisions/memory-map.md](https://github.com/venkatsairishith-dev/jarvis-vault/blob/main/decisions/memory-map.md)

### Inferences
- A plan should include a vault catch-up step (daily note + memory-map update for: WSL lab gone, Verdict native, branches status), since jarvis/CLAUDE.md tells every session to read the memory map and newest daily note first.

### Gaps
- `Backlog.md` referenced in the memory map was not found at the jarvis-hq root listing (not searched elsewhere).

## Q19. Repo rules the plan must respect

### Takeaway
Hard rules: work only in D:\jarvis on the laptop (big files on D:); never read/print/commit secrets (D:\jarvis-secrets); Oracle Always Free only, never touch hermes-agent-vm-2; phase gates (test, report, wait for go); vault is memory (agents write inbox/daily/decisions only); at most 5 files in docs/; reuse maintained GitHub projects first and justify custom code in 01_RESEARCH_AUDIT.md; plugin edits stay inside `plugin/plugins/<plugin_id>/` (platform layer needs escalation); N.E.K.O PR rules (regression report for app/main_logic/main_routers/memory changes; "Why Not Split" over 20 files; i18n zh/en/ja; UI screenshots).

### Cited Findings
- jarvis/CLAUDE.md: "Workspace: D:\jarvis only … Check free space"; "Secrets: never ask for, read, print, log or commit one. Windows: D:\jarvis-secrets. macOS: ~/.jarvis/secrets.env"; "Oracle: Always Free only … delete one named resource at a time … (must be $0)"; "Gates: stop after Step 11 and wait for the go. After that, one phase at a time: test, report what passed and what could not be tested, wait."; "Memory: vault is the memory. Agents write only inbox/, daily/, decisions/."; "Docs: at most 5 files in docs/. Plan changes go into the Project JARVIS doc too."; "Say which OS (Windows or macOS) each step runs on and when to reboot."; "NEVER touch instance hermes-agent-vm-2"; "Reuse first … do not remake anything or create new repos unless truly necessary. Justify any custom code against an existing project in docs/01_RESEARCH_AUDIT.md." — [R jarvis/CLAUDE.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/CLAUDE.md)
- CONTEXT.md: "`plugin/plugins/<plugin_id>/` is the only default editable workspace"; Platform Layer (loading, lifecycle, registry, config parsing, SDK) "must not modify … unless explicitly escalated"; Plugin Identity must be settled first; Plugin Design Brief before implementation — [R CONTEXT.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/CONTEXT.md)
- CONTRIBUTING.md: Python 3.11 only, uv; i18n (Chinese, English, Japanese minimum); regression report if `*.py` under app/, main_logic/, main_routers/, memory/ change; "Why Not Split" if >20 counted files; UI changes need before/after screenshots — [R CONTRIBUTING.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/CONTRIBUTING.md)
- Testing culture visible in commits: each fix "with a test that fails on HEAD"; mutation-tested checks; deferred cases never simulated; "Every statement checked against the code, commits and result files" — [C 2e9d337b](https://github.com/venkatsairishith-dev/jarvis-hq/commit/2e9d337ba349db58748e3b4b9c65bf407ab5f7f1)
- providers.json: "paid must stay false"; enabled only after key file + live call — [R jarvis/config/providers.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/config/providers.json)
- NEKO_REQUIREMENTS non-functional: N1 CPU-only 8 GB RAM with remote planning; N3 honesty (no fabricated progress/results); N4 backups before data changes; N5 no paid service without approval, Oracle $0; N6 local first, remote only via privacy gateway and public lane — [R NEKO_REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/NEKO_REQUIREMENTS.md)
- Roadmap: "Never add a skill marketplace or auto-install"; taint tracking; caretaker stays off until a safe credential exists; planner prompt stays small (decider 2048 tokens) — [R JARVIS_ROADMAP_2026-10-22.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md)
- Kernel policy treats providers.json, policy.py, audit.py, store.py, CLAUDE.md, settings.json etc. as SAFETY_COMPONENTS (agent writes to them are restricted) — [R policy.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/policy.py)

### Inferences
- The owner's existing 2026-10-22 roadmap (phases 0-5) and SELF_IMPROVEMENT plan already cover several of the owner's requirements; a new phased plan should map onto (not replace) them and cite `JARVIS_ROADMAP_2026-10-22.md` section 5 owner decisions (kernel deploy, OpenRouter/NVIDIA key, name redaction, phone channel, kill-switch approval policy, create-file/folder capability).
- Note jarvis/docs/neko/ already holds ~35 docs; the "5 files in docs/" rule is applied to the top level of jarvis/docs only in practice.

### Gaps
- "Project JARVIS doc" (where plan changes must also go) is not in either repo; location unknown.
