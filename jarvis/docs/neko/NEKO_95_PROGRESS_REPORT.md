# Neko 9.5: progress report (2026-10-16, Windows laptop + hermes-agent-vm)

Scores come only from saved results, through `jarvis/evals/neko95/score.py` under the frozen rules v1. Score files are in `jarvis/evals/neko95/results/score-*.json`.

**Not at 9.5:**
- Several criticals still need the owner in person (real voice, Chrome tokens).
- Two latency targets are missed on this GPU.
- See "What blocks 9.5" below.

## Scorecard over the day

| Capability | Baseline 15:06 | Interim 16:19 | After round 3 |
|---|---|---|---|
| C1 voice | 5.0 | 5.0 | _see final_ |
| C2 conversation | 0.0 | 0.0 → 5/5 cases pass 3/3 (16:20) | |
| C3 command understanding | 5.0 | 8.5 (SLO missed) | |
| C4 Windows control | 5.0 | 5.0 | |
| C5 browser | 6.0 | 6.0 | |
| C6 long missions | 0.0 | 6.0 | |
| C7 troubleshooting | 5.0 | 5.0 | |
| C8 research / documents | 0.0 | 0.0 | |
| C9 perception | 5.0 | 5.0 | |
| C10 proactivity | 0.0 | 10.0 | |
| C11 performance | 0.0 | 0.0 | |
| C12 orchestration | 6.0 | 6.0 | |
| C13 self-improvement | 0.0 | 5.0 | |
| C14 safety | 5.0 | 5.0 | |
| **Mean** | **3.0** | **4.75** | |

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

## What blocks 9.5 (owner actions)

1. **Real voice (C1):**
   - run `jarvis/tests/neko_voice_owner_record.py` (type YES; 10 × "Hey Neko" at 0.5 m and at 1.5 m; the audio stays on D:);
   - then `jarvis/wake/eval_owner.py` tunes on your real voice;
   - barge-in and echo cancellation need these recordings.
2. **Unattended browser connection (C5):** per profile, copy the token from the Playwright MCP extension, then run `jarvis/scripts/save_playwright_token.ps1 -Profile "<folder>"`. The token is never shown, and the clipboard is cleared.
3. **Skill promotion (C13):** only you can promote a proposal: `python -m plugin.plugins.desktop.skills promote vN` (none is pending that passed).
4. **Change the PIN** you typed in chat earlier, and restart Chrome once (the extension token was regenerated earlier today).
