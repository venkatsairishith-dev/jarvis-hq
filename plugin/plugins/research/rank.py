"""Split pages into passages and keep the ones that best match the question (BM25; no model needed)."""
from __future__ import annotations

import math
import re
from collections import Counter

STOP = set("""a an and are as at be but by for from has have how i in is it its of on or that the this to was were what when
where which who why will with you your can do does should would about into than then there these those our we they them
their more most best use using""".split())
AUTHORITY = (".gov", ".edu", ".mil", ".int", "wikipedia.org", "nist.gov", "owasp.org", "who.int", "europa.eu", "ietf.org",
             "w3.org", "python.org", "microsoft.com/learn", "learn.microsoft.com", "developer.mozilla.org", "docs.")


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9\-]+", (text or "").lower()) if w not in STOP]


def chunks(text: str, size: int = 700) -> list[str]:
    """Passages of about `size` characters, cut at line or sentence ends."""
    out, cur = [], ""
    for piece in re.split(r"(?<=[.!?])\s+|\n", text or ""):
        piece = piece.strip()
        if not piece:
            continue
        if len(cur) + len(piece) + 1 > size and cur:
            out.append(cur)
            cur = piece
        else:
            cur = (cur + " " + piece).strip()
    if cur:
        out.append(cur)
    return [c for c in out if len(c) >= 80]


def bm25(query: str, docs: list[str], k1: float = 1.4, b: float = 0.75) -> list[float]:
    q = words(query)
    toks = [words(d) for d in docs]
    n = len(docs) or 1
    avg = sum(len(t) for t in toks) / n or 1
    df = Counter(w for t in toks for w in set(t))
    scores = []
    for t in toks:
        tf = Counter(t)
        s = 0.0
        for w in q:
            if w in tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(t) / avg))
        scores.append(s)
    return scores


def authority(url: str) -> float:
    u = (url or "").lower()
    return 0.3 if any(a in u for a in AUTHORITY) else 0.0


def best_passages(query: str, pages: list[dict], per_page: int = 3, total: int = 18) -> list[dict]:
    """[{page index, text, score}] the strongest passages, at most per_page from one page."""
    cands = []
    for i, p in enumerate(pages):
        cs = chunks(p["text"])
        if not cs:
            continue
        sc = bm25(query, cs)
        top = sorted(zip(sc, cs), key=lambda x: -x[0])[:per_page]
        cands += [{"page": i, "text": c, "score": s * (1 + authority(p["url"]))} for s, c in top if s > 0]
    cands.sort(key=lambda c: -c["score"])
    return cands[:total]
