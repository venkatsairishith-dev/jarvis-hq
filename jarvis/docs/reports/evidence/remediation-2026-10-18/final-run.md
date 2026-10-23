# Final test run, remediation tree (2026-10-18)

Base commit 88ec49a2, local branch remediation-2026-10-18, uncommitted changes. All runs: Linux test server, rootless podman image neko-test, nice 19, throw-away overlay. NOT hardware tests.

## Run A (first full pass; started 2026-10-18T10:46Z) — base commit 88ec49a2ba056f85ae5bbcfc65509852b58ad4f0 branch remediation-2026-10-18 changed_files=89 start 2026-10-18T10:46:23Z

### kernel unittest
Ran 672 tests in 781.132s
OK (skipped=16)
### console unittest
Ran 112 tests in 35.585s
OK (skipped=3)
### plugin / local_models / wake / tools / caretaker / lab suites
plugin/plugins/deliverables/tests: 36 passed in 4.42s
plugin/plugins/desktop/tests: 165 passed in 40.49s
plugin/plugins/doc_tools/tests: 10 passed in 1.38s
plugin/plugins/jarvis_bridge/tests: 23 passed in 7.80s
plugin/plugins/learn_vault/tests: 18 passed in 0.82s
plugin/plugins/lifekit/tests: 319 passed, 1 skipped in 5.11s
plugin/plugins/local_team/tests: 31 passed in 0.82s
plugin/plugins/main_agent/tests: 104 passed in 12.05s
plugin/plugins/research/tests: 40 passed in 15.08s
plugin/plugins/wechat_integration/tests: 12 passed in 1.04s
jarvis/local_models: 12 passed in 0.60s
jarvis/wake/test_wake_listener.py: 26 passed in 3.37s
jarvis/tools: 53 passed in 14.86s
jarvis/caretaker/tests: 44 passed in 49.10s
jarvis/lab/tests: 14 passed, 13 skipped in 1.84s
### plugin/tests (N.E.K.O)
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[desktop]
8 failed, 4289 passed, 5 skipped, 42 warnings in 140.63s (0:02:20)
### tests/unit (N.E.K.O) — one NEW failure at this point: test_recall_render_token_budget (the planner-memory module rendered its own lines); fixed afterwards, see run B
=========================== short test summary info ============================
FAILED tests/unit/test_cloudsave_runtime.py::test_clearing_read_only_keeps_a_directory_traversable
FAILED tests/unit/test_knowledge_subsystem.py::test_cancelled_request_still_finishes_admission
FAILED tests/unit/test_recall_render_token_budget.py::test_every_query_memory_client_renders_through_the_entry_point
FAILED tests/unit/voice_identity_service/test_service_activation.py::test_public_guards_and_status_shape
FAILED tests/unit/test_builtin_plugin_packaged_metadata.py::test_builtin_plugin_packaged_metadata_matches_source_tree
5 failed, 41980 passed, 176 skipped, 185 warnings in 537.19s (0:08:57)
### claim checker (MiniCheck default)
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
### benchmark v2, unsaved
{"pass": 834, "fail": 0, "error": 0, "deferred": 26}

## Run B (after the recall-render fix; base commit 88ec49a2ba056f85ae5bbcfc65509852b58ad4f0 changed_files=90 start 2026-10-18T11:27:09Z)
### benchmark v2 saved + scorecard
{"pass": 834, "fail": 0, "error": 0, "deferred": 26}
saved /src/jarvis/evals/neko95/results/bench-20261018-113507.json
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
saved /src/jarvis/evals/neko95/results/scorecard-v2-20261018-113508.json
### tests/unit (N.E.K.O)
=========================== short test summary info ============================
FAILED tests/unit/test_cloudsave_runtime.py::test_clearing_read_only_keeps_a_directory_traversable
FAILED tests/unit/test_knowledge_subsystem.py::test_cancelled_request_still_finishes_admission
FAILED tests/unit/voice_identity_service/test_service_activation.py::test_public_guards_and_status_shape
FAILED tests/unit/test_builtin_plugin_packaged_metadata.py::test_builtin_plugin_packaged_metadata_matches_source_tree
4 failed, 41981 passed, 176 skipped, 184 warnings in 545.47s (0:09:05)
### main_agent + tools after the fix
..............                                                           [100%]
158 passed in 28.07s
