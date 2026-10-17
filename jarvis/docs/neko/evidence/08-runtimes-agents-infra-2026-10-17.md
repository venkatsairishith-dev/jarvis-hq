# Neko infrastructure research (2026-10-17)

Web research only. Nothing was installed or run.
Labels: **[M]** = measured by a named party (paper or leaderboard); **[V]** = vendor/author claim; **[J]** = my judgement;
**[U]** = recalled, not re-verified today. Repo stats come from the GitHub API on 2026-10-17.

---

## 1. Inference runtimes

| Runtime | Licence | Latest / activity | Fit for 4 GB AMD Vulkan + CPU |
|---|---|---|---|
| llama.cpp | MIT | b11706 (2026-10-17). Several builds a day. 131k stars, 2,563 open issues | **Best fit.** You are on b11669, about 37 builds behind |
| llama-swap | MIT [U] | v265 (2026-10-14). Releases every few days | Keep it, or replace it with the llama-server router (see below) |
| Ollama | MIT | v0.40.5 (2026-10-14). Wraps llama.cpp and MLX | Vulkan is experimental and needs OLLAMA_VULKAN=1. Adds a layer and gives no extra control. No |
| vLLM | Apache-2.0 | v0.31.0 (2026-10-05) | Built for big NVIDIA/ROCm GPUs. No Vulkan. CPU wheels are aimed at servers (AMX/AVX512). **Not relevant** [J] |
| SGLang | Apache-2.0 [U] | active | Same as vLLM [J] |
| MLC-LLM | Apache-2.0 | last push 2026-10-06. 359 issues | Has a Vulkan backend, but models need compiling. Its JSON-schema support is weaker than llama.cpp's grammar engine and it has fewer GGUF models [J] |
| ONNX Runtime GenAI | MIT | v0.17.0 (2026-09-28). v0.16 added a guidance constrained-output engine, WebGPU and an AMDGPU EP | DirectML/WebGPU could work on Windows, but the models are separate ONNX exports. A Windows-only fallback at most [J] |
| bitnet.cpp | MIT | last push 2026-07-27 (slowing) | CPU-only 1.58-bit models. Few useful models. A curiosity only [J] |
| LM Studio | App is proprietary. lms CLI, SDKs and MLX engine are MIT | active | A closed GUI around the same llama.cpp. No |

**llama.cpp in 2026 (from the server README, fetched today)**
- **Router mode** (`--models-dir`, `--models-preset` INI, `--models-max N` default 4, `--models-autoload`) lets one llama-server load and unload several models. This overlaps with llama-swap. Community reports say there is no single unload-all endpoint. With `--models-max 1` it behaves like a swap.
- **Structured output:** `--grammar`, `--json-schema`/`-jf`, and `response_format` with a schema on /v1/chat/completions.
- **Tool calling:** OpenAI-style through `--jinja`, which is now on by default.
- **Multimodal:** `--mmproj` for images and audio, and video input. Local files need `--media-path`.
- **APIs:** OpenAI chat, completions, embeddings and responses routes, plus Anthropic Messages compatibility.
- **Batching:** parallel slots (`-np`), continuous batching on by default, `/slots` with saving and restoring prompt caches.
- **New risk surface [M, from the README]:**
  - `--tools` and `--agent` give the server built-in file read/write, grep and **shell**.
  - `--mcp-servers-config` starts MCP child processes with the server's privileges.
  - `--ui-mcp-proxy` exists.
  - **By default CORS reflects any Origin, with credentials allowed.** It is limited to localhost only when `--tools` or `--agent` is on.
  - The web UI is on by default. The default bind address is 127.0.0.1. `--api-key` exists, and `/health` stays public.
  - **Action:** start llama-server with `--no-ui`, an `--api-key`, no `--tools`, `--agent` or MCP flags, and keep it behind the kernel proxy. Pin the build. Any web page the owner's Chrome visits can call a localhost server whose CORS is open.
