# PC control: operations (2026-10-16)

Windows, unless marked otherwise. See D26/D27 in `jarvis/docs/03_DECISIONS.md`, the research in PC_AGENT_RESEARCH.md, the
numbers in PC_CONTROL_BENCHMARK.md and the test results in PC_CONTROL_TEST_REPORT.md.

## How a request flows
1. **Request in:** "Hey Neko" (wake listener 48945), the Neko icon, or the main agent calling `desktop:run_pc_task`.
2. **Decision** (`plugin/plugins/desktop/decide.py`): the local model `decider` (llama-swap alias, 127.0.0.1:8080) returns
   steps under a JSON schema.
   - Code then resolves the account (the Chrome profile registry; a name that fits several profiles → one question),
     the site, the app, keys and the deliverable.
   - An unclear or invalid plan → a question, never a guess.
3. **Coordinator** (`coordinator.py`):
   - holds the PC task lock (`<data>\neko\pc_task.lock`), so one PC task runs at a time;
   - runs at most 4 steps within 600 s, retrying a step once only after a timeout or a lost browser connection.
4. **Agents:**
   - browser: the owner's Chrome profiles, through the Playwright extension;
   - desktop: UI Automation and keys, through the node;
   - troubleshooter, and research (the existing research plugin).
   - Every action is a **JARVIS kernel job** (console owner route → kernel → laptop node). The kernel alone decides:
     ordinary actions run at once; paying, sending, deleting, installing, signing in or out, risky keys and edits wait
     for the PIN; secrets, password managers, banks and internal addresses are refused.
5. **Verification:** each step is checked against what was observed (the right profile's window, the chosen video
   playing, a window that appeared, an error gone). Only checked steps count as done.
6. **Record:** `<data>\neko\pc_tasks\<id>.json` holds the plan (model, time), each step (agent, attempts, result,
   check, uncertainty) and the status. The Neko icon shows the active agent and step; the record has no screen or page text.

## Models
- `decider` = **Qwen3-4B-Instruct-2507** Q4_K_M (D:\jarvis\models\gguf, 2.38 GB, Apache-2.0), loaded on the GPU by llama-swap
  when asked for, and unloaded 300 s after its last use.
- Only one model is on the 4 GB GPU at a time (group `specialists`), so asking for `decider` after the general model
  swaps them (cold start measured at 16–25 s; warm calls about 3 s).
- **Low-memory mode:** point the alias at `decider-qwen3-1.7b` (1.0 s, 1.6 GB; lower accuracy, see the benchmark). Edit
  `jarvis/local_models/llama-swap.yaml`, then restart llama-swap with `local-models.ps1 -Stop` and `local-models.ps1`.
  With no local model available, the coordinator answers "fallback" and nothing runs.
- Kept: Qwen3-1.7B (low-memory mode) and Qwen3-4B (already on disk before). Granite-4.0-1B and LFM2.5-1.2B were benchmarked
  and deleted afterwards (2026-10-16); jarvis\evals\pc_control\fetch_candidates.py downloads them again, hash-checked.

## Chrome profiles and the extension
- **Extension:** Microsoft's Playwright extension (id `mmlmfjhmonkocbjadbfplnigmagldckm`, version 0.4.0) is installed and enabled in all six
  profiles (Default, Profile 1, 2, 4, 5, 6).
  - Installer: `jarvis/scripts/install_playwright_extension.ps1 -Profile "<folder>"`. It uses UI Automation on
    Chrome's own Web Store page and its "Add extension" confirmation.
- **Connections are approved by hand** (owner's choice): each new connection opens the extension's page and the owner
  presses **Allow & select** on a harmless tab.
  - For unattended use, save a token from that page into `D:\jarvis-secrets\playwright\extension-token.txt`. The
    token gives full access to that browser, so keep it secret.
  - After a token leak, press "Generate new token" and restart Chrome.
- **Registry:** `<data>\neko\browser_profiles.json` holds aliases, masked accounts and a one-way account fingerprint.
  - "default" is venkatgaminggem17; "VenkatAMV" is Profile 1.
  - "Venkat" fits four profiles, so Neko asks which one.
  - Delete the file to rebuild it from Chrome's profile list.
- **Account check (`whoami`):** opens myaccount.google.com in the selected tab and compares the fingerprint.

## Privacy
- **Screen and page text:** written by the node to `outputs\neko-local`, read once and deleted by the plugin. Anything
  older than 10 minutes there is swept automatically.
  - The model sees page text only if `NEKO_SCREEN_TEXT_TO_MODEL=1` is set (off).
- **Playwright MCP console logs:** kept in a private folder, removed after each session.
- **Screenshots:** no PC-control flow takes or keeps them.
- **Remote models:** only via the privacy gateway (48950, redaction first). Decisions are local.

## Running the checks
| What | Command (repo root) | Notes |
|---|---|---|
| Whole suite | `D:\jarvis\tools\neko-venv\Scripts\python.exe jarvis\evals\pc_control\run_suite.py` | A–F, about 15 min; opens Notepad and the sample app briefly |
| Decision models | `...\python.exe jarvis\evals\pc_control\eval_decision.py --models decider-qwen3-4b-2507 [--set decision_cases_holdout.json]` | one model at a time |
| Desktop / browser / diagnosis | `live_desktop.py` (browser-venv), `live_browser.py` (browser-venv), `live_diagnosis.py` (neko-venv) | the browser cases use an isolated headless Chrome only |
| All Chrome profiles | `jarvis\tests\neko_profiles_live.py` | owner's real profiles; approves the connect prompt for example.com |
| YouTube | `jarvis\tests\neko_youtube_live.py` | plays, verifies, pauses |
| Owner-guided (PIN, deny, voice, RESUME) | `jarvis\tests\neko_guided_owner_test.py` | needs the owner at the PC |

## Emergency stop
- **Stop:** `POST /api/owner/kill` from the console, or the console's stop control. The kernel refuses every action,
  and the node stops the job in hand.
- **Lift:** only at the kernel console, typed by a person: in a Windows terminal run
  `ssh -t -i D:\jarvis-secrets\ssh\jarvis_vm_ed25519 ubuntu@100.125.91.51 "cd ~/jarvis/kernel && python3 -m jarvis_kernel resume"`,
  then type `RESUME`.
