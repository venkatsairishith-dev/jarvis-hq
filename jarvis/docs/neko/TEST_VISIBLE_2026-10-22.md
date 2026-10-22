# Testing where you can see it (2026-10-22)

Last night the tests ran hidden in the background, and every failure showed up as an error in the N.E.K.O chat or a log nobody watched.
From now on a test you are meant to watch runs in a normal window and shows what it is doing.

**Double-click** `D:\jarvis\hq-neko\jarvis\scripts\test-visible.cmd` (or run `test-visible.ps1`). It opens one visible window and:
1. **Pre-flight** (about a minute): the live health check, every `Jarvis*` scheduled task (a task that fails each time is an error waiting
   to appear: a script that no longer exists, a program that crashed), the wake listener, and room on D:. Any red line stops the run
   before anything starts, with the reason. `-SkipLiveCheck` makes it shorter.
2. **The 10-task baseline** (`jarvis/evals/neko95/task_baseline.py`): for each task it prints `>>> T03: saying "open Calculator"`, the
   real result (PASS/FAIL and why, judged by what is on the screen), and leaves the opened window up for 5 seconds so you can see it.
   Only windows it opened are closed; it refuses to start over your own Notepad or Calculator windows. `-Only T01,T03` runs some.
3. The window stays open at the end. A copy of everything printed is kept in `D:\jarvis\outputs\visible-tests` (nothing is written outside D:).

Rules for any session that tests for you: run it in a visible window (`Start-Process powershell -NoExit ...`), never with `-WindowStyle Hidden`;
make the pre-flight all green BEFORE you sit down; report real numbers; do not change a test to make it pass.

What the chat says when something fails now: the opening words are unchanged ("I could not plan that task: ...") followed by one plain
sentence on what to do: planner busy (rate limit: try again in a minute), planner key wrong, "say it again in one sentence after Hey Neko",
or planner answered in the wrong format.
