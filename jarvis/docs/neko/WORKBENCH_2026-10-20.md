# Workbench: learning tasks, client intake, prompt generator (2026-10-20)

Linux test server, branch `remediation-2026-10-18`, HEAD `88ec49a2`. Nothing committed, pushed or deployed. No
credentials, security policies, model routing or retention changed; caretaker off; no VM touched.

## What was built
A new N.E.K.O plugin, `plugin/plugins/workbench` (16 entries, panel `ui/panel.tsx`). Details are in its README.
1. **Client projects**: files are copied into an isolated project folder. Audio goes to the local speech server
   (en / hi / te). Requirements quote and cite every source (file + message / timestamp / page). The extraction also
   finds conflicts, open questions and quarantined AI-addressed text. Then a plan with a tool per task, a prompt per
   task, and deliverables from a verified LOCAL model. Each deliverable is validated (reopens; coverage; no secrets;
   no leaks; no figures that are not in the sources) and `SUMMARY.md` is written.
2. **Prompt Generator**: structured prompts in 9 fixed sections, for 5 kinds. Sources are fenced and scrubbed;
   client material is withheld from non-local models. Prompts are versioned (edit and regenerate, never overwritten).
   Execution is owner-only (`confirm`) through main_agent or JARVIS, gated on the kill switch, and recorded as
   "submitted", not done.
3. **Learning unfamiliar tasks**: the pipeline is analyse, then research (official sources graded first), then
   plan, then draft a workflow (owner, local model or library). The workflow is tested in an isolated sandbox
   process on example cases and refined with Propose-Probe-Compose (Harness² method). It is adopted only if every
   case passes. The skill gate rates the result, then a skill and lessons are stored with evidence. Real runs go
   only through JARVIS (kernel policy, approvals, audit, kill switch), and their outputs are checked afterwards.

**Harness²** (google-research/harness2, Apache-2.0, commit 1cfe254e):
- Inspected. Its 5 tests and its deterministic demo pass offline on Python 3.11.
- Its method is adapted in `ppc.py`. Its runtime is not used: the executors run with unrestricted network and the
  Codex sandbox off.
- `harness2_compat.py` runs upstream's demo from a pinned, hash-checked checkout. Result: passed, 2 done, 0 failed.

**Changes outside the plugin:**
- `main_agent/planner.py`: `PLANNER_EXCLUDED_ENTRIES`, so a plan can never run a prompt or a workflow, or change a
  provider policy.
- `main_agent/outcome.py`: schemas for the 16 entries, so "needs review" or "submitted" is never read as done.
- `main_agent/tests/test_outcome_registry.py`: Workbench added to `OURS`, with success and failure examples.
- `main_agent/TOOL_INVENTORY.md`, `jarvis/docs/01_RESEARCH_AUDIT.md`.
- `plugin.meta.json` for workbench and main_agent, built with the project's generator (checker: OK, 0 refused).

## Tests (real output)
| Suite | Result |
|---|---|
| workbench | 60 passed, 1 skipped (the upstream Harness² check, which needs `HARNESS2_SRC`; run separately with the checkout: 1 passed) |
| main_agent | 490 passed |
| doc_tools | 10 passed |
| research | 125 passed, 2 skipped |
| jarvis_bridge | 31 passed |
| _shared | 174 passed, 2 skipped |
| deliverables | 36 passed |
| learn_vault | 18 passed |
| local_team | 49 passed |
| desktop | 465 passed |
| wechat_integration | 12 passed |
| jarvis/skills | 4 passed |
| lifekit | 318 passed, 1 failed (see below) |
| `plugin/tests` (offline run) | 4301 passed, 1 failed, 5 skipped |
| ruff (workbench, planner) | clean |
| tsc (both panels) | exit 0 (a control with a deliberate type error gives 2 errors) |
| gitleaks (workbench, research audit) | no leaks |

**The two failures:**
- **lifekit** `test_ambiguous_nearby_results_are_visible_and_actionable_to_llm`: also fails on the clean base, 6 of 6
  runs (current tree 6 of 6). It passed in this morning's full run, so it is probably date-dependent. Lifekit is
  untouched. Not fixed: upstream test.
- **`plugin/tests`** `test_generated_ruff_config_ignores_vendor_but_checks_plugin_source`: it downloads ruff with
  `uvx`, and the run had no network. Rerun with network: 1 passed.

**Live, with a real local model.** Qwen3-4B-Instruct-2507 Q4 on llama.cpp b11714, CPU, `-c 2048` as in production,
synthetic data only. All runs used the final code except where marked.

| Check | Result |
|---|---|
| Model-suggested requirements | 3 accepted, 1 rejected: its quote was not in the cited segment |
| Bakery project: deliverables made | 2 |
| Bakery logo | needs review, only for the Hinglish item (check by hand) |
| Bakery deck | needs review: Telugu item, plus invented figures caught ("12%", "30%", "₹12.5 lakh") |
| English brief (stem matching added after a live false negative) | **verified**: the report was drafted by the model and passed every check |
| Unfamiliar task (unique email domains) | the model's first draft failed 0/2 cases; Propose-Probe-Compose (a pair of edits) reached 2/2; validated, one lesson stored |

The local-model proposer produced no applicable edits in that run; the repair came from the deterministic edits.

**Bugs found and fixed by these runs:**
- Prompts too large for a 2,048-token model.
- Wall-clock noise counted as "helped".
- YAML in the skill frontmatter crashed the skill gate. The gate failed closed, as designed.
- Figures invented by the model were not caught.
- Hinglish requirements were word-checked as English.
- Budgets were inherited as deliverable content.
- A cross-file message counted as the answer to a question.
- Plain word matching gave a false "needs review" (fixed with stems).

## Blocked or not verified
- **Audio from a real recording:** needs the laptop's speech server and models, and ffmpeg for non-WAV files.
  Tested only with an injected transcriber.
- **No speaker separation:** audio requests are attributed to "the recording".
- **Telugu and Hindi:** recognised by a cue list only, with no translation; every such item needs a hand check.
- **Decks and PDFs:** the deliverables plugin needs Windows tools. Elsewhere an outline document is made.
- **No OCR of images** in intake.
- **Code, website and research tasks:** run only when the owner executes their prompts. The kernel and lab paths
  were not exercised live here (a fake bus in tests).
- **Real research:** the live learning run had no research (no web search configured here). Source grading is
  tested with fixtures.
- **Panel:** type-checked only, not clicked in a real N.E.K.O window (Windows).
- **The executor's own postconditions:** a run is "submitted" until its output files are checked with
  `check_workflow_output`.

## Owner decisions
- Whether any client project may use an external AI provider (`set_provider_policy`; the default is local only).
- Whether to install `openpyxl` (spreadsheet intake) and ffmpeg (non-WAV audio) on the laptop.

## Addendum (master integration pass, same day)
The Workbench now has more (30 entries, 76 tests):
- skill lifecycle (`skillhub.py`);
- requirement confidence, basis, validation status and sensitive-data flags;
- plan scope, risks, acceptance criteria and test plan;
- prompt rename, duplicate, compare, export, runtime and provenance;
- GitHub research sources;
- live online research;
- briefing.

**Correction to the section above:** the earlier "16 entries" and "60 passed" were true at that time. See
`MASTER_INTEGRATION_REPORT_2026-10-20.md` for the reconciled counts, the live research results and the capability
matrix, and `RUNBOOK_WORKBENCH_2026-10-20.md` for operations.
