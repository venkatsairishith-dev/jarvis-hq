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
