"""Convert a claim-checker candidate (verify.BACKENDS) to int8 ONNX and check the conversion. Evidence 11, 2026-10-17.

Runs only in a THROW-AWAY container venv: the neko-test image and the laptop's runtime have no PyTorch. Versions used:
    torch==2.8.0+cpu transformers==4.57.1 tokenizers==0.22.1 huggingface-hub==0.35.3 safetensors==0.6.2
    onnx==1.19.1 onnxruntime==1.25.0 numpy==1.26.4  (python:3.11-slim; full `pip freeze` goes into export.json)

    python -I nli_export.py --model factcg --dir /m/factcg-deberta-v3-large --repo /src

The source files come from jarvis/tools/jarvis.lock.json (sha256-checked with fetch.py --check first); nothing is
downloaded here (HF_HUB_OFFLINE). HHEM-2.1-Open is rebuilt as T5ForTokenClassification from its safetensors, so its
remote code (trust_remote_code) is never run. Writes <dir>/onnx/model.onnx (fp32) and <dir>/onnx/model_quint8.onnx
(dynamic int8, QUInt8 weights like the xsmall avx2 file), then compares PyTorch fp32, ONNX fp32 and ONNX int8 on the
36 nli_pairs (whole passage, the input verify.encode builds) and writes <dir>/export.json.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load_verify(repo):
    spec = importlib.util.spec_from_file_location("verify", os.path.join(repo, "plugin/plugins/research/verify.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["verify"] = mod                                            # dataclasses look the module up here
    spec.loader.exec_module(mod)
    return mod


def torch_model(name, d):
    import torch
    from transformers import AutoModelForSequenceClassification
    if name != "hhem":
        return AutoModelForSequenceClassification.from_pretrained(d, local_files_only=True).eval(), False, d
    from safetensors.torch import load_file
    from transformers import T5Config, T5ForTokenClassification
    cfg = T5Config.from_pretrained(os.path.join(d, "flan-t5-base"), local_files_only=True)
    cfg.num_labels = 2
    m = T5ForTokenClassification(cfg)
    sd = {k[3:]: v for k, v in load_file(os.path.join(d, "model.safetensors")).items() if k.startswith("t5.")}
    missing, unexpected = m.load_state_dict(sd, strict=False)
    tied = {"transformer.encoder.embed_tokens.weight"}                      # tied to transformer.shared
    assert not unexpected and set(missing) <= tied, (missing, unexpected)
    if missing:
        m.transformer.encoder.embed_tokens.weight = m.transformer.shared.weight
    torch.set_grad_enabled(False)
    return m.eval(), True, os.path.join(d, "flan-t5-base")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--repo", default="/src")
    ap.add_argument("--threads", type=int, default=3)
    a = ap.parse_args(argv)
    import numpy as np
    import onnxruntime as ort
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from tokenizers import Tokenizer
    from transformers import AutoTokenizer
    torch.set_num_threads(a.threads)
    verify = load_verify(a.repo)
    b = verify.BACKENDS[a.model]
    model, first_token, tok_dir = torch_model(a.model, a.dir)

    class Wrap(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask):
            lg = self.m(input_ids=input_ids, attention_mask=attention_mask).logits
            return lg[:, 0, :] if first_token else lg

    os.makedirs(os.path.join(a.dir, "onnx"), exist_ok=True)
    fp32, int8 = os.path.join(a.dir, "onnx", "model.onnx"), os.path.join(a.dir, b.onnx)
    tok = Tokenizer.from_file(os.path.join(a.dir, b.tokenizer))
    tok.no_truncation()
    tok.no_padding()
    t0 = time.time()
    if not os.path.isfile(fp32):
        ex = verify.encode(b, tok, "The tower is 330 metres tall.", "The tower is tall.")
        ids = torch.tensor([ex.ids]); mask = torch.tensor([ex.attention_mask])
        torch.onnx.export(Wrap(model).eval(), (ids, mask), fp32, input_names=["input_ids", "attention_mask"],
                          output_names=["logits"], opset_version=17, do_constant_folding=True, dynamo=False,
                          dynamic_axes={"input_ids": {0: "batch", 1: "seq"}, "attention_mask": {0: "batch", 1: "seq"},
                                        "logits": {0: "batch"}})
    if not os.path.isfile(int8):
        quantize_dynamic(fp32, int8, weight_type=QuantType.QUInt8, per_channel=False, reduce_range=False)
    convert_secs = time.time() - t0

    # Check: verify.encode (tokenizers library) gives the same ids as the reference tokenizer, and the int8 model agrees
    # with PyTorch fp32 on the 36 labelled pairs.
    hf = AutoTokenizer.from_pretrained(tok_dir, use_fast=True, local_files_only=True)
    if a.model.startswith("minicheck"):
        assert b.prompt == "{premise}" + hf.eos_token + "{claim}", (b.prompt, hf.eos_token)
    fx = json.load(open(os.path.join(a.repo, "plugin/plugins/research/tests/fixtures/nli_pairs.json"), encoding="utf-8"))
    opts = ort.SessionOptions(); opts.intra_op_num_threads = a.threads
    sess = {k: ort.InferenceSession(p, opts, providers=["CPUExecutionProvider"]) for k, p in (("onnx_fp32", fp32), ("onnx_int8", int8))}
    rows, id_mismatch = [], 0
    for pid, lab, claim in fx["pairs"]:
        prem = fx["passages"][pid]
        enc = verify.encode(b, tok, prem, claim)
        ref = hf(b.prompt.format(premise=prem, claim=claim))["input_ids"]
        id_mismatch += list(enc.ids) != list(ref)
        feeds = {"input_ids": np.array([enc.ids], dtype=np.int64), "attention_mask": np.array([enc.attention_mask], dtype=np.int64)}
        r = {"label": lab}
        with torch.no_grad():
            lg = Wrap(model)(torch.tensor([ref]), torch.ones(1, len(ref), dtype=torch.long))[0].numpy()
        for k, lgk in [("torch_fp32", lg)] + [(k, s.run(None, feeds)[0][0]) for k, s in sess.items()]:
            p = np.exp(lgk - lgk.max()); r[k] = float((p / p.sum())[1])
        rows.append(r)
    check = {"pairs": len(rows), "token_id_mismatches": id_mismatch}
    for k in ("onnx_fp32", "onnx_int8"):
        check[k] = {"max_abs_diff_vs_torch": round(max(abs(r[k] - r["torch_fp32"]) for r in rows), 4),
                    "decision_flips_at_0.5": sum((r[k] >= 0.5) != (r["torch_fp32"] >= 0.5) for r in rows)}
    check["torch_fp32_accuracy_at_0.5"] = round(sum((r["torch_fp32"] >= 0.5) == (r["label"] == "yes") for r in rows) / len(rows), 3)
    check["onnx_int8_accuracy_at_0.5"] = round(sum((r["onnx_int8"] >= 0.5) == (r["label"] == "yes") for r in rows) / len(rows), 3)
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True).stdout.split()
    src = sorted(f for f in os.listdir(a.dir) if os.path.isfile(os.path.join(a.dir, f)) and f != "export.json")
    if os.path.isdir(os.path.join(a.dir, "flan-t5-base")):
        src += ["flan-t5-base/" + f for f in sorted(os.listdir(os.path.join(a.dir, "flan-t5-base")))]
    out = {"model": a.model, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "convert_seconds": round(convert_secs, 1),
           "outputs": {os.path.relpath(p, a.dir): {"bytes": os.path.getsize(p), "sha256": sha256(p)} for p in (fp32, int8)},
           "sources": {f: {"bytes": os.path.getsize(os.path.join(a.dir, f)), "sha256": sha256(os.path.join(a.dir, f))} for f in src},
           "quantization": "onnxruntime.quantization.quantize_dynamic(weight_type=QUInt8, per_channel=False, reduce_range=False)",
           "export": "torch.onnx.export(dynamo=False, opset 17); inputs input_ids, attention_mask; output logits [batch, 2]"
                     + (" (HHEM: first token)" if first_token else ""),
           "check": check, "pip_freeze": freeze, "python": sys.version.split()[0]}
    json.dump(out, open(os.path.join(a.dir, "export.json"), "w"), indent=1)
    print(json.dumps({k: out[k] for k in ("model", "convert_seconds", "outputs", "check")}, indent=1))


if __name__ == "__main__":
    main()
