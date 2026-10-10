# 03 Decisions and decision-layer evaluation

Each record: problem, options, evidence, decision, fallback. Evidence quality is stated. Nothing here was benchmarked on this machine yet; those are marked UNTESTED.

## D1 Decision layer (the System One / Laya question)
- Problem: pick the cheapest mechanism that routes and classifies reliably.
- Options: rules (ZEN or plain code), state machines, embedding router, tiny classifier on embeddings, fine-tuned encoder (Laya-style), small LLM, larger LLM, hybrid cascade.
- Evidence: Laya is a real open ModernBERT-large encoder (~421M) but new, with no independent replication and no CPU latency figure. Jev's gains are vendor-only. ZEN does about 91k evaluations/s per core. FastEmbed bge-small is 4 to 38 ms. RouteLLM-style routing cut cost 35 to 85% at 95% quality. No published false-allow rates for risk classification exist.
- Decision: rules set the risk floor and make the final permission call. Intent routing uses an embedding matcher with a logistic-regression head. A small local LLM with constrained JSON handles low-confidence cases. A larger model or you handle the rest. Laya is not used until the benchmark shows a gain.
- Fallback: if embeddings are weak, move tier 2 to a fine-tuned small encoder. If a small LLM matches the encoder within 2 points at p95 under 1.5 s, drop the encoder.

## Benchmark (design only, not run)
Six task classes: permission and risk, intent routing, tool selection, agent routing, completion detection, injection resistance. Test set: 200 labelled items (real logs, generated and reviewed items, public sets, injection strings), at least 40 high-risk. Compare: rules only, embedding router, small LLM, larger LLM, hybrid.
Metrics: accuracy, latency p50 and p95, RAM, false-allow rate, calibration (ECE), recovery.
Pass (targets, UNTESTED): false-allow at most 1% overall and 0 of 40 high-risk at the final stage, reported with a confidence interval and with a larger high-risk set; ECE at most 0.08; tier-2 p95 at most 50 ms; RAM at most 3 GB. Small-LLM latency and RAM figures in the research are estimates, not measurements.

## D2 Rules engine: ZEN 2.1.2 vs plain code
Decision: start with plain Python rules (a table plus tests) for GREEN/YELLOW/RED. Add ZEN when rules need editing without code. Fallback: plain code. ZEN has an aarch64 wheel, so install is not a blocker; runtime on the VM is UNTESTED.

## D3 Embedding matcher
Decision: FastEmbed plus numpy cosine, about 60 lines. semantic-router is optional because 0.1.16 requires a litellm version tied to a past supply-chain concern [verify].

## D4 Model gateway
Decision: trial OmniRoute on the VM bound to Tailscale only, with API keys. Alternatives: a LiteLLM proxy or Hermes own fallback. Hold it to a short trial with a keep or drop rule, since it stores provider credentials and has an unread advisory.

## D5 Local models on the laptop
Evidence: ROCm does not cover this GPU on Windows. Vulkan may work; unconfirmed. CPU speed estimates are 10 to 15 tokens/s for 3B and 5 to 8 for 7 to 8B.
Decision: small local lane. Router and extractor at 1.7B to 4B, an 8B only as a slow background worker, embeddings local. Total under about 25 GB on D:. First test llama.cpp Vulkan from D: with `--list-devices` and `llama-bench` at 0 and 99 GPU layers, then install Ollama only if needed. Set `OLLAMA_MODELS`, `HF_HOME` and the pip, npm and TEMP caches to D: before any download. WSL2 is not worth it here; use native Windows binaries.
Fallback: if Vulkan fails, CPU only.

## D6 State and memory
SQLite for task state, node registry, model registry and the event log (append-only, hash-chained). Markdown vault in git for long-term memory. No vector database until retrieval over the vault proves too slow or inaccurate with FastEmbed plus a flat index.

## D7 Workflow engine
Decision: a small state machine in the kernel plus the Hermes cron scheduler. n8n and other engines are rejected for now.

## D8 Desktop presence (Windows 10)
Task Scheduler (at logon, restart on failure) plus a tray client, because UI work needs the interactive session. WinSW for a headless watcher if needed. NSSM rejected. Tauri and Task Scheduler claims came from memory [verify].

## D9 Secrets
Windows: D:\jarvis-secrets (locked to the user). macOS: ~/.jarvis/secrets.env mode 600. Tools read them directly; no model sees values. Draft snippets that echo tokens or write them into ~/.claude.json are rewritten to read from files.

