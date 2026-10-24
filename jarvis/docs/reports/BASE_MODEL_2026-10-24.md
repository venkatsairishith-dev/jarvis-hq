# Which model is JARVIS's brain? (2026-10-24)

Owner's ask: find models related to JARVIS, choose the best base model, and use the decision model as well.

## What was found

**No open model is "specially designed for JARVIS".** The nearest are community fine-tunes that only borrow the name:
[JARVIShks/JARVIS-Model](https://huggingface.co/JARVIShks/JARVIS-Model) (a 1B Llama-3.2 fine-tune for a voice demo, no licence or evaluation shown),
[hadadrjt/JARVIS](https://huggingface.co/hadadrjt/JARVIS) (a text model "inspired by" the character) and a Piper voice named jarvis. None is a candidate for the
brain: too small, unmeasured, unclear licence. Stanford's [OpenJarvis](https://openjarvis.stanford.edu/) is a framework, not a model; it picks a model for the
hardware (its examples are Qwen3.5-class models). Our own tree already vendors a piece of it (`skills/vendor/openjarvis`).

What matters for JARVIS is models **built for tool use and planning**, so that is what was compared:

| Family | Why it is relevant | Licence / catch | Source |
|---|---|---|---|
| GLM-5.x (and the Flash variants) | Rated among the best open models for tool-call discipline | check the model card per release | [list](https://fast.io/resources/top-open-source-llms-agents/), [list](https://benchlm.ai/best/tool-use) |
| DeepSeek-V4 Flash/Pro | strong for coding agents | terms vary per release: read the card | [guide](https://www.betterclaw.io/blog/best-free-llm-ai-agents-2026) |
| Kimi K3 | top of several agent lists | custom licence | [guide](https://www.betterclaw.io/blog/best-free-llm-ai-agents-2026) |
| MiniMax M-series | cheap, our current default (M2.7) | unverified in the sources found | [guide](https://www.betterclaw.io/blog/best-free-llm-ai-agents-2026) |
| Qwen3.5 / Qwen3.x | best small open models for tool calling; 9B > 4B > 2B on BFCL-V4 (0.66 / 0.50 / 0.44, vendor-reported) | Apache-2.0 | [comparison](https://www.digitalapplied.com/blog/small-language-models-on-device-agents-2026-guide), [BFCL table](https://d-central.tech/local-llm-agent-capability/) |
| Gemma 4 E4B, FunctionGemma 270M | small, function-calling oriented; FunctionGemma is meant to be fine-tuned | Gemma terms | [guide](https://www.ertas.ai/blog/on-device-tool-calling-2026-qwen3-gemma4-phi4) |
| Phi-4-mini 3.8B | native tool tokens | MIT | [guide](https://www.ertas.ai/blog/on-device-tool-calling-2026-qwen3-gemma4-phi4) |
| Hermes-4 (Nous), xLAM, Hammer | tool-call fine-tunes | xLAM and Hammer2.1 are CC-BY-NC: not for commercial use | [overview](https://presenc.ai/research/open-weight-function-calling-tool-use-2026), [licence list](https://github.com/xigh/open-weight-models) |

The web sources are blogs and aggregators and **disagree** (one ranks Kimi K3 first, another GLM-5.2, a third an entry I could not verify); vendor benchmark
numbers are not independent. So the choice below rests on **JARVIS's own measurement**, not on those lists.

## The two-brain design (what "use the decision model also" means here)

| Role | Model | Where | Why |
|---|---|---|---|
| **Planner / thinker** (understands the request, plans steps, writes) | one hosted open model through the privacy gateway (Dahl offers MiniMax-M2.7, GLM-5.3-Flash, DeepSeek-V4-Flash-0731) | hosted, redacted | needs real tool-planning quality that a 4 GB laptop GPU cannot give |
| **Decision model** (fast PC-control choices: which window, which tool; works offline) | **Qwen3-4B-Instruct-2507** (Apache-2.0), alias `decider` | local, llama.cpp + llama-swap | measured 152/157 on 157 fixed cases (D30); nothing tested since beats it |
| Fast fallback | Qwen3-1.7B | local | 130/157, 1.0 s on CPU |

The planner and the decision model do different jobs, so JARVIS uses both: the planner decides *what to do*, the decision model makes the quick on-PC choices
and keeps basic control alive when the internet is down. Claude is not part of this at run time (D35).

## How the planner model is chosen (measured, not assumed)

`jarvis/evals/quality95/bench/hosted_model_compare.py` sends the **real planner prompt** and checks the answer with the **real plan parser** on 15 labelled
requests (chat, search, research, notes, calendar, code, impossible requests, an injection attempt), for every model the gateway lists, twice each. It
reports: valid plans, correct first step, answers cut off by the token budget, raw `<think>` text, median seconds and tokens. It uses the gateway with the
placeholder key (no key typed), synthetic requests only. Run it on the laptop:

```
python jarvis\evals\quality95\bench\hosted_model_compare.py
```

Rule for the decision, written before the run: the planner model is the one with the most **correct** plans; ties go to fewer cut-off answers, then lower
median time. A model whose reasoning regularly exhausts the budget (as MiniMax-M2.7 did for the research writer) loses to a model that does not.
Changing the default is the owner's call: set `JARVIS_AGENT_MODEL` before `jarvis-start.ps1`, or edit the default there.

## Fine-tuning a JARVIS-specific model (not started)

If a model "specially designed for JARVIS" is wanted, the route is to fine-tune a small open base with a permissive licence (Qwen3.x Apache-2.0, Phi-4-mini
MIT, FunctionGemma) on JARVIS's own labelled plans: the episode stats and the 157+ decision cases are the seed data. This needs a GPU service for training and a
clean, private dataset; the learning loop (`SELF_IMPROVEMENT_2026-10-22.md`) already collects plan shapes without words. Decide after the comparison above:
if a hosted open model already scores near the ceiling, fine-tuning buys little.

## Result (measured on the laptop, 2026-10-24) and the pick

15 labelled requests, 3 repeats each (45 calls per model), through the privacy gateway (`results/hosted-models-20261024-180058.json`):

| Model | Valid plans | Correct | Raw `<think>` in the answer | Cut off |
|---|---|---|---|---|
| **zai-org/GLM-5.3-Flash** | 42/45 | **40/45** | 0 | 0 |
| MiniMaxAI/MiniMax-M2.7 (the old default) | 39/45 | 36/45 | 45 of 45 | 0 |
| deepseek-ai/DeepSeek-V4-Flash-0731 | 33/45 | 33/45 | 0 | 0 |

- **Pick: GLM-5.3-Flash** as the planner and chat brain. It has the most correct and the most valid plans and leaks no reasoning text. Speed is not a deciding
  factor: the three medians (0.2 to 1.3 s on the repeat run, 2 to 5 s on the first) moved more between runs than between models.
- MiniMax-M2.7 put reasoning text in every reply and was the only model to miss the plain translation and web-search requests every time.
- DeepSeek-V4-Flash returned invalid JSON for every refusal-type request (pizza, flight, delete everything) and missed the code request each time.
- Scoring note: the "delete every file" request is correctly answered by a refusal, which the first scoring counted as a miss for all three models; the script now
  counts a refusal as correct there. The numbers above are from the old scoring, so the order is unchanged and GLM's margin only grows.
- Caveat: 15 requests are a small set; rerun after any new model appears (`python jarvis\evals\quality95\bench\hosted_model_compare.py`).

Applied: `jarvis-start.ps1` defaults `JARVIS_AGENT_MODEL` to `zai-org/GLM-5.3-Flash`; the face's default chat model is the same, and a profile that still holds the old
MiniMax default is moved once (any other model the owner picked is kept). Set `JARVIS_AGENT_MODEL` to override. The local decision model stays Qwen3-4B-Instruct-2507.
