# Pass 4 final-tree runs (2026-10-19)

Base 88ec49a2, local branch remediation-2026-10-18, uncommitted. Linux test server, rootless podman image neko-test, nice 19, throw-away overlay. NOT hardware tests. Scripts: scratchpad final18/run.sh (+ final17 lock stress, final19 e2e rerun).

## final18/meta.txt
```
start 2026-10-19T13:06:35Z head 88ec49a2 porcelain_lines 259
end 2026-10-19T14:16:40Z
```

## final18/kernel.log
```
exit=0
Ran 866 tests in 618.408s
OK (skipped=22)
```

## final18/console.log
```
exit=0
Ran 176 tests in 62.886s
OK (skipped=3)
```

## final18/suites.log
```
plugin/plugins/_shared/tests: rc=0 99 passed, 2 skipped in 36.17s
plugin/plugins/deliverables/tests: rc=0 36 passed in 4.45s
plugin/plugins/desktop/tests: rc=0 397 passed in 101.09s (0:01:41)
plugin/plugins/doc_tools/tests: rc=0 10 passed in 1.39s
plugin/plugins/jarvis_bridge/tests: rc=0 31 passed in 10.66s
plugin/plugins/learn_vault/tests: rc=0 18 passed in 0.84s
plugin/plugins/lifekit/tests: rc=0 319 passed, 1 skipped in 5.15s
plugin/plugins/local_team/tests: rc=0 32 passed in 0.82s
plugin/plugins/main_agent/tests: rc=0 364 passed in 44.45s
plugin/plugins/research/tests: rc=0 63 passed, 2 skipped in 57.73s
plugin/plugins/wechat_integration/tests: rc=0 12 passed in 1.06s
jarvis/local_models: rc=0 19 passed in 0.63s
jarvis/wake/test_wake_listener.py: rc=0 62 passed in 6.81s
jarvis/tools: rc=0 53 passed in 23.33s
jarvis/caretaker/tests: rc=0 44 passed in 47.95s
jarvis/evals/neko95/tests: rc=0 32 passed in 38.89s
jarvis/privacy: rc=0 12 passed, 1 skipped in 4.74s
```

## final18/desktop_order.log
```
desktop file order rc=0
397 passed in 101.29s (0:01:41)
37 passed in 22.76s
37 passed in 22.72s
37 passed in 22.78s
37 passed in 22.77s
37 passed in 22.72s
37 passed in 22.70s
37 passed in 22.74s
37 passed in 22.71s
37 passed in 22.74s
37 passed in 22.74s
37 passed in 22.70s
37 passed in 22.77s
37 passed in 22.68s
37 passed in 22.70s
37 passed in 22.77s
37 passed in 22.73s
37 passed in 22.72s
37 passed in 22.75s
37 passed in 22.75s
37 passed in 22.74s
```

## final18/lab_modes.log
```
lab keep-id rc=0
132 passed, 45 skipped, 83 subtests passed in 46.76s
lab root no separate users rc=0
132 passed, 45 skipped, 83 subtests passed in 46.66s
lab root with agent+jarvis-sandbox+jarvis-verify rc=0
SKIPPED [1] jarvis/lab/tests/test_verdict_integrity.py:716: root can open any file
176 passed, 1 skipped, 100 subtests passed in 83.83s (0:01:23)
kernel lab_report+tasks as root rc=0
Ran 66 tests in 43.894s
OK
```

## final18/privacy_real.log
```
rc=1
SKIPPED [1] jarvis/privacy/test_privacy.py:147: GLiNER-PII model not installed
SKIPPED [1] jarvis/privacy/test_privacy.py:155: GLiNER-PII model not installed
1 failed, 165 passed, 9 skipped, 8 warnings in 60.15s (0:01:00)
```

## final18/plugin_tests.log
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
9 failed, 4288 passed, 5 skipped, 28 warnings in 297.68s (0:04:57)
```

## final18/tests_unit.log
```
rc=1
      queued, bypassed = await self._next_queued()
