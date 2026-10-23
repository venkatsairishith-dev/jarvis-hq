# Claim-checker candidates measured on this CPU (FactCG, MiniCheck, HHEM)

Date: 2026-10-17. Linux test server, neko-test container, `--cpus=3`, `nice -n 19`, one heavy run at a time. No cloud
model and no Claude model was used. Evidence 09 named these checkers; this note measures them.

## 1. Decision rule (written down before any candidate result was seen)

Written at 15:18 EDT, before any candidate was downloaded or run.

- **Baseline:** `xsmall` (cross-encoder/nli-deberta-v3-xsmall, int8 ONNX), re-measured under the same conditions as the
  candidates (same container, `--cpus=3`, nice 19). If its false-acceptance counts differ from the earlier numbers
  (2/46, 1/18, 10/200), the re-measured ones are the baseline.
- **Same pipeline for everyone:** each candidate replaces only the model call (`verify.entailment`). `supported()`, its
  passage and sentence-window logic, and every deterministic guard (numbers, units, qualifiers, bounds, hedges, injected
  text) stay the same. Thresholds stay at xsmall's values: 0.5 on a whole passage, 0.95 on a sentence window.
  **No threshold is tuned.** `vitaminc_sample` is held out and is never used to choose anything.
- **Adopt a candidate as the new default only if all three hold:**
  1. false acceptances (count) are **lower or equal** to xsmall's on **each** of claims_v2, nli_pairs and vitaminc_sample;
  2. recall on vitaminc_sample is **strictly higher** than xsmall's;
  3. latency is **at most 5.0x** xsmall's, measured as seconds per claim over all three sets together (total seconds /
     total claims). The laptop is slower than this server, so there is no rounding up.
- If more than one candidate passes: take the lowest total false acceptances over the three sets, then the highest
  VitaminC recall, then the lowest latency.
- The licence must allow use. A model whose licence forbids it (e.g. non-commercial) is measured but marked
  "not adoptable: licence".
- Measured form: the int8 dynamic-quantised ONNX model, the same form as xsmall, so it runs with onnxruntime only (no
  PyTorch at run time). The conversion is checked against PyTorch fp32 on the 36 nli_pairs. If int8 changes the 0.5
  decision on more than 2 of the 36 pairs, the fp32 ONNX model is measured as well and both are reported.
- Peak memory (RSS) and model size are reported; they do not decide alone.
- Otherwise xsmall stays the default and the measurements are kept as evidence.

## 2. Candidates, licences and conversion

| Name (`RESEARCH_NLI_MODEL`) | Hugging Face repo, revision | Licence | Source weights (sha256 from HF, in jarvis.lock.json) | Run-time file |
|---|---|---|---|---|
| `xsmall` (baseline) | cross-encoder/nli-deberta-v3-xsmall a1508764 | Apache-2.0 | already locked | int8 ONNX from HF, 87 MB |
| `minicheck-roberta` | lytang/MiniCheck-RoBERTa-Large 74c89196 | MIT | pytorch_model.bin 67af45a2... (1.42 GB) | int8 ONNX, 357 MB, sha256 eb111209...2dc0 |
| `minicheck-deberta` / `-fp32` | lytang/MiniCheck-DeBERTa-v3-Large 2f2d01a5 | MIT | pytorch_model.bin 32ced784... (1.74 GB) | int8 642 MB / fp32 1742 MB |
| `factcg` / `-fp32` | yaxili96/FactCG-DeBERTa-v3-Large 0430e350 | MIT (HF card; code repo MIT too) | model.safetensors 379a343d... (1.74 GB) | int8 642 MB / fp32 1742 MB |
| `hhem` | vectara/hallucination_evaluation_model de9a5e6b (+ google/flan-t5-base 7bcac572 tokenizer/config) | Apache-2.0 | model.safetensors 634de18a... (439 MB) | int8 ONNX, 110 MB |

All licences allow use; none is "not adoptable: licence". Bespoke-MiniCheck-7B (CC BY-NC) and the Flan-T5 MiniCheck were
not measured (too big for this CPU budget, as agreed). Every weight file passed `fetch.py --check` (4 sha256 ok, 20 small
files git-blob SHA-1 and size ok). Downloads were rate-limited to 8 MB/s on this shared machine.