- **Vulkan on RDNA1:** no 2026 data was found for the mobile RX 5500M or 5600M. In older Debian tests on the desktop RX 5600 XT, Vulkan matched ROCm after fixes. ROCm has dropped RDNA1, so Vulkan is the only GPU path.
  - RDNA1 has no matrix cores, so llama.cpp uses its generic shaders (no coopmat) [J].
  - Keep a CPU fallback through `-ngl` partial offload.

**Recommendation [J]:** stay on llama.cpp. Upgrade to the latest build in a pinned, tested way. Within a single model family, llama-server router mode (`--models-max 1`) could replace llama-swap and remove one moving part. Keep llama-swap if you need its swap matrix (groups that run together) or its UI.
- **Concurrency:** on 4 GB, use `-np 1-2`. VRAM for the KV cache is the limit, so quantise the KV cache (`-ctk q8_0`).
- Keep the json_schema grammar. It is still the most mature open constrained-decoding path on this hardware.

Links: https://github.com/ggml-org/llama.cpp/releases · https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md ·
https://github.com/mostlygeek/llama-swap/releases · https://docs.ollama.com/gpu · https://lists.debian.org/debian-ai/2025/01/msg00170.html ·
https://github.com/microsoft/onnxruntime-genai/releases · https://github.com/vllm-project/vllm/releases · https://github.com/microsoft/BitNet

---

## 2. Browser and computer use

**Playwright** (Apache-2.0)
- v1.64.1 (2026-10-14).
- v1.64 added `page.webmcp` (experimental WebMCP: tools that a page registers), `Locator.within()` and `page.getByRef()`.
- Deterministic. Keep it.

**Playwright MCP** (Apache-2.0 [U])
- v0.0.83 (2026-09-28). Security notes from the release notes:
  - v0.0.82 exposes the page's **WebMCP tools as MCP tools**. The release notes themselves say these are untrusted input.
  - v0.0.81 **turns off the Chromium sandbox by default on Linux** for the bundled build.
  - v0.0.81 fixed a symlink escape in file-root checks, and stopped one client's recorded actions from reaching another client under `--shared-browser-context`.
  - `--profile-dir-name` selects a Chrome profile in extension mode.
- **Action:** pin the version. Filter out `webmcp_*` tools in the kernel policy, or treat them as untrusted, approval-gated tools.

| Tool | Licence / activity | Driven by | Windows | Benchmarks (who measured) | Value over a11y-first + DOM-first |
|---|---|---|---|---|---|
| browser-use | MIT [U]. v0.13.13 (2026-10-15) | LLM every step | yes (browser) | Online-Mind2Web numbers are mostly self-reported. Vendor tables mix sources [V] | Low. Our Playwright and policy layer is stricter [J] |
| Stagehand | MIT. Very active | Mixed: act/extract/observe with action caching | yes | Nothing independent found | Low to medium. Its "observe then cache" idea is worth copying, not the library (TypeScript-first) [J] |
| Skyvern | **AGPL-3.0** | LLM + vision | yes | WebVoyager figure is self-reported [V/U] | No (licence, heavy) |
| OpenAI Operator / CUA | proprietary, cloud | model | n/a | Online-Mind2Web 61.3% human-judged, from the OSU paper's own runs [M]. Others about 30% [M] | No. Cloud model, so private data leaves |
| Microsoft UFO2 / UFO3 | MIT. 10k stars, pushed 2026-10-14. UFO2 is LTS. UFO3 "Galaxy" is experimental | LLM using UIA + Win32 + COM + vision | **Windows native** | UFO2 paper [M by authors, arXiv 2504.14603]: WAA 30.5% / OSWorld-W 32.7% (o1). Operator 20.8% / 14.3%. **UIA-only scored 25.3% vs hybrid 27.9% on WAA; OmniParser-only was worse (20.8%)** | Medium as a **source of ideas**: hybrid UIA + vision detection, batching several actions to cut LLM calls (−51% [V]), and calling app APIs over GUI. The framework is cloud-model oriented with YAML API keys [J] |
| Windows-MCP | MIT. 9.3k stars. Pushed 2026-10-16. 24 issues | Deterministic tools (UIA tree, click, type) plus a **PowerShell/shell tool** | yes | none | Medium as a **reference**. Do not expose its shell tool. Our UIA layer already does this [J] |
| pywinauto | BSD-3. Pushed 2026-10-15. 524 open issues | Deterministic UIA/Win32 | yes | n/a | Medium. A tested Python UIA wrapper that could replace hand-written ctypes paths. Its release cadence is slow [U] |
| OmniParser v2 | Code CC-BY-4.0. **icon_detect weights AGPL-3.0**, icon_caption MIT. Last push 2026-07-20 | Vision parsing | yes | ScreenSpot-Pro 39.6 [V] | **No.** AGPL weights, and UI-Venus already does grounding. UFO2's ablation shows OmniParser alone is weaker than UIA [M] |
| UI-TARS-1.5 / UI-TARS-desktop | Apache-2.0. 39k stars | End-to-end VLM | yes | OSWorld 42.5 (100 steps), WAA 42.1 (50 steps) [V, ByteDance] | 7B is too large for 4 GB VRAM. A possible grounding fallback on CPU only [J] |
| UI-Venus-2-9B | ? | VLM | – | ScreenSpot-Pro 73.0, OSWorld-Verified 70.8 [V, no independent check] | Watch it. 9B does not fit 4 GB [J] |
| Agent S3 (Simular) | Apache-2.0. Pushed 2026-10-08 | LLM planner + grounding model. **Runs generated Python (pyautogui)** | yes | OSWorld 66% (100 steps), 72.6% with best-of-N. WAA 50.2% → 56.6% with 3 rollouts [V] | No. It executes arbitrary code and needs a frontier model [J] |