FAILED tests/unit/test_cloudsave_runtime.py::test_clearing_read_only_keeps_a_directory_traversable
FAILED tests/unit/test_knowledge_subsystem.py::test_cancelled_request_still_finishes_admission
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_permanently_blocked_backend_close_is_bounded_and_leaves_no_resources[close]
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_reset_discards_in_flight_result_by_generation
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_reset_cancels_stale_observation_delivery
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_close_retries_after_callback_consumes_first_cancellation
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_checkpoint_accepts_pcm_while_intermediate_score_is_running
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_permanently_blocked_backend_close_is_bounded_and_leaves_no_resources[load]
FAILED tests/unit/asr_client/speaker_shadow/test_runtime.py::test_permanently_blocked_backend_close_is_bounded_and_leaves_no_resources[score]
FAILED tests/unit/voice_identity_service/test_service_activation.py::test_public_guards_and_status_shape
FAILED tests/unit/test_builtin_plugin_packaged_metadata.py::test_builtin_plugin_packaged_metadata_matches_source_tree
11 failed, 42013 passed, 176 skipped, 129 warnings in 1106.85s (0:18:26)
```

## final18/bench.log
```
rc=0
{"pass": 834, "fail": 0, "error": 0, "deferred": 26}
saved /src/jarvis/evals/neko95/results/bench-20261019-141001.json
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
saved /src/jarvis/evals/neko95/results/scorecard-v2-20261019-141001.json
```

## final18/claims.log
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

## final17/lockstress.log
```
run 1 rc=0 37 passed in 23.12s
run 2 rc=0 37 passed in 23.19s
run 3 rc=0 37 passed in 23.24s
run 4 rc=0 37 passed in 23.16s
run 5 rc=0 37 passed in 23.18s
run 6 rc=0 37 passed in 23.18s
run 7 rc=0 37 passed in 23.13s
run 8 rc=0 37 passed in 23.19s
run 9 rc=1 1 failed, 10 passed in 7.45s
---FAIL 9
..........F
=================================== FAILURES ===================================
_ test_a_cancelled_task_keeps_the_pc_lock_until_the_node_confirms_its_job_ended _
plugin/plugins/desktop/tests/test_lock_unconfirmed.py:120: in test_a_cancelled_task_keeps_the_pc_lock_until_the_node_confirms_its_job_ended
    assert first["status"] == "cancelled" and r["holder"] == first["task"], "the lock was let go while the node job was unconfirmed"
