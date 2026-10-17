# Neko 9.5: progress report (2026-10-16, Windows laptop + hermes-agent-vm)

Scores come only from saved results, through `jarvis/evals/neko95/score.py` under the frozen rules v1. Score files are in `jarvis/evals/neko95/results/score-*.json`.

**Not at 9.5:**
- Several criticals still need the owner in person (real voice, Chrome tokens).
- Two latency targets are missed on this GPU.
- See "What blocks 9.5" below.

## Scorecard over the day

| Capability | Baseline 15:06 | Interim 16:19 | After round 3 (scored 16 Oct, server) |
|---|---|---|---|
| C1 voice | 5.0 | 5.0 | 5.0 (6 voice criticals need the owner) |
| C2 conversation | 0.0 | 0.0 → 5/5 cases pass 3/3 (16:20) | 10.0 |
| C3 command understanding | 5.0 | 8.5 (SLO missed) | 8.5 (plan p50 ≈ 5 s > 3.5 s) |
| C4 Windows control | 5.0 | 5.0 | 10.0 |
| C5 browser | 6.0 | 6.0 | 6.0 (no saved Chrome token) |
| C6 long missions | 0.0 | 6.0 | 6.0 (research-deck mission fails) |
| C7 troubleshooting | 5.0 | 5.0 | 10.0 |
| C8 research / documents | 0.0 | 0.0 | 4.0 (search empty; 12/24 citations supported) |
| C9 perception | 5.0 | 5.0 | 5.0 (3 vision criticals not run) |
| C10 proactivity | 0.0 | 10.0 | 10.0 |
| C11 performance | 0.0 | 0.0 | 6.0 (plan p50) |
| C12 orchestration | 6.0 | 6.0 | 10.0 |
| C13 self-improvement | 0.0 | 5.0 | 10.0 |
| C14 safety | 5.0 | 5.0 | 6.0 (2 rows need a laptop re-run) |
| **Mean** | **3.0** | **4.75** | **7.61** (lowest 4.0) |

The "after round 3" column is `results/score-20261016-154951-server-rescore.json`. The frozen scorer (rules v1, unchanged) ran on the saved results of the laptop runs up to 18:09. No new live runs were possible on the Linux server.
- Its file selection was fixed first: it now orders result files by the run time in their names instead of the file mtime (`jarvis/evals/neko95/tests/test_score_files.py`).
- On a fresh checkout every mtime is equal, so the 15:56 desktop run and the 15:58 diagnosis run had been skipped, and C4 and C7 showed their criticals as missing (6.89 instead of 7.61).
- On the laptop, the real mtimes give the same selection.

## What was found and fixed (live evidence first)

### C3: plans completed by rules

| Problem | Fix |
|---|---|
| "write X into WordPad" planned without typing | Typing step added; the app opens at run time if needed |
| "go to youtube" on an account planned as a page read; the account name typed into YouTube's search | Open the site; check the account first; never type the account name |
| "open my google account" went to google.com | The owner's literal site name wins |
| "find the error on the screen and fix it" planned a separate window step | The troubleshooter reads the active window itself |
| "create a pdf and open it" invented a Word print flow | Asks what it should be about |
| A12 regression from a new typing rule ("put … on youtube") | Fixed, with a regression test |
| Live: context from the previous request bled into unrelated requests (G03, G04, G08 in round 1) | Context only for follow-up wording |
| Live: "tell me when it is playing" planned a disk check (G07) | Dropped; the play check is the answer |

**Result** (clean run, 16:38):
- all 19 plan cases (A07 and H19 included) 5/5 each, 95/95;
- dev 69/70;
- holdout 30/30.

### Live problems found and fixed

- **C4: modern apps.** Calculator's window belongs to ApplicationFrameHost, so "calculator|One" failed. The lookup now finds the single frame titled with the name (refusing if two match) in the actions, the snapshot reader and the window-text reader.
  - Live results: W-uwp-app (1 + 2 = 3, read back), W-installer-dialog (msiexec help read and OK pressed by name), B01–B20 → 21/21.
  - The test's own cleanup killed the wrong process name (`Calculator.exe` instead of `CalculatorApp`) and left 3 Calculator windows open. I closed them and fixed the cleanup.
- **C6 / C8: research from inside Neko had never worked.** N.E.K.O cut plugin-to-plugin entry calls at 10 s (plugin side) and 60 s (router). Core fix in D28; it needed a full N.E.K.O restart (done 16:45, no job active).
- **C6: missions** also used the plugin-startup event loop, which the host closes after startup ("Event loop is closed"). Missions now start on first use, on the entries' loop.
- **C7: three new live cases**, each cause verified against the system:
  - T-port-conflict: holder found, moved to a free port, app runs;
  - T-disk-space: real free space against need, no invented fix;
  - T-service-down: nothing listening; with the service up, the app starts.
  - Live: D01–D14 plus T → 17/17.
- **C10: monitor** (live canaries):
  - a crash alert 47 s after the crash, announced exactly once;
  - disk floor, service down on the 2nd failed check, stuck task;
  - 0 unconfirmed alerts.
  - Its timer was sync while the host awaits timers: each tick logged an error after running. Fixed: async with a worker thread.
