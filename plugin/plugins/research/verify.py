"""Check whether a cited passage supports a sentence, with a small local NLI model (no PyTorch; onnxruntime only).

Model: cross-encoder/nli-deberta-v3-xsmall (Apache-2.0), int8 ONNX, pinned revision a150876, sha256-checked, 87 MB, in
D:\\jarvis\\models\\nli. Labels: contradiction, entailment, neutral. A sentence counts as supported when one of its cited
passages entails it, and every number in it appears in that passage (models are weak at numbers).
Measured choice (2026-10-16): the Jev decision model answered "supported" for every claim (8/16), so it is not used here.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

import numpy as np

MODEL_DIR = os.environ.get("RESEARCH_NLI_DIR", r"D:\jarvis\models\nli\nli-deberta-v3-xsmall")
ENTAIL_MIN = 0.5
# A window is one of many tries per passage, so it must be near certain: on the labelled set
# (tests/fixtures/nli_pairs.json, 36 pairs, 2026-10-16) windows at 0.95 confirmed 18/18 true claims (13/18 without) and
# accepted 2/18 false ones (1/18 without); at 0.5 they accepted 3/18.
WINDOW_MIN = 0.95
MAX_TOKENS = 384


@lru_cache(maxsize=1)
def _load():
    import onnxruntime as ort
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file(os.path.join(MODEL_DIR, "tokenizer.json"))
    tok.enable_truncation(MAX_TOKENS)
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 4
    sess = ort.InferenceSession(os.path.join(MODEL_DIR, "onnx", "model_quint8_avx2.onnx"), opts, providers=["CPUExecutionProvider"])
    return tok, sess, {i.name for i in sess.get_inputs()}


def available() -> bool:
    return os.path.isfile(os.path.join(MODEL_DIR, "onnx", "model_quint8_avx2.onnx"))


def entailment(premise: str, hypothesis: str) -> float:
    tok, sess, names = _load()
    enc = tok.encode(premise, hypothesis)
    feeds = {"input_ids": np.array([enc.ids], dtype=np.int64), "attention_mask": np.array([enc.attention_mask], dtype=np.int64)}
    if "token_type_ids" in names:
        feeds["token_type_ids"] = np.array([enc.type_ids], dtype=np.int64)
    logits = sess.run(None, feeds)[0][0]
    p = np.exp(logits - logits.max())
    p /= p.sum()
    return float(p[1])


_NUM = re.compile(r"(?<![\d.])\d+(?:\.\d+)*")


def _numbers(text: str) -> set[str]:
    """Whole numbers in a text ('1,500' -> 1500, '3.50' == '3.5'; '3.11.9' stays a version string)."""
    out = set()
    for n in _NUM.findall(re.sub(r"(?<=\d),(?=\d{3}\b)", "", text or "")):
        out.add(repr(float(n)) if n.count(".") <= 1 else n)
    return out


def numbers_match(sentence: str, passage: str) -> bool:
    """Every number in the sentence is a number in the passage. Compared as whole numbers: '5' inside '15' or '2025'
    used to count as a match, so a sentence could change a figure and still pass."""
    return _numbers(sentence) <= _numbers(passage)


_MARK = re.compile(r"\[(?:\d{1,3}|[a-z]|citation needed|note \d+)\]", re.I)    # wiki-style reference markers
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def windows(passage: str) -> list[str]:
    """The passage's sentences and pairs of neighbouring sentences, reference markers removed. The small checker misses
    support that sits inside a longer passage (measured 2026-10-16: 0.21 on the whole 700-character passage, 0.99 on the
    sentence that says it); a window is part of the passage, so what a window entails, the passage says."""
    sents = [x.strip() for x in _SENT.split(_MARK.sub("", passage or "")) if x.strip()]
    return sents + [a + " " + b for a, b in zip(sents, sents[1:])] if len(sents) > 1 else []


def supported(sentence: str, passages: list[str]) -> tuple[bool, float]:
    """(supported, best entailment probability) over the passages the sentence cites: each whole passage, then its
    sentence windows. The numbers in the sentence must appear in the same text that entails it."""
    best, ok = 0.0, False
    for p in passages:
        e = entailment(p, sentence)
        best = max(best, e)
        if e >= ENTAIL_MIN and numbers_match(sentence, p):
            return True, e
        for w in windows(p):
            e = entailment(w, sentence)
            best = max(best, e)
            if e >= WINDOW_MIN and numbers_match(sentence, w):
                return True, e
    if len(passages) > 1:                                    # a sentence may combine what two passages say
        joined = " ".join(passages)
        e = entailment(joined, sentence)
        best = max(best, e)
        ok = e >= ENTAIL_MIN and numbers_match(sentence, joined)
    return ok, best
