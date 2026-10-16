# PC control benchmark (this laptop, 2026-10-16)

Every number comes from a saved result file in `jarvis/evals/pc_control/results/` (named in brackets).
- **Hardware:** i9-9980HK (8 cores, 16 threads, AVX2), 31.9 GB RAM, Radeon Pro 5500M 4 GB (Vulkan), Windows 10 19045.
- **Model serving:** llama.cpp b11669 (Vulkan, `-fa off`) via llama-swap v261, one model on the GPU at a time.
- **Memory figures:** "RAM" is the llama-server working set. "GPU" is the largest per-process dedicated GPU memory
  (Windows counter), so it is approximate.

## 1. Decision models: plan accuracy on PC requests
- **Task:** turn a request into steps under a JSON schema, with the deterministic resolution described in decide.py.
- **Sets:** 70 development requests (`decision_cases.json`) and 30 held-out requests (`decision_cases_holdout.json`).
  The held-out set was written before any held-out result was seen.
- **Scoring:** first tool, steps, Chrome profile, URL host, app, query, key, deliverable, action and check, as
  specified per case.
- **Validity:** every model produced valid JSON on every case (grammar-constrained).

| Model (Q4_K_M) | Size | Round 1: rules only | Round 2: + examples, enums | Round 3: + deterministic account, key and site resolution | p50 / p95 (round 3) | Cold first call | RAM / GPU |
|---|---|---|---|---|---|---|---|
| **Qwen3-4B-Instruct-2507** | 2.38 GB | – | – | **65/70 (92.9%)** | 3.26 / 5.64 s | 18.9 s | 3.6 GB / 2.6 GB |
| Qwen3-4B (hybrid, thinking off) | 2.33 GB | 49/70 (70.0%) | 51/70 (72.9%) | 64/70 (91.4%) | 3.01 / 5.75 s | 21.3 s | 3.5 / 2.7 GB |
| Qwen3-1.7B | 1.03 GB | 40/70 (57.1%) | 48/70 (68.6%) | 58/70 (82.9%) | 1.00 / 1.31 s | 10.6 s | 1.6 / 1.5 GB |
| Ling-3.0-tiny (existing router, MoE) | 4.49 GB | 44/70 (62.9%) | 42/70 (60.0%) | 53/70 (75.7%) | 2.87 / 6.13 s | 17.8 s | 4.8 / 2.4 GB |
| Granite-4.0-1B | 0.98 GB | – | – | 50/70 (71.4%) | 1.17 / 2.34 s | 8.6 s | 1.3 / 1.5 GB |
| LFM2.5-1.2B-Instruct | 0.70 GB | – | – | 36/70 (51.4%) | 1.07 / 2.56 s | 2.5 s | 0.9 / 1.1 GB |

**Held-out, blind** (round-3 code, [decision-20261016-132535]):

| Model | Correct | p50 / p95 |
|---|---|---|
| Qwen3-4B-Instruct-2507 | **27/30 (90.0%)** | 1.78 / 3.63 s |
| Qwen3-4B | 25/30 (83.3%) | 2.36 / 5.78 s |
| Qwen3-1.7B | 23/30 (76.7%) | 0.67 / 0.95 s |

**After one fix** (media keys the owner names outright, e.g. "next song", are read from the request; this was found on
suite run 1 and covered by a regression test): dev **69/70**, held-out **29/30**
[suite-20261016-134136]. The held-out set is no longer blind for the two key cases; the blind score above stands.

**Notes:**
- Round 3 overlapped other local tests for a few minutes, so its latencies are slightly pessimistic.
- The earlier finding that Qwen3.5-4B (vision-capable) is slow (10.7 tok/s) was not repeated. Its text-only sibling
  Qwen3-4B-Instruct-2507 is fast enough here, because a plan is short (under 150 tokens).

**Choice:**
- **Threshold:** at least 90% on dev and on held-out.
- **`decider` = Qwen3-4B-Instruct-2507**, the only model to meet it; its hybrid sibling reached 83% on held-out.
- **Low-memory mode:** Qwen3-1.7B (1.0 s, 1.6 GB; 83% dev, 77% held-out). Plans that do not validate become questions,
  not actions.
