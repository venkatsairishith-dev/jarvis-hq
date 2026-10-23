# 02 - Voice evidence: wake word, VAD, STT, TTS (for "Neko")

Date: 2026-10-16. Research-only agent: nothing was installed, cloned or run. Two small text data files (the KWS lexicon `en.phone` and `tokens.txt`) were downloaded into the session scratchpad and searched as text. Builds on `jarvis/docs/research/02-voice-and-local-models.md` (2026-10-11, measured on this laptop) and does not repeat its measurements in full.

All work in this report runs on **Windows 10** (the MacBook Pro 16 running Boot Camp). No reboot is needed for any of it.

**Evidence labels**
- **[local]**: read directly from code or data in this repository today.
- **[measured-prior]**: measured on this laptop by an earlier project run (file cited).
- **[source]**: read today on the project's own page, model card, release API or PyPI JSON.
- **[claim]**: a vendor or README statement that nobody has checked independently.
- **[second-hand]**: a forum or third-party report.
- **[UNVERIFIED]**: no primary source was found.

---

## 0. Bottom line

| Area | Keep | Add or swap | Why |
|---|---|---|---|
| Wake word | **sherpa-onnx open-vocabulary KWS** (already in N.E.K.O, zh-en 3M model) | Add a **"Hey Neko"** keyword set built from phones that exist in the model's vocabulary. Fallback: train a custom **livekit-wakeword** model (Apache-2.0) only if the measured false-accept rate is poor | No training is needed, and the tokens are present (section 1.3). openWakeWord's pretrained models are NC-SA; Porcupine's free tier ended; Precise is Linux-only and dormant |
| VAD | **Silero VAD v6.2.1** (N.E.K.O endpointing) and Silero through sherpa (JARVIS speech server) | Optionally bump to v6.2.3 after a re-test. Do **not** adopt TEN VAD (non-compete licence terms) | Silero is MIT, tiny and already wired into both stacks |
| STT | **Parakeet-TDT 0.6B v3 int8** (en), **IndicConformer int8 CTC** (hi, te) | Nothing is clearly better on this CPU. Benchmark **SraVaani-1.0** (MIT, 430M; published Telugu mean WER 25.1 vs 26.4) and, licence permitting, **Indic-Transcribe-core** (vendor claims Telugu 11.4 vs 20.0 OIWER, but it is 1.2B, custom licence and GPU-oriented) | Published gains are small (SraVaani) or vendor-only and too heavy for this CPU (Indic-Transcribe) |
| TTS | **Kokoro v1.0 fp32** (en), **Piper** hi_IN / te_IN | Optional Hindi trial: **Supertonic 3** (99M ONNX, OpenRAIL-M). No open Telugu TTS beats Piper on CPU real time | Only Piper and the heavy Indic Parler-TTS / IndicF5 cover Telugu |
| Barge-in | N.E.K.O already has it: browser `echoCancellation: true`, `_interrupt_tts_now`, and an echo-text filter | Measure the latency from the user's speech onset to TTS silence, and self-trigger with speakers on | Section 4.3 |

---

## 1. Wake word "Hey Neko"

### 1.1 What N.E.K.O already has [local]

| Item | Value | File |
|---|---|---|
| Engine | sherpa-onnx `KeywordSpotter` in a disposable `spawn` worker process. Native crashes stay inside that process. No raw audio or text is logged | `main_logic/voice_input/wake_word/sherpa_backend.py` |
| Required runtime | Exactly **`1.13.8+neko.kws2`**. The worker refuses the stock wheel with `WAKE_WORD_RUNTIME_FIX_REQUIRED` | `sherpa_backend.py` line 22 |
| How the runtime is built | `scripts/wake_word/build_wake_word_runtime.ps1` clones sherpa-onnx tag v1.13.8 (commit 11afbd0...), applies two patches (`patches/sherpa-onnx-kws-timestamps.patch`, `sherpa-onnx-kws-decoder.patch`: cumulative frame offsets after decoder resets and alignment scores), builds with **Visual Studio 2022 + cmake 3.31.10** through `uv`, runs native KWS tests, and produces a wheel | build script |
| Model | `sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20`, files `encoder/decoder/joiner-epoch-13-avg-2-chunk-8-left-64` (int8 encoder and joiner), SHA-256 pinned `68447f4f...16a6` | `main_logic/voice_identity_service/wake_word_bundle.py`, `sherpa_backend.model_files` |
| Decoder settings | `keywords_threshold 0.25`, `keywords_score 1.0`, `max_active_paths 8`, `num_trailing_blanks 1`, 1 thread, CPU, 16 kHz | `SherpaWakeWordConfig` |
| Keywords | **Hard-coded** `DEFAULT_WAKE_WORD_KEYWORDS`: `y ōu y í @悠宜`, `Y UW1 IY0 @yui`, `y ōu y ú @悠宜`, `l iú y ú @悠宜` ("Yui"). They are passed unchanged by `session_activation_factory.py`, `resource_manager.py`, `provision_wake_word_model.py` and the release smoke test. **There is no environment variable or UI setting for keywords**, so "Hey Neko" is not configurable today | `config/voice_wake_word.py` |
| Keyword validation | Each line needs at least 2 parts, a final `@label`, no `/`, at most 32 lines, and **every non-label part must be a token in `tokens.txt`**. Per-line `:boost` / `#threshold` suffixes would therefore be rejected (`WAKE_WORD_TOKEN_UNKNOWN`), so only the global threshold and score apply | `SherpaWakeWordConfig.__post_init__`, `_StreamingSpotter.__init__` |
| Enable switches | `NEKO_WAKE_WORD_MODEL_DIR` (deployment-managed) or `NEKO_WAKE_WORD_ENABLED`; otherwise `%LOCALAPPDATA%\N.E.K.O\voice-resources\wake-word\preference.json` | `config/voice_wake_word.py` |
| Activation flow | WAITING (KWS listening) -> wake hit -> VERIFYING (CAM++ owner voiceprint, similarity threshold 0.40) -> REPLAYING (0.3 s pre-roll from an 8 s ring buffer) -> ACTIVE -> idle 30 s -> back to standby. Wake frames are batched (640 samples, up to 16 frames, 30 ms wait) | `voice_input/activation/controller.py`, `voice_identity_service/activation_runtime.py` |
| Evaluation tools | `scripts/wake_word/evaluate_wake_word.py` runs a manifest of WAVs through the real worker. It supports `--keyword` overrides (repeatable), `--threshold`, and `--synthetic` labelling, and it reports **false hits per hour** of negative audio. `evaluate_wake_runtime.py --paced` measures latency in real time through the production scheduler | scripts |
| **State on this laptop** | **Not provisioned.** `D:\jarvis\tools\neko-venv` has no `sherpa_onnx` package, `%LOCALAPPDATA%\N.E.K.O\voice-resources\wake-word` does not exist, and no KWS model is on D:. The earlier JARVIS research found **cmake absent** on this laptop. Wake word is therefore off, and the patched wheel has never been built here | file-system checks today |

