"""Intent-routing evaluation: which method gives the best first guess of what a JARVIS request is?

Compares, on the held-out TEST split of data/intent_set.jsonl (fit on train, choose settings on val, never touch test):
  rules          hand-written keyword rules (no model)
  tfidf-knn      word + character n-gram TF-IDF, cosine nearest neighbours on train
  bge-lr         bge-small-en-v1.5 embeddings (llama.cpp) + softmax regression
  qwen3-*        small chat model, zero-shot, JSON answer (plain prompt; grammar did not help in the research notes)
  verdict-1.4    OpenJev typed choice, 13 options          laya-1.0   OpenJev typed choice, 13 options
Reports accuracy, macro-F1, per-category recall, the confusion matrix, schema-valid rate, latency, and the two numbers the
kernel cares about: BLOCKED requests that were not called blocked (false-allow) and benign ones that were (over-block).
A method that is unreachable is reported as not run, with the reason; nothing is invented.
Usage: python run_intent_eval.py [--qwen http://127.0.0.1:8098=qwen3-4b] [--verdict URL] [--laya URL] [--embed URL]
"""
import json
import math
import os
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from make_intent_set import CATEGORIES  # noqa: E402

sys.path.insert(0, os.path.join(HERE, "..", "console"))
from jarvis_console.intent import DEFS, RULES, PROMPT, rules_predict  # noqa: E402  (one definition, used by the console too)


def load():
    rows = [json.loads(l) for l in open(os.path.join(HERE, "data", "intent_set.jsonl"), encoding="utf-8")]
    return {s: [r for r in rows if r["split"] == s] for s in ("train", "val", "test")}


# ---------------------------------------------------------------- methods
def ngrams(text):
    t = " " + re.sub(r"\s+", " ", text.lower()) + " "
    w = re.findall(r"\w+", t)
    out = [("w", x) for x in w] + [("b", a + " " + b) for a, b in zip(w, w[1:])]
    out += [("c", t[i:i + 3]) for i in range(len(t) - 2)]
    return out


class Tfidf:
    def __init__(self, train):
        self.df, self.n = Counter(), len(train)
        for r in train:
            self.df.update(set(ngrams(r["text"])))
        self.vecs = [(self.vec(r["text"]), r["label"]) for r in train]

    def vec(self, text):
        c = Counter(ngrams(text))
        v = {k: (1 + math.log(n)) * math.log((self.n + 1) / (self.df.get(k, 0) + 1) + 1) for k, n in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1
        return {k: x / norm for k, x in v.items()}

    def predict(self, text, k=3):
        q = self.vec(text)
        sims = sorted(((sum(w * v.get(f, 0) for f, w in q.items()), lab) for v, lab in self.vecs), reverse=True)[:k]
        votes = Counter()
        for s, lab in sims:
            votes[lab] += s
        return votes.most_common(1)[0][0]


def http_json(url, body, timeout=120, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **(headers or {})})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    return out, (time.perf_counter() - t0) * 1000


def embed(url, texts):
    vecs = []
    for t in texts:
        o, _ = http_json(url.rstrip("/") + "/v1/embeddings", {"input": t, "model": "bge"})
        v = np.array(o["data"][0]["embedding"], dtype="float32")
        vecs.append(v / (np.linalg.norm(v) or 1))
    return np.stack(vecs)


def softmax_regression(X, y, n_cls, l2=1e-3, lr=0.5, epochs=400):
    W = np.zeros((X.shape[1], n_cls), "float32")
    Y = np.eye(n_cls, dtype="float32")[y]
    for _ in range(epochs):
        z = X @ W
        z -= z.max(axis=1, keepdims=True)
        p = np.exp(z)
        p /= p.sum(axis=1, keepdims=True)
        W -= lr * (X.T @ (p - Y) / len(X) + l2 * W)
    return W


def chat_predict(url, model, text):
    prompt = PROMPT.format(defs="\n".join(f"- {k}: {v}" for k, v in DEFS.items()), names=", ".join(CATEGORIES), text=text)
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0, "max_tokens": 40}
    o, ms = http_json(url.rstrip("/") + "/v1/chat/completions", body)
    raw = (o["choices"][0]["message"].get("content") or "").strip()
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
    m = re.search(r"\{.*?\}", raw, flags=re.S)
    try:
        cat = json.loads(m.group(0)).get("category") if m else None
    except ValueError:
        cat = None
    return (cat if cat in CATEGORIES else None), ms, (o.get("usage") or {}).get("prompt_tokens", 0)


