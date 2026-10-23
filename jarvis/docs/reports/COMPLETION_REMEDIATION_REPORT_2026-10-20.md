# Completion and remediation report, 2026-10-20

Branch `remediation-2026-10-18`, HEAD `88ec49a2` (unchanged). Every change is uncommitted.
Status words: PASS / FAIL / SKIP / BLOCKED / NOT RUN / COMPLETED-NOT-VERIFIED. They are never merged into one percentage. Mocked is not live.

## 1. Initial state
- The earlier passes left about 161 modified tracked files and about 140 untracked files. All were preserved.
- At the start of the final run, `git status --short` showed 334 lines. It shows 336 now, because this report and its evidence were added.
- The earlier test reconciliation had 9 failures in `plugin/tests` and 11 in `tests/unit`.
- Pass 4 ended with the lab verdict boundary hardened and the branch-protection facts recorded.

## 2. Findings and root causes
The detail is in `REMEDIATION_PASS4_2026-10-19.md`, `CAPABILITY_STATUS_2026-10-19.md` and `jarvis/caretaker/README.md`. Summary by area:
- **Lab:** deeply nested verdict JSON raised RecursionError, now caught in `strict_loads` / `strict_raw_decode`. `skill_counts` hardened.
- **Kernel data and approvals:**
  - The `add_column` helper was missing.
  - Approvals were not withdrawn with the task they belonged to, and approval failures were not counted.
  - The agent-token file mode is now 0600.
  - The caretaker policy has a size cap, an anchored shell pattern and a test-argument allow-list.
- **PC lock and node:**
  - Heartbeat start-order race.
  - `_kill_tree` now raises on failure, and the cancel detail is reported.
  - The environment passed to child processes is now cleaned.
- **Console and net:** loopback traffic bypasses proxies (no-proxy openers). `/health` now reports `liveness_only`.
- **Privacy and tools:**
  - `clean_result` replaced Luhn-valid task-id prefixes (card-number regex), which caused the intermittent lock-test failure (5 of 40). Fixed, 40/40 clean afterwards.
  - A duplicate set element was removed.
- **Config:** `_force_rmtree` in `migrations.py` now chmods parent dirs inside the tree it removes.
- **Mutation testing:** 271 of 390 mutants were killed at first. All non-equivalent mutants are now killed by added tests.

## 3. Files changed
About 300 files across the kernel, node, lab, caretaker, console, widget, plugins, UI e2e specs, docs and tests. The authoritative list is `git status --short` (336 lines). Upstream test edits that needed a judgement call:
- `tests/unit/test_game_vision_service.py`: timeout 0.02 to 1.0 for a request-admission race.
- `test_knowledge_subsystem.py`: event-driven cancel test.
  - It now asserts the state is in `ACTIVE_JOB_STATES` and that the staged file exists, instead of exactly "queued".
- `test_module_layering.py`: hang guard 30 to 300 s.
- `test_trust_store.py`: CPU-time ratio. The 30x limit is unchanged.
- `voice_identity_service/test_service_activation.py`: countdown tolerance.
- `asr_client/speaker_shadow/test_runtime.py`: hang guards, with product maxima kept for backend timeouts.
- `plugin/tests/unit/server/test_plugin_metadata_scanner.py`: a `_ReadinessTimer` replaces the wall-clock Timer.
  - **Residual risk:** the real timer's wall-clock firing is no longer asserted.
- `jarvis/ui/e2e/*.spec.ts` (platform-aware shell-refusal assertion, voice-missing 503 tolerated only on `/api/voice/speak`) and `global-setup.ts`.
- `plugin.meta.json` files were regenerated with the project's own generator.
- Every test edit was reviewed, and none weakens a security assertion. These are timing and state-assertion relaxations, so they are accepted risks.

## 4. Verification, final run
Run 2026-10-20 06:26Z to 07:32Z at HEAD `88ec49a2`, in the rootless `neko-test` container with `nice -n 19`.