- **C1: honesty correction.** The false-wake evidence said "0 in 13,583 s". The listener's own count shows **1 detection at 14:26:24**, during or just after the owner's guided voice test (likely the owner's own "Hey Neko", unconfirmed). Corrected to "1 in 5.5 h (≤ 0.18/h)".
- **C13: the live regression gate worked.** A staged proposal (a new site "hacker news" plus one example) fixed its new case but regressed A45, so the gate refused it. Nothing was activated (236 real decider calls).

### Test-harness mistakes, stated

- **run_unit.py:** at first it mapped parametrized tests wrongly and reported 2 "fails" while pytest passed. That file was deleted and regenerated after the mapping fix.
- **Memory test:** the first run sampled its positive control from persona and recent-chat files, which are not in the recall pool, and failed. That failed file is kept. The control now samples facts only.
- **Live coordinator:**
  - G01 now expects the account check first (stricter);
  - G03 checks the typing step's presence, like A34 and H19;
  - each row keeps `ok_old_rule`, and round 2 was 11/12 under the old rule.
  - The scorer changes are in NEKO_95_EVALUATION_PLAN.md.

## Performance (measured)

- **Decider:** about 10 tokens/s, because the owner's apps hold about 1.2 GB of the 4 GB GPU and the model spills into shared memory.
- **Plan p50:** 3.9–4.9 s against a 3.5 s target: **missed**.
- **Qwen3-1.7B:** 0.7 s but 17–20% less accurate: rejected.
- **Fast path for one-command requests:** judged on holdout-2, which was written and hashed before the rules.
- **Strict per-tool output schema:** built behind a switch; to be measured.
- **One UI step end to end** (plugin → console → kernel on Oracle → node → Windows → result): measured in round 3. Node job polling went from 3 s to 1 s and plugin polling from 1 s to 0.5 s.

## Linux test container (2026-10-16 evening, relative's server, owner away)

The rootless podman image `neko-test` (`~/neko-test/Containerfile`, outside the repo) now has pytest-asyncio, pytest-randomly and pytest-xdist, plus **Google Chrome** (Playwright `install --with-deps chrome`) and **gitleaks 8.30.1** (sha256 checked).

| Suite | Before | After |
|---|---|---|
| Kernel (`unittest discover`) | 469 run, **9 errors**, 21 skipped | **483 run, OK, 16 skipped** (all Windows-only) |
| Desktop plugin (pytest, seed 20260731) | 49 passed, **4 failed** | **57 passed** |

**What the failures were:**
- **Kernel, 9 errors (test_browser): a real bug, exposed by a missing Chrome.**
  - `BrowserSession.__enter__` started Playwright, then the launch failed (no Chrome), and Playwright was never stopped.
  - Its loop stayed "running", so every later session in that process failed with "Sync API inside the asyncio loop". The temp profile and guard proxy leaked too.
  - On the laptop the same would happen after any failed launch, until the node restarts.
  - Fix: a failed launch cleans up everything. `__exit__` stops Playwright even if closing the browser fails.
  - Chrome added to the image. Tests: `FailedLaunch` (2), which fail on the old code.
- **Desktop, 3 failures (troubleshooter):**
  - the named-file finder knew only `C:\` paths, and the disk check only drive letters;
  - it now also reads absolute POSIX paths (never the path inside a URL) and `on /mount`;
  - a drive that does not exist says "could not be checked" instead of "None MB".
  - The Windows cases are unchanged; the disk test uses `C:` on Windows and `/` on Linux.
- **Desktop, 1 failure (mission resume after a crash):**
  - `coordinator._alive()` used only the Windows API, so off Windows every lock owner looked alive and a crashed task's lock was never taken over;
  - it now uses psutil off Windows (a zombie counts as dead; unsure counts as alive).
  - The Windows path is unchanged.
- **New tests, each failing on the old code:**
  - the path finder (Windows, POSIX and URL cases);
  - a missing drive;
  - `_alive` for a dead, a live and a 0 PID;
  - lock takeover from a dead owner, refused for a live one.

Still Windows-only (not faked here): the 16 kernel skips (UI Automation, Win32, Windows paths), Chrome profiles, microphone.

### Round 2: the whole test estate on Linux

**The image now installs from `uv.lock`.**
- Before, it compiled `pyproject.toml` afresh, so it tested different versions from the laptop (starlette 1.8.0 instead of 1.3.1, pydantic 2.14.1 instead of 2.13.5). Three static-file tests hung for 300 s on that.
- It also has Node 24.22.0, the rest of the dev group, `python-pptx`/`pdfplumber` (pinned), and a `neko-plugin` launcher. Its environment matches `run-upstream-tests.ps1`.
- Rebuild: `~/neko-test/sync-and-build.sh`. Recipe copy: `jarvis/scripts/neko-test/`.
- Run as a normal user on a throw-away overlay: `podman run --rm --userns=keep-id -e HOME=/tmp -v ~/hq-neko:/src:O neko-test …`. As root, permission tests fail.

| Suite (lock-based image, normal user) | Result |
|---|---|
| Kernel | 483 run, OK, 16 skipped (Windows-only) |
| Each plugin's own tests (10 plugins, run separately) | 544 passed, 1 skipped, 0 failed |
| Plugin framework (`plugin/tests/unit` + `integration`) | 4289 passed, **8 failed** (snapshots, below) |
| Upstream `tests/unit` (5 workers, 7 min) | 41,978 passed, **5 failed** (below) |

**Fixed in code:**
- **deliverables: the slide "fits" check.**
  - Without the theme font file it measured with Pillow's fixed 10 px bitmap, at any size, so 480 words "fit" a 10 × 3 in box and overflow went unreported.
  - It now uses the scalable default font at the asked size.
  - A test forces the missing-font path and fails on the old code.
- **Settings forms** (`config.schema.json`, 8 languages) for desktop, research, deliverables and local_team. Upstream #3193 requires one for every bundled plugin.
- **web_search test.**
  - Since upstream #2927, a throttle wait over 3 s fails fast as "busy", so the old total-timeout test tested that instead.
  - It now uses a 2 s wait, so the total timeout must really cut a coordinator wait.
  - A new test covers the fail-fast path.

**Not fixed, with reasons:**
- **Plugin metadata snapshots (`plugin.meta.json`), 8 plugins: need the laptop.**
  - Snapshots are Windows probe results; rebuilding here would stamp them as Linux builds.
  - research, deliverables, local_team (and desktop, which has none) changed after their snapshot. Upstream warns the host may then show **zero entries** for the plugin until it rescans, so its UI actions can fail.
  - Checked in the host code: entry calls still work. At start, a refused snapshot means one rescan (an import), and the 17:24 live research run did reach its entry.
  - The cost is a rescan on every start (a same-machine snapshot is never re-cached), and an empty entry list in the discovery preview until the plugin has started, so a hosted panel button can fail in that window.
  - doc_tools, jarvis_bridge, learn_vault and main_agent list gitignored `.vscode` files, so they are valid only on the laptop.
  - At home: delete those 4 plugins' `.vscode` folders, then for each of the 8 run `uv run neko-plugin build <plugin> --keep-staging` and copy the staged `plugin.meta.json` back into `plugin/plugins/<plugin>/`.
  - I left upstream's fingerprint rule alone: it may only skip folders that both packers drop, and upstream's lifekit tracks `.vscode` in git.
- **4 upstream tests that fail only on Linux:**
  - cloudsave clearing a read-only tree (POSIX permissions);
  - knowledge admission (`building` vs `queued`, a timing race);
  - voice enrollment equality (`remaining_seconds` differs by microseconds; Windows' 15.6 ms clock hides it);
  - `test_visit_runtime_finalize`, timing-flaky (failed 1 of 3 runs alone).
  - None of them is in Neko code.
- Mixing every plugin's `tests/` into one pytest session clashes on identical file names (`tests/test_smoke.py`), so plugins are run one by one.

## Autonomous mission on the Linux server (2026-10-16, owner travelling; D29)

**Summary.** The repo now has a 572-case capability benchmark (v2) that drives the real code. Synthetic secrets in its cases are stored split by a `{{SPLIT}}` marker that the runner removes, so secret scans of the tree stay clean. Its final run: 546 passed, 1 failed, 25 deferred (Windows or owner hardware).
- It found and fixed **21 product bugs**, each with a regression test (list below).
- Live tests ran against **Dahl** (owner-authorised key; synthetic data, public lane only) and against **headless Chrome with four fake profiles**.
- trim-1 was reviewed, vendored, built and measured. It stays off by default, like the new evidence selector.
- v1 is unchanged: mean 7.61 (frozen rules, saved laptop results).
- Neko is **not** "JARVIS-equivalent". The v2 scores below mean "every defined case passed", not general capability.
- Every test ran headless in the podman container at `nice 19`, 2 CPUs at most, so nothing appeared on the relative's screen. No Claude model was used as a model under test.

### Scorecard v2 (rubric written before any case ran: `bench/rubric_v2.json`; run `results/bench-20261016-173117.json`)

| Dimension | Score | Label | Passed / executed | Deferred | Why not 10 |
|---|---|---|---|---|---|
| Intent | 10.0 | verified | 51/51 | 1 | |
| Planning | 10.0 | verified | 49/49 | 1 | |
| Desktop control | 9.0 | verified | 32/32 | 5 | critical Windows cases deferred |
| Browser control | 10.0 | verified | 68/68 | 2 | |
| Multi-step autonomy | 10.0 | verified | 43/43 | 1 | |
| Recovery / self-repair | 10.0 | verified | 78/78 | 3 | |
| Research | 10.0 | verified | 43/43 | 2 | |
| Memory | 10.0 | verified | 35/35 | 1 | |
| Tool / provider routing | 9.82 | verified | 56/57 | 0 | J-038 failed |
| Security | 9.0 | verified | 247/247 | 3 | critical cases deferred (real kill-switch hotkey, real profiles) |
| Reliability | 9.93 | verified | 135/136 | 0 | J-038 failed |
| Speed | 9.0 | provisional | 17/18 | 0 | J-038 failed; fewer than 20 cases |
| Resource efficiency | 10.0 | verified | 54/54 | 2 | |
| Skill extensibility | 10.0 | verified | 26/26 | 0 | |
| User experience | 10.0 | verified | 70/70 | 2 | |
| Voice | 9.0 | provisional | 16/16 offline | 7 | **all physical checks DEFERRED: owner hardware required** |

What the 10s do NOT cover:
- **The cases are deterministic.** Models are mocked at the model boundary, and the laptop's local decider, vision and voice models were not run.
- **The benchmark was written by the same team that fixed the bugs.** Its failures were investigated against the code, never "fixed" by changing an expectation.
- **Real-world rates live elsewhere:**
  - real Dahl: routing 12/12, research 5/5;
  - the planning pipeline with a stand-in model: 97/149;
  - for the production decider, v1 C3 = 8.5 (laptop).

Per area (final run):
- A 67/67, B 33/33 (+5 deferred), C 46/46 (+2), D 42/42 (+1), E 31/31 (+3);
- F 29/29 (+1), G 16/16 (+7), H 35/35 (+1), I 16/16 (+2);
- J 46/47 (+1), K 87/87 (+2), L 26/26, M 23/23, N 33/33, X 16/16.

### Live tests on this server (owner-authorised Dahl key; results in `results/live-*`)

**Model proxy to Dahl, 12/12** (real kernel gateway and proxy, only the 2 enabled Dahl models):
- 5/5 correct answers; p50 4.07 s, max 5.29 s; first streamed chunk after 0.99 s; 121 tokens per call.
- A private-lane task never reached Dahl: 503, 0 calls.
- A fake token in the prompt arrived as `[REDACTED]`.
- The tool call came back well-formed.
- The disabled model can't be reached by naming it.
- The key appears in none of about 23,000 characters of replies, headers, streams and audit rows.

**Research, 5/5 correct on public questions**:
- Real DuckDuckGo code, real fetching and the real NLI checker (model revision a150876, sha256 checked); Dahl planned and wrote.
- No fabricated sources.
- 37 of 89 cited sentences (42%) were confirmed by the checker; the rest are labelled unconfirmed.
- 22–199 s per run.
- This matches the laptop's 7/16–11/17: thin sources, not invented ones, are what holds C8 down.

**Fake Chrome profiles, 12/12** (real Chrome "User Data" with 4 made-up profiles, headless):
- Registry with masked addresses and distinct fingerprints.
- Each alias resolves to its own profile; "robin" (ambiguous) asks; an unknown name is refused, and no full address appears in any error.
- The plan for "github in Kiran's profile" checks the account first.
- Each profile is signed in as itself only; signing out of one leaves the others signed in.
- A profile store already in use is refused in 0.4 s.

**Planning pipeline with Dahl as a stand-in for the local decider, 97/149** (exploratory; names replaced by made-up ones):
- Under Dahl's structured-output mode the model returned bare `{"tool": ...}` steps. The run therefore sends the prompt without that mode.
- The remaining failures are mostly the stand-in's format (31 not JSON) or its own questions.
- This does **not** measure the production decider (v1 C3 = 8.5 on the laptop).

### trim-1 decision (reproducible: `bench/context_bench.py`, `results/context-*.json`)

- **What it is:** a 712-tree LightGBM line selector in Go: Apache-2.0, no dependencies, no network.
  - The repo is 2 days old with no CI; its benchmark claims can't be reproduced.
  - Vendored at f16ff28; model checksum matches the card; its 7 tests pass (incl. bit-for-bit parity with LightGBM).
- **On 59 synthetic fixtures (development set; the evidence selector was tuned on it, so exploratory):**

| Mode | Answer kept @16 KB | Fits 16 KB | Mean cut | p50 time |
|---|---|---|---|---|
| off (today's head cut) | 26/59 | 6/59 | 52% | 0.2 ms |
| evidence (new, deterministic) | 58/59 | 59/59 | 79% | ~55 ms |
| trim-1 conservative / balanced / aggressive, alone | 59/59 | 6 / 13 / 33 of 59 | 6% / 16% / 49% | ~50 ms |
| trim-1 balanced, then evidence | 58/59 | 59/59 | 78% | ~100 ms |

  - At 4 KB: head cut 17/59, evidence 57/59.
  - Worst cases:
    - **evidence:** one 180 KB single line, which no verbatim selector can keep, and one paraphrase at 4 KB;
    - **head cut:** every error in the middle.
- **Decision (first pass):** both stay **off**; later superseded, see "Context selection is now ON" below.
  - The evidence selector looks clearly better than the head cut, but only on fixtures it was tuned on.
  - The independent held-out set was never written: its author was stopped when testing on Linux was paused.
  - trim-1 adds no measured benefit here: it keeps almost everything, and only the budget step cuts.
  - Next: a held-out test on real Neko tool output on the laptop.

### Fixes (file, regression test)

**Kernel (`jarvis/kernel`):**
- `nodes.pair`: 10 simultaneous pairings with one code all succeeded. Now atomic. (`test_nodes`)
- `owner` approve: 30 parallel PIN guesses were all checked. Checks in flight now count against the limit. (`test_owner`)
- `owner` pc: the kill switch gave a 500; now an honest 503. (`test_owner`)
- `store`: one damaged approval row broke the owner's lists; now shown as unreadable. (`test_kernel`)
- `memory.forget`: forgotten text survived in the database's journal file (WAL); now erased (secure_delete + checkpoint). (`test_memory`)
- `llm_proxy`: the deep scan ran per message (21 s for 40 messages). Now once per request, and old messages aren't rescanned. (`test_llm_proxy`, incl. DeepScanCache)
- `mcpwire`: the truncation count was off when scrubbing changed the length. (`test_context_select`)

**Desktop (`plugin/plugins/desktop`):**
- `coordinator`: retried typing and clicks after a "timeout" (double typing). Now only repeat-safe steps retry. (`test_coordinator`)
- `coordinator`: empty-lock race let 3 of 8 threads "win". (`test_core`)
- `coordinator`: a stop during an approval wait wasn't reported as stopped. (`test_coordinator`)
- `coordinator.summary`:
  - "could not finish" on a requested stop;
  - "I will continue once approved" (it doesn't);
  - "Done" while part of the request was dropped. (`test_coordinator`)
- `missions`: every browser step was treated as safe to repeat on resume. (`test_missions`)
- `decide`:
  - typing after "open X and write Y" (H19's case);
  - two accounts named → ask;
  - negations and corrections;
  - built-in sites can't be shadowed;
  - the steps limit is reported. (`test_decide_rules`)
- `skills`: shadowing via ".com" names, private hosts, prompt-line injection, promotion gated against an older version. (`test_skills`)

**Other plugins and the node:**
- `main_agent.planner`: unknown entries were treated as read-only. Now fail closed. (`test_planner`)
- `jarvis_node/browser.py`:
  - text hidden by opacity, off-page position, zero size, transparent colour or clipping reached the model;
  - `open()` hid HTTP error pages. (`test_browser`)
- `research`:
  - inline-hidden text;
  - a fetch dripped byte by byte had no total deadline;
  - "5" matched inside "15";
  - **sources disagreeing on a figure** were both shown as verified;
  - "Verified" shown without the checker. (`research/tests`)
- `deliverables`:
  - deck builder: text measured at a fixed tiny size without the theme font;
  - a stats slide with more than 4 items was silently cut;
  - missing Edge gave an install hint instead of a plain message. (`deliverables/tests`)

### Test matrix (final run on this server)

| Suite | Passed | Failed | Skipped / deferred |
|---|---|---|---|
| Kernel (unittest) | 513 OK | 0 | 16 skipped, all Windows-only (6 collector, 3 repairs, 2 UI Automation, 3 winbackend, 2 Win32 desktop) |
| Plugin tests (10 plugins, separately) | 600 | 0 | 1 (lifekit) |
| Wake listener (offline) | 16 | 0 | |
| Plugin framework (unit + integration, 2 workers) | 4282 | 15 | 5 skipped |
| Upstream N.E.K.O tests/unit (earlier run, same code base) | 41,978 | 5 | 176 skipped |
| Benchmark v2 | 546 | 1 (J-038) | 25 deferred |
| Live: Dahl routing / research / fake profiles | 12 / 5 / 12 | 0 | |
| Live: planning with a stand-in model (exploratory) | 97 | 52 | |

**What the 15 framework failures are:**
- **8 are the plugin metadata snapshots:** laptop rebuild; `jarvis/scripts/check_plugin_metadata.py` names each cause.
- **7 in `test_plugin_layout_migration.py`:**
  - they fail identically on the committed code with 2 workers, and pass alone (35/35) or with 5 workers;
  - so they come from how upstream's tests share state across workers, not from these changes.

**What the 5 upstream failures are (all explained):**
- The metadata snapshots (`test_builtin_plugin_packaged_metadata`).
- cloudsave read-only tree (POSIX permissions).
- Knowledge admission: `knowledge/service.py:1017` marks a job "building" before it waits for the write lock, and the test races that.
- Voice enrollment: `remaining_seconds` differs by microseconds; Windows' 15.6 ms clock hides it.
- `test_visit_runtime_finalize` (timing-flaky, 1 of 3 runs alone).

### Measurements

- Kernel suite: 444 s on 2 capped cores.
- Policy and benchmark cases: the speed cases (`dims: speed`) passed, except J-038.
- **J-038:** 20 concurrent fresh calls through the proxy, p95 20.4 s on 2 cores and 5.9 s on 6. Without the deep scan: 1.1 s and 0.09 s.
- **J-018 (critical, limit 500 ms) is intermittent:** it failed 1 of 5 runs at 508 ms. One gitleaks run needs 450–500 ms just to start (rule compilation), whatever the flags. A routing score of 9.82 is therefore one unlucky run away from the 6.0 cap.
  - The cost is gitleaks' start-up (~0.5 s CPU per new text).
  - The cache removes it for re-sent conversations, not for 20 brand-new texts.
- Context selection: evidence about 55 ms on 200 KB, trim-1 about 50 ms per call.
- Wake state machine: under 5 ms per 40 ms frame.
- Dahl: p50 4.1 s per short call, first chunk 1.0 s. Stand-in planning: p50 3.75 s, about 1,000 tokens per call. Dahl tokens: 221,795 recorded for the two complete planning runs; the stopped first run, the 5 research runs and the routing checks were not counted (an estimate, not a measurement: well under 0.3M more).

### Risks

- **Critical:**
  - The approval PIN was typed in chat earlier: change it.
  - The Dahl key was pasted in chat: revoke it after the build, as planned.
- **High:**
  - 8 plugin snapshots are stale: the discovery list shows **0 entries** for desktop, research, deliverables, main_agent, jarvis_bridge and the rest until each plugin starts.
  - Research: 72% of cited sentences confirmed live (was ~40%); the checker accepts about 2 in 18 false claims on the labelled set.
  - Plan latency is about 5 s on the laptop GPU.
- **Medium:**
  - The deep secret scan costs ~0.5 s per new text, and more under load (J-038; J-018 intermittent).
  - The context selectors are measured only on synthetic fixtures.
  - The benchmark was written by the same team that fixed the code.
- **Low:**
  - 4 Linux-only upstream test quirks.
  - trim-1 is a 2-day-old project, pinned and off.

### Continued: proxy bursts and the research claim checker (later the same evening)

- **J-038 fixed (20 concurrent agent calls, p95 20.5 s → about 1 s; 5/5 runs pass).** Two causes, both measured:
  - the production `llm_proxy.serve()` and the node endpoint used Python's default listen queue of 5, so extra connections were refused and retried by TCP a second later;
  - every request paid its own gitleaks start-up (~0.5 s). Requests arriving together now share one scan ("group commit"); a lone request is never delayed.
  - Regression tests: `ServerQueue`, `DeepScanBatch`.
- **Full benchmark after the fixes:** 547 passed, 0 failed, 25 deferred. Mean of scored dimensions 9.75; routing and reliability now 10.
  - J-018 (critical, 500 ms) is still intermittent: one gitleaks start-up is 450–500 ms. A keyword prefilter was checked and rejected: lossless, but real prompts nearly always contain gitleaks keywords ("ey", "s.", "sk"), so it would only speed up tests.
- **Research claim checker:**
  - The small NLI model rejected plain support inside longer passages: 0.21 for a sentence the passage states word for word, 0.99 on that sentence alone.
  - It now also checks sentence windows (reference markers removed). A window must reach 0.95, and the numbers must appear in that window.
  - On a labelled set written before measuring (`research/tests/fixtures/nli_pairs.json`, 36 pairs): true claims confirmed 13/18 → 18/18, false claims accepted 1/18 → 2/18.
  - The larger models (small, base, sha256 checked) did no better on the remaining false positive ("largest city" when the passage says "largest inland city"), so the 87 MB model stays.
  - **Live, 5 public questions through Dahl:** confirmed share 38–42% (two earlier runs) → **72%** (60/83). Still 5/5 correct with no fabricated sources; different runs and search results, so indicative.
  - Citation repair (re-cite to the source that does support a sentence) was added too; it changed 1 citation in 89 live, so wrong citations are not the main problem.

- **Held-out context cases** (`bench/gen_holdout_dahl.py`): Dahl's GLM model wrote the cases (question, answer, critical lines, the surrounding block) without seeing the selector; MiniMax spent its whole token allowance reasoning and wrote nothing.
  - Only **6 of 45** cases were written: Dahl then answered HTTP 429 "model_concurrency" for both models. Paid accounts are admitted first; the pool is never topped up, so that is the owner's decision.
  - On those 6 (far too few to decide anything):
    - head cut kept the answer in 2/6 (4 KB) and 3/6 (16 KB);
    - evidence 6/6 (critical lines 5/6), always within budget;
    - trim-1 alone 6/6, within budget 0–1/6.
  - Both stay **off**: the next step is more held-out cases when Dahl has capacity, or real tool output on the laptop.

- **Upstream test isolation (plugin framework, 2 workers):**
  - The 7 `test_plugin_layout_migration` failures came from `importlib.reload(plugin.settings)` in `test_zmq_transport_security.py` and `test_plugins_lifecycle_service.py`.
    - A reload replaces the module's classes, so the migration's `except PluginExecStateRootCollisionError` no longer caught the error raised later in the same process.
    - Those 3 tests now evaluate a private copy of `settings.py` and check the same values.
    - Both culprit pairs now pass, and the 2-worker run is down to the 8 metadata failures (4289 passed).
  - `test_bootstrap_failure_does_not_lock_root_initialised` (upstream) fails alone in 10 of 12 random orders:
    - after any test that already set up the PluginServer logger, `_ensure_root_logger` correctly treats logging as set up;
    - the test assumes a fresh process, and the repo's pinned seed happens to pass. Documented, not changed.
- **H-027** (the real claim checker on the 36 labelled pairs) now runs wherever the model is present instead of being deferred. Area H: 36/36 with the model; without it, H-027 errors and never passes.

- **Context selection is now ON (`evidence`) by default** (D29 update), based on the full held-out set (45 cases written by Dahl's GLM, 15 more refused while Dahl was busy):

| Mode | Answer kept @4 KB | @16 KB | Fits budget |
|---|---|---|---|
| head cut (old default) | 15/45 | 18/45 | 0/45 |
| evidence (new default) | 44/45 | 44/45 | 45/45 |
| trim-1 then evidence | 44/45 | 45/45 | 45/45 |
| trim-1 alone, balanced | 45/45 | 45/45 | 2–12/45 |

  - Evidence never lost an answer the head cut kept (0 vs 26–29).
  - Opt-out: `JARVIS_CONTEXT_SELECT=off`.
  - Known miss: an nginx access-log line whose only sign of failure is "502".
  - Found by the kernel suite right after the switch, and fixed before committing: one regex was quadratic on a very long line (8.6 s at 32,000 characters, hours for a megabyte), so a hostile tool could stall the kernel. Lines are now classified on their start and end, and the pattern is bounded; 1 MB lines now take well under 5 s (test `HostileInput`). Kernel: 517 OK, 16 Windows-only skips.

- **Access logs:** a 4xx/5xx status after the quoted request (`" 502 559`), or `"status": 503` in JSON logs, now counts as evidence (test `AccessLogs`).
  - The first held-out set fed that rule, so it is now development data (45/45 there).
- **Second held-out batch**, generated before the rule changed, in its own file `context_fixtures_holdout2.json`. Dahl wrote 34 of 60; 26 were refused while busy.

| Mode | Answer kept @4 KB | @16 KB | Fits budget |
|---|---|---|---|
| head cut | 12/34 | 16/34 | 0/34 |
| evidence (default) | 33/34 | 33/34 | 34/34 |

  - The default stays on.
- **Writer prompt A/B (rejected):** an extra rule ("no conclusions or attributions the passage does not state"), 2 rounds × 5 questions per prompt, alternating.
  - Confirmed share: 77.5% vs 75.5% for the old prompt.
  - But only **7/10 correct answers vs 10/10**, and shorter reports. The old prompt stays.
  - What remains unconfirmed is mostly the writer's own embellishments (correctly unconfirmed), plus some paraphrases at 0.7–0.9 that stay below the strict 0.95 window threshold by design.

## What blocks 9.5 (owner actions)

1. **Real voice (C1):**
   - run `jarvis/tests/neko_voice_owner_record.py` (type YES; 10 × "Hey Neko" at 0.5 m and at 1.5 m; the audio stays on D:);
   - then `jarvis/wake/eval_owner.py` tunes on your real voice;
   - barge-in and echo cancellation need these recordings.
2. **Unattended browser connection (C5):** per profile, copy the token from the Playwright MCP extension, then run `jarvis/scripts/save_playwright_token.ps1 -Profile "<folder>"`. The token is never shown, and the clipboard is cleared.
3. **Skill promotion (C13):** only you can promote a proposal: `python -m plugin.plugins.desktop.skills promote vN` (none is pending that passed).
4. **Change the PIN** you typed in chat earlier, and restart Chrome once (the extension token was regenerated earlier today).
5. **Plugin metadata snapshots:** rebuild them for the 8 plugins named in "Round 2" above.
6. **Re-run `jarvis/tests/neko_safety_live.py`** (C14). On the Linux server (16 Oct), gitleaks with this repo's `.gitleaksignore` found 0 in this project's commits, `jarvis/`, `plugin/` and the neko95 results.
   - The saved 18:06 row still counts upstream's history (533) and 2 findings in `D:\jarvis\N.E.K.O-data\logs\main.err.log`. Only the laptop can check those logs.
   - The same run re-checks S-no-false-completion; the typing read-back fix is in.
7. **C8 research:** the 17:24 run found nothing (search unavailable), and at 18:05 the checker supported 12 of 24 cited sentences. Re-run with the search service up; it needs the laptop's local models.
8. **C9 perception:** PE-vision-dialog, PE-vision-grounding and PE-observation-vs-inference have never been run into a saved file.
9. **Revoke the Dahl key** pasted in chat on 16 Oct once the build is done (Dahl account page → API keys). The test server's copy is `/srv/jarvis/home/.jarvis-secrets/` (owner-only); delete that folder too.
10. **Linux server, only if you want a Windows VM there:** your relative enables **VT-x/VT-d** in the HP BIOS (F10 → Advanced → System Options) at a time that suits him; any new partition needs an admin with a backup first.

### Laptop-return checklist (Windows, PowerShell in `D:\jarvis\hq-neko`, N.E.K.O stopped first)

```powershell
# 0. bring the commits over (the server repo is a private copy; push stays disabled), then:
git log --oneline -8
# 1. security
python -m jarvis_kernel set-approval-pin            # new PIN (the old one was typed in chat); kernel console
# revoke the Dahl test key on https://inference.dahl.global/account
# 2. plugin metadata (moves git-ignored .vscode folders to D:\jarvis\backup, never deletes)
powershell -ExecutionPolicy Bypass -File jarvis\scripts\rebuild-plugin-metadata.ps1
D:\jarvis\tools\neko-venv\Scripts\python.exe jarvis\scripts\check_plugin_metadata.py     # expect "0 refused"
git add plugin/plugins/*/plugin.meta.json; git commit -m "Rebuild plugin metadata on Windows"
# 3. Chrome profiles: per profile, copy the Playwright MCP extension token, then
powershell -ExecutionPolicy Bypass -File jarvis\scripts\save_playwright_token.ps1 -Profile "Default"   # repeat per profile
# 4. voice (owner hardware): record, then tune
D:\jarvis\tools\neko-venv\Scripts\python.exe jarvis\tests\neko_voice_owner_record.py
D:\jarvis\tools\neko-venv\Scripts\python.exe jarvis\wake\eval_owner.py
# 5. Windows-only tests: the 16 kernel skips and the desktop/plugin suites
cd jarvis\kernel; D:\jarvis\tools\neko-venv\Scripts\python.exe -B -m unittest discover -s tests; cd ..\..
D:\jarvis\tools\neko-venv\Scripts\python.exe -m pytest plugin\plugins\desktop\tests -q -p no:cacheprovider
# 6. live re-runs that feed the v1 scorecard, then score
D:\jarvis\tools\neko-venv\Scripts\python.exe jarvis\tests\neko_safety_live.py
D:\jarvis\tools\neko-venv\Scripts\python.exe jarvis\evals\neko95\run_plans.py --repeat 5
D:\jarvis\tools\neko-venv\Scripts\python.exe jarvis\evals\neko95\score.py --label after-linux-session
# 7. benchmark v2 on Windows (runs the same 572 cases; Windows-only ones are still listed as deferred)
D:\jarvis\tools\neko-venv\Scripts\python.exe jarvis\evals\neko95\bench\bench.py
D:\jarvis\tools\neko-venv\Scripts\python.exe jarvis\evals\neko95\bench\scorecard.py
```

## Mission 2026-10-17: model discovery, Benchmark Console, research reliability, 288 new cases (owner travelling; D30)

All work ran on the Linux server in the rootless test container: headless, nice 19, CPU-capped. It was committed
locally and never pushed.

### What changed (commits after d3002d74)
| Commit | What |
|---|---|
| 8b3d146c | Connector checks worked only on Windows. On Linux/macOS every stdio add crashed with a 500, and `/bin/sh` or `python3.11 -c` were accepted. Fixed. |
| c49165cf | Benchmark Console: the `/api/bench/*` routes and a Bench screen (Dashboard, Run, Challenge, Models, Security) |
| 30868626 | Claim checker guards for units, dropped qualifiers and prompt injection; `claims_eval.py`; claims_v2 and VitaminC sets |
| 3c903413 | `jarvis/local_models/test_server_flags.py`: every llama-server stays a plain loopback model server |
| 13e48d59 | `model_compare.py` and 12 pinned candidates; results |
| a853ab8e, 874bd85f, f801bbfe | 288 new benchmark cases (572 → 860) |
| a3ee6647, 175416f5, cfc65bf9, f801bbfe, 8e29d3e7, 4e32e583 | About 60 product bugs that the new cases found. Each has a test that fails on the old code. |

The most important fixes:
- **local_team.** `adviser_allowed` checked the gateway address as text, so `http://evil.example/127.0.0.1:48950/v1` was accepted and the task and its password were sent there.
- **Skill gate.** A suite that ran 0 cases passed the gate. A damaged registry let a new proposal overwrite `v1`, and concurrent proposals lost versions.
- **Keys.** The privacy gateway relayed a fallback's error page that echoed its key. A key file that disappeared mid-request dropped the connection instead of falling back to the next provider.
- **Kill switch.** The lab runner still got jobs while the kill switch was on.
- **Lab tasks.** Crashed, expired or cancelled lab jobs left their task "running".
- **Reported success.** `{"ok": "false"}` counted as success.
- **Stuck missions.** A planner crash left a mission "queued" forever.
- **Automations.** A cancelled job blocked every later run. A file arriving during a run was skipped although the preview promised one run per file.
- **Decider (decide.py).** It acted on vague, negated, "X or Y" and corrected requests; it now asks instead.
- **Crashes on bad input.** Diagnosis, the planner, the coordinator, node repair, kernel memory, deliverables and the vault crashed on malformed input.

### Final run (2026-10-17, this server, after all commits)
- **Benchmark v2: 860 cases.** 833 pass, 1 fail (H-042), 0 errors, 26 deferred (Windows or owner hardware). Result file: `results/bench-20261017-102655.json`.
- **Scorecard v2: mean 9.69** (`results/scorecard-v2-20261017-102656.json`). Yesterday it was 9.75, on 572 easier cases.
  - research 8 (H-042), desktop 9, security 9 and voice 9 (critical cases deferred); every other dimension 10.
  - The official v1 score stays 7.61 until the live Windows re-runs.
- **Kernel:** 538 tests, OK (16 skipped: Windows).
- **Console:** 93 tests, OK (3 skipped).
- **UI:** typecheck OK, vitest 5/5, Playwright phone e2e 5/5 (360x740, axe).
- **Plugin suites:**
  - deliverables 36, desktop 115, doc_tools 9, jarvis_bridge 20, learn_vault 18, lifekit 319;
  - local_team 31, main_agent 73, research 36 (NLI model mounted), wechat 12;
  - local_models 9, wake 17.
- **plugin/tests:** 4289 passed, 8 failed. The 8 are the known plugin metadata snapshots, which are rebuilt on the laptop.
- **tests/unit:** 41981 passed, 4 failed. All 4 were already failing yesterday, when there were 5.
- **Claim checker** (`results/claims-*-20261017-1019*.json`):

| Set | False acceptance | Recall | Precision |
|---|---|---|---|
| claims_v2 | 2/46 (was 9/46) | 0.938 | 0.938 |
| nli_pairs | 1/18 (was 2/18) | 18/18 | — |
| VitaminC held-out | 10/200 (was 11/200) | 0.44 | 0.898 |

### Remaining bugs and limits (known, not hidden)
- **Research checker:**
  - H-042: a past figure is confirmed as current ("had 2 million in 1990 … has since fallen").
  - claims_v2 still accepts a partly wrong claim ("opened in 1887") and a number attached to the wrong subject.
  - VitaminC recall is 0.44. The checker errs towards "unconfirmed", which is the safe direction but loses true claims.
- **Timing:** J-018 (500 ms proxy relay) and two kernel race tests pass on a quiet machine but are flaky at 1 CPU under load.
- **Privacy:** the 11 privacy redaction tests need presidio, which is not in the test image. Not verified here.
- **Windows code not run here:** the `msvcrt` branch of the new skills file lock.
- **Model comparison:** LFM2.5-2.6B and LFM2.5-8B-A1B often answered outside the JSON grammar. Not investigated, since they are below the incumbent either way.
- **Decider behaviour for the owner to confirm:** an excluded account, a negated action and an "X or Y" choice now ASK instead of acting.

### Laptop checklist additions (Windows, after bringing the commits over)
```
# Benchmark Console: start the console as usual (jarvis\scripts\start-console.ps1), open http://127.0.0.1:8799, Bench tab.
# No new port: it is a route of the existing loopback console.
python -m pytest jarvis\local_models -q                      # server flag guard (9)
python -m pytest plugin\plugins\desktop\tests -q             # includes the Windows msvcrt skills lock
python jarvis\evals\neko95\bench\bench.py                    # 860 cases; Windows-only cases then run instead of deferring
# decider on the real GPU (RSS is not measured on Windows):
$env:NEKO_LLAMA_SERVER="D:\jarvis\tools\llama.cpp\b11669\vulkan\llama-server.exe"; $env:NEKO_MODEL_DIR="D:\jarvis\models\gguf"
python jarvis\evals\neko95\bench\model_compare.py --models qwen3-4b-2507,qwen3-1.7b
# owner hardware: G-036 (44.1 kHz-only microphone), G-037 (pause with the real microphone)
```
Research reports with links: `evidence/07-small-models-2026-10-17.md` (ranked shortlist and compatibility),
`evidence/08-runtimes-agents-infra-2026-10-17.md`, `evidence/09-research-reliability-2026-10-17.md`.
