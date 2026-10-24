# JARVIS face

The avatar, chat and voice window of JARVIS. It replaced N.E.K.O (removed on 2026-10-23) as JARVIS's face. It is a desktop app (Electron, Vue) that
runs on the owner's laptop (Windows) and shows a Live2D, VRM or MMD avatar.

The face decides nothing. Chat goes through the privacy gateway, speech goes to the local speech server, and every action goes
to the JARVIS tool server, which hands it to the console and the kernel. The kernel decides what may run and asks the owner for
the PIN when a step is risky.

```
face (this folder) --chat--> privacy gateway 127.0.0.1:48950/v1 --> model provider (private values replaced)
                   --voice-> local speech server 127.0.0.1:48930/v1 (Kokoro / Piper, speech to text)
                   --tools-> JARVIS tool server 127.0.0.1:48970/mcp (jarvis/bridge/face_tools.py)
                                --> JARVIS console --> kernel (policy, PIN, audit)
```

## Where it comes from

Built from **Project AIRI** (moeru-ai/airi, MIT), tag v0.12.0, commit `3b0672e3668bd1ade1210e579818f379e4f8a224`. It is
JARVIS's own code now: it is not tracked against AIRI and is never updated from AIRI's releases. Commit `7390cb5` is the
unchanged import, so every JARVIS change can be read as a diff against it. The internal package names (`@proj-airi/*`) are
kept so that a later AIRI security fix can still be compared and ported by hand.

Taken: the desktop app (`apps/stage-tamagotchi`), the 44 workspace packages it needs, three small `server/packages` libraries,
and the workspace files. Left out: AIRI's web and mobile apps, its cloud server, its Godot app, its computer-use service and
its CJK display fonts (62 MB).

## What JARVIS changed

