# Desktop shell (Windows 10 tray/widget/push-to-talk) and the agent's own VM (incl. openmuse) — as of 22 Oct 2026

Method note: the openmuse repo (HEAD of 6–7 Oct 2026), the N.E.K.O.-Tauri repo (main, last commit 29 Jul 2026) and tauri-apps/global-hotkey (v0.9.0) were shallow-cloned and read directly. The GitHub API was not available in this session, so the open-issue list came from the public issues page. Pricing pages for cloud sandboxes were seen only through third-party summaries, which conflict in places; those figures are flagged.

## A1. Tauri v2 building blocks on Windows 10: tray, global push-to-talk, always-on-top transparent click-through, single instance, autostart, cost compared with Electron

### Takeaway
Tauri v2 covers every shell feature JARVIS needs with official plugins: tray-icon, global-shortcut with Pressed and Released events, single-instance and autostart. The global-shortcut plugin is built on Win32 `RegisterHotKey`. That gives push-to-talk only on a keyboard chord, the chord is taken from every other app while JARVIS holds it, and registration fails if another app already owns it. For a mouse button, or a key that other apps should still receive, you need your own low-level hook. The claimed RAM advantage over Electron is real in direction but poorly measured.

### Cited Findings
**Global shortcut and push-to-talk**
- `tauri-plugin-global-shortcut` is desktop-only. It is installed with `npm run tauri add global-shortcut` and needs Rust 1.90 or later. No permissions are on by default; a capability must grant `global-shortcut:allow-register`, `allow-unregister` and `allow-is-registered`. The docs call shortcuts "inherently dangerous". — [Tauri Global Shortcut plugin](https://v2.tauri.app/plugin/global-shortcut/)
- The handler gets an event whose `state()` is `ShortcutState::Pressed` or `ShortcutState::Released`. The JS type is `state: "Released" | "Pressed"`. This is the documented basis for push-to-talk. — [Tauri Global Shortcut plugin](https://v2.tauri.app/plugin/global-shortcut/); [JS reference](https://v2.tauri.app/reference/javascript/global-shortcut/)
- JS reference: "If the shortcut is already taken by another application, the handler will not be triggered." — [Tauri JS global-shortcut reference](https://v2.tauri.app/reference/javascript/global-shortcut/)
- Source code of the underlying crate (tauri-apps/global-hotkey **v0.9.0**, `src/platform_impl/windows/mod.rs`, read directly):
  - Registration calls Win32 `RegisterHotKey(hwnd, id, mods | MOD_NOREPEAT, vk)`.
  - If Windows returns `ERROR_HOTKEY_ALREADY_REGISTERED`, the crate returns `Error::AlreadyRegistered(hotkey)`. Other failures return `OsError`, or `FailedToRegister("Unknown VKCode…")` for keys it cannot map.
  - On `WM_HOTKEY` it emits `Pressed`, then spawns a thread that polls `GetAsyncKeyState(vk)` every **50 ms** until the key is up, then emits `Released`. The code comment says the sleep was added so a push-to-talk hold does not burn a core (issue #176).
  - [global-hotkey repo](https://github.com/tauri-apps/global-hotkey)
- Platform notes for the crate: Linux support is X11 only. On Windows a win32 event loop must run on the thread that creates the manager. — [global-hotkey README](https://github.com/tauri-apps/global-hotkey)

**Tray icon**
- Enable the `tray-icon` Cargo feature on `tauri`. Build the icon with `TrayIconBuilder::new().build(app)` in Rust or `TrayIcon.new()` in JS. A menu can be attached, and `show_menu_on_left_click(false)` / `menuOnLeftClick:false` turns off left-click menus.
- Tray events: Click (with button and button state), DoubleClick, Enter, Move, Leave. They are not emitted on Linux, which does not matter here.
- The docs' example restores the window with `get_webview_window("main")`, then `unminimize`, `show` and `set_focus`.
- [Tauri System Tray guide](https://v2.tauri.app/learn/system-tray/)

**Single instance**
- `tauri-plugin-single-instance` closes a second launch and passes its `(app, args, cwd)` to the running instance. A typical callback focuses the main window. The plugin "must be the first one to be registered". — [Tauri Single Instance plugin](https://v2.tauri.app/plugin/single-instance/)

**Autostart**
- `tauri-plugin-autostart` provides `enable`, `disable` and `isEnabled` from JS, or `app.autolaunch()` from Rust. It takes optional launch args. Permissions `autostart:allow-enable`, `allow-disable` and `allow-is-enabled` must be granted. The docs do not say how Windows autostart is implemented. — [Tauri Autostart plugin](https://v2.tauri.app/plugin/autostart/)

**Always-on-top, transparent, click-through: what N.E.K.O.-Tauri already does**
- `src-tauri/tauri.conf.json` sets the main window to `alwaysOnTop: true`, `transparent: true`, `backgroundColor [0,0,0,0]`, `decorations: false`, `shadow: false`, `visibleOnAllWorkspaces: true` and `visible: false` until first paint. Read directly from the [N.E.K.O.-Tauri repo](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri).
- How click-through works on Windows (`src-tauri/src/platform/windows.rs`):
  - It calls Tauri's `window.set_ignore_cursor_events(ignore)`.
  - `radar.rs` tracks `window.cursor_position()` and emits cursor events.
  - An injected `click_through.js` decides per pixel or region whether the window should ignore the mouse, then calls the `set_click_through` command.
  - This is the standard "click-through except over the avatar" pattern, and it is already implemented.
  - [N.E.K.O.-Tauri repo](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri)

**Memory and CPU compared with Electron**
- Third-party 2026 figures for an idle single window include **Tauri ~42 MB vs Electron 34 ~168 MB** and **~80 MB vs ~160 MB (Tauri 2.9.6 vs Electron 33)**. None publish a reproducible method, so treat them as unverified. — [tech-insider (Tauri vs Electron 2026)](https://tech-insider.org/tauri-vs-electron-2026/); [BetterStack guide](https://betterstack.com/community/guides/scaling-nodejs/tauri-vs-electron-vs-deno-vs-electrobun/); [dev.to comparison](https://dev.to/devtoolpicks/tauri-vs-electron-for-indie-hackers-in-2026-honest-comparison-o0e)
- A Tauri GitHub issue argues older benchmarks overstated Electron's RAM because Chromium processes share memory. Once shared memory is counted the gap narrows. — [tauri-apps/tauri #5889](https://github.com/tauri-apps/tauri/issues/5889)
- The current Electron line ships Chromium 152 and Node 24 (Electron 44.7.0). — [tech-insider Electron 44.7.0](https://tech-insider.org/electron-44-7-0-chromium-152-node-24-2026/)

### Inferences
- **Push-to-talk recommendation.** Use the plugin with a chord the user is unlikely to need elsewhere, such as `Ctrl+Alt+Space`, or a rarely used key such as F13–F24, Pause or ScrollLock via a macro key.
  - Handle the `AlreadyRegistered` error by offering a re-bind dialog.
  - Expect up to ~50 ms of release latency, which is fine for push-to-talk.
  - For a **mouse side button**, or a key that must still reach other apps (non-exclusive), `RegisterHotKey` cannot do it. You need a Rust `WH_KEYBOARD_LL`/`WH_MOUSE_LL` hook, for example the `windows` crate or a crate like `rdev`/`willhook`. This is my knowledge, not verified in this session.
  - Low-level hooks can be flagged by anti-cheat or antivirus, and they receive no events while an elevated window has focus unless JARVIS also runs elevated. This is standard Win32 UIPI behaviour, not verified here.
- **Plugin order.** Register single-instance first, then global-shortcut, then autostart. Add the `tray-icon` feature. Wire the tray menu: Show/Hide widget, Mute mic, Toggle click-through, Open dashboard, Quit.
- **Edge app-mode window.** Keep it as a fallback. It already shares Edge's processes, but it cannot do tray icons, global hotkeys or click-through without a native helper. That gap is the whole reason to finish the Tauri shell.
- **WebView2 on Windows 10.** WebView2 is the Evergreen runtime that Edge already uses, so the extra RAM for a small widget should be modest compared with a separate Electron Chromium. Measure private working set yourself; do not rely on the published numbers.

### Gaps
- I did not verify any Windows-10-specific bugs for `transparent: true` with WebView2, such as black backgrounds or DPI or multi-monitor issues. The N.E.K.O.-Tauri repo has Windows code but no Win10 test notes.
- I did not find any official Tauri statement on conflict behaviour beyond the JS reference line. The `AlreadyRegistered` detail comes from crate source.
- I found no first-party, reproducible WebView2-vs-Electron memory benchmark.

## A2. N.E.K.O.-Tauri repo status, and alternatives (Electron, Wails, Flutter, native WinUI or Rainmeter-style, Windows 11 Widgets)

### Takeaway
N.E.K.O.-Tauri, the "N.E.K.O. Tauri desktop client", is a small experimental shell under Apache-2.0. It already does the hard Windows parts: a transparent always-on-top window, per-region click-through, native screen capture, and spawning or attaching to the Python backend. It has **no tray, global-shortcut, single-instance or autostart** yet. Main has been quiet since 29 Jul 2026. Extending it, as the PATCHES.md plan proposes, is still the lowest-effort path. Upstream also has an Electron shell, N.E.K.O.-PC, which I could not inspect.

### Cited Findings
**Repository status**
- The org lists **N.E.K.O.-Tauri**: "N.E.K.O. Tauri desktop client", Rust, Apache-2.0, "updated Oct 14, 2026". The org also has N.E.K.O (Python, Apache-2.0, updated Oct 22 2026), N.E.K.O.-RN and others. — [Project-N-E-K-O repositories](https://github.com/orgs/Project-N-E-K-O/repositories)
- The repo name has a dot: **`N.E.K.O.-Tauri`**. `github.com/Project-N-E-K-O/N.E.K.O-Tauri` without the dot returns 404. — [N.E.K.O.-Tauri](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri)
- Main-branch history (cloned):
  - The last commit is **29 Jul 2026**, "chore: adopt Apache-2.0 license".
  - 27 Jul: "refactor: make Tauri repository a standalone desktop shell" and "docs: remove development status notice".
  - 24 Jul: native desktop capture bridge, KDE/niri overlay fixes, "bypass proxies for loopback startup".
  - Branches: `main`, `agent/standalone-tauri-repo`, `chore/apache-license-notice-20260729`.
  - The "updated Oct 14" on the org page is probably from non-code activity; this is unverified.
  - [N.E.K.O.-Tauri](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri)

**What the repo covers (README, Chinese)**
- In scope: window hosting, mouse click-through, per-platform adaptation, native screen capture, and starting and stopping the backend.
- Out of scope: backend, models and shared Web UI (Live2D/VRM/MMD), which live upstream.
- At runtime it attaches to an existing N.E.K.O at `127.0.0.1:48911` without taking ownership. Otherwise it starts `NEKO_BACKEND_DIR` in dev, or the bundled `projectneko_server(.exe)` in release.
- It points to the Electron shell **N.E.K.O.-PC** and to upstream PR N.E.K.O #2488 for host compatibility.
- [N.E.K.O.-Tauri README](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri)

**Dependencies and capabilities (`src-tauri/Cargo.toml`)**
- Dependencies: `tauri = "2"` with `macos-private-api`, `xcap 0.3`, `image`, `serde` and `base64`. There is **no** tray-icon feature, no `tauri-plugin-global-shortcut`, no `-single-instance` and no `-autostart`.
- The remote capability lets the backend UI at `http://127.0.0.1:48911/*` call `allow-set-click-through` and the screen-capture commands.
- Version 0.1.0, identifier `com.projectneko.tauri`.
- [N.E.K.O.-Tauri](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri)

**Upstream N.E.K.O**
- Upstream is packaged as `n.e.k.o-bin` 0.8.3 on the AUR (Apache-2.0). Its dependencies (gtk3, nss, libxtst) suggest the Electron build. — [AUR n.e.k.o-bin](https://aur.archlinux.org/packages/n.e.k.o-bin)

### Inferences
**Alternatives ranked for this owner (Windows 10, web UI already built, Live2D or icon in a webview)**
1. **Finish N.E.K.O.-Tauri, recommended.** It has an Apache-2.0 base, click-through is already solved, and the plugins are official. It needs four small additions: single-instance, tray, global-shortcut with a re-bind UI, and autostart. A low-level-hook push-to-talk is optional.
2. **Electron, through N.E.K.O.-PC.** It is the most mature API: `Tray`, `globalShortcut`, `app.requestSingleInstanceLock`, `setIgnoreMouseEvents(true, {forward:true})` and `setLoginItemSettings`.
   - Electron's `globalShortcut` has no key-up event, so true push-to-talk still needs `uiohook-napi` or a similar native hook. This is my knowledge; it is not verified in this session.
   - It costs more RAM, but upstream maintains it, so it is the fallback if the Tauri shell stalls.
3. **Wails v3.** A Go plus WebView2 shell with tray and window APIs. There is no N.E.K.O integration and I did not verify its v3 stability, so it brings no advantage over Tauri here.
4. **Flutter desktop.** It would mean rewriting the UI and would lose the existing web/Live2D UI. Not recommended.
5. **Native WinUI 3, or a Rainmeter-style skin.** Rainmeter can draw a lightweight always-on-top desktop widget, but it cannot host a webview avatar or do push-to-talk without a helper. WinUI would be a full rewrite.
6. **Windows 11 Widgets board.** Not available on Windows 10. In any case the board is a pop-up panel, not an always-on-top widget.
- **Platform caveat.** Windows 10 reached end of support on 14 Oct 2025 (Microsoft lifecycle, from my knowledge). Playwright now lists "Windows 11+" for Windows ([Playwright system requirements](https://playwright.dev/docs/intro)). Any browser-automation work on the laptop itself should go through WSL or a VM, not native Win10.

### Gaps
- I could not clone or inspect **N.E.K.O.-PC**; the clone hit a private or auth prompt. Its tray and shortcut features are unverified.
- I did not find any open issues or a roadmap in N.E.K.O.-Tauri for tray or push-to-talk.
- I did not research the current status of Wails v3 or Flutter desktop releases.

## B1. The Oracle Always Free ARM VM as the agent computer: limits, idle reclaim, aarch64 browser compatibility, remote-desktop stack, memory budget

### Takeaway
**Critical change:** Oracle's own Always Free page now lists Ampere A1 as **1,500 OCPU-hours and 9,000 GB-hours a month, which is 2 OCPU and 12 GB**. That is half of the 4 OCPU / 24 GB the owner's VM uses. Oracle never announced the cut. Over-limit instances on free accounts reportedly risk being stopped. On Pay As You Go the extra half reportedly costs about $25–28 a month, and support has given conflicting answers.

Technically, the aarch64 VM suits a headless agent browser well. Chromium, Firefox and Playwright all support Ubuntu arm64, and linuxserver/webtop (Selkies) and Kasm images ship arm64 builds. But the memory budget has to be redone at 12 GB.

### Cited Findings
**Current Always Free limits**
- "first 1,500 OCPU hours and 9,000 GB hours per month… equivalent to 2 OCPUs and 12 GB of memory". You can run one instance, or up to two 1-OCPU instances.
- 200 GB of total block storage, 5 backups, 10 TB a month of outbound data.
- The page contradicts itself on minimum boot volume: 47 GB in one place, 50 GB in another.
- [Oracle Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)

**Idle reclaim text, verbatim**
- An instance is idle if, over 7 days, "CPU utilization for the 95th percentile is less than 20%", "Network utilization is less than 20%", and "Memory utilization is less than 20% (applies to A1 shapes only)".
- The page does not say Pay As You Go accounts are exempt from reclaim. It does say PAYG is not charged for Always Free usage, only for usage above the limits.
- [Oracle Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)

**The cut and its consequences**
- Linuxiac (14 Jun 2026): the allowance was cut from 3,000/18,000 hours to 1,500/9,000 hours with "no clear public announcement". Over-limit resources on free accounts "may be disabled or reclaimed". Reddit reports say support told users the new limits apply to PAYG too; this comes from user reports, not Oracle. — [Linuxiac](https://linuxiac.com/oracle-quietly-cuts-free-tier-ampere-a1-resources-in-half/)
- TerminalBytes (26 Jun 2026): on free accounts an over-limit instance "gets stopped until it's resized", though enforcement is inconsistent. PAYG support answers conflict: overage billed, or "remain eligible under the current policy". The author estimates ~$25–28 a month to keep 4/24. — [TerminalBytes](https://terminalbytes.com/oracle-cloud-free-tier-changes-2026/)
  - TerminalBytes' advice: resize to 2/12, and if you hit "Out of host capacity", stop first and try another fault domain. If you stay on PAYG at 4/24, set a ~$1 budget alert.
- An August 2026 write-up by a **Hermes** user resized to 2/12. They kept VPN, SSH and Hermes always-on, moved k3s, MySQL and a local LLM server to on-demand, and backed up config first. — [joeyw: When an Always Free server gets smaller](https://joeyw.reallyenglish.com/posts/oracle-always-free-arm-downsize/)
- Other secondary sources say the doc change appeared around 15 Jun 2026, with enforcement reported from around 18 Aug 2026. **Unverified against Oracle.** — [Oracle Cloud Customer Connect thread (503 when fetched)](https://community.oracle.com/customerconnect/discussion/970310/oci-always-free-updated-ampere-a1-compute-allocation)

**aarch64 browser compatibility**
- Playwright system requirements: "Debian 12 / 13, Ubuntu 22.04 / 24.04 / 26.04 (x86-64 or arm64)". Windows support is listed as "Windows 11+, Windows Server 2019+ or WSL". — [Playwright intro](https://playwright.dev/docs/intro)
- Google announced Chrome for ARM64 Linux for **Q2 2026**. No search result confirmed it has shipped. Open-source Chromium has long run on arm64. — [9to5Google, 12 Mar 2026](https://9to5google.com/2026/03/12/chrome-arm64-linux/); [TechRepublic](https://www.techrepublic.com/article/news-google-chrome-arm64-linux-support-2026/)

**Remote-desktop stacks**
- linuxserver/webtop supports **amd64 and arm64**. It was rebased to **Selkies** in June 2025; it used KasmVNC before.
  - Flavours: XFCE (Alpine/Ubuntu/Debian/Fedora/Arch), i3, KDE, MATE, LXQt.
  - Recommended `shm_size: 1gb`. Ports 3000 (HTTP, needs a reverse proxy) and 3001 (HTTPS).
  - **No authentication by default**, and the web terminal has passwordless sudo. "should not be exposed to the Internet". HTTPS is needed for audio.
  - [linuxserver webtop docs](https://docs.linuxserver.io/images/docker-webtop/)
- KasmVNC 1.5.0 added an optional H.264/H.265/AV1 video mode (VAAPI or NVENC). 1.4.0 has a WebP compression-versus-CPU tunable. A linuxserver KasmVNC base image exists for linux/arm64 (~945 MB). — [KasmVNC releases](https://github.com/KasmTech/KasmVNC/releases); [lsiobase/kasmvnc tags](https://hub.docker.com/r/lsiobase/kasmvnc/tags)
- Selkies uses WebSockets by default, with WebRTC as an opt-in transport. — [selkies-project/selkies](https://github.com/selkies-project/selkies)

### Inferences
**Memory budget at 2 OCPU / 12 GB.** These are rough working-set estimates of mine, not measured on this VM:

| Component | Approx. RAM |
|---|---|
| Hermes Agent | ~0.5–1 GB |
| OmniRoute | ~0.2–0.5 GB |
| Qwen3-4B (4 GB weights) | ~4.5–5 GB with KV cache |
| Kernel, OS, Tailscale | ~0.7 GB |
| XFCE desktop + Selkies/KasmVNC | ~0.6–1 GB |
| One Chromium with a few tabs | ~1–2 GB |
| openmuse API + browser worker | ~1.5–2.5 GB (worker's compose `mem_limit` is 2 GB; Render says 512 MB is too small for the API because of PGlite) |
| **Total with everything** | **~10–13 GB** |

- At 12 GB with everything running, the VM will swap or OOM. Fix it by making Qwen3-4B on-demand, or moving it to the laptop or the relative's server, and by giving the browser worker a cgroup memory limit.
- At 24 GB (PAYG, about $25–28 a month) it fits comfortably.
- **Idle reclaim is not the real risk for this VM.** An agent desktop plus a resident LLM keeps memory above 20%, so the A1 memory criterion is not met. The size cut is the real risk.
  - If the VM is downsized and the LLM goes on-demand, memory still stays above 20% of 12 GB (2.4 GB) with Hermes, OmniRoute and a desktop running.
  - Avoid "keep-alive" CPU burners; they are pointless if memory is above 20%.
- **Remote desktop choice.** Prefer **KasmVNC or Selkies (webtop) over TigerVNC plus noVNC**. Both are single-container, have arm64 images, and use better codecs than raw VNC over noVNC. Bind to the Tailscale interface or localhost only, and add a password. Never expose port 3000 or 3001 publicly.
- **Per-component fit.** A Hermes `computer_use` X11 driver (cua-driver or an xdotool equivalent) can target the webtop or Kasm X display. openmuse's browser worker can run alongside as a separate headless Chromium with persistent profiles.

### Gaps
- I found no first-party Oracle statement on enforcement dates or on PAYG treatment of existing 4/24 instances. The Customer Connect thread returned HTTP 503.
- I did not confirm that Chrome stable for Linux arm64 actually shipped in Q2 2026.
- I did not verify that the owner's current Oracle account is Free or PAYG; that determines whether 4/24 means a stop or a bill.
- There are no measured RAM figures for Selkies or KasmVNC on Ampere.

## B2. Other "own VM" options: rebuild WSL2, a Hyper-V or VirtualBox VM on the laptop, Docker desktops (webtop, Kasm), cloud sandboxes (E2B Desktop, Daytona, Browserbase, Steel, Hyperbrowser)

### Takeaway
For an always-on, private computer that the assistant owns, the **Oracle VM (resized or PAYG) running a Docker desktop** is the best fit. A laptop-hosted VM dies whenever the laptop sleeps, and rebuilding WSL2 only restores what was lost.

Cloud sandboxes are good for burst or isolation but not as the main computer:
- **E2B Desktop** is the strongest, and openmuse already supports it natively.
- **Steel** has the most generous free browser hours, but that figure is contested.
- **Browserbase** free is only 1 hour.
- All of them mean a third party sees the browsing.

### Cited Findings
**E2B**
- Hobby: no fee, a **one-time $100 credit**, **1-hour sessions**, 20 concurrent sandboxes. Pro is $150 a month with 24-hour sessions. Usage is billed per second at about $0.0504 per vCPU-hour and $0.0162 per GiB-hour (third-party pricing page dated 16 Jun 2026). — [E2B billing docs](https://e2b.dev/docs/billing); [Beam: E2B pricing explained](https://www.beam.cloud/blog/e2b-pricing-explained)
- E2B Desktop is "a Linux VM with an Xfce desktop, Firefox ESR, Google Chrome and LibreOffice". In openmuse's integration, a sandbox pauses after 15 idle minutes while `/workspace` persists; processes do not. — [openmuse docs/COMPUTER.md](https://github.com/CopilotKit/openmuse/blob/main/docs/COMPUTER.md)

**Daytona**
- **$200 free compute, no card**, plus 5 GB storage. Higher limits need card top-ups ($25 / $500 / $2,000 tiers). One analysis says the practical minimum for browsing agents is closer to a $500 deposit. — [Daytona pricing](https://www.daytona.io/pricing); [agent37 analysis](https://www.agent37.com/blog/daytona-pricing)

**Browserbase**
- Free: **1 browser hour**, 3 concurrent browsers, 15-minute sessions.
- Developer: $20 a month for 100 hours, then $0.12 per hour. Startup: $99 for 500 hours.
- [TinyFish: Browserbase pricing](https://www.tinyfish.ai/blog/browserbase-pricing); [humanbrowser.cloud](https://humanbrowser.cloud/blog/browserbase-alternative-2026)

**Steel**
- Sources conflict:
  - "$10 monthly credits, 100 browser hours, 2 concurrent, 15-min cap" — [Kernel: best free agent browsers 2026](https://www.kernel.sh/ai-library/best-free-agent-browsers-2026)
  - versus "Launch $0 with $30 one-time credit (90 days), $0.10 per browser-hour" — [humanbrowser.cloud (Sep 2026)](https://humanbrowser.cloud/blog/browserbase-alternative-2026)
- The open-source core can be self-hosted for free.

**Hyperbrowser**
- Credit-based: about $0.10 per browser-hour and $10 per GB of proxy traffic. One source lists a free tier of 1,000 credits with 1 concurrent browser. Inconsistent. — [Sendwin comparison](https://blog.send.win/browserbase-alternatives-steel-vs-hyperbrowser-comparison/); [simular.ai alternatives](https://www.simular.ai/alternatives/browserbase-alternatives)

**Docker desktops**
- linuxserver/webtop (Selkies, arm64 and amd64) is covered in B1. — [linuxserver webtop docs](https://docs.linuxserver.io/images/docker-webtop/)
- Kasm's arm64 base image also exists. — [lsiobase/kasmvnc tags](https://hub.docker.com/r/lsiobase/kasmvnc/tags)

### Inferences
**Option comparison.** All ratings below are my synthesis.

| Option | Cost | Always on? | Privacy | Isolation | Fit for "its own VM like muse" |
|---|---|---|---|---|---|
| **Oracle A1 + Docker (webtop/Kasm + openmuse worker), Tailscale-only** | $0 at 2/12; ~$25–28/mo at 4/24 PAYG | Yes | Your tenancy; Oracle infrastructure | VM-level from laptop; container-level inside | **Best overall** |
| Rebuild WSL2 "JarvisLab" | $0 | Only while laptop is awake | Local | Weak; WSL shares the host kernel, and Windows files are mounted by default | Good dev/test box; already proven; easy to lose again unless you `wsl --export` regularly |
| VirtualBox VM on the laptop | $0 | Only while laptop is awake | Local | Strong (full VM) | Fine. Hyper-V needs Win10 Pro/Enterprise, and enabling Hyper-V or WSL2 makes VirtualBox run on the Hyper-V backend, which is slower. RAM competes with the user's own work |
| Relative's server, rootless podman | $0 | Yes | **Not for owner accounts** (as stated) | Container | Test only, as the owner already decided |
| E2B Desktop | $100 one-time, then per-second | Pauses after idle; 1-h sessions on Hobby | Third party | Firecracker microVM | Good **burst / disposable** computer; openmuse supports it natively |
| Daytona | $200 credit | Per-use | Third party | Sandbox | Viable alternative to E2B; no openmuse integration |
| Browserbase / Steel / Hyperbrowser | Free tiers small or contested | Per-session | Third party sees all browsing and cookies | Managed browser only, no desktop or terminal | Use only for hard sites (CAPTCHA, proxies); not a "computer" |

- **Recommended setup ("use the best one").** Put the agent computer on the Oracle VM. Decide first between paying about $26 a month to keep 4/24, or resizing to 2/12 and moving Qwen3-4B elsewhere or on-demand.
  - Run linuxserver/webtop (Ubuntu XFCE, Selkies) or KasmVNC with Chromium and Firefox for Hermes `computer_use`.
  - Run openmuse's API, web and browser worker in Docker beside it.
  - Bind everything to Tailscale only.
  - Keep E2B Desktop as an optional disposable second computer for risky browsing.
  - Rebuild WSL2 JarvisLab only as a local dev mirror, with scheduled `wsl --export` backups.

### Gaps
- I did not fetch official pricing pages for Steel, Hyperbrowser or Browserbase; the figures come from third parties and conflict.
- I did not verify E2B Desktop's per-hour cost (vCPU/RAM of the default desktop template).
- I did not check Kasm Workspaces' (full product) Community Edition limits for 2026.

## B3. openmuse (CopilotKit/openmuse): features, architecture, cloud key, data, search, telemetry, ARM64, status, issues

### Takeaway
openmuse (stylised OpenMuse) is an **MIT-licensed alpha (0.1.0-alpha, 15 Sep 2026)**. It is a "personal agent with a browser, terminal, files, and work that keeps going".

It is not fully self-hosted:
- The API **refuses to start without a CopilotKit Intelligence project key**. Chat threads are stored in CopilotKit's cloud.
- Telemetry goes to PostHog **by default at full sampling**.
- Web search sends queries to Parallel.

All three can be disabled or are being questioned in open issues, except the Intelligence key, which is tracked as issue #73 and is still required. Its in-chat browser is mostly **read public pages plus human takeover**: autonomous click-and-type in chat is open issue #61. Full desktop control (`use_desktop`) exists only with the **E2B Desktop** provider.

### Cited Findings
**Release status and activity**
- License MIT. Version 0.1.0. CHANGELOG: "0.1.0-alpha — 2026-09-15 Initial public OpenMuse alpha".
- README: "Alpha, for self-hosting and building on".
- Recent commits are 6–7 Oct 2026 (Gmail parsing hardening, an OpenAI-compatible-gateway fix #80, "show what changed when a watched page changes" #66).
- [openmuse repo](https://github.com/CopilotKit/openmuse); [CHANGELOG](https://github.com/CopilotKit/openmuse/blob/main/CHANGELOG.md)

**Features (README table)**
- **Chat:** CopilotKit headless with AG-UI, mailbox search, follow-up queue, inline email/browser/PDF/plan/finance cards.
- **Agent computer:** persistent browser profiles plus a takeover console, an optional isolated Linux terminal, files and PDF transfer.
- **Activity:** durable plans, input requests, pause/resume/cancel/retry, approvals, receipts, and SQL leases.
- **Ideas** with evidence.
- **Goals & Tracking:** recurring public-page checks for change, text or USD price, with alerts.
- **Documents:** email attachment, then PDF form fill, then reviewed reply.
- **Finance:** CSV to spending summary.
- **Gmail & Calendar:** OAuth, with every send or change reviewed.
- **Personal context and memory.**
- **Rich Threads.**
- Roadmap only: voice, device push, health/bank/social connectors, generated tools, reservations and payments.
- [openmuse README](https://github.com/CopilotKit/openmuse); [ROADMAP](https://github.com/CopilotKit/openmuse/blob/main/ROADMAP.md)

**Architecture**
- **Client:** an Expo / React Native client for iOS, Android and web (`apps/mobile`).
- **API:** a Hono API running the CopilotKit runtime (`@copilotkit/runtime` 1.70.1) with an in-process durable task worker (`apps/server`).
- **Database:** PGlite by default, or Postgres via `DATABASE_URL`.
- **Browser worker:** a token-protected Playwright/Chromium service (`apps/worker`) on port 8790.
- **Computer:** an optional Docker Linux "computer" (`apps/computer`, a nonroot `node:22-bookworm-slim` with bash, python3 and git).
- **Requirements:** Node 24 LTS and pnpm 11.19.0.
- **Render deployment:** 3 services (API on the Standard plan with a 1 GB disk, a static web site, and a private browser service on Standard with 1 GB). 512 MB runs out of memory because of PGlite.
- [openmuse README](https://github.com/CopilotKit/openmuse)

**Browser worker API and limits**
- Endpoints: sessions, navigate, a 1280×800 screenshot, read (visible text, capped at 100k characters), input (click, text, key, scroll) and PDF downloads.
- "Popups and dialogs are dismissed; service workers and WebSockets are disabled. Sites requiring those features may not work yet."
- Docker image `mcr.microsoft.com/playwright:v1.62.1-noble`. Compose sets `mem_limit: 2g`, `shm_size: 256mb`, read-only root, `cap_drop ALL`, and binds `127.0.0.1:8790`.
- [apps/worker/README](https://github.com/CopilotKit/openmuse/blob/main/apps/worker/README.md); [infra/compose.yaml](https://github.com/CopilotKit/openmuse/blob/main/infra/compose.yaml)
- README: "Agent tools can read public pages and hand interactive work to the person." Also: "Graphical desktops and autonomous checkout remain future work." — [openmuse README](https://github.com/CopilotKit/openmuse)

**Docker computer**
- Commands run nonroot with no host mounts. **Terminal networking is disabled**, there is a **30-second command limit**, and output is capped at 128 KB.
- A named `/workspace` volume persists. "This is a Linux container, not a full operating-system VM."
- [docs/COMPUTER.md](https://github.com/CopilotKit/openmuse/blob/main/docs/COMPUTER.md)

**E2B Desktop provider**
- Set `COMPUTER_PROVIDER=e2b-desktop` with `E2B_API_KEY` and `COMPUTER_DEPLOYMENT_ID`. You get an Xfce desktop with Firefox ESR, Chrome and LibreOffice.
- The agent gets a `use_desktop` tool shaped like Anthropic's computer-use tool: screenshot, clicks, type, key, scroll and wait via xdotool, at 1280×800.
- The stream URL "carries a VNC password… treat it as a bearer secret".
- [docs/COMPUTER.md](https://github.com/CopilotKit/openmuse/blob/main/docs/COMPUTER.md)

**Required CopilotKit Intelligence key**
- "Every deployment requires `CPK_INTELLIGENCE_API_KEY`… The API fails at startup with a missing-key error when the key is unset."
- What CopilotKit stores: chat threads. "Chat threads are stored by CopilotKit Intelligence, so a thread can still load after you sign back in even when the disk was never attached."
- Intelligence "is not included in this repository's MIT license".
- [docs/RICH-THREADS.md](https://github.com/CopilotKit/openmuse/blob/main/docs/RICH-THREADS.md); [README](https://github.com/CopilotKit/openmuse)

**Telemetry**
- Runtime telemetry goes to `https://telemetry.copilotkit.ai/ingest`, then to PostHog. It is tagged `accessibility_title:"OpenMuse"`, and `COPILOTKIT_TELEMETRY_SAMPLE_RATE` defaults to 1 (full capture).
- Events: `oss.runtime.instance_created`, `oss.runtime.copilot_request_created`. These are joined to the Clerk user ID of the Intelligence signup.
- **Opt out:** set `COPILOTKIT_TELEMETRY_DISABLED=true` or `DO_NOT_TRACK=1`, then restart.
- [docs/TELEMETRY.md](https://github.com/CopilotKit/openmuse/blob/main/docs/TELEMETRY.md)

**Web search**
- On by default via Parallel's free, keyless Search MCP.
- It "sends model-generated queries and context, which may include information from your conversation or task, plus a random per-chat/task session identifier to Parallel".
- It is rate-limited with no paid fallback. Disable with `WEB_SEARCH_ENABLED=false`.
- [openmuse README](https://github.com/CopilotKit/openmuse)

**Models**
- `AGENT_BACKEND=model` with `MODEL=provider/model-id` (OpenAI, Anthropic or Google). There are `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL` / `GOOGLE_GENERATIVE_AI_BASE_URL` overrides and an `OPENAI_CHAT_COMPLETIONS=true` switch. Commit #80 "route OpenAI-compatible gateways to the Chat Completions adapter".
- `AGENT_BACKEND=agui` with `AGENT_URL` and `AGENT_TOKEN` lets openmuse front an **external AG-UI agent**.
- [.env.example](https://github.com/CopilotKit/openmuse/blob/main/.env.example)

**Open issues (12, as of 22 Oct 2026)**

| # | Title |
|---|---|
| #184 | "Contains" page watch never matches when its text has a trailing space |
| #181 | Dark mode |
| #179 | A working keyless local thread store (follow-up to #73) |
| #176 | Telemetry probes time out on Windows |
| #153 | Renovate dashboard |
| #78 | Desktop control panel for non-developers |
| **#73** | **Make CopilotKit Intelligence optional for self-hosted deployments** |
| **#61** | **Let the chat agent click, type and fill forms on web pages** |
| #53 | Scope upstreaming a large downstream personal-agent derivative |
| #35 | Preserve minimal provenance on durable task evidence |
| #34 | Preserve evidence origin and freshness separately from evidence identity |
| #28 | Provider-neutral web search contract |

No ARM64 issues were found. — [openmuse issues](https://github.com/CopilotKit/openmuse/issues)

**Verification claims**
- 154 tests, a real Chromium lifecycle test and a Docker computer smoke test pass locally.
- "Live model and Google-account acceptance remain outside this recording."
- [docs/VERIFICATION.md](https://github.com/CopilotKit/openmuse/blob/main/docs/VERIFICATION.md)

### Inferences
- **ARM64.** No ARM-specific code or blocks were found, and every base is multi-arch: the official Playwright Docker images publish arm64, `node:22-bookworm-slim` is multi-arch, and PGlite is WASM. It very likely runs on the Oracle A1. **Unverified; no ARM build was tested in this session.** The E2B Desktop provider runs in E2B's cloud, so host architecture does not matter for it.
- **Privacy posture if self-hosted.**
  - Set `COPILOTKIT_TELEMETRY_DISABLED=true` and `DO_NOT_TRACK=1`.
  - Decide on `WEB_SEARCH_ENABLED=false`; use SearXNG or Vane through JARVIS instead, or accept Parallel.
  - Point `OPENAI_BASE_URL` at OmniRoute.
  - Accept that **chat threads still go to CopilotKit Intelligence** until #73/#179 land. Patching it out locally is possible under MIT but means forking.
- **What openmuse has that JARVIS lacks:**
  - A durable task engine with plans, approvals and receipts.
  - Persistent per-session browser profiles with a takeover console.
  - Page-watch Goals and Tracking with alerts.
  - Ideas with evidence.
  - PDF form fill and reviewed Gmail/Calendar actions.
  - A mobile/web app.
  - A sandboxed terminal with file transfer.
- **What JARVIS plus Hermes already does better:** true `computer_use` on a full desktop (cua-driver), voice and avatar, and a local LLM.
- **How to combine them.**
  - Run openmuse beside Hermes on the Oracle VM as the "Muse-like" research and task UI, with model calls through OmniRoute.
  - Hermes can stay the agent that actually drives the webtop desktop.
  - `AGENT_BACKEND=agui` could later make openmuse's UI front Hermes directly, if Hermes exposes AG-UI (**unverified**).

### Gaps
- I did not see the PATCHES-like changes for issue #73 or any timeline. Whether a keyless mode is planned is unknown.
- I did not test the actual RAM of the openmuse API on ARM.
- I did not confirm whether the Parallel Search MCP free tier has stated rate limits.

## B4. Fully self-hosted alternatives doing the same job (Open WebUI, LibreChat agents, Suna/Kortix, OpenManus, Agent-Zero, OpenHands, browser-use web-ui, Perplexica/Vane)

### Takeaway
No single self-hosted project matches openmuse's mix of task engine, page tracking and mobile UI. The closest fully local combination for "research by using a browser" is:
- **Vane** (formerly Perplexica) for cited web research via SearXNG plus a local or OmniRoute model.
- A **browser-agent runtime**: Hermes `computer_use` on a webtop desktop, or the browser-use web-ui, or OpenManus.
- **Open WebUI or LibreChat** as the chat front end.

Kortix (formerly Suna) is the closest "Manus-like" all-in-one, but it is heavier and its positioning has moved toward company platforms.

### Cited Findings
- **Vane.** Perplexica was renamed **Vane** (commit "feat(app): rename to 'vane'", reportedly 2026-03-09). The Docker image moved to `itzcrazykns1337/vane`. It is still SearXNG plus an LLM with cited answers, and supports Ollama, LM Studio or cloud APIs. The rename details come from secondary sources. — [Korben](https://korben.info/en/vane-perplexica-new-name.html); [Glukhov quickstart](https://glukhov.org/llm-hosting/llm-frontends/vane-perplexica-2/); [Railway template](https://railway.com/deploy/vane-self-hosted-ai-search-formerly-perplexica--vane-ai-search)
- **browser-use web-ui.** MIT, Gradio UI. It offers "Use Own Browser" with a persistent browser and a Docker VNC viewer at `:6080/vnc.html`, with an ARM64 variant. The latest changelog entry is **2025-01-26**, so the project looks stale. — [browser-use/web-ui](https://github.com/browser-use/web-ui)
- **OpenManus.** From the MetaGPT team, MIT, uses Playwright browsing, needs Docker and your own API keys. — [Hypereal: OpenManus](https://hypereal.cloud/a/openmanus-open-source-manus-ai-alternative); [openalternative.co](https://openalternative.co/alternatives/manus)
- **Kortix (Suna).** Launched as Apache-2.0. It is now positioned as a company-wide agent platform with self-hostable, git-versioned agents and skills; a hosted tier costs $40 per seat per month. Licence claims conflict between sources. — [AIbase: Suna launch](https://www.aibase.com/news/www.aibase.com/news/17416); [Lindy: Manus alternatives](https://www.lindy.ai/blog/manus-ai-alternatives)
- **OpenClaw.** Listed in 2026 roundups as an MIT self-hosted agent that runs through messaging apps. — [Hostinger: Manus alternatives](https://www.hostinger.com/tutorials/manus-alternatives/)

### Inferences
**Mapping openmuse capabilities to self-hosted replacements:**

| openmuse capability | Self-hosted replacement |
|---|---|
| Cited web research | Vane (SearXNG) |
| Interactive browsing with takeover | Hermes `computer_use` on webtop/KasmVNC, where the owner can watch and take over in the same web desktop; or browser-use web-ui |
| Durable tasks and page watches | Not well covered; would need Hermes cron or skills, or keeping openmuse |
| Chat UI with threads stored locally | Open WebUI or LibreChat |

- Of these, Open WebUI, LibreChat, Agent-Zero and OpenHands are all actively maintained, open-source and Docker-deployable. This is from my knowledge; I did **not** verify their Oct 2026 versions or ARM64 images in this session.
- **Bottom line.** If "do all the work muse can do" is the goal, the pragmatic choice is to **run openmuse itself on the Oracle VM**, with telemetry off, OmniRoute as the model gateway and an Intelligence key (a free CopilotKit account), and pair it with a webtop desktop for Hermes.
- If no third-party cloud may hold chats, use Vane plus Open WebUI or LibreChat plus Hermes on webtop, and accept losing openmuse's task, tracking and PDF workflows. Or fork openmuse to replace the Intelligence thread store; issue #179 tracks exactly that.

### Gaps
- I did not research the current status, licences or ARM64 support of Agent-Zero, OpenHands, Open WebUI or LibreChat in this session.
- The Kortix/Suna licence status as of Oct 2026 is unconfirmed.
- I found no head-to-head evaluation of these tools for browser research quality.
