"""The browser agent's hands on the node (D64, 2026-10-26): every op of plugins/desktop/browser_agent.py, run through ONE pooled Playwright MCP
session on the owner's real Chrome. The kernel has already judged the job (policy.py); this module is the second lock:

  * the page the step acts on must really be on the host the job declares (https, exact host), and never a bank, wallet, password manager or AI chat site;
  * the element must really have the name the job declares (the plugin read it from the same page a moment ago; a page that changed under the click is
    refused with "the page changed", never clicked blind);
  * a password, the alias e-mail or the display name is typed only from a reference (@cred:password, @identity:email, @identity:name), only on the
    granted host, only into a field whose name fits, and the value is never returned, logged or kept: the result says how many characters;
  * a download is collected only from the MCP event or Chrome's downloads folder, checked and moved into the quarantine folder (browserdl.py);
  * an upload takes a file NAME and finds it only in the owner's known folders (never a path from a page or a model);
  * page text, tab titles and the accessibility snapshot go to a local result file on this laptop (outputs\\local-only), never into the job result.

CAPTCHA, "are you a robot", 2FA and sign-in pages are not bypassed here: observe says `challenge_required` / `auth_required` and the engine stops.
"""
import json
import os
import re
import time
import urllib.parse

from . import browsercred, browserdl, browserext as B, runtime
from .paths import local_result_dir, sweep_local
from .sensitive import ai_chat_site, sensitive_target

WAIT_CAP_S = 25.0
POLL_S = 1.0
ACTION_WORDS = re.compile(r"[a-z0-9]+")
DOWNLOADED = re.compile(r'^- Downloaded file (.+?) to "(.+)"\s*$', re.M)
UPLOAD_FOLDERS = ("Documents", "Desktop", "Downloads", "Pictures")


class OpError(B.BrowserError):
    pass


# ---------------------------------------------------------------- reading the page
def _words(s: str) -> set:
    return set(ACTION_WORDS.findall(str(s or "").lower()))


def same_name(actual: str, declared: str, role: str = "") -> bool:
    """The element called `actual` is the one the job declared: equal after case and spacing, or (unnamed element) the declared name is its role."""
    a, d = " ".join(str(actual or "").lower().split()), " ".join(str(declared or "").lower().split())
    if not a:
        return d == (role or "").lower() or d in ("", "unnamed")
    return a == d or (len(d) >= 4 and a.startswith(d)) or (len(a) >= 4 and d.startswith(a))


def tabs_info(text: str) -> list:
    """[(index, current, title, url)] from a browser_tabs list reply."""
    out = []
    for m in re.finditer(r"^\s*-\s*(\d+):\s*(\(current\))?\s*\[(.*?)\]\s*\((\S+?)\)\s*$", text or "", re.M):
        out.append((int(m.group(1)), bool(m.group(2)), m.group(3)[:120], m.group(4)[:300]))
    return out


def boxes(snapshot: str) -> dict:
    """{ref: (x, y, w, h)} in viewport CSS pixels (the server runs with --snapshot-boxes)."""
    out = {}
    for line in (snapshot or "").splitlines():
        r = re.search(r"\[ref=([^\]]+)\]", line)
        b = re.search(r"\[box=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+(?:\.\d+)?)\]", line)
        if r and b:
            out[r.group(1)] = tuple(float(b.group(i)) for i in range(1, 5))
    return out


def live_page(sess, entry=None) -> tuple:
    """(snapshot text, meta {url, title}, host, https) of the page the session is on now."""
    t = sess.call("browser_snapshot", {})
    _note_events(t, entry)
    meta = B.page_meta(t)
    u = urllib.parse.urlsplit(meta.get("url", ""))
    return t, meta, (u.hostname or "").lower().rstrip("."), u.scheme == "https"


def _note_events(text: str, entry) -> None:
    """Downloads the MCP server reports in its own ### Events section: remember the file it saved (its private output folder)."""
    if entry is None:
        return
    for m in DOWNLOADED.finditer(text or ""):
        p = m.group(2)
        if p not in entry.downloads:
            entry.downloads.append(p)


