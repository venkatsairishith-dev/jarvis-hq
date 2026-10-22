# Tool and plugin inventory: effect, result contract, retry rule, memory writers (pass G/F, 2026-10-19)

Generated from the code (AST scan of `@plugin_entry` in `plugin/plugins/*`, `outcome.SCHEMAS`, `planner.effect_of`, the examples in
`tests/test_outcome_registry.py`), then read by hand for the sections below. Status words follow the completion brief.
Linux server-synthetic only: nothing here was run against the real desktop, the laptop node, a real provider or the Oracle VM.

## 1. How a step's result is judged (one place: `main_agent/outcome.py`, `executor.py`)

* **effect** is decided by code from the entry name (`planner.effect_of`), never by a model: `none` = reads (retried once with a jittered
  pause), `non_idempotent` = anything that may change something, and every name the verb lists do not recognise (fail closed).
* **outcome.judge(ref, value, effect)** returns success / failed / cancelled / waiting / unknown. A registered entry (`SCHEMAS`, our code)
  needs positive evidence (a success status word, `ok`/`success` true, its own result key, required flags, postconditions such as
  `login.logged_in is False` after logout). A false flag, an error key, a `failed` status, a failed `qa.passed`, a failed step in `steps`,
  `partial`, `timed_out`, NaN or empty values never count as success. An unregistered (third-party) entry is judged on negative evidence and
  on the rule "a writer that returns an empty or unrecognisable payload is **unknown**, not success".
* **unknown on a non_idempotent step = HELD**: never retried, nothing after it starts, the final text says "could not confirm ... did not
  retry". The owner chooses (`resume_run` with a rerun list). Duplicate delivery of a task or a resume while a run is active answers busy.
* **Hostile text** (`_shared/untrusted.py`): every `StepResult.text/error` is cleaned once at construction (invisible and bidi characters out,
  fake `{{sN}}` references, role markers, special tokens, tool-call tags, HTML, markdown images, `javascript:` and fence markers defused;
  Indic ZWJ/ZWNJ, paths, ids, URLs and long values are untouched). Think steps get each earlier result in a random-tagged fenced block. The chat
  push is cleaned again and carries `metadata.tool_output = "data"`. No tool result is an argument of `planner_messages` or `parse_plan`.
  `{{sN}}` fills only `content/text/body/message/note/summary` of a step that depends on `sN`, in one pass (a fake reference inside the
  data is not expanded).

## 2. Plugin entries (all 155 `@plugin_entry` in `plugin/plugins/*`; workbench's 16 added 2026-10-20)

`ours` = registered in `outcome.SCHEMAS` with real success AND failure examples (a test fails when a new entry of ours has none). `third-party`
= conservative rules only (their own `Err(...)` is the failure channel).