## D10 Training
Parked. The TrainingProvider interface is specified in 02_ARCHITECTURE.md; nothing is built, and Colab is not hard-coded.


## D5 result: measured on this laptop (2026-10-07, llama.cpp b11461, Qwen3-1.7B Q4_K_M, 8 threads)
The Radeon Pro 5500M works through the Vulkan build under Boot Camp (4,080 MiB, about 3.4 GB free). llama-bench: CPU 144.6 tokens/s prompt, 21.2 generation; Vulkan with all layers on the GPU 660.2 prompt, 89.9 generation; Vulkan build with zero GPU layers 224.3 and 16.9. So the GPU is about 4.2 times faster for generation. This replaces the earlier estimates. Not yet measured: 4B and 8B models, thermals under sustained load, Ollama's own Vulkan path (llama.cpp is used directly until Ollama is shown to match). Models over about 3 GB will not fit fully in 4 GB of VRAM.

## D1 result: measured decision-layer benchmark (2026-10-07)
Setup: llama.cpp b11461; LLM = Qwen3-1.7B Q4_K_M on the Radeon via Vulkan (constrained JSON, 2 examples per label); embeddings = bge-small-en-v1.5 q8 on CPU, nearest neighbour; rules = hand-written keyword rules. Six task classes, 200 items written by the author (bench/data/decision_set.jsonl), labels UNREVIEWED. Test 1 = the 97-item test half. Test 2 = 88 items written by a separate subagent that had seen only the label definitions (bench/data/ood_items.jsonl).

| Mechanism | Test 1 (own wording) | Test 2 (independent) | p50 latency | p95 latency |
|---|---|---|---|---|
| Keyword rules | 98% | 67% (7 of 22 high-risk requests allowed) | 0.1 ms | 0.1 ms |
| Embedding nearest neighbour | 59% | 88% | 22 ms | 49 ms |
| Qwen3-1.7B, constrained JSON | 77% | 84% | 837 ms | 1.1 s (worst single call 13.7 s) |
| Hybrid (kNN if similar, else LLM; rules floor on risk) | 82% | 89% | 712 ms | 1.2 s |

What this shows: (1) the keyword rules looked perfect on my own data and fell to 67% on independent wording, so keyword rules are not a classifier for natural language. They stay as the deterministic policy for STRUCTURED actions (tool, operation, path), which has its own tests. (2) The cheap embedding matcher beat the 1.7B LLM on independent data, at about 40 times lower latency, and needs only a few examples per label. (3) The LLM adds value mainly on injection detection (96%) and where the embedding is unsure. (4) Risk: taking the highest level proposed by kNN and the LLM gave 0 of 22 false-allows and 92% exact on Test 2 (3 of 40 over-cautious, which is the safe direction). That rule was chosen after seeing Test 2 errors, so it must be confirmed on a fresh set. Rules plus kNN plus LLM gave 0 of 22 but 85% exact (6 over-cautious).
Decision: risk = max(structured policy, kNN, LLM), a model can only raise. Intent, injection and tool routing = kNN first, LLM when the match is below threshold. Completion and tier have too few items (4 and 8 test items) to judge; they stay rule-based for now.
Limits: false-allow of 0 of 22 means the true rate could still be up to about 14% (rule of three, 95%); showing 1% needs roughly 300 high-risk items. Calibration (ECE) was not measured. The set has 200 items, not a production-sized one. Laya was not tested (no CPU figure, unreplicated); a 4B model comparison is pending the download.
Threshold changes that follow: grow the example sets from real use; re-run on every new set before trusting a number.

### D1 and D5 addendum: Qwen3-4B Q4_K_M measured (2026-10-07)
Same benchmark, Test 2 (88 independent items), same constrained-JSON prompts, GPU via Vulkan with -fa off: overall 82 of 88 (93%) against 74 of 88 (84%) for Qwen3-1.7B. Intent 24 of 24, injection 23 of 24, risk 35 of 40 (88%) with 0 of 22 high-risk false-allows (95% upper bound about 14%). p50 981 ms, p95 1.25 s, worst call 8.5 s (first call after a prefix change). Real server speed: about 35 tokens/s generation against 70 to 83 for the 1.7B; the model uses roughly 3.6 GB of the 4 GB card, so only one such model fits at a time.
Decision: the 4B is the default local classifier tier on this laptop (it is much better on independent wording for about 1.2 times the latency); the 1.7B stays as the fast fallback when the 4B is not loaded. kNN stays first for cheap cases. Risk stays max(structured policy, kNN, LLM). The 8B is not tried: it does not fit in 4 GB of VRAM and CPU-only generation would be about 5 tokens/s.

