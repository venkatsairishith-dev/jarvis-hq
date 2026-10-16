# PC agent research: computer-use models, browser agents, decision models (2026-10-16)

Scope: what to reuse for Neko's PC control on this laptop.
- **Hardware:** i9-9980HK (8 cores, AVX2), 32 GB RAM, Radeon Pro 5500M with 4 GB (Vulkan only, no CUDA or ROCm), Windows 10 19045.
- **Already in place:** a deterministic execution layer (Windows UI Automation through fixed PowerShell scripts; Playwright MCP with Microsoft's Chrome extension), the JARVIS kernel that authorizes every action, and llama.cpp (Vulkan) behind llama-swap.
- **Method:** facts were verified against the repositories, model cards and official docs listed under each table. Nothing was installed except the three small decision-model candidates that were benchmarked (PC_CONTROL_BENCHMARK.md).

## Four capabilities, and what fills each one
| Capability | Chosen | Why |
|---|---|---|
| Decision model (understand the request, plan, pick tools) | A small local LLM held to a JSON schema by llama.cpp's grammar, plus deterministic resolution | Measured on this laptop (benchmark). The model only extracts structure; code resolves accounts, sites and apps against facts. |
| Computer-use (visual) model | Qwen3.5-4B with its vision projector, kept for occasional one-shot visual questions only, **not installed yet** | Best verified small model on ScreenSpot-Pro (60.3, official card). Speed on this GPU is unverified and the Vulkan vision path has an open bug report, so it stays a planned fallback. |
| Browser agent | Neko's own browser agent over Microsoft Playwright MCP (`--extension`, the owner's real profiles) | The only maintained route into the owner's signed-in Chrome profiles: since Chrome 136, CDP attach is blocked on the default user-data folder. |
| Deterministic execution and verification | The existing JARVIS node (UIA, Win32, PowerShell scripts, Playwright tools on an allowlist) behind the kernel | Every action is authorized and audited, and each one is verified against observed state. |