def guard(meta: dict, host: str, declared: str, need_https: bool = True) -> None:
    """The live page is where the job says it is, and not somewhere the node never acts."""
    url = meta.get("url", "")
    why = sensitive_target([url, host]) if host else ""
    if why:
        raise OpError("this page is a bank, wallet, broker or password-manager site: JARVIS does not act there")
    site = ai_chat_site(url)
    if site:
        raise OpError(f"{site} is open for you to use yourself: JARVIS does not read, click or type there (its terms forbid automated use)")
    if declared:
        if need_https and not str(url).startswith("https://"):
            raise OpError("the page is not an https page, so the grant for the site does not apply")
        if host != declared:
            raise OpError(f"the page is on {host or 'an unknown host'}, not on {declared}: the step was not done (the page may have moved on)")


# ---------------------------------------------------------------- resolving an element
def shown_name(snap: str, ref: str) -> str:
    """What a person reads on the element: its accessible name, else the first text inside it (a clickable <div> has no name of its own)."""
    lines = (snap or "").splitlines()
    for n, line in enumerate(lines):
        if f"[ref={ref}]" not in line:
            continue
        m = re.search(r"""-\s+'?[a-z]+\s+"(.+?)"(?:\s+\[[^\]]*\])*?\s*\[ref=""", line)
        if m:
            return m.group(1)
        depth = len(line) - len(line.lstrip())
        for nxt in lines[n + 1:n + 12]:
            d2 = len(nxt) - len(nxt.lstrip())
            if d2 <= depth:
                break
            t = re.match(r"\s*-\s+text:\s*(.+)$", nxt)
            if t:
                return t.group(1).strip().strip("'\"")[:100]
        return ""
    return ""


def resolve(sess, snap: str, p: dict, roles=None, field: bool = False) -> tuple:
    """(role, name, ref) of the element the job means, checked against the live snapshot."""
    target, ref = p.get("target", ""), p.get("ref", "")
    if p.get("sel") and not ref:
        # an XPath / CSS hint from a playbook: the server snapshots just that element, and its name must still be the declared one
        part = sess.call("browser_snapshot", {"target": p["sel"]})
        found = B.elements(part)
        if not found:
            raise OpError("the hint no longer finds an element: observe the page again")
        role, name, ref = found[0]
        if not same_name(name, target, role):
            raise OpError(f"the page changed: the hint now finds '{name[:60]}', not '{target[:60]}'")
        return role, name, ref
    if ref:
        el = B.element_by_ref(snap, ref)
        if el is None:
            raise OpError(f"element {ref} is not on the page now: observe the page again")
        role, name, _ = el
        shown = name or shown_name(snap, ref)
        if not same_name(shown, target, role):
            raise OpError(f"the page changed: {ref} is now '{shown[:60]}', not '{target[:60]}'. Observe the page again")
        return role, shown, ref
    ref = B.find_ref(snap, target, roles or (B.TYPABLE if field else B.CLICKABLE))
    el = B.element_by_ref(snap, ref) or ("", target, ref)
    return el[0], el[1], ref


def _refuse_if_human_check(snap: str, url: str, name: str) -> None:
    if B.challenge_required(snap, url) and (B.CHALLENGE.search(name or "") or B.challenge_required(name or "", "")):
        raise OpError("this page asks for a human check (CAPTCHA / are you a robot): JARVIS does not answer it; please complete it yourself, then continue")


# ---------------------------------------------------------------- the ops
def local_doc(kind: str, doc) -> str:
    return B.save_local(kind, json.dumps(doc, ensure_ascii=False))