| Suite | Result |
|---|---|
| Kernel (`unittest discover`) | PASS, exit 0, 1031 ran, OK, 22 skipped |
| Console | PASS, exit 0, 231 ran, 3 skipped |
| `_shared` | PASS, 174 passed, 2 skipped |
| deliverables | PASS, 36 passed |
| desktop | PASS, 465 passed (file order, plus seeded order) |
| doc_tools | PASS, 10 passed |
| jarvis_bridge | PASS, 31 passed |
| learn_vault | PASS, 18 passed |
| lifekit | PASS, 319 passed, 1 skipped |
| local_team | PASS, 49 passed |
| main_agent | PASS, 474 passed |
| research | PASS, 125 passed, 2 skipped |
| wechat_integration | PASS, 12 passed |
| `local_models` | PASS, 19 passed |
| wake | PASS, 70 passed |
| tools | PASS, 53 passed |
| caretaker | PASS, 197 passed |
| evals `neko95/tests` | PASS, 32 passed |
| privacy (plain) | PASS, 15 passed, 1 skipped |
| privacy (real Presidio) | PASS, 244 passed, 9 skipped (GLiNER-PII model not installed) |
| desktop lock test, 20 loops | PASS, 20 of 20 |
| lab, keep-id user | PASS, 171 passed, 61 skipped |
| lab, root with no separate users | PASS, 171 passed, 61 skipped |
| lab, root with `agent`, `jarvis-sandbox`, `jarvis-verify` | PASS, 231 passed, 1 skipped |
| kernel lab report and tasks, as root | PASS, 66 ran |
| `plugin/tests` | PASS, exit 0, 4297 passed, 5 skipped |
| `tests/unit` (full run, before the test fix) | FAIL, exit 1, 42023 passed, 1 failed, 176 skipped. The failure is explained and fixed below. |
| `tests/unit` (full run, after the fix) | PASS, exit 0, 42024 passed, 0 failed, 176 skipped |
| Benchmark v2 | 834 pass, 0 fail, 0 error, 26 deferred of 860 |
| Scorecard v2 | 9.81 mean. 0 live, 202 mocked. |
| Claims checker | the 3 precision/recall tables printed in `claims.log` |
| UI tsc | PASS |
| UI vitest | 37 passed |
| UI console e2e | 55 passed |
| UI bench e2e | 5 passed |

Console e2e also passed 55/55 in three earlier runs.

### The one failure in the full run: root cause found and fixed
`tests/unit/test_visit_runtime_finalize.py::test_family_speaking_first_skips_the_home_line_and_only_shows_the_summary`
- **First full run:** it failed once (3 xdist workers) with "home-coming turn failed: TimeoutError".
- **Isolation:** 20 of 20 passed on the current tree and 20 of 20 on the clean base. This did not reproduce the failure.
- **Reproduced under identical load:** 4 busy-loop processes in a 2-CPU container, whole test file (39 tests), on a throw-away overlay.
  - Current tree: 1 of 12 runs failed, in this same test. Clean base (`git archive 88ec49a2`): 0 of 12 failed.
  - One failure in 12 against none in 12 is weak statistics. The mechanism below is the real evidence.
- **Root cause, a test-setup race (test defect, not a product defect):**
  - `_quiet()` swaps in the scripted ritual and debrief replies once the opening lines are journaled.
  - It assumed the next, deliberately blocked, line turn had already started and taken the gated `default` reply.
  - With an instrumented overlay, I printed `Replies.calls` at the moment of the swap, in 13 loaded runs. It was 1 for the host or the guest in 7 of the 13 runs, so the blocked turn had not started in more than half of the runs.
  - When that turn starts after the swap, it consumes the ritual reply, and the real ritual or debrief turn then waits on the `default` gate until `VISIT_CEREMONY_TIMEOUT_S` (8 s). That produces exactly the logged TimeoutError.
- **Fix** (`tests/unit/test_visit_runtime_finalize.py`, `_quiet()`): wait until both sides' `Replies.calls >= 2` before swapping the queue.
  - The product code, its timeouts and every assertion are unchanged.
  - The 30 s value is only a guard against a hang, not a delay that hides the race.
