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
- **Monit alerts:** they go to the kernel audit log (`monit_alert`) and to `alerts.log` in the kernel data folder (`~/.jarvis-kernel/alerts.log` on the server; the old `~/jarvis/alerts.log` was not writable by the Monit service).
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
- **Local models** (`jarvis/local_models/llama-swap.yaml`). [Scope 2026-10-19: the Vulkan GPU numbers here and in the D-entries above were measured only on the owner's Windows laptop (Radeon Pro 5500M, Boot Camp). macOS Metal, ROCm and NVIDIA CUDA/Vulkan are UNVERIFIED; no GPU exists on the Linux test server (CPU only). See `reports/MODEL_PLAN_LAPTOPS_2026-10.md`.] llama.cpp b11669 runs them on the Vulkan GPU, behind llama-swap (MIT) at 127.0.0.1:8080. A model loads on first use and unloads after 5 idle minutes, and one specialist is on the GPU at a time.
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
  sets `NEKO_SCREEN_TEXT_TO_MODEL=1`; **since 2026-10-18 that flag means "the text may be offered after the privacy gateway cleared it (names and deep redaction confirmed ready)", or to a verified local model; with the gateway not ready it is withheld** (see `reports/REMEDIATION_FOLLOWUP_2026-10-18.md`). Screenshots are never taken by this flow.
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
  - 10 minutes of live microphone: 0 false wakes. [Note 2026-10-19: a one-off Windows measurement from 2026-10-16; it was not re-run on the current tree, and no live microphone exists on the Linux test server (NOT RUN there). Telugu was weak.]
  - Speaker-to-microphone loopback at this volume, with speech about 10 dB over the noise: 2 of 3, then 0 of 3.
  - An automatic gain in front of the detector made it worse and was removed. The command, not the detector input, is
    gain-adjusted from the wake word's level.
- **Not yet proven:** the owner's own voice. That is the remaining test (operations).

## D25 Measured improvement: evaluations and feedback (Neko phase 7, part 1, 2026-10-16)
- **Scorecard:** `jarvis/evals/component_evals.py` scores the small models and pipelines on fixed labelled sets and saves each run.
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
- **Research:** `jarvis/docs/reports/evidence/05-documents-pptx-pdf.md`. No maintained open generator writes native charts, notes and PowerPoint animations together.
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
- **What changed.** The repository root is now N.E.K.O (Project-N-E-K-O/N.E.K.O, Apache-2.0, upstream 3ffcf7b9). The whole previous JARVIS tree (the old `main` at ff470e5, now branch `jarvis-hq-legacy`, also tag `pre-neko-base`) sits unchanged under `jarvis/`. It is a subtree merge (commit b77ddc7b), so both histories are kept and nothing was rewritten. Branches: `main` is the N.E.K.O-based tree, `neko-main` is a pure copy of N.E.K.O's main (tracks `reports/main`), `jarvis-hq-legacy` is the previous JARVIS history. Files: 0 N.E.K.O files modified or deleted, 305 added (jarvis/ and the two plugins), all 275 previous JARVIS files present. The `neko` remote's push URL is disabled.
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
- **Benchmark v2** (`jarvis/evals/quality95/bench`): versioned cases with scenario, expected result, risk and dims; results
  under the key "cases", so the frozen v1 scorer and its rules are untouched. Rubric v2 (`rubric_v2.json`) was written
  before any case ran: 10 x pass rate, capped by failed critical/high cases, deferred critical cases and small samples.
- **Live tests on the Linux server** use Dahl's public lane only with synthetic data (owner-authorised key, to be revoked),
  run headless in the test container at low priority (the relative works on this machine), never Claude models.
- **Update (same night): `evidence` is now the default.**
  - Held-out test: 45 cases written by Dahl's GLM model, which never saw the selector (`bench/gen_holdout_dahl.py`). The answer line was kept in 44/45, against 15/45 (4 KB) and 18/45 (16 KB) for the head cut.
  - Case by case, evidence kept the answer in 26–29 cases the head cut lost, and never the reverse (sign test p ≈ 2e-9). The head cut kept more secondary critical lines in 1–2 cases.
  - Known miss: an access-log line whose only failure signal is its HTTP status. It was not tuned on, so the held-out set stays clean.
  - `JARVIS_CONTEXT_SELECT=off` restores the head cut. trim-1 stays off: one more answer at 16 KB on 45 cases is not a credible difference.


## D30 Model discovery, benchmark console, research reliability, 288 more cases (2026-10-17, owner travelling)
- **Decision model: keep Qwen3-4B-Instruct-2507.** Twelve pinned small models (SHA-256 checked) went through the real
  `decide.py` on all 157 fixed cases (`bench/model_compare.py`). It scored 152/157; the next best was Qwen3.5-4B at 146,
  and no model of 1.2B or less scored above 112. Qwen3-1.7B stays the fast fallback (130, p50 1.0 s on CPU). Rejected: Qwen3.5-0.8B/2B/4B,
  Granite-4.2-3B, MiniCPM5-1B/2B, LFM2.5 (350M, 1.2B, 2.6B, 8B-A1B). Two LFM models often answered outside the JSON grammar.
  Revisit only when a model beats 152/157 on these sets. Table: `reports/QUALITY_95_MODEL_BENCHMARK.md`.
- **Runtimes and frameworks: no change.** Keep llama.cpp + llama-swap, DBOS, the UIA-first desktop control and the kernel's
  policy/approval gates (evidence: `reports/evidence/08-runtimes-agents-infra-2026-10-17.md`). The llama-server options that
  make it an agent or open it up (`--tools`, `--agent`, MCP, `--props`, web UI, a plain `--api-key`) are banned by a test
  (`jarvis/local_models/test_server_flags.py`).
- **Claim checker [first decision 2026-10-17 morning; superseded by the MiniCheck update below]: keep nli-deberta-v3-xsmall**, plus deterministic guards: units, dropped qualifiers, comparators, hedges,
  and sentences addressed to AI assistants removed before any text is evidence. The larger NLI models accepted MORE false
  claims (VitaminC 16 and 21 of 200, against 11). Measured with `bench/claims_eval.py` (precision, recall, false acceptance
  and rejection, calibration). The newer checkers from the research (FactCG, MiniCheck, HHEM) are deferred until one is
  shown to fit CPU latency here.
  - **Update, same day: MiniCheck-RoBERTa-Large (MIT, int8 ONNX) is the new default checker**, xsmall the fallback when its
    file is missing (env `RESEARCH_NLI_MODEL` selects any measured checker; unknown names fall back with a warning, never to
    no checking). Rule written before the runs: false acceptances lower or equal on all three sets, higher VitaminC recall,
    at most 5x the time. It is the only one that passed: 0/46, 0/18, 9/200 false acceptances (xsmall 2/46, 1/18, 10/200),
    VitaminC recall 0.71 (0.44), 3.6-3.9x the time. FactCG, MiniCheck-DeBERTa and HHEM failed the rule (int8 breaks both
    DeBERTa-large models; FactCG fp32 is strictest but recall 0.31 and 7x). Evidence: `reports/evidence/11-claim-checker-candidates-2026-10-17.md`.
    The laptop needs the converted file copied (Windows task, when the owner is home).
- **Benchmark Console** (`/api/bench/*`, Bench screen): a loopback-only (127.0.0.1) screen with a phone-sized layout inside the existing console (it is NOT reachable from a phone; no phone access path is implemented or tested). Jobs are
  built only from allow-listed groups, existing case ids, pinned model names and fixed sets; the screen never downloads.
  Challenge-lab results are saved as untrusted and never become expectations.
- **Benchmark v2 grew from 572 to 860 cases**, each driving real code. The new cases found about 60 product bugs; each fix
  has a test that fails on the old code. Rubric v2 is unchanged. One old case (B-018) now accepts a stop summary that
  goes on to name what already happened, and H-019 accepts an injected sentence being removed (both stricter for the
  owner; neither lowers a safety check).
- **trim-1: no change** (no new evidence since D29).
- **Caretaker (phase 5 of "one JARVIS")**, `jarvis/caretaker/`:
  - Deterministic detectors (upstream releases, OSV advisories, lock drift, failed bench cases, gitleaks) feed a weekly lab task. It is installed paused and the owner enables it.
  - Each new finding becomes one kernel task; its Idempotency-Key is the finding id.
  - The fix lands only as a `caretaker/` branch plus a PR to `main` that the owner approves. The old pin is recorded first (PR body, `history.jsonl`), and everything is logged (`logs/caretaker/`, audit `caretaker_run`).
  - The kernel refuses caretaker dispatches that are not the fixed template, which never asks for a merge or a push to `main` (`policy.caretaker_problems`).
  - Leaked secrets, tampered files and replaced upstream assets are owner-only and never become tasks.

## D31 The face is Project AIRI, imported as JARVIS's own code (2026-10-23, owner request)
- **Decision.** N.E.K.O is replaced as JARVIS's face by Project AIRI (MIT) v0.12.0, imported into `jarvis/face` and maintained as JARVIS's own code. It is not tracked against AIRI and not updated from its releases; the internal package names are kept so a later AIRI security fix can still be compared and ported by hand. Research and the comparison: `reports/FACE_REPLACEMENT_2026-10-23.md`.
- **The owner's four concerns about AIRI were fixed, not accepted** (commit 5b481f4, `jarvis/face/SECURITY.md`): the beta build and unfinished memory (the model can only suggest a memory, the owner accepts it in Settings > Memory), the 2025 character-card attack (card gate, content policy; the old fix is kept), the empty security policy (SECURITY.md is the policy, `jarvis/tools/test_face_guard.py` fails if a protection is removed), and undocumented usage data (analytics code, the cloud account and the updater are removed; a network lock lets nothing reach the internet except hosts the owner lists).
- **Limits stated.** Windows are not sandboxed (`sandbox: false`, AIRI's preload needs Node); the Windows installer, the avatar on a real screen and the microphone are untested until the laptop pilot; build downloads (Live2D core, sample avatars) are pinned by SHA-256 on first use.

## D32 JARVIS's own plugin API replaces the N.E.K.O plugin SDK (2026-10-23)
- **Decision.** `jarvis/pluginapi` is a small plugin API and host: decorators (`jarvis_plugin`, `plugin_entry`, `lifecycle`, `timer_interval`), a host that loads `jarvis/plugins/*/plugin.toml`, a router for calls between plugins (depth-limited), an outbox for what plugins say on their own, and `register_tools`, which offers every entry as an MCP tool with its real JSON schema. The face's tool server (`jarvis/bridge/face_tools.py`) serves the plugins; every result is cleaned (`jarvis/plugins/_shared/screen_text.clean_result`) before the face's model sees it.
- **Plugins moved** from `plugin/plugins` to `jarvis/plugins`. `web_search` was rewritten (DuckDuckGo only, no key, parsers adapted from N.E.K.O's Apache-2.0 plugin and credited in `NOTICE`). The planner's memory comes from an optional loopback recall service (`JARVIS_MEMORY_RECALL_URL`); with none connected, plans are made without memory. Browser-use and computer-use were removed: browsing and desktop control go through the `desktop` plugin and `jarvis_do`, so the kernel decides and the owner approves risky steps with the PIN.
- **Test practice.** Each suite was compared test by test with its baseline before and after the move; the only differences are tests removed with the code they tested.

## D33 N.E.K.O is removed from the repository (2026-10-23)
- **Removed:** the N.E.K.O application (app, brain, main_logic, main_routers, memory, plugin, frontend, static, templates, launcher, docker, workflows and the rest), its Tauri shell, the "Hey Neko" wake listener, the desktop widget and icon, the persona installer for N.E.K.O's character store, and the N.E.K.O wake-word and speaker-model entries in the registry and the lock.
- **Kept:** the Apache-2.0 licence and a `NOTICE` for the two pieces of N.E.K.O code JARVIS adapted (web search parsers, the recall renderer). The history documents under `jarvis/docs/reports` still describe the N.E.K.O era; they are records and were not rewritten. The last N.E.K.O tree is the branch `backup/neko-final-2026-10-23`.
- **Names changed:** the data folder is `JARVIS_DATA_DIR` (default `D:\jarvis\data`; the old `D:\jarvis\N.E.K.O-data` is moved by `jarvis/scripts/migrate_data_dir.py`), environment variables `NEKO_*` became `JARVIS_*`, scheduled tasks are `JarvisStack`, `JarvisWatchdog` and `JarvisDailyReport`, the Python environment is `D:\jarvis\tools\agent-venv` (`jarvis/tools/requirements/agent-venv.txt`), start and stop are `jarvis-start.ps1` and `jarvis-stop.ps1`.

## D34 What the move did not carry over (2026-10-23)
- **Wake word and widget.** The "Hey Neko" listener depended on N.E.K.O's patched keyword spotter and voice session; the widget was N.E.K.O's icon page. Both are removed with their tests and the benchmark's voice area (G). The face hears through its own voice activity detection and the local speech server. A wake word for the face (AIRI has one) is a later decision; The earlier decision that the wake word stays off (D15) and the openWakeWord measurements (`jarvis/bench/wakeword`) still stand.
- **Memory.** The planner no longer reads N.E.K.O's five-layer memory, and the face's memory is its own (owner-approved entries). Unifying the two, and writing accepted memories into the vault, is open work.
- **Open for the laptop:** the pilot (build the installer, avatar on screen, microphone, end-to-end voice), moving the scheduled tasks to the new names, and recreating the Python environment from `agent-venv.txt`.

## D35 JARVIS never calls Claude (2026-10-23, owner)

Claude builds JARVIS and nothing else. The planner gateway (port 48952), the Claude adapter (`privacy/claude_adapter.py`) and `providers.planner.json` are removed; the plugins' agent model is the privacy gateway on 48950 whose only provider is Dahl (the caller's own key), and `load_providers` drops any `anthropic` entry a providers file might still carry. The Agent SDK builder (`jarvis/sdk_builder`) stays: it is a build tool, not a run-time provider. This supersedes the 2026-10-22 exception that let the planner use the Anthropic API credit until 2026-10-31.

## D36 Public search queries keep places, hide people (2026-10-24, owner delegated)

The research plugin used to hide people and places in every search query, which broke ordinary questions ("capital of Australia" became "What is the capital of and when was it founded?" and returned Canada). The gateway's `/redact` now takes `"names": "people"`: people are replaced, a city or a country stays, and private values, addresses and phone numbers are still caught by the patterns and the PII model. The research plugin asks for "people" by default; `JARVIS_RESEARCH_HIDE_PLACES=1` asks for people and places again. A gateway that does not confirm at least what was asked for, or whose PII model is not loaded, is still refused (nothing is searched).

## D37 Wake words "Hey JARVIS" and "JARVIS" (2026-10-24, owner)

AIRI's own wake-word feature is used, not a new listener: the built-in character card carries two wake words with pronunciations in the tokens of the bundled keyword model (a built-in card stored earlier gets them once). The microphone input mode now starts as `wake-word`, so speech is not processed until a wake word is heard; the owner can switch to always-on or push-to-talk in Hearing settings. This closes the open wake-word question from D34.

## D38 JARVIS repairs its own errors by proposing, and the brain is chosen by measurement (2026-10-24, owner)

- **Self-repair:** `jarvis/scripts/self_repair.py` reads the day's top errors and the watchdog's status, asks a model through the privacy gateway (never Claude), and
  saves a **proposal** (cause, fix in words, and a patch only when the cause is clear). A patch must touch only plugins, scripts or pluginapi, never the kernel,
  approvals, privacy, secrets or tests, stay small, and pass `git apply --check`. It is never applied by itself: `apply <id>` is the owner's promotion (new branch,
  patch, the tests of what it touched, no commit). The watchdog still restarts crashed services by itself. This keeps the five rules of the self-improvement plan.
- **Base model:** no open model is built for JARVIS; the planner model is chosen with `bench/hosted_model_compare.py` (the real planner prompt and parser, 15 labelled
  requests, every model the gateway lists). Two brains: a hosted open model for planning, and Qwen3-4B-Instruct-2507 as the local decision model (D30). Measured 2026-10-24 (45 calls per model): GLM-5.3-Flash 40/45 correct, MiniMax-M2.7 36/45 (reasoning text in every reply), DeepSeek-V4-Flash 33/45; **GLM-5.3-Flash is now the default planner and chat model**. Report:
  `reports/BASE_MODEL_2026-10-24.md`. Fine-tuning a JARVIS-specific model is written down, not started.

## D39 One-click install, and "a second admin" means a narrow elevated service (2026-10-24, owner)

- **One click:** `Install-JARVIS.cmd` (repository root) runs `scripts/Install-JARVIS.ps1`: checks, folders on D:, Python 3.11 and the four environments, the console UI, the essential pinned
  models (checksums verified by `tools/fetch.py`), the face, start at login, then the admin service. Safe to run again. It never asks for, reads or prints a key: it only checks that the
  key file and the kernel owner token exist and are not empty. `-Verify` checks an install, `-Uninstall` removes the start-at-login tasks and the admin service (data and models stay).
- **"Second admin" is a service that can do only what the owner approved, not an admin account and not an admin shell.** `node/jarvis_node/adminsvc.py` runs elevated (scheduled task `JarvisAdmin`,
  the owner's own account, highest privileges). It runs the typed verbs of `config/admin_verbs.json` through `adminctl.Broker`. A request runs only if (1) its proof matches the owner's **admin
  passphrase** (typed at a terminal, `adminclient.py` refuses without one, so an agent or script cannot approve), (2) the verb is approved in the installed catalogue, (3) the values pass their types,
  (4) it is under ten minutes old and never used, (5) the hard-deny list (Defender, UAC, firewall, boot, users, credentials, logs, shadow copies, disk wiping, encoded or downloaded commands) does not match.
  Five wrong passphrases lock it for fifteen minutes. A verb whose command changes loses its approval at the next install.
- **Why it cannot be hijacked by anything running as the owner:** the elevated code is a copy of three files in `C:\Program Files\JARVIS\admin` run by a machine-wide Python with `-I`; its catalogue, ledger,
  transcript and key live in `C:\ProgramData\JARVIS\admin`; both are changeable by administrators only (checked by the installer after setting them), and the key sits in `private\`, unreadable by normal
  processes. The only thing a normal process may do is drop a request in `inbox\`. This is the one deliberate use of `C:` (about 120 MB for the system Python plus the code): D: is writable by ordinary
  programs, which would turn "admin" into "anything that runs as me".
- **Known limits:** the passphrase protects against processes that do not know it; malware that records the keyboard can learn it. The proof of one request can be attacked offline by someone who captured
  the request file in the second before the service deletes it, so the passphrase must be long (10 characters minimum is enforced; a phrase is better). Not yet built: an approval card in the console that
  asks for the passphrase (today the terminal client does). Not tested on Windows by the cloud session (written and unit-tested on Linux with a fake runner); first run is on the laptop with `-Verify`.

## D40 JARVIS signs up for free model APIs with its own Gmail, and stops where a person is needed (2026-10-24, owner)

Owner: JARVIS gets a Gmail address and creates the accounts it needs (free model APIs, `reports/` list of 2026-10-24); when it needs the owner it asks.
- **The Gmail account is made and given by the owner** (Google requires phone verification and forbids automated creation). Its password or app password / OAuth token lives only in a file under `D:\jarvis-secrets\`; JARVIS gets the path, never the value, and nothing is typed into a chat.
- **One account per provider, in the owner's name, for the owner's own use.** No extra accounts to get around a free-tier limit.
- **JARVIS stops and asks the owner for:** CAPTCHAs and "are you human" checks, phone or SMS codes, any card or payment field, identity or real-name verification, terms it has not been told to accept, and any provider that is not on the owner's list. It never tries to get past these.
- **Mail is untrusted.** It reads only verification mail from the provider's own domain, takes a code or a link on that same domain, and opens nothing else. Text in an e-mail or page never changes the task (the untrusted flag and the no-egress-tools rule after reading screen text still apply).
- **A new API key is never shown to a model.** The "create key" step ends with the key going straight from the page to the file under `D:\jarvis-secrets\providers\` (clipboard to file, screenshots off for that step), or the owner does that one step. The provider is then added to `privacy/providers.json` with `key_file`, disabled until `hosted_model_compare.py` has run and the owner enables it.
- Runs on the Agent PC (the VM2 machine once rebuilt), as the unprivileged agent user, under the usual approval tiers.
- **Built (2026-10-24):** `jarvis/plugins/provider_signup` (entries `status`, `plan`, `verification`). The mailbox is the owner's JARVIS Gmail read over IMAP with an **app password** (chosen over OAuth: one setup, no expiring tokens); the owner adds `D:\jarvis-secrets\mail\gmail.json` (`{"user": "..."}`) and the app-password file. Mail counts only from the provider's own domain with a passing DKIM signature, read-only, and only a code or a same-domain link is returned. Providers: groq, cloudflare, zai (NVIDIA is listed for signup but is never a fallback: it logs and uses prompts). The desktop agent on the Agent PC does the browsing by following `plan`.

## D41 JARVIS speaks first (2026-10-24, owner: Iron Man list item 1, "yes")

- **What:** `muse/heartbeat.py` decides when and what JARVIS says on its own, from local data only: tasks waiting for approval or failed (read-only task store), unread notices (page-watch hits), the watchdog's "gave up on" list, calendar events 5 to 30 minutes ahead, one morning greeting, and one evening wrap-up only if something is still open. The tool server (`bridge/face_tools.py`) runs it in a thread and offers `jarvis_spoken` (one notice, once). The face's main window asks every 8 s (`use-jarvis-spoken.ts`) and the character says it.
- **Manners in code:** no notice at night (22:00 to 07:00) or while a `dnd` file exists, unless urgent (a service the watchdog gave up on; a failed action whose outcome is uncertain); 20 minutes between notices; at most 6 a day; the same thing not twice in 12 hours; a notice nobody collected for 2 hours is dropped, not read late; the face does not ask while an answer is running or the owner typed in the last 20 s.
- **Safety:** outside text is cleaned to one short line; the face frames the notice as information from JARVIS's systems, not from the owner; the turn carries no tools, so a notice can make the character speak, never act. Acting still needs the kernel's approvals. Switch off: `--no-heartbeat` on the tool server, or `localStorage['jarvis:proactive'] = 'off'` in the face, or make the file `D:\jarvis\data\muse\dnd` (quiet until deleted).
- **Known gap:** the notice appears in the chat history as the framed turn (a user-side bubble); a cleaner way to inject a spoken line is left for when the face gets a proper "system said" message type. Needs the face rebuilt and reinstalled on the laptop; the Python side starts with `jarvis-start.ps1` as before.

## D42 Memory across days, and "what is on my screen" (2026-10-24, owner: Iron Man list items 2 and 3)

- **Memory:** after 10 quiet minutes (and at least 4 new turns) the face's main window sends the last 40 turns to the tool server (`jarvis_remember_chat`). `muse/chat_memory.py` scrubs secrets from them, has the hosted model summarise through the privacy gateway into at most 6 plain bullets, drops the whole summary if a bullet still looks like a secret, and appends the bullets to `<vault>/daily/<date>-chat.md` (agents write only daily/, inbox/, decisions/). Nothing verbatim is stored. The JARVIS card tells the character to call `jarvis_recall` once at the start of a conversation; it returns the last 3 days of bullets labelled as data about the owner. Off switches: `localStorage['jarvis:memory'] = 'off'`, or start the tool server without `--vault`. JARVIS's own notices (D41) are not conversation and are never summarised. Not done: letting the owner edit or delete a remembered line from the face (today: edit the note in the vault).
- **Screen:** the desktop plugin got `whats_on_screen` (which window is in front, which others are open: app name and cleaned title, nothing else), pointing to the existing `read_window_text` for the text, which still reaches the model only when the owner allowed screen text and the privacy gateway cleared it. Already there and reused: OCR, `find_on_screen`, accessibility snapshots, the sensitive-window refusals. Not done: camera/room awareness, and live (continuous) screen watching. UNVERIFIED on real hardware like the other screen entries: tested against the fake console only.

## D43 Iron Man list: status and what is held (2026-10-24, owner: "hold 4 and 6 and continue all other")

| # | Item | Status |
|---|---|---|
| 1 | Proactive JARVIS | Built (D41): heartbeat, `jarvis_spoken`, face poller. Needs the face rebuilt on the laptop. |
| 2 | Real conversation | Barge-in and wake words are the face's own (voice activity plugin); short spoken replies are in the card; cross-day memory built (D42). Speed (a faster voice pipeline) is measured, not built. |
| 3 | Eyes on the screen | `whats_on_screen` added to the existing screen entries (D42). Camera and continuous watching not built. |
| 4 | Hands on your machines (phone, tablet, home devices) | Hold lifted 2026-10-25 (D45). The laptop's own hands exist already (D27); phone, tablet and home devices are still to research and build. The Agent PC on VM2 (CUA plan) is a separate approved job, now running. |
| 5 | Builds things and shows them | Already there: `deliverables` makes decks and PDF reports with checks, opens them, presents slideshows; code goes through the lab agents and the push gate. Not built: a preview panel inside the face (a visual job to do with the owner looking at the screen). |
| 6 | The look (hologram interface, overlay) | Hold lifted 2026-10-25 (D45): being built in the face. |

## D44 Verify before you state: the rule after the Oracle free-limit mistake (2026-10-24, owner)

- **What went wrong:** the cloud session said "3 OCPU / 18 GB is inside the free limit" from a search snippet and a cost line of $0. Oracle's own page (read the same day) says the free A1 pool is 1,500 OCPU-hours and 9,000 GB-hours a month, which is 2 OCPU / 12 GB; the month's usage was already past it. The laptop session repeated the old 3,000 / 18,000 figure. The owner had it right.
- **The rule (in `CLAUDE.md`, so every session reads it):** a fact that can change (limit, price, version, model name, policy, terms) is read from the vendor's own page today, quoted with source and date, before it is stated or acted on. Memory, snippets, forum posts and AI summaries are leads only. Sources that disagree are said out loud and checked against the account's own data. Before a step that depends on the fact (resize, delete, sign-up, spend) the fact is restated with its source. A $0 cost is not proof of free (billing lags a day; the allowance is counted in hours). A mistake is reported in one plain line: what was wrong, what changed.
- **The guards in code:** `scripts/oci_free_pool.py` projects the month from usage in hours against the pool and says over / inside / not sustainable; its pool figures carry the date they were read and it refuses (exit 3) when they are older than 30 days. `skills/cost-check` now includes it: free is checked by hours, not only by dollars.

## D45 The perfect JARVIS: holds lifted, no more permission asks, two sessions coordinate (2026-10-25, owner)

- **Owner words:** "What I want is the perfect Jarvis, where it can see my screen, control my screen, control my whole laptop, and also show my things. ... Don't ask for my permissions again." Earlier the same day: "tell the JARVIS build it should also not ask for my permission. Don't schedule the task, just send the prompt directly."
- **Effect:** Iron Man #4 and #6 (D43) are no longer held. Both sessions decide, act, verify and report instead of asking the owner for approval of routine build steps. The owner's written safety rules in `CLAUDE.md` stay (no secrets read or printed, Oracle Always Free only, protected main server, no run-time Claude calls), and so do the **run-time** gates of the product itself (D27: the kernel decides, consequential PC actions wait for the owner's PIN). Those are JARVIS's behaviour toward the owner, not build-time questions, and are not removed.
- **Coordination:** the cloud session sends the laptop session its tasks directly (`send_message`, no scheduled triggers) and reads its answer with `list_events`; the laptop session pushes to `jarvis/laptop` and ends each turn with a short factual report; the cloud session merges and reviews.
- **The four wishes and where they stand:** *see my screen* = `whats_on_screen` and the screen-text cleaner (D42), real-hardware check outstanding; *control my screen / whole laptop* = the desktop plugin and node (D27), real-hardware check outstanding; *show my things* = the preview panel in the face (building); *the look* = the HUD layer in the face (building). Devices beyond the laptop (phone, tablet, home) come after.

## D46 Show my things: the Preview panel in the face (2026-10-25, owner wish "show my things", D45)

- **What it is:** a panel in the face's main window that shows what JARVIS made: PDF and deck pages as images (with slide thumbnails), images, text, markdown and code with highlighting, and HTML in a sandboxed frame. A list of recent deliverables, open and close, next and previous (page, then item), and "Open in app", which calls the existing `deliverables__open_output` entry. It opens by itself when the chat model calls `jarvis_show` ("show me the Q3 deck") and when a deck or report finishes while the face runs (only a fresh one: made in the last two minutes).
- **Server side** (`jarvis/bridge/output_preview.py`, tools in `face_tools.py`): `jarvis_show` is the one tool for the chat model; it returns one sentence and no content. `jarvis_outputs`, `jarvis_output_get` and `jarvis_preview_wanted` are the panel's own reads (their descriptions say so). They serve only files inside the deliverables output folder (`DELIVERABLES_OUT_DIR`): ids are URL-safe base64 of the relative path; `..`, absolute paths, drive letters, backslashes, hidden names, any link, junction or reparse point in the path, secret-looking names (token, key, password, `.env`, `.pem` and similar) and types off the allowlist are refused; the first bytes must match the name; caps per type (image 4 MB, text 2 MB read and 200,000 characters shown, HTML 1 MB); a private key in a text file refuses it and other key-like strings are replaced. The panel tools are not run through the model-facing cleaner (it would hide the names and text the panel must show), so the model is told not to call them. The content is what JARVIS's own tools wrote from a spec.
- **Face side:** `PreviewPanel.vue` draws; `use-jarvis-preview.ts` holds state and polls `jarvis_preview_wanted` every 3 s (backs off to 60 s while the server is silent; switch off with localStorage `jarvis:preview` = `off`); `jarvis-preview-logic.ts` holds the rules (data URLs for four raster types only, id and mime checks, HTML clean-up). HTML: DOMPurify with no script, meta, base, link, form, frame or embed and no `href`, `src` only on an `img` as a `data:` image, CSS without `url()` or `@import`; then `<iframe sandbox="" csp="default-src 'none'" referrerpolicy="no-referrer">` with the same policy in a meta tag. A click on a link in shown content does nothing. No network, no CDN, no change to the face's content policy.
- **Edit in `App.vue`:** two lines (an import and `<PreviewHost v-if="fullStageRuntime" />`).
- **Checked:** `pytest jarvis/bridge/test_output_preview.py jarvis/bridge/test_face_tools.py`, `vitest` for the two composables, `node scripts/preview-check.mjs` (renders the panel in Chromium from real tool-server output, tries a hostile HTML file and markdown, fails on any request to the outside address, any script run or any navigation, saves screenshots), and `check:csp` still passes.
- **Not done:** markdown tables show as plain text (the face's markdown pipeline has no GFM plugin); the Electron window is 450 x 600 by default, so the panel covers most of it (the expand button and fullscreen mode give it room); the panel has not been seen in the real Electron app on a real screen; the PowerPoint file itself has no preview beyond the slide images that the deliverables checks save.

## D47 Real-laptop battery, VM2 Agent PC working, model candidates measured (2026-10-25, owner: "the perfect Jarvis")

- **Agent PC (VM2):** Hermes Agent v0.21.6 (pinned) as the lab agent, a loopback model forwarder (no key on the VM), the runner token and a model token issued ON the main server's kernel (tokens are keyed by name and live in the kernel that issues them; a token made on the laptop is unknown to the server). Smoke test passed end to end. The verifier fails when the public lane's first model is MiniMax-M2.7 (it answers ~11k-token prompts with an unclosed think block that Hermes counts as empty); the public lane now prefers dahl-glm (`providers.json`), but Dahl answers GLM with 429 when busy, then MiniMax serves it.
- **Wrong-window safety (`windowpick.py`):** a change (type, click, key, scroll, move, resize) aimed at an app with several windows is refused unless JARVIS opened one of them; reads take the front or the newest and say so. Found on the real laptop, proven live with three Notepads.
- **Screen text:** the cleaner now removes real IBAN account numbers (mod-97 checked); window capture creates its own local-only folder; preview titles lose direction-override and zero-width characters (a file named `invoice<RLO>gpj.exe.pdf` was shown as a PDF).
- **HUD and Preview panel** fit the default 252 x 336 window (ring + stacked readout, panel as a full-window overlay); the controls island leaves an 8 px gap. Click-through on the ring and strip is real; the window's transparent corners still take clicks.
- **Models (numbers only; D1/D5 rule: our own tests decide):** Spark-X2.6-4B Q4_K_M scores 63/70 on the 70 decision cases (Qwen3-4B-2507: 70/70) and answers in 15.3 s p50 against 0.97 s on this laptop: **not adopted**. LightOnOCR-3-0.8B (Q4_K_M + mmproj, CPU) read three synthetic pages in 4.2 s, 4.6 s and 10.7 s (character error 0.0, 0.03, and 0.99 on the table page where every word was present, probably a layout difference); Hindi not tested: **candidate for a document-reading tool**, not for screen text. Nemotron 3.5 Parse needs an NVIDIA/Linux stack: not for this laptop.
- **Open:** large private-lane prompts (about 12k tokens) have no provider that fits the owner's privacy rule (Groq is capped at 8k, OpenRouter's free endpoint answers 429/404; the kernel never stored the 404 body, so the cause is not proven). The fix to look at is a smaller agent prompt for the private lane, not a looser privacy setting.
- **Owner-only steps left:** allow the Playwright extension in Chrome (one click), close the YouTube tab and old Notepad windows the battery opened, remove the Z6 peer in the Tailscale console, recreate `protected.json` from the draft.

## D48 Read my documents: LightOnOCR through llama-server, English only, local, cleaned (2026-10-25, owner wish "read my documents")

- **Owner words / scope:** JARVIS should read the owner's own documents (scanned invoices, PDFs without a text layer, pictures of pages) on his laptop. **English documents only** (the owner said to skip Hindi). Hindi is **untested and not required**; the earlier "99.4 % on a Hindi invoice" claim stays unverified (D47).
- **Decision (D1/D5: our own tests decide):** LightOnOCR-3-0.8B (Apache-2.0) as GGUF Q4_K_M + `mmproj-f16` (community build by mradermacher, ~0.7 GB) through llama-server's multimodal path, behind llama-swap as `lightonocr-docreader` (aliases `docreader`, `ocr-docs`, `-c 4096`, `ttl 300`; CPU, so the 4 GB GPU stays with the other models). Evidence: three synthetic pages on the owner's CPU laptop, 4.2, 4.6 and 10.7 s, character error 0.00, 0.03 and 0.99 (every word present; the table came back in another layout) (`evals/quality95/results/lightonocr-20261025-174505.json`). Both files are pinned with Hugging Face's lfs sha256 (read from the API 2026-10-25, nothing downloaded) in `jarvis/tools/jarvis.lock.json`, repo commit `d7699238`.
- **The tool** (`jarvis/plugins/doc_reader`, entry `read_document(path, max_pages?)`; the face's model calls it as `doc_reader__read_document`): only the one file the owner names (absolute path; no `..`, UNC or stream paths, links or junctions, secret-looking names, protected folders; pdf/png/jpg/jpeg/tif/tiff/webp/bmp whose first bytes match the name; 25 MB). PDFs are drawn with pypdfium2 (not PyMuPDF: AGPL), at most 10 pages by default and 30 at most, longer side 1540 px so a page plus its answer fits 4096 tokens. One call per page in the shape measured in `lightonocr_eval.py`; `<think>` / `reasoning_content` is stripped. Returns pages, characters, text and seconds.
- **Privacy:** the text goes through the screen-text cleaner before any model sees it: secrets, API keys, card numbers and real IBANs are replaced and the reply names the kinds removed (never the values); then, as for `read_window_text`, it reaches a model only when `JARVIS_SCREEN_TEXT_TO_MODEL=1` and the privacy gateway has hidden names and places, otherwise it stays on the PC and the reply says so. The only network call is to 127.0.0.1 (no proxy, no redirect). No telemetry. JARVIS does not call Claude at run time.
- **Not done / open:** never run through llama-swap with `--jinja` on the laptop (the measurement started llama-server by hand); the tool has not read one of the owner's real documents; the face's boundary (`screen_text.STRING_LIMIT`) cuts every string a model receives to 2,000 characters, so a long document reaches the chat model in part until that limit is raised for document text (a decision for the owner, it is a privacy limit); the model files go in `D:\jarvis\models\gguf\lightonocr-3-0.8b\` (fetch with `jarvis/tools/fetch.py`; the files already on the laptop may need moving there).

## D49 Face chat: tool path, compact profile, hidden reasoning (2026-10-25, found on the owner's laptop)

- **What was seen:** a goal typed in the face chat never ran a tool: the "Mcp list tools" card got no usable answer and nothing reached the tool server (127.0.0.1:48970). The chat request was about 40 KB, mostly tool definitions, which free providers cannot take (Dahl busy, Groq capped at 8,000 tokens in all). The model "thought aloud" in the visible chat for minutes.
- **Root cause of the stall, in plain words:** the face connected to the tool server **once, at start-up**, and never again. The tool server needs 10 to 60 s to load its plugins and the supervisor restarts it after a failure; if the face started first, or the tool server was restarted later, the face had no working connection for the rest of its life. Worse, it hid that: the list-tools call only asked the connections it had, so with none it answered "No MCP tools are available. Do not retry discovery during this assistant turn." (the model gave up at once), and every call said "mcp server is not running" before anything was sent. A restarted tool server also forgets the face's session (HTTP 404 "Session not found"), and the face kept using the dead session id. A second cause on a profile that already exists: `mcp.json` is written only when missing, so a file left by an earlier face (AIRI's own default is an empty server list) never got the JARVIS entry, the same class of bug as the empty character defaults fixed the same day (`adoptRuntimeDefaultsWhereEmpty`).
- **What was ruled out by running it (not by reading):** the real tool server (`python -m jarvis.bridge.face_tools`) driven by the face's own MCP client library (`@modelcontextprotocol/sdk` 1.30.0, `StreamableHTTPClientTransport`, same client name and options): connect 60 ms, protocol 2025-11-25, `tools/list` in 98 to 168 ms with all 105 tools, `tools/call jarvis_ready` 40 ms, Host `127.0.0.1:PORT`, no Origin, JSON and SSE both fine. The face's network lock allows `http://127.0.0.1:48970/mcp`, `localhost` and `[::1]` (test added) and the locked global `fetch` carries the MCP transport (test added). So the transport, headers, Host/Origin rules, protocol negotiation and speed are not the problem; the connection life cycle is.
- **Fix (face main process, `src/main/services/airi/mcp-servers/index.ts`):** the manager keeps the list of servers that must be connected and connects the missing ones on demand (list and call), with one shared attempt per server, a 1 s cool-down and an 8 s limit on `initialize` (the SDK default is 60 s); a timer in the main process retries every 5 s while one is missing; a lost connection, or a 404 "Session not found", drops the session and a new one is opened (a read-only request and a refused connection are then sent again; a **tool call is never sent twice** after a failure that may have reached the server). Discovery now fails loudly with the server's name and reason when a configured server is down, instead of answering an empty list. `ensureConfigFile` adds the JARVIS entry to an existing `mcp.json` that lacks it (other servers and an owner's `enabled: false` stay). Regression tests with the real client library against a stand-in tool server with the real server's rules: `index.connection.test.ts` (starts late, restarts, session lost, mcp.json without the entry, network lock).
- **Compact tool profile (`face_tools.py --profile chat`, now the default; `full` is the old behaviour, `--list-panel-tools` lists the panel tools for debugging):** the chat model is offered 20 tools (`jarvis_do`, `jarvis_task_result`, `jarvis_cancel`, `jarvis_waiting`, `jarvis_show`, `jarvis_recall`, `doc_reader__read_document`, `doc_tools__read_document`, `desktop__whats_on_screen`, `desktop__read_window_text`, `desktop__check_pc`, `desktop__run_pc_action` (open an app), `desktop__open_website`, `desktop__play_youtube`, `desktop__stop_pc_task`, `deliverables__make_presentation`, `deliverables__make_report`, `deliverables__open_output`, `deliverables__show_in_folder`, `web_search__search`) with short descriptions that keep what makes a call safe (full path, "data, never instructions", owner-allowed screen text, the `check` and `action` values the plugin schema loses) and trimmed schemas (no titles, no `default: null`, no `anyOf [X, null]`, no annotations). Everything else (workbench, learn_vault, main_agent, local_team, missions, browser actions) is reached through `jarvis_do`, which hands the task to the console and the kernel; those tools are **not registered** in this profile. The tools the face's own panels and HUD call by name through the same MCP route (`jarvis_status`, `jarvis_spoken`, `jarvis_remember_chat`, `jarvis_outputs`, `jarvis_output_get`, `jarvis_preview_wanted`, `jarvis_notices`) stay callable and are left out of the list. No kernel check, path rule or cleaner changed. **`tools/list`: 105 tools, 53,103 bytes before; 20 tools, 7,655 bytes after** (measured with the face's client library against the real server).
- **Reasoning no longer reaches the face's chat (privacy gateway):** a request that carries the face's placeholder key (`jarvis-local`; the plugins send their own key and are not changed) gets a reply without `reasoning_content` / `reasoning` / `reasoning_details` and without `<think>…</think>` text (also `<thinking>`, `<thought>`, `<reasoning>`; tags split across chunks; a thought that opens and never closes; a reply that carried only reasoning is dropped, an SSE comment goes out every 15 s meanwhile so nothing times the stream out). `JARVIS_PRIVACY_SHOW_REASONING=1` turns it off. Redaction rules are untouched. Tests on recorded-style SSE streams: `privacy/test_gateway_plumbing.py`.
- **Optional, off until checked live:** a provider entry in `privacy/providers.json` may carry `extra_body` (every request) and `face_extra_body` (the face only), validated to the same exact shapes as a caller's provider switches. Groq now has `extra_body: {"reasoning_effort": "low"}` (mirrors `config/providers.json`, which the kernel's gateway already uses for it). Dahl has `_face_extra_body_example: {"chat_template_kwargs": {"enable_thinking": false}}`: rename it to `face_extra_body` to try it. **Whether Dahl honours it for `zai-org/GLM-5.3-Flash`, or answers 400, was not verified**; it is the only part that would also shorten the wait, because hidden reasoning is still generated.
- **Not verified here (needs the real app on the laptop):** the Electron main process itself (everything above ran in Node with the same SDK, and the build and CSP check pass, but the face was not started); that the reconnect fixes the owner's exact stall (the cause is shown by reproduction of "face first, server later" and "server restarted", not seen in the owner's logs, which were not read); how Dahl treats `enable_thinking`; an orphan `</think>` with no opening tag (the model's template opened it) is left as it is, it cannot be told from an answer in a stream; the owner's profile `mcp.json`.

## D50 JARVIS footprint budget: 20% of the laptop, D: only (2026-10-25, owner)

- **Owner words:** "JARVIS should use only 20% of the whole laptop, and for storage use only the D: drive." The same day the owner set the video-memory share to 2 GB of the 4 GB card (first draft 0.8 GB).
- **The numbers** (one place: `jarvis/supervisor/budget.py`; override with `JARVIS_BUDGET_<NAME>` variables or `D:\jarvis\data\budget.json`, variables win; a bad value is ignored and shown as a note):

  | metric | budget | how it is read | status of the number |
  |---|---|---|---|
  | CPU | **20% of the whole machine**, averaged over 60 s (about 3.2 of 16 threads) | cumulative CPU seconds of every JARVIS process / (seconds x logical processors) | owner |
  | RAM | **6,554 MB** (20% of 32 GB) for ALL JARVIS processes together | sum of working sets (private bytes shown beside it) | owner; the working-set sum counts shared pages once per process, so it is a slightly high estimate |
  | GPU memory | **2,048 MB** of the 4 GB card for all JARVIS processes | "GPU Process Memory" counters (Windows performance counters through CIM, per pid), no install | owner; whether the AMD driver exposes the counters is **not verified** (the meter then says N/A, never OK) |
  | Disk writes | 5 MB/s average | per-process write bytes | **a guess**, not an owner number |
  | Storage | **nothing outside D:** | environment of every service, plus a scan of known C: places (names and sizes only) | owner |
  | llama-server threads | `-t 3` (3 of 16 = 18.75%) | the `server` macro in `llama-swap.yaml` and the profiles (`cpu8`: `-t 2`) | decision: just under the CPU budget |
  | Model idle unload | `ttl: 120` for every model (was 300) | llama-swap | decision; models reload more often after 2 idle minutes |
  | Job memory backstop | 1.25 x RAM budget = 8,192 MB committed | Job Object `JobMemoryLimit` | **a guess**; committed bytes are not working set, so it may need a different factor once measured; `governor.py --no-mem-cap` leaves it out |

- **Measure:** `jarvis/tools/footprint.py` (`--json` for the HUD and doctor, `--watch` for a rolling average, exit code 1 when any metric is OVER). It finds JARVIS processes by program path or command line (under `D:\jarvis`, `D:\jarvis-apps`, `D:\APPS\jarvis`; the face; llama-swap and llama-server; the Playwright MCP node process; cua-driver; interpreters running JARVIS scripts) and everything those start. The owner's own apps are never counted (an editor that has the repo open is not an interpreter running a JARVIS script). It uses psutil where the venv has it (agent-venv does) and PowerShell/CIM otherwise.
- **Enforce, hard limits** (`jarvis/supervisor/jobobject.py`, `jarvis/tools/governor.py`): every JARVIS process gets BELOW_NORMAL priority and EcoQoS (`PROCESS_POWER_THROTTLING_EXECUTION_SPEED`), so JARVIS yields to the owner's apps. All of them sit in a Job Object with a CPU rate **hard cap** of 20% of the machine (`JOBOBJECT_CPU_RATE_CONTROL_INFORMATION`, `CpuRate` 2000 = 1/100 percent units) and the memory ceiling above. The supervisor's tree gets this on creation (kill-on-close stays). Processes started by `jarvis-start.ps1` are adopted by `governor.py` (started by that script, one copy) into a second job **without** kill-on-close, so closing the governor never kills the face; the caps stay while any member lives. Honest limits: a process already in another job can join a new one only if Windows allows the nesting (8+), error 5 means no; it is then logged once ("job-refused") and left to the soft layer. The supervisor's job and the governor's job are each capped at 20%, so together they could reach 40%; the soft layer watches the total. The hard cap also slows the face if JARVIS as a whole exceeds 20%.
- **Enforce, soft governor** (same program, every 5 s): over budget (CPU of the last sample, RAM or GPU memory) for more than 20 s: (1) ask llama-swap to unload its models (`POST /api/models/unload`, else `GET /unload`; neither is verified against llama-swap v261 here, the program logs which one answered); (2) lower llama-server's `-t` by one (minimum 2) for the **next** load: `llama_threads.json` is written and llama-swap is restarted through `local-models.ps1 -Threads N` only when nothing is loaded; (3) duty-cycle llama-server (suspend about 2.5 s per sample, resume in a `finally`). Only llama-server is ever suspended or unloaded. The face, the kernel client, the console, the supervisor and the governor are protected: if they are the offenders it only logs ("over-budget-no-action"). Nothing is killed. Every action is a line in `D:\jarvis\data\logs\governor.jsonl`.
- **Storage:** `budget.storage_env()` is the one function: TEMP, TMP, TMPDIR, HF_HOME, HF_HUB_CACHE, XDG_CACHE_HOME, PIP_CACHE_DIR, UV_CACHE_DIR, npm_config_cache, PNPM_HOME, npm_config_store_dir, PNPM_STORE_DIR, PLAYWRIGHT_BROWSERS_PATH, ELECTRON_CACHE, ELECTRON_BUILDER_CACHE, OLLAMA_MODELS, LLAMA_CACHE and `APP_USER_DATA_PATH` (the face's Electron profile, also `budget.face_args()` = `--user-data-dir=...`). It keeps the folders `d-env.ps1` has used since 2026-10-21 (`D:\jarvis\tmp`, `D:\jarvis\cache\...`) so what is already downloaded stays valid (the owner's text said `D:\jarvis\data\...`; the old paths were kept for that reason, the face profile is under `data\face`). `scripts/d-env.ps1` (dot-sourced by jarvis-start, the installer and every start script) mirrors it, and `test_budget.py` fails if they differ. The installer and `jarvis-start.ps1` refuse with one line when a data, cache or temp path is not on D:. The supervisor refuses a manifest whose env names such a path (`ManifestError`), and refuses to start a component whose merged env has one (`StorageError`). The face's main process (`jarvis/face/.../main/jarvis/storage-lock.ts`) sets `userData`, `sessionData`, `temp`, `logs` and `crashDumps` under D: on Windows when D: exists; the network lock and CSP are untouched.
- **Local models:** `-t 3` and `ttl 120` in `llama-swap.yaml` and the profiles; the exclusive swap group already keeps one model loaded. No model or file was changed. **Over the GPU budget at `-ngl 99` (flagged, not changed): `decider-qwen3-4b` and `decider-qwen3-4b-2507`** (about 2.6 GB on the GPU, the figure given with the 2 GB decision, not measured by this change). Option: fewer GPU layers (`-ngl N`, N chosen by measuring, which also changes the 3.3 s decision time). `ling-tiny` (shared layers on the GPU with `--n-cpu-moe 16`), the vision and document models are expected to fit; not measured under this budget.
- **Guess versus measured:** nothing here was measured on the laptop. Measured elsewhere and reused: the existing model speeds and the `ling-tiny` MoE split (D-series notes). Guesses: the disk-write limit, the 1.25 memory factor, `-t 3` giving acceptable speed, the effect of the hard cap on the face's smoothness, the unload endpoint of llama-swap v261, GPU counters on this driver, EcoQoS on the face's windows. The laptop session verifies these (list in the build report).
- **Reuse (CLAUDE.md rule):** Windows Job Objects, `SetPriorityClass`, `SetProcessInformation(ProcessPowerThrottling)` and `NtSuspendProcess` are the operating system's own; psutil and PowerShell/CIM for measuring; llama-swap's own `ttl`, unload and groups. I know of no maintained open-source project that caps a named set of processes at a share of the machine and keeps their files on D: (Process Lasso is a proprietary tool and Docker/WSL limits would add a virtual disk on a drive with 8 GB free; neither vendor page was read today, so this is a lead, not a verified comparison). The glue (about 900 lines with tests, see `docs/01_RESEARCH_AUDIT.md`) is custom.

## D51 (2026-10-25): online models reason, local models execute; simple requests are answered locally first
- **Owner's words:** "use local model MoE", "for the simple tasks", "provider models only do reasoning and execution will be done by local models".
- **Rule:** an online provider (Dahl, Groq, Cerebras) only reasons: it understands the goal and writes a short plan with a success check per step, from redacted text. It does not emit PC tool calls and never sees a screenshot. The local MoE model on this laptop (Ling-3.0-tiny-heretic; decider and parse models as before) executes: it picks the exact tool, the arguments and the UI target, through the desktop decider and coordinator, and the kernel still permits or gates every action (D27).
- **Simple requests:** the gateway puts the local model first when a request is short and carries at most 3 tools (`prefer_first`, `prefer_max_tokens` 3000, `prefer_max_tools` 3 on the `local` provider). A local error of any kind falls through to the online chain. Big or tool-heavy requests still go online first until the on-screen benchmark (`evals/onscreen_bench/PROTOCOL.md`) shows the local model can take them.
- **Why:** the failures of 2026-10-25 were mostly online models forming tool-call JSON (Dahl 400 "Unterminated string"), rate caps, and the old toolset; execution on the laptop removes the first, spends fewer online tokens, and keeps screen content on the PC.
- **Not measured:** the local MoE's speed and tool accuracy on this laptop (estimates only, `01_RESEARCH_AUDIT.md`). The benchmark decides whether the thresholds move.

## D52 (2026-10-25): the face's chat request is about 8 KB, not 40 KB; no second model request beside a turn

- **What the 40 KB were** (measured by building the request with the real orchestrator, tool factories and prompts; `jarvis/face/packages/stage-ui/src/stores/chat/request-budget.test.ts`, no network): 31,125 bytes of tool definitions (15 tools: AIRI's `builtIn_emitSparkCommand` 5,971, `configure_wake_words` 3,523, `builtIn_proposeRecipe` 3,931, memory x3 3,980, `stage_widgets` 2,543, `image_journal` 1,796, `builtIn_drawCard` + `builtIn_searchIcons` 2,930, `builtIn_useRecipe` 1,715, the debug random-number tool 930, the MCP proxies 2,191 and `get_weather` 1,615), every tool carrying two extra arguments `call_summary` + `call_icon` (about 670 bytes a tool, 10 KB in all); persona 4,162; emotion rules in the user message 1,718; memory prompt 877; card prompt 630; format rules 437; Live2D controls about 480 (stand-in). The MCP tool list of D49 is not in this request; it arrives as a tool result and adds about 6.4 KB to the next one.
- **The second request** (about 8 KB, seen beside a turn whenever a tool call finished): the chat's *tool cards* (`stores/tool-cards.ts`). When a call to a tool finishes, the face asks the chat model, on the same provider and rate limit, to design a card for the result: the card-language rules (about 8 KB) plus the call, once for each new tool, with a second round when the program does not validate, and a tool (`builtIn_searchIcons`) for icon names. It is a convenience, not a JARVIS capability. Now: tool cards and reply cards are **off by default** (Settings turns them on), and when on, a card is made only when the owner unfolds the row of a call, never for a call that just finished. The voice end-of-speech judge and the transcript rephrase (`hearing` settings, both on by default) are two more small model requests for each *spoken* turn; they are **not changed** here (a typed turn has none). Turn them off in Settings > Hearing if a spoken turn meets a rate limit.
- **What a request carries now:** the tools `builtIn_mcpListTools`, `builtIn_mcpCallTool` and `get_weather` (the weather card is drawn from the result of `get_weather` itself, so `stage_widgets` is not needed), each with a one-line `call_summary` label and no icon argument. `builtIn_emitSparkCommand` and the debug tool are offered only when a caller sets `airiModuleTools` (nobody does). Memory (prompt + 3 tools) goes into a request only when the owner has accepted memory entries or the message says remember/forget; the recipe tools only with a usable recipe (use) or a message about recipes or skills (propose); the wake word editor only when the message names wake words; reply cards (draw + icon search + prompt) only when the owner turns them on. `stage_widgets` and `image_journal` stay registered but are not selected. The rules are in `stores/chat/run-tools.ts` and `lean-tools.ts` (English words; a Hindi or Telugu message that wants one of these can name it in English). Persona: 4,162 to 3,550 bytes (same rules; a new "Doing things" block names `desktop__do_goal` for PC, screen and document goals, `jarvis_do` only for server work, `jarvis_recall`, `jarvis_show`, the two document readers and the `jarvis::<tool>` call form); emotion rules in the user message 1,718 to 864.

  | part | before (bytes) | after (bytes) |
  |---|---|---|
  | tool definitions | 31,125 (15 tools) | 2,056 (3 tools) |
  | persona | 4,162 | 3,550 |
  | format rules / Live2D controls (stand-in) | 437 / 484 | 437 / 484 |
  | memory prompt / card prompt | 877 / 630 | 0 / 0 |
  | owner text + time + emotion rules | about 1,850 | 984 |
  | envelope | about 250 | 234 |
  | **first request** | **about 39,900** (seen live: 40-41 KB) | **7,745** (limit in the test: 12,000) |
  | request after the tool list | about 48,000 (seen live: 48-49 KB) | 15,144 (limit 22,000) |

- **Guard:** `request-budget.test.ts` fails when the first request passes 12,000 bytes, names the part that grew, checks that the AIRI helpers listed above are absent and the honesty and routing rules are present, and `jarvis/bridge/test_face_tools.py` checks that its tool-list snapshot names the same tools as `CHAT_TOOLS`. `JARVIS_PRINT_BUDGET=1` prints the table.
- **Not verified (needs the real app on the laptop):** the actual bytes on the wire (the test has no network and uses a stand-in Live2D list and the provider adapter adds a few hundred bytes); that the models now pick the right tools; the Hindi and Telugu behaviour of the word triggers; the persona against `jarvis/persona/cases.json` with a live model (the rules were kept, not re-run). To see the real request on the laptop: open DevTools in the face's chat window (Network tab, each `POST .../chat/completions`: the Size column and the Payload tab show the bytes and the body), or print `Content-Length` in the privacy gateway's POST handler for one run (the gateway logs no sizes today and was not changed), and compare with the table. The on-screen benchmark records `request_kb` per request the same way.

## D53 (2026-10-26): JARVIS controls the PC on screen; on-screen permission buttons the owner lists are clicked without the PIN
- **Owner's words (said more than once):** "JARVIS can click for permission on screen", "Jarvis can control my PC on screen".
- **Already true (policy.py, 2026-10-16):** the owner's own request (owner_direct, private lane) runs ordinary PC actions at once: open, focus, click, type, key, scroll, minimize, maximize, restore. Opening Calculator needed no PIN in the 2026-10-26 on-screen runs. Only a click or key whose words could pay, send, delete, install, sign in or out, or accept something waits for the PIN (D27).
- **New:** `jarvis/config/auto_click.json` lists permission buttons as `{process, element}` (both match the whole name). A `ui.click` from the owner's own request on a listed button is `YELLOW_AUTO` even though `accept/agree/allow/grant/authorize/confirm` are in its words. Words for money, deleting, sending, installing, signing in and passwords are never waived (`HARD_CONSEQUENCE`). The file is console-only (in `SAFETY_COMPONENTS`): an agent cannot add itself. A missing or broken file waives nothing.
- **Not done:** the real button names. The default entry (Chrome, "Connect") is a placeholder; the laptop session adds the real name of the Playwright extension dialog after looking at it on screen. UAC and other secure-desktop prompts are not reachable by UI Automation and stay the owner's.
