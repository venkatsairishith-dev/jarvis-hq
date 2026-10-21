# Final test run, round 2 (2026-10-18, after the second set of fixes)

Base commit 88ec49a2, local branch remediation-2026-10-18, uncommitted. Linux test server, rootless podman image neko-test, nice 19, throw-away overlay. NOT hardware tests.

## Run C: base commit 88ec49a2ba056f85ae5bbcfc65509852b58ad4f0 changed_files=171 start 2026-10-18T13:02:08Z
### kernel unittest
Ran 717 tests in 470.126s
OK (skipped=16)
### console unittest
Ran 127 tests in 40.593s
OK (skipped=3)
### suites
plugin/plugins/deliverables/tests: 36 passed in 4.52s
plugin/plugins/desktop/tests: 256 passed in 45.28s
plugin/plugins/doc_tools/tests: 10 passed in 1.41s
plugin/plugins/jarvis_bridge/tests: 27 passed in 10.04s
plugin/plugins/learn_vault/tests: 18 passed in 0.82s
plugin/plugins/lifekit/tests: 319 passed, 1 skipped in 5.20s
plugin/plugins/local_team/tests: 31 passed in 0.82s
plugin/plugins/main_agent/tests: 128 passed in 17.69s
plugin/plugins/research/tests: 46 passed in 15.30s
plugin/plugins/wechat_integration/tests: 12 passed in 1.05s
jarvis/local_models: 19 passed in 0.64s
jarvis/wake/test_wake_listener.py: 48 passed in 4.86s
jarvis/tools: 53 passed in 14.29s
jarvis/caretaker/tests: 44 passed in 47.67s
jarvis/lab/tests: 27 passed, 13 skipped in 12.11s
jarvis/evals/neko95/tests: 14 passed in 0.85s
jarvis/privacy/test_privacy.py: 1 error in 0.66s
(jarvis/privacy/test_privacy.py cannot be collected here: presidio is not installed. With presidio stubbed (run_privacy_tests.py): ran=35 failures=12 skipped=9; all 12 need the real presidio, including the new Groq/Stripe/PGP test.)
### plugin/tests (N.E.K.O)
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[desktop]
8 failed, 4289 passed, 5 skipped, 42 warnings in 141.97s (0:02:21)
### tests/unit (N.E.K.O)
=========================== short test summary info ============================
FAILED tests/unit/test_cloudsave_runtime.py::test_clearing_read_only_keeps_a_directory_traversable
FAILED tests/unit/test_knowledge_subsystem.py::test_cancelled_request_still_finishes_admission
FAILED tests/unit/voice_identity_service/test_service_activation.py::test_public_guards_and_status_shape
FAILED tests/unit/test_builtin_plugin_packaged_metadata.py::test_builtin_plugin_packaged_metadata_matches_source_tree
4 failed, 42017 passed, 176 skipped, 185 warnings in 523.94s (0:08:43)
### claim checker
 "precision": 1.0,
 "recall": 1.0,
 "false_acceptance": "0/46",
 "false_rejection": "0/32",
 "precision": 1.0,
 "recall": 1.0,
 "false_acceptance": "0/18",
 "false_rejection": "0/18",
 "precision": 0.94,
 "recall": 0.71,
 "false_acceptance": "9/200",
 "false_rejection": "58/200",
### benchmark v2 saved + scorecard in Run C — FIVE troubleshooter cases FAILED here (E-026/027/028/044/045); scorecard 9.12
FAIL  E-026 Troubleshooter: approved fix, verified by restart: run 1/1: outcome 'None', approvals asked 0, repairs run 0, states [], local files left 0, verification None
FAIL  E-027 Troubleshooter: denied fix changes nothing: run 1/1: outcome 'None', approvals asked 0, repairs run 0, states [], local files left 0, verification None
FAIL  E-028 Troubleshooter: fix applied but the error stays: run 1/1: outcome 'None', approvals asked 0, repairs run 0, states [], local files left 0, verification None
FAIL  E-044 Troubleshooter: empty screen read is a hypothesis, not a fix: run 1/1:  outcome None, cause 'None', approvals 0, repairs 0, files left 0
FAIL  E-045 Troubleshooter: malformed check after the fix keeps the undo: run 1/1: repairs 0, backups on disk 0, report backup None, undo None, outcome None, files left 0
{"pass": 829, "fail": 5, "error": 0, "deferred": 26}
saved /src/jarvis/evals/neko95/results/bench-20261018-133053.json
intent       10.00 verified    74/74 passed, 1 deferred

Cause: the benchmark's fake console had no page, but the desktop client now reads the console's per-launch secret from its page before any write (SEC-D9). Fixed in the fake server (jarvis/evals/neko95/bench/adapters/e.py _serve); case files untouched. Area E alone: 56 pass.
### UI (Linux Chrome in the container; the shipped Playwright config is Windows-only)
TSC_OK
[2m Test Files [22m [1m[32m4 passed[39m[22m[90m (4)[39m
[2m      Tests [22m [1m[32m21 passed[39m[22m[90m (21)[39m
✓ built in 245ms
console e2e: 45 passed, 2 failed (Linux environment: no voice engine so /api/voice/speak answers 503; the Alt+0 shortcut does not fire in Linux Chrome; a Windows cmd.exe connector form did not run); bench e2e 5 passed (phone viewport)

## Run D (after the fix): base commit 88ec49a2ba056f85ae5bbcfc65509852b58ad4f0 changed_files=175 start 2026-10-18T13:40:19Z
{"pass": 834, "fail": 0, "error": 0, "deferred": 26}
saved /src/jarvis/evals/neko95/results/bench-20261018-134602.json
intent       10.00 verified    74/74 passed, 1 deferred
planning     10.00 verified    70/70 passed, 1 deferred
desktop       9.00 verified    32/32 passed, 5 deferred (critical cases deferred)
browser      10.00 verified    85/85 passed, 2 deferred
autonomy     10.00 verified    74/74 passed, 1 deferred
recovery     10.00 verified    139/139 passed, 3 deferred
research     10.00 verified    72/72 passed, 1 deferred
memory       10.00 verified    61/61 passed, 1 deferred
routing      10.00 verified    78/78 passed, 0 deferred
security      9.00 verified    327/327 passed, 4 deferred (critical cases deferred)
reliability  10.00 verified    249/249 passed, 0 deferred
speed        10.00 verified    24/24 passed, 0 deferred
resources    10.00 verified    81/81 passed, 2 deferred
skills       10.00 verified    39/39 passed, 0 deferred
ux           10.00 verified    123/123 passed, 2 deferred
voice         9.00 verified    28/28 passed, 9 deferred (critical cases deferred)
mean of scored dimensions: 9.81 | unverified: []
saved /src/jarvis/evals/neko95/results/scorecard-v2-20261018-134603.json
