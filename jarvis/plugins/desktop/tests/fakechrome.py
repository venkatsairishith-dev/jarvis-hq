"""A fake Chrome behind a fake Playwright MCP server, for the D64 browser-agent tests.

Real: the web pages are HTML files in a temp folder served over HTTP by a real local server (several virtual hosts: `<root>/<host>/<path>`); the page's
accessibility snapshot is built from the HTML in the exact text format of @playwright/mcp 0.0.83 ("- button "Generate" [ref=e7]", "[box=...]", "### Events"
with "Downloaded file"); the kernel's policy, job validator and approvals, the node's browserext + browserops + browserdl + browsercred, and the plugin's engine
are the REAL code (see chromeharness.py). Faked: Chrome itself (this module) and the console's HTTP.

A tiny behaviour language lives in data-* attributes of the test pages:
  data-on-click="hide:#id; show:#id; toggle:#id; text:#id=words; set:#field=value; goto:/path; open:/path; submit:/path; download:/file; delay:N:stmt;
                 alert:message; timeline:start; signin:google"   (statements run in order)
  data-hidden            hidden until a statement shows it            data-hidden-until="N"   appears after N ticks (a tick = one snapshot or wait)
  data-timeline="3:text:#status=Slide 1;6:show:#done"   runs once `timeline:start` has been executed        data-blocks   an overlay: elements outside it cannot be clicked
  data-esc-closes        Escape hides it                      data-hover-shows="#menu"   hovering shows it       data-load-more="#list"  scrolling adds items
  data-needs-session     the page needs a signed-in session (otherwise /login is served)                      data-icon-only   a button with no accessible name
  data-captcha           the page is a human check            data-disabled-until-text="#id=words"   a button enabled once a field holds the words
Nothing here is clever: it only has to be honest enough that the engine's verification-by-effect and recovery ladder are exercised for real.
"""
from __future__ import annotations

import html
import http.server
import json
import os
import re
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

VOID = {"input", "br", "hr", "img", "meta", "link"}
ROLE_OF_TAG = {"a": "link", "button": "button", "h1": "heading", "h2": "heading", "h3": "heading", "p": "paragraph", "select": "combobox", "textarea": "textbox",
               "dialog": "dialog", "iframe": "iframe", "li": "listitem", "option": "option", "nav": "navigation", "main": "main", "header": "banner", "label": "generic"}


class Node:
    _ids = 0

    def __init__(self, tag, attrs, parent=None):
        Node._ids += 1
        self.nid = Node._ids
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    def text(self) -> str:
        return " ".join(c.strip() if isinstance(c, str) else c.text() for c in self.children if (isinstance(c, str) and c.strip()) or isinstance(c, Node)).strip()

    def find(self, sel: str):
        if sel.startswith("#"):
            want = sel[1:]
            for n in self.walk():
                if n.attrs.get("id") == want:
                    return n
        return None

    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.walk()

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent


class Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {})
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        n = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur)
        self.cur.children.append(n)

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        if data.strip():
            self.cur.children.append(data)


def parse_html(src: str) -> Node:
    p = Parser()
    p.feed(src)
    return p.root


