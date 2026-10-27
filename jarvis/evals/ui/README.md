# Face render harness (D72)

Draws the JARVIS face's real components in Chromium, takes screenshots and checks the layout with the DOM. It answers "does the face look
right, in every state, at every window size, in light and dark?" without Electron, a model or a network.

```
cd jarvis/face && pnpm install --frozen-lockfile && pnpm run build:packages     # once (workspace packages' dist/)
cd jarvis/evals/ui
node render.mjs                                     # every screen x 450x600, 900x700, 1400x900 x light, dark   (about 12 min)
node render.mjs --only '^ask:' --sizes 360x640      # a part
node render.mjs --audit --report .work/report.json  # also contrast and a Tab walk (every stop shows a ring and stays on screen)
python summarize.py .work/report.json               # the warnings grouped by kind and screen
node render.mjs --label after --format webp --out shots --only '^(chat:mixed|ask:cleanup-ready)$' --sizes 450x600 --themes dark
```

Playwright's own browser is used when installed; otherwise the first `chromium-*` under `PLAYWRIGHT_BROWSERS_PATH` or `/opt/pw-browsers`
(or set `CHROMIUM_PATH`). `JARVIS_UI_JOBS` sets how many pages render at once (default 3).

## What is real and what is stand-in

| Screen | Real | Stand-in |
|---|---|---|
| `chat:mixed` `chat:tools` `chat:waiting` `chat:empty` `chat:offline` | `InteractiveArea.vue` (history, composer, send, voice button), `chat-page-shell`, `TitleBar`, the Pinia stores | the Electron bridge (`harness/electron-vueuse-mock.ts`), the messages (`make_fixtures.py`: English, Telugu, Hindi, very long paths and words, a 40-line tool result, a tool error, a gateway error) |
| `hud:idle` `acting` `safe` `pin` `offline` `halted` `listening` `speaking` | `HudLayer.vue`, `hud-model.ts` (the status goes through `parseJarvisStatus`, so `pc_line`, "Safe mode" and "Waiting for your approval" are the real strings) | a flat shape where Live2D would be |
| `ask:*` (24 forms: the cleaner with sizes in 5 states, confirm, choose, pick a file, settings, code, compare, data, schedule, save, a job; plus `indic-choose`, `indic-text` and `long-confirm` with Telugu, Hindi and very long words) | `AskPanel.vue` | forms built by the REAL Python backend (`jarvis.bridge.ask`, `ui_patterns`, `cleaner_ui`) on a fake PC |
| `timeline:small` `long` `many` (59 steps) `empty` `unavailable` | `TimelinePanel.vue` | views built by the real flight recorder |
| `orb:*` (D78: the seven states, sizes S and L, the right-click menu, an ask form and a Telugu and Hindi form open beside it, the count after an unhide) and `bar:*` (the minimal bar at S, M, L and in three states) | the REAL `OrbShell.vue` with `OrbView`, `MinimalBar`, `OrbMenu`, `OrbSpeechHost` and the HUD and look stores | the Electron main process: the window layout comes from `computeOrbWindow`, the function the main process uses |
| `preview:*` (pptx deck, pdf, png, markdown, code, hostile html) | `PreviewPanel.vue` | the tool results of `jarvis.bridge.output_preview` |

The harness page uses the same Content-Security-Policy as the app. The PIN is never drawn anywhere in the face (the HUD only says that an
approval is waiting), so there is no PIN screen.

## Idle cost (D78): `--perf`

```
node render.mjs --perf --only '^$'                                  # the orb and the bar: animation-frame calls a second, JS heap, canvases, avatar scripts fetched
node render.mjs --perf --only '^$' --avatar-baseline DIR            # also the real Live2D renderer, for the before / after numbers
```

`DIR` holds `live2dcubismcore.min.js` and `hiyori_free_zh.zip`, the two files the face's build downloads (`jarvis/face/apps/stage-tamagotchi/downloads.lock.json`; they are not stored in this repository). The run FAILS when the idle orb asks for more than 15 animation frames a second, creates a WebGL context, or fetches a script that belongs to the avatar. WebGL in the harness is software (SwiftShader), so only the JavaScript-side numbers are meaningful; CPU and GPU on a real GPU are for the laptop.

## What fails the run (exit 1)

- the page scrolls sideways, or a visible element sticks out of the window on the left or right
- a text is clipped (overflow hidden/clip, no ellipsis) so letters are cut off; 1 px screen-reader-only text is exempt
- the page raises an error (a `ResizeObserver loop` notice and the content policy's own refusals of the hostile fixtures are not errors)
- the page asks for anything that is not a file of the harness (127.0.0.1:1, where AIRI's unreachable cloud clients point, is allowed: it fails at once)

## What only warns

an ellipsis without a tooltip, a touch target under 32 px, a Tab stop without a ring or off screen (`--audit`), text under WCAG AA contrast (4.5:1, 3:1 for large text; disabled controls exempt;
translucent layers are composited over the page colour, so a value near the limit needs a look). The counts are printed; `--report` lists them.

## Files

`render.mjs` (build, serve, screenshot, check), `harness/` (page, Vite config, bridge stand-in), `make_fixtures.py`, `summarize.py`, `webp.py`
(PNG to WebP for the committed shots), `shots/` (`<screen>-<size>-<theme>-before.webp` and `-after.webp`: the six most important screens at 450x600 and 900x700 or 1400x900, about 0.4 MB in all),
`.work/` (scratch, ignored). The face's own checks (`pnpm run check:hud|timeline|preview`, `scripts/ask-check.mjs`) are unchanged and still
the place for behaviour; this harness is for how it looks.
