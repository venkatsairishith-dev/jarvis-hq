# Open-source LLM routers/gateways and free/cheap provider tiers for JARVIS (as of 2026-10-22)

Scope note: research only, no implementation. All pages were seen on 2026-10-22 unless stated otherwise. GitHub API (`gh api`) was blocked in this session, so repo metadata comes from the rendered GitHub pages, npm, and Docker Hub. Several provider data-use claims are available only through secondary aggregators. Each one is flagged.

---

## 1. 9router (github.com/decolua/9router): features, security defaults, storage, ARM, license, versions, advisories, ToS

### Takeaway
9router is a popular (~31k stars) MIT-licensed Next.js router on port 20128. Its features are combos/fallback, multi-account round-robin, RTK and Headroom token savers, and OAuth "subscription" providers. Its defaults are insecure: password `123456`, published default `API_KEY_SECRET`, `REQUIRE_API_KEY=false`, `CLOUD_URL=https://9router.com`. It has a long 2026 run of critical CVEs, including an unauthenticated API-key leak, a hardcoded JWT secret, RCE via the default password, SSRF, and a still-unfixed-as-reported config-injection RCE through the **Hermes Agent settings endpoint**. Its OAuth-subscription providers (Claude Code, Antigravity, Codex, Cursor) are exactly what Anthropic and Google have banned or enforced against. It should not hold private-lane traffic or real subscription credentials unless it is fully patched, kept Tailscale-only, and has API keys enforced.