| id | owner | effect | outcome schema | real success shape | real failure shape | retry rule |
|---|---|---|---|---|---|---|
| app_launcher:add_app | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| app_launcher:export_apps | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| app_launcher:get_all_autostart_status | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| app_launcher:get_autostart_status | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| app_launcher:get_available_apps | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| app_launcher:get_categories | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| app_launcher:get_recent_apps | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| app_launcher:launch_app | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| app_launcher:list_apps | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| app_launcher:remove_app | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| app_launcher:set_autostart | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| app_launcher:update_app | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| claude_companion:clear_events | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| claude_companion:get_events | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| claude_companion:get_status | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| claude_companion:set_cooldown | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| claude_companion:set_user_name | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| claude_companion:test_push | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| deliverables:list_deliverables | ours | none | `read` | {items: [], tools: {}} | {error: "no folder"} | read: one retry on error/unknown |
| deliverables:make_presentation | ours | non_idempotent | `write` key file | {file: "d.pptx", folder: "f", qa: {"passed": true}, slides: 5} | {file: "d.pptx", qa: {"passed": false}} | never retried; unknown => held; owner resumes/reruns |
| deliverables:make_report | ours | non_idempotent | `write` key file | {file: "r.pdf", folder: "f", qa: {"passed": true}, kind: "pdf"} | {file: "r.pdf", folder: "f", qa: {"passed": false}} | never retried; unknown => held; owner resumes/reruns |
| deliverables:open_output | ours | non_idempotent | `write` key opened | {opened: "C:\\out\\r.pdf"} | {opened: false} | never retried; unknown => held; owner resumes/reruns |
| deliverables:regenerate | ours | non_idempotent | `write` key file | {file: "d.pptx", folder: "f", qa: {"passed": true}} | {file: "d.pptx", qa: {"passed": false}} | never retried; unknown => held; owner resumes/reruns |
| deliverables:show_in_folder | ours | non_idempotent | `write` key shown | {shown: "C:\\out\\r.pdf"} | {shown: false} | never retried; unknown => held; owner resumes/reruns |
| deliverables:start_slideshow | ours | non_idempotent | `write` key presenting | {presenting: "C:\\out\\d.pptx"} | {presenting: false} | never retried; unknown => held; owner resumes/reruns |
| desktop:check_pc | ours | none | `read` | {state: "done", ok: true, rows: [{"free_gb": 120}]} | {state: "failed", ok: false, error: "no window"} | read: one retry on error/unknown |
| desktop:find_on_screen | ours | none | `read` | {ok: true, found: false, source: "from OCR"} | {ok: false, error: "capture failed"} | read: one retry on error/unknown |
| desktop:list_browser_profiles | ours | none | `read` | {profiles: [{"name": "default"}]} | {error: "no registry"} | read: one retry on error/unknown |
| desktop:mission_status | ours | none | `read` | {mission: "m20261019-010101-abcd, status: "running", steps: []} | {mission: "m", status: "failed", error: "budget"} | read: one retry on error/unknown |
| desktop:open_website | ours | non_idempotent | `write` | {state: "done", ok: true, rows: [], observed: true} | {state: "failed", ok: false, error: "no window"} | never retried; unknown => held; owner resumes/reruns |
| desktop:play_youtube | ours | non_idempotent | `write` | {state: "done", ok: true, rows: [], observed: true} | {state: "failed", ok: false, error: "no window"} | never retried; unknown => held; owner resumes/reruns |
| desktop:read_window_text | ours | none | `read` | {ok: true, app: "notepad", lines: 3, source: "from OCR (less reliab} | {ok: false, error: "no window"} | read: one retry on error/unknown |
| desktop:recent_pc_alerts | ours | none | `read` | {alerts: [], count: 0} | {error: "monitor off"} | read: one retry on error/unknown |
| desktop:recover_pc_lock | ours | non_idempotent | `write` | {recovered: true, how: "gone"} | {recovered: false, reason: "not held"} | never retried; unknown => held; owner resumes/reruns |
| desktop:resume_mission | ours | non_idempotent | `write` | {ok: true, mission: "m", status: "continuing", from: "waiting"} | {ok: false, error: "no such mission"} | never retried; unknown => held; owner resumes/reruns |
| desktop:run_browser_action | ours | non_idempotent | `write` | {state: "done", ok: true, rows: [], observed: true} | {state: "failed", ok: false, error: "no window"} | never retried; unknown => held; owner resumes/reruns |
| desktop:run_pc_action | ours | non_idempotent | `write` | {state: "done", ok: true, rows: [], observed: true} | {state: "failed", ok: false, error: "no window"} | never retried; unknown => held; owner resumes/reruns |
| desktop:run_pc_task | ours | non_idempotent | `write` | {status: "completed", task: "t", steps: [{"n": 1, "passed": tr} | {status: "failed", steps: [{"passed": false}]} | never retried; unknown => held; owner resumes/reruns |
| desktop:run_troubleshooter | ours | non_idempotent | `write` | {outcome: "fixed and verified"} | {outcome: "the fix was not appli} | never retried; unknown => held; owner resumes/reruns |
| desktop:start_mission | ours | non_idempotent | `start` key mission | {mission: "m20261019-010101-abcd, status: "started"} | {mission: "m", status: "failed"} | never retried; unknown => held; owner resumes/reruns |
| desktop:stop_mission | ours | non_idempotent | `stop` key mission | {mission: "m", stopped: true} | {mission: "m", stopped: false} | never retried; unknown => held; owner resumes/reruns |
| desktop:stop_pc_job | ours | non_idempotent | `stop` key id | {id: "j1", state: "cancelled", note: "ok"} | {id: "j1", state: "failed", error: "unknown job"} | never retried; unknown => held; owner resumes/reruns |
| desktop:stop_pc_task | ours | non_idempotent | `stop` key stopped_jobs | {stopped_jobs: 0, stopped: [], still_running: [], requested: 0} | {stopped_jobs: 0, still_running: [{"id": "t"}], requested: 1, message: | never retried; unknown => held; owner resumes/reruns |
| doc_tools:list_outputs | ours | none | `read` | (run for real in test: see test_outcome_registry) | - | read: one retry on error/unknown |
| doc_tools:read_document | ours | none | `read` | (run for real in test: see test_outcome_registry) | - | read: one retry on error/unknown |
| doc_tools:write_document | ours | non_idempotent | `write` key path | (run for real in test: see test_outcome_registry) | - | never retried; unknown => held; owner resumes/reruns |
| game_agent_minecraft:game_agent_reload_config | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| game_agent_minecraft:game_agent_status | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| game_agent_minecraft:query_inventory | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| jarvis_bridge:cancel_task | ours | non_idempotent | `stop` key command_id | {command_id: "c-1", cancelled: true, tasks_stopped: 1, message: "ok"} | {command_id: "c-1", cancelled: false, message: "already finished"} | never retried; unknown => held; owner resumes/reruns |
| jarvis_bridge:connector_overview | ours | none | `read` | {connectors: []} | {error: "console down"} | read: one retry on error/unknown |
| jarvis_bridge:delegate_task | ours | non_idempotent | `start` key command_id | {command_id: "c-1", state: "running", message: "JARVIS has the task"} | {state: "failed", error: "console down"} | never retried; unknown => held; owner resumes/reruns |
| jarvis_bridge:jarvis_status | ours | none | `read` | {ready: true, busy: false, message: "JARVIS is ready."} | {ready: false, error: "down"} | read: one retry on error/unknown |
| jarvis_bridge:pending_approvals | ours | non_idempotent | `read` | {count: 0, approvals: []} | {error: "console down"} | never retried; unknown => held; owner resumes/reruns |
| jarvis_bridge:task_result | ours | none | `read` | {command_id: "c-1", state: "done", message: "JARVIS finished."} | {command_id: "c-1", state: "failed", message: "JARVIS could not comp} | read: one retry on error/unknown |
| jukebox_controller:control_jukebox | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| learn_vault:get_progress | ours | none | `read` | (run for real in test: see test_outcome_registry) | - | read: one retry on error/unknown |
| learn_vault:list_notes | ours | none | `read` | (run for real in test: see test_outcome_registry) | - | read: one retry on error/unknown |
| learn_vault:read_note | ours | none | `read` | (run for real in test: see test_outcome_registry) | - | read: one retry on error/unknown |
| learn_vault:record_progress | ours | non_idempotent | `write` key topic | (run for real in test: see test_outcome_registry) | - | never retried; unknown => held; owner resumes/reruns |
| learn_vault:save_note | ours | non_idempotent | `write` key path | (run for real in test: see test_outcome_registry) | - | never retried; unknown => held; owner resumes/reruns |
| learn_vault:search_notes | ours | none | `read` | (run for real in test: see test_outcome_registry) | - | read: one retry on error/unknown |
| learn_vault:vault_status | ours | none | `read` | (run for real in test: see test_outcome_registry) | - | read: one retry on error/unknown |
| lifekit:add_location | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| lifekit:air_quality | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| lifekit:countdown | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| lifekit:currency_convert | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| lifekit:days_between | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| lifekit:food_recommend | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| lifekit:get_config | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| lifekit:get_weather | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| lifekit:hourly_forecast | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| lifekit:list_locations | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| lifekit:random_recipe | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| lifekit:remove_location | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| lifekit:search_nearby | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| lifekit:search_recipe | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| lifekit:set_default_location | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| lifekit:travel_advice | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| lifekit:trip_advice | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| lifekit:unit_convert | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| lifekit:update_config | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| local_team:ask_local | ours | none | `write` key answer/plan/text | {specialist: "general", routed_by: "rule", answer: "42", seconds: 1.0} | {error: "no answer"} | read: one retry on error/unknown |
| local_team:solve_with_team | ours | non_idempotent | `write` key answer/plan/text | {specialist: "code", answer: "x", adviser_used: false, note: "adviser  | {error: "no answer"} | never retried; unknown => held; owner resumes/reruns |
| local_team:team_status | ours | none | `read` | {local_models: "up", privacy_gateway: "down"} | {error: "x"} | read: one retry on error/unknown |
| main_agent:plan_task | ours | non_idempotent | `read` | {mode: "work", summary: "s", steps: []} | {error: "no model"} | never retried; unknown => held; owner resumes/reruns |
| main_agent:resume_run | ours | non_idempotent | `start` key run_id | {run_id: "ab12cd34ef", state: "running"} | {state: "failed", error: "nothing to resume"} | never retried; unknown => held; owner resumes/reruns |
| main_agent:pending_inputs | ours | none | `read` | {count: 1, requests: [...]} | {count: 0, requests: []} | read only; safe to repeat |
| main_agent:answer_input | ours | non_idempotent | `start` key run_id | {state: "answered"} | {error: "That question expired…"} | one answer per question id; repeats refused; never retried |
| main_agent:cancel_input | ours | non_idempotent | `stop` key run_id | {state: "cancelled"} | {error: "no such question"} | cancelling twice is refused, not repeated |
| main_agent:learning_stats | ours | none | `read` | {episodes: 12, success_rate: 0.75} | {error: "..."} | read only; safe to repeat |
| main_agent:forget_learning | ours | non_idempotent | `stop` | {state: "forgotten", forgotten: 12} | {error: "..."} | owner-only; erasing twice erases nothing the second time |
| main_agent:run_status | ours | non_idempotent | `read` | {run_id: "ab12cd34ef", state: "held", steps: {}} | {error: "no such run"} | never retried; unknown => held; owner resumes/reruns |
| main_agent:run_task | ours | non_idempotent | `start` key run_id | {run_id: "ab12cd34ef", mode: "work", state: "planning", steps: []} | {state: "failed", error: "no plan"} | never retried; unknown => held; owner resumes/reruns |
| main_agent:stop_task | ours | non_idempotent | `stop` key message/requested | {requested: true, stopped: true, still_running: [], message: "Stopped" | {requested: true, stopped: false, still_running: [{"id": "r"}], messag | never retried; unknown => held; owner resumes/reruns |
| mcp_adapter:add_server | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| mcp_adapter:call_tool | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| mcp_adapter:connect_server | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| mcp_adapter:disconnect_server | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| mcp_adapter:gateway_invoke | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| mcp_adapter:list_servers | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| mcp_adapter:list_tools | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| mcp_adapter:remove_servers | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| mcp_adapter:set_chat_injection | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| memo_reminder:add_reminder | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| memo_reminder:bind_task | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| memo_reminder:clear_reminders | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| memo_reminder:delete_reminder | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| memo_reminder:list_reminders | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| music_pusher:create_schedule_task | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| music_pusher:delete_music_item | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| music_pusher:delete_schedule_task | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| music_pusher:get_push_settings | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| music_pusher:list_music_items | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| music_pusher:list_schedule_tasks | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| music_pusher:push_music_url | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| music_pusher:report_playback_state | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| music_pusher:run | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| music_pusher:run_schedule_task_now | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| music_pusher:set_push_settings | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| music_pusher:skip_current_track | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| music_pusher:stop_schedule_task | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| music_pusher:toggle_schedule_task | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| music_pusher:update_schedule_task | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| music_pusher:upload_music_file | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| netease_music:clear_music_u | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| netease_music:play_netease_music | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| netease_music:save_music_u | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| proactive_controller:command | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| proactive_controller:get_state | third-party | none | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | read: one retry on error/unknown |
| proactive_controller:set_mode | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| proactive_controller:set_settings | third-party | non_idempotent | none (conservative: negative evidence, empty/unknown payload from a writer => held) | (third-party: shape not read) | (third-party: shape not read) | never retried; unknown => held; owner resumes/reruns |
| research:research_topic | ours | non_idempotent | `write` key spec/summary | {summary: "s", spec: {"summary": "s"}, stats: {}, notes: []} | {summary: "s", qa_passed: false} | never retried; unknown => held; owner resumes/reruns |
| web_search:search | ours | none | `read` | (run for real in test: see test_outcome_registry) | - | read: one retry on error/unknown |
| web_search:search_summary | ours | none | `read` | (run for real in test: see test_outcome_registry) | - | read: one retry on error/unknown |
| wechat_integration:get_dashboard_state | ours | none | `read` | {login: {"logged_in": true, "a, qrcode: {"url": "", "has_sessi, settin | {login: {"logged_in": false, ", qrcode: {"url": "", "has_sessi, settin | read: one retry on error/unknown |
| wechat_integration:logout | ours | non_idempotent | `write` key config_ready/login | {login: {"logged_in": false, ", qrcode: {"url": "", "has_sessi, settin | {login: {"logged_in": true, "a, qrcode: {"url": "", "has_sessi, settin | never retried; unknown => held; owner resumes/reruns |
| wechat_integration:poll_login_status | ours | none | `read` | {login: {"logged_in": true, "a, qrcode: {"url": "", "has_sessi, settin | {login: {"logged_in": false, ", qrcode: {"url": "", "has_sessi, settin | read: one retry on error/unknown |
| wechat_integration:refresh_qrcode | ours | non_idempotent | `write` key config_ready/login | {login: {"logged_in": true, "a, qrcode: {"url": "", "has_sessi, settin | {login: {"logged_in": false, ", qrcode: {"url": "", "has_sessi, settin | never retried; unknown => held; owner resumes/reruns |
| wechat_integration:save_settings | ours | non_idempotent | `write` key config_ready | {login: {"logged_in": true, "a, qrcode: {"url": "", "has_sessi, settin | {login: {"logged_in": true, "a, qrcode: {"url": "", "has_sessi, settin | never retried; unknown => held; owner resumes/reruns |
| wechat_integration:send_message | ours | non_idempotent | `write` key user_id | {status: "sent", user_id: "u1"} | {status: "failed", error: "no context_token"} | never retried; unknown => held; owner resumes/reruns |
| wechat_integration:start_auto_reply | ours | non_idempotent | `write` key config_ready/status | {login: {"logged_in": true, "a, qrcode: {"url": "", "has_sessi, settin | {login: {"logged_in": true, "a, qrcode: {"url": "", "has_sessi, settin | never retried; unknown => held; owner resumes/reruns |
| wechat_integration:start_login | ours | non_idempotent | `write` key config_ready/login | {login: {"logged_in": true, "a, qrcode: {"url": "", "has_sessi, settin | {login: {"logged_in": false, ", qrcode: {"status": "wait", "ha, settin | never retried; unknown => held; owner resumes/reruns |
| wechat_integration:stop_auto_reply | ours | non_idempotent | `write` key config_ready/status | {login: {"logged_in": true, "a, qrcode: {"url": "", "has_sessi, settin | {login: {"logged_in": true, "a, qrcode: {"url": "", "has_sessi, settin | never retried; unknown => held; owner resumes/reruns |
| workbench:create_project | ours | non_idempotent | `write` key project_id | {project_id: "acme", policy: {...}} | {error: ...} | never retried; unknown => held |
| workbench:add_project_files | ours | non_idempotent | `write` key added | {added: [{id: "F1"}], refused: []} | {added: [], refused: [...]} | never retried; unknown => held |
| workbench:analyze_project | ours | non_idempotent (rewrites the project's derived files) | `write` key stats | {stats: {...}, open_questions: [...]} | {error: "No such project."} | never retried; unknown => held |
| workbench:answer_questions | ours | non_idempotent | `write` key stats | {stats: {...}} | {error: "Analyse the project first."} | never retried; unknown => held |
| workbench:build_deliverables | ours | non_idempotent | words: verified=success; needs_review / partly_verified=unknown; blocked=failed | {status: "verified", verified: 2} | {status: "needs_review"}, {status: "blocked"} | never retried; unknown => held |
| workbench:project_status | ours | none | `read` | {project_id, stats} | {error: ...} | read: one retry on error/unknown |
| workbench:set_provider_policy | ours | non_idempotent; OWNER ONLY (never in a plan) | `write` key project_id | {project_id, policy} | {error: "needs the owner's explicit confirmation"} | never retried; unknown => held |
| workbench:generate_prompt | ours | non_idempotent (saves a suggestion) | `write` key id, free status | {id, version: 1, status: "suggestion"} | {error: ...} | never retried; unknown => held |
| workbench:get_prompt | ours | none | `read`, free status | {id, status, text} | {error: "Unknown prompt id."} | read: one retry on error/unknown |
| workbench:edit_prompt | ours | non_idempotent (new version) | `write` keys id, version | {id, version: 2} | {error: ...} | never retried; unknown => held |
| workbench:regenerate_prompt | ours | non_idempotent (new version) | `write` keys id, version | {id, version: 3} | {error: ...} | never retried; unknown => held |
| workbench:execute_prompt | ours | non_idempotent; OWNER ONLY (confirm=true, never in a plan) | `start` key ref; submitted=success (submitted, not done) | {state: "submitted", ref: "cmd-1"} | {state: "refused"} (kill switch / JARVIS unreachable), {state: "failed"} | never retried; unknown => held |
| workbench:learn_task | ours | non_idempotent (library) | words: validated=success; not validated / not adopted / invalid=failed; needs examples / a workflow / the lab=waiting | {status: "validated", workflow, version} | {status: "not validated"}, {status: "needs examples"} | never retried; unknown => held |
| workbench:list_learned | ours | none | `read` | {workflows: [], lessons: []} | {error: ...} | read: one retry on error/unknown |
| workbench:run_learned_workflow | ours | non_idempotent; OWNER ONLY (confirm=true, never in a plan) | `start` key command_id; submitted=success | {state: "submitted", command_id} | {state: "refused"}, {state: "failed"} | never retried; unknown => held |
| workbench:check_workflow_output | ours | none | `read`, required verified=true | {verified: true} | {verified: false, why} | read: one retry on error/unknown |

## 3. Other callable surfaces

| surface | members | effect / approval | result contract | status |
|---|---|---|---|---|
| main_agent step kinds (`planner.KINDS`) | think; plugin; browser; jarvis; computer | think = none (local-only when memory-influenced); plugin = per table; browser, computer, jarvis = non_idempotent always | a step ends success / failure / unknown / not_executed; browser and computer follow the agent server's `success` flag and task id; jarvis polls `task_result` (`done`/`failed`/`cancelled`); text is cleaned in `StepResult` | PASS (tests in main_agent/tests) |
| node job kinds (`jarvis/node/jarvis_node/jobs.py`) | project_check, project_apply, output_save, output_show, ui_action, pc_open, ui_windows, ui_snapshot, screenshot, connector_op, browser_ext, diag, repair, media_state | the kernel decides per action (below); the node refuses unknown kinds (`{"ok": False, "error": "unknown job kind"}`) | every kind returns a dict with `ok` (project_check returns `ran`/`exit_code`, no `ok`; the kernel treats it by job state); errors are the exception class name only | read by hand; the node runs only on the laptop: BLOCKED for a live run |
| kernel actions (`policy.RULES`) | fs read/list/write; state; git status/log/diff/branch_create; tests run; docs read; pkg install(_pinned_sandbox); config write; service restart_noncritical; message send_routine; project apply_patch; browser open/snapshot/extract (GREEN), click/type/select/press (approval), download/upload (RED), play (auto); output save/show; lab share/task/dispatch; models status/prefer; ui snapshot/list_windows (GREEN), screenshot (auto), launch/focus/click/type/key/scroll/click_at/minimize/maximize/restore (approval); pc open_app/open_folder/open_file/list_folder; task create/update/get/dispatch; memory list/get/resolve/render (GREEN), add/update/forget (auto, own entries); skill (de)activate; automation, agent_def (auto); connector list/describe/manage/result (GREEN), call (auto, tool risk decides); media state; diag * (GREEN); repair restore (auto), restart_app/edit_file/install (approval); caretaker report; money, delete, credential, publish, egress, oci (RED) | kernel is authoritative: GREEN runs, YELLOW_AUTO runs and is audited, YELLOW_APPROVAL needs the owner's PIN approval, RED/DENY refused | allow/deny decision + approval id, audited | not changed in this pass |
| MCP connectors (`connector_service.call_tool`, `connectors.finish_result`) | any owner-enabled tool of a registered server; tool must be present, enabled, risk-classified and digest-approved; `webmcp_*` refused | read = auto; others per the owner's risk class; arguments scrubbed (a credential refuses the call), 16 KB cap, 1 call at a time per connector, hourly cap | `{"status": done/failed, "result": {"untrusted": true, "source": ..., "text": scrubbed+capped, "is_error", "truncated"}, "error_class"}`; text is scrubbed but not defused (residual R4) | PASS in kernel tests (not re-run for this section beyond test_node_screen_egress / llm_proxy / scrub) |
| `jarvis/tools/registry.json` (88 entries) | upstream projects, models, binaries, pip/npm packages, OS tools | **install-time / inventory metadata, not callable tools.** Runtime-callable only through: connectors (Playwright MCP, MCP servers), node jobs (browser_ext, diag, pcopen use OS tools), the lab agents (OpenCode, goose via the lab), llama.cpp / llama-swap (local model endpoints), and the plugins above | no result contract of its own | classification COMPLETED-NOT-VERIFIED (read from `integration` fields: 21 pip, 19 model weights, 10 downloaded binaries, 5 planned-only, 5 external services, 3 vendored source, 2 npm, the rest OS-provided or single items) |

## 4. Entries that WRITE memory or persistent state (memory-writer review)

"Approval" = what stops a bad write today. The kernel's own memory (`memory add|update|forget`) is the agent's own entries, YELLOW_AUTO,
scrubbed, audited. The N.E.K.O plugins below have NO kernel approval: they are plain plugin entries the planner may choose, and a step
planned with the owner's memory is limited to `MEMORY_LOCAL_ENTRIES` (main_agent/__init__.py).

| writer | what it persists | where | approval today | on the memory allowlist |
|---|---|---|---|---|
| memory server (N.E.K.O, not a plugin) | conversation memory | N.E.K.O memory store | none from these plugins; main_agent only READS it (`planner_memory`, verified-local planner model only) | n/a |
| learn_vault: save_note, record_progress | notes, progress | `LEARN_VAULT_DIR` (separate from JARVIS's memory vault) | path confined to the vault; `create` refuses overwrite; no owner approval | yes |
| doc_tools: write_document | documents | outputs folder | name sanitised, folder fixed | yes |
| deliverables: make_report, make_presentation, regenerate | decks, reports, QA records | outputs folder | none; QA gate must pass for success | yes (with open_output, show_in_folder, start_slideshow) |
| research: research_topic | run record JSON (question as typed, sources, passages, writer draft) and optional report | `RESEARCH_OUT_DIR` / deliverable folder | none; question is gateway-redacted only for the SEARCH, the local record keeps the question | no |
| memo_reminder: add_reminder, bind_task, delete_reminder, clear_reminders | reminders; pushes its text into the chat later | plugin store | none | no (blocked while memory-influenced) |
| lifekit: update_config, add_location, remove_location, set_default_location | locations and settings | plugin store | none | no |
| app_launcher, music_pusher, netease_music, proactive_controller, claude_companion | app lists, schedules, cookies (`save_music_u`), settings, events | plugin stores | none | no |
| mcp_adapter: add_server, connect_server, call_tool, gateway_invoke | server registrations; whatever the tool does | plugin store, remote servers | none at the plugin; the JARVIS connector path has the approval gates in section 3 | no |
| wechat_integration: save_settings, start/stop_auto_reply, send_message, start_login | WeChat settings and messages | plugin store, WeChat | none beyond the plugin's own login | no |
| desktop: run files, missions, locks, skills overlay | task records, mission files, `registry.json` of promoted skills (the overlay feeds the decision model's examples and site names) | desktop run folders | missions/tasks run under kernel `pc`/`ui` policy; skill promotion goes through `skills.py` gates | read/act entries only (see 5) |
| workbench: projects, prompts, library | copies of client files, extracted segments, requirements, plans, prompts (versioned), deliverables, validated workflows, lessons | `WORKBENCH_DIR` (project folders isolated) | paths confined to each project; client text only to a verified local model unless the owner's per-project policy says otherwise; lessons refuse client material, contact details and secrets; running anything is owner-only | no (not offered to memory-influenced steps) |
| main_agent: run files, planning marker | step texts (cleaned; screen text is NOT written) | `RunStore` folder | none | n/a |

## 5. Owner decision: desktop `open_website`, `play_youtube`, `run_browser_action`, `run_pc_task` are NOT on the memory allowlist

State now (not broadened by this pass): a step planned with the owner's memory that calls any of them fails with "Needs the owner: ...",
nothing is sent. Allowed instead: `check_pc`, `list_browser_profiles`, `recent_pc_alerts`, `read_window_text`, `find_on_screen`,
`mission_status`, `run_pc_action`, `stop_pc_task`, `stop_pc_job`, `stop_mission`, `recover_pc_lock`.

Concrete consequences if allowed:

* `open_website`: the step's text becomes (part of) a URL opened in the owner's Chrome profile. A memory such as a diagnosis, a name or an
  address inside `https://search.example/?q=...` goes to that site, its logs, the browser history and any account sync.
* `play_youtube`: the title/query goes to YouTube under the owner's Google account (watch history, recommendations, Google's logs).
* `run_browser_action`: types text into fields of arbitrary web pages. A memory can be posted into any form, chat box or comment field the
  page offers, including one a hostile page planted.
* `run_pc_task` (also `start_mission`, `resume_mission`, `run_troubleshooter`): its own planner may call a model, the web and the research
  agent, so memory text can leave through those paths and the owner cannot see which.

The one-line change, if the owner accepts a specific leak: in `plugin/plugins/main_agent/__init__.py`, add the entry id to the `"desktop"`
set of `MEMORY_LOCAL_ENTRIES`, for example `... "mission_status", "run_pc_action", "open_website", ...`. Do not use
`MAIN_AGENT_MEMORY_TO_EXTERNAL_STEPS=1` for this: it lifts the guard for every plugin, JARVIS and all unknown entries.

## 6. Private lane to public models (plugin and gateway side), as traced

| hop | what protects it | evidence |
|---|---|---|
| planner prompt | the request text only; owner memory only for a verified-local endpoint (`memory_allowed`; the gateway is refused), re-checked at the call (`local_only`) | test_memory_context, test_screen_egress |
| think steps | prior results fenced and cleaned; screen/disk text only as the gateway cleared it or a note; memory-influenced steps local-only | test_hostile_results, test_screen_egress |
| `jarvis` step | JARVIS's reply is NOT handed back unless `JARVIS_BRIDGE_SPEAK_REPLY=1` (owner flag, default off) | jarvis_bridge test_client |
| gateway to providers | whitelist of fields; every string (and now every non-plain JSON key) deep-redacted; media withheld; unknown field dropped; unreadable shape refused with 400; degraded gateway = 503 and nothing forwarded; same body for the fallback providers in `providers.json` (each with its own key and model) | test_gateway_plumbing, test_privacy (real Presidio) |
| local_team adviser | only a URL whose host:port IS the gateway; adviser plan returned to the local specialist as fenced untrusted text; outage / 503 / timeout skip the adviser once and answer locally | local_team/tests |
| research | question redacted by the gateway with names on, refused when the gateway is down or degraded; links in queries lose user:password, query string and fragment, tokens and internal hosts are dropped; fetch refuses non-http(s), odd ports, user-info, non-public addresses (checked again at connect time against the same answer, and per redirect); writer prompt holds web text in a fenced block | research/tests |
| kernel llm_proxy | built-in + gitleaks scrub of every text and now every key; private key blocks the call; media withheld per provider | test_llm_proxy, test_scrub |

## 7. Residual risks (not fixed here)

* R1. `names` redaction is off by default for model calls through the gateway (people and places go to Dahl/Groq as typed unless a caller
  asks per call). Existing design decision (memory gate treats the gateway as public).
* R2. The chat model that receives `_say` text is N.E.K.O's own and cannot be verified from here; text is cleaned and marked, not blocked.
* R3. Hostile text that does not look like markup (plain sentences asking for something) is shown as data; only the "ignore the above" classes
  stripped in research (`strip_injected`) are removed. The defence for the rest is structural: results are never a plan or a tool argument
  except through `{sN}` into content-type fields.
* R4. Connector tool results are scrubbed and labelled untrusted but not defused of invisible characters in the kernel.
* R5. The node browser (`jarvis_node/browser.py`) allows any port on a public address (it browses through the egress checkpoint).
* R6. A third-party non_idempotent writer with an empty payload is now held (owner decision from pass 4 stands).
