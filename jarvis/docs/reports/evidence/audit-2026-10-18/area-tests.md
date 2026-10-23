# Audit: tests and benchmark reconciliation (JARVIS, HEAD 88ec49a2)

Auditor scope: result artifacts in `jarvis/evals/neko95/results/` and `jarvis/evals/pc_control/results/`, git history,
`bench.py`, `scorecard.py`, `rubric_v2.json`, `jarvis/console/jarvis_console/bench.py`. `jarvis/evals/results` does not
exist. Read-only: nothing in the repo was changed. Only single bench cases and one small pytest file were run, inside the
`neko-test` container with a throw-away `:O` overlay and `--no-save`.

This is a Linux server with no microphone, no GPU and no Windows. Every hardware claim below is checked only through code
and the recorded result files. Nothing hardware-related was verified here.

Evidence types used in this report:
- **live**: a recorded run against real services or real hardware, with a result file.
- **integration**: real components wired together, with the external boundaries faked.
- **unit/mocked**: the code under test runs with a stand-in for a boundary (for example, the model's answer is a fixture).
- **none**: there is no artifact; the docs only say so.

Time zones: bench and scorecard file names and `time` fields are **UTC** (written in the container). The server's local
time and the commit times are **EDT (-0400)**. The laptop files (pc_control/*, score baseline and interim) carry **IST
(+0530)** stamps. One folder holds stamps in three time zones.

---

## 1. Reconciliation table

### 1a. Benchmark v2 full runs (`bench-YYYYMMDD-HHMMSS.json`, no suffix)

| Run file | Ran at (EDT) | Code it ran on | Added in | Totals pass/fail/error/deferred (of N) | Scorecard v2 (file) | Type | Notes |
|---|---|---|---|---|---|---|---|
| bench-20261016-170841 | 10-16 13:08 | working tree on 9f278eee + **uncommitted** changes (committed 13:35-13:37 as b8887770, 8549a252, e0817402, 37a52136; the case files themselves were uncommitted) | 37a52136 | 542/5/0/25 (572) | none saved; recomputed 9.29 | 501 integration / 71 mocked / 0 live | first v2 run; failures H-024, J-038, L-007, L-011, L-017 |
| bench-20261016-173117 | 10-16 13:31 | same as above (uncommitted tree) | 37a52136 | 546/1/0/25 (572) | 9.73 (scorecard-v2-20261016-173117) | same | J-038 failed (p95 20.4 s against a 1.5 s limit) |
| bench-20261016-181824 | 10-16 14:18 | b791960f + **uncommitted** proxy/research fixes (committed 14:40 as b15d9e29) | b15d9e29 | 547/0/0/25 (572) | 9.75 (…-181824) | same | J-038 fixed by the proxy burst change |
| bench-20261017-031703 | 10-16 23:17 | 58dd28d3 (8d045a0f contains only results) | 8d045a0f | 548/0/0/24 (572) | 9.75 (…-031704) | same | H-027 now runs the real NLI model: 2/18 false claims accepted |
| bench-20261017-102655 | 10-17 06:26 | 4e32e583 (bbe769cc adds only docs, a console test and results) | bbe769cc | **833/1/0/26 (860)** | **9.69** (…-102656) | 658 integration / 202 mocked / 0 live | H-042 FAIL (xsmall checker). README, 04_BUILD_PLAN and 05_OPERATIONS still quote this run |
| **bench-20261017-220721** (latest) | 10-17 18:07 | **15d62f50** (87eeaf48 adds only docs and results) | 87eeaf48 | **834/0/0/26 (860)** | **9.81** (…-220722) | 658 integration / 202 mocked / 0 live | H-042 passes with the MiniCheck checker |
| (HEAD 88ec49a2) | n/a | caretaker commit: changes `kernel/jarvis_kernel/llm_proxy.py` (+/-32 lines) and `policy.py` (+56) | n/a | no full run at HEAD in the repo; the audit rerun (2026-10-18 04:55-05:15 UTC, overlay, not saved) gave **833/1/0/26** (J-018 timing fail) | **9.12** (rerun) | 658 integration / 202 mocked | see section 4 |

Recomputing each scorecard from the bench rows with the repo's own `scorecard.score()` gives exactly the stored numbers
(9.73, 9.75, 9.75, 9.69, 9.81). There are no diffs. `rubric_v2.json` and `scorecard.py` have not changed since 37a52136.

### 1b. Other suites and benchmarks

| Suite | Result file / log | Commit | Date | Totals | Type | Reproducible here? | Notes |
|---|---|---|---|---|---|---|---|
| v1 scorecard (official) | score-20261016-154951-server-rescore.json | 9f278eee | 10-16 15:49 UTC | mean **7.61**, lowest 4.0; 398 rows: 361 pass, 35 fail, 1 not_tested, 1 not_run | mixed: **live** laptop runs (Windows, IST, 10-16) + hand-entered `evidence_manual.json` rows (about 17 rows with sources such as "guided-owner-test.json 14:2x" or "live runs") | rescoring yes; the inputs no (they need Windows and the laptop) | frozen rules v1 (`capabilities.json`). No input is newer than 10-16 18:09 IST, so none of the 10-17 fixes are reflected |
| v1 baseline / interim | score-20261016-150635-baseline (3.0), score-20261016-161950-interim (4.75) | 092b2858 | 10-16, IST stamps | — | same | — | excluded from "official" by label in the console's `_official_v1` |
| pc_control suite (Windows laptop) | pc_control/results/suite-20261016-144424.json | 7dd88fb1 | 10-16 14:44 IST | 224 pass, 2 fail (A07, H19), 2 info, 1 not_tested (B18) of 229 | **live** (real decider, Windows) | no (Windows) | the newest live Windows evidence in the repo; it predates every 10-17 change |
| pc_control desktop / diagnosis / coordinator / browser | desktop-…-155621 (21/21), diagnosis-…-155827 (17/17), coordinator-…-180855 (10 completed, 1 clarification, 1 not a PC task), browser-…-144244 (19/19) | 092b2858, 002cf9d9, 7dd88fb1 | 10-16, IST | as shown | **live** (Windows) | no | |
| Live Dahl runs | live-dahl-…-163134 (12/12), live-profiles-…-165202 (12/12, fake profiles), live-research-dahl-…-184005 (5/5, supported share 0.723), live-plans-dahl-…-171332 (plan_cases 6/9, dev 40/70, holdout 20/30, holdout2 31/40) | 37a52136, b15d9e29 | 10-16 | as shown | **live** (public Dahl model over the network) | needs the network and a key: not run | live-plans: Dahl substitutes for the local decider, so these are not the product's model |
| Model comparison | models-20261017-060424 / -074642 (12 models), plus re-runs 19:18-20:53 UTC | 13e48d59, 15d62f50 | 10-17 | Qwen3-4B-2507 152/157 (re-runs 152-153); Qwen3-1.7B 130 (132-133) | **live** local model on this server's CPU (real llama-server, real decide.py, fixed sets) | yes, but heavy (CPU); not run | CPU-only; not a laptop GPU forecast |
| llama-bench | llama-bench-*.jsonl (36+24+48 lines) | 15d62f50 | 10-17 | throughput rows | live, CPU | heavy; not run | |
| Claim checker evals | claims-*-minicheck-roberta-20261017-2158xx/2159xx | 87eeaf48 | 10-17 21:58 UTC | claims_v2 FA 0/46, nli_pairs FA 0/18, VitaminC FA 9/200, recall 0.71 | real local ONNX model on fixed in-repo sets | yes, with /srv/jarvis/models/nli mounted | numbers match the docs; xsmall (10:19 UTC files): 2/46, 1/18, 10/200, recall 0.44 |
| Context selection | context-holdout2-20261017-045315 (33/34 answer kept), context-holdout-…-023635 (44/45) | d3002d74 era | 10-17 | as shown | integration (fixtures) | yes | |
| Kernel unittest | **no log or result file in the repo** | — | — | docs: 538 (bbe769cc), 570 (87eeaf48 report) | unit/integration | yes (container) | see 1c |
| Console, plugin suites, plugin/tests, tests/unit, UI | **no log or result file in the repo** | — | — | docs: console 93 (3 skipped); plugin/tests 4289 pass / 8 fail; tests/unit 41981 pass / 4 fail | unit/integration | yes (container), except Windows/UI | the commit message of 87eeaf48 says "all suites green", but the same report lists 8 + 4 known failures |

### 1c. Kernel test totals 538 / 570 / 574

There is no artifact for any of these totals. I reconciled them by counting `def test_` statically in
`jarvis/kernel/tests` at each commit. The static count runs a constant 21 below the unittest totals (generated or
inherited tests):

| Commit | static `def test_` | +21 | doc claim |
|---|---|---|---|
| bbe769cc (D30 docs, 10-17 06:33) | 517 | 538 | 538 (README section, 04_BUILD_PLAN, 05_OPERATIONS, report "Final run") |
| 15d62f50 / 87eeaf48 | 549 | 570 | 570 (report "Phase 4") |
| 88ec49a2 (HEAD) | 553 | 574 | not stated in the docs; the guide page says 570 |

- **+32 tests** (538 to 570) were added in 15d62f50: test_gateway (+60 lines), test_idempotency, test_memory_retrieval and
  test_webmcp.
- **+4 tests** (570 to 574) were added in 88ec49a2: test_caretaker_guard.py. I ran it at HEAD: 4 passed, 7 subtests passed.
- The "16 skipped: Windows" figure has no artifact.

### 1d. Benchmark v2 at the latest run (bench-20261017-220721): per area and evidence type

Classification uses exactly the console rule (`jarvis_console/bench.py::evidence_of`):
1. The optional `evidence` field. No case has one.
2. Otherwise **mocked** if the scenario matches the regex `MOCKED`
   (`mock(ed|s)?|fake[sd]?|faked|hand-written|recorded|recorder|stand-?in|stands in|scripted|stub(bed)?`).
3. Otherwise **integration**. The console never labels a bench case **live**; only `results/live-*.json` files are live.

| Area | Cases | Pass | Fail | Error | Deferred | Integration | Mocked | Live |
|---|---|---|---|---|---|---|---|---|
| A intent | 95 | 95 | 0 | 0 | 0 | 43 | 52 | 0 |
| B desktop | 38 | 33 | 0 | 0 | 5 | 34 | 4 | 0 |
| C browser | 61 | 59 | 0 | 0 | 2 | 59 | 2 | 0 |
| D long-horizon | 83 | 82 | 0 | 0 | 1 | 55 | 28 | 0 |
| E recovery | 59 | 56 | 0 | 0 | 3 | 45 | 14 | 0 |
| F memory | 50 | 49 | 0 | 0 | 1 | 49 | 1 | 0 |
| G voice (offline) | 37 | 28 | 0 | 0 | 9 | 26 | 11 | 0 |
| H research | 60 | 60 | 0 | 0 | 0 | 44 | 16 | 0 |
| I documents | 33 | 31 | 0 | 0 | 2 | 30 | 3 | 0 |
| J routing | 71 | 70 | 0 | 0 | 1 | 45 | 26 | 0 |
| K security | 111 | 109 | 0 | 0 | 2 | 100 | 11 | 0 |
| L UX | 38 | 38 | 0 | 0 | 0 | 18 | 20 | 0 |
| M skills | 35 | 35 | 0 | 0 | 0 | 32 | 3 | 0 |
| N reliability | 73 | 73 | 0 | 0 | 0 | 62 | 11 | 0 |
| X context | 16 | 16 | 0 | 0 | 0 | 16 | 0 | 0 |
| **Total** | **860** | **834** | **0** | **0** | **26** | **658** | **202** | **0** |

By status: 632 pass/integration, 202 pass/mocked, 26 deferred/integration. **Zero bench cases are live.**

Caveats on the labels:
- **The regex undercounts "mocked".** About 19 more cases say the model or planner reply is a "fixture" without a regex
  keyword, so the console calls them integration: A-052, A-060, A-061, A-064, A-065, A-092, A-093, A-094, A-095, L-008,
  L-024, L-038, M-013, M-014, M-015, M-016, M-017, M-019, M-020. Many B (desktop) cases also inject the node or agent
  reply ("the agent reports ok… but the node reports unconfirmed"): they test real coordinator logic against faked node
  responses. **No bench case drives real Windows UI.**
- **About 10 H cases use a real local model.** H-027, H-037 to H-044 and H-060 run the real ONNX claim checker; without
  the model mounted they ERROR, never pass. These are integration with a real model component, not live.

The 26 deferred cases are exactly the cases whose `platform` is not "any". All 26 have `"check": "none"` (no implementation
exists), and `bench.py::run_case` defers them by platform **unconditionally, on any OS**:

| Id | Risk | Platform | Title / reason |
|---|---|---|---|
| B-034 | high | windows | Modern (UWP) app: Calculator 1+2=3 |
| B-035 | medium | windows | Installer dialog read and closed by name |
| B-036 | critical | windows | Typing under the foreground lock |
| B-037 | high | windows | Real typing read back in WordPad |
| B-038 | high | windows | Save-changes dialog appears mid-task |
| C-047 | high | logged_in_browser | Owner's logged-in profile: open and confirm the account |
| C-048 | medium | logged_in_browser | Owner's profile: play a video and verify playback |
| D-043 | high | owner_hardware | Live research-deck mission on the laptop |
| E-032 | high | windows | Real crash diagnosed from the Windows event log |
| E-033 | medium | windows | Real restart of a Windows app |
| E-034 | medium | owner_hardware | winget install through the UAC prompt |
| F-030 | high | owner_hardware | Live memory lifecycle with the real agent |
| G-017 | critical | owner_hardware | Wake recall at 0.5 m, owner's voice |
| G-018 | critical | owner_hardware | Wake recall at 1.5 m, owner's voice |
| G-019 | critical | owner_hardware | False wakes in a normal room |
| G-020 | critical | owner_hardware | Cancel by voice, real microphone |
| G-021 | critical | owner_hardware | Barge-in while Neko speaks |
| G-022 | critical | owner_hardware | No self-wake from the speakers |
| G-023 | high | owner_hardware | Speech recognition word error rate, owner |
| G-036 | medium | owner_hardware | Microphone that only offers 44.1 kHz (real device) |
| G-037 | high | owner_hardware | Pause from the Neko icon with the real microphone |
| I-017 | medium | windows | PDF printed by the laptop's Microsoft Edge |
| I-018 | medium | windows | Animations validated by OfficeCLI and shown in PowerPoint |
| J-048 | medium | owner_hardware | GPU/VRAM fit of local models on the laptop |
| K-040 | critical | windows | Physical kill-switch button on the laptop |
| K-089 | critical | windows | Windows Credential Manager and DPAPI prompts |

Deferred cases by platform: 11 windows, 13 owner_hardware, 2 logged_in_browser. 9 are critical.

### 1e. Reconciling the claims

**833/1/0/26 against 834/0/0/26**
- These are two different full runs on two different commits:
  - 102655 (code at 4e32e583), H-042 FAIL;
  - 220721 (code at 15d62f50), H-042 pass.
- The only status change between them is H-042. H-027 also improved: 1/18 false claims accepted became 0/18.
- 15d62f50 switched the default claim checker in `plugin/plugins/research/verify.py` from nli-deberta-v3-xsmall to
  MiniCheck-RoBERTa-Large (`DEFAULT_MODEL = "minicheck-roberta"`), with xsmall as the fallback when the file is missing.
- The bench row does not record which checker ran. That it was MiniCheck is inferred from two facts: H-027 shows 0/18,
  which matches the minicheck nli_pairs file, while xsmall gives 1/18; and the minicheck claim files were produced 8
  minutes earlier.
- I confirmed it at HEAD:
  - H-042 passes with the default checker;
  - with `RESEARCH_NLI_MODEL=xsmall` it FAILS with the same verdict as 102655;
  - without the model mount it ERRORs.
- So the "fix" is a model swap, not a logic change.

**Scorecard 9.69 to 9.81**
- The research dimension went from 8.0 (capped by the high-risk failure H-042) to 10.0. Every other dimension is unchanged.
- 9.81 = (13 × 10 + 3 × 9) / 16 = 9.8125. Desktop, security and voice are capped at 9.0 because critical cases are
  deferred: B-036; K-040 and K-089; G-017 to G-022.
- **9.81 is the maximum this server can ever report.** It means "every executable case passed". It cannot rise without
  hardware runs, and those runs need checks that do not exist yet (`check: none`).

**v1 score 7.61**
- `score.py` applies the frozen v1 rules (`capabilities.json`) to 14 capabilities, C1 to C14. It uses the laptop's live
  result files plus the hand-entered `evidence_manual.json` rows.
- Caps: a critical case not run caps at 5.0; a critical case failed caps at 6.0.
- The file was produced on the server (9f278eee, 10-16 15:49 UTC) by **re-scoring** the laptop files. It is frozen
  because its inputs come only from live Windows runs, and those have not been redone since 10-16.
- Low capabilities:
  - C8 research_documents 4.0 (3 critical failed);
  - C1 voice 5.0 and C9 perception 5.0 (critical not run);
  - C5, C6, C11 and C14 at 6.0.
- It reflects the code of 10-16 IST, not HEAD.

**Rubric v2: what a 10 means**
- Score = 10 × passed / executed over the dimension's cases. Deferred cases do not count.
- Caps: 6.0 for any critical failure, 8.0 for any high failure, 9.0 for any deferred critical case, 9.0 below 20
  executed cases.
- The rubric says it plainly: "a 10 means the defined cases all pass; it says nothing about the fictional JARVIS".
- **A 10 is a pass rate over these in-repo cases, which mostly use fixtures. It is not real-world verification.** Even a
  "verified" label only means at least 20 executed cases and at most half deferred.
- Example: desktop is "verified" at 9.0 on 32 executed cases, and none of them touched Windows.

---

## 2. Mismatches between the docs and the artifacts

1. **README.MD header (Status 2026-10-17)** quotes 833/1/0/26, `bench-20261017-102655.json` and scorecard 9.69. The
   latest artifact is 834/0/0/26, `bench-20261017-220721.json`, 9.81. The header is stale.
2. **04_BUILD_PLAN "Status (2026-10-17)"** is stale in three places:
   - it quotes 833/1, 9.69 and "kernel 538 OK";
   - it says "Claim checker: … xsmall NLI kept", but the default has been MiniCheck since 15d62f50;
   - Phase 4 and the caretaker are not mentioned.
3. **05_OPERATIONS "Baseline, final run 2026-10-17"** still says kernel 538, benchmark 833/1 (H-042) and
   `bench-20261017-102655.json`.
4. **03_DECISIONS D30**:
   - it carries the MiniCheck "Update, same day" and the caretaker, but never records the 834/0/26 run or 9.81;
   - its main bullet still reads "Claim checker: keep nli-deberta-v3-xsmall";
   - the bullet "Benchmark v2 grew … each driving real code" overstates: 202 cases are mocked by the console's own rule,
     and about 19 more use model fixtures.
5. **NEKO_95_PROGRESS_REPORT**:
   - "Remaining bugs" still lists H-042 as open, while the Phase 4 bullet says it is fixed;
   - the "Final run (… after all commits)" section is no longer the final run;
   - the Phase 4 suite totals (kernel 570, console 93, research 40, local_models 12, tools 52, plugin/tests 4289/8,
     tests/unit 41981/4) have **no log or result file** in the repo;
   - nothing records a run at HEAD 88ec49a2, although that commit changed kernel code (`llm_proxy.py`, `policy.py`).
6. **Commit 87eeaf48's message says "all suites green"**, but the report it adds lists 8 failing plugin metadata snapshots
   and 4 failing tests/unit tests.
7. **"On Windows the Windows-only cases run instead of deferring"** is false against the code. All 26 deferred cases have
   `check: "none"`, no adapter function exists for them, and `run_case` defers by platform on every OS. Running `bench.py`
   on the laptop will still report 26 deferred. The claim appears in:
   - the progress report (lines 471 and 543);
   - PC_CONTROL_OPERATIONS.md:83;
   - QUALITY_95_EVALUATION_PLAN.md:132;
   - IMPLEMENTATION_ROADMAP.md:110.
8. **Guide page "JARVIS In Depth"** has four problems:
   - it says "kernel 570 tests", but HEAD has 574 by the static count; see section 4 for the rerun;
   - it says "860 benchmark cases, real code … any replaced part is labelled 'fake' or 'mock'". That is only partly true:
     about 19 cases say "fixture" and are not labelled mocked by the console's rule;
   - "834 / 0" and "9.81" match the artifacts, but they are on 15d62f50, not HEAD;
   - "N.E.K.O's own 46,000+ tests" (41981 + 4289) has no artifact and includes 12 known failures.
9. **Scorecard wording.** Docs present 9.81 next to "verified" dimensions, but 9.81 is the ceiling for this server
   (section 1e). The docs do not say that the score cannot move without hardware checks, and those checks do not exist yet.
10. **Mixed time zones in result stamps** (UTC on the server, IST on the laptop). `score.py::run_time` orders by the
    stamp, so laptop and server files can be ordered wrongly when they are mixed. Example: the interim file stamped
    16:19 IST sorts after the server rescore stamped 15:49 UTC, which was actually 5 hours later. The console avoids this
    for v1 only because it filters by label.
11. **The headline 834/0 and 9.81 are not stable.** The HEAD rerun gave 833/1 and 9.12: J-018, a critical case with a
    500 ms limit, measured 505 ms. It also failed on the 87eeaf48 code at 1 CPU. No doc presents 9.81 as conditional on
    timing.
12. **H-027's scenario text** still names "nli-deberta-v3-xsmall, revision a150876", but the default checker that ran it in
    220721 is MiniCheck. The bench row records no model name, so the result file cannot prove which checker produced it.

---

## 3. Commands run and their real outputs (abridged)

```
$ git log -8 --format='%h %ad %s' --date=iso
88ec49a2 2026-10-17 19:03:04 -0400 Caretaker (phase 5) …
87eeaf48 2026-10-17 18:17:28 -0400 Phase 4: full run … 834/0/0/26 of 860, scorecard v2 9.81, all suites green
15d62f50 2026-10-17 17:36:05 -0400 Use the tools fully (phase 3): MiniCheck claim checker …

$ for f in bench-*.json scorecard-v2-*.json score-*.json: git log --diff-filter=A …
bench-20261016-170841.json: 37a52136 2026-10-16 13:37:11 -0400 | mtime 13:08:41
bench-20261016-173117.json: 37a52136 … | mtime 13:31:17
bench-20261016-181824.json: b15d9e29 2026-10-16 14:40:29 -0400 | mtime 14:18:24
bench-20261017-031703.json: 8d045a0f 2026-10-16 23:17:08 -0400 | mtime 23:17:03
bench-20261017-102655.json: bbe769cc 2026-10-17 06:33:16 -0400 | mtime 06:26:55
bench-20261017-220721.json: 87eeaf48 2026-10-17 18:17:28 -0400 | mtime 18:07:21
score-20261016-154951-server-rescore.json: 9f278eee 2026-10-16 11:50:33 -0400

$ git show --stat 87eeaf48 (non-result files)   -> only jarvis/docs/reports/QUALITY_95_PROGRESS_REPORT.md
$ git show --stat 8d045a0f (non-result files)   -> none
$ git show --stat bbe769cc (non-result files)   -> console/tests/test_bench_routes.py + docs
$ git diff --stat 87eeaf48 88ec49a2             -> caretaker/*, kernel llm_proxy.py (32), policy.py (+56), tests/test_caretaker_guard.py

$ python3 -I audit/evid.py bench-20261017-220721.json -v   (console evidence rule re-applied)
{'pass': 834, 'fail': 0, 'error': 0, 'deferred': 26} rows 860 missing case defs 0
evidence: {'integration': 658, 'mocked': 202} explicit field: {}
{('pass','integration'): 632, ('pass','mocked'): 202, ('deferred','integration'): 26}

$ (same for bench-20261017-102655.json)
{'pass': 833, 'fail': 1, 'error': 0, 'deferred': 26}; NONPASS H-042 H fail high | run 1/1: wrong verdicts: [('The city has 2 million residents.', True)]

$ scorecard.score() recomputed vs stored
bench-20261016-173117 9.73 = 9.73 | -181824 9.75 = 9.75 | -031703 9.75 = 9.75 | -102655 9.69 = 9.69 | -220721 9.81 = 9.81 | -170841 9.29 (no stored file)

$ git grep -c "def test_" kernel/tests per commit
bbe769cc 517 | 15d62f50 549 | 87eeaf48 549 | 88ec49a2 553   (+21 offset -> 538 / 570 / 574)

$ nice -n 19 podman run --rm --cpus=1 --userns=keep-id -e HOME=/tmp -v /srv/jarvis/models/nli:/nli:ro \
    -e RESEARCH_NLI_DIR=/nli/nli-deberta-v3-xsmall -v ~/hq-neko:/src:O neko-test \
    sh -c "cd /src && python jarvis/evals/neko95/bench/bench.py --id H-042 --no-save"
{"pass": 1, "fail": 0, "error": 0, "deferred": 0}
$ (same, --id H-027)
{"pass": 1, "fail": 0, "error": 0, "deferred": 0}
$ (same, -e RESEARCH_NLI_MODEL=xsmall, --id H-042)
FAIL  H-042 A past figure is not confirmed as current (real checker): run 1/1: wrong verdicts: [('The city has 2 million residents.', True)]
{"pass": 0, "fail": 1, "error": 0, "deferred": 0}
$ (no model mount, --id H-042)
ERROR H-042 …: FileNotFoundError: NLI model files not found (RESEARCH_NLI_DIR)
{"pass": 0, "fail": 0, "error": 1, "deferred": 0}
$ (no model mount, --id G-017)
{"pass": 0, "fail": 0, "error": 0, "deferred": 1}
$ podman … "cd /src/jarvis/kernel && python -m pytest tests/test_caretaker_guard.py -q -p no:cacheprovider"
4 passed, 7 subtests passed in 0.57s

$ python: platform != any cases -> 26, all with "check": "none"; grep 'def none' adapters/ -> no match

$ podman … --cpus=1 … bench.py --id J-018 --repeat 5 --no-save        (HEAD)
FAIL  J-018 …: run 1/5: HTTP 200 in 627 ms …
{"pass": 0, "fail": 1, "error": 0, "deferred": 0}       (+3 single runs: all fail)
$ git archive 87eeaf48 / HEAD -> scratchpad; J-018 --repeat 3 each
--cpus=1: prev FAIL 651 ms, head FAIL 599 ms, prev FAIL 596 ms, head FAIL 643 ms
--cpus=3: prev FAIL 513 ms, head {"pass": 1, …}
$ J-018 in committed runs: 489, 469, 496, 467, 456, 474 ms (all pass, limit 500)
```

---

## 4. Full-suite rerun at HEAD 88ec49a2 (other job)

The rerun is **finished**: the `done` file exists, and `meta.txt` reads
`commit 88ec49a2… dirty=0 start 2026-10-18T04:55:10Z`.

How it ran:
- container `--cpus=3`, with `/srv/jarvis/models/nli` mounted read-only;
- result files were written only inside the `:O` overlay, so they are discarded and are not in the repo.

| Suite | Result at HEAD 88ec49a2 (real log output) | Compared with the docs |
|---|---|---|
| Kernel unittest | `Ran 574 tests in 520.126s` / `OK (skipped=16)` | matches the static count 574; the docs and guide say 570 (538 in the stale sections) |
| Console unittest | **no readable summary**: run.sh kept only `tail -8`, and that tail holds stdout noise from an "english" migration test, not unittest's result line | docs say 93 OK (3 skipped); **not confirmed** |
| deliverables / desktop / doc_tools / jarvis_bridge / learn_vault | 36 / 115 / 9 / 20 / 18 passed | match |
| lifekit / local_team / main_agent / research / wechat_integration | 319 passed, 1 skipped / 31 / 73 / 40 / 12 passed | match (research 40 with the model mounted) |
| local_models / wake / tools / caretaker | 12 / 17 / 52 / 44 passed | match the Phase 4 and caretaker commit numbers |
| plugin/tests, tests/unit, UI | not run by the rerun | the docs' 4289/8 and 41981/4 remain without an artifact |
| **Benchmark v2 (full, 860)** | **`{"pass": 833, "fail": 1, "error": 0, "deferred": 26}`**; `FAIL J-018 Proxy: primary success relayed, key added: run 1/1: HTTP 200 in 505 ms` | the docs and guide say 834/0/0/26 |
| **Scorecard v2** | **mean 9.12**: routing 6.00, security 6.00 and speed 6.00 ("a critical case failed", J-018); desktop 9, voice 9; all others 10 | the docs say 9.81 |

**J-018 follow-up** (my own runs, `--no-save`):
- At HEAD, `--cpus=1`, alone on a quiet machine (load about 0.8): failed 1 + 3 times, at 627 ms and up.
- A/B using `git archive` copies of 87eeaf48 and HEAD in the scratchpad (since deleted):
  - `--cpus=1`: prev FAIL 651 / 596 ms; head FAIL 599 / 643 ms;
  - `--cpus=3`: prev FAIL 513 ms; head **pass**.
- **Conclusion: this is not a caretaker regression.** 88ec49a2 only adds a `caretaker/report` branch and a dispatch guard
  to `llm_proxy.py`, and the old code fails the same way.
- J-018 is a timing test with a 500 ms limit. Its recorded margins were always thin: 456-496 ms in all six committed runs.
- Because it is marked **critical**, one timing miss caps three dimensions at 6.0 and drops the mean from 9.81 to 9.12.
- **So the published 834/0 and 9.81 are not reproducible on demand here.** Whether they come out depends on CPU
  allowance and load. The progress report admits that "J-018 … flaky at 1 CPU under load", but no headline number
  mentions it.
