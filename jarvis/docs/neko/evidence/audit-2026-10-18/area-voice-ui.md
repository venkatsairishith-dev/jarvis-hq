# Audit: voice (A), interface / console / UI / phone access (G), startup, supervision and network exposure

- Repo: `/srv/jarvis/home/hq-neko`, commit `88ec49a2`, branch `main`, clean (re-checked at the end: `git status --short` empty).
- Auditor environment: the Linux test server (rootless podman image `neko-test`). There is **no microphone, no speaker, no GPU, no Windows and no running laptop**. Every hardware claim below was checked only through code and through result files / logs committed to the repo. Nothing here was observed on real hardware.
- Evidence classes used: **E2E** = recorded live run on real hardware with a result file in the repo; **Integration** = real components wired together, external boundary faked; **Unit/mocked** = fakes for the component's own collaborators; **Manual claim** = a sentence in a doc or a hand-entered row with no artifact in the repo; **None**.
- The guide page "JARVIS In Depth" was treated as a claim. Its voice/UI/startup statements are checked in section 4.

---

## 1. Real execution flows (file:line)

There are **three separate voice paths**. They do not share code beyond the speech models, and they reach different "agents".

### 1a. "Hey Neko" wake path (Windows only) -> N.E.K.O main_agent plugin
1. Started by `jarvis/scripts/neko-start.ps1:94-101` (only if a KWS model exists under `D:\jarvis\models\voice\kws\versions\*` and `-NoWake` is not given).
2. Microphone: `jarvis/wake/wake_listener.py:451-491` `MicSource` (sounddevice, 16 kHz mono, falls back to the device rate + `resample_int16` at :88-99). Frames of 40 ms (:38).
3. Detector: N.E.K.O's `SherpaWakeWordDetector` built in `make_detector` (:528-534), threshold 0.25 default (:588), keywords = 4 phone strings (:47-48). Fed per frame in `WakeLoop._listen` (:312-336).
4. Echo guard: a detection is **dropped** if the speech server's `/activity.speaking_estimate` is true (`_is_speaking` :356-360, used at :323-326). `speaking_estimate` is "synthesized less than its own length + 1 s ago" (`jarvis/scripts/local_speech_server.py:111-120`), i.e. an estimate, not playback state.
5. Recording: command gain derived from the wake word (:338-354), Silero VAD (`Vad` :110-135), command ends after 1.0 s silence or 12 s max, 5 s wait for speech (:202-205, :374-386).
6. STT: `transcribe(pcm, lang="en")` (:147-149) -> `POST http://127.0.0.1:48930/v1/audio/transcriptions?language=en`. `_handle` calls `self.transcribe(audio)` with **no language** (:393), so the wake path is **English only** (Parakeet). Speech server routes `en` to Parakeet, `hi`/`te` to IndicConformer (`jarvis/console/jarvis_console/voice.py:86-92`); unknown/missing language silently becomes `en` (`local_speech_server.py:200`).
7. Intent split (:405-415): `STOP_WORDS` + `task_running()` -> `stop_task()`; `PURE_CANCEL` or short cancel phrase while running -> cancel; else submit.
8. Voice -> action: `submit_task(text)` (:167-176) -> `POST http://127.0.0.1:48940/api/task {"task", "mode":"work"}` -> widget `start_task` (`jarvis/widget/widget_server.py:131-145`) -> `POST http://127.0.0.1:48916/runs {"plugin_id":"main_agent","entry_id":"run_task"}` -> `plugin/plugins/main_agent/__init__.py:356-371 run_task` -> `_make_plan` (LLM call; Dahl, 10-60 s per the docs) -> `_start` (:324-333) -> `_execute` -> `executor.run_plan` -> `_dispatch` (:238-296): `plugin` steps call other plugins (e.g. `desktop:run_pc_task`, `plugin/plugins/desktop/__init__.py:636`), `jarvis` steps call `jarvis_bridge:delegate_task` -> `plugin/plugins/jarvis_bridge/client.py:71-79` -> console `POST /api/owner/command` -> kernel `owner.command` (`jarvis/kernel/jarvis_kernel/owner.py:139`, route :708) -> the owner's Hermes agent.
   - The widget returns `ok: True, note: "planning"` after at most 12 polls (~6 s) even though planning has not finished (`widget_server.py:138-145`).
9. Stop by voice: `stop_task()` (:179-183) -> `POST /api/action {"action":"stop"}` -> `widget_server.do_action` (:254-257) -> `run_plugin_entry("main_agent","stop_task")` (:200-212) -> `main_agent.stop_task` (:488-492) sets the cancel event: the run stops **after the step in progress**; `executor.run_plan` only checks `cancel` before starting a step (`executor.py:128`). A delegated kernel command, a desktop PC job or a lab task already in flight is **not** cancelled (jarvis_bridge has no cancel entry).
   - `task_running()` (:159-164) reads `/api/status.task.state` from the newest run **file** (`widget_server.newest_run` :74-89). A run that is still planning has no file yet.
