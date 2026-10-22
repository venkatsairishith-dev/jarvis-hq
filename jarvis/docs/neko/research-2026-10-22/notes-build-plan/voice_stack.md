# Voice stack for JARVIS (local, human-like, interruptible, Windows 10 + AMD 4 GB VRAM, 32 GB RAM), as of Oct 2026

Scope note: research only. Current JARVIS stack (from owner context): sherpa-onnx KWS "Hey Neko" -> Silero VAD -> Parakeet-TDT 0.6B v3 int8 (EN) / IndicConformer int8 (HI/TE) -> LLM -> Kokoro 82M (EN) / Piper (HI/TE), inside N.E.K.O's realtime voice session. No CUDA, so every recommendation below assumes CPU (ONNX Runtime) or, at best, DirectML. Items marked "[not re-fetched this session]" come from well-known canonical pages I did not re-open during this research run; the report writer should treat them as lower confidence than fetched items.

## Summary: recommended stack per stage (keep vs change)

### Takeaway
Keep most of the current stack: it is already close to the best CPU-feasible open choice. The changes worth making are (1) switch the wake word to openWakeWord's pretrained "hey jarvis" model, or train a LiveKit-wakeword/openWakeWord custom model, (2) add Smart Turn v3.x on top of Silero VAD for endpointing, (3) add WebRTC AEC3 so barge-in works on speakers, (4) A/B test SraVaani-1.0 against IndicConformer for Telugu/Hindi, and (5) replace Piper for Indic with a better Indic TTS such as Indic Parler-TTS or IndicF5, if CPU latency allows. Full-duplex speech-to-speech models are not feasible on this hardware.

