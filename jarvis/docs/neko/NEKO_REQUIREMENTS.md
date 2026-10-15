# Neko requirements

Each requirement has an ID, a test that decides whether it is met, and a status on 2026-10-16. The statuses are:

| Status | Meaning |
|---|---|
| **TESTED** | Met, with recorded test output |
| **PRESENT** | The code exists, but it has not been exercised here |
| **PARTIAL** | Some of the requirement is met; the gap is noted |
| **MISSING** | Not built |
| **OWNER** | Blocked on an owner action, such as a credential, an install approval or the PIN |

The roadmap (`NEKO_IMPLEMENTATION_ROADMAP.md`) updates these statuses as work lands.

## 1. Experience and states

| ID | Requirement | Acceptance test | Status |
|---|---|---|---|
| X1 | A recognisable Neko icon or animated character is always available on the desktop | The icon is visible and stays on top; the CPU cost while idle is measured | PARTIAL: status widget; no Neko icon yet |
| X2 | Clicking the icon activates Neko | One click opens the listening or input state | PARTIAL: the widget has a task box |
| X3 | "Hey Neko" activates Neko by voice | A wake benchmark on real recordings: misses ≤ 5%, false accepts ≤ 0.5 per hour | MISSING: runtime not built |
| X4 | Push-to-talk works as an alternative to the wake word | Holding the hotkey records; releasing it sends | MISSING |
| X5 | States: sleeping, wake-listening, waking, listening, thinking, working, speaking, completed, needs-permission, error/offline | Every state is driven by a real signal (no timers faking progress); a unit test per transition; a live check | MISSING |
| X6 | Clickable fallback when the wake word is unavailable | Wake engine off: the icon still activates Neko | MISSING (with X1/X2) |
| X7 | The completed state offers Open file, Show in folder and Regenerate | Each button works on a real output file | PARTIAL (plugin entries exist and work; the icon UI comes in phase 3) |
| X8 | The needs-permission state shows exactly what needs approval | The text matches the kernel's pending action | PARTIAL: the widget shows a count |
| X9 | Sleeping does not run a large LLM | No LLM process is loaded while idle (llama-swap TTL) | TESTED (TTL unload) |

## 2. Voice

| ID | Requirement | Acceptance test | Status |
|---|---|---|---|
| V1 | Speech recognition offline for English, Hindi and Telugu | Word error rate on real-microphone clips (benchmark B5) | PARTIAL: synthetic clips and one real Telugu note (86% word recall) |
| V2 | Local TTS with configurable voice and rate | Settings change the output | TESTED (local server); the rate setting is not exposed |
| V3 | Stop speaking or interrupt | Audio stops within 300 ms of an interrupt | PRESENT (N.E.K.O interrupt); not measured |
| V4 | The microphone works end to end on this PC | Speak, get a transcript, get a reply, hear speech | MISSING: never exercised |

## 3. Models and hardware

| ID | Requirement | Acceptance test | Status |
|---|---|---|---|
| M1 | Hardware detector (CPU, RAM, GPU/VRAM, disk, OS) portable to other PCs | A unit test with fake readings; a live run gives the right profile here | MISSING |
| M2 | Recommend models per hardware profile | The profile selects a llama-swap config | MISSING |
| M3 | Load on demand, unload idle models, enforce a memory budget | A RAM check before load refuses an oversize model | PARTIAL: TTL and exclusive GPU group; no RAM guard |
| M4 | Route each task to the smallest suitable model | Routing tests; the D20 measurements | TESTED (rules → Jev → Ling) |
| M5 | Show which provider and model handles a task | The run record and UI show the model | PARTIAL |
| M6 | Remote use only when configured and authorised; private data redacted | Privacy gateway tests; lane rules | TESTED (D19/D20) |
| M7 | Ordered provider fallback with bounded retries; no infinite loops; detect 429 and quota exhaustion | Tests with a fake 429 or timeout | MISSING (single upstream) |
| M8 | Multiple keys per provider, stored securely | Keys only as references in `D:\jarvis-secrets`; per-provider key list | PARTIAL |
| M9 | Each provider and model can be disabled | Config flag; tests | PARTIAL (kernel providers have `enabled`) |
| M10 | Benchmarks for latency, resources, quality and task success on this machine | Reproducible scripts with saved results | PARTIAL (D5, D19, D20 benches) |

## 4. Orchestration

| ID | Requirement | Acceptance test | Status |
|---|---|---|---|
| O1 | A coordinator plans, decides direct vs planned work, delegates to role sub-agents, collects, verifies and delivers | main_agent tests and live runs | TESTED (plan-then-run) |
| O2 | Roles: research, verification, browser/search, document/presentation, code, desktop, QA | Each role is a catalogue capability with its own tools | PARTIAL: browser, jarvis, computer and think exist; research, documents and QA are missing |
| O3 | Least-privilege tools per role | The catalogue is filtered per role and mode | PARTIAL (mode filters, web-cue gating) |
| O4 | A real task timeline: agent names, current operation, source counts, queue, completion | Typed run events feed the UI; no fabricated progress | PARTIAL (run JSON, panel refresh) |
| O5 | Pause, cancel, retry and approve; cancelling stops subprocesses | Tests per control | PARTIAL (stop_task, resume) |
| O6 | Bounded budgets: time, steps and pages; a stop condition | Limits enforced in tests | PARTIAL (steps and time in main_agent) |

