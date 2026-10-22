# OpenClaw (ex-Clawdbot/Moltbot) and xAI Grok Bot: capabilities and JARVIS feature-parity (as of Oct 22, 2026)

Scope note: The owner's handwritten note says JARVIS should do "so many more things what [Clawd]bot or Grokbot do". Reading "Clawdbot" is an inference from handwriting. It fits well: Clawdbot was the January 2026 name of the agent now called OpenClaw. "Grokbot" is read as xAI's **Grok Bot** product (x.ai/bot, launched Aug 11, 2026). It is not the @grok reply bot on X. Research only. Where a JARVIS capability is described, it comes from the coordinator's brief and was not checked against the repo.

## Q1. OpenClaw (ex-Clawdbot/Moltbot): what it is, license, architecture, install, security, current version

### Takeaway
OpenClaw is an MIT-licensed, self-hosted, always-on personal agent by Peter Steinberger, now stewarded by the OpenClaw Foundation. A local "Gateway" control plane connects 20+ chat channels, skills/plugins (ClawHub), automations (cron; "heartbeat" is now folded into automations), memory, browser/computer use, paired device "nodes" (voice, canvas, camera, screen) and sub-agents. Its current major line is 2.0 (v2026.8.1, Aug 30-31, 2026), with stable 2026.9.x releases into October. Its security record is poor: 300 to 1,000+ malicious ClawHub skills, several high/critical CVEs, tens of thousands of internet-exposed gateways, and sandbox still **off by default** in 2.0.

### Cited Findings

