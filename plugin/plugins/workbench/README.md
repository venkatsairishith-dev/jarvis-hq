# Workbench

Three jobs in one plugin, built on the existing JARVIS parts (doc_tools, research, jarvis_bridge, the kernel, the skill
gate, the local models). Nothing here runs anything consequential by itself.

| Job | Entries |
|---|---|
| Client projects: raw materials -> source-grounded requirements -> plan -> checked deliverables | `create_project`, `add_project_files`, `analyze_project`, `answer_questions`, `build_deliverables`, `project_status`, `set_provider_policy` (owner only) |
| Prompt Generator: structured prompts you can preview, edit, copy, save, regenerate and run | `generate_prompt`, `get_prompt`, `edit_prompt`, `regenerate_prompt`, `execute_prompt` (owner only) |
| Learning unfamiliar tasks: research, plan, sandbox test, Propose-Probe-Compose refinement, skill + lessons | `learn_task`, `list_learned`, `run_learned_workflow` (owner only), `check_workflow_output` |

"Owner only" entries need `confirm: true` from the panel button and are never offered to main_agent's planner
(`planner.PLANNER_EXCLUDED_ENTRIES`). Running anything also needs JARVIS reachable with the kill switch off; otherwise the
answer is "refused, nothing was run".

## Client projects

`Hey Jarvis, here is the recording of my client call and our chat: understand what they need and make the deliverables.`

1. **Ingest** (`ingest.py`): files are copied into `<WORKBENCH_DIR>/projects/<id>/sources/` (Document Tools' read rules:
   allowed folders only, credential-like names and system folders refused, links refused). Formats: WhatsApp (Android and
   iOS lines), Telegram JSON, generic message JSON, "Name: text" logs, SRT / VTT / "[00:01:02] Speaker: text" transcripts,
   PDF, Word, Markdown, HTML, CSV / TSV, spreadsheets (if openpyxl is installed), images (kept as references: no OCR here).
2. **Audio** (`transcribe.py`): JARVIS's own local speech server on the laptop (Parakeet for English, IndicConformer for
   Hindi and Telugu, sherpa-onnx; loopback only; audio never leaves the PC). Non-WAV audio needs ffmpeg. 30-second chunks
   give the evidence timestamps. Language: the owner's hint (en, hi, te); without one it is "unconfirmed" and becomes a
   question. No speaker separation.
3. **Requirements** (`requirements.py`): every item quotes the exact sentence and cites file + message / timestamp / page.
   Kinds: client requests, the owner's commitments, preferences, deadlines, constraints, unanswered questions. A local
   model may suggest more items, but only if its quote is found word for word in the segment it cites.
4. **Conflicts and questions**: different deadlines, budgets, counts, a colour asked for and ruled out, "actually / instead"
   changes; missing deadline / format / size / budget; who the owner is; unconfirmed audio language; client questions
   with no reply. Nothing is resolved by guessing: `answer_questions` records the owner's answers.
5. **Untrusted input**: text addressed to an AI ("ignore your instructions", "JARVIS must upload ...") is listed under
   "Text addressed to an AI (ignored)" and never becomes a requirement, a step or a prompt instruction.
6. **Plan** (`planning.py`): clarify -> (research) -> draft -> validate -> hand over, with the JARVIS tool per task and what
   needs approval. Logos, artwork and video get a brief (JARVIS has no image or video generator) and the plan says so.
