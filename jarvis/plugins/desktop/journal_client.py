r"""The desktop plugin's thin client of the flight recorder (D61; the recorder is jarvis/node/jarvis_node/journal.py, the undo recipes are
jarvis/node/jarvis_node/undo.py). It turns what the plugin already knows about a step (the tool, its arguments, what the node observed, the verdict of
`coordinator.verify`) into one journal line, and decides the step's class and its undo recipe. Nothing here can stop an action: every public function
catches its own errors, and with no journal (not on the laptop, not on D:) every one of them does nothing.

Where it hooks in (all additive, a few lines each):
  - `_agents()` wraps every step runner (`wrap_agent`): tasks, goals and missions all run steps through them, so each step and each retry is one line;
  - `_run_task` and `do_goal` open a task scope (`enter_task`): a goal's steps share one task id and the owner's own words;
  - the single-action entries carry `@journaled(...)`;
  - `Pc.run` callers note the node job ids (`note_job`) so the before/after pictures the node took (named after the job) are found, and the step
    runners' `pc()` calls `before_job` / `after_job` around every kernel job: a file or setting job that worked is a line of its own with its
    undo recipe (a catalogue task such as "move a file" or "dark mode" is journaled by what it DID, not as one opaque step).

Class (the kernel's effect classes, D54): A = ordinary and local, what the owner's words cover (look, open, focus, type, a NEW file or folder, a
copy); B = changes something that was there, a setting, the network, or runs something (rename, move, sort, a setting, a click in a web page, typing
in a terminal); C = deletes, repairs, or anything held for the owner's PIN. Verified: `verified` only when the step passed and nothing was left unobserved,
`unconfirmed` when the node said ok but it was not seen, `failed` otherwise.

Privacy: labels are fixed phrases plus an app or site name, cleaned like every other plugin text (`screen_text.clean_message`); typed text is
never in a line (only its length); a file is named by its class ("a .docx file"); the recipe keeps the real path (it must, to undo) and stays
in the local journal. The chat model never gets a recipe or a picture (jarvis/bridge/timeline.py).
"""
from __future__ import annotations

import contextvars
import functools
import os
import re
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from jarvis.node.jarvis_node import journal as J
from jarvis.node.jarvis_node import undo as U
from jarvis.plugins._shared import screen_text

from . import coordinator as coord

READS = {"check_pc", "stop"}
BROWSER_READS = {"read", "find", "tabs", "whoami", "media"}
SAVE_KEYS = {"ctrl+s", "ctrl+shift+s"}
WATCH = ("Documents", "Desktop", "Downloads", "Pictures", "Music", "Videos")
WATCH_DEPTH = 2
WATCH_CAP = 4000
NEW_FILES_MAX = 5

_STATE: dict[str, Any] = {"journal": None, "tried": False}
_TASK: "contextvars.ContextVar[TaskCtx | None]" = contextvars.ContextVar("journal_task", default=None)
_STEP: "contextvars.ContextVar[StepTok | None]" = contextvars.ContextVar("journal_step", default=None)


# ------------------------------------------------------------------------------------------------------------------------------- the journal
def get_journal() -> "J.Journal | None":
    """The journal of this process (None off the laptop or off D:). One instance, so the Timeline panel hears every append."""
    if not _STATE["tried"]:
        _STATE["tried"] = True
        try:
            root, _why = J.default_root()
            _STATE["journal"] = J.Journal(root) if root is not None else None
        except Exception:  # noqa: BLE001
            _STATE["journal"] = None
    return _STATE["journal"]


def use(journal: "J.Journal | None") -> None:
    """Tests (and hosts that own a journal) set it."""
    _STATE.update(journal=journal, tried=True)


def watch_roots() -> list[str]:
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return [os.path.join(home, w) for w in WATCH]


# -------------------------------------------------------------------------------------------------------------------------------- task scope
@dataclass
class TaskCtx:
    task_id: str
    goal: str
    started: float
    snapshot: "dict | None" = None
    saw_save: bool = False
    steps: list = field(default_factory=list)       # ids of the lines written in this task


def enter_task(goal: str) -> Any:
    """Open the scope of one owner request. A scope already open (a goal around a task) wins: the steps belong to the goal. Returns a token."""
    try:
        if _TASK.get() is not None or get_journal() is None:
            return None
        return _TASK.set(TaskCtx(time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3), screen_text.clean_message(goal, 400), time.time()))
    except Exception:  # noqa: BLE001
        return None