**Leaderboards**
- OSWorld-Verified: frontier agents now score about 83-90% against a human baseline of 72.36% (official board, as reported by presenc.ai). Vendor self-reports and official-board entries differ.
- Windows Agent Arena official board: still only human 74.5% and Navi 19.5% [M, Microsoft 2024].

**Judgement [J]:** the measured evidence (UFO2's ablation) supports **accessibility first, vision as fallback**. Hybrid detection adds roughly 2-4 points over UIA alone. Pure vision is worse. No framework here beats a deterministic UIA + DOM core for a personal assistant. Borrow ideas instead:
1. a hybrid detector that merges UIA controls with grounding boxes;
2. batching several deterministic actions per model call;
3. calling an app's API or COM before driving its GUI.

Links: https://github.com/microsoft/playwright/releases · https://github.com/microsoft/playwright-mcp/releases ·
https://arxiv.org/html/2504.14603v2 · https://github.com/microsoft/UFO · https://github.com/CursorTouch/Windows-MCP ·
https://huggingface.co/microsoft/OmniParser-v2.0 · https://github.com/simular-ai/Agent-S · https://arxiv.org/abs/2504.01382 ·
https://presenc.ai/research/osworld-computer-use-leaderboard-2026 · https://arxiv.org/abs/2409.08264

---

## 3. Orchestration, reliability and safety

**Durable execution**
- **DBOS Transact-py** (MIT): 3.3.0 (2026-10-15). 6 open issues. Very active.
  - 3.3.0 moved the Conductor client into an `enterprise` extra. Open-source users are unaffected.
  - Library only; Postgres or SQLite. **Keep it** [J].
- **Temporal** (MIT SDK): needs a separate server cluster. Too heavy for one laptop [J].
- **Restate**: GitHub reports the licence as NOASSERTION. The server is BSL 1.1 [U]. Needs a server process. No [J].
- **LangGraph checkpointers** (MIT): carry deserialization CVEs:
  - **CVE-2025-64439**: JsonPlusSerializer RCE, fixed in checkpoint 3.0.0.
  - **CVE-2026-48775**: in the SQLite checkpointer, fixed in 4.1.1 (per SentinelOne, not checked on NVD).
  - Neither is a reason to switch [J].
- **Idempotency:** DBOS gives workflow IDs plus step checkpoints. The side-effect journal should also carry an **idempotency key per external action**, derived as hash(workflow_id, step_id, args), and send it wherever the API accepts one (for example Stripe- or Gmail-style headers) [J].
  - At-least-once step retry plus a non-idempotent side effect is the classic double-send.

**Typed tools and constrained decoding**
- Pydantic AI (MIT, 2.57.0) has a DBOS durable-execution integration.
- Instructor (MIT, 1.17.0, 2026-09-09).
- Outlines (Apache-2.0, 1.3.3).
- XGrammar (Apache-2.0, 0.2.8).
- [J] llama.cpp's own json_schema grammar already gives server-side constraint. Outlines and XGrammar need in-process inference (transformers or vLLM), so they add nothing on llama-server. Instructor and Pydantic AI only add client-side validate-and-retry. A thin Pydantic validator in the kernel is enough.

**MCP**
- The current specification is **2026-07-28** (GA). It **removes sessions and the initialize handshake (stateless)**, adds `server/discover`, and adds MRTR (input_required results) in place of server-initiated sampling and elicitation.
- Tasks are now an extension. Roots, Sampling and Logging are deprecated.
- **Auth:** RFC 9207 `iss` validation is now a MUST for clients. Credentials are bound to the issuing authorization server. **Dynamic Client Registration is deprecated in favour of Client ID Metadata Documents.**
- inputSchema and outputSchema accept full JSON Schema 2020-12, with `$ref` bounds.
- [J] Playwright MCP and other servers will move over gradually. The kernel's MCP client should pin the protocol version and treat every tool description and result from a server as untrusted data.

**Prompt-injection defences: evidence**
- **CaMeL** (Google DeepMind, arXiv 2503.18813): **77% of AgentDojo tasks with provable policy security vs 84% undefended** [M by the authors]. Design: a privileged planner that never sees untrusted data, a quarantined LLM, and capability tags on values.
  - Limit: plans cannot depend on untrusted content.
  - Our kernel (deterministic policy, single-use approvals, lanes) is already close to this. Add **taint and provenance tags on data flowing into tool arguments** [J].
- **Spotlighting** (Microsoft, arXiv 2403.14720): attack success fell from above 50% to below 2% on GPT-3.5-era models, on the authors' own 1,000-document set [M by the authors]. Base64 encoding works only with strong models.
  - Datamarking is cheap and helps small models somewhat [J].
- **Llama Prompt Guard 2** (86M multilingual mDeBERTa / 22M English): Llama 4 Community Licence, gated, 512-token window.
  - Meta's LlamaFirewall paper: AgentDojo attack success **−57%** with 1.5% utility loss [V/M by Meta].
  - A third-party critique reports 7-37% detection on indirect and agentic injections [unverified].
- **"The Attacker Moves Second"** (arXiv 2510.09023; authors from OpenAI, Anthropic and Google DeepMind): adaptive attacks broke 12 published defences, **most at above 90% attack success**.
  - [J] Classifiers are a tripwire, not a boundary. Architecture (policy, approvals, taint) is the boundary.

Links: https://github.com/dbos-inc/dbos-transact-py/releases · https://modelcontextprotocol.io/specification/2026-07-28/changelog ·
https://arxiv.org/abs/2503.18813 · https://arxiv.org/abs/2403.14720 · https://arxiv.org/pdf/2505.03574 · https://arxiv.org/abs/2510.09023 ·
https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-22M · https://cvefeed.io/vuln/detail/CVE-2025-64439

---

## 4. Memory

| System | Licence | Activity | Local-first? | Evidence |
|---|---|---|---|---|
| Mem0 | Apache-2.0 | 67k stars, 840 issues, pushed 2026-10-16 | Possible with local LLM, embedder and vector store, but it is built around LLM fact extraction | LoCoMo 92.5% / LongMemEval 94.4% [V], **from its managed platform, not the OSS SDK** |
| Zep / Graphiti | Graphiti Apache-2.0. Zep cloud proprietary | Graphiti pushed 2026-10-15 | Graphiti needs a graph DB (Neo4j or FalkorDB) | LoCoMo dispute: Zep claimed 84%, Mem0 re-ran it at 58.44%, Zep countered with 75.14% (getzep/zep-papers#5). LongMemEval 71.2% (GPT-4o) [V] |
| Letta (MemGPT) | Apache-2.0 | pushed 2026-09-10 | Server plus Postgres. The agent edits its own memory | No independent results found |
| LangMem | MIT | 1.7k stars | LangGraph-bound | none |
| A-MEM | MIT | **last push 2025-12** (stale) | research code | paper only |
| sqlite-vec | MIT / Apache-2.0 | stable 0.1.9. 0.1.10 still alpha (May 2026). Single maintainer | **yes, embeds in SQLite** | n/a |
| LanceDB | Apache-2.0 | 0.40.0 | yes, embedded | n/a |
| Chroma | Apache-2.0 [U] | active | yes | n/a |

**Judgement [J]**
- No independent, like-for-like LoCoMo or LongMemEval audit exists. Every headline number is a contested vendor number.
- Our kernel store already does versions plus verified deletion. Mem0, Letta and Zep all add LLM-written memories, which brings injection-into-memory risk and makes deletion harder.
- **Adopt only sqlite-vec**, as an optional vector index inside the existing SQLite store, next to BM25 (hybrid retrieval).
- **Borrow Graphiti's bi-temporal "valid_from / invalid_at" fact model** as a schema idea.
- Build our own small LongMemEval-style test subset rather than trusting vendor scores.

Links: https://github.com/mem0ai/mem0 · https://github.com/getzep/graphiti · https://github.com/getzep/zep-papers/issues/5 · https://github.com/asg017/sqlite-vec ·
https://mem0.ai/blog/ai-memory-benchmarks-in-2026

---

## 5. Evaluation

| Framework | Licence / activity | Local CPU vs OpenAI-compatible endpoint? | Subset that fits Neko |
|---|---|---|---|
| BFCL v4 (gorilla) | Apache-2.0. Pushed 2026-04 | Yes. Has an OpenAI-compatible handler [U]. EvalScope also wraps it | simple, multiple, parallel, irrelevance/hallucination and multi-turn. **Epoch AI rates v4 "flawed": 24 of 50 sampled tasks had defects** [M] |
| τ²/τ³-bench (Sierra) | MIT. Very active (2026-10-16). The package is still called `tau2`. v1.0.1 re-graded banking | Uses LiteLLM, so a local endpoint should work [U]. A user simulator LLM is needed. Voice mode is cloud-only | retail and airline (policy following). Pin the version |
| AgentDojo (ETH) | MIT. Last push 2026-06-02 | Yes, through its local/OpenAI-compatible option [U]. Also an Inspect bridge (AgentDojo-Inspect, NIST) | workspace, slack, banking and travel injection suites. **Best fit for the kernel** |
| InjecAgent | [U] MIT | yes | the 1,054 indirect-injection cases. A cheap smoke test |
| Inspect AI (UK AISI) | MIT. Pushed 2026-10-17 | **Yes**: `openai-api/<name>/<model>` with a base URL, plus ollama and llama-cpp-python providers | inspect_evals has more than 200 evals, including GAIA and BFCL v1 |
| promptfoo | MIT. **OpenAI acquired it (announced 2026-03-09)**. Still OSS | yes | red-team and regression YAML. Watch its governance |
| DeepEval | Apache-2.0 [U] | yes, but its default judges are cloud | Its LLM-as-judge depends on a strong judge. Low value |
| lm-evaluation-harness | MIT [U] | yes (`local-chat-completions`, gguf) | Capability sanity checks after model swaps (IFEval, GSM8K) |
| WebArena / VisualWebArena | Apache-2.0 [U] | needs several website Docker containers. Heavy | No |
| OSWorld / WindowsAgentArena | Apache-2.0 / MIT [U] | **need KVM VMs. VT-x is off here** | No. Note WAA as "when home" work |
| ToolBench / StableToolBench | Apache-2.0 [U] | needs a GPT-4 judge and API simulation | No |

**Judgement [J]**
- Keep the 572-case benchmark as the gate.
- Add AgentDojo (injection, under the kernel's real policy) and a BFCL subset (tool-call format, on the private lane through llama-server).
- Inspect AI is the cleanest local harness to run them under.
- Do not chase leaderboard comparisons; use them as regression signals only.

Links: https://github.com/ShishirPatil/gorilla · https://epoch.ai/benchmarks/berkeley-function-calling-leaderboard/review · https://github.com/sierra-research/tau2-bench ·
https://github.com/ethz-spylab/agentdojo · https://inspect.aisi.org.uk/providers.html · https://appsecsanta.com/promptfoo

---

## Ranked low-risk adoptions

| # | Adoption | Why | Effort | Risk |
|---|---|---|---|---|
| 1 | **Harden llama-server flags**: `--no-ui`, `--api-key-file`, no `--tools`, `--agent` or MCP flags, pinned build, plus a test that asserts the flags | Default CORS reflects any Origin with credentials, and the new shell and file tools exist | S | Very low |
| 2 | **Upgrade llama.cpp** from b11669 to a pinned recent build, after running the 572-case benchmark | Fixes, router mode, newer chat templates | S | Low (regressions are caught by the benchmark) |
| 3 | **Treat Playwright MCP WebMCP tools as untrusted** (deny `webmcp_*` by default). Pin v0.0.83. Check the Chromium sandbox setting on Linux | Page-supplied tool schemas are an injection channel | S | Very low |
| 4 | **AgentDojo through the kernel policy** (+ InjecAgent smoke test) as a security regression suite | The only measured, reproducible injection benchmark that fits | M | Low |
| 5 | **Taint and provenance tags** on values from untrusted sources. Policy denies tainted data in sensitive tool arguments without approval (CaMeL-lite) | The best evidence-backed defence. Classifiers fail against adaptive attacks | M | Low to medium |
| 6 | **Idempotency keys** in the DBOS side-effect journal: hash(workflow, step, args) checked before each external action | Prevents double-sends on retry | S | Very low |
| 7 | **sqlite-vec** as an optional hybrid (BM25 + vector) index in the memory store | Local, embedded, permissive licence | M | Low (0.1.x; single maintainer) |
| 8 | **Spotlighting datamarking** of untrusted text in prompts (both lanes) | Cheap. Measured gains on older models | S | Very low |
| 9 | **Hybrid UIA + grounding merge** (UFO2-style): UIA first, UI-Venus boxes only when UIA is empty or opaque. Batch deterministic actions | Measured +2-4 points over UIA alone in UFO2. Fewer model calls | M | Low (Windows test waits until he is home) |
| 10 | **Inspect AI** as a harness for external evals (BFCL subset, GAIA L1) against the private-lane endpoint | Mature, MIT, runs locally | M | Low |

## Not worth adopting

- **vLLM, SGLang**: no Vulkan, and on CPU they target server hardware. Useless on 4 GB RDNA1.
- **Ollama, LM Studio**: wrappers over the same llama.cpp, with less control. Vulkan is experimental in Ollama and LM Studio's GUI is closed.
- **MLC-LLM, ONNX GenAI, bitnet.cpp**: model conversion overhead and fewer models, for no gain over GGUF. bitnet's activity is slowing.
- **Skyvern** (AGPL) and **OmniParser v2 icon_detect** (AGPL weights): licence risk, and the measured value is below UIA.
- **Agent S3, browser-use, UI-TARS-desktop, UFO3 Galaxy** as frameworks: model-driven, they run generated code or need cloud models, and they bypass the kernel's deterministic policy. Their benchmark numbers are mostly vendor claims.
- **Windows-MCP's shell tool**: arbitrary PowerShell. Only its UIA ideas are worth taking.
- **Temporal, Restate**: they need a server; DBOS already covers this. Restate's server is source-available (BSL) [U].
- **Mem0, Letta, Zep** as memory: LLM-written memory, contested benchmarks, and weaker deletion guarantees than the existing store.
- **Outlines, XGrammar, Instructor**: duplicate llama.cpp's server-side json_schema grammar.
- **Prompt Guard 2 as a security boundary**: gated Llama licence, a 512-token window, and adaptive attacks get past it. At most an optional tripwire.
- **OSWorld, WAA, WebArena** here: they need KVM or heavy Docker. Defer WAA until he is home.
