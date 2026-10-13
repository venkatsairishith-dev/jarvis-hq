# 03 Computer control: what to reuse for the Windows execution adapter

Date of research: 2026-10-11. Author: research agent (read-only on `D:\jarvis\hq`, this file is the only change there).
Scope: control of the owner's Windows 10 PC and of the Linux "Agent PC" (WSL2 Ubuntu 24.04, XFCE on TigerVNC display :1, noVNC in the hub).

## 0. Summary and recommendation

1. **Do not adopt any of the big agent frameworks as the runtime.** UFO, Browser Use and OpenHands each run their own
   plan/act loop and decide for themselves when to ask. In JARVIS the kernel is the only permission authority and a model never
   authorizes. The controllable layer that all of them stand on is a small set of OS primitives (UI Automation, SendInput,
   screenshots, window management, Playwright). Reuse those primitives and the best of their guard patterns, not their loops.
2. **Build a thin "Windows execution adapter" on the laptop node** (extends `node/jarvis_node/winbackend.py`, `ui.py`, `jobs.py`):
   fixed job kinds, schema-checked parameters, kernel approval consumed first, then UIA-first action with read-back verification and a
   tri-state result (`verified | unverified | mismatch`). Section 8 gives the design, section 9 the ranked work list (items 1 to 10: about 16 to 23 days;
   the first three items, about 4.5 to 6.5 days, close most of the gap).
3. **Library choice:** `uiautomation` (yinkaisheng, Apache-2.0, v2.0.29) over `comtypes` for UIA when the PowerShell-per-call approach
   becomes the bottleneck; `mss` (MIT) for screenshots; Playwright (Apache-2.0) behind the existing Smokescreen checkpoint for the web;
   keep `xdotool` + AT-SPI on the Agent PC. Avoid `pyautogui` (last release 2023-05-24), AutoHotkey (GPL-2.0), and OmniParser's
   `icon_detect` v2 weights (AGPL-3.0).
4. **Pieces worth lifting (read and re-implement or vendor with attribution):**
   - `CursorTouch/Windows-MCP` (MIT, v0.8.8): `desktop/service.py` `bring_window_to_top`, `_validate_screen_point`, paste-vs-SendKeys typing; `tree/service.py` + `tree/budget.py` snapshot shape; `desktop/control.py`, `control_ledger.py` for human take-over on Windows.
   - `microsoft/UFO` (MIT, v3.0.10): `ufo/automator/path_validator.py` (stdlib only, CWE-22 checks), the Data/Action server split in `ufo/client/mcp/local_servers/ui_mcp_server.py`, the set-text fallback ladder in `ui_control/controller.py` (`set_edit_text`).
   - `anthropics/claude-quickstarts` `computer-use-demo`: only as the reference for coordinate scaling and the xdotool/scrot action set (already mirrored by `lab/jarvis_mcp.py`).