def exit_task(token: Any) -> None:
    """Close the scope; a task that pressed a save key gets one line per NEW file that appeared in the owner's folders (the new-file rule)."""
    if token is None:
        return
    ctx = _TASK.get()
    try:
        if ctx is not None and ctx.saw_save and ctx.snapshot is not None:
            _record_new_files(ctx)
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            _TASK.reset(token)
        except ValueError:
            pass


def snapshot_files(roots: "list[str] | None" = None) -> dict:
    """{path: (size, mtime_ns)} of the owner's folders, two levels deep, at most WATCH_CAP entries."""
    out: dict = {}
    for root in roots if roots is not None else watch_roots():
        base_depth = root.rstrip("\\/").count(os.sep)
        for base, dirs, files in os.walk(root):
            if base.rstrip("\\/").count(os.sep) - base_depth >= WATCH_DEPTH - 1:
                dirs[:] = []
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                try:
                    st = os.lstat(os.path.join(base, name))
                except OSError:
                    continue
                out[os.path.join(base, name)] = (st.st_size, st.st_mtime_ns)
                if len(out) >= WATCH_CAP:
                    return out
    return out


def _born(path: str) -> float:
    st = os.stat(path)
    return float(getattr(st, "st_birthtime", st.st_ctime))


def _record_new_files(ctx: TaskCtx) -> None:
    after = snapshot_files()
    fresh = [p for p in after if p not in ctx.snapshot and not os.path.basename(p).startswith(("~", "."))]
    made = []
    for p in sorted(fresh, key=lambda p: after[p][1], reverse=True):
        try:
            if _born(p) >= ctx.started - 2:
                made.append(p)
        except OSError:
            continue
    j = get_journal()
    for p in made[:NEW_FILES_MAX]:
        rec = U.recipe_recycle_new(p)
        if rec is None or j is None:
            continue
        _write(j, ctx, label=f"Saved {screen_text.file_class(p)}", tool="save", cls="B", target="", result="a new file appeared in your folders",
               verified="verified", ms=0, shots=None, undo=rec)


# ------------------------------------------------------------------------------------------------------------------------------ one step
@dataclass
class StepTok:
    tool: str
    step: dict
    t0: float
    jobs: list = field(default_factory=list)
    ctx_token: Any = None
    effects: int = 0                     # lines `after_job` wrote for this step's file and setting jobs


def note_job(job_id: Any) -> None:
    """A node job belongs to the step running now (so its before/after pictures can be found)."""
    try:
        tok = _STEP.get()
        if tok is not None and job_id:
            tok.jobs.append(str(job_id))
    except Exception:  # noqa: BLE001
        pass


def begin(tool: str, step: dict) -> "StepTok | None":
    try:
        if get_journal() is None:
            return None
        ctx = _TASK.get()
        if ctx is not None and tool == "press_key" and str(step.get("key") or "").lower() in SAVE_KEYS:
            ctx.saw_save = True
            if ctx.snapshot is None:
                ctx.snapshot = snapshot_files()
        tok = StepTok(tool, {k: v for k, v in step.items() if not k.startswith("_")}, time.time())
        tok.ctx_token = _STEP.set(tok)
        return tok
    except Exception:  # noqa: BLE001
        return None


def end(tok: "StepTok | None", res: Any, raised: bool = False) -> None:
    if tok is None:
        return
    try:
        _STEP.reset(tok.ctx_token)
    except (ValueError, TypeError):
        pass
    try:
        j = get_journal()
        if j is None:
            return
        res = res if isinstance(res, dict) else {"ok": False, "error": f"the agent returned a {type(res).__name__}"}
        passed, checked, unc = _verdict(tok.step, tok.tool, res)
        if tok.tool == "simple_task" and tok.effects and passed and not raised:
            return                                                   # each of its file and setting jobs is a line of its own (with its own undo recipe)
        cls, label, target = classify(tok.tool, tok.step, res)
        verified = "failed" if not passed and res.get("ok") is not True else ("verified" if passed and not unc else "unconfirmed")
        if raised:
            verified = "failed"
        if res.get("state") in ("waiting_for_permission", "denied", "cancelled"):
            verified = "failed"
            checked = {"waiting_for_permission": "waiting for the owner's approval", "denied": "the owner did not approve it", "cancelled": "stopped by the owner"}[res["state"]]
        undo = recipe_for(tok.tool, tok.step, res, passed, tok.t0)
        if res.get("state") == "waiting_for_permission" or res.get("approval_id"):
            cls = "C"
        said = (checked or "") + (f" ({unc})" if unc else "")
        _write(j, _TASK.get(), label=label, tool=tok.tool, cls=cls, target=target, result=said,
               verified=verified, ms=int((time.time() - tok.t0) * 1000), shots=shots_for(j, tok.jobs), undo=undo if verified != "failed" else None, goal_fallback=label)
    except Exception:  # noqa: BLE001 - the recorder never breaks an action
        pass