def observe(sess, p, entry) -> dict:
    t, meta, host, https = live_page(sess, entry)
    guard(meta, host, p.get("host", ""))
    tabs_t = ""
    try:
        tabs_t = sess.call("browser_tabs", {"action": "list"})
    except B.BrowserError:
        pass
    tabs = tabs_info(tabs_t)
    path = local_doc("page", {"url": meta.get("url", ""), "title": meta.get("title", ""), "host": host, "https": https, "snapshot": t,
                              "tabs": [{"index": i, "current": c, "title": ti, "url": u} for i, c, ti, u in tabs]})
    return {"ok": True, **{k: meta.get(k, "") for k in ("url", "title")}, "host": host, "https": https, "chars": len(t), "tabs": len(tabs),
            "auth_required": B.auth_required(t, meta.get("url", "")), "challenge_required": B.challenge_required(t, meta.get("url", "")),
            "local_result": path}


def goto(sess, p, entry) -> dict:
    if p.get("text") == "new_tab":                       # the first page of a task: a tab of its own inside the connected Chrome
        sess.call("browser_tabs", {"action": "new", "url": p["url"]})
    else:
        B.navigate(sess, p["url"])
    t, meta, host, _ = live_page(sess, entry)
    guard(meta, host, "")
    return {"ok": True, **meta, "host": host}


def reload_(sess, p, entry) -> dict:
    _, meta, host, _ = live_page(sess, entry)
    guard(meta, host, p.get("host", ""))
    B.navigate(sess, meta["url"])
    return {"ok": True, **meta, "host": host}


def back(sess, p, entry) -> dict:
    sess.call("browser_navigate_back", {})
    t, meta, host, _ = live_page(sess, entry)
    guard(meta, host, "")
    return {"ok": True, **meta, "host": host}


def _acted(sess, p, entry, tool: str, args_fn, roles=None, field=False, after=None) -> dict:
    t, meta, host, _ = live_page(sess, entry)
    guard(meta, host, p.get("host", ""))
    role, name, ref = resolve(sess, t, p, roles, field)
    _refuse_if_human_check(t, meta.get("url", ""), name)
    if B.secret_field(role, name) and p["op"] == "type" and not browsercred.REF_RX.match(p.get("text", "")):
        raise OpError(B.NO_SECRET_TYPING)
    entry.before_dl = browserdl.baseline()
    entry.click_at = time.time()
    out = sess.call(tool, args_fn(ref, name, role))
    _note_events(out, entry)
    res = {"ok": True, "ref": ref, **B.page_meta(out)}
    if after:
        res.update(after(role, name, ref))
    return res


def click(sess, p, entry) -> dict:
    return _acted(sess, p, entry, "browser_click", lambda ref, name, role: {"target": ref, "element": (p.get("target") or name)[:80]})


def hover(sess, p, entry) -> dict:
    return _acted(sess, p, entry, "browser_hover", lambda ref, name, role: {"target": ref, "element": (p.get("target") or name)[:80]}, roles=None)


def check(sess, p, entry) -> dict:
    # browser_check is a CLI-skill tool in this server version; a click on a checkbox or radio does the same
    return _acted(sess, p, entry, "browser_click", lambda ref, name, role: {"target": ref, "element": (p.get("target") or name)[:80]},
                  roles=("checkbox", "radio", "switch", "menuitemcheckbox"))


def select(sess, p, entry) -> dict:
    vals = [v for v in p.get("text", "").split("|") if v][:5]
    return _acted(sess, p, entry, "browser_select_option", lambda ref, name, role: {"target": ref, "element": (p.get("target") or name)[:80], "values": vals},
                  roles=("combobox", "listbox", "option", "button", "textbox"))


