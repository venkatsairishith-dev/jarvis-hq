# From skeleton to body to brain: and how it learns, improves and corrects itself (2026-10-22)

Owner's order (2026-10-22): skeleton, then body, then brain, and **self-learning, self-improving and self-correcting is the most important**.
This is the plan, in that order, with what is already built, what comes next, and the rules that make a self-improving agent safe to leave running.

## The three layers, honestly

| Layer | Meaning | State today |
|---|---|---|
| **Skeleton** | The structure: kernel (approvals, audit, kill switch), plugin host, chat, wake/STT/TTS, plan-first agent, memory store | Built and tested; live on the laptop for the basics |
| **Body** | What it can DO and sense: hands (apps, typing, files, browser), eyes (screen, windows), voice, integrations | Opens apps, types, reads windows, browses; **cannot create files or folders, no calendar/mail, voice loop not proven live** |
| **Brain** | Understanding, planning, remembering, choosing | Plan-first agent works when a capable planner answers; local models too small; memory not typed or visible |
| **Learning loop** | Gets better from use, safely, measurably | **Layer 1 built today** (episodes, hints, stats); layers 2-5 below |

## The learning loop (what makes it self-improving)

```
 do a task ──► record an EPISODE ──► measure (stats, baseline) ──► find weak spots
     ▲                                                              │
     │                                                              ▼
 use it next time ◄── promote (owner, after a passing gate) ◄── propose a change (skill / example / recipe / fix)
```

Nothing in this loop is trusted just because the model wrote it. A change is **proposed**, **tested against the regression gate**, and only the
**owner** promotes it; every version is immutable and can be rolled back. That is the part of this project that already works and is the real asset.

### Layer 1: remember what happened (BUILT today)
`plugin/plugins/main_agent/episodes.py`. One private line per finished task: the plan shape, each step's outcome with an error class, seconds, whether it asked
a question or was held for an untrusted-content approval. **Never stored:** request text, goals, arguments, results, error text. The request is kept as salted word
hashes so it can recognise "similar" without being readable. Used for: (a) a one-line **hint** to the planner ("for similar requests this shape succeeded 3 of 4
times; that one failed with no_match"), shapes only, so nothing private reaches a cloud model; (b) **`learning_stats`**: success rate per shape and per helper,
commonest error classes, whether the last 20 tasks beat the 20 before, helpers failing more than half the time. `forget_learning` erases everything. 180 days / 2,000 lines.
Tests: `tests/test_episodes.py` (12). The 10-task baseline (`jarvis/evals/neko95/task_baseline.py`) and your daily use both feed it, so there is one number going up or down.

### Layer 2: correct yourself within a run (BUILT 2026-10-22, bounded)
When a task ends with a **definite** failure (the step reports failed; not "unknown", not held, not waiting for you), the agent may re-plan **once**: it asks the planner again with
the failure class (no text) and the helpers still available, and runs only the remaining work. Guards: one replan per run; never after an unknown or held step (it may have happened);
every step of the new plan goes through the same checks (approvals, untrusted-content hold, kernel policy); a replan that repeats the failed shape is refused; the episode records
that a replan happened and whether it rescued the task. Built in `main_agent._replan_once` with ten tests, each guard checked by mutation (disabling any one makes a test fail); `learning_stats.self_correction` shows tried vs rescued. This is the "self-correcting" part: it turns "no single file matches" into "ask which file" or "search by name" without any new permission.

### Layer 3: turn what works into skills (NEXT, uses the existing gate)
The desktop plugin already has a Voyager-style gate (`desktop/skills.py`): a proposal may only add **examples** (a request with the plan the decider should produce) and **sites**;
it runs the regression suite (dev + holdout + plan cases) and passes only if nothing that worked now fails; **only the owner promotes**, from the command line; versions are immutable.
Layer 3 mines the desktop plugin's own task records for requests that were executed and verified on screen at least 3 times with the same plan and no failure, and *proposes* them as
examples. It never promotes and cannot express a tool, permission or safety change. These records stay on the laptop (local decider only; nothing goes to a cloud model).

### Layer 4: lessons you can read and delete (NEEDS the kernel deploy)
Typed memories in the kernel with a source and a time: "preference", "lesson" (e.g. "report requests: ask which report"), with provenance (the episode ids), a validity date and
a console view with **Forget**. Needs the kernel changes waiting in `KERNEL_DEPLOY_2026-10-22.md` and a schema decision (roadmap phase 2).

### Layer 5: learn from the world and from other sessions (LATER)
The caretaker (weekly: find tool issues, a lab sub-agent opens a PR, never merges) and Workbench (research, draft, sandbox, validate, skill) already exist; they stay off or owner-gated until
a safe credential exists. The server-side Claude and the laptop Claude already work as two reviewers of each other's changes: that is the human-in-the-loop version of self-improvement.

## Rules for any self-improving part (non-negotiable)
1. **Propose, gate, owner promotes.** No automatic change to prompts, code, skills, permissions or approvals. Code changes only as a pull request.
2. **Cannot touch safety.** What a proposal may express is whitelisted; tools, permissions, approvals, kill switch, privacy rules and prompt rules are not expressible.
3. **Measured.** Every change is judged by the same numbers before and after (the regression gate, the 10-task baseline, `learning_stats` trend). No number, no promotion.
4. **Reversible.** Immutable versions with hashes, one-step rollback, an audit line for each change.
5. **Private.** Learning stores shapes, classes and counts, not words; cloud models get shapes at most; one button erases it all.
6. **Bounded.** One replan per run, a cap on proposals per day, nothing runs while the kill switch is on, nothing learns from a run that was stopped by the owner.
7. **Honest.** An episode records failures as failures; an improvement is claimed only when the trend shows it.

## Body, in order of value (what to build so there is something to learn from)
1. **Reliable hands:** the 10-task baseline green on your laptop, then widen it to 30 tasks (open, find, type, save, rename, copy between apps). Every red row is a body fix.
2. **Create files and folders** through a kernel-approved operation (the current honest answer is "I cannot create folders yet").
3. **Voice loop proven live:** wake, speech to text, answer, speak back, interrupt; with a measured wake-to-first-sound.
4. **Calendar and mail, read-only,** through the kernel allow-list; then "what's on today" and a morning briefing.
5. **Proactive, with a budget:** briefing, reminders, "your task finished".

## Brain, in order of value
1. **A planner that does not rate-limit** (OpenRouter or another, through the privacy gateway), plus the retry already added; local 4B models stay for grounding.
2. **Time and context in every prompt** (done: clock line), then the owner's typed memory (layer 4).
3. **Plan quality from experience** (layer 1 hints, layer 2 replan), measured by the baseline.

## What is deliberately not here
Training model weights from your data; auto-installing skills or plugins from the internet (the OpenClaw marketplace problem: about 13% of scanned skills had a critical flaw); letting any
learned rule loosen an approval. Those are different projects with different risks.
