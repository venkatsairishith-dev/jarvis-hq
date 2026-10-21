# JARVIS asks, N.E.K.O answers: task questions in the chat (2026-10-20)

Goal (master prompt "JARVIS × N.E.K.O: real interactive, voice-first desktop agent"): when a running JARVIS task needs
something from the owner, it asks in N.E.K.O's own chat, and a typed or spoken answer resumes the same task from its
checkpoint. This pass built that one flow end to end (phases 1–5 for it) and stopped there, as the prompt says.
Nothing is committed, pushed or deployed. The caretaker stays off.

## 1. Upstream vs. this copy

Upstream N.E.K.O (github.com/Project-N-E-K-O/N.E.K.O, shallow clone of `30c6d755`, 2026-10-19) has no way for an agent
task to ask the user a question: its "pending input" code is audio/session buffering only. Its chat has a choice layer
(visit bar, choice prompts) and plugin chat cards, which we reused.

Upstream drift since our base touches the same files in other places only (reply-tail captions in `App.tsx`, two extra
script tags in `index.html`): no overlapping lines with ours, so a later rebase is mechanical.

| File | Ours vs. HEAD | Upstream vs. HEAD |
|---|---|---|
| frontend/react-neko-chat/src/App.tsx | +17 −1 | 18 lines (reply tail) |
| frontend/react-neko-chat/src/FullChatSurface.tsx | +9 | none |
| frontend/react-neko-chat/src/message-schema.ts | +44 | none |
| static/app/app-react-chat-window/geometry-and-messages.js | +5 | none |
| templates/index.html, templates/chat.html | +1 each | 4 lines each |

## 2. What was reused (no new endpoint, no new credential)

- The chat's React choice layer and its styling (the card sits where the visit bar sits).
- The plugin card-action route `/api/plugin-cards/<plugin>/action/<action>` with its CSRF check and one token retry,
  the same one plugin cards use.
- main_agent's durable runs: held steps (`NEEDS_YOU`), `resume_run(rerun=[step])`, one active run, run store.
- The widget voice path (wake word → `/api/task` → `main_agent.run_task(source="widget")`).
- `SELF_PLUGINS`: the planner never sees main_agent's own entries, so a task cannot answer its own question.

## 3. The flow

1. A plugin step returns `{"input_request": {...}}` instead of a result (contract: `main_agent/input_request.py`;
   kinds text, secret, choice, multi, yes/no; at most 6 fields and 12 options; the id is made by the server).