- **Existing router (Ling-3.0-tiny):** kept for one-word routing; worse at structured plans, and larger in RAM.
- **Remote inference for decisions:** none. Before this work, PC tasks were planned by the main agent's remote model (Dahl).

## 2. Execution layers on this laptop
| Layer | Test set | Result | Typical time |
|---|---|---|---|
| Windows UI Automation (Neko node, PowerShell scripts) | 20 desktop cases on Notepad and the sample app (1 not tested: no harmless password field) | **19/19** after fixes [desktop-20261016-135405] (first run 14/20, one of them a false pass) | median 1.7 s per case, at most 7.0 s |
| Playwright MCP 0.0.83, isolated headless Chrome, local pages | 19 browser cases | **19/19** [browser-20261016-133159] (first run 17/19) | median 0.05 s per action after navigation, at most 3.9 s |
| Playwright MCP + Chrome extension, the owner's 6 real profiles | open + connect + account check per profile | **6/6** profiles: right profile, connection approved, account verified by fingerprint | one manual "Allow & select" per new connection |
| YouTube in the owner's default profile | search, choose, play, verify, pause | **pass** once the parser and checks were fixed: the chosen video id, the page title and Windows' media session agree | not timed |
| Diagnosis and recovery (sample-app copies) | 14 cases | **14/14** [diagnosis-20261016-133239] | median under 1 s, at most 5.1 s |

**Fixes made because of these runs** (each kept only with its test passing and no regression):
- **Focus:** an Alt tap that closed open menus; typing that could not take the foreground (Windows' foreground lock),
  with a retry using a thread-input hand-over.
- **Dialogs:** an app's own dialogs nested under its window; task-dialog buttons (`CCPushButton`) and classic Win32
  buttons, clicked with BM_CLICK.
- **Errors and fields:** a clean "no window" error when an app is not running; class names in snapshots, to find classic
  text fields.
- **Browser:** clicks and typing by element name, resolved to a Playwright ref; ambiguity is refused.
- **Navigation:** a retry after a failed load. On YouTube: no playlists, Shorts or ads; quoted titles parsed.
- **Cleanup:** local result files and Playwright logs swept after 10 minutes.

## 3. Windows-MCP (best open-source Windows agent in the research) vs Neko's UIA layer
- **Setup:** Windows-MCP 0.8.7 (MIT; 0.8.8 was under two weeks old), isolated venv `D:\jarvis\tools\windows-mcp-venv`,
  149 MB, telemetry off, only `Snapshot` and `Type` enabled.
- **Surface:** 20 tools, including PowerShell, Registry, FileSystem and Process kill. That is far wider than the node's
  allowlist.
- **Protocol:** it speaks only the 2026 MCP protocol, and slowly: the node's MCP client needed a 60 s discovery wait.
- **What it sees:** its snapshot (native COM UI Automation) names Notepad's field `edit "Text Editor"` with coordinates.
  Neko's .NET UIA layer sees a `Pane` of class `Edit`. Borrowable idea: COM UIA (comtypes) for richer control types.
- **One clean single run:** snapshot 2.5 s on the whole desktop (100 lines).
- **Five-round comparison: inconclusive.**
  - The first scripts had bugs.
  - Later rounds ran while Visual Studio Code was hung ("Not Responding" ghost window in front). Windows-MCP's
    whole-desktop snapshot then hung (60 s timeouts, 5/5).
  - Neko refused to type, with "target is not the foreground window" (0/5 typed, nothing typed into the wrong window).
  - To be re-run on a healthy desktop; see the test report.

## 4. Vision (computer-use) model: not benchmarked
Qwen3.5-4B with its vision projector is the researched choice for the rare visual step (PC_AGENT_RESEARCH.md). It was
**not installed**, for two reasons:
- **No need yet:** no test in this round needed vision, because every dialog and control was readable through UI
  Automation or the DOM.
- **Unverified cost:** an estimated 3.3–3.7 GB of weights plus an image encode measured at 5–30 s or more elsewhere,
  with an open Vulkan projector bug.

It stays an open item, with a memory plan: llama-swap swaps it in alone, crops to the UIA-known window, and runs one-shot only.
