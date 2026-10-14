"""Score Telugu-script speech-recognition output against a romanized (Latin-script) reference text. Windows or any OS.

    python asr_score.py --hyp hypothesis_te.txt --ref reference_latin.txt

Compare Telugu-script ASR output with a romanized (Latin-script) reference.

Both are put on a rough phonetic footing (romanization of Telugu is inconsistent: aa/a, ee/e, th/t, dh/d ...), then scored:
  cer_full   character error rate on the normalized string
  cer_cons   character error rate on the consonant skeleton (vowels dropped, robust to spelling variation)
  recall     share of reference words whose consonant skeleton is matched (>= 0.75 similarity) by some hypothesis word
Absolute numbers are approximate; the point is a fair, repeatable ranking of variants.
"""
import re

import numpy as np

V_IND = {"అ": "a", "ఆ": "aa", "ఇ": "i", "ఈ": "ii", "ఉ": "u", "ఊ": "uu", "ఋ": "ru", "ఎ": "e", "ఏ": "ee", "ఐ": "ai",
         "ఒ": "o", "ఓ": "oo", "ఔ": "au"}
V_SIGN = {"ా": "aa", "ి": "i", "ీ": "ii", "ు": "u", "ూ": "uu", "ృ": "ru", "ె": "e", "ే": "ee", "ై": "ai", "ొ": "o",
          "ో": "oo", "ౌ": "au"}
CONS = {"క": "k", "ఖ": "kh", "గ": "g", "ఘ": "gh", "ఙ": "ng", "చ": "ch", "ఛ": "chh", "జ": "j", "ఝ": "jh", "ఞ": "ny",
        "ట": "t", "ఠ": "th", "డ": "d", "ఢ": "dh", "ణ": "n", "త": "t", "థ": "th", "ద": "d", "ధ": "dh", "న": "n",
        "ప": "p", "ఫ": "ph", "బ": "b", "భ": "bh", "మ": "m", "య": "y", "ర": "r", "ఱ": "r", "ల": "l", "ళ": "l",
        "వ": "v", "శ": "sh", "ష": "sh", "స": "s", "హ": "h"}


def te_to_latin(s):
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        if ch in CONS:
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if nxt in V_SIGN:
                out.append(CONS[ch] + V_SIGN[nxt])
                i += 2
                continue
            if nxt == "్":
                out.append(CONS[ch])
                i += 2
                continue
            out.append(CONS[ch] + "a")
        elif ch in V_IND:
            out.append(V_IND[ch])
        elif ch == "ం":
            out.append("m")
        elif ch == "ః":
            out.append("h")
        elif ch == "ఁ":
            out.append("n")
        elif ch.isspace():
            out.append(" ")
        elif ch.isalnum() and ord(ch) < 128:
            out.append(ch)
        i += 1
    return "".join(out)


def norm(latin):
    s = latin.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    for a, b in (("chh", "C"), ("ch", "C"), ("sh", "s"), ("kh", "k"), ("gh", "g"), ("th", "t"), ("dh", "d"), ("ph", "p"),
                 ("bh", "b"), ("jh", "j"), ("ny", "n"), ("ng", "n"), ("ck", "k"), ("x", "ks"), ("q", "k"), ("w", "v"),
                 ("z", "j"), ("c", "k"), ("f", "p")):
        s = s.replace(a, b)
    for a, b in (("aa", "a"), ("ii", "i"), ("uu", "u"), ("ee", "e"), ("oo", "o"), ("ou", "au"), ("ey", "e"), ("ay", "ai")):
        s = s.replace(a, b)
    s = re.sub(r"(.)\1+", r"\1", s)                # doubled letters
    s = re.sub(r"[0-9]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def cons(s):
    return re.sub(r"[aeiou ]", "", s)


def lev(a, b):
    """Levenshtein distance, vectorized per row."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    A = np.frombuffer(a.encode("utf-32-le"), dtype=np.uint32)
    B = np.frombuffer(b.encode("utf-32-le"), dtype=np.uint32)
    prev = np.arange(len(B) + 1, dtype=np.int32)
    idx = np.arange(len(B) + 1, dtype=np.int32)
    for i in range(1, len(A) + 1):
        cost = (B != A[i - 1]).astype(np.int32)
        cur = np.empty_like(prev)
        cur[0] = i
        cur[1:] = np.minimum(prev[1:] + 1, prev[:-1] + cost)
        cur = np.minimum.accumulate(cur - idx) + idx
        prev = cur
    return int(prev[-1])


def sim(a, b):
    m = max(len(a), len(b))
    return 1.0 if m == 0 else 1.0 - lev(a, b) / m


def score(hyp_te, ref_latin):
    h, r = norm(te_to_latin(hyp_te)), norm(ref_latin)
    cer_full = lev(h.replace(" ", ""), r.replace(" ", "")) / max(len(r.replace(" ", "")), 1)
    hc, rc = cons(h), cons(r)
    cer_cons = lev(hc, rc) / max(len(rc), 1)
    hw = [cons(w) for w in h.split() if cons(w)]
    rw = [cons(w) for w in r.split() if cons(w)]
    hit = 0
    for w in rw:
        if len(w) <= 1:
            hit += 1 if w in hw else 0
            continue
        if any(abs(len(w) - len(x)) <= 2 and sim(w, x) >= 0.75 for x in hw):
            hit += 1
    return {"cer_full": round(cer_full, 3), "cer_cons": round(cer_cons, 3), "recall": round(hit / max(len(rw), 1), 3),
            "hyp_words": len(h.split()), "ref_words": len(r.split())}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Score Telugu ASR output against a romanized reference")
    ap.add_argument("--hyp", required=True, help="text file with the recognizer output (Telugu script)")
    ap.add_argument("--ref", required=True, help="text file with the reference (Latin script)")
    a = ap.parse_args()
    hyp = open(a.hyp, encoding="utf-8").read()
    ref = open(a.ref, encoding="utf-8").read()
    print(score(hyp, ref))


if __name__ == "__main__":
    main()
