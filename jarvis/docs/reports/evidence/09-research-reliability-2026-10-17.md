# Making the research plugin more reliable: literature and model survey

Date: 2026-10-17. Web research only. Nothing was installed or run. Every number below comes from the cited
source (paper, model card or leaderboard). Numbers marked "estimate" are mine and still need measuring here.

Current pipeline (for reference): LLM plans queries -> DDG/Tavily search -> fetch (no JS) -> ~700-char passages,
BM25 + authority boost -> LLM writer emits JSON statements citing source ids -> each cited sentence checked by
cross-encoder/nli-deberta-v3-xsmall (ONNX int8): entail >= 0.5 on the passage, or >= 0.95 on a 1-2 sentence window, and
every number must appear literally -> failures go to "unconfirmed"; differing numbers across sources go to "Sources disagree".
Measured here: 18/18 true claims confirmed and 2/18 false claims accepted on 36 pairs; about 75% of cited sentences confirmed in live runs.

---

## 1. Claim decomposition (atomic facts)

| Work | What it is | Who measured what |
|---|---|---|
| FActScore (Min et al., EMNLP 2023) https://arxiv.org/abs/2305.14251 | Split text into atomic facts and check each one against retrieved Wikipedia with an LM | Authors: automated estimator has an error rate under 2% against humans; ChatGPT scores 58% on biographies |
| SAFE / LongFact (Wei et al., Google DeepMind, 2024) https://arxiv.org/abs/2403.18802 | LLM splits the answer into facts, then runs a multi-step Google Search check on each | Authors: agrees with crowd annotators on 72% of about 16k facts; judged correct in 76% of 100 sampled disagreements; more than 20x cheaper than humans |
| VeriScore (Song, Kim, Iyyer, Findings EMNLP 2024) https://arxiv.org/abs/2406.19276 | Extracts only *verifiable* claims, keeping qualifiers such as time and place | Authors: humans judged its extracted claims more sensible than other methods' across 8 tasks |
| Claimify (Metropolitansky & Larson, Microsoft, ACL 2025) https://arxiv.org/abs/2502.10855 | Selection -> disambiguation -> decomposition. It *drops* sentences whose meaning it cannot resolve, and brackets any context it inferred | Authors (GPT-4o): 99.0% of claims entailed by the source (VeriScore 99.2%); best sentence-level coverage (91.8%) and element-level coverage (87.9%) |
| **Decomposition Dilemmas (Hu, Long, Wang, NAACL 2025)** https://arxiv.org/abs/2411.02400 | Tests decompose-then-verify across verifiers of different strength | Authors: **decomposition helps weak verifiers and hurts strong ones.** WICE: AlignScore BAcc 54.8 -> 56.3 to 56.9 (helps); MiniCheck 80.0 -> 76.8 at best, 71.1 with FActScore-style splitting (hurts). Error types: dropped context, ambiguity, over-splitting, changed meaning |
| MiniCheck paper section 7.1 (Tang, Laban, Durrett, EMNLP 2024) https://arxiv.org/abs/2404.10774 | Tried atomic-fact decomposition on top of MiniCheck | Authors: no consistent accuracy gain, and 2-4x the inference cost and time. They advise against it |
| Alignment bottleneck (arXiv 2602.10380, 2026) https://arxiv.org/html/2602.10380v1 | When decomposition helps | Authors: it helps only when each sub-claim gets its own evidence (+0.06 macro-F1); repeating claim-level evidence for each sub-claim can hurt (-0.07 on MMM-Fact) |

**What this means for us:** with the xsmall NLI model, decomposition probably helps a little. With a MiniCheck- or
FactCG-class checker it probably hurts, except on long compound sentences. The cheapest form of decomposition happens at
the source: tell the writer to put one fact per sentence and to keep every qualifier ("inland", "in 2023", "per capita").
Claimify's rule fits our "unconfirmed" bucket: if the meaning is ambiguous, don't assert it.

## 2. Grounding / fact-checking models