### Cited Findings
| Stage | Current | Recommendation | License | CPU feasibility / evidence |
|---|---|---|---|---|
| Wake word | sherpa-onnx KWS "Hey Neko" | openWakeWord pretrained `hey_jarvis` (ONNX on Windows) now; consider livekit-wakeword custom model later | oWW code Apache-2.0, pretrained models CC BY-NC-SA 4.0 | Pi 3 single core runs 15-20 models — [openWakeWord](https://github.com/dscripka/openWakeWord) |
| VAD | Silero VAD | Keep Silero; TEN VAD as optional alternative | TEN VAD Apache-2.0 + non-compete clause | Windows DLL shipped — [TEN VAD](https://github.com/ten-framework/ten-vad) |
| Turn detection | VAD silence only | Add Pipecat Smart Turn v3.x (audio-based, ONNX CPU) | BSD-2-Clause | about 10 ms on some CPUs (vendor) — [smart-turn](https://github.com/pipecat-ai/smart-turn) |
| STT EN | Parakeet-TDT 0.6B v3 int8 | Keep (speed leader); optional Canary-Qwen 2.5B / Qwen3-ASR only if accuracy matters more than latency | CC-BY-4.0 (Parakeet) [not re-fetched] | — [ASR Leaderboard paper](https://arxiv.org/html/2510.06961v4) |
| STT HI/TE | IndicConformer 600M int8 | Keep; A/B against SraVaani-1.0 (430M FastConformer TDT-CTC) | Both MIT (gated) | — [IndicConformer](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual), [SraVaani-1.0](https://huggingface.co/ARTPARK-IISc/SraVaani-1.0) |
| TTS EN | Kokoro 82M | Keep Kokoro as the fast default; Chatterbox (Turbo) for a cloned "JARVIS" voice if CPU latency is acceptable; Supertonic 3 / Kyutai Pocket TTS as fast alternatives | Kokoro Apache-2.0; Chatterbox MIT; Supertonic OpenRAIL-M weights | Kokoro ONNX RTF 0.64 on CPU in a third-party test — [codesota](https://www.codesota.com/guides/tts-models), [X benchmark](https://x.com/withneo/article/2075126134413672841) |
| TTS HI/TE | Piper | Piper is still usable but its upstream repo is archived and the maintained fork is GPL-3.0. Evaluate Indic Parler-TTS / IndicF5 / Praxy (Chatterbox+LoRA) | see TTS section | — |
| AEC / barge-in | none measured | WebRTC AEC3 via Python bindings, using a loopback far-end reference | BSD (WebRTC) | — [pywebrtc-audio](https://github.com/strands-labs/pywebrtc-audio), [aec-audio-processing](https://pypi.org/project/aec-audio-processing/) |
| S2S / full duplex | n/a | Not feasible (needs 10-24 GB GPU) | — | — [Moshi](https://github.com/kyutai-labs/moshi) |
| Framework | N.E.K.O realtime session | Keep N.E.K.O's session; borrow Pipecat's turn-taking and interruption patterns plus its Smart Turn integration | Pipecat BSD-2 [not re-fetched] | — [Pipecat smart-turn docs](https://docs.pipecat.ai/api-reference/server/utilities/turn-detection/smart-turn-overview) |

### Inferences
- The biggest gains in "human-like and interruptible" will come from turn-taking (Smart Turn), barge-in with AEC, and streaming TTS sentence by sentence, not from swapping STT models. Parakeet at 0.04-0.1x real time is already fast enough.
- For the personality, the levers are the voice (a cloned or custom voice via Chatterbox/IndicF5, or a Kokoro voice blend) plus LLM prompting. TTS model choice is secondary to latency here.

### Gaps
- None of the licenses or speeds were measured on the owner's actual laptop. Every RTF number here comes from other hardware.

## Wake word: openWakeWord vs sherpa-onnx KWS vs microWakeWord vs Porcupine vs livekit-wakeword

### Takeaway
openWakeWord ships a pretrained "hey jarvis" model that runs on Windows through ONNX Runtime. That is the fastest path to "Hey Jarvis", but its pretrained weights are non-commercial (CC BY-NC-SA 4.0), which is fine for a personal assistant. LiveKit's April 2026 livekit-wakeword claims far fewer false positives than openWakeWord, but you have to train your own model and the comparison is vendor-run. sherpa-onnx KWS, the current engine, can switch to "hey jarvis" with only a keywords-file change and no training, which makes it the zero-effort baseline to A/B against.

### Cited Findings
- openWakeWord pretrained models include "alexa", "hey mycroft", **"hey jarvis"**, "hey rhasspy", "current weather" and "timers" — [openWakeWord GitHub](https://github.com/dscripka/openWakeWord)
- openWakeWord licensing: code Apache-2.0, pretrained models CC BY-NC-SA 4.0, because the training data has unknown or restrictive licenses — [openWakeWord GitHub](https://github.com/dscripka/openWakeWord)
- openWakeWord states design targets of under 5% false-reject and under 0.5 false accepts per hour "with appropriate threshold tuning". These are targets, not per-model measured results — [openWakeWord GitHub](https://github.com/dscripka/openWakeWord)
- openWakeWord on Windows works only through onnxruntime (tflite-runtime is Linux only). Speex noise suppression is Linux only — [openWakeWord GitHub](https://github.com/dscripka/openWakeWord)
- openWakeWord custom training: a Colab notebook makes a basic model in under an hour from thousands of synthetic TTS clips. The included models used about 30,000 h of negative data. Optional per-speaker "custom verifier" models reduce false activations — [openWakeWord GitHub](https://github.com/dscripka/openWakeWord)
- openWakeWord's last tagged release mentioned is v0.6.0 (Feb 2024), so the project looks low-activity — [openWakeWord GitHub](https://github.com/dscripka/openWakeWord)
- livekit-wakeword (published 6 Apr 2026): an open-source train-your-own pipeline (TTS synthetic data + augmentation + conv-attention classifier on embeddings, ONNX export). Vendor comparison: **0.08 false positives/h vs 8.50 for openWakeWord; recall 86% vs 69%**. No pretrained "hey jarvis" model is mentioned. The license is not stated on the blog — [LiveKit blog](https://livekit.com/blog/livekit-wakeword)
- Conflict: LiveKit's measured 8.5 FP/h for openWakeWord contradicts openWakeWord's own target of under 0.5 FP/h. The difference is probably due to threshold, phrase or test set, so treat both as unverified — [LiveKit blog](https://livekit.com/blog/livekit-wakeword) vs [openWakeWord](https://github.com/dscripka/openWakeWord)
- microWakeWord (ESPHome/Home Assistant) has a hey_jarvis model aimed at ESP32 microcontrollers. Home Assistant says FA/h numbers are "considered by most companies to be a trade secret" and does not publish them. microWakeWord v2 is claimed "twice as well" as v1, especially with its VAD feature — [Home Assistant Voice Chapter 7](https://www.home-assistant.io/blog/2024/06/26/voice-chapter-7/), [ESPHome micro_wake_word](https://esphome.io/components/micro_wake_word.html)
- Real-world report (June 2026): a "Hey Jarvis" microWakeWord setup fired on phonetically similar ambient speech. The user fixed it with a transcript-based post-filter, re-checking the STT text for "jarvis" — [bitworking.org](https://bitworking.org/news/2026/06/fixing-false-positives-in-the-homebrain/)
- Porcupine (Picovoice): proprietary SDK that needs an AccessKey. There is a free tier for personal/non-commercial use, and custom wake words are trained in Picovoice Console — [Picovoice Porcupine](https://github.com/Picovoice/porcupine) [not re-fetched]
- sherpa-onnx KWS: Apache-2.0, open-vocabulary keyword spotting from a keywords file (phrases tokenized to BPE/phonemes) with no retraining, plus per-keyword boosting and thresholds — [sherpa-onnx KWS docs](https://k2-fsa.github.io/sherpa/onnx/kws/index.html) [not re-fetched]

### Inferences
- Recommended path: (1) Immediately change the sherpa-onnx keyword to "hey jarvis" and run openWakeWord `hey_jarvis` in parallel or shadow mode for a few days, logging false wakes per hour and the owner's recall (for example, 50 deliberate utterances at different distances). (2) Pick the winner. (3) If both are weak, train a livekit-wakeword or openWakeWord custom model with the owner's own positive clips added, or an oWW custom verifier. (4) Add the cheap transcript re-check of Parakeet's output for "jarvis" after a wake to cut false accepts further.
- "Jarvis" is a common word in films and TV, so false wakes from media audio are a real risk. AEC on JARVIS's own TTS output also matters.
- Porcupine is the most accurate-feeling option, but it is closed and needs an AccessKey. It conflicts with "open-source", so it is not recommended.

### Gaps
- No independent, published FA/h or recall numbers for any "hey jarvis" model.
- livekit-wakeword's license was not confirmed (the GitHub repo was not fetched).

## VAD and turn detection / endpointing

### Takeaway
Keep Silero VAD and add Pipecat's Smart Turn v3.x. It is an open (BSD-2) audio-native end-of-turn model that runs on CPU in tens of milliseconds, and it is Pipecat's default turn-stop strategy. LiveKit's turn-detector is text-based, about 0.1B parameters, supports Hindi, and runs on CPU, but it is under the restrictive "LiveKit Model License" and is designed as a LiveKit Agents plugin. TEN Turn Detection is an 8B (Qwen2.5-7B) model and is too heavy.

### Cited Findings
- Smart Turn: BSD-2-Clause and "truly open". It runs "in as little as 10ms on some CPUs, under 100ms on most cloud instances" (vendor claim). It is not a VAD: it runs alongside Silero only during silences — [pipecat-ai/smart-turn](https://github.com/pipecat-ai/smart-turn), [Pipecat docs](https://docs.pipecat.ai/api-reference/server/utilities/turn-detection/smart-turn-overview)
- Pipecat bundles smart-turn-v3.2-cpu, and `LocalSmartTurnAnalyzerV3` is Pipecat's default user-turn-stop strategy as of v0.0.102 — [Pipecat reference](https://reference-server.pipecat.ai/en/stable/api/pipecat.audio.turn.smart_turn.local_smart_turn_v3.html)
- Third-party ONNX export of Smart Turn v3.2: about 36 ms per 8 s window with 2 threads, about 20 ms with 4 threads, on an Apple M5 Pro (independent, different hardware) — [soniqo HF](https://huggingface.co/soniqo/Smart-Turn-v3.2-ONNX)
- LiveKit turn-detector: "LiveKit Model License". About 0.1B params, INT8 ONNX on CPU, under 500 MB RAM. Covers 14 languages including Hindi (TPR 99.4% / TNR 96.3%) and English (99.3% / 87.0%). It is text-only, depends on STT output, and is "designed for use as a turn detection plugin within the LiveKit Agents framework". No Telugu — [HF livekit/turn-detector](https://huggingface.co/livekit/turn-detector)
- TEN VAD: Windows x64/x86 DLL, Apache-2.0 "with additional conditions" (no deployment that competes with Agora). TEN Turn Detection is 8B BF16 on a Qwen2.5-7B base, and its license is inconsistent between HF (Apache-2.0) and GitHub (Apache-2.0 with restrictions) — [TEN VAD GitHub](https://github.com/ten-framework/ten-vad), [TEN VAD LICENSE](https://github.com/ten-framework/ten-vad/blob/main/LICENSE), [TEN Turn Detection](https://github.com/ten-framework/ten-turn-detection), [HF TEN_Turn_Detection](https://huggingface.co/TEN-framework/TEN_Turn_Detection)

### Inferences
- Smart Turn is audio-based and operates on prosody, so it should work, at least partly, for Hindi and Telugu speech. Its language coverage list was not checked, and Telugu accuracy is unknown.
- Suggested pipeline: Silero VAD (about 200 ms silence) triggers Smart Turn. If it predicts "complete", end the turn. Otherwise wait up to a fallback timeout of about 1.5-3 s. This is the Pipecat pattern and can be ported into N.E.K.O's session without adopting Pipecat wholesale.
- Push-to-talk (missing today) is trivial to add as a manual override that bypasses both the wake word and endpointing. It is also the fallback for noisy rooms.

### Gaps
- No CPU latency number for Smart Turn on an AMD laptop CPU. No Telugu or Hindi accuracy numbers for Smart Turn.
- The exact LiveKit Model License terms were not read.

## STT English: Parakeet v3 vs Whisper turbo / distil vs Moonshine vs Canary vs Kyutai STT vs Voxtral

### Takeaway
On the Open ASR Leaderboard (March 2026 snapshot), Canary-Qwen 2.5B (5.63% avg WER) and Qwen3-ASR 1.7B (5.76%) are the most accurate. Parakeet-TDT 0.6B is close (v2: 6.05%) and far faster. For a CPU-only laptop, the current Parakeet v3 int8 remains the right choice. Re-examine only if real-world English WER on the owner's (Indian-accented) speech is poor.

### Cited Findings
- Open ASR Leaderboard paper snapshot (27 Mar 2026): Canary-Qwen 2.5B at 5.63% avg WER (RTFx 418); Qwen3-ASR 1.7B at 5.76%; Parakeet TDT 0.6B v2 at 6.05%. 74 of 86 listed models are open source — [ASR Leaderboard arXiv 2510.06961](https://arxiv.org/html/2510.06961v4); consistent with [Codesota leaderboard](https://www.codesota.com/speech/stt-leaderboard)
- Parakeet is the throughput leader (the 1.1B variant has RTFx above 2,000). Parakeet 0.6B v3 covers 25 European languages with automatic language ID, so it has no Hindi or Telugu — [Gladia blog (vendor)](https://www.gladia.io/blog/best-open-source-speech-to-text-models), [Northflank (vendor)](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
- One May 2026 source (presenc.ai) claims Canary 1B leads at about 6.7% and omits Canary-Qwen. This conflicts with the arXiv snapshot and is likely stale — [presenc.ai](https://presenc.ai/research/best-open-weight-speech-to-text-models-2026)
- Svarah benchmark: English ASR evaluated specifically on Indian accents, relevant for the owner's English — [Svarah arXiv](https://arxiv.org/pdf/2305.15760)
- Moonshine (Useful Sensors): small edge English models, MIT for English [not re-fetched] — [moonshine GitHub](https://github.com/usefulsensors/moonshine). Kyutai STT (delayed-streams-modeling, EN/FR, streaming) — [kyutai-labs/delayed-streams-modeling](https://github.com/kyutai-labs/delayed-streams-modeling) [not re-fetched]. Voxtral (Mistral, Apache-2.0, 3B/24B) — [HF mistralai](https://huggingface.co/mistralai) [not re-fetched]

### Inferences
- Canary-Qwen 2.5B and Qwen3-ASR 1.7B are LLM-decoder models: several times heavier than Parakeet on CPU, and probably 0.3-1x real time on a laptop CPU. That is not worth it for a conversational loop. Whisper large-v3-turbo is similar and less accurate than Parakeet on the leaderboard.
- Qwen3-ASR (52 languages) could in principle replace both EN and Indic models with one model, but its Telugu quality is unverified.
- Keep Parakeet v3 int8 via sherpa-onnx. Its speed (0.04-0.1x RT) leaves headroom.

### Gaps
- No CPU RTF numbers for Canary-Qwen or Qwen3-ASR on x86 laptops were found.
- Kyutai STT, Moonshine and Voxtral details were not re-verified this session.

## STT Hindi / Telugu: IndicConformer vs SraVaani vs Omnilingual ASR vs Sarvam

### Takeaway
IndicConformer 600M (MIT) is still a strong open choice. SraVaani-1.0 (IISc/ARTPARK, ~430M FastConformer hybrid TDT-CTC, MIT, gated, 63-65 Indian languages, Aug 2026) is the most credible 2026 challenger and is the same architecture family as Parakeet, so it likely runs in sherpa-onnx or NeMo ONNX. Omnilingual ASR 300M covers Telugu but is a general 1,600-language model with no language conditioning in CTC mode. No head-to-head Telugu WER exists, so the owner must A/B test on their own recordings.

### Cited Findings
- IndicConformer 600M multilingual: MIT (gated), 22 languages including hi/te, hybrid CTC+RNNT. The only listed WER is Hindi 13.2 on Vaani-Benchmark-V1.0. The card recommends the newer "Indic-Transcribe" (25 languages, "better transcription quality") — [HF ai4bharat/indic-conformer-600m-multilingual](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual)
- SraVaani-1.0: ~430M FastConformer, hybrid TDT-CTC decoder. Self-supervised pretraining on 31,255 h of VAANI audio, then about 31,270 h of supervised fine-tuning. 63 languages per the card vs 65 per the paper. MIT, gated. Hindi WER 12.4 (averaged over several test sets, author-reported) — [HF ARTPARK-IISc/SraVaani-1.0](https://huggingface.co/ARTPARK-IISc/SraVaani-1.0), [arXiv 2608.08235](https://arxiv.org/pdf/2608.08235), [SraVaani-0.5-live card](https://huggingface.co/ARTPARK-IISc/SraVaani-0.5-live)
- There is also a streaming "SraVaani-0.5-live" variant, which is relevant for low-latency partials — [HF](https://huggingface.co/ARTPARK-IISc/SraVaani-0.5-live)
- Omnilingual ASR (Meta): 300M CTC v2 is about 325M params and needs about 2 GiB, decodes 16-96x faster than real time, has no language conditioning, and is Apache-2.0 (secondary sources). The 7B LLM-ASR reaches CER under 10 for 78% of 1,600+ languages. Per-language CERs are in `per_language_results_table_7B_llm_asr.csv` (not retrieved) — [mintlify Omnilingual docs](https://mintlify.com/facebookresearch/omnilingual-asr/models/model-specifications), [OpenSourceForU](https://www.opensourceforu.com/?p=91727)
- Independent agricultural-domain benchmark (Feb 2026, 10,934 recordings): best Hindi WER 16.2% and best Telugu WER 33.2%, both from Google STT (commercial). Sarvam scored 46.5% on Hindi and MMS 67.5% on Hindi. This shows Telugu is much harder than Hindi in real-world audio — [arXiv 2602.03868](https://arxiv.org/html/2602.03868v1)
- New 2026 Indian benchmarks: Vaani Benchmark v1.0 (Hindi, multi-reference WER changes scores substantially) and Vimarsha (in-the-wild audio, spelling variation) — [arXiv 2606.21408](https://arxiv.org/pdf/2606.21408), [arXiv 2609.24199](https://arxiv.org/pdf/2609.24199)
- Trelis/tara claims 12.06 mean WER across the 7 Vistaar sets vs Sarvam Saaras-v3 at 12.32 (self-reported) — [HF Trelis/tara](https://huggingface.co/Trelis/tara)

### Inferences
- Sarvam's Saarika/Saaras appear as API products in benchmarks. I found no evidence that their weights are open, so they are not candidates for a local stack.
- Recommended: keep IndicConformer as the default. Export SraVaani-1.0 (and Indic-Transcribe if open) and run a 20-50 utterance owner-voice Telugu/Hindi test, including code-mixed Telugu-English. Pick by WER, then latency. Code-mixing is the likely real-world pain point.
- The 86% word recall on one Telugu note is in line with or better than the agricultural benchmark (33% WER best). One note is not a statistically meaningful sample.

### Gaps
- No Telugu WER found for IndicConformer, SraVaani or Omnilingual 300M on a common test set. No Vistaar leaderboard numbers for these models were retrieved.
- SraVaani's ONNX/sherpa-onnx export status and CPU speed are unknown.
- Whether Sarvam has released any open ASR weights in 2026 was not confirmed.

## TTS: English and Indic, voice cloning and personality

### Takeaway
For English, Kokoro 82M (Apache-2.0) remains the best quality-per-CPU-cycle default. For a distinctive cloned JARVIS voice, Chatterbox (MIT, zero-shot cloning; Turbo variant) is the leading permissive option but wants a GPU. Supertonic 3 (99M, ONNX, 31 languages including Hindi, OpenRAIL-M weights) and Kyutai's Pocket TTS (~100M, MIT, CPU) are fast CPU alternatives. For Hindi and Telugu, Piper's upstream is archived (Oct 2025), and the active fork, OHF-Voice/piper1-gpl, is GPL-3.0. Indic Parler-TTS and IndicF5 (AI4Bharat) or Praxy (Chatterbox + Telugu LoRA) give much better Indic quality but are heavier.

### Cited Findings
- Kokoro-82M: Apache-2.0 code and weights. Third-party CPU benchmark: ONNX mean RTF 0.641, PyTorch 0.665 (0.832 on long inputs) — [codesota TTS licenses](https://www.codesota.com/guides/tts-models), [CPU TTS benchmark (X article)](https://x.com/withneo/article/2075126134413672841). The owner's measured 0.35x RT is better than this third-party number.
- Kyutai Pocket TTS: ~100M params, MIT, CPU-only capable, RTF ~0.69-0.76 in the same CPU benchmark — [X article](https://x.com/withneo/article/2075126134413672841)
- Chatterbox Turbo: MIT. Zero-shot cloning plus a commercial-friendly license, but "wants real GPU time" (secondary). No CPU RTF found — [ttslab compare](https://ttslab.dev/compare/kokoro-82m-vs-chatterbox-turbo), [localaimaster](https://localaimaster.com/blog/best-local-tts-models)
- Chatterbox Multilingual: MIT, 23 languages including Hindi but not Telugu — [Praxy paper](https://arxiv.org/html/2604.25441)
- Supertonic 3: 99M params, ONNX, 31 languages including `hi` (no Telugu). Sample code MIT, weights OpenRAIL-M (use restrictions). Official claim: RTF 0.200 on CPU with 16 threads (vendor) — [supertone-inc/supertonic GitHub](https://github.com/supertone-inc/supertonic), [Better Stack guide](https://betterstack.com/community/guides/ai/supertonic-3/). Note: the GitHub org appears as "supertone-oss-archive" in search results, so check maintenance status.
- Piper: rhasspy/piper made read-only on 6 Oct 2025. Development moved to OHF-Voice/piper1-gpl (GPL-3.0). Version info conflicts (v1.8.0 Sept 2026 per one review vs 1.4.2 on PyPI early 2026), and Alpine packages libpiper 1.4.2 as MIT, also conflicting — [PromptQuorum review](https://www.promptquorum.com/power-local-llm/piper-tts-review), [Alpine libpiper](https://pkgs.alpinelinux.org/package/edge/testing/x86_64/libpiper)
- IndicF5 (AI4Bharat): about 1,417 h of training audio, 11 Indian languages including Hindi and Telugu. Voice cloning from a reference clip. The model card requires users to clone only voices they have explicit permission for — [AI4Bharat/IndicF5](https://github.com/AI4Bharat/IndicF5)
- Indic Parler-TTS (AI4Bharat): handles Telugu (forum report), with style controlled by a text description, not cloning — [OneHack forum](https://onehack.st/t/local-voice-solution/317336); model card [HF ai4bharat/indic-parler-tts](https://huggingface.co/ai4bharat/indic-parler-tts) [not re-fetched; Apache-2.0, ~0.9B per training knowledge]
- Praxy Voice (2026): open-source Hindi/Telugu/Tamil/English TTS with code-mixing and cloning. Built on Chatterbox with a LoRA for Telugu/Tamil, using IndicF5 for code-mixed input. Its headline comparisons use only 10-utterance pilot sets, and it admits open bases trail commercial systems on phonological measures — [praxelhq/praxy](https://github.com/praxelhq/praxy), [arXiv 2604.25441](https://arxiv.org/html/2604.25441)
- XTTS-v2: Coqui Public Model License (non-commercial), and Coqui the company shut down — [HF coqui/XTTS-v2](https://huggingface.co/coqui/XTTS-v2) [not re-fetched]. F5-TTS: code MIT, pretrained weights CC-BY-NC (Emilia data) — [SWivid/F5-TTS](https://github.com/SWivid/F5-TTS) [not re-fetched]. Orpheus 3B, Sesame CSM-1B, Dia 1.6B: English-centric LLM-style TTS that needs a GPU for real-time output [not re-fetched]

### Inferences
- On 4 GB AMD VRAM and no CUDA, the 0.5B+ autoregressive TTS models (Chatterbox, IndicF5, Indic Parler-TTS, Orpheus, CSM, Dia) will likely run slower than real time on CPU. Use them for pre-rendered phrases (greetings, confirmations), or tolerate a first-chunk delay. Kokoro, Supertonic 3, Pocket TTS and Piper are the only clearly real-time CPU choices.
- Personality recipe: (a) keep Kokoro for English and pick or blend a voice that fits "JARVIS" (Kokoro supports voice mixing) [not re-fetched]; (b) if a cloned voice is wanted, test Chatterbox Turbo on CPU via ONNX and measure time-to-first-audio; (c) for Telugu, A/B Piper vs Indic Parler-TTS vs IndicF5 for quality and RTF. Piper stays as the fast fallback. The GPL-3.0 fork does not matter for personal use.
- Do not clone a real person's voice (for example, the film JARVIS actor) without consent. The IndicF5 card restricts this explicitly.

### Gaps
- No CPU RTF found for Chatterbox Turbo, IndicF5 or Indic Parler-TTS. No TTS Arena ranking was retrieved this session.
- The Kyutai TTS 1.6B license and speed were not found. Sarvam Bulbul is believed to be API-only, but this was not verified.

## Speech-to-speech / full-duplex models (Moshi, Sesame, Qwen-Omni, GLM-4-Voice, MiniCPM-o)

### Takeaway
None is feasible on a Windows laptop with 4 GB AMD VRAM and no CUDA. The smallest quantized full-duplex options need about 10-12 GB of NVIDIA VRAM or Apple Silicon. Stay with the cascaded pipeline.

### Cited Findings
- Moshi: the PyTorch version has no quantization and needs about 24 GB of GPU memory. About 200 ms latency on an L4 GPU. The MLX version runs on M-series Macs — [kyutai-labs/moshi](https://github.com/kyutai-labs/moshi)
- Qwen3-Omni-30B-A3B is half-duplex out of the box. A community full-duplex retrofit runs on a 12 GB RTX 3060 at Q4 (~10 GB) with 62 GB of host RAM (unofficial, unverified) — [thecodacus/qwen3-omni-duplex](https://github.com/thecodacus/qwen3-omni-duplex)
- MiniCPM-o 4.5 (9B): INT4 uses about 11 GB on an RTX 4090. llama.cpp-omni full-duplex needs ≥12 GB NVIDIA GPU or an M4 Max with 24 GB — [LearnOpenCV](https://learnopencv.com/minicpm-o-4-5-a-9b-model-that-can-see-hear-and-speak-at-the-same-time/)
- No source showed real-time CPU-only operation for any of these models. A third-party guide lists Moshi on CPU as "not real-time" — [localaimaster Moshi guide](https://localaimaster.com/blog/moshi-realtime-speech-guide)

### Inferences
- The "human-like" feel can be approximated in a cascade by streaming LLM tokens into sentence-chunked TTS, adding backchannel and filler audio, Smart Turn, and fast barge-in. This is how Pipecat, LiveKit and Kyutai's Unmute (a cascaded STT-LLM-TTS demo) work.

### Gaps
- Sesame CSM and GLM-4-Voice hardware needs were not retrieved. Kyutai Unmute's requirements were not found.

## Echo cancellation and barge-in on Windows

### Takeaway
Use WebRTC AEC3 through a Python binding (pywebrtc-audio or aec-audio-processing), with JARVIS's own TTS output buffer as the far-end reference. Because the reference is our own TTS stream, there is no need to capture system loopback. Then run Silero VAD on the AEC output: if user speech lasts more than about 200-300 ms while TTS plays, stop TTS and cancel the LLM. SpeexDSP is the lighter fallback. Windows' built-in communications-mode AEC (Voice Capture DSP) is another option [not verified this session].

### Cited Findings
- pywebrtc-audio: pybind11 bindings for WebRTC APM (AEC3, noise suppression, AGC, VAD). Internal processing at 16, 32 or 48 kHz — [strands-labs/pywebrtc-audio](https://github.com/strands-labs/pywebrtc-audio)
- aec-audio-processing (PyPI): wraps WebRTC audio processing with AEC, NS and AGC — [PyPI](https://pypi.org/project/aec-audio-processing/)
- speexdsp-python: EchoCanceller class with near/far processing. A community thread warns that the default filter length and frame size need tuning — [xiongyihui/speexdsp-python](https://github.com/xiongyihui/speexdsp-python), [OpenConversational forum](https://community.openconversational.ai/t/ec-respeaker-echo-cancellation/8450/18)
- Windows reference implementations: aec-client (WebRTC AEC3, NKF-AEC and DTLN-AEC engines on Windows, using VB-CABLE) — [samudinzul/aec-client](https://github.com/samudinzul/aec-client). PJSIP lists AEC3 among its software echo cancellers — [PJSIP AEC docs](https://docs.pjsip.org/en/latest/specific-guides/audio/aec.html)

### Inferences
- Measurement plan (currently missing): (1) play a TTS reply through the laptop speakers while recording mic raw and AEC output, and compute ERLE (echo return loss enhancement, dB). (2) Count self-interruptions (false barge-ins) over N replies. (3) Have the owner interrupt N times and measure stop latency from speech onset to TTS mute; under 300 ms is the target. (4) Repeat with headphones as the control.
- Time alignment matters: the AEC far-end frames must match what the speaker plays, within the AEC's delay-estimation window. Feeding the exact PCM sent to the output device is the simplest approach.

### Gaps
- No ERLE numbers on laptop hardware were found. LiveKit's built-in APM and Windows Voice Capture DSP were not researched.

## Voice-agent frameworks: Pipecat, LiveKit Agents, TEN, RealtimeVoiceChat, Wyoming/Home Assistant, OVOS

### Takeaway
Keep N.E.K.O's realtime session and borrow components and patterns rather than migrating. Pipecat (BSD-2, Python) is the best source to borrow from: Smart Turn integration, interruption handling, and sentence-aggregated streaming TTS. LiveKit Agents is tied to WebRTC rooms and its turn model has a restrictive license. TEN's turn detector is 8B, and its VAD has an Agora non-compete clause. Wyoming/Home Assistant is a useful reference for the wake word plus satellite protocol (openWakeWord, microWakeWord, Piper all come from that ecosystem).

### Cited Findings
- Pipecat's default turn stop strategy is the local Smart Turn v3 analyzer (v0.0.102+), with the model bundled CPU-only — [Pipecat reference](https://reference-server.pipecat.ai/en/stable/api/pipecat.audio.turn.smart_turn.local_smart_turn_v3.html)
- LiveKit turn-detector is "designed for use as a turn detection plugin within the LiveKit Agents framework" under the LiveKit Model License — [HF livekit/turn-detector](https://huggingface.co/livekit/turn-detector)
- TEN framework is an open-source conversational voice-agent framework. TEN VAD has an Apache-2.0 + Agora non-compete license, and TEN Turn Detection is 8B — [TEN framework](https://github.com/ten-framework/ten-framework), [TEN VAD LICENSE](https://github.com/ten-framework/ten-vad/blob/main/LICENSE), [HF TEN_Turn_Detection](https://huggingface.co/TEN-framework/TEN_Turn_Detection)
- Home Assistant's voice ecosystem (microWakeWord, openWakeWord, Piper via Wyoming) is now maintained under the Open Home Foundation (OHF-Voice org) — [Home Assistant blog](https://www.home-assistant.io/blog/2024/06/26/voice-chapter-7/), [PromptQuorum (Piper move to OHF-Voice)](https://www.promptquorum.com/power-local-llm/piper-tts-review)
- RealtimeVoiceChat (KoljaB) and OVOS: not researched this session.

### Inferences
- Borrow list: (1) Pipecat's Smart Turn analyzer and its "user started speaking, interrupt bot" logic; (2) sentence-level TTS streaming; (3) Home Assistant's transcript re-check pattern for wake-word false accepts.
- The owner's note to "research which git repo is best and use it" points to sherpa-onnx (k2-fsa) as the single most useful repo. It already hosts KWS, Silero VAD, Parakeet, Kokoro, Piper and many Indic models in one ONNX/Windows-friendly runtime, and JARVIS already uses it. Adding Pipecat's smart-turn (ONNX) and a WebRTC AEC binding completes the interruptible loop.

### Gaps
- No hands-on comparison of Pipecat vs LiveKit Agents vs TEN on Windows was found. RealtimeVoiceChat, OVOS, Wyoming protocol details and Pipecat's license were not fetched this session.
- No independent latency measurements of full local pipelines on AMD CPU-only laptops were found.
