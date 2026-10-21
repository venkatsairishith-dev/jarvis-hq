# Third pass: final-tree runs (run 16) and base comparisons, 2026-10-19

Base 88ec49a2, local branch remediation-2026-10-18, uncommitted. Linux test server, rootless podman image neko-test, nice 19, throw-away overlay. NOT hardware tests.

## final15/meta.txt
```
start 2026-10-19T07:39:02Z head 88ec49a2 porcelain_lines 221
end 2026-10-19T08:42:32Z
```

## final15/kernel.log
```
exit=0
Ran 808 tests in 587.648s
OK (skipped=16)
```

## final15/console.log
```
exit=0
Ran 149 tests in 47.787s
OK (skipped=3)
```

## final15/suites.log
```
plugin/plugins/_shared/tests: rc=0 38 passed, 2 skipped in 15.43s
plugin/plugins/deliverables/tests: rc=0 36 passed in 4.43s
plugin/plugins/desktop/tests: rc=0 376 passed in 98.23s (0:01:38)
plugin/plugins/doc_tools/tests: rc=0 10 passed in 1.38s
plugin/plugins/jarvis_bridge/tests: rc=0 27 passed in 9.99s
plugin/plugins/learn_vault/tests: rc=0 18 passed in 0.81s
plugin/plugins/lifekit/tests: rc=0 319 passed, 1 skipped in 5.08s
plugin/plugins/local_team/tests: rc=0 32 passed in 0.85s
plugin/plugins/main_agent/tests: rc=0 207 passed in 33.12s
plugin/plugins/research/tests: rc=0 63 passed, 2 skipped in 50.70s
plugin/plugins/wechat_integration/tests: rc=0 12 passed in 1.04s
jarvis/local_models: rc=0 19 passed in 0.62s
jarvis/wake/test_wake_listener.py: rc=0 54 passed in 6.69s
jarvis/tools: rc=0 53 passed in 23.64s
jarvis/caretaker/tests: rc=0 44 passed in 47.63s
jarvis/lab/tests: rc=0 108 passed, 14 skipped, 19 subtests passed in 46.31s
jarvis/evals/neko95/tests: rc=0 32 passed in 34.93s
jarvis/privacy: rc=0 12 passed, 1 skipped in 4.75s
```

## final15/desktop_order.log
```
desktop file order rc=0
376 passed in 98.01s (0:01:38)
37 passed in 22.55s
37 passed in 22.56s
37 passed in 22.58s
37 passed in 22.54s
37 passed in 22.56s
37 passed in 22.60s
37 passed in 22.59s
37 passed in 22.60s
37 passed in 22.55s
37 passed in 22.53s
```

## final15/lab_root.log
```
lab root(nobody) rc=0
SKIPPED [1] jarvis/lab/tests/test_lab_runner.py:149: lab box only, as root
108 passed, 14 skipped, 19 subtests passed in 46.32s
lab root(real agent user) rc=0
SKIPPED [1] jarvis/lab/tests/test_verdict_integrity.py:692: root can open any file
121 passed, 1 skipped, 19 subtests passed in 56.32s
```

## final15/privacy_real.log
```
rc=0
SKIPPED [1] jarvis/privacy/test_privacy.py:147: GLiNER-PII model not installed
SKIPPED [1] jarvis/privacy/test_privacy.py:155: GLiNER-PII model not installed
105 passed, 9 skipped, 8 warnings in 38.32s
```

## final15/plugin_tests.log
```
```

## final15/tests_unit.log
```
rc=1
tests/unit/test_offline_reply_ownership.py::test_a_voice_reply_cut_in_a_discard_keeps_no_discarded_text[stream_failed]
FAILED tests/unit/test_cloudsave_runtime.py::test_clearing_read_only_keeps_a_directory_traversable
FAILED tests/unit/test_knowledge_subsystem.py::test_cancelled_request_still_finishes_admission
FAILED tests/unit/test_trust_store.py::test_signal_ledger_normalizes_in_linear_time
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_explicit_checkpoints_emit_1500ms_and_3000ms_observations
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_reset_cancels_stale_observation_delivery
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_close_retries_after_callback_consumes_first_cancellation
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_checkpoint_accepts_pcm_while_intermediate_score_is_running
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_permanently_blocked_backend_close_is_bounded_and_leaves_no_resources[load]
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_permanently_blocked_backend_close_is_bounded_and_leaves_no_resources[score]
FAILED tests/unit/voice_identity_service/test_service_activation.py::test_public_guards_and_status_shape
FAILED tests/unit/test_builtin_plugin_packaged_metadata.py::test_builtin_plugin_packaged_metadata_matches_source_tree
11 failed, 42013 passed, 176 skipped, 128 warnings in 1160.14s (0:19:20)
```

