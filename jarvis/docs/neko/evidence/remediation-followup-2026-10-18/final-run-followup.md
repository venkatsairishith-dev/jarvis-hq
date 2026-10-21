# Final runs of the follow-up pass (2026-10-18)

Base 88ec49a2, local branch remediation-2026-10-18, uncommitted. Linux test server, rootless podman image neko-test, nice 19, throw-away overlay. NOT hardware tests. Scripts: scratchpad final9/run.sh, final10/unit.sh, final11/run.sh (same shape as final7/run.sh of round 2).

## final9/meta.txt
```
base commit 88ec49a2ba056f85ae5bbcfc65509852b58ad4f0 changed_files=196 start 2026-10-18T15:48:10Z
end 2026-10-18T16:55:44Z
```

## final9/kernel.log
```
exit=1
FAIL: test_a_kill_during_a_stream_stops_the_relay_between_chunks (test_llm_proxy.KillSwitchBetweenSteps.test_a_kill_during_a_stream_stops_the_relay_between_chunks)
Ran 768 tests in 561.286s
FAILED (failures=1, skipped=16)
```

## final9/console.log
```
exit=0
Ran 149 tests in 47.789s
OK (skipped=3)
```

## final9/suites.log
```
plugin/plugins/deliverables/tests: 36 passed in 4.46s
plugin/plugins/desktop/tests: 320 passed in 77.73s (0:01:17)
plugin/plugins/doc_tools/tests: 10 passed in 1.40s
plugin/plugins/jarvis_bridge/tests: 27 passed in 10.03s
plugin/plugins/learn_vault/tests: 18 passed in 0.82s
plugin/plugins/lifekit/tests: 319 passed, 1 skipped in 5.18s
plugin/plugins/local_team/tests: 32 passed in 0.84s
plugin/plugins/main_agent/tests: 163 passed in 19.17s
plugin/plugins/research/tests: 59 passed, 2 skipped in 45.72s
plugin/plugins/wechat_integration/tests: 12 passed in 1.04s
jarvis/local_models: 19 passed in 0.62s
jarvis/wake/test_wake_listener.py: 54 passed in 6.64s
jarvis/tools: 53 passed in 23.75s
jarvis/caretaker/tests: 44 passed in 47.90s
jarvis/lab/tests: 41 passed, 13 skipped in 18.65s
jarvis/evals/neko95/tests: 14 passed in 0.86s
jarvis/privacy/test_privacy.py: 
```

## final9/privacy_real.log
```
..........sssssssss..............................                        [100%]
40 passed, 9 skipped in 12.78s
```

## final9/claims.log
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

## final10/tests_unit_full.log
```
                raise RuntimeError("first checkpoint callback failed")
                raise RuntimeError("callback failed")
            for reason in ("scored", "insufficient", "dropped", "failed")
                # In case task cancellation failed with some
                # In case task cancellation failed with some
FAILED tests/unit/test_cloudsave_runtime.py::test_clearing_read_only_keeps_a_directory_traversable
FAILED tests/unit/test_knowledge_subsystem.py::test_cancelled_request_still_finishes_admission
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_checkpoint_callback_failure_does_not_block_confirmation
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_failures_stay_inside_shadow_and_have_one_terminal_state[callback]
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_permanently_blocked_backend_close_is_bounded_and_leaves_no_resources[close]
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_explicit_checkpoints_emit_1500ms_and_3000ms_observations
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_close_error_is_fail_open_and_next_candidate_can_reload
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_close_cancels_in_flight_observation_and_reaps_host
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_reset_discards_in_flight_result_by_generation
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_ordered_finish_scores_once_and_rejects_late_pcm
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_permanently_blocked_backend_close_is_bounded_and_leaves_no_resources[score]
FAILED tests/unit/voice_identity_service/test_service_activation.py::test_public_guards_and_status_shape
FAILED tests/unit/test_builtin_plugin_packaged_metadata.py::test_builtin_plugin_packaged_metadata_matches_source_tree
13 failed, 42011 passed, 176 skipped, 155 warnings in 1201.67s (0:20:01)
```

## final11/meta.txt
```
start 2026-10-18T17:24:45Z head 88ec49a2 status_lines 198
end 2026-10-18T17:45:37Z
```

## final11/kernel.log
```
exit=0
Ran 768 tests in 560.858s
OK (skipped=16)
```

## final11/console.log
```
exit=0
Ran 149 tests in 48.773s
OK (skipped=3)
```

## final11/suites.log
```
plugin/plugins/desktop/tests: 321 passed in 77.80s (0:01:17)
plugin/plugins/main_agent/tests: 163 passed in 20.32s
plugin/plugins/jarvis_bridge/tests: 27 passed in 10.00s
plugin/plugins/research/tests: 59 passed, 2 skipped in 52.13s
jarvis/caretaker/tests: 44 passed in 47.76s
jarvis/lab/tests: 41 passed, 13 skipped in 18.66s
jarvis/wake/test_wake_listener.py: 54 passed in 6.68s
```

## final11/bench.log
```
FAIL  K-106 A cut approval prompt says so, and the approval binds the full text: run 1/1: prompt says truncated=False; changed tail -> approval is for a different action; exact action -> True
{"pass": 833, "fail": 1, "error": 0, "deferred": 26}
saved /src/jarvis/evals/neko95/results/bench-20261018-174537.json
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
saved /src/jarvis/evals/neko95/results/scorecard-v2-20261018-174537.json
```

## final9/plugin_tests.log (tail)
```
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[local_team]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[deliverables]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[jarvis_bridge]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[main_agent]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[learn_vault]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[doc_tools]
FAILED plugin/tests/unit/plugins/test_bundled_config_schemas.py::test_packaged_metadata_stays_valid_after_schema_migration[desktop]
FAILED plugin/tests/unit/server/test_development_build.py::test_cancelled_build_does_not_publish_later
FAILED plugin/tests/unit/server/test_plugin_metadata_scanner.py::test_metadata_scan_reaps_plugin_spawned_helpers[timeout]
10 failed, 4287 passed, 5 skipped, 42 warnings in 502.90s (0:08:22)2026-10-18 16:03:07 - N.E.K.O.utils.api_config_loader - INFO - 成功加载配置文件: /src/config/api_providers.json
```

## final10 UI e2e summary
```
[2m Test Files [22m [1m[32m5 passed[39m[22m[90m (5)[39m
[2m      Tests [22m [1m[32m33 passed[39m[22m[90m (33)[39m
  ✓  22 [app] › e2e/app.spec.ts:22:3 › screens and navigation › all ten screens render and navigate, with no uncaught errors or failed API calls (2.0s)
    Error: expect(locator).toBeVisible() failed
  2 failed
    [app] › e2e/app.spec.ts:59:3 › command center › a typed command reaches JARVIS and the answer is shown with its source 
    [app] › e2e/connectors.spec.ts:23:1 › an empty catalogue explains itself, and Alt+0 opens the screen 
  8 did not run
  45 passed (1.7m)
```
