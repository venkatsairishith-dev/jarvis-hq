# Research

`research_topic(question, depth=quick|standard, deliverable=none|report|presentation, open_after)`

**How a run works:**
1. **Redact:** the question goes through the privacy gateway's redactor, so nothing private ends up in a search query.
2. **Plan:** the local generalist writes up to 4 search queries (constrained JSON). If that fails, the question itself is the query.
3. **Search:** N.E.K.O's own `web_search` plugin (AnySearch, with DuckDuckGo fallback, rate-limited and cached).
4. **Read:** pages are fetched with limits:
   - public addresses only, re-checked on every redirect;
   - http(s) on ports 80 and 443;
   - at most 3 redirects, 1.5 MB, 15 s per page;
   - a 24 h cache in `D:\jarvis\cache\research`.
   Scripts, navigation and forms are dropped.
5. **Rank:** BM25 passages, at most 3 per page, with a small boost for authoritative domains.
6. **Write:** Dahl's MiniMax, through the privacy gateway (streamed), writes JSON in which every factual sentence carries `[S#]`. If that fails, the local generalist writes it.
7. **Check:** a local NLI model (`cross-encoder/nli-deberta-v3-xsmall`, int8 ONNX) tests each cited sentence against the passages it cites, and every number must appear in the source.
   - Unsupported, uncited or wrongly cited factual sentences move to "Statements that could not be confirmed from the sources" (uncertain).
   - Opinion and recommendation text may stand without a citation.
8. **Deliver:** the report spec goes to `deliverables` as a PDF, or as a deck built from it deterministically.

**Budgets:**
- quick: 2 queries, 4 pages, ~3 min.
- standard: 4 queries, 8 pages, ~7 min.

When a budget stops the run, the result says so in `notes`.

**Measured (2026-10-16):** the NLI check got 16 of 16 on a small hand-made set of supported and unsupported claims, at ~0.1 s per check. The Jev decision model said "supported" for every claim (8 of 16), so it is not used here.

**Tests:** `pytest plugin/plugins/research/tests` (fakes only; no network).