Conversion (`jarvis/evals/neko95/bench/nli_export.py`, throw-away python:3.11-slim venv: torch 2.8.0+cpu, transformers
4.57.1, tokenizers 0.22.1, onnx 1.19.1, onnxruntime 1.25.0, numpy 1.26.4; full `pip freeze` in each model folder's
export.json). Input formats follow the authors' inference code (FactCG instruction template; MiniCheck "document + eos
token + claim"; HHEM prompt, class 1 of the first token). HHEM's remote code was not run (rebuilt as
T5ForTokenClassification). Check on the 36 nli_pairs against PyTorch fp32:

| Model | token ids vs reference tokenizer | fp32 ONNX vs PyTorch | int8 ONNX vs PyTorch | PyTorch accuracy / int8 accuracy at 0.5 |
|---|---|---|---|---|
| minicheck-roberta | 0 mismatches | identical | max diff 0.029, 0 flips | 1.000 / 1.000 |
| minicheck-deberta | 0 | identical | max diff 0.761, **8 flips** | 0.972 / 0.806 |
| factcg | 0 | identical | max diff 0.859, **18 flips** | 1.000 / 0.500 |
| hhem | 0 | identical | max diff 0.534, 1 flip | 0.917 / 0.889 |

Dynamic int8 breaks both DeBERTa-v3-large models, so by the rule their fp32 ONNX files were measured as well.

## 3. Results (claims_eval.py, `--cpus=3`, nice 19, model loaded before timing)

The other agent's llama.cpp benchmark (4 threads) was running during every run, the baseline included, so all models
shared the same conditions. Result files: `jarvis/evals/neko95/results/claims-<set>-<model>-20261017-*.json`.

| Model | Set | Precision | Recall | False acceptance | FAR | False rejection | FRR | s/claim | Peak RSS MB |
|---|---|---|---|---|---|---|---|---|---|
| xsmall | claims_v2 | 0.938 | 0.938 | 2/46 | 0.043 | 2/32 | 0.062 | 0.180 | 591 |
| xsmall | nli_pairs | 0.947 | 1.000 | 1/18 | 0.056 | 0/18 | 0.000 | 0.165 | 592 |
| xsmall | vitaminc_sample | 0.898 | 0.440 | 10/200 | 0.050 | 112/200 | 0.560 | 0.045 | 592 |
| **minicheck-roberta** | claims_v2 | 1.000 | 1.000 | **0/46** | 0.000 | 0/32 | 0.000 | 0.644 | 528 |
| **minicheck-roberta** | nli_pairs | 1.000 | 1.000 | **0/18** | 0.000 | 0/18 | 0.000 | 0.623 | 518 |
| **minicheck-roberta** | vitaminc_sample | 0.940 | **0.710** | **9/200** | 0.045 | 58/200 | 0.290 | 0.185 | 518 |
| minicheck-deberta (int8) | claims_v2 | 0.794 | 0.844 | 7/46 | 0.152 | 5/32 | 0.156 | 0.942 | 1776 |
| minicheck-deberta (int8) | nli_pairs | 0.789 | 0.833 | 4/18 | 0.222 | 3/18 | 0.167 | 0.901 | 1777 |
| minicheck-deberta (int8) | vitaminc_sample | 0.842 | 0.320 | 12/200 | 0.060 | 136/200 | 0.680 | 0.246 | 1778 |
| minicheck-deberta-fp32 | claims_v2 | 0.879 | 0.906 | 4/46 | 0.087 | 3/32 | 0.094 | 0.890 | 3085 |
| minicheck-deberta-fp32 | nli_pairs | 0.947 | 1.000 | 1/18 | 0.056 | 0/18 | 0.000 | 0.831 | 3117 |
| minicheck-deberta-fp32 | vitaminc_sample | 0.902 | 0.415 | 9/200 | 0.045 | 117/200 | 0.585 | 0.220 | 3003 |
| factcg (int8, broken) | claims_v2 | 0.516 | 1.000 | 30/46 | 0.652 | 0/32 | 0.000 | 0.555 | 1778 |
| factcg (int8, broken) | nli_pairs | 0.529 | 1.000 | 16/18 | 0.889 | 0/18 | 0.000 | 0.361 | 1778 |
| factcg (int8, broken) | vitaminc_sample | 0.554 | 0.740 | 119/200 | 0.595 | 52/200 | 0.260 | 0.251 | 1779 |
| factcg-fp32 | claims_v2 | 1.000 | 0.969 | 0/46 | 0.000 | 1/32 | 0.031 | 1.253 | 3078 |
| factcg-fp32 | nli_pairs | 1.000 | 1.000 | 0/18 | 0.000 | 0/18 | 0.000 | 1.174 | 3047 |
| factcg-fp32 | vitaminc_sample | 0.953 | 0.305 | 3/200 | 0.015 | 139/200 | 0.695 | 0.325 | 3036 |
| hhem | claims_v2 | 0.689 | 0.969 | 14/46 | 0.304 | 1/32 | 0.031 | 0.176 | 288 |
| hhem | nli_pairs | 0.895 | 0.944 | 2/18 | 0.111 | 1/18 | 0.056 | 0.206 | 288 |
| hhem | vitaminc_sample | 0.865 | 0.480 | 15/200 | 0.075 | 104/200 | 0.520 | 0.061 | 288 |