### Cited Findings
**Features (README)**
- Combos chain models into tiers (subscription → cheap → free). The router switches when quota runs out or errors occur. — [9router README](https://github.com/decolua/9router)
- Multi-account per provider with round-robin or priority routing. When an account hits quota, it falls back to the next account. — [9router README](https://github.com/decolua/9router)
- RTK Token Saver compresses tool output (git diff, grep, ls) before it reaches the LLM. It claims 20–40% input-token savings and is **on by default**. It can be bypassed per request with the header `X-9Router-Token-Saver: off`. — [9router README](https://github.com/decolua/9router)
- Headroom Token Saver is an optional external `/v1/compress` proxy, run separately and enabled in the dashboard. If it fails, requests pass through unchanged. — [9router README](https://github.com/decolua/9router)
- Other features: "Caveman Mode", "Ponytail" prompt injection, real-time quota tracking, format translation, auto token refresh, request logging, cloud sync, usage analytics. — [9router README](https://github.com/decolua/9router)
- OAuth providers: Claude Code, Antigravity, Codex, GitHub, Cursor, Kimchi. Listed free providers: Kiro AI (50 credits/month), OpenCode Free (no auth), Vertex AI ($300 GCP credits). The README says **iFlow, Qwen Code and Gemini CLI free tiers were discontinued in 2026**. — [9router README](https://github.com/decolua/9router)

**Security defaults (README env table)**
- `INITIAL_PASSWORD` = `123456` (used for the first login when no saved hash exists).
- `JWT_SECRET` is auto-generated and stored at `~/.9router/jwt-secret`.
- `API_KEY_SECRET` = `endpoint-proxy-api-key-secret` and `MACHINE_ID_SALT` = `endpoint-proxy-salt`. Both defaults are publicly published.
- `REQUIRE_API_KEY` = `false`. When true it enforces Bearer keys on `/v1/*`, and the README recommends it for internet-exposed deploys.
- `AUTH_COOKIE_SECURE` = `false`. `ENABLE_REQUEST_LOGS` = `false` (when on, logs go to `logs/`).
- `CLOUD_URL` = `https://9router.com`. `BASE_URL` = `http://localhost:20128`. `DATA_DIR` = `~/.9router`. Docker sets `HOSTNAME=0.0.0.0`.
- Source for all of the above: [9router README](https://github.com/decolua/9router)

**Storage, ARM, license, versions**
- State is stored in SQLite at `${DATA_DIR}/db/data.sqlite`, with backups in `${DATA_DIR}/db/backups/`. Older versions used `db.json`; the current README does not mention it. — [9router README](https://github.com/decolua/9router)
- Docker images are multi-platform **linux/amd64 + linux/arm64** on Docker Hub and GHCR. The README also covers a PM2 VPS deployment. — [9router README](https://github.com/decolua/9router)
- License: MIT. Stars: about 31.2k. — [9router README](https://github.com/decolua/9router); npm also lists MIT — [npm registry](https://registry.npmjs.org/9router)
- The latest **npm** version is **0.5.104**. — [npm registry](https://registry.npmjs.org/9router). The latest **GitHub Release** is **v0.5.35 (16 Jul)**, with releases v0.5.2→v0.5.35 between 17 Jun and 16 Jul. — [9router releases](https://github.com/decolua/9router/releases). **Conflict:** the GitHub Releases page lags npm by about 70 patch versions, so npm appears to be the real release channel and cadence there is very fast (multiple patches per week).
- Release notes include security items: v0.5.2 "hardened reverse-proxy local-access trust + SSRF hardening on web fetch". v0.5.18 fixes cross-IdP account overwrite and **adds MITM root CA generation on server startup**. — [9router releases](https://github.com/decolua/9router/releases)

**Security advisories (2026)**
- CVE-2026-59801 / GHSA-7f4f-856q-69wc: unauthenticated access to provider-management API endpoints, ≤0.4.41. — [GitHub Advisory](https://github.com/advisories/GHSA-7f4f-856q-69wc)
- Unauthenticated plaintext API-key exposure via `/api/usage/stats` in 0.4.41. The search summary gives CVE-2026-62327, CVSS 9.3. — [VulnCheck](https://www.vulncheck.com/advisories/9router-unauthenticated-api-key-exposure-via-api-usage-stats); a related unauthenticated info disclosure — [VulnCheck](https://www.vulncheck.com/advisories/9router-unauthenticated-information-disclosure-via-api-usage-endpoints)
- CVE-2026-49352 (CVSS 9.8): hardcoded fallback JWT secret allows forged login cookies, 0.2.21–0.4.44, fixed in 0.4.44. — [Strix](https://www.strix.ai/cve/CVE-2026-49352)
- RCE via the default password, before 0.4.60 (CVE-2026-63732). — [VulnCheck](https://www.vulncheck.com/advisories/9router-before-remote-code-execution-via-default-password). A blog says 0.4.60 was never released and recommends ≥0.4.80; it also notes that sources disagree on the affected range. — [kkm-mako blog](https://kkm-mako.com/en/blog/articles/9router-cve/)
- SSRF via `/v1/web/fetch` before 0.4.72. — [VulnCheck](https://www.vulncheck.com/advisories/9router-before-server-side-request-forgery-via-v1-web-fetch)
- CVE-2026-56679 (CVSS 8.7): an authenticated user can disable login protection through the settings endpoint, reportedly fixed in 0.5.4. — [Strix](https://www.strix.ai/cve/CVE-2026-56679)
- **CVE-2026-108593 (CVSS 7.3, CWE-94, published 2026-10-10):** config injection in `POST /api/cli-tools/hermes-settings`. Authenticated dashboard users can inject arbitrary keys into **Hermes Agent's `config.yaml`**, and a crafted `baseUrl` adds a hook that runs a shell command after an LLM call. Affects ≥0.4.1 ≤0.5.99. **No fixed version reported.** — [HOL Guard CVE page](https://hol.org/guard/security/cves/CVE-2026-108593-9router-041-through-0599-config-injection-rce-via); [VulnCheck](https://www.vulncheck.com/advisories/9router-0.4.1-through-0.5.99-config-injection-rce-via-hermes-settings-endpoint)

**ToS / account-sharing**
- Anthropic: a terms update on 2026-02-20 prohibited using consumer-subscription OAuth tokens in third-party tools, with enforcement reported from 2026-04-04. Some outlets say server-side blocking started on 2026-01-09. — [alternativeto.net](https://alternativeto.net/news/2026/2/anthropic-officially-bans-using-subscription-authentication-for-third-party-claude-use); [openclaw.report](https://openclaw.report/ecosystem/anthropic-bans-oauth-tokens-third-party-tools). **Conflict:** one source reports a 2026-05-13 reversal into an "Agent SDK credit" that was then paused on 2026-06-15 — [betterclaw.io](https://www.betterclaw.io/blog/openclaw-anthropic-subscription-ban). Another, dated about mid-Oct 2026, says subscription tokens used outside Claude Code are rejected at the API layer — [kersai.com](https://kersai.com/anthropic-killed-third-party-claude-access-heres-every-workaround-that-still-works/). The current status is unresolved; no Anthropic primary page was checked.
- Google: Antigravity accounts have been suspended for "use of 3rd party tools or proxies to access Antigravity resources and quotas". Per a Google team member quoted in a GitHub discussion, such bans also cut off Gemini CLI and Code Assist because the backend is shared. — [gemini-cli Discussion #20632](https://github.com/google-gemini/gemini-cli/discussions/20632). Paid AI Ultra/Pro subscribers were reportedly suspended in Sept 2026. — [Enterprise DNA](https://enterprisedna.co/resources/ai-pulse/ai-pulse-2026-09-03-google-suspends-paying-antigravity-subscribers-for-using-thi/); [Google AI forum appeal thread](https://discuss.ai.google.dev/t/appeal-for-antigravity-account-ban-unintentional-tos-violation-via-third-party-proxy/141906)
- A third-party repo bulk-creates Dahl accounts "with 9Router connector", which illustrates the multi-account farming culture around these routers. — [F0Rextasy/dahl-bulk-apikey](https://github.com/F0Rextasy/dahl-bulk-apikey)

### Inferences
- Routing Claude Code, Antigravity, Codex or Cursor *subscription* OAuth through 9router (or OmniRoute) is ToS-risky. The Google case is documented with account bans that spread to Gemini CLI. Multi-account round-robin used to multiply free quotas likely violates most providers' "one account per person / no circumvention" terms. JARVIS should use only official API keys in the router.
- The Hermes-settings CVE matters directly here: the owner runs Hermes Agent on the same VM. Any 9router instance (if the "9router setup" is still live on the laptop or VM) must not have write access to Hermes' config, or should be removed.
- RTK is "on by default" and Caveman/Ponytail inject into prompts, so content is altered silently. For a privacy gateway that needs deterministic redaction, turn these off or verify they run *after* redaction.
- `CLOUD_URL=https://9router.com` is the default. Whether cloud sync uploads provider credentials was not confirmed, so treat it as a possible exfiltration path and disable or blank it.

### Gaps
- No exact release dates for npm versions after 0.5.35. The npm `time` object was not readable.
- Whether 0.5.100–0.5.104 fixes CVE-2026-108593 is unverified.
- What cloud sync uploads to 9router.com (credentials? usage?) is not documented in the README text seen.
- No official 9router statement on ToS was found.

---

## 2. OmniRoute (diegosouzapw/OmniRoute)

### Takeaway
OmniRoute is a far larger, faster-moving MIT gateway: v3.8.53, 375 providers claimed, 20 routing strategies, MCP server, A2A, guardrails with opt-in PII masking, per-key USD budgets, AES-256-GCM credential encryption, telemetry off by default, and multi-arch amd64/arm64 images. It labels ToS-risky providers `tos: avoid` and excludes them from auto-routing. Its security record is also poor: a critical 2026 unauthenticated RCE when `requireLogin=false`, an authz bypass, and a Socket.dev MITM/root-CA flag. The owner's instance (Tailscale-only) is a reasonable base if it is kept on the latest 3.8.x, login is required, and code-exec routes are disabled.

### Cited Findings
- One local endpoint at `http://localhost:20128/v1`. Claims 375 providers (150+ free) and 1,400+ models. Works with Claude Code, Codex, Cursor. — [OmniRoute README](https://github.com/diegosouzapw/OmniRoute)
- 20 routing strategies plus "auto" combos that score providers live. Fallback across tiers with circuit breakers and cooldowns. Built-in MCP server (110 tools) and A2A. Guardrails include prompt-injection protection and **opt-in PII masking with custom patterns**. Per-key USD spend quotas with cost headers. Scheduled log export to BigQuery. Opt-in persistent memory. Token compression (claims 15–95%). — [OmniRoute README](https://github.com/diegosouzapw/OmniRoute)
- Credentials are encrypted with AES-256-GCM. Optional OIDC dashboard login; password login stays available. "Local-first"; the client does not upload prompts, traffic, or telemetry to the Radar service. Telemetry is disabled by default. — [OmniRoute README](https://github.com/diegosouzapw/OmniRoute)
- Storage: `~/.omniroute/` with better-sqlite3 (WAL) plus LowDB for legacy JSON. Docker volume `omniroute-data` at `/app/data`. — [OmniRoute README](https://github.com/diegosouzapw/OmniRoute)
- The README lists native ARM support (Raspberry Pi, ARM servers, Apple Silicon) and multi-arch AMD64/ARM64 images. — [OmniRoute README](https://github.com/diegosouzapw/OmniRoute). On Docker Hub, tags `next`, `next-web`, `next-web-bun` are amd64+arm64 (updated 2026-10-21), but **`next-bun` and `latest-bun` are amd64-only**. — [Docker Hub tags API](https://hub.docker.com/v2/repositories/diegosouzapw/omniroute/tags?page_size=5)
- ToS handling: providers marked `tos: avoid` (e.g. Kiro) are excluded from auto routing by default. Keyless providers may reject third-party clients. Some links are affiliate links. — [OmniRoute README](https://github.com/diegosouzapw/OmniRoute)
- Version v3.8.53 (release branch shown). Roadmap to a v3.9.0 LTS. About 79.6k stars and 11.6k forks per the page capture. — [OmniRoute README](https://github.com/diegosouzapw/OmniRoute). Security policy: 3.8.x active, 3.7.x gets security fixes, 48h acknowledgement, 14 business days for critical patches. — per search summary of [OmniRoute Security](https://github.com/diegosouzapw/OmniRoute/security)
- **GHSA-hf57-cqmx-p4gr / CVE-2026-88062 (critical, published 2026-09-03):** the ACP custom-agent registration endpoint passes attacker input to command execution. Anonymous RCE if `requireLogin=false`. Fixed-version conflict: 3.8.49 vs 3.8.50 vs "≤3.8.50 affected, no patch listed". — [Repo advisory](https://github.com/diegosouzapw/OmniRoute/security/advisories/GHSA-hf57-cqmx-p4gr); [GitHub Advisory DB](https://github.com/advisories/GHSA-hf57-cqmx-p4gr)
- GHSA-hcwm-v74f-pfvh: authz bypass in which the LOCAL_ONLY manage-scope bypass used an incomplete blocklist of process-spawning routes, fixed toward v3.8.52. — [PR #16477](https://github.com/diegosouzapw/OmniRoute/pull/16477); threat model cites GHSA-fhh6-4qxv-rpqj (spawn-capable routes exposed to non-local traffic) — [Threat Model wiki](https://github.com/diegosouzapw/OmniRoute/wiki/Threat-Model)
- v3.8.5 was flagged by Socket.dev for suspected MITM/root-CA behavior. — [Issue #2863](https://github.com/diegosouzapw/OmniRoute/issues/2863). The 3.8.52 image ships two HIGH adm-zip advisories (fixed in adm-zip 0.6.1). — [pfnapp/base-images #192](https://github.com/pfnapp/base-images/issues/192)

### Inferences
- OmniRoute is a better fit than 9router for JARVIS: encryption at rest, budgets, PII masking, a ToS-avoid flag, and arm64 images. Its attack surface (MCP with 110 tools, ACP agent spawning, A2A, Playwright web-cookie providers, MITM CA) is much larger than a router needs. Hardening: `requireLogin=true`, API keys required, disable ACP/spawn routes and unused MCP tools, keep it bound to Tailscale, pin a digest of a multi-arch tag (not `*-bun`).
- Do not rely on OmniRoute's PII masking as the privacy boundary. JARVIS's own gateway should redact *before* OmniRoute, and lane enforcement should pick only allowlisted provider connections per lane.

### Gaps
- Fork lineage from 9router was **not confirmed** by any page seen. The README mentions 9router only in a comparison table (image). The GitHub API was blocked.
- OmniRoute's default password and REQUIRE_API_KEY-equivalent defaults were not found in the README text.
- The exact first patched version for CVE-2026-88062 is unresolved.

---

## 3. LiteLLM proxy and other open routers: which is best for a privacy-lane-aware setup?

### Takeaway
LiteLLM remains the most featureful OSS proxy (100+ providers, fallbacks, budgets, virtual keys, tag/team routing). It had a **second supply-chain compromise on 2026-10-21**: v1.107.0 on PyPI and GHCR shipped a credential stealer, after an earlier one in March 2026. Bifrost (Go, Maxim AI) is the strongest lean alternative. Portkey's OSS gateway is MIT but budgets live in its hosted/enterprise tiers. TensorZero was reportedly archived in June 2026. For lanes, the cleanest design is to keep lane logic in the JARVIS gateway, which maps lane → allowlisted model group. Use OmniRoute (already deployed) or a pinned LiteLLM/Bifrost purely as the fallback executor.

### Cited Findings
- LiteLLM compromise, 2026-10-21: all seven `litellm` 1.107.0 wheels (including manylinux/musllinux **aarch64**) and `ghcr.io/berriai/litellm:v1.107.0` were affected. `latest`, `main-latest` and `main-stable` resolved to it around 13:14 UTC. The payload (`cook.pth` + `sitecustomize.py`) runs at Python startup and steals cloud/SSH/GPG keys, AI-tool auth files, env vars, and the LiteLLM DB if `DATABASE_URL` is set. Exfiltration goes to `litellm.dev`. The build came from a lookalike repo `Berri-AI/litellm`. Upwind recommends pinning **1.106.2**. — [Socket.dev](https://socket.dev/blog/litellm-compromise); also [The Hacker News](https://thehackernews.com/2026/10/litellm-compromised-in-supply-chain.html), [Rescana](https://www.rescana.com/post/litellm-1-107-0-supply-chain-compromise-october-2026). Accounts of exact timing and injection path differ.
- Earlier LiteLLM compromise on 2026-03-24 (v1.82.7/1.82.8, attributed to "TeamPCP", via a Trivy dependency in CI), live about 40 minutes. — [LiteLLM security update](https://docs.litellm.ai/blog/security-update-march-2026)
- Bifrost: Go gateway with vendor-reported ~11 µs overhead at 5k RPS. Virtual keys, hierarchical budgets, rate limits, RBAC, OIDC/SCIM. Smaller community. Content guardrails are Enterprise-only. — per search summary of [MarkTechPost 2026-10-14](https://www.marktechpost.com/2026/10/14/best-ai-gateways-2026/), [getmaxim.ai](https://www.getmaxim.ai/articles/tensorzero-alternatives-5-llm-gateways-compared-in-2026/) (vendor-authored)
- Portkey gateway: OSS is MIT. Budgets and rate limits are in hosted/enterprise. Ownership is reported as Palo Alto Networks since May 2026 (sources differ on "owned" vs "backed"). — per search summary of [cloudzero.com](https://www.cloudzero.com/blog/llm-gateways/), [coworker.ai](https://coworker.ai/blog/litellm-alternatives)
- TensorZero was reportedly archived in June 2026. — per search summary of [getmaxim.ai](https://www.getmaxim.ai/articles/tensorzero-alternatives-5-llm-gateways-compared-in-2026/) (unverified against the repo)
- Codex CLI integration with LiteLLM is documented (`model_catalog_url`, `/v1/models`). — [LiteLLM Codex docs](https://docs.litellm.ai/docs/proxy/client_setup/codex_cli.md)

### Inferences
- Recommendation: **keep OmniRoute as the single router on the Oracle VM** (it already runs, is arm64, and has budgets, fallback and encryption). Do not add 9router. Put lane enforcement in JARVIS's privacy gateway with one OmniRoute combo per lane, e.g. `lane-private` containing only no-train/ZDR providers and `lane-public` containing free-tier providers. Give each lane its own OmniRoute API key so a public-lane key cannot reach private-lane connections.
- If OmniRoute's attack surface is unacceptable, Bifrost (single Go binary, smaller surface) is the fallback choice. LiteLLM should be used only pinned by digest to a verified version (≤1.106.2 per Upwind), with secrets isolated, given two compromises in 2026.
- RouteLLM and Helicone are not general privacy-lane routers. RouteLLM is a strong/weak model-cost router (research code), and Helicone is primarily observability. Neither was verified as current.

### Gaps
- Bifrost license, ARM64 image availability, and current version were not verified (GitHub API blocked).
- Helicone ai-gateway and RouteLLM maintenance status were not verified.
- TensorZero archival was not confirmed on GitHub.

---

## 4. Free/cheap provider tiers (Oct 2026): limits and data-use terms → lane assignment

### Takeaway
Big 2026 churn: **GitHub Models retired 2026-07-30**. Per one tracker, Cerebras, SambaNova, Hugging Face free credits and the DeepSeek signup grant were delisted, and **Gemini CLI's personal free tier ended 2026-06-18**. Free tiers that remain *and* have credible no-training terms are Groq (no retention by default, self-serve ZDR, US), Cloudflare Workers AI (no training without consent, 10k neurons/day) and Ollama Cloud (claims no logging/training/ZDR). Gemini free/unpaid, Mistral Free/Experiment (training on by default, opt-out available), NVIDIA trial (conflicting terms), DeepSeek (China storage, training by default), Z.ai free Flash (ambiguous) and OpenRouter `:free` endpoints (many providers log or train) belong in the **public lane only**. Gemini **paid** is acceptable for the private lane (no training, abuse logging for a limited period).

### Cited Findings
**Google Gemini API / AI Studio**
- Unpaid Services: Google uses submitted content and responses "to provide, improve, and develop" products. "Human reviewers may read, annotate, and process your API input and output." "Do not submit sensitive, confidential, or personal information." — [Gemini API Additional Terms](https://ai.google.dev/gemini-api/terms) (effective 2026-03-23, last updated 2026-04-28)
- Paid Services: Google "doesn't use your prompts" or responses to improve products. It logs prompts and responses "for a limited period of time, solely for detecting and preventing violations" (duration not stated). Data may be cached in any country. A DPA applies. Grounding with Search/Maps stores data for 30 days. In EEA/CH/UK, paid-style data terms apply to all usage. Users must be 18 or older. — [Gemini API Additional Terms](https://ai.google.dev/gemini-api/terms)
- Free-tier per-model RPM/RPD is **no longer published** on the rate-limits page ("View your active rate limits in AI Studio"). Tier 1 has a $10 per rolling 10-minute spend cap. — [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) (last updated 2026-10-15)
- Gemini CLI: the personal Google-login free tier (formerly 1,000 RPD / 60 RPM) ended 2026-06-18 and users were steered to Antigravity CLI (`agy`). An API-key free tier is listed at 250 RPD / 10 RPM, Flash only. — per search summary of [tembo.io](https://www.tembo.io/blog/gemini-cli-pricing), [yaw.sh](https://yaw.sh/blog/gemini-cli-not-free-alternatives/), [Gemini CLI quota docs](https://geminicli.com/docs/resources/quota-and-pricing/). Consistent with the 9router README saying the Gemini CLI free tier was discontinued in 2026 — [9router README](https://github.com/decolua/9router)
- Free models seen live on 2026-10-19: gemini-3.8-flash, gemini-3.5-flash, gemini-3.1-flash-lite, gemma-4. — [awesome-free-ai-coding providers](https://mvalentsev.github.io/awesome-free-ai-coding/providers/)

**Groq**
- "By default, Groq does not retain customer data for inference requests." Inputs and outputs are retained only for batch/fine-tuning or reliability/abuse logs (up to 30 days). **All customers may enable ZDR** in Data Controls. Data is stored in GCP buckets in the US. The page does not address training. — [Groq "Your Data"](https://console.groq.com/docs/your-data) (no date on page). The page meta still says "30-day retention" (stale), and Opper claims ZDR is enterprise-only, which conflicts. — per search summary
- Trust center FAQ reportedly says Groq does not use customer data to train its own models (secondary). — per search summary of [meetily.ai](https://meetily.ai/llm-privacy/groq)
- Free models live 2026-10-19: gpt-oss-120b, gpt-oss-20b, qwen3.8-27b. — [awesome-free-ai-coding](https://mvalentsev.github.io/awesome-free-ai-coding/providers/)

**OpenRouter**
- `:free` models: 20 RPM. 50 RPD if less than 10 credits were ever purchased, **1,000 RPD with at least 10 credits purchased (all-time)**. BYOK is not gated. 429 responses include `X-RateLimit-*` headers. — [OpenRouter limits](https://openrouter.ai/docs/api-reference/limits)
- Separate account toggles for paid and free models control whether routing may hit providers that train. Opting out stops routing to training providers. Per-request data-policy filtering is available. The training opt-out "has no bearing on OpenRouter's own policies". OpenRouter does *not* route based on provider retention. In-region EU/US routing is Business/Enterprise only. — [OpenRouter provider logging](https://openrouter.ai/docs/guides/privacy/provider-logging)

**Cloudflare Workers AI**
- 10,000 Neurons/day free, resets 00:00 UTC. $0.011 per 1k Neurons above that on Workers Paid. — [Cloudflare pricing](https://developers.cloudflare.com/workers-ai/platform/pricing) (via search summary)
- Terms: Cloudflare won't use customer content to train models or improve services without explicit consent, and content is not shared with other customers. Storage applies only if you attach R2/KV/etc. — per search summary quoting Cloudflare terms ([cloudflare llms-full](https://developers.cloudflare.com:8443/workers/llms-full.txt))

**Mistral (La Plateforme / "Mistral Studio")**
- Official: API requests under the Experiment/Free plan **may be used to train**. Opt out via Admin Console → Privacy → disable "Anonymous improvement data". Scale plan is opted out by default. Free mode has the lowest limits (see the Limits page). — per search summary of [Mistral help 455207](https://help.mistral.ai/en/articles/455207-can-i-opt-out-of-my-input-or-output-data-being-used-for-training), [Mistral help 450104](https://help.mistral.ai/en/articles/450104-how-can-i-try-the-api-for-free-with-the-experiment-plan)
- The API and Vibe opt-outs are separate. Labs/Preview models are trained on regardless of opt-out (quoted from commercial terms). — [agentdeals PR #2365](https://github.com/robhunter/agentdeals/pull/2365) (secondary)
- "~1B tokens/month, ~1 req/s" figures are third-party and unverified. — [freellm.net](https://freellm.net/providers/mistral-ai)

**GitHub Models**: fully retired 2026-07-30 (playground, catalog, inference API, BYOK). — [GitHub changelog](https://github.blog/changelog/2026-07-30-github-models-is-now-retired/); [announcement](https://github.blog/changelog/2026-07-01-github-models-is-being-fully-retired-on-july-30-2026)

**NVIDIA NIM / build.nvidia.com**
- A trial for "prototyping, research, development and testing" only. Rate varies per model; users widely report 40 RPM and NVIDIA says it won't raise it. — [NVIDIA NIM FAQ](https://forums.developer.nvidia.com/t/nvidia-nim-faq/300317); [forum thread](https://forums.developer.nvidia.com/t/rpm-increase-request-40-rpm-200-rpm/376235)
- Data use is **conflicting**. A secondary summary quotes "will not keep or use content after each session". A forum post cites §3.3(iv) allowing use of prompts and outputs to improve products. — per search summary; trial terms not read directly
- Live models 2026-10-19 include glm-5.3, nemotron-3-ultra, gemma-4-31b, gpt-oss-20b. — [awesome-free-ai-coding](https://mvalentsev.github.io/awesome-free-ai-coding/providers/)

**Cerebras**: free offer delisted 2026-09-16. The only offer now is $5 credit after adding a payment method, expiring in 30 days. — [awesome-free-ai-coding](https://mvalentsev.github.io/awesome-free-ai-coding/providers/). Secondary summary: it does not retain prompts or outputs and has no right to train on Service Content (unverified). — [developersdigest/cerebras](https://www.developersdigest.tech/models/free-api/cerebras)

**SambaNova**: delisted 2026-09-23 (new accounts get no free usage). **Hugging Face Inference Providers**: delisted 2026-10-08 (free accounts get no credits). **DeepSeek**: delisted 2026-07-27 (no free grant on the official pricing page). — [awesome-free-ai-coding](https://mvalentsev.github.io/awesome-free-ai-coding/providers/)

**DeepSeek data**: per its privacy policy, personal data including prompts is stored in the PRC. Inputs may be used for training; an opt-out is described, but reportedly no clear API mechanism as of April 2026. Data can be shared with authorities. — per search summary of [meetily.ai/deepseek](https://meetily.ai/llm-privacy/deepseek), [datanorth.ai](https://datanorth.ai/blog/deepseek-data-privacy-and-security-key-insights-on-protection-and-risks) (secondary)

**Z.ai (Zhipu)**: free Flash models glm-4.7-flash, glm-4.5-flash, glm-4.6v-flash were live on 2026-10-19. — [awesome-free-ai-coding](https://mvalentsev.github.io/awesome-free-ai-coding/providers/). Terms quoted secondhand: "For enterprises and developers using API Services, we will not use your User Content… unless you explicitly agree". Training rights are reserved over "individual users". — per search summary of [opper.ai](https://opper.ai/zhipu/glm-4-7-flash) (unverified)

**Dahl Inference (inference.dahl.global)**
- "First 100 million tokens free". Base URL `https://inference.dahl.global/v1` (OpenAI-compatible). Models on the page: MiniMax M2.7, DeepSeek Flash, GLM 5.3 Flash, Qwen 3.8 Flash. **No retention or training claims on the landing page**; the operator is not named there. — [Dahl landing](https://inference.dahl.global/); [Dahl docs](https://docs.dahl.global/authentication)
- Terms v2.0 (2026-09-08) reportedly name the operator FROMZERO OÜ (Tallinn, Estonia), which reserves the right to end discretionary free access. The awesome-list maintainer rejected it as a one-time allocation. — [awesome-free-llm-apis PR #122](https://github.com/mnfst/awesome-free-llm-apis/pull/122). "Zero data retention" claims appear only in social posts. — [X post](https://x.com/0xDecaso/status/2076002947394388145). Cloudflare may return 403 to plain curl. — [DEV.to](https://dev.to/victor_tarasov_057fe5583a/four-openai-compatible-endpoints-are-giving-away-free-tokens-right-now-3l2l)

**Ollama Cloud**
- $0 plan, 1 concurrent request, unpublished monthly allowance (resets from signup, no rollover). Starter models only; adding credits unlocks all. Base `https://ollama.com/v1`. Verified 2026-10-19. — [awesome-free-ai-coding/ollama-cloud](https://mvalentsev.github.io/awesome-free-ai-coding/providers/ollama-cloud/)
- Ollama says prompt and response data is never logged or trained on, partners must have no-logging/no-training/ZDR, and hosting is in the US and Europe. — [Ollama blog](https://ollama.com/blog/transparent-pricing) (via search summary)

**Together AI**: no current free tier found.

### Inferences
Suggested lane mapping (verify each provider's terms in its console before go-live):
- **Private lane (no training, minimal retention):** Gemini API **paid** (Tier 1, plus a billing cap; note abuse logging). Groq with **ZDR enabled** (US). Cloudflare Workers AI (no-train terms). Ollama Cloud (policy-based ZDR claim). Mistral only on a paid/Scale plan or with the opt-out confirmed, excluding Labs/Preview models. OpenRouter paid models only with the account "no training" toggle on, per-request `data_collection: deny`, and ideally ZDR-only endpoints. The local model on the laptop/VM is the safest option. Cerebras paid also fits if its no-retention claim is verified.
- **Public lane (synthetic/public data only):** Gemini free/AI Studio (training plus human review), Mistral Free/Experiment, NVIDIA NIM trial, OpenRouter `:free`, Z.ai free Flash, DeepSeek, Dahl (unknown operator terms, promotional, at risk of Cloudflare 403).
- Remove from any plan: GitHub Models (retired), HF free credits, SambaNova free, Cerebras free, DeepSeek free grant, and the Gemini CLI personal free tier.
- Cheapest useful upgrade: a one-time $10 OpenRouter credit purchase lifts `:free` from 50 to 1,000 RPD. Still public-lane only.

### Gaps
- Gemini free-tier per-model RPM/RPD for Oct 2026 (now shown only inside AI Studio).
- Primary texts for Cloudflare, Cerebras, NVIDIA trial terms, Z.ai, and DeepSeek were not fetched directly; findings are from search summaries or aggregators.
- Groq training statement was not found on the primary "Your Data" page.
- Dahl privacy/terms pages were not read directly.
- Together AI current free/credit status and terms were not researched.
- Mistral exact free limits were not confirmed.

---

## 5. Connecting coding CLIs to a router endpoint

### Takeaway
Most CLIs accept a custom base URL. Claude Code needs an **Anthropic Messages-compatible** endpoint (`ANTHROPIC_BASE_URL`, no `/v1`). Codex CLI now needs an **OpenAI Responses API** endpoint (`/v1/responses`; `wire_api="chat"` removed in early 2026), so the router must support Responses. Gemini CLI takes `GOOGLE_GEMINI_BASE_URL` with an API key. OpenCode, goose and Aider take OpenAI-compatible base URLs. The Antigravity CLI (`agy`) custom-endpoint story was not found, and proxying Antigravity *subscription* OAuth is what triggers bans.

### Cited Findings
- **Claude Code:** `ANTHROPIC_BASE_URL` redirects requests, and the endpoint must speak Anthropic Messages. OpenAI-only providers need a translating proxy. The URL is given without `/v1` (Claude Code appends `/v1/messages`). The env var is read only at process start. — [api.foo docs](https://api.foo/docs/en/claude-code-custom-api/); [fazm.ai](https://fazm.ai/t/claude-code-custom-api-base-url-anthropic-base-url) (secondary)
- **Codex CLI:** custom provider in `~/.codex/config.toml` under `[model_providers.<id>]` with `base_url` (including `/v1`), `env_key`, and `requires_openai_auth=false`. Reserved ids are openai/ollama/lmstudio. **Chat Completions support was removed (reported Feb 2026)**, so the gateway must expose `/v1/responses`. Run `/logout` first, because Codex prefers OAuth. Project-local config may be ignored (sources conflict). — [Stanford UIT guide](https://uit.stanford.edu/service/ai-api-gateway/userguide/openai-codex-cli-setup); [danielvaughan guide](https://codex.danielvaughan.com/2026/04/23/codex-cli-custom-model-providers-configuration-guide/); [LiteLLM Codex docs](https://docs.litellm.ai/docs/proxy/client_setup/codex_cli.md)
- **Gemini CLI:** `GOOGLE_GEMINI_BASE_URL` + `GEMINI_API_KEY`. — [getmaxim.ai](https://www.getmaxim.ai/articles/how-to-route-gemini-cli-and-codex-through-an-ai-gateway/) (vendor blog)
- **OpenCode:** `opencode.json` provider entry with `npm: "@ai-sdk/openai-compatible"`, `options.baseURL`, and a `models` map. Project config overrides `~/.config/opencode/opencode.json`. Credentials go in `~/.local/share/opencode/auth.json` via `/connect`. There is a reported issue where baseURL is not forwarded. — [OpenCode providers docs](https://opencode.ai/en/docs/providers); [computingforgeeks](https://computingforgeeks.com/opencode-cli-openai-compatible-api/)
- **goose:** `GOOSE_PROVIDER=openai`, `OPENAI_HOST` (origin only, no `/v1`) and `OPENAI_BASE_PATH` (e.g. `v1/chat/completions`). A wrong base path gives a 404. — [Opper goose docs](https://docs.opper.ai/integrations/coding-agents/goose); [Zuplo](https://zuplo.com/docs/ai-gateway/integrations/goose.md)
- **Aider:** `--openai-api-base` / `OPENAI_API_BASE` (with `/v1`) + `OPENAI_API_KEY`. The model name must use the `openai/` prefix. — [Aider OpenAI-compat docs](https://aider.chat/docs/llms/openai-compat.html)
- OmniRoute ships `omniroute configure codex` and a Claude Code configuration guide. — [OmniRoute README](https://github.com/diegosouzapw/OmniRoute)

### Inferences
- From the Windows laptop, point every CLI at OmniRoute over Tailscale (e.g. `http://<vm-tailnet-name>:20128/v1`), and give each CLI its own scoped OmniRoute key tied to the **public-lane combo by default**. Coding context often contains secrets and personal paths, so either redact first or use the private-lane combo for real repos.
- Before relying on Codex CLI, confirm that OmniRoute exposes `/v1/responses` and that Claude Code works against OmniRoute's Anthropic-format endpoint.

### Gaps
- Antigravity CLI (`agy`) support for a custom base URL or API key is unknown.
- Official Anthropic and OpenAI docs pages for these settings were not fetched; the details come from third-party guides.
- Whether OmniRoute implements `/v1/responses` and `/v1/messages` fully was not verified.
