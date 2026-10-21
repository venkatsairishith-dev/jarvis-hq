# Model selector, phase 1: recommend only (2026-10-20)

A separate, reversible phase. The question-and-resume flow (TASK_INPUT_2026-10-20.md) is untouched. Nothing is
committed, pushed or deployed, and the live route did not change.

## Rules it keeps (owner, 2026-10-20)

| Rule | How |
|---|---|
| Never invent model capabilities | Capabilities come only from the open models.dev catalog, the one OpenCode uses (MIT), filtered to our providers' models. A capability it doesn't state is reported `*_UNVERIFIED`, and a task that needs it skips that provider. The owner can accept unverified per task (`--allow-unverified`). A host with no listing (Dahl, local llama.cpp, Ollama tunnels, Puter) gets no borrowed numbers. Measured facts come only from the audit log (the providers' own token counts, latency, failures). |
| Never silently change production providers | `model_select.py` only returns a recommendation (`applies: false`, `calls_made: 0`). It writes nothing. `gateway.call()` and `llm_proxy` are unchanged. Making a choice the default stays the existing approval-gated `models.prefer` action, done by a person. |
| No unapproved paid API calls | The selector makes no calls at all (tests block the network). Paid providers are refused by the existing gateway rule. A non-zero list price in the catalog is shown as `LIST_PRICE_NONZERO`, because free use then depends on the provider's free tier. |
| No duplicating retries of consequential actions | `fallback_allowed(kind, stage, idempotency_key)`. A model call may move on only before any output (`not_sent`, `rejected`). An action may only be resent if the request never left **and** it has an idempotency key. Anything after output started, or with an unknown outcome, is held for the owner. |
| Lanes | Unchanged from `gateway.explain()`: a private task never reaches a public provider, even when the owner asks for one. |

## Files (all new except the registry row)

- `jarvis/kernel/jarvis_kernel/model_select.py`: `requirements()`, `select()`, `evidence()`, `fallback_allowed()`, CLI.
- `jarvis/scripts/refresh_model_catalog.py`: builds the snapshot from a downloaded copy (`--source`) or `--fetch`
  (https only, size cap).
- `jarvis/config/model_catalog.json`: 3 providers, 37 models, 22 KB. Source sha256 `b5873873a668…`, built 2026-10-20.
- `jarvis/kernel/jarvis_kernel/capability_probe.py` and `jarvis/kernel/tests/test_capability_probe.py` (phase 1b).
- `jarvis/kernel/tests/test_model_select.py`: 18 tests.
- `jarvis/tools/registry.json`: models.dev row (89 entries).

To undo: delete the four new files and the registry row. Nothing else reads them.

## Use

    cd jarvis/kernel
    python -m jarvis_kernel.model_select "fix the failing test" --tools
    python -m jarvis_kernel.model_select "review this repo" --tools --chars 200000 --evidence
    python -m jarvis_kernel.model_select "news summary" --lane public --prefer dahl-glm

The output lists the recommendation, the chain, every provider with its reason codes, what the live route would choose
today, and whether they differ.

## Dry run on the real providers.json (no network; key files faked as empty files where noted)

| Case | Recommended | Live route today | Note |
|---|---|---|---|
| Private chat, this server | vm-llama-cpu | same | other providers lack key files here |
| Private + tools (keys present) | groq gpt-oss-120b | same | tool calls declared by the catalog; local models' tool support unverified |
| Private + tools, 200k chars | none | openrouter-private | **finding:** the live route would send it to OpenRouter, whose default model `apodex/apodex-1.1-mini:free` is not in today's catalog (possibly retired). Tool support unverified. |
| Public, owner prefers dahl-glm | refused (`KEY_MISSING` here) | n/a | override shown, not forced |
| Private, owner asks for dahl-minimax | groq | groq | refused: `LANE_MISMATCH` |

## Tests (neko-test image, network off)

- test_model_select + test_capability_probe: 24 ran, OK (ruff clean).
- jarvis/tools: 53 passed.
- Full kernel suite: see the session summary.

## Phase 1b: shadow replay, tool probe, OpenRouter and 9Router checks (same day)

Done without touching the live route. Wiring the selector into `llm_proxy` is still not done.

**Shadow replay** (`python -m jarvis_kernel.model_select --shadow 7`): reads the audit log through the read-only
connection, asks the selector what it would pick today for each real call (lane, tool count and estimated tokens as the
log kept them), and reports agreement and every difference with the reason codes of the provider that actually served
it. It writes nothing and records nothing in the live path. Caveat: it replays against today's configuration, so a
difference is a thing to look at, not a fault. *Real data: blocked here* (no audit log on this server); run it on the
laptop. Tested on a synthetic log (3 compared, 1 same, 2 skipped, nothing written).

**Local tool probe** (`python -m jarvis_kernel.capability_probe --provider vm-llama-cpu --runs 3 [--write]`): sends a
fixed prompt offering a fake `get_weather` tool, passes only a well-formed call with valid JSON arguments. Refuses
anything that is not local lane and loopback, or paid, before sending a request; reads no key; never executes the tool.
`--write` stores the result in `jarvis/config/measured_capabilities.json`; the selector then reports `TOOLS_MEASURED`
(or `TOOLS_MEASURED_FAILED`, which beats a catalog "yes"), only for the model that was measured. *Real run: blocked here*
(nothing listens on 127.0.0.1:8080 on this server). Run it on the laptop for the laptop's models. Tested against a fake
loopback server (pass, fail, server down, refusals, atomic save).

**OpenRouter default** (checked against OpenRouter's public model list, no key, 2026-10-20, 468 models): the configured
`apodex/apodex-1.1-mini:free` is **no longer listed**, and nothing with "apodex" is. So the `openrouter-private` entry
in `providers.json` (and `openrouter-public`, same model) would fail when called. 17 free models are listed; 16 declare
tool support (all but `nvidia/nemotron-3.5-content-safety`). Largest relevant picks include `google/gemma-4-31b-it:free`,
`nvidia/nemotron-3-super-120b-a12b:free`, `poolside/laguna-s-2.1:free`. Which to choose, and the `model_pattern`/privacy
check (`data_collection: deny`) for that model, is the owner's call; I changed nothing. A live test needs the key on the
laptop.

**9Router** (read from source, not run or installed: github.com/decolua/9router v0.5.104, 2026-10-15; MIT; Next.js
gateway, 34 dependencies, CLI on npm):
- Fit: it does what our `gateway.py` + `llm_proxy` already do (one OpenAI-compatible endpoint, fallback, usage), plus a
  dashboard, 40+ provider formats and OAuth for subscription accounts.
- Concerns found in the source and README: default dashboard password `123456`; `REQUIRE_API_KEY` defaults to `false`;
  stores provider credentials and OAuth tokens in its own SQLite under `~/.9router`, outside our secrets folder rule;
  the CLI checks the npm registry for updates; an optional "cloud sync"; a `mitm` component that intercepts Copilot,
  Cursor, Kiro and Google Cloud Code hosts (DNS, certificates), which would need root and is not something this machine
  may do; its headline feature is routing "subscription" accounts, which can break those providers' terms. I found no
  private/public lane model (a keyword search hit only 3 files, not read in depth) and no paid-provider refusal like
  ours.
- Recommendation: do not install on this server. If wanted at all, trial it on the laptop only, loopback only, with a
  changed password, `REQUIRE_API_KEY=true`, no MITM, no subscription accounts, no cloud sync, and put it **behind**
  our gateway as one more provider (so lanes and the paid rule still apply). The selector can already do the routing
  we need, so the gain is mainly the provider list. Owner decision; no install done.

## Not done (next phases, each needs the owner)

1. Run the probe and the shadow replay on the laptop (real model, real audit log).
2. Wiring the selector into the live route (live shadow logging, then via `models.prefer`). Needs approval; not authoritative until the real shadow results look right.
3. Replacing OpenRouter's retired default model in providers.json. A provider-settings change: needs approval, and a live check with the key on the laptop.
4. 9Router: not installed; trial on the laptop only if you want it (see above).
5. Task kind from `intent.classify` / `local_team.route`: these live in other packages, so the selector takes needs as explicit flags for now.

## Laptop test steps (about 15 minutes)

Windows, in `D:\jarvis\hq-neko` (copy of this working tree). Nothing below changes a setting unless it says `--write`.

1. Chat question card: `cd frontend\react-neko-chat`, `npm ci`, `npm run build`; start N.E.K.O as usual; steps are in
   TASK_INPUT_2026-10-20.md section 8.
2. Selector dry run: `cd jarvis\kernel`, then `python -m jarvis_kernel.model_select "fix the failing test" --tools`.
   Expect `applies: false`, `calls_made: 0`.
3. Tool probe (needs the local model running on 127.0.0.1:8080):
   `python -m jarvis_kernel.capability_probe --provider local-llama-vulkan --runs 3` (add `--write` to keep the result).
   Exit 0 = tool calls work, 1 = they do not, 2 = refused.
4. Shadow replay on the real log: `python -m jarvis_kernel.model_select --shadow 7`. Send me the numbers (counts only).
5. Do NOT paste keys anywhere. Do not install 9Router.
