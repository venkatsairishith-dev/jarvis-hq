# Research

`research_topic(question, depth=quick|standard, deliverable=none|report|presentation, open_after)`

**How a run works:**
1. **Redact:** the question goes through the privacy gateway's redactor, so nothing private ends up in a search query.
2. **Plan:** the local generalist writes up to 4 search queries (constrained JSON). If that fails, the question itself is the query.
3. **Search:** the face's own `web_search` plugin (AnySearch, with DuckDuckGo fallback, rate-limited and cached).
   Tavily is asked only when its key file exists and `web_search` fails or finds fewer than half the wanted results.
   Junk search items (a string, `null`, a URL that is not text) are skipped; the good results after them are kept.
   A Tavily reply of the wrong shape counts as no results.
4. **Read:** pages are fetched with limits:
   - public addresses only, re-checked on every redirect;
   - http(s) on ports 80 and 443;
   - at most 3 redirects, 1.5 MB, 15 s per page;
   - a 24 h cache in `D:\jarvis\cache\research`;
   - scripts, navigation, forms and text hidden by attribute or inline style are dropped;
   - sentences addressed to an AI reader ("ignore your instructions", "note to AI assistants") are removed from every
     page (`verify.strip_injected`). They are matched on NFKC text with zero-width characters and soft hyphens removed,
     so full-width letters and invisible characters do not hide them. A page or search title like that is replaced by
     the host name. `notes` says how many sentences were removed.
5. **Rank:** BM25 passages, at most 3 per page, with a small boost for authoritative domains.
6. **Write:** Dahl's MiniMax, through the privacy gateway (streamed), writes JSON in which every factual sentence carries `[S#]`. If that fails, the local generalist writes it.
7. **Check:** a local NLI model (`cross-encoder/nli-deberta-v3-xsmall`, int8 ONNX, loaded from `RESEARCH_NLI_DIR`, default `D:\jarvis\models\nli\nli-deberta-v3-xsmall`) tests each cited sentence against the passages it cites. The model is weak at details, so `verify.py` adds guards; each one can only reject:
   - **numbers:** every number in the sentence must be in the source, as a whole number (`5` does not match `15`);
   - **units:** a number with a unit needs the same unit in the source (330 metres is not 330 feet; Wh/kg is not kWh/kg);
   - **qualifiers:** a superlative may not drop the source's restriction ("largest inland city" does not confirm "largest city");
   - **comparators:** "more than", "at least", "up to" and the like must match the source's bound on that number;
   - **hedges:** a source that says "may", "could" or "suggests" does not confirm the same statement said as certain.
   - A sentence its own citation does not support is tried against the other sources; if one supports it, the citation is corrected.
   - Unsupported, uncited or wrongly cited factual sentences move to "Statements that could not be confirmed from the sources" (uncertain).
   - Fact sentences that give different figures for the same point from different sources go to "Sources disagree"; neither counts as confirmed.
   - Opinion and recommendation text may stand without a citation.
8. **Deliver:** the report spec goes to `deliverables` as a PDF, or as a deck built from it deterministically.
   - An oversized writer reply is capped, not a failed deliverable: at most 28 sections and a 3000-character summary.
     `notes` says how much was left out.
   - Long statements are cut to fit a slide bullet (220 characters); their citation markers are kept.

**Budgets:**
- quick: 2 queries, 4 pages, ~3 min.
- standard: 4 queries, 8 pages, ~7 min.

When a budget stops the run, the result says so in `notes`.

**Measured (2026-10-16):** the NLI check got 16 of 16 on a small hand-made set of supported and unsupported claims, at ~0.1 s per check. The Jev decision model said "supported" for every claim (8 of 16), so it is not used here.

**Measured (2026-10-17):** `jarvis/evals/quality95/bench/claims_eval.py` runs `verify.supported` on labelled (passage, claim) sets in `tests/fixtures` and reports precision, recall, false acceptance (an unsupported claim confirmed: the serious error), false rejection and a calibration table. Results go to `jarvis/evals/quality95/results/claims-<set>-<time>.json`.

| Set | False acceptance | Recall |
|---|---|---|
| `claims_v2` (78 pairs, written before any change was measured on it) | 2/46 (was 9/46 before the guards) | 0.938 |
| `nli_pairs` (36 pairs) | 1/18 | 18/18 |
| `vitaminc_sample` (400 VitaminC test pairs, held out) | 10/200 | 0.44 |

The guards do not help much on VitaminC: low recall there means many true claims stay "unconfirmed", which is the safe side. Larger NLI models accepted more false claims, so xsmall is kept.

```
RESEARCH_NLI_DIR=<model dir> python jarvis/evals/quality95/bench/claims_eval.py --set claims_v2
```

**Tests:** `pytest plugin/plugins/research/tests` (fakes only; no network). The test that uses the real NLI model is skipped unless `RESEARCH_NLI_DIR` points at the model files.
