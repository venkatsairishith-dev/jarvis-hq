# JARVIS reliability: where each thing stands against 90%

Written 2026-10-26 22:54. A bar fills to the measured pass rate; the : is the 90% line. 'not measured yet' means no run has been made: it is not a good or a bad number.

**0 of 15 capabilities are at 90% or better** with zero unsafe actions.

| capability | my guess before | measured | | status | runs | from |
|---|---:|---:|---|---|---:|---|
| Open, type, save and click | 75% | not measured yet | `[....................]` | - | 0 | - |
| Reading the screen (text) | 80% | not measured yet | `[....................]` | - | 0 | - |
| Reading pictures on the screen (OCR / vision) | 50% | not measured yet | `[....................]` | - | 0 | - |
| The final answer arrives | 80% | not measured yet | `[....................]` | - | 0 | - |
| Delete, restore and safe cleanup | 85% | not measured yet | `[....................]` | - | 0 | - |
| Cleaner panel | 85% | not measured yet | `[....................]` | - | 0 | - |
| Generated decks, reports and the Preview panel | 80% | not measured yet | `[....................]` | - | 0 | - |
| Window responsiveness | 60% | 100% | `[##################:##]` | incomplete | 30 | reliability-2026-10-26.json |
| Telugu and Hindi in their own scripts | 60% | not measured yet | `[....................]` | - | 0 | - |
| Tenglish and Hinglish (Telugu / Hindi in English letters) | 40% | not measured yet | `[....................]` | - | 0 | - |
| The local MoE model as the only brain | 30% | not measured yet | `[....................]` | - | 0 | - |
| Ambiguous requests are asked about, not guessed | - | not measured yet | `[....................]` | - | 0 | - |
| Class-C requests stop for the PIN | - | not measured yet | `[....................]` | - | 0 | - |
| Simple everyday tasks (catalogue) | - | not measured yet | `[....................]` | - | 0 | - |
| Solving a problem shown on the screen | - | not measured yet | `[....................]` | - | 0 | - |

## What is holding each one back

- **Window responsiveness**: 0 of 10 goals have 5+ valid runs. Top failure causes: 0 of 10 goals have 5+ valid runs.

## Not measured yet

Open, type, save and click, Reading the screen (text), Reading pictures on the screen (OCR / vision), The final answer arrives, Delete, restore and safe cleanup, Cleaner panel, Generated decks, reports and the Preview panel, Telugu and Hindi in their own scripts, Tenglish and Hinglish (Telugu / Hindi in English letters), The local MoE model as the only brain, Ambiguous requests are asked about, not guessed, Class-C requests stop for the PIN, Simple everyday tasks (catalogue), Solving a problem shown on the screen.

---

# Laptop progress (appended after each phase; the reliability report may rewrite the top of this file, these lines are re-appended)

