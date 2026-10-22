# From this project to an actual JARVIS: research and roadmap (2026-10-22)

Question: what turns what we have (N.E.K.O + the JARVIS kernel, plugins, node and wake/voice stack) into something that feels like
Tony Stark's JARVIS for one owner on one Windows laptop, without giving up the safety rules? Two inputs: a read-only inventory of this
repo (docs only, no tests run) and web research done 2026-10-22. Web claims are marked with their source; several are vendor blogs or
single pages and are called out as unverified. Nothing here was changed in code.

## 1. What "actual JARVIS" means, as testable behaviours

1. **Always there, voice first.** Say "Hey Neko", speak naturally, hear the answer start within about a second or two, interrupt it.
2. **Does things on the PC and on the web** reliably, and says plainly what it cannot do.
3. **Knows me**: remembers facts, preferences, calendar and ongoing work; I can see and delete what it knows.
4. **Proactive but trusted**: tells me the useful thing at the right time (briefing, reminder, "your file finished"), never nags, never acts on its own for anything that matters.
5. **Gets better with use**: successful tasks become skills, failures become tests, and changes are gated by regression checks.
6. **Safe by construction**: a stranger's web page or e-mail cannot make it spend, send, delete or leak.

## 2. Where we really are (inventory, with the caveat that matters most)

Almost every "works" claim in our own docs is server-synthetic (Linux image, CPU, fakes). Live on the laptop this week: the stack runs;
Notepad and Calculator tasks work; the question card held a run and a spoken answer resumed it; the decider model passes the
tool-call probe 3/3; the wake word fired but only some of the time (67% of repo test clips); the planner fails on Dahl rate limits.

| Area | State | Biggest gap |
|---|---|---|
| Voice in/out | Wake, STT (Parakeet en; IndicConformer hi/te), TTS (Kokoro; Piper hi/te) built; barge-in off and unverified | No measured end-to-end latency; wake hit rate low for this owner's voice; no streaming STT, no full duplex |
| Understanding/planning | Plan-first agent with checkpoints, ask-back card, recommend-only model selector | Local models too small to plan (decider ctx 2048); cloud planner rate-limited; no pause |
| Computer control | UIA read, click/type, browser, repairs; strong approvals | Not verified on the current tree; no create-file/folder capability; open GUI agents are fragile (see 3.3) |
| Memory | Kernel memory with real erasure; N.E.K.O memory | Not typed or cited; no "what do you know about me" view; per-user behaviour unknown |
| Proactivity | Automations, monitors, reminders exist; most OFF | No schedule created; reminder zone is Asia/Shanghai; no phone/push channel |
| Integrations | MCP connector framework with allow-list | No working calendar/mail; no home devices |
| Self-improvement | Skills gate with regression suite (live-proven), Workbench, lab verdict isolation | Lab box and Hermes CLI blocked; caretaker off (no safe credential) |
| Safety | Kernel approvals, kill switch, audit chain, privacy gateway | Kernel on Oracle is 2026-10-16; the 2026-10-22 fixes are undeployed; names not redacted by default |
| Ops | Watchdog, autostart, health check | Autostart unverified on this tree; depends on Oracle VM + Tailscale |

## 3. Research findings and what they mean for us