# ----------------------------------------------------------------------------------------------------------------------------- the web
class Web:
    """The virtual hosts, served by a real HTTP server from a folder, plus server-side hooks for forms and sessions."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.hooks: dict = {}            # (host, path) -> fn(world, form) -> statement string
        self.files: dict = {}            # (host, path) -> bytes (a generated file)
        self.signed_in: set = set()      # hosts with a session
        self.log: list = []              # (host, path) requests
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                parts = self.path.split("?")[0].lstrip("/").split("/", 1)
                host, path = parts[0], "/" + (parts[1] if len(parts) > 1 else "")
                outer.log.append((host, path))
                f = outer.root / host / (path.lstrip("/") or "index.html")
                if f.is_dir():
                    f = f / "index.html"
                elif not f.is_file() and (f.parent / (f.name + ".html")).is_file():
                    f = f.parent / (f.name + ".html")
                if (host, path) in outer.files:
                    body, code = outer.files[(host, path)], 200
                elif f.is_file():
                    body, code = f.read_bytes(), 200
                else:
                    body, code = b"<html><head><title>Not found</title></head><body><h1>Page not found</h1><p>404</p></body></html>", 404
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def page(self, host: str, path: str) -> str:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/{host}{path}", timeout=10) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.read().decode("utf-8", "replace")

    def put(self, host: str, path: str, content: str) -> None:
        f = self.root / host / path.lstrip("/")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")


class Tab:
    def __init__(self, url, root, title):
        self.url, self.root, self.title = url, root, title
        self.values: dict = {}           # node id -> typed value / checked
        self.shown: set = set()          # hidden-by-default nodes a statement showed
        self.hidden: set = set()         # nodes a statement hid
        self.texts: dict = {}            # node id -> replaced text
        self.tick = 0
        self.scheduled: list = []        # (due tick, statements)
        self.timeline_started = None
        self.timeline_done: set = set()
        self.history: list = []
        self.dialog = None               # an open JS dialog: message
        self.scrolls = 0
        self.hovered: set = set()
        self.loaded_more = 0
        self.frames: dict = {}           # iframe node id -> (url, root)
        self.refs: dict = {}             # ref -> Node
        self.boxes: dict = {}            # ref -> (x, y, w, h)
        self.clicked: list = []


class FakeChrome:
    """The Playwright MCP server + the owner's Chrome. One shared WORLD per test (set by the harness); a session object per MCP connection."""
    WORLD: "FakeChrome | None" = None

    def __init__(self, argv=None, env=None):
        self.argv, self.env = argv or [], env or {}
        self.work = self.argv[self.argv.index("--output-dir") + 1] if "--output-dir" in self.argv else ""
        if self.work:
            os.makedirs(self.work, exist_ok=True)

    # ---- the MCP session interface browserext.Session uses
    def start(self):
        pass

    def handshake(self, timeout=0):
        return {}

    def close(self):
        pass

    def alive(self):
        return True

    def call(self, name, args, timeout=0):
        w = FakeChrome.WORLD
        if self.work:
            w.mcp_work = self.work
        try:
            text = w.tool(name, args or {})
        except RuntimeError as e:                       # the real server reports a failed tool as an error result, not as a crash
            return {"isError": True, "content": [{"type": "text", "text": str(e)}]}
        return {"content": [{"type": "text", "text": text}]}



