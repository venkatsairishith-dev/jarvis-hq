# Licensing and monorepo plan: merging every external project into one JARVIS codebase and one installable product (as of 22 Oct 2026)

> NOT LEGAL ADVICE. These notes summarise license texts and FAQs for engineering planning only. Where a conclusion depends on interpretation (for example GPL "aggregate vs combined work", FSL "Competing Use", or dataset licenses behind voice models), it is flagged. Get a lawyer before any public or commercial distribution.

Method note: upstream LICENSE/NOTICE files were read directly on 2026-10-22 from shallow, blob-less `git clone`s of each public repo. The GitHub API and `gh` were blocked in this session, but `git clone` worked. Licence texts, FAQs and docs were fetched from the primary sites (apache.org, gnu.org, mozilla.org, creativecommons.org, ai.google.dev, v2.tauri.app, docs.astral.sh, chromium.googlesource.com, git/git). The private jarvis-hq repo was **not** re-read this session, so facts about it come from `/home/user/research_notes/JARVIS research and build plan/repo_current_state.md`.

---

## Q1. License compatibility matrix for one private repo and one distributed installer

### Takeaway
Nearly everything is permissive: Apache-2.0, MIT, BSD-2/3, MIT-licensed Everything/ES, CC-BY-4.0 Parakeet weights, Apache-2.0 Qwen/gpt-oss/Kokoro weights. All of it can be vendored, modified and shipped in one installer, provided LICENSE texts and Apache NOTICE files are carried along and modified files are marked.

The problem items fall into four groups:
- **Keep as separate programs, never linked or imported:** GPL-3.0 piper1-gpl, GPL-2.0 KasmVNC, GPL-3.0 webtop, and the AGPL parts of cua (cua-som, the cua-perception model). The better route is to replace or drop them.
- **Do not ship in a distributed installer:** non-commercial models. These are openWakeWord's pretrained CC BY-NC-SA models, Bespoke-MiniCheck-7B (CC BY-NC), Holo2 (CC-BY-NC), and possibly the IndicTTS-trained Piper voices.
- **Never vendor or redistribute:** proprietary agy (no license file) and CopilotKit's hosted "Intelligence".
- **Keep out of the tree:** FSL-licensed cua Spaces. It is usable internally, but the redistribution terms apply to all copies.
- **Ship with extra paperwork:** Gemma weights need a NOTICE line, a copy of the Gemma terms, and pass-through use restrictions. Download them at first run rather than bundling them.
- **Use, don't fork:** MPL-2.0 LibreOffice is fine unmodified, run as a separate `soffice` process.

### Cited Findings