10. Result to the user: `main_agent._execute` ends with `push_message(... visibility=["chat"], ai_behavior="respond")` (:456-460) into N.E.K.O's chat, which speaks it through N.E.K.O's TTS -> local speech server `/v1/audio/speech` (Kokoro en, Piper hi/te; `local_speech_server.py:211-253`). Whether it is spoken depends on an open N.E.K.O session; not verifiable here.
11. Status / control: `serve_status` (:538-581) on `127.0.0.1:48945`, Host must be loopback, Origin must be absent or the widget's (:543-546). `/cancel`, `/pause`, `/resume`.

### 1b. JARVIS console push-to-talk (browser) -> kernel owner command -> Hermes
1. UI `jarvis/ui/src/lib/voice.ts:35-74` opens the mic (getUserMedia with browser echoCancellation/noiseSuppression), AudioWorklet PCM, max 60 s (:12, :65).
2. `finish(lang)` (:85-102) -> console `POST /api/voice/transcribe?lang=<en|hi|te>` (`jarvis/console/jarvis_console/server.py:337-361`) -> the console's own in-process `Voice` (a second copy of the models besides the speech server).
3. Text lands in the editable box (`CommandCenter.tsx:174-187`); optional auto-send after 3 s; send = `owner.post("command", {text, source})` (`CommandCenter.tsx:146`) -> console `_owner` proxy (`server.py:306-310`) -> kernel `/v1/jarvis/owner/command` over Tailscale (`config.py` default `http://100.125.91.51:8790`) -> `owner.command` -> Hermes. **This path does not go through N.E.K.O, main_agent or the desktop plugin.**
4. Reply: the UI speaks replies of commands it started (`CommandCenter.tsx:153-161`) via `/api/voice/speak`. Barge-in = pressing Talk or Esc calls `stopSpeaking()` (`voice.ts:38`, `App.tsx:72`): manual, button-driven, not voice-activated.
5. Language: the user picks en/hi/te in a dropdown (`CommandCenter.tsx:225-228`); no automatic language detection for STT.

### 1c. N.E.K.O's own browser voice session -> N.E.K.O chat LLM -> plugins
- `neko-start.ps1:40` sets `NEKO_WHISPER_ENDPOINT` so N.E.K.O's faster_whisper worker sends each utterance to the speech server (`main_logic/asr_client/workers/faster_whisper.py:306-361`, `_EndpointModel`, loopback-only check at :316-321). The language field is sent only when the session language is not `auto` (:334-336); `auto` therefore becomes English at the speech server.
- `jarvis/docs/05_OPERATIONS.md:205` and `:239`: microphone capture in the browser and a spoken conversation were **never exercised end to end**; the realtime audio mode is refused by Dahl. NEKO_REQUIREMENTS V4: "MISSING: never exercised".

### 1d. Neko icon state (widget)
- `widget_server.neko_state` (:184-197) polls gateway `/health`, speech `/activity`, console `/api/owner/state`, wake `/status`, then `neko_state.decide` (`jarvis/widget/neko_state.py:40-91`). States come from real signals; "speaking" is an estimate; "listening" means "just transcribed" (or wake recording). Run step detail marks every unfinished step "running" while the run is running (`widget_server.py:84-87`), ignoring the run file's real `running` list, so "step k of n (agent)" is approximate. Final run states `partial`, `failed`, `held`, `cancelled` are never shown (only `done`-like states map to "completed", :78-79), so a failed voice task shows as "sleeping".
- SSE `/api/events` every 1 s (:282-307).

### 1e. Console UI: real kernel state
- `jarvis/ui/src/lib/store.ts:58-96`: polls `/api/owner/state` every 3 s and follows the kernel SSE `/api/stream`. Approvals with PIN + typed id (`CommandCenter.tsx:18-77`), RED only at the kernel console; task pause/resume/cancel (`Tasks.tsx:19-28`) -> `owner.post("task", {id, op})`; Stop all and Kill (`App.tsx:84-95`). The progress bar is explicitly "time budget used" (`CommandCenter.tsx:96-107`), not fake completion. "Stop waiting" only abandons the command, the kernel says tasks keep running (`owner.py:254-256`).
- Bench screen: `jarvis/console/jarvis_console/bench.py` (routes :925-966), runs only allow-listed `bench.py` / `model_compare.py` argv in a process group (:480-481), labels evidence with a regex on the case's scenario text (:60-69, :223-229).

### 1f. Startup and supervision (Windows laptop)
- `install-autostart.ps1`: per-user logon task `JarvisNekoStack`, 30 s delay, runs `neko-start.ps1` (doc: installed 2026-10-14; not verifiable here).
- `neko-start.ps1` order: privacy gateway 48950 -> llama-swap 8080 (if installed) -> upstream `launcher.py` (48911/48912/48915/48916) -> speech 48930 -> widget 48940 -> wake 48945 -> plugin refresh/start of 9 plugins -> console 8799 via `start-console.ps1`. Idempotent ("start if not listening").
- `watchdog.py` every 10 min (`install-watchdog.ps1`): probes 9 servers (:26-30) and 8 plugins (:31), reruns `neko-start.ps1` if any server is down, starts a stopped plugin, max 3 heals/hour/problem then `gave_up_on`. **It does not probe the wake listener (48945) and does not heal the `desktop` plugin.** The desktop plugin's monitor does alert on 48945 being down (`plugin/plugins/desktop/monitor.py:24-25`) but nothing restarts it.
- `daily_report.py` (09:00 task): writes a local Markdown file only. It is not sent anywhere (Telegram off).
- Server side (Oracle) systemd units in `jarvis/deploy/`: all `Restart=on-failure`, plus monit checks for kernel and llm. The Telegram unit exits 0 when `config/telegram.off` exists (`jarvis/kernel/jarvis_kernel/cli.py:113-119`), so `Restart=on-failure` does not restart it. Telegram is off.

