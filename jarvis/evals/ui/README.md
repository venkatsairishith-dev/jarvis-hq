# Face render harness (D72)

Draws the JARVIS face's real components in Chromium, takes screenshots and checks the layout with the DOM. It answers "does the face look
right, in every state, at every window size, in light and dark?" without Electron, a model or a network.

```
cd jarvis/face && pnpm install --frozen-lockfile && pnpm run build:packages     # once (workspace packages' dist/)
cd jarvis/evals/ui
node render.mjs                                     # every screen x 450x600, 900x700, 1400x900 x light, dark   (about 12 min)
node render.mjs --only '^ask:' --sizes 360x640      # a part
node render.mjs --audit --report .work/report.json  # also contrast and the lists of warnings
node render.mjs --label after --format webp --out shots --only '^(chat:mixed|ask:cleanup-ready)$' --sizes 450x600 --themes dark
```

Playwright's own browser is used when installed; otherwise the first `chromium-*` under `PLAYWRIGHT_BROWSERS_PATH` or `/opt/pw-browsers`
(or set `CHROMIUM_PATH`). `JARVIS_UI_JOBS` sets how many pages render at once (default 3).

## What is real and what is stand-in

| Screen | Real | Stand-in |
|---|---|---|
| `chat:mixed` `chat:tools` `chat:waiting` `chat:empty` `chat:offline` | `InteractiveArea.vue` (history, composer, send, voice button), `chat-page-shell`, `TitleBar`, the Pinia stores | the Electron bridge (`harness/electron-vueuse-mock.ts`), the messages (`make_fixtures.py`: English, Telugu, Hindi, very long paths and words, a 40-line tool result, a tool error, a gateway error) |
| `hud:idle` `acting` `safe` `pin` `offline` `halted` `listening` `speaking` | `HudLayer.vue`, `hud-model.ts` (the status goes through `parseJarvisStatus`, so `pc_line`, "Safe mode" and "Waiting for your approval" are the real strings) | a flat shape where Live2D would be |
| `ask:*` (21 forms: cleaner with sizes, confirm, choose, settings, code, compare, ...) | `AskPanel.vue` | forms built by the REAL Python backend (`jarvis.bridge.ask`, `ui_patterns`, `cleaner_ui`) on a fake PC |
| `timeline:small` `long` `many` (59 steps) `empty` `unavailable` | `TimelinePanel.vue` | views built by the real flight recorder |
| `preview:*` (pptx deck, pdf, png, markdown, code, hostile html) | `PreviewPanel.vue` | the tool results of `jarvis.bridge.output_preview` |

The harness page uses the same Content-Security-Policy as the app. The PIN is never drawn anywhere in the face (the HUD only says that an
approval is waiting), so there is no PIN screen.

## What fails the run (exit 1)

- the page scrolls sideways, or a visible element sticks out of the window on the left or right
- a text is clipped (overflow hidden/clip, no ellipsis) so letters are cut off; 1 px screen-reader-only text is exempt
- the page raises an error (a `ResizeObserver loop` notice and the content policy's own refusals of the hostile fixtures are not errors)
- the page asks for anything that is not a file of the harness (127.0.0.1:1, where AIRI's unreachable cloud clients point, is allowed: it fails at once)

## What only warns

an ellipsis without a tooltip, a touch target under 32 px, text under WCAG AA contrast (4.5:1, 3:1 for large text; disabled controls exempt;
translucent layers are composited over the page colour, so a value near the limit needs a look). The counts are printed; `--report` lists them.

## Files

`render.mjs` (build, serve, screenshot, check), `harness/` (page, Vite config, bridge stand-in), `make_fixtures.py`, `webp.py`
(PNG to WebP for the committed shots), `shots/` (before and after pairs of the six most important screens, under 2 MB in all),
`.work/` (scratch, ignored). The face's own checks (`pnpm run check:hud|timeline|preview`, `scripts/ask-check.mjs`) are unchanged and still
the place for behaviour; this harness is for how it looks.
