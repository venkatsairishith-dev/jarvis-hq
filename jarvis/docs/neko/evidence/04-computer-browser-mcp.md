# 04 Evidence: computer use, browser automation and MCP for Neko

Date: 2026-10-16. Author: research subagent (read-only research; this file is the only change).
Scope: category 6 (browser automation and computer-use agents), category 7 (desktop automation on Windows, Linux portability),
category 8 (MCP clients, servers, registries) and "MCP server discovery". Builds on, and does not repeat in full,
`jarvis/docs/research/03-computer-control.md` (2026-10-11) and `jarvis/docs/research/09-connectors.md` (2026-10-11).

Evidence tags:
- **[V]** verified here: primary source read (official doc, source code, GitHub/PyPI/registry API metadata, advisory database) or a read-only local check on this laptop.
- **[C]** claim: README, vendor blog or self-reported number; not reproduced.
- **[U]** unverified: could not be confirmed; needs a test on the laptop or a primary source.

Nothing was installed or executed. Metadata came from `gh api` (public, read-only), PyPI JSON, the MCP registry API and web pages.
Local checks were read-only: installed browser versions, which browser profile is signed in to the owner's account (a match flag only,
no other e-mail printed), presence of extension folders, PowerPoint/LibreOffice/VS Code CLI presence.

---

## 0. Summary (decisions first)

