# Integrating the voice stack, desktop shell and model files into JARVIS as one product (October 2026)

Scope and method. Research only; nothing in jarvis-hq was changed. Evidence was gathered on 2026-10-22 by:
- installing `sherpa-onnx==1.13.8` (the version the repo pins) in a scratch venv and reading its Python API directly (class names, constructor signatures and docstrings);
- sparse-cloning `k2-fsa/sherpa-onnx` master (commit 51ec64f7, 2026-10-22) and `dscripka/openWakeWord`, and fully cloning `Project-N-E-K-O/N.E.K.O.-Tauri` (HEAD b7b4fd7, 2026-07-29, the same commit the jarvis-hq submodule pins);
- querying PyPI JSON and the Hugging Face model API for versions and licences;
- reading `jarvis/tools/fetch.py` and `jarvis/tools/jarvis.lock.json` on branch `remediation-2026-10-18` through the GitHub connector.

Labels: "[direct]" means I read it from code, a wheel or an API response myself. "[inference]" means my own reasoning, not a sourced fact. "[unverified]" means it comes from general knowledge and was not re-checked in this session.

---

## Q1. Can the whole voice pipeline run in-process through the sherpa-onnx Python API, and which model types and classes does sherpa-onnx support in 2026?

### Takeaway
Yes, with two exceptions: echo cancellation and Smart Turn. sherpa-onnx 1.13.8 (Apache-2.0, Windows wheels for cp310–cp312+, bundled onnxruntime 1.28.2) covers every other stage in one process: KWS (`KeywordSpotter`), VAD (`VoiceActivityDetector` with Silero or TEN VAD), noise suppression (`OnlineSpeechDenoiser`), offline ASR including Parakeet (`OfflineRecognizer.from_transducer(model_type="nemo_transducer")`) and NeMo CTC models, streaming ASR (`OnlineRecognizer`), spoken-language ID, speaker embeddings, and TTS. TTS covers Kokoro, VITS/Piper, Matcha, Kitten, ZipVoice and, new since early 2026, **Pocket TTS** and **Supertonic 3** (`OfflineTts` with per-family `OfflineTts*ModelConfig`). AEC (WebRTC AEC3) and Smart Turn (plain onnxruntime) have to sit next to it in the same Python process.

