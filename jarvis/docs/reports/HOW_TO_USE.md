# How to use JARVIS, step by step

For the owner, on the Windows laptop. Everything here is what the repo does **today** (2026-10-23); items marked *not yet* are in
the build plan (`research-2026-10-22/JARVIS_FULL_INTEGRATION_PLAN.md`). How the parts fit together: `HOW_IT_WORKS.md`. Where each
of your 21 handwritten items stands: `OWNER_NOTES_STATUS.md`. Technical detail lives in `05_OPERATIONS.md`, every file is
explained in `FILE_MAP.md`, every model in `MODELS.md`.

## 1. What runs where

| Where | What | Address |
|---|---|---|
| Laptop | N.E.K.O, the character you talk to (chat, voice, memory, plugins) | http://127.0.0.1:48911 |
| Laptop | JARVIS console: approvals (PIN), audit, connectors, models, kill switch | http://127.0.0.1:8799 (open it with the launcher, step 4) |
| Laptop | Neko icon widget (always on top, bottom right) | `open-widget.ps1` |
| Laptop | Local models (llama-swap), local speech, privacy gateway, wake listener | loopback only |
| Oracle VM `hermes-agent-vm` | The kernel (the only authority: policy, approvals, audit, kill switch), Hermes, model proxy | over Tailscale only |

Rules that never change: secrets stay as files in `D:\jarvis-secrets` (never typed into chat); the Oracle account stays on Always
Free ($0); `hermes-agent-vm-2` is never touched.

## 2. Start and stop

1. **Start everything:** `powershell -File D:\jarvis\hq-neko\jarvis\scripts\neko-start.ps1`.
   It starts N.E.K.O, the local speech server, the wake listener, Verdict, the JARVIS console and the plugins, and prints what is up.
   Running it again is safe. Add `-NoWake` to leave the microphone listener off.
2. **Start at login (once):** `jarvis\scripts\install-autostart.ps1` (task `JarvisNekoStack`; `-Status`, `-Remove`).
3. **Stop:** `jarvis\scripts\neko-stop.ps1` (stops N.E.K.O, speech and wake; the console and the laptop node keep running).
4. **One-screen health:** `jarvis\scripts\dev-status.ps1` (git, ports, scheduled tasks, disk; read only).

## 3. Open it

- **Desktop window:** `jarvis\scripts\open-desktop-app.ps1` (N.E.K.O in its own window) or `-Target jarvis` (the console).
- **Neko icon:** `jarvis\scripts\open-widget.ps1`. Click it to give a task; it shows sleeping, listening, thinking, working, needs
  permission, done (with Open file / Show in folder) and error. `install-widget.ps1 -Autostart` opens it at every login.
- *Not yet:* a tray icon and true push-to-talk (plan P8, the Tauri shell).

## 4. Sign in to the console