1. **Driving the owner's signed-in Chrome.** The owner's account is signed in to the Chrome **`Default` profile in the default user
   data directory** [V, local `Local State` read, match flag only]. Chrome 155 is installed [V, local]. Since Chrome 136,
   `--remote-debugging-port`/`--remote-debugging-pipe` are **ignored on the default user data dir** [V, Chrome blog 2025-03-17],
   independently re-observed on Chrome 151 [V, trycua/cua#2916]. Playwright's own docs now say automating the default Chrome profile
   "is not supported" [V]. So the classic "launch with a debug port" and "persistent context on the real profile" routes are closed.
   Three sanctioned routes remain, all with a human-visible consent step:
   - **(A, recommended) Playwright MCP extension mode** (`@playwright/mcp --extension`, "Playwright Extension" from the Chrome Web Store,
     Chrome and Edge): connects to tabs of the real profile, a per-connection approval dialog by default, a per-client tab group
     limits which tabs the agent sees, `--profile-dir-name Default` pins the profile [V, README in microsoft/playwright `packages/extension`].
     Not installed yet [V, local].
   - **(B) Chrome's `chrome://inspect/#remote-debugging` toggle (Chrome 144+)** with Chrome DevTools MCP `--autoConnect`: per-request
     consent dialog and an "is being controlled by automated test software" banner [V, Chrome blog 2025-12-11]. Gives full CDP
     (JavaScript evaluation, network, storage) to the client: more power than Neko needs; keep as a debug-only route.
   - **(C) A dedicated automation profile** (`--user-data-dir D:\jarvis\browser\owner-auto`) that the owner signs in to once by hand.
     Works with plain Playwright/CDP and the existing Smokescreen guard, but it is a second Google session, and Google may refuse
     sign-in inside an automation-launched browser [U]. Good fallback, and the right choice for everything that does not need the owner's session.
   - **Avoid** browser-use's real-profile mode: it **copies the profile (cookies included) into `%TEMP%`** (`profile.py` `_copy_profile`) [V, source],
     which leaves session material in a temp folder and, because Chrome binds cookie encryption to the data directory, may not even keep the login [U].
2. **Always verify the account before acting**: read the signed-in e-mail from the page the agent will use (for example the Google account
   chip on `myaccount.google.com`), compare with the configured owner account, refuse on mismatch. Do not infer it from the profile name.
3. **Playing a song and verifying playback.** Prefer: official search API (YouTube Data API `search.list`, needs the owner's API key, 100 quota units per call [C])
   -> open the watch URL in the owner's browser -> **verify with Windows System Media Transport Controls (SMTC)**
   (`GlobalSystemMediaTransportControlsSessionManager`, Windows 10 1809+ [V, Microsoft Learn]; Python binding `winrt-Windows.Media.Control` 3.2.1, MIT, cp314 wheels [V, PyPI]):
   source app = chrome, `PlaybackStatus == Playing`, title contains the expected title, and the timeline position advancing over ~3 s.
   When a DOM channel is open (route A), cross-check `video.paused === false` and `currentTime` increasing; YouTube's player state 1 = playing [V, IFrame API docs].
   `navigator.mediaSession.playbackState` is **set by the page** and is not authoritative [V, MDN]. Autoplay with sound needs a user gesture or
   high Media Engagement Index [V, Chrome autoplay policy]. **Owner decision needed:** YouTube's Terms forbid accessing the service "using any automated
   means" without written permission [V, YouTube ToS 2022-01-05]; opening a URL and reading OS media state is lower risk than clicking inside YouTube by automation.
4. **Desktop layer for Windows**: reuse the JARVIS node (pywin32 window list, UIA snapshot, `pc_open`) and grow it per `03-computer-control.md` section 8
   with `uiautomation` (Apache-2.0) and `mss` (MIT); OS-specific "drivers" behind one small interface (section 9). Screenshot computer use
   (N.E.K.O `brain/computer_use.py`, pyautogui) is the **last resort only** and must be routed through kernel approval: its AST allowlist permits
   `hotkey`/`write`, i.e. arbitrary keystrokes (Win+R, then any command) [V, source].
5. **MCP**: current spec **2026-07-28** (stateless, `server/discover`, MRTR replaces server-initiated sampling/elicitation; Roots, Sampling, Logging deprecated) [V].
   Official registry is **still "preview"**, permissive moderation, "assume minimal-to-no moderation" [V]. Reference servers left in
   `modelcontextprotocol/servers`: everything, fetch, filesystem, git, memory, sequentialthinking, time (release v1.0.0, 2026-10-11) [V];
   brave-search, github, puppeteer, gdrive, slack, sqlite, postgres, redis and others are in the **archived** repo [V].
6. **Minimal MCP set for Neko** (section 10): time (have), Playwright MCP in extension mode (wrapped), filesystem (read-only, outputs root), fetch (via the
   kernel's remote/SSRF path), GitHub (`--read-only`), one search server (SearXNG self-hosted or Brave with key), all behind the existing kernel
   connector policy (owner classifies each tool; fingerprint pinning; approvals bound to arguments). **Do not add**: Desktop Commander (the model can
   rewrite its own `allowedDirectories`/`blockedCommands` via `set_config_value` [V, README]), Windows-MCP's PowerShell/Registry/FileSystem tools,
   mcp-server-git write tools (5 advisories 2025-12 to 2026-02 [V]), any third-party gateway as a second authority.

---

## 1. What the existing stack already has (local read)

| Item | Finding | Tag / source |
|---|---|---|
| N.E.K.O browser agent | `brain/browser_use_adapter.py` uses `browser-use==0.11.13` (`requirements.txt` L79) and `playwright==1.63.0` (L364); creates `BrowserSession(headless=..., keep_alive=..., executable_path=chrome)` (L497-505), i.e. browser-use's **own default profile**, not the owner's | [V] local source |
| N.E.K.O computer use | `brain/computer_use.py`: one VLM call per step with a screenshot, the model writes pyautogui code; `_sanitize_generated_code` (L408-473) allows only literal-argument calls to `click, doubleClick, rightClick, moveTo, dragTo, scroll, write, typewrite, press, keyDown, keyUp, hotkey, tripleClick, mouseDown, mouseUp, hscroll, position` + `time.sleep`, then `exec` (L1432). Good AST hygiene, but keystroke-level freedom means any OS action is reachable | [V] local source |
| N.E.K.O availability endpoints | `app/agent_server/api_routes.py` manages `computer_use_enabled` / `browser_use_enabled` flags and readiness (L357-454); main_agent plugin probes them | [V] local source |
| N.E.K.O MCP client | `plugin/plugins/mcp_adapter`: own JSON-RPC client, stdio/SSE/streamable-HTTP, sends `initialize` with `protocolVersion: "2024-11-05"` (`__init__.py` L386-403, L468-511), spawns `command + args` from its config (L366-374); no per-tool owner classification or argument-bound approval found by grep | [V] local source |
| N.E.K.O -> JARVIS door | `plugin/plugins/jarvis_bridge`: `delegate_task`, `task_result`, `pending_approvals`, `connector_overview`; "Approvals for risky actions are given in JARVIS (console or kernel), never through N.E.K.O." | [V] README |
| JARVIS kernel jobs | `jarvis/kernel/jarvis_kernel/jobs.py` KINDS: `project_check, output_save, output_show, ui_action, pc_open, lab_run, ui_windows, ui_snapshot, screenshot, connector_op`; `UI_OPS={launch,click,type}`; `PC_OPS={open_app,open_folder,open_file,list_folder}` | [V] L17-33 |
| JARVIS policy | `policy.py`: browser `open/snapshot/extract` GREEN, `click/type/select/press` YELLOW_APPROVAL, `download/upload` RED; ui `snapshot/list_windows` GREEN, `screenshot` YELLOW_AUTO, `launch/focus/click/type/key/scroll` YELLOW_APPROVAL; pc ops YELLOW_AUTO (raised for public lane); connector `call` YELLOW_AUTO then owner class decides; DENY for password manager/wallet/banking UI | [V] L21-70, L253-267 |
| JARVIS node | `jarvis/node/jarvis_node/`: `windows.py` (Win32 window list, `wait_for`, PNG screenshot), `pcopen.py` (known folders, Start-menu apps, SENSITIVE regex), `browser.py` (Playwright + Smokescreen, throwaway profile), `mcpclient.py`/`connectors.py` (kernel-governed MCP) | [V] file list |
| Connector governance | Kernel holds tool decisions, SHA-256 fingerprint of each tool declaration (changed tool switches off), argument-bound single-use approvals, results labelled untrusted, sampling/elicitation/roots refused | [V] `09-connectors.md` section 3 |

**Gap that matters for Neko:** two parallel paths exist. N.E.K.O's own `browser_use`, `computer_use` and `mcp_adapter` act **without** the JARVIS kernel;
`jarvis_bridge` is the governed path. For any action on the owner's real accounts or desktop, the N.E.K.O-native paths must be disabled or routed through the kernel (section 11).

Local machine facts [V, read-only]: Chrome 155.0.8059.40 and Edge 155.0.4283.45 installed; Chrome profiles `Default`, `Profile 1, 2, 4, 5, 6`, all signed in;
**only `Default` matches the owner account and it is the last-used profile**; Edge `Default` is not signed in. Playwright Extension and Nanobrowser are **not** installed.
No `DevToolsActivePort` file (remote debugging is off). VS Code CLI at `%LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code.cmd`. **No PowerPoint and no LibreOffice** installed. D: free 20 GB.

---

## 2. Category 6: browser automation and computer-use agents

Metadata via GitHub API on 2026-10-16 [V] unless stated.

| Project | URL | Licence | Latest release (date) | OS | Key deps | Needs | Main security notes | Verdict for Neko |
|---|---|---|---|---|---|---|---|---|
| Playwright (Python) | https://github.com/microsoft/playwright-python | Apache-2.0 | PyPI 1.64.0 (2026-10-13) | Win/macOS/Linux | Node driver bundled | none offline | Default Chrome profile automation "not supported"; `connect_over_cdp` "significantly lower fidelity", Chromium only [V docs] | **Keep** (already in JARVIS `browser.py`) |
| Playwright MCP | https://github.com/microsoft/playwright-mcp | Apache-2.0 | v0.0.83 (2026-09-28) | Win/macOS/Linux | Node, npx | none | "**not** a security boundary"; `--allowed-origins` "does not serve as a security boundary and does not affect redirects"; `--secrets` redacts values in tool output; extension mode token `PLAYWRIGHT_MCP_EXTENSION_TOKEN` bypasses the approval dialog [V README] | **Adopt** for route A, wrapped by kernel |
| Chrome DevTools MCP | https://github.com/ChromeDevTools/chrome-devtools-mcp | Apache-2.0 | v1.11.0 (2026-10-15) | Win/macOS/Linux; "officially supports Google Chrome and Chrome for Testing only" | Node, Puppeteer | none (CrUX calls optional) | **Usage statistics on by default** (`--no-usage-statistics`); trace URLs to CrUX (`--no-performance-crux`); update checks; `--autoConnect` (Chrome 144+); advisories CVE-2026-53765, CVE-2026-53766 (2026-06) [V] | Debug-only; not default |
| browser-use | https://github.com/browser-use/browser-use | MIT | 0.13.11 (2026-10-07); N.E.K.O pins 0.11.13 | Win/macOS/Linux | CDP (`cdp-use`), 40+ deps, posthog | LLM | Telemetry default on; real-profile mode copies profile to temp; CVE-2025-47241 allowlist bypass (fixed 0.1.45) [V] | Keep only as N.E.K.O's sandboxed web agent on its own profile; not for owner accounts |
| Skyvern | https://github.com/Skyvern-AI/skyvern | **AGPL-3.0** | v1.0.56 (2026-10-14) | Win needs C++ build tools (README) | Playwright, SQLite/Postgres, UI server | LLM | Can attach to your Chrome via `chrome://inspect/#remote-debugging` + `BROWSER_TYPE=cdp-connect` [C README]; credential vault integrations; cloud offers CAPTCHA solvers (we must not) | Reject (AGPL, heavy server, overlaps) |
| Stagehand | https://github.com/browserbase/stagehand | MIT | TS 3.7.3 (2026-08-28); PyPI `stagehand` 4.2.1 (2026-10-10) | Win/macOS/Linux | Node or Python, LLM | LLM; Browserbase optional | `act/observe/extract` primitives; "observe() returns real selectors, so credentials never reach the model" [C] | Pattern source (observe-then-act); not needed |
| Steel browser | https://github.com/steel-dev/steel-browser | Apache-2.0 | v0.5.4-beta (2026-08-25) | Docker | Puppeteer, CDP | Docker | Ships "stealth plugins and fingerprint management" and proxy rotation [C] (anti-detection; conflicts with never-bypass rule) | Reject |
| Nanobrowser | https://github.com/nanobrowser/nanobrowser | Apache-2.0 | v0.3.0 (2026-10-15) | Chrome, Edge (extension) | `chrome.debugger` API, Puppeteer | own API key or local model | Runs inside the real profile; sends daily anonymous usage counts [C README/PRIVACY]; its own agent loop decides actions (no kernel) | Reject as runtime; proves extension approach works in Edge |
| UI-TARS-desktop / Agent TARS | https://github.com/bytedance/UI-TARS-desktop | Apache-2.0 | v0.3.1 (2026-10-08) | Windows/macOS/browser [C] | Electron, VLM | UI-TARS / Seed models (cloud or self-hosted) | "Remote Computer/Browser Operator ... completely free" = vendor-hosted machines [C]; screenshot-only grounding | Reject as runtime; model optional later |
| UI-TARS model | https://huggingface.co/ByteDance-Seed/UI-TARS-1.5-7B | apache-2.0 (HF card) | 2025-04-18 | any (GPU) | vLLM etc. | 7B VLM | Screenshot grounding errors | Optional grounding model for UIA-blind apps |
| OmniParser | https://github.com/microsoft/OmniParser | repo **CC-BY-4.0** (GitHub API), HF card MIT; v2 icon_detect AGPL | v.2.0.1 (2025-09-12) | any (GPU) | torch, ultralytics, paddleocr | GPU | Licence mix (see 03 doc section 3.7) | Defer |
| Open Interpreter | https://github.com/openinterpreter/openinterpreter | Apache-2.0 | rust-v0.0.56 (2026-10-07) | Win/macOS/Linux | Rust | LLM | **Repo is now a Codex-fork coding agent**; Python `open-interpreter` (with OS mode) last on PyPI 0.4.3, 2024-10-26 [V] | OS mode is abandoned: reject |
| Microsoft UFO2/UFO3 | https://github.com/microsoft/UFO | MIT | v3.0.10 (2026-09-22) | Windows | pywinauto 0.6.8 pins, Py 3.10/3.11 | LLM | 8 advisories 2026-08..09; model-chosen `CONFIRM` gate (03 doc 3.1) | Study only |
| Anthropic computer-use reference | https://github.com/anthropics/claude-quickstarts | MIT | n/a | Linux container | xdotool | Claude API | Docs: dedicated VM, no credentials, confirm consequential actions (03 doc 3.8) | Vocabulary for screenshot fallback |

Notes:
- Independent web-agent success is far below vendor claims (Browser Use 30.0 % on Online-Mind2Web vs 89.1 % self-reported WebVoyager; 03 doc section 3.3) [V/C]. Design for verification and human fallback, not for autonomy.
- browser-use profile copy, exact code: `_copy_profile()` runs in `model_post_init`, `shutil.copytree(<user_data_dir>/<profile_directory>, tempfile.mkdtemp(prefix='browser-use-user-data-dir-'))` plus `Local State`; on locked files it tells the user to close Chrome or use `--cdp-url`. Only applies when the path or channel looks like Chrome, so an Edge profile path would be used **in place** [V, `browser_use/browser/profile.py` L849-899 on main, 2026-10-16].

---

## 3. KEY QUESTION: driving the owner's existing signed-in Chrome/Edge profile safely (2026)

### 3.1 What changed and what still works

| Route | Status in 2026 | Evidence |
|---|---|---|
| `chrome --remote-debugging-port=N` on the default user data dir | **Ignored since Chrome 136** (also `--remote-debugging-pipe`). Reason: cookie theft via debugging after App-Bound Encryption; a non-default dir uses a different encryption key | [V] https://developer.chrome.com/blog/remote-debugging-port (2025-03-17) |
| Same flag + non-default `--user-data-dir` | Works (re-tested on Chrome 151: port listens, `/json/version` answers) | [V] https://github.com/trycua/cua/issues/2916 (2026-08-05) |
| Playwright `launch_persistent_context(user_data_dir=<real Chrome dir>)` | "Due to recent Chrome policy changes, automating the default Chrome user profile is not supported ... may result in pages not loading or the browser exiting" | [V] https://playwright.dev/docs/api/class-browsertype |
| `chrome://inspect/#remote-debugging` toggle (Chrome 144+) | Off by default; when on, each new debugging client triggers a **permission dialog**; banner "Chrome is being controlled by automated test software" while connected. Toggle-enabled browsers write `DevToolsActivePort` (WebSocket path) but **404 the `/json/*` discovery endpoints** | [V] https://developer.chrome.com/blog/chrome-devtools-mcp-debug-your-browser-session (2025-12-11); [V] cua#2916 |
| Chrome DevTools MCP `--autoConnect` | Connects to a running Chrome 144+ for the channel's user data dir after the toggle; also `--browserUrl`, `--wsEndpoint`, `--isolated`, `--user-data-dir` (default `~/.cache/chrome-devtools-mcp/chrome-profile`) | [V] `docs/configuration.md` in the repo |
| Playwright MCP `--extension` + Playwright Extension | Connects to the **existing** Chrome/Edge profile through an extension; first use opens a tab-picker page; each connection needs approval unless `PLAYWRIGHT_MCP_EXTENSION_TOKEN` is configured; multiple clients each get their own colour-coded tab group and see only those tabs; `--profile-dir-name "Default"` selects the profile | [V] https://github.com/microsoft/playwright/tree/main/packages/extension (README) and playwright-mcp README `--extension` row ("Edge/Chrome only") |
| browser-use "real browser" (`Browser.from_system_chrome(profile_directory=...)`) | Copies the profile into `%TEMP%`; docs say "You may need to fully close Chrome" | [V] source; [V] https://docs.browser-use.com/open-source/customize/browser/real-browser.md |
| Skyvern "control your own browser" | Uses the same `chrome://inspect` toggle + CDP connect | [C] Skyvern README |
| Own extension (Nanobrowser style, `chrome.debugger`) | Works in Chrome and Edge; would be custom code; Reuse-first rule says use Playwright's extension instead | [C] Nanobrowser README |
| Edge | Playwright MCP extension mode explicitly supports Edge [V]. Whether Edge enforces the Chrome 136 debug-port rule is **not documented by Microsoft** (third-party reports say yes) [U]. Edge `Default` here is not signed in [V local], so Edge is not the owner-account browser today | sources above |

### 3.2 Recommendation (and why)

1. **Route A: Playwright MCP in extension mode, owner's Chrome `Default` profile.**
   - Owner installs "Playwright Extension" (Web Store id `mmlmfjhmonkocbjadbfplnigmagldckm`) in the `Default` profile (owner-only step, Windows).
   - Run `@playwright/mcp --extension --profile-dir-name Default` as a **kernel connector on the laptop node** (stdio), pinned version, no `PLAYWRIGHT_MCP_EXTENSION_TOKEN` at first (keep Chrome's per-connection approval as a second human gate). Only consider the token later; it is a credential and goes in `D:\jarvis-secrets\connectors\` by reference.
   - Agent sees only tabs in its tab group: start every task by opening a **new** tab, never touch existing owner tabs.
   - Why A over B: A exposes accessibility-snapshot tools with refs (fits the kernel's `expect_name` check), scopes the agent to a tab group, and keeps JavaScript evaluation out unless `--caps` adds it. B (CDP via toggle) grants the whole browser (all tabs, cookies via `Network`/`Storage` domains) to any client that wins the consent dialog.
   - Limits: Playwright MCP is not a security boundary; origin lists are not a boundary; a malicious page can still inject prompts. Hence: kernel approval per write action, untrusted-session flag after reading page content (already in JARVIS), no download/upload (RED), deny banking/password sites (already DENY).
2. **Route C for everything that does not need the owner's session** (research, scraping public pages, N.E.K.O's own browser agent): non-default `--user-data-dir` on D:, through the Smokescreen egress guard as `browser.py` does.
3. **Never**: copy the real profile; launch the default profile with a debug port; read cookies/Login Data; automate Google sign-in, CAPTCHA or 2-step prompts (stop and hand to the owner).

### 3.3 Verify the active account (do not assume)

| Check | How | Tag |
|---|---|---|
| Which local profile is the owner's | `Local State` -> `profile.info_cache[*].user_name` compared to the configured account, key-only read, result = match flag. Done here: `Default` matches | [V] local |
| Which Google account the **web session** uses | After attaching, open a new tab to `https://myaccount.google.com/` and read the account e-mail from the accessibility snapshot; refuse if it differs from the configured owner account or if more than one account is signed in and the active one is not the owner's | [U] (page structure not tested; read-only, no automation of sign-in) |
| YouTube session | On `youtube.com`, the avatar/account menu snapshot; use only as a second check | [U] |
| Result handling | Record "account verified: yes/no" in the kernel audit; never log the e-mail of other accounts | design |

---

## 4. Playing a song and verifying playback

| Step | Preferred mechanism | Fallback | Tag / source |
|---|---|---|---|
| Find the song | YouTube Data API v3 `search.list` with the owner's API key (official API; 100 quota units per search, default 10,000 units/day) | Accessibility snapshot of YouTube search results in route A | [C] https://developers.google.com/youtube/v3/determine_quota_cost |
| Start playback | Open `https://www.youtube.com/watch?v=<id>` in the owner's browser (route A new tab, or OS `pc_open` of a URL) | Click the player's play button (kernel YELLOW_APPROVAL), or SMTC `TryPlayAsync` on the session | IFrame API: autoplay may be blocked for "unmuted playback without user interaction" [V] https://developers.google.com/youtube/iframe_api_reference; Chrome autoplay policy (MEI, user gesture) [V] https://developer.chrome.com/blog/autoplay |
| Verify (OS level, browser-agnostic) | **SMTC**: `GlobalSystemMediaTransportControlsSessionManager.RequestAsync()` -> `GetSessions()`/`GetCurrentSession()` -> `SourceAppUserModelId` contains chrome, `GetPlaybackInfo().PlaybackStatus == Playing`, `TryGetMediaPropertiesAsync().Title` matches, `GetTimelineProperties().Position` advances over 3 s | n/a | [V] https://learn.microsoft.com/en-us/uwp/api/windows.media.control.globalsystemmediatransportcontrolssessionmanager (Windows 10 1809+, capability `globalMediaControl` listed for packaged apps; unpackaged Python use [U] on this laptop). Chrome feeds SMTC on Windows [V] Chromium commit https://chromium.googlesource.com/chromium/src/+/53bfe24384b08243df09c19c825d663cb00882f5%5E%21/ |
| Verify (DOM level, route A only) | `document.querySelector('video')`: `paused === false`, `currentTime` increases, `readyState >= 3`, not muted / volume > 0; YouTube `getPlayerState()` 1 = playing, 2 = paused, 3 = buffering | requires JS evaluation (`--caps` or a read-only snapshot field); keep read-only | [V] IFrame API states; `paused`/`currentTime` are standard HTMLMediaElement |
| Not authoritative | `navigator.mediaSession.playbackState` is written by the page | - | [V] https://developer.mozilla.org/en-US/docs/Web/API/MediaSession/playbackState |
| Audio actually audible | Optional: Windows Core Audio session peak meter for chrome.exe (`IAudioMeterInformation`) | - | [U] not researched in depth |
| Linux equivalent | MPRIS over D-Bus (`playerctl status/metadata`), Chromium exposes MPRIS | - | [C] https://specifications.freedesktop.org/mpris-spec/latest/ ; playerctl LGPL-3.0, last push 2024-06-13 [V] |

Result contract: `verified` only if SMTC (or MPRIS) says Playing **and** title matches **and** position advances; `unverified` if playing but title unknown; `mismatch` if another track/app is playing.
Spotify is not installed [V local]; its Web API playback control needs **Premium** and scope `user-modify-playback-state` [V] https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback.

Policy note: YouTube Terms of Service: "access the Service using any automated means (such as robots, botnets or scrapers)" is not allowed except search engines or with prior written permission [V] https://www.youtube.com/static?template=terms (effective 2022-01-05). Owner decision: prefer API search + human-equivalent URL open + OS-level verification; keep in-page clicking as an approved fallback.

---

## 5. Category 7: desktop automation on Windows (and portability)

| Component | URL | Licence | Latest (date) | OS | Permissions | Risk notes | Verdict |
|---|---|---|---|---|---|---|---|
| Microsoft UI Automation (COM `IUIAutomation`) | https://learn.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32 | in-box | Windows 7+ | Windows | medium IL; cannot reach elevated UI or secure desktop (UIPI) | Provider stalls, partial trees (03 doc section 7) | **Core** |
| `uiautomation` (yinkaisheng) | https://github.com/yinkaisheng/Python-UIAutomation-for-Windows | Apache-2.0 | PyPI 2.0.29 (2025-08-05) | Windows | as UIA | Ships prebuilt DLLs (03 doc 3.6) | Adopt for the persistent worker |
| pywinauto | https://github.com/pywinauto/pywinauto | BSD-3-Clause | 0.6.9 (2025-01-06), pushed 2026-10-15 | Windows (+experimental AT-SPI) | as UIA/Win32 | Slow releases | Optional for Win32-only apps |
| Windows-MCP (CursorTouch) | https://github.com/CursorTouch/Windows-MCP | MIT | v0.8.8 (2026-10-10); registry entry says 1.0.1 (mismatch) | Windows, Py >= 3.14 | full user session | CVE-2026-48989 HTTP transports unauthenticated PowerShell (fixed 0.7.5); telemetry default on; PowerShell/Registry/FileSystem/Process/Clipboard tools | Read and port functions (03 doc); do not expose |
| Microsoft UFO2 | https://github.com/microsoft/UFO | MIT | v3.0.10 (2026-09-22) | Windows | full | 8 advisories in 2026 | Study only |
| AutoHotkey v2 | https://github.com/AutoHotkey/AutoHotkey | GPL-2.0 | v2.0.30 (2026-10-09) | Windows | full | Second scripting language; GPL | Reject |
| pyautogui | https://github.com/asweigart/pyautogui | BSD-3-Clause | 0.9.54 (2023-05-24) | Win/macOS/Linux | full input | Unmaintained; coordinates only (used by N.E.K.O computer_use) | Last resort only |
| mss | https://github.com/BoboTiG/python-mss | MIT | 10.2.0 (2026-04-23) | Win/macOS/Linux | screen read | - | Adopt for screenshots |
| pywinrt (`winrt-Windows.Media.Control`) | https://github.com/pywinrt/pywinrt | MIT | 3.2.1 (2025-06-06), cp310-cp314 wheels | Windows | read media sessions | - | Adopt for playback verification |
| VS Code CLI | https://code.visualstudio.com/docs/configure/command-line | MIT (OSS) / MS licence (product) | installed | Win/macOS/Linux | launches editor | Opening a folder can run workspace tasks/extensions if the owner trusts it (Workspace Trust) [U for this setup] | **Use**: `code -n <folder>`, `code -r -g file:line`, or `vscode://file/<path>` |
| Shell selection (Explorer) | `Shell.Application.Windows()` -> `Document.SelectedItems()` | in-box | - | Windows | read | Only Explorer windows; returns paths | Use for "organise selected files" [C] https://learn.microsoft.com/en-us/windows/win32/shell/shellfolderview-selecteditems |
| reveal.js (slides from generated content) | https://github.com/hakimel/reveal.js | MIT | 6.0.2 (2026-09-10) | any browser | none | - | Slideshow path while no PowerPoint/LibreOffice is installed |
| xdotool / AT-SPI (Linux) | https://github.com/jordansissel/xdotool ; https://github.com/GNOME/at-spi2-core | BSD-3 / LGPL-2.1 | pushed 2026-09-20 / 2026-10-10 | X11 Linux | X session | X11 only | Linux driver (Agent PC) |
| ydotool (Wayland) | https://github.com/ReimuNotMoe/ydotool | **AGPL-3.0** | pushed 2025-12-22 | Linux | uinput (root or group) | AGPL, root-level input | Avoid; prefer AT-SPI/portals |

Task mapping (owner's requirements):

| Requirement | Layer chosen | Kernel kind / level today | Verification |
|---|---|---|---|
| Open a website | Route A new tab (owner session needed) or OS default-browser open (`pc_open` URL op, new) | browser `open` GREEN; new `pc open_url` suggest YELLOW_AUTO for allow-listed hosts | tab URL/title in snapshot; window title |
| Use signed-in Google account | Route A + account check (3.3) | browser click/type YELLOW_APPROVAL | account e-mail equals owner account |
| Search and play a song | Section 4 | browser open GREEN; play click YELLOW_APPROVAL | SMTC Playing + title + position |
| Open VS Code project | `code -n <folder>` via node (folder inside allowed roots) | new `pc open_project`, YELLOW_AUTO | new window with `Code.exe` and folder name in title (`windows.wait_for`) |
| Open generated files | existing `output_show` / `pc_open open_file` | YELLOW_AUTO | window appears (`_verify_open`) |
| Organise selected files | read Explorer selection (Shell COM) -> plan (exact list of moves/renames inside allowed roots, no overwrite, no delete) -> owner approves the exact plan -> execute with an undo journal | new `fs organise`, **YELLOW_APPROVAL**; delete stays RED | re-list folders, compare with plan |
| Start a slideshow | Google Slides "present" in route A for a Slides deck [U URL form], or generate reveal.js HTML into outputs and open it full screen; Windows Photos slideshow for images [U on 19045] | output_show YELLOW_AUTO; browser key press YELLOW_APPROVAL | window is full-screen (window rect equals monitor rect) |

---

## 6. Category 8: MCP specification, registry, servers

### 6.1 Specification

| Fact | Tag / source |
|---|---|
| Current revision **2026-07-28** (GitHub release tag 2026-07-28, published 2026-07-28); previous 2025-11-25 | [V] https://modelcontextprotocol.io/specification/2026-07-28/changelog ; gh release |
| Stateless: no `initialize` handshake, no `Mcp-Session-Id`; version and client capabilities in `_meta` of every request; `server/discover` MUST be implemented by servers | [V] changelog items 1-3 |
| Multi Round-Trip Requests: servers return `resultType: "input_required"` instead of sending `sampling/createMessage`, `elicitation/create`, `roots/list`; `resultType` required on results | [V] items 7-8 |
| Tasks moved to extension `io.modelcontextprotocol/tasks`; `subscriptions/listen` replaces GET stream; SSE resumability removed | [V] items 4, 6, 9 |
| Deprecated: Roots, Sampling, Logging; HTTP+SSE transport; Dynamic Client Registration in favour of Client ID Metadata Documents | [V] Deprecated 1-4 |
| Auth: clients MUST validate `iss` (RFC 9207); credentials keyed by issuer; tool schemas may use any JSON Schema 2020-12 keywords | [V] minor 7-10 |
| Twelve-month minimum deprecation window | [V] governance |

Impact: JARVIS's client already probes `server/discover` and falls back to `initialize` and refuses server-initiated requests (`09-connectors.md`) [V]. N.E.K.O's `mcp_adapter` speaks only `2024-11-05` [V]; another reason to route through the kernel client.

### 6.2 Official registry

| Fact | Tag / source |
|---|---|
| `registry.modelcontextprotocol.io`, repo https://github.com/modelcontextprotocol/registry, release v1.8.1 (2026-08-06) | [V] |
| Status: "**currently in preview**. Breaking changes or data resets may occur before general availability"; API freeze v0.1 since 2025-10-24 | [V] README and docs `faq.mdx` |
| Moderation: "quite permissive ... only remove illegal content, malware, spam, and completely broken servers"; "consumers should assume minimal-to-no moderation"; will **not** remove servers with security vulnerabilities | [V] `moderation-policy.mdx` |
| Namespaces: `io.github.<user>/*` via GitHub auth (org namespace only for org Owners), reverse-DNS via DNS TXT or HTTP proof | [V] `authentication.mdx` |
| Subregistries (GitHub MCP Registry, Docker MCP Catalog, PulseMCP, Smithery) are expected to curate | [V] `docs/design/ecosystem-vision.md` |
| API: `GET /v0.1/servers?search=<text>&version=latest&limit=N` returns server.json entries with `packages` (npm/pypi/oci) and `repository.url` | [V] live query 2026-10-16 |
| **Observed impersonation risk**: search "chrome-devtools" returns `io.github.secforge/chrome-devtools-mcp` (npm `@secforge/chrome-devtools-mcp`) whose `repository.url` claims the official `ChromeDevTools/chrome-devtools-mcp` repo; plus two other look-alikes | [V] live query |
| **Observed metadata drift**: registry lists `io.github.CursorTouch/Windows-MCP` 1.0.1 while PyPI/GitHub latest is 0.8.8; Playwright MCP 0.0.82 vs GitHub 0.0.83 | [V] live query vs PyPI/GitHub |

### 6.3 Reference servers (modelcontextprotocol/servers)

Repo pushed 2026-10-13, release **v1.0.0 (2026-10-11)**: npm `server-everything`, `server-filesystem`, `server-memory`, `server-sequential-thinking` 1.0.0; PyPI `mcp-server-fetch`, `mcp-server-git`, `mcp-server-time` 2026.10.10 [V].

| Server | Status | Notes for Neko | Tag |
|---|---|---|---|
| filesystem | Active | Allowed dirs by CLI args **or Roots; Roots "completely replace" server-side allowed directories** (Roots is deprecated in 2026-07-28; JARVIS refuses roots, so CLI args hold). Advisories CVE-2025-53109 (symlink bypass), CVE-2025-53110 (prefix collision), both high, 2025-07-01, fixed | [V] README; advisories |
| fetch | Active | README: by default refuses private/loopback/link-local/metadata IPs and checks each redirect; `--allow-private-ips` disables; no DNS-rebinding protection between check and connect; obeys robots.txt for model-initiated requests | [V] README |
| git | Active | **5 advisories** (CVE-2025-68143/68144/68145, 2025-12-17; CVE-2026-27735, 2026-02-25): path traversal, argument injection, arbitrary-location `git_init` | [V] advisories |
| memory | Active | JSONL knowledge graph, file lock; overlaps the vault/kernel memory (vault is the memory) | [V] README |
| time | Active | Already tested live as JARVIS's first connector | [V] 09 doc |
| sequentialthinking | Active | Pure prompt scaffold; no value with current models | [V] |
| everything | Active | Test server only | [V] |
| **Archived** (`modelcontextprotocol/servers-archived`, archived, last push 2025-05-28): aws-kb-retrieval, brave-search, everart, gdrive, git (old copy), github, gitlab, google-maps, postgres, puppeteer, redis, sentry, slack, sqlite | Archived | Use vendor-maintained successors (e.g. `github/github-mcp-server`, `brave/brave-search-mcp-server`) | [V] gh API |

### 6.4 Other servers asked about

| Server | URL | Licence | Latest | Needs | Risks | Verdict |
|---|---|---|---|---|---|---|
| GitHub MCP server | https://github.com/github/github-mcp-server | MIT | v2.0.2 (2026-10-08) | GitHub PAT (fine-grained, read-only) | Indirect prompt injection through issues reached private repos (Invariant Labs, 2025-05) [C]; advisories CVE-2026-48529 (medium), CVE-2026-47427 (high DoS) [V]; `--read-only` and `--toolsets` limit surface [V 09 doc] | Template (owner token), read-only |
| Desktop Commander | https://github.com/wonderwhy-er/DesktopCommanderMCP | MIT | v0.3.0 (2026-10-13) | Node | Terminal + file edit; "not a sandbox"; `allowedDirectories` "terminal commands can still access files outside these directories"; **`set_config_value` lets the model change `blockedCommands`/`allowedDirectories`/`telemetryEnabled`**; installer via `curl | bash` / `iex` | [V] README | **Reject** |
| Brave Search MCP | https://github.com/brave/brave-search-mcp-server | MIT | v2.1.4 (2026-09-17) | Brave API key | Queries leave to Brave | [V] metadata | Option (key) |
| Exa MCP | https://github.com/exa-labs/exa-mcp-server | MIT | tag v0.x, pushed 2026-10-07 | Exa API key, hosted | Hosted vendor | [V] metadata | Option |
| Tavily MCP | https://github.com/tavily-ai/tavily-mcp | MIT | pushed 2026-10-15 | Tavily key | Hosted vendor | [V] metadata | Option |
| SearXNG MCP | https://github.com/ihor-sokoliuk/mcp-searxng | MIT | v2.6.0 (2026-10-13) | a SearXNG instance (self-host) | No vendor key; instance choice matters | [V] metadata | **Preferred** if a SearXNG instance runs on the VM |
| Playwright MCP / Chrome DevTools MCP / Windows-MCP | see sections 2 and 5 | | | | | |

### 6.5 Gateways and proxies

| Project | URL | Licence | Latest | What it does | Security record | Verdict |
|---|---|---|---|---|---|---|
| Docker MCP Gateway (`docker mcp`) | https://github.com/docker/mcp-gateway | MIT | tag v0.44.1; Docker Desktop 4.59+ | Runs catalog servers in containers, secrets via Docker Desktop, profiles, OAuth, call logging; declares itself "a security boundary" with documented trust assumptions | **10 advisories**, e.g. CVE-2026-76092 critical (unauthenticated proxied tools, container mode), CVE-2026-76099 critical (config-driven bind mount), CVE-2026-76094 high ("image signature verification off by default"), CVE-2026-76097 tool-name shadowing, GHSA-qg99-jrvf-j9fh case-variant tool names (2026-10-13), CVE-2025-64443 DNS rebinding | [V] `docs/security.md`, advisories | Not adopted: second authority + Docker Desktop on the laptop; borrow ideas (containerise risky servers, signature checks, reserved names case-insensitively) |
| sparfenyuk/mcp-proxy | https://github.com/sparfenyuk/mcp-proxy | MIT | v0.13.0 (2026-10-10) | stdio <-> SSE/streamable-HTTP bridge | none published | [V] | Only if a remote client must reach a stdio server; bind loopback + token |
| TBXark/mcp-proxy | https://github.com/TBXark/mcp-proxy | MIT | v1.1.0 (2026-09-15) | aggregates many servers behind one HTTP endpoint | - | [V] metadata | Not needed |
| hoophq/mcpproxy | (09 doc) | MIT | 2026-08 | allow/deny, approvals, rug-pull fingerprints | - | Ideas already adopted in kernel [V 09 doc] |
| snyk/agent-scan (was invariantlabs `mcp-scan`) | https://github.com/snyk/agent-scan | Apache-2.0 | v0.6.8 (2026-09-29) | scans MCP configs/tool descriptions for poisoning | sends descriptions to a scanning API by default [U] | Optional owner-run check before enabling a server |
| MCP Inspector | https://github.com/modelcontextprotocol/inspector | per-package | 2.11.0 (2026-10-14) | debugging client | CVE-2025-49596 critical (2025-06-13), CVE-2025-58444 high (2025-09-06) | [V] | Dev only, never left running |

### 6.6 Known MCP security issues and what JARVIS does about them

| Issue | Evidence | JARVIS/Neko control |
|---|---|---|
| Tool poisoning (hidden instructions in tool descriptions) | Invariant Labs, 2025-04-01 [V] https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks | Owner reviews description; description shown as untrusted; names validated; class from tool NAME only [V 09 doc] |
| Rug pull (description changes after approval) | same source [V] | SHA-256 fingerprint of name+description+schema; change switches the tool off [V 09 doc] |
| Cross-server shadowing / name collisions | same source; Docker gateway CVE-2026-76097 and case-variant GHSA (2026-10-13) [V] | Per-connector namespacing; compare names case-insensitively (add if missing [U]) |
| Indirect prompt injection via tool results | GitHub MCP case (Invariant 2025-05) [C]; screen/page injection (03 doc 6.1) | Results labelled untrusted, size-capped; untrusted-session flag removes egress tools [V] |
| Malicious package (supply chain) | `postmark-mcp` 1.0.16 BCC'd every e-mail to the attacker (2025-09) [C] https://snyk.io/blog/malicious-mcp-server-on-npm-postmark-mcp-harvests-emails/ ; registry look-alikes (6.2) [V] | Pin exact versions/hashes; prefer vendor org repos; owner-only install; no `npx @latest` |
| Client-side command injection from server-supplied URLs | `mcp-remote` CVE-2025-6514 critical (2025-07-09) [V GHSA-6xpm-ggf7-wc3p]; spec section "OAuth Authorization URL Validation" [V] | Kernel never shells out to open server URLs; https-only remote endpoints |
| Confused deputy (OAuth proxy, static client id, consent cookie) | spec Security Best Practices [V] https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices | JARVIS does not act as an OAuth proxy for others; per-connector secrets by reference |
| Token passthrough | spec: servers "MUST NOT accept any tokens that were not explicitly issued for the MCP server" [V] | Bearer token sent only to its own endpoint [V 09 doc] |
| SSRF during OAuth discovery / fetch | spec SSRF section; Docker gateway CVE-2026-76093 [V] | Address-pinned, private ranges blocked, no redirects [V 09 doc] |
| Local server exposure (HTTP on localhost, DNS rebinding) | spec "Local MCP Server Compromise": use stdio or token; Windows-MCP CVE-2026-48989; Docker CVE-2025-64443 [V] | stdio only for local servers |
| One-click install consent | spec: client MUST show the exact command without truncation [V] | Console-only allowlist file; exact command shown [V 09 doc] |

---

## 7. MCP server discovery (how Neko should find and admit servers)

1. **Discovery is an owner activity, never an agent tool.** An agent may *suggest* a server name; it may not search, install or enable.
2. **Sources in order of trust**: (a) the vendor's own GitHub org (e.g. `github/`, `microsoft/`, `ChromeDevTools/`, `brave/`); (b) the official registry API filtered to namespaces that match that org (`io.github.<org>/...`) and whose `packages[].identifier` matches the vendor's published package; (c) curated subregistries (GitHub MCP Registry, Docker MCP Catalog) as hints only. The registry repository field is self-declared (look-alike found, section 6.2) [V].
3. **Admission checklist** (owner, in the JARVIS console "Add connector" flow that already exists [V 09 doc]): licence; latest release date and advisories (`gh api repos/<r>/security-advisories`); telemetry default and opt-out flag; transport (stdio only for local); required secrets (by reference in `D:\jarvis-secrets\connectors\`); exact pinned version and, for npm/PyPI, the integrity hash; tool list fetched with "Test connection"; each tool classified read/write/send by the owner; write tools off until needed.
4. **Re-check**: on every connect the kernel compares tool fingerprints (existing); monthly owner review of advisories for enabled servers.
5. Registry polling by the kernel is optional; if added, read-only `GET /v0.1/servers?search=...&version=latest` through the SSRF-checked https path, results shown as untrusted metadata.

---

## 8. Recommended automation layer stack (Windows, with an OS abstraction)

Preference order (from the brief): official APIs > DOM/accessibility > OS accessibility (UIA) > screenshot computer use.

```
Neko (voice/chat) --jarvis_bridge.delegate_task--> JARVIS console --> kernel (policy, approvals, audit, kill switch)
                                                                        |
                         +----------------------------------------------+-------------------------------------+
                         |                          |                                 |                       |
                 L1 official APIs          L2 browser DOM/a11y               L3 OS accessibility       L4 screenshot (last)
          (YouTube Data API, GitHub,   Route A: Playwright MCP --extension   node "desktop driver":   N.E.K.O computer_use or
           Google Workspace connectors)  (owner Chrome, Default profile)       UIA + Win32 + Shell COM   Anthropic toolset vocabulary,
                                       Route C: Playwright + Smokescreen     + SMTC media + mss         only with per-step approval
                                         (automation profile on D:)          Linux: AT-SPI + xdotool
                                                                               + MPRIS
```

OS abstraction (thin, in the node; one interface, two drivers):

| Interface method | Windows driver | Linux driver | Notes |
|---|---|---|---|
| `list_windows()` | pywin32 `EnumWindows` (exists, `windows.py`) | `xdotool search` / EWMH | identity = (hwnd/xid, pid, exe, create time) |
| `snapshot(window)` | UIA tree (`ui.py`, later `uiautomation`) | AT-SPI (`gi.repository.Atspi`) | untrusted text, caps |
| `act(ref, op, expect)` | UIA patterns, then SendInput fallback | AT-SPI actions, then xdotool | verified/unverified/mismatch (03 doc 8.5) |
| `open(kind, target)` | `pc_open`, `code`, `explorer`, URL open via ShellExecute (no shell) | `xdg-open`, `code` | allow-listed roots and apps |
| `media_state()` | SMTC (pywinrt) | MPRIS (D-Bus) | play verification |
| `selected_files()` | Shell COM `SelectedItems()` | file manager specific [U] | read only |
| `screenshot(region)` | mss | mss / xwd | lane rule |
| `browser` | Playwright (route A via MCP, route C via Python) | same | cross-platform already |

Reuse vs custom: everything above is existing libraries or existing JARVIS node code; custom code is only the adapter glue and verification, as already justified in `03-computer-control.md` section 8 (no maintained project offers kernel-bound approvals with read-back verification). Record any new custom piece in `docs/01_RESEARCH_AUDIT.md` per CLAUDE.md.

---

## 9. Minimal MCP server set for Neko, with permission tier per tool

Tiers map to kernel levels: GREEN (no prompt), YELLOW_AUTO (logged, runs), YELLOW_APPROVAL (owner approves exact arguments), RED (console only), DENY.

| Server (pinned) | Transport / node | Tools to enable | Tier | Tools kept OFF |
|---|---|---|---|---|
| `mcp-server-time` 2026.10.10 | stdio, laptop | `get_current_time`, `convert_time` | GREEN | - |
| `@playwright/mcp` 0.0.83, `--extension --profile-dir-name Default` (no extension token initially) | stdio, laptop | `browser_tabs` (list/new only), `browser_navigate`, `browser_snapshot`, `browser_wait_for`, `browser_take_screenshot` (private lane) | GREEN for snapshot/wait; navigate YELLOW_AUTO to allow-listed hosts else YELLOW_APPROVAL | `browser_click`, `browser_type`, `browser_fill_form`, `browser_press_key`, `browser_select_option`, `browser_handle_dialog` -> **YELLOW_APPROVAL** each; `browser_evaluate`, `browser_run_code_unsafe`, `browser_file_upload`, all `browser_cookie_*`, `browser_localstorage_*`, `browser_sessionstorage_*`, `browser_storage_state`/`browser_set_storage_state`, `browser_route*`, `browser_network_*`, `browser_mouse_*_xy` (vision caps) -> **DENY** (do not enable `--caps devtools/storage/network/config/vision`); `browser_close` on owner tabs DENY. Tool names checked against the playwright-mcp README tool list 2026-10-16 [V] |
| `@modelcontextprotocol/server-filesystem` 1.0.0, args `D:\jarvis\outputs` | stdio, laptop | `read_text_file`, `list_directory`, `search_files`, `get_file_info` | GREEN (private lane) | `write_file`, `edit_file`, `create_directory`, `move_file` -> use kernel `output_save` / new `fs organise` job instead (YELLOW_APPROVAL) |
| `mcp-server-fetch` 2026.10.10 (default private-IP guard on) | stdio on the VM or kernel-side | `fetch` | YELLOW_AUTO, result untrusted; sets untrusted-session flag | `--allow-private-ips` never |
| `github-mcp-server` v2.0.2 `--read-only --toolsets repos,issues,pull_requests` | stdio, laptop (owner PAT by reference) | read tools | GREEN/YELLOW_AUTO (private lane only) | all write tools (not loaded in read-only mode) |
| Search: `mcp-searxng` v2.6.0 (self-hosted instance) or `brave-search-mcp-server` v2.1.4 | stdio | `search` | YELLOW_AUTO (query leaves the machine; public lane allowed) | - |
| Optional later: Chrome DevTools MCP 1.11.0 `--autoConnect --slim --no-usage-statistics --no-performance-crux` | stdio | snapshot/read tools | debug sessions only, owner-started | `evaluate_script` DENY (`--no-javascript-evaluation` equivalent), performance/CrUX off |

Not in the set: memory (vault is the memory), sequentialthinking, git server (advisory history; use kernel `git` policy rows), Desktop Commander, Windows-MCP, Docker gateway, browser-use MCP mode.

Desktop actions (open VS Code, open files, organise files, slideshow, media verification) stay **kernel job kinds on the node** (not MCP), because the kernel already owns schema checks, approvals and verification for jobs; MCP adds no value there.

---

## 10. Wiring into JARVIS approvals

1. **Single entry point.** Neko hands tasks to JARVIS with `jarvis_bridge.delegate_task` [V]. In N.E.K.O settings, keep `computer_use_enabled` and `browser_use_enabled` **off** for owner-account work, and do not configure owner-account servers in N.E.K.O's own `mcp_adapter` (it has no argument-bound approvals and an old protocol) [V]. If N.E.K.O's browser agent stays on, it uses its own profile only (route C).
2. **Browser via connector.** Register Playwright MCP (extension mode) as a kernel connector (`09-connectors.md` section 4 format), lanes `["private"]`, classify tools as in section 9; changed tool declarations switch off automatically [V 09 doc].
3. **Policy rows to add** (proposal; `policy.py`): `("pc", "open_url|open_project", YELLOW_AUTO)`, `("fs", "organise", YELLOW_APPROVAL)`, `("media", "state", GREEN)`, `("media", "play|pause", YELLOW_APPROVAL)`, `("browser", "account_check", GREEN)`; keep `browser download|upload` RED and the password/wallet/banking DENY.
4. **Approval content** is rendered by the kernel from resolved facts (tab URL, element role+name from the snapshot, text length/hash, account verified yes/no), never from model prose (03 doc 6.3). Approval is single-use and bound to the action hash; a re-resolved element that differs -> `mismatch`, no action.
5. **Human gates kept in the browser**: Chrome's own connection approval (extension) stays on; never automate Google sign-in, CAPTCHA, 2-step verification, payment, or permission prompts: the job returns `needs_owner` and Neko speaks a short prompt.
6. **Verification before "done"**: song -> SMTC verdict; VS Code -> window with folder title; files -> re-listed plan diff; slideshow -> full-screen window. Neko speaks the verdict from the kernel result, not from the agent's claim.
7. **Kill switch and take-over**: existing kernel kill flag stops new jobs; add cooperative cancellation between steps and the owner-activity gate before any input injection (03 doc 8.6).
8. **Audit**: action hash, approval id, account-verified flag, verdict; no page text, no e-mails other than the match flag.

Owner-only steps (Windows, no reboot needed): install Playwright Extension in Chrome `Default`; decide on YouTube automation vs API+URL (ToS); create a YouTube Data API key file in `D:\jarvis-secrets\connectors\` if the API path is chosen; optionally install SearXNG on the VM.

---

## 11. Open items and UNVERIFIED list

- Edge: whether Microsoft enforces the Chrome 136 debug-port rule (no Microsoft doc found); Edge `Default` not signed in, so not blocking.
- Copied-profile cookie decryption under App-Bound Encryption (browser-use path) not tested; recommendation is to avoid it anyway.
- SMTC from an unpackaged Python process on Windows 10 19045 with Chrome 155: not run here (no execution allowed in this task). First test of the phase.
- Account-chip reading on `myaccount.google.com` and the Google Slides "present" URL form: not tested.
- Google sign-in inside an automation-launched browser (route C) may be blocked by Google: not tested.
- Windows Photos slideshow command line on 19045; VS Code Workspace Trust behaviour for agent-opened folders.
- Registry version drift (Windows-MCP 1.0.1 in registry vs 0.8.8 on PyPI/GitHub): cause unknown.
- YouTube Data API quota numbers came from the docs URL cited, not re-read in full.
- GitHub MCP prompt-injection case and postmark-mcp details are vendor/security-firm reports [C].

## 12. Sources

Local (read-only): `D:\jarvis\hq-neko\requirements.txt`, `pyproject.toml`, `brain\browser_use_adapter.py`, `brain\computer_use.py`, `app\agent_server\api_routes.py`, `plugin\plugins\mcp_adapter\*`, `plugin\plugins\jarvis_bridge\README.md`, `jarvis\kernel\jarvis_kernel\{jobs,policy}.py`, `jarvis\node\jarvis_node\*`, `jarvis\docs\research\03-computer-control.md`, `jarvis\docs\research\09-connectors.md`; Chrome/Edge install folders and `Local State` (match flag only).

Browser and profile: https://developer.chrome.com/blog/remote-debugging-port ; https://developer.chrome.com/blog/chrome-devtools-mcp-debug-your-browser-session ; https://github.com/trycua/cua/issues/2916 ; https://playwright.dev/docs/api/class-browsertype ; https://github.com/microsoft/playwright-mcp (README) ; https://github.com/microsoft/playwright/tree/main/packages/extension ; https://github.com/ChromeDevTools/chrome-devtools-mcp (README, docs/configuration.md) ; https://docs.browser-use.com/open-source/customize/browser/real-browser.md ; https://github.com/browser-use/browser-use/blob/main/browser_use/browser/profile.py ; https://github.com/Skyvern-AI/skyvern ; https://github.com/browserbase/stagehand ; https://github.com/steel-dev/steel-browser ; https://github.com/nanobrowser/nanobrowser ; https://github.com/bytedance/UI-TARS-desktop ; https://huggingface.co/ByteDance-Seed/UI-TARS-1.5-7B ; https://github.com/microsoft/OmniParser ; https://huggingface.co/microsoft/OmniParser-v2.0 ; https://github.com/openinterpreter/openinterpreter ; https://pypi.org/project/open-interpreter/ ; https://github.com/microsoft/UFO ; https://github.com/CursorTouch/Windows-MCP.

Media: https://learn.microsoft.com/en-us/uwp/api/windows.media.control.globalsystemmediatransportcontrolssessionmanager ; https://pypi.org/project/winrt-Windows.Media.Control/ ; https://github.com/pywinrt/pywinrt ; https://chromium.googlesource.com/chromium/src/+/53bfe24384b08243df09c19c825d663cb00882f5%5E%21/ ; https://developer.chrome.com/blog/autoplay ; https://developers.google.com/youtube/iframe_api_reference ; https://developers.google.com/youtube/v3/determine_quota_cost ; https://developer.mozilla.org/en-US/docs/Web/API/MediaSession/playbackState ; https://www.youtube.com/static?template=terms ; https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback ; https://specifications.freedesktop.org/mpris-spec/latest/ ; https://github.com/altdesktop/playerctl.

Desktop: https://learn.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32 ; https://learn.microsoft.com/en-us/windows/win32/shell/shellfolderview-selecteditems ; https://code.visualstudio.com/docs/configure/command-line ; https://github.com/yinkaisheng/Python-UIAutomation-for-Windows ; https://github.com/pywinauto/pywinauto ; https://github.com/AutoHotkey/AutoHotkey ; https://github.com/asweigart/pyautogui ; https://github.com/BoboTiG/python-mss ; https://github.com/hakimel/reveal.js ; https://github.com/jordansissel/xdotool ; https://github.com/GNOME/at-spi2-core ; https://github.com/ReimuNotMoe/ydotool.

MCP: https://modelcontextprotocol.io/specification/2026-07-28/changelog ; https://github.com/modelcontextprotocol/modelcontextprotocol (release 2026-07-28) ; https://github.com/modelcontextprotocol/registry (README, docs/modelcontextprotocol-io/{faq,moderation-policy,authentication}.mdx, docs/design/ecosystem-vision.md) ; https://registry.modelcontextprotocol.io/v0.1/servers ; https://github.com/modelcontextprotocol/servers (release v1.0.0, src/*/README.md) ; https://github.com/modelcontextprotocol/servers-archived ; https://github.com/modelcontextprotocol/servers/security/advisories ; https://github.com/modelcontextprotocol/inspector/security/advisories ; https://github.com/github/github-mcp-server ; https://github.com/wonderwhy-er/DesktopCommanderMCP ; https://github.com/brave/brave-search-mcp-server ; https://github.com/exa-labs/exa-mcp-server ; https://github.com/tavily-ai/tavily-mcp ; https://github.com/ihor-sokoliuk/mcp-searxng ; https://github.com/docker/mcp-gateway (docs/security.md, security advisories) ; https://github.com/sparfenyuk/mcp-proxy ; https://github.com/TBXark/mcp-proxy ; https://github.com/snyk/agent-scan ; https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices ; https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks ; https://github.com/advisories/GHSA-6xpm-ggf7-wc3p ; https://snyk.io/blog/malicious-mcp-server-on-npm-postmark-mcp-harvests-emails/ ; https://labs.cloudsecurityalliance.org/research/csa-research-note-mcp-security-crisis-20260504-csa-styled/ (secondary, CVE list not re-verified).