### Cited Findings
- PyPI `sherpa-onnx` latest is **1.13.8**, uploaded 2026-09-10, licence "Apache licensed". Windows wheels exist for cp310/cp311/cp312 win_amd64 (plus win32 and win_arm64) — [PyPI sherpa-onnx](https://pypi.org/pypi/sherpa-onnx/json) [direct]
- Public classes in the 1.13.8 wheel [direct, `dir(sherpa_onnx)`]: `KeywordSpotter`, `VoiceActivityDetector`, `VadModelConfig`, `SileroVadModelConfig`, `TenVadModelConfig`, `CircularBuffer`, `OfflineRecognizer`, `OnlineRecognizer`, `OfflinePunctuation`/`OnlinePunctuation`, `OfflineSpeechDenoiser`/`OnlineSpeechDenoiser` (GTCRN and DPDFNet configs), `SpeakerEmbeddingExtractor`/`SpeakerEmbeddingManager`, `SpokenLanguageIdentification` (Whisper-based), `OfflineSpeakerDiarization`, `AudioTagging`, `OfflineTts` with `OfflineTtsVitsModelConfig`, `OfflineTtsMatchaModelConfig`, `OfflineTtsKokoroModelConfig`, `OfflineTtsKittenModelConfig`, `OfflineTtsZipvoiceModelConfig`, `OfflineTtsPocketModelConfig`, `OfflineTtsSupertonicModelConfig`, and `GenerationConfig`. `onnxruntime_version` reports 1.28.2 — [sherpa-onnx 1.13.8 wheel](https://pypi.org/project/sherpa-onnx/1.13.8/) [direct]
- Offline ASR factories in 1.13.8 [direct]:
  - `OfflineRecognizer.from_transducer`, `from_nemo_ctc`, `from_nemo_canary`, `from_whisper`, `from_moonshine`, `from_moonshine_v2`
  - `from_qwen3_asr`, `from_omnilingual_asr_ctc`, `from_cohere_transcribe`, `from_sense_voice`, `from_paraformer`, `from_fire_red_asr(_ctc)`, `from_funasr_nano`, `from_dolphin_ctc`, `from_medasr_ctc`, `from_telespeech_ctc`, `from_wenet_ctc`, `from_zipformer_ctc`, `from_tdnn_ctc`

  Streaming factories: `OnlineRecognizer.from_transducer`, `from_nemo_ctc`, `from_paraformer`, `from_t_one_ctc`, `from_wenet_ctc`, `from_zipformer2_ctc`. Source: [sherpa-onnx wheel](https://pypi.org/project/sherpa-onnx/1.13.8/) [direct]
- Parakeet is loaded with `OfflineRecognizer.from_transducer(..., model_type="nemo_transducer")`. The signature also takes `provider="cpu"`, `hotwords_file`, `lm`, `hr_lexicon` (homophone replacer) and `blank_penalty` — [offline-nemo-parakeet-decode-file.py](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/offline-nemo-parakeet-decode-file.py) [direct]
- `KeywordSpotter(tokens, encoder, decoder, joiner, keywords_file, num_threads=2, sample_rate=16000, feature_dim=80, max_active_paths=4, keywords_score=1.0, keywords_threshold=0.25, num_trailing_blanks=1, provider='cpu', device=0)` — [sherpa-onnx wheel](https://pypi.org/project/sherpa-onnx/1.13.8/) [direct]
- `SileroVadModelConfig(model, threshold=0.5, min_silence_duration=0.5, min_speech_duration=0.25, window_size=512, max_speech_duration=20)` [direct]
- TTS config fields [direct]:
  - Kokoro: `model, voices, tokens, lexicon, data_dir, dict_dir, length_scale, lang`
  - VITS/Piper: `model, lexicon, tokens, data_dir, dict_dir, noise_scale=0.667, noise_scale_w=0.8, length_scale=1.0`
  - Pocket: `lm_flow, lm_main, encoder, decoder, text_conditioner, vocab_json, token_scores_json, voice_embedding_cache_capacity=50`
  - Supertonic: `duration_predictor, text_encoder, vector_estimator, vocoder, tts_json, unicode_indexer, voice_style`
  - ZipVoice: `tokens, encoder, decoder, vocoder, data_dir, lexicon, feat_scale, t_shift, target_rms, guidance_scale`
  - Source: [sherpa-onnx wheel](https://pypi.org/project/sherpa-onnx/1.13.8/)
- `OfflineTts.generate` has three overloads:
  - `(text, sid, speed, callback)`
  - `(text, config: GenerationConfig, callback)`, where `GenerationConfig` has `reference_audio, reference_sample_rate, reference_text, num_steps, sid, speed, silence_scale, extra`
  - `(text, prompt_text, prompt_samples, sample_rate, speed, num_steps, callback)` for zero-shot cloning

  The callback receives `(samples, progress)` chunks during generation. "Return a non-zero value to stop generation early" is the hook for streaming playback and barge-in cancellation. Source: [sherpa-onnx wheel docstring](https://pypi.org/project/sherpa-onnx/1.13.8/) [direct]
- Pocket TTS in sherpa-onnx: model archive `sherpa-onnx-pocket-tts-int8-2026-01-26.tar.bz2`. Voice cloning is done by setting `gen_config.reference_audio` / `reference_sample_rate`, and "PocketTTS does **not** require reference text" — [pocket-tts.py](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/pocket-tts.py); [sherpa docs: Pocket](https://k2-fsa.github.io/sherpa/onnx/tts/pocket.html). The changelog records "Add C++ runtime and Python support PocketTTS for streaming voice cloning on CPU (#3083)" — [CHANGELOG](https://github.com/k2-fsa/sherpa-onnx/blob/master/CHANGELOG.md)
- Supertonic 3 in sherpa-onnx: archive `sherpa-onnx-supertonic-3-tts-int8-2026-05-11.tar.bz2`, with `voice_style=".../voice.bin"`. The example says "This single model supports 31 languages" — [supertonic-tts.py](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/supertonic-tts.py); changelog "Support Supertonic 3 TTS (#3605)" — [CHANGELOG](https://github.com/k2-fsa/sherpa-onnx/blob/master/CHANGELOG.md)
- English KWS models: `sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01` ("trained on GigaSpeech XL subset (10000 hours), it supports only English") and `sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20`. Keywords are converted with `sherpa-onnx-cli text2token` (BPE for GigaSpeech, `phone+ppinyin` + `en.phone` for zh-en). No licence is stated for these weights — [sherpa KWS pretrained models](https://k2-fsa.github.io/sherpa/onnx/kws/pretrained_models/index.html)
- The DirectML build option exists in the sherpa-onnx CMake (`SHERPA_ONNX_ENABLE_DIRECTML`, `cmake/onnxruntime-win-x64-directml.cmake`) — [sherpa-onnx CMakeLists](https://github.com/k2-fsa/sherpa-onnx/blob/master/CMakeLists.txt) [direct]
- What the repo uses today, from the lock:
  - `parakeet-tdt-0.6b-v3-int8-sherpa` archive, 487.2 MB
  - IndicConformer int8 `hi` and `te`, 197.6 MB each, from `parismitaglobalsolutions/indicconformer-sherpa-onnx`
  - `kokoro-multi-lang-v1_0` archive, 349.9 MB
  - Piper `hi_IN-rohan-medium` and `te_IN-venkatesh-medium`, about 63 MB each
  - `silero_vad.onnx` v6.2.3, 2.3 MB, unpinned
  - `sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20`, 32.9 MB, optional

  The KWS runtime is a **locally built wheel `1.13.8+neko.kws2` from commit 11afbd00 with two patches**, "no upstream artefact to pin". Source: [jarvis.lock.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/tools/jarvis.lock.json) [direct]

### Inferences
- **One voice engine, one process.** Replace the split between `local_speech_server.py` (48930) and `jarvis/wake` (separate process, talking HTTP to 48930 and 48940) with a single `jarvis/voice/engine.py`. It would load every sherpa-onnx object once and run one audio graph:
  - **Mic thread:** 16 kHz frames, 10 ms. Each frame goes through AEC3 (pywebrtc-audio, with the TTS ring buffer as far-end) and an optional `OnlineSpeechDenoiser`, then fans out to `KeywordSpotter.decode_stream` and `VoiceActivityDetector`.
  - **On speech end:** run Smart Turn (onnxruntime). If the turn is complete, run `OfflineRecognizer` (Parakeet, or the IndicConformer selected by language or by the user's setting).
  - **Text to agent:** the text goes to the N.E.K.O session or main_agent in-process.
  - **Reply:** sentence-chunked `OfflineTts.generate(..., callback=...)` writes into a playback ring buffer. The same buffer is the AEC far-end. A non-zero callback return aborts generation on barge-in.
  - **Port 48930:** keep the existing OpenAI-compatible `/v1/audio/speech` and `/v1/audio/transcriptions` routes as a thin facade over the same engine, so N.E.K.O's realtime session and other callers keep working.
- **Extra in-process capabilities sherpa-onnx gives for free** [inference]:
  - `SpeakerEmbeddingExtractor`/`Manager` for owner-voice verification after a wake. This is a strong defence against "Jarvis" spoken on TV.
  - `SpokenLanguageIdentification` to route between Parakeet and IndicConformer automatically, instead of fixed voices `en|hi|te`.
  - `hotwords_file` / `hr_lexicon` to bias Parakeet towards names the owner uses.
- **Parakeet v3 is offline-only** (transducer, not streaming). Partial transcripts would need a streaming zipformer via `OnlineRecognizer`, or simulated streaming (re-decoding a growing buffer; sherpa-onnx ships `simulate-streaming-*` examples). For a command assistant, VAD-segmented offline decoding at about 0.04–0.1x real time is enough [inference].
- **The custom KWS wheel is the one piece that is not "integrated".** Either upstream the two patches to k2-fsa, or vendor them as a reproducible build script whose output hash goes into the lock (the lock already notes it is unpinned). If wake moves to a different engine (Q2), the stock 1.13.8 wheel suffices.
- **Pin sherpa-onnx at 1.13.8 for now.** Master has moved on (commit 51ec64f7, 2026-10-22). Any upgrade should go through the existing pip lock and bench.
- **Keep ONNX voice inference on CPU.** The 4 GB GPU is fully used by llama.cpp; DirectML would compete with it [inference].

### Gaps
- Whether the PyPI Windows wheel exposes `provider="directml"`, or only `cpu`, was not tested; that needs a Windows run.
- The exact sherpa-onnx loader for the `parismitaglobalsolutions` IndicConformer export (`from_nemo_ctc` vs `from_transducer`) was not confirmed; the lock lists a single `model.int8.onnx` per language plus `tokens.txt`.
- No CPU RTF on the owner's laptop for Pocket TTS or Supertonic 3 via sherpa-onnx.
- Whether sherpa-onnx's pybind calls release the GIL during inference was not verified. This matters for running KWS, ASR and TTS on separate threads.

---

## Q2. Wake word: openWakeWord "hey jarvis" licence, permissive alternatives, and the licence of a custom-trained model

### Takeaway
openWakeWord's pretrained `hey_jarvis` is **CC BY-NC-SA 4.0**. It is usable for the owner's personal assistant but cannot be part of a product meant to stay permissively licensed. Training your own model with the openWakeWord notebook does not escape this cleanly: the notebook's default negative data, the precomputed ACAV100M features, is itself **CC BY-NC-SA 4.0**, and the maintainers say nothing about the licence of models trained on it.

The cleanest permissive paths are:
1. **sherpa-onnx KWS with a "HEY JARVIS" keyword**: no training, but the weights' licence is unstated, and the English model is trained on GigaSpeech, whose access terms are non-commercial.
2. **livekit-wakeword** (Apache-2.0 code, Sept 2026 v0.3.0): train your own `hey_jarvis` with Piper or VoxCPM synthetic positives, plus negatives that you choose with clear licences, then export to ONNX.

### Cited Findings
- openWakeWord: "All of the code in this repository is licensed under the Apache 2.0 license." The pre-trained models are licensed CC BY-NC-SA 4.0 "due to the inclusion of datasets with unknown or restrictive licensing as part of the training data". The README says nothing about the licence of custom-trained models. On Windows "only onnxruntime is installed" — [openWakeWord](https://github.com/dscripka/openWakeWord)
- PyPI `openwakeword` is still at 0.6.0, from 2024-02-11 — [PyPI](https://pypi.org/pypi/openwakeword/json) [direct]
- The openWakeWord automatic training notebook [direct, `notebooks/automatic_model_training.ipynb`]:
  - clones `rhasspy/piper-sample-generator` and downloads `en_US-libritts_r-medium.pt` for synthetic positives ("currently only supports linux systems")
  - downloads `openwakeword_features_ACAV100M_2000_hrs_16bit.npy` as negatives
  - uses AudioSet (`agkphysics/AudioSet`) and FMA (`rudraml/fma`) as background audio
  - uses `davidscripka/MIT_environmental_impulse_responses` for room impulse responses
  - Source: [openWakeWord notebooks](https://github.com/dscripka/openWakeWord/tree/main/notebooks)
- Licences of those datasets per the Hugging Face API [direct]:
  - `davidscripka/openwakeword_features` (the ACAV100M features): **cc-by-nc-sa-4.0** ("intended to be used as general purpose negative data … for training custom openWakeWord models") — [HF dataset](https://huggingface.co/datasets/davidscripka/openwakeword_features)
  - `agkphysics/AudioSet`: cc-by-4.0 — [HF](https://huggingface.co/datasets/agkphysics/AudioSet)
  - `rudraml/fma`: openrail — [HF](https://huggingface.co/datasets/rudraml/fma)
  - `MIT_environmental_impulse_responses`: unknown — [HF](https://huggingface.co/datasets/davidscripka/MIT_environmental_impulse_responses)
- Third-party views of the licence of trained models conflict. One training-pipeline README says the ACAV100M CC-BY-NC-SA applies, while a custom-model card lists ACAV100M features as CC BY 4.0. There is no primary statement from the maintainer — [atlas-voice-training](https://github.com/briankelley/atlas-voice-training); [pantorn/wuwei-hey-wu](https://huggingface.co/pantorn/wuwei-hey-wu)
- livekit-wakeword:
  - "This project is licensed under the Apache License 2.0". PyPI 0.3.0 was uploaded 2026-09-30 as a pure-Python wheel — [livekit-wakeword](https://github.com/livekit/livekit-wakeword); [PyPI](https://pypi.org/pypi/livekit-wakeword/json) [direct]
  - The only pretrained model shipped is `hey_livekit.onnx`; there is no "hey jarvis".
  - Synthetic positives come from Piper (English) or VoxCPM2 (multilingual).
  - Python API: `WakeWordModel.predict()` and the async `WakeWordListener.wait_for_detection()`.
  - Export: `livekit-wakeword export configs/prod.yaml` (ONNX by default).
  - Windows is not mentioned; system dependencies are documented for macOS and Ubuntu only.
  - Vendor benchmark on "hey livekit": FPPH 0.08 vs 8.50 for openWakeWord, recall 86.1% vs 68.6%.
  - Source: [livekit-wakeword](https://github.com/livekit/livekit-wakeword)
- GigaSpeech, the training data of the English sherpa KWS model, has gated access "only for non-commercial research and educational purposes", and SpeechColab does not own the audio copyright — [GigaSpeech terms via HF](https://huggingface.co/datasets/distil-whisper/gigaspeech-l-token-ids/blob/main/README.md); [Loquacious Set paper](https://arxiv.org/pdf/2505.21578)
- The jarvis-hq lock records the zh-en KWS weights as "licence=unknown (upstream states none for these weights)" — [jarvis.lock.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/tools/jarvis.lock.json) [direct]
- Repo state: the wake fired on 67% of test clips. The roadmap plans to record 200 owner clips plus 1 h of room audio and compare sherpa KWS, livekit-wakeword and openWakeWord at a fixed false-alarm rate — [JARVIS_ROADMAP_2026-10-22.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md)

### Inferences
- **For a personal, non-distributed JARVIS, licences are not a blocker.** Ship sherpa KWS "HEY JARVIS" as the default, and treat openWakeWord `hey_jarvis` as an optional download marked "non-commercial" in the model manager (Q8).
- **To keep everything permissive**, train a livekit-wakeword `hey_jarvis`:
  - Positives: Piper or Kokoro synthetic speech plus the owner's 200 clips (the owner owns those).
  - Negatives and background: CC-BY or self-recorded audio. AudioSet (CC-BY-4.0, attribution needed), the owner's room/TV recordings, and permissive speech corpora. Do not use the ACAV100M features.
  - Run training on the Linux agent box or in WSL, not on Windows: the Piper sample generator is Linux-only and LiveKit documents only Linux/macOS.
  - The resulting ONNX model is then the owner's own work, licensed as they choose. Upstream TTS voice licences still need checking: `en_US-libritts_r-medium` is trained on LibriTTS-R, which is CC-BY-4.0 [unverified].
- **Defence in depth against "Jarvis" in films and TV** (each step is cheap):
  1. Wake engine
  2. sherpa-onnx speaker-embedding check against the owner's enrolled voice
  3. Transcript re-check for "jarvis" in Parakeet's output
  4. AEC, so JARVIS's own voice never wakes it
- **openWakeWord itself is dormant** (no release since Feb 2024). Avoid adding it as a runtime dependency. If it is used, use only its ONNX model through onnxruntime (its feature pipeline needs the melspectrogram + embedding ONNX files, which are Apache-2.0 per the README) [inference].

### Gaps
- No primary statement from the openWakeWord maintainer on the licence of custom-trained models.
- The licence of sherpa-onnx's KWS weights is unstated upstream.
- livekit-wakeword on Windows is untested.
- The licence of livekit-wakeword's own pretrained `hey_livekit.onnx` was not stated.

---

## Q3. Pipecat Smart Turn v3 used standalone, without Pipecat

### Takeaway
Smart Turn runs standalone with only `numpy`, `onnxruntime` and Whisper's log-mel feature extractor (currently imported from `transformers`). The model is BSD-2-Clause, about 8 MB int8 for CPU, and takes up to 8 s of 16 kHz mono audio. Hindi is supported and Telugu is not listed. To keep the product light, reimplement the Whisper feature extractor in numpy (or use one already shipped) rather than pulling in `transformers`.

### Cited Findings
- "This is a truly open model (BSD 2-clause license)." "The datasets, training script, and model weights are all open source." Version v3.2. "Smart Turn takes 16kHz mono PCM audio as input. Up to 8 seconds of audio is supported." The CPU build is int8, about 8 MB; the GPU build is fp32, about 32 MB. It runs "in as little as 10ms on some CPUs". There are 23 languages, including Hindi and Marathi; Telugu is not listed — [pipecat-ai/smart-turn](https://github.com/pipecat-ai/smart-turn); HF licence `bsd-2-clause` — [pipecat-ai/smart-turn-v3](https://huggingface.co/pipecat-ai/smart-turn-v3) [direct API]
- `requirements_inference.txt` lists `numpy`, `onnxruntime`, `transformers`. `inference.py`:
  - builds an `ort.InferenceSession` with `ORT_SEQUENTIAL`, `inter_op_num_threads=1`, `ORT_ENABLE_ALL`
  - computes features with `WhisperFeatureExtractor(chunk_length=8)` (`padding="max_length"`, `max_length=8*16000`, `do_normalize=True`)
  - feeds `{"input_features": ...}`
  - returns `prediction = 1 if probability > 0.5`
  - hard-codes `ONNX_MODEL_PATH = "smart-turn-v3.1.onnx"`
  - Source: [inference.py](https://github.com/pipecat-ai/smart-turn/blob/main/inference.py) [direct]
- Pipecat's default turn-stop strategy is `LocalSmartTurnAnalyzerV3`, bundled CPU model — [Pipecat reference](https://reference-server.pipecat.ai/en/stable/api/pipecat.audio.turn.smart_turn.local_smart_turn_v3.html)

### Inferences
- Integration (`jarvis/voice/turn.py`, about 80 lines):
  - Silero VAD flags about 200 ms of silence.
  - Take the last ≤8 s of the utterance and left-pad.
  - Compute the Whisper log-mel spectrogram in numpy (port `transformers`' `WhisperFeatureExtractor._np_extract_fbank_features`; the mel filters can be precomputed into a `.npy`).
  - Run the ONNX model.
  - If p > 0.5, end the turn. Otherwise keep listening up to a 1.5–3 s fallback.
  - Validate the numpy features against `transformers` on 100 clips (max abs diff), then drop the dependency.
- Pin the exact ONNX filename and sha256 in `jarvis.lock.json`. The repo's `inference.py` still names v3.1 while the README says v3.2, so lock the specific file you test.
- For Telugu, keep Smart Turn as advisory with a longer silence fallback, and measure its false "complete" rate on the owner's Telugu clips (Q9).

### Gaps
- The exact v3.2 ONNX filenames, sizes and sha256 values on HF were not read.
- There is no CPU latency on the owner's AMD laptop.

---

## Q4. WebRTC AEC3 Python bindings: names, licences, Windows wheels

### Takeaway
Use **pywebrtc-audio 0.2.0** (Apache-2.0, published 2026-09-03). It has Windows x86_64 wheels for Python 3.10–3.14 and an `EchoCanceller(sample_rate, num_channels, stream_delay_ms)` class with `process(near, far)`, plus NS, AGC2 and VAD. **aec-audio-processing 1.0.1** (BSD-3-Clause, Sept 2025, Windows wheels only, cp311–cp313) is the fallback. The 2019 `webrtc-audio-processing` package has no Windows wheels and should be avoided.

### Cited Findings
- pywebrtc-audio:
  - Licence "Apache-2.0".
  - "Pre-built wheels for Linux (x86_64, aarch64), macOS (x86_64, arm64), and Windows (x86_64). Python 3.10-3.14."
  - API: `EchoCanceller(sample_rate=16000, num_channels=1, stream_delay_ms=0)` with `process(near, far)` and `reset()`; `NoiseSuppressor(level 0–3 = 6/12/18/21 dB)`; `GainController` (AGC2); `VoiceDetector` (~2 µs per 10 ms frame); and the combined `AudioProcessor(sample_rate, noise_suppression, echo_cancellation, auto_gain_control, stream_delay_ms)`.
  - "Internally splits into 10ms frames in a single GIL-released loop." "Instances are not thread-safe."
  - Sources: [strands-labs/pywebrtc-audio](https://github.com/strands-labs/pywebrtc-audio); [PyPI 0.2.0, cp310–cp314 win_amd64 wheels](https://pypi.org/pypi/pywebrtc-audio/json) [direct]
- aec-audio-processing:
  - BSD-3-Clause, version 1.0.1 (2025-09-01), Windows x86-64 wheels for CPython 3.11/3.12/3.13 only.
  - API `AudioProcessor(enable_aec, enable_ns, enable_agc, enable_vad)` with `set_stream_format`, `set_reverse_stream_format`, `set_stream_delay(ms)`, `process_stream(10 ms bytes)` and `has_voice()`.
  - Source: [PyPI aec-audio-processing](https://pypi.org/project/aec-audio-processing/)
- `webrtc-audio-processing` 0.1.3 (2019) is BSD with no wheels; `speexdsp-ns` 0.1.2 (2023) has no wheels — [PyPI](https://pypi.org/pypi/webrtc-audio-processing/json) [direct]

### Inferences
- **Wiring:**
  - The far-end is the exact PCM JARVIS writes to the output device, resampled to 16 kHz. It is taken from the playback ring buffer, so no system loopback capture is needed.
  - Set `stream_delay_ms` to the measured output-plus-input buffer latency (WASAPI shared mode is typically tens of ms; measure it with `loopback_test.py`).
  - Use one `EchoCanceller` instance owned by the audio thread.
- **Barge-in rule:** while TTS is playing, run Silero/VoiceDetector on the **AEC output**. If speech lasts at least 200–300 ms, return non-zero from the TTS callback, flush the playback buffer, and cancel the LLM stream.
- **Licence fit:** Apache-2.0 + BSD-3 are both permissive and fine. Add WebRTC's BSD notice to the product's third-party notices.

### Gaps
- The bundled WebRTC revision in pywebrtc-audio was not stated.
- There is no ERLE measurement on laptop speakers.
- pywebrtc-audio's maturity is unknown (v0.2.0, few releases).

---

## Q5. Piper licensing: piper1-gpl (GPL-3) vs running Piper voice ONNX files through sherpa-onnx, and the voice licences

### Takeaway
Running Piper `.onnx` voices through sherpa-onnx's `OfflineTtsVitsModelConfig` avoids the **piper-tts 1.8.0 package (GPL-3.0-or-later)**. However, the sherpa-onnx 1.13.8 wheel itself links **espeak-ng** (GPL-3.0) for phonemization: the strings are present in the binary, and the repo's own lock notes "espeak-ng data inside is GPL-3.0" for the Kokoro package. So both routes carry espeak-ng.

The bigger issue is the **per-voice dataset licences**, which vary:
- `te_IN-venkatesh` is CC-BY-4.0 (attribution needed). This is what JARVIS ships.
- `hi_IN-rohan` uses the IIT Madras IndicTTS licence (terms unclear). This is also what JARVIS ships.
- `hi_IN-pratham` and `hi_IN-priyamvada` are **CC-BY-NC-SA** (avoid).

### Cited Findings
- PyPI `piper-tts` 1.8.0 (2026-09-04) is licensed "GPL-3.0-or-later", with a `cp39-abi3-win_amd64` wheel — [PyPI piper-tts](https://pypi.org/pypi/piper-tts/json) [direct]. The upstream rhasspy/piper is archived and development moved to OHF-Voice/piper1-gpl — [PromptQuorum](https://www.promptquorum.com/power-local-llm/piper-tts-review)
- The HF repo `rhasspy/piper-voices` is tagged `mit` at repo level — [HF API](https://huggingface.co/rhasspy/piper-voices) [direct]. The per-voice MODEL_CARDs give the dataset licences [direct]:
  - `hi_IN-pratham-medium` and `hi_IN-priyamvada-medium`: dataset licence "http://creativecommons.org/licenses/by-nc-sa/4.0/" (training data AI4Bharat indicnlp_corpus).
  - `hi_IN-rohan-medium`: IITM IndicTTS "Hindi Mono Male", licence `iitm.ac.in/donlab/indictts/downloads/license.pdf`; "Finetuned from U.S. English lessac voice".
  - `te_IN-maya-medium`: IITM IndicTTS licence.
  - `te_IN-padmavathi-medium` and `te_IN-venkatesh-medium`: "ai4bharat/indicvoices_r … License: CC-BY-4.0".
  - `en_US-lessac-medium`: Blizzard 2013 Lessac licence.
  - `en_GB-northern_english_male-medium`: "CC-BY-SA 4.0".
  - `en_GB-alan-medium`: "See URL" (mimic3 voices).
  - Source: [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices/tree/main)
- The IITM IndicTTS licence excerpt seen via search grants "a perpetual, non-exclusive, worldwide, transferable, sub-licensable, royalty-free right". Its commercial position could not be confirmed (the PDF fetch timed out), and AIKosh lists some IndicTTS Phase-3 sets as CC-BY-4.0, which conflicts — [IITM licence PDF](https://www.iitm.ac.in/donlab/indictts/downloads/license.pdf); [AIKosh Hindi IndicTTS](https://aikosh.indiaai.gov.in/home/datasets/details/hindi_female_mono_indictts_phase3.html)
- The sherpa-onnx 1.13.8 Linux wheel's `libsherpa-onnx-c-api.so` contains espeak-ng messages ("Failed to initialize espeak-ng with data dir", "Wrong version of espeak-ng-data"), so espeak-ng is compiled in [direct, `strings`]. On master (2026-10-22) the cmake directory no longer contains an espeak-ng file. New `*-phonemize.py` examples call `piper_phonemize.phonemize_espeak` from Python ("no espeak-ng data needed"), alongside `*-lexicon.py` variants — [sherpa-onnx python-api-examples](https://github.com/k2-fsa/sherpa-onnx/tree/master/python-api-examples) [direct]. Whether upstream is deliberately moving espeak-ng out of the core is **[unverified]**: I did not see the commit.
- jarvis-hq lock: `kokoro-multi-lang-v1_0-sherpa` "Apache-2.0 (espeak-ng data inside is GPL-3.0)"; `piper-te_in-venkatesh-medium` "CC-BY-4.0 (dataset ai4bharat/indicvoices_r)"; `piper-hi_in-rohan-medium` "dataset licence: IITM IndicTTS" — [jarvis.lock.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/tools/jarvis.lock.json) [direct]

### Inferences
- **Keep Piper voices running inside sherpa-onnx** (already done). Never add `piper-tts` / piper1-gpl as a dependency.
- espeak-ng's GPL-3.0 does not matter for a privately used JARVIS. If the owner ever distributes JARVIS binaries, either ship the corresponding source or offer it (GPL compliance), or move to lexicon-based front-ends. sherpa-onnx's `lexicon=` path for Kokoro, Matcha and Piper avoids espeak at runtime [inference; legal review needed].
- **Voice choice by licence:**
  - Telugu: venkatesh or padmavathi (CC-BY-4.0). Show attribution.
  - Hindi: rohan is acceptable only after the IITM licence PDF is read. Kokoro v1.0 also has Hindi voices (`hf_alpha`, `hf_beta`, `hm_omega`, `hm_psi`, grade C) under Apache-2.0 [see Q6], and Supertonic 3 includes `hi` (OpenRAIL-M). Both remove the IITM uncertainty for Hindi.
  - Never ship pratham or priyamvada (NC-SA).

### Gaps
- The full IITM IndicTTS licence text was not read (fetch timed out).
- The Blizzard 2013 Lessac licence was not read (the CSTR server returned 503). Many Piper voices are fine-tuned from lessac, and whether lessac's terms carry over to fine-tunes is open.
- Whether Windows sherpa-onnx wheels link espeak-ng statically was not checked (only the Linux wheel was inspected).

---

## Q6. Permissive, CPU-capable voice cloning for a "JARVIS" voice (Kyutai Pocket TTS, Supertonic, Chatterbox Turbo, others)

### Takeaway
**Pocket TTS** is the best fit. It is about 100M parameters, English-centric, clones zero-shot from a short clip, runs at about 6x real time on an M4 CPU with about 200 ms to first chunk, and is already integrated in sherpa-onnx with cloning via `GenerationConfig.reference_audio`. Its weights are **CC-BY-4.0** (attribution, gated download, prohibited-use terms including cloning without consent).

The other candidates fall short:
- **Supertonic 3** (99M, OpenRAIL-M, 31 languages including Hindi) is fast and in sherpa-onnx, but its open release has **only preset voice styles**; custom voices need the paid Voice Builder.
- **Chatterbox Turbo** (350M, MIT, English, built-in Perth watermark) clones well, but its examples assume CUDA and there is no official ONNX, so it does not fit a CPU-only, in-process design.
- **ZipVoice** (also in sherpa-onnx, zero-shot with `prompt_text` + `prompt_samples`) is an option, but its licence was not confirmed.
- Kokoro (Apache-2.0) cannot clone, but a British male voice (`bm_george`, `bm_fable`, grade C) is a zero-risk "butler" default.

### Cited Findings
- Pocket TTS:
  - HF licence `cc-by-4.0`, gated ("you have to accept the conditions to access its files").
  - "Small model size, 100M parameters"; "~6x real-time on a CPU of MacBook Air M4"; "~200ms to get the first audio chunk"; uses 2 CPU cores.
  - Languages: English, French, German, Portuguese, Italian, Spanish.
  - Voice cloning from a wav file.
  - Prohibited uses include "voice impersonation or cloning without explicit and lawful consent".
  - Sources: [kyutai/pocket-tts](https://huggingface.co/kyutai/pocket-tts). There is also an ungated `kyutai/pocket-tts-without-voice-cloning`, CC-BY-4.0 — [HF API](https://huggingface.co/kyutai/pocket-tts-without-voice-cloning) [direct]. PyPI `pocket-tts` 4.0.1 was uploaded 2026-10-21 — [PyPI](https://pypi.org/pypi/pocket-tts/json) [direct]
- sherpa-onnx supports Pocket TTS for "streaming voice cloning on CPU" (#3083), with an embedding cache (`voice_embedding_cache_capacity`) and seed support (#3189) — [CHANGELOG](https://github.com/k2-fsa/sherpa-onnx/blob/master/CHANGELOG.md); [pocket-tts.py](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/pocket-tts.py)
- Supertonic 3:
  - "The accompanying model is released under the OpenRAIL-M License"; sample code is MIT.
  - About 99M parameters; 31 languages including `hi`, not Telugu.
  - "The open-weight package includes fixed preset voice styles". Custom voice-style JSON from reference audio comes via the paid "Supertonic Voice Builder".
  - Sources: [Supertone/supertonic-3](https://huggingface.co/Supertone/supertonic-3); PyPI `supertonic` 1.3.1, MIT — [PyPI](https://pypi.org/pypi/supertonic/json) [direct]
- Chatterbox Turbo:
  - "built on a streamlined 350M parameter architecture"; MIT; English only.
  - "requires a reference clip for voice cloning".
  - Every output carries "Resemble AI's Perth (Perceptual Threshold) Watermarker".
  - Paralinguistic tags such as `[laugh]`.
  - The example uses `device="cuda"`, and ONNX is not mentioned.
  - Sources: [ResembleAI/chatterbox-turbo](https://huggingface.co/ResembleAI/chatterbox-turbo); PyPI `chatterbox-tts` 0.1.7 (MIT) — [PyPI](https://pypi.org/pypi/chatterbox-tts/json) [direct]
- ZipVoice in sherpa-onnx: `OfflineTtsZipvoiceModelConfig` and `generate(text, prompt_text, prompt_samples, sample_rate, speed, num_steps)`. The `k2-fsa/ZipVoice` HF repo has no licence tag — [sherpa-onnx wheel](https://pypi.org/project/sherpa-onnx/1.13.8/) [direct]; [HF k2-fsa/ZipVoice](https://huggingface.co/k2-fsa/ZipVoice) [direct API]
- Kokoro-82M is Apache-2.0 [direct API]. The British male voices are `bm_daniel` D, `bm_fable` C, `bm_george` C and `bm_lewis` D+; the Hindi voices are `hf_alpha` C, `hf_beta` C, `hm_omega` C and `hm_psi` C — [Kokoro VOICES.md](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)
- Kyutai Pocket TTS CPU RTF of about 0.69–0.76 in one third-party benchmark (from the earlier research notes) — [X article](https://x.com/withneo/article/2075126134413672841)

### Inferences
- **Recommended JARVIS voice plan:**
  1. Default English: Kokoro `bm_george` or `bm_fable`, already shipped, Apache-2.0.
  2. "Signature" voice: Pocket TTS via sherpa-onnx, cloned from a reference clip of a **consenting** speaker. Options are the owner themselves, a friend with written consent, or a voice designed with Kokoro and then re-recorded. **Not** the film actor. Cache the voice embedding at startup.
  3. Hindi: Kokoro `hm_omega` / `hm_psi`, or Supertonic 3 `hi` preset.
  4. Telugu: Piper venkatesh or padmavathi (CC-BY-4.0).
- Attribution lines required in the app: "Pocket TTS by Kyutai (CC-BY-4.0)", the Piper Telugu voice dataset (AI4Bharat IndicVoices-R, CC-BY-4.0), and the Supertonic OpenRAIL-M use restrictions if Supertonic is shipped.
- Avoid Chatterbox in the core product. It would add PyTorch (about 2+ GB) and a GPU expectation, and the Perth watermark is an extra component. Keep it as an optional offline "pre-render phrases" tool if wanted.

### Gaps
- No CPU RTF on an x86 AMD laptop for Pocket TTS, Supertonic 3 or ZipVoice through sherpa-onnx.
- The ZipVoice weights licence and Pocket TTS's exact gating conditions text were not read.
- The Supertonic 3 OpenRAIL-M restriction list was not read; the card defers to its LICENSE file.

---

## Q7. Shell: merging N.E.K.O.-Tauri into the repo; tray, global-shortcut push-to-talk, single-instance, autostart, updater; Python backend as sidecar; Windows 10 pitfalls

### Takeaway
N.E.K.O.-Tauri (Apache-2.0, with a NOTICE file) is small and already contains the hard part of the sidecar problem. `backend_runtime.rs` launches the Python backend in one of three ways:
- an explicit executable (`NEKO_BACKEND_EXECUTABLE`)
- a **bundled `resources/backend/projectneko_server.exe`** (a frozen build)
- a **source checkout with `.venv\Scripts\python.exe launcher.py`**, created by `uv sync --frozen`

It also attaches to an already-running backend if `GET 127.0.0.1:48911/health` returns `{"app":"N.E.K.O"}`, and kills the child on `RunEvent::Exit`.

Integration steps:
1. Convert the submodule to a `git subtree` (squash), keeping LICENSE and NOTICE.
2. Add the four plugins: single-instance first, then global-shortcut (Pressed/Released), tray-icon and autostart.
3. Replace the plain `Child.kill()` with a Windows Job Object so grandchildren (speech worker, llama-swap, llama-server) die too.
4. Ship the Python side as a uv-managed embedded runtime rather than a PyInstaller exe, to avoid AV false positives and the NSIS stale-sidecar bug.

### Cited Findings
- N.E.K.O.-Tauri HEAD `b7b4fd7` (2026-07-29, "chore: adopt Apache-2.0 license") is the commit jarvis-hq pins, with both `LICENSE` and `NOTICE` ("Copyright 2025-2026 Project N.E.K.O. Team; Copyright (c) 2025 Hongzhi Wen … Licensed under the Apache License, Version 2.0") — [N.E.K.O.-Tauri](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri) [direct]; [jarvis .gitmodules](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/.gitmodules)
- `src-tauri/Cargo.toml` depends on `tauri = "2"` (`macos-private-api`), `xcap 0.3`, `image`, `serde_json` and `base64`. There is no tray, global-shortcut, single-instance or autostart plugin [direct].
- `tauri.conf.json` [direct]:
  - main window `transparent: true`, `alwaysOnTop: true`, `decorations: false`, `visible: false`
  - `"security": {"csp": null}`
  - identifier `com.projectneko.tauri`
  - no `externalBin`
  - Source: [N.E.K.O.-Tauri](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri)
- `backend_runtime.rs` [direct]:
  - `MAIN_SERVER_ADDRESS = "127.0.0.1:48911"`
  - Resolves `NEKO_BACKEND_EXECUTABLE`, then `resource_dir/backend/projectneko_server.exe`, then `NEKO_BACKEND_DIR` (`.venv\Scripts\python.exe` + `launcher.py`; the error text says "请在后端仓库执行 uv sync --frozen", i.e. run `uv sync --frozen` in the backend repo).
  - Spawns with `std::process::Command`, not the shell plugin, setting `PYTHONUTF8=1`.
  - Polls `/health` for up to 600×200 ms, then navigates the window to the backend URL.
  - `terminate()` calls `process.kill()` on `RunEvent::Exit`.
  - Source: [backend_runtime.rs](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri/blob/main/src-tauri/src/backend_runtime.rs)
- The capability `local-backend.json` grants the **remote origin `http://127.0.0.1:48911/*`** the custom commands `allow-set-click-through`, `allow-open-new-window`, `allow-get-screen-capture-sources` and `allow-capture-screen-source` — [capabilities](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri/tree/main/src-tauri/capabilities) [direct]
- Tauri sidecar (official route), all from [Tauri sidecar docs](https://v2.tauri.app/develop/sidecar/):
  - `bundle.externalBin` entries need a `-$TARGET_TRIPLE` suffix (e.g. `-x86_64-pc-windows-msvc.exe`).
  - The capability is `shell:allow-execute` / `shell:allow-spawn` with `"sidecar": true` and arg validators.
  - The Rust call is `app.shell().sidecar("name").spawn()`, which returns `(rx, child)`.
  - The docs mention Python "API servers bundled using `pyinstaller`".
  - The docs do not cover killing the sidecar on exit.
- Third-party guidance: "Tauri spawns sidecars; it does **not** supervise them. Crash detection, restart, signal handling, kill-on-exit — all yours". Escalate to `taskkill /T /F` or a Job Object — [tauri-sidecar-lifecycle (community)](https://claudskills.com/skills/tauri-sidecar-lifecycle/SKILL.md). `processfamily` uses "a shared Job Object" so children die with the parent — [PyPI processfamily](https://pypi.org/project/processfamily)
- Tauri issue #15134 (open since 2026-03-21):
  - With NSIS and a PyInstaller sidecar, reinstalling over the same version "did not overwrite the installed sidecar binary". The sidecar has "no Windows version resource", and a running sidecar may block replacement.
  - The workaround is an NSIS `PREINSTALL` hook that runs `taskkill` and deletes the old exe, plus bumping the version.
  - The same report needed CSP changes to reach the sidecar API.
  - Source: [tauri #15134](https://github.com/tauri-apps/tauri/issues/15134)
- PyInstaller AV false positives: Defender `!ml` heuristic detections are tied to the self-extracting bootloader and the lack of signatures. The mitigations are vendor false-positive submissions, rebuilding the bootloader, and signing. Nuitka "dramatically lower" detection claims come from a vendor blog — [discuss.python.org](https://discuss.python.org/t/pyinstaller-false-positive/43171); [hankhank10/false-positive-malware-reporting](https://github.com/hankhank10/false-positive-malware-reporting); [beatsyncpro (vendor)](https://beatsyncpro.ai/blog/why-pyinstaller-gets-antivirus-flags.html)
- In-process alternatives: `pytauri` (PyO3, with a `standalone` module for embedding Python) and `tauri-plugin-python` (RustPython or PyO3) — [docs.rs pytauri](https://docs.rs/pytauri/latest/pytauri/); [docs.rs tauri-plugin-python](https://docs.rs/tauri-plugin-python)
- Windows installer facts, from [Tauri Windows installer docs](https://v2.tauri.app/distribute/windows-installer/):
  - On Windows 10 (April 2018+) and 11, "the WebView2 runtime is distributed as part of the operating system".
  - WebView2 modes: `downloadBootstrapper` (default, 0 MB), `embedBootstrapper` (~1.8 MB), `offlineInstaller` (~127 MB), `fixedVersion` (~180 MB; you manage security patches), `skip` ("WILL NOT work" without the runtime).
  - NSIS per-user install under `%LOCALAPPDATA%` needs no admin; MSI needs WiX on Windows.
- Updater, from [Tauri updater docs](https://v2.tauri.app/plugin/updater/):
  - "needs a signature … This cannot be disabled."
  - Endpoints must be TLS in production; a static JSON or GitHub `latest.json` works.
  - Windows `installMode` is `passive`, `basicUi` or `quiet`.
  - "Tauri will automatically quit your application before installing updates on Windows."
  - Permission `updater:default`; JS `check()` → `downloadAndInstall()` → `relaunch()`.
- Global shortcut and the other plugins (from the earlier notes): `tauri-plugin-global-shortcut` fires `ShortcutState::Pressed`/`Released`, which is the basis for push-to-talk. It uses Win32 `RegisterHotKey`, so a chord already taken by another app never fires. single-instance "must be the first one to be registered". autostart offers `enable/disable/isEnabled`. The jarvis PATCHES.md pins global-shortcut 2.4.1 and single-instance 2.5.4 — [Tauri global-shortcut](https://v2.tauri.app/plugin/global-shortcut/); [single-instance](https://v2.tauri.app/plugin/single-instance/); [autostart](https://v2.tauri.app/plugin/autostart/); [PATCHES.md](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/third_party/PATCHES.md)
- Win10 WebView2 rendering: a Tauri v2 issue on Windows 10 / WebView2 124 reports a window that renders wrongly until resized — [tauri #9740](https://github.com/tauri-apps/tauri/issues/9740)
- The lock does not pin `uv` ("No uv version is chosen anywhere in the repo") — [jarvis.lock.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/tools/jarvis.lock.json) [direct]

### Inferences
- **Repo layout for "one product"** [inference]:
  ```
  jarvis/desktop/shell/      <- git subtree of N.E.K.O.-Tauri (squash), LICENSE + NOTICE kept, add "Modified by JARVIS" notices to changed files (Apache-2.0 §4(b))
  jarvis/desktop/shell/src-tauri/src/jarvis/{tray.rs, ptt.rs, jobobject.rs, firstrun.rs}
  jarvis/voice/{engine.py, turn.py, aec.py, tts_stream.py, wake/…}   <- replaces jarvis/wake + scripts/local_speech_server.py (keeps 48930 facade)
  jarvis/models/{catalog.json -> merged into jarvis/tools/jarvis.lock.json, fetch.py, licences/}
  ```
  - Commands: `git rm jarvis/third_party/neko-tauri` (and the `.gitmodules` entry), then `git subtree add --prefix=jarvis/desktop/shell https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri b7b4fd7 --squash`.
  - Upstream changes come in later with `git subtree pull … --squash`.
  - JARVIS patches can still be offered upstream with `git subtree split --prefix=jarvis/desktop/shell`. That keeps PATCHES.md's "offer upstream, no long-lived fork" rule [git subtree behaviour from general knowledge, unverified this session].
- **Plugin wiring order:** single-instance (focus the existing window, forward args) → global-shortcut → tray (`tray-icon` feature) → autostart → updater.
  - **Push-to-talk:** on Pressed, send `ptt_start` to the Python engine. On Released, send `ptt_stop`, which bypasses the wake word and Smart Turn. Default chord `Ctrl+Alt+Space`, with a re-bind UI. If `register` fails (already taken), the tray shows "PTT unavailable – choose another key".
  - **Tray menu:** Show/Hide, Mute mic, Pause wake word, Release mouse (click-through fail-safe), Open console, Check for updates, Licences, Quit. The icon follows the state (idle/listening/thinking/speaking/muted).
- **Transport between Rust and Python:** the shell already talks HTTP to 127.0.0.1:48911. Add a loopback-only, token-authenticated control route (`/api/voice/ptt`, `/api/voice/state` SSE) to the N.E.K.O backend. Do not expose new Tauri commands to the remote origin, consistent with PATCHES.md's "no new powerful commands" rule.
- **Tighten the existing capability.** `local-backend.json` lets any page served from 127.0.0.1:48911 call `capture_screen_source`. With `csp: null`, any script injected into that origin could capture the screen. Scope it to the specific window and URL paths, set a CSP, and gate capture behind a user gesture or kernel approval [inference].
- **Process supervision:** in `backend_runtime::start`, create a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and assign the child, so the whole tree dies with the shell (Win32 API; [unverified this session]). The backend must in turn put llama-swap and llama-server in the same job, or its own. Keep the "attach if already running" behaviour but log it in the tray tooltip.
- **Python packaging decision** [inference]:
  - **Option A (recommended for this owner):** the shell bundles a pinned `uv.exe` (pin it in the lock), `pyproject.toml` + `uv.lock`, and the JARVIS source.
    - First run: `uv python install 3.11` (python-build-standalone) and `uv sync --frozen` into `%LOCALAPPDATA%\JARVIS\runtime\.venv`. The shell's existing source-mode path already expects this.
    - No PyInstaller bootloader, so fewer heuristic AV hits, and the venv updates independently of the Tauri updater.
    - Cost: a first-run network step of several hundred MB of wheels.
  - **Option B:** PyInstaller `--onedir` (never `--onefile`) as `resources/backend/projectneko_server.exe`, with a Windows version resource bumped every release, an NSIS PREINSTALL `taskkill` hook (#15134), a Defender false-positive submission per release, and code signing if available.
  - **Option C:** Nuitka standalone. The AV benefit is anecdotal, so test it on VirusTotal.
- **WebView2 on Win10:** use `embedBootstrapper` (+1.8 MB) so a machine with a removed runtime still installs; not `fixedVersion`, so security patches stay with Windows. Keep the 30-minute transparency soak test from PATCHES.md (issue #9740-type glitches).
- **Updater:** the Tauri updater updates only the shell bundle. Python code updates via `git pull`/uv sync, and models via the lock and fetcher (Q8). Since jarvis-hq is private, host `latest.json` and the signed artefacts on a TLS endpoint the laptop can reach without a token. The Oracle VM behind Tailscale with HTTPS, or a separate public release repo, are the options [inference]. Keep the updater private key off the laptop's repo, in a CI secret or offline.

### Gaps
- Not verified: Tauri's handling of `ShortcutState::Released` when the window loses focus mid-press; autostart's Windows mechanism (registry Run key vs Startup folder); plugin version compatibility with the `tauri = "2"` resolved version.
- No measured AV detection rates for PyInstaller vs Nuitka vs a uv venv on Windows 10 with Defender.
- N.E.K.O.-PC (Electron) was not inspected.

---

## Q8. Models: shipping about 15 models as part of the product — first-run downloader, disk budget, licences, attribution, non-commercial items

### Takeaway
JARVIS already has the right foundation. `jarvis/tools/fetch.py` is stdlib-only, https-only, resumable (HTTP Range), sha256-verified with an atomic rename, checks for disk space with a 1 GiB margin, and never runs or unpacks downloads. `jarvis.lock.json` has 81 entries with a `licence` field per entry.

The laptop's mandatory set is already **16.2 GB, plus 2.5 GB optional**, against about 19 GB free. Adding the roster's gpt-oss-20b (about 12 GB) is therefore impossible without pruning.

On licences, every LLM, embedding, reranker, guard and checker in the roster is **Apache-2.0 or MIT**. Gemma 4 moved to Apache-2.0 in April 2026, and gpt-oss is Apache-2.0. The attribution-bearing items are Parakeet (CC-BY-4.0), Pocket TTS (CC-BY-4.0), the Piper Telugu voice (CC-BY-4.0 dataset), AudioSet (if used for wake training), the N.E.K.O/N.E.K.O.-Tauri NOTICE files, and Supertonic's OpenRAIL-M restrictions. The non-commercial items to avoid are openWakeWord pretrained models and ACAV100M features, the Piper `hi_IN-pratham`/`priyamvada` voices, Holo2, F5-TTS weights and XTTS-v2.

### Cited Findings
- `fetch.py` behaviour [direct]:
  - Data root `JARVIS_DATA_ROOT`, else `D:\jarvis`.
  - `--list`, `--check` (ok/missing/MISMATCH/unpinned), `--fetch` to `<dest>.part` with Range resume, then size+sha256 check, then `os.replace`.
  - Refuses unpinned entries unless `--allow-unpinned`; refuses non-https URLs and redirects; refuses unsafe `dest`.
  - Disk check `free < need + 1 GiB`.
  - "It never runs, unpacks or marks executable anything it downloads ("unpack_to" in the lock is a note for the human)".
  - Machine filter (`laptop`, `linux-test`, `oracle-vm`, …).
  - Source: [fetch.py](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/tools/fetch.py)
- `jarvis.lock.json` [direct]:
  - schema_version 1, generated 2026-10-17, 81 entries.
  - Entry keys: `name, kind, machine, url, revision, filename, dest, bytes, sha256, git_blob_sha1, licence, optional, notes, unpack_to, source_evidence, registry_name, existing_pin, unpinned_reason`.
  - "Values come only from upstream metadata (Hugging Face API lfs.oid, GitHub release asset digests…); nothing was downloaded to compute a hash."
  - Laptop totals: **16.18 GB required, 2.53 GB optional**.
  - Large items: Ling-3.0-tiny Q4_K_M 4.82 GB; Qwen3-4B-2507 2.50 GB; Qwen3-4B 2.50 GB (optional); MiniCheck RoBERTa-L `pytorch_model.bin` 1.42 GB; UI-Venus-1.5-2B 1.28 GB + mmproj 0.82 GB; Qwen2.5-Coder-1.5B 1.12 GB; Qwen3-1.7B 1.11 GB; Qwen2.5-Math-1.5B 0.99 GB; LibreOffice MSI 0.37 GB.
  - Voice items total about 1.5 GB compressed.
  - Several small JSON/tokenizer files are unpinned (`git_blob_sha1` only); `silero_vad.onnx` is unpinned.
  - Source: [jarvis.lock.json](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/tools/jarvis.lock.json)
- Licences from the Hugging Face API, `cardData.license` [direct]:

  | Model | HF licence | Notes |
  |---|---|---|
  | Qwen3-4B-Instruct-2507, Qwen3.5-2B, Qwen3.5-4B, Qwen2.5-Coder-1.5B, Qwen2.5-Math-1.5B, Qwen3-Embedding-0.6B, Qwen3-Reranker-0.6B, Qwen3Guard-Gen-0.6B | apache-2.0 | [Qwen org](https://huggingface.co/Qwen) |
  | openai/gpt-oss-20b | apache-2.0 | [HF](https://huggingface.co/openai/gpt-oss-20b) |
  | google/gemma-4-E4B-it, gemma-4-26B-A4B-it | apache-2.0, license_link `ai.google.dev/gemma/docs/gemma_4_license` | [HF](https://huggingface.co/google/gemma-4-E4B-it); Gemma 4 released 2 Apr 2026 under Apache 2.0, dropping the custom Gemma Terms (secondary) — [implicator.ai](https://www.implicator.ai/google-releases-gemma-4-under-apache-2-0-dropping-its-custom-ai-license/) |
  | inclusionAI/Ling-3.0-tiny | mit | [HF](https://huggingface.co/inclusionAI/Ling-3.0-tiny) |
  | lytang/MiniCheck-RoBERTa-Large | mit | [HF](https://huggingface.co/lytang/MiniCheck-RoBERTa-Large) |
  | cross-encoder/nli-deberta-v3-xsmall | apache-2.0 | [HF](https://huggingface.co/cross-encoder/nli-deberta-v3-xsmall) |
  | knowledgator/gliner-pii-small-v1.0 | apache-2.0 | [HF](https://huggingface.co/knowledgator/gliner-pii-small-v1.0); (nvidia/gliner-PII uses the NVIDIA Open Model License — not what JARVIS uses) |
  | BAAI/bge-reranker-v2-m3 | apache-2.0 | [HF](https://huggingface.co/BAAI/bge-reranker-v2-m3); bge-small-en-v1.5 MIT per lock |
  | nvidia/parakeet-tdt-0.6b-v3 | cc-by-4.0, "ready for commercial/non-commercial use" | [HF](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) |
  | ai4bharat/indic-conformer-600m-multilingual | mit, gated=auto | [HF](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual) |
  | ARTPARK-IISc/SraVaani-1.0 | mit, gated=auto | [HF](https://huggingface.co/ARTPARK-IISc/SraVaani-1.0) |
  | hexgrad/Kokoro-82M | apache-2.0 | [HF](https://huggingface.co/hexgrad/Kokoro-82M) |
  | kyutai/pocket-tts | cc-by-4.0, gated=auto | [HF](https://huggingface.co/kyutai/pocket-tts) |
  | Supertone/supertonic-3 | openrail (OpenRAIL-M) | [HF](https://huggingface.co/Supertone/supertonic-3) |
  | ResembleAI/chatterbox-turbo | mit | [HF](https://huggingface.co/ResembleAI/chatterbox-turbo) |
  | pipecat-ai/smart-turn-v3 | bsd-2-clause | [HF](https://huggingface.co/pipecat-ai/smart-turn-v3) |
  | UI-Venus-1.5-2B GGUF | Apache-2.0 (upstream), per lock | [lock](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/tools/jarvis.lock.json) |

- Non-commercial or restricted items found: openWakeWord pretrained models (CC BY-NC-SA 4.0) and ACAV100M features (CC-BY-NC-SA-4.0) — [openWakeWord](https://github.com/dscripka/openWakeWord); [HF dataset](https://huggingface.co/datasets/davidscripka/openwakeword_features). Piper `hi_IN-pratham` and `hi_IN-priyamvada` (dataset BY-NC-SA) — [piper-voices](https://huggingface.co/rhasspy/piper-voices). Holo2 (CC-BY-NC) per the earlier roster notes — [zaxby-hyperv-mcp #46](https://github.com/zaxbysauce/zaxby-hyperv-mcp/issues/46). The English sherpa KWS model was trained on GigaSpeech (non-commercial access terms) — [GigaSpeech terms](https://huggingface.co/datasets/distil-whisper/gigaspeech-l-token-ids/blob/main/README.md)
- CC-BY-4.0 requires credit, a link to the licence, and an indication of changes [CC-BY-4.0 legal code, general knowledge — [creativecommons.org](https://creativecommons.org/licenses/by/4.0/), not re-fetched]. Apache-2.0 §4 requires a copy of the licence, notices on modified files, and redistribution of NOTICE contents [general knowledge — [apache.org](https://www.apache.org/licenses/LICENSE-2.0), not re-fetched].

### Inferences
- **Product-grade model manager.** Extend, do not replace, the lock and `fetch.py`:
  1. **Profiles in the lock**, e.g. `"profile": ["core","voice","vision","deep","indic"]`, with a computed size per profile. First-run UI shows "Core 7.9 GB / +Voice 1.6 GB / +Vision 2.1 GB / +Deep 12 GB" against `shutil.disk_usage(D:)` and refuses selections exceeding free space minus a margin. The current 1 GiB margin should be at least 3–5 GB, because Windows page files, llama.cpp mmap and log growth live on the same disk.
  2. **A safe unpack step.** Today fetch.py deliberately never unpacks, so the archives (Parakeet, Kokoro, KWS) need a separate verified unpack. Use Python `tarfile` with `filter="data"`, which is available in Python 3.11.4+ through the PEP 706 backport ([PEP 706](https://peps.python.org/pep-0706/), general knowledge). Then record per-file sha256 of the extracted tree in a manifest and **delete the archive**. That reclaims about 1.2 GB on the laptop, since archives plus extracted trees otherwise count twice [inference].
  3. **Pin the unpinned entries** (silero_vad.onnx, tokenizer/config JSONs) by computing sha256 once on a trusted download and recording it with `source_evidence: "computed"`.
  4. **A UI surface.** The shell's tray "Models…" opens a console page with status from `fetch.py --check`, download progress (fetch.py already prints progress every 15 s; switch to JSON lines for the UI), the licence, and an attribution line per model.
  5. **Gated downloads** (IndicConformer, SraVaani, Pocket TTS with cloning) cannot use the token-less fetcher. Either mirror them to the owner's own storage after accepting the terms once (licences allow redistribution with attribution: MIT, CC-BY), or add an optional `HF_TOKEN` path that is kept out of logs [inference].
- **Disk budget.** With 16.2 GB required plus 2.5 GB optional already used, any new addition needs pruning. Pruning candidates from the roster notes:
  - Qwen3-1.7B (1.1 GB), after Qwen3.5-2B passes the 157-case A/B
  - Qwen2.5-Math-1.5B (1.0 GB)
  - the optional Qwen3-4B (2.5 GB, duplicates the 2507 decider)
  - MiniCheck `pytorch_model.bin` (1.42 GB). Convert it to ONNX int8 at about 0.36 GB [estimate, not measured], which also removes the torch dependency.
  - the LibreOffice MSI (0.37 GB) after install

  The deep model (gpt-oss-20b, about 12 GB) still does not fit on D: alongside the rest without freeing space or moving it to another drive. That needs an owner decision.
- **Licence UI requirements** [inference]:
  - An "About → Open-source licences" page generated from the lock's `licence` field plus code-dependency notices (Rust crates and Python wheels; tools such as `cargo-about` and `pip-licenses` exist [unverified]).
  - Mandatory visible attribution: "Speech recognition: NVIDIA Parakeet-TDT 0.6B v3 (CC-BY-4.0)", "Voice: Kyutai Pocket TTS (CC-BY-4.0)" if used, "Telugu voice: Piper te_IN-venkatesh, trained on AI4Bharat IndicVoices-R (CC-BY-4.0)", "Desktop shell based on Project N.E.K.O.-Tauri (Apache-2.0)" plus the NOTICE text, and the Supertonic OpenRAIL-M use-restriction text if shipped.
  - MIT and Apache notices for the rest go in the licences page.
- **Default product build excludes all NC items.** Mark them `"licence_class": "non-commercial"` in the lock, with the model manager showing a warning and requiring explicit opt-in. They are legal for this personal assistant but would block any future sharing.

### Gaps
- The licence of "OpenJev Verdict 151M" could not be resolved (no HF repo found under the guessed id).
- The gpt-oss usage policy text was not read.
- The exact text of the Gemma 4 licence page was not fetched; Apache-2.0 is from HF metadata and secondary news.
- The Silero VAD licence (believed MIT) was not re-checked; the HF API refused.
- Sizes of the Pocket TTS and Supertonic 3 sherpa archives were not read.

---

## Q9. Measurement plan for voice on the real laptop (latency, WER, false wakes per hour, barge-in stop time)

### Takeaway
The repo already has the harness pieces: `jarvis/wake/{eval_owner.py, eval_wake.py, wake_latency.py, command_cut_check.py, loopback_test.py}` and `jarvis/bench/wakeword/score.py`. What is missing is a single timestamped event log from the in-process engine, plus a fixed protocol. Every metric below should be run **with an LLM loaded on the GPU and CPU-resident models running**, because CPU contention is the real risk on this laptop.

### Cited Findings
- Repo voice evaluation tools exist (`eval_owner.py`, `eval_wake.py`, `wake_latency.py`, `command_cut_check.py`, `loopback_test.py`). The wake threshold is 0.20, and commands with under 0.4 s of speech are ignored — [jarvis/wake](https://github.com/venkatsairishith-dev/jarvis-hq/tree/remediation-2026-10-18/jarvis/wake); [commit 3b280da9](https://github.com/venkatsairishith-dev/jarvis-hq/commit/3b280da9)
- No wake-to-first-audio latency is recorded anywhere in the repo, and the wake fired on 67% of test clips — [repo_current_state notes / roadmap](https://github.com/venkatsairishith-dev/jarvis-hq/blob/remediation-2026-10-18/jarvis/docs/neko/JARVIS_ROADMAP_2026-10-22.md)
- openWakeWord's design targets are under 5% false-reject and under 0.5 false accepts per hour. LiveKit measured openWakeWord at 8.5 FP/h on its own test set — [openWakeWord](https://github.com/dscripka/openWakeWord); [livekit-wakeword](https://github.com/livekit/livekit-wakeword)
- Smart Turn's vendor latency is 10–100 ms. Pocket TTS reports about 200 ms to first chunk on an M4 — [smart-turn](https://github.com/pipecat-ai/smart-turn); [kyutai/pocket-tts](https://huggingface.co/kyutai/pocket-tts)

### Inferences
Proposed protocol (all [inference]; targets are design goals, not sourced numbers):

1. **Instrumentation.** The engine emits monotonic-clock events to `voice_events.jsonl`. Events: `mic_frame`, `kws_hit(score)`, `spk_verify(score)`, `vad_start`, `vad_end`, `smartturn(p)`, `asr_start`, `asr_end(text, lang)`, `llm_first_token`, `tts_first_chunk`, `audio_out_first_sample`, `bargein_detected`, `tts_stopped`.

2. **Wake word.**
   - Recall: 200 owner clips at 0.5 m, 2 m and 4 m × quiet/fan/TV-on, plus 50 clips from other people.
   - False accepts: at least 24 h of room audio, including deliberate film and TV audio containing "Jarvis" (Iron Man scenes) and Telugu/Hindi TV. With 1 h and zero events, the 95% upper bound is still about 3 FA/h by the rule of three, so 1 h cannot show the < 0.5/h target. A 24 h run bounds it at about 0.125/h.
   - Report a DET curve per engine (sherpa KWS, livekit-wakeword custom, openWakeWord as reference) with and without speaker verification and the transcript re-check. Pick the threshold at a fixed false-accept rate (e.g. ≤ 0.2/h) and compare recall.

3. **ASR accuracy.**
   - Test sets: 100 English utterances (Indian-accented, the owner's real commands), 50 Hindi, 50 Telugu, 30 Telugu-English code-mixed, each with hand transcripts.
   - Score WER with `jiwer` (Apache-2.0 [unverified]) after normalisation (lowercase, punctuation, Indic script normalisation). Also report CER for Telugu.
   - A/B: Parakeet v3 vs (optional) Qwen3-ASR via `from_qwen3_asr` for English; IndicConformer vs SraVaani-1.0 for hi/te.

4. **Latency** (per turn, median and p90 over 50 scripted turns played from a second device):

   | Interval | What it measures | Target |
   |---|---|---|
   | `vad_end` → `smartturn` | endpoint cost | < 100 ms |
   | `vad_end` → `asr_end` | ASR | < 300 ms for 5 s of speech |
   | `asr_end` → `llm_first_token` | local decider vs cloud planner, separately | — |
   | `llm_first_token` → `audio_out_first_sample` | first-sentence TTS | < 400 ms Kokoro, < 600 ms Pocket |
   | user stops speaking → JARVIS starts speaking (headline) | end to end | ≤ 1.2 s local path, ≤ 2.5 s cloud path |

   Also log RTF for each TTS (Kokoro, Pocket, Supertonic, Piper) and each ASR on 20 fixed sentences.

5. **Barge-in.**
   - Record two channels: mic raw, and the output loopback through WASAPI loopback, or just the engine's own output buffer timestamps.
   - The owner interrupts 30 replies at random points. Measure speech onset (from the raw mic) to the last non-zero output sample. Target < 300 ms.
   - Over 50 uninterrupted replies played on the laptop speakers, count self-interruptions (false barge-ins) with AEC on vs off.
   - Compute ERLE (dB) from mic-raw vs AEC-out energy during TTS-only segments.
   - Repeat with headphones as the control.

6. **Resource contention.** Repeat items 4 and 5 with (a) idle, (b) the decider on the GPU, and (c) the deep MoE running with experts on the CPU. Report CPU% and the voice-thread frame-drop count (mic buffer overruns); any overrun is a bug.

7. **Exit criteria for "integrated":**
   - One process tree started by the Tauri shell.
   - All models present and verified by `fetch.py --check`.
   - Push-to-talk and wake both work.
   - Metrics 2–5 are recorded in a dated `jarvis/evals/voice/YYYY-MM-DD.json`.
   - Bench cases are added so regressions fail CI on the Linux test box using recorded audio fixtures.

### Gaps
- No baseline numbers exist yet on the owner's laptop for any of the metrics above.
- The repo's existing `wake_latency.py` / `loopback_test.py` were not read in detail, so how much of the instrumentation already exists is unknown.
- The rule-of-three bound is standard statistics, not a source-cited claim.