## A. Desktop / computer-use agents and frameworks
| Candidate | License, maintenance | Windows, install | Models it needs | Security model | Verdict |
|---|---|---|---|---|---|
| **Microsoft UFO² / UFO³** ([repo](https://github.com/microsoft/UFO)) | MIT. Pushed 2026-10-14. UFO² is "LTS" | Windows ≥10, Python ≥3.10 | Documented with GPT-4o/Azure; Ollama listed (`llava`, ≥20k context) | `SAFE_GUARD` confirmations; `run_shell` uses `shell=False`; no built-in allowlist | **Borrow ideas.** UIA-first, vision-fallback control backend, safe-guard confirmations. Its Host/App planner assumes a large model. |
| **UI-TARS-desktop** ([repo](https://github.com/bytedance/UI-TARS-desktop)) | Apache-2.0. Last desktop installer v0.2.4 (2025-08-21); later releases are the Agent TARS CLI | Electron app; CLI needs Node ≥22 | UI-TARS-1.5 / Seed-VL; local serving not documented | No action-approval flow | **Skip.** Its own closed loop would bypass the kernel. |
| **UI-TARS models** ([repo](https://github.com/bytedance/ui-tars)) | Apache-2.0 | n/a | Open weights: UI-TARS-1.5-7B only | n/a | 7B Q4 is 4.4 GB plus a 0.8 GB projector: does not fit 4 GB of VRAM. Superseded. |
| **OmniParser v2** ([repo](https://github.com/microsoft/OmniParser)) | Repo shows both CC-BY-4.0 and MIT; the icon detector weights are AGPL | Python 3.12 + PyTorch; OmniTool targets a Windows 11 VM | Parser only | None | **Skip.** PyTorch (CPU only here), AGPL weights, not servable by llama.cpp. |
| **Simular Agent S3** ([repo](https://github.com/simular-ai/Agent-S)) | Apache-2.0, v0.3.2 | Windows/macOS/Linux, pip | GPT-5-class planner + UI-TARS-1.5-7B grounding | `--enable_local_env` runs arbitrary Python/Bash | **Skip.** Cloud-class planner and arbitrary code execution. |
| **Windows-MCP** ([repo](https://github.com/CursorTouch/Windows-MCP)) | MIT, v0.8.8 (2026-10-10) | Win10/11, Python 3.14+ | Any LLM; vision optional | PowerShell, registry, file and process tools ("full system access"); telemetry on by default | **Borrow ideas** (snapshot with element ids, tool allow/deny lists). It duplicates the node and widens what can run. |
| Terminator, MCPControl | MIT | Windows | Any | No approval mechanism | Not needed. |
| **Qwen Code** ([repo](https://github.com/QwenLM/qwen-code)) | Apache-2.0 | Node CLI | Local models via Ollama/vLLM | Computer use through `cua-driver` plus a Node REPL that runs model-written JavaScript with full permissions; Windows path unverified | **Skip** for PC control. It is a coding CLI. |

## B. Small vision / GUI-grounding models (candidates for the occasional visual step)
| Model | Params, license | Claimed grounding | GGUF Q4 + projector | Notes |
|---|---|---|---|---|
| **Qwen3.5-4B** ([card](https://huggingface.co/Qwen/Qwen3.5-4B)) | 4B, Apache-2.0 | ScreenSpot-Pro 60.3, OSWorld-Verified 35.6 | community GGUFs, about 3.3–3.7 GB in total | General model: reads dialogs **and** grounds elements. Earlier text test on this GPU: 10.7 tok/s. |
| Holo-3.1-4B ([card](https://huggingface.co/Hcompany/Holo-3.1-4B)) | Qwen3.5 base, Apache-2.0 | scores only in images (not verified) | 3.07 + 0.37 GB | A/B alternative on the same base. |
| Fara1.5-4B ([card](https://huggingface.co/microsoft/Fara1.5-4B)) | MIT | WebVoyager 80.8 | 2.88 + 0.67 GB | Browser-only, pixels-only harness. |
| UI-Venus-1.5-2B ([card](https://huggingface.co/inclusionAI/UI-Venus-1.5-2B)) | Apache-2.0 | ScreenSpot-Pro 57.7 | GGUFs exist; llama.cpp support for its Qwen3-VL base not confirmed | Grounding-only fallback if 4B is too slow. |
| UI-TARS-1.5-7B, GUI-Actor, UGround, ShowUI, OS-Atlas | — | older or lower | too big, or no GGUF | Superseded. |

Speed on the Radeon Pro 5500M is unverified. The nearest public reports are about 32 s per image on a 16-core CPU for a 2B model, and 82 s for a 9B model on the CPU fallback. llama.cpp issue #20081 reports wrong Qwen3.5 projector output on Vulkan (closed stale; workaround: encode on CPU). Estimate: 5–30 s per cropped screenshot. Hence vision stays a **one-shot perception tool**, cropped to the window that UI Automation already identified, and is never an autonomous loop.

## C. Browser agents and benchmarks
| Candidate | Facts | Fit |
|---|---|---|
| **Playwright MCP `--extension`** ([repo](https://github.com/microsoft/playwright-mcp), [extension](https://github.com/microsoft/playwright/tree/main/packages/extension)) | Connects to the running Chrome; `--profile-dir-name` picks the profile; manual approval of each connection unless a per-profile token is set; one tab group per client; "not a security boundary"; `browser_run_code_unsafe` = arbitrary code | **Used.** Neko calls an allowlist of tools only, and in pages one fixed script. |
| browser-use ([repo](https://github.com/browser-use/browser-use)) | MIT, 0.13.11; Ollama support; real-profile use needs Chrome closed, or CDP | CDP attach to the default profile is blocked since Chrome 136, so not for the owner's live profiles. |
| Stagehand ([repo](https://github.com/browserbase/stagehand)) | MIT; local Chrome via `userDataDir` or CDP | Same CDP limit; local-model support unverified. |
| **BrowserGym** ([repo](https://github.com/ServiceNow/BrowserGym)) | Apache-2.0, 0.14.3; MiniWoB++ needs no server (a file:// clone) | Light enough. Its scenarios (forms, missing elements, ambiguity, waits) were adapted into Neko's own local pages rather than installing the gym. |
| **OSWorld-V2** ([repo](https://github.com/xlang-ai/OSWorld-V2)) | Exists; v2.1 (2026-09-16), Python ≥3.12; Docker-on-Linux (KVM) or AWS VMs, gated assets | **Not feasible** on this laptop (no Linux VM host, and an Ubuntu VM would compete for the 32 GB). Not run. |

## D. Small local decision models (tool routing, structured plans)
Published tool-calling numbers (BFCL v3 unless noted; vendor-reported, not comparable across versions):

| Model | Size Q4_K_M | License | Published | Benchmarked here |
|---|---|---|---|---|
| Qwen3-4B-Instruct-2507 | 2.38 GB | Apache-2.0 | BFCL v3 61.9 | yes |
| Granite-4.0-1B | 0.98 GB | Apache-2.0 | BFCL v3 54.8 | yes |
| LFM2.5-1.2B-Instruct | 0.70 GB | LFM Open License v1.0 | BFCL v3 49.1 (Liquid's own scoring) | yes |
| Qwen3-1.7B, Qwen3-4B (hybrid thinking) | 1.03 / 2.33 GB | Apache-2.0 | BFCL v3 46.3 (1.7B) | yes (already on disk) |
| Ling-3.0-tiny (the existing router) | 4.49 GB MoE | MIT | — | yes |
| xLAM-2-3b-fc-r | 1.93 GB | **CC-BY-NC-4.0** | BFCL v3 65.1 | no: non-commercial licence |
| Phi-4-mini, SmolLM3-3B, Llama 3.2 3B, Gemma 4 E2B | 2–3 GB | various | not comparable or none | no: not better than the above |

## Recommendation (as built)
1. **No framework is adopted whole.** Each one either assumes a cloud-class planner (UFO², Agent S3) or runs its own action loop with broad rights and no approval step (Windows-MCP, UI-TARS-desktop, Agent S3), which would sidestep the kernel.
2. **Reused:** Playwright MCP and its extension (browser); llama.cpp + llama-swap (models); the node's UIA scripts (desktop). **Ideas borrowed:** UIA first with vision only as a fallback (UFO²); element-id snapshots and tool allowlists (Windows-MCP); "critical point" stops before irreversible actions (Fara/Holo), which here is the kernel's consequential-word approval.
3. **Decision model:** the measured winner in PC_CONTROL_BENCHMARK.md, used only to extract structure under a JSON schema. Accounts, sites, apps, keys and deliverables are resolved deterministically. An invalid or unclear plan becomes a question to the owner, never a guess.
4. **Vision:** Qwen3.5-4B (or Holo-3.1-4B) only when UIA and DOM cannot see the needed element; cropped, one-shot, sequential, and swapped in by llama-swap (one model on the GPU at a time). Not installed in this round: no current test needed it, since every dialog and control so far was readable through UIA, including classic Win32 dialogs via class names. That is recorded as an open item, not a capability.
