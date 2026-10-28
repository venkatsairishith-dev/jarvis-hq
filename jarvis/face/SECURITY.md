# Security of the JARVIS face

This is the security policy of `jarvis/face`, the avatar, chat and voice window of JARVIS. It replaces AIRI's policy page, which
was unfilled template text. Everything below says what the code does, where, and how to check it. What is not covered is
listed in section 6.

## 1. Reporting a problem

JARVIS is one owner's personal system, not a product with a support desk. Report a problem to the owner
(`venkatsairishith-dev`, by a private GitHub security advisory on the `jarvis-hq` repository). There is no bounty and no
promised time. Do not post details, working exploits, keys or personal data in a public issue.

Only the newest commit of `jarvis/face` on the main branch is supported.

## 2. What the face is allowed to do

The face asks; JARVIS decides. It has no authority of its own:

| Thing | Rule | Enforced in |
|---|---|---|
| Actions on the PC | Only through the JARVIS tool server, which hands them to the console and the kernel. The kernel applies its policy, asks the owner for the PIN when a step is risky, and writes the audit log. The face cannot approve, deny, lift the kill switch or change policy. | `jarvis/bridge/face_tools.py` |
| Plugin tools | The tool server also offers the entries of the JARVIS plugins. Their reach is each plugin's own: the `desktop` plugin acts on the PC only as kernel jobs (policy, PIN, audit); `doc_tools` reads only the folders the owner allows and refuses credential files; `deliverables` and `doc_tools` write only into the outputs folders; `research` and `web_search` go out only through the privacy gateway and the egress guard; `main_agent` plans, and its steps are the same entries or `jarvis_do`. Every result is cleaned (names of files and folders, keys and e-mail addresses are removed or withheld) before the face's model sees it, and what the plugins say on their own arrives through `jarvis_messages`. `--plugins` narrows the set. | `jarvis/bridge/face_tools.py`, `jarvis/pluginapi/host.py`, `jarvis/plugins/_shared/screen_text.py` |
| Showing the owner's files | The Preview panel reads only the deliverables output folder, through the tool server: no `..`, no links, no secret-looking names, an allowlist of types whose first bytes must match, size caps. Shown content is data: images only as `data:` URLs of four raster types, HTML cleaned by DOMPurify and shown in `iframe sandbox=""` with `default-src 'none'`, links inert. "Open in app" is the deliverables plugin's own `open_output` entry. | `jarvis/bridge/output_preview.py`, `src/renderer/composables/jarvis-preview-logic.ts`, `components/PreviewPanel.vue` |
| Starting programs | Never. The MCP connector refuses stdio servers (a command written into `mcp.json`), and the Godot launcher is disabled. AIRI's own computer-use and Home Assistant control are removed. | `src/main/services/airi/mcp-servers`, `godot-stage`, `src/main/index.ts` |
| Network, outbound | Only this PC (loopback), the tailnet (100.64.0.0/10) and hosts the owner lists in `JARVIS_FACE_ALLOW_HOSTS`. This covers every Chromium request, the main process's `fetch`, and every Node TCP socket (http, https, WebSocket, tls). Chromium resolves no other host name, and WebRTC may not use UDP. | `src/main/jarvis/network-lock.ts`, `socket-lock.ts` |
| Network, inbound | Every server the app starts listens on 127.0.0.1 only, whatever its settings say (AIRI let its channel server listen on every interface). | `socket-lock.ts` |
| Remote debugging | Never in an installed app. | `src/main/app/debugger.ts` |
| Accounts and cloud | None. AIRI's account, inbox and feature-flag clients point at `127.0.0.1:1`, where nothing listens. | `packages/stage-shared/src/jarvis.ts` |
| Showing and hiding itself (D78) | Presentation only. "Hidden" means JARVIS is not displayed: every window is hidden with `BrowserWindow.hide()`, the taskbar buttons go, the tray icon goes unless the owner keeps it, and one gate in the main process refuses and queues every way a window, notification, dialog or DevTools could appear. It is NOT a way to be unseen by screen sharing, recording or monitoring software: there is no content protection or capture-exclusion flag, no hiding from the task list or process list, no renamed process, no window-class trick, and nothing detects such software (an explicit non-goal, D78). A hide never approves anything: the kernel keeps a PIN approval pending, and voice cannot approve (`buttonBlockedForVoice` is unchanged). | `src/main/jarvis/window-gate.ts`, `window-gate-electron.ts`, `hidden-mode.ts` |
| Updates | None from AIRI. The updater is off and the release feed is removed. The owner builds the installer from this repository. | `electron-builder.config.ts` |

## 3. Telemetry: none

AIRI sent product events to its own analytics service (OpenPanel) through an adapter. JARVIS removed the adapter, its
configuration and the dependency; `isAnalyticsAvailableInBuild()` returns false in every build. There is no other tracking
code in the tree (checked by searching for the usual analytics, error-reporting and beacon libraries and calls).