**License texts and FAQs (rules)**
- Apache-2.0 §4 redistribution conditions: (a) "give any other recipients … a copy of this License"; (b) "cause any modified files to carry prominent notices stating that You changed the files"; (c) retain all copyright, patent, trademark and attribution notices in Source form; (d) if the Work has a NOTICE file, derivative works must include its attribution notices in a NOTICE file, in the docs, or in a third-party-notices display — [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)
- The FSF "considers the Apache License, Version 2.0 … compatible with version 3 of the GPL". The compatibility is one-way: GPLv3 code cannot go into Apache-licensed projects. The FSF "has never considered the Apache License to be compatible with GPL version 2" (because of the patent-termination and indemnification terms) — [Apache: GPL compatibility](https://www.apache.org/licenses/GPL-compatibility.html)
- On aggregates, the GPL FAQ says: "An 'aggregate' consists of a number of separate programs, distributed together on the same CD-ROM or other media". "Pipes, sockets and command-line arguments are communication mechanisms normally used between two separate programs", but exchanging complex internal data structures can make them one program. "If modules are designed to run linked together in a shared address space, that almost surely means combining them into one program." — [GNU GPL FAQ #MereAggregation](https://www.gnu.org/licenses/gpl-faq.html#MereAggregation)
- GPLv2 and GPLv3 are mutually incompatible. "GPLv2 or later" code is compatible with GPLv3 — [GNU GPL FAQ #v2v3Compatibility](https://www.gnu.org/licenses/gpl-faq.html#v2v3Compatibility)
- A plain interpreter treats the interpreted program as data. An interpreter extended with bindings to other facilities is effectively linked to them, so in-process use of GPL Python packages counts as linking — [GNU GPL FAQ #InterpreterIncompat](https://www.gnu.org/licenses/gpl-faq.html#InterpreterIncompat)
- Source obligations when shipping GPL binaries:
  - "if you distribute binaries, you must distribute the complete corresponding source code too";
  - a written offer must be valid for anyone who holds the binary;
  - GPLv3 §6(d) allows the source to sit on a different server;
  - diffs alone are not enough.
  — [GNU GPL FAQ #UnchangedJustBinary, #WhatDoesWrittenOfferValid, #SourceAndBinaryOnDifferentSites, #DistributingSourceIsInconvenient](https://www.gnu.org/licenses/gpl-faq.html)
- MPL-2.0 copyleft is file-level ("The copyleft applies to any files containing MPLed code"). New files with no MPL code are not Modifications. When you distribute executables, you must tell recipients where to get the source of the MPL parts. "The MPL only creates obligations for you if you want to distribute the software outside your organization." Passing on unchanged executables someone else built normally needs nothing more — [MPL 2.0 FAQ Q1, Q5, Q7, Q8, Q10–Q12](https://www.mozilla.org/en-US/MPL/2.0/FAQ/)
- CC BY-NC-SA 4.0:
  - "NonCommercial means not primarily intended for or directed towards commercial advantage or monetary compensation";
  - attribution (creator, copyright notice, licence notice, disclaimer, URI, indication of modifications) is required whenever the material is shared;
  - adapted material must stay under BY-NC-SA or a compatible license.
  — [CC BY-NC-SA 4.0 legal code](https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.en)
- FSL-1.1-MIT:
  - The grant covers "use, copy, modify, create derivative works … and redistribute the Software for any Permitted Purpose".
  - A Competing Use is "making the Software available to others in a commercial product or service" that substitutes for it or offers substantially similar functionality.
  - Permitted Purposes include "your internal use and access", non-commercial education and non-commercial research.
  - "The Terms and Conditions apply to all copies, modifications and derivatives". Redistributors must include a copy of or link to the terms and keep copyright notices.
  - Each version becomes MIT "on the second anniversary of the date we make the Software available".
  — [cua apps/cua-spaces/LICENSE (FSL-1.1-MIT text)](https://github.com/trycua/cua/blob/main/apps/cua-spaces/LICENSE); [fsl.software](https://fsl.software/)
- Gemma Terms of Use, last modified 1 April 2026. §3.1 requires redistributors to:
  - include the §3.2 use restrictions "as an enforceable provision" in downstream agreements;
  - "provide all third party recipients … a copy of this Agreement";
  - mark modified files;
  - include a Notice file reading "Gemma is provided under and subject to the Gemma Terms of Use found at ai.google.dev/gemma/terms".

  §3.2 incorporates the Prohibited Use Policy by reference and reserves Google's right "to restrict (remotely or otherwise) usage" — [Gemma Terms of Use](https://ai.google.dev/gemma/terms)

**Per-component licenses (verified from upstream LICENSE files, 2026-10-22, unless noted)**

| Component | Verified license (file) | Notes | Source |
|---|---|---|---|
| N.E.K.O | Apache-2.0; pyproject `requires-python = "==3.11.*"` | base of jarvis-hq | [Project-N-E-K-O/N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) |
| N.E.K.O.-Tauri | Apache-2.0 + **NOTICE** ("Project N.E.K.O. Copyright 2025-2026 Project N.E.K.O. Team; Copyright (c) 2025 Hongzhi Wen") | last commit 2026-07-29 | [N.E.K.O.-Tauri NOTICE](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri/blob/main/NOTICE) |
| Tauri | dual MIT / Apache-2.0 (LICENSE-MIT, LICENSE-APACHE-2.0) | | [tauri-apps/tauri](https://github.com/tauri-apps/tauri) |
| Hermes Agent | MIT ("Copyright (c) 2025 Nous Research"); `requires-python = ">=3.11,<3.15"` | | [NousResearch/hermes-agent LICENSE](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE) |
| OpenCode | MIT ("Copyright (c) 2025 opencode"); `packageManager: bun@1.4.2` | Bun/TypeScript | [sst/opencode LICENSE](https://github.com/sst/opencode/blob/dev/LICENSE) |
| goose | Apache-2.0 | Rust | [block/goose LICENSE](https://github.com/block/goose/blob/main/LICENSE) |
| Codex CLI | Apache-2.0 + **NOTICE** ("OpenAI Codex Copyright 2025 OpenAI"; includes code derived from Ratatui, MIT, "Copyright (c) 2016-2022 Florian Dehau") | Rust, Bazel build (MODULE.bazel) | [openai/codex NOTICE](https://github.com/openai/codex/blob/main/NOTICE) |
| openmuse | MIT ("Copyright (c) 2026 OpenMuse contributors"); pnpm@11.19.0, node ≥22; ships `patches/metro@0.83.3.patch` | README: "Intelligence is a separate service and is not included in this repository's MIT license" | [CopilotKit/openmuse README](https://github.com/CopilotKit/openmuse) |
| OmniRoute | MIT ("Copyright (c) 2026 diegosouzapw"); v3.8.53; Next.js; node ">=22.22.2 <23 \|\| >=24.0.0 <27" | | [diegosouzapw/OmniRoute](https://github.com/diegosouzapw/OmniRoute) |
| 9router | MIT ("Copyright (c) 2024-2026 decolua and contributors"); 9router-app 0.5.104, Next ^16.1.6 | | [decolua/9router](https://github.com/decolua/9router) |
| llama.cpp | MIT ("Copyright (c) 2023-2026 The ggml authors") plus a `licenses/` dir (e.g. LICENSE-jsonhpp) | bundle third-party licence files too | [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) |
| llama-swap | MIT ("Copyright (c) 2024 Benson Wong") | Go | [mostlygeek/llama-swap LICENSE.md](https://github.com/mostlygeek/llama-swap/blob/main/LICENSE.md) |
| sherpa-onnx | Apache-2.0 | N.E.K.O CI builds a custom wheel `1.13.8+neko.kws2` | [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx); [N.E.K.O build-desktop.yml](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/.github/workflows/build-desktop.yml) |
| Silero VAD | MIT ("Copyright (c) 2020-present Silero Team") | | [snakers4/silero-vad](https://github.com/snakers4/silero-vad) |
| Parakeet TDT 0.6B v3 weights | CC-BY-4.0: "GOVERNING TERMS: Use of this model is governed by the CC-BY-4.0 license"; "ready for commercial/non-commercial use" | attribution required | [nvidia/parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) |
| IndicConformer | MIT ("Copyright (c) 2024 AI4Bhārat") (repo) | weight licence per HF card not re-checked | [AI4Bharat/IndicConformerASR](https://github.com/AI4Bharat/IndicConformerASR) |
| Kokoro | Apache-2.0 code; README: "Apache-licensed weights" | | [hexgrad/kokoro](https://github.com/hexgrad/kokoro) |
| Piper (original) | MIT ("Copyright (c) 2022 Michael Hansen"); last commit 2025-08-26 (archived) | | [rhasspy/piper](https://github.com/rhasspy/piper) |
| piper1-gpl | **GPL-3.0** (COPYING) + `licenses/` (e.g. LICENSE.g2pW-Apache-2.0) | active fork | [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl) |
| Piper Telugu voice te_IN-maya | dataset IndicTTS (IIT Madras); "License: www.iitm.ac.in/donlab/indictts/downloads/license.pdf" | **dataset licence terms not retrieved** | [piper-voices te_IN maya MODEL_CARD](https://huggingface.co/rhasspy/piper-voices/blob/main/te/te_IN/maya/medium/MODEL_CARD) |
| openWakeWord | Apache-2.0 code. README: "All of the included pre-trained models are licensed under the Creative Commons Attribution-NonCommercial-ShareAlike 4.0" | NC models | [dscripka/openWakeWord README](https://github.com/dscripka/openWakeWord) |
| Pipecat Smart Turn | BSD-2-Clause ("Copyright (c) 2024–2025, Daily") | | [pipecat-ai/smart-turn](https://github.com/pipecat-ai/smart-turn) |
| Playwright | Apache-2.0 + **NOTICE** ("Playwright Copyright (c) Microsoft Corporation") | | [microsoft/playwright](https://github.com/microsoft/playwright) |
| Playwright MCP | Apache-2.0 | Node | [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) |
| browser-use | MIT ("Copyright (c) 2024 Gregor Zunic") | | [browser-use/browser-use](https://github.com/browser-use/browser-use) |
| Windows-MCP | MIT ("Copyright (c) 2025 JEOMON GEORGE"); install needs Python 3.14+ (earlier notes) | | [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) |
| ES (es.exe) | MIT ("Copyright (c) 2025 voidtools") | | [voidtools/ES](https://github.com/voidtools/ES) |
| Everything (client) | voidtools License.txt is an MIT-style permission notice ("Copyright (C) 2018 David Carpenter … Permission is hereby granted, free of charge … without restriction"), plus a separate PCRE licence | **contradicts** the earlier note that Everything is merely "freeware" | [voidtools License.txt](https://www.voidtools.com/License.txt) |
| Smokescreen | MIT ("Copyright (c) 2016 Stripe") | Go | [stripe/smokescreen](https://github.com/stripe/smokescreen) |
| gitleaks | MIT ("Copyright (c) 2019 Zachary Rice") | Go | [gitleaks/gitleaks](https://github.com/gitleaks/gitleaks) |
| Presidio | MIT + **NOTICE** (third-party software notices) | | [microsoft/presidio](https://github.com/microsoft/presidio) |
| GLiNER | Apache-2.0 (code) | model weights vary per HF card (GLiNER-PII not checked) | [urchade/GLiNER](https://github.com/urchade/GLiNER) |
| MiniCheck | repo is **Apache-2.0** (not MIT as the brief says). README: "For commercial licensing, please contact company@bespokelabs.ai" | | [Liyan06/MiniCheck](https://github.com/Liyan06/MiniCheck) |
| MiniCheck-Flan-T5-Large weights | MIT | | [lytang/MiniCheck-Flan-T5-Large](https://huggingface.co/lytang/MiniCheck-Flan-T5-Large) |
| Bespoke-MiniCheck-7B weights | **CC BY-NC 4.0** ("For commercial licensing, please contact …") | NC | [bespokelabs/Bespoke-MiniCheck-7B](https://huggingface.co/bespokelabs/Bespoke-MiniCheck-7B) |
| python-pptx | MIT ("Copyright (c) 2013 Steve Canny"); last commit 2024-08-07 | | [scanny/python-pptx](https://github.com/scanny/python-pptx) |
| OfficeCLI | Apache-2.0 + **NOTICE** ("OfficeCLI Copyright 2026 OfficeCLI … Created and maintained by goworm") | | [iOfficeAI/OfficeCLI](https://github.com/iOfficeAI/OfficeCLI) |
| DBOS Transact (Python) | MIT ("Copyright (c) 2024 DBOS, Inc."); `requires-python >=3.10` | | [dbos-inc/dbos-transact-py](https://github.com/dbos-inc/dbos-transact-py) |
| cua | root MIT. **FSL-1.1-MIT** for Spaces apps, cua-spacesd, Keyvault, teleport, cua-volume, streaming client, and Cua Driver Bench (from 11 Oct 2026). `cua-som` is AGPL-3.0-or-later. `libs/cua-driver/rust/crates/cua-perception/models/licenses/` contains **AGPL-3.0-only.txt** | README: "The MIT parts never depend on the FSL parts" | [trycua/cua README §Licensing](https://github.com/trycua/cua); [legal/LICENSING.md](https://github.com/trycua/cua/blob/main/legal/LICENSING.md) |
| KasmVNC | **GPL-2.0** (LICENSE.TXT, "Version 2, June 1991") | | [kasmtech/KasmVNC](https://github.com/kasmtech/KasmVNC) |
| docker-webtop | **GPL-3.0** | | [linuxserver/docker-webtop](https://github.com/linuxserver/docker-webtop) |
| Caddy | Apache-2.0 | | [caddyserver/caddy](https://github.com/caddyserver/caddy) |
| agy (Antigravity CLI) | no license file; the winget manifest says "Proprietary"; The Register says "not open source" | | earlier notes: [The Register](https://www.theregister.com/ai-ml/2026/05/20/bye-bye-gemini-cli-google-nudges-devs-toward-antigravity/5243605) |
| Qwen3/3.5 GGUF, gpt-oss | Apache-2.0 per model cards (earlier notes) | | [unsloth/Qwen3.5-2B-GGUF](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF) |
| Holo2 (GUI grounding) | CC-BY-NC (earlier notes) | NC | [zaxby-hyperv-mcp #46](https://github.com/zaxbysauce/zaxby-hyperv-mcp/issues/46) |

### Inferences

**Distribution trigger.** While jarvis-hq stays private and the installer is used only by the owner, almost no redistribution obligations bite. Apache, MIT, BSD, CC and Gemma obligations attach to redistribution or sharing; MPL says so explicitly (Q5); GPL source duties attach to conveying. The "owner may share it later" case is the one to design for now, because retrofitting notices and swapping out NC/GPL parts later is costly.

**JARVIS's own licence.** Apache-2.0 (N.E.K.O) allows a derivative to be distributed under other terms as long as §4 is met. The owner can keep JARVIS code private or proprietary, or stay Apache-2.0. Keeping the whole product Apache-2.0-compatible is simplest because every permissive component flows into it.

**Matrix (vendor-and-modify / library / separate process only / exclude):**

| Licence class | Components | Vendor + modify in-tree? | Link / import in-process? | Ship in distributed installer? | Obligations |
|---|---|---|---|---|---|
| Apache-2.0 | N.E.K.O, N.E.K.O.-Tauri, goose, Codex, sherpa-onnx, Kokoro, openWakeWord code, Playwright(+MCP), Caddy, GLiNER, MiniCheck code, OfficeCLI, Qwen/gpt-oss weights | Yes | Yes | Yes | LICENSE copy; keep NOTICE (N.E.K.O.-Tauri, Codex, Playwright, OfficeCLI); mark modified files (§4b) |
| MIT / BSD-2 / BSD-3 | Hermes, OpenCode, openmuse, OmniRoute, 9router, llama.cpp, llama-swap, Silero, IndicConformer, Piper (archived), Smart Turn, browser-use, Windows-MCP, ES/Everything, Smokescreen, gitleaks, Presidio, python-pptx, DBOS, cua MIT parts, Tauri | Yes | Yes | Yes | keep copyright + permission notice in all copies (incl. binaries' third-party notices) |
| CC-BY-4.0 weights | Parakeet | n/a (data) | n/a | Yes | attribution, licence link, mark modifications (e.g. quantisation or ONNX export) |
| Gemma terms | Gemma-4 weights | not as source; quantised files are "Model Derivatives" | n/a | Prefer first-run download; if shipped, Notice + Agreement copy + use restrictions | §3.1 duties; Prohibited Use Policy; Google remote-restriction clause |
| MPL-2.0 | LibreOffice | Not worth it (huge); never modify MPL files without republishing those files | Run `soffice --headless` as a separate process | Yes if unmodified (point to source) | tell recipients where MPL source is |
| GPL-3.0 | piper1-gpl, docker-webtop | **No** (would force the combined work to GPL-3.0) | **No** (importing the `piper` Python package in-process counts as linking per FSF) | Only as a separate program ("aggregate"), with complete corresponding source or a written offer | GPLv3 §6 source; licence text |
| GPL-2.0-only | KasmVNC | **No** | **No**. Apache-2.0 is GPLv2-incompatible per FSF, so it cannot be combined with N.E.K.O code at all | Only as a separate container/image on the Linux VM | GPLv2 §3 source offer |
| AGPL-3.0 | cua-som, cua-perception AGPL model, OmniParser icon_detect (earlier notes, unverified) | **No** | **No** | Exclude | network-use source duty |
| CC BY-NC(-SA) | openWakeWord pretrained models, Bespoke-MiniCheck-7B, Holo2; possibly IndicTTS-based Piper voices | n/a | n/a | **Exclude from any shared installer**. Personal non-commercial use is OK; train own wake model / use MIT MiniCheck variants | NC + SA (adapted models stay BY-NC-SA) |
| FSL-1.1-MIT | cua Spaces, cua-spacesd, Keyvault, Driver Bench | **Do not merge** (terms follow all copies and derivatives) | Avoid | Only if a non-competing, non-commercial purpose is certain; otherwise wait for the 2-year MIT conversion per version | FSL copy/link; no Competing Use |
| Proprietary | agy; CopilotKit Intelligence service | **No** | **No** | **No.** At most a detector/adapter that drives a user-installed `agy` | provider ToS |

**Specific replacements:**
- **Piper for Hindi/Telugu:** use the archived MIT `rhasspy/piper` binary, or run Piper-format VITS voices through sherpa-onnx (Apache-2.0) instead of piper1-gpl. Whether sherpa-onnx loads Piper voices was **not verified this session**. Either way the voice files carry their own dataset licence.
- **"Hey jarvis" wake word:** keep sherpa-onnx KWS (Apache), which N.E.K.O already uses. Do not ship openWakeWord's pretrained `hey_jarvis`.
- **MiniCheck:** keep the MIT Flan-T5/RoBERTa-class checkers, and never use Bespoke-7B in a shared build.

**One generated THIRD_PARTY_NOTICES.** The installer should carry one THIRD_PARTY_NOTICES file plus a `licenses/` folder generated from `jarvis/tools/registry.json`. It must include the NOTICE files of N.E.K.O.-Tauri, Codex (with the Ratatui MIT credit), Playwright, Presidio and OfficeCLI, and the llama.cpp `licenses/` dir. Transitive dependencies need the same treatment: Rust crates, npm packages, Go modules and Python wheels each carry their own MIT/Apache/BSD notices. Ecosystem scanners can collect them: cargo-about/cargo-deny, license-checker, go-licenses and pip-licenses. These tool names come from background knowledge and were not verified this session.

**GPL aggregate status is interpretive.** Talking to piper1-gpl or KasmVNC over HTTP, pipes or CLI arguments is what the FSF calls "normally" separate programs. A tight protocol that exchanges internal data structures could change that. The safest course is to avoid shipping GPL components in the Windows installer at all.

### Gaps
- IndicTTS dataset licence (license.pdf) could not be retrieved. That decides whether the hi/te Piper voices can go in a shared installer.
- License of the HF weights JARVIS actually uses was not re-checked for: MiniCheck-RoBERTa-L, GLiNER-PII, IndicConformer, Smart Turn v3, Silero ONNX and Kokoro-82M (README says Apache).
- Not verified this session: Verdict/OpenJev (Apache-2.0 per brief), trim-1 (licence unknown; no repo identified), the WebRTC AEC binding package (the `livekit/webrtc-audio-processing` guess does not exist), and the Gemma-4 specific terms version.
- GPL FAQ "plug-ins" answers (#GPLPlugins, #NFUseGPLPlugins) could not be fetched (gnu.org returned 429 and reset connections). The aggregate and interpreter answers above are the operative citations.
- Everything's MIT-style License.txt may be an older or SDK licence. The current 1.4/1.5 installer EULA was not compared.

---

## Q2. Monorepo mechanics: subtree vs vendoring vs submodule vs package dependency; upstream sync; local-modification tracking

### Takeaway
"No separate repos" is best met with a tiered approach:
- **git subtree (`--squash`)** under `third_party/<name>/`, only for upstreams JARVIS actually modifies (N.E.K.O.-Tauri, Smart Turn inference code, ported pieces of Windows-MCP/openmuse/OmniRoute).
- **Locked package dependencies** in `uv.lock` / `pnpm-lock.yaml` / `Cargo.lock` for unmodified libraries.
- **Hash-pinned release artifacts** in `jarvis.lock.json` for large binaries JARVIS does not modify: llama.cpp, Caddy, gitleaks, Smokescreen, LibreOffice, Playwright browsers.

Each entry is described Chromium-style (README.chromium fields) in `registry.json`, and the existing Caretaker automates `subtree pull` and lock bumps as PRs.

### Cited Findings
- `git subtree`: "Subtrees are not to be confused with submodules, which are meant for the same task". Subtrees need no special repository structure, and end users have nothing extra to do.
  - `--squash` "helps to reduce log clutter" by importing one commit's worth of changes instead of full history.
  - `add` creates a commit joining the imported history; `pull` is "Exactly like 'merge'" but fetches from a remote.
  - `split` extracts "a new, synthetic project history" of the prefix (for upstreaming patches); `push` does split then push.
  — [git-subtree docs (git/git contrib/subtree)](https://github.com/git/git/blob/master/contrib/subtree/git-subtree.adoc)
- Chromium's `third_party/` rules: every dependency needs a README.chromium and a LICENSE file ("Dependencies should not be added without a license file and license type").
  - README.chromium fields include URL plus Version or Revision (for vulnerability matching, which fails if Version is a git hash), License as a comma-separated list, Shipped, Security Critical, and a Description of security considerations.
  - `Update Mechanism:` takes Autoroll, Manual, Static or Static.HardFork; a HardFork must record the "closest point of divergence from the upstream".
  - OWNERS must "Update the dependency when a security or stability bug is fixed upstream".
  - `//tools/licenses/licenses.py scan` checks completeness. Licence categories: permissive/notice allowed, reciprocal allowed for open-source Chromium, restricted (GPL) needs approval and is allowed only for non-shipped deps.
  — [Chromium: Adding third_party libraries](https://chromium.googlesource.com/chromium/src/+/main/docs/adding_to_third_party.md)
- Copybara is "A tool for transforming and moving code between repositories". It keeps one authoritative repo, applies config-defined transformations (path moves, text replacement) and stores its state in the destination repo. Typical uses are syncing confidential and public repos and importing contributions. Git is the only officially supported VCS. It is Apache-2.0 and "used internally at Google", with weekly untested snapshot releases — [google/copybara](https://github.com/google/copybara)
- uv workspaces share "a single lockfile". Members depend on each other with `workspace = true` (editable). uv enforces one `requires-python`, "taking the intersection of all members' `requires-python` values". Members with conflicting requirements or needing separate environments should instead be independent projects linked by path dependencies — [uv: Workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/)
- Python constraints relevant to one workspace: N.E.K.O `requires-python = "==3.11.*"` ([N.E.K.O pyproject](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/pyproject.toml)); Hermes Agent `">=3.11,<3.15"` ([hermes-agent pyproject](https://github.com/NousResearch/hermes-agent/blob/main/pyproject.toml)); DBOS `">=3.10"` ([dbos-transact-py](https://github.com/dbos-inc/dbos-transact-py)); Windows-MCP requires Python 3.14+ (earlier notes, [Windows-MCP](https://github.com/CursorTouch/Windows-MCP)).
- Patch-file pattern in a vendored JS tree: openmuse keeps `patches/metro@0.83.3.patch` (pnpm patched dependency) and uses pnpm workspaces and renovate — [CopilotKit/openmuse tree](https://github.com/CopilotKit/openmuse)
- Today's state (from earlier notes):
  - `.gitmodules` pins submodule `jarvis/third_party/neko-tauri` at b7b4fd70;
  - `jarvis/third_party/PATCHES.md` is "PLAN ONLY" (tray, global push-to-talk via `tauri-plugin-global-shortcut` 2.4.1, single-instance 2.5.4, click-through fail-safe), with the rule "offer upstream as PRs (no fork)";
  - Caretaker detectors are upstream_release, advisory (OSV), lock_drift, test_failure and secret_scan, and only open PRs on `caretaker/<tool>-<id>` branches;
  - combine-tools has 88 projects in `registry.json` and "one lock for every download, verified fetcher".

  — [repo_current_state.md → .gitmodules, PATCHES.md, caretaker README](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/third_party/PATCHES.md); [commit 88ec49a2](https://github.com/venkatsairishith-dev/jarvis-hq/commit/88ec49a2ba056f85ae5bbcfc65509852b58ad4f0)
- Large-repo precedent: Codex itself is built with Bazel (`MODULE.bazel`, `MODULE.bazel.lock`, `BUILD.bazel` at repo root) — [openai/codex](https://github.com/openai/codex)

### Inferences

**Recommended layout (one repo, one tree):**
- `jarvis/` holds JARVIS code.
- `plugin/plugins/` holds the plugins.
- `third_party/<name>/` holds vendored subtrees, each with `UPSTREAM.json` (or registry fields) + `LICENSE` + `NOTICE` + `patches/`.
- `desktop/` holds the Tauri shell, a subtree of N.E.K.O.-Tauri.
- `binaries.lock` is the existing `jarvis.lock.json` with url + sha256 + licence per platform.

**Replace the submodule with a subtree.** Run `git submodule deinit` and `git rm` on the submodule, then `git subtree add --prefix desktop https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri <sha> --squash`. Apply PATCHES.md items as ordinary commits there; Apache §4(b) requires marking modified files, which a header line plus a PATCHES.md entry covers. Upstreaming stays possible via `git subtree split --prefix desktop` and a PR. This fully meets "integrate, not a repo" while keeping updates possible.

**Choose per upstream by modification rate:**
- **Subtree `--squash`:** when JARVIS edits the source. Candidates are the N.E.K.O.-Tauri shell, Smart Turn inference glue, and any copied files from OmniRoute/9router/openmuse/Windows-MCP. Port or copy only the needed files instead of the whole app, and keep the original copyright header in each copied file.
- **Package dependency (lockfile):** for unmodified libraries such as Hermes (if not patched), browser-use, Presidio, GLiNER, python-pptx, DBOS, sherpa-onnx wheel, Playwright, keyring and Tauri crates. Pin a git commit only when a fix is unreleased.
- **Hash-pinned artifacts (no source in tree):** for llama.cpp `llama-server` builds, llama-swap, Caddy, gitleaks, Smokescreen, es.exe, LibreOffice, Playwright browsers and model weights. Vendoring multi-hundred-MB C++/Go trees adds churn with no benefit unless JARVIS patches them. If it must, build from a subtree in CI and record the rebuild recipe.
- **Never:** submodules, which the owner rejected and which are the mechanism being removed.

**Tracking local modifications.** Mirror Chromium's README.chromium fields in `registry.json`: `upstream_url`, `version`/`revision`, `license` (SPDX list), `license_file`, `notice_file`, `shipped`, `security_critical`, `update_mechanism` (autoroll/manual/static/hardfork), `local_modifications` (list of patch files + reason), `divergence_point`. A CI check equivalent to `licenses.py scan` should fail the build if any shipped entry lacks a licence or NOTICE, or carries a forbidden SPDX id (GPL-*, AGPL-*, CC-BY-NC*, FSL-*, LicenseRef-Proprietary) in the Windows installer manifest.

**Patch queues.** For heavily patched subtrees, keep patches as files (`third_party/x/patches/*.patch`, applied by a script) so a squash pull can be done on a clean copy and the patches re-applied. quilt and stgit are alternatives, but plain patch files plus `git am`/`git apply --3way` are simpler for an agent-driven Caretaker. Copybara is overkill for one owner, though useful if a public mirror of parts of JARVIS is ever needed.

**Caretaker automation.**
1. `upstream_release` detects a new tag.
2. On branch `caretaker/<tool>-<ver>`, it runs `git subtree pull --squash` (or bumps the lock entry and sha256).
3. It re-applies patches, regenerates THIRD_PARTY_NOTICES, re-runs the licence gate and the ~860-case bench, then opens a PR.
4. `lock_drift` and OSV `advisory` already map onto this.

This keeps the "only opens PRs" safety rule.

**Python version split.** One uv workspace is feasible for N.E.K.O + JARVIS + Hermes + DBOS, since the intersection is 3.11. Windows-MCP (3.14+) cannot join the workspace. Port the needed UIA tools (MIT permits copying with notice) or run it as a separate managed process with its own uv-managed interpreter.

### Gaps
- Whether Hermes Agent's dependency set resolves together with N.E.K.O's pins in one uv.lock was not tested.
- Repository size impact of subtree-vendoring llama.cpp, goose or Codex sources was not measured.
- No source found on how Android or Bazel `third_party` vendoring tracks patches (not fetched this session). The Chromium rules are the only big-project precedent cited.

---

## Q3. Polyglot integration (Python core + TS + Go + Rust + C++): port, embed, child process, or single binary

### Takeaway
The maintainable split:
- Keep **Python as the one core**: N.E.K.O + JARVIS kernel, a uv workspace on 3.11.
- Use **Rust/TypeScript only for the Tauri desktop shell**.
- Treat **Go and C++ tools as prebuilt binaries** that a single JARVIS supervisor starts, configures and health-checks.
- **Port** the small, Python-shaped features of large TypeScript apps (OmniRoute/9router routing rules; openmuse page-watch and durable-task ideas, via DBOS) instead of shipping whole Next.js/Expo stacks.
- **Bundle at most one coding-agent CLI** as a managed binary.

### Cited Findings
- N.E.K.O.-Tauri already owns backend lifecycle. Its README says the repo maintains only "window, mouse click-through, platform adaptation, native screen capture, and **starting/stopping the backend process**". `backend_runtime.rs` spawns the Python backend (dev: `.venv/Scripts/python.exe`; env `NEKO_BACKEND_EXECUTABLE` / `NEKO_BACKEND_DIR`) and polls `GET /health` on `127.0.0.1:48911` — [N.E.K.O.-Tauri README](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri/blob/main/README.md); [backend_runtime.rs](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri/blob/main/src-tauri/src/backend_runtime.rs)
- Runtimes of the TS components:
  - OpenCode is Bun (`bun@1.4.2`) — [sst/opencode package.json](https://github.com/sst/opencode)
  - OmniRoute is a Next.js app needing Node 22.22+/24–26, with dev scripts using `--max-old-space-size=8192` — [OmniRoute package.json](https://github.com/diegosouzapw/OmniRoute)
  - 9router is Next ^16.1.6 — [9router package.json](https://github.com/decolua/9router)
  - openmuse is a pnpm monorepo (Node ≥22) with its own server, task worker and browser worker; "Intelligence is a separate service" — [openmuse README](https://github.com/CopilotKit/openmuse)
- Rust components: goose (Apache-2.0) and Codex (Apache-2.0, `codex-rs`, Bazel) — [block/goose](https://github.com/block/goose); [openai/codex](https://github.com/openai/codex)
- Go and C++ components: llama-swap (Go), Smokescreen (Go), gitleaks (Go), Caddy (Go) and llama.cpp (C++), all permissive (see Q1 table).
- cua's own policy shows a polyglot MIT/FSL boundary enforced in CI. A "CI: License direction" workflow "Checks that MIT packages only depend on internal packages under permissive licences … Blocking" — [cua .github/workflows/license-check.yml](https://github.com/trycua/cua/blob/main/.github/workflows/license-check.yml)
- Tauri sidecars are commonly "Python CLI applications or API servers bundled using `pyinstaller`" — [Tauri: Embedding External Binaries](https://v2.tauri.app/develop/sidecar/)

### Inferences

**Per-component recommendation (most maintainable):**

| Component | Integration mode | Why |
|---|---|---|
| N.E.K.O | It *is* the base. Track upstream with a `neko-upstream` remote and periodic merge, or subtree of the core | already in-tree |
| N.E.K.O.-Tauri | **Vendored subtree** `desktop/`, modified (tray, PTT, single-instance) | JARVIS must patch it |
| Hermes Agent | **uv library dependency** (or workspace member if patched); same 3.11 env | Python, MIT |
| OpenCode / goose / Codex | **Pick one** as the bundled coding agent, shipped as a hash-pinned binary run by the supervisor through the kernel lab. Keep the others as optional adapters | three agent CLIs is triple maintenance. OpenCode needs Bun, while Codex and goose are single Rust binaries |
| openmuse | **Port features** (durable tasks → DBOS; page-watch goals; PDF form fill) into JARVIS plugins. Copy individual MIT files with headers if useful. Do **not** ship its Expo/Hono stack or Intelligence | alpha, Node stack, non-MIT service dependency |
| OmniRoute / 9router | **Port** routing/fallback/quota rules into JARVIS's Python provider gateway. Running OmniRoute as a managed Node sidecar is the fallback | avoids a Next.js server in the product. Both MIT |
| llama.cpp | **Bundled binary** `llama-server` (per-platform build: CUDA/Vulkan on Windows, CPU/ARM64 on Linux), version-pinned (b11714 per earlier notes) | crash isolation, GPU builds; in-process bindings would tie Python to CUDA builds |
| llama-swap | **Bundled binary** (Go) driven by a generated YAML; or replace with the JARVIS supervisor's own swap logic later | small, MIT |
| sherpa-onnx, Silero VAD, Kokoro, Parakeet/IndicConformer, Smart Turn | **Python library deps + model files** in one voice service process | already Python. N.E.K.O ships a custom sherpa wheel |
| Piper | archived MIT binary or sherpa-onnx VITS; **not** piper1-gpl in-process | GPL |
| openWakeWord | code optional; **no** NC pretrained models in shared builds | NC |
| WebRTC AEC | Python binding as a library, licence to confirm | |
| Playwright / browser-use | **Python libs** (`playwright`, `browser-use`); browsers downloaded and pinned | Python-native |
| Playwright MCP | Node sidecar only if MCP-over-extension mode is needed; otherwise use playwright-python directly | avoids Node |
| Windows-MCP | **Port** needed UIA tools into the JARVIS desktop plugin, or separate 3.14 process | Python version conflict; "full system access" risk |
| es.exe + Everything | **Bundled binary** es.exe; Everything client installed/started by the installer (permissive licence text) | needs IPC client |
| Smokescreen, Caddy, gitleaks | **Bundled binaries**: Linux VM for Smokescreen/Caddy, both OSes for gitleaks | Go, unmodified |
| Presidio, GLiNER, MiniCheck, python-pptx, DBOS | **Python library deps** | |
| OfficeCLI | bundled binary/CLI (Apache-2.0, NOTICE) | |
| LibreOffice | **Detect, or download on demand**; run headless as a process; never vendor | MPL, huge |
| cua | MIT SDK/driver only, as a library; **exclude** Spaces/FSL, cua-som/AGPL, the AGPL perception model | licence boundaries |
| webtop / KasmVNC | **Not in the product.** Optional container image pulled on the Linux VM only | GPL |
| agy | **Exclude**; optional adapter that detects a user-installed agy | proprietary |
| Qwen / gpt-oss / Gemma weights | **First-run download** with sha256 from the lock; Gemma terms shown and NOTICE written | size + Gemma §3.1 |

**One supervisor.** The JARVIS kernel, or a small `jarvis-supervisor` module, should own every child process. It does this from one manifest (in `registry.json`): `cmd`, `args`, `env`, `port`, `health_url`, `restart_policy`, `resources`, `os`. On Windows, Tauri launches only the supervisor (the Nuitka-built backend), and the supervisor launches everything else. This keeps Tauri's Rust code small and avoids having to declare every child in Tauri's shell capability list.

**Compile into single binaries?** Only where upstream already does it: Go tools, Rust CLIs, Bun-compiled OpenCode, and Nuitka for the Python backend. Merging Go/C++/TS code into the Python process is not maintainable.

### Gaps
- No benchmark of the Python-gateway port vs running OmniRoute as a sidecar was done.
- Whether OpenCode's release ships a single compiled binary for win-x64 and linux-arm64 was not verified this session.
- The WebRTC AEC Python binding name and licence are unconfirmed.

---

## Q4. Packaging: one Windows installer + one Linux ARM64 VM deployment

### Takeaway
Upstream already shows the path:
- N.E.K.O's CI builds the Python backend with **Nuitka** into a single `projectneko_server(.exe)`.
- N.E.K.O.-Tauri bundles that file as a read-only resource via `tauri.release.conf.json` and supervises it.

JARVIS can copy this pipeline:
- **Build:** Nuitka-built `jarvis_server.exe`, plus per-triple `externalBin` sidecars (llama-server, llama-swap, es.exe, gitleaks, the coding agent).
- **Installer:** a Tauri NSIS installer.
- **Updates:** the mandatory-signature Tauri updater.
- **Signing:** Azure Artifact Signing or an OV cert.
- **Models:** downloaded on first run and verified against `jarvis.lock.json` sha256s.
- **Linux ARM64 VM:** a systemd bundle (one supervisor unit plus per-service units, like the existing `jarvis-omniroute.service`), with GPL desktop containers pulled separately.

### Cited Findings
- N.E.K.O desktop CI ("Step 1: Build Python backend with Nuitka"):
  - matrix output `projectneko_server.exe` (win x64);
  - `uv venv .venv`, `uv sync --group galgame …`, `uv pip install … nuitka ordered-set zstandard`;
  - asserts `sherpa_onnx.__version__ == "1.13.8+neko.kws2"`;
  - "Prepare filtered built-in plugins for Nuitka" (`scripts/prepare_nuitka_plugins.py`);
  - inputs `electron_repo`, `electron_ref`, `skip_signing`;
  - pins embedding model `jinaai/jina-embeddings-v5-text-nano-retrieval` at a fixed revision.

  — [N.E.K.O build-desktop.yml](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/.github/workflows/build-desktop.yml); [build-desktop-windows.yml](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/.github/workflows/build-desktop-windows.yml)
- N.E.K.O.-Tauri release build: "This repo does not store `.venv` or Python source snapshots". Release pipelines must first produce a single-file backend at `.artifacts/backend/projectneko_server(.exe)`. `npm run tauri:build` checks it exists and bundles it "as a read-only resource" via `tauri.release.conf.json` (`"resources": {"../.artifacts/backend/": "backend/"}`). `scripts/verify-backend-artifact.mjs` fails the build if the artifact is missing — [N.E.K.O.-Tauri README](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri/blob/main/README.md); [tauri.release.conf.json](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri/blob/main/src-tauri/tauri.release.conf.json)
- Tauri sidecars: "`externalBin` … expects a list of strings targeting binaries" and "a binary with the same name and a `-$TARGET_TRIPLE` suffix must exist" (find it with `rustc --print host-tuple`, Rust ≥1.84). Running them needs shell-plugin permissions `shell:allow-execute` / `shell:allow-spawn` with `"sidecar": true` — [Tauri: Embedding External Binaries](https://v2.tauri.app/develop/sidecar/)
- Tauri updater:
  - signatures are mandatory ("needs a signature to verify that the update is from a trusted source"); keys come from `tauri signer generate`, with the private key supplied by env var at build time (`.env` is not read);
  - `bundle.createUpdaterArtifacts: true`; a static JSON needs `version` and per-platform `url` + `signature`;
  - Windows `installMode` is `passive` (default), `basicUi` or `quiet`; Windows builds produce both NSIS and MSI.

  — [Tauri Updater plugin](https://v2.tauri.app/plugin/updater/)
- Windows signing: OV certs are "generally cheaper and available to individuals". "Since 2024, an EV Certificate no longer gives your app an immediate reputation with Microsoft SmartScreen". Azure Artifact Signing ("previously … Azure Trusted Signing") plugs in via `bundle > windows > signCommand`, which can call any signing tool. Cross-compiling from Linux/macOS needs a custom sign command — [Tauri: Windows Code Signing](https://v2.tauri.app/distribute/sign/windows/)
- Shipped Python + Tauri examples:
  - Voicebox: PyInstaller-packaged FastAPI backend bundled by Tauri as a sidecar next to a React UI — [Voicebox build docs](https://docs.voicebox.sh/developer/building)
  - GeoLibre: a Rust launcher spawns a uvicorn/FastAPI sidecar, and the frontend calls it on localhost "with per-launch auth tokens" (page dated 2026-07-31) — [GeoLibre sidecar explainer](https://instagit.com/opengeos/GeoLibre/how-does-the-fastapi-python-sidecar-communicate-with-the-frontend.md)
  - A Tauri + FastAPI + llama.cpp guide puts the interpreter, deps and llama.cpp DLLs in one PyInstaller exe as a sidecar — [AI Echoes Substack](https://aiechoes.substack.com/p/building-production-ready-desktop)

  These are search-result summaries and were not fetched in full.
- Existing Linux deployment pattern: `jarvis/deploy/jarvis-omniroute.service` is referenced from `registry.json` (earlier notes) — [registry.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/tools/registry.json)

### Inferences

**Windows build pipeline (one installer):**
1. `uv sync --frozen` produces the Nuitka-built `jarvis_server.exe`, reusing N.E.K.O's workflow and the `prepare_nuitka_plugins.py` step.
2. Fetch the pinned binaries from `jarvis.lock.json` (llama-server CUDA/Vulkan build, llama-swap, es.exe, gitleaks, the chosen coding-agent CLI) and rename them to `<name>-x86_64-pc-windows-msvc.exe` for `externalBin`.
3. Generate THIRD_PARTY_NOTICES and `licenses/`.
4. `tauri build` with NSIS (per-user install suits the updater, and MSI is also produced) plus `createUpdaterArtifacts`.
5. Sign the exe, the sidecars and the installer via `signCommand` (Azure Artifact Signing or OV).
6. Publish `latest.json` for the updater.

Nuitka is preferred over PyInstaller here because upstream already maintains a Nuitka recipe for this exact codebase. That includes the patched sherpa wheel, which is the riskiest native dependency.

**First-run model fetch.** The installer should stay small (no multi-GB weights). On first run the supervisor downloads models listed in `jarvis.lock.json` (url, sha256, size, licence, `requires_acceptance` for Gemma). It verifies hashes before use, writes the Gemma NOTICE/terms into the models folder, and refuses NC models when `distribution_profile=shared`. The "verified fetcher" from combine-tools phase 2 already exists.

**Linux ARM64 VM.** Use one tarball or `.deb`-like bundle:
- a uv-managed venv (`uv sync --frozen` on aarch64);
- `linux-aarch64` binaries from the same lock (llama-server CPU build, llama-swap, Smokescreen, Caddy, gitleaks);
- systemd units: `jarvis-supervisor.service` plus generated child units, or the supervisor managing children itself, with hardening (`DynamicUser`/`ProtectSystem`/`MemoryMax` for the 12 GB A1 limit noted earlier).

A single compose file is the alternative. Compose is the natural place for the GPL webtop/KasmVNC image and openmuse's browser worker if kept, because those stay separate images (aggregate) and are pulled, not built into JARVIS.

**Auto-update split.** The Windows shell and backend update together through the Tauri updater: the signed NSIS contains the new `jarvis_server.exe`. Models and binaries update through lock bumps, which the Caretaker PRs. The Linux VM updates by `git pull` of a release tag plus `uv sync --frozen` plus a binary fetch, or by downloading a release tarball.

### Gaps
- Nuitka-built backend size and start-up time for JARVIS's dependency set (torch? onnxruntime? playwright?) were not measured.
- Whether Tauri's NSIS updater handles a very large bundled backend well (delta updates) was not researched.
- Azure Artifact Signing eligibility for individuals in India in 2026, and its price, were not checked.
- PyInstaller vs Nuitka official docs were not fetched this session. The recommendation rests on N.E.K.O's existing Nuitka CI.

---

## Q5. Unified configuration, secrets, auth and audit across integrated components

### Takeaway
Use one settings tree as the source of truth (`jarvis/config/*.json`, which already exists). The supervisor **generates** each child's native config at launch: llama-swap YAML, Caddyfile, Smokescreen ACL, coding-agent config, router env.
- **Secrets:** one store through Python `keyring` (Windows Credential Locker on the laptop; Secret Service on Linux, which needs a D-Bus session and an unlocked gnome-keyring on a headless VM, otherwise a root-owned credentials file).
- **Auth:** a per-launch random token that the supervisor injects into every child and that Tauri passes to the UI, the GeoLibre pattern.
- **Audit:** one append-only audit log written by the kernel, which already gates tools.

### Cited Findings
- keyring 25.7.0 (16 Nov 2025) supports macOS Keychain, Freedesktop Secret Service ("supports many DE including GNOME", needs `secretstorage`), KWallet and Windows Credential Locker. Headless Linux works "without X11 server available (only D-Bus is required)" by installing GNOME Keyring, starting a D-Bus session and unlocking with `gnome-keyring-daemon --unlock`, then running the app in that D-Bus session — [keyring on PyPI](https://pypi.org/project/keyring/)
- Per-launch localhost auth tokens between a Tauri launcher and a FastAPI sidecar are used in GeoLibre — [GeoLibre sidecar explainer](https://instagit.com/opengeos/GeoLibre/how-does-the-fastapi-python-sidecar-communicate-with-the-frontend.md)
- N.E.K.O.-Tauri passes backend location by env (`NEKO_BACKEND_EXECUTABLE`, `NEKO_BACKEND_DIR`) and health-checks a fixed loopback port 48911 — [backend_runtime.rs](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri/blob/main/src-tauri/src/backend_runtime.rs)
- llama-swap has `apiKeys`, `security`, `profiles` and `routing` config keys (earlier notes) — [local_model_roster notes → llama-swap](https://github.com/mostlygeek/llama-swap)
- JARVIS already has:
  - `config/api_providers.json` with an `api_key_registry`;
  - a kernel policy with tiers GREEN/YELLOW_AUTO/YELLOW_APPROVAL/RED/DENY;
  - console sign-in and approvals;
  - idempotency keys;
  - the roadmap note "caretaker stays off until a safe credential exists".

  — [repo_current_state.md → policy.py, api_providers.json, JARVIS_ROADMAP_2026-10-22.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/kernel/jarvis_kernel/policy.py)

### Inferences

**One settings file.** Use `jarvis/config/jarvis.toml` or the existing JSON set with a schema. It needs sections for `models`, `voice`, `browser`, `router`, `egress`, `agents` and `services` (per child: enabled, port, resources). Children never read the user file directly. The supervisor renders their native config into `%LOCALAPPDATA%\JARVIS\run\` or `/run/jarvis/` on each start, so one change propagates everywhere and upstream config formats can change behind an adapter.

**One secrets store.** Every component gets provider keys only through env vars injected by the supervisor from `keyring` (service name `jarvis`, entries like `provider/openrouter`). Nothing secret goes into `registry.json`, generated configs on disk (use env or stdin where possible), logs or the repo. The existing gitleaks secret_scan guards the repo.

On headless Linux, keyring's Secret Service path needs D-Bus plus an unlocked gnome-keyring, which is awkward under systemd. A root-owned 0600 credentials file loaded via systemd `LoadCredential=` is the practical alternative. That systemd feature comes from background knowledge and was not verified here.

**One token system.** The supervisor mints a random per-boot token and passes it to Tauri (for the UI) and to each child (as an API key):
- llama-swap `apiKeys`;
- the router's key;
- the coding agent's local server password; OpenCode already uses `OPENCODE_PASSWORD` in its dev scripts ([opencode package.json](https://github.com/sst/opencode)).

Everything binds to 127.0.0.1. On the Linux VM, Caddy (Apache-2.0) is the single TLS/auth front door for the remote console.

**One audit log.** Route all child stdout/stderr and all tool calls through the kernel. Write one append-only JSONL audit log (who/what/tier/approval/idempotency key/child/version) and keep per-child logs only for debugging. Include each component's `registry.json` version and sha256 in the log so behaviour can be traced to an exact upstream revision.

### Gaps
- The jarvis-hq repo was not re-read this session, so the exact current secrets mechanism (DPAPI vs keyring vs env files) and the existing audit-log format were not confirmed.
- No primary source was fetched for Windows Credential Manager size limits. keyring's page does not mention them; older Windows docs cap a credential blob at about 2.5 KB from background knowledge, which is unverified.
- How each coding-agent CLI (OpenCode/goose/Codex) accepts injected provider config and auth in 2026 versions was not verified here.