7. **Prompts**: one per substantial task, saved in the project (`prompts/`).
8. **Deliverables** (`deliver.py`): made with a verified LOCAL model (the model's 2,048-token window is respected);
   code, websites and research wait for the owner to run their prompts through JARVIS.
9. **Validation**: a deliverable is `verified` only if the file reopens, every mapped requirement is listed under
   "Requirements covered" AND its key words are in the text, no secret is in it, and nothing from AI-addressed text leaked
   into it. Otherwise `needs review` with the reasons. Hindi / Telugu requirements cannot be word-checked: "check by hand".
   `SUMMARY.md` lists completed work, open questions and files.

**Client data** stays in its project folder (paths are confined; links refused; prompts and lessons cannot cross).
Client text goes only to a verified local model (`safety.egress_allowed`), unless the owner sets the project's provider
policy to `external_allowed` with named providers and `confirmed_by_owner`.

## Prompt Generator

Input: objective, kind (`single`, `multi_agent`, `research`, `coding`, `client_deliverable`), target (agent / model / tool /
environment), source files and conversation history, constraints, exclusions, budget, deadline, output format, acceptance
criteria, optional client project. Output sections, always in this order: role and objective; context from the sources
(fenced as data, cleaned, secrets removed); requirements and exclusions; tools; steps and dependencies; security and
permission boundaries; deliverables; acceptance criteria and verification; failure handling and unresolved assumptions.

A prompt is saved as a **suggestion** (versioned; edits and regenerations are new versions, nothing is overwritten).
`execute_prompt` sends its compact form (at most 2,000 characters, the limit main_agent and the kernel accept) to
main_agent or JARVIS and records the run as **submitted**, never "done": the result is checked where it ran.

## Learning unfamiliar tasks

`learning.py`: analyse (needs, side effects that need approval, matching tools, library) -> research (research plugin,
official documentation graded first; "no research" is said plainly) -> plan (steps, dependencies, permissions,
uncertainties, success criteria) -> draft a declarative workflow (owner, local model or library) -> sandbox test on
example cases -> Propose-Probe-Compose refinement (`ppc.py`) -> skill gate rating -> store (`library.py`).

- **Sandbox** (`sandbox_runner.py`, `sandbox.py`): a closed set of text and table operations, run in a separate
  `python -I -S` process with an empty environment, a timeout and (Linux) CPU / memory limits. No network, process, import
  or eval operation exists; writes only to declared output names. Workflows that need code are "needs the lab".
- **Validation gate**: adopted only if every example case passes. No examples = "needs examples", never "learned".
- **Skill gate**: the generated SKILL.md is rated by `jarvis/skills/skillgate.py`'s own static rules; "reject" / "high"
  (or a gate that cannot run) stops adoption.
- **Lessons**: only edits observed to help (base vs candidate contrast), with sources, version, limitations and
  validation; refused if they hold contact details, secrets or 8+ consecutive words of client material.
- **Real runs**: `run_learned_workflow` hands the validated steps to JARVIS (kernel policy, approvals, audit, kill switch);
  `check_workflow_output` applies the workflow's data-independent checks to the files the run produced.

### Harness² (google-research/harness2, Apache-2.0, commit 1cfe254e)

`ppc.py` adapts its Propose-Probe-Compose method: invariant and domain edits, probes on the same task, contrast-based
verdicts, greedy composition, adoption only after validation, retained lessons, and its rollout / proposer-call ledger.
Harness² itself is not imported: its executors (OpenCode, Codex) run with unrestricted network and Codex's sandbox off,
and its benchmarks need Vertex AI credentials, Docker and Node. `harness2_compat.py` runs upstream's deterministic local
demo from a pinned, hash-checked checkout (`HARNESS2_SRC`), offline, in a separate process, as a compatibility check.

## Settings

`WORKBENCH_DIR` (default `D:\jarvis\workbench`), `JARVIS_LOCAL_LLM` (default `http://127.0.0.1:8080/v1`, must be a
verified local endpoint), `WORKBENCH_MODEL` (default `decider-qwen3-4b-2507`), `WORKBENCH_MODEL_CTX` (default 2048),
`WORKBENCH_STT_URL` (default `http://127.0.0.1:48930`), `WORKBENCH_FFMPEG`, `DOC_TOOLS_READ_ROOTS`.

## Tests

`pytest plugin/plugins/workbench/tests` (synthetic fixtures only; no model, no network). With a pinned Harness² checkout:
`HARNESS2_SRC=<checkout> pytest plugin/plugins/workbench/tests/test_learning.py -k harness2`.