class World:
    def __init__(self, web: Web, tmp: Path):
        self.web, self.tmp = web, Path(tmp)
        self.tabs: list = []
        self.cur = 0
        self.calls: list = []            # (tool, args-without-text) in order
        self.typed: list = []            # (field name, value): the secrets a test may look for in every other place
        self.events: list = []           # pending "### Events" lines for the next reply
        self.mcp_out = self.tmp / "mcp-out"
        self.mcp_out.mkdir(parents=True, exist_ok=True)
        self.chrome_downloads = self.tmp / "chrome-downloads"
        self.chrome_downloads.mkdir(parents=True, exist_ok=True)
        self.download_mode = "mcp"       # "mcp": the server reports the file in its events; "chrome": Chrome saves it in its downloads folder
        self.refs_n = 0
        self.fail_next: dict = {}        # tool -> error text for the next call (a flaky page)
        self.session_signed_in = set()
        self.clicks_ignored = 0
        self.ignore_clicks_on: dict = {}  # label -> how many clicks are swallowed (a handler that is not ready yet)
        self.after_click: list = []      # fn(world, node) for tests
        self.mcp_work = str(self.tmp / "mcp-work")
        os.makedirs(self.mcp_work, exist_ok=True)
        self.alerts: list = []
        self.click_log: list = []         # every label clicked, across pages
        self.new_tab_blocked = False
        self.judge_runs: list = []        # (url, the editor's text, the verdict) for every Run on a fake judge page (D77)
        self.graded: list = []            # urls where a Submit-like control was activated: must stay EMPTY in every web-judge test

    # ---- loading pages ------------------------------------------------------------------------------------------------
    def open(self, url: str, new_tab: bool = False, push: bool = True):
        u = urllib.parse.urlsplit(url)
        host, path = (u.hostname or "").lower(), (u.path or "/") + (("?" + u.query) if u.query else "")
        page_path = u.path or "/"
        src = self.web.page(host, page_path)
        root = parse_html(src)
        # server-side: a page that needs a session shows the login page without one
        body = next((n for n in root.walk() if n.tag == "body"), root)
        if body.attrs.get("data-needs-session") is not None and host not in self.web.signed_in:
            src = self.web.page(host, "/login")
            root = parse_html(src)
            url, page_path = f"https://{host}/login", "/login"
        title = next((n.text() for n in root.walk() if n.tag == "title"), "")
        tab = Tab(url, root, title)
        if new_tab or not self.tabs:
            if new_tab:
                self.tabs.append(tab)
                self.cur = len(self.tabs) - 1
            else:
                self.tabs = [tab]
                self.cur = 0
        else:
            old = self.tabs[self.cur]
            tab.history = old.history + [old.url] if push else old.history
            self.tabs[self.cur] = tab
        for n in tab.root.walk():
            if n.tag == "iframe" and n.attrs.get("src"):
                src_url = urllib.parse.urljoin(url, n.attrs["src"])
                fu = urllib.parse.urlsplit(src_url)
                try:
                    tab.frames[n.nid] = (src_url, parse_html(self.web.page((fu.hostname or "").lower(), fu.path or "/")))
                except Exception:  # noqa: BLE001
                    pass
        return tab

    @property
    def tab(self) -> Tab:
        return self.tabs[self.cur]

    # ---- visibility and the tree --------------------------------------------------------------------------------------
    def visible(self, tab: Tab, n: Node) -> bool:
        for a in [n] + list(n.ancestors()):
            if a.tag == "root":
                break
            if a.nid in tab.hidden:
                return False
            if "hidden" in a.attrs and a.nid not in tab.shown:
                return False
            if "display:none" in a.attrs.get("style", "").replace(" ", "") and a.nid not in tab.shown:
                return False
            if "data-hidden" in a.attrs and a.nid not in tab.shown:
                return False
            if "data-hidden-until" in a.attrs and tab.tick < int(a.attrs["data-hidden-until"]) and a.nid not in tab.shown:
                return False
            if a.attrs.get("aria-hidden") == "true":
                return False
            if "data-hover-menu" in a.attrs and a.nid not in tab.hovered and a.nid not in tab.shown:
                return False
        return True

    def blocker(self, tab: Tab):
        """A visible overlay with data-blocks: the node that covers the page."""
        for n in tab.root.walk():
            if "data-blocks" in n.attrs and self.visible(tab, n):
                return n
        return None

    def name_of(self, tab: Tab, n: Node) -> str:
        if "data-icon-only" in n.attrs:
            return ""
        if n.attrs.get("aria-label"):
            return n.attrs["aria-label"]
        if n.tag == "input":
            if n.attrs.get("type") in ("submit", "button"):
                return n.attrs.get("value", "")
            lab = next((l for l in tab.root.walk() if l.tag == "label" and l.attrs.get("for") == n.attrs.get("id") and n.attrs.get("id")), None)
            if lab is not None:
                return lab.text()
            return n.attrs.get("placeholder") or n.attrs.get("name", "")
        if n.tag in ("textarea", "select"):
            lab = next((l for l in tab.root.walk() if l.tag == "label" and l.attrs.get("for") == n.attrs.get("id") and n.attrs.get("id")), None)
            return n.attrs.get("aria-label") or (lab.text() if lab is not None else n.attrs.get("name", ""))
        return tab.texts.get(n.nid, n.text())

    def role_of(self, n: Node) -> str:
        if n.attrs.get("role"):
            return n.attrs["role"]
        if n.tag == "input":
            t = n.attrs.get("type", "text")
            return {"checkbox": "checkbox", "radio": "radio", "submit": "button", "button": "button", "file": "button", "hidden": ""}.get(t, "textbox")
        return ROLE_OF_TAG.get(n.tag, "")

    def disabled(self, tab: Tab, n: Node) -> bool:
        if "disabled" in n.attrs:
            return True
        cond = n.attrs.get("data-disabled-until-text")
        if cond:
            sel, _, words = cond.partition("=")
            target = tab.root.find(sel)
            return not (target is not None and words.lower() in str(tab.values.get(target.nid, "")).lower())
        return False

    def render(self, tab: Tab) -> str:
        lines, tab.refs, tab.boxes = [], {}, {}
        counter = [0]

        def ref_for(n: Node, prefix: str = ""):
            counter[0] += 1
            return f"{prefix}e{counter[0]}"

        def emit(n, depth, prefix, frame_no):
            ind = "  " * depth
            if isinstance(n, str):
                lines.append(f"{ind}- text: {n}")
                return
            if not self.visible(tab, n):
                return
            role = self.role_of(n)
            clickable = "data-on-click" in n.attrs or "onclick" in n.attrs
            if n.tag in ("script", "style", "title", "head", "meta", "link"):
                return
            if not role and clickable:
                role = "generic"
            if n.tag == "label" and n.attrs.get("for"):
                return
            if not role:
                for c in n.children:
                    emit(c, depth, prefix, frame_no)
                return
            name = self.name_of(tab, n) if role not in ("paragraph", "generic", "listitem") else ""
            if role in ("heading",):
                name = n.text()
            if role == "dialog":
                name = n.attrs.get("aria-label", "")
            ref = ref_for(n, prefix)
            tab.refs[ref] = n
            attrs = ""
            if role == "heading":
                attrs += f" [level={n.tag[1:] if n.tag[0] == 'h' else 1}]"
            if self.disabled(tab, n):
                attrs += " [disabled]"
            if role in ("checkbox", "radio") and tab.values.get(n.nid, "checked" in n.attrs):
                attrs += " [checked]"
            if n.tag == "button" and n.attrs.get("aria-expanded") == "true":
                attrs += " [expanded]"
            attrs += f" [ref={ref}]"
            if clickable or n.tag in ("a", "button"):
                attrs += " [cursor=pointer]"
            idx = len(tab.boxes)
            nm = name or n.text()
            box = (10, 20 + idx * 34, max(30, 8 * len(nm) + 20), 28)
            tab.boxes[ref] = box
            attrs += f" [box={box[0]},{box[1]},{box[2]},{box[3]}]"
            head = f"{ind}- {role}" + (f' "{name}"' if name else "") + attrs
            if role in ("textbox", "searchbox", "combobox") and n.tag != "select":
                val = tab.values.get(n.nid, n.attrs.get("value", ""))
                lines.append(head + (f": {val}" if val else ""))
                return
            if role == "combobox":
                lines.append(head + ":")
                val = tab.values.get(n.nid, "")
                for c in n.children:
                    if isinstance(c, Node) and c.tag == "option":
                        sel = " [selected]" if (val == c.text() or (not val and "selected" in c.attrs)) else ""
                        lines.append(f"{ind}  - option \"{c.text()}\"{sel}")
                return
            if role == "link":
                lines.append(head + ":")
                lines.append(f"{ind}  - /url: {n.attrs.get('href', '')}")
                return
            if role == "iframe":
                lines.append(head + ":")
                fr = tab.frames.get(n.nid)
                if fr:
                    frame_no[0] += 1
                    fprefix = f"f{frame_no[0]}"
                    for c in fr[1].children:
                        emit(c, depth + 1, fprefix, frame_no)
                return
            kids = [c for c in n.children]
            if role in ("paragraph", "listitem") and kids and all(isinstance(c, str) for c in kids):
                lines.append(head + f": {' '.join(kids)}")
                return
            if role == "generic" and kids and all(isinstance(c, str) for c in kids):
                lines.append(head + ":")
                lines.append(f"{ind}  - text: {' '.join(kids)}")
                return
            if role == "button" and (not kids or all(isinstance(c, str) for c in kids)):
                lines.append(head)
                return
            lines.append(head + ":")
            for c in kids:
                emit(c, depth + 1, prefix, frame_no)

        for c in tab.root.children:
            emit(c, 0, "", [0])
        return "\n".join(lines)

    def find_in(self, tab: Tab, sel: str):
        t = tab.root.find(sel)
        if t is not None:
            return t
        for _url, root in tab.frames.values():
            t = root.find(sel)
            if t is not None:
                return t
        return None

    # ---- executing the behaviour language --------------------------------------------------------------------------------
    def run(self, tab: Tab, stmts: str, node: Node = None) -> None:
        for raw in [s.strip() for s in stmts.split(";") if s.strip()]:
            cmd, _, arg = raw.partition(":")
            if cmd == "hide":
                t = self.find_in(tab, arg)
                if t is not None:
                    tab.hidden.add(t.nid)
                    tab.shown.discard(t.nid)
            elif cmd == "show":
                t = self.find_in(tab, arg)
                if t is not None:
                    tab.shown.add(t.nid)
                    tab.hidden.discard(t.nid)
            elif cmd == "toggle":
                t = self.find_in(tab, arg)
                if t is not None:
                    if self.visible(tab, t):
                        tab.hidden.add(t.nid)
                        tab.shown.discard(t.nid)
                    else:
                        tab.shown.add(t.nid)
                        tab.hidden.discard(t.nid)
            elif cmd == "text":
                sel, _, words = arg.partition("=")
                t = self.find_in(tab, sel)
                if t is not None:
                    tab.texts[t.nid] = words
                    t.children = [words]
            elif cmd == "set":
                sel, _, words = arg.partition("=")
                t = self.find_in(tab, sel)
                if t is not None:
                    tab.values[t.nid] = words
            elif cmd == "goto":
                self.open(urllib.parse.urljoin(tab.url, arg))
                return
            elif cmd == "open":
                self.open(urllib.parse.urljoin(tab.url, arg), new_tab=True)
                return
            elif cmd == "submit":
                form = {}
                scope = next((a for a in ([node] + list(node.ancestors()) if node else []) if a.tag == "form"), tab.root)
                for n in scope.walk():
                    if n.tag in ("input", "textarea", "select") and n.attrs.get("type") != "submit":
                        key = n.attrs.get("name") or n.attrs.get("id") or self.name_of(tab, n)
                        form[key] = tab.values.get(n.nid, n.attrs.get("value", ""))
                host = (urllib.parse.urlsplit(tab.url).hostname or "").lower()
                hook = self.web.hooks.get((host, arg))
                follow = hook(self, form) if hook else ""
                if follow:
                    self.run(self.tab, follow)
                return
            elif cmd == "download":
                host = (urllib.parse.urlsplit(tab.url).hostname or "").lower()
                data = self.web.files.get((host, arg))
                if data is None:
                    f = self.web.root / host / arg.lstrip("/")
                    data = f.read_bytes() if f.is_file() else b""
                name = os.path.basename(arg)
                if self.download_mode == "mcp":
                    dest = self.mcp_out / name
                    dest.write_bytes(data)
                    self.events.append(f'- Downloaded file {name} to "{dest}"')
                else:
                    (self.chrome_downloads / name).write_bytes(data)
            elif cmd == "delay":
                n, _, rest = arg.partition(":")
                tab.scheduled.append((tab.tick + int(n), rest))
            elif cmd == "alert":
                tab.dialog = arg
            elif cmd == "timeline":
                tab.timeline_started = tab.tick
            elif cmd == "judge":             # D77: a fake judge: run the editor's text on the page's tests (data-tests='[[input, expected], ...]') with a real interpreter
                ed_sel, _, res_sel = arg.partition(",")
                ed, res = self.find_in(tab, ed_sel), self.find_in(tab, res_sel)
                main = next((n for n in tab.root.walk() if "data-tests" in n.attrs), None)
                code = str(tab.values.get(ed.nid, "")) if ed is not None else ""
                verdict = self.judge_eval(code, json.loads(main.attrs["data-tests"]) if main is not None else [])
                self.judge_runs.append((tab.url, code, verdict))
                if res is not None:
                    tab.texts[res.nid] = verdict
                    res.children = [verdict]
            elif cmd == "grade":             # D77: Submit / Finish on a fake judge: the grade is final
                self.graded.append(tab.url)
            elif cmd == "signin":
                host = (urllib.parse.urlsplit(tab.url).hostname or "").lower()
                self.web.signed_in.add(arg)

    def judge_eval(self, code: str, tests: list) -> str:
        if not code.strip():
            return "No code to run"
        from jarvis.plugins._shared import code_runner as cr
        runs = cr.LocalRunner().execute("python", code, [t[0] for t in tests], cr.Limits(cpu_s=2, wall_s=4, mem_mb=256))
        passed = 0
        for i, (t, r) in enumerate(zip(tests, runs), 1):
            if r.status != cr.OK:
                return f"Runtime error on sample test {i}"
            if cr.same_output(r.stdout, t[1]):
                passed += 1
            else:
                return f"Wrong answer on sample test {i}: expected {t[1].strip()}, got {r.stdout.strip()[:40]} ({passed}/{len(tests)} sample tests passed)"
        return f"Passed {passed}/{len(tests)} sample tests"

    def advance(self, tab: Tab) -> None:
        tab.tick += 1
        due = [s for s in tab.scheduled if s[0] <= tab.tick]
        tab.scheduled = [s for s in tab.scheduled if s[0] > tab.tick]
        for _, stmts in due:
            self.run(tab, stmts)
        body = next((n for n in tab.root.walk() if "data-timeline" in n.attrs), None)
        if body is not None and tab.timeline_started is not None:
            for item in body.attrs["data-timeline"].split(";"):
                t, _, stmt = item.partition(":")
                if t.strip().isdigit() and item not in tab.timeline_done and tab.tick - tab.timeline_started >= int(t):
                    tab.timeline_done.add(item)
                    self.run(tab, stmt)

    # ---- the MCP tools ------------------------------------------------------------------------------------------------
    def header(self, with_snapshot: bool = True) -> str:
        t = self.tab
        out = []
        if len(self.tabs) > 1:
            out.append("### Open tabs")
            for i, tb in enumerate(self.tabs):
                out.append(f"- {i}:{' (current)' if i == self.cur else ''} [{tb.title}] ({tb.url})")
        out += ["### Page", f"- Page URL: {t.url}", f"- Page Title: {t.title}"]
        if t.dialog:
            out += ["### Modal state", f"- [JavaScript dialog]: {t.dialog}"]
        if self.events:
            out += ["### Events"] + self.events
            self.events = []
        if with_snapshot:
            out += ["### Snapshot", "```yaml", self.render(t), "```"]
        return "\n".join(out)

    def resolve(self, target: str):
        tab = self.tab
        if not tab.refs:
            self.render(tab)
        if target in tab.refs:
            return tab.refs[target]
        if target.startswith(("xpath=", "css=")):
            self.render(tab)
            hint = target.split("=", 1)[1]
            m = re.search(r"@id=['\"]([^'\"]+)['\"]|^#([\w-]+)", hint)
            if m:
                want = m.group(1) or m.group(2)
                for n in tab.root.walk():
                    if n.attrs.get("id") == want and self.visible(tab, n):
                        return n
        raise RuntimeError(f"Error: Ref {target} not found in the current page snapshot")

    def ref_of(self, n: Node):
        for r, x in self.tab.refs.items():
            if x is n:
                return r
        return ""

    def tool(self, name: str, args: dict) -> str:
        shown = {k: ("<text>" if k == "text" else v) for k, v in args.items()}
        self.calls.append((name, shown))
        if name in self.fail_next:
            raise RuntimeError(self.fail_next.pop(name))
        tab = self.tab if self.tabs else None
        if name == "browser_snapshot":
            self.advance(tab)
            if args.get("target"):
                n = self.resolve(args["target"])
                self.render(tab)
                r = self.ref_of(n)
                role = self.role_of(n) or "generic"
                nm = self.name_of(tab, n)
                return self.header(False) + f"\n### Snapshot\n```yaml\n- {role}" + (f' "{nm}"' if nm else "") + f" [ref={r}]\n```"
            return self.header()
        if name == "browser_navigate":
            self.open(args["url"], push=True)
            return self.header()
        if name == "browser_navigate_back":
            if tab.history:
                prev = tab.history[-1]
                self.open(prev, push=False)
                self.tab.history = tab.history[:-1]
            return self.header()
        if name == "browser_tabs":
            a = args.get("action")
            if a == "new":
                self.open(args.get("url") or "about:blank", new_tab=True)
                return self.header()
            if a == "close":
                idx = args.get("index", self.cur)
                if len(self.tabs) > 1:
                    self.tabs.pop(idx)
                    self.cur = min(self.cur, len(self.tabs) - 1)
                return self.header()
            if a == "select":
                self.cur = int(args["index"])
                return self.header()
            return "\n".join(["### Open tabs"] + [f"- {i}:{' (current)' if i == self.cur else ''} [{tb.title}] ({tb.url})" for i, tb in enumerate(self.tabs)])
        if name in ("browser_click", "browser_hover", "browser_type", "browser_select_option", "browser_drag", "browser_file_upload", "browser_mouse_click_xy",
                    "browser_press_key", "browser_wait_for", "browser_handle_dialog", "browser_mouse_wheel", "browser_take_screenshot", "browser_find"):
            return getattr(self, "t_" + name[len("browser_"):])(tab, args)
        raise RuntimeError(f"fake chrome: unknown tool {name}")

    def _guard_click(self, tab: Tab, n: Node):
        blk = self.blocker(tab)
        if blk is not None and n is not blk and blk not in list(n.ancestors()) and n not in list(blk.walk()):
            raise RuntimeError("Error: element intercepts pointer events: a dialog covers the page")
        if self.disabled(tab, n):
            raise RuntimeError("Error: element is not enabled")
        if not self.visible(tab, n):
            raise RuntimeError("Error: element is not visible")

    def t_click(self, tab, args):
        n = self.resolve(args["target"])
        self._guard_click(tab, n)
        return self._do_click(tab, n)

    def _do_click(self, tab, n):
        label = self.name_of(tab, n) or n.text()
        tab.clicked.append(label)
        self.click_log.append(label)
        for k in list(self.ignore_clicks_on):
            if k.lower() == label.lower() and self.ignore_clicks_on[k] > 0:
                self.ignore_clicks_on[k] -= 1
                return self.header()
        for f in self.after_click:
            f(self, n)
        if n.tag == "input" and n.attrs.get("type") in ("checkbox", "radio"):
            tab.values[n.nid] = not tab.values.get(n.nid, "checked" in n.attrs)
        on = n.attrs.get("data-on-click")
        if on is None:
            for a in n.ancestors():
                if "data-on-click" in a.attrs and a.tag == "a":
                    on = a.attrs["data-on-click"]
                    break
        if on is None and n.tag == "a" and n.attrs.get("href"):
            href = urllib.parse.urljoin(tab.url, n.attrs["href"])
            if n.attrs.get("target") == "_blank":
                self.open(href, new_tab=True)
            else:
                self.open(href)
            return self.header()
        if on is None and n.tag == "button" and n.attrs.get("type", "submit") == "submit":
            form = next((a for a in n.ancestors() if a.tag == "form"), None)
            if form is not None and form.attrs.get("data-on-submit"):
                self.run(tab, form.attrs["data-on-submit"], node=n)
        if on:
            self.run(tab, on, node=n)
        return self.header()

    def t_hover(self, tab, args):
        n = self.resolve(args["target"])
        for a in [n] + list(n.ancestors()):
            sel = a.attrs.get("data-hover-shows")
            if sel:
                t = tab.root.find(sel)
                if t is not None:
                    tab.hovered.add(t.nid)
                    tab.shown.add(t.nid)
        return self.header()

    def t_type(self, tab, args):
        n = self.resolve(args["target"])
        self._guard_click(tab, n)
        if n.tag not in ("input", "textarea") and n.attrs.get("role") != "textbox":
            raise RuntimeError("Error: Element is not an <input>, <textarea> or [contenteditable] element")
        tab.values[n.nid] = args["text"]
        self.typed.append((self.name_of(tab, n), args["text"]))
        if args.get("submit"):
            self.run(tab, "", node=n)
        return self.header()

    def t_select_option(self, tab, args):
        n = self.resolve(args["target"])
        self._guard_click(tab, n)
        want = args["values"][0]
        opts = [c.text() for c in n.children if isinstance(c, Node) and c.tag == "option"]
        if want not in opts:
            raise RuntimeError("Error: did not find some options")
        tab.values[n.nid] = want
        if n.attrs.get("data-on-change"):
            self.run(tab, n.attrs["data-on-change"], node=n)
        return self.header()

    def t_drag(self, tab, args):
        a, b = self.resolve(args["startTarget"]), self.resolve(args["endTarget"])
        if "data-on-drop" in b.attrs:
            self.run(tab, b.attrs["data-on-drop"], node=b)
        return self.header()

    def t_file_upload(self, tab, args):
        for n in tab.root.walk():
            if n.tag == "input" and n.attrs.get("type") == "file" and self.visible(tab, n):
                tab.values[n.nid] = os.path.basename(args["paths"][0]) if args.get("paths") else ""
                if n.attrs.get("data-on-change"):
                    self.run(tab, n.attrs["data-on-change"], node=n)
                return self.header()
        raise RuntimeError("Error: no file chooser is open")

    def t_mouse_click_xy(self, tab, args):
        x, y = args["x"], args["y"]
        self.render(tab)
        best = None
        for r, (bx, by, bw, bh) in tab.boxes.items():
            if bx <= x <= bx + bw and by <= y <= by + bh:
                n = tab.refs[r]
                if best is None or bw * bh < best[1]:
                    best = (n, bw * bh)
        if best is None:
            return self.header()
        blk = self.blocker(tab)
        if blk is not None and best[0] is not blk and blk not in list(best[0].ancestors()):
            return self.header()                       # a coordinate click lands on the overlay
        return self._do_click(tab, best[0])

    def t_mouse_wheel(self, tab, args):
        return self._scroll(tab, args.get("deltaY", 0) > 0)

    def _scroll(self, tab, down: bool):
        if down:
            tab.scrolls += 1
            for n in tab.root.walk():
                sel = n.attrs.get("data-load-more")
                if sel and tab.loaded_more < int(n.attrs.get("data-pages", "3")):
                    t = tab.root.find(sel)
                    if t is not None:
                        k = tab.loaded_more
                        for i in range(3):
                            li = Node("li", {}, t)
                            li.children = [f"Item {k * 3 + i + 4}"]
                            t.children.append(li)
                        if k + 1 == int(n.attrs.get("data-pages", "3")) and n.attrs.get("data-final"):
                            fin = Node("button", {"data-on-click": "text:#status=Final item reached"}, t)
                            fin.children = [n.attrs["data-final"]]
                            t.children.append(fin)
                        tab.loaded_more += 1
        return self.header()

    def t_press_key(self, tab, args):
        key = args["key"]
        if key in ("PageDown", "End"):
            return self._scroll(tab, True)
        if key in ("PageUp", "Home"):
            return self.header()
        if key == "Escape":
            for n in tab.root.walk():
                if "data-esc-closes" in n.attrs and self.visible(tab, n):
                    tab.hidden.add(n.nid)
        if key == "Enter":
            for n in tab.root.walk():
                if n.tag == "form" and n.attrs.get("data-enter-submits") and self.visible(tab, n):
                    self.run(tab, n.attrs["data-on-submit"], node=n)
        return self.header()

    def t_wait_for(self, tab, args):
        self.advance(tab)
        return self.header()

    def t_handle_dialog(self, tab, args):
        tab.dialog = None
        return self.header()

    def t_take_screenshot(self, tab, args):
        name = args.get("filename") or "page.png"
        dest = Path(self.mcp_work) / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        import struct
        import zlib
        w, h = 800, 600
        raw = b"".join(b"\x00" + b"\xff" * (w * 3) for _ in range(h))
        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
        dest.write_bytes(png)
        return "ok"

    def t_find(self, tab, args):
        return self.header()