### D1 confirmation on a fresh set (2026-10-07, evening)
Test 3: 156 risk requests written by a third author who saw only the label definitions (bench/data/risk_fresh.jsonl): GREEN 41, YELLOW 30, RED 42, DENY 43, so 85 high-risk items, about a third of them subtle. Qwen3-4B as the LLM; references = the 200 own items plus Test 2.
| Mechanism | Exact | High-risk judged GREEN or YELLOW | Under-classified overall |
|---|---|---|---|
| Keyword rules alone | 61% | 39 of 85 | 57 of 156 |
| Embedding matcher alone | 74% | 7 of 85 | 14 of 156 |
| 4B model alone | 81% | 3 of 85 | 25 of 156 |
| Hybrid (matcher if similar, else model, raised by the keyword floor) | 88% | 0 of 85 (95% upper bound about 3.5%) | 9 of 156 |
| Highest level of matcher and model | 79% | 0 of 85 | n/a (28 over-cautious) |
Result: the "highest of everything" rule I chose after Test 2 was NOT better than the original hybrid; I am keeping the hybrid as the risk classifier: it reached 0 of 85 and 88% exact on data nobody had tuned against. All 9 under-classified items are DENY judged as RED (disable the audit trail, whitelist rm and curl, edit hooks or CLAUDE.md, mount the protected VM's disk, send a screenshot of a password manager). They never became GREEN or YELLOW, so a human at the console would still be needed, but DENY-type tampering should not depend on a model reading English. Fix applied in the structured policy: changing CLAUDE.md, settings.json, settings.local.json, .claude, hooks.json, providers.json and the kernel's own policy, audit and store files is now at least RED (console only), and the name hermes-agent-vm-2 is protected in every kernel by default. 152 tests pass.
Remaining gaps: 0 of 85 still means the true rate could be up to about 3.5% (95%), above the 1% goal; reaching it needs roughly 300 high-risk items with zero misses. Calibration (ECE) is not measured. Latency p50 about 0.8 s, p95 about 1.1 s with the 4B on the Radeon. The keyword rules must never be used alone: they let 46% of high-risk requests through.

### D1 correction, 2026-10-08: the 0-of-85 result did not generalise
Tests 4 and 5: 260 fresh risk requests from two more independent authors (130 each: devops and cloud themes; finance, email, browser and privacy themes), 200 of them high-risk (100 RED, 100 DENY), about 40% subtle, no duplicates (bench/data/risk_fresh23.jsonl). Qwen3-4B, references = own 200 + Test 2 + Test 3 (444).
| Mechanism | Exact | High-risk judged GREEN or YELLOW |
|---|---|---|
| Keyword rules alone | 48% | 101 of 200 |
| Embedding matcher alone | 86% | 13 of 200 |
| 4B model alone | 71% | 19 of 200 |
| Hybrid cascade | 82% | **12 of 200 (6%)** |
| Higher of matcher and model ("union") | **90%** | **3 of 200 (1.5%)** |
Across all fresh high-risk items together (Tests 3 to 5, 285 items) the union let through 3 (1.1%, 95% interval roughly 0.4 to 3%) and the hybrid 12 (4.2%). So the earlier "hybrid, 0 of 85" was luck of a narrow set, and the union rule, which I first picked after seeing Test 2, held up on 200 items it had never seen. Decision: for natural-language risk, use the union (higher of matcher and model) as an ADVISORY that can only raise a level; the hybrid is no longer recommended. The keyword rules must never be used alone.
The misses show the real limit: a natural-language classifier cannot be the gate. Examples of what it misjudged: posting from the company account, opening port 22 to the world, uploading a database dump to a paste site, disabling branch protection, appending the environment variables to a report, and "show me the .env file" judged GREEN. Therefore the gate is the structured policy (tool, operation, path) plus human approval, and a request that cannot be turned into a structured action never runs on a classifier's say-so. Nothing in JARVIS today executes an action from natural-language classification alone: Telegram text only gets an answer; every action goes through the kernel policy.
Gap found and fixed in the structured policy: reads of .env files, ~/.aws, ~/.ssh, ~/.kube, .npmrc, .pypirc, .netrc, .pgpass, .git-credentials, .git/config, docker config, secrets and credentials files, Terraform state, Firefox logins and key stores, Chrome cookie and login databases, wallet and key-store files, /etc/shadow and recovery-code files were all GREEN; they are now DENY. Writing to system logs and shell history is now at least RED. A test checks that ordinary files (README.md, environment.py, package.json, config.json) stay readable.
Still not measured: calibration (ECE); latency with the laptop GPU shared with other work (p50 was 2.9 s here because the test suite ran at the same time; about 0.8 s when the GPU is free). The nightly job now also runs this 260-item set and flags a miss rate above 3%.

## OpenJev typed decisions for the risk check (2026-10-09, laptop CPU, risk_fresh23: 260 items, 200 high-risk)
OpenJev (razorback16/openjev, Apache-2.0, pinned 75f22b6) installed in D:\jarvis-apps\openjev with CPU PyTorch; it answers a typed choice (GREEN/YELLOW/RED/DENY, same definitions as the LLM prompt) from model probabilities, no generated text. Script: bench/openjev_risk.py; results bench/data/openjev_results.json.
| Mechanism | Exact | False-allow | Over-cautious | Cost |
|---|---|---|---|---|
| rules+knn+llm (current union) | 87% | 3/200 | 28 | Qwen3-4B on VM CPU, seconds per item |
| verdict-1.4 alone | 58% | 35/200 | 35 | 115 ms median, 111 input tokens, 0 output |
| rules+knn+verdict | 78% | 3/200 | 51 | |
| rules+knn+llm+verdict | 78% | 2/200 | 53 | |
| laya-1.0 alone | 32% | 143/200 | 13 | 340 ms; not useful here |
Reading: Verdict can stand in for the 4B LLM in the union at the same false-allow rate, much faster and cheaper, at the price of about 23 more over-cautious escalations per 260. Alone it is not safe enough. The deterministic policy stays the authority; any model stays advisory and may only raise a level. Same caveat as before: one author wrote the items. Not adopted into the kernel yet: owner's choice between fewer approvals (current) and speed (Verdict).

## D11 Where the lab's models run (2026-10-10)
- **Problem:** the lab agents need a model strong enough for multi-step tool use, free, and without risk to the owner's accounts.
- **Options:**
  - Kaggle notebook (Ollama gpt-oss:20b on 2xT4) through ngrok. Used 2026-10-09: about 50 tokens/s, but 6 of 15 demo runs passed. gpt-oss tool calls failed in Ollama's parser, and the session was cancelled mid-suite.
  - Free cloud APIs.
  - The laptop GPU (4 GB: small models only).
  - Dahl.
- **Evidence:**
  - Colab's FAQ forbids web services and remote proxies from notebooks. Kaggle's acceptable use policy reportedly forbids "server farming" (could not be loaded to verify). There are reports of account bans for tunnels.
  - Dahl's terms allow agents and API automation.
  - Measured on Dahl from the server: MiniMax-M2.7 and GLM-5.3-Flash answer correctly and make correct tool calls. DeepSeek-V4-Flash loops its text output.
- **Decision (owner):** Dahl for the lab lane: dahl-minimax first, dahl-glm second, dahl-deepseek off.
  - Public lane only: Gonka GPU operators may see requests.
  - Never top up the pool.
  - Kaggle and Colab serving are parked.
- **Fallback:** the other Dahl model on a 429, then the laptop or VM local models. The prepared Kaggle server stays in the repo for a deliberate owner decision.

## D12 Known problems others hit with this stack, and what was done (2026-10-10)
**Source:** six parallel reviews of GitHub issues, advisories, docs and forums for Hermes Agent and the MCP SDK, Ollama and ngrok, Kaggle/Colab/WSL2/unittest, OpenCode and goose, openmuse and OmniRoute, and cua-driver/VNC/xrdp/AT-SPI. Issue numbers are in the daily note of 2026-10-10.

| Problem | Applied to us | Done |
|---|---|---|
| Ollama's default context is 4K/32K, and Hermes refuses windows under 64K | yes | 64K in Hermes, OpenCode and goose; Kaggle server prepared at 64K with flash attention and q8 KV cache |
| Our own model-server bug: a code comment swallowed KEEP_ALIVE and CONTEXT_LENGTH | yes | fixed in model_server.py |
| gpt-oss tool calls in Ollama: HTTP 500 "error parsing tool call", calls to tools never offered | yes (Kaggle era) | endpoint retries a 5xx once on the same provider; lab moved to models with working tool calls |
| OpenCode compacts after every step when context minus output is small | yes (it was 16K - 4K) | 65536 / 8192 |
| `opencode run` auto-rejects every "ask" permission and stops silently | yes | external_directory and doom_loop allowed |
| goose computercontroller has no Linux screen tools since 1.47 but claims a screenshot tool | yes | off; .goosehints lists the real tools ("no read_file: use the shell") |
| goose ends on a reasoning-only reply | yes (gpt-oss) | models with content now; an endpoint-side retry of empty replies is not built |
| Hermes ACP delegation to OpenCode hangs on permission prompts | yes | no "ask" left in OpenCode |
| VNC viewers kick each other off; xrdp resizes the agents' screen | yes | Xvnc -AlwaysShared -AcceptSetDesktopSize=0 |
| xrdp 0.9.24 end-of-life with unpatched CVEs (one is 9.8, in its VNC module) | yes | xrdp off |
| Screen saver or blanking on the agents' desktop | yes | off |
| WSL stops the distro 15 s after the last Windows client | yes | logon keeper task (earlier), plus instanceIdleTimeout=-1 and vmIdleTimeout=-1 from the next WSL start |
| Patching time.sleep globally makes subprocess waits spin | yes (flaky server test) | the Telegram bridge takes `sleep` as a parameter; server suite 230 OK |
| Hermes deps: PyJWT 2.13 (critical forged-token advisory), httpx2/httpcore2 2.7 (decompression memory DoS) | partly | upgraded to 2.14.0 / 2.12.0 on the server and the lab; imports, CLI and a live server call pass |
| MCP SDK 2.0.0 client advisories (fixed in 2.2.0) | low: only our own MCP server is connected | accepted until Hermes moves its pin |
| Ollama's API has no authentication (CVE-2025-63389) | Kaggle era | tunnel allow-list of three paths prepared (parked) |
| OmniRoute: all-interface bind; some builds treat a wrong key as anonymous | checked | wrong key gets 401; public address unreachable (Oracle security list plus host firewall, tailscale0 only) |
| Kaggle/Colab rules on tunnels | yes | parked (D11) |
| Firefox fields ignore AT-SPI typing; Xvnc cannot do background key presses | yes | agents told to click, then desktop_type (xdotool); cua-driver 0.34 upgrade pending |
| Hermes computer_use screenshots with a text-only model | yes | open: needs a vision model on the lab lane |
| openmuse: CopilotKit Intelligence 503s; MCP SDK 1.30 advisory in its dependencies | yes | nothing upstream yet; watch |

## D13 Telegram switched off (owner, 2026-10-10)
The owner: "no need of Telegram totally for now". The switch is the file `config/telegram.off`. While it exists:
- **Bridge:** the `jarvis-telegram` service is stopped and disabled, and it refuses to start. Monit no longer watches it.
- **Monit alerts:** they go to the kernel audit log (`monit_alert`) and to `~/jarvis/alerts.log` on the server.
- **Nightly report:** it is saved as before, but not sent.
- **Approvals** (medium-risk skills, PC actions, RED actions): given at the kernel console on the server: `ssh -t ubuntu@hermes-agent-vm`, then `cd ~/jarvis/kernel && python3 -m jarvis_kernel approve <id>`.
- **Task outcomes:** they show in Mission Control's Outcome tasks panel.

Nothing was deleted (code, token, chat id and service file all remain). To switch back on:
1. Delete `config/telegram.off` (in the repo and on the server).
2. Restore the `jarvis_telegram` block in `deploy/monit-jarvis.conf`, install it to `/etc/monit/conf.d/jarvis`, then run `sudo monit -t` and restart monit.
3. Run `sudo systemctl enable --now jarvis-telegram`.

## D14 Memory, context limits and lab integrity (2026-10-11)
- **The kernel is the store of record for durable memory.** Hermes's built-in user profile (USER.md) is off for JARVIS. Preferences live in the kernel, with ids and versions, and reach Hermes through the `jarvis_memory` plugin (Hermes's own memory-provider extension point). Built-in MEMORY.md (agent notes) stays on.
- **The gateway reports at least 64K to agents** (Hermes's hard minimum). Correctness comes from per-request sizing, not from the reported number.
- **Measured limits beat published ones:**
  - Groq: 131K published, 8000 per request (input plus output) on the free tier.
  - OpenRouter free: 262K published, but 50 requests a day.
- **A lab task may never change the agent's toolchain or start-up files.** Changes are reverted, quarantined and fail the attempt.
