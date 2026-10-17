# Caretaker

Keeps the tools JARVIS is built from healthy: everything in `jarvis/tools/registry.json` and `jarvis/tools/jarvis.lock.json`.

## What it does
- **Finds issues** (`detectors.py`). Deterministic checks; no model is asked.
  - `upstream_release`: a newer GitHub release or tag, a changed Hugging Face file, or a newer Node.js release than the lock pins.
  - `advisory`: OSV advisories for the pinned PyPI packages (uv groups `jarvis` / `jarvis-dev` with their dependencies, the registry's pip tools) and the `jarvis/ui` npm lock. Only package names and versions are sent.
  - `lock_drift`: `fetch.py`'s own check of the files on one machine.
  - `test_failure`: failed and errored cases of the newest full benchmark v2 run.
  - `secret_scan`: gitleaks over JARVIS's own code (`jarvis/`, `plugin/plugins/`; config `secret_scan_paths`), when gitleaks is installed. The vendored N.E.K.O base is left to upstream. Only the rule, file and line are kept, never the secret.
- **Logs and de-duplicates** (`run.py`). A finding id is a hash of the tool, the kind and the key evidence. The same issue keeps the same id on every run.
- **Hands each new finding to the lab** as a kernel outcome task (task create, then lab dispatch, through the kernel's HTTP API). The `Idempotency-Key` is the finding id, so a re-run never makes a second task.
  - Only findings of severity `medium` or higher get a task, at most 5 per run.
  - Owner-only findings never get a task: a tampered file, a leaked secret, an asset replaced under the same tag. They are logged for the owner.

## How a finding becomes a pull request
1. The weekly scan (a lab task) runs `python3 -m jarvis.caretaker.run --config ...` in the lab.
2. For each new finding, it creates a task as the agent `caretaker` (public lane). The kernel always marks such tasks `source=caretaker`.
3. It dispatches the task to the lab. The instructions come only from `template.py`.
   - The kernel refuses a caretaker dispatch whose text does not pass `policy.caretaker_problems`: the template mark, the fixed rules verbatim, no merge or push-to-main clause, and only a `caretaker/` branch pushed.
4. The lab sub-agent works in this order:
   1. Clones the repo and creates `caretaker/<tool>-<id>` from `origin/main`.
   2. Records the old pin in the PR body and in `jarvis/caretaker/history.jsonl`.
   3. Changes only what the finding needs and runs the mapped tests and bench areas (`test_map.json`).
   4. Pushes its branch and opens a PR to `main`. When tests fail, it opens a draft or reports "could not fix".
5. An independent verifier in the lab checks the criteria. The kernel records the outcome.
6. The owner reviews and merges the PR himself, or closes it.

## What the sub-agent may and may not do
- **May:** clone, create its own `caretaker/` branch, change what the finding needs, run tests, push that branch, and open one PR (or a draft).
- **May not:**
  - push to `main`, force-push or merge;
  - enable auto-merge, or approve, close or edit other PRs;
  - change bench cases, rubrics or scorers to make a test pass;
  - put secrets or private data anywhere.
- Text quoted from outside (release notes, advisories, bench output, upstream names in the evidence) is in `~~~~untrusted` blocks. It is data, not instructions.
- The kernel's guard is one gate. **GitHub branch protection on `main`** (a required review) is the second. Only the owner can switch that on.

## Switching it on (owner, from the console)
1. Install the preset `jarvis/caretaker/automation_preset.json` through Automations: POST it to `/v1/jarvis/owner/automations`. It arrives **paused** and runs only after you press **Resume** on "Caretaker: weekly tool scan" (Mondays 03:17 UTC).
2. Issue the caretaker's token on the kernel machine, from `jarvis/kernel`: `python -m jarvis_kernel agent-token caretaker --lane public --out <file>`. Put that file where the lab worker can read it (mode 600). The token is never printed.
3. In the lab, create `~/.config/jarvis-caretaker/config.json` with:

   ```json
   {"repo_url": "<the jarvis-hq clone URL>", "kernel": "<kernel endpoint>", "token_file": "<token file>",
    "machine": "<lock machine name of the lab host>", "root": "<data root>"}
   ```

4. The lab's agent user needs a GitHub login that can push branches and open PRs on that repo (`gh auth login`, `repo` scope).
5. On GitHub, protect `main` (require a pull request review, no force pushes).

## Logs
- Location: `<data root>/logs/caretaker/` (or `--log-dir`). It is never inside the repo; `run.py` refuses that.
  - `<date>/findings.jsonl`: every finding with the action taken (`task_created`, `task_exists`, `owner_only`, `below_threshold`, `deferred_max_tasks`).
  - `runs.jsonl`: one summary per run.
  - `seen.json`: the de-duplication state.
  - `cache/http.json`: upstream answers, kept for 6 hours.
- Every live run also appends `caretaker_run` to the kernel audit chain, with counts by kind and severity and the task ids it created.
- A refused caretaker dispatch is audited as `caretaker_dispatch_refused`.
- `history.jsonl` (in git, on each PR branch): `{time, tool, old_pin, new_pin, pr_url, finding_id}`. Helper: `python -m jarvis.caretaker.history add|set-pr|show`.

## Try it without changing anything
```
python -m jarvis.caretaker.run --dry-run --machine linux-test --root /srv/jarvis --log-dir /tmp/caretaker-logs
python -m pytest jarvis/caretaker/tests -q -p no:cacheprovider
```

A dry run prints the tasks it would create. It does not contact the kernel or change `seen.json`; its log is `findings.dryrun.jsonl`. Add `--offline` to skip all network checks.

**Where it runs:** the scan and the fixes run in the lab, on public-lane models (never Claude). They never run on the owner's machines or on the Linux test server; that server only builds and tests this code.