5. **Important facts that change earlier assumptions** (all verified 2026-10-11):
   - `OpenHands/OpenHands` is now the "Agent Canvas" front end (React/Electron); the Python agent runtime moved to `OpenHands/software-agent-sdk`. Both are MIT. The enterprise code is in separate repos under PolyForm Free Trial 1.0.0 (not usable beyond a trial). The earlier "[verify] enterprise licence" item in `01_RESEARCH_AUDIT.md` is resolved.
   - UFO has had **8 published security advisories between 2026-08-10 and 2026-09-22**, including two critical and three high. Windows-MCP had a **high** one (unauthenticated PowerShell over HTTP, fixed in 0.7.5). Browser Use had a **critical** allowlist bypass (fixed 0.1.45).
   - Windows-MCP and Browser Use both enable anonymous telemetry **by default** (PostHog); both can be switched off by an env var.
   - Frontier models, not frameworks, now dominate results (OSWorld-Verified leaders report 85 to 90 percent, self-reported; UFO2's 2025 paper reported 27.9 percent on WAA with GPT-4o). Keep the adapter model-agnostic and small.

Evidence tags used below: **[R]** reproducible or primary (source read, official doc, advisory, paper table); **[A]** anecdote or vendor/self-reported claim; **UNVERIFIED** could not be confirmed.

---

## 1. Method, pins and disk

Clones are shallow (`--depth 1`), inspected only, never executed, each in its own new directory under `D:\jarvis\research\`.
GitHub metadata through `gh api` (read-only, public endpoints). Vendor-doc and paper summaries came from a fetch tool that summarises with a
small model, so numbers taken that way are tagged "per fetch summary" and should be re-read in the paper before being quoted outside this file.

| Directory (D:\jarvis\research\) | Repo | Clone HEAD SHA | HEAD date | Latest release at research time |
|---|---|---|---|---|
| cc-UFO | microsoft/UFO | a795552d976c4c019d7c2f778a0effb5cef7de6b | 2026-09-29 | v3.0.10, 2026-09-22 |
| cc-Windows-MCP | CursorTouch/Windows-MCP | b74c507d9e709223a0904cf08019edf55f2a75bb | 2026-10-10 | v0.8.8, 2026-10-10 |
| cc-browser-use | browser-use/browser-use | c75e8476e26d18b7617643bc2ae082fae8eae431 | 2026-10-07 | 0.13.11, 2026-10-07 |
| cc-playwright-mcp | microsoft/playwright-mcp | b8b4183e099f136cbec0388a6088d4aa2f6b9685 | 2026-10-08 | v0.0.83, 2026-09-28 |
| cc-OpenHands | OpenHands/OpenHands | e0a26af82f0b2016423c0d53cc9ae32a357cef2d | 2026-10-10 | v1.26.0, 2026-10-08 |
| cc-software-agent-sdk | OpenHands/software-agent-sdk | c4b93299cf2b31c8b5d0b2c5a89965ccc5ff24f9 | 2026-10-10 | v1.54.0, 2026-10-09 |
| cc-OmniParser | microsoft/OmniParser | 354021201345a96178360b28733573e27269f2de | 2026-07-19 | none (HEAD only) |
| cc-claude-quickstarts | anthropics/claude-quickstarts | 9ec32b91df50b0d4906cae64b13c6298055ff40d | 2026-10-07 | none |
| cc-pywinauto | pywinauto/pywinauto | 18d2a95cebed2f0061ab4e4c80c3a76ece5dd4f3 | 2026-05-23 | 0.6.9, 2025-01-06 |
| cc-Python-UIAutomation-for-Windows | yinkaisheng/Python-UIAutomation-for-Windows | 05cab1972d6e08216eae5c5183f9b3d47e26266d | 2026-06-02 | none tagged; package 2.0.29 |
| cc-python-mss | BoboTiG/python-mss | becaba33b79c98e64c8a46c6ce5b5086bcdfc4ea | 2026-10-05 | v10.2.0, 2026-04-23 |

Not cloned (metadata only): `microsoft/playwright` (245 MB; v1.64.0, 2026-10-07, Apache-2.0), `microsoft/playwright-python` (v1.63.0,
2026-09-15, Apache-2.0), `mhammond/pywin32` (build 312 / PyPI 312, 2026-06-04), `enthought/comtypes` (1.4.17, 2026-09-21),
`ra1nty/DXcam` (v0.3.0, 2026-03-12, MIT), `AutoHotkey/AutoHotkey` (v2.0.30, 2026-10-09, GPL-2.0), `asweigart/pyautogui` (0.9.54, 2023-05-24, BSD),
`jordansissel/xdotool` (v4.20260303.1), `trycua/cua` (MIT, pushed 2026-10-10).

Disk: D: free 47,897,219,072 bytes before, 47,301,853,184 after (about 0.6 GB used; the clones are 336 MB on disk). Well under the 2 GB limit.

---

## 2. What the project has today (read-only review)

Files read: `node/jarvis_node/{pcopen,winbackend,browser,guardproxy,jobs,ui,collector,control,agent}.py`, `lab/{jarvis_mcp,lab_control}.py`,
`docs/02_ARCHITECTURE.md` (browser guard, agent control, Agent PC, take control, "JARVIS acts on the owner's requests"),
and for the policy table `kernel/jarvis_kernel/{policy,jobs}.py` (lines 14 to 60) and `llm_proxy.py` L160-195.

### 2.1 How the pieces connect [R]

```
Hermes / lab agent -> jarvis_mcp.py tool -> kernel POST /v1/jarvis/actions
   kernel: policy.decide (GREEN / YELLOW_AUTO / YELLOW_APPROVAL / RED / DENY) -> single-use approval bound to action hash
   kernel/jobs.py KINDS: project_check, output_save, output_show, ui_action, pc_open, lab_run
laptop node agent.py polls /jobs/next every 15 s -> jobs.run_job -> ui_action | pc_open | output_* | project_check
   ui_action  -> winbackend.BACKEND {launch, click, type}      (PowerShell + .NET UIA, one process per call)
   pc_open    -> pcopen.pc_open {open_app, open_folder, open_file, list_folder}
Agent PC (WSL box): jarvis_mcp.desktop_* (xdotool, xwd) -> acts directly on :1; lab_control.py freezes agents for take-over
Browser: browser.BrowserSession (Playwright + Smokescreen) is NOT a kernel job kind; reachable only in-process via controller_for()
```

### 2.2 Status against the six capabilities

| # | Capability | Exists | Missing or weak (file, line) |
|---|---|---|---|
| a | Open / focus / close app and window | **Open:** `pcopen.open_app` via `Get-StartApps` and `explorer shell:AppsFolder\<AppID>` (L104-151); `winbackend.launch` for 3 exes (L9, L71-75). Folders and files via known folders (L33-101, L137-184). **List windows on the Agent PC:** `jarvis_mcp.desktop_windows` (L338-350). | **Focus:** policy lists it (`policy.py:43`) but kernel `UI_OPS = {launch, click, type}` (`kernel/jobs.py:17`) and `BACKEND` (`winbackend.py:91`) do not. **Close:** nowhere. **List windows on Windows:** policy `list_windows` GREEN, no implementation. Target is a process *name*, first process with a main window (`winbackend.py:16`, `ui.py:22`): wrong instance when two windows exist; UWP apps hosted by ApplicationFrameHost may report no main window (UNVERIFIED, not run here). |
| b | Read UI tree of a Windows app via UIA | `ui.snapshot()` (`ui.py:47-66`): ControlView walk, depth and count caps, values only for non-password fields, secret scrubbing, foreground default. Read-only. | **No job kind and no MCP tool calls it** (`jobs.run_job` L154-172 has no snapshot kind; `Controller.observe` takes a callable that nothing supplies on Windows). No bounding rectangles, no runtime id, no element reference that a later click can re-resolve. Uses managed `System.Windows.Automation` through PowerShell (about 235 ms bare, 343 ms with the UIA assembly loaded on this laptop, measured 2026-10-11). |
| c | Click / type / keys with verification | `winbackend.click` = `InvokePattern.Invoke` only (L25); `type` = `ValuePattern.SetValue`, fallback keyboard only if the target process owns the foreground (L26-47), password fields refused, SendKeys metacharacters escaped (L44). `control.Controller.do` implements ask -> snapshot -> act -> snapshot -> expect (L55-79) with repeat-failure and budget guards. | **Verification is not on the Windows path:** `jobs.ui_action` (L139-151) returns `{"ok": True, "done": op}` straight after the backend call; the `Controller` is only used by `browser.controller_for`. No `key`, `scroll`, `double-click`, coordinate click. No retry/fallback when a control lacks InvokePattern (e.g. Electron, custom-drawn) = hard fail with reason `pattern`. Foreground check then `SendWait` has a time-of-check/time-of-use window. |
| d | Screenshot | Agent PC only: `desktop_screenshot` (`xwd -root | convert`, `jarvis_mcp.py:394-401`) and hub thumbnail (`lab_control.screen_jpeg`). | **Nothing on Windows.** No scaling to model limits, no region capture, no DPI handling. |
| e | Browser steps | `browser.py`: throwaway profile, no downloads/permissions/popups, Smokescreen checkpoint with a self-test, early URL guard, hidden-element filtering, `expect_name` check, action budget, scrubbed snapshot (L111-270). Policy rows exist (`policy.py:33-35`). | **Not reachable from a kernel job or MCP tool.** `playwright==1.63.0` pinned in `requirements-browser.txt`; the architecture doc itself says Hermes's browser toolset is off. No tab handling by design. |
| f | File access restricted to allowed paths | `pcopen.known_folder/resolve` (L33-101): six known folders, realpath containment, `..` and drive letters rejected, document/media extensions only, SENSITIVE regex; `jobs._output_path` (L98-107): `D:\jarvis\outputs\<agent>\`, name regex, never overwrites. | No read-file-content op (deliberate). No configured allowlist of additional roots (for example a project workspace). Containment relies on `str.startswith(root + os.sep)` after `realpath`: fine, but case-insensitive NTFS and 8.3 short names are not normalised (UNVERIFIED, not tested). UFO's `path_validator.py` covers reserved device names and trailing dots. |

### 2.3 Agent PC specifics [R]

- `jarvis_mcp.py` hard-codes DISPLAY=:1, 1600x900 bounds, `xdotool search --onlyvisible --name` then `windowactivate --sync`, `click`/`type`/`key`, `xwd` capture. No post-action verification; replies "clicked" after the process exits 0.
- Take-over is a real design, not a VNC view-only switch: `lab_control.py` SIGSTOPs every agent task tree and the screen drivers (`cua-driver`, `xdotool`), keeps windows alive, guards new tasks every 2 s, and the tool server injects a one-time "re-check the screen" note on hand-back (`jarvis_mcp._say`, L108-120). This is already ahead of the products surveyed below.
- `02_ARCHITECTURE.md` records the measured gaps of `cua-driver` on Linux (AT-SPI typing into web pages leaves fields empty, frame offsets), which is why xdotool/xwd were added.

### 2.4 Defects and risks found in the existing code (worth fixing in the adapter)

1. `ui_action` has no read-back. A click that raises no exception is reported as done.
2. Process-name targeting (`Get-Process -Name`) can pick the wrong window or an unrelated process with the same name; use PID + executable path + window handle.
3. `winbackend.FIND` returns `Descendants` first match for AutomationId OR Name: two controls with the same name silently resolve to the first.
4. A running job cannot be cancelled: `agent.py` only checks the kill switch before fetching the next job (L57-61); a PowerShell action runs up to the 30 s timeout (`winbackend.py:62`).
5. Observation is GREEN while the content is screen text. Window titles and element names are attacker-influenced (a web page title, a document name) and go to a model; they are scrubbed for secrets but not marked untrusted at the Windows layer the way browser pages are.

---

## 3. Per-project findings

### 3.1 Microsoft UFO / UFO2 / UFO3 (github.com/microsoft/UFO)

- **Status [R]:** MIT, v3.0.10 (2026-09-22), HEAD a795552 (2026-09-29), 9,982 stars, 87 open issues, created 2024-01-08, actively maintained. Windows only for the GUI agent. README roadmap lists "Picture-in-Picture mode" as in development (the UFO2 paper abstract presents it as a feature: UNVERIFIED whether it ships).
- **Lineage [R]:** UFO (Feb 2024) -> UFO2 "Desktop AgentOS" (arXiv 2504.14603, submitted 2025-04-20) -> UFO3 "Galaxy" multi-device DAG orchestration (arXiv 2511.11332, 2025-11-14, v2 2026-03-01). UFO2 stays the device agent inside Galaxy.
- **Architecture [R]:** `HostAgent` picks the application window and decomposes the task; an `AppAgent` per application chooses a control and an action (`ufo/agents/agent/`, `processors/strategies/`). The control layer is `ufo/automator/`: `ui_control/inspector.py` (UIA and Win32 backends through pywinauto, `BackendFactory`), `ui_tree.py`, `screenshot.py` (1,492 lines, annotated screenshots), `control_filter.py`, `controller.py` (command objects: `ClickInputCommand`, `SetEditTextCommand`, `keyboardInputCommand`, `WheelMouseInputCommand`, ...), `puppeteer.py`, plus `app_apis/{excel,word,powerpoint,shell,web}` (COM and CLI "API actions" that skip the GUI). Default `CONTROL_BACKEND: ["uia"]`; `omniparser` is the vision-grounding option (`config/ufo/system.yaml:11`, `ui_control/grounding/omniparser.py`).
- **"MCP-like" actions [R]:** UFO3 exposes the same capabilities as FastMCP servers under `ufo/client/mcp/local_servers/` (`ui_mcp_server.py` split into a *data* server and a *host/app action* server; `word_wincom`, `excel_wincom`, `ppt_wincom`, `cli_mcp_server.py`, `pdf_reader`) and `http_servers/` (`linux_mcp_server.py`, `mobile_mcp_server.py`, `hardware_mcp_server.py`). The Linux server is **shell only** (`execute_command`, `get_system_info`), so UFO adds nothing for the Agent PC GUI.
- **Standalone use of the control layer:** partly. `path_validator.py` (stdlib only), `control_filter.py` (stdlib only) and `inspector.py` (pywinauto + psutil) import cleanly; `controller.py`, `ui_tree.py` and `ui_mcp_server.py` pull `ufo.config`, `ufo.agents.processors.schemas`, `aip.messages`, so extracting them means vendoring half the package. `requirements.txt` pins `pywinauto==0.6.8`, `pandas==1.4.3`, `numpy==1.26.4`, `faiss-cpu==1.8.0`, `sentence-transformers==2.6.0`, `langchain==0.3.27`, and the README badge says Python 3.10 or 3.11. The laptop runs Python 3.14.7; these pins will not install there (inference from wheel availability, not tested).
- **Models [A/UNVERIFIED]:** multimodal model required for the vision path; paper used GPT-4o and o1; clients for OpenAI, Azure, Anthropic, Gemini and others are present. No model-weight dependency for the default UIA path.
- **Benchmarks (per fetch summary of arXiv 2504.14603 v2, Table 1, 30-step cap) [A, paper-reported]:** UFO2 27.9 percent (GPT-4o) and 30.5 percent (o1) on Windows Agent Arena (154 tasks, 15 apps); 28.6 and 32.7 percent on OSWorld-W (49 tasks); the paper attributes over 62 percent of UFO2-base failures on WAA to control-detection failures (non-standard UI, LibreOffice). Cross-application tasks about 9 percent. Original WAA paper (arXiv 2409.08264, 2024-09-12): best agent 19.5 percent vs 74.5 percent unassisted human [R]. UFO3's own benchmark NebulaBench: 70.9 percent task success on 55 cross-device tasks [A].
- **Safety features [R, code read]:**
  - `SAFE_GUARD: True` (`config/ufo/system.yaml:27`): a step is held for user confirmation **only when the model itself outputs status `CONFIRM`** (`prompts/share/base/app_agent.yaml:36-51`; `app_agent_processing_strategy.py:1361`; approval snapshot of the whole batch at L1370-1375). This is a model-judged gate: exactly the pattern JARVIS rejects. Approval of a batch cannot authorize a later response, a good detail to copy.
  - Shell allow-list with env-var scrubbing (`app_apis/shell/shell_client.py:19-200`), CWE-22 path validation (`path_validator.py`), SSRF guard (`ufo/utils/url_security.py`).
  - No password-field refusal (grep for IsPassword finds nothing). No kill switch, no take-over concept beyond the confirm prompt.
- **Security record [R]:** advisories `GHSA-24fq-m9rr-g3mm` (critical, 2026-08-10, unauthenticated Mobile MCP), `GHSA-7hrg-r8xr-p8gr` (high, 2026-08-10, IPv6 SSRF bypass), `GHSA-5cjx-4375-4877` (critical, 2026-09-11), `GHSA-3hwr-qx8m-9xxx` (medium), `GHSA-cmq9-vqjf-4q2q` (high, 2026-09-14, `explorer.exe` allow-list bypass gives arbitrary code execution; affected <= 3.0.8), `GHSA-3pgm-xw75-hq27`, `GHSA-85w2-wggf-rw49` (Linux MCP arbitrary file write), `GHSA-6ppj-5886-4f26` (high, 2026-09-22). One report states MSRC assessed the allow-list bypass as "None severity" because UFO already exposes unconstrained automation: i.e. UFO's own threat model treats the agent as fully trusted.
- **Verdict:** study, do not run. Reuse `path_validator.py` and the data/action server split. Telemetry: none found (grep).

### 3.2 CursorTouch/Windows-MCP (already "USE, filtered" in `01_RESEARCH_AUDIT.md`)

- **Status [R]:** MIT (`LICENSE.md`, 2025, Jeomon George), v0.8.8 (2026-10-10), three releases in 15 days, 8,593 stars, 24 open issues, `requires-python >=3.14`, classifiers list Windows 10 and 11. Advisory `GHSA-vrxg-gm77-7q5g` (high, 2026-05-14): SSE and Streamable-HTTP transports had no auth and wildcard CORS in front of the `PowerShell` tool; fixed in 0.7.5; the default stdio transport was never affected. Current README: no CORS headers by default, optional bearer key, IP allow-list, TLS, OAuth, `--tools` / `--exclude-tools`.
- **Tools [R]:** Click, Type, Scroll, Move, Shortcut, Wait, WaitFor, ControlStatus, DisplayInventory, Screenshot, Snapshot, App, PowerShell, FileSystem, Scrape, MultiSelect, MultiEdit, Clipboard, Process, Notification, Registry. The last seven are exactly the ones a guarded adapter must not expose.
- **Mechanics worth knowing [R, code]:**
  - UIA through a vendored fork of yinkaisheng's `uiautomation` (Apache-2.0 files inside an MIT repo, `src/windows_mcp/uia/{core,controls,patterns,enums}.py`, about 13.8k lines) plus its own tree walker with a node budget (`tree/service.py`, `tree/budget.py`). Sets `PerMonitorDpiAware` at import (`uia/core.py:1784`). Captures with dxcam -> mss -> Pillow fallback (`WINDOWS_MCP_SCREENSHOT_BACKEND`).
  - Input through SendInput-based `uia.Click`, `uia.SendKeys`; **coordinate-addressed**, bounds-checked against the virtual desktop because out-of-range points are clamped and "the tool still reports success" (`desktop/service.py:700-720`).
  - `bring_window_to_top` (L586-662): restores minimised windows, attaches the thread input queue to the foreground and target threads, `AllowSetForegroundWindow`, `SetForegroundWindow`; documents that `AttachThreadInput` fails on elevated processes.
  - Typing: per-character SendKeys for short text, clipboard paste above 20 characters because SendKeys "loses keystrokes ... 'hello' rendering as 'tttt'" on a loaded Win11 VM (code comment L767-815; developer anecdote [A]).
  - **Human take-over:** `desktop/control.py` + `control_ledger.py` + `control_win32.py` install low-level mouse and keyboard hooks; a physical mouse move beyond a threshold or `Ctrl+Alt+Shift+Backspace` returns control to the user for 10 s of idleness, a visual edge glow shows AI ownership, injected key-downs are tracked in a ledger and released on fault. The README itself says take-over "is best-effort, not a security boundary", a running PowerShell or App launch is not cancelled, and Windows may silently drop a low-level hook.
- **Telemetry [R]:** PostHog analytics on by default (`ANONYMIZED_TELEMETRY=true`); disable with `ANONYMIZED_TELEMETRY=false` or blank `POSTHOG_API_KEY` (README L673-678; `infrastructure/analytics.py`).
- **Verdict:** best source for Windows-specific hard-won handling. Two ways to use it: (1) read and port the functions in section 0 item 4; (2) as a sandbox-only trial running `windows-mcp serve --tools Screenshot,Snapshot,Click,Type,Shortcut,Wait,ControlStatus` over stdio with telemetry off. Do not put it directly behind a model: its tools take raw coordinates and carry no kernel binding.

### 3.3 Browser Use (browser-use/browser-use)

- **Status [R]:** MIT, 0.13.11 (2026-10-07), 117,564 stars, 522 open issues, Python >=3.11. Not Playwright: it speaks CDP through `cdp-use==1.4.5` (`browser_use/browser/session.py`, watchdog classes for downloads, popups, permissions, security, captcha).
- **Architecture [R]:** `agent/service.py` loop; `dom/service.py` + `dom/serializer` produce an indexed list of interactive elements from DOM + accessibility tree + paint order; `tools/` action registry; `browser/watchdogs/security_watchdog.py` enforces `allowed_domains` / `prohibited_domains` / `block_ip_addresses` (`browser/profile.py:628-636`) **inside the process**, which, like Playwright route hooks, is not a network boundary; `mcp/` can expose itself as an MCP server.
- **Differs from raw Playwright:** adds the LLM loop, element indexing for model consumption, memory/judge, cloud sync; removes deterministic selectors.
- **Dependencies [R]:** 40+ pinned packages including `posthog`, three Google client libraries, `openai`, `anthropic`, `groq`, `ollama`, `mcp==2.1.1`, `browser-use-sdk`, `browser-harness`, and an optional binary `browser-use-core` for the beta agent (provenance UNVERIFIED).
- **Telemetry [R]:** `ANONYMIZED_TELEMETRY` defaults to true (`config.py:58-63`) and `BROWSER_USE_CLOUD_SYNC` defaults to the same value. Disable with `ANONYMIZED_TELEMETRY=false` before import.
- **Reliability numbers:** Browser Use self-reported **89.1 percent on WebVoyager** (GPT-4o, 586 tasks of which 55 removed, WebVoyager's automatic evaluator plus manual review of "unknown" and "failed"; post dated 2024-12-15) [A]. An independent human-evaluated study, "An Illusion of Progress?" (arXiv 2504.01382, COLM 2025, per fetch summary Table 2) puts Browser Use at **30.0 percent on Online-Mind2Web** vs Operator 61.3, Claude Computer Use 3.7 56.3, SeeAct 30.7, Agent-E 28.0 [R]. Treat WebVoyager numbers as marketing, plan for 30 to 60 percent task success on open-web tasks. The repo's README now cites its own "BU Bench v2" (60-task subset), not WebVoyager.
- **Security record [R]:** `GHSA-x39x-9qw5-ghrf` (critical, 2025-05-04): `allowed_domains` bypass via a decoy domain in the URL userinfo; fixed in 0.1.45.
- **Verdict:** do not adopt; the project's own `browser.py` already implements the stronger network-level guard. Worth reading: `dom/serializer` for how it prunes elements, `security_watchdog.py` as a cautionary example.

### 3.4 Playwright and Playwright MCP

- **Playwright [R]:** Apache-2.0, v1.64.0 (2026-10-07) core; Python package 1.63.0 (2026-09-15). Deterministic locators, auto-wait, `ariaSnapshot` accessibility YAML, persistent or isolated contexts, `launch(proxy=...)`. Python 3.14 install tested by the project (`01_RESEARCH_AUDIT.md` row 51).
- **Playwright MCP [R]:** Apache-2.0, v0.0.83 (2026-09-28), source moved into the monorepo (`packages/playwright-core/src/tools/mcp`; the `microsoft/playwright-mcp` repo is a shell, 457 KB), tracks Playwright alpha builds (`1.64.0-alpha-...`). Accessibility-snapshot-first (`browser_snapshot`, refs), `--isolated`, `--proxy-server`, `--secrets` (redacts secret values in tool output), `--allowed-origins` / `--blocked-origins` (README: "does not serve as a security boundary and does not affect redirects"), `--allow-unrestricted-file-access` off by default (file:// blocked, files confined to workspace roots), `--caps vision|pdf|devtools`. README L825: "Playwright MCP is not a security boundary."
- **Fit behind the guard:** both Python API and MCP honour a proxy. The project's own measurement (architecture doc, "Browser guard") that a request hook misses redirect hops matches the README caveat. Therefore keep the order **model -> adapter (element-name expectation, scrub, budget) -> Playwright -> Smokescreen**, and treat origin lists only as an early refusal. Browser snapshots stay untrusted and flip the session's `untrusted_session` flag.
- **Verdict:** keep Playwright (Python) as in `browser.py`; if MCP compatibility is wanted later, run Playwright MCP over stdio with `--isolated --proxy-server http://100.125.91.51:4750 --secrets` and a kernel-owned wrapper; its `snapshot` refs map directly onto the `expect_name` check.

### 3.5 OpenHands

- **Status [R]:** `OpenHands/OpenHands` (MIT, v1.26.0, 2026-10-08, 90,515 stars) is now "Agent Canvas", a TypeScript/Electron control centre that can drive OpenHands, Claude Code, Codex, Gemini, OpenCode or any ACP agent. The Python runtime is `OpenHands/software-agent-sdk` v1.54.0 (2026-10-09, MIT) with four packages: `openhands-sdk`, `openhands-tools`, `openhands-workspace`, `openhands-agent-server`, Python >=3.12, `uv.lock`, `exclude-newer = "7 days"` dependency guard.
- **Licences [R]:** the repo root has one MIT `LICENSE`; the main repo has no `enterprise/` directory any more. Enterprise code lives in `OpenHands/enterprise`, `OpenHands-Cloud` (PolyForm Free Trial License 1.0.0, 2026) and `sandbox-server` (MIT with an `enterprise/` carve-out). Do not vendor from those.
- **Runtime and sandbox [R]:** `openhands-workspace/{docker,apptainer,remote_api,cloud,agent_sandbox}`; `openhands-sdk/openhands/sdk/{workspace,conversation,event,security,hooks,secret}`.
- **Event stream pattern [R]:** typed `ActionEvent` / `ObservationEvent` / `AgentErrorEvent` persisted per conversation (`sdk/event`, `conversation/event_store.py`), `StuckDetector` for repeated action-observation, action-error and monologue loops (`conversation/stuck_detector.py`, 20-event window).
- **Control UI patterns [R]:** `ConfirmationPolicyBase` with `AlwaysConfirm`, `NeverConfirm`, `ConfirmRisky(threshold)` (`sdk/security/confirmation_policy.py`); `SecurityRisk` LOW/MEDIUM/HIGH/UNKNOWN; analyzers: LLM-based risk, deterministic `PatternSecurityAnalyzer` and `PolicyRailSecurityAnalyzer` (regex and composed rules such as fetch-to-exec, `sdk/security/defense_in_depth`), `EnsembleSecurityAnalyzer`.
- **Computer use / browsing [R]:** no desktop computer-use tool in `openhands-tools` (terminal, file_editor, apply_patch, glob, grep, task_tracker, delegate, browser_use, ...). The browser tool wraps the **Browser Use library** (`browser-use>=0.8.0` in `openhands-tools/pyproject.toml`). There is no Windows GUI automation.
- **Advisory:** `GHSA-7h8w-hj9j-8rjw` (high, 2026-03-23, command injection in git diff handler).
- **Verdict:** pattern source, not a dependency. Adopt: typed event log per action (already similar to kernel audit), stuck/repeat detection (already in `Controller.failed`), deterministic pattern analyzers as an additional *deny-only* local filter. Reject: model-assessed risk deciding whether to ask.

### 3.6 Windows-native libraries

| Library | Version / date | Licence | Role | Notes |
|---|---|---|---|---|
| `uiautomation` (yinkaisheng) | 2.0.29, HEAD 2026-06-02, 154 open issues | Apache-2.0 | UIA client over `comtypes`; `Control` tree, patterns (`InvokePattern.Invoke`, `ValuePattern.SetValue`, `WindowPattern.Close`), `GetRuntimeId`, `IsPassword`, `CaptureToImage`, sets PerMonitorDpiAware at import (L2958) | Single 10k-line module. **Ships two prebuilt DLLs** (`bin/UIAutomationClient_VC140_X64.dll`, X86) loaded through `ctypes` for bitmap helpers only (L79-123); review or delete the `bin` folder if screenshots come from `mss`. Best fit to replace the PowerShell snippets. |
| `pywinauto` | 0.6.9 (2025-01-06), HEAD 2026-05-23, 536 open issues | BSD-3-Clause | Win32 and UIA backends, `Application`, keyboard/mouse; **has an experimental AT-SPI backend** (`pywinauto/linux/`) | Heavier object model; UFO pins 0.6.8. Good for Win32-only apps. Slow release cadence. |
| `pywin32` | 312 (PyPI), 2026-06-04 | PSF (PyPI classifier; no licence file in repo root, UNVERIFIED) | Win32 API: `win32gui`, `win32process`, `win32con` | Needed for window enumeration, focus, process info; no UIA. |
| `comtypes` | 1.4.17, 2026-09-21 | MIT-style (file header "OSI Certified"; GitHub reports NOASSERTION) | COM bridge under both `uiautomation` and Windows-MCP | Python 3.14 supported by 1.4.x line (UNVERIFIED for this exact build). |
| `pyautogui` | 0.9.54, 2023-05-24 (repo pushed 2024-08) | BSD-3-Clause | Coordinate mouse/keyboard | Effectively unmaintained; coordinates only; fail-safe corner. Avoid as a primary layer. |
| `mss` | v10.2.0, 2026-04-23 | MIT | Fast cross-platform screenshots (GDI/DXGI) | Pure Python, no deps; recommended capture. |
| `dxcam` | v0.3.0, 2026-03-12 | MIT | DXGI Desktop Duplication, GPU frame grabs | Needed only for high frame rate; fails in some sessions (RDP, no GPU); Windows-MCP falls back to mss. |
| AutoHotkey v2 | v2.0.30, 2026-10-09 | GPL-2.0 | Scripting, hotkeys | Own scripting language executed as code; GPL; not worth it here. |
| Managed `System.Windows.Automation` (current `ui.py`/`winbackend.py`) | in-box | n/a | UIA via PowerShell | Works with no install; spawn cost per call; does not expose newer COM interfaces (IUIAutomation3 to 6); nuance of what it runs on UNVERIFIED. |
| Windows App SDK | n/a | Microsoft redistributable terms (UNVERIFIED) | WinUI/app model SDK | **No external-app automation API**; it is not a control library. UIA3 here means the in-box COM `IUIAutomation` (Windows 7+) that all the above wrap. |

### 3.7 OmniParser (microsoft/OmniParser)

- **Status [R]:** HEAD 3540212 (2026-07-19), no GitHub releases. **Licence inconsistency:** `LICENSE` file is Creative Commons Attribution 4.0 (a content licence, unusual for code) while the README badge says MIT. Treat the code licence as unclear and ask upstream or avoid vendoring.
- **Weights [R per README and HF card, fetched 2026-10-11]:** `icon_detect` (YOLOv8/Ultralytics-based) **AGPL-3.0**; `icon_detect_v3` (YOLOv9-E, added 2026/7 via Hugging Face PR #37) MIT; `icon_caption` (Florence-2 fine-tune) MIT; HF card metadata says "mit" but states licences differ per folder. Whether `icon_detect_v3` is merged into the main model repo is UNVERIFIED (README points at a discussion PR).
- **Dependencies [R]:** `requirements.txt` pins `ultralytics==8.3.70` (AGPL), `torch`, `paddleocr`, `easyocr`, `gradio`, `streamlit`: a very large install for the laptop.
- **Use:** only as a fallback for apps that expose no UIA tree (games, custom-drawn canvases). UFO's paper shows control-detection failure is the dominant failure on non-standard UIs, which is the use case; but it costs a GPU model, an AGPL decision and a grounding error source. Defer.

### 3.8 Anthropic computer-use reference implementation

- **Repos [R]:** `anthropics/claude-quickstarts` (MIT): `computer-use-demo` (Docker, Xvfb/X11, `computer_use_demo/tools/computer.py` with xdotool and gnome-screenshot or scrot, tool versions `computer_20241022`, `20250124`, `20251124`); `computer-toolset` (minimal VNC/RFB driver for `computer_toolset_20260801`, ten tools, "not production code"); `computer-use-best-practices` (macOS only, pyautogui, `sandbox-exec`, trajectories, batch tools; its README states there are **no safeguards** and says to run it in a disposable VM).
- **Docs [R, per fetch of platform.claude.com computer-use page, 2026-10-11]:** current toolset `computer_toolset_20260801` (17 member tools, GA on the Claude API, required for Claude 5.5 and later on the first-party API; earlier models use `computer_20251124`). Screenshot limits: 2576 px long edge / about 3.75 MP for current models, 1568 px / about 1.15 MP for earlier; **coordinates are in the pixel space of the screenshots you return**; the API does not downscale. Recommended 1024x768 or 1280x720. Safety advice: dedicated VM, no credentials, domain allow-list, human confirmation for consequential actions, run the confirmation check before each block of a batch and stop the batch on the first failure. Server-side classifiers scan screenshots for injection but are not a substitute. "Claude may assume an action succeeded without checking": prompt for a screenshot after each step.
- **Fit:** the model-side contract is a good *vocabulary* for the Agent PC (screenshot, click at pixel, type, key, scroll, zoom, wait). Keep the executor ours: `lab/jarvis_mcp.py` already is that executor on xdotool/xwd.

### 3.9 Linux side (Agent PC)

| Tool | Version / licence | How it works under Xvnc in WSL2 | Caveat |
|---|---|---|---|
| `xdotool` | v4.20260303.1 (2026-03-03), BSD-3-Clause | XTEST extension of the X server; Xvnc is an X server so synthetic input is delivered; `search`, `windowactivate`, `type`, `key`, `mousemove`, `click` | `windowactivate`/`getactivewindow` need an EWMH window manager (`_NET_ACTIVE_WINDOW`); the Agent PC runs XFCE so xfwm4 provides it. Without a WM these silently no-op; fallback `windowfocus`. X11 only, not Wayland (fine: Xvnc is X11). Source: xfwm4 issue #634 and suckless threads (search result 2026-10-11). |
| `wmctrl` | GPL-2.0 (UNVERIFIED version) | EWMH client messages | Same WM dependency; use `xdotool` only to avoid a second tool. |
| AT-SPI: `pyatspi` / `gi.repository.Atspi`, `at-spi2-core` | LGPL-2.1, repo active 2026-10-10 | Needs the session D-Bus and `org.a11y.Bus`; project measured: 970 elements read after the desktop, driver and agent shared one systemd user bus (versus 1 before) | Toolkit support varies; Firefox needs accessibility enabled; typing into web fields through AT-SPI left them empty in the project's test. |
| `dogtail` | GPL-2.0-or-later (GitLab, fetched 2026-10-11) | Python scripting over AT-SPI | GPL; release status unclear; read the tree through `Atspi` directly instead. |
| `scrot` | 2.0.0 (2026-03-14) | X11 grab | GitHub licence "NOASSERTION" (UNVERIFIED). |
| `maim` | v5.8.2 (2026-05-28) | X11 grab (imlib2) | licence UNVERIFIED (GPL-3.0 upstream per memory). |
| `xwd` + ImageMagick `convert` | X11 / ImageMagick | what `jarvis_mcp.py` and `lab_control.py` use | Fine; add `-resize` to model limits. |
| RFB (noVNC / VNC protocol) | TigerVNC (project) | Capture and input at the protocol level, as in `computer-toolset/vnc_computer.py` | Quickstart warns `x11vnc` polls and may return a stale frame after an input; Xvnc is not a poller. |

---

## 4. Comparison matrix

| Dimension | UFO2/3 | Windows-MCP | Browser Use | Playwright (+MCP) | OpenHands SDK | uiautomation / pywinauto | OmniParser | Current JARVIS code |
|---|---|---|---|---|---|---|---|---|
| Layer | Agent loop + control layer | MCP server over UIA/SendInput | Agent loop + CDP browser | Browser driver | Agent loop + sandbox | UIA client libraries | Vision grounding model | Gated job executors |
| Windows 10 | Yes | Yes (Python 3.14 only) | Yes (browser only) | Yes | Not GUI | Yes | Yes (GPU advised) | Yes |
| Linux desktop | Shell only | No | Browser only | Browser only | Container shell | pywinauto AT-SPI (experimental) | Model is OS-agnostic | xdotool/xwd |
| Element addressing | UIA control + annotated id | label id or raw coordinates | numeric DOM index | role/name ref, selectors | n/a | AutomationId/Name/RuntimeId | boxes + captions | process\|AutomationId or Name |
| Verification | LLM re-reads next screenshot; `set_text` read-back | none (caller) | LLM judge | auto-wait, assertions | observation events | caller | none | Controller expect (not on Windows path) |
| Permission model | `CONFIRM` chosen by model | tool allow-list flags, no per-action approval | domain lists in-process | origin lists (not a boundary) | risk analyzers + confirm policy | none | none | kernel policy + single-use approvals |
| Human take-over | confirm prompt only | hooks + 10 s idle, best-effort | none | none | pause/resume conversation | none | none | SIGSTOP freeze (Agent PC); kill flag |
| Telemetry default | none found | on (PostHog) | on (PostHog) | none | not checked (UNVERIFIED) | none | none | none |
| Maintenance (2026-10) | active, 87 issues | very active, 24 issues | very active, 522 issues | very active | very active | moderate (2026-06) / slow (2026-05) | stale (2026-07) | n/a |
| Security advisories | 8 in 2026 (2 critical, 3 high, 3 medium) | 1 high (fixed) | 1 critical (fixed) | none | 1 high | none | none | n/a |
| Effort to reuse | high (extract) | low to medium (port functions) | do not adopt | low (already used) | pattern only | low | high | n/a |

## 5. Licence matrix

| Component | Licence | Verified how | Obligations / risk for JARVIS |
|---|---|---|---|
| microsoft/UFO | MIT | `LICENSE`, GitHub | Keep notice when vendoring `path_validator.py`. |
| CursorTouch/Windows-MCP | MIT (repo); vendored `uia/*` is Apache-2.0 files | `LICENSE.md`, file headers | Notice + Apache attribution if copied. |
| browser-use | MIT | `LICENSE`, `pyproject` | Dependency tree includes many third-party licences (not audited). |
| microsoft/playwright, playwright-python, playwright-mcp | Apache-2.0 | GitHub, `package.json` | Already used. |
| OpenHands/OpenHands, software-agent-sdk, OpenHands-CLI | MIT | `LICENSE`, GitHub | No `enterprise/` directory in main repos. |
| OpenHands/enterprise, OpenHands-Cloud | PolyForm Free Trial 1.0.0 | LICENSE fetched via API | Not for production use; do not vendor. |
| OpenHands/sandbox-server | MIT with `enterprise/` carve-out | LICENSE text | Check the carve-out before vendoring. |
| microsoft/OmniParser code | CC-BY-4.0 file vs MIT badge (inconsistent) | `LICENSE`, README | Unclear; ask upstream before copying. |
| OmniParser `icon_detect` v2 weights | AGPL-3.0 | HF card | Network use triggers source obligations; avoid. |
| OmniParser `icon_detect_v3`, `icon_caption` | MIT | README, HF card | Verify weight file availability (UNVERIFIED). |
| anthropics/claude-quickstarts | MIT | `LICENSE` | Reference only. |
| uiautomation | Apache-2.0 | `LICENSE`, GitHub | Includes prebuilt DLLs; review. |
| pywinauto | BSD-3-Clause | `LICENSE`, GitHub | Fine. |
| pywin32 | PSF | PyPI classifier | Fine. |
| comtypes | MIT-style | file header | Fine. |
| pyautogui | BSD-3-Clause | PyPI, GitHub | Fine, but unmaintained. |
| mss | MIT | GitHub | Fine. |
| dxcam | MIT | GitHub | Fine. |
| AutoHotkey | GPL-2.0 | GitHub | Copyleft; avoid embedding. |
| xdotool | BSD-3-Clause | GitHub | Fine. |
| dogtail | GPL-2.0-or-later | GitLab page | Use AT-SPI directly. |
| at-spi2-core | LGPL-2.1 | GitHub | Dynamic linking fine. |
| scrot, maim | unclear on GitHub | NOASSERTION | UNVERIFIED; they are separate executables, not linked. |

---

## 6. Security assessment

### 6.1 Threat model for computer control

1. **Indirect prompt injection via screen, page or window content.** Text that the adapter returns (UIA names, values, page text, OCR, screenshots) is attacker-influenced. Evidence: RedTeamCUA (arXiv 2505.21936, ICLR 2026; per search summary, ASR up to about 50 percent in realistic end-to-end settings and "attempt rate" up to 92.5 percent; versions differ, so confirm in the paper) [R/UNVERIFIED numbers]; OS-Harm (arXiv 2506.14866: injections in notifications and emails were followed in a sample, 50 traces labelled) [R]; Brave's 2025-10-21 post on near-invisible text in screenshots steering Perplexity Comet to read a Gmail one-time password [R, vendor disclosure].
2. **A confused deputy through a trusted tool.** UFO's `explorer.exe` allow-list bypass (GHSA-cmq9-vqjf-4q2q) and Windows-MCP's unauthenticated HTTP PowerShell (GHSA-vrxg-gm77-7q5g) show that tool-level allow-lists and local servers fail open if the call path is reachable from anywhere other than the authorised broker.
3. **Wrong target.** Stale element id, two windows with the same name, a new dialog stealing focus between check and send.
4. **Silent success.** Anthropic's docs and UFO's own paper both describe action steps that "assume success".
5. **Data egress.** Screenshots and UIA text sent to a model leave the machine. In JARVIS this is a lane question (private vs public) already modelled by the kernel.

### 6.2 Patterns observed and how they map to JARVIS

| Pattern | Seen in | Assessment |
|---|---|---|
| Model sets "needs confirmation" | UFO `CONFIRM` | Rejects the JARVIS principle (a model must not authorize). Only keep the *whole-batch snapshot* idea: an approval covers exactly the action hash. JARVIS already does this. |
| Risk classifier (LLM or regex) -> confirm threshold | OpenHands | LLM risk is advisory. A deterministic pattern analyzer can be used only to raise the level or DENY, never to lower it. |
| Tool allow-list | Windows-MCP `--tools`, Playwright `--caps`, UFO shell allow-list | Useful as defence in depth; the authority is the kernel policy table (`policy.py:27-66`). |
| Process / path / host deny lists | pcopen `SENSITIVE`, policy `SENSITIVE_APPS`, Smokescreen, UFO `path_validator` | Keep; add case-insensitive path normalisation. |
| Untrusted-session flag after reading external content, then egress tools removed | JARVIS `Controller.untrusted`, `policy.py:267` | Strongest pattern found anywhere in this survey. Extend it to UIA snapshots and screenshots of windows that show web or document content. |
| Read-back verification | UFO `set_edit_text` (`expected in window_text()`), JARVIS `Controller.do` | Make mandatory for every Windows op. |
| Action budget and repeat guard | JARVIS `Controller`, OpenHands stuck detector, UFO `MAX_STEP: 50` | Already present; port to the adapter. |
| Visible ownership indicator | Windows-MCP edge glow | Cheap and valuable on a shared desktop. UNVERIFIED how it renders on Win10 without composition changes. |
| Human take-over | Windows-MCP hooks; JARVIS SIGSTOP freeze | See 6.4. |
| Kill switch | JARVIS kernel flag, node stops fetching | Add in-flight cancellation (6.5). |

### 6.3 Confirmation design for the adapter

- The kernel shows the owner a **rendered, structured description** built from fields the adapter resolved (process exe path, window title, control type, control name, text length and a hash, destination), not from the model's prose. A model-written justification is displayed separately and labelled as untrusted.
- Approval is bound to `hash(op, process identity, element identity, text)`, single use, expiring, exactly as `02_ARCHITECTURE.md` hardening describes. If the element re-resolves differently at execution (different runtime id or name), the adapter refuses and returns `mismatch` without acting.
- Batches: one approval per action for YELLOW_APPROVAL; a "plan" approval would need a kernel-side list of exact actions; do not allow wildcard targets.

### 6.4 Human take-over semantics

| System | Mechanism | Limits |
|---|---|---|
| JARVIS Agent PC (`lab_control.py`) | SIGSTOP the whole agent process tree and screen drivers, keep GUI windows, freeze new tasks within 2 s, one-time hand-back note | Process-level, robust, but Linux only. Does not stop an action already inside X. |
| Windows-MCP | Low-level hooks detect physical input; AI lease gates tool calls; 10 s idle before AI may resume; ledger releases held keys | "Best-effort, not a security boundary"; hook can be silently removed; cannot cancel running PowerShell. |
| UFO | Confirmation prompt per step only | No mid-run pause. |
| Anthropic quickstarts | None (run in VM) | The docs push isolation instead. |
| OpenHands | Pause/resume conversation state | Not a physical-input model. |

For Windows: implement pause by **kernel flag + cooperative checkpoints between UIA steps**, plus `GetLastInputInfo`-based "owner is active, wait" before any SendInput-class action. The Windows-MCP hook design is the reference if hard input blocking is ever wanted, but it is a heavy, fragile component.

### 6.5 Logging

Log (kernel audit, already hash-chained): requested action hash, approval id, resolved target identity, pre-state digest, post-state digest, verdict (`verified|unverified|mismatch|error`), duration, reason code. Do not log text that was typed or screen text beyond lengths and hashes; return only fixed reason codes as `winbackend._run` already does (L62-68).

---

## 7. Failure modes and evidence

| Failure mode | What happens | Evidence | Tier | Mitigation in adapter |
|---|---|---|---|---|
| Hallucinated completion | Agent reports done without a changed state | Anthropic docs: "Claude may assume an action succeeded without checking"; UFO paper, planning and control errors | R (docs/paper) | Adapter-side read-back, tri-state result; the model never self-reports success. |
| Coordinate drift under DPI scaling | Clicks land offset when process is DPI-unaware or screenshot is scaled | Windows-MCP sets PerMonitorDpiAware at import and exposes `DisplayInventory` (effective DPI, scale); `uiautomation` does the same at import; Anthropic docs: coordinates are in the pixel space of the returned screenshot, scale factor formula given, Retina 2x; UFO has no explicit DPI call (grep) | R | Prefer UIA patterns (no coordinates). Where coordinates are needed, set Per-Monitor-v2 awareness once, capture and click in the same physical pixel space, and bounds-check (Windows-MCP `_validate_screen_point`). |
| Out-of-range point silently clamped | Click lands on edge pixel, tool reports success | Windows-MCP code comment `service.py:700-720` | R (code) | Reject points outside virtual desktop. |
| UAC / secure desktop / elevated windows | Input and UIA calls fail or are blocked; `SendInput` returns success count 0 or silently drops, UIPI | Microsoft docs: `SendInput` subject to UIPI, failure "will not indicate the failure was caused by UIPI"; UIA security overview (updated 2026-02-18): clients at medium IL cannot access elevated UI, and nothing short of SYSTEM-owned UAC desktop reaches the secure desktop | R | Run the node unelevated; detect target integrity level and refuse with reason `elevated-target`; never try UIAccess. A UAC prompt is a human-only step: pause and notify. |
| Focus stealing and foreground lock | `SetForegroundWindow` is refused or only flashes the taskbar; keystrokes go to the wrong app | Microsoft docs list the exact conditions; Windows-MCP `bring_window_to_top` works around with `AttachThreadInput` and notes elevated failure | R | Prefer pattern-based actions that need no focus; for keyboard fallback verify foreground PID immediately before each short chunk and again after; abort on change. |
| Background-window input | `SendKeys`/`SendInput` go to the foreground window, not the target | Same docs; `winbackend.TYPE` foreground check | R | UIA `ValuePattern.SetValue` or `InvokePattern` for background targets; otherwise focus first or fail. |
| Keystroke loss / ordering | Characters dropped on loaded systems; interleaving with user input | Windows-MCP code comment ("hello tttt" on a Win11 VM); Microsoft docs: held keys can interfere, `SendInput` does not reset keyboard state | A (comment) + R (docs) | Clipboard paste for long text with restore, read-back of the field, refuse while physical modifier keys are down. |
| Control detection gap | App exposes no or partial UIA tree | UFO paper (per fetch summary): >62 percent of failures on WAA were control detection, LibreOffice named; Windows-MCP README warns of slow or hung accessibility providers | R/A | Cap traversal by time and node count; report `no-uia-tree`; optional later vision fallback. |
| UIA provider stall | Tree walk blocks the worker | Windows-MCP README line 642 | R | Run UIA in a killable child process with a hard timeout (the current per-call PowerShell has this property). |
| Wrong window of same app | Multiple windows or instances | Code review of `winbackend.FIND` | R | Address by PID + HWND + title from a prior snapshot. |
| Prompt injection through screen text | Injected instruction in a page, email, notification | RedTeamCUA, OS-Harm, Brave (6.1) | R/A | Untrusted flag, no egress tools afterwards, kernel-rendered approvals. |
| Web success rates overstated | WebVoyager 89 percent vs Online-Mind2Web 30 percent for the same agent family | Browser Use blog 2024-12-15 vs arXiv 2504.01382 | R | Plan for human fallback; measure on JARVIS's own tasks. |

Distinguishing anecdote from evidence: code comments and blog posts by the tool authors are "A"; official Microsoft documentation, source read in the pinned commits, advisories with patched-version ranges, and peer-reviewed papers are "R".

---

## 8. Recommended minimal architecture: Windows execution adapter

### 8.1 Principles

1. The kernel is the only authority. The adapter **executes** jobs the kernel already authorised and **refuses** on any local invariant failure; it never widens anything and never asks a model.
2. Fixed job kinds with strict parameter schemas (like `_check_params` in `kernel/jobs.py`). No free-form commands, no PowerShell text from callers, no coordinates from a model unless derived from a snapshot the adapter produced.
3. UIA pattern first, keyboard second, coordinate click last and only with a snapshot-derived point.
4. Every action ends with an adapter-computed verification result. Unknown is a valid answer; "ok" without evidence is not.
5. Observation output is untrusted data, scrubbed, size-capped and lane-bound.

### 8.2 Layout

```
kernel (policy, approvals, audit, kill flag)
   |  job: kind=win_observe | win_act        (new kinds beside ui_action, pc_open)
   v
laptop node agent.py  ->  jobs.run_job
   v
adapter/win_exec.py      validates schema, re-checks local deny rules, owns step budget, checkpoints, cancellation
   |   JSON lines over stdin/stdout, killable child, hard timeout
   v
adapter/win_worker.py    (child process, medium integrity, PerMonitorV2 DPI)
   - window enum + identity: pywin32 (hwnd, pid, exe path, create time, title, class, integrity level)
   - UIA: `uiautomation` (pinned) or current PowerShell snippets in phase 1
   - capture: mss (region/window), downscale to model limits
   - input fallback: SendKeys-class / clipboard paste, with foreground checks
   - path/file: pcopen.resolve + UFO path_validator rules
```

### 8.3 Operation set (maps onto existing policy rows)

| Op | Policy level (today in `policy.py`) | Behaviour | Verification |
|---|---|---|---|
| `observe.windows` | GREEN (`ui list_windows`) | list visible top-level windows: hwnd, pid, exe, title (scrubbed), class, integrity, foreground flag | n/a |
| `observe.tree` | GREEN (`ui snapshot`) | UIA tree of one window by hwnd, caps on depth/nodes/time; each element gets a `ref` = snapshot id + index + (automation id, name, control type, runtime id, rect) | n/a |
| `observe.screenshot` | new row, suggest YELLOW_AUTO for private lane, YELLOW_APPROVAL if the model lane is public | window or region PNG, scaled so that long edge and pixel count fit the model; returns image bytes only to the caller lane | n/a |
| `app.launch` | YELLOW_APPROVAL | from Start list (reuse `pcopen.find_app` + `explorer shell:AppsFolder`) or allow-listed exe; wait for new window by pid | new window with expected exe within timeout |
| `win.focus` | YELLOW_APPROVAL | restore + foreground via Windows-MCP-style `bring_window_to_top`, only on a window from a fresh snapshot | `GetForegroundWindow` == hwnd |
| `win.close` | YELLOW_APPROVAL (new op) | `WindowPattern.Close` only; no `WM_CLOSE` spam, no process kill | hwnd gone or window shows a save/confirm dialog -> `unverified` + snapshot |
| `ui.click` | YELLOW_APPROVAL | `InvokePattern`, then `TogglePattern`/`SelectionItemPattern`/`ExpandCollapse` by control type; coordinate click from the element rectangle only if the element is on-screen and the same ref re-resolves | expectation supplied by caller or default (element state / window title / new window) |
| `ui.type` | YELLOW_APPROVAL | `ValuePattern.SetValue`; else focus + paste/keys; password and secure fields refused (`IsPassword`) | read value back, compare (length and hash; plaintext never returned) |
| `ui.key` | YELLOW_APPROVAL | allow-listed chords only (Enter, Tab, Esc, Ctrl+A/C/V/S/F, arrows, PageUp/Down); no Win key, no Alt+F4, no Ctrl+Alt+Del | foreground unchanged unless expected |
| `ui.scroll` | YELLOW_APPROVAL | `ScrollPattern` first | scroll percent changed |
| `fs.list`, `fs.open`, `fs.save` | existing `pc` and `output` rows | current `pcopen`/`jobs` code + configurable extra roots | existing behaviour |

### 8.4 Identity and resolution rules

- A target is `(hwnd, pid, exe_path, window_create_time)` from a snapshot taken less than N seconds ago, never a process name.
- An element ref re-resolves by `(automation_id, control_type, name)` inside that hwnd; zero or more than one match -> `mismatch` (no action).
- Windows owned by elevated processes, by the node itself, by the browser guard profile, and any title or exe matching `SENSITIVE` are refused at observe time too.
- The expected name must be supplied and equal to the live name, mirroring `browser._element` (L233-243).

### 8.5 Result contract

```
{ "ok": bool,                       # false on refusal or error
  "verdict": "verified|unverified|mismatch|error|refused",
  "reason": "fixed-code",           # no screen text
  "target": {"pid":..., "exe":..., "title_hash":...},
  "evidence": {"before": digest, "after": digest, "changed": ["value","focus"]},
  "snapshot_id": "..." }            # next call must use a ref from a fresh snapshot
```

`Controller.do` already has the control flow (ask -> observe -> act -> observe -> expect, repeat guard, budget). The adapter should reuse that class on Windows, supplying `snap` and `backend` callables, instead of re-implementing it.

### 8.6 Safety rails inside the adapter (defence in depth, never authority)

- Hard deny: SENSITIVE process/title regex (reuse `pcopen.SENSITIVE`), secrets folder, installers/uninstallers, `cmd`, `powershell`, `regedit`, `taskmgr`, `mmc`, UAC consent, Windows Security, Settings > Accounts.
- Budgets: per-session action count, per-action timeout, wall clock; stop on N consecutive failures.
- Cooperative cancellation: worker checks the kernel flag file or a named event between steps; node's report loop sets it on `killed`.
- Owner-activity gate: skip SendInput-class steps while `GetLastInputInfo` shows physical input within the last 2 s (report `owner-active`, do not retry silently).
- No telemetry libraries in the worker environment; `ANONYMIZED_TELEMETRY=false` if any vendored piece reads it.
- Child process environment stripped of secret-looking variables as `jobs._run` already does.

### 8.7 Agent PC counterpart

Same contract, different worker: `lab/jarvis_mcp.py` desktop tools become a kernel-routed `linux_act` kind (or stay lab-local with kernel audit), with:
(1) post-action screenshot digest compare or AT-SPI value read-back; (2) `xdotool getwindowfocus` check before type; (3) resolve windows by id from `xdotool search`, not title substring; (4) keep `lab_control.py` take-over unchanged; (5) AT-SPI tree via `gi.repository.Atspi` on the user session bus for non-web apps; Firefox pages through Playwright with the same guard as `browser.py`.

---

## 9. Ranked implementation list

Effort in focused working days for one developer with the existing tests as a base; ranges include tests and a measured run on the laptop.

| Rank | Item | Value | Effort | Reuse |
|---|---|---|---|---|
| 1 | Expose existing `ui.snapshot` as `win_observe` job + MCP tool; add `list_windows` (pywin32/PowerShell); mark output untrusted; element refs with rect and runtime id | Closes (b); unblocks every other step | 1 to 1.5 | `ui.py`, `Controller.observe`, Windows-MCP `tree/budget.py` idea |
| 2 | Verification on the Windows path: route `ui_action` through `Controller.do`, add per-op read-back (value, window appeared, foreground) and tri-state result; stop reporting `done` without evidence | Fixes the largest correctness gap | 1.5 to 2 | `control.py`, UFO `set_edit_text` check |
| 3 | Identity upgrade: target by hwnd + pid + exe path, refuse ambiguous matches, refuse elevated/own-profile windows, add `focus` and `close` ops, add `UI_OPS` + policy rows + tests | Closes (a) | 2 to 3 | Windows-MCP `bring_window_to_top`, UFO `path_validator` |
| 4 | Persistent worker with `uiautomation` (pinned, `bin` DLLs removed or hashed), JSON-lines protocol, timeouts, cancellation checkpoints | Latency (PowerShell spawn 235 to 343 ms per call, plus tree walk), richer patterns | 3 to 4 | `uiautomation`, Windows-MCP fork as reading material |
| 5 | Screenshot op with `mss`, window/region capture, scaling to model limits, PerMonitorV2 DPI, lane rule (private lane auto, public lane approval) | Closes (d) | 1 to 1.5 | `mss`, Anthropic scaling formula |
| 6 | `ui.key`, `ui.scroll`, clipboard-paste typing with restore, owner-activity gate | Completes (c) | 1.5 to 2 | Windows-MCP `type`/`_paste_text` |
| 7 | Kill and pause: in-flight cancellation via flag/event, owner-activity pause, optional visible "AI is controlling" indicator | Human take-over on Windows | 1.5 to 2 (hooks version: 4 to 5) | Windows-MCP `control.py` design |
| 8 | Browser job kind: `browser_step` (open/snapshot/click/type/select/press) using `browser.py` as is, kernel-routed, untrusted-session propagation | Closes (e) | 2 to 3 | `browser.py`, optionally Playwright MCP behind Smokescreen |
| 9 | Agent PC verification and AT-SPI read: post-action digest, window-id targeting, `Atspi` tree read | Agent PC reliability | 2 to 3 | Existing `jarvis_mcp.py`, `at-spi2-core` |
| 10 | File roots: configurable extra read/write roots with UFO-style validation (reserved names, trailing dots, case folding, 8.3 names), tests | Hardens (f) | 1 | `path_validator.py` |
| 11 | Optional: OmniParser v3 fallback for UIA-blind apps | Coverage | 3 to 5, GPU | `cc-OmniParser`, licence review first |
| 12 | Optional: trial Windows-MCP in a throwaway VM with a tool whitelist, to compare snapshot quality | Benchmark only | 0.5 to 1 | Windows-MCP |

Suggested stop point per `CLAUDE.md` gate rules: after items 1 to 3, test on Notepad, Calculator and one Win32 app, report what passed and what could not be tested (UWP window ownership, elevated apps, high-DPI second monitor), then wait for the go.

---

## 10. What to avoid, and why

| Avoid | Reason |
|---|---|
| Running UFO as the runtime | Model-judged `CONFIRM`, eight advisories in six weeks (shell tool, SSRF, Mobile MCP), pinned old dependencies, no password-field refusal, no kill/take-over, Windows-only. |
| Exposing Windows-MCP's PowerShell, Registry, FileSystem, Process, Clipboard tools; using its HTTP transports | Past unauthenticated-RCE advisory; telemetry default on; raw-coordinate tools; very high release churn (three releases in 15 days). |
| Browser Use as the agent loop | Telemetry default on, 40+ dependencies, in-process domain check, past critical bypass, independent success 30 percent on Online-Mind2Web. |
| Trusting origin allow-lists in Playwright or Browser Use as a boundary | Both projects say they are not a boundary; redirects bypass them. |
| `pyautogui` as the primary layer | Unmaintained since 2023, coordinate only. |
| AutoHotkey, `dogtail` | GPL and a second scripting layer for no gain. |
| OmniParser `icon_detect` v2 weights and `ultralytics` | AGPL-3.0. |
| OpenHands `enterprise` repos | PolyForm Free Trial. |
| Any `xrdp` route | Already removed (EOL with unpatched CVEs). |
| Letting a model write the approval text or choose the risk level | Violates the kernel-only authority rule. |
| Running anything above medium integrity or with UIAccess to reach UAC | Not needed; crosses a deliberate Windows boundary. |

## 11. Open items and UNVERIFIED list

- UFO2 Table 1 figures, UFO and NAVI baseline rows, and RedTeamCUA attack-success numbers came through a fetch summariser; read the papers before quoting.
- Whether UFO's Picture-in-Picture virtual desktop mode is shipped in v3.0.10.
- Whether `icon_detect_v3` weights are in the main OmniParser model repo; the code licence ambiguity (CC-BY-4.0 file vs MIT badge).
- `browser-use-core` binary provenance and licence.
- Telemetry behaviour of OpenHands SDK (not inspected).
- pywin32 licence file (PSF per PyPI only); `maim` and `scrot` licences; comtypes on Python 3.14 for this build.
- UWP main-window ownership (ApplicationFrameHost) and the `calc` launch allow-list entry on this Windows 10 build; not run here.
- Case-insensitive and 8.3 short-name path containment in `pcopen.resolve`; not tested.
- Rendering of a Windows-MCP-style ownership overlay on Windows 10 Pro 19045.
- Whether UFO could be built on Python 3.14 (inferred no).
- `uiautomation` prebuilt DLL provenance (built by the author; not reproducible by us).

## 12. Sources

Repos (all read 2026-10-11): https://github.com/microsoft/UFO, https://github.com/CursorTouch/Windows-MCP, https://github.com/browser-use/browser-use, https://github.com/microsoft/playwright, https://github.com/microsoft/playwright-mcp, https://github.com/microsoft/playwright-python, https://github.com/OpenHands/OpenHands, https://github.com/OpenHands/software-agent-sdk, https://github.com/OpenHands/enterprise, https://github.com/microsoft/OmniParser, https://huggingface.co/microsoft/OmniParser-v2.0, https://github.com/anthropics/claude-quickstarts, https://github.com/pywinauto/pywinauto, https://github.com/yinkaisheng/Python-UIAutomation-for-Windows, https://github.com/mhammond/pywin32, https://github.com/enthought/comtypes, https://github.com/asweigart/pyautogui, https://github.com/BoboTiG/python-mss, https://github.com/ra1nty/DXcam, https://github.com/AutoHotkey/AutoHotkey, https://github.com/jordansissel/xdotool, https://gitlab.com/dogtail/dogtail, https://github.com/GNOME/at-spi2-core.

Advisories: https://github.com/microsoft/UFO/security/advisories (GHSA-cmq9-vqjf-4q2q, GHSA-24fq-m9rr-g3mm, GHSA-5cjx-4375-4877, GHSA-7hrg-r8xr-p8gr, GHSA-85w2-wggf-rw49, GHSA-3pgm-xw75-hq27, GHSA-3hwr-qx8m-9xxx, GHSA-6ppj-5886-4f26); https://github.com/CursorTouch/Windows-MCP/security/advisories/GHSA-vrxg-gm77-7q5g; https://github.com/browser-use/browser-use/security/advisories/GHSA-x39x-9qw5-ghrf; https://github.com/OpenHands/OpenHands/security/advisories/GHSA-7h8w-hj9j-8rjw.

Papers and posts: UFO2 https://arxiv.org/abs/2504.14603 (2025-04-20); UFO3 https://arxiv.org/abs/2511.11332 (2025-11-14, v2 2026-03-01); Windows Agent Arena https://arxiv.org/abs/2409.08264 (2024-09-12); An Illusion of Progress? https://arxiv.org/abs/2504.01382; Browser Use SOTA report https://browser-use.com/posts/sota-technical-report (2024-12-15); RedTeamCUA https://arxiv.org/abs/2505.21936; OS-Harm https://arxiv.org/abs/2506.14866; Brave https://brave.com/blog/unseeable-prompt-injections/ (2025-10-21); OSWorld-Verified aggregators (presenc.ai, benchlm.ai, steel.dev) accessed 2026-10-11, self-reported rows.

Docs: https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool; https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput; https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow; https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-securityoverview (updated 2026-02-18); https://gitlab.xfce.org/xfce/xfwm4/-/issues/634.

Local evidence: `D:\jarvis\research\cc-*` clones (SHAs in section 1); PowerShell cold-start timings measured on this laptop 2026-10-11.