Calibration (score band -> share really supported) is in each result file. MiniCheck-RoBERTa is the best calibrated:
on VitaminC 0.0-0.1 -> 0.00 (83 claims), 0.1-0.5 -> 0.08 (111), 0.5-0.9 -> 0.75 (40), 0.9-0.99 -> 0.97 (166); xsmall's
0.0-0.1 band still holds 34% supported claims (262). FactCG int8 puts almost every claim in 0.5-0.9 (a broken model).

## 4. The rule applied

| Model | FA <= xsmall on all 3 sets | VitaminC recall > 0.44 | s/claim, 3 sets together (x xsmall 0.0741) | Verdict |
|---|---|---|---|---|
| minicheck-roberta | yes (0, 0, 9) | yes (0.71) | 0.2856 (3.85x) | **passes** |
| minicheck-deberta int8 | no (7, 4, 12) | no (0.32) | 0.3973 (5.36x) | fails |
| minicheck-deberta-fp32 | no (4, 1, 9) | no (0.415) | 0.3640 (4.91x) | fails |
| factcg int8 | no (30, 16, 119) | yes (0.74) | 0.3045 (4.11x) | fails (broken conversion) |
| factcg-fp32 | yes (0, 0, 3) | no (0.305) | 0.5251 (7.08x) | fails (recall, latency) |
| hhem | no (14, 2, 15) | yes (0.48) | 0.0883 (1.19x) | fails |

A repeat of xsmall and MiniCheck-RoBERTa (not saved) gave identical verdicts on every set and 3.62x latency
(0.0601 vs 0.2179 s/claim). No threshold was tuned; vitaminc_sample chose nothing.

**Adopted: MiniCheck-RoBERTa-Large (int8 ONNX) as the new default** (`verify.DEFAULT_MODEL = "minicheck-roberta"`). It
is the only candidate that passes all three conditions. xsmall stays as the fallback (`FALLBACK_MODEL`): when the
MiniCheck file is missing (the laptop today), verify.py warns and checks with xsmall; an unknown `RESEARCH_NLI_MODEL`
warns and uses the default. FactCG-fp32 accepts the fewest false claims (3/200) but confirms only 30% of true VitaminC
claims and is 7x slower; it stays selectable as `factcg-fp32` for a stricter mode.

Also measured with the real checker: research bench area H, MiniCheck-RoBERTa 60/60, xsmall 59/60 (H-042: "The city has 2
million residents." confirmed from a past figure).

## 5. Limits

- The int8 file is converted here, not downloaded: copy `/srv/jarvis/models/nli/minicheck-roberta-large/onnx/model_quint8.onnx`
  (and tokenizer.json) to `D:\jarvis\models\nli\minicheck-roberta-large\` on the laptop, or rerun nli_export.py there,
  and check the sha256 above. Until then the laptop keeps using xsmall.
- Latency is from this server (3 CPUs of 16, with another benchmark running). The laptop is slower; the ratio (about
  3.6-3.9x) is what the rule uses. The model is 357 MB on disk; peak RSS was about 520 MB (xsmall: about 590 MB).
- The labelled sets are small (78, 36, 400 pairs). claims_v2 and nli_pairs shaped the deterministic guards earlier, which
  helps every model equally. Training-data overlap of the candidates with VitaminC was not checked.
