# 04. Agentic OS and UI: research and recommended front-end architecture

Date: 2026-10-11. Author: technical researcher (agent). Scope: the front-end side of JARVIS (nine screens), how it should talk to Hermes and the kernel, what to reuse, what to build.
Status: research only. Nothing in D:\jarvis\hq was changed except this file. Claims I could not verify in this session are marked **UNVERIFIED**.

## 0. Summary of decisions

| Topic | Recommendation |
|---|---|
| Front-end stack | New React 19 + TypeScript + Vite 8 + Tailwind 4 app in `hq/ui/`, served by the existing Caddy beside the vanilla hub (path `/app/`, same origin `hub.localhost:8088`). Hub stays until the new app reaches parity. |
| State | TanStack Query for REST reads, one small zustand store for live events, nothing else. |
| Chat/transcript | assistant-ui primitives (MIT) over our own store, markdown through react-markdown without raw HTML (or streamdown after a security read). |
| Streaming | One `EventSource` for kernel events (audit `seq` as SSE id) and at most one for the active Hermes run. Browser-facing event vocabulary follows AG-UI (MIT) with CUSTOM events for approvals/tasks. |
| Talking to Hermes | Hermes API server (`/v1/runs`, `/api/sessions`, `/api/jobs`, `/v1/skills`...) through a thin owner-lane BFF on the kernel host. The Hermes key never reaches the browser. tui_gateway JSON-RPC only for `learning.*`, `display.*`, `profiles.*`, `approval.pending`. |
| Agent PC | Keep `lab_control.py` (freezes OpenCode and goose too, which Hermes' lease cannot). Replace the iframe + DOM poke with noVNC's `RFB` class in a React component, copying Hermes Desktop's `screen-pane.tsx` pattern. |
| Voice | Push-to-talk first: `getUserMedia` + AudioWorklet/MediaRecorder to a local STT server (speaches or whisper.cpp, both MIT). No Web Speech API by default (cloud). Wake word is an experiment last. |
| Safe preview | Keep outputs on `outputs.localhost` with the sandbox CSP; render PDF with pdf.js (`isEvalSupported:false`), docx/xlsx by lab-side conversion or a worker parser into escaped DOM, never `innerHTML` of file content. |
| Tests | vitest 5 (+ Testing Library, jsdom) and Playwright 1.64 via the installed Edge channel, axe-core for accessibility. |
| Open decision for the owner | Whether the browser may approve actions at all (today: console or Telegram only, CLI says "a script or model cannot approve"). Section 7. |

## 1. What exists today (verified in the working tree)

### 1.1 Hub (`D:\jarvis\hq\hub\`)
- `index.html` is 81,953 bytes, one file, no framework. `build_hub.py` inlines the Lucide sprite and `@fnando/sparkline` and copies Geist fonts into one output dir (`hub/build_hub.py` lines 10-22).
- Structure: sidebar `nav.side` (line 313) and header `.top` (320), `main#stage` (338) with one home view `#view-home` titled "Mission control" (339-363) and one lazily created iframe view per service (`ensureView`, line 472). Right rail "Live activity" (365), `dialog#palette` command palette with combobox/listbox roles (375-377), `dialog#preview` (378), toast region `aria-live="polite"` and an assertive announcer (380-381), skip link (311).
- Service list `SERVICES` (lines 402-420): Agent PC `/pc/vnc.html?...`, openmuse, lab Hermes, OpenCode, server Hermes (`hermes.localhost`), OmniRoute, plus model endpoint/Dahl/OpenJev health-only entries.
- Data: `fetch("/api/kernel/overview")` (line 544), `/control/*` with header `X-Jarvis-Hub: 1` (551, 568), `/status-check?url=` (529). Polling, no SSE or WebSocket in the hub's own code. Persistence is `localStorage` keys `jarvis-theme`, `jarvis-side`, `jarvis-rail-*`, `jarvis-hub-panel` (391-392, 430-436).
- Theme: CSS custom properties on `:root`/`[data-theme=dark]` (lines 15-29) and `[data-theme=light]` (30-39), Radix-like scales (`--accent:#5b5bd6`, status pairs `--run/--wait/--block/--err/--done` with `-fg`/`-bg`). Motion tokens and `prefers-reduced-motion` support (305-306). These tokens should be ported unchanged.
- Preview: `preview()` (776-790) shows images and media directly, HTML in `<iframe sandbox="" referrerpolicy="no-referrer">`, other text as escaped `<pre>` capped at 200,000 characters.
- Take control: `setViewOnly()` reaches into the noVNC iframe's DOM and flips the element `noVNC_setting_view_only` (578-584); on failure it reloads the iframe (588). This is fragile (depends on noVNC's internal ids) and is a client-side fence only.

### 1.2 Caddy (`hub/Caddyfile`)
Confirmed answer to question 7: outputs are served on a separate origin.
- `http://outputs.localhost:8088` (lines 42-52): `root /mnt/jarvis-outputs` (read-only mount for the `hub` user), headers `Content-Security-Policy "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'"`, `X-Content-Type-Options nosniff`, `Access-Control-Allow-Origin http://hub.localhost:8088`, `Cache-Control no-cache`, `file_server browse`.
- Hub origin `http://hub.localhost:8088` (10-38): `/status-check*` to 127.0.0.1:4000, `/control/*` to the Unix socket `/run/jarvis-lab/control.sock`, `/pc/*` to noVNC 127.0.0.1:6080, `/api/kernel/overview` rewritten to `/v1/jarvis/overview` on `100.125.91.51:8790` with `Authorization: Bearer {$HUB_KERNEL_TOKEN}` injected by Caddy (the browser never holds it), static files from `/opt/jarvis-hub/site`.
- Other dashboards each on their own `*.localhost:8088` name: `pc`, `muse`, `lab`, `code` (Basic auth injected), `hermes` (to 100.125.91.51:9119), `router` (OmniRoute; frame headers rewritten to allow the hub only).
- Everything binds 127.0.0.1; `auto_https off`, plain HTTP. `*.localhost` is a secure context (section 5), so HTTPS is not needed for the microphone.
- Observations: no Content-Security-Policy on the hub origin itself (the page uses inline script and style); the `X-Jarvis-Hub: 1` header is checked only inside `lab_control.py`, not by Caddy; `pc.localhost:8088` exposes noVNC with full input and no hub header at all.

### 1.3 Kernel HTTP surface (`kernel/jarvis_kernel/llm_proxy.py`)
`ThreadingHTTPServer`, `HTTP/1.0` (connection closes after each response, line 397), bearer tokens with a lane (`private`, `public`, `dashboard`, `runner`; `AgentTokens.issue` line 62-65).

| Method + path | Lane | Purpose |
|---|---|---|
| `GET /v1/jarvis/overview` | dashboard only (403 otherwise) | Whitelisted read model (`overview.py`) |
| `GET /v1/models` | any agent token | model list + context length |
| `GET /v1/jarvis/jobs/{id}` | owner of job | job state |
| `POST /v1/chat/completions` | private/public | scrubbed, lane-checked model call, supports `stream` |
| `POST /v1/jarvis/actions` | private/public | tool/op actions: `output.save|show`, `ui.*`, `pc.*`, `skill.activate|deactivate`, `models.status|prefer`, `task.create|update|get|dispatch|checkpoint`, `memory.list|get|resolve|render|add|update|forget`, `lab.task` |
| `POST /v1/jarvis/runner/{next,status,report,deliver,delivery,finish}` | runner only | lab runner protocol |

The dashboard lane is rejected on every POST (line 448). Approvals have no HTTP route: `cli.py` `approve` is interactive-only ("a script or model cannot approve", line 1), plus the Telegram bridge (currently off).

`overview()` shape (`overview.py` lines 118-136): `now, killed, kpis{model_calls_24h, failed_requests_24h, success_rate_24h, actions_24h, jobs_24h, avg_job_seconds_24h, approvals_pending, est_tokens_in_24h}, series_24h{calls,failed,actions}, agents[], actions_24h{}, providers[], nodes[{name,online,seconds_since_report}], outputs[{node,agent,name,bytes,modified}] (max 40 per node), tasks[15 recent], approvals[{id,level,expires,tool,op,target,lane}], jobs[25], audit[40 newest, whitelisted keys]`. Task fields: `id, created, updated, owner, lane, source, request, goal, plan, criteria, state, step, attempts, max_attempts, deadline, result, evidence, verification, events`. Task states: `planning, waiting_approval, queued, running, verifying, repairing, paused, completed, failed, cancelled` (`tasks.py` line 15). Memory entries: `id m-xxxxxxxx, kind preference|fact|instruction, text (<=500), version, scope, history`, update needs `expected_version`, forget is by id (`memory.py`).

