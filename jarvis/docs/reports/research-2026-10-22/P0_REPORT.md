# P0 report, 2026-10-22 (laptop, Windows 10 Pro 10.0.19045)

Run by the laptop Claude Code session at the request of cloud session session_016u4bqNCgjvNcfVVZVYEuju.
Read-only checks plus tests. Nothing was changed, installed, deleted, pushed or merged. `D:\jarvis-secrets` was listed by file name only, never read.

## Summary

- Tests: all green except one live check (explained below). Nothing blocks the gate.
- LiteLLM 1.107.0: **not present** anywhere checked. No key rotation needed on this account of it.
- 9router: **not on this laptop** (no npm package, no listener on 20128, no ~\.9router, no docker).
- **Needs owner attention:** `C:\Users\venkat\.claude\settings.json` has **no deny rule** for `//d/jarvis-secrets/**`. I did not edit it.
- The vault (`jarvis-vault/`, `decisions/memory-map.md`, `daily/`, `inbox/`) is **not on this laptop**, so I could not read the memory map or write the daily note there.

## Checkout state

- Repo: `D:\jarvis\hq-neko`, branch `dev-laptop` at `1c28d3a3`.
- Uncommitted: one untracked file, `jarvis/docs/reports/DAILY_DEV.md`. No modified tracked files.
- Fetched `origin/claude/implementation-plan-designs-00v4r6` (fetch only; no branch switch, stash or reset).

## a. Secrets deny rule

`settings.json` contains only model, effort, theme, update channel and UI flags. No `permissions.deny` block at all. **Not protected.** Suggested rules for the owner to add (I did not): `Read(//d/jarvis-secrets/**)`, `Edit(//d/jarvis-secrets/**)`.

## b. 9router inventory

| Check | Result |
|---|---|
| `npm ls -g --depth=0` | only `vercel@62.2.0` |
| Port 20128 | no listener |
| `~\.9router` | absent (no folder with "9router" in the profile root) |
| Processes with "9router" in the command line | none |
| Docker | not installed |

So the 9router to remove or lock down (plan P0 step 4) is somewhere else. The owner's decision #3 needs its location.

## c. LiteLLM check

Searched every directory named `litellm*` under `D:\jarvis`, `D:\jarvis-apps` and `D:\jarvis-keep` (includes the venvs `browser-venv`, `jarvis-venv`, `neko-venv`, `piper-venv`, `privacy-venv`, `colab-mcp\.venv`, `openjev\.venv`, `kaggle-cli`, the Hermes environments, and `D:\jarvis\cache\uv`), plus `C:\Users\venkat\AppData\Local\uv\cache`, and `litellm` in all `uv.lock`, `requirements*.txt` and `pyproject.toml` under `hq-neko`.
**Found: nothing.** No version 1.107.0 or any other. Not covered: the Oracle server and any other machine; system Python on C: had no hits in the profile Python folders.

## d. Disk

| Drive | Free | Used |
|---|---|---|
| C: | 58.2 GB | 118.2 GB |
| D: | 31.3 GB | 43.7 GB |

## e. Health (dev-status.ps1, live_check.py --no-asr)

Up: llama-swap 8080, JARVIS console 8799, N.E.K.O UI, memory, agent, plugin runtime, speech, widget, wake word, privacy gateway. Plugins running: deliverables, desktop, doc_tools, jarvis_bridge, learn_vault, local_team, main_agent, research, web_search. Scheduled tasks: JarvisNekoStack Running; Lab, DailyReport, Watchdog, Node, Widget Ready. Telegram OFF. Kernel reachable and authorized, kill switch off, audit chain verifies (34,619 events), laptop node online, 0 pending approvals.

live_check: **69 of 70 passed.** The failure:

```
[FAIL] local team: adviser saw a placeholder, the local answer has the real value -- ... adviser skipped: The public adviser is reached only through the privacy gateway (127.0.0.1:48950); N.E.K.O's agent model points elsewhere, so nothing was sent.
```

Cause shown by the message: the planner slot (`core_config.json` agentModel*, currently Groq direct per DAILY_DEV.md) is not routed through the privacy gateway, so the adviser step was skipped. Not a regression I introduced; not investigated further.

## f. Tests

| Suite | Result |
|---|---|
| Kernel `python -B -m unittest discover -s tests` (jarvis-venv) | **Ran 1056 tests, OK (skipped=60)**, 1321 s |
| Desktop plugin pytest (neko-venv) | **464 passed, 2 skipped**, 187 s |
| dev-test.ps1 quick: chat frontend vitest | 39 files, 906 tests passed |
| main_agent | 526 passed |
| jarvis_bridge | 31 passed |
| jarvis console | 251 passed, 1 skipped |
| dev-test.ps1 quick overall | 0 suites failed |

