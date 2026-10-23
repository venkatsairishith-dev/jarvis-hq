# Computer & Browser Control for JARVIS (Windows 10 PC): Computer-Use Agents, Safe Admin Elevation, NL File Finding/Organising, Browser Agents

Research date: 2026-10-22. Scope: options for giving JARVIS (kernel with GREEN/PIN/RED/DENY policy tiers, Windows node with UIA + Win32 backend, Playwright layer behind a filtering egress proxy, Playwright MCP extension on the owner's signed-in Chrome profiles, N.E.K.O browser-use/computer-use) broad PC power safely. Tagging used below: **[verified]** = read on a primary source this session; **[secondary]** = only third-party/aggregator; **[background, not fetched]** = from prior knowledge, not re-checked this session, so confirm before relying on it.

---

## Q1. Windows computer-use agents and tools in 2026 (capabilities, licenses, maturity, benchmarks)

### Takeaway
The practical, MIT-licensed building blocks for a Windows 10 desktop agent are **Windows-MCP** (CursorTouch; UIA-based tools including PowerShell, FileSystem, Registry, Process; Win10/11; it ships with explicit "full system access" warnings and had a 2026 security advisory), and **Microsoft UFO²** (LTS, hybrid GUI + API, MIT). UFO³ Galaxy is still in active development. Frontier models now score above the human baseline (~72%) on OSWorld-Verified, but most of those numbers are vendor self-reports on different harnesses. Windows-specific benchmark numbers (WindowsAgentArena) remain far lower in published papers. Microsoft's OS-level "agent connectors"/On-Device Registry are **Windows 11 only**, so they do not help a Windows 10 box.

### Cited Findings
**Windows-MCP (CursorTouch)**
- License MIT. Supports Windows 10 and 11. The current install requires Python 3.14+. Tools: Click, Type, Scroll, Move/drag, Shortcut, MultiSelect, MultiEdit, Wait, WaitFor, ControlStatus, DisplayInventory, Screenshot, Snapshot (full UI state with element IDs), App, **PowerShell, FileSystem, Process, Registry**, Clipboard, Notification, Scrape. Transports: stdio (default), SSE, streamable-HTTP. Auth options are Bearer key, IP allowlist, optional TLS, and OAuth 2.0 + PKCE. Claimed latency is 0.2–0.5 s between actions, and the README claims "2M+ users" in Claude Desktop Extensions (no methodology given). [verified] — [GitHub CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP)
- README warnings: "Windows-MCP operates with full system access and can perform irreversible operations". User takeover is "best-effort, not a security boundary". PowerShell/App launches may keep running after takeover, and the control gate does not undo completed side effects. [verified] — [GitHub CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP)
- Security advisory GHSA-vrxg-gm77-7q5g: a CORS/DNS-rebinding flaw meant HTTP modes could expose an **unauthenticated PowerShell tool**. It was fixed in v0.7.5. v0.8.5 (dated "01 Aug", year not shown on the page, presumably 2026) stopped writing to stdout, which had been corrupting stdio JSON-RPC, and added tree-capture size limits and multi-monitor fixes. [secondary; version sources conflict: x-cmd says 0.8.2, Skillselion says PyPI 0.8.1] — [releasealert CVE list](https://releasealert.dev/github/CursorTouch/Windows-MCP/cves), [GitHub releases](https://github.com/CursorTouch/Windows-MCP/releases), [x-cmd](https://www.x-cmd.com/install/windows-mcp/)

**Microsoft UFO² / UFO³ Galaxy**
- UFO² is labelled "STABLE & BATTLE-TESTED", "LTS (Long-Term Support)", and actively maintained, with no deprecation plans. It is a hybrid of "GUI clicks + API calls". Speculative multi-action "Batch predictions → 51% fewer LLM calls". MIT License. UFO² v2.0.0 was released April 2025. "2025-11 – UFO³ Galaxy Framework Released". Galaxy is in "Active Development" across Windows, Linux and Android (paper arXiv 2511.11332). The README gives no benchmark scores inline. [verified] — [GitHub microsoft/UFO](https://github.com/microsoft/UFO)

**Benchmarks**
- WindowsAgentArena (Microsoft, ICML 2025): 150+ Windows tasks adapted from OSWorld, parallelizable in Azure (~20 min full run). Best agent Navi scored **19.5%** against **74.5%** for an unassisted human. [verified abstract] — [arXiv 2409.08264](https://arxiv.org/abs/2409.08264), [PMLR v267](https://proceedings.mlr.press/v267/bonatti25a.html)
- A third-party leaderboard notes that precise markup (UIA tree + OmniParser) boosts scores more than any single pixel detector. [secondary] — [benchmarklist WAA](https://benchmarklist.com/benchmarks/windows_agent_arena/)
- OSWorld-Verified (Oct 2026, aggregator data). One aggregator lists Qwen3.8 Max at 86.1% (self-reported by Alibaba), then Claude Fable 5 and Claude Mythos 5 at ~85%. Steel.dev lists Claude Mythos Preview at 85.4%. Earlier verified runs from May 2026: Claude Opus 4.7 at 83.6% and Sonnet 4.6 at 81.5%, against a human baseline of ~72.4%. Another page claims an "Intelligence-Indeed Agent" at 90.19%. OSWorld 2.0 is much harder: BenchLeader lists Claude Opus 5 at 31.4% (7 Oct 2026), but another tracker gives conflicting "partial score" figures. **Sources conflict, and many are vendor self-reports on revised harnesses; confirm on os-world.github.io before quoting.** [secondary] — [Steel.dev OSWorld](https://leaderboard.steel.dev/leaderboards/osworld/), [BenchLM OSWorld-Verified](https://benchlm.ai/benchmarks/osworld-verified), [BenchLeader OSWorld 2.0](https://www.benchleader.com/benchmarks/osworld_2), [presenc.ai](https://presenc.ai/research/osworld-computer-use-leaderboard-2026)

**cua (trycua)**
- The SDK, Python/TS/Swift/Kotlin packages, the `cua` CLI, Cua Driver and Lume are MIT. The "Spaces" desktop app (including Windows NSIS/MSI builds) is source-available under **FSL-1.1-MIT**. It positions itself as open-source drivers + cross-OS sandboxes + benchmarks. [secondary via search summary of repo] — [GitHub trycua/cua](https://github.com/trycua/cua)

**Microsoft OS-level agent features**
- Windows **On-Device Registry (ODR)** of "agent connectors" (= MCP servers), in public preview since Ignite Nov 2025. Launch connectors are File Explorer (manage local files with consent) and System Settings. Connectors run "contained in a secure environment with their own identity and audit trail". Calls go through a proxy that handles authN, authZ and auditing. [verified via Microsoft blog summary] — [Windows Developer Blog, Ignite 2025](https://blogs.windows.com/windowsdeveloper/2025/11/18/ignite-2025-furthering-windows-as-the-premier-platform-for-developers-governed-by-security), [developer.microsoft.com/windows/agentic](https://developer.microsoft.com/pt-br/windows/agentic)
- Insider preview defaults are "locked down". A later Windows 11 build 26220.7344 adds a "Reducing Protections for Agent Connectors" setting. [secondary] — [The Register](https://www.theregister.com/2025/12/08/microsoft_windows_insiders_agentic_future/), [GeekRewind](https://geekrewind.com/?p=100348)

**Not re-verified this session [background, not fetched]**
- Windows 10 mainstream support ended 14 Oct 2025 (ESU available). Agentic Windows features (ODR, Agent Workspace, Copilot Actions) target Windows 11 only. — [Microsoft Windows 10 end of support](https://www.microsoft.com/en-us/windows/end-of-support)
- OmniParser V2 (Microsoft): screen-parsing model (icon detection + captioning) used to turn screenshots into structured element lists. Licensing is split, with icon_detect reportedly AGPL (YOLO-derived) and caption weights MIT. **Verify licenses at github.com/microsoft/OmniParser and the HF model cards before using it.**
- pywinauto (BSD-3) and `uiautomation` (Apache-2.0) are the standard Python UIA libraries. Windows-MCP uses UIA internally.
- Anthropic computer use (Claude API `computer` tool) and OpenAI's computer-use model/Operator agent are screenshot + mouse/keyboard loops. Both vendors recommend running them in a VM/container with minimal privileges and keeping a human in the loop for consequential actions.

### Inferences
- JARVIS already has the right architecture (a UIA snapshot → element-ID → act loop with a kernel gate). Adopting Windows-MCP wholesale would duplicate it and **reintroduce an unrestricted PowerShell/Registry surface**. The better path is to borrow its tool *designs* (WaitFor, MultiEdit, DisplayInventory, Snapshot with element IDs) into JARVIS's own node, and keep JARVIS's allowlist and PIN gate.
- If Windows-MCP is used at all, run it stdio-only (no HTTP modes), at ≥ v0.8.5, with PowerShell/Registry/Process tools disabled or routed through the kernel's RED tier.
- UFO²'s "GUI + API hybrid" is the key lesson. Prefer an API/COM/CLI route (e.g., the filesystem API, winget, Shell COM) over clicking whenever one exists, and fall back to UIA, then vision (OmniParser) only last.
- Because the PC is Windows 10, Microsoft's agent-connector sandbox and identity model is unavailable. JARVIS must provide its own isolation, audit and consent layer. If the owner upgrades to Windows 11, the File Explorer/Settings agent connectors could become an extra, OS-mediated backend.

### Gaps
- Could not confirm the exact latest Windows-MCP version/date on the official releases page (sources disagree: 0.8.1 / 0.8.2 / 0.8.5).
- No published UFO²/UFO³ or Windows-MCP WindowsAgentArena scores for 2026 were found. The WAA leaderboard state in 2026 was not verified.
- OmniParser v2 license specifics and Microsoft "Windows AI Foundry" agent APIs were not re-verified this session.
- OSWorld numbers are conflicting aggregator/self-reported figures. The official os-world.github.io leaderboard was not fetched.

---

## Q2. Safe admin elevation patterns (vs. giving an LLM a raw admin shell)

### Takeaway
Expert consensus does not hand the LLM an admin shell. Instead, the LLM runs **non-elevated**. Privileged work goes through a small **elevated broker** that exposes a fixed, typed, allowlisted operation set, validates parameters, requires out-of-band human approval (JARVIS's PIN), and logs everything. On Windows the native building block for this is **PowerShell JEA** (role capabilities + virtual accounts + transcripts, using NoLanguage-mode endpoints). Risky or untrusted executables can be tried first in **Windows Sandbox** (available on Win10 Pro/Enterprise, config via `.wsb`). Prompt injection makes any raw-shell design unsafe, because once untrusted content is in context the model "must be constrained".

### Cited Findings
- JEA "enables delegated administration for anything managed by PowerShell". It can use "virtual accounts or group-managed service accounts to perform privileged actions on behalf of regular users", it can "limit what users can do by specifying which cmdlets, functions, and external commands they can run", and it keeps "transcripts and logs that show you exactly which commands a user executed". With temporary privileged virtual accounts, users "connect ... using non-admin credentials and still run commands that typically require admin privileges". Applies to PowerShell 5.1+ (Windows 10 ships 5.1). [verified] — [Microsoft Learn: JEA overview](https://learn.microsoft.com/en-us/powershell/scripting/security/remoting/jea/overview)
- Windows Sandbox config files are XML `.wsb`, supported from Windows 10 build 18342 / Windows 11. `<Networking>` can be Enable/Disable/Default, and disabling it reduces attack surface. MappedFolders take HostFolder/SandboxFolder/ReadOnly, with read-only recommended. Microsoft warns that mapped host folders can let malware affect the host or steal data. Microsoft's sample disables vGPU and networking and maps Downloads read-only to test unknown files. [verified via MS Learn search summary] — [MS Learn: Windows Sandbox configuration](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file), [MS sample config](https://learn.microsoft.com/zh-tw/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-sample-configuration)
- OWASP Top 10 for LLM Apps 2025 includes **LLM06 Excessive Agency** (excessive functionality, permissions or autonomy), and it was expanded in 2025 because of agentic architectures. Organizations "often grant LLMs more authority and system access than they need". [secondary; the official OWASP LLM06 page was not fetched] — [Gravitee guide](https://www.gravitee.io/blog/owasp-top-10-for-llm-applications-2025-a-practical-guide), [Indusface LLM06](https://www.indusface.com/learning/owasp-llm-excessive-agency/)
- "Design Patterns for Securing LLM Agents against Prompt Injections" (IBM, Invariant Labs, ETH Zurich, Google, Microsoft et al., June 2025): "once an LLM agent has ingested untrusted input, it must be constrained so that it is impossible for that input to trigger any consequential actions". Also: "we believe it is unlikely that general-purpose agents can provide meaningful and reliable safety guarantees". [verified] — [Simon Willison review](https://simonwillison.net/2025/Jun/13/prompt-injection-design-patterns/), [arXiv 2506.08837](https://arxiv.org/html/2506.08837v1)
- The Windows-MCP case shows a real raw-shell risk: an HTTP-mode CORS/DNS-rebinding bug exposed an **unauthenticated PowerShell tool** (GHSA-vrxg-gm77-7q5g, fixed in 0.7.5). [secondary] — [releasealert CVE list](https://releasealert.dev/github/CursorTouch/Windows-MCP/cves)

**Not re-verified this session [background, not fetched]**
- UAC: under default settings, an admin user runs with a filtered (medium-integrity) token. Elevation shows a consent prompt on the Secure Desktop, which the user-mode automation (UIA/SendInput) of a medium-integrity process **cannot** click (UIPI). So an agent cannot, and should not, "click through UAC". The correct pattern is a pre-installed elevated component. — [MS Learn: How UAC works](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/user-account-control/how-it-works)
- Common elevated-broker implementations: (a) a Windows service running as LocalSystem or a dedicated admin account, exposing a named pipe with an ACL restricted to the owner's SID; (b) a Task Scheduler task with "Run with highest privileges", triggered on demand (`schtasks /run`) and reading a signed request file. A JEA endpoint (`Register-PSSessionConfiguration`, `RunAsVirtualAccount`, `SessionType RestrictedRemoteServer`, role capability `VisibleCmdlets`/`VisibleFunctions`) reached via `Enter-PSSession -ComputerName localhost -ConfigurationName JarvisAdmin` requires WinRM to be enabled locally. JEA transcripts plus PowerShell Script Block Logging (event 4104) provide audit.
- PowerShell Constrained Language Mode is only enforced robustly together with WDAC/AppLocker. On its own (via `__PSLockdownPolicy`) Microsoft does not treat it as a security boundary.
- `winget` supports `install/upgrade/uninstall --id <exact> --source winget --accept-package-agreements --silent`. Per-machine installs need elevation, so they should go through the broker. Pinning to exact IDs from an allowlist avoids typosquatted packages.

### Inferences
**Recommended design: "Elevated Broker" (JARVIS-Admin)**
1. Keep JARVIS's node running as the standard user (filtered token). **Never** run the node, the LLM-facing MCP servers, or the browser as admin.
2. Install a separate **JARVIS-Admin broker**. Option A is a Windows service; option B is a JEA endpoint wrapping PowerShell functions. Either way it exposes ~20–40 typed operations, never free-form commands. Examples:
   - `winget_install(id)` / `winget_upgrade(id|all)` / `winget_uninstall(id)`, restricted to an allowlist plus pinned source.
   - `service_restart(name ∈ allowlist)`, `set_power_plan`, `flush_dns`, `defender_quick_scan`, `create_restore_point`, `cleanup_temp`.
   - `set_env_var(user|machine, name ∉ {PATH-critical denylist})` and `firewall_rule_add(program, direction)`, both behind a confirm step.
   - `write_protected_path(path ∈ {Program Files\<allowlisted app>, hosts file})` with automatic backup.
   - Hard-denied: `bcdedit`, disabling Defender/UAC/firewall, creating users or adding to Administrators, registry edits under `HKLM\SAM`/`SECURITY`/LSA keys, credential stores, and anything touching password managers, wallets or banking (consistent with the existing DENY tier).
3. Request path: LLM → kernel policy (new **ADMIN** tier = RED + PIN + 2nd confirmation showing the exact operation and parameters in plain language) → signed request (HMAC with a key the LLM never sees) over a named pipe ACL'd to the node process's SID → broker re-validates against its own allowlist. The broker is the policy enforcement point, so it must not trust the kernel's decision alone.
4. Before any admin op that changes system state, create a System Restore point (where enabled) or a registry/config backup. Record a "before" snapshot in the undo journal.
5. Audit: an append-only log in the broker (JSONL + Windows Event Log source "JARVIS-Admin"), JEA transcripts, and PowerShell 4104 logging. A daily digest goes to the owner.
6. Untrusted executables or installers outside the winget allowlist: run first in Windows Sandbox (networking disabled, inputs mapped read-only) if the edition is Pro/Enterprise. Otherwise refuse or require explicit owner action.
7. Never let content-derived text become broker parameters without the owner's confirmation (for example, a web page saying "run winget install X"). Mark any request whose parameters came from browsed or tool-output content as **tainted** and force PIN + display.
8. "Full PC rights (admin)" should be explained to the owner as: *the owner keeps admin; JARVIS gets a growing catalogue of admin verbs, each approved*. That is a safer reading of the request than giving JARVIS an admin token.

### Gaps
- The official Microsoft Learn UAC, Task Scheduler, Constrained Language Mode and winget pages were not re-fetched this session (items marked background).
- Whether the owner's Windows 10 edition is Home (no Windows Sandbox, no Hyper-V) is unknown. Windows Sandbox requires Pro/Enterprise/Education [background; not confirmed this session].
- The official OWASP LLM06:2025 mitigation list was not fetched directly. The OWASP "Agentic AI Top 10" (if published for 2026) was not checked.

---

## Q3. Natural-language file finding and organising with undo

### Takeaway
For "help me find the folder named X", use a two-backend search: (1) **Everything (voidtools)** via `es.exe -json` or the SDK IPC. It is near-instant across all NTFS volumes, and ES is MIT-licensed but needs the Everything client running. (2) **Windows Search index** via OLE DB `Search.CollatorDSO` / `SYSTEMINDEX` SQL as a built-in fallback, which also gives full-text content search. The LLM extracts structured slots (name fragment, kind=folder/file, extension, date range, location hints), backends return candidates, and RapidFuzz re-ranks them. For organising, implement file ops as an **undo journal of reversible primitives** (mkdir, move/rename, copy, delete→Recycle Bin via send2trash/IFileOperation), never permanent deletes.

### Cited Findings
- Windows Search SQL extends SQL-92/99 for full-text search. "All features of Windows Search Structured Query Language (SQL) are compatible with Windows Search on Windows Vista, and later, including all versions of Windows 10". Samples (WSSQL, WSOleDB) use OLE DB. [verified] — [MS Learn: Querying the index with Windows Search SQL](https://learn.microsoft.com/en-us/windows/win32/search/-search-sql-windowssearch-entry)
- ES (Everything command-line) is **MIT** licensed. Options: `-path <path>`, `-regex`/`-r`, `/ad` (folders only), `/a-d` (files only), `-json`, `-sort <name|path|size|date-modified>`, `-n <num>`. It needs the Everything client running (error 8 = no IPC window). Journal options need Everything 1.5+. [verified] — [GitHub voidtools/ES](https://github.com/voidtools/ES)
- The Everything SDK is a DLL/Lib IPC wrapper (WM_COPYDATA). It is local only, thread safe, x86/x64, ANSI/Unicode, and requires Everything running in the background. [verified via search summary] — [voidtools SDK](https://voidtools.com/support/everything/sdk), [SDK IPC](https://www.voidtools.com/ko-kr/support/everything/sdk/ipc)
- send2trash on Windows uses **IFileOperation** (Vista+, with pywin32) and falls back to SHFileOperation. It is pure ctypes, needs no compilation, and raises OSError on failure. It has **no restore API**. [verified via PyPI/GitHub summary] — [GitHub arsenetar/send2trash](https://github.com/arsenetar/send2trash), [PyPI send2trash](https://pypi.org/project/send2trash)
- Microsoft's File Explorer agent connector on Windows 11 lets agents "manage local files with user consent", with identity and audit. It is not available on Windows 10. [verified via blog summary] — [Windows Developer Blog, Ignite 2025](https://blogs.windows.com/windowsdeveloper/2025/11/18/ignite-2025-furthering-windows-as-the-premier-platform-for-developers-governed-by-security)

**Not re-verified this session [background, not fetched]**
- Example Windows Search query (Python via `pywin32`/`adodbapi` or `win32com.client.Dispatch("ADODB.Connection")`): connection string `Provider=Search.CollatorDSO;Extended Properties='Application=Windows';`, then `SELECT System.ItemName, System.ItemPathDisplay FROM SYSTEMINDEX WHERE SCOPE='file:C:/Users/<u>' AND System.ItemType = 'Directory' AND System.FileName LIKE '%invoice%'`. The index covers only indexed locations (by default the user profile, Start Menu, etc.), not whole drives.
- The Everything GUI/client is freeware (it is free, but its own license is not MIT). It is bundled with a service to read the NTFS MFT/USN journal without elevation. Check the voidtools license before redistribution, though local personal use is fine.
- RapidFuzz (MIT) for fuzzy re-ranking: `process.extract(query, names, scorer=fuzz.WRatio)`.
- Recycle Bin restore: Shell `IShellFolder` for `CSIDL_BITBUCKET` / the `Shell.Application` `NameSpace(10)` item verbs ("Restore"), or read `$Recycle.Bin\<SID>\$I*` metadata files (original path + deletion time) and move the matching `$R*` file back.
- Windows `MoveFileEx`/`os.replace` is atomic within one volume. Cross-volume moves are copy+delete, so the journal must record them as copy-then-trash so they can be undone.

### Inferences
**Recommended design: "FileOps" capability with undo journal**
- **Tiers**: search/list/stat = GREEN. mkdir, rename, and moves within user folders (Desktop, Documents, Downloads, Pictures, Music, Videos, plus owner-approved roots) = **AMBER** (single-confirmation, no PIN; batch summaries for >N items). Delete to Recycle Bin = PIN. Operations outside user roots, system paths (`C:\Windows`, `Program Files`, `AppData\...\Credentials`, password-manager vaults, wallet folders) = DENY, or broker-only via ADMIN. Permanent delete = never (or owner-only manual).
- This would fix the decider's current refusal of "create a folder": mkdir in a user root is low risk and fully reversible, so it should not be PIN-gated.
- **Journal**: SQLite table `ops(id, batch_id, ts, op, src, dst, size, mtime, sha256_head, status, tainted)`. Every batch gets a plan → dry-run preview → execute → verify sequence. The `undo(batch_id)` call replays inverse ops in reverse order (move back, rmdir if empty, restore from Recycle Bin via the $I metadata or Shell verb). Refuse an undo if the destination changed since (compare mtime/size).
- **Conflict policy**: never overwrite. On a name collision append " (2)", or ask.
- **Organiser**: the LLM proposes rules ("move *.pdf older than 30 days from Downloads to Documents\PDFs\YYYY"). Deterministic code executes them, and the LLM never issues raw paths it hasn't seen from search results (path provenance check).
- **NL search pipeline**: utterance → slot extraction (JSON schema) → Everything `es.exe -json /ad -n 50 <terms>` (if installed) → else Windows Search SQL → RapidFuzz rank + recency boost → return the top 5 with full paths and a "reveal in Explorer" action (`explorer /select,`), which maps onto the existing open_path GREEN capability.

### Gaps
- The exact Windows Search SQL syntax and Python sample were not fetched this session (only the entry page).
- Everything's own (non-ES) license terms were not fetched.
- No authoritative source was fetched for programmatic Recycle Bin restore. It is listed as background and needs a prototype.

---

## Q4. Browser agents and prompt-injection defences

### Takeaway
browser-use (MIT, Python, can reuse the system Chrome profile) and Playwright MCP (Microsoft, accessibility-snapshot based, with `--extension` mode to attach to the owner's real Chrome/Edge tabs) are the mature open options. Playwright MCP explicitly states it "is **not** a security boundary", and its origin allow/block lists are not a boundary either. Signed-in browsing combined with untrusted pages and external comms is Willison's "lethal trifecta". Vendor mitigations (Anthropic's injection probes) reduce attack success but do not eliminate it: Anthropic itself says a 1% adaptive-attack success rate "still represents meaningful risk". Architectural defences (CaMeL / dual-LLM / plan-then-execute + egress control + human approval for consequential actions) are the recommended layer.

### Cited Findings
**Playwright MCP**
- `--extension`: "Connect to a running browser instance (Edge/Chrome only). Requires the 'Playwright Extension'". Env vars `PLAYWRIGHT_MCP_EXTENSION`, `PLAYWRIGHT_MCP_PROFILE_DIR_NAME`. Install as `@playwright/mcp@latest`. [verified] — [GitHub microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp)
- Security-relevant flags:
  - `--allowed-origins` defaults to allow-all and "does not serve as a security boundary" and "does not affect redirects".
  - `--blocked-origins`: the blocklist is evaluated before the allowlist.
  - `--isolated` keeps the profile in memory. `--storage-state` loads saved state.
  - The `secrets` option redacts matching text in tool responses, but is "a convenience and not a security feature".
  - `--caps` opt-in groups: vision, pdf, devtools, storage, network.
  - `browser_run_code_unsafe` "executes arbitrary JavaScript in the Playwright server process and is RCE-equivalent".
  - File system access is restricted to workspace roots by default, `file://` navigation is blocked, and `--allow-unrestricted-file-access` lifts both.
  - "Playwright MCP is **not** a security boundary."
  [verified] — [GitHub microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp)
- In extension mode each connection must be approved unless a profile-unique token is configured (auto-approve trades away safety). The first interaction shows a tab picker. A Chrome Web Store listing shows extension v0.1.0 dated 2026-04-07. [secondary] — [Mintlify guide](https://mintlify.com/microsoft/playwright-mcp/guides/browser-extension), [Debbie O'Brien blog](https://debbie.codes/blog/testing-logged-in-state-playwright-mcp-browser-extension), [chromeboard listing](https://chromeboard.com/extension/playwright-mcp-bridge-mmlmfjhmonkocbjadbfplnigmagldckm)

**browser-use**
- MIT ("The Python library is free and MIT-licensed"). `ChatBrowserUse` with BU2 (`bu-2-0`) is recommended, alongside ChatOpenAI/ChatAnthropic/ChatGoogle and Ollama. `Browser.from_system_chrome()` reuses a Chrome profile. Cloud profile sync carries cookies only. It claims "top score (90.5%) on Stealth Bench V2" for its cloud browsers and links its own "Browser Use Benchmark v2", with no WebVoyager number on the page. The Bash tool needs Linux/macOS (WSL on Windows). [verified] — [GitHub browser-use/browser-use](https://github.com/browser-use/browser-use)
- A third party reports BU Mini/BU Max models with ~40% fewer steps than GPT-4o on internal benchmarks, plus "Skill APIs" and 55k+ stars. [secondary, unverified] — [aitoolsatlas changelog](https://aitoolsatlas.ai/tools/browser-use/changelog)

**Vendor agentic browsers**
- Anthropic, Claude for Chrome pilot: 123 red-team cases across 29 scenarios. Without mitigations the attack success rate was **23.6%**. On browser-specific attacks (hidden form fields, URL text, tab titles) new mitigations cut it from **35.7% to 0%**. Probes scan tool results for injections and warn Claude to confirm with the user. A later post on adaptive attackers: "a 1% attack success rate—while a significant improvement—still represents meaningful risk". Claude in Chrome is now generally available. [verified via Anthropic pages in search; the vendor's own testing] — [Anthropic: Claude for Chrome](https://www.anthropic.com/news/claude-for-chrome), [Anthropic: prompt injection defenses](https://www.anthropic.com/research/prompt-injection-defenses), [Claude in Chrome GA](https://claude.com/resources/articles/claude-in-chrome-generally-available)

**Prompt-injection defences (research)**
- CaMeL (Google DeepMind et al., "Defeating Prompt Injections by Design"): a privileged LLM writes code from the trusted query, and a quarantined LLM parses untrusted data with no tool access. A custom interpreter tracks provenance (capabilities) and checks policies before each tool call. It solves **77%** of AgentDojo tasks with provable security, against 84% undefended (an earlier version reported 67%). The authors note it does not address every attack vector. Code: github.com/google-research/camel-prompt-injection. [verified abstract] — [arXiv 2503.18813](https://arxiv.org/pdf/2503.18813)
- Six design patterns (Action-Selector, Plan-Then-Execute, LLM Map-Reduce, Dual LLM, Code-Then-Execute, Context-Minimization). The key rule is that after ingesting untrusted input, the agent "must be constrained so that it is impossible for that input to trigger any consequential actions". [verified] — [Simon Willison](https://simonwillison.net/2025/Jun/13/prompt-injection-design-patterns/), [arXiv 2506.08837](https://arxiv.org/html/2506.08837v1)
- Lethal trifecta: private data + untrusted content + external communication = exfiltration risk. Willison calls guardrails that stop "95%" of attacks a failing grade. [secondary summaries of Willison] — [aiweekly](https://aiweekly.co/alerts/willison-names-lethal-trifecta-behind-ai-agent-data-leaks), [promptfoo](https://www.promptfoo.dev/blog/lethal-trifecta-testing/)

### Inferences
**Recommended design for "can go through any pages"**
- **Two browser lanes:**
  - (1) **Research lane**: isolated Playwright (`--isolated`, no cookies), behind JARVIS's filtering egress proxy. Read-only by policy, so it can visit any page. Downloads go to a quarantine folder, which is RED.
  - (2) **Signed-in lane**: Playwright MCP `--extension` on the owner's Chrome profile. Every navigation is allowed, but any **state-changing action** (form submit, purchase, post, send, settings change) on a signed-in origin requires PIN. Banking, wallet and password-manager origins are hard-DENY via `--blocked-origins` *and* the egress proxy (since `--allowed-origins` is not a boundary and ignores redirects).
- **Taint tracking (CaMeL-lite)**: once page content enters the context, mark the session "tainted". Tainted sessions cannot call admin, file-delete or messaging tools without PIN, and parameters derived from page text are shown verbatim to the owner. Use a quarantined summarizer model (dual-LLM) to read pages and return only structured fields to the planner.
- **Plan-then-execute** for multi-step tasks: the planner commits to the action list before reading untrusted content, and deviations need re-approval.
- **Credentials**: never type passwords via the agent. Rely on existing signed-in sessions; if a login wall appears, hand control to the owner. Never enable `browser_run_code_unsafe`, `--caps=devtools` or `--allow-unrestricted-file-access` in the signed-in lane. Do not set the extension auto-approve token.
- **JS-heavy pages**: prefer accessibility-snapshot actions (Playwright MCP's default) with explicit waits, and fall back to `--caps=vision` screenshots only when the a11y tree is poor.

### Gaps
- Not researched this session (no fetch): Stagehand (Browserbase) current version and v3 features, Skyvern (AGPL-3.0 per background knowledge; WebVoyager score unverified), Nanobrowser (Apache-2.0 Chrome extension per background knowledge), OpenAI Atlas/Operator/ChatGPT agent, and Perplexity Comet injection incidents. Treat all as unverified.
- browser-use's current release number, the `sensitive_data`/`allowed_domains` features (present in earlier versions, not visible on the current README capture), and Browser Use Benchmark v2 numbers were not confirmed.
- No 2026 Playwright MCP security changelog was found. The extension-token details come only from secondary sources.
- No independent (non-vendor) measurements of browser-agent prompt-injection rates in 2026 were found.