### 3.1 Voice: keep the cascade, fix the measured weak points first
- Pipecat (BSD-2) and LiveKit Agents (Apache-2.0) are the two leading open voice-agent frameworks; Pipecat is a pipeline you assemble, LiveKit is
  built on a WebRTC media server and fits phone/multi-party ([Forasoft](https://www.forasoft.com/blog/article/pipecat-vs-livekit-agents),
  [LiveKit](https://livekit.com/blog/best-open-source-voice-and-video-ai-agent-frameworks)). One source says the model matters more than the
  framework: speech-to-speech about 300 ms, a STT+LLM+TTS cascade adds 300-800 ms; a TEN-framework licence is reported to forbid end-user-device deployment (unverified).
- Full-duplex models (Moshi: about 160-200 ms, handles interruptions natively; weights CC BY 4.0) exist ([Moshi paper](https://arxiv.org/html/2410.00037v1)),
  but they need a real GPU; our laptop GPU has 4 GB. Not now.
- Wake word: openWakeWord's own guidance is under 0.5 false accepts per hour and under 5% false rejects "for most users"; a 2026 write-up
  reports 8.5 false positives per hour at its best threshold and a successor, [livekit-wakeword](https://github.com/livekit/livekit-wakeword),
  claiming about 100x fewer false positives (vendor-adjacent). **No source gives accent-specific numbers**
  ([Picovoice benchmark page](https://picovoice.ai/blog/wake-word-benchmarks/) says to compare false-reject at a fixed false-accept).
- **Decision:** do not rewrite the voice stack. Record 200 of the owner's own "Hey Neko" clips plus an hour of ordinary room audio, and measure
  each engine (our sherpa KWS with keyword variants, livekit-wakeword, openWakeWord with a verifier) at a fixed false-accept rate. Then pick.
  Add sentence-by-sentence TTS start and measure wake-to-first-audio. Revisit Pipecat only if our own listener becomes the bottleneck.

### 3.2 Planner: a cloud planner behind our gateway, local models for grounding
- Third-party BFCL-v4 numbers (single page, unverified, v3 and v4 not comparable): Qwen3.5 9B 0.661, 27B 0.685, 4B 0.503; 4B is called "marginal" for agents
  ([d-central](https://d-central.tech/local-llm-agent-capability/)). Matches our experience: ling-tiny returned no plan, the 2048-token decider cannot hold the planner prompt.
- **Decision:** planning goes to a cloud model through the privacy gateway (design already says so), with the retry we added. The local 4B decider stays for UI grounding
  and short decisions. Re-measure whether the 5500M can run the decider at ctx 4096 (the model plan says "not safe"). Keep the model selector recommend-only until shadow data from the Oracle audit log exists.

### 3.3 Computer control: prefer structure over pixels, measure on our own tasks
- OSWorld (369 desktop tasks): top closed model 79.6%, best open-weight entry 66.7% (self-reported), human about 72% ([leaderboard](https://leaderboard.steel.dev/leaderboards/osworld/)).
  A harder benchmark found open agents collapse (GUI-Owl-1.5-8B: 52.3% on OSWorld, 5.7% on OpenComputer) ([OpenComputer](https://benchmarklist.com/benchmarks/opencomputer_verifiable_software_worlds_for_computer_use_agents/)).
  The main Windows benchmark is from 2024 (Navi 19.5% vs human 74.5%). Agent S3 reports 66-72.6% on OSWorld and 50-57% on WindowsAgentArena (project's own README, [PyPI](https://pypi.org/project/gui-agents)).
- [Windows-MCP](https://github.com/CursorTouch/Windows-MCP) (MIT) controls Windows through UI Automation, no vision model; README latency 0.7-2.5 s per action.
- **Decision:** treat vision GUI agents as a fallback, not the base. Our node already has UIA read/click/type. Build a **10-task real-desktop baseline** (open, find, type, save, rename,
  copy between apps) and run it after every change; compare our node against Windows-MCP behind our kernel allow-list on the same 10 tasks before building more operations.
  Promote every task that succeeds twice into a deterministic skill (our skills gate already does the safe version of this). File/folder creation needs a kernel approval rule: design it, deploy it deliberately.

### 3.4 Memory: our kernel memory plus provenance; no vendor service
- Mem0 (simple memory API), Zep/Graphiti (temporal graph: when a fact was true), Letta (agent edits its own tiered memory; Apache-2.0) are the references
  ([comparison](https://mem0.ai/blog/mem0-vs-zep), [Letta forum](https://forum.letta.com/t/agent-memory-solutions-letta-vs-mem0-vs-zep-vs-cognee/85)); no independent head-to-head benchmark found, most claims are vendor-authored.
- **Decision:** keep the kernel's memory (real erasure, isolation by agent and lane, refuses secrets). Add what the references teach: typed memories (fact, preference, task, summary), a source and a
  time for every item, "valid until/superseded by", and a console view with Forget. Try Graphiti in a sandbox only if the owner wants relationship/time reasoning.

### 3.5 Proactivity: policy first, features second
- Ambient agents are event-triggered and need a policy and execution layer; research reports that task capability alone does not sustain trust and that current models are weak at implicit events
  ([Walturn](https://www.walturn.com/insights/chat-agents-vs-ambient-agents-two-paths-to-ai-driven-assistance), [ProEvent](https://arxiv.org/abs/2607.17701), [Proactive Agent](https://arxiv.org/html/2410.12361v3)).
  GAIA is an open example built on triggers from Gmail/Calendar/GitHub ([docs](https://www.mintlify.com/theexperiencecompany/gaia/concepts/proactive-ai)).
- **Decision:** one table of triggers (time, calendar event, file arrival, desktop alert, task finished) each mapped to {ignore, notify, ask, act}, with a daily budget and a logged "why I stayed quiet". Default is notify/ask; "act" only for things the owner has pre-approved.
  Start with three: morning briefing, reminders (fix the Asia/Shanghai default), "your task finished". Record accept/dismiss and tune from that.

### 3.6 Integrations: MCP through our allow-list, one at a time
- Windows-MCP, Home Assistant MCP servers and Google Workspace MCP (Gmail/Calendar) exist as community servers ([Hass-MCP](https://mcpservers.org/servers/voska/hass-mcp), [LM Studio write-up](https://book.st-hakky.com/en/news/connecting-local-llm-to-gmail-calendar-home-assistant));
  Home Assistant handles device sequencing so the model is out of the loop after the request.
- **Decision:** first a read-only calendar connector (YELLOW, pinned, tested live) and "what's on today". Mail read-only second. Home devices only if the owner has any (unknown). Phone: Telegram is off by owner decision; a notification-only channel is an owner decision.

### 3.7 Security: the closest comparable project is the cautionary tale
- OpenClaw (local personal agent, long-term memory, self-generated skills, 50+ channels) reportedly passed 160k GitHub stars; in early 2026 researchers found hundreds of malicious skills on its marketplace,
  13.4% of 3,984 scanned skills with a critical issue and 7.1% leaking credentials (Snyk, via [The Register](https://theregister.com/2026/02/05/openclaw_skills_marketplace_leaky_security/), [Cisco](https://blogs.cisco.com/?p=484896)),
  plus indirect prompt injection that can backdoor the machine. Root cause named: broad permissions.
- Research direction: architectural isolation (CaMeL-style: a trusted planner never sees untrusted content; [CaMeLs Can Use Computers Too](https://arxiv.org/abs/2606.26479) notes the screen-observation conflict)
  and deterministic policy outside the model; visual prompt injection is a real attack on computer-use agents (VPI-Bench, ICLR 2026).
- **Decision:** our kernel (deny by default, approval bound to the action digest, kill switch, audit chain) is the right shape; keep it. Never add a skill marketplace or auto-install. Add **taint tracking**: any value that came from the screen, a web page or a message is marked untrusted;
  a plan step influenced by untrusted content cannot send, delete, pay or change settings without a fresh approval showing the exact action (main_agent already tracks "memory-influenced" steps; extend the same mechanism).

## 4. Roadmap (each phase ends with numbers, not feelings)

| Phase | Goal | Work | Exit test |
|---|---|---|---|
| 0, this week | Real on the laptop | Planner through the gateway; OpenRouter planner (owner connects); 200-clip wake test set; 10-task desktop baseline; 20 spoken commands; kernel deploy (owner) | Recorded: wake FRR/FAR per engine; desktop pass rate; command pass rate; zero unknown "works" claims |
| 1 | Feels instant | Measure wake-to-first-audio; sentence-level TTS; barge-in on (verify "Hey Neko, stop"); wake guard (done); choose wake engine from phase 0 data | Median wake-to-first-audio and p95 recorded; target set from the measurement (proposal: 2 s median with the cloud planner) |
| 2 | Knows me | Typed memory with source and time; console view + Forget; read-only calendar then mail via MCP; morning briefing as a paused preset | The owner asks "what's on today" by voice and gets the right answer; can list and delete any memory |
| 3 | Acts safely | Taint tracking; create-file/folder capability with a kernel rule; Windows-MCP vs our node on the 10 tasks; skills from repeated successes | Injection test set (hidden text on a page/screen) never causes a send/delete; baseline pass rate up |
| 4 | Proactive | Trigger table with budgets and "why quiet" log; reminders; task-finished notices | Accept rate over two weeks; no more than the daily budget |
| 5 | Everywhere | Phone/notification channel (owner decision); home devices if any; full-duplex voice only if hardware changes | Per owner |

Always on: every change runs the regression gate; the caretaker stays off until a safe credential exists; the planner prompt stays small (the local decider has 2048 tokens).

### Tools ready for phase 0 (built 2026-10-22, tested on the server with fakes; the real runs need the laptop)
- **Desktop baseline:** `python jarvis\evals\neko95\task_baseline.py` (10 plain requests through the same call the voice path makes, judged by what is really on the screen; closes only the windows it opened;
  refuses to start while your own Notepad/Calculator windows are open; result in `jarvis/evals/neko95/results/baseline-<time>.json`). `--list` shows the tasks, `--only T01,T05` runs some.
- **Wake word on your voice:** `jarvis\tests\neko_voice_owner_record.py` records you, `jarvis\wake\eval_owner.py` replays the recordings through the listener's detector for a grid of thresholds and gains (hits vs false alarms).
- **Untrusted-content hold** in main_agent (section 3.7) and the **wake guard** (commands under 0.4 s of speech are ignored) are already in the code.

## 5. Owner decisions that unblock the most

1. Deploy the kernel to Oracle now? (checklist: `KERNEL_DEPLOY_2026-10-22.md`; first start purges old private text: decide retention first.)
2. Connect OpenRouter/NVIDIA for the planner (you enter the key in N.E.K.O's settings).
3. Redact names in the gateway (`JARVIS_PRIVACY_NAMES=1`)? Costs some usefulness, protects people in your contacts.
4. Phone/notification channel yes/no; home devices yes/no.
5. Does an approval granted before a kill survive resume? (`KILLSWITCH_APPROVAL_POLICY_2026-10-19.md`.)
6. Allow a create-file/folder capability with a kernel approval rule (phase 3).

## 6. Not found / unverified
No 2026 data for UI-TARS or OmniParser; no accent-specific wake-word numbers; no independent memory-system benchmark; BFCL figures from one third-party page; OpenClaw size from one glossary site;
whether wake, autostart and the watchdog are on right now, whether any calendar/mail connector works, and any home-device integration were not evidenced in our docs.