def type_(sess, p, entry) -> dict:
    text = p.get("text", "")
    host = p.get("host", "")
    secret = {"value": ""}

    def args(ref, name, role):
        if browsercred.REF_RX.match(text):
            kind = browsercred.REF_RX.match(text).group(1) + ":" + browsercred.REF_RX.match(text).group(2)
            if not host:
                raise OpError("a stored value is typed only for a named site")
            if not browsercred.field_fits(text.lstrip("@"), role, name):
                raise OpError(f"'{name[:40]}' is not a field for {kind}: nothing was typed")
            try:
                secret["value"] = browsercred.resolve(text, host)
            except browsercred.CredError as e:
                raise OpError(str(e)[:160]) from None
            value = secret["value"]
            sess.add_secret(value, text.split(":", 1)[1])         # before the call: even Playwright's echo of this very call is redacted
        else:
            value = text
        return {"target": ref, "element": (p.get("target") or name)[:80], "text": value}

    def after(role, name, ref):
        return {"typed_chars": len(secret["value"] or text)}
    try:
        return _acted(sess, p, entry, "browser_type", args, roles=B.TYPABLE, field=True, after=after)
    finally:
        secret["value"] = ""                       # the value existed only inside this call


def press(sess, p, entry) -> dict:
    t, meta, host, _ = live_page(sess, entry)
    guard(meta, host, p.get("host", ""))
    key = p.get("text") or "Enter"
    if not re.fullmatch(r"[A-Za-z0-9+]{1,24}", key):
        raise OpError("that key name is not accepted")
    out = sess.call("browser_press_key", {"key": key})
    _note_events(out, entry)
    return {"ok": True, "pressed": key, **B.page_meta(out)}


def scroll(sess, p, entry) -> dict:
    t, meta, host, _ = live_page(sess, entry)
    guard(meta, host, p.get("host", ""))
    how = p.get("text", "down")
    if how == "into_view":
        role, name, ref = resolve(sess, t, p)
        sess.call("browser_hover", {"target": ref, "element": (p.get("target") or name)[:80]})      # Playwright scrolls an element into view before it acts on it
        return {"ok": True, "scrolled": "into_view"}
    if entry.vision and how in ("up", "down"):
        sess.call("browser_mouse_wheel", {"deltaX": 0, "deltaY": -700 if how == "up" else 700})
    else:
        sess.call("browser_press_key", {"key": {"up": "PageUp", "down": "PageDown", "top": "Home", "bottom": "End"}[how]})
    return {"ok": True, "scrolled": how}


def wait(sess, p, entry) -> dict:
    """Wait for a CONDITION, polling the snapshot: text appeared, text gone, the page stopped changing, or (seldom) a few seconds."""
    cond = p.get("text", "idle")
    kind, _, arg = cond.partition(":")
    end = time.monotonic() + WAIT_CAP_S
    if kind == "seconds":
        n = min(float(arg or 1), WAIT_CAP_S)
        for _ in range(int(n)):
            runtime.check()
            time.sleep(1.0)
        return {"ok": True, "waited": cond, "met": True}
    last, same, met = None, 0, False
    meta, host = {}, ""
    while time.monotonic() < end:
        runtime.check()
        t, meta, host, _ = live_page(sess, entry)
        guard(meta, host, p.get("host", ""))
        low = t.lower()
        if kind == "text" and arg.lower() in low:
            met = True
        elif kind == "gone" and arg.lower() not in low:
            met = True
        elif kind == "idle":
            same = same + 1 if t == last else 0
            last = t
            met = same >= 2
        if met:
            break
        time.sleep(POLL_S)
    return {"ok": True, "waited": kind, "met": met, **meta, "host": host}


def tab(sess, p, entry) -> dict:
    act = "select" if p["op"] == "tab_select" else "close"
    args = {"action": act}
    if p.get("text"):
        args["index"] = int(p["text"])
    out = sess.call("browser_tabs", args)
    t, meta, host, _ = live_page(sess, entry)
    guard(meta, host, "")
    return {"ok": True, "tabs": len(tabs_info(out)), **meta, "host": host}


def dialog(sess, p, entry) -> dict:
    out = sess.call("browser_handle_dialog", {"accept": p.get("text") == "accept"})
    return {"ok": True, "dialog": p.get("text"), **B.page_meta(out)}