2. main_agent checks it, stores it on the run, holds the step, and posts one plain chat line ("JARVIS needs your
   input …"); the widget shows the phase `waiting_for_input`.
3. The chat (compact overlay and full window) polls `pending_inputs` every 5 s while visible and shows the newest
   question as a form above the input box. It never takes focus. "Later" folds it into a small "Answer" pill.
4. Typed answer → `answer_input`; spoken answer ("Hey Neko, the answer is …", "use Gemini as primary") → `run_task`
   from the widget → the same `_answer_core`. Checks: right run and question id, still open, not expired, fields
   valid, no other task running. Then the held step reruns with the answers merged into its arguments.
5. "Cancel question" (asks to confirm) → `cancel_input` → the run is cancelled.

First real user: the desktop PC agent. When it needs clarification it now asks through this card instead of failing.

## 4. Files

New: `plugin/plugins/main_agent/input_request.py`, `plugin/plugins/main_agent/tests/test_input_request.py`,
`frontend/react-neko-chat/src/TaskPromptCard.tsx`, `TaskPromptCard.test.tsx`, `jarvisTaskInputHost.test.ts`,
`static/app/app-react-chat-window/jarvis-task-input.js`, this document.

Changed: `plugin/plugins/main_agent/__init__.py`, `outcome.py`, `README.md`, `TOOL_INVENTORY.md`,
`tests/test_outcome_registry.py`, `plugin.meta.json` (regenerated, twice-identical); `plugin/plugins/desktop/__init__.py`,
`plugin.meta.json`; `frontend/react-neko-chat/src/App.tsx`, `FullChatSurface.tsx`, `message-schema.ts`, `styles.css`;
`static/app/app-react-chat-window/geometry-and-messages.js`; `templates/index.html`, `templates/chat.html`;
`static/locales/*.json` (20 keys in all 8 languages); `static/i18n-i18next.js` and `tests/unit/test_locale_cache_bust_contract.py` (locale version bump); `jarvis/widget/widget_server.py`, `jarvis/widget/neko_state.py`,
`jarvis/console/tests/test_neko_state.py`.

## 5. Security review

| Risk | What stops it |
|---|---|
| Secret answer stored or logged | Secret fields live only in main_agent memory, keyed by run and step, are passed to that one step and dropped after it; the run store and chat get field names only. Tested: not on disk, not in chat, gone after use. The form clears the field after sending; the host script keeps no values. |
| Secret said aloud | Voice answers are refused for any question with a secret field. |
| A page or plugin answering for the owner | Same-origin card route with CSRF; the planner cannot call main_agent entries; answers need the exact run id and server-made question id. |
| Old or repeated answers | Refused when the question is answered, cancelled, expired (default 24 h) or belongs to another run; refused while another task runs. |
| Malicious question text | Normalised and cleaned server-side (lengths, names, option counts, control characters); a malformed request fails the step instead of being shown. Rendered as text, never HTML. |
| Showing stale questions as live | When polling fails the card says "Not connected" and polling backs off to 60 s. |

Not changed: kernel permission and approval rules, credentials, provider settings, security policies, migrations.

## 6. Tests (real runs in the `neko-test` image, 2026-10-20)

| Suite | Result |
|---|---|
| main_agent (incl. 14 new) | 521 passed |
| desktop | 465 passed |
| jarvis/console | 247 passed, 3 skipped |
| jarvis/kernel (unittest) | 1031 ran, OK (27 skipped) |
| react-neko-chat vitest (incl. 15 new) | 39 files, 906 passed; `tsc -b` clean; `npm run build` OK |
| Headless Chrome on the built bundle, compact 420×640 and full 900×700 | card shown, 1 password field, focus stays put, no page errors, answer delivered with the right ids, secret cleared, "Later" pill shown |
| ruff on new Python, `node --check` on the host script, locale key count (20 × 8) | clean |
| tests/unit, locale + i18n subset | 1868 passed (after the locale version bump below) |
| gitleaks on the new files | no leaks |

Locale cache bust: new keys need a new `LOCALE_VERSION` or cached clients show raw keys. Done the project's way:
`static/i18n-i18next.js` → `2026-10-20-jarvis-task-prompt`, old value added to `RETIRED_LOCALE_VERSIONS`,
`LOCALE_KEY_SIGNATURE` refreshed in `tests/unit/test_locale_cache_bust_contract.py`.

Known failures not caused by this pass: `tests/unit/test_theater_numeric_v2_*` (23 fail on an untouched copy of HEAD
too); `scripts/check_*` report a UTF-8 BOM in `jarvis/wake/wake_listener.py` and two other JARVIS files (present at HEAD).

## 7. Not done, and why

- **Pause** (as distinct from stop/resume): main_agent has stop and resume from checkpoint; a true pause needs each
  step to support it. Not built.
- **Mission Control states beyond `waiting_for_input`**: the widget already shows planning/running/held/done; a fuller
  state board was not built in this pass.
- **9Router**: not in this repo or tools inventory. Installing and configuring it is a new external tool and a
  provider-settings change: needs the owner's approval.
- **Model selector / provider router**: phase 1 (recommend only) built afterwards: MODEL_SELECTOR_2026-10-20.md. Wiring it into the live route needs approval.
- **Saving a secret for later** (e.g. an API key the owner wants kept): today secrets are used once and dropped. A
  stored-secret path (into `D:\jarvis-secrets`, by file name, like the other connectors) needs approval.
- **OTP flows**: the secret field works for a one-time code (used once, dropped), but no connector asks for one yet.

## 8. Needs the laptop (blocked here: no mic, no Windows, no Electron)

1. Build: in `frontend\react-neko-chat` run `npm ci` and `npm run build`.
2. Start N.E.K.O and the plugin host as usual; open the chat (overlay and the Electron chat window).
3. Ask the PC agent something vague ("open my report"); the card should appear with its question; answer it; the task
   should continue. Try "Later", then "Answer"; try "Cancel question".
4. Voice: "Hey Neko, the answer is the one on the desktop" while a text question waits; check it resumes. With a
   secret question waiting, a spoken answer must be refused.
5. Check typing in the chat is never interrupted when a question appears (focus must stay where it was).
6. Windows Narrator / screen reader pass on the card.

## 9. Approvals needed from the owner

1. A stored-secret path (keep a key the owner types, in the secrets folder by name).
2. Installing 9Router and adding providers to it.
3. Any change to production provider or model routing (model selector).
4. Downloading any new model.