**Identity, history, license**
- Name history: Warelay (Nov 24, 2025), CLAWDIS (Dec 3, 2025), Clawdbot (Jan 2, 2026), Moltbot (Jan 27, 2026, after Anthropic trademark complaints), OpenClaw (Jan 30, 2026). Creator: Austrian developer Peter Steinberger. License: MIT — [Wikipedia: OpenClaw](https://en.wikipedia.org/wiki/OpenClaw); rename-at-Anthropic's-request also reported by [AlternativeTo](https://alternativeto.net/news/2026/1/trending-open-source-ai-agent-clawdbot-rebrands-to-moltbot-after-pressure-from-anthropic/) and [CNBC](https://www.cnbc.com/2026/02/02/openclaw-open-source-ai-agent-rise-controversy-clawdbot-moltbot-moltbook.html)
- On Feb 14, 2026 Steinberger said he was joining OpenAI, and a nonprofit OpenClaw Foundation now stewards the project — [Wikipedia](https://en.wikipedia.org/wiki/OpenClaw)
- Adoption: about 392.7k GitHub stars and 82.6k forks at time of fetch (Oct 2026) — [github.com/openclaw/openclaw](https://github.com/openclaw/openclaw). It had 247k stars on Mar 2, 2026 — [Wikipedia](https://en.wikipedia.org/wiki/OpenClaw)
- It works with Claude, DeepSeek, GPT and other LLMs. Config and history are stored locally. Moltbook, a social network for AI agents, launched Jan 27, 2026 — [Wikipedia](https://en.wikipedia.org/wiki/OpenClaw). IBM reports Moltbook grew past 1.5M agents — [IBM Think](https://ibm.com/think/news/clawdbot-ai-agent-testing-limits-vertical-integration)
- Caution: openclawd.ai is a third-party site that claims Moltbot and OpenClaw are separate projects. Mainstream sources treat them as one project — [openclawd.ai](https://openclawd.ai/) (treat as unreliable)

**Architecture (from official README/docs)**
- **Gateway** is "the local control plane for sessions, tools, events, and channel connections". The Control UI (web dashboard), CLI and TUI all connect to it. It can run as a personal assistant on a laptop or as a shared team deployment — [README](https://github.com/openclaw/openclaw)
- **Models** are swappable provider plugins, hosted or local (Claude, Codex, local models). 2.0 setup detects existing Codex/ChatGPT/Claude CLI sign-ins, API keys, and local Ollama/LM Studio models — [README](https://github.com/openclaw/openclaw); [dsebastien 2.0 write-up](https://www.dsebastien.net/2026-10-03-openclaw-2-0-easier-setup-shared-sessions-same-security-defaults/)
- **Channels**: core channels are Discord, iMessage, Signal, Slack, Telegram, WhatsApp and WebChat. Google Chat, Microsoft Teams and Feishu are also mentioned. Plugin channels include Matrix, Nostr, Twitch and Zalo. The README says "20+ more" — [docs.openclaw.ai](https://docs.openclaw.ai); [README](https://github.com/openclaw/openclaw)
- **DM pairing**: unknown senders on DM-capable channels must be paired (`openclaw pairing approve <channel> <code>`). Inbound messages are treated as untrusted — [README](https://github.com/openclaw/openclaw)
- **Skills / ClawHub**: skills are SKILL.md-defined procedures loaded on demand. ClawHub is the marketplace (publishing, curation, trust guidance). New capabilities are usually built with the plugin SDK (channel, provider and tool plugins) — [docs](https://docs.openclaw.ai); [Wikipedia](https://en.wikipedia.org/wiki/OpenClaw). 2.0 added a single skill lifecycle path and a "Skill Workshop" (proposals, checks, decisions, history). Admins can install curated ClawHub skills and "vetted MCP connectors" from the Control UI — [dsebastien](https://www.dsebastien.net/2026-10-03-openclaw-2-0-easier-setup-shared-sessions-same-security-defaults/)
- **Automations (formerly Cron) and Heartbeat**: cron jobs, webhooks and hooks live under `/automation`. "Heartbeat monitoring has been retired" and is now ordinary automation jobs. Setup creates a "Proactive check" job that runs every 30 minutes by default. HEARTBEAT.md is no longer read at runtime and is migrated by `openclaw doctor --fix` — [docs: heartbeat](https://docs.openclaw.ai/gateway/heartbeat). 2.0 renamed Cron to Automations, and `openclaw cron` still works — [dsebastien](https://www.dsebastien.net/2026-10-03-openclaw-2-0-easier-setup-shared-sessions-same-security-defaults/)
- **Workspace / persona files** (`~/.openclaw/workspace`):
  - AGENTS.md: operating instructions
  - SOUL.md: persona, tone, boundaries
  - USER.md: preferences, 4,000-char limit
  - IDENTITY.md: name, vibe, emoji
  - BOOT.md: startup checklist
  - BOOTSTRAP.md: first-run ritual
  - memory/YYYY-MM-DD.md: daily logs
  - MEMORY.md: curated long-term memory, loaded only in the private main session
  - skills/: workspace skills

  Source: [docs: agent workspace](https://docs.openclaw.ai/concepts/agent-workspace)
- **Memory**: 2.0 uses a built-in SQLite memory engine for search and recall. A personal Claw can recall context from the same agent's other private conversations. The QMD backend was retired, along with its reranking, query expansion and cross-agent transcript search. Sessions and transcripts moved to SQLite — [dsebastien](https://www.dsebastien.net/2026-10-03-openclaw-2-0-easier-setup-shared-sessions-same-security-defaults/). CNBC describes "persistent memory" over weeks — [CNBC](https://www.cnbc.com/2026/02/02/openclaw-open-source-ai-agent-rise-controversy-clawdbot-moltbot-moltbook.html)
- **Browser and computer use**: signed-in browsing through an isolated managed profile or explicitly shared Chrome tabs. Computer Use works on paired Macs and on Windows when enabled; Linux is experimental — [dsebastien](https://www.dsebastien.net/2026-10-03-openclaw-2-0-easier-setup-shared-sessions-same-security-defaults/). In the Clawdbot era it had first-class agent tools for browser, canvas, nodes and cron — [older docs mirror, Jan 2026 tag](https://ayuriel.duckdns.org/Ayuriel/openclaw/src/tag/v2026.1.8/docs/tools/index.md) (mirror, secondary)
- **Nodes / device pairing**: iOS and Android nodes support pairing, camera, screen and voice workflows. Native apps exist for macOS, iOS, Android, Windows and Linux. Supported platforms add "voice, Canvas, camera, screen, and device-local actions" — [docs](https://docs.openclaw.ai); [README](https://github.com/openclaw/openclaw). 2.0 adds Wear OS and macOS menu-bar Quick Chat — [dsebastien](https://www.dsebastien.net/2026-10-03-openclaw-2-0-easier-setup-shared-sessions-same-security-defaults/)
- **Voice / Talk Mode / Voice Wake**: Talk Mode is a continuous listen-think-speak loop with TTS and STT (e.g., ElevenLabs). A common recommendation is to run the gateway on a server and pair a node on a device with a mic and speaker — [LumaDock tutorial](https://lumadock.com/tutorials/openclaw-voice-tts-stt-talk-mode); [horadecodar](https://www.horadecodar.com.br/configurar-talk-mode-openclaw-elevenlabs/) (secondary; CLI commands like `openclaw talk` / `openclaw voicewake start` are unverified)
- **Canvas / A2UI**: the README confirms a "Canvas" surface on node platforms — [README](https://github.com/openclaw/openclaw). I found no official page detailing Canvas/A2UI.
- **Multi-agent / sub-agents / "A2A"**:
  - Multi-agent routing: isolated sessions per agent, workspace or sender — [docs](https://docs.openclaw.ai) (`/concepts/multi-agent`)
  - Sub-agents run in their own session `agent:<id>:subagent:<uuid>`. `sessions_spawn` is non-blocking. Results are announced back to the requester channel — [docs: subagents](https://docs.openclaw.ai/tools/subagents)
  - Session tools: sessions_list/history/send/spawn/yield, subagents, session_status — [docs: session tool](https://docs.openclaw.ai/concepts/session-tool/)
  - OpenClaw's "native A2A" is its own session-tool mechanism inside one gateway, not Google's A2A protocol
  - Limitations: no default child timeout; a gateway restart kills sub-agents; practical ceiling of about 3 tiers
  - A community `openclaw-a2a-gateway` plugin enables cross-server `sessions_send`. "Agent Teams" is only an RFC

  Source for the last four bullets: [GreenNode tutorial](https://greennode.ai/tutorial/openclaw-native-a2a-limitations-production) (secondary)
- **2.0 extras**:
  - "Multiplayer" shared live sessions with hand-off
  - Sessions can run on a paired computer or a temporary cloud worker (Crabbox)
  - Approvals are bound to the exact request, command, session and person
  - Protected credentials reach destinations without appearing in model-visible text
  - Private credential request flow and an `openclaw security audit` command

  Source: [dsebastien](https://www.dsebastien.net/2026-10-03-openclaw-2-0-easier-setup-shared-sessions-same-security-defaults/); [eesel review](https://www.eesel.ai/blog/openclaw-2-0-review)
- **Privacy/telemetry**: state, memory and credentials stay on the user's hardware. By default the only outbound call is a daily version check — [README](https://github.com/openclaw/openclaw)

**Install requirements**
- Runs on macOS, Linux, Windows (PowerShell installer) and WSL2. The npm package needs Node ≥24.16 or ≥26.1, and Node 26 is recommended; the installer can provision Node. Quick start: `openclaw onboard --install-daemon`, then `openclaw gateway status` and `openclaw dashboard`. The repo is a pnpm workspace — [README](https://github.com/openclaw/openclaw)

**Current version**
- OpenClaw 2.0 = v2026.8.1, announced Aug 30, 2026 (GitHub tag Aug 31 UTC). It had about 16k-17k PRs and roughly 930-990 contributors — [Wikipedia](https://en.wikipedia.org/wiki/OpenClaw); [search summary of release coverage incl. TechSpot](https://www.techspot.com/news/113686-openclaw-20-lands-easier-setup-rebuilt-browser-app.html)
- Stable line 2026.8.2 (Sep 1) through **2026.9.8 (Oct 3, 2026)**. Atomic updates arrived Sep 18. Microsoft "Autopilot" is built on OpenClaw (Sep 25). OpenClaw Enterprise (multi-tenant control plane) is in pilot (Sep 29). A package labeled "2026.9.1-beta.1" is actually 2026.8.1-beta.4 — [dsebastien](https://www.dsebastien.net/2026-10-03-openclaw-2-0-easier-setup-shared-sessions-same-security-defaults/) (single secondary source for post-2.0 versions; verify on GitHub releases)

**Security incidents**
- **ClawHavoc / malicious ClawHub skills**: Koi Security audited 2,857 ClawHub skills and found 341 malicious. 335 were one campaign that used fake "Prerequisites" steps (ClickFix-style) to install Atomic Stealer (AMOS) on macOS, also targeting Windows — [The Hacker News](https://thehackernews.com/2026/02/researchers-find-341-malicious-clawhub.html); [eSecurity Planet](https://esecurityplanet.com/threats/hundreds-of-malicious-skills-found-in-openclaws-clawhub)
  - Later counts conflict: about 900 of 10,700 (Bitdefender, via secondary), 1,184 (Trend Micro, via secondary), "over 800 (~20%)" — [Firecrawl](https://www.firecrawl.dev/blog/secure-openclaw); [Substack](https://mothasa.substack.com/p/the-most-popular-ai-agent-on-github) (unverified secondary)
  - Snyk "ToxicSkills": about 4,000 skills scanned, 36% vulnerable, 76 malware (via Firecrawl, secondary)
  - OpenClaw added VirusTotal scanning, but Unit 42 reportedly found 5 malicious skills that evaded VirusTotal/ClawScan — [CyberDesserts](https://blog.cyberdesserts.com/openclaw-malicious-skills-security/) (secondary)
  - Cisco found a third-party skill doing silent exfiltration and prompt injection — [Wikipedia](https://en.wikipedia.org/wiki/OpenClaw)
- **CVE-2026-25253** (1-click RCE / token exfiltration, CVSS 8.8 reported): the Control UI accepted a `gatewayUrl` parameter and sent the auth token to an attacker-controlled WebSocket. Fixed in 2026.1.29 — search summary of [Firecrawl](https://www.firecrawl.dev/blog/secure-openclaw) and others. NVD fetch failed, so this is **unverified against NVD**.
- **ClawJacked** (Oasis Security, Feb 26, 2026; fixed in 2026.2.25 within 24h; "High"): a malicious website opens a WebSocket to the localhost gateway. The rate limiter exempted loopback, so the script can brute-force the password, and localhost pairings were auto-approved. Result: admin access to message the agent, dump config, list nodes and read logs — [Oasis Security](https://www.oasis.security/blog/openclaw-vulnerability). Firecrawl labels it CVE-2026-32025, CVSS 8.8; another table lists 7.5 — [Firecrawl](https://www.firecrawl.dev/blog/secure-openclaw) (CVE ID/score not in the Oasis primary source)
- Other reported CVEs: CVE-2026-28363 (9.9, allowlist bypass), CVE-2026-32922 (9.9, privilege escalation), and sandbox-escape/command-injection flaws fixed in 2026.2.25-2026.3.12 — secondary search summary (**unverified**)
- **Exposed gateways**: 21,000 exposed instances in late January; "over 40,000" public instances per a Bitsight scan, 63% open to remote attack; about 1,000 with no login (Shodan via Kaspersky) — [Firecrawl](https://www.firecrawl.dev/blog/secure-openclaw) (secondary aggregation)
- Other risks listed by Firecrawl:
  - DMs share one session across platforms by default
  - Tokens appear in URL query strings
  - Plaintext credentials are readable by agents with shell access
  - The agent drives the real browser profile, and private IPs are allowed by default
  - OpenClaw rewrites `openclaw.json` on startup, which can drop security settings
  - An arXiv study (2603.10387) reports an 80% takeover rate on a fully updated instance

  Source: [Firecrawl](https://www.firecrawl.dev/blog/secure-openclaw)
- **2.0 security defaults**: sandbox is still off by default (Docker/Podman is opt-in). Secret Store values are **not encrypted at rest**. Shared sessions are not a security boundary. One Gateway equals one trust domain. The Register criticized 2.0 for lacking network/filesystem boundaries — [Wikipedia](https://en.wikipedia.org/wiki/OpenClaw); [dsebastien](https://www.dsebastien.net/2026-10-03-openclaw-2-0-easier-setup-shared-sessions-same-security-defaults/)
- **Bans and warnings**: in March 2026 Chinese authorities restricted state agencies, SOEs and banks from running OpenClaw. Satya Nadella called it a "virus"-like risk in Feb 2026, yet Microsoft later built on it. MoltMatch incident: an agent created a dating profile without explicit direction — [Wikipedia](https://en.wikipedia.org/wiki/OpenClaw)

### Inferences
- OpenClaw's feature set is broad but shallow on safety by default. JARVIS already has a policy-tier/PIN/audit/kill-switch kernel, which addresses OpenClaw's biggest structural weakness.
- OpenClaw's "A2A" is not the Google/Linux Foundation A2A protocol. N.E.K.O's `brain/openclaw_adapter.py` most likely talks to an OpenClaw gateway (sessions/WebSocket API) or a community A2A bridge. Verify in code before relying on it.
- Users drifting from OpenClaw to Hermes Agent and NanoClaw (per HN comments in dsebastien) suggest JARVIS's choice of Hermes Agent is aligned with community direction.

### Gaps
- Could not load an official current page for the browser tool, Canvas/A2UI, Voice Wake/Talk Mode, or the Gmail Pub/Sub hook. Feature existence comes from the README and secondary tutorials.
- NVD entries for CVE-2026-25253/32025/28363/32922 were not verified (NVD fetch failed). Scores are from secondary sources.
- Exact latest release after 2026.9.8 (Oct 3) not confirmed. Check GitHub releases.
- The Register's 2.0 article was not fetched directly; its claims came via Wikipedia and dsebastien.

## Q2. xAI Grok Bot (x.ai/bot) and plain Grok assistant features

### Takeaway
Grok Bot (beta, launched Aug 11, 2026) is a hosted team of persistent agent "Bots" coordinated by a chief-of-staff Bot. They run on an xAI-managed cloud computer (a Linux VM with browser, filesystem and terminal). They sign into your tools, including ones without APIs/MCP, learn routines by demonstration, remember preferences, and come back only for approvals. It is bundled into SuperGrok and Cursor plans (not standalone), runs on desktop and iOS, and cannot be self-hosted.

### Cited Findings
- **Launch**: Aug 11, 2026, beta — [x.ai/news/introducing-grok-bot](https://x.ai/news/introducing-grok-bot); [Unite.AI](https://www.unite.ai/xai-launches-grok-bot-always-on-ai-teammates-with-their-own-cloud-computers/)
- **Own computer**: each Bot has its own cloud computer, so jobs continue when you step away. Bots sign into existing tools and work across apps, inboxes and websites, "including those without a clean API or MCP", and "only come back when something needs your approval" — [x.ai news](https://x.ai/news/introducing-grok-bot)
  - Conflict on topology: Reworked and Rohit Ghumare say **one machine per account, shared by all of that user's Bots**, with shared logins and files — [Reworked](https://www.reworked.co/collaboration-productivity/xai-launches-grok-bot-ai-agents-in-beta/); [Rohit Ghumare](https://rohitghumare.com/blog/grok-bot-shared-computer/)
  - Described as a managed Linux VM with browser, filesystem and terminal; not self-hostable — [Vellum](https://www.vellum.ai/blog/official-grok-bot-breakdown) (secondary)
- **Chief of staff + team**: a chief-of-staff Bot sits above specialist Bots (inbox, expenses, recruiting, bug fixes, ops). Bots message each other, share thread context, coordinate in group chats, and involve you only for judgment calls — [x.ai news](https://x.ai/news/introducing-grok-bot). Unite.AI did not describe a specific chief-of-staff Bot — [Unite.AI](https://www.unite.ai/xai-launches-grok-bot-always-on-ai-teammates-with-their-own-cloud-computers/)
- **Routines (teach by demonstration)**: a Bot can watch a workflow once, save the steps as a routine, accept corrections, and run it later. It proactively follows up on dropped threads and nudges stalled handoffs. Examples include overnight research and a "Monday scoreboard" — [x.ai news](https://x.ai/news/introducing-grok-bot). "Scheduled routines" are listed for the Cursor Ultra tier — [Unite.AI](https://www.unite.ai/xai-launches-grok-bot-always-on-ai-teammates-with-their-own-cloud-computers/)
- **Memory**: remembers conversations, learns preferences and edge cases, picks up prior work, and learns when to ping vs. continue — [x.ai news](https://x.ai/news/introducing-grok-bot)
- **Integrations/plugins**:
  - Examples named: Gmail (invoices), CRM updates from call transcripts, LinkedIn drafting, product UI, support ticketing, expenses
  - A Marketplace page is linked; Cursor Teams Premium includes a "team marketplace for skills and plugins"
  - **GitHub and Slack plugins were not confirmed** in the sources fetched

  Source: [x.ai news](https://x.ai/news/introducing-grok-bot); [Unite.AI](https://www.unite.ai/xai-launches-grok-bot-always-on-ai-teammates-with-their-own-cloud-computers/)
- **Approvals**: Bots pause for approval. For example, a sales Bot queues drafts for approval before sending — [x.ai news](https://x.ai/news/introducing-grok-bot)
- **Platforms**:
  - Desktop (macOS download per official page; a Linux build per Unite.AI) and iOS
  - Same thread on phone or desktop
  - Android "coming soon"

  Source: [x.ai news](https://x.ai/news/introducing-grok-bot); [Unite.AI](https://www.unite.ai/xai-launches-grok-bot-always-on-ai-teammates-with-their-own-cloud-computers/)
- **Pricing/availability (conflicting; changed after launch)**:
  - At launch: Cursor Ultra $200/mo, Cursor Teams Premium $120/seat/mo, and SuperGrok Heavy — [Unite.AI](https://www.unite.ai/xai-launches-grok-bot-always-on-ai-teammates-with-their-own-cloud-computers/)
  - Current official page: included with SuperGrok, SuperGrok Plus, SuperGrok Heavy, Cursor Pro/Pro+/Ultra and Cursor Teams Standard/Premium. Bots have their own usage allotment separate from Grok/Cursor plan usage. Enterprise is waitlisted — [x.ai news](https://x.ai/news/introducing-grok-bot)
  - Expansion dated Aug 26 — [Rohit Ghumare](https://rohitghumare.com/blog/grok-bot-shared-computer/)
- **Oddities to flag**: the official page's Download buttons point to a cursor.sh domain, and some outlets call the company "SpaceXAI" — [x.ai news (as fetched)](https://x.ai/news/introducing-grok-bot); [4Geeks](https://4geeks.com/en/blog/ai-tools/what-is-grok-bot). This implies a corporate tie-up between xAI and Cursor that I did not verify.
- **Security**: the official page gives no concrete controls beyond links to Security/Privacy/Subprocessors/BAA/DPA pages — [x.ai news](https://x.ai/news/introducing-grok-bot)
- **Plain Grok assistant** (mostly secondary sources):
  - Voice mode with Live Camera, upgraded with Grok 4 (Jul 9, 2025)
  - Animated Companions (Ani, Rudi, Valentine) and selectable personas
  - Imagine image/video generation (Imagine Video v1.0, Feb 2026, with native audio; criticized 480p quality)
  - DeepSearch over web and X
  - Scheduled tasks/automations, office add-ins, a coding agent, an API
  - "Grok 4.6" (Aug 2026), unconfirmed

  Source: [pasqualepillitteri guide](https://pasqualepillitteri.it/en/news/526/grok-ai-complete-guide-xai-chatbot); [Wikipedia: Grok](https://en.wikipedia.org/wiki/Grok_(chatbot)); [app listing](https://apps.appfollow.io/ios/grok-ai/6670324846?country=us)

### Inferences
- Grok Bot's distinctive capabilities are:
  - (a) always-on remote computer per user/team
  - (b) org-chart multi-agent (chief of staff + specialists, group chat)
  - (c) teach-by-demonstration routines
  - (d) approval-queue UX
  - (e) operating API-less sites via GUI

  JARVIS's Hermes-on-VM plus Windows desktop control plus coordinator already covers the primitives for (a) and (e). (b), (c) and the phone-first approval queue are the real gaps.
- Grok Bot's "logins shared across Bots on one machine" is a blast-radius concern JARVIS should not copy. Use per-agent credential scoping via the kernel's policy tiers instead.

### Gaps
- Could not fetch x.ai/bot itself (only the news post). Full plugin/marketplace list, data retention, and whether Bots can run on user-owned machines are unknown.
- GitHub/Slack plugin claims in the brief are unconfirmed.
- Official current Grok app feature list (Tasks, Companions, voice) was not fetched from x.ai. Details are from secondary guides.

## Q3. Other 2026 personal agents worth listing for parity (brief)

### Takeaway
Hermes Agent (Nous Research, MIT) is the closest open-source peer and is already in JARVIS. Its README shows multi-channel gateway, cron, sub-agents, self-written skills, 7 sandbox backends, MCP, and an OpenClaw migration path. Manus, ChatGPT Agent and ChatGPT Pulse add cloud-autonomous research, wide parallel fan-out, and a proactive morning briefing.

### Cited Findings
- **Hermes Agent** (MIT):
  - Agent-curated memory with nudges, a persistent user model, FTS5 session search with LLM summarization, Honcho dialectic user modeling
  - Autonomous skill creation and self-improvement (agentskills.io compatible, Skills Hub)
  - One gateway for Telegram, Discord, Slack, WhatsApp, Signal, Email and CLI, with voice-memo transcription
  - Built-in natural-language cron with delivery to any platform
  - Isolated sub-agents
  - 7 terminal backends (local, Docker, SSH, Singularity, Modal, Daytona, Vercel Sandbox)
  - MCP client
  - Cloud browser (Browser Use) and TTS via the Nous Portal tool gateway
  - `hermes claw migrate` imports SOUL.md, memories, skills and channel settings from OpenClaw
  - Installers for Linux/macOS/WSL2, native Windows and Android/Termux

  Source: [github.com/NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). Version 0.13.0 dated May 7, 2026 and "70+ tools" come from secondaries — [parallel.ai](https://parallel.ai/articles/what-is-hermes-agent); [fast.io](https://fast.io/resources/hermes-agent-review-2026/)
- **ChatGPT Agent**: navigates websites, asks for logins, runs code, creates editable slides/spreadsheets. It merges Operator and Deep Research, and asks permission before consequential actions — [offthegridxp Substack](https://offthegridxp.substack.com/p/what-is-chatgpt-agent-openai) (secondary)
- **ChatGPT Pulse** (preview Sep 25, 2025, Pro/mobile first): overnight review of memory/chat/feedback produces 5-10 morning "cards" with sources. Optional Gmail/Calendar connections, off by default. Thumbs up/down and topic requests — [The Decoder](https://the-decoder.com/openais-new-pulse-feature-lets-chatgpt-start-the-conversation/); [Tom's Guide](https://www.tomsguide.com/uk/ai/chatgpt-pulse-is-here-now-ai-starts-the-chat-and-curates-your-feed)
- **Manus**: cloud-autonomous agent that keeps working with the laptop closed and switches between multiple LLMs. "Wide Research" (Jul 31, 2025) runs 100+ parallel agents — [SSOJet](https://ssojet.com/blog/what-is-manus-ai-agent-explained); [Techpoint](https://techpoint.africa/guide/manus-vs-chatgpt/) (secondary, mostly 2025-era)
- **Microsoft Autopilot** is built on OpenClaw (Sep 25, 2026). NanoClaw is a container-isolated alternative mentioned as the security reference model — [dsebastien](https://www.dsebastien.net/2026-10-03-openclaw-2-0-easier-setup-shared-sessions-same-security-defaults/)

### Inferences
- For JARVIS, the useful additions from this tier are:
  - a Pulse-style overnight brief, which builds on the existing proactive monitor
  - Manus-style wide parallel research, which builds on the coordinator and research pipeline
  - Hermes's self-written skills and multi-channel gateway, which are already available once the Telegram bridge is re-enabled

### Gaps
- No current (2026) primary sources were fetched for Manus, ChatGPT Agent or Pulse. Their 2026 feature changes (e.g., Pulse expansion to Plus) are unverified.
- Hermes Agent's current version number (Oct 2026) was not confirmed from the repo.

## Q4. Capability lists, JARVIS parity table, and safe-vs-risky replication

### Takeaway
Most OpenClaw and Grok Bot capabilities map onto things JARVIS already has. The main gaps are:
- **multi-channel messaging reach** (only Telegram via a switched-off Hermes bridge)
- **mobile/phone companion with push approvals**
- **voice wake / continuous talk mode**
- **teach-by-demonstration routines**
- **chief-of-staff multi-agent team UX**
- **a vetted skill marketplace process**
- **secrets kept out of model context**

The riskiest capabilities to replicate are third-party skill marketplaces, exposed gateways, real-browser-profile control, and unsandboxed shell. JARVIS's kernel (tiers/PIN/audit/kill switch) is the right place to gate all of them.

### Cited Findings
- Hardening patterns proven necessary by OpenClaw incidents (useful as JARVIS design requirements) — [Firecrawl](https://www.firecrawl.dev/blog/secure-openclaw):
  - bind the gateway to loopback with token auth, and use Tailscale or an SSH tunnel for remote access
  - never trust localhost implicitly (ClawJacked)
  - route untrusted content through a tool-less reader agent
  - require approval for exec
  - read and pin skill versions
  - per-channel-peer session scoping
  - workspace-only filesystem access
  - log redaction
  - a dedicated browser profile
  - a config guard against config rewrites
- Localhost-trust brute-force and auto-pairing allowed full takeover from any visited webpage — [Oasis Security](https://www.oasis.security/blog/openclaw-vulnerability)
- Malicious skills hid in SKILL.md "prerequisites" that told the agent or user to fetch scripts — [The Hacker News](https://thehackernews.com/2026/02/researchers-find-341-malicious-clawhub.html)
- Even OpenClaw 2.0 ships sandbox-off and unencrypted-at-rest secrets — [dsebastien](https://www.dsebastien.net/2026-10-03-openclaw-2-0-easier-setup-shared-sessions-same-security-defaults/)

### Inferences

**Combined capability list (union of OpenClaw + Grok Bot + Grok app), with JARVIS parity**

"JARVIS likely?" is based only on the coordinator's context, not code inspection. Suggested components are my recommendations (not sourced claims). Verify licenses and fit before adoption.

| # | Capability (source product) | JARVIS likely has? | Gap | Suggested open-source component |
|---|---|---|---|---|
| 1 | Always-on local gateway / control plane, web dashboard, CLI/TUI (OC) | Yes, partly: kernel + coordinator; dashboard unknown | Possibly a unified web control UI showing sessions, approvals and files side by side | Extend existing N.E.K.O UI. OpenClaw's Control UI is a design reference (MIT) |
| 2 | Chat-app channels: WhatsApp, Telegram, Discord, Slack, Signal, iMessage, Teams, Matrix, email, 20+ (OC, Hermes) | Partial: Telegram via Hermes (off) | Only one channel, currently off. No WhatsApp/Signal/Discord/email | Re-enable Hermes gateway (covers Telegram, Discord, Slack, WhatsApp, Signal, Email). Optional: signal-cli, Matrix plus mautrix bridges |
| 3 | DM pairing / sender allowlist (OC) | Unknown, but kernel has policy tiers | Need per-sender identity binding on channels | Implement in kernel: allowlist-only pairing (not OpenClaw's default "pairing" mode) |
| 4 | Persona files SOUL.md / USER.md / IDENTITY.md / AGENTS.md (OC, Hermes) | Likely (N.E.K.O persona + vault notes) | Format compatibility, if interop or migration is wanted | Adopt the SOUL.md/AGENTS.md convention for portability with Hermes/OpenClaw |
| 5 | Persistent memory: daily logs + curated MEMORY.md + SQLite/FTS search; cross-session recall (OC, Hermes, Grok Bot) | Yes (N.E.K.O 5-layer + vault) | Probably none. Possibly FTS over past sessions | SQLite FTS5. Optional Honcho-style user model |
| 6 | Skills (SKILL.md) loaded on demand; self-authored skills (OC, Hermes) | Partial (missions/automations; MCP) | Agent-written, reviewable skills | agentskills.io SKILL.md format; Hermes skill self-creation behind a kernel review gate |
| 7 | Skill/plugin marketplace (ClawHub; Grok Bot Marketplace) | No (assumed) | Discovery of third-party skills | **Do not auto-install.** Local curated registry with signing, version pinning, diff review and static scan |
| 8 | Cron / scheduled automations (OC, Hermes, Grok tasks) | Yes (missions/automations) | Delivery of results to a phone channel | APScheduler or existing missions, plus channel delivery |
| 9 | Heartbeat / proactive check every N min (OC) | Yes (proactive monitor/alerts) | None, beyond tuning | Existing monitor |
| 10 | Webhooks / event triggers, e.g. Gmail Pub/Sub (OC) | Unknown | Inbound event triggers | Small FastAPI webhook receiver behind the kernel; ntfy for push |
| 11 | Browser control with signed-in managed profile (OC, Grok Bot, ChatGPT Agent) | Yes (Windows desktop/browser control) | Dedicated isolated profile; private-IP blocking | Playwright persistent context on a dedicated profile; browser-use |
| 12 | Computer use, desktop GUI (OC on Mac/Windows; Grok Bot cloud PC) | Yes (Windows) | Linux VM desktop for unattended jobs | Run on the Hermes VM with a virtual display (Xvfb) |
| 13 | Always-on remote computer that keeps working when the laptop is off (Grok Bot, Manus) | Partial (Hermes on VM) | Coordinator jobs that hand off to the VM when the PC sleeps | Hermes Docker/SSH backends on the VM; self-hosted sandbox (e.g., Daytona OSS) |
| 14 | Teach-by-demonstration routines: watch once, save steps, replay with corrections (Grok Bot) | No (assumed) | Record-and-replay of user workflows into a skill | Playwright codegen / action recorder that emits a skill; human review before promotion |
| 15 | Multi-agent team: chief of staff + specialists, group chat, inter-agent messaging (Grok Bot; OC sessions_send/spawn) | Partial (plan-then-run coordinator) | Persistent specialist agents with own memory/roles; agent group-chat view | Coordinator as chief of staff; specialists as Hermes sub-agents. Google/LF A2A SDK for cross-process messaging |
| 16 | A2A interop with OpenClaw (N.E.K.O openclaw_adapter) | Present in N.E.K.O code (per README) | Unclear whether it is wired into JARVIS or which protocol it uses | Keep it disabled by default. If used, route through the kernel as an untrusted external agent |
| 17 | Sub-agents with timeouts and cleanup (OC, Hermes) | Partial (coordinator) | Default timeouts; restart-safe checkpointing (OpenClaw lacks this) | Implement in the coordinator |
| 18 | Approval queue: drafts await approval, "only come back for judgment calls" (Grok Bot); approvals bound to exact request/person (OC 2.0) | Yes (PIN approvals) | Mobile-first approval inbox; binding approval to the exact command hash | Telegram/ntfy actionable notifications; hash-bound approvals in the kernel |
| 19 | Secrets kept out of model context; credential request flow (OC 2.0) | Unknown | Secret broker that injects credentials at tool-call time | OS keyring / Bitwarden CLI / HashiCorp Vault OSS; encrypted at rest (OpenClaw is not) |
| 20 | Mobile/device nodes: iOS/Android/Wear OS app, camera, screen, location, notifications (OC) | No (assumed) | Phone as a node: capture photos, receive push, talk | Telegram as the "app"; ntfy for push; optional Home Assistant companion app for sensors |
| 21 | Canvas: agent-rendered interactive UI on device (OC) | Unknown (PPTX/PDF deliverables exist) | Live agent-generated UI panels | Render HTML panels in the N.E.K.O UI. Google's A2UI spec is a candidate (unverified) |
| 22 | Voice: STT/TTS, continuous Talk Mode, wake word, push-to-talk (OC; Grok voice mode) | Partial (local voice) | Wake word and continuous duplex loop, if missing | openWakeWord, faster-whisper / whisper.cpp, Piper or Kokoro TTS, Silero VAD |
| 23 | Live camera / vision in voice mode (Grok) | Unknown | Camera-grounded conversation | Local VLM via llama-swap (e.g., Qwen-VL class) |
| 24 | Companions / animated personas (Grok) | Likely (N.E.K.O is companion-oriented; inference) | Probably none | Existing N.E.K.O avatar |
| 25 | Deep research with citations (Grok DeepSearch, ChatGPT Agent) | Yes (with citation checking) | Wide parallel fan-out (Manus "Wide Research") | Coordinator fan-out to N parallel researchers |
| 26 | Document deliverables: slides, sheets (ChatGPT Agent) | Yes (PPTX/PDF) | XLSX, if missing | openpyxl |
| 27 | Image/video generation (Grok Imagine) | Unknown | Local generation | ComfyUI with local models (GPU-dependent) |
| 28 | Proactive morning brief cards (ChatGPT Pulse) | Partial (monitor/alerts) | Overnight synthesis into a daily brief | Nightly mission to a vault note plus a Telegram digest |
| 29 | Multi-person shared sessions / hand-off (OC 2.0 "multiplayer") | No | Low priority for a personal assistant | Skip |
| 30 | Model choice incl. local models, auto-detect (OC 2.0, Hermes) | Yes (llama-swap) | None | Existing |
| 31 | Security audit command (OC `security audit --deep`) | Partial (audit log) | Self-check of config posture | `jarvis doctor` script: bind addresses, auth, exposed ports, skill hashes |
| 32 | Kill switch / audit / policy tiers | **JARVIS ahead** (OpenClaw lacks hardened defaults) | — | — |

**Safe or advisable to replicate locally (low-to-moderate risk with the existing kernel)**
- Scheduled automations, proactive checks, morning brief
- Memory/FTS recall
- Local voice wake and talk mode
- Persona files
- Deep research fan-out
- Chief-of-staff coordinator with specialist sub-agents (inside one trust domain, with per-agent tool scopes)
- Approval inbox over Telegram
- Canvas-style UI panels rendered locally
- Teach-by-demo routines, *if* recorded routines become reviewable skills that run under policy tiers

**Replicate only with strong controls (higher risk)**
- **Multi-channel inbound messaging**: every inbound message is a prompt-injection vector. Use allowlist pairing and per-peer sessions, and run untrusted content through a tool-less reader first.
- **Signed-in browser and computer use**: use a dedicated profile, never the owner's main profile with password manager/sync; block private IPs; take screenshots for audit.
- **Remote always-on VM**: no public gateway port. Use Tailscale or an SSH tunnel and loopback bind with token auth, and don't trust localhost implicitly (ClawJacked lesson).
- **Secrets**: use a broker and encryption at rest. Never put secrets in prompts or logs.
- **A2A to OpenClaw or other external agents**: treat as untrusted and route through the kernel.

**Avoid or don't replicate**
- Auto-installing from public skill marketplaces such as ClawHub, given the 341 to 1,000+ malicious-skill record.
- Shared logins across all agents on one machine (Grok Bot model).
- WhatsApp via unofficial libraries, since ToS/ban risk is an inference.
- Exposing any agent gateway to the internet.
- Running OpenClaw itself as a JARVIS subsystem with default settings (sandbox off, unencrypted secrets).

### Gaps
- JARVIS's actual coverage of each row was not verified against the repo. A separate repo_current_state note exists in this folder and should be cross-checked, especially rows 3, 10, 16, 19, 21, 23 and 27.
- Whether N.E.K.O's openclaw_adapter targets OpenClaw's WebSocket gateway, the community openclaw-a2a-gateway, or Google A2A was not researched (out of scope).
- Google's A2UI spec as a Canvas substitute is unverified.