E   AssertionError: the lock was let go while the node job was unconfirmed
E   assert ('cancelled' == 'cancelled'
E     
E       cancelled and '20261019-121346-f83eca' == '[the text st...del read it)]'
E     
E     - [the text stayed on this PC (set NEKO_SCREEN_TEXT_TO_MODEL=1 to let the model read it)]
E     + 20261019-121346-f83eca)
----------------------------- Captured stdout call -----------------------------
2026-10-19 12:13:46 - neko.desktop.pc - INFO - pc operation task-d6ac3e [task]: stop requested: stopped by the owner
2026-10-19 12:13:46 - neko.desktop.pc - INFO - pc operation task-d6ac3e [task]: waiting for the node to confirm a cancelled job ended; the PC lock stays held
2026-10-19 12:13:46 - neko.desktop.pc - WARNING - pc operation task-d6ac3e [task] did not end within 0 s of the stop (state stopping, jobs ['j1']): still running
2026-10-19 12:13:46 - neko.desktop.pc - INFO - pc lock busy: 20261019-121346-c30634 asked, held by 20261019-121346-f83eca (task, running 0 s, last sign of life 0 s ago (waiting for the node to confirm the stop), stop requested 0 s
2026-10-19 12:13:47 - neko.desktop.pc - WARNING - pc operation task-d6ac3e [task] did not end within 0 s of the stop (state stopping, jobs ['j1']): still running
2026-10-19 12:13:47 - neko.desktop.pc - WARNING - pc lock recovery requested: {'recovered': False, 'reason': "the node has not confirmed that job(s) ['j1'] ended, so task-d6ac3e may still control the desktop; not recovering"}
------------------------------ Captured log call -------------------------------
INFO     neko.desktop.pc:coordinator.py:246 pc operation task-d6ac3e [task]: stop requested: stopped by the owner
INFO     neko.desktop.pc:coordinator.py:246 pc operation task-d6ac3e [task]: waiting for the node to confirm a cancelled job ended; the PC lock stays held
WARNING  neko.desktop.pc:coordinator.py:565 pc operation task-d6ac3e [task] did not end within 0 s of the stop (state stopping, jobs ['j1']): still running
INFO     neko.desktop.pc:coordinator.py:1051 pc lock busy: 20261019-121346-c30634 asked, held by 20261019-121346-f83eca (task, running 0 s, last sign of life 0 s ago (waiting for the node to confirm the stop), stop requested 0 s a
WARNING  neko.desktop.pc:coordinator.py:565 pc operation task-d6ac3e [task] did not end within 0 s of the stop (state stopping, jobs ['j1']): still running
WARNING  neko.desktop.pc:__init__.py:1202 pc lock recovery requested: {'recovered': False, 'reason': "the node has not confirmed that job(s) ['j1'] ended, so task-d6ac3e may still control the desktop; not recovering"}
=========================== short test summary info ============================
FAILED plugin/plugins/desktop/tests/test_lock_unconfirmed.py::test_a_cancelled_task_keeps_the_pc_lock_until_the_node_confirms_its_job_ended
!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!!
1 failed, 10 passed in 7.45s
run 10 rc=0 37 passed in 23.17s
run 11 rc=0 37 passed in 23.17s
run 12 rc=0 37 passed in 23.18s
run 13 rc=0 37 passed in 23.18s
run 14 rc=0 37 passed in 23.15s
run 15 rc=0 37 passed in 23.15s
run 16 rc=0 37 passed in 23.15s
run 17 rc=0 37 passed in 23.18s
run 18 rc=0 37 passed in 23.18s
run 19 rc=0 37 passed in 23.20s
run 20 rc=0 37 passed in 23.17s
run 21 rc=1 1 failed, 10 passed in 7.40s
---FAIL 21
..........F
=================================== FAILURES ===================================
_ test_a_cancelled_task_keeps_the_pc_lock_until_the_node_confirms_its_job_ended _
plugin/plugins/desktop/tests/test_lock_unconfirmed.py:120: in test_a_cancelled_task_keeps_the_pc_lock_until_the_node_confirms_its_job_ended
    assert first["status"] == "cancelled" and r["holder"] == first["task"], "the lock was let go while the node job was unconfirmed"
E   AssertionError: the lock was let go while the node job was unconfirmed
E   assert ('cancelled' == 'cancelled'
```

## UI (final18 and final19 reruns after the e2e type fix)
```
-- final18/ui_e2e.log
TSC_OK
e2e/global-setup.ts(9,47): error TS2339: Property 'dirname' does not exist on type 'ImportMeta'.
[2m Test Files [22m [1m[32m5 passed[39m[22m[90m (5)[39m
[2m      Tests [22m [1m[32m37 passed[39m[22m[90m (37)[39m
✓ built in 422ms
  ✓  22 [app] › e2e/app.spec.ts:22:3 › screens and navigation › all ten screens render and navigate, with no uncaught errors or failed API calls (2.0s)
    Error: expect(locator).toBeVisible() failed
  2 failed
    [app] › e2e/app.spec.ts:59:3 › command center › a typed command reaches JARVIS and the answer is shown with its source 
    [app] › e2e/connectors.spec.ts:23:1 › an empty catalogue explains itself, and Alt+0 opens the screen 
  8 did not run
  45 passed (1.8m)
-- final19/ui_e2e.log
TSC_OK
[2m Test Files [22m [1m[32m5 passed[39m[22m[90m (5)[39m
[2m      Tests [22m [1m[32m37 passed[39m[22m[90m (37)[39m
✓ built in 446ms
  ✓  22 [app] › e2e/app.spec.ts:22:3 › screens and navigation › all ten screens render and navigate, with no uncaught errors or failed API calls (2.0s)
    Error: expect(locator).toContainText(expected) failed
  2 failed
    [app] › e2e/app.spec.ts:59:3 › command center › a typed command reaches JARVIS and the answer is shown with its source 
    [app] › e2e/connectors.spec.ts:31:1 › a program that is not allowed is refused in the form and nothing is added 
  7 did not run
  46 passed (1.8m)
-- final18/ui_bench.log
TSC_OK
e2e/global-setup.ts(9,47): error TS2339: Property 'dirname' does not exist on type 'ImportMeta'.
[2m Test Files [22m [1m[32m5 passed[39m[22m[90m (5)[39m
[2m      Tests [22m [1m[32m37 passed[39m[22m[90m (37)[39m
✓ built in 416ms
  5 passed (21.8s)
```

## privacy with real deps, after the order-dependence fix: 166 passed, 9 skipped (see REMEDIATION_PASS4 section 3)