### 1g. Ports and binding (from code)
| Port | Service | Bind | Guard |
|---|---|---|---|
| 48911 / 48912 / 48915 / 48916 | N.E.K.O main, memory, agent, plugin runtime | 127.0.0.1 (`app/main_server/__main__.py:109`, `app/memory_server/__main__.py:68`, `app/agent_server/__main__.py:38-43`, `app/agent_server/plugin_host.py:164-167`) | upstream HostOriginGuard / mutation_auth |
| 48913 | N.E.K.O monitor | **0.0.0.0 by default** (`config/network.py:161-163`, `app/monitor.py:554`) | not started by `launcher.py` or `neko-start.ps1` (grep found no launch); exposure only if started by hand |
| 48930 | local speech server | 127.0.0.1 (`local_speech_server.py:284`) | loopback Host header; no Origin check (no CORS headers, so browsers cannot read replies) |
| 48940 | widget | 127.0.0.1 (`widget_server.py:370`) | loopback Host, same-origin POST |
| 48945 | wake status | 127.0.0.1 (`wake_listener.py:579`) | loopback Host, Origin absent or widget |
| 48950 | privacy gateway | 127.0.0.1 (`jarvis/privacy/gateway.py:377`) | |
| 8080 | llama-swap | 127.0.0.1 (`local-models.ps1:39`, guarded by `jarvis/local_models/test_server_flags.py`) | |
| 8799 | JARVIS console | 127.0.0.1 hard-coded (`config.py:13`) | exact loopback Host+port; POST/DELETE need same Origin or `X-Jarvis-Console: 1` |
| 8088 | hub (Caddy, in WSL) | 127.0.0.1 per site (`jarvis/hub/Caddyfile`) | WSL forwards to the laptop only (doc) |
| Oracle 8790 | kernel llm-proxy + owner plane | 127.0.0.1 + Tailscale 100.125.91.51 (`deploy/jarvis-llm-proxy.service`) | bearer tokens |
| Oracle 8765 | kernel node endpoint | Tailscale IP; CLI refuses 0.0.0.0 (`cli.py:108-110`) | node token |
| Oracle 4750 / 9119 | Smokescreen, Hermes dashboard | Tailscale IP | |
| Oracle 20128 | OmniRoute | bind lives in an env file not in the repo; unit says "loopback only" but the hub proxies to `100.125.91.51:20128` | unverifiable here |

### 1h. Phone access (how the travelling owner reaches anything)
- Telegram: **off** (`jarvis/config/telegram.off`, enforced in `cli.py:113-119`, `scripts/nightly.py:71`).
- JARVIS console and Bench: **127.0.0.1 on the laptop only**; the laptop is off. "Phone screen" means a 360x740 layout tested in Playwright (`jarvis/ui/playwright.bench.config.ts`), not reachability from a phone.
- Hub / Mission Control: loopback inside the laptop's WSL box.
- Kernel owner plane: Tailscale 8790 with the owner token, which lives on the laptop; there is no phone client. Approvals from a phone: not possible (console only, or kernel CLI over SSH).
- Only theoretical phone-reachable surface: Hermes's own dashboard on Tailscale 9119 (username + password), which is Hermes's UI, not JARVIS's approvals/tasks. Not verified.
- No client in the repo sends `Idempotency-Key` except the caretaker (`grep` result in section 6), and the console proxy (`server.py:306-310`, `kernel_client.py:36-38`) would drop such a header anyway.

---

## 2. Capability truth table

