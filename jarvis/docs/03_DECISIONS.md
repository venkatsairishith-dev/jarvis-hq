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
- **The kernel is the store of record for durable memory.** Hermes's built-in user profile (USER.md) is off for JARVIS. Preferences live in the kernel, with ids and versions, and reach Hermes through the `jarvis_memory` plugin (Hermes's own memory-provider extension point). Built-in MEMORY.md is off too (2026-10-11): with two memory tools side by side the model wrote a second copy and 'cleaned up' the corrected entry under a new id; with one (the kernel's), a correction is a single versioned update. Agent notes use the kernel's `fact` / `instruction` kinds.
- **The gateway reports at least 64K to agents** (Hermes's hard minimum). Correctness comes from per-request sizing, not from the reported number.
- **Measured limits beat published ones:**
  - Groq: 131K published, 8000 per request (input plus output) on the free tier.
  - OpenRouter free: 262K published, but 50 requests a day.
- **A lab task may never change the agent's toolchain or start-up files.** Changes are reverted, quarantined and fail the attempt.

## D15 The owner's control plane, the console and the Agent OS UI (2026-10-11)
Research behind this: `docs/research/01` to `07` (each report lists its own evidence and what it could not verify).
- **The owner gets an HTTP control plane in the kernel** (`/v1/jarvis/owner/*`, token lane `owner`, `kernel/jarvis_kernel/owner.py`). Before it, with Telegram off, nothing let the owner send a command, approve, pause, cancel or stop except an SSH console, and the loops that failed stuck lab jobs and told JARVIS a task had ended lived inside the Telegram bridge. The same module now runs those loops (sweeper tick every 30 s).
- **Approvals over HTTP are YELLOW only, need the id typed again and an owner PIN** (set at the kernel console with `set-approval-pin`; with no PIN the route refuses). RED approvals and lifting the kill switch stay at the console. Deny, cancel, pause, stop-all and kill need no PIN (safe direction). A spoken "yes" is never an approval.
- **The console is a local process, not a web page with a token** (`console/`, 127.0.0.1:8799, Host and Origin checked). It holds the owner token, serves the React UI (`ui/`), runs voice on this CPU, previews files without executing them and proxies the Agent PC controls.
- **Voice is local.** Parakeet TDT v3 int8 (English), AI4Bharat IndicConformer int8 (Hindi, Telugu), Silero VAD, Kokoro fp32 (English TTS), Piper in a separate process (Hindi, Telugu TTS). One speech model is resident at a time and unloaded after 5 minutes idle. Measured on real hardware in `docs/research/02` and the console tests; Hindi and Telugu were tested on synthetic speech only.
- **Wake word is decided but not enabled.** openWakeWord's pretrained models are CC BY-NC-SA 4.0 and have no published test set; push to talk and typing are the supported paths.
- **Laya is not used and was not fine-tuned.** On the held-out intent set (62 items, 13 categories) it scored 0.39 against 0.85 for Qwen3-4B zero-shot, 0.79 for hand rules (optimistic, written with the data in view) and 0.65 for embeddings plus logistic regression; it missed 3 of 3 requests that must be refused. Fine-tuning a 421M model on 114 labelled examples would not give a result that can be trusted to prove "no more unsafe selections" with only 3 blocked test items. Revisit when there are real, owner-corrected examples. The local model's guess is advisory metadata; the kernel decides.
- **Windows control stays inside the node's own adapter** (fixed job kinds, kernel approval first, read-back after the action) with three new read-only kinds: `ui_windows`, `ui_snapshot`, `screenshot`. UFO, Browser Use and OpenHands were not adopted as runtimes: each brings its own loop and a model-judged confirmation, which would be a second permission authority.
- **Automations are kernel records** (`automations`, `automation_runs`), fired by a pure `tick(now)` with `UNIQUE(automation_id, run_key)` per slot. Hermes cron was not used for definitions: its jobs sit outside kernel authorization and audit and have no outcome verification. File-arrival triggers are detected by the laptop node on its own heartbeat from an allow-list (`config/watches.json`).
- **Lessons are candidates until a gate and the owner accept them.** Only a task that failed, was repaired and passed independent verification yields one; the gate checks origin, secrets, authority claims and duplicates. This is an automated gate over recorded facts, not a replay of the failed scenario.
- **Memory forget is real erasure now** (text and history overwritten; a tombstone stays), and task updates, memory writes and audit rows are transactional with compare-and-set (`SafeDB.tx()`).
- **"DHAI" is the Dahl provider (owner-confirmed 2026-10-11, naming `dahl-credentials.txt`)**

## D16 Everyday-use hardening, found by using it live (2026-10-11)
Each item below was a real fault or gap seen against the live system, not a theoretical one. Evidence is in `docs/research/07` section 6.
- **The owner is told the truth about what ran.** The owner's agent keeps a one-day conversation (so "open it" works), and on a repeat request it answered "already open" from memory without running anything. The kernel now (a) prefixes every owner command with a one-line rule against answering from remembered state, and (b) appends its own record of the jobs and tasks that ran during the turn, or, for a request that sounds like an action with nothing run, says so. The model can still ignore (a); (b) cannot be ignored.
- **Approvals show the whole action.** The pending card carries the exact tool, operation, target and arguments (up to 2,000 characters), why it asks, and the digest it is bound to; the history shows the kernel's outcome (used once, denied, expired, job and result). Approval stays YELLOW-only, with the typed id and the owner's PIN. The agent-facing "approve on Telegram" wording is gone.
- **A dead or resting model provider is reported at once.** When every provider of the owner's lane is cooling after a free-tier limit, a command is refused with "back around HH:MM, nothing was sent" instead of sitting for the agent's own ten-minute back-off. A full conversation (Hermes answers it with an ordinary reply) starts a fresh session and resends once. "Stop waiting" hands the busy gate back and rotates the conversation, so the next command is not refused for up to fifteen minutes.
- **Measured token usage works on streams.** Providers nest objects inside `usage`; the first parser only matched flat ones and recorded nothing for every streamed call. It now decodes the JSON. The Models screen shows measured (not estimated) tokens per provider.
- **The laptop node looks for work every 3 s, reports every 15 s.** A spoken request used to wait up to a whole report interval before the laptop saw it. Same request, one run each: 54 s before, 21 s after (the rest is two model round trips, which vary).
- **The console is one process.** On Windows `SO_REUSEADDR` let a second console bind the same port and both lived, each holding models. Exclusive binding makes the second start fail at once. A dead local model server costs two seconds per call on Windows (refused connections are retried), so it is left alone for 30 s after a failure.
- **Hindi and Telugu speech stays resident.** Piper (GPL) runs as one long-lived worker process: 5.9 to 8.4 s per sentence became 0.16 to 0.22 s warm. Two voices at most, unloaded after 5 minutes idle.
- **Wake word stays off, measured.** openWakeWord `hey_jarvis` is licensed CC BY-NC-SA 4.0 (personal use is within it, redistribution is not), runs at 3 ms per 80 ms frame and 40 MB, and detected 96 of 96 synthetic "Hey Jarvis" clips. It also fired on 10 of 128 negative clips, every one of which contained the word "Jarvis" (including JARVIS's own spoken replies), and it hears English only. Without echo cancellation JARVIS would wake itself. No real-microphone false-activation figure exists. Push to talk and typing remain the supported paths; `bench/wakeword/` reproduces the numbers.
- **Not changed, on purpose:** Laya was not fine-tuned (no new evidence); the 100M-token allowance is documented by Dahl as a new-account gift to a pool that must be allocated to a key (the key JARVIS holds showed 6,720,752 available on 2026-10-11 through the documented `GET /tokens/current`); it is public lane only, so it cannot serve the private lane (see docs/research/08); Telegram stays off.

## D17 Connectors: reuse the servers, keep the authority (2026-10-11)
- **The kernel is the only authority over connectors.** A connector is a definition plus the owner's per-tool decisions in the kernel; agents have no path to a server. Risk classes are assigned by the owner and enforced by policy; nothing a server says about itself (descriptions, `readOnlyHint`) can lower one. Unknown, unclassified, changed or disabled means refused. destructive and credential tools can be recorded and never enabled.
- **Reuse, with a thin client.** The MCP servers are existing maintained projects (the official time server, GitHub's, Google Workspace's, Notion's, DeepWiki's hosted one). The client that speaks to them is about 400 lines of standard library: the official Python SDK would bring 13 or more dependencies into a kernel that has none, and `hoophq/mcpproxy` would be a second approval engine outside the kernel that also refuses the current protocol revision. Its ideas were adopted (declaration fingerprints, server requests refused, budgets, truncation, structured audit). Integration platforms (Nango, Composio) were not adopted: they hold the owner's credentials.
- **A command line is accepted in one place.** The console's local allowlist, from the owner's browser. The kernel never stores or sends one; a job names a connector. Programs that are shells, scripts or interpreters given code are refused.
- **Local servers run on the laptop node, remote ones in the kernel.** Credentials stay on the machine that uses them, as secret-file references; values are never returned, stored or shown, and a server that echoes its own credential gets it redacted.
- **A real defect found by the first tests** (and fixed): the audit chain recorded connector arguments (truncated) in the generic action entry; it now records their keys and size only, while the approval prompt shows them in full.
- **Not done, on purpose:** OAuth flows (a connector needing OAuth is a template until the owner sets it up), UI entry of credentials, automatic discovery of servers, and any connector for the public lane that carries private data.

## D19 Local specialist models, and a privacy gateway in front of Dahl (owner, 2026-10-15)
- **Owner direction.** Small or "large but only a tiny part active" models on this laptop share tasks. MiniMax or DeepSeek on Dahl do the reasoning, but never see private values: "my password = X" reaches them as `[PASSWORD_1]`, while the local models see X.
- **Privacy gateway** (`jarvis/privacy/`, 127.0.0.1:48950).
  - What it is: an OpenAI-compatible proxy built on Microsoft Presidio (MIT) and spaCy `en_core_web_sm`, with recognisers added for passwords/PINs/OTPs after a cue word, API keys and tokens, JWTs, private keys, Indian mobile numbers, Aadhaar and PAN. Names and places are left alone by default (`JARVIS_PRIVACY_NAMES=1` turns them on).
  - What it does: it redacts every chat request, then restores the placeholders in the reply, including streamed replies and tool-call arguments. It keeps no values and logs only counts.
  - Wiring: all five N.E.K.O model URLs (conversation, summary, correction, emotion, agent) now point at it. Backup: `D:\jarvis-apps\tmp\core_config.before-privacy-gateway-20261015-230616.json`.
  - Measured live: Dahl received `[PASSWORD_1]` and `[EMAIL_ADDRESS_1]`, and N.E.K.O got the real e-mail back.
- **Lanes do not change.** Dahl stays public-lane only and still gets no private connector data. The gateway is a second layer for what the owner types, not permission to send private material.
- **Local models** (`jarvis/local_models/llama-swap.yaml`). llama.cpp b11669 runs them on the Vulkan GPU, behind llama-swap (MIT) at 127.0.0.1:8080. A model loads on first use and unloads after 5 idle minutes, and one specialist is on the GPU at a time.
  - Generalist and router: Ling-3.0-tiny Q4_K_M, a mixture-of-experts model with 7.9B parameters in total but only 1.3B active per token (MIT). 31 tok/s, with 16 layers' experts kept in system RAM.
  - Code: Qwen2.5-Coder-1.5B, 80 tok/s.
  - Maths: Qwen2.5-Math-1.5B, 96 tok/s.
  - For comparison, MiniMax on Dahl does about 11 tok/s.
- **Measured and rejected:**
  - SmolLM2-360M as the router: 203 tok/s, but only 5 of 12 requests routed correctly. Ling with a clearer prompt got 11 of 12.
  - Vulkan flash attention: it produced garbage after the first token on this RDNA1 GPU, so `-fa off`.
  - 4 server slots: about 30% slower than 1.
- **Coordination** (`plugin/plugins/local_team`, a N.E.K.O plugin, so the main agent sees it as tools).
  - `ask_local`: keyword rules choose the specialist, then Ling; the answer is produced on the PC.
  - `solve_with_team`: MiniMax plans through the gateway, streaming its reply because Cloudflare cuts a silent request at ~100 s (HTTP 524). The local specialist then writes the answer with the real values.
  - Safety rule, tested: the adviser call is refused unless the agent model URL is the gateway.
- **Not done, on purpose:**
  - "Approve actions" stays with the owner PIN in the JARVIS console; admin rights on the PC do not replace the kernel's approvals.
  - DeepSeek-V4-Flash stays off (it loops).

## D20 TypeSafe-Jev-style decision models (owner: "use similar models like TypeSafe Jev", 2026-10-16)
- **What Jev is.** TypeSafe AI's "System One" model. You send a state and typed questions (choice, score, true/false) and get structured answers with calibrated probabilities, in one pass and with no generated text. The owner already runs an open version, OpenJev "Verdict" (151M ModernBERT + GLiClass, Apache-2.0, github.com/Heman10x-NGU/Verdict-open-jev), in WSL on 127.0.0.1:8091 (`POST /v1/systemone`). It is used as a service, with no code copied.
- **Decision 1, routing (`local_team`).** Keyword rules go first. Next, Jev picks code, math or general when its top probability is at least 0.5. Anything else goes to Ling.
  - Jev alone: 10 of 12, about 150 ms, no GPU.
  - Every Jev choice at 0.5 or above was right; both misses were below 0.5.
- **Decision 2, does a task need MiniMax (`solve_with_team`, `use_adviser=auto`).** MiniMax is called only when Jev's probability that the task is "deep" is at least 0.77.
  - Tuned on 12 tasks, then 11 of 12 right on 12 fresh ones, about 110 ms each.
  - `always` and `never` override it.
- **Decision 3, is a value a secret (privacy gateway).** GLiNER-PII small (Knowledgator, the same family as Verdict's GLiClass head, Apache-2.0, int8 ONNX, 83 MB) proposes candidates sentence by sentence at 0.3. Jev keeps a candidate only if it is a secret rather than ordinary information; one call per sentence covers all its candidates. Pattern hits are kept without a check. Results on 12 secrets with no cue word and 12 ordinary messages, none of which were used for tuning (`jarvis/privacy/eval_pii.py`):

  | Setup | Secrets hidden | Ordinary messages flagged |
  |---|---|---|
  | Patterns only | 0 of 12 | 0 of 12 |
  | Patterns + GLiNER | 12 of 12 | 12 of 12 |
  | Patterns + GLiNER + Jev | 12 of 12 | 3 of 12 |

- **Rules around the model pass:**
  - It runs on user and tool messages only. The generated system prompt and earlier assistant replies get the patterns. Running the model on N.E.K.O's 5,900-character system prompt took 30 s on a fresh start; with this rule a turn costs 0.6-0.7 s.
  - Sentences are cached in memory.
  - Plain cue words ("passcode", "ATM pin") are never blanked, and a span grows over symbols glued to it.
- **If the Jev service is down.** Routing falls back to Ling. The adviser decision falls back to calling MiniMax. The gateway hides every GLiNER candidate (the safe side) and caches nothing from that time. `/health` reports `jev_unreachable`.
- **Measured and rejected.** GLiNER-PII base: on the same set, fewer secrets found at a usable false-alarm level than small plus Jev. Whole-message reading: surrounding text lowers GLiNER's scores, so it reads sentence by sentence.
- **Seen live.** MiniMax spent its whole 2,000-token budget deliberating whether a password-related task was allowed. The adviser prompt now says to think briefly and that placeholders are the owner's own data. A budget spent on reasoning is reported instead of silently ignored.

## D27 Neko's hands on the PC: the kernel decides, the node acts, screen text stays local (Neko phase 6, 2026-10-16)
- **Owner decision (2026-10-16):** use the owner's EXISTING Chrome profiles (no Neko profile); ordinary actions without
  repeated approval; safeguards stay for elevated, destructive, sensitive and consequential actions; never bypass UAC.
- **One gateway:** every action is a kernel job queued by the owner plane (`Owner.pc`), so the kernel's policy, audit,
  kill switch and approvals cover all of it. New kinds: `browser_ext`, `diag`, `repair`, `media_state`; UI ops focus, key,
  scroll, click_at.
- **`owner_direct`:** a flag only `Owner.pc` sets (nodes and agents cannot). It lowers ordinary ui/browser actions from
  approval to YELLOW_AUTO. The flag is part of the approval digest.
  - Raised back to approval: clicks or typing whose words could pay, send, delete, install, sign in or out, or accept;
    risky keys (alt+f4, ctrl+w, delete); Enter in a chat app, or in a browser field that is not a search box.
  - Every old refusal still wins: secrets, password managers, wallets and banks, internal addresses.
  - Agents and the public lane keep the approval floor. The existing tests stay as they were.
- **Approvals:** `needs_approval` returns an approval id. The owner approves with the PIN in the console, and the same
  request is sent again with that id; the approval is bound to that action and account, and used once. The desktop
  plugin waits for the approval and continues by itself.
- **Browser:** Microsoft's Playwright MCP 0.0.83 with `--extension --profile-dir-name`, plus a profile registry
  (aliases and masked accounts only).
  - The node calls only an allowlist of tools, and in pages only one fixed script (video state).
  - Storage, cookie and code tools are never enabled.
  - Without the extension, `open` uses `chrome --profile-directory`.
  - YouTube playback is verified by the video's position moving, plus Windows media sessions as a second witness.
  - If the page will not start playback, Neko opens the video and says "press play".
- **Repairs:** `edit_file` (approval; backup first; exact single match; verified on disk); `restore` (auto: the safe
  direction, itself undoable); `restart_app` (graceful only, never forced); `install` (winget, the normal UAC prompt).
  - Not offered: elevated commands, registry, services, uninstall, anything that disables a protection.
  - Any settings.json stays console-only (existing rule).
- **Privacy:** window and page text is written by the node to `outputs\neko-local`, and only a summary goes to the server.
  The plugin reads the file once and deletes it. The main agent gets a diagnosis, not the screen text, unless the owner
  sets `NEKO_SCREEN_TEXT_TO_MODEL=1`. Screenshots are never taken by this flow.
- **Cancel:** `job/cancel`. The node reports every 3 s while busy, and `runtime.cancel` kills the process tree of the
  job. The kill switch now also stops the job in hand.
- **Rejected:** a fake desktop interface; free-form PowerShell from a model; a dedicated browser profile (owner).

## D26 "Hey Neko": N.E.K.O's own keyword spotter, as a standalone local listener (Neko phase 5, 2026-10-16)
- **Runtime:** N.E.K.O's `SherpaWakeWordDetector` requires the patched sherpa-onnx `1.13.8+neko.kws2`. It was built with
  upstream's own script, using the VS 2022 Build Tools that were already installed, so nothing was installed on C:.
  - The fix was `UCRTContentRoot` set to the x86 Windows Kits for the build only; the registry was not changed.
  - Model: upstream's pinned zh-en 3M bundle, via its provisioning script, on D:.
- **Why standalone:** N.E.K.O's activation is tied to its browser voice session and voiceprint. `jarvis/wake/wake_listener.py`
  runs this flow: microphone → detector → Silero VAD → local speech recognition (48930) → widget `/api/task` → back to
  listening.
  - Cancel: say "cancel" or "never mind"; say "Hey Neko, stop" while a task runs; or POST /cancel.
  - Detections are ignored while Neko speaks.
  - No audio is saved, and the log holds no words.
- **Keywords:** phone tokens, configurable with `NEKO_WAKE_WORD_KEYWORDS_FILE`. "nee-ko" is kept because the English
  voice says it that way, so "Hey Nico" also wakes it.
- **Measured** (synthetic voices, threshold 0.25):
  - English: 15 of 15 clean clips, faster and slower; Hindi voice: 10 of 10; Telugu voice: weak.
  - No false alarm on ordinary sentences; only on the near-misses "Hey Nico" and "Hey Mekko".
  - 10 minutes of live microphone: 0 false wakes.
  - Speaker-to-microphone loopback at this volume, with speech about 10 dB over the noise: 2 of 3, then 0 of 3.
  - An automatic gain in front of the detector made it worse and was removed. The command, not the detector input, is
    gain-adjusted from the wake word's level.
- **Not yet proven:** the owner's own voice. That is the remaining test (operations).

## D25 Measured improvement: evaluations and feedback (Neko phase 7, part 1, 2026-10-16)
- **Scorecard:** `jarvis/evals/neko_evals.py` scores the small models and pipelines on fixed labelled sets and saves each run.
  - It compares each run with the last one; a drop exits 1.
  - It is the gate for any prompt, model or threshold change. Changes go through git, which gives versioning and rollback.
- **Feedback:** the Neko icon records 👍/👎 per deliverable in `feedback.jsonl`. It is read by people when tuning and never applied automatically.
- **Not done, on purpose:**
  - No self-modifying prompts.
  - No automatic memory writes from feedback.
  - No fine-tuning (D10; research evidence 06 says an encoder intent model comes first if routing ever falls short).

## D24 Hardware profiles, memory guard, provider fallback (Neko phase 4, 2026-10-16)
- **Hardware detector:** reads GPU memory from llama.cpp itself (WMI's AdapterRAM is 32-bit and wrong above 4 GB). It picks one of five profiles from the model matrix, and each profile has its own llama-swap config.
- **Memory guard:** in `local_team` rather than as a wrapper around llama-server. A wrapper process would orphan the server on Windows when llama-swap stops it.
- **Fallback:** inside the privacy gateway, so every provider gets only redacted text.
  - One attempt per provider per request, and only on rate limits, server errors or timeouts; a 429 cools that provider down. There are no loops.
  - Keys are separate files, one per provider; no rotating several keys for one provider (evidence 06: provider terms).
- **Measured:** Qwen3.5-4B on this GPU is correct but only 10.7 tok/s, so it is not adopted here (model matrix).

## D23 Neko icon and states from real signals (Neko phase 3, 2026-10-16)
- **Reused:** the existing widget server and Edge app-mode window. No new process, and no edit to N.E.K.O files.
- **Signals come from our own components:**
  - the privacy gateway sees every model call (thinking);
  - the local speech server makes every spoken reply and every transcription (speaking, listening);
  - the run files, kernel approvals and health probes give the rest.
- **Rule:** no state is shown without a signal behind it. "Speaking" is an estimate and the docs say so.
- **Actions from the icon:** limited to the deliverables folder (open, folder, regenerate) and to stopping the main agent's current task after its step. Approving stays in the console with the PIN.
- **Not done yet:**
  - The N.E.K.O page bridge (exact microphone and playback state).
  - A transparent overlay and global push-to-talk (Tauri shell, phase 8).

## D22 Research with checked citations (Neko phase 2, 2026-10-16)
- **Design:** one bounded `research` plugin, built from borrowed patterns, not an adopted repo (evidence 03: GPT-Researcher needs Python 3.12 and 80+ dependencies; open_deep_research is archived).
  - Search through N.E.K.O's existing `web_search` plugin.
  - Fetching guarded against SSRF.
  - BM25 passages.
  - A writer that must attach sources to every statement.
- **Claim checking, measured on 16 hand-made claims:**

  | Checker | Correct |
  |---|---|
  | Jev decision model | 8/16 (said "supported" to everything) |
  | Word overlap + numbers | 15/16 |
  | Local NLI cross-encoder (`nli-deberta-v3-xsmall` int8, onnxruntime only, ~0.1 s) | 16/16 |

  NLI is used, plus a check that every number in a statement appears in its source.
- **What happens to statements that fail:**
  - Unsupported, uncited or wrongly cited factual statements are never presented as facts; they move to a labelled "could not be confirmed" section.
  - Opinion and recommendation sections may stand uncited.
- **Privacy:** the question is redacted before any search, and placeholders are dropped from queries. The writing call goes only through the privacy gateway (public lane), using passages from public web pages.
- **Not done, on purpose:**
  - No paid search API until the owner chooses one (Tavily/Brave need keys or a card).
  - No automatic writing of research into memory (memory-poisoning risk; evidence 03).

## D21 Deliverables: decks and reports (Neko phase 1, 2026-10-16)
- **Research:** `jarvis/docs/neko/evidence/05-documents-pptx-pdf.md`. No maintained open generator writes native charts, notes and PowerPoint animations together.
- **Decks:**
  - python-pptx (MIT) writes the content: three themes, ten layouts, native charts and tables, notes, citation footnotes, a Sources slide.
  - OfficeCLI (Apache-2.0) adds transitions and entrance animations and validates the file against the OpenXML schema.
  - LibreOffice (MPL-2.0) renders for checks and runs the slideshow, since there is no PowerPoint on this PC.
- **Reports:** an HTML/CSS template printed by the installed Edge through Playwright: tagged PDF, bookmarks, links, page numbers. Charts come from matplotlib as SVG. Untrusted text is escaped, JavaScript is off, and network requests are blocked.
- **Every deliverable carries a `qa.json`:**
  - Text fit is measured with the real fonts.
  - The rendered pages are checked: no text outside the page, page count, links, fonts, tags.
  - Page images go in a contact sheet, so a person or a vision model can look at the result.
- **Narrow desktop capability:** the plugin opens, shows in folder or presents **only files it made** under the output root (path-checked, extension allowlist). It needs no kernel round trip; this equals YELLOW_AUTO for its own outputs.
- **Not done, on purpose:** morph-heavy designs (PowerPoint-only); Presenton and PPTAgent (telemetry by default, Linux only).

## D18 N.E.K.O is the base of the repository (2026-10-14)
- **What changed.** The repository root is now N.E.K.O (Project-N-E-K-O/N.E.K.O, Apache-2.0, upstream 3ffcf7b9). The whole previous JARVIS tree (the old `main` at ff470e5, now branch `jarvis-hq-legacy`, also tag `pre-neko-base`) sits unchanged under `jarvis/`. It is a subtree merge (commit b77ddc7b), so both histories are kept and nothing was rewritten. Branches: `main` is the N.E.K.O-based tree, `neko-main` is a pure copy of N.E.K.O's main (tracks `neko/main`), `jarvis-hq-legacy` is the previous JARVIS history. Files: 0 N.E.K.O files modified or deleted, 305 added (jarvis/ and the two plugins), all 275 previous JARVIS files present. The `neko` remote's push URL is disabled.
- **Why.** Owner direction ("use neko as our full base"), and the reuse-first rule: N.E.K.O already provides the character, voice and vision front end, five-layer memory, a plugin runtime and an agent server. JARVIS provides what N.E.K.O lacks: a deterministic authority, approvals, lanes, audit, connectors and lab agents. Rebuilding either would break the rule.
- **Authority does not move.** The kernel is still the only authority. N.E.K.O parts are untrusted callers: `jarvis_bridge` reaches JARVIS only through the console's owner plane (`POST /api/owner/command`), holds no credential, and returns task state only unless the owner sets `JARVIS_BRIDGE_SPEAK_REPLY=1`. Anything JARVIS does is still decided by the kernel's policy and approvals.
- **Upstream stays mergeable.** No N.E.K.O file was edited. The additions are `jarvis/`, `plugin/plugins/main_agent/` and `plugin/plugins/jarvis_bridge/`. Updating is `git fetch neko main` then `git merge neko/main`. Core edits are separate, individually justified phases.
- **JARVIS is stored byte-for-byte.** The root `.gitattributes` (`text=auto eol=lf`) would have rewritten 25 JARVIS files, including skill `PROVENANCE.json` files whose SHA-256 values and the deployment sync compare exact bytes. `jarvis/.gitattributes` (`* -text`) prevents that; all 275 files were checked identical to `main`.
- **Main agent.** N.E.K.O's executor is a single-channel router (its Planner pipeline was removed upstream). `main_agent` adds plan-then-run orchestration as a plugin: one plan made from the request only, closed live catalogue, steps depend only on earlier steps, bounded time and steps, desktop control opt-in. JARVIS and its lab agents are one sub-agent kind among several.
- **Not done, on purpose.** The live JARVIS services (console, `JarvisNode` task, server deploy scripts) still run from `D:\jarvis\hq` (branch `jarvis-hq-legacy`); moving them is a cutover with its own check. Nothing was pushed. N.E.K.O's own `docs/` is left as upstream; the five JARVIS docs live in `jarvis/docs/`.
## D28 Neko 9.5 phase: plans, missions, monitor, perception, controlled self-improvement (2026-10-16)
- **Plans are completed and checked by rules.**
  - The local decider plans; `decide.py` then:
    - adds what the owner said but the model left out (a typing step; an account check before acting on a named account; "go to SITE" as opening it);
    - removes what is wrong (typing the account name into a page; an extra window step before the troubleshooter; a page read after research);
    - asks when a document has no topic.
  - Every step carries a post-condition. Each rule came from a measured miss and has a regression test.
- **Fast path for one-command requests.** "open notepad", "pause", "minimize X" and the like are planned by rules in milliseconds, through the same `validate()`.
  - Anything with a second part, a pronoun, an account or an unknown name goes to the model.
  - It is judged on a holdout set written and hashed **before** the rules (`decision_cases_holdout2.json`, `holdout2.frozen.json`).
- **Decider footprint.** Context 4096 → 2048. The owner's apps hold about 1.2 GB of the 4 GB GPU; the model spills into shared memory either way (about 10 tokens/s).
  - Qwen3-1.7B is 6× faster but 17–20% less accurate: not adopted.
- **Follow-ups.** The previous request is shown to the model **only** when the new one reads like a follow-up (a live run showed context bleeding into unrelated requests). "open it" re-opens what the last task verifiably made, through the deliverables plugin (its own outputs only).
- **Opening an app that is already open** brings it to the front instead of starting a second window.
- **Long missions** run on DBOS Transact (MIT, SQLite).
  - A side-effect journal repeats an interrupted step only if repeating it is harmless (open, read, check); typing, keys, research and repairs stop and ask.
  - Re-plan at most twice; budgets of 20 steps and 30 minutes; loop and ping-pong detection; cancellation reaches the running PC job.
  - Missions start on the first entry call: the plugin host runs startup in a temporary event loop.
- **Proactive monitor** (plugin timer, 60 s, read-only).
  - Watches: crashes (Application log bookmark), disk floors (C: 12 GB), local services down twice in a row, stuck tasks.
  - One alert per fault, then a 30-minute quiet period.
- **Perception.** UI Automation first. Then Windows' built-in OCR on a capture of **one** window at 2× (never the desktop; local, deleted after use). Then UI-Venus-1.5-2B through llama-swap for what has no text.
  - OCR text is "observed"; model output is "inferred". A model point is checked to be inside the window, and its click is verified.
  - New read-only kernel ops `window_ocr` and `window_locate` (GREEN, like `window_text`), deployed with the full suite passing in place.
- **Diagnosis checks the system, not the message.** Port in use (who holds it, plus a free-port fix), disk space (real free space), service down (nothing listening): each is verified live, or stated as a hypothesis.
- **Controlled self-improvement.**
  - Neko may only **propose** new site names and plan examples, as immutable hashed versions.
  - A regression gate on the real decider must show no regression.
  - Promotion and rollback are owner-only, from the command line; no plugin entry exists for them.
  - The first live gate refused a proposal that regressed one case.
- **N.E.K.O core edit (justified, minimal; upstream merge note).** `plugin/core/host.py`, `plugin/core/communication.py` and `plugin/server/messaging/handlers/plugin_to_plugin.py`.
  - The problem: a plugin calling another plugin's **entry** was cut at 10 s (plugin side) and 60 s (router), whatever the entry declared. Research (900 s) called from the desktop plugin therefore always failed.
  - Now entry calls get the entry's own timeout (and the caller's; the smaller wins). Other custom events keep 10 s and 60 s.
  - Re-apply after an upstream merge if upstream has not fixed it.
- **Node** polls for queued jobs every 1 s (was 3 s). The plugin polls job results every 0.5 s (was 1 s).
- **Not done:** real-voice tuning, barge-in and echo cancellation (they need the owner's recordings, `neko_voice_owner_record.py`); unattended browser connection (needs the owner to save each profile's token, `save_playwright_token.ps1`); the Project JARVIS doc outside this repo was not updated.

## D29 Context selection off by default; benchmark v2; Linux test server (2026-10-16, owner travelling)
- **Context selection** (`jarvis_kernel/context_select.py`, wired into `mcpwire.process_result`):
  - `JARVIS_CONTEXT_SELECT` = `off` (default, unchanged head cut) | `evidence` | `trim1-conservative|balanced|aggressive`.
  - Every kept line is verbatim and in order; omissions are marked and listed; the input's sha256 is returned.
  - Selection only changes what a model reads. Policy, approvals, verification and destructive decisions never use it.
  - Not enabled yet: the measurements are on synthetic fixtures the evidence mode was tuned on (exploratory). Enabling needs
    a held-out test on real Neko tool output on the laptop.
- **trim-1**: vendored and pinned (Apache-2.0, LightGBM in Go, no network, checksum-checked model). Alone it kept every
  answer line on our fixtures but cut only 5.7% (conservative) to 49% (aggressive) and rarely met a budget; as a first pass
  before "evidence" it added about 50 ms and changed no outcome. Kept available behind the flag, not used by default.
- **Benchmark v2** (`jarvis/evals/neko95/bench`): versioned cases with scenario, expected result, risk and dims; results
  under the key "cases", so the frozen v1 scorer and its rules are untouched. Rubric v2 (`rubric_v2.json`) was written
  before any case ran: 10 x pass rate, capped by failed critical/high cases, deferred critical cases and small samples.
- **Live tests on the Linux server** use Dahl's public lane only with synthetic data (owner-authorised key, to be revoked),
  run headless in the test container at low priority (the relative works on this machine), never Claude models.