Open the console with `python jarvis\scripts\open_console.py` (or the widget's "open console" link). It puts a one-time code in
the address that becomes a sign-in cookie. Typing http://127.0.0.1:8799 by hand shows the login page on purpose.

## 5. Talk to JARVIS

- **Type or speak** in the N.E.K.O window, like to a person. Small talk stays with her; a real task goes to the Main Agent
  (several steps) or to JARVIS (PC work). Switch the **Agent** master switch on in her settings first.
- **Voice:** say **"Hey Neko"**, wait for the chime, then the command. "Cancel" or "never mind" cancels; "Hey Neko, stop" stops a
  running task. The live microphone path has **not been tested on this laptop yet** (do the spoken test in `05_OPERATIONS.md`,
  "Hey Neko" section). *Not yet:* "Hey Jarvis" as the wake word.
- **Examples that work today:**
  - "Open YouTube and play \<song\>": opens the site in your checked profile and verifies it is playing.
  - "Research \<topic\> and make me a deck" / "... a PDF report": researches with checked citations, builds the file in
    `D:\jarvis\outputs\<date>\`, inspects it, and offers to open it.
  - "Why is \<app\> not starting?": the troubleshooter checks ports, disk and services and says what it found.
  - "Open \<app\> and write \<text\>" (asks you first before typing).
- **Unclear requests become questions:** "open the other one", "notepad or wordpad", "don't open YouTube" make it ask instead
  of guessing.

## 6. Approvals, the PIN and the kill switch

- Anything risky (typing, clicking, installing, sending) waits for your approval. The card shows exactly what will run.
- **Approve** in the JARVIS console with your PIN. N.E.K.O, the widget and agents cannot approve anything.
- **Set or change the PIN** (only you, at the kernel console): see "Approval PIN" in `05_OPERATIONS.md`. Never write the PIN in a
  chat, file or log.
- **Stop everything:** the console's Kill button. Lifting it is only possible at the kernel console (`python3 -m jarvis_kernel resume`).
- Banks, payment sites, wallets and password managers are always refused, even with your approval.
- ChatGPT, Gemini and Claude websites: JARVIS opens them for you, but never reads, clicks or types there (their terms forbid
  automated use). To get another model's opinion, JARVIS asks it through its API.

## 7. Models and providers

- Local models answer most requests (decider Qwen3-4B-2507, Ling for planning, coder, maths, Verdict for routing); they load on
  demand and unload when idle.
- **Planning model:** Dahl's GLM-5.3-Flash (chosen by measurement, `BASE_MODEL_2026-10-24.md`), through the privacy gateway. JARVIS never calls Claude (D35); Claude only builds it. Private data is replaced with placeholders before anything leaves the laptop.
- The console's **Models** screen shows which provider answered, what is resting after a free-tier limit, and token use.
- *Not yet:* "switch to \<provider\>" by voice and one router for every coding CLI (plan P3).

## 8. Files JARVIS makes

- Decks, reports and agent outputs: `D:\jarvis\outputs\` (never overwritten).
- Repairs keep a backup in `<data>\repair-backups\<id>`; `repair restore <id>` undoes one.
- *Built, not switched on yet:* find, create, move and organise files with an undo journal (`jarvis/node/jarvis_node/fileops.py`,
  `filesearch.py`). They become voice/typed commands when the decider gets its find/create/organise tools (plan P4 step 2).

## 9. Is everything healthy?

1. `D:\jarvis\tools\neko-venv\Scripts\python.exe jarvis\scripts\live_check.py`: servers, plugins, speech, kernel, audit chain,
   node and UI; exit code 0 means all passed (`--no-model` skips the paid model call).
2. `jarvis\scripts\daily_report.py`: one-page daily health report, no model calls.
   Safety posture: `python jarvis\tools\doctor.py` checks that JARVIS's ports listen only on this PC or Tailscale, no key sits in
   the repo, the secrets folder is outside it, providers are encrypted and free, and skills are unchanged since they were checked.
   Exit code 0 means no FAIL; it never prints a secret.
3. Tests: `jarvis\scripts\dev-test.ps1 quick` (about 6 minutes) or `full` (about 40 minutes).
4. The vault (`D:\jarvis\vault`, `daily\<date>.md`) records what was done each day and where work stopped.

## 10. When something goes wrong

| Symptom | Do this |
|---|---|
| A page or the console does not load | `dev-status.ps1`, then `neko-start.ps1` again (safe to repeat) |
| "Busy" or "rate limited" | A free provider is resting; the console says when to retry. Nothing is broken |
| A task seems stuck | Say "Hey Neko, stop", press Stop in the panel, or the console's Kill button |
| Something changed that should not have | The audit trail in the console shows every action and approval; repairs can be restored (section 8) |
| The kernel on the VM misbehaves after an update | Its previous copy is kept as `~/jarvis/kernel.old.<ts>`; the rollback steps are in `05_OPERATIONS.md` |

## 11. Built but not switched on yet

These are merged with passing tests and become usable as the plan wires them in (status per item: `OWNER_NOTES_STATUS.md`):
page watches ("tell me when this page changes") with a background runner, a notice inbox and phone pings that say only "JARVIS: 2 new
notices" (never the page, price or link; open JARVIS to read them), finance CSV summaries, PDF form filling after your approval, Gmail and Calendar
drafts (never sent by JARVIS), a morning brief in the vault (`daily\<date>-brief.md`: what waits for your PIN, what failed, page
changes, safety check; `python -m jarvis.muse.brief --vault D:\jarvis\vault --inbox <inbox> --print` makes one now), routine recipes, the router with automatic provider fallback, the Python egress proxy, and the
supervisor that will start and stop everything as one.

## 12. JARVIS's own personality

1. Stop N.E.K.O (`neko-stop.ps1`), then `python jarvis\persona\install_card.py` shows what will change; add `--apply` to
   install the JARVIS card (characters.json is backed up first) and `--activate` to make it the current character.
2. Start N.E.K.O again. JARVIS is calm and direct: it disagrees when something is unsafe, never flatters, never approves for you,
   and says "I haven't checked" instead of guessing.
3. Check it still behaves after any model change: `python jarvis\persona\run_cases.py --model <model>` (add `--judge-model` for a
   stricter second opinion). Every case must pass.

## 13. Let another assistant read your JARVIS (read-only)

Claude Code, Gemini CLI or OpenCode can look at JARVIS (tasks, notices, page watches, ideas, today's brief, the safety check)
but never change anything:
`claude mcp add jarvis -- python -m jarvis.bridge.readonly_mcp --data D:\jarvis\N.E.K.O-data\muse --vault D:\jarvis\vault`.
What it shows goes to that assistant's cloud model, so it gives counts and states, and text only after the privacy redactor has
hidden private values (no redactor, no text). Every look is logged in `mcp_reads.jsonl`.

## 14. A second opinion from another model

`python -m jarvis.bridge.second_opinion --file plan.md --question "What would you change?"` asks a different model (Groq's
gpt-oss-120b by default) to review a plan. It goes through the privacy gateway first, the answer says which model gave it, and it
can never trigger an action: JARVIS only shows it next to its own view. Gemini stays off as you chose; a paid ChatGPT or Gemini API
needs your approval first. The second-opinion gateway must be running (`gateway.py --port 48954 --providers providers.second.json`).

## 15. The new face (replacing N.E.K.O; pilot, not yet tried on the laptop)

JARVIS's new avatar, chat and voice window lives in `jarvis\face` (built from Project AIRI, now JARVIS's own code). Keep using
N.E.K.O until the pilot passes. To try it on the laptop (Windows):

1. Start the privacy gateway, the local speech server and the face's tool server:
   `python -m jarvis.bridge.face_tools --data D:\jarvis\N.E.K.O-data\muse --vault D:\jarvis\vault` (it listens on
   127.0.0.1:48970 only, and web pages cannot call it).
2. `cd jarvis\face`, then `pnpm install` and `pnpm run dev` (or `pnpm run build:win` for an installer). The first build
   downloads the Live2D Cubism core from Live2D (their licence, free for individuals) and the sample avatars.
3. In the face: Settings > Providers > OpenAI Compatible, paste the key the gateway forwards. Chat already points at the gateway
   and speech at the local speech server.
4. Ask it to do something: it hands the task to JARVIS (`jarvis_do`), the kernel decides, and anything risky waits for your PIN
   in the console. The face cannot approve.

The face can reach only this PC and your tailnet. To let it download AIRI's on-device speech models, add the host once:
`set JARVIS_FACE_ALLOW_HOSTS=huggingface.co,*.hf.co` before starting it. Details and what was removed: `jarvis\face\README.md`.

## 16. What is being built next

See the plan's phases P1 to P10: one supervisor for all processes, the Hermes safety hook, the router inside JARVIS, PC power with
undo and a PIN-gated admin broker, learned skills, coding agents with tested pushes, the openmuse features (watches, PDF forms,
Gmail/Calendar with review, finance), the tray app, "Hey Jarvis" and a JARVIS personality, phone approvals, and one installer.