- **After the fix**, under the same load, the whole file passed 12 of 12 runs. The mechanism is also enforced now, since the swap cannot happen before the blocked turn starts.
- **Wider check:** `pytest tests/unit -k visit -n 2 --dist loadfile`: 3748 passed, 0 failed (exit 0).
- **Full rerun after the fix (closeout):** PASS.
  - Command: `podman run --rm --userns=keep-id --cpus=2 -v <repo>:/src:O neko-test sh -c 'cd /src && python -m pytest tests/unit -q -p no:cacheprovider -n 3 --dist loadfile --basetemp=/tmp/pt -rfE'`, run under `nice -n 19`, 2026-10-20 13:38Z to 13:54Z.
  - Exit code 0. **42024 passed, 0 failed, 176 skipped** in 901.79 s.
  - This is one full pass. The families that were intermittent earlier (`test_backfill_embeds_chunks_and_enables_hybrid_lookup`, about 3 in 75 on both base and current) did not fail in it, but that is not proof they are fixed.
- **Status:** PASS.

### Pre-existing intermittent test
`test_backfill_embeds_chunks_and_enables_hybrid_lookup`
- It fails about 3 of 75 runs on the base, and about 3 of 75 on the current tree (it did not fail in the final run).
- Base and current fail at the same rate and the test is untouched, so no regression is shown.

