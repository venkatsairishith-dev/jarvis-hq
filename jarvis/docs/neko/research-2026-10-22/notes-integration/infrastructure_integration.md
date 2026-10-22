# Integrating JARVIS's infrastructure components into the JARVIS codebase (routing, local model serving, computer/browser control, egress, agent desktop), as of 22 Oct 2026

Scope: research only. The owner requires every project JARVIS uses to be integrated into JARVIS's own codebase and product (Python 3.11, repo venkatsairishith-dev/jarvis-hq, base N.E.K.O), not kept as a separate app. These notes build on the earlier notes in `/home/user/research_notes/JARVIS research and build plan/`: routers_and_providers.md, local_model_roster.md, computer_and_browser_control.md, desktop_shell_and_agent_vm.md and repo_current_state.md. Facts carried over from those notes are marked "(prior note)" and keep their original source links.

Evidence labels: **[verified]** means read on a primary page in this session. **[secondary]** means a third-party, search-summary or mirror source. **[inference]** means my reasoning, not a sourced fact. "Integration forms" means one of four things: a Python library import, vendored and modified source, a port of features into JARVIS's Python code, or a binary bundled under JARVIS's supervisor.

---

## Q0. Objective summary: the best integration form for each component

### Takeaway
Only a few components can be imported directly into a Python 3.11 process. The official provider SDKs, any-llm, uiautomation/pywinauto, Playwright-Python, browser-use and proxy.py all can. Everything else is Go, TypeScript or C++, or needs Python 3.14 (Windows-MCP). For those, the choice is between porting features into JARVIS's Python gateway, node or egress module, and shipping a pinned binary that JARVIS's own supervisor launches, configures and health-checks.

The recommended split is below.
- **Port:** the router features (OmniRoute/9router), the llama-swap swap policy, the Smokescreen ACL logic, and the Playwright-MCP extension relay.
- **Bundle under the supervisor:** the llama-server binaries, plus the Selkies or KasmVNC display server on the VM.
- **Vendor or reimplement:** the Windows-MCP UIA tree code.
- **Embed:** noVNC's or Selkies' JavaScript client in the JARVIS UI.