Three layers would stop a future change from sending something out:

1. the network lock blocks any host that is not this PC, the tailnet or the owner's list;
2. `pnpm run check:launch` starts the real app for 45 seconds and fails if the lock had to block anything, so a new outside
   call is found by a test and not by a user;
3. at run time, Chromium does not resolve other host names at all.

## 4. Malicious character cards and other untrusted text

In 2025 AIRI rendered a character card's text as HTML without cleaning it (GHSA-9832-f8jx-hw6f; fixed upstream in
v0.7.2-beta.3), so a crafted card could run script in the app. JARVIS keeps that fix and adds three more layers:

1. **Every place that shows untrusted text as HTML cleans it with DOMPurify** (card details, markdown in chat).
2. **A content policy** in `src/renderer/index.html` (AIRI shipped it commented out, so there was none). Inline script,
   `javascript:` links, script from any other site, plugins, `<base>` and form posts are refused. `pnpm run check:csp` loads the
   built page in Chromium, plants five injection attempts, and fails if any runs. Without the policy, three of them run.
3. **A gate on card import** (`packages/stage-ui/src/libs/cards/card-gate.ts`): all text in an imported card is reduced to plain
   text (tags, comments, `javascript:` links and hidden control characters removed; the nested-tag trick is handled), long text
   is cut, and cards with too many parts are refused. Markdown, placeholders and every language are kept.

Import cards only from people you trust anyway; the layers above are for the day one of them has a bug.

## 5. Memory

AIRI's memory let the model save and erase memories on its own, and its Settings page for memory was an empty placeholder.
In JARVIS:

- The model can only **suggest** a memory. A suggestion is invisible to the model until the owner accepts it in
  Settings > Memory (the page now exists: waiting suggestions, accepted memories, remove, make general, add by hand).
- The model cannot overwrite or erase an accepted memory; it can only withdraw its own pending suggestion.
- Its instructions say never to suggest a fact that came from a web page, an email, a file or a tool result.

This matches the JARVIS rule that agents write only to places the owner reviews. Memories are stored in the face's own
settings on the owner's PC. They are not yet written to the JARVIS vault (planned).

## 6. What is not covered

Said plainly, so nobody relies on more than is there:

- **Windows are not sandboxed.** Every window runs with `sandbox: false` because AIRI's preload script needs Node, and the
  preload hands the page Electron's generic IPC. A script that got running in a window could call any main-process handler.
  The layers above (content policy, cleaning, the card gate) are what keep script out; the removed features and the network
  and listener locks limit what such a script could reach. Moving the preload to a sandboxed, narrow bridge is future work.
- **Native modules and child processes** are not network-locked (the face carries none that talk to the network, and it
  starts no programs). `dgram` (UDP from Node) is not locked.
- **Build-time downloads** (the Live2D Cubism core and the sample avatars) are checked against SHA-256 values pinned on
  2026-10-23 (`downloads.lock.json`; `pnpm run build` fails on a difference). The pins were taken from the files the hosts
  served that day, so a file that was already altered then would be pinned too. Dependencies are pinned by `pnpm-lock.yaml`.
- **The Live2D core is Live2D's proprietary software**, downloaded under their licence, not stored in this repository.
- **Tested on Linux only.** The Windows installer, the avatar on a real screen and the microphone path have not been run.
- **AIRI's account code is still in the tree** (unreachable; it points at nothing). Removing it is clean-up, not security.
- **Beta software.** AIRI's own releases are marked pre-release. JARVIS pinned one commit and tests it; it does not follow AIRI.

## 7. Checking it yourself

```
pnpm -F @proj-airi/stage-tamagotchi exec vitest run   unit tests, including the network lock on real sockets
pnpm -F @proj-airi/stage-ui exec vitest run           card gate, memory proposals
pnpm run build                                        build, then verify the pinned downloads
pnpm -F @proj-airi/stage-tamagotchi run check:csp     injection attempts against the content policy (Chromium)
pnpm -F @proj-airi/stage-tamagotchi run check:launch  start the real app for 45 s; fails on any outside call
pnpm -F @proj-airi/stage-tamagotchi run check:render  start the real app and list every console error and policy refusal in every window;
                                                      fails when the policy broke something (the Live2D stage once failed on 'unsafe-eval')
python -m pytest jarvis/bridge/test_face_tools.py     the tool server and its refusals
python -m pytest jarvis/bridge/test_output_preview.py the Preview panel's file rules (traversal, links, size, type, secret names)
pnpm -F @proj-airi/stage-tamagotchi run check:preview renders the Preview panel in Chromium and attacks it with hostile HTML and markdown (needs python3 + Pillow)
```

`check:csp` and `check:launch` need Chromium (set `CHROMIUM_PATH` if Playwright's own download is not installed) and, for
`check:launch`, Linux with `xvfb-run` and the Electron binary.
