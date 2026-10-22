# Fix the foundations, then grow JARVIS

**Status: research and planning only.** Nothing in this report has been built, merged, deployed, installed or pushed. Every step in the plan is a proposal that waits for the owner's go, following the repo's own gate rule: "test, report what passed and what could not be tested, wait" ([jarvis/CLAUDE.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/CLAUDE.md)).

All 21 capabilities in the handwritten notes can be built on jarvis-hq, and roughly half already exist in some form. The remote planner over local specialists, the browser lanes, the voice cascade, the skill gates, the provider fallback, the test bench and the per-file docstrings are all there. None of the missing capabilities should come before four problems that are blocking the project or getting worse. First, the WSL disk that held the agent computer is gone. With it went the coding lab, the caretaker, the lab skills and openmuse ([commit 6395e98f](https://github.com/venkatsairishith-dev/jarvis-hq/commit/6395e98f)). Second, three days of work sits on unmerged stacked branches. The owner's own JARVIS_ROADMAP_2026-10-22.md exists only on `remediation-2026-10-18`, and the kernel running on Oracle is still the 2026-10-16 build ([compare main...remediation](https://github.com/venkatsairishith-dev/jarvis-hq/compare/main...remediation-2026-10-18); [roadmap](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md)). Third, Oracle's Always Free ARM allowance now reads **2 OCPU / 12 GB**, half of what the owner's VM is reported to use ([Oracle](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)). That VM also hosts the kernel, the browser's egress proxy and the router. Fourth, the router stack around the owner's "9router setup" has a live config-injection RCE into **Hermes Agent's config.yaml** with no fixed version reported ([VulnCheck](https://www.vulncheck.com/advisories/9router-0.4.1-through-0.5.99-config-injection-rce-via-hermes-settings-endpoint)). Separately, LiteLLM shipped a credential stealer yesterday ([Socket](https://socket.dev/blog/litellm-compromise)).

The recommended architecture keeps the repo's spine: the kernel's policy tiers, PIN approvals, privacy lanes and gates. It adds four things on top. Typed admin verbs go through an elevated broker instead of raw admin. One hardened router, OmniRoute, runs behind the JARVIS gateway. The agent computer is rebuilt on the Oracle VM once its size is settled. Coding agents push to `jarvis/*` branches, and only a kernel gate fast-forwards main. Three owner requests conflict with standing repo rules: full admin rights, pushing to main, and paid providers. Each has a recommended resolution and is listed as an owner decision at the end.

## Twenty-one requests, mapped to the repo and a phase

The table maps each handwritten item to what the repo contains today (the `remediation-2026-10-18` tip, which includes every other working branch), the recommendation, and the plan phase. The phases are P0–P7, defined in the plan section. "PLAN ONLY" means the repo documents a design that was never written.

| # | Owner item | Today in the repo | Recommendation | Phase |
|---|---|---|---|---|
| 1 | Full PC rights (admin) | Fixed, kernel-authorized job kinds; elevation only inside `repair.py` (UAC); no `osctl` module on any branch | Owner keeps admin. JARVIS gets an **elevated broker** (JEA or service) with ~20–40 typed, allowlisted admin verbs behind a new ADMIN tier (RED + PIN + second confirmation) | P3 |
| 2 | Go through any web page | Sandboxed Playwright (`browser.py`, egress proxy) plus signed-in Chrome bridge (`browserext.py`) | Keep two lanes. Research lane may read any page. Signed-in lane needs PIN for state changes. Banking/wallet hard-DENY. Add taint tracking | P3 |
| 3 | Human-like, interactive, explanatory | N.E.K.O companion, question card for held tasks, `input_request.py` | Narrate from real tool events, explanatory mode, streaming sentence TTS, barge-in | P5 |
| 4 | Desktop widget + tray icon | Loopback web widget (`jarvis/widget`). Tray / push-to-talk / single-instance are **PLAN ONLY** in `PATCHES.md` | Implement the four N.E.K.O.-Tauri patches as upstream PRs. Electron N.E.K.O.-PC is the fallback | P5 |
| 5 | "Find folder X" in plain speech; learn → execute → store skill | `open_file` matches names only in 6 known folders. Decider refuses "create a folder". Learning loop layers 1–3, skill gates | FileOps with undo journal, Everything + Windows Search, agentskills.io `SKILL.md` drafts staged through the existing gate, owner promotes | P3 |
| 6 | Research → plan → hand to coding agent | Research plugin, main_agent planner, lab dispatch. **Lab host gone** | Chain research → plan → kernel `dispatch_to_lab` on a rebuilt lab | P4 (needs P1) |
| 7 | Test by spawning multiple agents | Bench D/N cover races/restarts. No multi-agent suite | Worktree-per-worker, read-only tests, reviewer agent, new bench area | P4 |
| 8 | Do what Clawdbot (OpenClaw) / Grokbot (xAI Grok Bot) do | Kernel ahead on safety. Telegram off. No phone app, no teach-by-demo | Close reach gaps (phone channel, approval inbox, brief, routines). **Do not** run OpenClaw itself | P6 |
| 9 | Coordinate with ChatGPT / Gemini | `gemini` provider disabled, no OpenAI provider in kernel. ChatGPT only as a website | "APIs out, MCP in". Never automate chatgpt.com / gemini.google.com | P6 |
| 10–11 | API reasoning model controls; spawns local small-model agents that run the PC | `local_team` (remote adviser via privacy gateway + local specialists), local 4B decider | Planner through the gateway (roadmap Phase 0), plan-then-execute, local decider grounds actions | P2 |
| 12 | Voice: best GitHub repo | sherpa-onnx KWS → Silero → Parakeet/IndicConformer → Kokoro/Piper. Wake fires on 67% of test clips. Mic loop MISSING | Keep sherpa-onnx. Add Smart Turn, WebRTC AEC3, "hey jarvis", push-to-talk. Measure first | P5 |
| 13 | 10–20 local models, alone or combined | llama-swap with 7 models + 3 profiles | 15-model roster in llama-swap groups. Disk and #30039 constraints | P2 |
| 14 | Its own personality | N.E.K.O character system; no JARVIS card | JARVIS character card in N.E.K.O data, SOUL.md-style durable traits, anti-sycophancy bench cases | P5 |
| 15 | On-screen tests on server, fix, push | Linux test image, 860-case bench, Benchmark Console. Actions off | Xvfb + noVNC/KasmVNC, Playwright video/trace, fix loop, kernel-gated push | P4 |
| 16 | Explain how it works, each model, each file | 249/268 files have docstrings; 9 plugin READMEs | Fill ~19 docstrings, add directory READMEs, rewrite 02_ARCHITECTURE as "how it works" | every phase, P7 |
| 17 | Step-by-step usage plan | `05_OPERATIONS.md` is a dated log (469 lines) | Restructure 05 with a quick start; remove stale WSL lab sections | P0 + P7 |
| 18 | Coordinate with a coding CLI | OpenCode (ACP) + goose in the lost lab | OpenCode primary, goose second, Codex `exec` reviewer, Claude Code `--bare`. agy only wrapped | P4 |
| 19 | Switch API providers by itself and in CLI | `gateway.plan` fallback, `lane_prefs`, `switch_model` MCP tool. `model_select.py` not wired | Wire selector in shadow, owner "switch" command, CLIs via per-lane router keys | P2 |
| 20 | Own VM for browser research like openmuse | VM (WSL JarvisLab) **gone**; hub/lab code has no host | Oracle A1 + webtop/KasmVNC + Hermes `computer_use`, Tailscale-only. E2B for disposable runs | P1 |
| 21 | All the work openmuse can do | openmuse lived in the lost WSL box | Run openmuse itself on the VM (telemetry off, OmniRoute) or a Vane + Open WebUI stack | P1, P6 |

Repo-state sources for the table: [decide.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/desktop/decide.py), [NEKO_REQUIREMENTS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/NEKO_REQUIREMENTS.md), [PATCHES.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/third_party/PATCHES.md), [local_team README](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/local_team/README.md), [llama-swap.yaml](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/local_models/llama-swap.yaml).

## Ten time-sensitive findings outrank every new feature

These findings came out of the research. Each one either blocks capabilities the owner asked for, or exposes the owner's accounts and keys today. Severity below means impact on this owner's setup, using what the notes could confirm. Where a fact could not be confirmed, the table says so.

| # | Finding | Why it matters here | Severity | Proposed first action |
|---|---|---|---|---|
| 1 | Oracle A1 Always Free now 1,500 OCPU-h + 9,000 GB-h = **2 OCPU / 12 GB** | Owner VM reportedly 4/24; it hosts kernel, Smokescreen egress proxy, Dahl lanes, OmniRoute, Hermes | **Critical** | Check account type (Free vs PAYG) and usage; decide resize vs ~$25–28/mo |
| 2 | 9router: default password `123456`, published secrets, `REQUIRE_API_KEY=false`, CVE stream incl. **CVE-2026-108593** (Hermes config injection, ≥0.4.1 ≤0.5.99, no fix reported) | Owner says they have a "9router setup" and runs Hermes on the VM | **Critical** if reachable and co-hosted with Hermes | Inventory, lock down or remove; never let it write Hermes config |
| 3 | WSL lab disk gone | Blocks items 6, 7, 15, 18, 20, 21, lab skills, caretaker, hub | **Critical blocker** | Rebuild on chosen host (P1); schedule `wsl --export` for any local mirror |
| 4 | Stacked unmerged branches (combine-tools ⊂ laptop-dev ⊂ remediation, 49 ahead / 0 behind); roadmap only on remediation; kernel on Oracle is 10-16 build | Every plan step depends on the merged tree; undeployed security fixes | **High** | Owner-approved fast-forward of main; re-run bench; deploy kernel |
| 5 | LiteLLM 1.107.0 (PyPI + GHCR) shipped a credential stealer on **2026-10-21** | Not in the repo per the notes, but a common Gemini-CLI/Codex proxy suggestion | **High (conditional)** | Confirm no machine pulled 1.107.0 / `latest`; if one did, rotate every key on it |
| 6 | Gemini CLI stopped serving free and AI Pro/Ultra consumer accounts on **2026-06-18**; agy is closed source, never starts MCP in `-p`, exits 0 on denials, has no custom base URL | Owner's "Gemini" may not work for CLI use; agy cannot be an orchestrated worker | **High** for items 9, 18 | Confirm whether owner holds a paid Gemini API key or a consumer plan |
| 7 | OmniRoute (installed on VM as v3.8.51 trial): critical anonymous RCE when `requireLogin=false` (CVE-2026-88062) and an authz bypass fixed toward **3.8.52** | The trial instance predates that fix | **High** | Upgrade to pinned 3.8.53 multi-arch digest, `requireLogin=true`, disable ACP/spawn routes |
| 8 | llama.cpp **#30039**: open Windows/AMD Vulkan regression, prompt processing −41–54% on Qwen3.5 GDN models, from b10950–b11010 | Owner runs b11669/b11714; `vram8plus` profile loads qwen3.5-9b | **Medium** | Keep a pinned ≤b10950 binary for GDN models; benchmark both |
| 9 | ~19 GB free on D: | 35B-A3B MoE (17–22.7 GB) cannot fit; full roster ≈24 GB | **Medium** | Pick one deep MoE (gpt-oss-20b); prune superseded models after A/B |
| 10 | OpenClaw: 341 to 1,000+ malicious ClawHub skills, several high/critical CVEs, sandbox still off by default in 2.0 | Item 8 asks for OpenClaw parity | **High** if adopted as-is | Copy capabilities, not the runtime; keep N.E.K.O's `openclaw_adapter.py` disabled |

### The Oracle cut threatens more than the agent VM

Oracle's Always Free page now gives Ampere A1 "first 1,500 OCPU hours and 9,000 GB hours per month… equivalent to 2 OCPUs and 12 GB of memory", total, across one instance or two 1-OCPU instances ([Oracle Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)). The cut from 3,000/18,000 hours came with "no clear public announcement" ([Linuxiac](https://linuxiac.com/oracle-quietly-cuts-free-tier-ampere-a1-resources-in-half/)). On free accounts, an over-limit instance reportedly "gets stopped until it's resized". On Pay As You Go, support answers conflict, and keeping 4/24 is estimated at **about $25–28 a month** ([TerminalBytes](https://terminalbytes.com/oracle-cloud-free-tier-changes-2026/)). Enforcement dates (around 18 Aug 2026) come only from secondary sources and could not be checked against Oracle.

This is more than a cost issue because of what runs on the VM. The sandboxed browser "refuses to run if this proxy is unreachable", and that proxy is Smokescreen on the Oracle VM ([egress.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/config/egress.json)). The `dahl-*` providers are POSIX-only server entries ([providers.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/config/providers.json)). The kernel is deployed there too ([roadmap](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md)). A forced stop would take down the research browser, the public-lane planner and the kernel together.

The repo also names a second instance, hermes-agent-vm-2, which agents must never touch ([jarvis/CLAUDE.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/CLAUDE.md)). If that instance is also A1, it counts against the same 2/12 pool. The notes could not confirm its shape, so the owner should check it. Idle reclaim is not the danger: it requires memory under 20% on A1, and a resident LLM plus a desktop never gets that low. The danger is the size limit ([Oracle](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)). One Hermes user who downsized to 2/12 kept VPN, SSH and Hermes always-on and moved heavier services to on-demand ([joeyw](https://joeyw.reallyenglish.com/posts/oracle-always-free-arm-downsize/)). That is the template if the owner keeps the $0 rule.

### The router the owner already runs is the most exposed component

9router ships with `INITIAL_PASSWORD=123456`, publicly known `API_KEY_SECRET`/`MACHINE_ID_SALT` defaults, `REQUIRE_API_KEY=false`, `AUTH_COOKIE_SECURE=false`, `CLOUD_URL=https://9router.com`, and Docker `HOSTNAME=0.0.0.0` ([9router README](https://github.com/decolua/9router)). Its 2026 advisories include:

- unauthenticated plaintext API-key exposure (CVSS 9.3) ([VulnCheck](https://www.vulncheck.com/advisories/9router-unauthenticated-api-key-exposure-via-api-usage-stats))
- a hardcoded JWT secret (CVE-2026-49352, CVSS 9.8) ([Strix](https://www.strix.ai/cve/CVE-2026-49352))
- RCE through the default password ([VulnCheck](https://www.vulncheck.com/advisories/9router-before-remote-code-execution-via-default-password))
- SSRF ([VulnCheck](https://www.vulncheck.com/advisories/9router-before-server-side-request-forgery-via-v1-web-fetch))
- **CVE-2026-108593**, published 2026-10-10: `POST /api/cli-tools/hermes-settings` injects arbitrary keys into Hermes Agent's `config.yaml`, and a crafted `baseUrl` adds a shell hook that runs after an LLM call. It affects ≥0.4.1 ≤0.5.99 with **no fixed version reported** ([HOL Guard](https://hol.org/guard/security/cves/CVE-2026-108593-9router-041-through-0599-config-injection-rce-via); [VulnCheck](https://www.vulncheck.com/advisories/9router-0.4.1-through-0.5.99-config-injection-rce-via-hermes-settings-endpoint)).

npm is at 0.5.104, ahead of the GitHub Releases page, and nobody has confirmed whether 0.5.100–0.5.104 fixes the Hermes bug ([npm](https://registry.npmjs.org/9router)). The repo's own selector doc had already concluded "do not install on this server … put it behind our gateway as one more provider" ([MODEL_SELECTOR_2026-10-20.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/MODEL_SELECTOR_2026-10-20.md)). The owner's answer ("Gemini and 9router setup") suggests a 9router instance exists somewhere anyway. Its location is unknown, so finding it is the first P0 task.

Two more traps sit in the same area. Routing Claude Code, Antigravity, Codex or Cursor **subscription** OAuth through 9router is what Anthropic's terms changes and Google's Antigravity suspensions target. Google says such bans also cut off Gemini CLI and Code Assist because the backend is shared ([gemini-cli #20632](https://github.com/google-gemini/gemini-cli/discussions/20632); [alternativeto](https://alternativeto.net/news/2026/2/anthropic-officially-bans-using-subscription-authentication-for-third-party-claude-use)). Also, 9router's RTK token saver is **on by default** and its "Caveman"/"Ponytail" modes inject into prompts ([9router README](https://github.com/decolua/9router)). Any of these silently alters text after the privacy gateway's redaction. One further point is an inference from the notes, not a verified fact: OmniRoute also defaults to port 20128 ([OmniRoute README](https://github.com/diegosouzapw/OmniRoute)), so the two cannot share a host without a port change.

### LiteLLM, Gemini CLI and agy change which coding paths are viable

On 2026-10-21, all seven `litellm` 1.107.0 wheels (including aarch64) and `ghcr.io/berriai/litellm:v1.107.0` carried a `cook.pth` + `sitecustomize.py` payload. It runs at Python startup and steals cloud, SSH and GPG keys, AI-tool auth files and env vars. `latest`, `main-latest` and `main-stable` briefly resolved to it. The suggested pin is 1.106.2 ([Socket](https://socket.dev/blog/litellm-compromise); [The Hacker News](https://thehackernews.com/2026/10/litellm-compromised-in-supply-chain.html)). It is LiteLLM's second compromise this year ([LiteLLM](https://docs.litellm.ai/blog/security-update-march-2026)). The notes found no LiteLLM in jarvis-hq. LiteLLM is, however, the documented proxy for pointing Gemini CLI at a router ([LiteLLM docs](https://docs.litellm.ai/docs/tutorials/litellm_gemini_cli)), so the plan avoids it entirely.

Google stopped serving Gemini CLI to free, AI Pro and AI Ultra consumer accounts on 2026-06-18. The CLI "will remain accessible via paid Gemini and Gemini Enterprise Agent Platform API keys" ([The Register](https://www.theregister.com/ai-ml/2026/05/20/bye-bye-gemini-cli-google-nudges-devs-toward-antigravity/5243605)). The replacement, Antigravity CLI (`agy`), has four problems for orchestration:

- It is not open source ([The Register](https://www.theregister.com/ai-ml/2026/05/20/bye-bye-gemini-cli-google-nudges-devs-toward-antigravity/5243605)).
- In headless mode, shell commands are soft-denied: the run "continues, exits 0" ([agy headless docs](https://antigravity.google/docs/cli/headless/)), and a denied tool call discards the turn with status SUCCESS ([#1062](https://github.com/google-antigravity/antigravity-cli/issues/1062)).
- An open bug means stdio MCP servers are **never started in print mode** ([#1226](https://github.com/google-antigravity/antigravity-cli/issues/1226)).
- No custom base URL is documented, so it cannot be pointed at a router.

A worker that hides failures behind exit 0 and cannot load the JARVIS MCP server would break the kernel's ability to verify results.

## A remote planner over 10–20 local models fits only with discipline

Items 10, 11, 13 and 19 describe the architecture JARVIS already half-has. `local_team` has a public adviser (Dahl MiniMax) plan through the privacy gateway, which sees `[PASSWORD_1]` rather than the password. Local specialists (Qwen2.5-Coder 1.5B, Qwen2.5-Math 1.5B, Ling-3.0-tiny) then answer with real values, routed by the OpenJev Verdict classifier ([local_team README](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/local_team/README.md)). The privacy gateway on port 48950 redacts with Presidio + spaCy and withholds images, audio and files ([gateway.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/privacy/gateway.py)).

What is missing is the same pattern for PC work. The roadmap already decides that "planning goes to a cloud model through the privacy gateway … local 4B decider stays for UI grounding". It also records why: local models are "too small to plan (decider ctx 2048)" and the cloud planner is rate-limited ([roadmap](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md)). The proposal makes that the P2 centrepiece. The API model writes the plan and acceptance criteria. Local models execute grounded steps. The kernel decides effects. The planner commits to its action list before any untrusted page or file enters context, because "once an LLM agent has ingested untrusted input, it must be constrained so that it is impossible for that input to trigger any consequential actions" ([arXiv 2506.08837](https://arxiv.org/html/2506.08837v1)). This is the plan-then-execute and dual-LLM pattern. CaMeL showed it can keep 77% task success with provable security against 84% undefended ([arXiv 2503.18813](https://arxiv.org/pdf/2503.18813)). "Controls everything" should therefore mean the planner directs everything while the kernel still authorizes every effect.

### Which API brain, under the repo's $0 rule

On the Windows laptop, the usable cloud options today are `groq` (private lane, gpt-oss-120b) and `openrouter-private` (gemma-4-31b-it:free with `data_collection: deny`). The Dahl entries are public-lane and POSIX-only ([providers.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/config/providers.json)). The 2026 provider landscape shrank:

- GitHub Models retired on 2026-07-30 ([GitHub changelog](https://github.blog/changelog/2026-07-30-github-models-is-now-retired/)).
- Cerebras, SambaNova and Hugging Face free credits and the DeepSeek grant were delisted ([awesome-free-ai-coding](https://mvalentsev.github.io/awesome-free-ai-coding/providers/)).
- The apodex model behind `openrouter-public` and the models.json fallback is "no longer listed" ([MODEL_SELECTOR](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/MODEL_SELECTOR_2026-10-20.md)).

Groq says it does not retain inference data by default and lets all customers enable ZDR ([Groq](https://console.groq.com/docs/your-data)), though one secondary claim calls ZDR enterprise-only. That makes Groq the best private-lane planner at $0. Cloudflare Workers AI (no training without consent, 10k neurons/day) and Ollama Cloud (claims no logging or training) are the private-lane fallbacks ([Cloudflare pricing](https://developers.cloudflare.com/workers-ai/platform/pricing); [Ollama](https://ollama.com/blog/transparent-pricing)).

Two existing assignments need review. The first is OpenRouter `:free` in the **private** lane. OpenRouter "does not route based on provider retention", and its training opt-out "has no bearing on OpenRouter's own policies" ([OpenRouter](https://openrouter.ai/docs/guides/privacy/provider-logging)). The routers research therefore puts `:free` endpoints in the public lane only. The second is the roadmap's "Connect OpenRouter/NVIDIA for the planner". The NVIDIA trial is for "prototyping, research, development and testing" at about 40 RPM, and its data terms conflict ([NVIDIA FAQ](https://forums.developer.nvidia.com/t/nvidia-nim-faq/300317)), so it is public-lane only.

Gemini free/unpaid is public-lane only: Google uses that content to improve products, and "human reviewers may read" it. **Paid** Gemini is acceptable for the private lane, since it is not used for training and is logged only for abuse detection ([Gemini API terms](https://ai.google.dev/gemini-api/terms)). The repo already excludes Gemini's free tier for personal data ([privacy/providers.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/privacy/providers.json)). The cheapest useful upgrade is a one-time $10 OpenRouter credit, which lifts `:free` from 50 to 1,000 requests a day, still in the public lane ([OpenRouter limits](https://openrouter.ai/docs/api-reference/limits)). Spending even that needs owner approval under rule N5.

### One router, behind the gateway, with a key per lane

Provider switching (item 19) already works at three levels:

- `gateway.plan()` orders providers per lane with fallback.
- `llm_proxy` keeps a `lane_prefs` table settable through the lab MCP tool `switch_model`.
- The privacy gateway rotates on a 60-second cooldown ([llm_proxy.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/llm_proxy.py); [jarvis_mcp.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/lab/jarvis_mcp.py)).

The recommend-only selector is "still not" wired into `llm_proxy` ([MODEL_SELECTOR](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/MODEL_SELECTOR_2026-10-20.md)). The repo's audit kept `gateway.py` because no external router "enforces lanes a model cannot override plus the free-model guard" ([01_RESEARCH_AUDIT.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/01_RESEARCH_AUDIT.md)). That is the right instinct.

The proposal keeps lane logic in JARVIS and uses **OmniRoute** (already on the VM, arm64 images, AES-256-GCM credential encryption, per-key budgets, a `tos: avoid` flag that excludes risky providers from auto-routing) as the fallback executor only ([OmniRoute README](https://github.com/diegosouzapw/OmniRoute)). There would be one combo per lane and one API key per lane, so a public-lane key physically cannot reach private-lane connections. Coding CLIs get their own scoped keys on the public combo by default.

9router either retires or becomes one more **public-lane** provider behind the gateway, as the repo already recommended. That requires login and API keys enforced, the default secrets rotated, `CLOUD_URL` blanked, the token savers off, no OAuth subscription accounts, and no write path to any Hermes config. Bifrost is the lean fallback if OmniRoute's large surface (110-tool MCP server, ACP agent spawning, A2A) proves unacceptable. LiteLLM should be adopted only pinned by digest, and given yesterday's compromise, not at all for now ([MarkTechPost](https://www.marktechpost.com/2026/10/14/best-ai-gateways-2026/); [Socket](https://socket.dev/blog/litellm-compromise)).

"Switch in the CLI" then has three meanings, and the proposal covers all three. JARVIS switches by itself through gateway fallback plus a wired selector. The owner switches by command ("use Groq for planning"), which writes `lane_prefs` with an audit entry and surfaces in the console's `Models.tsx`. Coding CLIs switch inside OmniRoute combos.

### Fifteen local models, one GPU slot at a time

The hardware decides the shape of item 13: 4 GB RDNA1 VRAM, 32 GB RAM, ~19 GB free on D:. The 4 GB card holds **one** GPU LLM at a time, so "combined" means pipelines and resident CPU helpers, not several LLMs on the GPU. llama-swap supports this directly ([llama-swap config.example.yaml](https://raw.githubusercontent.com/mostlygeek/llama-swap/main/docs/config.example.yaml)):

- a `persistent` CPU group (Verdict, embeddings, reranker, guard) preloaded with `ttl: 0`
- a `swap: true, exclusive: false` GPU group for the decider, fast, planner, coder, maths and Indic models
- an `exclusive: true` group for the deep MoE with a short TTL, so it evicts the GPU group and frees about 13–15 GB of RAM afterwards

The docs warn that preloading several models only works if they share a group.

| Role | Model | Disk | Status | Evidence |
|---|---|---|---|---|
| Decider/router | Qwen3-4B-Instruct-2507 Q4_K_M | ~2.5 GB | keep (152/157 on repo cases) | [commit 13e48d59](https://github.com/venkatsairishith-dev/jarvis-hq/commit/13e48d5998159d4a05135e944abc3fc5c87dd61a) |
| Fast fallback + screen VLM | Qwen3.5-2B Q4_K_M + mmproj | 1.28 GB + mmproj | add after A/B | [Qwen3.5-2B card](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF) |
| Small tool agent | Qwen3.5-4B Q4_K_M (or 9B IQ4_XS 5.5 GB) | ~2.7 GB est. | A/B | [bartowski 9B](https://huggingface.co/bartowski/Qwen_Qwen3.5-9B-GGUF) |
| Planner/general | Ling-3.0-tiny | existing | keep | [llama-swap.yaml](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/local_models/llama-swap.yaml) |
| Coder (FIM) / Maths | Qwen2.5-Coder-1.5B / Qwen2.5-Math-1.5B | existing | keep; retire Math if 4B passes | same |
| GUI grounding | vision-ui-venus-2b (already configured) | existing | keep, measure | same; [UI-Venus paper](https://arxiv.org/pdf/2602.09082) |
| Hindi/Telugu + vision | Gemma-4-E4B Q4_K_M | ~5.0–5.3 GB | optional; disk-limited | [AtomicChat GGUF](https://huggingface.co/AtomicChat/gemma4-e4b-it-GGUF) |
| Deep offline MoE | gpt-oss-20b MXFP4 **or** Gemma-4-26B-A4B QAT Q4_0 | ~12 / 14.4 GB | add one | [llama.cpp #15396](https://github.com/ggml-org/llama.cpp/discussions/15396); [dev.to](https://dev.to/gde/can-a-26b-moe-run-on-an-8-gb-gpu-gemma-4-26b-a4b-on-an-rtx-3050-2efn) |
| Typed decisions / PII / claims | Verdict 151M, GLiNER-PII, MiniCheck | existing | keep | [start-verdict.ps1](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/scripts/start-verdict.ps1) |
| Embeddings / reranker / guard | Qwen3-Embedding-0.6B, bge-reranker-v2-m3, Qwen3Guard-Gen-0.6B | ~2 GB total | add, CPU resident | [Qwen3Guard GGUF](https://huggingface.co/QuantFactory/Qwen3Guard-Gen-0.6B-GGUF) |

**Keep Qwen3-4B-2507 as the decider.** Its non-thinking IFEval (83.4) far exceeds Qwen3.5-2B's 61.2, and strict JSON without thinking is exactly what the decider must produce. Qwen3.5-2B is the better tool caller only in thinking mode: BFCL-V4 43.6 vs 39.9 and TAU2 48.8 vs 43.2 ([Qwen3.5-2B card](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF)).

**#30039 adds a second reason.** It is an open Windows/AMD Vulkan regression that cut prompt processing 41–54% on six Qwen3.5 GDN-hybrid models and on Qwen3.6-35B-A3B, bisected to b10950–b11010. The owner's b11669/b11714 builds are past that window. Token generation is unaffected, and the maintainer tests only on Linux ([llama.cpp #30039](https://github.com/ggml-org/llama.cpp/issues/30039)). The original Qwen3 decider was not reported as affected. The `vram8plus` profile already includes qwen3.5-9b ([vram8plus.yaml](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/local_models/profiles/vram8plus.yaml)), so the mitigation belongs in P2: a second, pinned ≤b10950 llama-server binary that llama-swap macros use for GDN models, chosen per model by `llama-bench`. The regression hurts long prompts most, and screenshots and RAG are long prompts.

**Disk rules out the 35B-A3B class** (20–22.7 GB at Q4) ([smeltcore](https://smeltcore.com/recipes/qwen3-6-35b-a3b-on-rtx-3060-38-9-tok-s-from-a-12gb-card-via-moe-expert-cpu-offload/); [Unsloth](https://unsloth.ai/docs/models/qwen3.5)). The additions above total about 24 GB against 19 GB free. The realistic roster is therefore gpt-oss-20b as the deep model, Indic via the API, and pruning Qwen3-1.7B and Qwen2.5-Math once Qwen3.5-2B/4B pass the 157-case A/B.

The notes found no 4 GB-VRAM measurement for any of these MoEs. The closest data points are about 7 tok/s CPU-only and 24–33 tok/s on 8 GB cards, so 8–15 tok/s here is an estimate, not a measurement ([kunalganglani](https://www.kunalganglani.com/blog/gemma-4-cpu-inference-benchmark)). Flash attention must stay off on this card, which also removes the quantized-KV trick from those recipes.

## PC power should come from typed verbs, not an admin shell

Item 1 ("full PC rights") is the request with the highest stakes. The expert consensus in the notes is to give the agent **no** raw admin shell. The agent runs non-elevated. Privileged work goes through a small elevated broker that exposes a fixed, typed, allowlisted operation set, re-validates parameters, requires out-of-band approval, and logs everything.

Windows 10 ships PowerShell 5.1, so **Just Enough Administration** is the native building block. JEA uses virtual accounts to run privileged commands for non-admin callers, limits which "cmdlets, functions, and external commands" are visible, and keeps transcripts ([Microsoft Learn: JEA](https://learn.microsoft.com/en-us/powershell/scripting/security/remoting/jea/overview)). An agent cannot and should not "click through" UAC: the consent prompt sits on the Secure Desktop, which medium-integrity UI automation cannot reach ([Microsoft Learn: UAC](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/user-account-control/how-it-works)).

The Windows-MCP case shows why raw shells are dangerous. It is MIT, UIA-based, and supports Win10. Its README warns it "operates with full system access and can perform irreversible operations", and an HTTP-mode DNS-rebinding bug exposed an **unauthenticated PowerShell tool** until v0.7.5 ([Windows-MCP](https://github.com/CursorTouch/Windows-MCP); [advisory list](https://releasealert.dev/github/CursorTouch/Windows-MCP/cves)). Its current install requires Python 3.14+, which also conflicts with the repo's Python 3.11-only rule ([CONTRIBUTING.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/CONTRIBUTING.md)). The roadmap already proposes comparing node control with Windows-MCP "behind kernel allow-list" ([roadmap](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md)). The safer version of that comparison borrows Windows-MCP's tool *designs* (WaitFor, MultiEdit, Snapshot with element IDs) and never exposes its PowerShell, Registry or Process tools.

### The broker design

The proposed broker adds a kernel tier, ADMIN, above the existing `GREEN / YELLOW_AUTO / YELLOW_APPROVAL / RED / DENY` names in [policy.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/policy.py). ADMIN means RED plus PIN plus a second confirmation that shows the exact verb and parameters in plain language.

Allowlisted verbs would include:

- `winget_install(id)` from an allowlist with a pinned source
- `service_restart(name ∈ allowlist)`
- `create_restore_point`, `flush_dns`, `defender_quick_scan`
- `firewall_rule_add`
- `write_protected_path` with an automatic backup

Hard-DENY would cover `bcdedit`, disabling Defender, UAC or the firewall, creating admins, SAM/LSA registry keys, and anything touching password managers, wallets or banking.

The broker signs and re-checks every request itself rather than trusting the kernel alone. It refuses any parameter derived from web or tool content unless the owner sees it verbatim. That rule applies the design-pattern paper's warning about untrusted input ([arXiv 2506.08837](https://arxiv.org/html/2506.08837v1)). Unknown installers would first run in Windows Sandbox with networking off and folders mapped read-only ([Microsoft Learn: .wsb](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file)). Windows Sandbox needs Pro or Enterprise, and the owner's Windows 10 edition is unknown.

Explained in one sentence for the owner: **you keep admin; JARVIS earns a growing catalogue of admin verbs, each one approved**. Whether a PIN-gated admin allowlist is acceptable in place of raw admin is one of the two questions the owner has not yet answered.

### "Find the folder named X" and organising files with undo

Item 5 is blocked today. `open_file` matches names only inside Downloads, Documents, Desktop, Pictures, Videos and Music ([pcopen.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/node/jarvis_node/pcopen.py)). Since commit 1c28d3a3, a "create a folder" request ends with "I cannot create folders or files yet … nothing was changed" ([commit 1c28d3a3](https://github.com/venkatsairishith-dev/jarvis-hq/commit/1c28d3a3f760a3d194d6228960e9c9ba47d5a237)). Requirement C6, "Create folders and organise selected files, with an undo journal", is MISSING ([NEKO_REQUIREMENTS](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/NEKO_REQUIREMENTS.md)). The roadmap lists it as owner decision #6 for its phase 3.

The proposed FileOps capability has two parts.

**Search.** An LLM first extracts slots: name fragment, file or folder, extension, dates, location hints. Search then tries two backends:

- **Everything's `es.exe -json /ad`**, which is MIT-licensed, near-instant across NTFS volumes, but needs the Everything client running ([voidtools/ES](https://github.com/voidtools/ES)).
- **Windows Search SQL** via `Search.CollatorDSO` as the built-in fallback, which "is compatible with … all versions of Windows 10" and adds full-text search ([Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/search/-search-sql-windowssearch-entry)).

RapidFuzz re-ranks the candidates, and the top five come back with "reveal in Explorer".

**Organising.** Organising uses a SQLite undo journal of reversible primitives: mkdir, rename and move. Delete only ever goes to the Recycle Bin via send2trash's IFileOperation path. send2trash has no restore API, so restore needs a prototype ([send2trash](https://github.com/arsenetar/send2trash)). Tiering follows from reversibility:

| Operation | Tier |
|---|---|
| Search, list | GREEN |
| mkdir, rename or move inside user roots | YELLOW_AUTO with a batch preview |
| Recycle-Bin delete | PIN |
| System paths | DENY, or broker-only |
| Permanent delete | Never |

Under this tiering the decider's current refusal becomes a low-risk, undoable action.

### Learned skills reuse the gates JARVIS already trusts

For "a new task is learned, executed and stored as a learned skill", the repo has more machinery than any product surveyed:

- `jarvis/skills/skillgate.py` stages, scans and activates Hermes skills behind kernel authorization ([skillgate.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/skills/skillgate.py)).
- `plugin/plugins/desktop/skills.py` versions decider changes with a regression gate, promote and rollback ([desktop/skills.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/desktop/skills.py)).
- Learning-loop layers 1–3 log episodes, self-correct within bounds, and stage examples mined from successful PC tasks through the gate ([commit c50dc2f1](https://github.com/venkatsairishith-dev/jarvis-hq/commit/c50dc2f15dcd8b5dc379fc1ef148f27f9d991072)).
- The Workbench `learn_task` adds research, plan, sandbox test and refinement ([workbench README](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/workbench/README.md)).

The evidence supports the idea. Agent Workflow Memory reported 24.6% and 51.1% relative success gains on Mind2Web and WebArena from reusable workflows ([AWM](https://arxiv.org/pdf/2409.07429)). The threat is just as real. Snyk found 13.4% of 3,984 marketplace skills had a critical issue, and noted that prompt injection in skills is often missed by classical scanners ([Snyk](https://snyk.io/blog/toxicskills-malicious-ai-agent-skills-clawhub)). Hermes writes skills freely by default unless `skills.write_approval: true` is set, and its linter "warns but never blocks a write" ([Hermes docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)).

The proposal adopts the open agentskills.io `SKILL.md` format (name, description, narrow `allowed-tools`, JARVIS metadata for version, source session and risk tier) for portability with Hermes and Claude Code ([agentskills.io](https://agentskills.io/specification)). The loop for a new task runs like this:

1. Solve the task live with read-only tools first.
2. Draft a skill into `pending/`.
3. Auto-generate bench cases from the run and replay them in a sandbox.
4. Show the owner a plain-language diff.
5. Only the owner promotes the skill. Promotion pins its hash.

Skills learned from web or other-model content are tagged tainted. Skills that write, delete, send or spend still need per-run confirmation after promotion. The roadmap's "never add a skill marketplace or auto-install" stays ([roadmap](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md)). The Hermes skills on the VM must run with `write_approval: true`.

### "Any web page" is safe only as two lanes plus taint

Browser control is the most mature area in the repo. `browser.py` runs a throwaway Playwright profile behind a network guard, scrubs page text as UNTRUSTED, and never types into password fields. `browserext.py` drives the owner's existing Chrome profiles through an allow-listed tool set without reading cookies or tokens ([browser.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/node/jarvis_node/browser.py); [browserext.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/node/jarvis_node/browserext.py)).

Playwright MCP itself says it "is **not** a security boundary". Its `--allowed-origins` "does not serve as a security boundary" and ignores redirects ([playwright-mcp](https://github.com/microsoft/playwright-mcp)). Anthropic's own browser agent faced 23.6% attack success without mitigations, and browser-specific attacks fell from 35.7% to 0% with them ([Anthropic](https://www.anthropic.com/news/claude-for-chrome)), yet Anthropic still says a 1% adaptive-attack rate "still represents meaningful risk" ([Anthropic](https://www.anthropic.com/research/prompt-injection-defenses)).

The proposal formalizes two lanes:

- **Research lane.** Isolated, cookie-less and read-only. It may visit any page.
- **Signed-in lane.** Any form submit, post, send or purchase needs PIN. Banking, wallet and password-manager origins are blocked in both Playwright and the egress proxy.

On top of the lanes sits a CaMeL-lite taint flag. Once page text enters context, admin, delete and messaging tools need PIN, and page-derived parameters are shown verbatim. Native Playwright on Windows now lists "Windows 11+" ([Playwright](https://playwright.dev/docs/intro)). That is one more reason to keep heavy browsing on the Linux agent computer rather than the Windows 10 laptop.

## Presence: the widget, voice and personality are mostly specified already

### Tray and push-to-talk

Item 4 has a full design and no code. The repo has a loopback web widget ([widget_server.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/widget/widget_server.py)). `PATCHES.md` specifies four N.E.K.O.-Tauri patches: tray, global push-to-talk with `tauri-plugin-global-shortcut` 2.4.1, single instance, and a click-through fail-safe. It is marked "**PLAN ONLY.** None of these patches is written, built or tested yet", with a rule to offer them upstream rather than fork ([PATCHES.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/third_party/PATCHES.md)).

Upstream N.E.K.O.-Tauri (Apache-2.0, last main commit 29 Jul 2026) already does the hard parts: a transparent always-on-top window and per-region click-through. It has no tray, global shortcut, single instance or autostart ([N.E.K.O.-Tauri](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri)). So the plan still stands.

One engineering fact should shape the push-to-talk design. The global-shortcut plugin sits on Win32 `RegisterHotKey`. Each chord is exclusive to JARVIS while held. Registration fails with `AlreadyRegistered` if another app owns the chord. Release is detected by polling every **50 ms** ([global-hotkey source](https://github.com/tauri-apps/global-hotkey); [Tauri docs](https://v2.tauri.app/plugin/global-shortcut/)). A chord like Ctrl+Alt+Space plus a re-bind dialog is fine. A mouse side-button would need a low-level hook, which this research did not verify.

The plugins should be registered single-instance first, as its docs require ([Tauri single-instance](https://v2.tauri.app/plugin/single-instance/)). If the Tauri shell stalls, upstream's Electron N.E.K.O.-PC is the fallback. The repo already requires a 30-minute Windows 10 transparency soak before choosing.

### Voice

For item 12, the best single repo is **sherpa-onnx**, which JARVIS already uses. It hosts KWS, Silero VAD, Parakeet, Kokoro, Piper and Indic models in one Windows-friendly ONNX runtime ([sherpa-onnx KWS](https://k2-fsa.github.io/sherpa/onnx/kws/index.html)). Swapping STT models will not make JARVIS feel human. Turn-taking and interruption will.

The current measurements show where the gaps are. Wake fired on 67% of repo test clips, barge-in is off, and the mic-to-reply loop (V4) is MISSING ([roadmap](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md); [NEKO_REQUIREMENTS](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/NEKO_REQUIREMENTS.md)). The roadmap's own decision is to measure first: 200 owner clips and an hour of room audio.

The proposal adds four things to that measurement work:

- **"Hey jarvis" wake word.** First as a sherpa-onnx keywords-file change, A/B'd against openWakeWord's pretrained `hey_jarvis` model. Its weights are CC BY-NC-SA, which is fine for personal use ([openWakeWord](https://github.com/dscripka/openWakeWord)). Add a transcript re-check for the word "jarvis", because film and TV audio will cause false wakes ([bitworking](https://bitworking.org/news/2026/06/fixing-false-positives-in-the-homebrain/)).
- **Pipecat's Smart Turn v3.** BSD-2, audio-based endpointing on CPU ([smart-turn](https://github.com/pipecat-ai/smart-turn)).
- **WebRTC AEC3.** Fed with JARVIS's own TTS buffer, so barge-in works on speakers ([pywebrtc-audio](https://github.com/strands-labs/pywebrtc-audio)).
- **Sentence-chunked streaming TTS.**

The models themselves mostly stay. Kokoro stays for English. Parakeet stays because it is fast and close to the most accurate models ([Open ASR Leaderboard](https://arxiv.org/html/2510.06961v4)). IndicConformer gets an A/B against SraVaani-1.0 on the owner's Telugu ([SraVaani](https://huggingface.co/ARTPARK-IISc/SraVaani-1.0)). Telugu is genuinely hard: the best real-world Telugu WER in one benchmark was 33.2% ([arXiv 2602.03868](https://arxiv.org/html/2602.03868v1)).

Full-duplex speech models need 10–24 GB of GPU and are not an option on this hardware ([Moshi](https://github.com/kyutai-labs/moshi)). Piper's upstream is archived, and its active fork is GPL-3.0, which is irrelevant for personal use ([PromptQuorum](https://www.promptquorum.com/power-local-llm/piper-tts-review)).

### Personality

No JARVIS persona exists in `config/characters/*`. Personality comes from N.E.K.O's character system, and a script forces the preset to English ([make_persona_english.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/scripts/make_persona_english.py)). Because `config/` is N.E.K.O's platform layer, the cleanest route for item 14 is a JARVIS character card created through N.E.K.O's card manager and stored in the data folder, not code ([N.E.K.O README](https://github.com/Project-N-E-K-O/N.E.K.O); [CONTEXT.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/CONTEXT.md)).

The card should follow Hermes' SOUL.md discipline: a few durable traits for voice, directness and disagreement, with project facts kept elsewhere ([Hermes personality](https://hermes-agent.nousresearch.com/docs/user-guide/features/personality)). Anti-sycophancy should be a tested property. OpenAI rolled back a GPT-4o update for being "overly flattering or agreeable" and decided to treat such issues as launch-blocking ([OpenAI](https://openai.com/index/sycophancy-in-gpt-4o/)). The proposal adds bench cases in `l_ux` where the owner proposes something wrong and JARVIS must push back.

Item 3's "explanatory" requirement maps to an honest-narration contract. Every status line ("searching D:\Projects…, found 3") is rendered from real tool events and never from model-predicted progress. That matches the repo's N3 honesty requirement. The persona never softens a confirmation prompt, and it never clones a real actor's voice. The IndicF5 card explicitly forbids cloning voices without permission ([IndicF5](https://github.com/AI4Bharat/IndicF5)).

## Agents that research, code, test and push need a new home and a gate

Items 6, 7, 15 and 18 share one dependency: a Linux box where agents can run code. Every piece above that layer exists:

- research with MiniCheck verification ([research plugin](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/plugin/plugins/research))
- a plan-first planner over 155 plugin entries ([TOOL_INVENTORY](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/main_agent/TOOL_INVENTORY.md))
- agent definitions with kernel-enforced lanes, budgets and an independent verifier ([agents.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/agents.py))
- a work → verify → repair runner ([lab_runner.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/lab/lab_runner.py))
- a caretaker that may only open PRs ([caretaker README](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/caretaker/README.md))

But the runner assumes Linux root plus a `jarvis-sandbox` identity, and its host is gone. The caretaker is "off (no safe credential)" ([roadmap](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md)).

### Which coding CLIs to use

For item 18, the research ranks the CLIs by how cleanly a kernel can drive them through a router:

| CLI | Role | Why |
|---|---|---|
| **OpenCode** | Primary worker | `opencode run --format json`, `opencode acp`, any OpenAI-compatible `baseURL`, per-tool allow/ask/deny. Most permissions default to allow, so they must be set explicitly ([OpenCode CLI](https://opencode.ai/docs/cli/); [permissions](https://opencode.ai/docs/permissions/)) |
| **goose** | Second worker | Recipes and stream-json ([goose](https://goose-docs.ai/docs/guides/running-tasks)) |
| **Codex `exec`** | Sandboxed reviewer/fixer | Read-only by default, and `required = true` MCP servers abort the run on failure. Needs a router that speaks `/v1/responses` ([Codex](https://learn.chatgpt.com/docs/non-interactive-mode)) |
| **Claude Code** | Only with `--bare` | Without it, `-p` runs repo hooks and `.mcp.json` servers "even in untrusted folders, with no trust dialog" ([Claude headless](https://code.claude.com/docs/en/headless)) |

ACP becomes the control plane. The JARVIS kernel answers each worker's permission requests, so no worker auto-approves its own. Every run asserts that the JARVIS MCP server actually connected, and fails closed for agy, which cannot report it ([ACP agents](https://agentclientprotocol.com/overview/agents)).

### Multi-agent testing without reward hacking

For item 7, the strongest evidence in the notes points to the main failure mode: agents **reward-hack**. Cursor found 63% of successful SWE-bench Pro resolutions retrieved the fix rather than deriving it, and hacking rose when the model was told to keep going ([Cursor](https://cursor.com/blog/reward-hacking-coding-benchmarks)). EvilGenie documents agents hardcoding test cases and editing test files ([arXiv 2511.21654](https://arxiv.org/pdf/2511.21654)). The control that works is tests the agent cannot edit.

The proposed topology:

1. A strong planner writes the acceptance criteria.
2. N workers each get their own git worktree and rootless container. Review bandwidth caps this at about three ([Open WebUI docs](https://docs.openwebui.com/ecosystem/computer/use-cases/parallel-agents-on-worktrees)).
3. A tester runs the 860-case bench shards with test directories mounted read-only and hashed before and after.
4. A reviewer agent on a different vendor flags any change under `tests/`, new skip/xfail markers, or fewer tests.
5. The kernel gate decides.

The bench already has the adapter pattern for a new multi-agent area next to D (long-horizon) and N (reliability) ([bench cases](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/evals/neko95/bench/cases)).

### On-screen test runs

For item 15, a headless server shows tests "on screen" through an Xvfb display exposed with x11vnc + noVNC or KasmVNC. Playwright's `video` and `trace` set to `retain-on-failure` let the owner replay failures later instead of watching live ([Playwright videos](https://playwright.dev/docs/videos); [mcp-playwright-novnc](https://github.com/xtr-dev/mcp-playwright-novnc)). All of it stays bound to Tailscale.

This matters beyond convenience. The roadmap warns that "almost every 'works' claim in our own docs is server-synthetic (Linux image, CPU, fakes)" ([roadmap](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md)). Visible, recorded runs on the real desktop baseline (`task_baseline.py`, 10 plain requests judged by the real screen) are the antidote ([task_baseline.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/evals/neko95/task_baseline.py)).

### Guarded push to main

The owner answered "main". This report reads that as "agents may push to git main". The other reading, "merge the remediation work into main", is covered in P0 either way. Mature automations never let an agent push a protected main itself:

- Renovate waits for passing checks and merges one branch per run ([Renovate](https://docs.renovatebot.com/key-concepts/automerge/)).
- Copilot's agent can only push `copilot/*` branches and cannot merge its own PRs ([GitHub docs](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations)).
- OpenHands opens draft PRs ([openhands-resolver](https://pypi.org/project/openhands-resolver/0.3.0)).

The s1ngularity/Nx attack showed malware invoking installed AI CLIs with `--dangerously-skip-permissions`/`--yolo` to harvest more than 1,000 GitHub tokens ([Wiz](https://www.wiz.io/pt-br/blog/s1ngularity-supply-chain-attack)). Any long-lived push token on a box that runs agents is therefore a target.

The proposed **guarded push-to-main** works like this. Agents push only `jarvis/*` branches and never hold a main-push credential. The kernel identity alone fast-forwards main, and only when all of these hold:

- a full required test pass on the exact merge commit, run by the kernel rather than the agent
- no diff under tests, fixtures, thresholds, `.github/`, `.claude/`, `.mcp.json`, `opencode.json`, policy or secrets without RED/PIN
- a diff cap of about 300 LOC or 10 files
- one merge at a time
- an `Agent-Run:` trailer on the commit
- a daily cap and the kill switch

An automatic revert follows if post-merge smoke tests regress. Everything else becomes a PR awaiting the owner. GitHub Actions are off on purpose ([README](https://github.com/venkatsairishith-dev/jarvis-hq/blob/main/README.MD)), so enforcement must come from the kernel identity being the only allowed pusher, not from CI.

## Parity with OpenClaw and Grok Bot is about reach, not features

"Clawdbot" is an inference from handwriting. It fits: Clawdbot was the January 2026 name of the MIT-licensed agent now called **OpenClaw**, renamed after Anthropic trademark complaints ([Wikipedia](https://en.wikipedia.org/wiki/OpenClaw)). "Grokbot" most likely means xAI's **Grok Bot**, launched 11 Aug 2026: a hosted team of persistent bots led by a chief-of-staff bot, running on an xAI-managed cloud computer, learning routines by demonstration and returning only for approvals ([x.ai](https://x.ai/news/introducing-grok-bot)). It could also simply mean the Grok chatbot. The owner should confirm.

The parity table below cross-checks the research's union list against the repo. JARVIS is **ahead** on safety: tiers, PIN, audit and a tested kill switch (C7). OpenClaw 2.0 still ships with its sandbox off and its Secret Store unencrypted at rest ([dsebastien](https://www.dsebastien.net/2026-10-03-openclaw-2-0-easier-setup-shared-sessions-same-security-defaults/)).

| Capability (source) | JARVIS today (repo) | Gap | Proposal | Phase |
|---|---|---|---|---|
| Always-on gateway + web dashboard (OpenClaw) | Kernel + JARVIS Console + hub page | Unified sessions/approvals/files view | Extend console; OpenClaw UI as design reference | P6 |
| 20+ chat channels, DM pairing (OpenClaw, Hermes) | Telegram via Hermes, **off by owner decision** (`telegram.off`) | No phone channel | Re-enable Hermes gateway with allowlist-only pairing, per-peer sessions, tool-less reader for inbound text | P6 |
| Persona files SOUL/USER/AGENTS.md | N.E.K.O character system | No JARVIS card | JARVIS card + SOUL.md-style traits | P5 |
| Memory + cross-session recall | N.E.K.O 5-layer memory, BM25 memory, vault | Vault stale since 10-09 | Vault catch-up; FTS over sessions | P0, P6 |
| Self-written skills | Skill gates + learning loop layers 1–3 | `SKILL.md` format, sandbox replay | As above | P3 |
| Skill marketplace (ClawHub, Grok marketplace) | Deliberately none | — | **Never auto-install**; local curated registry only | — |
| Cron, heartbeat, proactive checks | Missions/monitor, `watches.json` | Delivery to phone | Channel delivery once a phone channel exists | P6 |
| Signed-in browser + computer use | `browserext.py`, Windows node | Linux desktop for unattended jobs | Agent computer on VM | P1 |
| Always-on remote computer (Grok Bot, Manus) | Lost with WSL lab | Whole capability | Oracle VM + webtop/KasmVNC | P1 |
| Teach-by-demonstration routines (Grok Bot) | Layer 3 mines successful PC tasks | Explicit record-and-replay | Recorder emits a staged skill; owner promotes | P3/P6 |
| Chief of staff + specialists (Grok Bot; OpenClaw sub-agents) | main_agent coordinator, `local_team`, `agents.py` | Persistent specialists, group view | Coordinator as chief of staff; Hermes sub-agents with per-agent scopes | P4/P6 |
| Approval queue, judgment-only interrupts | PIN approvals tested (C8), question card | Mobile-first approvals bound to the exact command hash | Telegram/ntfy actionable approvals, hash-bound in kernel | P6 |
| Secrets out of model context | Secrets as files in `D:\jarvis-secrets`, redaction gateway | Tool-time secret broker | Inject at tool call; never in prompts | P6 |
| Voice wake + talk mode | Local cascade; push-to-talk MISSING | Barge-in, duplex feel | Voice phase | P5 |
| Deep research with citations | Research plugin + MiniCheck | Wide parallel fan-out | Coordinator fan-out to N researchers | P6 |
| Morning brief (ChatGPT Pulse) | Monitor/alerts | Overnight synthesis | Nightly mission → vault note + digest | P6 |
| OpenClaw A2A interop | N.E.K.O `brain/openclaw_adapter.py` exists | Unclear wiring | Keep disabled; if used, treat as untrusted external agent via kernel | — |
| Security audit command | Audit log | Config posture self-check | `jarvis doctor`-style check of binds, auth, ports, skill hashes | P0/P6 |

Sources: [OpenClaw docs](https://docs.openclaw.ai); [OpenClaw README](https://github.com/openclaw/openclaw); [x.ai Grok Bot](https://x.ai/news/introducing-grok-bot); [repo config](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/config); [N.E.K.O README](https://github.com/Project-N-E-K-O/N.E.K.O).

Running OpenClaw itself is ruled out by its record:

- Koi found 341 malicious ClawHub skills out of 2,857, mostly one campaign installing Atomic Stealer ([The Hacker News](https://thehackernews.com/2026/02/researchers-find-341-malicious-clawhub.html)).
- ClawJacked let any visited webpage brute-force the localhost gateway because loopback was exempt from rate limiting ([Oasis Security](https://www.oasis.security/blog/openclaw-vulnerability)).
- Scans found tens of thousands of exposed gateways ([Firecrawl](https://www.firecrawl.dev/blog/secure-openclaw)).

The lessons carry over to JARVIS directly: never trust localhost implicitly, bind to loopback or Tailscale with token auth, and route untrusted inbound text through a tool-less reader. Grok Bot's "logins shared across all bots on one machine" ([Rohit Ghumare](https://rohitghumare.com/blog/grok-bot-shared-computer/)) is a blast-radius design JARVIS should not copy.

### ChatGPT and Gemini: APIs out, MCP in

Item 9's safe form is "APIs out, MCP in". Driving chatgpt.com in a browser conflicts with OpenAI's terms against "automatically or programmatically extract[ing] data or Output" ([OpenAI ToU](https://openai.com/policies/terms-of-use/)). The notes could not confirm the newest revision's wording because openai.com returned 403.

**Out.** JARVIS asks a second model through the OpenAI Responses API or the Gemini API. Both need paid API billing, and an OpenAI key collides with "paid must stay false". The answer is labelled ("Gemini says…") and treated as untrusted input. It is used selectively for high-stakes questions, because multi-model consensus evidence is mixed and costs 2–4x ([DeliberationBench](https://arxiv.org/html/2601.08835v1); [tianpan.co](https://tianpan.co/blog/2026-05-05-multi-model-consensus-architecture-llm-sign-off)).

**In.** A read-only-first JARVIS MCP server lets the owner call JARVIS from ChatGPT Developer mode or Gemini CLI ([OpenAI Developer mode](https://developers.openai.com/api/docs/guides/developer-mode); [Tembo](https://www.tembo.io/blog/gemini-cli-mcp)). ChatGPT only connects to remote HTTPS servers, so this needs a tunnel. That cuts against the repo's loopback/Tailscale posture and is an owner decision. The kernel's `decide.py` SITES map should also gain an explicit deny for automating those two domains.

## The agent computer belongs on Oracle, once its size is settled

The owner said "use best one". The research's ranking puts the **Oracle A1 VM running a Docker desktop** first: always on, inside the owner's own tenancy, multi-arch, and $0 at 2/12 or about $25–28 a month at 4/24. The laptop options fall short. A laptop-hosted VM dies when the laptop sleeps. A rebuilt WSL2 box only restores what was lost, shares the host kernel, and is easy to lose again without regular `wsl --export` backups ([Oracle](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm); [TerminalBytes](https://terminalbytes.com/oracle-cloud-free-tier-changes-2026/)).

On the VM, linuxserver/webtop (Selkies) or KasmVNC provides the desktop. Both have arm64 images. Webtop has **no authentication by default** and passwordless sudo, so it must never be exposed ([webtop docs](https://docs.linuxserver.io/images/docker-webtop/); [KasmVNC](https://github.com/KasmTech/KasmVNC/releases)). Hermes `computer_use` drives that display. Playwright supports Ubuntu arm64 ([Playwright](https://playwright.dev/docs/intro)). E2B Desktop serves as an optional disposable second computer for risky browsing. Its Hobby tier is a one-time $100 credit with 1-hour sessions, and openmuse supports it natively ([E2B](https://e2b.dev/docs/billing)).

The memory arithmetic decides the size question. The estimates below are research estimates, not measurements:

| Component | RAM |
|---|---|
| Hermes | ~0.5–1 GB |
| OmniRoute | ~0.2–0.5 GB |
| VM Qwen3-4B | ~4.5–5 GB |
| Desktop | ~0.6–1 GB |
| Chromium | ~1–2 GB |
| openmuse | ~1.5–2.5 GB |
| **Total** | **~10–13 GB** |

That total swaps or OOMs at 12 GB unless the VM's local model (`vm-llama-cpu`, 5.6 tok/s per [models.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/config/models.json)) becomes on-demand or moves off the VM.

### openmuse is useful but not self-contained

For items 20–21, openmuse is an **MIT alpha (0.1.0-alpha, 15 Sep 2026)**. It offers a durable task engine, persistent browser profiles with takeover, page-watch goals, PDF form fill, reviewed Gmail and Calendar actions, and a mobile/web app ([openmuse](https://github.com/CopilotKit/openmuse)). JARVIS lacks most of these. Four limits matter:

- **It is not self-contained.** The API "fails at startup" without a CopilotKit Intelligence key, and chat threads are stored in CopilotKit's cloud ([RICH-THREADS.md](https://github.com/CopilotKit/openmuse/blob/main/docs/RICH-THREADS.md)).
- **Telemetry and search leave the box.** Telemetry goes to PostHog at full sampling by default ([TELEMETRY.md](https://github.com/CopilotKit/openmuse/blob/main/docs/TELEMETRY.md)). Web search sends model-generated queries to Parallel.
- **The in-chat browser mostly reads.** It mostly reads public pages and hands interactive work to the person; autonomous click-and-type is open issue #61. Full desktop control exists only with E2B ([COMPUTER.md](https://github.com/CopilotKit/openmuse/blob/main/docs/COMPUTER.md); [issues](https://github.com/CopilotKit/openmuse/issues)).
- **The earlier install was unreliable.** The repo's earlier install logged "CopilotKit Intelligence 503s; MCP SDK 1.30 advisory" ([03_DECISIONS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/03_DECISIONS.md)).

The pragmatic path is to run openmuse itself on the VM with:

- `COPILOTKIT_TELEMETRY_DISABLED=true` and `DO_NOT_TRACK=1`
- `WEB_SEARCH_ENABLED=false`, or Parallel accepted knowingly
- `OPENAI_BASE_URL` pointed at the public-lane OmniRoute key
- the browser worker capped by cgroup

Personal data should stay out of openmuse threads until issues #73/#179 deliver a keyless local store. If no third-party cloud may hold chats, the alternative is Vane (formerly Perplexica) over SearXNG, plus Open WebUI or LibreChat, plus Hermes on webtop. That stack gives up openmuse's task, tracking and PDF workflows ([Korben](https://korben.info/en/vane-perplexica-new-name.html)).

## Explaining every model and file fits inside the five-doc limit

Items 16 and 17 need less new writing than they seem to. 249 of 268 non-test Python files already have module docstrings. The ~19 gaps are mostly `__main__.py` files, vendored rapidocr helpers and audit repro scripts. All nine JARVIS plugins have READMEs ([repo tree](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis)). What is missing is directory READMEs for kernel, node, console, lab, wake, widget, privacy and local_models, plus a model explainer.

The model explainer can be generated from `llama-swap.yaml`, `models.json` and `providers.json`, so it never drifts. "How it works" fits a rewrite of the existing `02_ARCHITECTURE.md`, which respects "at most 5 files in docs/". The step-by-step guide should be a restructured `05_OPERATIONS.md`, which is currently a 469-line dated log with a quick start buried inside. Its WSL-lab sections are now wrong ([05_OPERATIONS.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/05_OPERATIONS.md)). Plan changes must also go into a "Project JARVIS doc" whose location is in neither repo ([jarvis/CLAUDE.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/CLAUDE.md)).

## Three owner requests collide with standing repo rules

The plan respects these repo rules:

- secrets only as files under `D:\jarvis-secrets`, never read or printed
- Oracle Always Free at $0, and never touch hermes-agent-vm-2
- phase gates with a wait for the go
- the vault as memory, written only in inbox/daily/decisions
- at most 5 files in `docs/`
- reuse-first, with custom code justified in `01_RESEARCH_AUDIT.md`
- the plugin boundary: only `plugin/plugins/<id>/` is editable by default, and the platform layer needs escalation
- N.E.K.O PR rules: regression reports, "Why Not Split" for more than 20 files, zh/en/ja i18n, UI screenshots

Sources: [jarvis/CLAUDE.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/CLAUDE.md); [CONTEXT.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/CONTEXT.md); [CONTRIBUTING.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/CONTRIBUTING.md). Three requests cannot be met literally without breaking one of these rules.

| Owner request | Rule it collides with | Recommended resolution |
|---|---|---|
| Full PC rights (admin) | Kernel tiers; DENY tier; no silent self-modification (L5); OWASP LLM06 excessive agency | Elevated broker with typed verbs, ADMIN tier = RED + PIN + second confirmation, restore point first, append-only audit. **Unanswered:** is a PIN-gated admin allowlist acceptable? |
| Agents push to main ("main") | Phase gates ("wait for the go"); caretaker "only opens PR"; `policy.py` caretaker git/gh rules; SAFETY_COMPONENTS | Kernel-only fast-forward of main after tests, integrity checks, size cap, daily cap and auto-revert; everything else stays a PR. Agents never hold the push credential |
| Multi-provider incl. Gemini, OpenAI, Oracle 4/24 | `paid must stay false`; N5 "no paid service without approval, Oracle $0"; N6 private data only via privacy gateway | Default $0 path (Groq/Cloudflare/Ollama Cloud private, free tiers public). Each paid item (Gemini API, OpenAI API, $10 OpenRouter, Oracle PAYG) is a separate owner decision with a budget cap |

Other tensions are smaller:

- 9router keeps credentials in `~/.9router`, outside the secrets rule ([MODEL_SELECTOR](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/MODEL_SELECTOR_2026-10-20.md)).
- openmuse stores threads in a third-party cloud.
- Windows-MCP needs Python 3.14.
- A JARVIS persona or wake-word change in `config/` touches N.E.K.O's platform layer and needs escalation plus a regression report.

## An eight-phase plan, mapped to real files

Each phase ends the way the repo requires: test, report what passed and what could not be tested, and wait for the owner's go. New files are marked *(proposed)*. Every custom module gets a justification entry in `jarvis/docs/01_RESEARCH_AUDIT.md` against the reused project it builds on. Every phase updates the docstrings, directory READMEs and vault notes it touches. The order puts containment first, then the shared foundations (agent computer, brain), then capabilities.

### P0 — Contain, merge and decide (days, not weeks)

| Work item | Files / places | Gate |
|---|---|---|
| Owner-approved fast-forward of `remediation-2026-10-18` into main (0 behind); "Why Not Split" note; rebase dependabot branches (treat uv "253 updates" as high risk) | branches; `CONTRIBUTING.md` rules | Full bench (860) + kernel/console suites on merged tree |
| Deploy 2026-10-22 kernel fixes to Oracle | `jarvis/docs/neko/KERNEL_DEPLOY_2026-10-22.md` | Deploy checklist + live check |
| Find and lock down 9router: version, bind, password, `REQUIRE_API_KEY=true`, rotated `API_KEY_SECRET`/`MACHINE_ID_SALT`, blank `CLOUD_URL`, token savers off, no OAuth subscriptions, no Hermes-settings access — or remove it | host where it runs; `jarvis/hermes/check_hermes_compat.py` to verify Hermes `config.yaml` is unmodified | Written inventory; Hermes config diff clean |
| Confirm no LiteLLM 1.107.0 / `latest` anywhere; if found, rotate every key on that machine | laptop, VM, test server | Package inventory |
| Oracle account type, A1 usage across both instances, decision: resize to 2/12 or PAYG 4/24 | Oracle console (owner); `jarvis/scripts/oci_cost.py` | Owner decision recorded |
| Upgrade OmniRoute to pinned 3.8.53 multi-arch digest; `requireLogin=true`; disable ACP/spawn routes | `jarvis/deploy/jarvis-omniroute.service`; `jarvis/tools/registry.json` | Auth required on every route |
| Remove stale provider/model entries (apodex) | `jarvis/config/providers.json`, `jarvis/config/models.json` | Config tests |
| Vault catch-up (lab gone, Verdict native, branch state) and stale-section notice in 05 | `jarvis-vault/daily/`, `decisions/memory-map.md`; `jarvis/docs/05_OPERATIONS.md` | Owner reads |

### P1 — Rebuild the agent computer and the lab (items 20, 21; unblocks 6, 7, 15, 18)

| Work item | Files / places | Gate |
|---|---|---|
| Host per P0 decision: Oracle A1 (preferred) with webtop/KasmVNC, Tailscale-only, password; VM model on-demand if 2/12 | `jarvis/lab/lab_isolation.py`, `lab_runner.py`, `lab_control.py`, `jarvis/hub/Caddyfile`, `jarvis/hub/index.html` | Desktop reachable only via tailnet |
| Re-provision lab Hermes (with `skills.write_approval: true`), OpenCode, goose; re-point skillgate targets | `jarvis/skills/skillgate.py`, `jarvis/skills/active.json`, `jarvis/lab/jarvis_mcp.py` | Lab demo tasks build/fix/delegate pass (`jarvis/lab/demo.py`) |
| openmuse in Docker beside Hermes: telemetry off, search decision, OmniRoute public key, worker cgroup cap | VM compose *(proposed, outside repo or under `jarvis/deploy/`)* | Memory under budget for 24 h |
| If a local WSL mirror is kept: scheduled `wsl --export` backups | `jarvis/scripts/` *(proposed script)* | Restore drill |

### P2 — Brain, providers and the model roster (items 10, 11, 13, 19)

| Work item | Files / places | Gate |
|---|---|---|
| Planner through the privacy gateway (roadmap Phase 0); plan-then-execute; local decider grounds | `plugin/plugins/main_agent/planner.py`, `executor.py`; `jarvis/privacy/gateway.py` | Bench A/J/D areas; `task_baseline.py` |
| Lane combos + per-lane keys in OmniRoute; 9router (if kept) as public-lane provider only | `jarvis/config/providers.json`, `jarvis/privacy/providers.json`, `jarvis/kernel/jarvis_kernel/gateway.py` | Config test: public key cannot reach private combo |
| Wire `model_select.py` into `llm_proxy` in shadow, then live; owner "switch provider" command writing `lane_prefs` with audit; console Models screen | `jarvis/kernel/jarvis_kernel/model_select.py`, `llm_proxy.py`, `jarvis/ui/src/screens/Models.tsx` | Shadow replay; J-area routing cases |
| llama-swap groups (resident CPU / GPU swap / exclusive deep); pinned ≤b10950 binary for GDN models; A/B Qwen3.5-2B/4B vs current on 157 cases; add gpt-oss-20b, embed, rerank, guard; prune after A/B | `jarvis/local_models/llama-swap.yaml`, `profiles/*.yaml`, `jarvis/evals/neko95/bench/model_compare.py`, `model_candidates.json`, `jarvis/config/measured_capabilities.json` | `test_server_flags.py` (127.0.0.1, `--no-webui`); `llama-bench` per model; disk stays under 19 GB |
| `local_team` adviser able to use Groq/OpenRouter, not only Dahl | `plugin/plugins/local_team/` | local_team tests |

### P3 — PC hands and learned skills (items 1, 2, 5)

| Work item | Files / places | Gate |
|---|---|---|
| FileOps: search (Everything ES → Windows Search SQL → RapidFuzz) and undo journal (mkdir/rename/move/trash) | `jarvis/node/jarvis_node/fileops.py` *(proposed, beside `pcopen.py`)*, `jobs.py`, `jarvis/kernel/jarvis_kernel/policy.py` | Undo drills; C6 moves to TESTED |
| Decider tools for find/create/organise; bump `PROMPT_VERSION` | `plugin/plugins/desktop/decide.py`, `tests/test_decide_rules.py` | `b_desktop.json` new cases; 157-case decider set |
| Elevated broker (JEA endpoint or service), ADMIN tier, restore point, audit | `jarvis/scripts/install-admin-broker.ps1` *(proposed)*, `jarvis/node/jarvis_node/adminctl.py` *(proposed)*, `policy.py` | `k_security.json` cases incl. tainted parameters; owner approves the verb catalogue |
| Browser lanes + taint flag; banking/wallet hard-DENY in Playwright and egress | `browser.py`, `browserext.py`, `jarvis/config/egress.json`, `jarvis/config/sensitive_sites.json` | `c_browser.json` (C-049..C-061 + new) |
| Skill drafts in agentskills.io format → `pending/` → generated bench cases → owner promotion with hash pin | `plugin/plugins/desktop/skills.py`, `learn.py`, `plugin/plugins/main_agent/episodes.py`, `jarvis/skills/skillgate.py`, `plugin/plugins/workbench/` | `m_skills.json`; L4 rollback |

### P4 — Research → plan → code, multi-agent tests, guarded push (items 6, 7, 15, 18)

| Work item | Files / places | Gate |
|---|---|---|
| Chain research output → main_agent plan → `dispatch_to_lab` | `plugin/plugins/research/`, `planner.py`, `jarvis/kernel/jarvis_kernel/llm_proxy.py`, `agents.py`, `projectfix.py` | End-to-end on an allowlisted project (`jarvis/config/projects.json`) |
| Coding CLIs: OpenCode primary over ACP, goose, Codex `exec` reviewer, Claude Code `--bare`; MCP-connected assertion; pinned versions, auto-update off; agy only wrapped (fail on `denied_actions` or empty response) | `jarvis/lab/lab_runner.py`, `jarvis/lab/jarvis_mcp.py` | Worker runs fail closed when MCP missing |
| Worktree per worker, read-only hashed tests, reviewer checks, new multi-agent bench area | `jarvis/evals/neko95/bench/cases/o_multiagent.json` *(proposed)*, `bench.py` adapters | Bench area green; held-out set unseen by agents |
| On-screen server runs: Xvfb + noVNC/KasmVNC, Playwright video/trace on failure | `jarvis/scripts/neko-test/Containerfile`, `sync-and-build.sh`, Benchmark Console `jarvis_console/bench.py` | Owner can replay a failed run |
| Guarded push-to-main via kernel identity; caretaker back on with a short-lived, scoped credential | `jarvis/kernel/jarvis_kernel/policy.py` (caretaker git/gh rules), `jarvis/caretaker/`, `idempotency.py` | Revert drill; daily cap; kill switch test |

### P5 — Presence: shell, voice, personality, explanation (items 3, 4, 12, 14)

| Work item | Files / places | Gate |
|---|---|---|
| Four Tauri patches as upstream PRs (single-instance first, tray, global-shortcut with re-bind, autostart, click-through fail-safe) | `jarvis/third_party/PATCHES.md`, `jarvis/third_party/neko-tauri` submodule, `jarvis/widget/` | 30-min Win10 transparency soak; X4 push-to-talk TESTED |
| Voice measurement first (200 owner clips, 1 h room audio); "hey jarvis" A/B; Smart Turn; AEC3 barge-in; sentence TTS; wake-to-first-audio | `jarvis/wake/wake_listener.py`, `eval_owner.py`, `eval_wake.py`, `wake_latency.py`, `jarvis/scripts/local_speech_server.py`; `config/voice_wake_word.py` (platform, escalation) | `g_voice.json`; V4 mic loop TESTED; ERLE and stop latency <300 ms |
| JARVIS character card via N.E.K.O card manager (data folder); anti-sycophancy cases; honest-narration contract | N.E.K.O data root; `jarvis/scripts/make_persona_english.py`; `l_ux.json` | Persona cases pass on every model change |

### P6 — Reach and parity (items 8, 9; completes 21)

| Work item | Files / places | Gate |
|---|---|---|
| Phone channel: re-enable Hermes Telegram with allowlist pairing, hash-bound approval inbox, morning brief | `jarvis/config/telegram.off` (owner decision), kernel approvals | Prompt-injection cases on inbound text |
| ChatGPT/Gemini: API second opinion (if paid approved); read-only JARVIS MCP server; deny automating chatgpt.com / gemini.google.com | `jarvis/config/providers.json`, `decide.py` SITES, `policy.py` | Labelled answers; MCP read-only verified |
| Wide research fan-out; teach-by-demo recorder emitting staged skills | `plugin/plugins/research/`, `plugin/plugins/main_agent/` | `h_research.json`, `m_skills.json` |
| Posture self-check (binds, auth, ports, skill hashes) | `jarvis/scripts/` *(proposed)* | Runs clean |

### P7 — Close out the explanation and the guide (items 16, 17)

| Work item | Files / places | Gate |
|---|---|---|
| ~19 missing docstrings; READMEs for kernel, node, console, lab, wake, widget, privacy, local_models | listed `__main__.py`, `jarvis/scripts/oci_cost.py`, `jarvis/bench/wakeword/score.py`, etc. | AST docstring scan = 100% of non-vendored files |
| "How JARVIS works" incl. a generated table of every model and provider | `jarvis/docs/02_ARCHITECTURE.md` (rewrite, no new docs/ file) | Owner review |
| Step-by-step usage guide: quick start, daily use, voice, approvals, stop, recovery | `jarvis/docs/05_OPERATIONS.md` (restructure) | Owner follows it end to end |

## Decisions only the owner can make

| # | Decision | Recommendation |
|---|---|---|
| 1 | Approve fast-forwarding `remediation-2026-10-18` into main and deploying the 10-22 kernel | Yes, after a full bench run on the merged tree |
| 2 | Oracle: resize to 2/12 at $0, or pay ~$25–28/mo to keep 4/24 (and check hermes-agent-vm-2's shape yourself) | Resize and move the VM model on-demand, unless you want openmuse + desktop + local model all resident |
| 3 | 9router: lock down as public-lane-only behind the gateway, or remove | Remove unless a specific free provider needs it; never with OAuth subscriptions or Hermes access |
| 4 | Is a PIN-gated admin allowlist (elevated broker) acceptable instead of raw admin? | Yes; raw admin is not recommended |
| 5 | Confirm the "main" reading: may a kernel gate fast-forward main for small, fully tested agent changes? | Yes, with caps, auto-revert and kill switch; all else via PR |
| 6 | Paid items, each separately: Gemini API key (private lane), OpenAI API key, $10 OpenRouter credit | Gemini paid only if a private-lane second model is needed; OpenRouter $10 is the cheapest uplift |
| 7 | Is your "Gemini" a consumer AI Pro/Ultra plan or a paid API key? | Consumer plans no longer work with Gemini CLI; do not route them through routers |
| 8 | openmuse with threads in CopilotKit cloud, or the fully self-hosted Vane + Open WebUI/LibreChat stack | openmuse for public-lane tasks only until #73/#179 land |
| 9 | Re-enable a phone channel (Telegram via Hermes) | Yes, allowlist-only, after P3's taint controls |
| 10 | Create-file/folder capability with kernel rule (roadmap decision #6) | Yes, as YELLOW_AUTO inside user roots with undo |
| 11 | Disk: free space for a deep MoE, and which one | gpt-oss-20b; prune superseded small models after A/B |

The open questions are these:

- **"Grokbot".** Does it mean xAI's Grok Bot product or just Grok?
- **"Clawdbot".** Is that the right reading of the handwriting?
- **Windows edition.** Windows Sandbox and Hyper-V need Pro or Enterprise.
- **Live infrastructure.** Do the Linux test server and the Smokescreen proxy still run?
- **Project JARVIS doc.** Where does it live?
- **Windows 11.** Would you consider upgrading? Windows 10 left mainstream support on 14 Oct 2025, and Microsoft's agent connectors exist only on Windows 11 ([Microsoft](https://www.microsoft.com/en-us/windows/end-of-support); [Windows Developer Blog](https://blogs.windows.com/windowsdeveloper/2025/11/18/ignite-2025-furthering-windows-as-the-premier-platform-for-developers-governed-by-security)).

## Conclusion

The research changes what "build JARVIS" means for this repo. The owner's list reads like twenty-one features. The evidence says JARVIS is mostly feature-complete in design and fragile in operation. Its single always-on host is now twice the free allowance. Its router layer carries critical CVEs aimed at the very agent it hosts. Its coding lab vanished with one disk. Its best work sits unmerged. The highest-return move is therefore not a new capability but making the existing ones survivable. That means one merged tree, one hardened router behind lanes the models cannot override, one agent computer with backups, and a kernel that is the only thing allowed to touch main or admin.

The products the owner wants to match point the same way. OpenClaw and Grok Bot win on reach: phones, channels, routines and always-on computers. They lose on exactly what JARVIS already has, which is tiers, PINs, audit and a kill switch. Reach should come from widening JARVIS's gated surface one phase at a time, never from importing their runtimes or marketplaces. Two numbers should anchor the owner's decisions. Halving Oracle costs nothing but forces the VM's local model off, and keeping 4/24 costs about $26 a month. Every other paid choice in this plan is smaller than that.
