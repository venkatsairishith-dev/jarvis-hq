# Neko security model

Neko inherits the JARVIS authority model. **The kernel is the only component that authorises actions.** N.E.K.O, the plugins, the models and the MCP servers are untrusted callers. Evidence for the threats listed here is in `evidence/04-computer-browser-mcp.md` (section on MCP risks) and `evidence/03-orchestration-research-memory.md` (framework advisories).

## 1. Assets

| Asset | Where it lives | Protection |
|---|---|---|
| The owner's browser session and Google account | Chrome Default profile | Never copied. Reached only through the Playwright MCP extension. Every connection is approved in Chrome, and every write step needs kernel approval |
| API keys and tokens | `D:\jarvis-secrets` (file references) | Never read, printed, logged or committed. Configs hold only `file:` references. gitleaks runs before each commit |
| Personal data typed or spoken | Memory server, run files | Local disk. The privacy gateway redacts it before any public model sees it |
| Files | User folders | The filesystem MCP is read-only on outputs. Writes go through kernel jobs. Deletion is RED |
| The system itself | Windows | No privileged commands without RED approval. No security-setting changes |

## 2. Trust boundaries and data flows

1. **Owner → Neko** (voice, click, typed): trusted intent, but the speech-recognition text may be wrong. Destructive actions are always confirmed.
2. **Neko → local models** (llama-swap, Verdict, GLiNER): local. Models see real data and are not treated as secure; their output is untrusted.
3. **Neko → public models** (Dahl, later Groq, OpenRouter, Cloudflare): only through the privacy gateway, and only in the public lane.
   - Placeholders replace secrets and PII. The model pass covers user and tool messages; patterns cover everything (D19, D20).
   - Private connector data never goes to Dahl.
4. **Web, files, MCP results → Neko:** untrusted data.
   - It is labelled as untrusted and never followed as instructions.
   - It never goes into memory automatically.
   - It cannot raise its own permissions.
5. **Neko → computer** (browser, apps, files): only through kernel jobs and policy tiers.

## 3. Permission tiers (kernel policy)

| Tier | Meaning | Examples |
|---|---|---|
| GREEN | Runs immediately and is logged | Read a page snapshot, list windows, read outputs, time, search, open a URL in a new tab, media-state read |
| YELLOW_AUTO | Runs and is logged with an undo where possible | Take a screenshot, write a new output file, open a generated file, launch a known app |
| YELLOW_APPROVAL | Waits for owner approval, bound to the exact arguments and usable once | Click, type or select in the browser; organise files (an exact move list with an undo journal); start a slideshow by keypress |
| RED | Approval plus PIN, with a warning; some actions are never automated | Delete files, send messages, publish, download or upload executables, purchases, security settings, privileged commands |
| DENY | Never | Password managers, wallets, banking UIs, CAPTCHA solving, bypassing sign-in, disabling the kill switch or audit, editing the kernel policy |

"Approval" means the owner PIN in the JARVIS console. The PIN was set on 2026-10-16, and a protected action was approved live with it (`PC_CONTROL_TEST_REPORT.md`). It was typed in chat once, so **changing it is an open owner action**.

## 4. Controls