| Capability | Status | Files and symbols | Evidence (exact tests / result files, dates) | Limitations | What remains unverified |
|---|---|---|---|---|---|
| Wake word "Hey Neko" detection on the owner's voice | **Blocked** (owner hardware) | `wake_listener.make_detector`, `WakeLoop._listen` | None. `jarvis/tests/neko_voice_owner_record.py` never produced a `results/voice-owner-*.json` (none in repo). Bench G-017/G-018 deferred in `results/bench-20261017-220721.json`. Guided step: "0/5 woke, room at noise floor, inconclusive" (manual row, `evidence_manual.json`) | zh-en phone-token model; "Hey Nico" also wakes it (by design) | recall at 0.5 m / 1.5 m, threshold |
| Wake on synthetic voices | Implemented, unverified (manual claim) | `jarvis/wake/eval_wake.py` | Manual rows only: en 15/15, hi 10/10, **te 2/10 (fail)**; source `outputs/neko/wake-eval/report.json` is on the laptop, not in repo | Telugu-accent recall is poor | everything; no artifact |
| False wakes in a room | Partially working (manual claim) | | Manual row V-false-wakes-ambient "1 detection in 19,866 s" (listener `/status` count read by hand, 2026-10-16); no log in repo. Other docs still say 0 (section 4) | the one detection is unexplained | a recorded run |
| Wake state machine (record, VAD end, cancel words, stop, pause, detector restart, gain, resample) | **Mocked/simulated only** | `WakeLoop` | `jarvis/wake/test_wake_listener.py` (17 tests, FakeDetector/FakeVad/fake ASR); bench G-001..G-016, G-024..G-035 pass in `bench-20261017-220721.json` (Linux); 4 of them re-run by me (section 6) | | with the real detector + VAD + mic |
| Wake -> STT -> main_agent task, from a WAV file | Implemented, unverified (manual claim, laptop) | `wake_listener --wav` | Manual row V-file-loop-e2e "run ae341274 succeeded" (2026-10-16); no result file | file, not mic | live mic chain |
| Wake latency (keyword end -> detection) | Partially working (measured on synthetic clip) | `jarvis/wake/wake_latency.py` | `results/wakelatency-20261016-180934.json`: p50 0.215 s, max 0.291 s, 10 detections, synthetic Kokoro clip, real-time pace; host not recorded in the file (script is Windows-only, so the laptop) | synthetic voice | owner voice |
| Speaker->mic loopback recall | **Failing** (manual claim) | `jarvis/wake/loopback_test.py` | Manual row V-loopback-speaker: 2/3 then 0/3 at ~10 dB SNR | | |
| Echo / self-wake suppression | Implemented, unverified | `_is_speaking` + speech `/activity` estimate | G-004 mocked (speaking=True fake); G-022 deferred | estimate only: queued or delayed playback outside "synth time + length + 1 s" is unguarded | real speakers |
| Barge-in (interrupt Neko while she speaks) | **Missing by design on the wake path**; Partially (manual button) in console | wake: detections dropped while speaking (`wake_listener.py:323-326`); console: `voice.ts:38` | Repro R3 (section 6): "Hey Neko, stop" while speaking -> `ignored: Neko was speaking`, 0 stop calls. G-021 deferred. Upstream N.E.K.O TTS has interrupt plumbing (`main_logic/tts_client/_infra.py:62-91`), not measured (REQUIREMENTS V3 "not measured") | | everything live |
| Cancel by voice ("cancel", "never mind") | Mocked/simulated only | `PURE_CANCEL`, `CANCEL_WORDS` | G-002, G-024 (fake ASR); G-020 deferred | | real mic |
| Stop running task by voice | **Partially working** (code defects) | `stop_task` -> widget `/api/action` -> `main_agent.stop_task` | G-003, G-028 (fakes). Repros show: stop during planning is a no-op; real widget reports ok=True when nothing stopped; overlapping runs lose the stop (defects D1-D3) | stops after the current step only; does not cancel kernel/lab/desktop jobs in flight | live |
| STT English (Parakeet int8) | Implemented, unverified here (manual claim, laptop) | `Voice.transcribe`, speech server `/v1/audio/transcriptions` | `jarvis/console/tests/test_local_speech_server.py` (fake engine; I ran 7 selected, pass); doc: 0.04-0.1x real time warm (05_OPERATIONS:244, 2026-10-14, laptop), `live_check.py` "41/41" 2026-10-14 (doc only) | | owner WER (G-023 deferred, V-asr-wer-owner not run) |
| STT Hindi / Telugu (IndicConformer) | Partially working (console only); **Missing on wake path** | `voice.py:86-92` | Doc: Telugu WhatsApp note 86 % word recall (2026-10-14, laptop, doc only); synthetic TTS->ASR round trip in `live_check.py` (doc only) | wake path always `en` (repro R2); N.E.K.O path depends on session language, `auto` -> `en`; console needs a manual language pick | owner hi/te speech |
| TTS en/hi/te | Implemented, unverified here (manual claim) | `local_speech_server.py`, Kokoro/Piper | unit tests with fake synthesizer; doc: Kokoro 0.35x RT, Piper hi 0.9x / te 1.2x cold (05_OPERATIONS:244) | | quality, latency on owner hardware |
| Speech-end -> text latency SLO (1.5 s, `capabilities.json`) | **No evidence** | | no row in any score/result file | | all |
| Full voice -> action -> spoken reply latency | **No evidence** | `e2e-live/voice-task.spec.ts` measures it | no committed result | | all |
| N.E.K.O browser mic conversation | **Blocked / unverified** | `faster_whisper._EndpointModel` | `tests/unit/test_asr_local_endpoint.py` (fake server); 05_OPERATIONS:205/239 "not exercised end to end", realtime refused by Dahl | | everything |
| Push-to-talk in the JARVIS console | Implemented, unverified here (integration test on Windows) | `voice.ts`, `CommandCenter.tsx` | `jarvis/ui/e2e/voice.spec.ts` (real console + throwaway kernel + real models, fake mic from a WAV; msedge/D: paths, Windows only); no result file in repo | 60 s auto-stop drops the transcript and forces English (D6) | real mic |
| Global push-to-talk hotkey | **Missing** | | NEKO_REQUIREMENTS X4 "MISSING" | | |
| Console shows real kernel tasks, approvals, cancel, results | Partially working (integration-tested) | `store.ts`, `CommandCenter.tsx`, `Tasks.tsx`, `App.tsx` | Playwright `e2e/*.spec.ts` against `jarvis/console/tests/e2e_server.py` (real kernel code in a temp dir, scripted Hermes) - Windows config; L-027..L-033 (real kernel owner plane, fake agent) pass in `bench-20261017-220721.json` | "Stop waiting" does not stop tasks (documented) | live against the Oracle kernel |
| Neko icon state from real signals | Partially working | `neko_state.decide`, `widget_server.neko_state` | `jarvis/console/tests/test_neko_state.py` (unit); doc: live_check 66/66 (doc only) | failed/partial runs not shown; step detail approximate; "speaking" estimate | live |
| Bench screen | Partially working | `jarvis/console/jarvis_console/bench.py`, `ui/src/screens/Bench.tsx` | `jarvis/console/tests/test_bench_routes.py`; Playwright `e2e/bench.spec.ts` (fake runner) "5/5" per PROGRESS (not re-run) | evidence labels overstate voice cases (D7) | |
| Phone access to JARVIS | **Missing** (Telegram off) / Blocked | `config/telegram.off`; console 127.0.0.1 | code (section 1h) | | |
| Autostart at login | Implemented, unverified here | `install-autostart.ps1` | doc says installed 2026-10-14 | | that the task exists |
| Watchdog self-heal | Partially working | `watchdog.py` | `jarvis/console/tests/test_watchdog.py` (decide() only, unit; I ran 1); doc: "killed speech server back in 22 s" (2026-10-15, doc only) | does not watch wake listener or `desktop` plugin (D5) | live |
| Daily report | Implemented, unverified here | `daily_report.py` | none in repo | local file only, no delivery | |
| Loopback-only binding of laptop services | **Verified by code** (not by a port scan) | section 1g | `jarvis/local_models/test_server_flags.py`; widget/speech/wake host tests (I ran selected ones) | N.E.K.O monitor defaults to 0.0.0.0 if started | a live `netstat` on the laptop |

