# 02 - Local voice and small local models for the JARVIS personal assistant

Date of research: 2026-10-11. Author: research agent (read-only on hq; this file is the only hq change).
Scope: push-to-talk first, optional wake word, English + Hindi + Telugu input, TTS in at least English, small decision model, small planner LLM, runtime. Target: Windows 10 Boot Camp MacBookPro16,1 (i9-9980HK 8c/16t AVX2, 32 GB RAM, Radeon Pro 5500M 4 GB, Vulkan), WSL2 Ubuntu 24.04 on the same box.

Labels used everywhere: **measured here** (I ran it on this laptop today), **project-measured** (from the project's own docs/bench data, re-verified where noted), **published** (vendor/paper/leaderboard, not measured here), **UNVERIFIED** (could not confirm from a primary source).

---

## 0. Executive summary

1. **Hindi and Telugu are the hard part, and the answer is not Whisper.** Whisper (any size) is weak for Telugu (large-v2 FLEURS WER about 99%, published) and middling for Hindi (21.5%). **AI4Bharat IndicConformer** (MIT, 22 Indian languages, int8 CTC export about 190 MB per language, runs under sherpa-onnx) gave a **real-time factor of 0.04 to 0.05 on this CPU (measured here)** and published mean WER of 13.8% (Hindi) and 26.4% (Telugu) across benchmark sets. It is the first choice for Hindi and Telugu STT.
2. **English STT:** Parakeet TDT 0.6B v3 int8 via sherpa-onnx (RTF 0.11 to 0.14 measured here, correct punctuation, CC-BY-4.0). Fallback: whisper.cpp small-q5_1 (MIT).
3. **TTS:** English = Kokoro v1.0 **fp32** (RTF about 0.5 measured here; the int8 file is 3x slower on this CPU, RTF 1.7 to 2.0, do not use it). Hindi/Telugu = Piper voices (RTF 0.03 to 0.06 measured), but the Piper runtime is GPL-3.0 and several Hindi voices are CC BY-NC-SA. Kokoro Hindi exists but is grade C (4 voices, "MM minutes" of data); **Kokoro has no Telugu**.
4. **Wake word:** openWakeWord has a pretrained `hey_jarvis` model, but **its pretrained models are CC BY-NC-SA 4.0 (non-commercial)** and no false-accept/reject test set exists for it (its own doc says so). Fine for a personal project, a red flag if this ever ships.
5. **LLMs:** keep the llama.cpp Vulkan server already on the laptop (b11461, `-fa off` mandatory). Qwen3-4B Q4_K_M: **22.5 tok/s generation on Vulkan, 9.3 on CPU (measured here, llama-bench)**. Best small tool-calling candidate on published BFCL V4 evidence at <=4B that is also freely licensed: **Qwen3-4B-Instruct-2507** (non-live AST 87.9%, overall 35.7% because overall includes web-search/memory agentic tests). Qwen3.5-4B (Feb 2026, Apache-2.0) claims BFCL-V4 50.3 (vendor, not on the public leaderboard snapshot); the architecture loads and runs on this Vulkan stack at 0.8B scale (measured here), 4B not tried (2.7 GB download exceeded the 3 GB research budget).
6. **LAYA/OpenJev/TypeSafe:** OpenJev is an open-source, wire-compatible server for TypeSafe's hosted "Jev" System One API. LAYA and Verdict are small ModernBERT encoders that answer typed questions (yes/no, choice, score) from probabilities, with no generated text. The project's own benchmark already shows they do not beat the existing kNN+LLM union on risk classification (Verdict alone 58% exact, 35/200 false-allows; LAYA alone 32%, 143/200). Fine-tuning is feasible (Apache-2.0, training code in repo) but they must stay advisory.
7. **Nothing requires cmake or ffmpeg** on the recommended path: whisper.cpp ships a prebuilt Windows x64 CPU zip; sherpa-onnx ships Windows wheels. **There is no prebuilt whisper.cpp Vulkan binary** in the official release, so whisper on the GPU would need cmake + MSVC + Vulkan SDK (not installed, not attempted). CPU is the plan for all speech.

---

## 1. Facts about this machine (verified today)

| Fact | Value | How |
|---|---|---|
| CPU | i9-9980HK, 8c/16t, AVX2 + FMA + F16C, no AVX-512; ggml loaded `ggml-cpu-haswell.dll` | Win32_Processor; whisper.cpp system_info line |
| GPU visible to Windows | Only "AMD Radeon Pro 5500M", driver 30.0.13045.22003, AdapterRAM 4278190080 | Win32_VideoController. **The Intel UHD 630 iGPU is not enumerated**, so OpenVINO-on-iGPU is not an option; OpenVINO CPU would be the only OpenVINO path |
| Vulkan | llama.cpp b11461 Vulkan build finds 1 Vulkan device and runs on it | llama-bench output |
| RAM | 32 GB total, about 18 GB free at test time | Win32_OperatingSystem |
| Tools missing | cmake, ffmpeg not on PATH | `where.exe` |
| Already present | `D:\jarvis\tools\llama.cpp\{cpu,vulkan}` (b11461, commit 4d756bc72), `D:\jarvis\models\gguf\Qwen3-1.7B-Q4_K_M.gguf` (1.11 GB), `Qwen3-4B-Q4_K_M.gguf` (2.50 GB), `bge-small-en-v1.5-q8_0.gguf`; `D:\jarvis\hq\scripts\start-local-llm.ps1`; LM Studio (`C:\Users\venkat\.lmstudio`, models folder empty; not inspected further) | read-only listing |
| Existing server flags | `-ngl 99 -c 4096 -np 1 -fa off --jinja --chat-template-kwargs {"enable_thinking":false}`, comment: flash attention gives garbage on this driver | start-local-llm.ps1 header comment |
| Free space on D: | 44.6 GiB before, 41.3 GiB after (research dirs total about 2.8 GB; remainder is other agents' activity) | PowerShell |
| Background load | Other VS Code processes were running. CPU load was 62% for the first whisper run and 15-19% for later ones; clock readings were throttled (1.1 GHz idle reading). **Treat CPU timings as pessimistic and noisy.** | Win32_Processor |

---

## 2. Candidate table

Pins: GitHub HEAD SHA from `git ls-remote` on 2026-10-11; release tag/date from the project's releases.atom or PyPI upload time; license from GitHub license API / LICENSE file / HF card.

### 2.1 Speech recognition (STT), VAD, wake word

| Candidate | Pin (SHA / tag, date) | Code license | Weights license | Windows | CPU | Vulkan/GPU on this HW | API surface | Streaming | Hindi | Telugu |
|---|---|---|---|---|---|---|---|---|---|---|
| whisper.cpp (ggml-org) | HEAD d1be6fde11ac6e0407606b4e42fe72d34add8037; v1.9.5 2026-10-06 (build tag b5454 same commit) | MIT | Whisper weights MIT (large-v3-turbo HF card MIT; large-v3 HF card tagged Apache-2.0) | Prebuilt `whisper-bin-x64.zip` 8.5 MB (CPU, AVX2 DLL selection) and BLAS/CUDA zips | Yes, measured | Vulkan only by building (`-DGGML_VULKAN=1`); **no prebuilt Vulkan asset** in b5454. Not attempted | C API `whisper.h`; CLI `whisper-cli`; HTTP `whisper-server` (`POST /inference`); `whisper-vad-speech-segments`; `pywhispercpp` 1.5.1 wheel (MIT, 2026-08-22) | `whisper-stream` (SDL2, ~0.5 s windows) | Yes, weak-to-OK | Poor |
| sherpa-onnx (k2-fsa) | HEAD 9db1af1871ed78cee9357c23898e5a7d8702d21a (2026-10-09); v1.13.8 (PyPI 2026-09-10) | Apache-2.0 (TTS paths embed espeak-ng, GPL-3.0, see section 8) | per model | Windows x64/x86/arm64 wheels (2.2 MB + `sherpa-onnx-core`); also C API, C#, Go, etc. | Yes, measured | ONNX Runtime CPU. DirectML/Vulkan not documented in README (UNVERIFIED) | Python: OfflineRecognizer (from_nemo_ctc, from_transducer, from_whisper, from_qwen3_asr, from_omnilingual_asr_ctc, from_moonshine_v2 ...), OfflineTts, VoiceActivityDetector, KeywordSpotter; C API | Yes (streaming zipformer etc.) | via IndicConformer / Qwen3-ASR / Omnilingual / Whisper | via IndicConformer / Omnilingual / Whisper |
| AI4Bharat IndicConformer (monolingual hybrid CTC-RNNT, 120M each; 600M multilingual) | HF `ai4bharat/indic-conformer-600m-multilingual` sha 2a77d305038d (mod. 2026-10-08); `indicconformer_stt_{hi,te}_hybrid_ctc_rnnt_large` sha deada84ce880 / 4133836fef3b (2024-11-04) | MIT | MIT (gated: accept contact-sharing terms) | via sherpa-onnx int8 CTC export (community repo `parismitaglobalsolutions/indicconformer-sherpa-onnx`, sha 9721eb71eea1, 2026-09-22, repo tagged Apache-2.0, per-language weights keep MIT) | Yes, measured | n/a | sherpa `from_nemo_ctc(model, tokens)`; or `onnx-asr` with OpenVoiceOS per-language exports (MIT); original needs AI4Bharat NeMo fork | Offline | Yes | Yes |
| vasista22 Whisper fine-tunes (whisper-hindi-small/large-v2, whisper-telugu-tiny/base/small/large-v2) | HF sha fb4a24afc20c (hindi-small), 717212f9c4c1 (telugu-small), all 2023-04-24 | n/a | Apache-2.0 | Needs ggml/CT2/ONNX conversion (not done here); a third party ships sherpa-format int8 packs | n/a | n/a | Whisper API | n/a | Yes | Yes |
| Qwen3-ASR 0.6B / 1.7B | repo HEAD 7c6daf77a2421100f5fb066495372c00129d39ff (2026-06-26); HF 0.6B sha 5eb144179a02, 1.7B sha 7278e1e70fe2 (2026-01-30); `qwen-asr` 0.0.6 | Apache-2.0 | Apache-2.0 | Docs show CUDA examples only; sherpa-onnx has `sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25` (838 MB) | via sherpa int8 (not measured) | none | transformers/vLLM; sherpa `from_qwen3_asr` | vLLM only | **Yes (30 languages list)** | **No** |
| Meta Omnilingual ASR (CTC 300M/1B, v2) | HF `facebook/omniASR-CTC-300M` sha 8e35f0cc28fa (2025-11-27) | Apache-2.0 | Apache-2.0 | sherpa int8 300M = 279 MB, 1B = 751 MB (2026-02-05 v2) | via sherpa (not measured) | none | sherpa `from_omnilingual_asr_ctc`; 40 s audio limit | Offline | Claimed (1,600+ languages; per-language hi/te CER UNVERIFIED) | same |
| Parakeet TDT 0.6B v3 (NVIDIA) | HF sha 541d1f99c6b0 (mod. 2026-08-05); sherpa int8 asset 465 MB; whisper.cpp GGUF repo `ggml-org/parakeet-GGUF` sha 35156454d1a3 (2026-06-16, `parakeet-cli` ships in whisper.cpp b5454 zip) | NeMo Apache-2.0 (UNVERIFIED here) | **CC-BY-4.0** (the ggml-org GGUF is tagged MIT: attribution still owed) | via sherpa wheel or `parakeet-cli.exe` | Yes, measured via sherpa | none | sherpa `from_transducer(model_type="nemo_transducer")` | Offline (unified-en streaming variants exist for English) | **No** (25 European languages) | **No** |
| Moonshine (Useful Sensors / moonshine-ai) | HEAD 234f60faa0eb388b01cdf7e60aca232af37aefda; v0.1.6 2026-10-02; `moonshine-voice` 0.1.5 wheel 15.8 MB | MIT | MIT, **except** legacy non-streaming non-English (ar, ja, ko, zh, es, uk, vi) under non-commercial Moonshine Community License | win_amd64 wheel | Yes, measured via sherpa | none | `moonshine_voice` Python; sherpa `from_moonshine_v2` | Yes (streaming models) | No | No |
| Vosk (alphacep) | HEAD 05adbfcc0df27a1535913c6accd4b7fc60ffd59d; v0.3.50 2024-04-22 | Apache-2.0 | Apache-2.0 | win_amd64 wheel 13.3 MB | Yes (Kaldi) | none | Python/C | Yes | small-hi-0.22 (42 MB): WER 20.9 / 24.7 (IITM / MUCS); hi-0.22 1.5 GB: 14.8 / 13.1 | small-te-0.42 (58 MB): **WER 87.9 on FLEURS** (unusable) |
| faster-whisper / CTranslate2 | HEAD a52b026ac844c02117865e61968547639b60a6f8 (2026-10-10); v1.2.1 2025-10-31 | MIT | Whisper | Python wheel (needs ctranslate2, onnxruntime, av) | Yes (int8) | **No AMD/Vulkan backend** | Python | chunked | as Whisper | as Whisper |
| silero-vad | HEAD 1e261b036686cd0017d500ee96acd1c4ba572a9d; v6.2.3 2026-09-23 | MIT (GitHub API) | MIT (README badge text inconsistent, see section 8) | pip; ONNX via onnxruntime | <1 ms per 30 ms chunk single thread (published) | n/a | Python/ONNX; also inside sherpa-onnx and whisper.cpp (`ggml-silero-v6.2.0.bin`, 885 KB, measured working) | Yes | language-agnostic | language-agnostic |
| openWakeWord (dscripka) | HEAD 368c03716d1e92591906a84949bc477f3a834455 (2025-12-30); last release v0.6.0 2024-02-11; PyPI 0.6.0 | Apache-2.0 | **CC BY-NC-SA 4.0** for all pretrained models (HF `davidscripka/openwakeword` sha fee5800ab871) | onnxruntime only on Windows (tflite not installed) | Yes (15-20 models on one RPi3 core, published) | n/a | Python `Model(wakeword_models=["hey_jarvis"]).predict(frame)`, 16 kHz int16, 80 ms multiples | Yes | English only | English only |
| sherpa-onnx KWS (zipformer, open vocabulary) | part of sherpa-onnx | Apache-2.0 | models: zh-en 3M (2025-12-20), wenetspeech 3.3M, gigaspeech 3.3M (English); weight licence UNVERIFIED | yes | yes | n/a | `KeywordSpotter`; keywords as token lines with `:boost #threshold` | Yes | no | no |

### 2.2 Text-to-speech

| Candidate | Pin | Code license | Weights license | Size | Languages relevant | Speed here | Notes |
|---|---|---|---|---|---|---|---|
| Kokoro-82M v1.0 (+ sherpa-onnx export) | `hexgrad/kokoro` HEAD dfb907a02bba8152ca444717ca5d78747ccb4bec (2025-08-06), PyPI kokoro 0.9.4; HF `hexgrad/Kokoro-82M` sha f3ff3571791e (2025-04-10); `kokoro-onnx` HEAD 3596b26764286a7de9d90c363e988d50578918e5 (2026-09-01), PyPI 0.6.1, model-files-v1.1 (2025-03-01); sherpa `kokoro-multi-lang-v1_0` | Apache-2.0 (kokoro), MIT (kokoro-onnx) | Apache-2.0 | fp32 326 MB, int8 about 126 MB archive, fp16 156 MB (kokoro-onnx), voices 27 MB | 8 languages, 54 voices; Hindi = hf_alpha, hf_beta, hm_omega, hm_psi, **overall grade C**, "MM minutes" training, weak G2P; **no Telugu** | **fp32 RTF 0.48-0.62; int8 RTF 1.73-1.99** (measured, sherpa 1.13.8, 4 and 8 threads) | espeak-ng (GPL-3.0) used for non-English and OOD fallback; model card lists 2 CC BY datasets (tnc under 1 h, SIWIS under 11 h) in training data |
| Piper | `rhasspy/piper` (archived) HEAD 73c04d81d5590ecc46e522de3601ce7fb29fc2be, last release 2023.11.14-2; **active fork `OHF-Voice/piper1-gpl`** HEAD 6321ef9932b7bd573010f066a738b182d5c7461c, v1.8.0 2026-09-04, PyPI `piper-tts` 1.8.0 (win_amd64 abi3 wheel 32.5 MB) | rhasspy: MIT; **piper1-gpl: GPL-3.0-or-later** | per voice (see below) | about 60 MB per medium voice | en_US many; **hi_IN: pratham, priyamvada, rohan; te_IN: maya, padmavathi, venkatesh** | **RTF 0.03-0.06** (measured, piper-tts 1.8.0, python API, 1 sentence) | `PiperVoice.load(...).synthesize(text)`; also CLI/HTTP server docs in repo |
| Indic Parler-TTS (AI4Bharat) | HF `ai4bharat/indic-parler-tts` sha 660bc53c64ae (mod. 2026-10-06) | Apache-2.0 | Apache-2.0 (datasets: GLOBE "CC V1", IndicTTS, LIMMITS, Rasa CC BY 4.0) | 0.9B, F32 about 3.7 GB | Hindi: Rohit, Divya, Aman, Rani; Telugu: Prakash, Lalitha, Kiran | not measured (UNVERIFIED; 0.9B autoregressive on CPU is likely slower than real time) | gated (contact sharing); card recommends newer Indic-Speak |
| IndicF5 | HF `ai4bharat/IndicF5` sha fb652a96e508 (2026-10-06) | MIT tag | MIT tag (gated; voice-cloning terms; training data Rasa, IndicTTS, LIMMITS, IndicVoices-R licences not stated on card) | 0.4B F32 | 11 languages incl. hi, te | not measured | needs reference audio + its transcript for every call |
| Svara-TTS v1 | HF `kenpath/svara-tts-v1` sha db8a02fc1e4e (2025-10-27) | Apache-2.0 tag | Apache-2.0 tag (Orpheus/Llama-style 3.3B; upstream base licence UNVERIFIED) | 3.3B | 19 languages incl. hi, te | not measured; 3.3B is too heavy for CPU real time | GGUF tag present |
| Indic-Speak (Bodhan AI) | HF `bodhan-ai/indic-speak` sha 76e0814f1893 (2026-09-07) | n/a | **Indic Open Model License v1.0** (custom; hosting-as-a-service needs written permission; 500M MAU / $250M revenue cap; built on Llama-3.2-3B) | 3.3B | hi: Amit, Kavya; te: Sravani, Vamsi | not measured | gated, needs transformers>=5 |
| Meta MMS-TTS (hin, tel) | `facebook/mms-tts-hin` sha 1d83b223ec78, `mms-tts-tel` sha dea6807154ac (2023-09) | Transformers | **CC-BY-NC-4.0** | 36 M params each | hi, te | not measured | non-commercial |
| Qwen3-TTS 12Hz 0.6B/1.7B | repo HEAD 022e286b98fbec7e1e916cb940cdf532cd9f488e (2026-03-17); HF 0.6B-Base sha 5d83992436ea | Apache-2.0 | Apache-2.0 | 0.6B = 0.91B params bf16 | zh, en, ja, ko, de, fr, ru, pt, es, it. **No Hindi, no Telugu** | CPU/Windows not documented (UNVERIFIED); claims 97 ms first packet on GPU | examples assume CUDA + FlashAttention 2, so not usable on the 5500M via this stack |

Piper voice licences (from each voice's MODEL_CARD in `rhasspy/piper-voices`, sha c10ece1aade4, modified 2026-09-17):

| Voice | Dataset licence | Note |
|---|---|---|
| en_US lessac medium | Blizzard 2013 Lessac licence (custom page) | the voice most people use |
| en_US ryan high | CC BY-NC-SA 4.0 | non-commercial |
| hi_IN pratham, priyamvada (medium) | CC BY-NC-SA 4.0 (dataset AI4Bharat indicnlp_corpus, trained by PravalX) | non-commercial |
| hi_IN rohan (medium) | IIT Madras IndicTTS licence (`license.pdf`, terms UNVERIFIED), finetuned from lessac | |
| te_IN maya (medium) | IIT Madras IndicTTS licence (terms UNVERIFIED), finetuned from lessac | |
| te_IN padmavathi, venkatesh (medium) | **CC-BY-4.0** (IndicVoices-R) | cleanest Telugu voices |

### 2.3 Small LLMs (<= 4B class, or close) for planning / function calling

BFCL numbers are from the BFCL V4 leaderboard CSV (`gorilla.cs.berkeley.edu/data_overall.csv`, "last updated 2026-04-12", fetched 2026-10-11). "Overall" averages non-live AST, live, multi-turn, web search and memory, so small models score low on it; "non-live AST" and "live" are the single-turn function-call numbers that matter for simple tool use.

| Model | HF pin (sha, date) | Licence (code/weights) | Params | GGUF Q4_K_M | Context | BFCL V4 (public leaderboard) | Tool-calling note |
|---|---|---|---|---|---|---|---|
| **Qwen3-4B-Instruct-2507** | cdbee75f17c0 (2025-09-17 mod.) | Apache-2.0 | 4.02B | 2.50 GB | 262,144 native | **overall 35.68% (rank 54), non-live 87.88, live 76.39, multi-turn 22.12** (FC mode); vendor card BFCL-v3 61.9 | non-thinking only; Hermes-style tool calls via chat template |
| Qwen3-4B (hybrid thinking; what the project runs now) | 1cfa9a720891 | Apache-2.0 | 4.02B | 2.50 GB (local file 2,497,281,312 B) | 32K native (UNVERIFIED, not re-fetched) | not on the snapshot; vendor card BFCL-v3 57.6 non-thinking (from the 2507 card's comparison table) | needs `enable_thinking=false` (project already sets it) |
| Qwen3-1.7B | 70d244cc86cc | Apache-2.0 | 2.03B incl. embeddings | 1.11 GB | 32K (UNVERIFIED, not re-fetched) | overall 28.41% (rank 71), non-live 82.92, live 74.61, multi-turn 11.00 | project's fast fallback |
| Qwen3.5-4B (2026-02-27) | 851bf6e806ef (2026-03-02) | Apache-2.0 | 4.66B incl. vision tower | 2.74 GB (+ 0.68 GB mmproj, not needed) | 262,144 native | **not on the snapshot**; vendor card: BFCL-V4 50.3, TAU2 79.9, IFEval 89.8 (9B: 66.1) | hybrid Gated-DeltaNet + attention; thinking on by default (`enable_thinking=false`); 201 languages claimed |
| Qwen3.5-2B / 0.8B | 15852e8c1636 / 2fc06364715b | Apache-2.0 | 2.27B / 0.87B | 1.28 GB / 0.53 GB | 262,144 | not on the snapshot | 0.8B ran on this Vulkan stack (measured) |
| Gemma 4 E4B-it (2026-03-02) | ee0ef6023621 (2026-07-20 mod.) | Apache-2.0 tag (card also links a "Gemma 4 license" page: confirm before relying) | 4.5B effective, 8.0B with embeddings | ggml-org GGUF Q4_0 = 4.59 GB (too big for 4 GB VRAM) | 128K | none; Tau2 42.2% | E2B: 3.11 GB Q4_K_M, Tau2 24.5% |
| Gemma 3 4B-it / 1B-it | 093f9f388b31 / dcc83ea841ab | Gemma terms (gated) | 4.3B / 1.0B | n/a | 128K / 32K | 19.62% (rank 101) / 7.17% (rank 109), prompt mode | weak function calling |
| Llama 3.2 3B Instruct | 0cb88a4f764b | Llama 3.2 Community Licence (gated) | 3.21B | 2.02 GB | 128K | overall 21.95% (rank 98), non-live 82.67, live 58.33, multi-turn 4.00 | Hindi among 8 official languages, Telugu not |
| Phi-4-mini-instruct | cfbefacb9925 | MIT | 3.84B | 2.49 GB | 128K (card; not re-fetched) | not on the snapshot | multilingual list has no hi/te |
| SmolLM3-3B | a07cc9a04f16 | Apache-2.0 | 3.08B | 1.92 GB | 64K/128K (UNVERIFIED) | not on the snapshot | no hi/te in language list |
| Granite 4.1 3B | c0650403e44e (2026-05-04) | Apache-2.0 | 3.40B | 2.10 GB | UNVERIFIED | not on the snapshot (Granite-4.0-350m: 18.98%) | |
| LFM2.5-2.6B | 654f9463ce32 (2026-08-24) | LFM Open Licence v1.0 (custom; revenue thresholds UNVERIFIED) | 2.70B | 1.67 GB | UNVERIFIED | not on the snapshot | lists hi among 16 languages |
| xLAM-2-3b-fc-r | be83286f6a1a | **CC-BY-NC-4.0** | 3.09B | n/a | | overall 41.22% (rank 42), non-live 82.96, live 62.92, multi-turn 58.38 | best multi-turn at this size but non-commercial |
| Hammer2.1-3b / 1.5b | 702ce4215e13 / 62c20f5e1bfa | qwen-research / CC-BY-NC-4.0 | 3.09B / 1.54B | | | 29.71% / 27.88% | |
| FunctionGemma 270M | 39eccb091651 (2026-01-14) | Gemma terms (gated) | 0.27B | 0.25 GB | | not on the snapshot | meant to be fine-tuned for one tool set; evals UNVERIFIED |

### 2.4 Runtimes

| Runtime | Pin | Licence | Works on this exact HW? | Evidence |
|---|---|---|---|---|
| **llama.cpp (llama-server, llama-bench)** | local b11461 (commit 4d756bc72); upstream HEAD 23b0202a189c44a54625aadcb37a946dd1d6278d = b11552 (2026-10-10) | MIT | **Yes, Vulkan and CPU, measured**. Needs `-fa off`; default context for 262K-context models must be capped (`-c`), else Vulkan allocation of 2.2 GB compute buffer fails (measured, Qwen3.5-0.8B) | llama-bench tables below |
| Ollama | HEAD eab97e9f92b9a25c2d52d2cc6c1b1c99bd9fae21, v0.40.2 (2026-10-09) | MIT | UNVERIFIED on a Radeon Pro 5500M (Navi 14 / gfx1012). Official docs: Vulkan enabled by default; ROCm-on-Windows list does not include this GPU; `GGML_VK_VISIBLE_DEVICES` for mixed GPUs. Project's flash-attention garbage output on this driver may recur because Ollama manages its own flags | docs.ollama.com/gpu |
| LM Studio | installed on the laptop | proprietary freeware, free for work and personal use since 2025-07-08 | UNVERIFIED here. Ships a Vulkan llama.cpp runtime; AMD users report runtime regressions | lmstudio.ai/blog/free-for-work |
| ONNX Runtime GenAI | README only | not stated in README (UNVERIFIED; repo is Microsoft OSS) | Providers: CPU, CUDA, DirectML, OpenVINO, QNN, WebGPU; "AMD GPU is on the roadmap"; DirectML on a 2019 Boot Camp Radeon UNVERIFIED | README |
| OpenVINO (GenAI) | not fetched | Apache-2.0 (UNVERIFIED here) | **iGPU path is dead**: the Intel UHD 630 is not visible to Windows. CPU path (AVX2) possible, no reason to prefer it over llama.cpp | Win32_VideoController |
| ONNX Runtime (CPU) | 1.31.0 (PyPI 2026-10-09), MIT | MIT | Yes, used by sherpa-onnx and piper (measured) | pip |
| WSL2 | Ubuntu 24.04 "JarvisLab" running | n/a | Not tested. GPU compute in WSL2 for an AMD card is not exposed through CUDA/ROCm; assume CPU only. Keep voice on Windows | UNVERIFIED |

---

## 3. Per-candidate details

### 3.1 whisper.cpp

- **Release layout:** the "v1.9.5" GitHub release has **no assets**; binaries live in the numbered build release `b5454` (published 2026-10-06T16:51Z, commit d1be6fde...). Assets: `whisper-bin-x64.zip` (8.5 MB, my SHA-256 `6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb`), `whisper-blas-bin-x64.zip` (20.7 MB), CUDA 11.8/12.4 zips (272/653 MB), Win32, Windows arm64 and Adreno OpenCL. **No Vulkan asset.**
- **Contents of the x64 zip:** `whisper-cli`, `whisper-server`, `whisper-stream`, `whisper-command`, `whisper-vad-speech-segments`, `whisper-quantize`, `whisper-talk-llama`, **`parakeet-cli`** plus ggml CPU backend DLLs (sse42, sandybridge, haswell, alderlake, skylakex, cascadelake, icelake...). It selected `ggml-cpu-haswell.dll` here.
- **Server:** `whisper-server --host 127.0.0.1 --port 8080 -m model --vad -vm ggml-silero-v6.2.0.bin`; endpoint `/inference`; `--convert` needs ffmpeg (not installed, so send 16 kHz mono WAV). **Default port 8080 clashes with the project's llama-server on 8080**; use another port.
- **VAD:** built-in Silero v6.2.0 (`ggml-silero-v6.2.0.bin`, 885,098 bytes from `ggml-org/whisper-vad`, measured working: `whisper-vad-speech-segments` found 4 segments in jfk.wav).
- **Model sizes (published README):** tiny 75 MiB, base 142 MiB, small 466 MiB, medium 1.5 GiB, large 2.9 GiB; memory about 273 MB / 388 MB / 852 MB / 2.1 GB / 3.9 GB.
- **Speed, measured here** (jfk.wav = 11.0 s, English, 8 threads, CPU only, b5454; note whisper pads to a 30 s window unless `-ac` is set):

| Model file | Size | Beam 5 total (s) | Beam 1 total (s) | Beam 1 + `-ac 640` total (s) | RTF (best case) |
|---|---|---|---|---|---|
| ggml-base.bin (F16) | 148 MB | 2.7 (encode 1.5) | 3.0 (encode 2.2) | not run | about 0.25 |
| ggml-small-q5_1.bin | 190 MB | 8.4 (encode 6.0) | 8.4 (encode 7.3) | **1.9** (encode 1.4) | **0.18** |
| ggml-large-v3-turbo-q5_0.bin | 574 MB | 54.1 (encode 50.1, run under 62% background load) | 29.6 (encode 28.0) | **9.8** (encode 8.7) | 0.89 |

  The `-ac` (audio-context) trick shrinks the encoder window to the clip length; it is documented as experimental, transcripts for the 11 s clip were still correct, but accuracy impact on Hindi/Telugu is UNVERIFIED. Four threads were 25-40% slower than 8.
  Conclusion: **large-v3-turbo on CPU is not interactive on this laptop; small-q5_1 with `-ac` is.** Published (not measured) quality ordering for English is turbo > small > base.
- **Hindi/Telugu, published:** Whisper paper (Radford et al. 2022, arXiv 2212.04356, Appendix D.2.4 Table 13, FLEURS WER, columns read from rotated ISO-ordered headers): Hindi tiny 102.3, base 101.1, small 38.4, medium 26.8, large 26.9, **large-v2 21.5**; Telugu tiny 105.1, base 105.2, small 98.2, medium 82.8, large 100.6, **large-v2 99.0** (column mapping of the Telugu row is my reading of rotated ISO-ordered headers in the extracted PDF; it is consistent with the paper's 4.3 h of Telugu training audio, but treat as indicative). Appendix E: training audio for Hindi 12 h, Telugu 4.3 h of labelled ASR data. The README's per-language chart (large-v3/v2, Common Voice 15 and FLEURS) is an image I could not read as text. OpenAI states large-v3 reduces errors 10-20% over large-v2 (HF card, not re-fetched).
- **Hindi/Telugu, measured here (n=1, clean synthetic Piper speech, NOT a WER test):** small-q5_1 `-l hi` produced Devanagari with several word errors; `-l te` produced Telugu words written in Devanagari script with errors. turbo-q5_0 `-l hi` was close (3 small errors); `-l te` produced Telugu script but with many word errors. IndicConformer on the same Telugu clip was word-perfect.
- **Windows build from source:** MSVC or MinGW supported; Vulkan needs `cmake -B build -DGGML_VULKAN=1`; cmake/Vulkan SDK absent here.
- **Known issues:** large model memory; hallucination on silence (use VAD); my 62% load run shows sensitivity to contention.

### 3.2 sherpa-onnx

- v1.13.8: Windows x64 wheels for cp310-cp313 (2.2 MB) depend on `sherpa-onnx-core`; installed here with numpy, soundfile, onnxruntime 1.31.0 (needed by piper), total venv 219 MB. Release notes mention Intel NPU via OpenVINO, fixes for hallucinated text on silence (Moonshine v2, FunASR-Nano, Cohere Transcribe) and a race in Qwen3-ASR offline.
- **ASR families available as prebuilt assets in the `asr-models` release (499 assets):** NeMo (Parakeet 110M/0.6B v2/v3, Canary 180M flash en/es/de/fr, ctc/transducer), Whisper (19), Moonshine (12), Zipformer (80), Paraformer, SenseVoice (zh/en/ja/ko/yue), Dolphin (multi-language CTC base/small), FireRedASR, FunASR-Nano, MedASR, Cohere Transcribe (14 languages: no hi/te), **Qwen3-ASR 0.6B int8**, **Omnilingual ASR 300M/1B CTC**. None is named hindi/telugu; Indian-language coverage comes from Omnilingual, Qwen3-ASR (Hindi), Whisper and from the community IndicConformer export.
- **TTS assets** (`tts-models`, 200 assets): Kokoro v0.19/v1.0/v1.1 (fp32 and int8), Piper/VITS (many), MMS (deu, eng, fra, nan, rus, spa, tha, ukr only), Matcha, ZipVoice, Pocket-TTS, Supertonic. **No Hindi or Telugu Piper voices are pre-converted** (a Gujarati mimic3 voice exists); Hindi comes from Kokoro.
- **VAD:** Silero and TEN-VAD style models; KWS: zipformer open-vocabulary (zh-en 3M, wenetspeech 3.3M, gigaspeech 3.3M).
- **Licensing wrinkle:** CMake fetches `csukuangfj/espeak-ng` and `piper-phonemize` for Piper/Kokoro paths, so the wheel's TTS code links GPL-3.0 espeak-ng. Fine for personal use; matters if redistributed.

### 3.3 IndicConformer (Hindi, Telugu STT)

- **What:** AI4Bharat "IndicConformer": monolingual hybrid CTC-RNNT Conformer-Large (120M) per language, plus a 600M multilingual one. MIT (HF cards and GitHub). Download is gated on HF (accept contact sharing).
- **Runtime used here:** `parismitaglobalsolutions/indicconformer-sherpa-onnx` (Apache-2.0 tag; each export keeps upstream MIT) provides **int8 CTC-head-only** ONNX per language (188.4 MiB), one shared 5,632-token `tokens.txt` for all 22 Indian languages, and `from_nemo_ctc` usage. Only the CTC head is exported, so RNNT-quality gains are not available.
- **Speed, measured here:** hi and te int8, 7-8 s clip: decode 0.28 to 0.40 s, **RTF 0.036 to 0.051** (4 and 8 threads), model load about 1 s. This is roughly 4-5x faster than whisper-small-q5_1 with `-ac` and 20x faster than turbo.
- **Quality, published:** SraVaani 1.0 paper (arXiv 2608.08235, Aug 2026, Table 3, unweighted mean WER over eval sets): **Hindi 13.8 (8 sets), Telugu 26.4 (6 sets)** for IndicConformer-600M-Multilingual, vs Gemini 3 Flash 14.5 / 28.9 and Sarvam Saaras v3 14.5 / 28.7. The HF card lists Hindi WER 13.2 on Vaani-Benchmark. IndicWhisper on Vistaar (older): Kathbath Hindi 10.3, Telugu 25.0; FLEURS Hindi 15.0, Telugu 25.4 (from a search snippet of the Vistaar README, row labels not confirmed, UNVERIFIED). **These were not measured here**; the loopback below is not an accuracy test.
- **Loopback sanity (measured here, synthetic):** Piper hi_IN-rohan speech decoded to "नमस्ते आज मौसम बहुत अच्छा है कृपया मेरे ल कल सुबह नौ बजे की बैठक तय कर दीजिय" (one word clipped, one syllable dropped: about 2 errors in 15 words); Piper te_IN-venkatesh decoded word-perfect (punctuation removed). Clean TTS audio flatters any ASR.
- **Caveat:** the CTC head outputs no punctuation/casing; the card says the 600M multilingual model has a newer successor ("Indic-Transcribe", third-party, Canary-based, gated, custom licence) which I did not evaluate.

### 3.4 Qwen3-ASR (official)

- 0.6B (0.94B params, bf16) and 1.7B (2.35B); Apache-2.0; 30 languages + 22 Chinese dialects including **Hindi**, **not Telugu**; FLEURS en 4.39 / 3.35, multilingual FLEURS 7.57 / 4.90 (0.6B / 1.7B); no Hindi-specific number published. Streaming only through vLLM (CUDA). The repo examples are CUDA + FlashAttention; Windows/CPU not documented. The practical CPU path is sherpa's int8 export (838 MB, `from_qwen3_asr`), **not measured here**.

### 3.5 Parakeet TDT 0.6B v3 and Moonshine (English)

- Parakeet v3 int8 via sherpa (measured here, 4 threads): jfk.wav (11 s) decoded in 1.32 s (RTF 0.12) with capitalisation and punctuation; the German sample decoded correctly. 8 threads were not faster (RTF 0.11-0.22). Load 3.8-4.2 s. Languages: 25 European languages; **no Hindi/Telugu**. Weights CC-BY-4.0.
- Moonshine base-en quantized (sherpa `from_moonshine_v2`, measured here): correct on a 3.85 s clip at RTF 0.07, load 1.6 s, 138 MB. **It returned an empty result with an ONNX Runtime broadcast error on the 11 s jfk.wav in sherpa 1.13.8** (cause UNVERIFIED; sherpa release notes list Moonshine v2 silence-hallucination fixes, so version sensitivity is plausible). Treat as short-command-only until retested.

### 3.6 openWakeWord and "hey jarvis"

- Pretrained list in README: alexa, hey mycroft, **hey jarvis**, hey rhasspy, current weather, timers. `docs/models/hey_jarvis.md`: two 3-layer fully-connected networks (102,849 parameters, 0.41 MB) on a frozen Google speech embedding; about 200,000 synthetic clips (WaveGlow/LibriTTS and VITS/VCTK); negatives from ACAV100M, FSD50k, FMA, about 30,000 h overall. **Performance section: "Currently, there is not a test set available to evaluate this model."** Generic target for all included models: below 5% false-reject and below 0.5 false-accept per hour with threshold tuning (aims, not results). The phrase "jarvis" alone likely has higher false-reject.
- **Licence:** code Apache-2.0; **all pretrained models CC BY-NC-SA 4.0** "due to the inclusion of datasets with unknown or restrictive licensing" (README and HF card). English only.
- Windows: ONNX Runtime only (tflite not installed on Windows). Default threshold 0.5, 16 kHz int16 frames in multiples of 80 ms, optional Silero VAD gate (`vad_threshold`), optional Speex noise suppression (Linux only). Echo from device audio raises false-rejects unless AEC is used (relevant if TTS plays through the same speakers).
- Last release v0.6.0 (2024-02-11); last commit 2025-12-30 (ONNX to tflite conversion flag). Not installed or run here.
- Custom training: Colab notebooks, synthetic data; "under an hour" for a basic model. Using your own TTS (Piper is GPL runtime, outputs are fine for personal use) gives you your own weights; their licence then depends on the TTS and negative datasets you use (UNVERIFIED).

### 3.7 Kokoro

- 82M parameters, Apache-2.0. v1.0 (2025-01-27): 8 languages, 54 voices. Hindi: hf_alpha, hf_beta (female), hm_omega, hm_psi (male), "Target Quality B, Training Duration MM minutes, Overall Grade C" (VOICES.md). Hindi G2P goes through misaki/espeak-ng. The card documents weakness on utterances under 10-20 tokens (bundle short ones).
- **Speed, measured here** (sherpa 1.13.8, CPU, 5.6 s English sentence and 6.7-7.2 s Hindi sentence):

| Variant | Threads | English RTF | Hindi RTF | First load |
|---|---|---|---|---|
| int8 (`kokoro-int8-multi-lang-v1_0`, 126 MB archive) | 4 / 8 | 1.79-1.81 / 1.81-1.99 | 1.73-1.74 / 1.79-1.98 | 3.2-3.4 s |
| fp32 (`kokoro-multi-lang-v1_0`, 334 MB archive, model.onnx 326 MB) | 4 / 8 | 0.55-0.62 / 0.48-0.55 | 0.56-0.57 / 0.48-0.53 | not recorded |

  The int8 file is slower than real time on this CPU (no VNNI on Coffee Lake; dynamic-quant ops are slow). **Use fp32.** About 3 s of latency to first audio for a short sentence unless sentences are streamed through sherpa's callback and split.
- `kokoro-onnx` 0.6.1 (MIT) wheel needs `espeakng-loader`, `phonemizer`, onnxruntime; Windows install not documented; not run here (sherpa path used instead).

### 3.8 Piper

- The original `rhasspy/piper` is archived (MIT). The maintained fork **`OHF-Voice/piper1-gpl` is GPL-3.0-or-later** because it embeds espeak-ng; `pip install piper-tts` 1.8.0 ships a 32.5 MB Windows wheel with the phonemizer.
- Measured here (python API, medium voices, 5-8 s sentences, 1 thread-pool default): RTF 0.050-0.057 en_US-lessac, **0.032-0.036 hi_IN-rohan, 0.035-0.041 te_IN-venkatesh**; first load 5-8 s (cold). Audio files were written but **not listened to**, so naturalness is UNVERIFIED.
- Voice licence table in section 2.2. Telugu: only the IIT-Madras-derived voices (maya) and the CC-BY-4.0 IndicVoices-R voices (padmavathi, venkatesh) exist; Hindi: rohan (IITM licence) and two NC-SA voices.

### 3.9 Other Hindi/Telugu TTS

- **Indic Parler-TTS** (Apache-2.0, 0.9B): proper Hindi and Telugu speakers with natural-language style prompts; text is conditioned by a description string; CPU real-time unverified; gated. Best open-licence "quality" candidate if Piper is not good enough, but the card itself points to a newer Indic-Speak (custom licence).
- **IndicF5** (MIT tag, 0.4B, needs reference audio and its transcript every call; gated; voice-cloning terms): not suited to a fixed assistant voice without a licensed reference clip.
- **MMS-TTS**: CC-BY-NC-4.0.
- **Qwen3-TTS**: no Hindi/Telugu.

### 3.10 Other STT: Vosk, faster-whisper

- Vosk (Apache-2.0, last release 2024-04-22): Hindi small 42 MB WER 20.9/24.7 (IITM/MUCS), 1.5 GB model 14.8/14.8/13.1; **Telugu small 58 MB WER 87.9 (FLEURS)**; English small 40 MB WER 9.85 LibriSpeech. Useful only as a tiny offline fallback for Hindi.
- faster-whisper (MIT): CPU int8 only on this hardware (CTranslate2 has no Vulkan). No reason to prefer it over whisper.cpp or sherpa here except the ecosystem; the PyPI wheel needs `av` and ctranslate2.

---

## 4. Small LLM speed and fit on this exact hardware

**Measured here** with the project's own `D:\jarvis\tools\llama.cpp` b11461 (commit 4d756bc72), `llama-bench -p 128 -n 32 -r 2`, Vulkan with `-ngl 99 -fa 0`, CPU with `-t 8`, run after the voice tests with the machine otherwise idle-ish:

| Model (Q4_K_M) | File | CPU pp128 / tg32 (t/s) | Vulkan pp128 / tg32 (t/s) | Notes |
|---|---|---|---|---|
| Qwen3-1.7B | 1.03 GiB | 125.3 / 20.5 | 919 / **87.6** | project server measured 70-83 tok/s |
| Qwen3-4B | 2.32 GiB | 50.5 / 9.3 | 313 / **22.5** | project doc says about 35 tok/s in the server and "about 3.6 GB of the 4 GB card"; bench shows 22.5, difference unexplained (GPU state? measurement method?) |
| Qwen3.5-0.8B | 497 MiB | 205 / 30.1 | 1578 / 119.4 | first proof that the `qwen35` architecture runs on this driver; sane output verified ("The capital of France is the capital of the country."); **default context 262,144 fails to allocate (2.2 GB compute buffer): pass `-c 2048`+** |

Extrapolation (UNVERIFIED): Qwen3.5-4B Q4_K_M (2.74 GB) should land near Qwen3-4B speeds (about 20 tok/s Vulkan, 9 CPU); only one 4B-class model fits the 4 GB card at a time, so TTS/STT stay on CPU and the LLM keeps the GPU.

Function-calling reliability evidence: see section 2.3. Honest reading: at 4B, single-turn calls are good (non-live AST 87.9%, live 76.4% for Qwen3-4B-Instruct-2507), multi-turn and agentic search/memory are poor (22%); keep the planner shallow with structured JSON constrained by grammar/`response_format` (llama-server supports both), as the project already does.

---

## 5. LAYA / OpenJev / TypeSafe: what the project actually uses

### 5.1 Chain of components (verified from local files and upstream)

| Layer | Identity | Pin | Licence |
|---|---|---|---|
| Commercial API | **TypeSafe AI "Jev"**, hosted System One model, early access via waitlist, input $0.042/M tokens, output free; `POST /v1/systemone` (blog "Introducing System One models and Jev", page date 2026-10-09 as fetched; docs.typesafe.ai) | n/a (hosted) | not stated; weights not published |
| Open server (what is installed) | **`razorback16/openjev`** v0.6.0 "Open, Jev-compatible System One decision server on DiffusionGemma" | HEAD 75f22b6dad8c360fdba0e0ebd3dc0a1187628f60 (2026-10-06), verified identical to the local checkout in `D:\jarvis-apps\openjev` | Apache-2.0 |
| Server's own big model (not used by the project) | DiffusionGemma 26B-A4B (NVIDIA NVFP4 export), vLLM or MLX | HF `nvidia/diffusiongemma-26B-A4B-it-NVFP4` sha ec4ff3df2050 | Apache-2.0 |
| **`laya-1.0`** (as named in OpenJev) | **Laya** by Nandakishor M / Convai Innovations, checkpoint `convaiinnovations/laya-typed-decisions`, ModernBERT-large, 421.3M params, 1,024-token context | GitHub `NandhaKishorM/laya` HEAD 68804629e8ccd9d616d48a40e87de9fabbeae069, v0.4.2 (2026-10-10; project pins `laya==0.3.6`); HF sha e929ae5cf69b (2026-10-03) | Apache-2.0 (code, weights) |
| **`verdict-1.4`** | **Verdict** by Heman10x, checkpoint `heman10x/rlcd-modernbert-151m`, ModernBERT-base + GLiClass head, 151.4M params, 512 tokens | GitHub `Heman10x-NGU/Verdict-open-jev` HEAD 30f15564821626ca5c1ad5b2638c4eb7078787dd (2026-09-20); HF sha 8af2496eb63c | Apache-2.0 |
| SDK | `typesafe-sdk` 0.7.4 (PyPI 2026-10-09) used only by OpenJev tests | | licence field empty |

OpenJev README states it "is an independent project. It is not affiliated with or endorsed by TypeSafe AI."

### 5.2 What the models are

- Not "risk classifiers". They are **typed decision engines**: input `state` (text) + `questions` map; each question is `noul` (yes/no, returns P(true)), `choice` (up to 255 options for OpenJev/Laya, 24+1 abstain for Verdict; returns choice, probabilities, confidence = 1 - H(p)/ln K) or `score` (ordinal, 1-10 levels). No text is generated, so output cannot be off-schema.
- Wire schema used by `bench/openjev_risk.py`: `POST /v1/systemone {"model":"verdict-1.4","state":<text>,"questions":{"risk":{"type":"choice","instructions":"...","criteria":{"GREEN":"read-only inspection","YELLOW":"...","RED":"...","DENY":"..."}}}}` -> `answers.risk.{choice, confidence, probabilities}`, `usage.input_tokens`. Labels are whatever the caller supplies: the project supplied GREEN/YELLOW/RED/DENY with its own definitions; **neither encoder was trained on those labels**.
- Laya's checkpoint was fine-tuned on four synthetic workflows (agent-trace observability, customer service, invoice processing, security incidents) with RLCD (noise-on-logits REINFORCE + strictly proper scoring + soft CE to teacher distributions); official test split 400 cases / 2,000 decisions: accuracy 0.766 (Jev 1.13.0 0.727, third-party figure), soft accuracy 0.471 vs 0.580, ECE 0.213 (over-confident), base `laya` 0.362. The card says "Specialist model; expect base-level or worse performance elsewhere" and warns confidence is uncalibrated, plus choice accuracy drops sharply beyond about 20 options and the English checkpoint is 0.000 on Khmer at 95% raw confidence.
- Verdict: JevBench v1.4 (231 tasks) Easy 87.5, Standard 69.4, Hard 36.9; on TypeSafe's public eval (337 cases) OpenJev-Verdict scored 48.07 vs 90.80 for Jev (author's own numbers); abstention recall falls to 18% with confusable distractors; option-order reversal flips 3% of choices; only 3 of 12 cookbook tasks passed its gate.

### 5.3 Project numbers (project-measured 2026-10-09, risk_fresh23: 260 items, 200 high-risk; I recomputed them from `bench/data/openjev_results.json` and `risk_fresh23.jsonl` and they match docs/03_DECISIONS.md)

| Mechanism | Exact | High-risk judged GREEN/YELLOW (false-allow) | Over-cautious | Latency (CPU) |
|---|---|---|---|---|
| rules+kNN+Qwen3-4B (current union, from docs) | 87% | 3/200 | 28 | seconds per item on VM CPU (docs) |
| verdict-1.4 alone | 58% (152/260) | **35/200** | 35 | median 115 ms, about 111 input tokens |
| laya-1.0 alone | 32% (84/260) | **143/200** | 13 | median 340 ms |
| union rules+kNN+verdict | 78% | 3/200 | 51 | |
| union all four | 78% | 2/200 | 53 | |

Median model confidence was 0.08 (Verdict) and 0.02 (Laya), i.e. close to uniform: they are not confident on this task. Prediction distributions: Verdict called 154 of 260 RED; Laya called 129 YELLOW and only 5 DENY. These are zero-shot results on out-of-distribution labels, exactly what the Laya card warns about.

### 5.4 Fine-tuning feasibility

- **Laya:** Apache-2.0; repo ships `laya.train` (`TrainConfig`, `finetune(...)`), a Kaggle 2xT4 notebook and an Apple-Silicon/CPU script (build dataset, train with RLCD, calibrate temperatures, evaluate, export). Card says reproduction takes about 4-5 h on Kaggle 2xT4. Requirements stated: Python 3.10+, transformers 5.x, torch 2.14, huggingface_hub 1.x (the project's `openjev` venv already has torch 2.14.1+cpu and transformers 5.19.0). Training on this laptop's CPU would be slow for a 421M encoder (UNVERIFIED, likely many hours); Kaggle/Colab are parked by project decision D11, so training would be CPU-only or on a licensed GPU lane.
- **Verdict:** Apache-2.0; ModernBERT-base + GLiClass (Knowledgator, training code public); the repo README does not document the training procedure or data format (UNVERIFIED), so Laya is the more tractable of the two.
- **Data constraint:** the project has 260 + 156 + 88 + 200 labelled risk items from several authors. That is a plausible fine-tune set only if a held-out fresh set is reserved, otherwise the same "narrow set luck" documented in D1 repeats.
- **Recommendation:** do not adopt either as a gate (docs already say natural-language classifiers cannot be the gate). If a fine-tune is attempted, evaluate on a fresh author's set and require false-allow at or below the current union's 1.5% before it replaces the 4B LLM in the union; a cheaper first step is the already-working kNN embedding + logistic head on the `bge-small` GGUF.

---

## 6. Licence matrix (code vs weights)

| Component | Code | Weights / data | Red flag? |
|---|---|---|---|
| whisper.cpp / Whisper | MIT | MIT (large-v3 card tagged Apache-2.0) | no |
| sherpa-onnx | Apache-2.0 (TTS paths link GPL-3.0 espeak-ng) | per model | espeak-ng GPL-3.0 if redistributed |
| IndicConformer | MIT (AI4Bharat NeMo fork) | MIT (gated download) | training-data licences not stated on cards (UNVERIFIED) |
| vasista22 Whisper fine-tunes | n/a | Apache-2.0 tag | trained on ULCA/Shrutilipi/GramVaani/Fleurs etc.; data licences UNVERIFIED |
| Qwen3-ASR, Qwen3-TTS, Qwen3/3.5 | Apache-2.0 | Apache-2.0 | no |
| Omnilingual ASR | Apache-2.0 | Apache-2.0 (dataset CC-BY-4.0) | no |
| Parakeet TDT 0.6B v3 | NeMo (Apache-2.0, UNVERIFIED) | **CC-BY-4.0** (attribution required; ggml-org GGUF tagged MIT) | attribution |
| Moonshine | MIT | MIT, **non-commercial Community Licence for legacy ar/ja/ko/zh/es/uk/vi** | only non-English legacy |
| Vosk | Apache-2.0 | Apache-2.0 | no |
| faster-whisper | MIT | Whisper | no |
| silero-vad | MIT | MIT; README badge text mentions CC BY-NC 4.0 | **inconsistent**, re-check LICENSE before shipping |
| openWakeWord | Apache-2.0 | **CC BY-NC-SA 4.0 pretrained** (non-commercial, share-alike) | **yes** |
| Kokoro / kokoro-onnx | Apache-2.0 / MIT | Apache-2.0 (training data incl. two CC BY sets) | espeak-ng GPL-3.0 dependency |
| Piper (piper1-gpl) | **GPL-3.0-or-later** | per voice: Blizzard-Lessac custom, CC BY-NC-SA 4.0 (ryan, pratham, priyamvada), IITM custom (rohan, maya), CC-BY-4.0 (padmavathi, venkatesh) | **yes** (runtime GPL; several NC voices) |
| Indic Parler-TTS | Apache-2.0 | Apache-2.0; datasets include GLOBE "CC V1" | check dataset terms |
| IndicF5 | MIT tag | MIT tag, gated, voice-cloning restrictions | consent/ref-audio terms |
| Indic-Speak | custom | **Indic Open Model License v1.0** + Llama-3.2 base | custom terms |
| MMS-TTS | | **CC-BY-NC-4.0** | non-commercial |
| Gemma 3 / 3n / FunctionGemma | | Gemma Terms of Use (gated) | custom terms |
| Gemma 4 | | Apache-2.0 tag (card also links "Gemma 4 license") | confirm |
| Llama 3.2 | | Llama 3.2 Community Licence (gated) | custom terms |
| Phi-4-mini, SmolLM3, Granite 4.x | | MIT / Apache-2.0 / Apache-2.0 | no |
| LFM2.5 | | LFM Open Licence v1.0 (custom) | custom terms |
| xLAM-2, Hammer2.1 | | CC-BY-NC-4.0 / qwen-research | non-commercial |
| llama.cpp, Ollama, ONNX Runtime | MIT | n/a | no |
| LM Studio | proprietary | n/a | freeware terms |
| LAYA, Verdict, OpenJev | Apache-2.0 | Apache-2.0 (ModernBERT Apache-2.0) | no |
| TypeSafe Jev | n/a | hosted, not stated | API terms only |

The user's memory notes say outputs go to a personal assistant on a personal laptop, so NC licences are not a blocker today; they matter if JARVIS is ever published or sold.

---

## 7. Measured vs published (summary)

| Claim | Number | Status |
|---|---|---|
| whisper.cpp small-q5_1 `-ac 640`, 11 s clip, i9-9980HK 8 threads | 1.9 s total (RTF 0.18) | **measured here** |
| whisper.cpp large-v3-turbo-q5_0, same | 9.8 s with `-ac`, 29.6 s without (beam 1) | **measured here** (noisy) |
| IndicConformer int8 hi/te RTF | 0.036-0.051 | **measured here** (clean synthetic audio) |
| Parakeet v3 int8 RTF | 0.11-0.14 | **measured here** |
| Kokoro fp32 / int8 RTF | 0.48-0.62 / 1.73-1.99 | **measured here** |
| Piper RTF | 0.03-0.06 | **measured here** |
| Qwen3-1.7B / 4B / 3.5-0.8B tg Vulkan | 87.6 / 22.5 / 119.4 t/s | **measured here** |
| Qwen3-1.7B / 4B tg CPU | 20.5 / 9.3 t/s | **measured here** |
| Qwen3-4B "35 tok/s" in server, 93% on risk Test 2 | | project-measured |
| Whisper FLEURS WER Hindi 21.5 (large-v2), Telugu about 99 | | published (paper), not measured |
| IndicConformer mean WER hi 13.8 / te 26.4 | | published (arXiv 2608.08235), not measured |
| vasista22 FLEURS WER: hindi-small 9.02, telugu-small 11.59 | | published (self-reported model cards), not measured |
| Qwen3-ASR FLEURS en 4.39/3.35, multilingual 7.57/4.90 | | published |
| BFCL V4 figures (section 2.3) | | published (2026-04-12 snapshot) |
| Qwen3.5-4B BFCL-V4 50.3 | | published (vendor card), not on leaderboard snapshot |
| silero-vad <1 ms per chunk | | published |
| openWakeWord FA/FR on hey_jarvis | none published | **UNVERIFIED / not available** |
| Whisper WER on Hindi/Telugu with real microphone audio on this laptop | | **not measured** (no real recordings; no FLEURS clips downloaded) |

---

## 8. Recommended minimal stack for THIS hardware

All speech on CPU (RAM is plentiful, 8 physical cores; the GPU is reserved for the LLM, which already takes 3.6 GB of 4 GB with a 4B model).

| Role | First choice | Fallback | Integration surface |
|---|---|---|---|
| Audio capture + push-to-talk | `sounddevice` (PortAudio) + a hotkey; 16 kHz mono PCM | `pyaudio` | Python in the node/kernel process (no ffmpeg needed) |
| VAD | Silero VAD v6 through sherpa-onnx `VoiceActivityDetector` | whisper.cpp built-in `--vad` with `ggml-silero-v6.2.0.bin` (885 KB) | Python / HTTP flag |
| STT English | **Parakeet TDT 0.6B v3 int8** (465 MB, RTF 0.11-0.14, punctuation) | **whisper.cpp small-q5_1 + `-ac`** (190 MB, RTF 0.18); Moonshine base-en for very short commands once the 11 s failure is understood | sherpa `OfflineRecognizer.from_transducer(..., model_type="nemo_transducer")`; or `whisper-server` `POST /inference` on a free port |
| STT Hindi + Telugu | **IndicConformer int8 CTC per language** via sherpa `from_nemo_ctc` (190 MB each, RTF 0.04-0.05) | Hindi: Qwen3-ASR 0.6B int8 (sherpa `from_qwen3_asr`) or whisper.cpp turbo with `-ac` (-l hi); Telugu: Omnilingual 300M CTC int8 (279 MB, per-language accuracy UNVERIFIED) or a converted vasista22 Whisper fine-tune. Whisper stock is not acceptable for Telugu | same sherpa recogniser API |
| Language routing | explicit setting or per-hotkey (e.g. EN/HI/TE), because auto-LID quality on Indic and code-mixed speech is UNVERIFIED | whisper tiny LID in sherpa (UNVERIFIED) | |
| TTS English | **Kokoro v1.0 fp32** via sherpa `OfflineTts.generate(text, sid, speed)` (RTF about 0.5; stream by sentence) | Piper en_US-lessac-medium (RTF 0.05, GPL runtime) | sherpa Python/C API |
| TTS Hindi / Telugu | **Piper** hi_IN-rohan (or priyamvada for NC-ok personal use) and te_IN-venkatesh/padmavathi (CC-BY-4.0), run as a **separate process** to isolate GPL | Hindi: Kokoro hf_alpha (grade C). Telugu fallback: Indic Parler-TTS 0.9B (Apache-2.0, speed UNVERIFIED) | `piper` python API or CLI/HTTP server; sherpa for Kokoro |
| Wake word | **openWakeWord `hey_jarvis` ONNX** (personal use; threshold tune on this mic; add Silero gate) | sherpa-onnx KWS (open vocabulary, English gigaspeech model) or custom-trained openWakeWord | `openwakeword.Model(...).predict(frame)` |
| Small decision model | existing **kNN on bge-small + logistic head, rules floor**, with Qwen3-4B for low confidence (union rule as in docs/03_DECISIONS.md); Verdict/Laya remain advisory experiments | Qwen3-1.7B constrained JSON | llama-server `/v1/chat/completions` with JSON schema; `/v1/systemone` only if OpenJev encoders are run |
| Small planner LLM | **Qwen3-4B Q4_K_M now (installed)**; trial **Qwen3-4B-Instruct-2507 Q4_K_M** (2.50 GB, non-thinking, best published BFCL non-live among permissive <=4B) and **Qwen3.5-4B Q4_K_M** (2.74 GB, vendor BFCL-V4 50.3) as drop-ins, each behind a local tool-call test | Qwen3-1.7B (installed, 87.6 tok/s) | llama-server OpenAI-compatible API, `--jinja` for tool calls, `-fa off`, `-c 4096` (must cap context for Qwen3.5) |
| Runtime | **llama.cpp Vulkan b11461 server (already installed)** + sherpa-onnx wheels + whisper.cpp prebuilt x64 CPU zip | llama.cpp CPU build; Ollama / LM Studio only after a test on the 5500M | `start-local-llm.ps1` (port 8080); keep whisper-server off 8080 |

Memory picture (approximate, UNVERIFIED sum): LLM weights 2.5 GB VRAM + KV; Parakeet about 0.7 GB RAM; IndicConformer 0.2 GB each; Kokoro fp32 about 0.4 GB; Piper 0.1 GB each. Comfortable within 32 GB.

Install footprint of what I used: whisper.cpp bin 31 MB (+ models 871 MB), sherpa venv 219 MB, IndicConformer hi+te 377 MB, Kokoro fp32 384 MB, Parakeet+Moonshine 776 MB, Piper voices 121 MB. All under `D:\jarvis\research\` (section 10).

---

## 9. Risks

1. **Telugu is the weakest link everywhere.** Only IndicConformer (and Omnilingual, unverified) are credible; published Telugu mean WER is still 26% (versus 14% Hindi). Telugu ASR into an LLM that then plans actions needs confirmation echo ("I heard: ...") before any side effect.
2. **No real-speech accuracy was measured here.** All Hindi/Telugu timings used clean TTS loopback; accuracy on microphone speech, accents, code-mixing (Hinglish) and noise is unverified. A 30-clip recorded test per language is the first follow-up.
3. **CPU contention and thermal throttling:** the laptop's clock read 1.1 GHz and background load changed turbo timings 2x. A voice session while the LLM, browser and VS Code run will be slower than the idle figures above.
4. **Kokoro int8 trap:** the smaller file is 3x slower on this CPU; also Hindi grade C and no Telugu.
5. **Licence traps:** openWakeWord pretrained models (NC-SA), several Piper voices (NC-SA / IITM), MMS (NC), xLAM-2 and Hammer (NC), Gemma/Llama/LFM custom terms, Piper runtime GPL-3.0 and espeak-ng GPL-3.0 inside sherpa TTS, Parakeet CC-BY attribution. Fine for private use; blocks distribution.
6. **Gated downloads** (IndicConformer, Indic Parler-TTS, IndicF5, Gemma) need a Hugging Face login and acceptance; I did not use any account. The sherpa community mirror avoids the login for IndicConformer but is a third-party re-upload (supply-chain trust: pin its commit sha 9721eb71eea1 and hash the files).
7. **Flash attention bug on the Radeon Pro 5500M driver** (project note) will apply to any llama.cpp-derived runtime; Ollama/LM Studio behaviour on this GPU unverified. Large-context models (Qwen3.5 262K) crash on Vulkan unless `-c` is set.
8. **Single GPU, 4 GB:** a 4B model leaves about 0.4 GB; never put whisper/TTS on the GPU, and don't run two LLMs at once.
9. **Project churn:** openWakeWord has no release since 2024-02 (one commit in Dec 2025); piper moved to a GPL fork with few maintainers (README asks for maintainers); sherpa-onnx moves fast and a Moonshine v2 model failed on an 11 s clip in 1.13.8: pin versions.
10. **Wake word without echo cancellation:** TTS output through speakers will raise false-rejects and may self-trigger; use push-to-talk while speaking or a headset.
11. **LAYA/OpenJev drift:** Laya moved 0.3.6 to 0.4.2 in a week; OpenJev pins `laya==0.3.6`. TypeSafe's blog describes Jev as an early-access hosted API with no stated weights licence, so "Jev-compatible" does not mean Jev-quality (Verdict 48% vs Jev 91% on TypeSafe's own public eval, per the Verdict README).

---

## 10. What I ran (for reproducibility) and footprint

No repo was cloned. Only release artifacts and model files were downloaded, each into its own new directory under `D:\jarvis\research\`; I executed the official whisper.cpp release binaries, the PyPI wheels (sherpa-onnx 1.13.8, piper-tts 1.8.0, onnxruntime 1.31.0, numpy 2.5.4, soundfile 0.14.0 in a fresh venv) and the project's existing llama.cpp b11461 binaries on existing model files. No script from any repository was executed. My own scripts live in the session scratchpad, not in those directories.

| Directory (all new) | Content | Size |
|---|---|---|
| `whisper-cpp-bin-b5454` | `whisper-bin-x64.zip` + extracted `Release/` (SHA-256 6ba69e34...816ccb) | 31 MB |
| `whisper-ggml-models` | ggml-base.bin, ggml-small-q5_1.bin, ggml-large-v3-turbo-q5_0.bin (all from HF `ggerganov/whisper.cpp`), ggml-silero-v6.2.0.bin (`ggml-org/whisper-vad`) | 871 MB |
| `audio-samples` | jfk.wav (whisper.cpp samples), `synth/` (Kokoro/Piper outputs and whisper transcripts) | 2.3 MB |
| `voice-venv` | Python 3.12 venv with the wheels above | 219 MB |
| `indicconformer-int8` | hi/te int8 ONNX + shared tokens.txt + README from `parismitaglobalsolutions/indicconformer-sherpa-onnx` (sha 9721eb71eea1) | 377 MB |
| `tts-models-kokoro-fp32` | sherpa `kokoro-multi-lang-v1_0` (tarball SHA not recorded; deleted after extraction) | 384 MB |
| `piper-voices` | hi_IN-rohan-medium, te_IN-venkatesh-medium (.onnx + .json) from `rhasspy/piper-voices` sha c10ece1aade4 | 121 MB |
| `asr-models-sherpa` | `sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8` (tarball SHA-256 5793d0fd...16bf), `sherpa-onnx-moonshine-base-en-quantized-2026-02-27` (SHA-256 43232c1d...e891) | 776 MB |
| (deleted after use) | Kokoro int8 (182 MB), Qwen3.5-0.8B Q4_K_M (508 MB; unsloth GGUF sha not recorded), Piper en_US-lessac and te_IN-padmavathi | 0 |

Total retained: about 2.8 GB (under the 3 GB cap). Free space on D: 44.6 GiB before, 41.3 GiB after. No secret files were read; `D:\jarvis-secrets` untouched; no Oracle/Telegram/remote server contact; no hq file changed except this report. `models/gguf` and `tools/llama.cpp` were only read (and executed for llama-bench / one completion); one completion used `-c 2048` on a model in `research/`.

Not done and why: whisper.cpp Vulkan build (no cmake, MSVC, Vulkan SDK; would exceed the "no large installs" rule), Qwen3-ASR/Omnilingual/Qwen3.5-4B/Indic Parler-TTS runs (download sizes), real Hindi/Telugu microphone WER, openWakeWord install and false-accept test, Ollama/LM Studio on the 5500M, any WSL2 test.

---

## 11. Sources (all fetched 2026-10-11 unless dated otherwise)

- whisper.cpp: https://github.com/ggml-org/whisper.cpp (README raw, releases b5454 and v1.9.5, API asset list); https://huggingface.co/ggerganov/whisper.cpp ; https://huggingface.co/ggml-org/whisper-vad ; https://huggingface.co/ggml-org/parakeet-GGUF
- Whisper paper (FLEURS Table 13, Appendix E data hours): https://arxiv.org/abs/2212.04356 ; https://github.com/openai/whisper (README, model table) ; https://huggingface.co/openai/whisper-large-v3 , https://huggingface.co/openai/whisper-large-v3-turbo
- sherpa-onnx: https://github.com/k2-fsa/sherpa-onnx (README, releases v1.13.8, `asr-models` and `tts-models` release assets via API), https://pypi.org/project/sherpa-onnx/ , https://k2-fsa.github.io/sherpa/onnx/kws/index.html , https://github.com/k2-fsa/sherpa-onnx/blob/master/cmake/espeak-ng-for-piper.cmake
- IndicConformer: https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual , https://github.com/AI4Bharat/IndicConformerASR , https://huggingface.co/parismitaglobalsolutions/indicconformer-sherpa-onnx , https://huggingface.co/OpenVoiceOS/ai4bharat-indicconformer-te-onnx , SraVaani 1.0 https://arxiv.org/html/2608.08235v1 , Factors paper https://arxiv.org/abs/2606.09335 , Vistaar https://github.com/AI4Bharat/vistaar (snippet only)
- vasista22 fine-tunes: https://huggingface.co/vasista22/whisper-hindi-small , https://huggingface.co/vasista22/whisper-telugu-small
- Qwen3-ASR: https://github.com/QwenLM/Qwen3-ASR , https://huggingface.co/Qwen/Qwen3-ASR-0.6B (arXiv 2601.21337); Qwen3-TTS: https://github.com/QwenLM/Qwen3-TTS (arXiv 2601.15621)
- Omnilingual ASR: https://github.com/facebookresearch/omnilingual-asr , https://huggingface.co/facebook/omniASR-CTC-300M
- Parakeet: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 ; Moonshine: https://github.com/moonshine-ai/moonshine (README, LICENSE) ; Vosk: https://alphacephei.com/vosk/models ; faster-whisper: https://github.com/SYSTRAN/faster-whisper ; silero-vad: https://github.com/snakers4/silero-vad
- openWakeWord: https://github.com/dscripka/openWakeWord (README, docs/models/hey_jarvis.md) , https://huggingface.co/davidscripka/openwakeword
- Kokoro: https://huggingface.co/hexgrad/Kokoro-82M (+ VOICES.md), https://github.com/hexgrad/kokoro , https://github.com/thewh1teagle/kokoro-onnx (release model-files-v1.1)
- Piper: https://github.com/OHF-Voice/piper1-gpl , https://github.com/rhasspy/piper , https://huggingface.co/rhasspy/piper-voices (MODEL_CARDs); espeak-ng licence: https://github.com/espeak-ng/espeak-ng/blob/master/COPYING
- Indic TTS: https://huggingface.co/ai4bharat/indic-parler-tts , https://huggingface.co/ai4bharat/IndicF5 , https://huggingface.co/bodhan-ai/indic-speak , https://huggingface.co/kenpath/svara-tts-v1 , https://huggingface.co/facebook/mms-tts-hin
- LLMs: HF model cards for Qwen/Qwen3-4B-Instruct-2507, Qwen/Qwen3.5-4B, google/gemma-4-E4B-it, google/gemma-3-4b-it, meta-llama/Llama-3.2-3B-Instruct, microsoft/Phi-4-mini-instruct, HuggingFaceTB/SmolLM3-3B, ibm-granite/granite-4.1-3b, LiquidAI/LFM2.5-2.6B, Salesforce/xLAM-2-3b-fc-r, MadeAgents/Hammer2.1-3b, google/functiongemma-270m-it; GGUF sizes from HF tree API of unsloth/*-GGUF, ggml-org/gemma-4-E4B-it-GGUF, LiquidAI/LFM2.5-*-GGUF, bartowski/Llama-3.2-3B-Instruct-GGUF
- BFCL V4: https://gorilla.cs.berkeley.edu/leaderboard.html and https://gorilla.cs.berkeley.edu/data_overall.csv (snapshot "2026-04-12")
- Runtimes: https://github.com/ggml-org/llama.cpp , https://github.com/ollama/ollama , https://docs.ollama.com/gpu , https://www.lmstudio.ai/blog/free-for-work , https://github.com/microsoft/onnxruntime-genai
- LAYA/OpenJev/TypeSafe: https://github.com/razorback16/openjev , https://github.com/NandhaKishorM/laya , https://huggingface.co/convaiinnovations/laya-typed-decisions , https://github.com/Heman10x-NGU/Verdict-open-jev , https://huggingface.co/heman10x/rlcd-modernbert-151m , https://typesafe.ai/blog/introducing-system-one-models-and-jev , https://pypi.org/project/typesafe-sdk/ , https://huggingface.co/nvidia/diffusiongemma-26B-A4B-it-NVFP4
- Local evidence (read-only): `D:\jarvis\hq\bench\openjev_risk.py`, `bench\data\openjev_results.json`, `risk_fresh23.jsonl`, `D:\jarvis\hq\docs\03_DECISIONS.md` (D1 sections), `D:\jarvis\hq\scripts\start-local-llm.ps1`, `D:\jarvis-apps\openjev` (README, pyproject.toml, CHANGELOG.md, git HEAD 75f22b6)