| Requirement | Control | State |
|---|---|---|
| Credentials | `D:\jarvis-secrets` references (`NEKO_SECRET_REF_ROOTS`); never-dump rule; gitleaks | In place. The OS credential store (Windows Credential Manager via `keyring`) is a planned option for new provider keys |
| Redacted diagnostics | Logs hold counts by kind, never values (gateway); kernel audit holds argument hashes | In place |
| Prompt injection | Untrusted labels on tool results; closed capability catalogue (the plan is made from the request only, not from web content); argument-bound approvals | In place for main_agent and connectors. Research adds a rule: content may supply facts only, never steps. Since 2026-10-17, research removes sentences addressed to AI assistants from fetched pages and from evidence passages, and says how many it removed. Matching uses NFKC text with zero-width characters and soft hyphens removed. A page or search title addressed to an AI is replaced by the host name (`plugin/plugins/research/verify.py`, `core.py`) |
| Least privilege | Per-mode catalogue filtering; browser helper only on web cues; MCP tool allowlists; filesystem MCP read-only | In place, extended per role |
| MCP tool poisoning or rug pulls | SHA-256 fingerprint of each tool's name, description and schema; any change switches the tool off; sampling, elicitation and roots refused | In place (D17) |
| N.E.K.O's own agents | `mcp_adapter` (protocol 2024-11-05, no per-tool approval) and computer-use (any hotkey) stay **off** for account and desktop work. browser-use runs only in its own profile on D: | Policy |
| Emergency stop | Kernel kill switch (console). New: a Stop button on the Neko icon and the voice "Neko, stop", both calling it | Kill switch in place; icon and voice stop planned. Fixed 2026-10-17: the lab runner still got queued jobs while the kill switch was on; `Jobs.next_for` now hands out nothing (D-075) |
| Audit | Kernel audit log of every job and decision, without secrets | In place |
| Filesystem scope | Outputs under `D:\jarvis\outputs`; reads through doc_tools limited to allowed folders; no access to `D:\jarvis-secrets` | In place |
| Network and remote inference | Gateway upstream list; each provider can be disabled; no remote call without a configured key; no cost without approval | Gateway and ordered fallback in place (D24). Since 2026-10-17, a key file that disappears mid-request falls back to the next provider instead of dropping the connection (J-054/J-055) |
| Clear history and memory | N.E.K.O memory UI; a run-file cleanup script with backup | Partly in place |
| Self-modification | Agents cannot change code, policy or permissions; skills and prompts change only through git with owner review | In place. Fixed 2026-10-17 in the skill gate (`plugin/plugins/desktop/skills.py`): a gate run with 0 cases no longer passes (M-024). A damaged registry is refused instead of reset; version files are never overwritten, and numbering counts the existing files (M-029). Concurrent proposals are serialised by a thread and file lock, with unique temp files (M-030). The Windows `msvcrt` branch of the lock has not run yet |
| Packages | No arbitrary installs by agents. Installs are owner-approved, pinned, hash-checked where a hash is published, and at least 2 weeks old | Practice. The model comparison loads only files pinned in `bench/model_candidates.json`, with the SHA-256 checked |
| Public adviser address | `local_team` sends a task to the public adviser only when the URL's host and port are exactly the privacy gateway: http, no user info | Fixed 2026-10-17 (J-068, critical). Before, `http://evil.example/127.0.0.1:48950/v1` and `https://127.0.0.1:48950.evil.example/v1` were accepted |
| Keys in relayed text | The privacy gateway scrubs a fallback's error page that echoes its key before relaying it (J-062). A failed agent call on the owner plane keeps only the error class, with no internal hosts (L-028) | Fixed 2026-10-17 |
| Reported success | A job or step counts as done only when `ok` is the boolean `True`. `{"ok": "false"}` or `{"ok": 1}` is not success | Fixed 2026-10-17: kernel `Jobs.finish` (N-062), desktop coordinator (E-049) |
| Lab jobs | A lab job that crashed, expired or was cancelled now fails or cancels its task (`Jobs.close_lab_tasks`), so the owner hears about it | Fixed 2026-10-17 (D-072/073/074) |
| Vault names | Note names with control characters (newline, ESC, CR, NUL, DEL, C1) are refused | Fixed 2026-10-17 (K-108) |
| Connector programs | Adding a stdio connector checks the program against the secrets folder and against shell and interpreter names. Names are compared by stem (`python3.11` → `python`), for the given name and for a link's target, against the Windows sets plus Unix shells and system tools | Fixed 2026-10-17. Before, these checks worked only on Windows: on Linux/macOS every add crashed with a 500, and `/bin/sh` or `python3.11 -c` were accepted |
| Local model servers | Every llama-server binds 127.0.0.1 with `--no-webui`. No `--tools`, `--agent`, MCP flags, `--props` or a plain `--api-key` (llama-server b11669+ can act as an agent, and its CORS reflects any Origin). llama-swap listens on 127.0.0.1:8080 | In place, enforced by `jarvis/local_models/test_server_flags.py` (9 tests, 2026-10-17) |
| Deliverables | `report.html` turns markdown images into plain links, so a saved report loads nothing remote (I-022). Spec values of the wrong type, NaN and control characters are refused or replaced (I-019..I-021) | Fixed 2026-10-17 |
| Benchmark Console | Loopback only. DELETE is accepted only under `/api/bench/`, with the same same-origin check as POST. Jobs are built only from allow-lists (argument lists, no shell). Challenge-lab text is saved as untrusted under `jarvis/evals/neko95/results/challenges/`, which is git-ignored, and never becomes an expectation | In place (2026-10-17) |
| Repository | The private copy is committed locally only. Push is disabled, and GitHub Actions are off for the private repo. Actions is a GitHub setting, so it cannot be checked from the tree. Upstream's workflow files are unchanged, and several of them run on every push to `main`, so Actions must stay off. `.gitignore` keeps secrets folders, model weights and challenge-lab results out | Practice |

## 5. Known residual risks

- **Local models are not a security boundary.** A local model with the real data could still put it into a tool argument. Every write action is gated.
- **Playwright MCP is not a security boundary** (its own README). Its `--allowed-origins` does not cover redirects. Kernel approval per write step is the boundary.
- **The always-on microphone** keeps the Windows mic indicator on, and Windows 10 is out of support (2025-10-14). Wake word stays opt-in.
- **The free tier of a public provider may train on data** (Gemini). Such providers are excluded for personal use.
- **The GLiNER plus Jev redaction is measured, not proven:** 12 of 12 secrets hidden and 3 of 12 false alarms on a fresh set. Cue-less secrets in the system prompt get patterns only.
- **Not verified on the Linux test server (2026-10-17):** the 11 privacy redaction tests need presidio, which is not in the test image. The Windows `msvcrt` skills lock has not run.
- **Open owner actions:** revoke the Dahl test key pasted in chat on 16 Oct, and delete the server's copy. Change the approval PIN.