### 1.4 Agent PC control (`lab/lab_control.py`)
Unix-socket HTTP via Caddy `/control/*`: `GET /control/state` (mode `agent|owner`, running tasks with goal/seconds/paused/last step, recent actions), `POST /control/take` (SIGSTOP every agent task tree and screen driver, windows the agents opened stay usable), `POST /control/continue` (SIGCONT), `GET /control/screen.jpg` (800x450 JPEG, cached 3 s). Header `X-Jarvis-Hub: 1` required. A 2 s guard re-freezes tasks started while the owner has control. The lab runner honours kernel task states `paused` (freezes) and `cancelled` (stops) (`lab_runner.py` lines 92-99), so "pause/stop a lab task" can be a kernel state change.

### 1.5 How JARVIS already calls Hermes
`telegram.py` `hermes_agent()` (lines 64-106): `POST {base}/v1/chat/completions` on the Hermes API server (default `http://127.0.0.1:8642`), `Authorization: Bearer <key from a file>`, header `X-Hermes-Session-Id`, non-streaming, one session per day, a fresh session after a failure. Architecture doc: `jarvis-hermes-gateway` (API server on 127.0.0.1:8642) and `jarvis-hermes-serve` (backend for the Hermes desktop app, 100.125.91.51:9119, basic auth) on the server VM; Hermes v0.21.6 pinned at commit 818c13be (`docs/02_ARCHITECTURE.md` line 81).

## 2. Findings

### 2.1 OpenHands (investigation 1)
Sources: GitHub API and raw files on 2026-10-11; sparse clone `D:\jarvis\research\openhands-ui-sparse` at SHA `e0a26af82f0b2016423c0d53cc9ae32a357cef2d` (4.1 MB, never executed).

- Product and repo: the UI is now "Agent Canvas" (`@openhands/agent-canvas` 1.26.0, release v1.26.0 published 2026-10-08, node >= 24). It is the UI for the separate OpenHands Agent Server (repo `OpenHands/software-agent-sdk`). License MIT for both (GitHub license API; single root `LICENSE`, "Copyright (c) 2025 OpenHands contributors"). No `enterprise/` folder at root in the current tree.
- Stack (root `package.json`): React + react-router (framework mode, Vite), zustand, TanStack Query, Tailwind 4 (`@tailwindcss/vite`), `@xterm/xterm` + `addon-fit`, `monaco-editor`, vitest, Playwright, MSW, Electron + electron-builder, i18n. No `@novnc` dependency, and no `vnc` string in the sparse-cloned components.
- Event rendering: agent-server events are typed objects (`ActionEvent`, `ObservationEvent`, `MessageEvent`, system events; SDK files `event/llm_convertible/{action,observation,message,system}.py`). The chat renders them through `components/conversation-events/chat/` (`event-message.tsx`, `group-events.ts`, `event-thought-helpers.ts`, `task-tracking`). `group-events.ts` folds runs of two or more consecutive action/observation cards into a collapsible group, with dedicated renderers (group breakers) for finish, think, message, errors, plan preview, task tracker. Tool visualizers are a registry (`features/chat/tool-visualizers/{bash,file-editor,search,task,...}` plus `dispatcher.tsx`). This grouping and per-tool visualizer registry is the pattern worth copying.
- Transport: WebSocket `/sockets/events/{conversation_id}` with `resend_mode=all|since&after_timestamp=`, and `/sockets/bash-events`. Auth: first frame `{"type":"auth","session_api_key":...}` (10 s allowed), or deprecated query parameter, or header `X-Session-API-Key` for non-browser clients; close codes 4001 (auth) and 4004 (not found), 1013 for connection limits. REST: events search/get/batch, post message, respond to confirmation; `GET /vscode/url` and `GET /vscode/status`.
- User control: there is no remote-desktop "take over". The interaction model is Pause (cooperative: takes effect between agent steps, not during an LLM call, per SDK docs), a Stop button (`chat-stop-button.tsx`), confirmation mode (`confirmation-mode-enabled.tsx`, agent waits for user approval), and sending a message mid-run (`pending-user-messages.tsx`). Agent state chips in `features/controls/agent-status.tsx`.
- Embedding: terminal = xterm.js fed by the bash-events socket; VS Code = the server returns a URL from `/vscode/url`, the UI embeds it (iframe; exact component not read, UNVERIFIED); "browser" tab = screenshot `<img>` per observation (`browser-snapshot.tsx`), not a live view.
- Voice: `chat-dictation-button.tsx` + `useDictation` (states idle/starting/recording/transcribing). Same shape as OpenJarvis' hook.
- Copy vs reuse: MIT allows copying files with the notice. Copy as patterns: event grouping, tool-visualizer registry, agent-status mapping, stop/pending-message UX. Do not adopt the whole app: it is coupled to its own agent server and Electron/react-router framework mode.