Not run: dev-test `full` mode's caretaker/tools suite, and the plan's 860-case bench on a merged tree (no merge was done). The 23 known upstream `test_theater_numeric_v2_*` failures were not exercised (not in these suites). Kernel run printed many ResourceWarnings about unclosed loopback sockets (noise, not failures). The kernel suite also hit a state where a unittest wrapper process showed up twice in the process list; it completed normally.

## g. Branches (ahead of origin/main; all have 0 behind)

| Branch | Ahead |
|---|---|
| dev-laptop (local, checked out) | 32 |
| remediation-2026-10-21-test (local = origin) | 21 |
| origin/remediation-2026-10-18 | 11 |
| origin/combine-tools | 5 |
| origin/claude/implementation-plan-designs-00v4r6 | 50 |
| origin/main | 0 |

**The laptop checkout contains remediation-2026-10-18**: `origin/remediation-2026-10-18` is an ancestor of HEAD, and 0 of its commits are missing from `dev-laptop`. So main can fast-forward to it (it is 11 ahead, 0 behind).

## Could not be tested or done

- Vault reads/writes (no vault on this laptop). No daily note written.
- Oracle-side facts: instance shape, 9router location, OmniRoute auth, LiteLLM on other machines.
- Hermes `config.yaml` hash and the 860-case bench.
- Sending this summary back: see the message to the cloud session.

## Decisions for the owner (from the plan, with the plan's recommendation)

| # | Decision | Recommendation |
|---|---|---|
| 1 | Fast-forward main to `remediation-2026-10-18`, deploy the 10-22 kernel | Yes, after a full bench run on the merged tree. Laptop already contains 10-18; kernel suite is green here. |
| 2 | Oracle: resize to 2/12 at $0, or ~$25-28/month for 4/24; check hermes-agent-vm-2's shape yourself | Resize (free). Verify with a 24 h soak. |
| 3 | 9router remove or lock down; OmniRoute retire after router port | Remove 9router (not on this laptop, so locate it first); retire OmniRoute in P3. |
| 4 | PIN-gated admin allowlist instead of raw admin | Yes. Raw admin not recommended. |
| 5 | Meaning of "main" | Both, read separately: P0 merges; P6 gate allows capped, auto-reverted kernel-only fast-forwards. |
| 6 | Paid items, each separately (Gemini, OpenAI, Anthropic APIs, $10 OpenRouter, Oracle PAYG) | OpenRouter $10 is the cheapest uplift; others only for a named need. |
| 7 | Is "Gemini" a consumer plan or paid API key | Consumer plans don't work with Gemini CLI; do not route them. |
| 8 | Disk: prune small models, add a drive, or skip the deep MoE | Prune after A/B. D: has only 31.3 GB free, which supports this. |
| 9 | Phone channel and OS | PWA + Web Push first; ntfy if Android; Telegram pointers only. |
| 10 | May browser/phone approve | Yes, with in-app PIN and device pairing. |
| 11 | Grokbot / Clawdbot readings | Confirm xAI Grok Bot and OpenClaw. |
| 12 | Distribution profile | Design for shared; run personal. |
| 13 | Secrets: files or Credential Locker | Keep files (repo rule). Note the missing deny rule in (a). |
| 14 | Python packaging | uv runtime for the laptop; Nuitka profile for a shareable installer. |
| 15 | UI depth | Port, with provenance headers and caretaker diffs. |
| 16 | Coding agents | Accept mini-swe-agent + OpenCode; Codex optional; goose dropped. |
| 17 | Public HTTPS tunnel for ChatGPT's MCP | Defer; Gemini CLI over Tailscale first. |
| 18 | Create-file/folder capability | Yes, YELLOW_AUTO inside user folders with undo. (Laptop's last commit makes the decider refuse "create a folder" for now.) |
| 19 | Signature cloned voice | Optional; Kokoro `bm_george` meanwhile. |
| 20 | Windows edition / Windows 11 | This laptop is Windows 10 Pro 10.0.19045, so Sandbox is available; Windows 10 is past mainstream support. |

Added by this run: **21.** Add deny rules for `D:\jarvis-secrets` to `~\.claude\settings.json`? Recommend yes. **22.** Where is the vault, so the daily note and memory map can be read/written? **23.** Where did 9router run, if not here?

Stopping at the P0 gate. Waiting for the owner's go.
