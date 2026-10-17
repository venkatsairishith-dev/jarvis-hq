# Desktop

Neko's hands on the Windows PC: websites and YouTube in the owner's existing Chrome profiles, apps through the keyboard,
mouse and accessibility tree, read-only PC checks, and a troubleshooter that backs up, fixes after approval and verifies.
Every action is a JARVIS kernel job: the kernel decides, and consequential actions wait for the owner's PIN in the
JARVIS console.

| Module | What it does |
|---|---|
| `decide.py` | Request -> plan. One simple command ("open notepad", "pause") is planned by rules in milliseconds; anything else goes to the small local model, whose JSON is forced by a grammar. Deterministic code then resolves the account, site and app against facts (Chrome profile registry, known sites, Start-menu apps) and checks every step. The plan is only a proposal. |
| `coordinator.py` | Runs one PC task: each step by the right agent (browser, desktop, troubleshooter, research), then checks the observed result against that step's success criterion. One PC task at a time, at most 600 s, one retry and only for a transient failure. Keeps a task record. |
| `missions.py` | Long goals with several phases ("research X, make slides, open them") as a durable DBOS workflow on SQLite: survives a restart, never repeats a side effect, re-plans at most twice when a check fails, stops on a loop, a budget (20 steps, 30 min) or the owner's word. |
| `skills.py` | Controlled self-improvement: Neko may propose new site names and example plans; only the owner promotes them, from the command line. |
| `core.py` | Talks to the JARVIS console's owner route, and `diagnose()`: what went wrong, from the error dialog, event log and crash records, checked against the files they name. |
| `monitor.py` | Read-only timer: crashes, low disk, local services that stop answering, stuck tasks. |

## Safety rules
**The decider asks instead of acting** (`decide.validate`, rules applied after the model):
- vague: "research it", "open the other one", "make a pdf" with no topic -> it asks what;
- negated: "don't open youtube" is never planned; if nothing is left it asks what to do instead. A Chrome account the
  owner excluded ("but not in Satish's profile") is never used, even when the model picks it;
- a choice: "open notepad or wordpad" -> "Which one: notepad or wordpad?";
- two different accounts named for one request -> it asks which.

Requests with a correction ("open notepad, no wait, wordpad") never take the rule fast path; the model plans them and
the step the owner took back is dropped. A model field of the wrong type is a problem, not a crash.

**Only a boolean `ok: True` is success.** `{"ok": "false"}`, an HTML error page or a list from an agent is a failed
step. "Done" is reported only for steps whose check passed.

**A stop names what already happened.** A task stopped part-way says "Stopped." and then lists the checked steps that ran
before the stop (a window opened, a file saved), because those stay on the PC.

**The skill gate:** a proposal may only add sites (new names, public https addresses) and example plans; it cannot change tools,
permissions or prompt rules. Its regression suite must run at least one case, lose no case the active version passes,
and not score lower. A suite of zero cases never passes. Every version is a file `versions/v<N>.json` with its sha256
in `registry.json`; version files are never overwritten, a damaged registry is refused instead of reset, and promote
and rollback are logged in `audit.log`.

**Diagnosis** (`core.diagnose`): a cause is "verified" only when the named file really has the fault; otherwise it is
a hypothesis. A fix is offered only when its text is unique in the file (widened to whole lines if needed).
Wrong-typed inputs are dropped, not trusted. Screen text stays on the laptop unless `NEKO_SCREEN_TEXT_TO_MODEL=1`.

## Owner commands
```
D:\jarvis\tools\neko-venv\Scripts\python.exe -m plugin.plugins.desktop.skills status|promote v3|rollback
```

## Tests
`python -m pytest plugin/plugins/desktop/tests -q -p no:cacheprovider` (no model, no Windows needed). On Linux run it
in the neko-test container:
```
podman run --rm --userns=keep-id -e HOME=/tmp -v ~/hq-neko:/src:O neko-test sh -c "cd /src && python -m pytest plugin/plugins/desktop/tests -q -p no:cacheprovider"
```