---

## 3. Confirmed defects

Severity reflects impact on the owner's safety/control or on honesty of reporting.

**D1 - High - confirmed (code + repro). Two overlapping `run_task` calls both start; the main agent then forgets one, so Stop (voice, icon, panel) silently stops nothing.**
- `plugin/plugins/main_agent/__init__.py:360` checks `self._active` **before** the long `await self._make_plan(...)` (:364); `_active` is only set in `_start` (:328). A second request during planning passes the check. When the first run ends, `_execute`'s `finally` sets `self._active = None; self._cancel = None` (:453-454) although the second run is still running.
- Repro `repro_main_agent2.py` (real plugin class, fake LLM/dispatch, section 6): `states: {'voice task': 'done', 'typed task': 'running'} | _active: None`; `stop_task now: {'stopped': False, 'message': 'Nothing is running.'}`; a third `run_task` is accepted. `repro_main_agent.py`: 2 runs in state `running` at once.
- Trigger in practice: a "Hey Neko" command while a chat/tool-called task is still planning (planning takes 10-60 s with Dahl). Whether the N.E.K.O plugin runtime serializes entry calls was not verified; the class itself has no guard. Mitigation that still works: kernel kill switch / Stop all in the console (laptop only).

**D2 - Medium - confirmed (code + repro). "Hey Neko, stop" during planning does nothing; the task then starts.**
- `task_running()` (`wake_listener.py:159-164`) uses the newest run **file**; a run being planned has no file (`main_agent` persists in `_start`, :324-333). `repro_widget2.py`: `newest_run` returns the previous run's `done`. The listener then treats "stop" as `PURE_CANCEL` (:411-415) -> outcome "cancelled by voice" while the plan continues. `main_agent.stop_task` would also answer "Nothing is running" in that window (:489-490).

**D3 - Medium - confirmed (code + repro). The real widget reports a voice stop as successful when nothing was stopped; the bench fixture for this case does not match the real widget.**
- `widget_server.run_plugin_entry` returns `{"ok": True}` whenever the entry finished, ignoring `{"stopped": False}` (`widget_server.py:205-209`). `repro_widget2.py`: `do_action(stop) -> {'ok': True}` for a `Nothing is running.` answer.
- G-028 (`jarvis/evals/neko95/bench/adapters/g.py:365-389`) uses a FAKE widget that answers `{"ok": False, "error": "nothing to stop"}`, a reply the real widget never produces, so G-028 passes on a contract that does not exist.

**D4 - Medium - confirmed (code + repro). Voice tasks are reported "task started" before planning finishes; a plan that fails later is never reported on the voice path.**
- `widget_server.start_task` returns `ok: True, note: "planning"` after 12 polls (`:138-145`; `repro_widget.py`). If `run_task` then returns `Err` (PlanError, busy, model error; `main_agent/__init__.py:360-366`), no run file and no `push_message` are produced (push happens only in `_execute`). The wake outcome stays "task started"; the icon shows the previous run.

**D5 - Medium - confirmed (code). The wake listener is not supervised.**
- `watchdog.py:26-31` probes 9 servers and 8 plugins, not 48945 and not `desktop`. A mic error (device unplugged, driver error) raises out of `run()` (`wake_listener.py:512-525`) and the process exits; only detector exceptions are restarted (:262-270). The desktop monitor alerts on 48945 (`monitor.py:24-25`) but nobody restarts it until the next login.