## final15/bench.log
```
rc=0
{"pass": 834, "fail": 0, "error": 0, "deferred": 26}
saved /src/jarvis/evals/neko95/results/bench-20261019-083512.json
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
saved /src/jarvis/evals/neko95/results/scorecard-v2-20261019-083513.json
```

## final15/claims.log
```
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
```

## final16/followup.log
```
== plugin/tests (current tree, -n 3)
rc=1
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[research]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[local_team]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[deliverables]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[jarvis_bridge]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[main_agent]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[learn_vault]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[doc_tools]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[desktop]
FAILED plugin/tests/unit/server/test_plugin_metadata_scanner.py::test_metadata_scan_reaps_plugin_spawned_helpers[timeout]
9 failed, 4288 passed, 5 skipped, 28 warnings in 288.19s (0:04:48)
== isolation final
242 passed, 7 warnings in 38.88s
--
242 passed, 7 warnings in 37.76s
--
242 passed, 7 warnings in 37.97s
--
== isolation base
242 passed, 7 warnings in 38.93s
--
242 passed, 7 warnings in 37.86s
--
242 passed, 7 warnings in 38.04s
--
```

## final16/base_plugin_tests.log
```
rc=1
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[research]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[local_team]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[deliverables]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[jarvis_bridge]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[main_agent]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[learn_vault]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[doc_tools]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[desktop]
FAILED plugin/tests/unit/server/test_plugin_metadata_scanner.py::test_metadata_scan_reaps_plugin_spawned_helpers[timeout]
9 failed, 4288 passed, 5 skipped, 28 warnings in 279.91s (0:04:39)
```

## final16/base_tests_unit.log
```
rc=1
tests/unit/voice_identity_service/tse/test_assets.py::test_failed_reimport_preserves_existing_snapshot_and_files
FAILED tests/unit/test_check_prompt_zh_tw.py::test_cli_against_head_is_clean
FAILED tests/unit/test_cloudsave_runtime.py::test_clearing_read_only_keeps_a_directory_traversable
FAILED tests/unit/test_knowledge_subsystem.py::test_cancelled_request_still_finishes_admission
FAILED tests/unit/test_trust_store.py::test_signal_ledger_normalizes_in_linear_time
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_close_retries_after_callback_consumes_first_cancellation
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_checkpoint_accepts_pcm_while_intermediate_score_is_running
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_permanently_blocked_backend_close_is_bounded_and_leaves_no_resources[load]
FAILED tests/unit/test_emotion_reaction_evaluation.py::test_real_baseline_and_runtime_have_the_same_eight_languages
FAILED tests/unit/test_check_no_nonascii_asset_names.py::test_live_repo_passes
FAILED tests/unit/test_game_vision_service.py::test_timeout_ignoring_provider_retains_bounded_capacity
FAILED tests/unit/test_prepare_nuitka_plugins.py::test_no_bundled_plugin_ships_a_dotted_directory_besides_github
FAILED tests/unit/voice_identity_service/test_service_activation.py::test_public_guards_and_status_shape
12 failed, 41968 passed, 181 skipped, 128 warnings in 945.14s (0:15:45)
```

## UI (final15)
```
TSC_OK
[2m Test Files [22m [1m[32m5 passed[39m[22m[90m (5)[39m
[2m      Tests [22m [1m[32m33 passed[39m[22m[90m (33)[39m
✓ built in 427ms
  ✓  22 [app] › e2e/app.spec.ts:22:3 › screens and navigation › all ten screens render and navigate, with no uncaught errors or failed API calls (2.0s)
    Error: expect(locator).toContainText(expected) failed
  2 failed
    [app] › e2e/app.spec.ts:59:3 › command center › a typed command reaches JARVIS and the answer is shown with its source 
    [app] › e2e/connectors.spec.ts:31:1 › a program that is not allowed is refused in the form and nothing is added 
  7 did not run
  46 passed (1.8m)
[2m Test Files [22m [1m[32m5 passed[39m[22m[90m (5)[39m
[2m      Tests [22m [1m[32m33 passed[39m[22m[90m (33)[39m
  5 passed (21.2s)
```