def drag(sess, p, entry) -> dict:
    t, meta, host, _ = live_page(sess, entry)
    guard(meta, host, p.get("host", ""))
    _, n1, r1 = resolve(sess, t, p, roles=None)
    _, n2, r2 = resolve(sess, t, {"target": p.get("text", ""), "ref": ""}, roles=None)
    out = sess.call("browser_drag", {"startElement": n1[:80], "startTarget": r1, "endElement": n2[:80], "endTarget": r2})
    return {"ok": True, **B.page_meta(out)}


def xy_click(sess, p, entry) -> dict:
    """A click at page coordinates found by the local vision model or a snapshot box. The element there (from the snapshot's boxes) must not
    contradict the label the kernel judged (`text`)."""
    if not entry.vision:
        raise OpError("coordinate clicks are not enabled in this session")
    t, meta, host, _ = live_page(sess, entry)
    guard(meta, host, p.get("host", ""))
    x, y = (int(v) for v in p["target"].split(","))
    label = p.get("text", "")
    best = None
    for ref, (bx, by, bw, bh) in boxes(t).items():
        if bx <= x <= bx + bw and by <= y <= by + bh and (best is None or bw * bh < best[1]):
            best = (ref, bw * bh)
    if best:
        el = B.element_by_ref(t, best[0])
        if el and el[1] and label and not (_words(el[1]) & _words(label)) and el[0] in B.CLICKABLE:
            raise OpError(f"at that spot the page has '{el[1][:50]}', not '{label[:50]}': nothing was clicked")
    entry.before_dl = browserdl.baseline()
    entry.click_at = time.time()
    out = sess.call("browser_mouse_click_xy", {"x": x, "y": y})
    _note_events(out, entry)
    return {"ok": True, "clicked_at": [x, y], **B.page_meta(out)}


def png_size(path: str) -> tuple:
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise OpError("the screenshot is not a PNG")
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def shot(sess, p, entry) -> dict:
    """A picture of the page into outputs\\local-only (deleted within ten minutes). It goes only to the local vision model, never to an online one."""
    t, meta, host, _ = live_page(sess, entry)
    guard(meta, host, p.get("host", ""))
    import shutil
    name = f"shot-{int(time.time() * 1000)}.png"
    os.makedirs(local_result_dir(), exist_ok=True)
    dest = os.path.join(local_result_dir(), name)
    sess.call("browser_take_screenshot", {"type": "png", "filename": name})        # relative names are resolved against the server's private work folder
    src = os.path.join(sess.work, name)
    if not os.path.isfile(src):
        raise OpError("the screenshot file was not made")
    shutil.move(src, dest)
    sweep_local()
    w, h = png_size(dest)
    return {"ok": True, "width": w, "height": h, "local_result": dest}


def locate(sess, p, entry) -> dict:
    """Where on the page is `target`? The local vision model looks at a screenshot; the answer is a point (INFERRED) the engine checks and clicks."""
    from . import vision
    res = shot(sess, dict(p, op="shot"), entry)
    try:
        g = vision.ground_vision(res["local_result"], {"width": res["width"], "height": res["height"]}, p["target"])
    except vision.VisionError as e:
        return {"ok": False, "error": str(e)[:160]}
    finally:
        try:
            os.remove(res["local_result"])
        except OSError:
            pass
    if not g:
        return {"ok": True, "point": None, "basis": "inferred", "note": "the vision model did not find it"}
    return {"ok": True, "point": {"x": g["cx"], "y": g["cy"]}, "basis": "inferred", "found_by": "local vision model"}


def upload(sess, p, entry) -> dict:
    name = p["text"]
    path = find_upload(name)
    if not path:
        raise OpError("that file is not in the folders JARVIS may upload from")
    t, meta, host, _ = live_page(sess, entry)
    guard(meta, host, p.get("host", ""))
    role, el_name, ref = resolve(sess, t, p)
    sess.call("browser_click", {"target": ref, "element": (p.get("target") or el_name)[:80]})
    out = sess.call("browser_file_upload", {"paths": [path]})
    return {"ok": True, "uploaded": "a file of " + str(os.path.getsize(path)) + " bytes", **B.page_meta(out)}