def _verdict(step: dict, tool: str, res: dict) -> tuple[bool, str, str]:
    try:
        passed, checked, unc = coord.verify(dict(step, tool=tool), res)
    except Exception:  # noqa: BLE001
        passed, checked, unc = res.get("ok") is True, "the action reported success" if res.get("ok") is True else "the action did not succeed", ""
    if not passed and res.get("error") and checked in ("the action did not succeed",):
        checked = "did not succeed: " + str(res["error"])
    return bool(passed), str(checked or ""), str(unc or "")


def wrap_agent(tool: str, fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
    """A step runner that writes one line per run (a retry is a second line: it is a second action)."""
    @functools.wraps(fn)
    def run(step):
        tok = begin(tool, step if isinstance(step, dict) else {})
        try:
            res = fn(step)
        except BaseException as exc:
            end(tok, {"ok": False, "error": type(exc).__name__}, raised=True)
            raise
        end(tok, res)
        return res
    return run


def journaled(kind: str) -> Callable:
    """For the single-action entries (`async def entry(self, **kw)` returning Ok/Err): `kind` names how the call reads as a step."""
    def deco(fn):
        @functools.wraps(fn)
        async def run(self, *a, **kw):
            step = pseudo_step(kind, kw)
            tok = begin(step["tool"], step)
            try:
                out = await fn(self, *a, **kw)
            except BaseException as exc:
                end(tok, {"ok": False, "error": type(exc).__name__}, raised=True)
                raise
            value = getattr(out, "value", None)
            err = getattr(out, "error", None)
            res = dict(value) if isinstance(value, dict) else {"ok": False, "error": str(getattr(err, "message", err) or "the entry failed")[:120]}
            if not isinstance(value, dict) or "ok" not in res:
                res.setdefault("ok", value is not None and err is None)
            end(tok, res)
            return out
        run.journaled = kind
        return run
    return deco


# ---------------------------------------------------------------------------------------------------------------------- labels and classes
def _c(text: Any, n: int = 60) -> str:
    return J.text(screen_text.clean_message(text, n), n)


def _name(app: Any) -> str:
    a = _c(app, 40)
    return a[:1].upper() + a[1:] if a else "the app"


def _host(url: str, site: str = "") -> str:
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    return _c(host or site or "a website", 60)


def _key(key: Any) -> str:
    k = str(key or "").strip().lower()
    return k if re.fullmatch(r"[a-z0-9]+(\+[a-z0-9]+){0,3}", k) and (len(k) > 1) else "a key"


def pseudo_step(kind: str, kw: dict) -> dict:
    """How an entry call reads as a coordinator step (for its label, class and verdict)."""
    if kind == "run_pc_action":
        a, t, text = str(kw.get("action", "")), str(kw.get("target", "")), str(kw.get("text", ""))
        app = t.partition("|")[0]
        return {"open_app": {"tool": "open_app", "app": t}, "type": {"tool": "type_text", "app": app, "text": text}, "key": {"tool": "press_key", "app": app, "key": text},
                "focus": {"tool": "window", "action": "focus", "app": app}, "minimize": {"tool": "window", "action": "minimize", "app": app},
                "maximize": {"tool": "window", "action": "maximize", "app": app}, "restore": {"tool": "window", "action": "restore", "app": app},
                }.get(a, {"tool": "ui_" + (a or "action"), "app": app})
    if kind == "open_website":
        return {"tool": "open_website", "url": str(kw.get("url", "")), "profile": str(kw.get("profile", "default"))}
    if kind == "play_youtube":
        return {"tool": "play_media", "query": "", "profile": str(kw.get("profile", "default"))}
    if kind == "run_browser_action":
        return {"tool": "browser", "action": str(kw.get("action", "read")), "profile": str(kw.get("profile", "default")), "target": "", "text": str(kw.get("text", ""))}
    if kind == "run_troubleshooter":
        return {"tool": "troubleshoot", "app": str(kw.get("app", ""))}
    if kind == "check_pc":
        return {"tool": "check_pc", "check": str(kw.get("check", "disk"))}
    return {"tool": kind}


TERMINALS = {"windowsterminal", "wt", "cmd", "conhost", "powershell", "pwsh", "bash", "wsl", "mintty", "alacritty", "putty", "terminal", "git-bash", "gitbash"}
DELETING = {"trash", "delete_permanent", "empty_bin", "cleanup"}
SIMPLE_SKIP_NAME = re.compile(r"\s*\([^)]*\)")


def _terminal(app: Any) -> bool:
    return str(app or "").partition("|")[0].strip().lower().removesuffix(".exe") in TERMINALS


def _task_entry(step: dict) -> "dict | None":
    try:
        from . import simple_tasks
        return simple_tasks.get(str(step.get("task", "")))
    except Exception:  # noqa: BLE001
        return None


def classify(tool: str, step: dict, res: dict) -> tuple[str, str, str]:
    """(class, step label, target label). The class is the kernel's effect class (D54): A = ordinary and local, what the owner's words cover (look, open,
    focus, type, a NEW file or folder, a copy); B = changes something that was there, a setting, the network, or runs something (rename, move, a
    sort, a setting, a click in a page, typing in a terminal); C = deletes, repairs, or anything that was held for the owner's PIN."""
    app = step.get("app") or res.get("process") or ""
    waiting = res.get("state") in ("waiting_for_permission", "denied") or bool(res.get("approval_id"))
    if tool == "open_app":
        return "A", f"Open {_name(step.get('app'))}", _name(step.get("app"))
    if tool == "open_website":
        h = _host(str(step.get("url", "")), str(step.get("site", "")))
        return "A", f"Open {h}", h
    if tool == "play_media":
        return "A", "Play something on YouTube", "YouTube"
    if tool == "browser":
        act = str(step.get("action") or "read")
        words = {"read": "Read a page in Chrome", "find": "Find text in Chrome", "tabs": "List the Chrome tabs", "whoami": "Check the Chrome account",
                 "media": "Check what is playing", "click": "Click in Chrome", "type": "Type into a page in Chrome", "press": "Press a key in Chrome",
                 "open": "Open a page in Chrome", "play": "Play in Chrome"}
        return ("C" if waiting else "B" if act in ("click", "type", "press") else "A"), words.get(act, "Do something in Chrome"), "Chrome"
    if tool == "window":
        act = str(step.get("action") or "focus")
        if act == "inspect":
            return "A", f"Read the {_name(app)} window", _name(app)
        if act == "focus":
            return "A", f"Bring the {_name(app)} window to the front", _name(app)
        return "A", f"{act.capitalize() if act in ('minimize', 'maximize', 'restore') else 'Use'} the {_name(app)} window", _name(app)
    if tool == "type_text":
        return ("C" if waiting else "B" if _terminal(app) else "A"), f"Type {len(str(step.get('text') or ''))} characters into {_name(app)}", _name(app)
    if tool == "press_key":
        if res.get("done") == "nothing to do":
            return "A", "Check the media state", ""
        return ("C" if waiting else "B" if _terminal(app) else "A"), f"Press {_key(step.get('key'))}" + (f" in {_name(app)}" if app else ""), _name(app) if app else ""
    if tool == "open_file":
        name = str(step.get("name") or "")
        known = {"downloads", "documents", "desktop", "pictures", "videos", "music"}
        n = re.sub(r"(?i)\b(my|the|folder)\b", "", name).strip().lower()
        return "A", (f"Open the {n.title()} folder" if n in known else f"Open {screen_text.file_class(name)}"), ""
    if tool == "open_in_vscode":
        return "A", "Open a folder in VS Code", "VS Code"
    if tool == "type_in_editor":
        n = res.get("typed_chars")
        return ("C" if waiting else "B" if _terminal(app) else "A"), f"Type {n if isinstance(n, int) else len(str(step.get('text') or ''))} characters into {_name(app)}", _name(app)
    if tool == "save_as":
        where = _c(res.get("folder") or "", 40)
        return ("C" if waiting else "A"), f"Save {screen_text.file_class(res.get('saved') or step.get('name') or '')}" + (f" in {where}" if where else ""), where
    if tool == "drive":
        return ("C" if waiting else "B"), "Do a task by looking at the screen", _name(app) if app else ""
    if tool == "simple_task":
        e = _task_entry(step) or {}
        steps = [s.get("op") for s in (e.get("how") or {}).get("steps") or [] if isinstance(s, dict)]
        cls = "C" if waiting or DELETING & set(steps) else "A"
        return cls, _c(SIMPLE_SKIP_NAME.sub("", str(e.get("name") or "A simple task")), 60), ""
    if tool == "check_pc":
        chk = str(step.get("check") or step.get("action") or "disk")
        chk = chk if chk in {"processes", "disk", "network", "services", "app_info", "crashes", "media"} else "the PC"
        return "A", f"Check {chk.replace('_', ' ')}", ""
    if tool == "troubleshoot":
        return "C", f"Troubleshoot {_name(app)}", _name(app)
    if tool == "research":
        made = step.get("deliverable", "none")
        return "A", "Research a topic" + (f" and make a {made}" if made and made != "none" else ""), ""
    if tool == "web_task":
        return ("C" if waiting else "B"), "Make a deck on a free web generator", str(res.get("site") or "")[:60]
    if tool == "open_output":
        return "A", "Open a file JARVIS made", ""
    if tool == "stop":
        return "A", "Stop the PC task", ""
    if tool == "whats_on_screen":
        return "A", "Look at which windows are open", ""
    if tool == "read_window_text":
        return "A", "Read the text of a window", ""
    if tool == "find_on_screen":
        return "A", "Find something in a window", ""
    if tool == "save":
        return "A", "Save a file", ""
    return ("C" if waiting else "B"), tool.replace("_", " ").strip().capitalize()[:60] or "A PC action", _name(app) if app else ""


def recipe_for(tool: str, step: dict, res: dict, passed: bool, started: float) -> "dict | None":
    """The undo recipe of a step that worked (None when there is nothing to undo, `cannot_undo` when there is something that cannot be)."""
    if not passed:
        return None
    try:
        launched = res.get("launched")
        if tool in ("open_app", "open_file", "open_in_vscode") and res.get("verified") == "verified" and isinstance(launched, list) and launched:
            first = launched[0]
            if isinstance(first, dict) and first.get("proc"):
                return U.recipe_close_window(str(first["proc"]), int(first.get("pid") or 0), started)
        if tool in ("type_text", "type_in_editor"):
            return U.recipe_cannot("typed text cannot be taken back (if it was saved as a new file, that file can be undone)")
        if tool == "save_as" and res.get("saved"):
            base = folder_path(str(res.get("folder") or ""))
            return U.recipe_recycle_new(os.path.join(base, str(res["saved"]))) if base else None
        if tool == "browser" and step.get("action") in ("click", "type", "press"):
            return U.recipe_cannot("what was done in the web page cannot be taken back from here")
        if tool == "troubleshoot" and res.get("outcome") == "fixed and verified":
            return U.recipe_cannot("a repair: the troubleshooter keeps a backup of the changed file")
        if tool == "research" and res.get("file"):
            path = str(res["file"])
            try:
                if os.path.isfile(path) and _born(path) >= started - 2:
                    return U.recipe_recycle_new(path)
            except OSError:
                return None
        if tool == "simple_task":
            e = _task_entry(step) or {}
            if (e.get("how") or {}).get("handler") in ("type_active", "paste"):
                return U.recipe_cannot("typed text cannot be taken back")
    except Exception:  # noqa: BLE001
        return None
    return None


# ----------------------------------------------------------------------------------------------------- the effects of file and setting jobs (D54)
_ROOTS: dict[str, Any] = {"roots": None}


def use_roots(roots: "dict | None") -> None:
    """Tests set the known folders ({"documents": path, ...}); None forgets them."""
    _ROOTS["roots"] = roots


def known_roots() -> dict:
    if _ROOTS["roots"] is None:
        try:
            from jarvis.node.jarvis_node import savefile
            _ROOTS["roots"] = savefile.folder_roots()
        except Exception:  # noqa: BLE001
            _ROOTS["roots"] = {}
    return _ROOTS["roots"]


def folder_path(label: str) -> str:
    """The real path of a folder label the node gave ("Documents", "Documents/notes"); "" when it is not one of the known folders."""
    parts = [p for p in re.split(r"[\\/]+", str(label or "")) if p]
    root = known_roots().get(parts[0].lower()) if parts else None
    return os.path.join(root, *parts[1:]) if root else ""


def _parts(path: Any) -> list:
    return [p for p in re.split(r"[\\/]+", str(path or "")) if p]


SETTING_OF = {"dark_mode": "dark_mode", "light_mode": "dark_mode", "dnd": "dnd", "brightness": "brightness", "wallpaper": "wallpaper"}
_READER: dict[str, Any] = {"fn": None}


def use_setting_reader(fn: "Callable[[str], Any] | None") -> None:
    """Tests give a fake reader of the current value of a setting."""
    _READER["fn"] = fn


def read_setting(name: str) -> Any:
    try:
        if _READER["fn"] is not None:
            return _READER["fn"](name)
        return U.WinSettings().get(name)                 # Windows only; anywhere else it raises and the value stays unknown
    except Exception:  # noqa: BLE001
        return None


def before_job(body: dict) -> "dict | None":
    """Called just before a kernel job runs: a setting job reads the current value (it is what an undo puts back). Never raises."""
    try:
        if get_journal() is None or body.get("op") != "setting":
            return None
        name = SETTING_OF.get(str(body.get("action") or ""))
        return {"name": name, "previous": read_setting(name)} if name else None
    except Exception:  # noqa: BLE001
        return None


def _setting_new(body: dict, name: str) -> str:
    act, val = str(body.get("action") or ""), str(body.get("text") or "").strip()
    if name == "dark_mode":
        return "dark" if act == "dark_mode" else "light"
    if name == "dnd":
        return "off" if val.lower() in ("off", "0", "false") else "on"
    return val.rpartition("|")[2] if name == "wallpaper" else val


def _file_effect(body: dict, r: dict) -> "tuple | None":
    """(label, class, target, result, recipe) of one file job that worked, or None for a job that changed nothing (find, reveal)."""
    act, where = str(body.get("action") or ""), _c(r.get("folder") or "", 40)
    base = folder_path(str(r.get("folder") or ""))
    given = _parts(body.get("path"))
    fc = screen_text.file_class
    if act == "mkdir" and r.get("created"):
        return (f"Create a folder in {where}", "A", where, "the folder exists",
                U.recipe_recycle_new(os.path.join(base, str(r["created"]))) if base else None)
    if act == "create_file" and r.get("created"):
        return (f"Create {fc(r['created'])} in {where}", "A", where, "the file is on disk" + (", its text was read back" if r.get("content_ok") else ""),
                U.recipe_recycle_new(os.path.join(base, str(r["created"]))) if base else None)
    if act == "rename" and r.get("renamed_to") and given and base:
        src = os.path.join(base, *given)
        return (f"Rename {fc(given[-1])} in {where}", "B", where, "it has its new name", U.recipe_move_back(src, os.path.join(os.path.dirname(src), str(r["renamed_to"]))))
    if act in ("move", "copy"):
        name = r.get("moved") or r.get("copied")
        src_base, dst_base = folder_path(str(r.get("from") or "")), folder_path(str(r.get("to") or ""))
        if not name or not dst_base:
            return None
        dst = os.path.join(dst_base, str(name))
        if act == "copy":
            return (f"Copy {fc(name)} to {_c(r.get('to') or '', 40)}", "A", _c(r.get("to") or "", 40), "the copy is there", U.recipe_recycle_new(dst))
        return (f"Move {fc(name)} to {_c(r.get('to') or '', 40)}", "B", _c(r.get("to") or "", 40), "it is in the new folder",
                U.recipe_move_back(os.path.join(src_base, *given), dst) if src_base and given else None)
    if act == "zip" and r.get("archive") and base:
        return (f"Zip {fc(given[-1] if given else '')} in {where}", "A", where, "the archive is on disk",
                U.recipe_recycle_new(os.path.join(base, *given[:-1], str(r["archive"]))))
    if act == "unzip" and r.get("unpacked_to") and base:
        return (f"Unpack {fc(given[-1] if given else '')} in {where}", "A", where, f"{int(r.get('files') or 0)} files unpacked into a new folder",
                U.recipe_recycle_new(os.path.join(base, *given[:-1], str(r["unpacked_to"]))))
    if act == "sort_downloads":
        moved = int(r.get("moved") or 0)
        pairs = [(os.path.join(base, str(m[0])), os.path.join(base, str(m[1]), str(m[2]))) for m in r.get("moves") or [] if isinstance(m, (list, tuple)) and len(m) == 3] if base else []
        return (f"Sort {moved} files in {where}", "B", where, f"{moved} files moved into type folders", U.recipe_moves(pairs) if moved else None)
    if act == "trash" and r.get("trashed") and base:
        name = str(r["trashed"])
        return (f"Move {fc(name)} to the Recycle Bin", "C", where, "it is in the Recycle Bin",
                U.recipe_restore_bin(os.path.join(base, *given[:-1], name), size=int(r["bytes"]) if os.path.splitext(name)[1] and r.get("bytes") is not None else None))
    if act == "delete_permanent" and r.get("deleted"):
        return (f"Delete {fc(r['deleted'])} for good", "C", where, "it is gone and not in the Recycle Bin", U.recipe_cannot("deleted for good: it is not in the Recycle Bin"))
    if act == "empty_bin":
        return ("Empty the Recycle Bin", "C", "", "the Recycle Bin is empty", U.recipe_cannot("the Recycle Bin was emptied for good"))
    if act == "cleanup":
        return ("Clean up the PC (the safe list)", "C", "", "the fixed safe list was removed",
                U.recipe_cannot("caches were removed for good and the Recycle Bin was emptied; nothing can be put back from here"))
    if act == "undo":
        return ("Undo the last file move", "B", "", "put back by the file journal", None)
    return None


def after_job(body: dict, r: dict, pre: "dict | None", t0: float) -> None:
    """Called after a kernel job came back (`r` is the plugin's flattened result): a file or setting job that WORKED is one journal line of its own,
    with its undo recipe. The step that ran it (a catalogue task) then writes no second line. Never raises."""
    try:
        j = get_journal()
        op = body.get("op")
        if j is None or op not in ("file", "setting") or not r.get("ok") or r.get("state") not in (None, "done"):
            return
        tok = _STEP.get()
        ms = int((time.time() - t0) * 1000)
        if op == "file":
            got = _file_effect(body, r)
            if got is None:
                return
            label, cls, target, said, recipe = got
            tool = "file"
        else:
            name = SETTING_OF.get(str(body.get("action") or ""))
            nice = str(r.get("setting") or body.get("action") or "a setting")[:60]
            prev = (pre or {}).get("previous")
            recipe = U.recipe_setting(name, prev, _setting_new(body, name)) if name and prev not in (None, "") else (
                U.recipe_cannot("the earlier value could not be read, so it cannot be put back from here") if name else None)
            label, cls, target, said, tool = f"Change {_c(nice, 40)}", "B", _c(nice, 40), "changed" + (", read back" if r.get("read_back") else ""), "setting"
        _write(j, _TASK.get(), label=label, tool=tool, cls=cls, target=target, result=said, verified="verified" if r.get("verified") is not False else "unconfirmed",
               ms=ms, shots=shots_for(j, tok.jobs[-1:]) if tok is not None else None, undo=recipe)
        if tok is not None:
            tok.effects += 1
    except Exception:  # noqa: BLE001 - the recorder never breaks an action
        pass


# --------------------------------------------------------------------------------------------------------------------------------- writing
def shots_for(j: "J.Journal", jobs: list) -> "dict | None":
    """The first 'before' and the last 'after' picture among the step's node jobs."""
    before = next((r for r in (J.Shots.ref(x, "before") for x in jobs) if j.shots.exists(r)), None)
    after = next((r for r in (J.Shots.ref(x, "after") for x in reversed(jobs)) if j.shots.exists(r)), None)
    return {"before": before, "after": after} if before or after else None


def _write(j: "J.Journal", ctx: "TaskCtx | None", *, label: str, tool: str, cls: str, target: str, result: str, verified: str, ms: int, shots: Any,
           undo: Any, goal_fallback: str = "") -> "dict | None":
    task_id = ctx.task_id if ctx else "solo-" + time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    goal = ctx.goal if ctx else (goal_fallback or label)
    entry = J.make_step(task_id=task_id, goal=goal, step_label=label, tool=tool, cls=cls, target=target, result=_c(result, 180), verified=verified,
                        duration_ms=ms, shots=shots, undo=undo)
    j.append(entry)
    if ctx is not None:
        ctx.steps.append(entry["id"])
    return entry


def record_call(ref: str, args: dict, ok: bool, value: Any, err: str, ms: int) -> None:
    """A helper entry another plugin ran for a goal (a document read, a deliverable opened): one line. Entries of this plugin are journaled by their own decorator."""
    try:
        j = get_journal()
        if j is None or ref.startswith("desktop:"):
            return
        words = {"doc_reader:read_document": ("A", "Read a document", screen_text.file_class(args.get("path", ""))),
                 "deliverables:list_deliverables": ("A", "List what JARVIS made", ""),
                 "deliverables:open_output": ("B", "Open a file JARVIS made", "")}.get(ref, ("B", "Run a helper", ""))
        v = value if isinstance(value, dict) else {}
        good = bool(ok) and v.get("ok") is not False
        _write(j, _TASK.get(), label=words[1], tool=ref.split(":")[0], cls=words[0], target=words[2],
               result="done" if good else _c(err or v.get("error") or "did not succeed", 160), verified="unconfirmed" if good else "failed", ms=ms, shots=None, undo=None)
    except Exception:  # noqa: BLE001
        pass


WEB_WORDS = {"click": "Click", "type": "Type into a field", "select": "Choose an option", "press": "Press a key", "scroll": "Scroll", "hover": "Hover", "check": "Tick a box",
             "goto": "Open a page", "back": "Go back", "reload": "Reload the page", "wait": "Wait for the page", "download": "Collect a download", "upload": "Upload a file",
             "drag": "Drag", "tab_select": "Switch tab", "tab_close": "Close a tab", "dialog": "Answer a page dialog", "xy_click": "Click a spot on the page",
             "cred_new": "Make a password for a site", "observe": "", "locate": "", "shot": ""}


def record_web_step(ev: dict) -> None:
    """D64: one content-free line per ACTION of the browser agent (browser_agent.py's `_log`). It carries the kind of action, the site's host, the class, whether it
    went through, how long it took: never a typed value, a page text, a label of what was clicked, an address path or a credential. A wall handed to the owner and a
    skipped phase are lines of their own (class A)."""
    try:
        j = get_journal()
        if j is None or not isinstance(ev, dict):
            return
        host = _c(ev.get("host") or "", 60)
        kind = ev.get("kind")
        if kind == "action":
            words = WEB_WORDS.get(str(ev.get("act")), "Act on a page")
            if not words:
                return                                                 # reading the page is not an action
            ok, res = bool(ev.get("ok")), str(ev.get("res") or "")
            cls = str(ev.get("cls") or "B") if str(ev.get("cls") or "B") in ("A", "B", "C") else "B"
            _write(j, _TASK.get(), label=f"{words} on {host}" if host else words, tool="browser_agent", cls=cls, target=host,
                   result="done" if ok else ("held for the owner's PIN" if res == "approval" else "did not succeed" + (f" ({_c(res, 30)})" if res else "")),
                   verified="unconfirmed" if ok else "failed", ms=int(ev.get("ms") or 0), shots=None, undo=None)
        elif kind in ("wall", "ask", "banner", "offlist", "fallback"):
            said = {"wall": "A step only the owner can do", "ask": "Asked the owner", "banner": "Closed a banner or pop-up", "offlist": "Left a page the task may not visit",
                    "fallback": "Tried the next way in"}[kind]
            _write(j, _TASK.get(), label=f"{said} on {host}" if host else said, tool="browser_agent", cls="A", target=host, result=_c(ev.get("wall") or "", 40) or "noted",
                   verified="unconfirmed", ms=0, shots=None, undo=None)
    except Exception:  # noqa: BLE001 - the recorder never breaks an action
        pass


def record_setting_change(name: str, previous: Any, new: Any, ok: bool, ms: int = 0) -> None:
    """For a settings change (dark mode, volume, brightness, wallpaper, do-not-disturb): call it with the value read BEFORE the change."""
    try:
        j = get_journal()
        if j is None:
            return
        _write(j, _TASK.get(), label=f"Change {str(name).replace('_', ' ')}", tool="setting", cls="B", target=str(name).replace("_", " "),
               result=("changed" if ok else "did not change"), verified="verified" if ok else "failed", ms=ms, shots=None,
               undo=U.recipe_setting(name, previous, new) if ok and previous is not None else None)
    except Exception:  # noqa: BLE001
        pass
