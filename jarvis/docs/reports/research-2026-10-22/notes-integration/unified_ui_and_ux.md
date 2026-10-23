# One unified JARVIS app (desktop via Tauri, phone and web): how to merge every UI, and the end-to-end UX (as of 22 Oct 2026)

Method: read the background report and notes, then a read-only look at the local clone `/home/user/jarvis-hq`. That clone is on a branch based on `laptop-dev` (tip 1c28d3a). `jarvis/ui` exists on `laptop-dev` and `remediation-2026-10-18`, not on `main`. After that, web research with primary sources where reachable. Nothing was built or changed. Repo links point to the `remediation-2026-10-18` tip, abbreviated R = https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/ (directories: https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/). Some web results came only from third-party summaries. Those are marked (secondary) or UNVERIFIED.

## Q1. How to unify the React, Vue, plain HTML and third-party frontends: micro-frontends (single-spa / Module Federation), iframes with a shared token, or one React app

### Takeaway
JARVIS already has the seed of the single app. `jarvis/ui` is a React 19 + Tailwind 4 + Vite 8 SPA, described as "JARVIS console UI: voice, command center, agents, tasks, automations, workspace, memory, models, settings". It has 11 screens, a live SSE event feed, Stop-all/Kill controls and axe/Playwright e2e tests. The in-repo research doc had already chosen this stack. The best-supported approach for a one-developer project is a **strangler-style port into that one React app**. Two exceptions apply. First, **sandboxed iframes** stay for N.E.K.O-upstream surfaces (avatar, chat, card manager, the 34 templates) and plugin-hosted TSX panels, because porting those would fork the upstream base. Second, **native React components over the services' APIs** (noVNC `RFB`, the Hermes run API, kernel audit) replace the third-party dashboards. Module Federation and single-spa pay off mainly for many independently deploying teams, and the repo mixes React 18, React 19 and Vue 3, so they add runtime-version risk for little gain.

### Cited Findings
**What exists today (repo)**
- `jarvis/ui/package.json`: `"description": "JARVIS console UI: voice, command center, agents, tasks, automations, workspace, memory, models, settings"`. Dependencies: react/react-dom 19.3.0. Dev dependencies: tailwindcss 4.3.3, vite 8.3.4, vitest 5.0.3, @playwright/test 1.64.0, @axe-core/playwright 4.13.0 — [R jarvis/ui/package.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/ui/package.json)
- `jarvis/ui/src/App.tsx` defines `SCREENS`: Command Center, Agentic OS (Desktop), Agent Factory, Tasks, Automations, Workspace, Memory, Models & skills, Settings & health, Bench, Connectors. It uses hash routing with Alt+1…0 shortcuts and "/" to focus the command input. The header has a connection badge, mic chip, "N running", "N approvals waiting", **Stop all** (cancels every open task and denies pending approvals) and **Kill**. The kill switch is "lifted at the kernel console", deliberately "it cannot be undone from this page" — [R jarvis/ui/src/App.tsx](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/ui/src/App.tsx)
- `jarvis/ui/src/lib/store.ts`: "Live state: the kernel's owner state (polled), its event feed (server-sent events)…". It polls every 3 s, checks health every 15 s, and opens `EventSource('/api/stream?since=<lastSeq>')`. Prefs: `lang: "en" | "hi" | "te"`, `speakReplies`, `autoSend`, `theme`, `micMuted` — [R jarvis/ui/src/lib/store.ts](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/ui/src/lib/store.ts)
- e2e specs in `jarvis/ui/e2e`: a11y, app, approvals, bench, connectors, kill, nomic, responsive, voice. There is also a separate `e2e-live` — [R jarvis/ui/e2e](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/ui/e2e). Main's final run reported "UI e2e 5/5" — [commit bbe769cc](https://github.com/venkatsairishith-dev/jarvis-hq/commit/bbe769ccd12defd1320991a78a9b26e52924d2e2)
- The console server (`jarvis/console/jarvis_console/server.py`) serves "static UI, kernel owner-plane proxy, voice, files, desktop control proxy". It "Binds 127.0.0.1 only. Every request must carry an exact loopback Host (name AND port: DNS-rebinding defence)". The SPA fallback means "unknown paths are screens". Auth uses an HttpOnly SameSite=Strict session cookie obtained from a ONE-TIME launch code, plus a double-submit token. "The real boundary for anything that matters stays the owner's PIN: approving needs the PIN, which no page and no process learns from here" — [R jarvis/console/jarvis_console/server.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/console/jarvis_console/server.py)
- N.E.K.O chat window `frontend/react-neko-chat` runs on **React ^18.3.1** with Vite ^7.3.6, react-markdown, KaTeX and @xyflow/react. It has `TaskPromptCard.tsx` and `jarvisTaskInputHost.ts`. Its i18n delegates to the "Xiao8 i18n system (window.t / window.safeT), English as the final fallback" — [R frontend/react-neko-chat](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/frontend/react-neko-chat)
- N.E.K.O plugin manager `frontend/plugin-manager` uses **Vue ^3.5.43** + element-plus ^2.14.6 + pinia ^3 + vue-i18n ^9.14.5 + Vite ^7. It hosts plugin panels written in TSX. `tsxRuntime.ts` transforms source with **sucrase**, bundles it with a `@neko/plugin-ui` UI kit and renders it in `HostedSurfaceFrame.vue`. The main_agent panel notes "The panel runs in a sandboxed frame that does not allow native form submission… props.api.call" — [R frontend/plugin-manager/src/components/plugin/hosted](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/frontend/plugin-manager/src/components/plugin/hosted); [R plugin/plugins/main_agent/ui/panel.tsx](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/main_agent/ui/panel.tsx)
- Six plugins ship hosted TSX panels: jarvis_bridge, lifekit, main_agent, mcp_adapter, netease_music and workbench. Together they are 1,834 lines (local clone count) — [R plugin/plugins](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/plugin/plugins)
- `templates/` holds **34** HTML files in the local clone, not the 24 in the brief. JARVIS-relevant ones include chat, character_card_manager, memory_browser, api_key_settings, model_manager, voice_clone, voice_identity, agenthud, subtitle, toast and openclaw_guide. The rest are N.E.K.O games and avatar editors — [R templates](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/templates)
- Hub `jarvis/hub/index.html` is titled "JARVIS Mission Control". It is served by Caddy at `hub.localhost:8088` and has "one lazily created iframe view per service". The kernel overview token is added by Caddy and "never reaches the browser". `outputs.localhost` serves agent files with `Content-Security-Policy "sandbox; default-src 'none'…"`. noVNC is reverse-proxied at `/pc/*` — [R jarvis/hub/Caddyfile](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/hub/Caddyfile); [R jarvis/hub/index.html](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/hub/index.html)
- The hub's "Take control" works by having `setViewOnly()` reach into the noVNC iframe's DOM to flip `noVNC_setting_view_only`. The repo's own research calls this "fragile (depends on noVNC's internal ids) and is a client-side fence only". It recommends replacing "the iframe + DOM poke with noVNC's `RFB` class in a React component, copying Hermes Desktop's `screen-pane.tsx` pattern" — [R jarvis/docs/research/04-agentic-os-and-ui.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/research/04-agentic-os-and-ui.md)
- The same doc chose: "New React 19 + TypeScript + Vite 8 + Tailwind 4 app… Hub stays until the new app reaches parity". Live events use "One `EventSource` for kernel events… Browser-facing event vocabulary follows AG-UI (MIT) with CUSTOM events for approvals/tasks". Hermes is reached "through a thin owner-lane BFF… The Hermes key never reaches the browser". It also includes a Hermes→AG-UI event mapping table (run.started→RUN_STARTED, tool.started→TOOL_CALL_START, approval.request→CUSTOM jarvis.approval.*, etc.) — [R 04-agentic-os-and-ui.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/research/04-agentic-os-and-ui.md)
- The hub replaced Dashy. Dashy 4.7.0 was "Tried first"; its "workspace view opened as an empty…" frame, and a custom single page was justified instead — [R jarvis/docs/01_RESEARCH_AUDIT.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/01_RESEARCH_AUDIT.md)
- The widget (`jarvis/widget/widget_server.py`) is "standard library only; loopback only; no model calls". It is read-only for approvals ("PIN in console") and uses one-time launch-code auth. Its states in `neko_state.py` include sleeping, listening, thinking, working, speaking, attention and error — [R jarvis/widget/widget_server.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/widget/widget_server.py); [R jarvis/widget/neko_state.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/widget/neko_state.py)