## Phase 0 (2026-10-26, merge 0c559ab and rebuild): DONE
- Merged origin/claude/implementation-plan-designs-00v4r6 (0c559ab2c) into jarvis/laptop; the only conflict was the face persona (resolved to the cloud's compact text; the recent_files and exact deck-name lines now live in jarvis/persona/jarvis_card.json, the source).
- send2trash 2.1.0 installed in agent-venv and pinned in tools/requirements/agent-venv.txt. Tests: kernel 1577 passed; bridge 220 passed + 3 known Windows symlink failures; node 285; persona 35; tools, supervisor, plugins/_shared, plugins/desktop pass; privacy 102 (2 new pytest-style modules run in agent-venv: 29 passed).
- Fixed on the way: test_savefile.py f-string backslash (Python 3.11 refused to import it, so 'node' tests could not run), test_privacy stand-in missing the gateway's new `names` argument (2 false 503s).
- Face rebuilt and reinstalled (D:\APPS\jarvis, 18:30); gateway 48950/48954, tool server 48970, governor, llama-swap 8080, speech, watch runner restarted with jarvis-stop/start; face started once (saved-profile migration runs).

## Phase 1 (2026-10-26, on-screen smoke on the normal chain, Windows 11): DONE except what needs the server kernel
- On screen (real goals typed in the face): Calculator + Paris weather, Notepad write, what-is-on-screen, 3-slide deck, PC cleaner panel (scan 4.6 GB safe + review rows, matches the folders: TEMP 954 vs 1063 MB, bin 277 vs 264 MiB, no OneDrive redirect), jarvis_ask form (after the PII fix), Timeline panel (59 steps), YouTube play with JARVIS clicking the extension approval itself (21 s end to end).
- Verified live on this laptop through the node jobs (the kernel is not needed for these): Save As in Win11 Notepad (file, title, content ok; second save -> "(2)"; Replace prompt answered No, file unchanged), VS Code launch (needs ELECTRON_RUN_AS_NODE removed; new file via the Command Palette; paste into Monaco; Save As; disk read-back ok), Recycle Bin trash + restore ($I layout fine), WM_CLOSE (unsaved window refused, clean window closed), Paint drag, win+d twice, one-window screenshot (0.9 to 1.0 s each, 13 to 32 KB, timing.jsonl).
- Fixed from those runs: Save As box/buttons have no UIA patterns (WM_SETTEXT, posted BM_CLICK, CCPushButton), VS Code (4 causes), the Playwright connect page ("Allow & select", only the row of this launch's relay port; auto_click.json + tests), the PII model marking app names, do_goal giving the decider the owner's own words for one-step plans. The page also prints a connection token: it was shown once in this session's tool output while reading the page; rotate it with "Generate new token" on that page if you want.
- NOT done / needs the owner: (1) deploy the new kernel to hermes-agent-vm (the old one refuses `ui save_as`, delete, undo, cleaner apply, ...): write+save, delete+undo and the cleaner's Clean button cannot be run end to end until then; (2) settings scripts (theme, DND, brightness, wallpaper, Radios) were NOT run: they change system settings; (3) the screenshot per step costs 2 x about 1 s in PowerShell (see Phase 2).

## Phase 5 (2026-10-26, OmniRoute, read-only): DONE
- `bash -n deploy/omniroute-upgrade.sh` OK. The VM is already on 3.8.52 (upgraded earlier under the standing approval; backup ~/jarvis/omniroute-backup-1792958523). Fixed the script for that state: the backup now includes data/ (storage.sqlite, migrated in place by 3.8.52) and restores it, the "public" check no longer curls the VM's own address (always true through lo) but checks that only tailscale0 accepts the port and the chain ends in REJECT, disk need 6 GB, a note when already at the wanted version.
- Dry run on the VM would inspect: service state, installed vs npm latest version, MemoryMax/current, free RAM and /home disk, listening sockets and iptables rules for 20128, backup size. It changes nothing without --apply.
- `omniroute` provider (enabled false) is in privacy/providers.json. The owner enters the Dahl key in the OmniRoute dashboard himself (Tailscale: http://100.125.91.51:20128/dashboard, management password), then creates an API key there and saves it as D:\jarvis-secrets\providers\omniroute.key.

## Phase 2 (2026-10-26, speed, D60 procedure, Dahl GLM-5.3-Flash pinned, fresh face per run, 5 goals x 5): DONE, targets met in total, not for tool goals
- Before (D60 switches off) -> after (defaults), end to end `done`: p50 22.9 s -> 13.2 s (target 15), p90 61.7 s -> 28.6 s (target 30); model rounds per turn p50 2 -> 1; per goal p50: Notepad write 97.6 -> 13.5 s (rules, no hosted model), open calculator 36 s (5 of 5 errored) -> 7.5 s (5 of 5 done), what is on screen 43.5 -> 16.5 s, weather 18 -> 34.5 s (worse), joke 18 -> 22.5 s (worse; Dahl variance). Files: results/latency-{before,after,compare}-2026-10-26.*.
- Not met: first token p50 5.5 s (target 2 s) with a FRESH face per run (the first message pays the tool-list read); in one running session turn 1 and 2 had first token 2.1 and 3.4 s. Dahl rounds: gateway first_ms p50 1.2 s but total_ms p50 8.0 s (p90 22.8 s): GLM's hidden thinking; tool goals that need a model decision stay about 22 s (done_with_tools p50 22.7 s). gateway redact_ms p50 351 -> 275 ms (cache hits 51 of 52 strings), queue_ms 0.
- One change: pin `dahl deepseek-ai/DeepSeek-V4-Flash-0731` (3 x weather, screen, joke): first_ms p50 2.3 s (GLM 1.2 s), total_ms p50 7.3 s (8.0), done p50 10.1 s but p90 82 s (weather 82.6 / 79.6 / 9 s, screen 10.5 / 97.6 / 40.5 s): not better, NOT adopted; GLM stays first.
- Bug found and fixed by measuring: the quick-command answer is wrapped {ok, result} by the tool server and the face read the wrong level, so every plain command went to a model (fixed, test added). The screenshot per step costs about 0.9 to 1.0 s each (PowerShell), 13 to 32 KB.

## Phase 3 (2026-10-26, responsiveness, D57 trace procedure on the laptop): DONE, one real bug found and fixed
- Found: the avatar window opened OUTSIDE every display (saved bounds x -2379, y -3140, 1536 x 920; the app re-saved them at every start): the owner saw no avatar. Root of the saved value not found (no setBounds on that window was seen at start); fixed at the use: saved bounds that no display shows or that cover a whole display are dropped and never saved again (usableSavedBounds, 4 tests). After the fix the window opens at 543,160 (450 x 600).
- Measured with raw DevTools input (6 restarts, 5 with a local model loaded, 1 without; the harness's Playwright probe said 24 s for "first click" and was wrong: connectOverCDP itself returned after 20 s): window visible 0.9 s after launch, first accepted click 0.9 to 1.0 s (about 0.25 s of it is the launch call), 6 long tasks of 0.84 to 1.0 s in total (longest 240 to 350 ms, Live2D shader set-up), then input p50 6 to 8 ms, p90 13 ms, max 84 to 104 ms over 30 s, with and without the model. Harness (fixed probe, fresh, 3 repeats each): input p90 14.7 ms, worst 25 ms under idle, local model generating, online stream, PC task, document read; 30 of 30 valid runs pass.
- Processes: main process idle (busy 438 ms in the first second, then under 80 ms a second); avatar renderer about 20 to 30% of a core (rAF 60 per second while idle: the 30 fps idle cap is not visible there), GPU process about 40% of a core, steady. Priority: face (17 processes), gateway, tool server, console, node agent, speech are Normal with EcoQoS off (governor log `interactive`); llama-server, llama-swap, muse runner, verdict and watchdog are BelowNormal with EcoQoS (D56 as designed).
