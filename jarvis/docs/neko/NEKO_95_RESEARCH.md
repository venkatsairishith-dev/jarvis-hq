# Neko 9.5: research (2026-10-16)

Web research by three background agents, checked against this laptop:
- Windows 10 19045, i9-9980HK, 32 GB RAM, Radeon Pro 5500M with 4 GB (Vulkan only).

Nothing was installed by the research. **[U]** marks a figure that is unverified or second-hand and must be measured here before it is relied on. Reuse-first applies: each recommendation names an existing maintained project. Any custom code is justified in 01_RESEARCH_AUDIT.md.

## 1. Voice (C1)

### Wake word

- **Keep sherpa-onnx KWS** (Apache-2.0, already running). It is tuned through the keyword file, with a per-line `:boost` and `#threshold`, so no training is needed.
- **Next steps:**
  - add Indian-English spelling variants (`HH EY1 N EY1 K OW0`, `HH EH1 N IY1 K OW0`);
  - sweep threshold and boost on recorded WAVs.
- **Fallback if real recall at 1.5 m stays under about 90%:**
  - **openWakeWord** (code Apache-2.0; pretrained models CC BY-NC-SA; last release Feb 2024) or **livekit-wakeword** (Apache-2.0, 2026; its ONNX output loads in openWakeWord's runtime);
  - train "hey_neko" from Piper synthetic clips plus the owner's recordings;
  - add openWakeWord's custom verifier (≥3 owner clips);
  - run it alongside KWS and wake if either fires.
- **Excluded:**
  - Porcupine: its free tier reportedly ended on 2026-06-30 (second-hand);
  - EfficientWord-Net: Python 3.6–3.9 only.
- **Owner-only wake:** a sherpa-onnx speaker-embedding check runs *after* a detection, with 5–10 enrolled clips. Cost is negligible because it only runs on detections.

### Audio front end

This is the real problem. The measured room speech was about -40 dBFS against a -49 dBFS noise floor, so the signal-to-noise ratio is about 9 dB. Gain does not fix SNR.

- **Front end:** **LiveKit `rtc.AudioProcessingModule`** (pip `livekit`, Apache-2.0, has a win_amd64 wheel). It provides WebRTC echo cancellation, noise suppression, a high-pass filter and auto-gain from Python.
  - Feed it 10 ms frames.
  - Use Neko's own TTS PCM as the reverse stream, so no loopback alignment is needed.
  - Estimated cost: 3–6% of one core **[U]**.
- **Reference:** echoff (MIT, alpha, WASAPI loopback plus WebRTC echo cancellation), for cancelling *all* system audio.
- **Fallbacks:**
  - pyaec (Speex);
  - DeepFilterNet (real-time factor about 0.19 on one thread, about 40 ms delay) if WebRTC noise suppression is not enough.
- **Cheapest large win:** a USB or headset microphone **[U]**. This is an owner decision.
- **How others do barge-in:**
  - echo reference plus VAD and KWS on the cleaned signal, then cut TTS;
  - Pipecat and RealtimeSTT do VAD barge-in without echo cancellation and recommend headphones.

### Speech recognition and TTS

- **English streaming ASR:**
  - **Nemotron Speech Streaming EN 0.6B** (sherpa-onnx ≥ 1.13.4; int8 is 632 MB; word error rate 7.7% at 160 ms chunks).
  - Parakeet TDT stays as the final pass.
  - Hindi: Nemotron 3.5 streaming.
  - Telugu: IndicConformer after VAD (no streaming model found).
- **TTS:** Kokoro (English) and Piper (Hindi/Telugu). Piper 1.x is now GPL-3.0; existing voices are unaffected **[U]**.
  - Synthesize sentence by sentence.
  - **Instant cut:** a `sounddevice` OutputStream callback with 10–20 ms blocks and an atomic "silence" flag, then clear the queue and cancel synthesis. Estimated about 30–50 ms to silence **[U]**.
  - RealtimeTTS (MIT) is the reuse candidate.
- **Device recovery:** a watchdog reopens the stream after 2 s without callbacks, re-picks the device by name, and backs off 1/2/5/10 s.

### Real-voice test protocol (owner, physical mic)

- **Positives:** 50 × "Hey Neko" at 0.5 m and at 1.5 m, each in three conditions: quiet, fan or TV at about 55 dBA, and Neko speaking.
- **Negatives:** ≥5 h of household audio plus 100 confusable phrases.
- **Recording:** raw and processed WAVs, so thresholds can be retuned offline.
- **Targets:** recall ≥95% at 0.5 m and ≥85% at 1.5 m; ≤0.5 false alarms per hour; ≥90% barge-in success.

## 2. Long missions (C6)

- **No agent framework.** The kernel and coordinator already run the agent loop.
- **Durable layer: DBOS Transact** (MIT; a library, with SQLite as the default store).
  - `@DBOS.workflow` on a mission and `@DBOS.step` on each step.
  - The workflow ID is the idempotency key.
  - Windows support is **[U]**.
  - **Plan B:** a small SQLite journal (`missions`, `steps`, `effects(idempotency_key UNIQUE)`), justified in 01_RESEARCH_AUDIT.md if DBOS is awkward on Windows.
- **Rejected:**
  - LangGraph: needs a rewrite as a graph;
  - Microsoft Agent Framework: pickle checkpoints are a trust boundary;
  - AutoGen: maintenance mode;
  - CrewAI: its resume API is being deprecated;
  - Prefect, Hatchet, Temporal: each needs a server.
- **Changes ranked by reliability gain:**
  1. a step journal with checkpoint and resume;
  2. idempotency keys on side effects, checking whether the effect already exists before any retry;
  3. hard budgets: steps, wall time, tool calls, replans;
  4. **stagnation detection**: hash (tool, args, result) over the last ~20 calls, warn at 3 repeats, catch A→B→A ping-pong;
  5. replanning only when verification fails, at most 2 replans;
  6. ReWOO-style single-call plans with variables, which suit a 4B model;
  7. cancellation by token between steps, with child processes in a Windows Job Object.

## 3. Research, decks and PDFs (C8)

- **Decks:**
  - python-pptx builds the content (it has no animation API).
  - **OfficeCLI** (Apache-2.0, single .NET binary) adds transitions and animations, plus `validate` against the OpenXML schema and `view issues` (text overflow, missing alt text).
  - QA:
    - OfficeCLI validate and issues;
    - a geometry check (every shape inside the slide; estimated text height ≤ box);
    - a LibreOffice → PDF → PNG render, treated as a warning, not a failure.
  - Marp, Slidev and reveal.js are rejected: their PPTX output is images.
- **PDFs:**
  - keep Edge printing with the tagged-PDF and document-outline flags;
  - check with pypdf (outline, language, title, page count) and pdfplumber (text extractable, nothing outside the page box);
  - veraPDF `ua1` optional (Java).
- **Claim checking:**
  - **HHEM-2.1-Open** (Apache-2.0, 0.1B, under 600 MB RAM, about 1.5 s per 2k tokens on CPU) checks each claim against the top-3 chunks of the cited source;
  - borderline scores (0.35–0.65) go to **MiniCheck-Flan-T5-L** (MIT);
  - the current NLI model stays only for spotting contradictions between sources.
- **Citation validity:**
  - the cited URL was really fetched;
  - the quoted span is found (fuzzy match ≥ 0.9);
  - the claim is supported by *that* passage.
- **Deck metrics:**
  - schema-valid;
  - zero overflow;
  - body text ≥ 18 pt;
  - ≤ about 40 words per slide;
  - contrast ≥ 4.5:1;
  - 100% alt text;
  - no empty placeholders.

## 4. Evaluation and self-improvement (C13)

- **WindowsAgentArena** (MIT) is a Windows 11 VM in QEMU under Docker. On Windows 10, nested KVM in WSL2 is unavailable, so it would run without KVM, which its README calls "not recommended" for speed. **Not worth it here.**
- OSWorld, WorkArena and AgentBench are excluded: they need a VM or cloud service, or run mostly on Linux.
- **Practice to adopt:**
  - a private regression suite of mission specs with recorded tool fakes;
  - trajectory assertions (approvals present, nothing forbidden) plus outcome assertions;
  - **pass^k** (all k trials succeed, k = 3–5).
- **Self-changes:**
  1. drafted in staging;
  2. the full suite must pass with no regression;
  3. promoted only with owner or kernel approval;
  4. rollback is one step.
- **Skills:** a Voyager-style registry (code, description and a passing test before admission) in the SKILL.md folder format.
- **Prompt optimization:** DSPy MIPROv2 or GEPA works with local models.
- **Fine-tuning:** only after about 500 verified plan traces. QLoRA of a 4B model in 4 GB is unlikely to fit (RDNA1 is limited to Vulkan inference).

## 5. Proactive monitoring (C10)

- **Event Log:** pywin32 `EvtSubscribe` (push callbacks, XPath queries, **bookmarks that survive reboots**). Use the Application channel for crashes (Application Error 1000, .NET Runtime 1026, WER 1001).
- **Disk:** psutil polling.
- **Services:** status of the local ports (48911–48950, 8080, 8799).
- **Stuck tasks:** the coordinator task records against their time budget.
- **Avoid:**
  - WMI `__InstanceCreationEvent` polling, which misses short-lived processes;
  - pywintrace, which is archived.
- **Measurement:**
  - precision from owner feedback;
  - recall from **canary injections** (`eventcreate`, a test process, a temporary large file);
  - time to alert at p50/p95;
  - alerts per day against a budget.
  - Targets: precision ≥ 0.8, canary recall ≥ 0.95.

## 6. Vision (C9)

- **OCR first:**
  - **Windows.Media.Ocr** (built into Windows 10; `winocr` 0.0.15 from Python) returns lines and words with boxes, which gives free grounding for any control with a text label.
  - Then RapidOCR (ONNX, CPU).
  - Then a vision model, only for icons, unlabeled controls and "did it change" checks.
- **Vision models**, one at a time, in this order (llama.cpp, `-m` plus `--mmproj`, F16 projector, never BF16 on RDNA1):

  | # | Model | Files | Why |
  |---|---|---|---|
  | 1 | **UI-Venus-1.5-2B** Q4_K_M | 2.10 GB | Grounding specialist: ScreenSpot-Pro 57.7, V2 92.8; fits fully in 4 GB |
  | 2 | Qwen3.5-4B Q4_K_M | 3.41 GB | Reader and describer with grounding (ScreenSpot-Pro 60.3). Run the projector on the CPU (`--no-mmproj-offload`) |
  | 3 | Holo-3.1-4B (mradermacher, Q8_0 projector) | 3.43 GB | Only if #2's grounding is weak |

- **Coordinates:** 0–1000 normalized; scale back with the exact pixel size of the image sent.
- **Image tokens:** one token covers 32×32 pixels (Qwen3-VL / Qwen3.5). Grounding needs ≥1024 image tokens, so use two llama-swap entries: reading with no minimum, grounding with min = max = 1024.
- **Vulkan risks:**
  - #30230: about 250× slowdown when GPU memory is overcommitted, so never overcommit 4 GB;
  - #20081: garbled Qwen3.5 projector output on Vulkan, so compare GPU and CPU output;
  - Windows' 2-second GPU timeout (DeviceLost): shrink the image; never edit the registry.
- **Disk:** D: has about 11 GB free and the top three models need 8.94 GB. **Only UI-Venus-1.5-2B is downloaded first**; free space is checked before and after.

## Sources

The agents' full source lists are in their reports; the key ones:
- [sherpa-onnx KWS](https://k2-fsa.github.io/sherpa/onnx/kws/index.html)
- [openWakeWord](https://github.com/dscripka/openWakeWord)
- [livekit-wakeword](https://github.com/livekit/livekit-wakeword)
- [LiveKit APM](https://docs.livekit.io/python/livekit/rtc/apm.html)
- [Nemotron streaming EN](https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b)
- [RealtimeTTS](https://github.com/KoljaB/RealtimeTTS)
- [DBOS Transact](https://github.com/dbos-inc/dbos-transact-py)
- [OfficeCLI](https://github.com/iOfficeAI/OfficeCLI)
- [HHEM](https://huggingface.co/vectara/hallucination_evaluation_model)
- [MiniCheck](https://github.com/Liyan06/MiniCheck)
- [LLM-AggreFact](https://llm-aggrefact.github.io/)
- [WindowsAgentArena](https://github.com/microsoft/WindowsAgentArena)
- [pywin32 EvtSubscribe](https://mhammond.github.io/pywin32/win32evtlog__EvtSubscribe_meth.html)
- [llama.cpp multimodal](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)
- [UI-Venus-1.5 report](https://arxiv.org/abs/2602.09082)
- [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B)
- [winocr](https://pypi.org/project/winocr/)
- llama.cpp issues [#20081](https://github.com/ggml-org/llama.cpp/issues/20081) and [#30230](https://github.com/ggml-org/llama.cpp/issues/30230)