| Change | Where |
|---|---|
| **Network lock.** Every request from every window, every `fetch` and every Node TCP socket in the main process may reach only this PC (loopback), the tailnet (100.64.0.0/10) and hosts the owner lists in `JARVIS_FACE_ALLOW_HOSTS` (comma separated, `*.example.com` allowed). Every server the app starts listens on 127.0.0.1 only. Chromium resolves no other host name and WebRTC may not use UDP. | `apps/stage-tamagotchi/src/main/jarvis/network-lock.ts`, `socket-lock.ts` |
| **Wake words.** The built-in character answers to "Hey JARVIS" and "JARVIS" (the bundled keyword model runs on this PC). The microphone input mode starts as `wake-word`: nothing you say is sent anywhere until a wake word is heard. Change the mode in Settings > Modules > Hearing. | `packages/stage-ui/src/libs/voice/jarvis-wake-words.ts` |
| **Content policy.** AIRI shipped its Content-Security-Policy commented out, so the page had none. Now injected inline script, `javascript:` links and script from other sites are refused (`pnpm run check:csp` proves it in Chromium). | `src/renderer/index.html` |
| **Card gate.** Text in an imported character card is reduced to plain text and size-capped (the 2025 attack came through a card). | `packages/stage-ui/src/libs/cards/card-gate.ts` |
| **Memory is owner-approved.** The model can only suggest a memory; it is invisible to the model until you accept it in Settings > Memory (AIRI's page for it was an empty placeholder, and the model could write and erase on its own). | `packages/stage-ui/src/stores/memory.ts`, `packages/stage-pages/src/pages/settings/memory/` |
| **Pinned downloads.** The Live2D core and sample avatars the build downloads are checked against SHA-256 values; a difference fails the build. | `downloads.lock.json`, `scripts/verify-downloads.mjs` |
| **Tools only from JARVIS.** The face never starts a program as an MCP server (AIRI allowed any command in `mcp.json`). It connects only to HTTP servers on this PC or the tailnet, and a new config starts with the JARVIS tool server. | `src/main/services/airi/mcp-servers/index.ts` |
| **No computer use, no Home Assistant control.** Both acted on the PC or the home outside the kernel. Removed: the main-process service, the chat tool, the composer button, the native `@auv-js` packages. | main `index.ts`, `src/renderer/stores/tools/` |
| **No analytics.** The OpenPanel adapter, its configuration (AIRI's analytics endpoint) and the dependency are removed; no build can turn analytics on. | `packages/stage-ui/src/libs/product-signals/` |
| **No updates from AIRI.** The auto-updater is off and the GitHub release feed (moeru-ai/airi) is removed from the installer config. | main `index.ts`, `electron-builder.config.ts` |
| **No AIRI account.** The sign-in button is removed; AIRI's account, inbox and feature-flag clients point at `127.0.0.1:1`, where nothing listens, so they fail at once and nothing leaves the PC. | controls island, `packages/stage-shared/src/jarvis.ts` |
| **JARVIS defaults.** Empty chat, hearing and speech settings start on the privacy gateway and the local speech server. Settings the owner made are never changed. | `packages/stage-ui/src/stores/modules/jarvis-defaults.ts`, `packages/stage-shared/src/jarvis.ts` |
| **JARVIS character.** The built-in card is named JARVIS and its prompt is generated from `jarvis/persona/jarvis_card.json` (`python jarvis/persona/face_prompt.py`; `--check` tells whether they match). AIRI's character prompt is gone. | `packages/i18n/src/locales/en/base.yaml` |
| **Name.** App id `jarvis.face`, product name JARVIS, window titles, tray, English interface text. Uninstalling keeps the face's settings. | `electron-builder.config.ts`, `src/main/windows/`, `packages/i18n/` |
| **English only.** The 10 other interface languages and the translation glossary are removed. JARVIS still answers in the language the owner speaks. | `packages/i18n/` |
| **No Spine runtime.** Spine avatars need the Spine Runtimes, which only a Spine Editor licence holder may integrate. The runtime is removed; a Spine avatar shows a notice. | `packages/stage-ui-spine/` |
| **Smaller fonts.** The three CJK display fonts are stand-ins that map their names to fonts already on the PC. | `packages/font-*` |

## Build and run (Windows laptop, or Linux for tests)

Needs Node 22 and pnpm 11 (`corepack enable`). The first build downloads, and caches under `.cache/`:

- the **Live2D Cubism SDK for Web** core from cubism.live2d.com. It is Live2D's proprietary software, never stored in this
  repository; by building, the owner accepts Live2D's licence (free for individuals and small businesses);
- the sample avatars (Hiyori from Live2D's sample collection, two VRoid sample avatars) from dist.ayaka.moe.

```
cd jarvis\face
pnpm install
pnpm run build          # packages, then the app (out\)
pnpm run build:win      # an installer under apps\stage-tamagotchi\dist\
pnpm run dev            # development window
```

Start JARVIS's side first: the privacy gateway (48950), the local speech server (48930) and the tool server:

```
python -m jarvis.bridge.face_tools --data D:\jarvis\data\muse --vault D:\jarvis\vault     # jarvis\scripts\jarvis-start.ps1 does this
```

The tool server also offers every entry of the JARVIS plugins (`jarvis/plugins`) as a tool named `<plugin>__<entry>`, and a
`jarvis_messages` tool for what plugins say on their own. `--plugins none` turns the plugins off, `--plugins research,doc_tools`
narrows them. Their results are cleaned before the face's model sees them.

You type no key. The face sends the placeholder `jarvis-local`; the privacy gateway replaces it with the key in
`D:\jarvis-secrets\providers\dahl.key` (the file must exist; `jarvis/privacy/providers.json` names it). The default chat model is
`zai-org/GLM-5.3-Flash`. A profile from an earlier build is repaired once at the first start. Speech needs no key.

## Security

`SECURITY.md` is the policy: what the face may do, where each rule is enforced, what is not covered, and how to check it. In
short: no telemetry, no cloud, no updates, no programs started, nothing reachable from outside the PC, injected script refused,
memories only with your approval. `jarvis/tools/test_face_guard.py` fails if any of those protections is removed.

## Tests

```
pnpm -F @proj-airi/stage-tamagotchi exec vitest run
pnpm -F @proj-airi/stage-ui exec vitest run
pnpm -F @proj-airi/stage-tamagotchi run check:csp      # injection attempts against the content policy (Chromium)
pnpm -F @proj-airi/stage-tamagotchi run check:launch   # the real app for 45 s under a virtual display (Linux)
pnpm -F @proj-airi/stage-tamagotchi run check:render   # the real app: every console error and policy refusal in every window (Windows or Linux)
python -m pytest jarvis/bridge/test_face_tools.py jarvis/tools/test_face_guard.py
```

Measured on 2026-10-23 against the unchanged import (`efe0dd0`): the suites of stage-tamagotchi, stage-ui, stage-shared and
provider-inference give the same results, apart from tests removed or renamed with the code they tested. New tests cover the
network lock, the MCP lockdown, the JARVIS defaults and the tool server.

## Known limits

- **Not yet run on the laptop.** It is built and tested on Linux; the Windows installer, the avatar on a real screen, the
  microphone and the end-to-end voice path still need the laptop (pilot, Phase B in
  `jarvis/docs/reports/research-2026-10-22/FACE_REPLACEMENT_2026-10-23.md`).
- **Non-English data remains** where it is data, not prose: the speech readers' Japanese, Korean and Chinese tables for maths
  and chemistry, MMD morph names (they are the identifiers inside MMD model files) and test fixtures.
- AIRI's cloud client code is still present but unreachable (it points at nothing). Removing it is clean-up, not security.
- Windows are not sandboxed (`sandbox: false`; AIRI's preload needs Node). See `SECURITY.md`, section 6, for this and the
  other limits.
- AIRI's security history: the character-card XSS that could run commands (GHSA-9832-f8jx-hw6f) was fixed in
  v0.7.2-beta.3, and the CI advisory (GHSA-fvf7-wrqx-3m6c) concerned AIRI's GitHub workflows, not the app. JARVIS keeps the
  fix and adds the content policy and the card gate. Import character cards only from sources you trust.

## Licences

AIRI's code is MIT (`LICENSE`, copyright Neko Ayaka and the Moeru AI Project AIRI Team); keep that file with the code.
`packages/font-departure-mono` is SIL OFL 1.1 (its `LICENSE`). The Live2D Cubism core and the sample avatars are downloaded at
build time under their own terms and are not part of this repository.
