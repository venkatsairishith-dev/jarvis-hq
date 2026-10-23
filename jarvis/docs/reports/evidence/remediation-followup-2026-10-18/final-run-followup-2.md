# Final runs, second follow-up pass (run 12 and isolation comparisons), 2026-10-18

Base 88ec49a2, local branch remediation-2026-10-18, uncommitted. Linux test server, rootless podman image neko-test, nice 19, throw-away overlay (:O), --cpus=2 (isolation comparisons --cpus=1). NOT hardware tests. Script: scratchpad final12/run.sh.

## final12/meta.txt
```
start 2026-10-18T20:11:22Z head 88ec49a2 porcelain_lines 213
end 2026-10-18T21:05:53Z
```

## final12/kernel.log
```
exit=0
Ran 791 tests in 570.697s
OK (skipped=16)
```

## final12/console.log
```
exit=0
Ran 149 tests in 47.807s
OK (skipped=3)
```

## final12/suites.log
```
plugin/plugins/_shared/tests: rc=0 38 passed, 2 skipped in 15.44s
plugin/plugins/deliverables/tests: rc=0 36 passed in 4.48s
plugin/plugins/desktop/tests: rc=0 361 passed in 90.47s (0:01:30)
plugin/plugins/doc_tools/tests: rc=0 10 passed in 1.40s
plugin/plugins/jarvis_bridge/tests: rc=0 27 passed in 10.01s
plugin/plugins/learn_vault/tests: rc=0 18 passed in 0.84s
plugin/plugins/lifekit/tests: rc=0 319 passed, 1 skipped in 5.16s
plugin/plugins/local_team/tests: rc=0 32 passed in 0.83s
plugin/plugins/main_agent/tests: rc=0 207 passed in 33.10s
plugin/plugins/research/tests: rc=0 63 passed, 2 skipped in 54.34s
plugin/plugins/wechat_integration/tests: rc=0 12 passed in 1.05s
jarvis/local_models: rc=0 19 passed in 0.62s
jarvis/wake/test_wake_listener.py: rc=0 54 passed in 6.66s
jarvis/tools: rc=0 53 passed in 23.51s
jarvis/caretaker/tests: rc=0 44 passed in 47.82s
jarvis/lab/tests: rc=0 65 passed, 13 skipped, 7 subtests passed in 51.45s
jarvis/evals/neko95/tests: rc=0 14 passed in 0.85s
jarvis/privacy: rc=0 12 passed, 1 skipped in 4.75s
```

## final12/privacy_real.log
```
rc=0
SKIPPED [1] jarvis/privacy/test_privacy.py:164: GLiNER-PII model not installed
SKIPPED [1] jarvis/privacy/test_privacy.py:173: GLiNER-PII model not installed
SKIPPED [1] jarvis/privacy/test_privacy.py:126: GLiNER-PII model not installed
SKIPPED [1] jarvis/privacy/test_privacy.py:151: GLiNER-PII model not installed
SKIPPED [1] jarvis/privacy/test_privacy.py:143: GLiNER-PII model not installed
SKIPPED [1] jarvis/privacy/test_privacy.py:147: GLiNER-PII model not installed
SKIPPED [1] jarvis/privacy/test_privacy.py:155: GLiNER-PII model not installed
105 passed, 9 skipped, 8 warnings in 38.86s
```

## final12/plugin_tests.log
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
9 failed, 4288 passed, 5 skipped, 28 warnings in 267.74s (0:04:27)
```

## final12/tests_unit.log
```
rc=1
FAILED tests/unit/test_cloudsave_runtime.py::test_clearing_read_only_keeps_a_directory_traversable
FAILED tests/unit/test_knowledge_subsystem.py::test_cancelled_request_still_finishes_admission
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_failures_stay_inside_shadow_and_have_one_terminal_state[callback]
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_explicit_checkpoints_emit_1500ms_and_3000ms_observations
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_reset_cancels_stale_observation_delivery
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_close_retries_after_callback_consumes_first_cancellation
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_checkpoint_accepts_pcm_while_intermediate_score_is_running
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_permanently_blocked_backend_close_is_bounded_and_leaves_no_resources[load]
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_permanently_blocked_backend_close_is_bounded_and_leaves_no_resources[score]
FAILED tests/unit/test_game_vision_service.py::test_timeout_ignoring_provider_retains_bounded_capacity
FAILED tests/unit/voice_identity_service/test_service_activation.py::test_public_guards_and_status_shape
FAILED tests/unit/test_builtin_plugin_packaged_metadata.py::test_builtin_plugin_packaged_metadata_matches_source_tree
12 failed, 42012 passed, 176 skipped, 129 warnings in 1106.98s (0:18:26)
```

## final12/bench.log
```
rc=1
FAIL  K-106 A cut approval prompt says so, and the approval binds the full text: run 1/1: prompt says truncated=False; changed tail -> approval is for a different action; exact action -> True
{"pass": 833, "fail": 1, "error": 0, "deferred": 26}
saved /src/jarvis/evals/neko95/results/bench-20261018-205900.json
intent       10.00 verified    74/74 passed, 1 deferred
planning     10.00 verified    70/70 passed, 1 deferred
desktop       9.00 verified    32/32 passed, 5 deferred (critical cases deferred)
browser      10.00 verified    85/85 passed, 2 deferred
autonomy     10.00 verified    74/74 passed, 1 deferred
recovery     10.00 verified    139/139 passed, 3 deferred
research     10.00 verified    72/72 passed, 1 deferred
memory       10.00 verified    61/61 passed, 1 deferred
routing      10.00 verified    78/78 passed, 0 deferred
security      6.00 verified    326/327 passed, 4 deferred; failed ['K-106'] (a critical case failed; critical cases deferred)
reliability  10.00 verified    249/249 passed, 0 deferred
speed        10.00 verified    24/24 passed, 0 deferred
resources    10.00 verified    81/81 passed, 2 deferred
skills       10.00 verified    39/39 passed, 0 deferred
ux           10.00 verified    123/123 passed, 2 deferred
voice         9.00 verified    28/28 passed, 9 deferred (critical cases deferred)
mean of scored dimensions: 9.62 | unverified: []
saved /src/jarvis/evals/neko95/results/scorecard-v2-20261018-205901.json
```

## final12/claims.log
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

## final13/isolation.log
```
== final
83 passed, 2 warnings in 43.12s
--
83 passed, 2 warnings in 43.08s
--
== base
83 passed, 2 warnings in 43.16s
--
83 passed, 2 warnings in 43.29s
--
```

## final12 UI (tsc, vitest, build, console e2e, bench e2e)
```
TSC_OK
[2m Test Files [22m [1m[32m5 passed[39m[22m[90m (5)[39m
[2m      Tests [22m [1m[32m33 passed[39m[22m[90m (33)[39m
✓ built in 428ms
  ✓  22 [app] › e2e/app.spec.ts:22:3 › screens and navigation › all ten screens render and navigate, with no uncaught errors or failed API calls (1.9s)
    Error: expect(locator).toBeVisible() failed
  2 failed
    [app] › e2e/app.spec.ts:59:3 › command center › a typed command reaches JARVIS and the answer is shown with its source 
    [app] › e2e/connectors.spec.ts:23:1 › an empty catalogue explains itself, and Alt+0 opens the screen 
  8 did not run
  45 passed (1.7m)
[2m Test Files [22m [1m[32m5 passed[39m[22m[90m (5)[39m
[2m      Tests [22m [1m[32m33 passed[39m[22m[90m (33)[39m
  5 passed (22.6s)
```