def find_upload(name: str, roots=None) -> str:
    """The file called `name` in one of the owner's known folders (or JARVIS's outputs); a secret-looking name, a link or a program never."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.()\-]{0,79}", name or "") or re.search(r"(?i)secret|passw|token|\.env|\.pem|\.key|\.exe|\.bat|\.cmd|\.ps1|\.lnk", name):
        return ""
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    folders = roots if roots is not None else [os.path.join(home, f) for f in UPLOAD_FOLDERS] + [browserdl.outputs_root()]
    for f in folders:
        p = os.path.join(f, name)
        try:
            if os.path.isfile(p) and not os.path.islink(p):
                return os.path.realpath(p)
        except OSError:
            continue
    return ""


def download(sess, p, entry) -> dict:
    t, meta, host, _ = live_page(sess, entry)
    guard(meta, host, p.get("host", ""))
    exts = [e for e in p.get("ext", "").split(",") if e]
    since = entry.click_at or (time.time() - 120)
    res = None
    # 1. the MCP server saved the file itself and said so in its ### Events section
    end = time.monotonic() + 45
    while time.monotonic() < end and not entry.downloads:
        runtime.check()
        live_page(sess, entry)
        time.sleep(POLL_S)
    for path in list(entry.downloads):
        if os.path.isfile(path):
            res = collect_file(path, host, exts)
            entry.downloads.remove(path)
            break
    # 2. Chrome saved it into its own downloads folder
    if res is None:
        res = browserdl.collect(host, entry.before_dl or {}, exts, wait_s=60, since=since)
    return finish_download(res)


def collect_file(path: str, host: str, exts) -> dict:
    """A file the MCP server saved in its own output folder: the same checks, then the same move into quarantine."""
    import shutil
    ext = os.path.splitext(path)[1].lower()
    allowed = browserdl._wanted_ext(exts)
    if ext not in allowed:
        return {"ok": False, "refused": "type", "error": f"a {ext or 'file without a type'} file arrived; only {', '.join(e[1:] for e in allowed)} are taken"}
    if os.path.getsize(path) > browserdl.MAX_BYTES:
        return {"ok": False, "refused": "size", "error": "the file is larger than the cap"}
    try:
        browserdl.check_content(path, ext)
    except browserdl.DownloadError as e:
        return {"ok": False, "refused": "content", "error": str(e)}
    q = browserdl.quarantine_dir(host)
    os.makedirs(q, exist_ok=True)
    dest = browserdl._free_name(q, time.strftime("%Y%m%d-%H%M%S-") + browserdl.safe_name(os.path.basename(path)))
    shutil.move(path, dest)
    return {"ok": True, "name": os.path.basename(dest), "ext": ext[1:], "size": os.path.getsize(dest), "sha256": browserdl._sha256(dest),
            "source_verified": True, "source_host": host, "quarantine": True, "local_result_file": dest}


def finish_download(res: dict) -> dict:
    res = dict(res)
    dest = res.pop("local_result_file", None)
    if dest:
        res["local_result"] = local_doc("download", {"file": dest})
    return res


def cred_new(sess, p, entry) -> dict:
    t, meta, host, _ = live_page(sess, entry)
    guard(meta, host, p.get("host", ""))
    try:
        r = browsercred.new_cred(host)
    except browsercred.CredError as e:
        raise OpError(str(e)[:200]) from None
    return {"ok": True, **r, "note": "the password is saved in the secrets folder for this site; it is typed by the node and never shown"}


def session_end(sess, p, entry) -> dict:
    return {"ok": True, "ended": True}


OPS = {"observe": observe, "goto": goto, "reload": reload_, "back": back, "click": click, "hover": hover, "check": check, "select": select, "type": type_,
       "press": press, "scroll": scroll, "wait": wait, "tab_select": tab, "tab_close": tab, "dialog": dialog, "drag": drag, "xy_click": xy_click,
       "shot": shot, "locate": locate, "upload": upload, "download": download, "cred_new": cred_new, "session_end": session_end}