The LLM-AggreFact leaderboard (https://llm-aggrefact.github.io/) has 11 human-labelled datasets covering summaries, RAG
and long-form QA. The metric is balanced accuracy (BAcc).

| Model | Size | Licence | LLM-AggreFact BAcc (source) | CPU / ONNX notes |
|---|---|---|---|---|
| **FactCG-DeBERTa-v3-Large** (`yaxili96/FactCG-DeBERTa-v3-Large`), NAACL 2025 https://arxiv.org/abs/2501.17144, code https://github.com/derenlei/FactCG | 0.4B | Code is MIT; the HF card states no licence for the weights | **75.6** at threshold 0.5; 77.2 with per-dataset thresholds (paper; leaderboard shows 75.6) | Standard `AutoModelForSequenceClassification` (2 labels), so optimum ONNX export should work like our current model. Chunks of about 550 tokens; final score = max over chunks. No CPU latency reported |
| MiniCheck-Flan-T5-L (`lytang/MiniCheck-Flan-T5-Large`) | 0.8B | MIT | 75.0 (leaderboard); 74.7 in paper Table 2 | Community ONNX exists (`FamiliarTools/MiniCheck-Flan-T5-Large-onnx`: 1.3 GB encoder + 1.8 GB decoder, fp32, unverified third-party upload) |
| MiniCheck-RoBERTa-L / DeBERTa-v3-L (`lytang/MiniCheck-DeBERTa-v3-Large`) | 0.4B | MIT | 72.7 / 72.6 (paper); 73.5 / 73.1 (FactCG paper's rerun) | Encoder classifiers, easy to export. Card: about 800 docs/min on GPU |
| Bespoke-MiniCheck-7B | 7-8B (InternLM2.5 base) | **CC BY-NC 4.0** (non-commercial) | **77.4**, top of the leaderboard | 32K context; vLLM only; no GGUF listed. Too heavy for a capped CPU box |
| Granite Guardian 3.3 | 8B | Apache-2.0 (IBM) | 76.5 (leaderboard) | Also covers prompt-risk detection; 8B is heavy on CPU |
| HHEM-2.1-Open (`vectara/hallucination_evaluation_model`) https://huggingface.co/vectara/hallucination_evaluation_model | 0.11B (Flan-T5-base) | Apache-2.0 | Not reported on LLM-AggreFact. Vectara reports 76.55% on AggreFact-SOTA (the older summary benchmark), 64.4% on RAGTruth-Summ and 74.3% on RAGTruth-QA | **Vectara: about 1.5 s per 2k-token input on a modern x86 CPU, under 600 MB RAM (fp32).** Community ONNX at `FamiliarTools/HHEM-2.1-Open-onnx`. Needs `trust_remote_code` |
| AlignScore-large / base https://github.com/yuh-zha/AlignScore | 355M / 125M (RoBERTa) | MIT | 70.4 (MiniCheck paper) | 350-token chunks, spaCy sentence split, max over chunks per sentence, then mean |
| SummaC-ZS / SummaC-Conv | NLI-based | not checked | 67.9 / 62.1 (MiniCheck paper) | Older; behind the models above |
| TrueTeacher (`google/t5_11b_trueteacher_and_anli`) | 11B | CC BY-NC 4.0 | T5-NLI-Mixed scored 61.0 in the MiniCheck paper | Too large; summary-oriented |
| `MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli` | 0.4B | MIT | Not on LLM-AggreFact. Card: MNLI 91.2, ANLI 70.2, WANLI 77 | Card tags it ONNX-compatible. Plain sentence-level NLI, not trained on document-level grounding |
| LettuceDetect (ModernBERT) https://arxiv.org/abs/2502.17125 | about 150-400M | MIT code | Evaluated on RAGTruth only (token-level spans), not LLM-AggreFact | 4k context; finds unsupported spans |
| Our current model: `cross-encoder/nli-deberta-v3-xsmall` | about 70M (22M backbone) | Apache-2.0 | Not reported | Trained on SNLI+MNLI only, with no fact-verification or contrastive data |

Reference points from the same papers: GPT-4 scores 75.3 (MiniCheck paper) and GPT-4o 75.9 (FactCG paper). A 0.4B
FactCG matches GPT-4o here.

CPU latency: none of the encoder models above publish CPU numbers. **Estimate:** a 0.4B DeBERTa-v3-large does about
10-14x the backbone compute of xsmall per token. Expect roughly 0.3-1 s per 512-token pair at int8 on 6 threads. This
must be measured on this box before switching.

## 3. Qualifiers, negation, numbers, units

- **Known weak spots.** Classic NLI stress tests (Naik et al. 2018, https://arxiv.org/abs/1806.00692) found that negation
  words push models toward predicting "contradiction". NaN-NLI (https://arxiv.org/abs/2210.03256) found negation with
  quantifiers hardest. Our "largest city" vs "largest inland city" failure is the reverse case: the claim drops a
  *restrictive modifier*, and the claim is more general than the evidence supports.
- **Contrastive training data works.** VitaminC (Schuster et al., NAACL 2021, https://arxiv.org/abs/2103.08541) has over
  400k claim-evidence pairs built from Wikipedia edits that change one fact. Training on it improved adversarial fact
  verification by about 10% and adversarial NLI by about 6% (authors). MiniCheck and FactCG train on synthetic "minimal
  error" data for the same reason.
- **Numerical claims are hard even for good systems.** QuanTemp (SIGIR 2024, https://arxiv.org/abs/2403.17169) has
  15,514 real numerical claims; the best baseline reached only 58.32 macro-F1. The authors found that claim
  decomposition, MNLI pre-fine-tuning and number-aware models all help.
- **Practical techniques (deterministic, take milliseconds):**
  1. *Quantity normalisation* instead of literal number matching. `quantulum3` (MIT, https://github.com/nielstron/quantulum3)
     extracts quantities, units, ranges and "±" values from text; `pint` converts units. Compare normalised values with
     a tolerance, and treat ranges and "about" sensibly. This fixes "1,000 vs 1000", "5 km vs 5,000 m" and "3.2 million vs 3,200,000".
  2. *Modifier/qualifier diff:* when the evidence span that best matches the claim has a superlative or a restrictive
     modifier (inland, per capita, non-, excluding, among X, in <year>, at least, about) that the claim lacks, or the
     reverse, mark the claim "unconfirmed" instead of "confirmed". The same idea works for negation (claim and evidence
     differ in not/no/never) and comparative direction (higher vs lower).
  3. *Contrastive self-check:* also score a minimally edited counter-claim (drop or negate the qualifier, swap
     higher/lower). Accept only if the original scores clearly higher than the counter-claim. This costs one more checker
     call per risky sentence. Please treat it as a sensible heuristic: I found no paper that measured it for this exact use.
  4. *Entity/number alignment* (in the spirit of QAGS/QAFactEval): every named entity and date in the claim must appear,
     or be normalised to something that appears, in the evidence window.

## 4. Source quality

- **LLMs ignore credibility cues.** Wan, Wallace & Klein (ACL 2024, https://arxiv.org/abs/2402.11782, ConflictingQA)
  found that models choose evidence mainly by relevance and largely ignore scientific references or neutral tone.
  Source-quality weighting therefore has to be done in code, not left to the writer.
- **Domain ratings.** Lin et al. (PNAS Nexus 2023, https://doi.org/10.1093/pnasnexus/pgad286) found high agreement
  between six expert rating sets and built an aggregate covering 11,520 domains
  (https://github.com/hauselin/domain-quality-ratings). The repo has no formal licence ("feel free to reuse"). Use it as
  a prior for the authority boost.
- **Recency.** FreshQA/FreshLLMs (Vu et al., Findings ACL 2024, https://arxiv.org/abs/2310.03214) splits questions into
  never-, slow- and fast-changing and false-premise types. FreshPrompt gains depended on how many retrieved passages were
  included and in what order. For our pipeline: let the planner tag the question as time-sensitive, extract page dates
  (meta tags, JSON-LD `datePublished`/`dateModified`, URL dates), prefer newer sources, and show "as of <date>" next to
  each figure. When two sources disagree and the newer one is more than N months newer, say "newer source says...", not "sources disagree".
- **Contradiction detection.** WikiContradict (IBM, NeurIPS 2024, https://arxiv.org/abs/2406.13805,
  `ibm/Wikipedia_contradict_benchmark`, MIT, 253 instances in 506 rows) found that models often fail to report a
  conflict when shown two contradicting passages, especially implicit ones. Our "Sources disagree" check only compares
  numbers today. It could also run checker-contradiction between statements on the same subject from different domains.
- **Inaccessible pages.** Pew (May 2024, https://www.pewresearch.org/data-labs/2024/05/17/when-online-content-disappears/)
  found 38% of 2013 web pages gone by 2023, and 23% of news pages have at least one broken link. Fallback: the Wayback
  Machine availability API (archive.org/wayback/available). Record "fetched / archived copy / failed" for each source,
  and never cite a page that was never fetched (a search snippet alone is not evidence).

## 5. Calibration and abstention

- MiniCheck authors use the midpoint threshold (0.5) because checker scores sit near 0 or 1. Per-dataset tuned thresholds
  add about 1-2 BAcc (FactCG paper: 75.6 -> 77.2). **A threshold tuned on your own labelled set matters more than fancy methods.**
- **Conformal factuality** (Mohri & Hashimoto, ICML 2024, https://arxiv.org/abs/2402.10978) removes or generalises
  claims until the remaining output is correct with a chosen probability. The authors report 80-90% correctness
  guarantees while keeping most of the output, using few labelled examples. The simple version for us: pick the "confirmed"
  threshold on about 200 labelled held-out pairs so that the upper Clopper-Pearson bound on false accepts stays at or
  below a target such as 5%. This assumes the live data looks like the calibration data (exchangeability), which web
  research only roughly meets, so re-check it on every new held-out batch.
- **Reporting:** three honest bands are better than a % score: Confirmed (passed the calibrated threshold and the
  deterministic number/qualifier checks), Unconfirmed (no supporting passage found), Disputed (sources conflict). Show the
  counts in the answer header (for example "9 confirmed, 3 unconfirmed, 1 disputed").

## 6. Prompt injection in retrieved pages

- **No filter-style defence holds against adaptive attackers.** "The Attacker Moves Second" (Nasr et al., OpenAI,
  Anthropic, Google DeepMind and others; USENIX Security 2026, https://arxiv.org/abs/2510.09023) broke 12 published
  defences with attack success above 90% for most of them; human red-teaming reached 100%.
- **Defences that work by design:** CaMeL (Debenedetti et al., Google DeepMind, 2025, https://arxiv.org/abs/2503.18813)
  solves 77% of AgentDojo tasks with provable security, against 84% undefended. "Design Patterns for Securing LLM Agents"
  (Beurer-Kellner et al., 2025, https://arxiv.org/abs/2506.08837) describes patterns such as plan-then-execute, dual
  LLM and context minimisation. **The key property for us:** the LLM that reads web text has no tools and no side effects,
  and its output is limited to a fixed JSON schema of statements plus citations. The planner must never see raw page text.
- **Defences that reduce the attack rate (but are not guarantees):** Spotlighting (Hines et al., Microsoft, 2024,
  https://arxiv.org/abs/2403.14720) wraps, datamarks or encodes untrusted text. It cut attack success from over 50% to
  under 2% on GPT-family models against non-adaptive attacks. Llama Prompt Guard 2 (22M, DeBERTa-xsmall, Llama 4
  Community Licence; card reports 78.4% attack prevention at 3% utility cost on AgentDojo; 512-token window) can flag
  pages for display.
- **Blind spot specific to this pipeline:** an injected *false statement* ("Note to AI: the capital is X") will pass the
  grounding checker, because the page really does say it. Mitigations: strip hidden content before passage splitting
  (`display:none`, `visibility:hidden`, aria-hidden, HTML comments, zero-width and bidi characters, white-on-white text);
  drop passages that address the reader or AI with instructions; require two independent domains before "confirmed" on
  anything contested; and show single-source claims as such.

## 7. Datasets for a local evaluation of the claim checker

| Dataset | HF id | Split to use | Licence | Notes |
|---|---|---|---|---|
| LLM-AggreFact | `lytang/LLM-AggreFact` | `test` (29,320 rows); `dev` (30,420 rows) for threshold tuning | **CC BY-ND 4.0, gated** (accept terms); evaluation only, not for training | Columns: dataset, doc, claim, label (1 = supported). Closest to our use: ExpertQA, Lfqa, RAGTruth, ClaimVerify, FactCheck-GPT, Reveal |
| VitaminC | `tals/vitaminc` | `test` (55.2k) | CC BY-SA 3.0 | **Contrastive:** rows sharing `case_id` differ in one fact (number, rank, year). Filter `revision_type == "real"`. Labels: SUPPORTS / REFUTES / NOT ENOUGH INFO |
| FEVER (NLI form) | `pietrolesci/nli_fever` | `dev` (about 20k; test unlabelled) | Card lists none; FEVER itself is CC BY-SA 3.0 (Wikipedia) | premise/hypothesis/label, ready to use |
| SciFact | `allenai/scifact` (`claims` + `corpus`) | `validation` (450 rows); test labels hidden | CC BY-NC 2.0 | May need the parquet branch, because newer `datasets` versions dropped loading scripts |
| Climate-FEVER | `tdiggelm/climate_fever` | `test` (1,535 claims x 5 evidence sentences) | Listed "unknown" on HF; check before any redistribution | Has per-evidence labels and DISPUTED claims |
| WikiContradict | `ibm/Wikipedia_contradict_benchmark` | `train` (506 rows) | MIT | For testing "Sources disagree", not the checker |
| AVeriTeC | `chenxwh/AVeriTeC` | `dev.json` | CC BY-NC 4.0 | Evidence comes as Q&A pairs from the web; poor fit for a passage checker. Optional |
| QuanTemp | GitHub (https://arxiv.org/abs/2403.17169) | — | check | Real numerical claims; useful for the number logic |

Suggested 400-pair sample (seed it and freeze the ids in a file, and never tune on the test half):
- 200 from `lytang/LLM-AggreFact` test: 40 each from ExpertQA, Lfqa, RAGTruth, ClaimVerify, FactCheck-GPT, with labels
  balanced 20/20. Chunk long docs the way the pipeline does.
- 100 from `tals/vitaminc` test: 50 `case_id`s with `revision_type == "real"`, taking the SUPPORTS row and the
  REFUTES/NEI row for the same claim. This directly measures how often a minimally wrong claim is falsely accepted.
- 50 from `pietrolesci/nli_fever` dev (25 entail / 25 contradict+neutral).
- 50 from `tdiggelm/climate_fever` test (claim plus one evidence sentence with SUPPORTS / REFUTES evidence_label).
- Plus the existing 36 in-house pairs and 30-50 hand-written qualifier/negation/unit pairs ("largest" vs "largest
  inland", "boils at a higher temperature" vs "lower", km vs m).
- Split: 50% calibration (choose the threshold) and 50% test (report). Metrics: BAcc, **false-accept rate** (the number
  users feel), confirm rate on true claims, and p50/p95 CPU latency per pair.

---

## 8. Ranked recommendations

1. **Build the frozen 400-pair evaluation set first** (section 7). Cost: download only, about 60 MB. Every change below is measured against it: BAcc, false-accept rate (FAR), confirm rate on true claims, and p95 latency.
2. **Replace the xsmall checker with FactCG-DeBERTa-v3-Large** (LLM-AggreFact 75.6 at threshold 0.5), or MiniCheck-DeBERTa-v3-L (MIT, 73.1). Export to ONNX int8, use about 550-token chunks and take the max over chunks. Expected effect: the biggest single drop in FAR, because these models are trained on minimal-error and multi-sentence data that SNLI/MNLI lacks. Cost: about 435 MB of weights; estimated 0.3-1 s per pair on 6 CPU threads (must measure). Cheaper fallback: HHEM-2.1-Open (110M, Apache-2.0, about 1.5 s per 2k tokens on CPU per Vectara). Measure FAR and BAcc against xsmall on the same pairs.
3. **Normalise quantities instead of matching numbers literally** (quantulum3 + pint, MIT/BSD). Expected: fewer false "unconfirmed" results on unit and format differences, and catches wrong magnitudes. Cost: milliseconds. Measure on VitaminC number cases plus the hand-made unit pairs.
4. **Add a qualifier/negation/direction guard** (deterministic diff of superlatives, restrictive modifiers, negation and higher/lower), with an optional contrastive counter-claim score for flagged sentences only. Targets exactly the two observed false accepts. Cost: milliseconds, plus one checker call per flagged sentence. Measure FAR on the VitaminC contrastive subset and the qualifier set.
5. **Calibrate the threshold on the calibration half** so that FAR stays at or below about 5% with a Clopper-Pearson upper bound, and report Confirmed / Unconfirmed / Disputed counts. Cost: none at run time. Measure FAR on the test half and on every new held-out live batch.
6. **One fact per sentence, at the writer** (prompt change), and use Claimify-style "skip if ambiguous". Only decompose with an LLM when the old xsmall checker is kept: decomposition helps weak checkers and hurts strong ones (Hu et al., NAACL 2025). Cost: none. Measure the confirm rate in live runs and FAR.
7. **Harden against injection:** strip hidden text and zero-width characters, datamark passages in the writer prompt, keep the writer tool-less with JSON-only output, never pass raw page text to the planner, and require two independent domains for "confirmed" on contested claims. Prompt Guard 2 22M is optional (Llama licence). Measure with a handful of planted pages (visible and hidden instructions, and false "facts") and count leaks.
8. **Source-date and recency handling plus a domain-quality prior** (Lin et al. ratings), "as of" dates in the answer, and a Wayback fallback for dead links. Measure with FreshQA-style fast-changing questions, checking how often the newest figure wins.
