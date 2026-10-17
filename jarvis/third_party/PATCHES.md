# Planned patches for N.E.K.O.-Tauri

**PLAN ONLY.** None of these patches is written, built or tested yet. They apply to `neko-tauri` at the commit
pinned in `README.md`. Source: `jarvis/docs/neko/evidence/01-assistants-and-desktop-shell.md`, sections 5.3, 6 and 8.
Keep each patch small, and offer it upstream as a pull request (reuse first, no fork).

| # | Patch | What it does | Uses | Fixes upstream | Laptop test (Windows 10) |
|---|---|---|---|---|---|
| 1 | Tray | Tray icon and menu: Show/Hide Neko, Mute mic, Release mouse, Pause wake word, Open JARVIS console, Quit. The tooltip and icon follow the JARVIS state. | Tauri 2 core `tray-icon` feature | no tray | The menu items work, and the icon changes with the state |
| 2 | Global push-to-talk | One chord (draft: `Ctrl+Alt+Space`): `Pressed` starts the mic, `Released` stops it. It calls the page's existing voice-session logic through an injected function. The exact entry point is still unknown. | `tauri-plugin-global-shortcut` 2.4.1 (`RegisterHotKey`, no global keyboard hook) | no hotkey | Hold for 30 s: CPU must not climb (global-hotkey #176). Press and release start and stop the mic |
| 3 | Single instance | A second launch focuses the running window and exits. | `tauri-plugin-single-instance` 2.5.4 | #5 | Start twice: one window only |
| 4 | Click-through fail-safe | A native watchdog: if no hit-test heartbeat arrives for N seconds, or backend health fails, it calls `set_ignore_cursor_events(false)` and shrinks the window to the cat or hides it. Adds the tray item "Release mouse". | Tauri window API | #2 (whole desktop unclickable after a backend crash) | Kill the backend: the desktop stays clickable |

Related work from the same plan, not part of this patch set: idle radar back-off, the page state tap, the badge
overlay, and exposing `ActivationState` to the page.

Rules for the patches (evidence 01, section 9):
- No new powerful commands (file system, shell) in the main-window capability. The page renders model and LLM output,
  and CSP is `null` upstream (#7).
- Pin Tauri 2.x. Do not move to the Tauri 3 alpha.
- Before choosing this shell for good, run the Win10 transparency soak (Tauri #15947): 30 minutes. If it fails, the
  fallback is a minimal Electron host.