### Cited Findings
- OmniRoute and 9router are Node/Next.js applications (9router is "Next.js", OmniRoute uses better-sqlite3/LowDB), so Python cannot import them (prior note) — [9router README](https://github.com/decolua/9router), [OmniRoute README](https://github.com/diegosouzapw/OmniRoute)
- llama-swap is Go, MIT, with the UI built in Node.js. The latest release is v265 (14 Oct). [verified] — [llama-swap repo](https://github.com/mostlygeek/llama-swap), [llama-swap releases](https://github.com/mostlygeek/llama-swap/releases)
- Smokescreen is Go and MIT. Its only documented embedding API is for Go (`cmd.NewConfiguration`, `smokescreen.StartWithConfig`). [verified] — [stripe/smokescreen](https://github.com/stripe/smokescreen)
- Windows-MCP 0.8.8 (released 2026-10-10) requires **Python >=3.14** and is MIT. [verified] — [PyPI windows-mcp JSON](https://pypi.org/pypi/windows-mcp/json)
- browser-use 0.13.11 requires Python `<4.0,>=3.11` and is MIT. [verified] — [PyPI browser-use JSON](https://pypi.org/pypi/browser-use/json)
- any-llm (Mozilla.ai) is Apache-2.0, requires Python 3.11+, and uses official provider SDKs. [verified] — [mozilla-ai/any-llm](https://github.com/mozilla-ai/any-llm)
- Selkies is MPL-2.0 and installs with `pip install selkies` (Python 3.9+). [verified] — [selkies-project/selkies](https://github.com/selkies-project/selkies)
- noVNC (`@novnc/novnc` 1.7.0) is MPL-2.0 and exports `./core/rfb.js`. [verified] — [npm registry @novnc/novnc](https://registry.npmjs.org/@novnc/novnc/latest)

### Inferences
**Integration matrix [inference, built from the findings in Q1–Q8]**

| Component | Upstream language / license | Recommended form inside JARVIS | Where in JARVIS (proposed) |
|---|---|---|---|
| OmniRoute / 9router routing features | TS/Node, MIT | **Port the features** into the Python kernel gateway. Retire the OmniRoute VM trial. | `jarvis/kernel/jarvis_kernel/router/` (combos, keypool, quota, breaker, cache, translate) behind `llm_proxy.py` |
| Format translation (OpenAI Chat ↔ Responses ↔ Anthropic Messages ↔ Gemini) | — | **Write it in-house** on the official `openai` / `anthropic` / `google-genai` SDK types. Optionally import any-llm (Apache-2.0) for upstream calls. Use LiteLLM only as a read-only reference, or pinned at ≤1.106.2. | `router/translate/*.py` |
| llama.cpp inference | C++, MIT | **Bundle `llama-server` binaries** for each GPU profile under the JARVIS supervisor. Do **not** use llama-cpp-python in-process. | `jarvis/local_models/bin/<profile>/`, `supervisor.py` |
| llama-swap | Go, MIT | **Port the swap policy** (groups/exclusive/persistent, TTL, preload, matrix with evict costs) to a Python supervisor that spawns one llama-server per model. Alternatively use llama-server router mode for simple cases. | `jarvis/local_models/swapper.py` |
| Windows-MCP | Python 3.14, MIT | **Vendor selected UIA/tree code** (MIT notice kept), backported to 3.11 with posthog and fastmcp removed. Or reimplement on `uiautomation` / `pywinauto`. | `jarvis/node/jarvis_node/winbackend.py` + `vendor/windows_mcp_tree/` |
| pywinauto / uiautomation | Python, BSD-3 / Apache-2.0 | **Import as libraries** | node |
| UFO² | Python, MIT | **Port the patterns** (puppeteer / app_apis "API before GUI"). Vendor a module only where useful. | node `apis/` |
| Everything / Windows Search | C DLL + ES (MIT) / OS OLE DB | **ctypes wrapper** over Everything64.dll (user installs the Everything client). Windows Search through pywin32 ADODB. | node `filesearch.py` |
| browser-use | Python, MIT | **Import as a library** in the research lane only, pinned and with telemetry off. Or borrow its CDP/DOM code. | `jarvis/node/jarvis_node/browseragent.py` |
| Playwright MCP extension mode | TS, Apache-2.0 | **Port the CDP relay to Python** and drive it with Playwright-Python `connect_over_cdp`, keeping Microsoft's bridge extension. Alternatively use the Chrome `chrome://inspect` toggle. | `browserext.py` |
| Smokescreen | Go, MIT | **Port the ACL and SSRF logic** into an asyncio CONNECT proxy, either hand-written or as a proxy.py plugin. Keep Smokescreen as a bundled fallback binary. | `jarvis/egress/proxy.py` |
| Agent desktop | Selkies MPL-2.0 (Python); KasmVNC GPL-2.0; webtop GPL-3.0 (Dockerfiles); noVNC MPL-2.0 | **Selkies via pip under the JARVIS supervisor on the VM**, with its HTML5 client embedded in the JARVIS UI. If VNC is chosen, embed the noVNC `RFB` client. Run KasmVNC only as an unmodified separate binary. Do not copy webtop. | `jarvis/vm/desktop.py`, UI `AgentDesktop` panel |

### Gaps
- Nobody has measured how many lines each port would take. The sizes in later sections are estimates.

---

## Q1. Routing: which OmniRoute/9router features matter, and can JARVIS's Python gateway implement them (port vs embed)?

### Takeaway
The features worth having are these:
- per-lane combos with ordered fallback
- a circuit breaker with cooldown (JARVIS already has a 60 s cooldown)
- quota and rate-limit tracking from response headers
- per-key and per-lane USD/token budgets
- multiple keys per provider (only where each key is legitimately the owner's own)
- format translation for coding CLIs
- an optional, lane-scoped exact or semantic cache

The JARVIS gateway already enforces lanes. OmniRoute and 9router are Node apps with poor 2026 security records, so **porting** these features into `jarvis_kernel` (Python) beats embedding a router. Leave out the subscription-OAuth providers, multi-account farming and the prompt-altering "token savers"; they are ToS or privacy hazards.

For translation, build on the official SDK types. LiteLLM's translation modules are a useful *reference*, but the package was compromised on 2026-10-21 (v1.107.0) and its Anthropic→OpenAI bridge has open bugs. any-llm (Apache-2.0, Python 3.11+) is the cleanest importable multi-provider client if a library is wanted.

### Cited Findings
**Features in the routers (prior note)**
- **9router:**
  - combos (subscription → cheap → free) with switching on quota or error
  - multi-account per provider with round-robin or priority, and fallback to the next account at quota
  - RTK token saver, on by default
  - real-time quota tracking and format translation
  - insecure defaults and many 2026 CVEs, including CVE-2026-108593, an RCE through the Hermes-settings endpoint
  - [9router README](https://github.com/decolua/9router); [VulnCheck](https://www.vulncheck.com/advisories/9router-0.4.1-through-0.5.99-config-injection-rce-via-hermes-settings-endpoint)
- **OmniRoute:**
  - 20 routing strategies plus "auto" combos
  - fallback across tiers with circuit breakers and cooldowns
  - per-key USD spend quotas with cost headers
  - opt-in PII masking and token compression
  - an MCP server with 110 tools
  - critical unauthenticated RCE GHSA-hf57-cqmx-p4gr (CVE-2026-88062)
  - [OmniRoute README](https://github.com/diegosouzapw/OmniRoute); [GHSA-hf57-cqmx-p4gr](https://github.com/advisories/GHSA-hf57-cqmx-p4gr)
- OmniRoute in JARVIS today is "installed on the VM as a trial but routes nothing". The internal audit verdict on `gateway.py` is KEEP ("None enforces lanes a model cannot override plus the free-model guard") (prior note) — [R 02_ARCHITECTURE.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/02_ARCHITECTURE.md); [R 01_RESEARCH_AUDIT.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/01_RESEARCH_AUDIT.md)
- What JARVIS already has (prior note):
  - `gateway.plan(providers, task_lane, purpose=None, prefer_local=True)`, `probe`, `call`
  - a `lane_prefs` table, `preferred(lane)`, `lane_wait(lane)`, and the rule "Lane comes from the token, never from the request"
  - the privacy gateway's ordered fallback with `cooldown_seconds 60`, and Presidio + spaCy redaction with placeholder restore in streaming
  - [R gateway.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/gateway.py); [R llm_proxy.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/llm_proxy.py); [R privacy/gateway.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/privacy/gateway.py)
- Rate-limit signals are available in headers. For example, OpenRouter returns `X-RateLimit-*` on 429, and its `:free` tier is 20 RPM and 50 or 1,000 RPD (prior note) — [OpenRouter limits](https://openrouter.ai/docs/api-reference/limits)
- ToS: subscription-OAuth proxying (Claude Code, Antigravity, Codex) is banned or enforced against. Antigravity bans also cut off Gemini CLI (prior note) — [gemini-cli Discussion #20632](https://github.com/google-gemini/gemini-cli/discussions/20632)

**LiteLLM (reference or pinned dependency only)**
- **Compromise.** The 1.107.0 wheels (including aarch64) and the GHCR image shipped a credential stealer (`cook.pth` + `sitecustomize.py`) on 2026-10-21. Upwind recommends pinning **1.106.2** (prior note). There was an earlier compromise in March 2026 (1.82.7/1.82.8). — [Socket.dev](https://socket.dev/blog/litellm-compromise); [LiteLLM security update](https://docs.litellm.ai/blog/security-update-march-2026)
- **Translation code locations.**
  - `/v1/messages` requests aimed at OpenAI or Azure models are internally routed through the **Responses API**.
  - The conversion lives in `litellm/llms/anthropic/experimental_pass_through/responses_adapters/transformation.py`.
  - An older Chat-Completions path lives in `adapters/transformation.py` + `adapters/handler.py`, and its streaming wrapper converts OpenAI chunks into Anthropic stream events.
  - Mapping example: `max_tokens` → `max_output_tokens`, and `top_k` is silently dropped.
  - [secondary: LiteLLM docs] — [Messages→Responses mapping](https://docs.litellm.ai/docs/anthropic_unified/messages_to_responses_mapping); [creating adapters](https://docs.litellm.ai/docs/extras/creating_adapters.md)
- **Known bugs.** Issue #23841 says content blocks typed `"input_text"`, which Claude Code sends, are dropped by the transformation and cause a 422 downstream. The env var `LITELLM_USE_CHAT_COMPLETIONS_URL_FOR_ANTHROPIC_MESSAGES` is also not respected. [secondary: issue title and summary] — [BerriAI/litellm #23841](https://github.com/BerriAI/litellm/issues/23841)

**any-llm (Mozilla.ai)**
- Apache-2.0 and Python 3.11+. It offers `completion` and `AnyLLM.create()`, plus `responses` / `aresponses` "for providers that implement the OpenAI-style Responses API". It uses official provider SDKs where available, and its responses are OpenAI-compatible. Budget, key management, analytics and multi-tenancy have moved to a separate project, "Otari". Migrating from LiteLLM means changing model strings, e.g. `openai/gpt-4o` → `openai:gpt-4o`. [verified] — [mozilla-ai/any-llm](https://github.com/mozilla-ai/any-llm)
- The Mozilla.ai docs index lists a "Messages" page described as "Anthropic Messages API for all providers" and a "Responses" (OpenResponses) page. [secondary: docs index only; pages not opened] — [Mozilla.ai docs llms.txt](https://docs.mozilla.ai/llms.txt)

**Other gateways, ideas only (prior note)**
- Bifrost (Go) offers virtual keys, hierarchical budgets and rate limits. Portkey's OSS gateway is MIT, but budgets are in its hosted tier. [secondary] — [MarkTechPost](https://www.marktechpost.com/2026/10/14/best-ai-gateways-2026/); [cloudzero](https://www.cloudzero.com/blog/llm-gateways/)

### Inferences
- **Port, don't embed.** Each feature maps to a small Python module next to `gateway.py` and `llm_proxy.py`. These names are proposals [inference]:
  - **`router/combos.py`.** Named combos per lane, e.g. `private-default: [groq, openrouter-private, local-llama-vulkan]` and `public-default: [dahl-minimax, dahl-glm, …]`, loaded from `providers.json`. `plan()` already does ordered fallback, so this mostly *formalises* existing behaviour. Add strategies: priority (current), round-robin, least-recent-429, cheapest-first, and fastest-p50 (tracked latency).
  - **`router/keypool.py`.** N keys per provider entry, as `key_files: [...]`, selected round-robin or by remaining quota. Restrict this to keys the owner legitimately holds (e.g. separate org or project keys). Do **not** build account farming [inference from the ToS findings].
  - **`router/quota.py`.** Parse `x-ratelimit-remaining-*` / `retry-after` / `X-RateLimit-*` per (provider, key). Persist in SQLite next to `lane_prefs`. Feed `lane_wait()`, which already shows "back at HH:MM".
  - **`router/breaker.py`.** A per-(provider, model) state machine: closed → open on N consecutive 5xx/timeout/429 → half-open probe after the cooldown. Generalise the privacy gateway's 60 s cooldown into exponential backoff. This is ~100 lines and needs no library (`aiobreaker`/`purgatory` exist but are optional) [inference; libraries not verified this session].
  - **`router/budget.py`.** Per-key and per-lane token and USD ledgers, with hard stops. This matches the policy "paid must stay false" and Dahl's "never top up".
  - **`router/cache.py`.**
    - Start with an exact-match cache keyed on (lane, model, normalized redacted request), stored in SQLite with TTL.
    - Make a semantic cache (Qwen3-Embedding-0.6B, already on the roster, plus sqlite-vec) opt-in, **per lane and never shared across lanes**, and skip it for tool-calling turns.
    - Never cache private-lane responses containing restored placeholders [inference].
  - **Token savers (RTK/Headroom).** If wanted, implement them as an explicit, logged transform of *tool output* that runs **after** redaction. Never apply them silently. Do not port 9router's "Caveman"/"Ponytail" prompt injection.
- **Translation layer.** A translation core needs these pieces:
  1. Pydantic request/response models taken from the official SDKs:
     - `openai.types.chat.*`
     - `openai.types.responses.*`
     - `anthropic.types.*` (MessageParam, ContentBlock, RawMessageStreamEvent)
     - `google.genai.types` (Content, Part, GenerateContentResponse)
  2. Converters to and from one internal canonical form (messages, tool calls, tool results, images, reasoning).
  3. Streaming re-encoders for each format:
     - OpenAI chat SSE chunks
     - Responses events (`response.output_text.delta` etc.)
     - Anthropic events (`message_start` … `message_delta`, `message_stop`, `ping`)

  This lets the existing Presidio redaction run on one canonical form before any upstream call [inference]. LiteLLM's adapters (pinned ≤1.106.2, read-only) and any-llm's Messages/Responses code are references for edge cases.
- **Importing any-llm** is reasonable for the upstream call (provider SDK calls, `aresponses`). JARVIS's lane/redaction/budget logic stays in front of it. Do not import LiteLLM into the kernel process: the October payload runs at interpreter start through `.pth`, so any compromised install exposes every kernel secret.
- **What to retire.** The OmniRoute VM service (`jarvis/deploy/jarvis-omniroute.service`) can be removed once the ported features ship. This also closes the OmniRoute RCE surface.

### Gaps
- I did not verify any-llm's current version, or whether its Messages API supports streaming tool use for all providers. The docs pages behind the index were not opened.
- I did not open LiteLLM's MIT vs `enterprise/` license split this session. My background knowledge is that the core is MIT and `enterprise/` is commercial; check before copying code.
- I found no Python circuit-breaker or semantic-cache library vetted for 2026 (GPTCache/redisvl status unverified).

---

## Q2. How do coding CLIs connect to an endpoint inside JARVIS (OpenAI Chat, Responses API, Anthropic Messages, Gemini)?

### Takeaway
To serve every coding CLI, the JARVIS gateway (e.g. a new listener next to the privacy gateway on 48950) must expose four API surfaces:
1. `/v1/chat/completions` for OpenCode, Aider and goose.
2. `/v1/responses` for Codex CLI. Codex removed `wire_api="chat"` in Feb 2026.
3. `/v1/messages` + `/v1/messages/count_tokens` + `/v1/models` for Claude Code.
4. Gemini's `generateContent` paths for Gemini CLI via `GOOGLE_GEMINI_BASE_URL`.

Anthropic's own gateway guide is strict:
- Forward `anthropic-beta` / `anthropic-version` and body fields unchanged.
- Do not buffer SSE, and keep `ping` events.
- Rewriting or redacting request bodies "breaks the pairing".
- Anthropic "doesn't support routing Claude Code to non-Claude models through any gateway".

So Claude Code through JARVIS should be a **pass-through to Anthropic** (key-holding, audit, budget), not a redacting translator. Local llama-server already speaks `/v1/messages` and `/v1/responses` natively.

### Cited Findings
**Claude Code (official docs)**
- **Formats and endpoints.** "A gateway must expose at least one of the following API formats". The Anthropic Messages format is selected by `ANTHROPIC_BASE_URL` and uses endpoints `/v1/messages` and `/v1/messages/count_tokens` (optional). Forward "`anthropic-beta` and `anthropic-version` request headers" unchanged. Inference posts to `/v1/messages?beta=true`, and there is a `HEAD /api/hello` warm-up probe. [verified] — [Claude Code gateway compatibility guide](https://code.claude.com/docs/en/llm-gateway-protocol)
- **Streaming.**
  - "If your gateway buffers a response until it is complete, Claude Code stalls."
  - It needs the full event sequence through `message_delta` and `message_stop`.
  - A gateway that strips `ping` events can trip the idle watchdog.
  - Return `retry-after` as integer seconds, and pass `x-should-retry` and `anthropic-ratelimit-unified-*` through.
  - [verified] — [same page](https://code.claude.com/docs/en/llm-gateway-protocol)
- **Redaction conflict.**
  - "A gateway that rewrites or redacts request bodies for content inspection breaks the pairing the same way stripping does, so inspect without modifying."
  - "Forward the `system` array exactly as received". If the gateway must reshape it, set `CLAUDE_CODE_ATTRIBUTION_HEADER=0`.
  - `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` reduces the beta/body pairs.
  - [verified] — [same page](https://code.claude.com/docs/en/llm-gateway-protocol)
- **Model discovery.** `GET /v1/models?limit=1000` runs only with `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`, and only IDs containing `claude` or `anthropic` are kept. Routing hint headers such as `x-claude-code-request-class` and `x-claude-code-session-id` can be used for routing and attribution. [verified] — [same page](https://code.claude.com/docs/en/llm-gateway-protocol)
- **Non-Claude models and subscriptions.** "Anthropic doesn't endorse … third-party gateway products, and doesn't support routing Claude Code to non-Claude models through any gateway." With a gateway credential or `apiKeyHelper`, requests carry that credential instead of the claude.ai subscription and are billed per token. `allowedProviders: ["customEndpoint"]` can pin Claude Code to the gateway (v2.1.285+). [verified] — [Claude Code: Other LLM gateways](https://code.claude.com/docs/en/llm-gateway)

**Codex CLI**
- `wire_api="chat"` was deprecated and became a hard error in Feb 2026. `wire_api="responses"` is the only working value, and a gateway without `/v1/responses` returns 404s. `model_providers` takes effect only in the user-level `~/.codex/config.toml`. The ids `openai`, `ollama` and `lmstudio` are reserved. [secondary: blogs and a Jan GitHub issue; sources conflict on whether omitting `wire_api` defaults to responses] — [janhq/jan #7413](https://github.com/janhq/jan/issues/7413); [danielvaughan guide](https://codex.danielvaughan.com/2026/04/23/codex-cli-custom-model-providers-configuration-guide/); [OpenRouter Codex tutorial](https://openrouter.ai/blog/tutorials/codex-cli-openrouter/)

**Other CLIs (prior note)**
- Gemini CLI uses `GOOGLE_GEMINI_BASE_URL` + `GEMINI_API_KEY` [vendor blog]. OpenCode uses `@ai-sdk/openai-compatible` with `baseURL`. goose uses `OPENAI_HOST` + `OPENAI_BASE_PATH`. Aider uses `OPENAI_API_BASE` with an `openai/` model prefix. — [getmaxim.ai](https://www.getmaxim.ai/articles/how-to-route-gemini-cli-and-codex-through-an-ai-gateway/); [OpenCode providers](https://opencode.ai/en/docs/providers); [Zuplo goose](https://zuplo.com/docs/ai-gateway/integrations/goose.md); [Aider](https://aider.chat/docs/llms/openai-compat.html)

**Local backend**
- llama-server offers "Anthropic Messages API compatible chat completions" at `POST /v1/messages` and `POST /v1/messages/count_tokens`, plus `POST /v1/responses` ("OpenAI-compatible Responses API", implemented by converting Responses requests into Chat Completions). [verified] — [llama.cpp server README](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md)

### Inferences
- **Design: one "CLI front door" in the JARVIS gateway with per-CLI tokens** [inference]:
  - **Claude Code** gets `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>` + `ANTHROPIC_AUTH_TOKEN=<jarvis token bound to lane>`. The JARVIS handler for `/v1/messages`:
    - (a) passes the request through to Anthropic's API byte-for-byte, holding the owner's API key, with budget and audit, if an Anthropic provider exists. Today none exists ("paid must stay false"), which is a policy decision for the owner.
    - (b) for **local models**, proxies to llama-server's own `/v1/messages`. This is unsupported by Anthropic for Claude Code, but works for experiments.
    - The handler must not run Presidio rewrites on Claude Code bodies.
    - Instead, make lane choice explicit: Claude Code on real repos uses a private-lane upstream or local only.
  - **Codex** gets `[model_providers.jarvis] base_url="http://127.0.0.1:<port>/v1" wire_api="responses" env_key="JARVIS_CODEX_TOKEN" requires_openai_auth=false`. The JARVIS `/v1/responses` handler translates to the lane's upstream: chat for Groq/Dahl/OpenRouter, or Responses passthrough for llama-server.
  - **OpenCode, Aider and goose** use `/v1/chat/completions`, which is the format the privacy gateway already handles.
  - **Gemini CLI**: implement `POST /v1beta/models/{model}:generateContent` and `:streamGenerateContent` (path shapes from my background knowledge of the Gemini REST API, not verified this session), translated to the lane upstream. Or skip Gemini CLI. Its free personal tier ended 2026-06-18 (prior note).
- Lane comes from the token (existing rule), so each CLI token is minted for exactly one lane.

### Gaps
- I did not fetch OpenAI's official Codex config docs. The Codex facts are from secondary guides.
- I did not verify the Gemini CLI's exact request paths against a custom base URL, or the Antigravity CLI (`agy`) custom-endpoint support.
- I did not test whether llama-server's `/v1/messages` handles Claude Code's tool-search/beta fields. It probably ignores them [inference].

---

## Q3. Local models: llama-cpp-python vs a bundled llama-server binary, llama-swap vs a Python swapper vs llama-server router mode, AMD Vulkan on Windows, and shipping binaries per GPU profile

### Takeaway
Ship **llama-server binaries** (official release zips) per GPU profile under a JARVIS Python supervisor, and do not use llama-cpp-python:
- llama-cpp-python lags upstream (0.3.36).
- Its wheels come from a side index, not PyPI.
- In-process inference would tie GPU crashes and VRAM release to the JARVIS process.
- It cannot pin a different build per model, which is needed because of the RDNA1 regression #30039.

llama-server now has a native **router mode**:
- `--models-dir`, `--models-preset` INI, `--models-max` (default 4)
- `--no-models-autoload`
- `POST /models/load`, `POST /models/unload`, `GET /models/sse`
- `--sleep-idle-seconds`

That covers TTL and basic multi-model serving, but not llama-swap's groups, exclusive, persistent, matrix or evict costs, and not per-model binaries. The cleanest fully integrated design is a **Python port of llama-swap's policy** (`swapper.py`) that spawns one llama-server per model, each with its pinned build, and reverse-proxies requests.

### Cited Findings
**llama-server router mode**
- **Starting it.** "To start in router mode, launch `llama-server` **without specifying any model**". Models come from the cache (`LLAMA_CACHE`), `--models-dir`, or `--models-preset` (INI). In the INI, each section is a preset, keys are CLI args without dashes, and `[*]` holds global defaults. Preset-only keys are `load-on-startup`, `stop-timeout` (default 10 s) and `dedup-cache-models`. [verified] — [llama.cpp server README](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md)
- **Endpoints.**
  - `GET /models` (`?reload=1`)
  - `POST /models/load {"model": …}` and `POST /models/unload {"model": …}`
  - `GET /models/sse` (status events)
  - `POST /models` (download) and `DELETE /models?model=`
  - Requests are routed by the `"model"` field. "Model instances inherit both command line arguments and environment variables from the router server."
  - [verified] — [same](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md)
- **Flags.**
  - `--models-max N` ("maximum number of models to load simultaneously (default: 4, 0 = unlimited)")
  - `--models-autoload` / `--no-models-autoload`
  - `--sleep-idle-seconds` ("the model and its associated memory (including the KV cache) are unloaded")
  - `/metrics` needs `?model=` in router mode
  - [verified] — [server README (GitHub view)](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- A blog says there is no single "unload all" endpoint and that LRU eviction occurs at the limit. [secondary; the README section seen does not describe the eviction policy] — [Medium: unload all router models](https://medium.com/@rosgluk/unload-all-llama-cpp-router-models-without-restarting-ae44fa14fd6f)

**llama-swap**
- MIT and Go (UI needs Node.js to build).
- Endpoints:
  - `POST /api/models/unload` (all) and `POST /api/models/unload/:model_id`
  - `/upstream/:model_id`, `/running`, `/logs`
- It has matrix, TTL, hook preload and API keys. Release binaries exist for Linux, Mac, Windows and FreeBSD.
- [verified] — [llama-swap repo](https://github.com/mostlygeek/llama-swap)
- Latest is **v265 (14 Oct)**: sub-path serving and a `models.capabilities.voices` setting. v264 added the matrix synthetic set `auto:orphans` for always-loaded models. v263 added a per-model `disableKeepAlives`. v258 added peers via Tailcat, where an empty `tailcat.allow` denies all. [verified] — [llama-swap releases](https://github.com/mostlygeek/llama-swap/releases)
- Group keys `swap`/`exclusive`/`persistent`/`members`, matrix `vars`/`sets`/`evict_costs`, `ttl`, `globalTTL` and `hooks.on_startup.preload` were documented in `docs/config.example.yaml` (prior note) — [config.example.yaml](https://raw.githubusercontent.com/mostlygeek/llama-swap/main/docs/config.example.yaml)
- JARVIS currently runs llama-swap at `127.0.0.1:8080/v1` with a 5-minute idle unload and profiles cpu8/cpu16/vram8plus. A guard test requires every llama-server to bind 127.0.0.1 with `--no-webui` and no `--tools/--agent/--mcp-servers-config` (prior note) — [R local_team README](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/plugin/plugins/local_team/README.md); [C 3c903413](https://github.com/venkatsairishith-dev/jarvis-hq/commit/3c903413e090052dc0e457f44b23b0dbf6aca689)

**llama-cpp-python**
- Latest version 0.3.36, MIT, `requires_python >=3.8`. PyPI files seen are sdists. The README says prebuilt wheels come from `abetlen.github.io/llama-cpp-python/whl/...` and recommends installing from source. [verified, partial page] — [PyPI llama-cpp-python JSON](https://pypi.org/pypi/llama-cpp-python/json)
- A release tracker lists per-backend tags such as `v0.3.34-vulkan`. Third-party Vulkan wheel collections are stale (0.3.16). [secondary] — [releasealert](https://releasealert.dev/github/abetlen/llama-cpp-python); [AIencoder wheels](https://huggingface.co/datasets/AIencoder/llama-cpp-wheels/commit/d86bde101edecf489a5c7cfd90f2c5438041db77)

**Official binaries per GPU profile (build b11669, the owner's build)**
- Windows assets:
  - `llama-b11669-bin-win-vulkan-x64.zip`
  - `llama-b11669-bin-win-cpu-x64.zip`
  - `llama-b11669-bin-win-cuda-12.4-x64.zip` / `-cuda-13.4-x64.zip` (+ `cudart-llama-bin-win-cuda-*.zip`)
  - `llama-b11669-bin-win-rocm-10.0-x64.zip`
  - `llama-b11669-bin-win-sycl-x64.zip`
  - `llama-b11669-bin-win-openvino-2026.4.1-x64.zip`
  - arm64 variants
- Linux assets:
  - `llama-b11669-bin-ubuntu-arm64.tar.gz` (for the Oracle A1 VM)
  - `llama-b11669-bin-ubuntu-vulkan-arm64.tar.gz` / `-vulkan-x64`
  - `-rocm-10.0-x64`, cuda, sycl
  - `llama-b11669-ui.tar.gz`
- [verified] — [llama.cpp b11669 assets](https://github.com/ggml-org/llama.cpp/releases/expanded_assets/b11669)
- The GitHub "latest release" page labels the release "v0.7.0 (nightly build b11669)", dated 15 Oct. [verified but odd; I could not confirm whether llama.cpp moved to semver tags] — [llama.cpp latest release](https://github.com/ggml-org/llama.cpp/releases/latest)
- **RDNA1 regression.** Issue #30039 is an open Windows/AMD Vulkan prompt-processing regression (−41–54% on Qwen3.5 GDN / Qwen3.6 MoE) that began between b10950 and b11010. The owner's b11669/b11714 builds are after it (prior note) — [llama.cpp #30039](https://github.com/ggml-org/llama.cpp/issues/30039)

### Inferences
- **Recommended form: bundled binaries plus a Python swapper (port of llama-swap)** [inference]:
  - **`jarvis/local_models/bin/`**
    - One directory per *build × backend*, e.g. `b10940-win-vulkan-x64/` (pre-regression, for Qwen3.5 GDN models), `b11714-win-vulkan-x64/`, `b11714-win-cpu-x64/`, `b11714-ubuntu-arm64/` (VM).
    - A manifest `binaries.json` with SHA-256 of each zip, verified at install, since there is no signed upstream channel.
    - Profiles (`cpu8`, `cpu16`, `vram8plus`, plus a new `rdna1-4gb`) choose the backend.
    - Detect the GPU at install through `vulkaninfo --summary` or DXGI adapter enumeration [inference].
  - **`jarvis/local_models/swapper.py`** (≈400–700 lines of asyncio):
    - Reads the existing `llama-swap.yaml`, keeping the format so profiles migrate unchanged.
    - Implements `models[*].cmd/ttl/aliases/env/concurrencyLimit` and `groups{swap, exclusive, persistent, members}`.
    - Also implements `hooks.on_startup.preload`, health checks via `/health`, and optionally matrix `sets` + `evict_costs`.
    - Exposes `/v1/*` reverse proxy plus `/running` and `/unload`.
    - Runs under the JARVIS supervisor, so its logs and audit go into JARVIS.
    - Enforces the existing guard: 127.0.0.1, `--no-webui`, no `--tools/--agent/--mcp-servers-config`.
  - **Use llama-server router mode** only where one build suffices (e.g. the Oracle VM's single CPU model). There it replaces both llama-swap and the swapper: `--models-preset` + `--models-max 1` + `--sleep-idle-seconds 300`. Router mode can't pin a per-model binary, because instances inherit the router's args and env, and it has no "exclusive group" semantics.
  - **Keep upstream llama-swap only as a fallback.** It is MIT, so vendoring the Go source under `third_party/` and building it is legal. But that is not "Python-integrated", and it needs a Go toolchain.
  - **Avoid llama-cpp-python** except possibly for tiny CPU-only helpers. It has a slower update cadence, a side-index wheels story, and in-process crash risk, and it cannot hot-swap builds.

### Gaps
- I did not verify whether router-mode child instances are separate processes or on internal ports, nor its eviction policy (LRU is claimed only by a blog).
- I did not check whether `--sleep-idle-seconds` can be set per preset (i.e. per-model TTL).
- No fix or status update for #30039 was checked this session. AMD's Windows ROCm 10.0 build (`win-rocm-10.0-x64`) support for RDNA1 gfx1010 is unverified, and is likely unsupported [inference].
- llama-swap v265 asset filenames (Windows amd64, linux arm64) were not listed on the page fetched.

---

## Q4. Computer control on Windows: Windows-MCP (import vs MCP server), pywinauto/uiautomation, Microsoft UFO², Everything and Windows Search

### Takeaway
Windows-MCP can't be imported as-is:
- 0.8.8 requires **Python 3.14**, while JARVIS uses 3.11.
- It depends on `fastmcp>=3.0` and `posthog`, a telemetry SDK.
- It exposes raw PowerShell, Registry and Process tools.

Treat it as **MIT source to vendor selectively** (its UIA tree-snapshot and element-ID logic), backported to 3.11, or reimplement the same tree walk on **`uiautomation`** (Apache-2.0, which Windows-MCP credits). Use **pywinauto** (BSD-3; 1.0.0b1 beta out 2026-10-15) for app-level helpers.

From UFO² (MIT), port the *pattern* (puppeteer + app_apis: use an API/COM route before GUI), not the framework. Everything search integrates as a ctypes wrapper over the SDK DLL, which needs the Everything client running. Windows Search uses OLE DB through pywin32.

### Cited Findings
- **Windows-MCP 0.8.8 metadata** [verified] — [PyPI windows-mcp JSON](https://pypi.org/pypi/windows-mcp/json):
  - Released 2026-10-10, `requires_python >=3.14`, MIT ("Copyright (c) 2025 Jeomon George").
  - Dependencies: click, **comtypes**, **dxcam**, **fastmcp>=3.0**, fuzzywuzzy, markdownify, pillow, platformdirs, **posthog>=7.4.0**, psutil, pygments, python-levenshtein, **pywin32>=311**, requests, tabulate, thefuzz, uuid7.
  - The README badge says 3.13+, which conflicts with the metadata.
- **Windows-MCP README** [verified] — [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP):
  - The package is under `src/windows_mcp`, with a policy table at `src/windows_mcp/tools/control_policy.py`.
  - It is documented only as a server (`windows-mcp serve`, stdio/SSE/streamable HTTP). There is no documented Python library API.
  - It credits "Python-UIAutomation-for-Windows" (the `uiautomation` package).
- The tool set includes PowerShell, FileSystem, Process and Registry with "full system access". GHSA-vrxg-gm77-7q5g was an unauthenticated PowerShell exposure in HTTP modes, fixed in 0.7.5 (prior note) — [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP); [releasealert](https://releasealert.dev/github/CursorTouch/Windows-MCP/cves)
- **uiautomation**: 2.0.29 (2025-08-05), Apache-2.0, depends only on `comtypes>=1.2.1`. Classifiers list Python 3.4–3.12. [verified] — [PyPI uiautomation JSON](https://pypi.org/pypi/uiautomation/json)
- **pywinauto**: latest stable 0.6.9 (2025-01-06). **1.0.0b1 pre-release uploaded 2026-10-15**. BSD-3, depends on comtypes and pywin32. [verified] — [PyPI pywinauto JSON](https://pypi.org/pypi/pywinauto/json)
- **UFO²** [verified] — [microsoft/UFO ufo/automator](https://github.com/microsoft/UFO/tree/main/ufo/automator); [microsoft/UFO](https://github.com/microsoft/UFO):
  - `ufo/automator/` contains `ui_control/`, `app_apis/`, `puppeteer.py`, `action_execution.py`, `basic.py` and `path_validator.py`. Module roles were not described on the page.
  - UFO² is MIT and LTS, a hybrid of "GUI clicks + API calls".
- **Everything SDK from Python** [verified] — [voidtools SDK Python](https://www.voidtools.com/support/everything/sdk/python/):
  - Load the DLL with `ctypes.WinDLL`, then call `Everything_SetSearchW`, `Everything_SetRequestFlags`, `Everything_QueryW`, `Everything_GetNumResults` and `Everything_GetResultFullPathNameW` / `GetResultSize` / `GetResultDateModified`.
  - "requires Everything running in the background".
  - The page's sample loads `Everything32.dll`, and no redistribution terms are stated there.
- ES (es.exe) is MIT and offers `-json`, `/ad`, `-n`, `-sort`. The SDK is IPC over WM_COPYDATA (prior note) — [voidtools/ES](https://github.com/voidtools/ES); [voidtools SDK](https://voidtools.com/support/everything/sdk)
- Windows Search SQL works on all Windows 10 versions via OLE DB (prior note) — [MS Learn: Windows Search SQL](https://learn.microsoft.com/en-us/windows/win32/search/-search-sql-windowssearch-entry)

### Inferences
- **Windows-MCP: vendor, don't run** [inference]:
  - Copy only the UIA tree/snapshot code (the part that builds "Snapshot (full UI state with element IDs)") and the input helpers. Put them in `jarvis/node/jarvis_node/vendor/windows_mcp/` with the MIT LICENSE and a `VENDORED.md` recording the upstream commit.
  - Strip `fastmcp`, `posthog`, PowerShell, Registry and Process.
  - Backport 3.12–3.14-only syntax (e.g. PEP 695 `type` aliases, new generic syntax, and any `match`/typing features newer than 3.11). Which specific features it uses was **not checked**.
  - Map its tools onto JARVIS's existing `winbackend.py` / `windows.py` and the GREEN/PIN/RED/DENY kernel gate.
  - Alternative with less maintenance debt: reimplement the snapshot on `uiautomation` (`uiautomation.GetRootControl()`, `WalkControl`, `Control.BoundingRectangle`, `GetInvokePattern`, `GetValuePattern`; API names from background knowledge).
  - `dxcam` (DXGI screen capture) is worth adopting as a library for fast screenshots.
- **UFO²**:
  - Port the `puppeteer.py` / `app_apis/` idea into a node `apis/` registry: Office via COM (`win32com.client.Dispatch("Excel.Application")`), Explorer via Shell COM, winget via the elevated broker.
  - The UIA path is the fallback and the VLM path is last.
  - Vendoring whole UFO² modules would pull in its agent/config framework [inference].
- **Everything**: `jarvis/node/jarvis_node/filesearch.py` with:
  - (1) ctypes `Everything64.dll` (bundle the SDK DLL only after checking the SDK license, otherwise ask the owner to install Everything 1.4/1.5)
  - (2) `es.exe -json` subprocess fallback
  - (3) Windows Search via `win32com.client.Dispatch("ADODB.Connection")` with `Provider=Search.CollatorDSO;Extended Properties='Application=Windows';` (connection string from background knowledge)
  - (4) a `os.scandir` walk of user roots as the last resort
- **Python version:** stay on 3.11 for the node. Moving the node to 3.14 just to import Windows-MCP would also require re-validating pywin32, comtypes, Presidio and spaCy [inference].

### Gaps
- I did not open Windows-MCP's source tree to list module names (e.g. `desktop/`, `tree/`) or measure the backport effort.
- I did not read the Everything SDK license or redistribution terms, nor the Everything 1.5 release status.
- UFO² module internals (what `ui_control/` contains, e.g. inspector/controller) were not listed on the page.
- What changed in pywinauto 1.0.0b1 (e.g. Python 3.12+ support, UIA backend changes) is unknown.

---

## Q5. Browser control: browser-use as a library, Playwright MCP (TypeScript) vs Playwright-Python with an extension or CDP connection to the owner's Chrome

### Takeaway
For the **sandbox/research lane**, JARVIS's Playwright-Python `browser.py` is already the right form, and browser-use (MIT, Python ≥3.11, CDP-based via `cdp-use`, no Playwright dependency) can be imported as an *agent loop* library there.

For the **signed-in Chrome lane**, there are two Python-native routes that would remove the Node-based Playwright MCP:
- **(a) Port the Playwright-MCP extension's CDP relay to Python.** The relay is a WebSocket server with `/extension` (the extension forwards `chrome.debugger`) and `/cdp` endpoints. Point Playwright-Python `connect_over_cdp` at the relay's `/cdp` and keep Microsoft's bridge extension, which keeps per-tab scoping.
- **(b) Use Chrome's `chrome://inspect/#remote-debugging` toggle (M144+)**, reading the `DevToolsActivePort` file and connecting to `ws://…/devtools/browser/<id>`. This exposes the *whole* browser to any local CDP client while it is on.

Chrome 136+ ignores `--remote-debugging-port` on the default profile.

### Cited Findings
- **browser-use 0.13.11** [verified] — [PyPI browser-use JSON](https://pypi.org/pypi/browser-use/json):
  - MIT, `requires_python <4.0,>=3.11`.
  - Pinned dependencies include **cdp-use 1.4.5**, **bubus 1.5.6** and **posthog 7.7.0**, plus anthropic, openai, google-genai, groq, ollama, mcp, aiohttp, httpx, pydantic and the Google API client libs. It also depends on `browser-harness` and `browser-use-sdk`.
  - **Playwright is not a dependency.**
- browser-use `Browser.from_system_chrome()` reuses a Chrome profile. Its Bash tool needs Linux/macOS/WSL (prior note) — [browser-use README](https://github.com/browser-use/browser-use)
- **Chrome 136 change.** It now ignores `--remote-debugging-port` / `--remote-debugging-pipe` on the default data directory, and they work only with a non-default `--user-data-dir`. [secondary summary of the Chrome blog] — [Chrome for Developers blog](https://developer.chrome.com/blog/remote-debugging-port?hl=zh-cn); [Browserless](https://www.browserless.io/blog/chrome-remote-debugging)
- **The chrome://inspect toggle** (cua issue #2916, Aug–Oct 2026, verified on Chrome 151):
  - The toggle is "the only sanctioned route to CDP" on a real profile.
  - It publishes a WebSocket endpoint in the `DevToolsActivePort` file (`/devtools/browser/<uuid>`).
  - `/json/version` returns 404 for toggle-enabled browsers.
  - [verified, single-user report] — [trycua/cua #2916](https://github.com/trycua/cua/issues/2916)
- **Playwright and the toggle.** `connectOverCDP()` to an M144+ browser enabled via chrome://inspect timed out. A maintainer later said "Depending on the browser config, `/devtools/browser` is required as the path" and closed it as completed (label v1.60, April 2026). [verified] — [microsoft/playwright #40027](https://github.com/microsoft/playwright/issues/40027)
- **Playwright MCP attach modes.**
  - `--extension` attaches to existing tabs ("reusing your logged-in sessions, cookies, and installed extensions"), with `--profile-dir-name` to pick a profile.
  - `--cdp-endpoint=chrome` connects by channel name after enabling remote debugging at `chrome://inspect/#remote-debugging`.
  - [verified] — [playwright.dev MCP browser extension](https://playwright.dev/mcp/configuration/browser-extension)
- **Relay architecture** (`cdpRelay.ts`): a WebSocket server with `/cdp` (full CDP for Playwright) and `/extension` (the extension forwards `chrome.debugger` calls). The file header is Apache-licensed Microsoft code. [secondary: forks mirrored on glama.ai] — [glama mirror cdpRelay.ts](https://glama.ai/mcp/servers/@maywzh/playwright-mcp/blob/4890b9d509ede4dd662b7f0c4f3718cec207081e/src/extension/cdpRelay.ts)
- **Extension mode security.** Each connection needs approval unless a token is configured (prior note). "Playwright MCP is **not** a security boundary" (prior note). — [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp)
- JARVIS today has `browserext.py` with an `ALLOWED_TOOLS` set over Playwright MCP extension mode, plus scripts `install_playwright_extension.ps1`, `allow_playwright_connection.ps1` and `save_playwright_token.ps1` (prior note) — [R browserext.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/node/jarvis_node/browserext.py)

### Inferences
- **Signed-in lane: port the relay (route a)** [inference]:
  - Write `jarvis/node/jarvis_node/cdp_relay.py`, an asyncio `websockets` server on 127.0.0.1 that implements the relay's two endpoints, porting Apache-2.0 logic with NOTICE kept. Then `playwright.async_api.chromium.connect_over_cdp("ws://127.0.0.1:<port>/cdp/<token>")`.
  - JARVIS's existing `ALLOWED_TOOLS` gate becomes Python methods (navigate, snapshot via `page.accessibility`/aria snapshot, click, type).
  - This removes Node and `@playwright/mcp` and keeps the extension's tab-picker and approval UX.
  - **Risk:** the extension↔relay message protocol is internal and unversioned, so pin the extension version (Chrome Web Store v0.1.0 seen in Apr 2026, prior note) and add a contract test.
- **Route b (toggle) is simpler but broader.** While enabled, any local process that can read `DevToolsActivePort` gets full CDP over every tab and profile in that Chrome instance, which conflicts with the "no cookie/password/token read" rule. If used, JARVIS should turn it on only for a session and verify socket ownership the way cua does [inference from cua #2916].
- **browser-use:**
  - Import it only in the research lane (isolated profile, Smokescreen/JARVIS egress proxy).
  - Its large dependency set (multiple LLM SDKs, Google API clients, posthog) and telemetry argue for either (i) installing it in a separate venv/process launched by the JARVIS supervisor, or (ii) using only `cdp-use` (typed CDP client) + JARVIS's own loop.
  - Disable telemetry with `ANONYMIZED_TELEMETRY=false`. That env name is from background knowledge, so verify it in the current source.
  - Route its LLM calls to JARVIS's gateway (OpenAI-compatible base URL) so lanes and redaction apply.

### Gaps
- The official `microsoft/playwright-mcp` relay source path and the extension protocol were not read on GitHub (the extension README URL 404'd). The relay details are from mirrors.
- I did not verify whether the chrome://inspect toggle shows a per-connection user prompt or persists across restarts.
- I did not confirm which Playwright-Python release contains the #40027 fix (v1.60 label only).
- I did not verify browser-use's telemetry opt-out variable and the `browser-harness` package's role.

---

## Q6. Egress filtering: Smokescreen (Go) vs a Python proxy (mitmproxy as a library, proxy.py, or a small asyncio CONNECT proxy)

### Takeaway
Smokescreen's value is small and well defined:
- a CONNECT proxy that **resolves each hostname and refuses non-public IPs**
- per-role hostname ACLs (YAML; role from the mTLS client cert CN)
- allow/deny lists
- rate and concurrency limits

None of that requires Go, so porting it into a JARVIS Python module is feasible and matches the "integrated" requirement. There are two good bases:
- **proxy.py**: BSD-3, embeddable with the `proxy.Proxy(...)` context manager and plugins (e.g. `FilterByUpstreamHostPlugin`), asyncio "threadless" core, TLS interception off by default.
- **A hand-written asyncio CONNECT proxy** of a few hundred lines.

mitmproxy is MITM-oriented and heavier, and is only worth it if content inspection is wanted. Keep the Smokescreen binary as an optional bundled fallback.

### Cited Findings
- **Smokescreen** [verified] — [stripe/smokescreen](https://github.com/stripe/smokescreen):
  - MIT, Go. "a HTTP CONNECT proxy".
  - Hostname ACLs with per-service `open`/`report`/`enforce` policies, plus `global_allow_list` / `global_deny_list`.
  - It resolves domains and requires a publicly routable IP, with IP allow/deny rules and self-connection guards.
  - Rate and concurrency limits, and Prometheus metrics.
  - Optional mTLS, where the role defaults to the cert CN (customisable through `RoleFromRequest`).
  - YAML config through `--config-file`.
  - Go embedding through `cmd.NewConfiguration` / `smokescreen.StartWithConfig`. No Python port is mentioned.
- **proxy.py** [verified] — [abhinavsingh/proxy.py](https://github.com/abhinavsingh/proxy.py):
  - BSD-3-Clause.
  - Embeddable with `proxy.Proxy(port=…)` (context manager) or blocking `proxy.main()`.
  - Plugins are passed as classes, e.g. `FilterByUpstreamHostPlugin`.
  - `port=0` gives an ephemeral port.
  - "Threadless executions using asyncio".
  - HTTPS is not decrypted by default (interception needs `--ca-*` flags).
  - Badges list Python 3.6–3.12. The stable line is 2.x, with 2.4.4rc builds referenced.
- **mitmproxy** can be embedded by constructing `DumpMaster` (from `mitmproxy.tools.dump`) with options, adding addon objects, and `await master.run()` inside `asyncio.run`. Addon hooks can be sync or async. [secondary: GitHub discussion and community docs; class location has changed across releases] — [mitmproxy discussion #5255](https://github.com/mitmproxy/mitmproxy/discussions/5255); [mitmproxy addon examples](https://docs.mitmproxy.org/stable/addons-examples)
- JARVIS `browser.py` already has a network guard (no localhost, private or Tailscale addresses). `egress.json` points to Smokescreen on the Oracle VM over Tailscale, and "The browser refuses to run if this proxy is unreachable" (prior note) — [R egress.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/config/egress.json); [R browser.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/node/jarvis_node/browser.py)

### Inferences
- **Recommended: `jarvis/egress/proxy.py`, a Smokescreen-equivalent in asyncio** [inference]:
  - Handle `CONNECT host:port` (and plain-HTTP absolute-URI GET if needed). Allow ports {443, 80} only.
  - Resolve with `asyncio.get_running_loop().getaddrinfo`, then **check every resolved IP** with `ipaddress`. Reject:
    - `is_private`, `is_loopback`, `is_link_local`, `is_multicast`, `is_reserved`, `is_unspecified`
    - 100.64.0.0/10 (Tailscale/CGNAT) and fd7a:115c:a1e0::/48 (Tailscale IPv6)
    - 169.254.169.254 (cloud metadata, critical on the Oracle VM)
    - IPv4-mapped IPv6
  - **Connect to the vetted IP, not the hostname**, to defeat DNS rebinding.
  - Per-role ACLs: the role comes from a per-lane proxy credential (`Proxy-Authorization`) or a per-lane listening port, instead of mTLS. Load the ACL from the existing YAML and keep Smokescreen's `open`/`report`/`enforce` semantics.
  - Write JSONL audit lines into JARVIS's audit store, enforce rate and concurrency limits per role, and use a deny list for banking, wallets and password managers (prior note).
  - Hard deny on Smokescreen's 'self-connection' equivalent: the proxy's own and the gateway's ports.
- **proxy.py as a base** saves the HTTP parsing and connection pooling; write a plugin overriding `before_upstream_connection`. The plugin hook name is from background knowledge, so verify it. Its CI badges stop at Python 3.12, which is fine for 3.11.
- **mitmproxy** only if JARVIS wants TLS-terminating inspection (e.g. stripping secrets from outbound bodies). That would need a JARVIS CA installed in the sandbox browser, which is acceptable for the throwaway profile and never for the owner's Chrome.
- **Placement:** run the same module on the VM (agent desktop) and on the laptop (sandbox Playwright). The binary-under-supervisor fallback is Smokescreen's release or a `go build` from vendored MIT source.

### Gaps
- The latest Smokescreen version and release cadence were not shown on the page.
- I did not verify proxy.py's latest stable version or Python 3.13 support, and did not check the exact plugin hook signatures.
- I did not fetch the official mitmproxy docs page for programmatic use (404 on the URL tried).

---

## Q7. Agent desktop on the Oracle ARM VM: webtop (GPL) / KasmVNC (GPL) / noVNC (MPL-2.0) / Selkies; embedding the viewer in JARVIS's UI and license implications

### Takeaway
**Selkies** is the best fit for "integrated into JARVIS":
- MPL-2.0, Python, `pip install selkies` (server + web client + capture extensions).
- WebSockets by default, WebRTC opt-in, and explicitly embeddable in "any HTML5 web interface".

JARVIS can launch `selkies` under its own supervisor on the VM against an Xvfb/XFCE session and embed the client in the JARVIS UI.

If a VNC stack is preferred:
- Embed **noVNC's `RFB`** class (`@novnc/novnc` 1.7.0, MPL-2.0, export `core/rfb.js`) in the JARVIS UI.
- Pair it with a VNC server run as a separate process (TigerVNC/x11vnc, or KasmVNC, which is GPL-2.0 per secondary sources).

linuxserver/webtop's repository is GPL-3.0 and has **no auth by default** plus a passwordless-sudo web terminal. Use it only as a reference, not copied code.

On licensing:
- MPL-2.0 is file-level copyleft, so JARVIS's own files stay under JARVIS's license if MPL files are kept as separate, unmodified-or-republished files.
- Running GPL servers as separate processes is generally treated as aggregation. That is my inference; get legal confirmation.

### Cited Findings
- **Selkies** [verified] — [selkies-project/selkies](https://github.com/selkies-project/selkies):
  - "License: MPL 2.0".
  - `pip install selkies` (Python 3.9+) installs "the server, its web client, and the capture extensions". `selkies session` runs the host's desktop.
  - Streams "over plain WebSockets by default, with WebRTC available as an opt-in transport". X11 and Wayland.
  - Can be used in "any HTML5 web interface you wish to embed inside".
  - arm64 is not mentioned on the page.
- **linuxserver/webtop** [verified] — [linuxserver/docker-webtop](https://github.com/linuxserver/docker-webtop):
  - The repository is "GPL-3.0 license" and is "based on Docker Baseimage Selkies".
  - No authentication by default. `CUSTOM_USER`/`PASSWORD` enable HTTP basic auth, suitable only for a "trusted local network".
  - The web terminal has passwordless sudo.
- webtop images are amd64+arm64, rebased to Selkies in June 2025, with `shm_size: 1gb` and ports 3000/3001 (prior note) — [linuxserver webtop docs](https://docs.linuxserver.io/images/docker-webtop/)
- **KasmVNC.** The license is GPL-2.0 per a software directory and a Gitee mirror, and RPM specs in a mirror say GPLv2+. It is a TigerVNC fork. [secondary; official LICENSE not read] — [LinuxLinks](https://www.linuxlinks.com/kasmvnc-browser-based-remote-access/); [Gitee mirror](https://gitee.com/rgbitx/KasmVNC). KasmVNC 1.5.0 added H.264/H.265/AV1 modes, and a linux/arm64 base image exists (prior note) — [KasmVNC releases](https://github.com/KasmTech/KasmVNC/releases); [lsiobase/kasmvnc tags](https://hub.docker.com/r/lsiobase/kasmvnc/tags)
- **noVNC.**
  - npm `@novnc/novnc` 1.7.0 is MPL-2.0 ("An HTML5 VNC client"), and `exports` points to `./core/rfb.js`. [verified] — [npm registry](https://registry.npmjs.org/@novnc/novnc/latest)
  - The license file defines the MPL-2.0 core as `core/**/*.js` and `app/*.js`. HTML/CSS are 2-Clause BSD, fonts SIL OFL, images CC BY-SA, and `vendor/pako` MIT. [secondary: copies of LICENSE.txt] — [OpenStack deb-novnc LICENSE.txt](https://git.openstack.org/openstack/deb-novnc/src/commit/c5e051b3b322ca512f4bb4d9c761eedd827bfec4/LICENSE.txt)
- The Oracle Always Free A1 allowance is now 2 OCPU / 12 GB (1,500 OCPU-h / 9,000 GB-h). A desktop + Selkies/KasmVNC is estimated at ~0.6–1 GB RAM (prior note; the estimate is inference) — [Oracle Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)

### Inferences
- **Recommended form** [inference]:
  - **VM side: `jarvis/vm/desktop.py` (supervisor unit).**
    - Starts `Xvfb :1` (or Xorg dummy) + XFCE.
    - Starts `selkies` pinned in JARVIS's VM venv, bound to 127.0.0.1 or the Tailscale IP only. JARVIS's gateway or the Tailscale ACL provides auth.
    - Never uses webtop's default no-auth setup.
    - Hermes/JARVIS `computer_use` drives the same `DISPLAY=:1` (xdotool or python-xlib).
  - **UI side.**
    - Selkies route: embed the Selkies HTML5 client in an `AgentDesktop` panel of the JARVIS UI (N.E.K.O web UI or the Tauri shell) as an iframe served through the JARVIS reverse proxy. That gives one origin and one auth.
    - noVNC route: import `RFB` from `@novnc/novnc/core/rfb.js` and use `new RFB(container, "wss://…/vnc", {credentials})` plus `viewOnly` (API names from background knowledge). Toggle `viewOnly` for "watch" vs "take over", matching the takeover UX.
  - **License hygiene.**
    - Ship noVNC/Selkies JS as separate, unmodified files with their LICENSE. If they are modified, publish the modified MPL files.
    - Don't copy webtop's GPL-3.0 init scripts into JARVIS; write JARVIS's own.
    - Running KasmVNC (GPL-2.0) unmodified as a separate binary over a network protocol does not by itself make JARVIS GPL. If KasmVNC's *modified noVNC client* is embedded, check that client's license separately. Background knowledge says KasmVNC's web client is a noVNC fork; not verified.
- **Memory.** At 12 GB, Selkies (Python + encoder) vs KasmVNC (C) RAM and CPU on Ampere is unmeasured. WebSocket JPEG/x264 software encoding on 2 OCPUs may be CPU-heavy, so benchmark both before committing [inference].

### Gaps
- Selkies' arm64 support, latest version, and pip-wheel availability for aarch64 (its capture extensions may be compiled) were not verified.
- The KasmVNC official LICENSE file and its web-client license were not read.
- I found no measured Selkies/KasmVNC resource figures for Ampere A1.
- I did not check whether Selkies' client can be embedded cross-origin or needs same-origin (iframe/WebSocket auth details).

---

## Q8. Cross-cutting: supervisor, pinning, supply chain

### Takeaway
"Integrated" in practice means JARVIS owns lifecycle, configuration, auth, logging and updates for every component:
- Python libraries are pinned with hashes.
- Bundled binaries (llama-server, optionally Smokescreen or llama-swap) are fetched by a JARVIS installer with a SHA-256 manifest.
- Vendored sources (Windows-MCP tree code, the Playwright-MCP relay port, noVNC/Selkies client) live in-repo with license and NOTICE files and an upstream-commit record.

The October 2026 LiteLLM compromise and the telemetry SDKs bundled in several candidates (posthog in Windows-MCP and browser-use) make this discipline necessary, not optional.

### Cited Findings
- LiteLLM 1.107.0's payload ran at interpreter startup through a `.pth` file and stole cloud/SSH keys, AI-tool auth files and env vars (prior note) — [Socket.dev](https://socket.dev/blog/litellm-compromise)
- Windows-MCP 0.8.8 depends on `posthog>=7.4.0` [verified] — [PyPI windows-mcp](https://pypi.org/pypi/windows-mcp/json); browser-use 0.13.11 pins `posthog 7.7.0` [verified] — [PyPI browser-use](https://pypi.org/pypi/browser-use/json)
- JARVIS treats `providers.json`, `policy.py`, `audit.py` and others as SAFETY_COMPONENTS that agent writes are restricted from (prior note) — [R policy.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/policy.py)

### Inferences
- **Add `jarvis/supervisor/`** [inference]:
  - Unit definitions for `swapper`, `llama-server@<model>`, `egress-proxy`, `cdp-relay`, `selkies` (VM) and `verdict`, each with health checks, restart backoff, log capture into JARVIS's audit, and bind-address assertions (127.0.0.1 or Tailscale only).
  - On Windows, run them as child processes of the node service. On the VM, use systemd units generated by JARVIS.
- Use `uv`/`pip --require-hashes` lockfiles. Block any package that installs `.pth` files except the allowlisted ones. Pin litellm (if present at all) to `==1.106.2` with hash, or exclude it.
- Add the new modules (`router/*`, `egress/*`, `cdp_relay.py`, `swapper.py`) to SAFETY_COMPONENTS so agents cannot edit their own routing or egress rules.

### Gaps
- I did not check which components the N.E.K.O base already supervises, or how (process manager in N.E.K.O vs JARVIS kernel).