**External: micro-frontend tradeoffs**
- Module Federation 2.0 extends webpack 5's built-in federation with "dynamic type hinting, a Manifest, a Federation Runtime, and a Runtime Plugin System". It lists Rspack and webpack support — [module-federation.io guide](https://module-federation.io/guide/start/index.html). A Vite plugin `@module-federation/vite` exists, with `vue-host`/`vue-remote` and React examples — [@module-federation/vite README](https://cdn.jsdelivr.net/npm/@module-federation/vite@1.16.12/README.md); [Nx Vite MF docs](https://nx.dev/docs/technologies/module-federation/vite-module-federation). A mixed React+Vue federation config exists only as a blog snippet, "not a tested walkthrough" — [Leapcell](https://leapcell.io/blog/seamless-integration-of-react-and-vue-applications-in-astro-using-module-federation) (secondary)
- Practitioner guides say Module Federation "moves integration from build time to runtime". "Different versions of shared dependencies than the ones you've tested with" are a significant runtime risk. Micro-frontends are "a poor fit for small teams (under 10 developers)". Their advice is to ask first whether "a well-structured monorepo" solves the problem, and to use iframes "when you need hard isolation or are integrating heterogeneous legacy code" — [techinterview.org](https://www.techinterview.org/post/3233475066/module-federation-micro-frontends/); [iloveblogs 2026 guide](https://www.iloveblogs.blog/post/micro-frontends-architecture-guide-2026) (secondary, practitioner blogs)
- OpenClaw's own Control UI is a single Vite + Lit SPA "served by the Gateway on the same port as the gateway itself", talking to the Gateway over WebSocket. Chat, sessions, exec approvals, channels, cron, skills, nodes, config, logs and debug are all tabs of one app — [docs.openclaw.ai control-ui](https://docs.openclaw.ai/web/control-ui) (via search summary)

**External: native embedding of noVNC**
- noVNC API: `new RFB(target, urlOrChannel, options)`. Options are `shared`, `credentials` and `wsProtocols`. Properties `viewOnly`, `scaleViewport`, `resizeSession`, `clipViewport`, `focusOnClick` and `qualityLevel` (0–9, default 6) are settable at runtime. Events are `connect`, `disconnect` (`detail.clean`), `credentialsrequired` and `clipboard` — [noVNC API](https://novnc.com/noVNC/docs/API.html)

### Inferences
- Because `viewOnly` is a runtime property of `RFB`, the "Watch ↔ Take control" toggle becomes one line in a React component. It no longer needs a DOM poke or an iframe reload. The server-side lease (`lab_control.py` freezing the agent) stays the real fence.
- Each frontend family should get its own treatment:
  1. **JARVIS-owned** (console screens, hub home and activity rail, Benchmark Console, widget, approvals): already React, or plain HTML that is easy to re-express. **Port natively into `jarvis/ui`** and retire the hub page and the widget's separate HTML.
  2. **N.E.K.O upstream** (Live2D/VRM avatar, react-neko-chat, character card manager, memory browser, api_key page, Vue plugin manager, games): keep upstream-owned. Mount them as **same-origin routes or sandboxed iframes inside the JARVIS shell**, behind the console's single sign-in. Porting them would fork N.E.K.O and trigger its PR rules (zh/en/ja, screenshots, regression reports).
  3. **Plugin-hosted TSX panels**: the sandboxed-frame design is a security boundary and should stay. The JARVIS shell can host the same `HostedSurfaceFrame` runtime. One option is lifting `tsxRuntime.ts` + ui-kit into a framework-neutral package, since it emits an HTML document and is not tied to Vue (to verify). The other is linking to the Vue plugin manager for plugin admin.
  4. **Third-party dashboards** (Hermes dashboard, OpenCode web, OmniRoute dashboard, openmuse Expo web, noVNC page, Dashy): **replace with native JARVIS screens over their APIs**: Hermes `/v1/runs` events via the BFF, noVNC `RFB`, router health/usage via the kernel. Keep at most an "Advanced → open native admin" deep link for OmniRoute, never embedded with credentials. Dashy is already gone.
- Version skew argues against Module Federation here. react-neko-chat is React 18 and jarvis/ui is React 19, so a shared-singleton React would force an upstream upgrade or two React copies. The plugin manager is Vue. Iframes plus a typed `postMessage` bridge (theme, locale, auth state, "open approval X") give isolation without that coupling.
- "Shared auth token" for iframes should not mean passing a bearer token into frames. The console already uses an HttpOnly cookie plus a double-submit token on one origin, and Caddy injects backend tokens server-side. The unified app should serve every embedded surface from the **same origin** (path-mounted through the console or Caddy), so the cookie covers them and no token enters page JavaScript.

### Gaps
- Whether N.E.K.O's main server on 48911 can be path-mounted under the console origin without breaking its absolute URLs and WebSockets was not tested.
- Whether `tsxRuntime.ts` runs outside Vue unchanged was not verified (only its first 30 lines were read).
- No primary benchmark of iframe overhead for this case was found; the cited performance-cost claims are practitioner opinion.
- The OpenCode web and openmuse API surfaces (needed for native replacement) were not re-checked here; openmuse lived in the lost WSL box.

## Q2. Best-practice UX for agentic assistants in 2026: live timeline, approval inbox, live view, interruption, explanation, trust calibration, undo

### Takeaway
The products converge on one pattern set:
- a **live step log driven by real events**
- **pause / stop / take over at any time**
- **explicit confirmation before consequential actions**, scoped per action or per site, with hard blocks for the riskiest classes
- an **isolated agent workspace you can watch**
- **notify on completion or when input is needed**
- **replayable history**

The research guidance (HAX 18 guidelines, PAIR) adds calibrated rather than maximal trust, partial explanations scaled to stakes, and global controls. It also warns that approval prompts cause habituation and slow the agent. JARVIS's honest-narration rule (N3), PIN approvals, kill switch and X5 state machine already match most of this. The gaps are a unified timeline (O4 PARTIAL), pause/retry (O5 PARTIAL), undo (C6 MISSING) and phone approvals.

### Cited Findings
**Research frameworks**
- HAX: 18 guidelines in four phases (Amershi et al., CHI 2019, validated with 49 practitioners on 20 products) — [Microsoft Research](https://www.microsoft.com/en-us/research/publication/guidelines-for-human-ai-interaction/); [paper PDF](https://www.microsoft.com/en-us/research/wp-content/uploads/2019/01/Guidelines-for-Human-AI-Interaction-camera-ready.pdf)
  - **Initially:** G1 "Make clear what the system can do"; G2 "Make clear how well the system can do what it can do".
  - **During interaction:** G3 "Time services based on context"; G4 "Show contextually relevant information"; G5 "Match relevant social norms"; G6 "Mitigate social biases".
  - **When wrong:** G7 "Support efficient invocation"; G8 "Support efficient dismissal"; G9 "Support efficient correction"; G10 "Scope services when in doubt"; G11 "Make clear why the system did what it did".
  - **Over time:** G12 "Remember recent interactions"; G13 "Learn from user behavior"; G14 "Update and adapt cautiously"; G15 "Encourage granular feedback"; G16 "Convey the consequences of user actions"; G17 "Provide global controls"; G18 "Notify users about changes".
- The HAX Toolkit Design Library pairs each guideline with patterns and examples — [HAX Design Library](https://www.microsoft.com/en-us/haxtoolkit/library/example,guideline,pattern/3,6,20/)
- Google PAIR, "Explainability + Trust" — [PAIR chapter](https://pair.withgoogle.com/chapter/explainability-trust/):
  - "Users shouldn't implicitly trust your AI system in all circumstances, but rather calibrate their trust correctly."
  - "the perfect time to show explanations is in response to a user's action."
  - "Don't say 'what' without saying 'why' in a high stakes scenario."
  - "The best explanation is likely a partial one."
  - Increase automation in small steps as trust grows.
  - For voice, put fuller explanations in the visual interface for later review.
  - Be careful with numeric confidence; use categories tied to clear next actions.

**Nielsen Norman Group**
- On ChatGPT Agent (8 Aug 2025):
  - The agent "explained its actions and problems", and screenshots of sources were "an effective sourcing technique" for trust.
  - Failures: no clarifying questions on a vague prompt; it picked 12:15 PM itself when noon was unavailable ("the choice wasn't theirs to make"); the takeover browser window was cramped and could not be resized.
  - Confirmations "erode the agent's value proposition" when overused.
  - The form took 11 minutes against about 2 for a human.
  - Conclusion: "the computer should adapt to the needs of the human" and the agent "will need to be more inquisitive, much faster" — [NN/g Impressions of ChatGPT Agent](https://www.nngroup.com/articles/impressions-chatgpt-agent/)
- NN/g "AI Agents as Users": clear labels, predictable patterns and semantic structure serve both humans and agents, and "accessibility work already makes interfaces easier for agents to navigate" — [NN/g](https://www.nngroup.com/articles/ai-agents-as-users/)

**Products**
- **ChatGPT agent.** OpenAI's help text, as quoted by Simon Willison: "ChatGPT is trained to ask before taking many important actions, and you can pause, interrupt, or take over the browser at any time". It "will pause to ensure you're watching it take actions on specific sensitive sites such as financial institutions". In logged-out mode it "won't use any pre-existing cookies" — [Simon Willison quoting OpenAI help](https://simonwillison.net/b/9060). The OpenAI announcement page returned HTTP 403 to the fetcher and could not be read directly. The "watch mode" description comes from third-party guides only — [Spurnow](https://www.spurnow.com/es/blogs/how-to-use-chatgpt-agent-mode) (secondary)
- **Claude in Chrome.**
  - Modes: "Ask before acting" (plan, then approval) and "Act without asking".
  - Per-site prompts offer "Allow this action" (once) or "Always allow actions on this site".
  - Hard blocks: e.g. executing trades and permanently deleting files.
  - It always asks before purchases, permanent deletion and password/security changes, even in autonomous mode.
  - Anthropic warns it "can't guarantee Claude will request permission". Researchers note users "may become accustomed to repeatedly granting permissions without closely reviewing" — [Anthropic support](https://support.claude.com/en/articles/12012173-getting-started-with-claude-for-chrome); [Anthropic news](https://www.anthropic.com/news/claude-for-chrome) (via search summary)
- **Microsoft Copilot Actions (Windows).**
  - It runs in a separate agent workspace under its own standard account.
  - Users "watch a live log of steps", "pause the agent at any time", step in at dialogs and retake control.
  - It asks explicit approval for sensitive data or consequential changes.
  - Default folder scope is Documents, Downloads, Desktop and Pictures.
  - It is off by default (Settings > System > AI components > Agent tools) — [Windows Experience Blog, Securing AI agents on Windows](https://blogs.windows.com/windowsexperience/2025/10/16/securing-ai-agents-on-windows/); [Microsoft Learn, agentic security](https://learn.microsoft.com/en-au/windows/security/book/operating-system-agentic-security). The details date from the Oct 2025 Insider preview; current GA status was not confirmed.
- **Manus.** A side panel ("Manus's Computer") shows the agent opening tabs and filling forms live. Sessions are replayable step by step and shareable, and tasks continue in a cloud sandbox after you close the laptop, with a notification on finish — [workos](https://workos.com/blog/introducing-manus-the-general-ai-agent); [aiindigo](https://aiindigo.com/tutorials/getting-started-with-manus-cloud-computer-automating-complex-workflows) (secondary; official help not reached)
- **Cursor background agents.** They run in the background on web, mobile and an installable PWA. The PWA adds "push notifications when agents finish", full screen and offline viewing of past runs. Users can send follow-ups, run parallel agents and hand off to desktop "with just one click" — [Cursor docs, Web & Mobile](https://docs.cursor.com/background-agent/web-and-mobile); [Cursor forum](https://forum.cursor.com/t/introducing-cursor-on-web-mobile/111183)
- **OpenClaw Control UI.**
  - Chat streams "live tool-call output" and can "abort runs".
  - Exec approvals: host exec is denied by default, with policy deny/allowlist/full stored in `~/.openclaw/exec-approvals.json`.
  - A new browser or device needs one-time pairing approval; loopback is auto-approved but Tailnet/LAN connects need explicit approval — [docs.openclaw.ai](https://docs.openclaw.ai/web/control-ui) (via search summary)
  - The earlier parity notes add that OpenClaw 2.0 approvals are "bound to the exact request, command, session and person". This is taken from `/home/user/research_notes/JARVIS research and build plan/openclaw_grokbot_parity.md` and was not re-verified here — [docs.openclaw.ai](https://docs.openclaw.ai/web/control-ui)
- **xAI Grok Bot** (launched 11 Aug 2026): Bots "only come back when something needs your approval". A sales Bot queues drafts for approval before sending — [x.ai news](https://x.ai/news/introducing-grok-bot) (from prior notes)
- **AG-UI** is an event protocol over SSE from agent to frontend. Its families are run lifecycle, steps, text messages, tool calls, state snapshot/delta and custom events. Sources disagree on the count (16 vs 17 vs 24 types) — [CopilotKit AG-UI architecture](https://docs.copilotkit.ai/ag-ui/concepts/architecture); [CopilotKit blog](https://copilotkit.ai/blog/master-the-17-ag-ui-event-types-for-building-agents-the-right-way)

**JARVIS requirements already aligned**
- X5: states "sleeping, wake-listening, waking, listening, thinking, working, speaking, completed, needs-permission, error/offline", each "driven by a real signal (no timers faking progress)".
- X8: needs-permission shows "the level, bound action and reason".
- O4: "A real task timeline: agent names, current operation, source counts, queue, completion… Typed run events feed the UI; no fabricated progress" is PARTIAL.
- O5: "Pause, cancel, retry and approve; cancelling stops subprocesses" is PARTIAL.
- C6: undo journal is MISSING. C7: kill switch is TESTED. C8: PIN approval is TESTED.
- Source: [R jarvis/docs/reports/REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/reports/REQUIREMENTS.md)
- The background report (`/home/user/reports/JARVIS research and build plan.md`, Personality section) proposes an honest-narration contract. Every status line is "rendered from real tool events and never from model-predicted progress", which matches the repo's N3 honesty requirement, and the persona never softens a confirmation prompt — [R REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/reports/REQUIREMENTS.md)

### Inferences
These are pattern decisions for JARVIS, each mapped to sources.
- **Live timeline.** One `RunTimeline` component consumes the kernel SSE stream, normalised to AG-UI names as the repo's own table specifies. It is grouped by run → agent → step, and every row carries the source event's `seq`. Hidden reasoning stays collapsed ("thinking"). The visible narration is one plain sentence per step, e.g. "Searching D:\Projects for 'invoices'… found 3". This covers HAX G4, G11 and N3.
- **"Show me what you're doing."** A live-view pane next to the timeline, with these tabs:
  - Agent PC: noVNC `RFB`, `viewOnly=true` by default, "Take control" acquires the server lease.
  - Browser: screenshots from browser.py steps, each linked to its source URL, which fixes the NN/g complaint about unlinked screenshots.
  - Desktop: screenshot of the owner's PC only on request.
  - Terminal/code diff: lab runs.
  The pane must be resizable and pop-out-able, the NN/g "cramped takeover" lesson.
- **Interruption.** Use three levels, matching what the products expose:
  - Per run: pause, steer with a typed follow-up, stop.
  - Global: "Stop all", which already exists.
  - Kill switch: exists, liftable only at the kernel console.
  - Voice "stop" / Esc / tray "Stop" map to the same kernel verbs (HAX G17).
- **Approval inbox.** One queue of kernel approvals on desktop, widget, tray badge and phone. Each card shows the exact bound action, the level (YELLOW/RED/ADMIN), why it is needed, what changes, whether it can be undone, a screenshot or diff preview, and the requesting agent. Approval needs the PIN. Offer "Allow once" or "Always allow for this site/verb" only where kernel policy permits. Set default-deny expiry. Batch similar low-risk items and prefer fewer, more meaningful prompts to fight habituation (Claude in Chrome caveat; NN/g "erode value").
- **Clarify, don't substitute.** When a goal's constraint fails (noon unavailable), JARVIS asks through the existing question card (`input_request.py`) rather than choosing silently (NN/g; HAX G10).
- **Explanations.** Use a "Why?" affordance on every step and every approval. Give a partial explanation (PAIR): the plan step, the tool, the evidence used, the model/provider that decided it (M5 "show provider/model" is PARTIAL), and the policy rule that triggered the approval. Show a "Sources" list on research answers (MiniCheck verified). For voice, speak one sentence and put details on screen (PAIR multimodal).
- **Trust calibration.** Show a per-skill/verb track record ("this verb: 41 runs, 0 reverts"). Graduate automation step by step: kernel tiers YELLOW_APPROVAL → YELLOW_AUTO only by owner promotion (PAIR; HAX G14). Use category confidence labels with actions, not numeric scores.
- **Undo.** Add an Activity/History screen with an "Undo" button per reversible action, backed by the FileOps undo journal (C6). Mark irreversible actions as such before approval (HAX G16).

### Gaps
- OpenAI's own ChatGPT agent page (403), Manus official help and Grok Bot UI details were not read first-hand.
- No 2026 NN/g article specifically on approval-inbox or timeline design was found; NN/g's ChatGPT Agent piece is one anecdotal test.
- No quantitative study on approval-fatigue thresholds for personal agents was found.
- Claude's computer-use reference UI and Microsoft Copilot Actions' current (post-preview) UI were not verified for 2026.

## Q3. Phone access: PWA vs Tauri mobile vs Telegram; secure remote access over Tailscale; push notifications (ntfy, Web Push) and privacy

### Takeaway
Serve the same React app to the phone as an **installable PWA over Tailscale Serve**. Tailnet-only HTTPS on `*.ts.net` gives the secure context that service workers, microphone and Web Push need. Use **Web Push** (end-to-end encrypted payloads, RFC 8291) or **self-hosted ntfy** for notifications, and keep the PIN step inside the app. Tauri mobile works, but plugins and desktop parity on mobile are incomplete, so it is a later option. Telegram bot chats are not end-to-end encrypted, so use Telegram, if at all, only for content-free "you have 1 approval waiting" pings that link into the PWA.

### Cited Findings
- **Tailscale Serve.**
  - `tailscale serve 3000` proxies tailnet traffic to `http://127.0.0.1:3000`.
  - It requires tailnet HTTPS certificates on `device-name.tailnet-name.ts.net`.
  - Serve is tailnet-only and Funnel is the public variant; the last command configured on a port wins.
  - Serve adds `Tailscale-User-Login`, `Tailscale-User-Name` and `Tailscale-User-Profile-Pic` headers and strips spoofed copies. The backend should "listen only on localhost" if it trusts them.
  - Funnel and tagged-device traffic carry no identity headers.
  - Source: [Tailscale KB, Serve](https://tailscale.com/kb/1312/serve)
- The repo already restricts the kernel token: "kernel token only to loopback/Tailscale/https hosts" — [commit f587583a](https://github.com/venkatsairishith-dev/jarvis-hq/commit/f587583a). The console enforces "an exact loopback Host (name AND port)" — [R server.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/console/jarvis_console/server.py)
- The repo's UI research lists "secure-context failure from Tailscale HTTP" among microphone failure paths — [R 04-agentic-os-and-ui.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/research/04-agentic-os-and-ui.md)
- **iOS Web Push.**
  - It works only for Home-Screen-installed web apps on iOS/iPadOS 16.4+, and the permission prompt needs a user gesture — [OneSignal docs](https://documentation.onesignal.com/docs/web-push-for-ios); [Pushpad](https://pushpad.xyz/blog/ios-special-requirements-for-web-push-notifications) (vendor sources)
  - Declarative Web Push is reportedly supported in installed web apps on iOS 18.4+ — [Progressier](https://progressier.com/pwa-capabilities/declarative-web-push) (secondary; Apple/WebKit primary source not reached)
  - Developers report subscriptions lost after long inactivity — [webscraft 2026](https://webscraft.org/blog/pwa-pushspovischennya-na-ios-u-2026-scho-realno-pratsyuye?lang=en) (secondary)
- **Web Push privacy.** RFC 8291 provides "confidentiality and integrity for messages sent from an application server to a user agent". Push services cannot read payloads — [RFC 8291](https://datatracker.ietf.org/doc/html/rfc8291). A July 2026 draft proposes obsoleting 8291 with an HPKE-based scheme — [draft-thomson-webpush-sym/hpke](https://ftp.kaist.ac.kr/ietf/draft-thomson-webpush-hpke-00.html) (draft, not standard)
- **ntfy publishing.** Up to three action buttons (`view`, `http`, `broadcast` Android-only, `copy`); `http` actions default to POST. Priority runs 1–5, with 4–5 giving pop-over and long vibration. Markdown is opt-in. Attachments default to 15 MB and expire after 3 h on ntfy.sh — [ntfy publish docs](https://docs.ntfy.sh/publish/)
- **ntfy self-hosting.**
  - With `upstream-base-url` (needed for prompt iOS delivery), the self-hosted server sends upstream "only the message ID (in the `X-Poll-ID` header)" plus a SHA256 of the topic URL. The text is always "New message"; the iOS app then fetches the real message from your server.
  - Without it, iOS delivery "can take hours".
  - `auth-default-access: deny-all` is recommended for private instances.
  - Access tokens "grant users **full access to the user account**"; granular tokens are roadmap only.
  - No end-to-end encryption is documented; use HTTPS.
  - Source: [ntfy config docs](https://docs.ntfy.sh/config/)
- **Telegram.** Bot chats are cloud chats with server-held keys. Bots cannot join Secret Chats, so bot conversations are not end-to-end encrypted — [reclaimthenet](https://reclaimthenet.org/is-telegram-encrypted); [PIA](https://www.privateinternetaccess.com/blog/is-telegram-safe/) (secondary; Telegram primary docs not reached). In the repo, Telegram is off (`jarvis/config/telegram.off`), and the background plan proposes re-enabling it allowlist-only as an owner decision — [background report P6 / decision 9](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/reports/JARVIS_ROADMAP_2026-10-22.md)
- **Tauri mobile.**
  - At the 2.0 RC the team said "you can develop production ready mobile applications with Tauri NOW", but "not all of our desktop features and plugins are ported or available on mobile yet" — [Tauri 2.0 RC blog](https://v2.tauri.app/blog/tauri-2-0-0-release-candidate/)
  - Official mobile plugins include notifications, barcode scanner, biometric, haptics, geolocation and NFC — [Tauri start](https://v2.tauri.app/start/)
  - A mid-2026 blog places Tauri on the 2.11 line (2.11.5, 1 Jul 2026) — [mayhemcode](https://www.mayhemcode.com/2026/07/build-mobile-and-desktop-apps-from-one.html) (secondary; no 2026 official mobile-maturity statement found)
- **Cursor as precedent.** Cursor ships its phone experience as a PWA ("Add to Home Screen") with push on finish rather than a native app — [Cursor docs](https://docs.cursor.com/background-agent/web-and-mobile); [eesel](https://www.eesel.ai/blog/cursor-ios-app) (secondary)
- **OpenClaw.** Tailnet/LAN browsers need explicit device-pairing approval; only loopback is auto-approved — [docs.openclaw.ai](https://docs.openclaw.ai/web/control-ui) (via search summary)

### Inferences
- **Recommended phone stack:**
  1. `tailscale serve` on the laptop maps `https://<laptop>.<tailnet>.ts.net/` to the console.
  2. The console must accept that exact Host, plus `Tailscale-User-Login` = the owner's login, only when the request comes from the local `tailscaled` proxy.
  3. Phone sign-in is a one-time device-pairing code shown on the desktop app (OpenClaw-style), exchanged for a long-lived device session cookie.
  4. Approvals still need the PIN, and optionally biometric unlock (WebAuthn) before the PIN field. This is UNVERIFIED for the console.
- **Push payloads carry no secrets.** ntfy or Web Push sends "JARVIS needs your approval (RED)" plus a deep link `#/inbox/<id>`. Use the ntfy `view` action, not `http` approve buttons. One-tap HTTP approval would bypass the PIN and is unsafe because ntfy tokens are full-account.
- Prefer Web Push from the PWA because payloads are encrypted end-to-end. Use self-hosted ntfy (deny-all, upstream poll-only for iOS) as the Android-friendly fallback, which also works when the PWA is closed.
- **Telegram** is only a channel for reach (OpenClaw/Grok-style chat) for public-lane content. It must never carry private-lane content or approval secrets.
- **Tauri mobile** is worth reconsidering only if the PWA hits hard limits: background audio and wake word on phone, reliable iOS push, or biometric gating. The same React build can be wrapped later.

### Gaps
- How `tailscale serve` sets the upstream `Host` header (ts.net name vs 127.0.0.1), and therefore what the console's exact-Host check would need, was not verified.
- Apple/WebKit primary docs on iOS Web Push reliability in 2026 were not reached.
- The owner's phone OS (Android or iOS) is unknown; it changes the ntfy vs Web Push choice.
- Whether Hermes Telegram supports "approval pointer only" messages was not checked.

## Q4. One settings screen covering models, providers/keys (as file references), routing, hardware profile, voice, permissions, memory, skills, logs, evals (requirement S4)

### Takeaway
S4 is PARTIAL today: "N.E.K.O settings + JARVIS console". Settings live in at least four places: N.E.K.O's api_key/model templates, the plugin manager's model-binding pages, jarvis/ui's Settings, Models & skills, Connectors and Bench screens, and config files. The unified app should have **one Settings area with sub-pages**, each reading live state (the S4 acceptance test) and each showing which config file it writes. Keys are shown only as **file-reference status** ("D:\jarvis-secrets\groq.key: present, last used 2 h ago"), never as values, in line with S1.

### Cited Findings
- S4: "Settings screen: models, downloads and disk, providers and keys, routing, hardware profile, MCP, permissions, voice, memory, logs and evals". Test: "Each panel reads live state". Status: "PARTIAL (N.E.K.O settings + JARVIS console)". S1: "file references in `D:\jarvis-secrets`"; "OS credential store not used". S3 audit without secrets is TESTED. S5 "Clear history and memories" is PRESENT (N.E.K.O memory UI) but not checked — [R REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/reports/REQUIREMENTS.md)
- Models/providers requirements: M5 "show provider/model" PARTIAL; M8 multiple keys PARTIAL; M9 disable each provider PARTIAL — [R REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/reports/REQUIREMENTS.md)
- Settings and config sources in the repo:
  - N.E.K.O `templates/api_key_settings.html` and `model_manager.html` — [R templates](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/templates)
  - `config/api_providers.json` with core and assist provider lists plus `api_key_registry` — [R config/api_providers.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/config/api_providers.json)
  - JARVIS `jarvis/config/providers.json` (lanes; `gemini` disabled) — [R](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/config/providers.json)
  - `jarvis/privacy/providers.json` — [R](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/privacy/providers.json)
  - `jarvis/local_models/llama-swap.yaml` plus profiles (e.g. `vram8plus`) — [R](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/local_models/llama-swap.yaml)
  - `jarvis/config/egress.json` — [R](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/config/egress.json)
  - `jarvis/skills/allowlist.json` / `active.json` — [R jarvis/skills](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/skills)
  - The plugin manager's model-binding i18n files (`model-api.ts`, `model-bindings.ts`) — [R frontend/plugin-manager/src/i18n](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/frontend/plugin-manager/src/i18n)
- jarvis/ui already has `Settings.tsx` ("Settings & health", including a voice phrase/training flow with `lang` attributes), `Models.tsx`, `Connectors.tsx` and `Bench.tsx` — [R jarvis/ui/src/screens](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/ui/src/screens)
- The console has modules `connectorcfg.py`, `connector_templates.json`, `config.py`, `voice.py`, `voicebench.py` and `bench.py` — [R jarvis/console/jarvis_console](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/console/jarvis_console)
- OpenClaw's Control UI similarly folds config editing, skills, logs and debug into tabs of the same dashboard — [docs.openclaw.ai](https://docs.openclaw.ai/web/control-ui) (via search summary)
- HAX G1/G2 ("make clear what the system can do / how well") and PAIR ("Disclose data sources… how users can remove or reset it") support showing capability, accuracy and data-use information in settings — [HAX paper](https://www.microsoft.com/en-us/research/wp-content/uploads/2019/01/Guidelines-for-Human-AI-Interaction-camera-ready.pdf); [PAIR](https://pair.withgoogle.com/chapter/explainability-trust/)

### Inferences
Proposed Settings information architecture. Each page has a live status header, a "file this writes" footer, "test now" and "revert to last good".
1. **General**: language (UI) and voice language (en/hi/te), theme, startup/autostart, tray behaviour, widget on/off, notifications (desktop / phone push / Telegram pointer).
2. **Brain & models**:
   - The remote planner, lane by lane (private / public), the provider currently in use and the reason it was chosen.
   - Local roster from llama-swap (loaded/unloaded, VRAM, TTL), hardware profile picker (`vram8plus`…), disk free and downloads.
   - The explainer table generated from `llama-swap.yaml` / `models.json` / `providers.json`.
   - Covers S4 models, downloads and disk, hardware; M5.
3. **Providers & keys**:
   - Each provider shows enabled, lane, paid flag, key file reference and status (present/missing/expired, last success), rate-limit state, and a disable toggle (M9).
   - "Add key" means "put the file at D:\jarvis-secrets\X.key, then press Re-check". No key text box. Router (OmniRoute) health appears here.
4. **Routing**: lane prefs, fallback order, `switch_model` owner command, privacy gateway status (redaction on, last redaction counts).
5. **Permissions & safety**:
   - Kernel tiers per verb/app/site, "always allow" grants with revoke, signed-in browser accounts (masked), sensitive apps, egress proxy status.
   - PIN change happens at the kernel console. The kill-switch state is shown (lift only at the console).
6. **Voice**: wake word on/off and sensitivity, push-to-talk chord with re-bind (AlreadyRegistered handling), mic test, TTS voice per language, speak replies, barge-in, owner clip recording for eval_owner.
7. **Memory**: vault notes, N.E.K.O memories (browser), retention, "clear history/memories" (S5) with a preview of what will be deleted.
8. **Skills & plugins**: staged/active skills with gate results and provenance, promote/rollback (L4/L5), plugin list (embed or link the Vue plugin manager), MCP servers (mcp_adapter panel).
9. **Connectors**: existing Connectors screen.
10. **Logs & audit**: kernel audit (search, filter by agent or tier), console logs, redacted diagnostics export.
11. **Evals & health**: Bench / Benchmark Console, last scorecard, requirement matrix status, posture self-check.
12. **Character**: JARVIS character card (embed N.E.K.O card manager), persona traits, anti-sycophancy cases.

### Gaps
- Whether the kernel exposes read APIs for every item (e.g. llama-swap state, privacy gateway counters, skill gate results) was not checked endpoint by endpoint.
- How N.E.K.O's own settings (api_key page) and JARVIS provider config should be reconciled, given there are two provider registries, is an open design question.

## Q5. Accessibility and i18n (zh/en/ja required for N.E.K.O PRs; owner uses Hindi/Telugu)

### Takeaway
Accessibility tooling is already in place: axe-core e2e, `aria-live` toasts, `aria-current` nav, keyboard shortcuts, a responsive spec. i18n is fragmented across three mechanisms: N.E.K.O's i18next `window.t`, the plugin manager's vue-i18n, and **no UI string catalog in jarvis/ui**. The language pref there covers only voice (en/hi/te). N.E.K.O ships 8 locales with **no Hindi or Telugu**. The unified app should adopt i18next catalogs shared with N.E.K.O's keys, ship zh-CN/en/ja for anything touching N.E.K.O plus hi/te for JARVIS surfaces, and treat live agent updates as WCAG status messages.

### Cited Findings
- N.E.K.O locales in `static/locales`: en, es, ja, ko, pt, ru, zh-CN, zh-TW. `static/i18n-i18next.js` is present — [R static/locales](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/static/locales)
- react-neko-chat's `i18n.ts` uses "Xiao8 i18n system (window.t / window.safeT), English as the final fallback" with i18next interpolation — [R frontend/react-neko-chat/src/i18n.ts](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/frontend/react-neko-chat/src/i18n.ts)
- The plugin manager uses vue-i18n ^9.14.5 with `localeBundles.ts`; hosted panels receive a `locale` option, and there is an `mcpAdapterI18n.test.ts` — [R frontend/plugin-manager/src/i18n](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/frontend/plugin-manager/src/i18n)
- jarvis/ui hard-codes English strings in `App.tsx` ("Stop everything?", "Command Center"…). Prefs `lang: "en" | "hi" | "te"` drive voice model warm-up (`/api/voice/warm`). `Settings.tsx` sets `lang={lang}` on phrase text — [R jarvis/ui/src/App.tsx](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/ui/src/App.tsx); [R store.ts](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/ui/src/lib/store.ts)
- N.E.K.O PR rules include zh/en/ja i18n and UI screenshots — [R CONTRIBUTING.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/CONTRIBUTING.md) (as summarised in the background report)
- V1: offline speech recognition for English, Hindi and Telugu is PARTIAL — [R REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/reports/REQUIREMENTS.md). Telugu ASR is hard: the best real-world WER in one benchmark was 33.2% — [arXiv 2602.03868](https://arxiv.org/html/2602.03868v1) (from the background report)
- jarvis/ui e2e includes `a11y.spec.ts` (axe-core) and `responsive.spec.ts`. App.tsx uses `aria-live="polite"` for toasts, `role="alert"` for errors, `aria-current="page"` and skip-to-`#main` focus — [R jarvis/ui/e2e](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/ui/e2e)
- NN/g: semantic structure and clear labels help both humans and agents, and "accessibility work already makes interfaces easier for agents to navigate" — [NN/g AI Agents as Users](https://www.nngroup.com/articles/ai-agents-as-users/)
- The repo's UI research picked Radix/Base UI primitives for audited dialogs, focus traps and comboboxes — [R 04-agentic-os-and-ui.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/research/04-agentic-os-and-ui.md)
- WCAG 2.2 SC 4.1.3 "Status Messages" requires status updates to be programmatically determinable without receiving focus — [W3C Understanding SC 4.1.3](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html) (cited from standard knowledge; page not fetched in this session)

### Inferences
- **One i18n layer.** Use i18next in jarvis/ui; N.E.K.O already ships i18next, so keys and the fallback chain can be shared. Namespaces: `jarvis.*` for JARVIS surfaces and N.E.K.O's existing keys for embedded surfaces. Pass locale to iframes and hosted panels via the bridge (`locale` is already an option of the hosted TSX runtime).
- **Locale set.** en, zh-CN and ja are mandatory wherever a change lands in N.E.K.O platform code (PR rule). Add **hi** and **te** for JARVIS surfaces and offer them upstream as optional N.E.K.O locales. Keep UI language and voice language as two separate settings: the owner may read English but speak Telugu.
- **Scripts and fonts.** Devanagari and Telugu need suitable fonts, e.g. the Noto Sans Devanagari / Noto Sans Telugu families (UNVERIFIED which fonts jarvis/ui bundles; `public/fonts` exists). Avoid fixed-width chips that clip longer translations, and test with pseudo-localisation.
- **Agent-specific accessibility.**
  - The live timeline is an `aria-live="polite"` log that announces one-sentence step summaries, not every token.
  - Approvals are `role="alertdialog"` with focus moved to them, and the PIN field gets a proper label.
  - Everything is operable by keyboard (existing Alt+N and "/").
  - Voice works as an alternative input everywhere.
  - Respect `prefers-reduced-motion` for the avatar and widget animations.
  - Contrast tokens exist for both themes (hub CSS variables).
- Extend the existing axe e2e to the new inbox, timeline and settings screens, and to each locale (hi/te rendering).

### Gaps
- No source was checked on Indic font rendering quality in WebView2 (Tauri on Windows) or on how Android/iOS WebViews handle Telugu conjuncts.
- Whether N.E.K.O upstream would accept hi/te locales is unknown.
- How much of react-neko-chat's strings are already translated into ja/zh was not counted.

## Q6. The deliverable: the unified app's architecture and the detailed list of user-facing screens and flows

### Takeaway
There is one React app, `jarvis/ui` grown into "JARVIS". It is built once and shown in four containers:
1. a **Tauri desktop main window** (N.E.K.O.-Tauri shell)
2. a **Tauri widget window** (`#/widget`, transparent, always on top, the avatar plus a state ring)
3. a **tray menu**
4. the **phone/web PWA over Tailscale Serve**

All four read the same kernel SSE stream and use the same approval inbox and PIN. N.E.K.O surfaces and plugin panels are mounted inside it. No other dashboard remains.

### Cited Findings
- The Tauri pieces are specified in `PATCHES.md`, which is "PLAN ONLY":
  - Patch 1, Tray: Show/Hide, Mute mic, Release mouse, Pause wake word, Open console, Quit; state-following icon.
  - Patch 2: global push-to-talk via `tauri-plugin-global-shortcut` 2.4.1 (draft Ctrl+Alt+Space).
  - Patch 3: single instance via `tauri-plugin-single-instance` 2.5.4.
  - Patch 4: click-through fail-safe.
  - A 30-min Win10 transparency soak, else fall back to Electron.
  - Source: [R jarvis/third_party/PATCHES.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/third_party/PATCHES.md)
- Upstream N.E.K.O.-Tauri already does the transparent always-on-top window and per-region click-through, but has no tray, global shortcut, single instance or autostart — [N.E.K.O.-Tauri](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri) (from the background report)
- The global shortcut uses Win32 `RegisterHotKey`. It fails with `AlreadyRegistered` if another app owns the chord, and release is polled every 50 ms — [Tauri global-shortcut](https://v2.tauri.app/plugin/global-shortcut/); [global-hotkey](https://github.com/tauri-apps/global-hotkey) (from the background report)
- Widget requirements:
  - X1 always-available icon: PARTIAL
  - X2 click activates: TESTED
  - X3 "Hey Neko": PARTIAL
  - X4 push-to-talk: MISSING
  - X6 click fallback: TESTED
  - X7 completed state with Open file / Show in folder / Regenerate: PARTIAL
  - X8 needs-permission shows the exact action: PRESENT
  - X9 no LLM while sleeping: TESTED
  - Source: [R REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/reports/REQUIREMENTS.md)
- The voice path is wake_listener → STT 127.0.0.1:48930 → `POST 127.0.0.1:48940/api/task` (widget server) — [R jarvis/wake/wake_listener.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/wake/wake_listener.py)
- Safe preview: outputs on `outputs.localhost` with a sandbox CSP; pdf.js with `isEvalSupported:false`; docx via lab-side conversion; "never `innerHTML` of file content" — [R 04-agentic-os-and-ui.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/research/04-agentic-os-and-ui.md)
- The repo's open owner decision is "Whether the browser may approve actions at all (today: console or Telegram only, CLI says 'a script or model cannot approve')" — [R 04-agentic-os-and-ui.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/research/04-agentic-os-and-ui.md). Note that jarvis/ui already ships `approvals.spec.ts` and `lib/approval.ts` — [R jarvis/ui/src/lib](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/ui/src/lib)
- Product precedents behind the flows below:
  - Copilot Actions: live step log, pause, take over — [Windows blog](https://blogs.windows.com/windowsexperience/2025/10/16/securing-ai-agents-on-windows/)
  - Manus: replay — [workos](https://workos.com/blog/introducing-manus-the-general-ai-agent)
  - Cursor: PWA push when agents finish, one-click handoff — [Cursor docs](https://docs.cursor.com/background-agent/web-and-mobile)
  - Claude in Chrome: allow once vs always for a site — [Anthropic support](https://support.claude.com/en/articles/12012173-getting-started-with-claude-for-chrome)
  - OpenClaw: device pairing — [docs.openclaw.ai](https://docs.openclaw.ai/web/control-ui)

### Inferences
**Architecture (proposed, not built)**
- One SPA (`jarvis/ui`) serves everything. The console (127.0.0.1:8799) is the only backend the browser talks to: it proxies the kernel owner plane, voice, files, desktop control, plus new BFF routes for Hermes runs and lab control.
- The widget server (48940) becomes an internal API: `/api/task` stays for the wake listener. Its HTML goes away in favour of the SPA's `#/widget` route.
- The hub's Caddy keeps its server-side token injection, `outputs.localhost` sandboxing and `/pc/*` noVNC proxy, but its HTML is retired once parity is reached.
- N.E.K.O (48911) is mounted at a path or embedded via iframe for avatar, chat, card manager, memory browser and games.
- The Vue plugin manager is embedded at `#/plugins` or opened as an "Advanced" route.

**Containers**
- Tauri main window: full app.
- Tauri widget window: transparent avatar plus state ring plus a mini input and approval badge, click-through except the avatar.
- Tray: state icon, Talk (PTT), Pause wake word, Mute, Stop all, Open JARVIS, approvals count, Quit.
- PWA on phone: Home (status + inbox + chat), with the same routes, responsive.

**Screen list** (A, B, C… are new or merged; the existing screen is in brackets)
- **A. Home / Command Center** [CommandCenter]:
  - Greeting with state, plus a one-line "what I'm doing now" from the latest real event.
  - Command bar (type or hold-to-talk).
  - Approvals waiting.
  - Running tasks with progress from events.
  - Recent results with Open / Show in folder / Regenerate (X7).
  - Morning brief card (P6).
- **B. Chat / Conversation** (merge react-neko-chat as an embedded surface or a ported component):
  - Streaming replies, sources, question cards (`input_request`), task cards linked to the timeline, voice transcript.
  - Language toggle and "Explain more" per message.
- **C. Approval Inbox** (new dedicated route, today a section of Command Center):
  - A list sorted by urgency and expiry.
  - Each card shows exact action, tier, requesting agent, why, what changes, reversible yes/no, preview (diff, screenshot, recipients), "Why?" expander and policy rule.
  - Actions: PIN entry → Approve once, "Always for this site/verb" where policy allows, Deny with reason, Ask JARVIS a question.
  - History of decisions. The same cards appear on the phone and as a widget/tray badge.
- **D. Activity / Live timeline** [Tasks + hub activity rail]:
  - Runs → agents → steps from SSE; filters; per-run Pause, Steer, Retry step, Stop.
  - Budget meters (time, steps, pages: O6). Model/provider used per step (M5). "Earlier events dropped" notice.
- **E. Live view: "Show me"** [hub noVNC iframe → RFB component; Desktop screen]:
  - Tabs for Agent PC (Watch/Take control with lease, Hand back), Browser (step screenshots with source links), My PC (on-demand screenshot), Lab terminal/diff.
  - Pop-out and resize. Replay a finished run step by step (Manus-like).
- **F. Agents / Agent Factory** [AgentFactory]:
  - Create an agent from a sentence (agents.py), with lane, budgets, acceptance criteria and verifier.
  - Agent list with status. Coding-agent runs show branch, tests and the "push gate" status (kernel-gated FF of main).
- **G. Automations** [Automations]:
  - Routines and missions, schedule, delivery channel (desktop/phone), last runs.
  - Teach-by-demo recorder → staged skill.
- **H. Workspace / Outputs** [Workspace + outputs.localhost]:
  - Delivered files with safe preview (sandboxed), Open / Show in folder, provenance (which run made it).
- **I. Files & Undo** (new):
  - FileOps journal: "JARVIS moved 12 files to D:\Invoices\2026" → Undo (C6).
  - Search-by-description results ("find folder X").
- **J. Memory** [Memory + N.E.K.O memory browser + vault]:
  - What JARVIS remembers, where it came from, edit/forget, clear (S5).
- **K. Models & skills** [Models]:
  - Roster and loaded state; skills staged/active with gate results; promote/rollback.
- **L. Connectors** [Connectors]: accounts and MCP servers, scopes, status.
- **M. Settings** (S4 IA from Q4): General, Brain & models, Providers & keys, Routing, Permissions & safety, Voice, Memory, Skills & plugins, Logs & audit, Evals & health, Character.
- **N. Bench / Health** [Bench + Benchmark Console + posture self-check]: run, watch, cancel and compare; scorecard; requirement matrix.
- **O. Character & avatar** (embed N.E.K.O card manager, Live2D/VRM editors) for the JARVIS persona.
- **P. Plugins** (embed the Vue plugin manager and hosted TSX panels: main_agent, workbench, research, mcp_adapter, lifekit…).
- **Q. Help / "How JARVIS works"**:
  - Generated model and provider table, glossary of tiers, step-by-step guide (05_OPERATIONS), "What can you do?" capability list with honest maturity labels (HAX G1/G2).
- **R. Onboarding / first run**:
  - Pick UI and voice language, mic test, PTT chord, wake word opt-in, PIN explanation, phone pairing, notification opt-in, a demo task with an approval.
- **S. Phone pairing & devices**: paired devices, revoke, last seen (OpenClaw-style).
- **T. Widget** (`#/widget` route): avatar, state ring (X5 states), mini-transcript, approval badge (read-only plus "open inbox"), result chip (X7).

**End-to-end flows**
1. **Voice task with explanation.**
   - Hold Ctrl+Alt+Space (or say "Hey Jarvis"): widget goes to listening, transcript appears, release.
   - Thinking (real signal), then a plan summary spoken in one sentence and shown as a card ("I'll search D: and Documents, then open the newest match").
   - Working, with timeline steps narrated from events. Done: result chip with Open / Show in folder / Undo if a change was made.
   - "Why did you pick that one?" gets a partial explanation (PAIR).
2. **Approval on desktop.**
   - A RED action triggers the widget's needs-permission state and a tray badge, and the inbox card opens.
   - The owner reads the exact action and preview, enters the PIN, and approves once. The audit entry is shown.
   - If the owner is idle more than N minutes, a phone push follows.
3. **Approval on phone.**
   - The push says "JARVIS needs approval (RED): Send email to 3 recipients". It contains no secrets and deep-links to `#/inbox/<id>`.
   - The PWA opens over Tailscale (device session). The owner reviews the preview, enters the PIN, and approves or denies.
   - The desktop timeline resumes, and a completion push arrives later.
4. **Watch and take over.**
   - In Live view (Agent PC) the owner watches read-only, then clicks "Take control". The kernel lease freezes the agent and noVNC `viewOnly=false`.
   - The owner types the password themselves (ChatGPT-agent pattern), then clicks "Hand back". The agent resumes and narrates what changed.
5. **Stop and interrupt.**
   - Say "stop" or press Esc: speech stops (<300 ms target, V3) and the current run pauses.
   - "Stop all" asks for confirmation and cancels tasks. The kill switch is shown with "lift at console" text.
6. **Clarify instead of guess.** When a constraint fails, a question card offers 2–3 choices plus free text. The run waits (held), and the phone gets a low-priority push.
7. **Undo a file organisation.** Activity shows the journal entry. Undo restores the files, and the result is verified and narrated.
8. **Coding agent run** (research → plan → code):
   - The plan card is approved, and lab runs are visible in the timeline (worker, verifier, reviewer).
   - The live view shows the test run on screen (Xvfb/noVNC). The diff appears in Workspace.
   - The push gate shows a checklist (tests, size cap, daily cap). The kernel fast-forwards main, or a PR opens. The phone is notified.
9. **Trust graduation.** After N clean runs of a verb, JARVIS suggests "Allow 'open project in VS Code' without asking?". The owner accepts in Settings → Permissions, and the change is audited and revocable (HAX G14/G17).
10. **Settings change with live validation.** The owner adds a provider key file: Providers shows "missing" → the owner places the file → Re-check → "present, test call OK (public lane)". The routing page shows the new fallback order.
11. **First-run onboarding** as described in screen R.
12. **Morning brief and automations.** A scheduled routine produces a brief card on Home and a phone push with a summary that excludes private-lane content.

### Gaps
- Exact kernel/console endpoints for pause, steer, retry-step and lease were not enumerated. O5 is PARTIAL, so some verbs do not exist yet.
- The owner decision on whether browser/phone approval is allowed at all is still open. The repo doc says today it is "console or Telegram only", yet jarvis/ui has approval code and an e2e spec; which reflects current policy was not verified.
- Whether N.E.K.O.-Tauri can host a second window that loads a JARVIS route (rather than N.E.K.O's own page) without patching was not checked.
- Effort estimates for porting the hub, widget and Benchmark Console screens were not produced.