### 1.2 Option comparison

| Option | Training | Licence (code / models) | Maintenance | Windows | CPU / size | Published accuracy | Fit for "Hey Neko" |
|---|---|---|---|---|---|---|---|
| **sherpa-onnx KWS, open vocabulary** (zh-en 3M 2025-12-20) | **None.** Write phone lines; `sherpa-onnx-cli text2token --tokens-type phone+ppinyin --lexicon en.phone` [source](https://k2-fsa.github.io/sherpa/onnx/kws/pretrained_models/index.html) | Apache-2.0 code [source](https://github.com/k2-fsa/sherpa-onnx). Model weight licence not stated [UNVERIFIED] | sherpa pushed 2026-10-15; v1.13.8 on 2026-09-10 [source: GitHub/PyPI API] | Yes. N.E.K.O needs its own patched wheel | Encoder 4.4 MB int8, joiner 85 KB, decoder 743 KB; chunk-8 means about **160 ms latency** [source](https://k2-fsa.github.io/sherpa/onnx/kws/pretrained_models/index.html). CPU load not published [UNVERIFIED] | **None published** (no FA/h or recall on the docs page) [source](https://k2-fsa.github.io/sherpa/onnx/kws/index.html) | **Best fit**: already integrated, no data collection, and swapping the phrase is a config change |
| sherpa KWS English-only (gigaspeech 3.3M, 2024-01-01) | None (BPE tokens) | as above | old model | Yes | 4.6 MB int8 encoder, chunk-16 only (about 320 ms) [source](https://k2-fsa.github.io/sherpa/onnx/kws/pretrained_models/index.html) | none | Not needed: the zh-en model already has English phones |
| **openWakeWord** custom model | Colab notebook "<1 hour", plus `automatic_model_training.ipynb`; several thousand synthetic positives; about 30,000 h of negatives in the stock models [source](https://github.com/dscripka/openWakeWord) | Apache-2.0 code; **pretrained models CC BY-NC-SA 4.0** [source](https://github.com/dscripka/openWakeWord). A custom model's licence depends on the TTS and negative data used [UNVERIFIED] | Last release v0.6.0 on **2024-02-11**; last push 2025-12-30 [source: GitHub API] | onnxruntime only on Windows [source](https://github.com/dscripka/openWakeWord) | "15-20 models on one Raspberry Pi 3 core" [claim]. **3.13 ms mean / 5.42 ms p95 per 80 ms frame here, RSS +42 MB** [measured-prior](../../../bench/wakeword/result.json) | Target "<5 % FRR, <0.5 FA/h" (a goal, not a result) [source]. LiveKit measured openWakeWord DNN at **8.5 FA/h, 68.6 % recall** on its own "hey livekit" set [claim](https://github.com/livekit/livekit-wakeword). Local synthetic test of `hey_jarvis`: 96/96 hits, **10/128 false at 0.5** [measured-prior] | Needs training plus a second runtime. Stock-model licence red flag |
| **livekit-wakeword** (openWakeWord-derived) | One YAML: Piper TTS synthesis (English), adversarial negatives, augmentation, 3-phase training [source](https://github.com/livekit/livekit-wakeword) | **Apache-2.0** [source](https://pypi.org/pypi/livekit-wakeword/json) | 0.1.0 on 2026-03-16, **0.3.0 on 2026-09-30** [source: PyPI JSON] | Pure-Python wheel (`py3-none-any`), onnxruntime; Windows not mentioned [source] | Same frozen feature extractors as openWakeWord plus a conv-attention head | Own test set (15,000 positives, 45,084 negatives, 25 h): conv-attention **0.08 FA/h, 86.1 % recall** [claim](https://github.com/livekit/livekit-wakeword) | **Best training-based fallback** if sherpa's open-vocabulary FA/h is too high. Training needs torch (laptop CPU or a GPU lane; Colab is parked by project decision) |
| **Picovoice Porcupine** | Console-trained custom keywords | Proprietary; needs an AccessKey at engine start | **Free Tier discontinued 2026-06-30; "There is no non-commercial tier planned"; "The SDK does not run without a valid key"** (Picovoice replies quoted in [second-hand](https://community.home-assistant.io/t/fyi-picovoice-confirmed-free-tier-accesskeys-will-stop-working-after-june-30-2026/1012744), [second-hand](https://community.home-assistant.io/t/porcupine-free-tier-shutdown-alternatives-for-home-assistant-voice-users/1012382)). The pricing page did not render for fetching [UNVERIFIED] | Yes [UNVERIFIED today] | Very low [claim] | Vendor benchmarks [claim](https://picovoice.ai/blog/wake-word-benchmarks/) | **Reject**: paid enterprise only, an online key check, and lock-in |
| **microWakeWord** (OHF-Voice) | Notebook; Piper synthetic samples plus pre-computed negative features; "complex, for advanced users"; the basic notebook "will most likely not be usable" without experimentation [source](https://github.com/OHF-Voice/micro-wake-word) | Apache-2.0 [source: GitHub API] | Pushed 2026-07-06; last tagged models 2024-12 (`okay_nabu`) [source: GitHub API] | Targets TFLite Micro (ESP32-class); desktop runtime not documented | Tiny | No figures; "early release" [source]. Community SEED method: 0.187 FA/h, FRR 1.55 % from 180 samples [second-hand](https://community.home-assistant.io/t/seed-train-a-custom-wake-word-from-just-a-handful-of-recordings-not-hundreds/1018736) | Wrong target (microcontrollers) |
| **Mycroft Precise** | "Takes lots of data" | Apache-2.0 | **Last push 2023-11-25** (dormant) [source: GitHub API] | **Linux only** [source](https://github.com/MycroftAI/mycroft-precise) | GRU | none | Reject |

### 1.3 "Hey Neko" tokens: verified against the model vocabulary

The zh-en 3M model uses **ARPAbet phones for English** plus pinyin parts for Chinese [source](https://k2-fsa.github.io/sherpa/onnx/kws/pretrained_models/index.html). I checked `tokens.txt` and `en.phone` from a third-party mirror of the model assets (`openEuler/sherpa-kws`, same model name and date [source](https://huggingface.co/openEuler/sherpa-kws)). **Before trusting the result, re-check against the official tarball** (32,885,699 bytes at `https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2`).

- `tokens.txt` contains `HH EY1 N EH1 EY1 IY1 K OW0` (all needed) [local scratchpad check].
- `en.phone` has `HEY HH EY1`, `NECO N IY1 K OW0`, `NICO N IY1 K OW0`. **`NEKO` is not in the lexicon**, so `text2token` cannot generate it and the lines must be written by hand.
- The mirror's sample `keywords.txt` uses `HH EY1 R OW1 B AA2 T :2.0 #0.25 @HEY_ROBOT`, and its "sensitive" variant uses `:2.5 #0.15`.

Proposed lines. These fit N.E.K.O's validator, which allows no per-line `:`/`#` suffixes:

```
HH EY1 N EH1 K OW0 @hey_neko      # "ne-ko" (Japanese / US)
HH EY1 N EY1 K OW0 @hey_neko      # "nay-ko" (common Indian-English reading)
HH EY1 N IY1 K OW0 @hey_neko      # "nee-ko" (lexicon NECO/NICO)
```

All three share one label, the same pattern upstream uses for the four 悠宜/yui spellings. Use the two-word phrase, not the bare name. In the JARVIS benchmark, **7 of the 10 false hits at threshold 0.5 were sentences containing the bare name** ("Jarvis is checking...", "Let me see if Jarvis, the dog next door...") [local: `jarvis/bench/wakeword/make_audio.py` + `result.json`]. If Neko's own replies say "Neko", this is also a self-trigger risk.

### 1.4 Concrete implementation in the existing code (proposal, not done)

1. **Make keywords configurable, with upstream defaults kept.** In `config/voice_wake_word.py`, add `wake_word_keywords()`. It returns `DEFAULT_WAKE_WORD_KEYWORDS` unless `NEKO_WAKE_WORD_KEYWORDS_FILE` names a small UTF-8 file (size cap of 4 KB or less, no symlink). Read the file once and leave validation to `SherpaWakeWordConfig`. Replace the four direct uses of `DEFAULT_WAKE_WORD_KEYWORDS` (`session_activation_factory.py:103`, `resource_manager.py:80`, `scripts/provision_wake_word_model.py:21`, `wake_word_release_smoke.py:60`) with the function. Upstream's pinned test (`test_wake_word_keyword_config.py`) stays green because the default is unchanged. Add one new unit test for the override and one for a rejected file.
2. **Build the patched runtime (Windows, one-off):** install VS 2022 Build Tools (C++) on D: if possible, then run `scripts\wake_word\build_wake_word_runtime.ps1 -Python D:\jarvis\tools\neko-venv\Scripts\python.exe -OutputDirectory D:\jarvis\build\kws-runtime`. Install the resulting wheel into `neko-venv`. This is the only path the backend accepts. The stock `sherpa-onnx 1.13.8` wheel used by the JARVIS speech server is refused by the version check. Check free space on D: before and after (20 GB free today).
3. **Provision the model on D:** `python scripts\provision_wake_word_model.py --model-dir D:\jarvis\models\voice\kws` (hash-verified download, about 33 MB). Set `NEKO_WAKE_WORD_MODEL_DIR` and `NEKO_WAKE_WORD_KEYWORDS_FILE` in `neko-start.ps1`.
4. **Owner voiceprint:** the wake hit only opens the microphone after CAM++ verifies the owner (threshold 0.40). Enrol from the Voice identity settings.
5. **Tune with real audio** (section 6, B1), sweeping the global `keyword_threshold` from 0.15 to 0.40. The decoder default is 0.25.
6. **Only if B1 fails** (for example FRR above 10 % at 1 FA/h or worse): train a livekit-wakeword "hey neko" model and add it as a second backend behind the same `WakeWordDetector` contract. Justify that in `docs/01_RESEARCH_AUDIT.md`.

### 1.5 Always-on microphone, Windows privacy, idle cost

- **Where the microphone is held:** N.E.K.O captures the microphone in the browser (`static/app/app-audio-capture.js`, `getUserMedia` with `echoCancellation: true, noiseSuppression: false`; 16 kHz input bypasses backend DSP and 48 kHz uses backend AGC) [local]. The desktop window is Edge in app mode (05_OPERATIONS.md). So Windows sees **msedge.exe** as the microphone user, and a wake-listening session keeps the microphone open continuously.
- **Windows 10 controls:** Settings > Privacy > Microphone. Desktop apps share **one switch**, "Allow desktop apps to access your microphone", and **cannot be toggled individually**. A **microphone icon appears in the notification area while any app uses the microphone**, and the settings page lists recently used desktop apps [source](https://support.microsoft.com/en-us/windows/windows-camera-microphone-and-privacy-a83257bc-e990-d54a-d212-b5e41beba857). With wake word on, expect that icon to show **all the time**. Edge also shows its own in-tab recording indicator [UNVERIFIED in app mode].
- **Windows 10 support ended 2025-10-14** (same Microsoft page). This does not block always-on capture, but it is a security note for a box with an always-open microphone.
- **Privacy design already present:** the KWS worker never logs audio or text, frames are bounded (16 frames, 640 samples per batch), and the 8 s ring buffer is in memory only [local]. Nothing leaves the laptop on the wake path.
- **CPU and idle power:** openWakeWord measured **3.1 ms per 80 ms frame** (about 4 % of one core) [measured-prior]. The sherpa zh-en 3M model is smaller (4.4 MB int8 encoder), but its per-frame cost on this CPU and the battery impact are **not measured**. Continuous inference every 160 ms stops the CPU from reaching deep idle states, so measure on battery (B2). Keep `num_threads=1` (the default).

---

## 2. VAD

| Option | Licence | Latest | Windows | Cost | Quality evidence | Status in this stack |
|---|---|---|---|---|---|---|
| **Silero VAD** | MIT (repo licence; the README badge alt-text still says "CC BY-NC 4.0", an inconsistency) [source](https://github.com/snakers4/silero-vad) | v6.2.1 2026-02-24, v6.2.2 2026-09-17, **v6.2.3 2026-09-23** [source: GitHub API] | Yes (ONNX) | "<1 ms per 30+ ms chunk on one CPU thread", about 2 MB, 8 / 16 kHz, trained on 6,000+ languages [claim](https://github.com/snakers4/silero-vad) | Vendor quality page | **N.E.K.O endpointing uses Silero v6.2.1** (`silero_vad.onnx`, SHA-256 `1a153a22...88e3`, 512-sample windows, onset 0.5 / offset 0.35, min speech 200 ms, candidate silence 300 ms) plus **Smart Turn v3.2 CPU** (BSD-2-Clause, about 8 MB int8), default `enabled=False` [local: `main_logic/asr_client/endpointing/models/manifest.json`, `config.py`]. **The JARVIS speech server uses Silero through sherpa** (threshold 0.5, min silence 0.2 s, measured best on conversational Telugu) [local: `jarvis/console/jarvis_console/voice.py`, 05_OPERATIONS.md] |
| **TEN VAD** (Agora) | **Apache-2.0 "with additional conditions"**: you may not deploy it "in a way that competes with Agora's offerings" or that enables third parties to build applications; use only "for your benefit and the benefit of your direct End Users" [source: LICENSE via GitHub API](https://github.com/TEN-framework/ten-vad) | pushed 2026-10-09; PyPI `ten-vad` 1.0.6.9 2026-10-09 (pure-Python wheel) [source: PyPI] | Windows x64/x86 C libs; Python prebuilt added 2025-07; the ONNX path is documented for Linux/macOS only [source](https://github.com/TEN-framework/ten-vad) | 10 / 16 ms hops; **RTF 0.0150 on an i7-10710U (Windows)**; Silero 0.0127 vs TEN 0.0086 on a Xeon 6348; library 508 KB [claim](https://github.com/TEN-framework/ten-vad) | "Superior precision" vs WebRTC and Silero, faster speech-to-non-speech transitions (Silero lags "several hundred ms"); PR curves only, no numbers [claim] | **Not adopted.** The gain is unproven on Telugu, and the licence terms are non-standard |
| **WebRTC VAD** (`py-webrtcvad`) | MIT-style (NOASSERTION on GitHub); `webrtcvad-wheels` 2.0.14.post1, MIT, 2026-10-02, win_amd64 wheels [source: PyPI JSON](https://pypi.org/pypi/webrtcvad-wheels/json) | original repo last push **2024-07-04** [source: GitHub API] | Yes | Very cheap (GMM, 10/20/30 ms frames) | Older and less accurate than neural VADs. TEN's README compares against it [claim] | Not used. At most a cheap pre-gate |

**Verdict:** keep Silero. A bump from v6.2.1 to v6.2.3 needs a manifest SHA change and a re-run of the Telugu voice-note score (B4) before it is adopted. Smart Turn v3.2 lists **23 languages including Hindi and Marathi but not Telugu** [source](https://github.com/pipecat-ai/smart-turn), so keep it off for Telugu turns.

---

## 3. STT: English, Telugu, Hindi, code-mixed

### 3.1 What runs today [local, measured-prior]
JARVIS `local_speech_server.py` serves `POST /v1/audio/transcriptions`, and N.E.K.O's worker posts to it through `NEKO_WHISPER_ENDPOINT`:
- **English:** Parakeet-TDT 0.6B v3 int8 (sherpa)
- **Hindi and Telugu:** IndicConformer int8 CTC (sherpa `from_nemo_ctc`)

Measured: **0.04-0.1x real time warm**. Telugu WhatsApp voice note: word recall **86 %** after per-utterance decoding (05_OPERATIONS.md, 2026-10-14). Whisper int8 base/small failed Telugu. Models are at `D:\jarvis\models\voice\stt\{parakeet-tdt-0.6b-v3-int8, indicconformer-int8}` [local].

### 3.2 Candidates

| Model | Licence | Size | Languages relevant | CPU path on Windows | Published accuracy (caveats) | Maintained | Verdict |
|---|---|---|---|---|---|---|---|
| **IndicConformer-600M multilingual** / per-language 120M hybrid CTC-RNNT (AI4Bharat) | MIT, gated [source](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual) | int8 CTC export about 188 MB per language [measured-prior] | 22 Indian languages incl. hi, te | sherpa `from_nemo_ctc` (community export); official card uses `transformers` with `trust_remote_code` and onnxruntime 1.20.1 [source] | Card: Hindi **13.2** WER on Vaani-Benchmark-V1.0, no Telugu figure [source]. SraVaani paper Table 3 (mean over datasets): **hi 13.8 (8 sets), te 26.4 (6 sets)** [source](https://arxiv.org/html/2608.08235v2) | per-language cards 2024-09; collection updated 2026 | **Keep**. The card itself now recommends Indic-Transcribe |
| **SraVaani-1.0** (ARTPARK-IISc, SPIRE Lab) | **MIT**, gated [source](https://huggingface.co/ARTPARK-IISc/SraVaani-1.0) | about **430M**, FP16 about 900 MB; third-party ONNX fp32 encoder 1.77 GB [source](https://huggingface.co/killbanhar/sravaani-onnx) | 65 Indian languages/dialects incl. hi, te, en; code-mixing not mentioned | `transformers` remote code; third-party ONNX with greedy TDT in onnxruntime (needs torch for STFT). **No sherpa / int8 export** | Paper Table 3 means: **hi 14.0, te 25.1** vs IndicConformer 13.8 / 26.4, Gemini 3 Flash 14.5 / 28.9, Saaras v3 14.5 / 28.7 [source](https://arxiv.org/html/2608.08235v2). Card averages: hi 12.4, te 25.1, en 14.8 [source] | modified 2026-09-02 | **Benchmark only.** A 1.3-point Telugu gain is within dataset noise, and the model is 2-4x heavier |
| **Indic-Transcribe-core** (Bodhan AI, on Canary-1B-v2) | **Indic Open Model License v1.0**: share-alike for derivatives, written sign-off for hosted APIs, cap at 500M MAU / $250M revenue; gated [source](https://huggingface.co/bodhan-ai/indic-transcribe-core) | **1.2B** (encoder 811M + decoder 419M), F32/bf16 | 25 incl. hi, te, en, Bhojpuri; **claims code-mixed (Hinglish) transcription as spoken** | `transformers` / NeMo; no ONNX mentioned. onnx-asr runs "Canary v2" at only RTFx 8 on a 9800X3D [source](https://github.com/istupakov/onnx-asr), so this CPU would be several times slower [UNVERIFIED] | "Voice of India" OIWER (orthography-informed, not plain WER): **hi 3.5 vs IndicConformer 6.5; te 11.4 vs 20.0** [claim, vendor] | created 2026-08-19 | **Most promising on paper, heaviest in practice.** Benchmark for Telugu code-mixed notes only if the licence is acceptable and CPU latency is tolerable for voice notes, not for live turns |
| **Meta Omnilingual ASR** (CTC 300M / 1B v2) | Apache-2.0 [source](https://github.com/facebookresearch/omnilingual-asr) | sherpa int8 300M v2 / 1B v2 (2026-02-05) [source: sherpa asr-models assets] | 1,600+ languages | sherpa `from_omnilingual_asr_ctc`; **<40 s audio** per call [source] | Per-language CER is published **only for the 7B LLM-ASR**: hin_Deva **3.6**, tel_Telu **6.5** CER [source: per_language_results_table_7B_llm_asr.csv](https://github.com/facebookresearch/omnilingual-asr). 300M CTC numbers for hi/te not published [UNVERIFIED] | v2 Dec 2025; last push 2025-12-30 | Benchmark as a Telugu fallback only |
| **Parakeet-TDT 0.6B v3** | CC-BY-4.0 (attribution) [source](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) | 0.6B; sherpa int8 465 MB | **25 European languages, no Hindi, no Telugu**; auto language detection [source] | sherpa; onnx-asr RTFx 36 on 9800X3D [source](https://github.com/istupakov/onnx-asr) | Open ASR leaderboard mean WER 6.32-6.34 % [source] | card 2025-08 | **Keep for English.** No NVIDIA Indic Parakeet/Canary on Hugging Face (author listing checked today; an NGC "Parakeet RNNT 1.1B Indic, 4 languages" collection exists, languages not confirmed [second-hand](https://catalog.ngc.nvidia.com/orgs/nvidia/collections/parakeet-1-1b-rnnt-multilingual)) |
| Canary-1B-v2 / 180M-flash | CC-BY-4.0 (card) [UNVERIFIED today] | 1B / 180M | European languages / en, es, de, fr | sherpa (180M flash) | n/a for Indic | 2025 | Not useful for hi/te |
| **Whisper large-v3-turbo / distil / faster-whisper / whisper.cpp** | MIT | turbo q5_0 574 MB | turbo multilingual; **distil-whisper is English-only** | whisper.cpp prebuilt CPU; faster-whisper int8 (no AMD GPU) | Whisper large-v2 FLEURS: hi 21.5, **te about 99** [measured-prior doc, paper]. Turbo RTF 0.89 on this CPU [measured-prior] | whisper.cpp pushed 2026-10-12; faster-whisper 2026-10-13 [source: GitHub API] | **Reject for Telugu**. English is already better served by Parakeet |
| **IndicWhisper (Vistaar)** | MIT [source](https://github.com/AI4Bharat/vistaar) | Whisper-medium class, per-language zip | hi, te and others | needs conversion to ggml/ONNX | **hi: Kathbath 10.3, FLEURS 15.0, avg 13.6; te: Kathbath 25.0, FLEURS 25.4, avg 28.8** [source](https://github.com/AI4Bharat/vistaar) | repo last push 2025-06-08 | No better than IndicConformer for Telugu; heavier |
| **Moonshine** (v2 streaming) | MIT; legacy non-English non-streaming models under a non-commercial community licence [source](https://github.com/moonshine-ai/moonshine) | tiny to base | **No Hindi/Telugu listed** | Windows supported [source] | n/a | pushed 2026-10-02 | English short commands only |
| **SenseVoice-Small** | "model-license" (custom) [source](https://huggingface.co/FunAudioLLM/SenseVoiceSmall) | Whisper-small class | zh, yue, en, ja, ko | sherpa | 70 ms per 10 s [claim] | pushed 2026-09-30 | No Indic |
| **Qwen3-ASR 0.6B** | Apache-2.0 | sherpa int8 838 MB | Hindi yes, **Telugu no** [measured-prior doc] | sherpa | no Hindi number | 2026 | Hindi-only fallback |
| **Sarvam** (Saaras / Saarika / Shuka) | Saaras v3 is **API only**; `sarvamai/shuka-1` (audio-LLM) is the only open audio model in the HF org listing [source: HF API](https://huggingface.co/sarvamai) | n/a | 22 Indian languages (API) | n/a | Saaras v3 te 28.7 / hi 14.5 mean (SraVaani Table 3) | | Not offline. Reject |

**Is anything clearly better than what runs today?** No, on this CPU. SraVaani is a sidegrade (MIT, slightly better published Telugu mean, no int8/sherpa path). Indic-Transcribe-core shows the largest claimed gain, especially for code-mixed Telugu, but it is vendor-measured on its own metric, carries a custom licence and is a 1.2B model tuned for GPU. The best practical gains remain the ones already found locally: per-utterance decoding and short VAD gaps. For code-mixed speech, the cheap next step is a vocabulary list or hotwords (sherpa supports contextual biasing for transducers, not for the CTC export [UNVERIFIED]) or routing by an explicit language setting.

---

## 4. TTS

### 4.1 Current [local, measured-prior]
- **English:** Kokoro v1.0 **fp32** (int8 is 3x slower on this CPU), RTF about 0.35-0.5.
- **Hindi and Telugu:** Piper `hi_IN-rohan`, `te_IN-venkatesh` in their own resident process, because the Piper runtime is GPL-3.0. Cold-request RTF 0.9 (hi) and 1.2 (te); warm 0.03-0.06.
- Models are in `D:\jarvis\models\voice\tts\{kokoro-multi-lang-v1_0, piper-voices}`.

### 4.2 Candidates

| Model | Licence | Size | en / hi / te voices | CPU real time on this kind of CPU | Maintained | Notes / verdict |
|---|---|---|---|---|---|---|
| **Kokoro-82M** | Apache-2.0 (espeak-ng GPL in phonemizer) | 326 MB fp32 | en many; hi 4 (grade C); **no te** | RTF 0.48-0.62 fp32 [measured-prior] | PyPI `kokoro` 0.9.4 2025-04-05; repo pushed 2025-08-06 [source] | **Keep (en).** Upstream is quiet |
| **Piper (piper1-gpl)** | GPL-3.0 runtime; voices per card (te venkatesh / padmavathi CC-BY-4.0; hi rohan IITM; pratham / priyamvada CC BY-NC-SA) | about 60 MB per voice | en, hi 3, **te 3** | RTF 0.03-0.06 warm [measured-prior] | `piper-tts` 1.8.0 2026-09-04; pushed 2026-10-15 [source] | **Keep (hi, te)** |
| **Supertonic 3** (Supertone) | Code MIT; **model OpenRAIL-M** (use-based restrictions) [source](https://huggingface.co/Supertone/supertonic-3) | about **99M** params, ONNX | 31 languages incl. **Hindi; no Telugu** | "Runs fast on CPU"; no figure published [claim] | HF created 2026-05-06; the GitHub repo `supertone-inc/supertonic` is **archived** (2026-09) [source: GitHub API] | Optional Hindi A/B against Piper. sherpa ships Supertonic assets [measured-prior doc] |
| **Kitten TTS** | Code Apache-2.0; nano / micro / mini 0.8 weights Apache-2.0 [source](https://huggingface.co/KittenML/kitten-tts-mini-0.8); **KittenTTS 2 (1.7B) under the "Stellon Labs Community License"** [source](https://github.com/KittenML/KittenTTS) | 15M (25 MB int8) / 40M / 80M; v2 1.7B | 0.8 models: English (8 voices); KittenTTS 2: 20 languages incl. **Hindi, no Telugu** | Small models are CPU-friendly [claim]; v2's only timing is on an A100 [source] | 0.8 on 2026-02-24; kitten-tts-2 created 2026-09-30 [source] | Not better than Kokoro for English quality [UNVERIFIED]. Skip |
| **Chatterbox** (Resemble AI) | MIT; **every output carries the Perth watermark** [source](https://github.com/resemble-ai/chatterbox) | Multilingual V3 500M; Turbo 350M (en); Nano 110M (en) | en; **Hindi** (multilingual and a Hindi pack); no Telugu | Nano "about 3x real time on 8 cores" [claim]; multilingual is GPU-oriented | pushed 2026-07-21 | Too heavy for Hindi on this CPU. Skip |
| **Indic Parler-TTS** (AI4Bharat) | Apache-2.0, gated | 0.9B, F32 about 3.7 GB | hi 4 speakers, **te 3 speakers** | **No CPU RTF published**; tutorials advise a GPU [second-hand]; OVOS fp32 ONNX port exists, quantised graphs "not verified and not shipped" [source](https://huggingface.co/OpenVoiceOS/phoonnx-indic-parler) | card modified 2026-10 [measured-prior doc] | Quality candidate for Telugu, but CPU real time is doubtful. Benchmark (B6) |
| **IndicF5** (AI4Bharat) | MIT tag, gated, voice-cloning terms | 0.4B | 11 languages incl. hi, te; **needs reference audio plus its transcript on every call** | Third-party ONNX build "not realtime on Apple M1 CPU" [second-hand](https://huggingface.co/developerabu/IndicF5-ONNX) | repo pushed 2025-09-24 | Skip for a live voice |
| **Sarvam Bulbul v3** | API only; no open weights found [second-hand](https://sarvam.ai/models) | | 11 Indian languages | n/a | | Not offline |
| F5-TTS (base) | MIT code (weights CC-BY-NC per earlier releases [UNVERIFIED]) | 0.3B | en / zh | GPU | pushed 2026-10-14 | Skip |

**Verdict:** keep Kokoro (en) and Piper (hi, te). The only Telugu upgrade path is Indic Parler-TTS, and it must first prove CPU real time.

### 4.3 Interruption (barge-in) and stop-speaking

Already in N.E.K.O [local]:
- **Echo control at the source:** browser capture with `echoCancellation: true`. Chromium's AEC needs the TTS to play through the same browser audio output, which it does here, because the TTS audio is queued to the frontend.
- **Hard stop:** `tts_runtime._interrupt_tts_now()` interrupts the TTS worker and drops audio already queued for the frontend. `turn.py` has `_interrupt_offline_reply`, `interrupt_mirror_speech` and realtime `cancel_response` barge-in.
- **Echo-text guard:** `_shared._looks_like_recent_ai_echo` drops STT text that matches the assistant's recent speech (similarity 0.88 or more). Its docstring: "Voice barge-in during AI playback should keep flowing unless it resembles the AI text."

Reference pattern: Pipecat starts a user turn on VAD speech after `start_secs` (default 0.2 s) or on a transcript. It can require a **minimum word count** before interrupting, and it then "immediately stops speaking, in-flight work is cancelled". It advises noise filtering upstream rather than raising VAD thresholds [source](https://docs.pipecat.ai/guides/learn/speech-input).

Gaps to measure (not missing features):
- The latency from speech onset to TTS silence.
- Whether the wake-word worker should pause while TTS plays. Neko saying her own name is the self-trigger case seen with "Jarvis".
- Laptop speaker echo with AEC on.

---

## 5. Licence red flags (for this stack)

| Item | Flag |
|---|---|
| openWakeWord pretrained models | CC BY-NC-SA 4.0 |
| Porcupine | Free tier gone (2026-06-30); needs an online key [second-hand, Picovoice quoted] |
| TEN VAD | Apache-2.0 plus Agora non-compete / own-benefit conditions |
| Indic-Transcribe-core, Indic-Speak | Indic Open Model License (share-alike, hosting sign-off, MAU/revenue caps) |
| Supertonic 3 model | OpenRAIL-M use restrictions |
| KittenTTS 2 | Stellon Labs Community License |
| Chatterbox | MIT but outputs are watermarked |
| Piper runtime, espeak-ng | GPL-3.0 (kept in its own process) |
| Piper hi pratham / priyamvada | CC BY-NC-SA |
| Parakeet v3 | CC-BY-4.0 attribution |
| sherpa KWS model weights | Licence not stated [UNVERIFIED] |

Personal use on this laptop is fine for all of these. They matter only if Neko is redistributed.

---

## 6. Local benchmarks to run (Windows, laptop; nothing big on C:)

| # | Benchmark | How (existing tools first) | Pass bar (proposal) |
|---|---|---|---|
| **B1** | "Hey Neko" accuracy | **Positives:** record the owner (and 2 other people) saying each of the 3 pronunciations 20 times, at 0.5 m and 2 m, with and without a fan or TV. **Negatives:** at least 3 h of real room audio, Telugu, Hindi and English TV/YouTube, the owner's normal speech, and **Neko's own TTS saying "Neko"** through the speakers. Annotate intervals. Run `scripts/wake_word/evaluate_wake_word.py --model-dir ... --manifest ... --keyword "HH EY1 N EH1 K OW0 @hey_neko" --keyword ... --threshold {0.15,0.2,0.25,0.3,0.4}` | FRR 5 % or less at 0.5 FA/h or less (openWakeWord's own target) |
| **B2** | KWS CPU and idle power | `evaluate_wake_runtime.py --paced` for latency. Worker process CPU time over 30 min idle (`Get-Process` CPU delta). Battery: `powercfg /batteryreport` before and after 1 h wake-on vs wake-off, same screen brightness. Compare against openWakeWord's 3.1 ms/frame | Average CPU 2 % or less of one core; battery drain delta reported |
| **B3** | Self-trigger and barge-in | Play 50 replies containing "Neko" through the laptop speakers with browser AEC on, and count wake hits. Measure the time from user speech onset to TTS audio stop (log timestamps in `_interrupt_tts_now`) | 0 self-triggers; stop in 300 ms or less |
| **B4** | VAD | Silero v6.2.1 vs v6.2.3 (and TEN VAD only if curious) on the 5 min 19 s Telugu note, scored with `jarvis/scripts/asr_score.py` | No loss in word recall (86 %) |
| **B5** | STT | 30 real microphone clips each in en, hi and te, plus 20 code-mixed Telugu-English clips, plus the Telugu note. Run IndicConformer int8 (baseline) vs SraVaani-1.0 ONNX vs Omnilingual 300M v2 int8, and Indic-Transcribe-core only after licence review. Record WER/CER, RTF and peak RAM | Adopt only if Telugu WER improves by 3 points or more at RTF 0.3 or less |
| **B6** | TTS | Listening test (owner, 10 sentences each): Piper te vs Indic Parler-TTS (fp32 ONNX) RTF on CPU; Piper hi vs Supertonic 3 hi vs Kokoro hf_alpha | Adopt only if RTF is 1.0 or less warm and the owner prefers it |

---

## 7. Sources (fetched 2026-10-16 unless marked)

- sherpa-onnx KWS docs: https://k2-fsa.github.io/sherpa/onnx/kws/index.html ; pretrained models: https://k2-fsa.github.io/sherpa/onnx/kws/pretrained_models/index.html ; repo https://github.com/k2-fsa/sherpa-onnx (releases API: v1.13.8 2026-09-10) ; model mirror (lexicon and tokens checked): https://huggingface.co/openEuler/sherpa-kws
- openWakeWord: https://github.com/dscripka/openWakeWord ; PyPI JSON https://pypi.org/pypi/openwakeword/json
- livekit-wakeword: https://github.com/livekit/livekit-wakeword ; https://pypi.org/pypi/livekit-wakeword/json
- Porcupine: https://community.home-assistant.io/t/fyi-picovoice-confirmed-free-tier-accesskeys-will-stop-working-after-june-30-2026/1012744 ; https://community.home-assistant.io/t/porcupine-free-tier-shutdown-alternatives-for-home-assistant-voice-users/1012382 ; https://picovoice.ai/pricing (did not render) ; https://picovoice.ai/blog/wake-word-benchmarks/
- microWakeWord: https://github.com/OHF-Voice/micro-wake-word ; SEED: https://community.home-assistant.io/t/seed-train-a-custom-wake-word-from-just-a-handful-of-recordings-not-hundreds/1018736
- Mycroft Precise: https://github.com/MycroftAI/mycroft-precise
- Windows microphone privacy: https://support.microsoft.com/en-us/windows/windows-camera-microphone-and-privacy-a83257bc-e990-d54a-d212-b5e41beba857
- Silero VAD: https://github.com/snakers4/silero-vad ; TEN VAD: https://github.com/TEN-framework/ten-vad (LICENSE) ; https://pypi.org/pypi/ten-vad/json ; WebRTC VAD: https://github.com/wiseman/py-webrtcvad , https://pypi.org/pypi/webrtcvad-wheels/json ; Smart Turn: https://github.com/pipecat-ai/smart-turn , https://huggingface.co/pipecat-ai/smart-turn-v3 ; Pipecat: https://docs.pipecat.ai/guides/learn/speech-input
- IndicConformer: https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual ; SraVaani: https://arxiv.org/abs/2608.08235 , https://arxiv.org/html/2608.08235v2 , https://huggingface.co/ARTPARK-IISc/SraVaani-1.0 , https://huggingface.co/killbanhar/sravaani-onnx ; Indic-Transcribe: https://huggingface.co/bodhan-ai/indic-transcribe-core ; Vistaar: https://github.com/AI4Bharat/vistaar ; Omnilingual: https://github.com/facebookresearch/omnilingual-asr (+ per_language_results_table_7B_llm_asr.csv) ; Parakeet v3: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 ; HF author listings (nvidia, sarvamai, ai4bharat) via https://huggingface.co/api/models ; NGC Parakeet 1.1B: https://catalog.ngc.nvidia.com/orgs/nvidia/collections/parakeet-1-1b-rnnt-multilingual ; onnx-asr: https://github.com/istupakov/onnx-asr ; Moonshine: https://github.com/moonshine-ai/moonshine ; SenseVoice: https://huggingface.co/FunAudioLLM/SenseVoiceSmall ; Sarvam: https://sarvam.ai/models
- TTS: https://huggingface.co/Supertone/supertonic-3 ; https://github.com/KittenML/KittenTTS ; https://huggingface.co/KittenML/kitten-tts-mini-0.8 ; https://github.com/resemble-ai/chatterbox ; https://huggingface.co/OpenVoiceOS/phoonnx-indic-parler ; https://huggingface.co/developerabu/IndicF5-ONNX ; PyPI JSON for kokoro, piper-tts
- GitHub repo metadata (archived flag, last push, licence) via the authenticated `gh api repos/...` for every repo named above.
- Local: `main_logic/voice_input/wake_word/*`, `config/voice_wake_word.py`, `main_logic/voice_identity_service/{session_activation_factory,activation_runtime,wake_word_bundle}.py`, `scripts/wake_word/*`, `scripts/provision_wake_word_model.py`, `tests/unit/voice_input/wake_word/*`, `main_logic/asr_client/endpointing/{models/manifest.json,config.py,silero_vad.py}`, `main_logic/core/{tts_runtime,_shared}.py`, `static/app/app-audio-capture.js`, `jarvis/console/jarvis_console/voice.py`, `jarvis/bench/wakeword/{make_audio.py,result.json}`, `jarvis/docs/research/02-voice-and-local-models.md`, `jarvis/docs/05_OPERATIONS.md`.