## 5. Research

| ID | Requirement | Acceptance test | Status |
|---|---|---|---|
| R1 | Search, read and compare sources; prefer current, authoritative ones; keep dates | Research run on a fixed topic; every claim maps to a fetched source | MISSING |
| R2 | Citations with title, URL, date and supported claim; no fabricated sources | A checker verifies each cited sentence against its source text | MISSING |
| R3 | Separate verified facts, opinions, uncertain points and recommendations | Report sections are labelled | MISSING |
| R4 | Caching, deduplication and budgets | Tests | MISSING |

## 6. Deliverables

| ID | Requirement | Acceptance test | Status |
|---|---|---|---|
| D1 | PPTX: outline, layouts, theme per topic, native charts and tables, speaker notes, source footnotes, Sources slide | Build a deck; python-pptx reopens it; rendered pages show no overflow | TESTED (live, 2026-10-16) |
| D2 | PPTX transitions and entrance animations that play in PowerPoint and degrade in LibreOffice | The XML has `p:transition` and `p:timing`; OfficeCLI validates the file | TESTED in the file; playback in PowerPoint not seen (OWNER) |
| D3 | Render slides to images and inspect them visually | PNG per slide; overflow check | TESTED (LibreOffice render, contact sheet) |
| D4 | Open the deck and start the slideshow | `soffice --show` starts full screen | TESTED (launched full screen through the plugin) |
| D5 | PDF: cover, headings, page numbers, links, tables, captions, charts, references, searchable text, embedded fonts | pypdf and pdfplumber checks pass; visual check of page images | TESTED (live, 2026-10-16) |
| D6 | Open the file or show it in the folder | The action works | TESTED (deliverables `open_output`, `show_in_folder` for its own files) |

## 7. Computer control

| ID | Requirement | Acceptance test | Status |
|---|---|---|---|
| C1 | Open Google or any website | A browser window shows the URL | PRESENT (pc open) |
| C2 | Use the owner's signed-in Google account; check the active account | The account e-mail read from the page equals the configured one; refuse otherwise | OWNER: needs the Playwright MCP extension |
| C3 | Search and play a song, and verify playback | SMTC reports Playing, the title matches, the position advances | MISSING |
| C4 | Open a project in VS Code | A VS Code window with the folder open | PRESENT (app launch); not verified |
| C5 | Find and open a generated PDF or PPTX | The file opens | PARTIAL |
| C6 | Create folders and organise selected files, with an undo journal | Approved plan executed; undo restores | MISSING |
| C7 | Stop Neko acting (visible stop, voice stop, kill switch) | The kill switch blocks new actions within a second | TESTED (kernel kill switch) |
| C8 | Confirm before high-impact actions | Policy tiers; approval needs the PIN | TESTED up to the PIN (PIN not set: OWNER) |

## 8. MCP

| ID | Requirement | Acceptance test | Status |
|---|---|---|---|
| P1 | Registry to inspect, enable, disable, configure and test servers | Console connector page | PARTIAL (JARVIS console connectors) |
| P2 | Minimal allowlisted set, tiered permissions, fingerprinting | Kernel connector tests | TESTED (D17 governance); new servers not added |

## 9. Memory and improvement

| ID | Requirement | Acceptance test | Status |
|---|---|---|---|
| L1 | Persistent preferences and project context | N.E.K.O memory | TESTED |
| L2 | Feedback on answers and documents, recorded as signals | A thumbs control writes a feedback row | MISSING |
| L3 | Task success and failure recorded; evals with regression comparison | `jarvis/evals` reports a delta | PARTIAL |
| L4 | Versioned prompts, skills and configs with rollback | Rolling back restores the previous version | PARTIAL (git; no runtime versioning) |
| L5 | No silent self-modification of core code, permissions or security rules | Policy | TESTED (kernel authority; skill gate) |

## 10. Security and settings

| ID | Requirement | Acceptance test | Status |
|---|---|---|---|
| S1 | Secrets stored outside code and logs; redacted diagnostics | gitleaks; never-dump rule; reference-only config | TESTED (file references in `D:\jarvis-secrets`); OS credential store not used |
| S2 | Untrusted-input handling for web, files and tool output | Results labelled untrusted; prompt-injection tests | PARTIAL |
| S3 | Audit history without secrets | Kernel audit | TESTED |
| S4 | Settings screen: models, downloads and disk, providers and keys, routing, hardware profile, MCP, permissions, voice, memory, logs and evals | Each panel reads live state | PARTIAL (N.E.K.O settings + JARVIS console) |
| S5 | Clear history and memories | A control exists and works | PRESENT (N.E.K.O memory UI); not checked |

## Non-functional requirements

- **N1, hardware:** must run on CPU-only 8 GB RAM with remote planning. This laptop is the 4 GB VRAM profile.
- **N2, responsiveness:** the UI never blocks; long tasks run as queued runs with progress.
- **N3, honesty:** no fabricated sources, progress, test results or completed actions.
- **N4, reversibility:** backups before data changes; rollback documented.
- **N5, cost:** no paid service without the owner's approval. Oracle Always Free stays at $0.
- **N6, privacy:** local first. Remote calls go only through the privacy gateway, and only in the public lane.
