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

"Approval" means the owner PIN in the JARVIS console. **The PIN is not set yet**, which is an owner action. Until it is, approval flows stop at "waiting".

## 4. Controls

| Requirement | Control | State |
|---|---|---|
| Credentials | `D:\jarvis-secrets` references (`NEKO_SECRET_REF_ROOTS`); never-dump rule; gitleaks | In place. The OS credential store (Windows Credential Manager via `keyring`) is a planned option for new provider keys |
| Redacted diagnostics | Logs hold counts by kind, never values (gateway); kernel audit holds argument hashes | In place |
| Prompt injection | Untrusted labels on tool results; closed capability catalogue (the plan is made from the request only, not from web content); argument-bound approvals | In place for main_agent and connectors. Research adds a rule: content may supply facts only, never steps |
| Least privilege | Per-mode catalogue filtering; browser helper only on web cues; MCP tool allowlists; filesystem MCP read-only | In place, extended per role |
| MCP tool poisoning or rug pulls | SHA-256 fingerprint of each tool's name, description and schema; any change switches the tool off; sampling, elicitation and roots refused | In place (D17) |
| N.E.K.O's own agents | `mcp_adapter` (protocol 2024-11-05, no per-tool approval) and computer-use (any hotkey) stay **off** for account and desktop work. browser-use runs only in its own profile on D: | Policy |
| Emergency stop | Kernel kill switch (console). New: a Stop button on the Neko icon and the voice "Neko, stop", both calling it | Kill switch in place; icon and voice stop planned |
| Audit | Kernel audit log of every job and decision, without secrets | In place |
| Filesystem scope | Outputs under `D:\jarvis\outputs`; reads through doc_tools limited to allowed folders; no access to `D:\jarvis-secrets` | In place |
| Network and remote inference | Gateway upstream list; each provider can be disabled; no remote call without a configured key; no cost without approval | Gateway in place; fallback list planned |
| Clear history and memory | N.E.K.O memory UI; a run-file cleanup script with backup | Partly in place |
| Self-modification | Agents cannot change code, policy or permissions; skills and prompts change only through git with owner review | In place |
| Packages | No arbitrary installs by agents. Installs are owner-approved, pinned, hash-checked where a hash is published, and at least 2 weeks old | Practice |

## 5. Known residual risks

- **Local models are not a security boundary.** A local model with the real data could still put it into a tool argument. Every write action is gated.
- **Playwright MCP is not a security boundary** (its own README). Its `--allowed-origins` does not cover redirects. Kernel approval per write step is the boundary.
- **The always-on microphone** keeps the Windows mic indicator on, and Windows 10 is out of support (2025-10-14). Wake word stays opt-in.
- **The free tier of a public provider may train on data** (Gemini). Such providers are excluded for personal use.
- **The GLiNER plus Jev redaction is measured, not proven:** 12 of 12 secrets hidden and 3 of 12 false alarms on a fresh set. Cue-less secrets in the system prompt get patterns only.