def jev_predict(url, model, text):
    body = {"model": model, "state": text, "questions": {"intent": {"type": "choice", "instructions": "What kind of request is this for a personal AI assistant?",
                                                                    "criteria": {k: v for k, v in DEFS.items()}}}}
    o, ms = http_json(url.rstrip("/") + "/v1/systemone", body)
    a = (o.get("answers") or o.get("choices") or {}).get("intent", {})
    c = a.get("choice")
    return (c if c in CATEGORIES else None), ms, (o.get("usage") or {}).get("input_tokens", 0)


# ---------------------------------------------------------------- scoring
def score(truth, pred, lat=None):
    n = len(truth)
    valid = [p is not None for p in pred]
    pr = [p or "clarify" for p in pred]           # an unparseable answer is treated as "ask back": the safe fallback
    acc = sum(a == b for a, b in zip(truth, pr)) / n
    f1s, per = [], {}
    for c in CATEGORIES:
        tp = sum(1 for t, p in zip(truth, pr) if t == c and p == c)
        fp = sum(1 for t, p in zip(truth, pr) if t != c and p == c)
        fn = sum(1 for t, p in zip(truth, pr) if t == c and p != c)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else None
        per[c] = {"n": tp + fn, "recall": None if rec is None else round(rec, 2)}
        if tp + fn:
            f1s.append(0 if prec + (rec or 0) == 0 else 2 * prec * (rec or 0) / (prec + (rec or 0)))
    blocked = [(t, p) for t, p in zip(truth, pr) if t == "blocked"]
    benign = [(t, p) for t, p in zip(truth, pr) if t != "blocked"]
    conf = defaultdict(Counter)
    for t, p in zip(truth, pr):
        conf[t][p] += 1
    out = {"n": n, "accuracy": round(acc, 3), "macro_f1": round(sum(f1s) / len(f1s), 3), "schema_valid": round(sum(valid) / n, 3),
           "blocked_missed": f"{sum(1 for t, p in blocked if p != 'blocked')}/{len(blocked)}",
           "benign_blocked": f"{sum(1 for t, p in benign if p == 'blocked')}/{len(benign)}",
           "per_category": per, "confusion": {t: dict(c) for t, c in conf.items()}}
    if lat:
        s = sorted(lat)
        out["latency_ms"] = {"median": round(s[len(s) // 2]), "p95": round(s[min(len(s) - 1, int(len(s) * 0.95))])}
    return out


def main():
    args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a and a.startswith("--"))
    args = {k.lstrip("-"): v for k, v in args.items()}
    d = load()
    test = d["test"]
    truth = [r["label"] for r in test]
    results = {"dataset": {k: len(v) for k, v in d.items()}, "methods": {}}

    def run(name, fn, size=None, note=None):
        try:
            res = fn()
            if size:
                res["model_size"] = size
            if note:
                res["note"] = note
            results["methods"][name] = res
            print(f"{name:14s} acc {res['accuracy']:.2f}  macroF1 {res['macro_f1']:.2f}  valid {res['schema_valid']:.2f}  blocked missed {res['blocked_missed']}  "
                  f"benign blocked {res['benign_blocked']}  {res.get('latency_ms', '')}")
        except Exception as e:
            results["methods"][name] = {"not_run": f"{type(e).__name__}: {str(e)[:120]}"}
            print(f"{name:14s} NOT RUN: {type(e).__name__}: {str(e)[:100]}")

    run("rules", lambda: score(truth, [rules_predict(r["text"]) for r in test]), "0 (regex)")
    tf = Tfidf(d["train"])
    run("tfidf-knn", lambda: score(truth, [tf.predict(r["text"]) for r in test]), "0 (n-gram index)")
    if args.get("embed"):
        def bge():
            Xtr, Xte = embed(args["embed"], [r["text"] for r in d["train"]]), embed(args["embed"], [r["text"] for r in test])
            ytr = np.array([CATEGORIES.index(r["label"]) for r in d["train"]])
            W = softmax_regression(Xtr, ytr, len(CATEGORIES))
            pred = [CATEGORIES[i] for i in (Xte @ W).argmax(axis=1)]
            return score(truth, pred)
        run("bge-lr", bge, "33M (bge-small-en-v1.5 q8) + 13x384 head")
    for key, label in (("qwen", "qwen"), ("verdict", "verdict-1.4"), ("laya", "laya-1.0")):
        spec = args.get(key)
        if not spec:
            continue
        url, _, model = spec.partition("|")
        model = model or label

        def llm(url=url, model=model, key=key):
            preds, lat = [], []
            fn = chat_predict if key == "qwen" else jev_predict
            for r in test:
                p, ms, _ = fn(url, model, r["text"])
                preds.append(p)
                lat.append(ms)
            return score(truth, preds, lat)
        run(model, llm, {"qwen": "see model file", "verdict": "151M", "laya": "421M"}[key])
    out = os.path.join(HERE, "data", "intent_results.json")
    json.dump(results, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("wrote", out)


if __name__ == "__main__":
    main()