**D6 - Low - confirmed (code). Console push-to-talk: the 60 s auto-stop transcribes in English and drops the text.**
- `jarvis/ui/src/lib/voice.ts:65` calls `this.finish("en")` regardless of the selected language, and the returned text is not put in the command box (only `CommandCenter.stopTalk` does that, :174-187). Repro: hold Talk for 60 s in Hindi or Telugu. Not run here (needs a browser + mic).

**D7 - Medium (honesty of reporting) - confirmed (repro). The Bench screen labels most voice cases "integration", including the 9 physical-hardware cases and the FakeDetector-based ones.**
- `bench.py:60-69, 223-229` decides "mocked" by a regex on the scenario text. `repro_bench_labels.py` with the real `Bench.evidence_of`: `g_voice {'mocked': 11, 'integration': 26}`; G-002/G-003/G-004 (fake detector + fake ASR, per the adapter's own docstring `adapters/g.py:1-3`) and G-017..G-023, G-036, G-037 (deferred, `check: none`) are all "integration". The dimension tally counts deferred ids too (`bench.py:276-280`). The 05_OPERATIONS and guide say replaced parts are labelled "fake"/"mock"; in practice the word "fixtures" is not matched, so many L cases with fixture agents are also "integration".

**D8 - Low - confirmed (code + repro). Cancel "from the screen" while submitting still starts the task.**
- `_cancel_now` cancels the asyncio task awaiting `asyncio.to_thread(self.submit, ...)` (`wake_listener.py:431-437`), but the thread keeps going. `repro_wake.py` R1: outcome `cancelled from the screen`, `counts.cancelled: 1`, and `submit()` still completed (`['open notepad']`).

**D9 - Low - confirmed (code). Wake path is English-only.** `_handle` calls `self.transcribe(audio)` (:393) with the default `lang="en"`; repro R2: `['en']`. Hindi/Telugu commands after "Hey Neko" go to the English model. Docs present hi/te STT as available without saying the wake path cannot use it.

**D10 - Low - confirmed (code). Icon hides failed/partial/held/cancelled outcomes and approximates the current step** (`neko_state.py:78-79`; `widget_server.py:84-87`). A failed voice task shows "sleeping ... Click me or type a task".

**D11 - Low - theoretical. A permanently missing optional server makes the icon show "error" forever.** `local_models` (8080) is in the watchdog's server list (`watchdog.py:30`); `neko-start.ps1:56-60` only starts llama-swap if its exe exists. If it is absent, the watchdog reruns the stack 3 times an hour and then sets `gave_up_on`, which `neko_state.decide` turns into `error` (`neko_state.py:46-49`).

**D12 - Informational - confirmed (code).** N.E.K.O monitor defaults to `0.0.0.0` with auth off (`config/network.py:161-163`, `app/monitor.py:544-554`); not started by `launcher.py`/`neko-start.ps1`, so not exposed unless started by hand.

**D13 - Informational - theoretical.** Echo guard depends on an estimate (synthesis time + audio length + 1 s); delayed or queued playback can self-wake. Not testable here (G-022 deferred).

---

## 4. Doc-vs-code discrepancies

| Claim (where) | What the code / evidence shows |
|---|---|
| Guide sec. 3: "You: voice ... -> N.E.K.O session: speech to text -> main_agent -> desktop plugin" as the voice route | Three different voice routes (sec. 1a-1c). The working-on-paper wake route goes widget -> main_agent; console push-to-talk goes to the kernel/Hermes, not main_agent; the N.E.K.O mic route was never exercised end to end (05_OPERATIONS:205, :239; REQUIREMENTS V4 "MISSING") |
| Guide sec. 7: STT "Parakeet (English), IndicConformer (Hindi, Telugu)" | True for console push-to-talk with a manual language pick. Wake path always English (D9). N.E.K.O path sends no language on `auto`, which the speech server treats as English |
| Guide sec. 7: "TTS ... interrupt drops queued audio" | Upstream N.E.K.O plumbing only; not measured (REQUIREMENTS V3). Wake path cannot barge in (detections ignored while speaking) |
| Guide sec. 7: "Real-microphone tests (G-036, G-037) wait for the laptop" | 9 voice cases are deferred, 7 of them **critical**: G-017..G-023 (owner recall at 0.5/1.5 m, false wakes, voice cancel, barge-in, self-wake, WER) plus G-036, G-037. v1 score C1 = 5.0 with 6 criticals missing (`score-20261016-154951-server-rescore.json`) |
| Guide sec. 12: "Each drives the real code; any replaced part is labelled 'fake' or 'mock'" | Bench labels 26/37 G cases "integration" incl. fully faked and hardware-only cases (D7) |
| Guide sec. 12 / D30: Benchmark Console "usable from a phone", "a loopback-only phone screen" | Bound to 127.0.0.1 on the laptop; usable only at a phone-sized viewport on the laptop itself. No phone path exists while Telegram is off and the laptop is off |
| Guide sec. 4: "Idempotency keys: if your phone resends a request..." | No phone client exists; no UI/console/widget/wake code sends `Idempotency-Key` (only `jarvis/caretaker/run.py`); the console proxy does not forward request headers. PROGRESS lists "send Idempotency-Key from the phone UI / desktop" as still to do |
| Guide sec. 2: "Everything listens on 127.0.0.1 only" | True for code started by `neko-start.ps1`; N.E.K.O monitor defaults to 0.0.0.0 (not started). Not checked live |
| Guide sec. 10 / 05_OPERATIONS:252: watchdog "restarts what is down" / "checks the six servers, the four plugins" | Code checks 9 servers and 8 plugins; omits the wake listener and the desktop plugin (D5) |
| D26 "10 minutes of live microphone: 0 false wakes"; PC_CONTROL_TEST_REPORT:42 "0 false wakes in about 3 hours"; NEKO_REQUIREMENTS X3 "0 false wakes in about 3 hours" | PROGRESS_REPORT:72 and `evidence_manual.json` corrected this to "1 detection in 5.5 h". The two other docs were not updated |
| 05_OPERATIONS:239 "Local speech recognition is faster-whisper base" | Superseded by :244 (int8 endpoint via `NEKO_WHISPER_ENDPOINT`); the file contradicts itself |
| 05_OPERATIONS:392 / D26 "'Hey Neko, stop' stops a running task" | Not during planning (D2), not when runs overlap (D1), only after the current step, and only main_agent runs (not kernel/desktop/lab jobs in flight) |
| OmniRoute unit description "loopback only" | Hub Caddyfile proxies `router.localhost` to `100.125.91.51:20128`; the real bind is in an env file outside the repo. Unverifiable |
| `capabilities.json` C1 SLO `speech_end_to_text_s: 1.5` | Never measured; no row in any result |

---

## 5. Bench cases (G voice, L UX) classified

Source: `jarvis/evals/neko95/bench/cases/g_voice.json`, `l_ux.json`, adapters `g.py`, `l.py`; latest run `results/bench-20261017-220721.json` (2026-10-17 22:07, Linux x86_64, py3.11.17): **G 28 pass / 9 deferred; L 38 pass**.

**G (voice)** - all executed G cases drive `WakeLoop` with `FakeDetector`/`FakeVad` from `test_wake_listener.py` (adapter docstring). My classification (not the Bench screen's):
| Cases | Real parts | Faked | Class |
|---|---|---|---|
| G-001..G-012, G-014, G-016, G-024, G-025, G-029..G-033 | `WakeLoop` state machine, cancel/stop regexes, gain, timing | detector, VAD, ASR, submit/stop | **Unit/mocked** |
| G-013, G-035 | `resample_int16`, `read_wav_16k`, `FileSource` | none (pure functions) | Unit (real code, synthetic audio) |
| G-015, G-034 | real `serve_status` HTTP server on loopback | the loop's collaborators | Integration (HTTP guard) |
| G-026, G-027, G-028 | real `submit_task` / `transcribe` / `task_running` / `stop_task` HTTP clients | FAKE widget / FAKE speech server; G-028's fake answer does not match the real widget (D3) | Integration with faked boundary |
| G-017..G-023 (all critical except G-023 high), G-036, G-037 | none | n/a | **Deferred** (no check), shown as "integration" by the Bench screen (D7) |

**L (UX)** - no voice involvement; relevant to "UI shows real state":
| Cases | Real parts | Faked | Class |
|---|---|---|---|
| L-001..L-007, L-011, L-014..L-018, L-034..L-038 | desktop `coordinator.run` + `summary()`, `decide.validate` | PC agents (fixture results), model plan | Integration with fixture agents (owner-facing text) |
| L-008..L-010 | `core.Pc` approval wait | console HTTP calls | Integration, faked console |
| L-012, L-013 | real kernel `Kernel` approval record in a temp dir | none | Integration |
| L-019, L-020 | `missions.explain` | mission records | Unit |
| L-021..L-026 | main_agent executor texts, plugin entries (`run_task`, `stop_task`, `run_status`) | model, dispatch | Unit/mocked |
| L-027..L-033 | real kernel owner plane + llm_proxy over loopback HTTP | provider (mock 429/up), Hermes agent (fake) | Integration |
None of the L cases exercises the React UI or the console HTTP server; the UI is covered only by Playwright specs (Windows config, not re-run here).

---

## 6. Commands run and real outputs

All read-only. Repo mounted with `:O` (throw-away overlay); repro scripts in the scratchpad (`.../scratchpad/audit/repro/`), mounted read-only. Nothing in the repo was modified (`git status --short` was empty before and after).

1. Existing wake tests (4 of 17):
```
nice -n 19 podman run --rm --cpus=1 --userns=keep-id -e HOME=/tmp -v ~/hq-neko:/src:O neko-test sh -c "cd /src && python -m pytest 'jarvis/wake/test_wake_listener.py::test_stop_while_a_task_runs_stops_it' 'jarvis/wake/test_wake_listener.py::test_cancel_from_the_screen_during_recording' 'jarvis/wake/test_wake_listener.py::test_ignored_while_neko_speaks' 'jarvis/wake/test_wake_listener.py::test_status_server_loopback_and_origin' -q -p no:cacheprovider"
....                                                                     [100%]
4 passed in 1.20s
```
(A first attempt with `-p no:randomly` failed: `unrecognized arguments: --randomly-seed=20260731` from `pytest.ini`; no tests ran.)

2. Selected widget + speech-server tests:
```
... python -m pytest jarvis/console/tests/test_widget_server.py jarvis/console/tests/test_local_speech_server.py -q -p no:cacheprovider -k 'loopback or newest_task or chosen_language or foreign_host or valid_task'
.......                                                                  [100%]
7 passed, 19 deselected in 3.74s
```
3. Neko-state + watchdog (selected):
```
... python -m pytest jarvis/console/tests/test_neko_state.py::test_wake_word_heard_shows_listening_even_while_working jarvis/console/tests/test_watchdog.py -q -p no:cacheprovider -k 'wake_word_heard or gives_up'
..                                                                       [100%]
2 passed, 7 deselected in 0.59s
```
(A first attempt named a non-existent class `BuildStatusTests`: "no tests ran".)

4. Repro scripts (`python -B /repro/<name>.py` in the same container):
```
### repro_wake
R1 state while submitting: submitting
R1 outcome after cancel: {'what': 'cancelled from the screen', 'at': '05:04:00'} | counts.cancelled: 1
R1 submit() calls that completed anyway: ['open notepad']
R2 language passed to transcribe(): ['en'] (no caller ever passes hi/te)
R3 speaking=True, task running, said 'Hey Neko, stop': stop calls 0 | last {'what': 'ignored: Neko was speaking', 'at': '05:04:01'}
### repro_main_agent
run_task #1: Ok {'run_id': '2b8090f94b', ...}
run_task #2: Ok {'run_id': '1aa8601190', ...}
runs in state 'running' at once: 2 | _active = 1aa8601190
stop_task: {'stopped': True, 'message': 'Stopping after the current step.'}
final states: {'2b8090f94b': 'cancelled', '1aa8601190': 'done'}
### repro_bench_labels
g_voice {'mocked': 11, 'integration': 26}
   G-002 integration | check: cancel_words | ...   G-003 integration | ...   G-004 integration | ...
   G-017 .. G-023, G-036, G-037 integration | check: none | ...
l_ux {'mocked': 20, 'integration': 18}
### repro_widget
start_task -> {'ok': True, 'run_id': 'abc', 'note': 'planning'} after 12 status polls
### repro_main_agent2
states: {'voice task': 'done', 'typed task': 'running'} | _active: None
stop_task now: {'stopped': False, 'message': 'Nothing is running.'}
a third run_task while 'typed task' still runs: Ok
### repro_widget2
do_action(stop) -> {'ok': True}
newest_run while a new task is still planning (no file yet) -> done
```
5. Result-file reads (host Python, read-only `json.load`):
- `results/wakelatency-20261016-180934.json`: `V-wake-latency pass ... p50 0.215 s, max 0.291 s over 10 detections ... synthetic clip (timing only)`.
- `results/uilatency-20261016-180855.json`: `L-ui-action-p50 pass ... p50 2.65 s over 6 steps` (PC UI step, not voice; earlier run 16:51 failed at p50 7.34 s).
- `results/score-20261016-154951-server-rescore.json`: C1_voice score 5.0, cap 5.0, `critical_missing`: V-real-recall-0.5m, V-real-recall-1.5m, V-cancel-by-voice, V-barge-in, V-echo-no-self-wake, V-asr-wer-owner. Manual rows: V-synthetic-te **fail** (2/10), V-loopback-speaker **fail**.
- `results/bench-20261017-220721.json`: G `{'pass': 28, 'deferred': 9}`, L `{'pass': 38}`; G-014 `0.017 ms per 40 ms frame`; G-028 observed `'ok': False, 'note': 'nothing to stop'` (from the fake widget).
- `ls results | grep voice-owner` -> nothing (the owner recording was never run or never committed).
6. Greps used for exposure and phone checks: `grep -rn "0\.0\.0\.0"` (only N.E.K.O monitor, test/telemetry servers and deploy deny-ranges outside tests), `grep -rln -i "idempotency-key"` -> `jarvis/caretaker/run.py`, kernel files, `app/memory_server/idempotency.py` only; `grep -rn "telegram.off"` -> `cli.py`, `telegram.py`, `nightly.py`, monit conf, tests.

### Latency figures found (dates, machine)
| What | Value | Date / machine | Artifact |
|---|---|---|---|
| Wake keyword-end -> detection | p50 0.215 s, max 0.291 s (n=10, synthetic) | 2026-10-16 18:09, laptop (Windows-only script; host not in file) | `results/wakelatency-20261016-180934.json` |
| Wake state machine | 0.017 ms per 40 ms frame (fakes) | 2026-10-17, Linux test server | `results/bench-20261017-220721.json` G-014 |
| STT int8 | 0.04-0.1x real time warm; 5 min Telugu note in 11 s | 2026-10-14, laptop i9-9980HK | doc only (05_OPERATIONS:244-245) |
| TTS | Kokoro 0.35x RT (5.6 s sentence: 11 s cold, 4 s warm); Piper hi 0.9x, te 1.2x cold | 2026-10-14, laptop | doc only |
| One PC UI step end to end | p50 2.65 s (n=6) | 2026-10-16 18:08, laptop + Oracle | `results/uilatency-20261016-180855.json` |
| Speech end -> text; voice command -> spoken reply | not measured | - | none |