### 2.2 OpenJarvis frontend/desktop (investigation 2)
Local clone `D:\jarvis\research\OpenJarvis` at `ecbde4b32091e2a8b02e8acfa41cb5f95e4da560` (2026-10-10), Apache-2.0 (`LICENSE`).
- `frontend/` is package `openjarvis-chat` 1.0.1: React 19.3, react-router 8, Tailwind 4, zustand 5, Base UI + shadcn components (`src/components/ui`), `react-markdown` + `remark-gfm` + KaTeX, recharts, motion, sonner, Tauri 2 plugins, vite 8, vitest 5. Engines `node >=22.22`, `npm >=11.19 <12` (the laptop's npm 11.17.0 is below that pin, harmless unless its lockfile is copied). `desktop/src-tauri` and `frontend/src-tauri` hold the Tauri shell.
- Chat: `components/Chat/{ChatArea,InputArea,MessageBubble,ToolCallCard,ResearchTimeline,XRayFooter,SystemPanel,MicButton,AudioPlayer,SpeakMessageButton}.tsx`. Streaming: `lib/sse.ts` is a plain `fetch` + `ReadableStream` SSE reader (`event:`/`data:` lines, `[DONE]`), `lib/useAgentEvents.ts` a WebSocket `/v1/agents/events` with the API key carried in WebSocket subprotocols (`openjarvis.auth.v1`, `openjarvis.key.b64url.<key>`), so keys stay out of URLs.
- Voice: `hooks/useSpeech.ts` (92 lines): `getUserMedia({audio:true})` -> `MediaRecorder` -> POST blob to `/v1/speech/transcribe`; probes `/v1/speech/health`; errors "Microphone not supported" / "Microphone access denied". Backend `src/openjarvis/speech/faster_whisper.py`. This is the push-to-talk MVP, copyable (Apache-2.0).
- Agent management UI: `components/Desktop/AgentsPanel.tsx` (1216 lines) has a launch wizard (template, tools, schedule, budget, learning toggle) over `/v1/managed-agents` (list/create/update/delete, `pause`, `resume`, `run`, `recover`, `tasks`, `channels`, `state`, `messages`) and `/v1/templates`. `MemoryBrowser.tsx`, `LearningCurve.tsx`, `TraceDebugger.tsx`, `CommandPalette.tsx`, `ApprovalBell.tsx` are relevant references.
- Do not copy: `lib/analytics.ts` wraps `posthog-js` (autocapture, session replay), `lib/supabase.ts`, `OptInModal.tsx`. These conflict with the egress rules (`config/egress.json`); strip if any file is reused. Backend contracts are OpenJarvis' own, not ours.
- Reuse verdict: UI components and the wizard UX are reusable as patterns; the existing hub already vendors OpenJarvis parsers under Apache-2.0 (`skills/vendor/openjarvis`), so NOTICE handling exists.

### 2.3 Hermes Agent interfaces (investigation 3)
Local clone `D:\jarvis\research\hermes-agent-818c13be` (pinned `818c13be1dc4fd28987e1e881a9408224afd4535`, MIT). The clone is a backend-only subset: it has no `tui_gateway/`, `web/`, `apps/`, `acp_adapter/`. For UI-side evidence I made a sparse shallow clone of upstream: `D:\jarvis\research\hermes-ui-sparse` at `9456670d2f9e6936d3b53e23cecc6a702bd0a365` (2026-10-10; paths `apps/shared`, `apps/desktop/{src,electron}`, `web/src`; 60 MB; never executed). Upstream is newer than the pin, so RPC names below come from upstream HEAD and are **UNVERIFIED against the running v0.21.6**; feature-detect at runtime.

Hermes documents three programmatic interfaces (official "Programmatic Integration" page): ACP (JSON-RPC over stdio, for editors), TUI gateway (JSON-RPC over stdio or WebSocket, "custom hosts needing fine-grained control"), API server (HTTP + SSE, OpenAI-compatible).

**A. API server** (`gateway/platforms/api_server.py`, `_http_route_table` line 1718; enabled with `API_SERVER_ENABLED`, `API_SERVER_KEY`, optional `API_SERVER_HOST`/`API_SERVER_PORT` (default 127.0.0.1:8642) and `API_SERVER_CORS_ORIGINS`). Names only, no secrets.
- Auth: `Authorization: Bearer <API_SERVER_KEY>` (constant-time compare, `_check_auth` line 1559). The server refuses to start with a missing, placeholder or under-16-character key (line 4413-4437). CORS is off unless origins are listed; response security headers include `CSP default-src 'none'; frame-ancestors 'none'`, `Permissions-Policy: camera=(), microphone=()` (lines 800-807).
- Health/meta: `GET /health`, `/v1/health`, `/health/detailed`, `GET /v1/models`, `GET /api/model/options`, `GET /v1/capabilities` (feature flags and an `endpoints` map; static flags include `run_events_sse`, `run_approval_response`, `run_steer`, `reasoning_streaming`, and `False` for `admin_config_rw`, `memory_write_api`, `jobs_admin`, `audio_api`, `realtime_voice`), `GET /v1/skills`, `GET /v1/toolsets`.
- Chat: `POST /v1/chat/completions` (stateless; SSE `chat.completion.chunk` plus custom `hermes.tool.progress`), `POST /v1/responses`, `GET|DELETE /v1/responses/{id}`.
- Sessions: `GET|POST /api/sessions`, `GET|PATCH|DELETE /api/sessions/{id}`, `GET .../messages`, `POST .../fork`, `POST .../chat`, `POST .../chat/stream`, `POST .../model`.
- Runs (best fit for the Command Center): `POST /v1/runs` (body: `input`, optional `session_id`, `previous_response_id`, `instructions`, `model`; header `Idempotency-Key` 1-255 visible ASCII, replay returns `Idempotency-Replayed: true`), `GET /v1/runs/{id}`, `GET /v1/runs/{id}/events` (SSE; resume with `Last-Event-ID` or `?last_seq=`; backlog 1000 events, a `replay.truncated` event when older events were dropped; `: keepalive` comment after 10 s idle), `POST /v1/runs/{id}/approval` (`choice` once|session|always|deny, optional `request_id`, `all`), `POST /v1/runs/{id}/steer` (`input`), `POST /v1/runs/{id}/stop`.
- Run event names seen in `api_server_runs.py`: `run.queued`, `run.started`, `message.started`, `message.delta`, `message.interim`, `assistant.completed`, `reasoning.available`, `tool.started` (fields `tool`, `preview`), `tool.completed` (`tool`, `duration`, `error`), `tool.failed`, `tool.progress`, `subagent.start`, `subagent.complete`, `approval.request` (fields from the approval payload, `command` redacted, `choices[]`), `approval.responded`, `run.steered`, `run.stopping`, `run.completed`, `run.failed`, `run.cancelled`, `run.interrupted`, `replay.truncated`. Each frame is `{event, run_id, timestamp, ...}` as `data:` JSON with `id:` = sequence. Terminal payloads include usage (`input_tokens, output_tokens, total_tokens, cache_read_tokens, cache_write_tokens`).
- Jobs (cron): `GET|POST /api/jobs`, `GET|PATCH|DELETE /api/jobs/{id}`, `POST /api/jobs/{id}/pause|resume|run`.
- Other: `POST /v1/artifacts/upload`, `GET /v1/artifacts/download/{id}`, browser-control (`POST /v1/browser-control/register`, `GET /v1/browser-control/ws`, opt-in), per-profile mirrors under `/p/{profile}/...`, session headers `X-Hermes-Session-Id` and `X-Hermes-Session-Key`.

**B. Dashboard/desktop backend (`hermes serve`, port 9119 here)**. React 19 + TS + Tailwind 4 + shadcn-style, Vite 8, FastAPI, xterm.js terminal over `WS /api/pty` (docs). Auth: loopback = per-process session token in header `X-Hermes-Session-Token`; non-loopback = auth provider gate (`basic` scrypt, `nous`, `self_hosted` OIDC) with HttpOnly cookie `hermes_session_at`; WebSockets use a single-use ticket from `/api/auth/ws-ticket` (`web/src/lib/api.ts`). REST (from `web/src/lib/api.ts` and docs): `/api/status` (public), `/api/sessions...`, `/api/config` (+`/defaults`, `/schema`, `/raw`), `/api/env`, `/api/logs`, `/api/analytics`, `/api/skills` (+`/toggle`, `/hub/*`), `/api/tools/toolsets`, `/api/cron/jobs` (+`pause|resume|trigger`), `/api/mcp/*`, `/api/memory` (+`/provider`, `/reset`), `/api/profiles` (+`/active`), `/api/curator`, `/api/credentials/pool`, `/api/pairing/*`, `/api/webhooks`, `/api/gateway/*`, `/api/ops/*`, `/api/system/stats`, kanban plugin under `/api/plugins/kanban/*` with `WS /api/plugins/kanban/events`. Kanban is single-host by design and its plugin routes are unauthenticated by default (docs), which is the reason `tasks.py` keeps kernel tasks as the authority.

**C. tui_gateway JSON-RPC (WebSocket `/api/ws` per docs; stdio for the TUI)**. `apps/shared/src/gateway-contract.openrpc.json` is a generated OpenRPC 1.3.2 contract with 257 methods, and `gateway-contract.generated.ts` the matching TypeScript types (python is the single source; a test fails when the committed file is stale). Methods relevant to JARVIS:
- Session/turn: `session.create|list|resume|history|interrupt|steer|branch|compress|usage|status|events.since`, `prompt.submit` (busy sessions queue/steer/redirect), `prompt.background`.
- Approvals: `approval.pending`, `approval.respond` (`choice`, `all`, `request_id`); server-to-client requests `approval`, `clarify`, `sudo`, `secret`, `terminal.read`, `preview.act|read`.
- Learning/memory: `learning.detail`, `learning.edit`, `learning.delete` ("archive a skill, restorable via curator, or remove a memory chunk"), `learning.frames`.
- Agent definition: `profiles.list|create|configure|describe|get_asset|set_asset`, `agents.list`, `skills.manage|reload`, `tools.list|configure`, `toolsets.list`, `mcp.servers.*`, `cron.manage` (list/add/remove/pause/resume).
- Screen: `display.start|stop|status|observe|thumbnail|lease.acquire|lease.release`.
- Voice: `voice.record` (VAD-bounded push-to-talk), `voice.toggle`, `voice.tts`, `wake.start|stop|pause|resume|status|feed`.
- Notifications (events): `gateway.ready`, `message.start|delta|interim|complete`, `reasoning.delta|available`, `tool.start|generating|complete|output_risk`, `subagent.start|progress|tool|thinking|complete`, `approval.cancelled`, `cron.changed`, `display.lease`, `display.status`, `voice.transcript|partial|status|interrupted`, `wake.detected`, `session.info|usage|title`, `status.update`, `todo.updated`.
- Handshake detail: after `gateway.ready` a WebSocket client must call `client.capabilities` with `{"server_requests": true}` once per connection or server-to-client requests are refused (docs).
- A 257-method surface that includes billing, pets, free-tier and `shared_metrics.*` is a reason to stay on the narrower API server for the BFF, and to use the RPC only for the handful of methods above.

**D. Bot Desktop and take-over lease (important, reuse-first relevant)**. `tools/bot_desktop/` exists in the pinned clone: `lease.py` (holder `agent|human`, `viewer_id`, `epoch`, authority in `<HERMES_HOME>/bot-desktop/lease.json` under an fcntl lock; the `computer_use` and browser tools refuse to act, even screenshots, while a human holds the lease), `rfb_filter.py`, `thumbnail.py`, `runtime.py` (Xvnc + Xfce per profile). The module states its own limit: "a TOOL-LEVEL fence, not a property of the X server"; a hand-started process (the terminal tool) is not stopped. JARVIS' `lab_control.py` freezes the whole process tree (including OpenCode and goose, which are not Hermes tools), so keep it; use Hermes' lease semantics (Watch / Take over / Hand back, epoch, viewer-hash) as the UI model and consider acquiring the Hermes lease in addition when the lab Hermes uses `computer_use` (UNVERIFIED whether it does; lab_control mentions `cua-driver`).

**E. ACP and MCP/A2A**
- ACP: `hermes acp`, `hermes-acp`, `python -m acp_adapter`; JSON-RPC over stdio only (`agent/acp_openai_bridge.py`, `plugins/model-providers/copilot-acp` exist in the pinned clone). Needs a host process to relay to a browser. Not for the web UI.
- MCP server: `hermes mcp serve` (stdio only) exposes 10 tools: `conversations_list`, `conversation_get`, `messages_read`, `attachments_fetch`, `events_poll`, `events_wait`, `messages_send`, `channels_list`, `permissions_list_open`, `permissions_respond`. Hermes as MCP client: `mcp_servers` in config.
- A2A: `plugins/platforms/a2a` (README: v1.0 Agent Card at `/.well-known/agent-card.json`, JSON-RPC `message/send`, `message/stream`, `tasks/get|list|cancel|subscribe`, per-peer tokens, localhost-only without a token). Agent-to-agent, not a UI protocol; potentially useful later for lab-to-server hand-off, out of scope for the UI.
- Kanban: see B; the kernel keeps its own task records.

**F. Hermes Desktop (`apps/desktop`, MIT per README and per-plugin LICENSE)**: Electron 40 + React 19.2 + react-router + TanStack Query + nanostores, `@assistant-ui/react` + `@assistant-ui/react-streamdown` + `streamdown`, `@novnc/novnc`, `@xterm/*`, `cmdk`, `dompurify`, `use-stick-to-bottom`, `@tanstack/react-virtual`, `@dnd-kit`, `mermaid`, `katex`, vitest + Playwright + Testing Library. Directories of direct interest: `src/app/{learning,artifacts,cron,command-center,profiles,agents,starmap,command-palette}`, `src/plugins/hermes-bots/{screen-pane.tsx, cron.tsx, profile-config.tsx, create-dialog.tsx, skills-hub.tsx}`, `web/src/pages/ProfileBuilderPage.tsx` (834 lines, the profile/agent builder: name regex `^[a-z0-9][a-z0-9_-]{0,63}$`, MCP server drafts, skill hub). `screen-pane.tsx` (569 lines, with several `screen-*.test.*` files) is the working reference for noVNC view/take-over: server-minted viewer id via `display.observe`, single-use ticket, `RFB.viewOnly = !leaseHeldBy(...)`, `scaleViewport = true`, clipboard paste forwarding, re-attach in watch mode on close code 4000 (`control-taken`), and the explicit comment that `viewOnly` is UX only while the gateway drops input from non-holders. It depends on `@hermes/plugin-sdk` and `@hermes/shared`, so copy the logic, not the file.

### 2.4 Other agent UIs and protocols (investigation 4)
License and activity from the GitHub license API and `pushed_at` on 2026-10-11 (unless noted). "Use" = what to take.

| Project | License | Last push | Use |
|---|---|---|---|
| assistant-ui/assistant-ui | MIT | 2026-10-10 | Headless chat primitives (thread, composer, tool UI, branching). Used by Hermes Desktop. npm 0.15.27, pre-1.0 churn: pin. |
| ag-ui-protocol/ag-ui | MIT | 2026-10-10 | Event vocabulary for agent-to-UI streams; `@ag-ui/core` 1.0.2 and `@ag-ui/client` 1.0.2 (MIT). |
| CopilotKit/CopilotKit | MIT | 2026-10-10 | AG-UI reference host; generative-UI patterns. Not adopted as a framework. |
| vercel/ai-elements | Apache-2.0 (LICENSE text; GitHub SPDX says NOASSERTION) | 2026-09-01 | shadcn-registry components for chat/tool/reasoning/plan cards; copy-in. |
| OpenHands Agent Canvas | MIT | 2026-10-08 (v1.26.0) | Event grouping, tool visualizers, status model (2.1). |
| Hermes Desktop / web dashboard | MIT | 2026-10-10 | Screen pane, learning, profile builder, cron UIs (2.3F). |
| OpenJarvis frontend | Apache-2.0 | 2026-10-10 | Wizard, memory browser, dictation hook (2.2). |
| goose (aaif-goose/goose) | Apache-2.0 | 2026-10-09 | Desktop agent UX reference; MCP Apps host. Not read in detail. |
| cline/cline | Apache-2.0 | 2026-10-10 | Approval/diff/checkpoint UX in an editor; patterns only. |
| BloopAI/vibe-kanban | Apache-2.0 | 2026-09-19 | Kanban-of-agents with per-task worktrees and diff review; pattern for the Tasks board. |
| builderz-labs/mission-control | MIT | 2026-10-09 | 6-column task board, agents, cost, 30+ panels; self-described alpha, Next.js + SQLite. Pattern only (its own stores would compete with the kernel, which `02_ARCHITECTURE.md` already rejected). |
| Mintplex-Labs/anything-llm | MIT | 2026-10-09 | Workspace/RAG UX; not needed. |
| dyad-sh/dyad | Apache-2.0 except `src/pro/` (separate license, not read) | 2026-10-09 | App-builder UX; avoid `src/pro`. |
| open-webui/open-webui | BSD-3-style + branding clause (cannot remove "Open WebUI" branding above 50 users per 30 days) | 2026-10-10 | Do not copy code. Patterns (model switcher, voice call overlay) only. |
| lobehub/lobehub | LobeHub Community License (Apache-2.0 + restrictions: derivative works need a commercial license) | 2026-10-10 | Do not copy code. |
| a2aproject/A2A | Apache-2.0 | 2026-10-08 | Agent-to-agent; not for the browser. |
| agentclientprotocol/agent-client-protocol | Apache-2.0 | 2026-10-10 | Editor-to-agent over stdio; not for the browser. |
| MCP Apps (SEP-1865) | spec; status "Final" per the SEP page, extension id `io.modelcontextprotocol/ui`, `ui://` resources rendered in sandboxed iframes | n/a | Park. Candidate for the Workspace screen later (interactive widgets from tools); the iframe sandbox model matches our preview model. |

Protocol decision:
- Adopt the AG-UI event vocabulary as the browser-facing contract (RUN_STARTED/FINISHED/ERROR, STEP_*, TEXT_MESSAGE_START/CONTENT/END, TOOL_CALL_START/ARGS/END/RESULT, STATE_SNAPSHOT/DELTA, ACTIVITY_*, REASONING_*, CUSTOM). Hermes run events map almost one to one (section 6.2). Reason: it decouples the UI from Hermes version drift, gives validated TypeScript types, and lets kernel events (task, job, approval) ride as CUSTOM or STATE_DELTA events through the same reducer.
- Known mismatch: AG-UI models human-in-the-loop as a run that finishes with an `interrupt` outcome and is resumed by a new run, while Hermes parks the same run in `waiting_for_approval` and resolves it with `POST /v1/runs/{id}/approval`. Carry approvals as CUSTOM events (`jarvis.approval.request` / `.responded`) rather than forcing the interrupt model.
- Do not adopt A2A, ACP or MCP-UI/MCP Apps for the browser path. No official AG-UI adapter for Hermes exists (a feature request is open, `hermes-agent#501`; web search result, not opened), so the adapter is custom: about 150 lines in the BFF, justified in `01_RESEARCH_AUDIT.md` against AG-UI's own adapters.

### 2.5 Browser voice (investigation 5)
Facts verified this session unless marked.
- Secure context: MDN Secure Contexts lists `127.0.0.0/8`, `::1`, `localhost` and any host ending in `.localhost` as potentially trustworthy, so `http://hub.localhost:8088` and `http://127.0.0.1:8088` can use `getUserMedia` over plain HTTP. `getUserMedia` is undefined and throws `TypeError` on an insecure origin, for example the Tailscale address `http://100.x.x.x`: using the new app from a phone over Tailscale would need HTTPS (Caddy internal CA or `tailscale cert`), **UNVERIFIED** for this setup.
- Permission is per origin: a grant for `hub.localhost:8088` does not carry to `127.0.0.1:8088` (standard behaviour, **UNVERIFIED** in Edge here). Pick one canonical origin.
- Iframes: only the top-level document can ask; an iframe needs `allow="microphone"` and the parent's Permissions-Policy must allow it; a sandboxed iframe cannot call `getUserMedia` unless it has `allow-same-origin` (MDN). The hub embeds every panel in an iframe, so the voice UI must live in the top-level page. The hub's `f.allow` currently lists only clipboard and fullscreen (line 499).
- Errors to handle (MDN): `NotAllowedError` (blocked by user, policy or insecure context), `NotFoundError` (no device), `NotReadableError` (device busy or OS-level block; typical when Windows "Microphone privacy" blocks desktop apps or another app holds the device), `OverconstrainedError`, `SecurityError`, `AbortError`; the permission prompt promise may never settle (add a timeout and a visible "waiting for permission" state). Give separate help text for: site blocked in Edge (`edge://settings/content/microphone`), Windows privacy toggle (Settings > Privacy & security > Microphone), device in use.
- Web Speech API: MDN marks `SpeechRecognition` "Limited availability, not Baseline"; in Chrome "your audio is sent to a web service". That is a cloud path and would violate the private-lane rule; make it an explicit opt-in labelled "cloud", off by default. Edge's backend is also an online service (**UNVERIFIED**).
- Capture design: `getUserMedia({audio:{echoCancellation:true, noiseSuppression:true, autoGainControl:true, channelCount:1}})`. Two tiers: (1) MVP = `MediaRecorder` (webm/opus) on key-down/key-up, POST the blob to an OpenAI-compatible `/v1/audio/transcriptions` endpoint (copy OpenJarvis `useSpeech.ts`); (2) streaming = AudioWorklet downsampling to 16 kHz PCM16 and a WebSocket to the STT server (Hermes' own `tools/transcription_streaming.py` contract is 16 kHz mono s16le, useful as the format). AudioWorklet also requires a secure context.
- STT server candidates (both MIT, verified from LICENSE files): `speaches-ai/speaches` (OpenAI-compatible HTTP, faster-whisper) and `ggml-org/whisper.cpp` server. Hermes itself cannot serve the browser: its API server flags `audio_api: False`, `realtime_voice: False`; its `voice.record` RPC records on the server side (no browser mic). Model download sizes are my estimate (tiny ~75 MB, base ~140 MB, small ~460 MB), **UNVERIFIED**; check D: free space (44 GB free now) before downloading.
- Wake word in the browser: official openWakeWord (`dscripka/openWakeWord`) is Python-first; its FAQ says a JavaScript port is "not on the roadmap" and shows a websocket-to-Python workaround. Code is Apache-2.0 but the pretrained models (including `hey_jarvis`) are CC BY-NC-SA 4.0 (non-commercial; fine for personal use, record it). Community browser ports exist: `openwakeword-wasm-browser` 0.1.1 (MIT, last modified 2025-11-23) and `openwakeword-web` 0.1.0 (Apache-2.0, 2026-06-30), both onnxruntime-web (MIT, 1.30.0). Feasible but early and unreviewed: needs the melspectrogram, embedding and classifier ONNX models hosted locally, an always-open tab with mic permission, and tuning for false accepts. Treat as a late experiment. Note that Hermes exposes `wake.*` RPC for its own surfaces (UNVERIFIED how it captures audio).
- Barge-in/echo: use `echoCancellation: true` and recommend headphones; run a VAD (`@ricky0123/vad-web` 0.0.31, ISC, bundled Silero model MIT, onnxruntime-web) on the mic while the assistant speaks; on speech start pause TTS immediately and call `POST /v1/runs/{id}/stop` or `/steer`. Whether the browser's AEC removes the page's own playback is platform dependent, **UNVERIFIED** for Edge on this laptop; test with speakers and headphones.

### 2.6 Front-end build decision (investigation 6)
Local tools (only `--version` run): Node `v24.19.0`, npm `11.17.0` at `C:\Program Files\nodejs`. D: had 45 GB free before and 44 GB after; research downloads total 86 MB.

Measured with `npm pack` tarballs (no install scripts, no code executed), directory `D:\jarvis\research\ui-size-probe`:

| Item | Raw | Gzip | Note |
|---|---|---|---|
| preact 11.0.1 `dist/preact.mjs` | 11,897 B | 4,971 B | core only; `hooks`/`compat` extra |
| htmx.org 2.0.11 `htmx.min.js` | 52,182 B | 16,838 B | 0BSD |
| alpinejs 3.17.4 `cdn.min.js` | 55,891 B | 19,906 B | |
| marked 18.1.0 `marked.umd.js` | 47,225 B | 14,296 B | |
| dompurify 3.4.16 `purify.min.js` | 28,885 B | 11,374 B | |
| react-dom 19.3.0 `cjs/react-dom-client.production.js` | 625,168 B | 110,528 B | not minified: upper bound; a minified Vite bundle of react + react-dom is commonly 55-60 KB gzip (estimate, **UNVERIFIED**) |
| react 19.3.0 `cjs/react.production.js` | 18,040 B | 4,626 B | |
| npm unpacked sizes | react 178,663; react-dom 8,063,782; zustand 95,173; @tanstack/react-query 1,731,356; react-router 2,856,262; vite 2,376,813; tailwindcss 772,893; pdfjs-dist 34,913,648 (lazy chunk only) | | `npm view` |

All of this is served over loopback (sub-millisecond transfer), so bundle size is not a deciding factor. Ecosystem and test tooling are.

| Criterion | (a) React + TS + Tailwind (Vite) beside hub | (b) vanilla + modules | (c) Preact / HTMX / Alpine |
|---|---|---|---|
| Reuse-first fit | Best: Hermes Desktop, OpenJarvis and OpenHands are all React 19 + Tailwind 4 (+ zustand/nanostores, TanStack Query, vitest, Playwright); components and tests can be copied | Nothing to copy; every pattern re-typed | Preact works with most React libraries through `compat`, but assistant-ui, Radix/Base UI and React 19-only APIs are a risk (**UNVERIFIED**); HTMX needs server-rendered fragments, the kernel is a JSON read model |
| Accessibility primitives | Radix/Base UI dialogs, combobox, tabs, focus traps (audited) | Hand-rolled (the hub already did a decent palette) | Alpine: hand-rolled; Preact+compat: same as React if it works |
| Streaming/transcript UX | assistant-ui, streamdown, virtualisation available | Hand-rolled | Hand-rolled |
| Type safety / contracts | TS + generated types (AG-UI, OpenRPC from Hermes) | None | Preact: TS fine; HTMX/Alpine: none |
| Testing | vitest + Testing Library + Playwright | Playwright only | Preact: same as React; HTMX/Alpine: Playwright only |
| Tooling cost | Node 24.19 present; `node_modules` of a few hundred MB on D: (estimate); build step; static output deployed to `/opt/jarvis-hub/app` like `build_hub.py` | None | Low |
| Migration risk | Low: separate path, same origin and tokens, hub untouched | n/a | Low |
| Long-term maintainability for 9 screens | Good | Poor beyond about 3 screens (the single file is already 82 KB) | Mixed |

Recommendation: (a). Concretely:
- Directory `hq/ui/` inside the existing repo (no new repo, per the reuse-first rule). Output served at `/app/` on the hub origin so `localStorage` theme keys, same-origin `/api/*` and `/control/*` and the (future) CSP are shared. Use `HashRouter` to avoid Caddy rewrites.
- Libraries: react 19.3, react-router 8, @tanstack/react-query 5.104, zustand 5, tailwindcss 4.3 (`@theme` fed by the hub's CSS variables), Radix or Base UI primitives (shadcn copy-in pattern), lucide-react (ISC), `@novnc/novnc` 1.7.0 (MPL-2.0, use unmodified), `@xterm/xterm` 6 only if a terminal view is added, `react-markdown` 10 + `remark-gfm`, `@ag-ui/core`, `@assistant-ui/react` pinned, `cmdk` for the palette. Lazy-load pdf.js, onnxruntime-web, VAD.
- Keep the hub as the shell until parity; link new screens from the hub nav; later the new app embeds the existing dashboards panels by iframe exactly as the hub does.
- Lint/type/test scripts: `tsc -b`, `vitest run`, `playwright test`.

Testing approach:
- Unit/component: vitest 5.0.3 + @testing-library/react + jsdom. Test the SSE parser (copy `lib/sse.ts` shape, comment lines starting `:` skipped), the Hermes-to-AG-UI adapter against recorded frames, state chips for the ten task states, approval card logic, mic error mapping.
- End to end: `@playwright/test` 1.64 (Apache-2.0) using the installed Edge (`channel: "msedge"`) so no browser download; network mocked with `page.route` for kernel/Hermes JSON and SSE; mic via launch args `--use-fake-ui-for-media-stream --use-fake-device-for-media-stream` (and `--use-file-for-fake-audio-capture=<wav>`) plus `permissions: ["microphone"]` (**UNVERIFIED** flag behaviour in current Edge); keyboard-only flows via `page.keyboard`; `colorScheme` and `reducedMotion` emulation for light/dark; `toHaveScreenshot` for theme regressions; `@axe-core/playwright` 4.13 (MPL-2.0, test only) for WCAG checks.
- Cannot be tested headless: a real microphone and Windows privacy settings, a real Agent PC through noVNC, real Hermes approvals. These need manual gates per phase.

### 2.7 Safe preview of agent-produced files (investigation 7)
Current protection is good for HTML and images (section 1.2). Gaps and the target tiers:

| Type | Current | Recommended |
|---|---|---|
| png/jpg/gif/webp | `<img>` from outputs origin | keep; `loading="lazy"`, cap dimensions |
| svg | `<img>` (script never runs in an image context) | keep `<img>` only; never `<object>`, `<embed>`, inline `<svg>`, or direct navigation without the CSP (the outputs CSP also blocks it) |
| html | `<iframe sandbox="">` + CSP `sandbox` header + separate origin | keep. "Run interactive page" later as an explicit opt-in on a third origin with `sandbox="allow-scripts"` (no `allow-same-origin`) and CSP `connect-src 'none'` |
| markdown | text in `<pre>` | render with react-markdown without raw HTML, `rel="noopener noreferrer"`, images only from the outputs origin; if HTML must be allowed use DOMPurify (3.4.16, MPL-2.0 OR Apache-2.0) then a sandboxed `srcdoc` iframe |
| txt/json/csv/log/py | escaped `<pre>`, 200,000 char cap | keep; add a CSV table view with `textContent` only |
| pdf | not handled (download/raw) | pdf.js (`pdfjs-dist`, Apache-2.0) in a worker, canvas + text layer, `isEvalSupported:false`, no scripting/forms. Do not use the native viewer: Chrome's PDF viewer needs scripts and plugins, so it fails inside sandboxed frames (Chromium behaviour reported by MS Edge engineers and several vendors; not tied to a bug number here). Use a current pdfjs-dist: CVE-2024-4367 (arbitrary JS via font handling when `isEvalSupported` is true, fixed by removing `eval`, PR 18015) |
| docx | not handled | best: lab-side conversion to PDF (LibreOffice headless) delivered as a second file; client-side fallback `mammoth` 1.13 (BSD-2) -> DOMPurify -> sandboxed `srcdoc` iframe |
| xlsx | not handled | best: lab-side export to CSV/HTML; client-side `xlsx` (SheetJS CE, Apache-2.0; the npm copy 0.18.5 is old and known-vulnerable, SheetJS ships current builds from its own CDN: **UNVERIFIED** details) in a Web Worker with row/column caps, rendering with `textContent`; `exceljs` 4.4.0 (MIT) last published 2024-12 |
| video/audio | `<video>/<audio controls>` | keep; `preload="metadata"` |
| anything else | "Open raw" | `Content-Disposition: attachment` response header for unknown types on the outputs origin; show name, size, hash |

Additional hardening items:
1. Add a CSP to the hub/app origin once the inline script is gone (Vite emits external module scripts): `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: http://outputs.localhost:8088; media-src http://outputs.localhost:8088; frame-src http://*.localhost:8088; connect-src 'self' ws://hub.localhost:8088 http://outputs.localhost:8088; object-src 'none'; base-uri 'none'` (sketch, to be tested; pdf.js worker needs `worker-src 'self' blob:`).
2. `Permissions-Policy: microphone=(self)` on the app and `allow="microphone"` only where needed; `Cross-Origin-Opener-Policy` not required.
3. Add `X-Jarvis-Hub: 1` enforcement and an `Origin` check at Caddy for every mutating `/api/*` and `/control/*` route, not only inside lab_control.
4. Treat file names as data: escape everywhere (already done with `esc()`), never put them into `href` without the `encodeURIComponent` path form the hub uses.

## 3. License matrix (components proposed for use)

| Component | License | Mode | Notes |
|---|---|---|---|
| React / react-dom 19.3.0 | MIT | dependency | |
| TypeScript, Vite 8.3.4 | Apache-2.0 / MIT | dev | |
| Tailwind CSS 4.3.3 | MIT | dependency | |
| react-router 8.4.0 | MIT | dependency | |
| @tanstack/react-query 5.104.1, @tanstack/react-virtual 3.14.14 | MIT | dependency | |
| zustand 5.0.15 | MIT | dependency | |
| Radix UI, Base UI, cmdk 1.1.1, shadcn pattern | MIT | dependency / copy-in | |
| class-variance-authority 0.7.1 | Apache-2.0 | dependency | |
| lucide-react 1.55.0 | ISC | dependency | hub already vendors Lucide sprite (ISC) |
| Geist fonts | OFL | asset | already vendored |
| @assistant-ui/react 0.15.27 | MIT | dependency, pin | pre-1.0 |
| @ag-ui/core, @ag-ui/client 1.0.2 | MIT | dependency | types + reducer |
| react-markdown 10.1.0 / streamdown 2.7.0 | MIT / Apache-2.0 | dependency | read streamdown's HTML handling before use |
| @novnc/novnc 1.7.0 | MPL-2.0 | dependency, unmodified | file-level copyleft |
| @xterm/xterm 6.0.0 | MIT | optional | |
| pdfjs-dist 6.4.299 | Apache-2.0 | lazy dependency | `isEvalSupported:false` |
| DOMPurify 3.4.16 | MPL-2.0 OR Apache-2.0 | dependency | choose Apache-2.0 |
| mammoth 1.13.0 | BSD-2-Clause | optional | |
| docx-preview 0.4.1 | Apache-2.0 | optional | |
| xlsx 0.18.5 (SheetJS CE) | Apache-2.0 | optional, review | stale on npm |
| @ricky0123/vad-web 0.0.31 | ISC (+ Silero model MIT) | lazy dependency | |
| onnxruntime-web 1.30.0 | MIT | lazy dependency | |
| openwakeword-wasm-browser 0.1.1 / openwakeword-web 0.1.0 | MIT / Apache-2.0 | experiment | models CC BY-NC-SA 4.0 |
| speaches / whisper.cpp | MIT / MIT | server component | |
| vitest 5.0.3, msw 3.0.3 | MIT | dev | |
| @playwright/test 1.64.0 | Apache-2.0 | dev | |
| axe-core 4.14.0, @axe-core/playwright 4.13.0 | MPL-2.0 | dev (test only) | |
| Hermes Agent + Desktop + web | MIT | pattern/copy with notice | |
| OpenHands / software-agent-sdk | MIT | pattern/copy with notice | |
| OpenJarvis | Apache-2.0 | pattern/copy, keep NOTICE | existing vendor/NOTICE.md |
| htmx 0BSD, Alpine MIT, Preact MIT | | not selected | |
| Open WebUI, LobeHub, dyad `src/pro` | custom | do not copy | |

## 4. Recommended architecture

```
Browser (Edge/Chrome on Windows)            hub.localhost:8088 (Caddy in WSL, 127.0.0.1 only)
  /app/  React SPA  ------------------------> static files /opt/jarvis-hub/app
     |  REST (same origin)  /api/kernel/*  -> kernel 100.125.91.51:8790   (token injected by Caddy)
     |  SSE   /api/kernel/events           -> kernel (new, read only, id = audit seq)
     |  REST+SSE /api/jarvis/*             -> kernel "ui" lane BFF (new, owner verbs only) -> Hermes API 127.0.0.1:8642
     |  REST  /control/*                   -> lab_control socket (exists)
     |  WS    /pc/websockify               -> noVNC 127.0.0.1:6080 (exists)
     |  REST  /stt/*                       -> local STT server (new, optional)
     |  files outputs.localhost:8088       -> read-only outputs, sandbox CSP (exists)
     \  iframes: lab./code./hermes./router./muse.localhost  (unchanged)
```

Components:
- **State**: TanStack Query owns server snapshots (overview, tasks, memory, jobs, models). A single zustand store `liveStore` ingests events (kernel SSE and the active run's SSE), keyed by run/task id with sequence numbers for dedupe and resume (the hub's `feedKeys` set is the same idea). UI-only preferences in `localStorage` with try/catch as the hub does.
- **Event streaming**: kernel SSE `GET /v1/jarvis/events?since=<seq>` (new): tails the `audit` table by `seq`, emits `id: <seq>`, whitelisted fields only (reuse `overview._short`), heartbeat comment every 10 s, supports `Last-Event-ID`. Falls back to polling `overview` (the hub's current behaviour) when the stream is down. Hermes run stream proxied byte for byte then translated to AG-UI frames. Caddy needs `flush_interval -1` on these routes. Because the origin is plain HTTP/1.1, browsers allow about 6 concurrent connections per host; budget two SSE streams per tab (kernel + active run) and share them across screens (**UNVERIFIED** limit value for current Edge).
- **Auth**: unchanged principle: the browser never holds a token. Caddy injects per-route bearer tokens. New lane `ui` (owner verbs) must be separate from `dashboard` (read only), never given to agents, with an allow-list of verbs, audit entry per call, and the same-origin header and Origin check at Caddy. Hermes' API key stays on the VM, used only by the BFF. Hermes' own dashboard (9119) remains reachable only as an iframe panel (its basic-auth cookie flow is cross-origin from the app; calling it from the SPA would need CORS with credentials, **UNVERIFIED**).
- **Where the BFF lives**: inside the kernel process as new routes under `/v1/jarvis/ui/*` (the kernel is already the policy/audit authority and on the same VM as the Hermes API server). Custom code is justified because no existing project combines Hermes runs, kernel tasks/approvals/memory and the lab freeze in one read/write model; record it in `01_RESEARCH_AUDIT.md`.

## 5. Screen to API mapping

Legend: **E** exists today; **N** new (kernel `ui` lane or Caddy); H = Hermes API server (8642, via BFF); R = Hermes tui_gateway RPC (via BFF, feature-detected).

| # | Screen | Reads | Writes / commands | Stream |
|---|---|---|---|---|
| 1 | Command Center | E `GET /api/kernel/overview` (tasks, approvals, outputs); H `GET /api/sessions/{id}/messages`, `GET /v1/runs/{id}` | N `POST /api/jarvis/say {text, session}` -> H `POST /v1/runs` (+`Idempotency-Key`); N `POST .../runs/{id}/steer|stop` -> H; voice: `POST /stt/v1/audio/transcriptions` then say | N kernel SSE; N `GET /api/jarvis/runs/{id}/events` -> H `/v1/runs/{id}/events` (AG-UI translated) |
| 2 | Agentic OS desktop | E `GET /control/state`, `GET /control/screen.jpg`; kernel `nodes[]` for targets | E `POST /control/take`, `POST /control/continue`; N stop = kernel task `cancelled` (`POST /api/jarvis/tasks/{id}/state`), pause = `paused` | E noVNC WebSocket `/pc/websockify` through `RFB`; E poll `/control/state` every 2-3 s |
| 3 | Agent Factory | H `GET /v1/skills`, `/v1/toolsets`, `/v1/models`; R `profiles.list|describe`; kernel `skills/active.json` status via N read endpoint | N `POST /api/jarvis/agents/draft {nl}` (one private-lane model call through the kernel gateway, strict JSON schema, server-side validation); N `POST .../agents/{id}/activate` -> `tool=skill op=activate` policy (low auto, medium needs approval) or R `profiles.create|configure`; delete = deactivate with rollback | N kernel SSE for approval status |
| 4 | Tasks monitor | E `overview.tasks` (15), `overview.jobs` (25); N `GET /api/jarvis/tasks?state=&limit=&before=`, `GET .../tasks/{id}` (events up to 40, evidence, verification) | N pause/resume/cancel (`Tasks.update(None, id, state=...)`, only `paused`, `cancelled`, `queued`/`running` resume) | kernel SSE (`task_*`, `job_*` audit kinds) |
| 5 | Automation Studio | H `GET /api/jobs`, `GET /api/jobs/{id}` (Hermes cron); E kernel jobs history | H `POST|PATCH|DELETE /api/jobs`, `POST /api/jobs/{id}/pause|resume|run`; R `cron.manage` fallback | H none (poll 5-10 s); R `cron.changed` event |
| 6 | Workspace / deliverables | E `overview.outputs`; E `outputs.localhost:8088/<agent>/<name>` (listing with `file_server browse`) | none (read only). Delete/rename only through a kernel job later | n/a |
| 7 | Memory & learning | N `GET /api/jarvis/memory?scope=&q=` -> `Memories.list/get` (history included); H `GET /api/memory` or R `learning.detail|frames` for Hermes' own memory/skills | N `PATCH /api/jarvis/memory/{id} {text, expected_version}` (409 shows current), `DELETE .../{id}` (verified forget); R `learning.edit|delete` (skills archive is restorable via curator) | kernel SSE for memory audit kinds |
| 8 | Models / tools / skills | E `overview.providers` (+preferred, ok/failed counts); H `GET /v1/models`, `/api/model/options`, `/v1/skills`, `/v1/toolsets`; skill gate state N | N `POST /api/jarvis/models/prefer {provider}` (same lane only, 20/day, existing `tool=models op=prefer`); skill activate/deactivate as in 3 | n/a |
| 9 | Settings / health | E `GET /status-check?url=...`, `overview.killed`; H `GET /health/detailed`, `/v1/capabilities`; `GET /api/status` on 9119 (public) | N `POST /api/jarvis/kill` (fail-safe verb, allowed); resume stays console-only until the step-up design exists; theme/density in `localStorage` | n/a |

Approvals (cross-cutting, in the Command Center and a bell in the top bar): two sources with one inbox model, tagged by origin.
- Kernel approvals: E read via `overview.approvals`. Approve/deny verbs are the open decision (section 7).
- Hermes run approvals: H `approval.request` events; resolve with `POST /v1/runs/{id}/approval {choice, request_id}`; show the redacted command and the offered `choices[]` (do not offer `always` when not listed). Never auto-approve; never resolve `all` from a single click.

## 6. Hermes event to UI mapping

### 6.1 Normalised internal event (AG-UI names)
| Hermes run event | AG-UI event | UI effect |
|---|---|---|
| `run.started` | RUN_STARTED | progress bar to "working" |
| `message.started`, `message.delta` (`delta`), `assistant.completed` | TEXT_MESSAGE_START / CONTENT / END | streaming transcript |
| `reasoning.available` (`text`) | REASONING_MESSAGE_* | collapsible "thinking" |
| `tool.started` (`tool`, `preview`) | TOOL_CALL_START (+ARGS preview) | tool card, running |
| `tool.completed` (`tool`, `duration`, `error`) / `tool.failed` | TOOL_CALL_END + RESULT | tool card done/failed, grouped like OpenHands `group-events` |
| `subagent.start/complete` | SUBAGENT_STARTED/FINISHED | nested activity |
| `approval.request` / `approval.responded` | CUSTOM `jarvis.approval.*` | approval card |
| `run.steered`, `run.stopping`, `run.interrupted` | CUSTOM / RUN_FINISHED(outcome) | status chip |
| `run.completed` (usage) / `run.failed` / `run.cancelled` | RUN_FINISHED / RUN_ERROR | final state, usage counters |
| `replay.truncated` | CUSTOM | "earlier events dropped" notice, refetch run status |
| kernel audit `task_*`, `job_*`, `request`, `agent_action` | STATE_DELTA / CUSTOM | tasks board, activity rail |

### 6.2 Contract tests
Record real frames from `GET /v1/runs/{id}/events` (after the redaction Hermes already performs) into `hq/ui/tests/fixtures/` and replay them in vitest and Playwright. Pin the adapter to `GET /v1/capabilities` feature flags (`run_events_sse`, `run_approval_response`, `run_steer`) and warn in Settings when a flag is missing.

## 7. Risks and open decisions

1. **Browser approvals (decision needed).** The kernel's design says approvals need an interactive terminal; Telegram is off. A hub page can be scripted (XSS or a hostile extension). If browser approval is allowed, require: same-origin header + Origin check, a per-approval typed confirmation of the id (mirrors CLI "type the id again" and Telegram `/approve` then `CONFIRM`), a short TTL, audit with source `ui`, no approval of RED actions, and optionally WebAuthn user verification (UNVERIFIED). Safer default until decided: show approvals read-only with the exact console command.
2. **Authority creep.** Every new owner-lane write verb widens what a compromised hub page can do. Keep verbs minimal, allow-listed, audited and rate limited; keep `dashboard` read only; ship the strict CSP (section 2.7) with the SPA; no third-party scripts, no remote fonts.
3. **Hermes key blast radius.** `API_SERVER_KEY` can run terminal-capable work (the server itself says so). It must stay on the VM; the BFF should forward only the `/v1/runs`, `/api/sessions`, `/api/jobs`, `/v1/skills`, `/v1/toolsets`, `/v1/models`, `/health*`, `/v1/capabilities` allow-list.
4. **Version drift.** Pinned 818c13be (v0.21.6) vs upstream 9456670d: RPC and dashboard routes may differ. Feature-detect with `GET /v1/capabilities` and `gateway.capabilities`/`client.capabilities`; do not hard-code the 257-method contract.
5. **assistant-ui pre-1.0** (0.15.x): pin, wrap behind our own `Transcript` component so it can be swapped for plain components.
6. **AG-UI interrupt mismatch** (section 2.4): use CUSTOM events for in-run approvals.
7. **HTTP/1.1 connection budget** (about 6 per host): two SSE streams max per tab; WebSockets (noVNC) are counted separately by browsers (**UNVERIFIED**).
8. **noVNC "view only" is advisory.** `viewOnly` is a client flag; `pc.localhost:8088` exposes full input. The real guarantee is the agents' freeze in lab_control, not the VNC server. Document it; do not present "view only" as security. The Hermes Desktop pane makes the same caveat.
9. **Microphone paths**: secure-context failure from Tailscale HTTP, iframe permission, Windows privacy toggle, device busy; a cloud Web Speech default would leak audio. Wake word models are non-commercial licensed and the ports are early.
10. **Telemetry in copied code**: posthog/supabase in OpenJarvis, `shared_metrics.*` and `/api/shared-metrics` in Hermes clients. Strip or never call.
11. **Copyleft files**: noVNC and axe-core are MPL-2.0 (file-level); keep unmodified. DOMPurify: take the Apache-2.0 option.
12. **Kanban/Hermes duplicates**: do not embed Hermes' kanban plugin as the task board; its routes are unauthenticated by default and single-host.
13. **Process gates**: `docs/04_BUILD_PLAN.md` line 7 parks voice, Agent Factory and extra nodes. Building them needs the owner's go under the phase gates in `CLAUDE.md`. Also `CLAUDE.md` limits `docs/` to five files; this report sits in the existing `docs/research/` subfolder at the requester's instruction; the Project JARVIS doc needs the matching plan note.
14. **Node/npm**: Node 24.19.0 satisfies Vite 8 and OpenHands (node >= 24). npm 11.17.0 is below OpenJarvis' `>=11.19` engine pin; irrelevant unless its lockfile is reused. Keep `node_modules` on D: and out of any sync folder.

## 8. Build order (each phase: build, test, report passed and untested, wait for the go)

| Phase | Deliver | Backend work | Test gate |
|---|---|---|---|
| P0 Scaffold | `hq/ui` Vite app, tokens ported, shell (sidebar, top bar, palette, theme toggle, skip link, toasts), build script copying `dist` to the hub site `app/` | Caddy `/app/` route only | vitest smoke; Playwright smoke in Edge; axe on the shell; keyboard tab order; light/dark screenshots |
| P1 Read-only parity | Tasks monitor, Workspace with preview tiers, Models, Settings/health using existing endpoints | none (maybe `GET /v1/jarvis/tasks` read) | preview XSS corpus (script in html/svg/md/pdf), outputs CSP check, fixtures for overview |
| P2 Agentic OS desktop | `RFB`-based pane, take/continue, task drawer, target selector | none | Playwright against a fake noVNC/websockify and mocked `/control/*`; manual run against the real Agent PC |
| P3 Live events | kernel SSE endpoint, liveStore, activity rail | kernel `GET /v1/jarvis/events` | resume with `Last-Event-ID`, duplicate/ordering tests, Caddy flush check |
| P4 Command Center (text) | transcript, run progress, steer/stop, deliveries, approvals read-only | kernel `ui` lane + BFF to Hermes `/v1/runs`, AG-UI adapter | recorded-frame contract tests; live run with a harmless prompt; kill switch honoured |
| P4b Approvals (owner decision) | approve/deny with step-up | kernel `ui` verbs | adversarial tests: scripted click without typed id, wrong origin, replay, expired |
| P5 Voice | push-to-talk, mic error help, STT proxy, then VAD barge-in; wake word last | STT service on the laptop (speaches or whisper.cpp), Caddy `/stt/*` | fake-device Playwright; manual Edge permission matrix (denied, device busy, privacy off); latency measure |
| P6 Memory & learning | list/inspect/correct/delete kernel memory, Hermes learning view | kernel owner-lane memory verbs | version-conflict (409) UX, verified delete, no secrets accepted (existing scrub) |
| P7 Automation Studio | Hermes cron CRUD with dry-run preview | BFF allow-list for `/api/jobs*` | create/pause/run/delete round trip on a test job |
| P8 Agent Factory | NL to definition, review diff, risk rating, activate through skill gate, rollback | kernel `agent_defs` table (versioned), draft endpoint, skill gate integration | schema validation fuzz, activation requires policy/approval, rollback restores previous |

## 9. Sources and evidence index

Local files read: `hub/index.html` (structure, lines cited), `hub/Caddyfile`, `hub/build_hub.py`, `kernel/jarvis_kernel/{overview,tasks,memory,jobs,gateway,llm_proxy,telegram,cli}.py`, `lab/{lab_control,lab_runner}.py`, `skills/skillgate.py`, `docs/02_ARCHITECTURE.md`, `docs/04_BUILD_PLAN.md`. Research clones: `research/hermes-agent-818c13be` (pin 818c13be), `research/OpenJarvis` (ecbde4b3). New directories created this session: `research/hermes-ui-sparse` (upstream SHA 9456670d, sparse, 60 MB), `research/openhands-ui-sparse` (SHA e0a26af8, sparse, 4.1 MB), `research/ui-size-probe` (npm tarballs and extracted files, 22 MB). No repository code was executed; no secret files or token values were opened or printed.

URLs: https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard, .../api-server, .../acp, .../mcp, .../kanban, https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration, https://github.com/NousResearch/hermes-agent (apps/desktop README), https://github.com/OpenHands/OpenHands, https://github.com/OpenHands/software-agent-sdk, https://docs.openhands.dev/sdk/guides/agent-server/local-server, https://docs.ag-ui.com/concepts/events, https://github.com/ag-ui-protocol/ag-ui, https://github.com/assistant-ui/assistant-ui, https://github.com/vercel/ai-elements, https://github.com/open-webui/open-webui (LICENSE), https://github.com/lobehub/lobehub (LICENSE), https://github.com/dyad-sh/dyad (LICENSE), https://github.com/BloopAI/vibe-kanban, https://github.com/builderz-labs/mission-control, https://github.com/a2aproject/A2A, https://github.com/agentclientprotocol/agent-client-protocol, https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp, https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts, https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia, https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition, https://developer.mozilla.org/en-US/docs/Web/API/MediaTrackConstraints/echoCancellation, https://github.com/dscripka/openWakeWord, https://github.com/novnc/noVNC (docs/API.md), https://github.com/ricky0123/vad, https://github.com/speaches-ai/speaches, https://github.com/ggml-org/whisper.cpp, https://textslashplain.com/2024/05/06/err_blocked_by_client-and-html5-sandbox/, https://codeanlabs.com/?p=1431 (CVE-2024-4367).

## 10. Unverified list (collected)

- RPC method/event names are from upstream HEAD 9456670d, not from the running v0.21.6.
- Whether the lab Hermes uses `computer_use` (and therefore the Hermes display lease).
- Edge-specific behaviour: Web Speech backend, per-origin permission persistence, fake-device flags, AEC of page playback, HTTP/1.1 connection limit value.
- Streamdown's raw-HTML handling; assistant-ui AG-UI runtime support.
- `hermes-agent#501` AG-UI request status (seen only in a search result).
- Exact OpenHands VS Code embed component; OpenHands browser tab beyond screenshots.
- SheetJS current distribution and vulnerability state; whisper model sizes; `node_modules` size.
- CORS acceptance of `hub.localhost:8088` by the Hermes dashboard on 9119.
- Hermes latest release and its date (GitHub API was rate-limited after the first calls).