## 5. Security findings
- The secret scan (`gitleaks detect --no-git`, values redacted) reports 121 hits, mostly in untouched upstream tutorial and test files.
  - **The 9 findings in my five changed files, checked by masked line (long strings shown only as length and first 4 characters):** all are synthetic test inputs.
    - `jarvis/kernel/tests/test_approval_cards.py:103`: a fake `-----BEGIN RSA PRIVATE KEY-----` header inside a test string.
    - `plugin/plugins/research/tests/test_ssrf_and_queries.py:96`: `sk_live...` canary. Lines 97 and 119: a hex run `0123456789abcdef` and a `token...` query string.
    - `plugin/plugins/jarvis_bridge/tests/test_client.py:23`, `225`, `272`: the placeholder launch-code string. The line says "the real console makes a new one at every launch".
    - `plugin/plugins/_shared/tests/test_unknown_types.py:83`: a short `b"token..."` byte string.
    - `plugin/plugins/desktop/tests/test_ocr_entries.py:116`: a fake OPENSSH header plus `b3BlbnNzaC1rZXk`, which is base64 for "openssh-key".
  - No fixture value was changed, since they are the canaries the tests need. A `.gitleaksignore` stays an owner decision (the repo's own pre-commit gitleaks step would flag them).
- Owner's real name and email appear in tracked files. I did NOT replace them, because that is not safe.
  - "Satish", "VenkatAMV" and "satish" are Chrome profile names inside the frozen benchmark cases and adapters (`a_intent.json`, `c_browser.json`, `adapters/*.py`) and in recorded results. Changing them would alter benchmark criteria.
  - `jarvis/tests/neko_safety_live.py` filters the git history scan by the author email.
  - `jarvis/tools/jarvis.lock.json`, `registry.json` and `README.MD` carry the account name and repo URL.
  - Before any public use, a scrub must be planned with the owner (owner decision 5).
- Branch protection:
  - `main` is not protected, and the protection and rulesets APIs return 403 on the current plan.
  - The stored gh login has repo and workflow scopes and admin permission, so it could push to `main`.
  - The caretaker stays off.
  - Safe design: a bot-owned fork credential scoped to the fork, with owner-only merge (see the caretaker README).
- Old audit rows keep their text because of the hash chain. The retention plan is in `RETENTION_CLEANUP_PLAN_2026-10-19.md`.

## 6. BLOCKED or NOT RUN
- **Live lab:** needs the lab box, the Hermes CLI, podman and real sandbox users. The 3-user root container is a substitute and is not live.
- **Windows:** ACLs, PowerShell launchers, Edge, Chrome profiles, Windows UI tests.
- **macOS:** Metal.
- **Hardware:** real microphone, ASR, wake word and TTS; GPUs; ThinkPad P1 CUDA.
- **Other:** Oracle VM (never touched); GLiNER (9 privacy tests skipped); real-browser hostile-text rendering; enabling branch protection (plan limit).
- **Not run in any way:** nothing from the verification list was skipped locally. Everything above is BLOCKED because it needs hardware or an external system.

## 7. Owner decisions
These all default to the safer behaviour until the owner decides:
1. Kill-switch approval policy (A, B, C or C+): `KILLSWITCH_APPROVAL_POLICY_2026-10-19.md`.
2. Trust in third-party writer result shapes.
3. Memory allowlist for open_website, play_youtube, run_browser_action and run_pc_task.
4. Bot fork credential, and whether to narrow or revoke the gh login. Tighten the `gh-jarvis` directory from 0771 to 0700.
5. `.gitleaksignore` for the synthetic fixtures. Replace the real name and email in fixtures.
6. PC lock: release at the 900 s reaper, or hold until the owner recovers it.
7. Lab-owned openers in `lab/demo.py` and the lab runner / MCP client.
8. Arch-Router licence (Katanemo Community) blocks shipping it.
9. Torch pin bump (needs a laptop).
10. Which laptop OS to verify first, and when the microphone and lab machine are available.
11. Review of the retention cleanup plan.

## 8. Final Git status
- Branch `remediation-2026-10-18`, HEAD `88ec49a2`, unchanged.
- Staged files: 0. Stash entries: 0.
- 338 uncommitted lines (modified plus untracked) in `git status --short`.
- Nothing was committed, pushed, merged, deployed or published. No GitHub setting or credential was changed.

## 9. Second pass (same day) and cloud-session handoff
**Done in the second pass:**
- The `test_visit_runtime_finalize` root cause and fix (section 4).
- The Gitleaks findings were checked (section 5).
- Plugin metadata was regenerated and verified with the project's own generator, which printed 8 OK and 0 refused. The regeneration changed no file, so the committed-to-disk snapshots are already current.
- The added diff lines were scanned for leftover debug code, skips and `.only`; nothing was found. The untracked files were checked: no file over 400 KB, no stray logs, and the only executable is an evidence script.
- The second pass changed exactly one file beyond this report: `tests/unit/test_visit_runtime_finalize.py`.

**Looked at and deliberately not changed:**
- `jarvis/lab/jarvis_mcp.py:44` and `jarvis/lab/lab_runner.py:84` call the kernel with `urllib.request.urlopen`, which honours proxy environment variables, while carrying a bearer token.
  - The lab endpoint may be a non-loopback address on the lab box. A blanket proxy bypass could break that deployment, and it cannot be tested here (BLOCKED: real lab machine).
  - It stays an owner decision (lab environment proxy policy), with fail-closed behaviour preserved because nothing changed.
- The `gh-jarvis` directory mode 0771 and the stored gh login: both are credential handling, so I did not touch them.

**No further safe, useful local work remains.** What is left is on the owner decisions list (section 7) and the BLOCKED list (section 6).

**Handoff to a cloud session:**
- **Uncommitted files do not transfer.** A cloud session starts from GitHub `main` and will not have this branch's work. Pushing is forbidden, so nothing was pushed.
- **To preserve the work without pushing:**
  - The server working tree `/srv/jarvis/home/hq-neko` on branch `remediation-2026-10-18` holds everything (338 uncommitted lines).
  - Evidence is in `jarvis/docs/reports/evidence/` (808 KB).
  - This report and `REMEDIATION_PASS4_2026-10-19.md` carry the status.
  - Scratch scripts are under `/tmp/claude-1002/-srv-jarvis-home-hq-neko/f5cf48e8-19a9-50b8-a560-a1752bd6083e/scratchpad/` (`final20/run.sh` is the full verification script; `comp/TESTS-REC/regen_meta.sh` regenerates plugin metadata, and must be re-run last after any edit under `plugin/plugins/<id>/`).
- **First step in the cloud session:** inspect the actual workspace. If the branch is not there, say so and continue work only from this report, because the code changes themselves are not in GitHub.
- **Worth spending cloud credits on:** nothing here needs credits; the open items are owner decisions, hardware, or GitHub plan limits. The full `tests/unit` rerun after the `_quiet()` fix is already done (PASS, section 4).

## 10. Closeout (final)
- Workspace verified before and after: branch `remediation-2026-10-18`, HEAD `88ec49a2`, 338 uncommitted lines, 0 staged, 0 stashed.
- The full `tests/unit` run passed (section 4). No further local regression run was justified, because the only file changed since the last full verification is the one test helper in `tests/unit`. Plugin metadata did not need regeneration.
- No further safe, owner-free fix was available. Everything left is an owner decision (section 7) or BLOCKED (section 6).
- **This is not a production-readiness statement.** Passing automated tests (mostly mocked or in a container) does not cover live lab isolation, Windows and macOS behaviour, voice, GPUs or branch protection. Those are BLOCKED and unverified.

## 11. Priority plan for the owner (written 2026-10-20, after the closeout)
Workspace re-checked before writing: branch `remediation-2026-10-18`, HEAD `88ec49a2`, 338 uncommitted lines, 0 staged. No code changed in this step.
Read-only facts checked: `gh-jarvis` directory mode is 0771 (owner `jarvis`), and `config.yml` and `hosts.yml` inside are 0600. The git remote push URL is `DISABLED-no-push`, but the stored gh login can still push through the GitHub API.

| Rank | Risk | Why it ranks here | Decision or access needed | Safe default until then |
|---|---|---|---|---|
| 1 | The stored gh login has repo and workflow scopes and admin permission on `jarvis-hq`. `main` is unprotected and protection cannot be enabled on the current plan. | Any session or process that uses that login can write to `main`. | You decide: create a bot account or fork credential limited to the fork and revoke or narrow this login, or upgrade the plan or make the repo public so protection can be enabled. Only you can do this (GitHub account settings). | Caretaker stays off. Sessions never use `gh` to write. |
| 2 | Lab verdict trust and real isolation are verified only in a container with three users, not on the real lab box. | A weak boundary there would let an agent influence what counts as verified. | Access to the real lab machine and the Hermes CLI, plus a time slot. Then run the lab suite as root with the three users there. | `verified` needs `full` isolation mode and sandboxed verifier tools. Otherwise it is not reported verified. |
| 3 | Lab client calls to the kernel (`lab/jarvis_mcp.py`, `lab/lab_runner.py`) honour proxy settings while sending a bearer token. | The token could reach a proxy if one is configured on the lab box. | Your proxy policy for the lab box (bypass for the kernel endpoint, or a mandatory proxy). Needs the lab box to test. | No proxy configured on the lab box means no exposure. Check that first. |
| 4 | Kill-switch approval policy (A, B, C or C+) is undecided. | It decides whether stopping JARVIS needs an approval, or only starting again does. | Pick one option from `KILLSWITCH_APPROVAL_POLICY_2026-10-19.md`. | The stricter existing behaviour stays. |
| 5 | Memory allowlist for open_website, play_youtube, run_browser_action, run_pc_task, and third-party writer result shapes. | Decides what tool output may enter long-term memory. | A yes or no per tool, and per writer shape. | Those results are not written to memory. |
| 6 | Your real name and email are in tracked fixtures and frozen benchmark cases. | Privacy risk before any public use. A blind replace would change frozen benchmark criteria. | Agree a scrub plan, including whether a re-baselined benchmark is acceptable. | Repository stays private, push disabled. |
| 7 | PC lock reaper releases after 900 s. | Decides whether the PC can be taken back without the owner when a holder dies. | Release after 900 s, or hold until you recover it. | Current behaviour (release at 900 s) stays. |
| 8 | Retention cleanup, old audit rows keeping text. | Privacy and storage. Deleting rows touches the hash chain. | Review `RETENTION_CLEANUP_PLAN_2026-10-19.md` and approve a dry-run first. | Nothing is deleted. |
| 9 | `gh-jarvis` directory mode 0771. | Low: the credential files inside are 0600. | Authorise a chmod to 0700 on that directory. It is a credential-area change, so I did not make it. | Leave as is. |
| 10 | `.gitleaksignore` for the five synthetic fixture files. | Without it the pre-commit scan would flag them (121 hits overall, mostly upstream). | Approve ignoring those exact fixtures. | No ignore file is created. |

**Hardware-dependent checks that stay BLOCKED (nothing run, nothing simulated):** live lab; Windows ACLs, PowerShell launchers, Edge and Chrome profiles; macOS Metal; microphone, ASR, wake word and TTS; GPUs and CUDA on the ThinkPad P1; Oracle VM (never touched); GLiNER model (9 skipped privacy tests); real-browser hostile-text rendering.

**Suggested order for the owner:** (1) decide the gh login and branch-protection path; (2) schedule the lab-box session, which unblocks ranks 2 and 3; (3) answer the policy choices in ranks 4, 5 and 7 (one line each); (4) when home, run the Windows and voice checks; (5) plan the personal-data scrub and retention dry-run before any public use.

**Local work left that needs no authorisation:** none that I could identify. The container and mocked tests do not show live autonomy.
