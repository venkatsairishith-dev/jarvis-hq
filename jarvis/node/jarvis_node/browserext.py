"""The owner's EXISTING Chrome profiles for Neko (2026-10-16). Windows.

Profiles: a registry built from Chrome's profile list (Local State -> profile.info_cache: folder, display name, Google
account name, the account's name before the @). Only aliases and a masked account ("ven***@gmail.com") are kept, in
D:\\jarvis\\N.E.K.O-data\\neko\\browser_profiles.json. No cookie, password, token or key is ever read: the encrypted key
in the same file is not touched, and no profile folder file is opened. "default" is the owner's default account.
An alias that fits more than one profile is refused with the candidates, so Neko asks the owner once.

Two ways to act, both on the owner's real, logged-in browser:
 1. Without the extension: "open" starts Chrome with --profile-directory, which opens a new tab in that profile's window.
 2. With Microsoft's Playwright MCP extension installed in that profile (Chrome Web Store id mmlmfjhmonkocbjadbfplnigmagldckm):
    @playwright/mcp --extension --profile-dir-name "<folder>" connects to the running Chrome. Only these tools are ever
    called: browser_tabs, browser_navigate, browser_snapshot, browser_find, browser_click, browser_type, browser_press_key,
    browser_wait_for, and browser_evaluate with ONE fixed script from this file (the video element's state). The storage,
    cookie, network, devtools and code-running tools are not enabled (no --caps) and never called. Images are not returned.
    The connection token (shown in the extension) is read from D:\\jarvis-secrets\\playwright\\extension-token.txt into the
    server's environment only, never logged or returned. Without it the extension asks the owner to allow each connection.
Page text stays on this laptop: it is written to outputs\\neko-local and only a summary (title, address, counts) goes back.
"""
import json
import os
import re
import secrets
import time
import urllib.parse

from . import runtime
from .paths import local_result_dir, sweep_local

CHROME = next((p for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                           os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
                           r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe") if os.path.isfile(p)), None)
USER_DATA = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
REGISTRY = os.environ.get("NEKO_BROWSER_PROFILES", r"D:\jarvis\N.E.K.O-data\neko\browser_profiles.json")
DEFAULT_ACCOUNT = os.environ.get("NEKO_DEFAULT_ACCOUNT_PREFIX", "venkatgaminggem17")   # the owner's default (2026-10-16)
NODE = next((p for p in (r"C:\Program Files\nodejs\node.exe",) if os.path.isfile(p)), "node")
MCP_CLI = os.environ.get("NEKO_PLAYWRIGHT_MCP", r"D:\jarvis\tools\playwright-mcp\node_modules\@playwright\mcp\cli.js")
TOKEN_FILE = r"D:\jarvis-secrets\playwright\extension-token.txt"
EXTENSION_ID = "mmlmfjhmonkocbjadbfplnigmagldckm"
ALLOWED_TOOLS = {"browser_tabs", "browser_navigate", "browser_snapshot", "browser_find", "browser_click", "browser_type",
                 "browser_press_key", "browser_wait_for", "browser_evaluate"}
VIDEO_STATE = ("() => { const v = document.querySelector('video'); return v ? {paused: v.paused, ended: v.ended, "
               "position_s: Math.round(v.currentTime * 10) / 10, muted: v.muted, title: document.title, url: location.href} "
               ": {none: true, title: document.title, url: location.href}; }")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class BrowserError(Exception):
    pass


# ---------------------------------------------------------------- profiles
def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def mask(email: str) -> str:
    return re.sub(r"^(.{3}).*(@.*)$", r"\1***\2", email) if "@" in (email or "") else ""


def account_id(email: str) -> str:
    """A one-way fingerprint of an account address: tells apart accounts whose masks look the same (ven***@gmail.com)
    without keeping the address."""
    import hashlib
    return hashlib.sha256(("neko-account:" + (email or "").strip().lower()).encode()).hexdigest()[:16] if email else ""


def build_registry(local_state_path=None, path=REGISTRY) -> dict:
    """Read only profile.info_cache names from Chrome's Local State and write the alias registry."""
    with open(local_state_path or os.path.join(USER_DATA, "Local State"), encoding="utf-8") as f:
        info = (json.load(f).get("profile") or {}).get("info_cache") or {}
    profiles = []
    for folder, i in info.items():
        account = str(i.get("user_name") or "")
        aliases = {_norm(i.get("name")), _norm(i.get("gaia_name")), _norm(i.get("gaia_given_name")), _norm(account.split("@")[0]),
                   _norm(folder)}
        default = bool(account) and account.lower().startswith(DEFAULT_ACCOUNT.lower() + "@")
        if default:
            aliases.add("default")
        profiles.append({"dir": folder, "name": str(i.get("name") or "")[:40], "account": mask(account), "account_id": account_id(account),
                         "default": default, "aliases": sorted(a for a in aliases if a)})
    reg = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "profiles": profiles}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=1)
    return reg


def load_registry(path=REGISTRY) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            reg = json.load(f)
        if all("account_id" in p for p in reg.get("profiles", [])):
            return reg
    except (OSError, ValueError):
        pass
    return build_registry(path=path)                     # missing, unreadable or from before account fingerprints


def resolve(alias: str, reg=None) -> dict:
    """One profile for an alias, or BrowserError naming the candidates (so the owner is asked once)."""
    reg = reg or load_registry()
    q = _norm(alias) or "default"
    exact = [p for p in reg["profiles"] if q in p["aliases"]]
    if len(exact) == 1:
        return exact[0]
    near = exact or [p for p in reg["profiles"] if any(len(a) >= 4 and (a in q or q in a) for a in p["aliases"])]
    if len(near) == 1:
        return near[0]
    def label(p): return f"{p['name']} ({p['account'] or 'no account'}, {p['dir']})"
    if near:
        raise BrowserError("ambiguous profile '" + alias + "': " + "; ".join(label(p) for p in near) + " — which one?")
    raise BrowserError("no Chrome profile matches '" + alias + "'; known: " + "; ".join(label(p) for p in reg["profiles"]))


# ---------------------------------------------------------------- without the extension
# The profile name on Chrome's own toolbar button in the front Chrome window (a display name, never an address).
AVATAR = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type @'
using System; using System.Runtime.InteropServices;
public class FG { [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); }
'@
$A = [System.Windows.Automation.AutomationElement]
$w = $A::FromHandle([FG]::GetForegroundWindow())
$b = $w.FindFirst([System.Windows.Automation.TreeScope]::Descendants, (New-Object System.Windows.Automation.PropertyCondition($A::ClassNameProperty, 'AvatarToolbarButton')))
if ($b) { $b.Current.Name }
"""


def open_plain(profile: dict, url: str) -> dict:
    if not CHROME:
        raise BrowserError("Chrome is not installed in the usual place")
    runtime.popen([CHROME, f"--profile-directory={profile['dir']}", url], close_fds=True, creationflags=0)
    from . import windows
    want = urllib.parse.urlsplit(url).hostname or ""
    w = windows.wait_for(lambda x: x.get("process", "").lower() == "chrome" and x.get("foreground"), timeout=8) if windows.ON_WINDOWS else None
    shown = ""
    if w:
        try:
            shown = EMAIL.sub("***", (runtime.powershell(AVATAR, {}, 20).stdout or "").strip())[:60]
            shown = re.sub(r"^(hi|hello),\s*", "", shown, flags=re.I)     # newer Chrome greets: "Hi, Venkat"
        except Exception:  # noqa: BLE001 - only a second witness
            shown = ""
    return {"ok": True, "opened": url, "profile": profile["dir"], "account": profile["account"], "via": "chrome --profile-directory",
            "window": (w or {}).get("title", "")[:120], "verified": "window" if w else "unconfirmed",
            "profile_name_shown": shown, "profile_name_matches": bool(shown) and shown.lower() == profile["name"].lower(),
            "note": f"opened in the {profile['name']} profile" + ("" if extension_ready(profile) else
                    "; page reading and playback checks need the Playwright extension"), "host": want}


def token_file(profile_dir: str, folder: str = os.path.dirname(TOKEN_FILE)) -> str | None:
    """The extension's connection token for this profile: each profile's extension has its own, saved by the owner's
    helper as extension-token-<profile folder>.txt (e.g. extension-token-Profile_1.txt); the single shared file is the
    fallback. Only the path is returned here; the value goes into the server's environment and nowhere else."""
    own = os.path.join(folder, "extension-token-" + re.sub(r"[^A-Za-z0-9]+", "_", profile_dir) + ".txt")
    for p in (own, os.path.join(folder, "extension-token.txt")):      # (a plain name: basename of a Windows path fails on Linux)
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            return p
    return None


def extension_ready(profile: dict) -> bool:
    """Is the Playwright extension installed in this profile? (its folder under the profile's Extensions folder)"""
    return os.path.isdir(os.path.join(USER_DATA, profile["dir"], "Extensions", EXTENSION_ID))


# ---------------------------------------------------------------- with the extension
class Session:
    """One Playwright MCP connection to the running Chrome profile, for the length of one job."""

    def __init__(self, profile: dict, session_factory=None, isolated: bool = False):
        self.profile = profile
        env = dict(os.environ)
        tok = token_file(profile["dir"])
        if tok:
            with open(tok, encoding="utf-8") as f:
                env["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = f.read().strip()
        # Playwright MCP writes the pages' console logs into its working folder: give it a private one, deleted on exit
        self.work = os.path.join(local_result_dir(), f"playwright-{os.getpid()}-{secrets.token_hex(3)}")
        argv = [NODE, MCP_CLI, "--extension", "--profile-dir-name", profile["dir"], "--image-responses", "omit",
                "--output-dir", self.work]
        if isolated:                    # tests and risky scenarios: a throwaway headless Chrome, no owner profile, no extension
            argv = [NODE, MCP_CLI, "--headless", "--isolated", "--browser", "chrome", "--image-responses", "omit",
                    "--output-dir", self.work]
        if session_factory is None:                     # the real server, started through runtime so a cancel kills it
            from .mcpclient import StdioSession
            os.makedirs(self.work, exist_ok=True)
            self.s = StdioSession(argv, env=env, cwd=self.work, popen=runtime.popen)
        else:
            self.s = session_factory(argv, env=env)

    def __enter__(self):
        self.s.start()
        self.s.handshake(timeout=60)                    # the owner may have to allow the connection in the extension
        return self

    def __exit__(self, *a):
        self.s.close()
        import shutil
        for _ in range(5):                              # page console logs are page content: not kept (retry: Windows file locks)
            shutil.rmtree(self.work, ignore_errors=True)
            if not os.path.exists(self.work):
                break
            time.sleep(0.5)
        sweep_local()

    def call(self, tool: str, args: dict, timeout=45.0) -> str:
        if tool not in ALLOWED_TOOLS:
            raise BrowserError("tool not allowed")
        if tool == "browser_evaluate" and args.get("function") != VIDEO_STATE:
            raise BrowserError("only the fixed video check may run in a page")
        runtime.check()
        res = self.s.call(tool, args, timeout=timeout)
        text = "\n".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
        if res.get("isError"):
            raise BrowserError(f"{tool} failed: " + _first_line(text))
        return text


def _first_line(t: str) -> str:
    return (t or "").strip().splitlines()[0][:160] if (t or "").strip() else "no detail"


def page_meta(text: str) -> dict:
    """Title and address from a Playwright response's '- Page URL:' / '- Page Title:' lines."""
    url = re.search(r"Page URL:\s*(\S+)", text or "")
    title = re.search(r"Page Title:\s*(.+)", text or "")
    return {"url": url.group(1)[:300] if url else "", "title": title.group(1).strip()[:200] if title else ""}


def watch_links(snapshot: str) -> list:
    """Video results in a YouTube results snapshot: [(name, ref, /watch url)], ads and Shorts skipped."""
    out, last, prev = [], None, ""
    for line in (snapshot or "").splitlines():
        m = re.search(r"""- '?link "(.+?)" \[ref=([^\]]+)\]""", line)   # titles with a colon come quoted: - 'link "A: B" [ref=..]'
        if m:
            # a search result's title is the link right under its heading; menu and guide links are not results
            last = (m.group(1), m.group(2)) if "- heading" in prev else None
            prev = line
            continue
        prev = line
        u = re.search(r"/url:\s*\"?(/watch\?v=[A-Za-z0-9_-]{6,})", line)
        if u and last and "list=" not in line:          # a playlist starts with some other video: one chosen video only
            name = last[0]
            if not re.search(r"(?i)\b(sponsored|ad\b)", name):
                out.append((name, last[1], u.group(1)))
        last = None                                     # a link's address is its very next line, or it has none of its own
    seen, uniq = set(), []
    for n, r, u in out:
        if u not in seen:
            seen.add(u)
            uniq.append((n, r, u))
    return uniq


REF = re.compile(r"^(?:f\d+)?e\d+$")
CLICKABLE = ("button", "link", "checkbox", "radio", "menuitem", "tab", "option", "switch", "combobox")
TYPABLE = ("textbox", "searchbox", "combobox", "spinbutton")


def elements(snapshot: str) -> list[tuple[str, str, str]]:
    """(role, name, ref) for every named element in a Playwright accessibility snapshot."""
    out = []
    for line in (snapshot or "").splitlines():
        m = re.search(r"""-\s+'?([a-z]+)\s+"(.+?)"(?:\s+\[[^\]]*\])*?\s*\[ref=([^\]]+)\]""", line)
        if m:
            out.append((m.group(1), m.group(2), m.group(3)))
    return out


def find_ref(snapshot: str, name: str, roles=CLICKABLE) -> str:
    """The ref of the element the owner named, by accessible role and name: an exact name first, then one that starts
    with it, then one that contains it. Two equally good matches -> an error that names them (never a guess)."""
    want = " ".join((name or "").lower().split())
    if not want:
        raise BrowserError("no element named")
    cands = [(r, n, ref) for r, n, ref in elements(snapshot) if r in roles]
    for test in (lambda n: n == want, lambda n: n.startswith(want), lambda n: want in n):
        hit = [(r, n, ref) for r, n, ref in cands if test(" ".join(n.lower().split()))]
        if len(hit) == 1:
            return hit[0][2]
        if len(hit) > 1:
            raise BrowserError("more than one element matches '" + name + "': " + "; ".join(f"{r} '{n[:40]}'" for r, n, _ in hit[:4]))
    raise BrowserError(f"no {'/'.join(roles[:3])} named '{name}' on this page")


AUTH_HOSTS = ("accounts.google.com", "login.microsoftonline.com", "login.live.com", "github.com/login")


def auth_required(snapshot: str, url: str = "") -> bool:
    """Does this page ask the owner to sign in? (a password box, or a sign-in page) Neko never fills in passwords."""
    if any(h in (url or "") for h in AUTH_HOSTS):
        return True
    els = elements(snapshot)
    has_password = any(r == "textbox" and re.search(r"(?i)password|passcode", n) for r, n, _ in els)
    sign_in = any(r in ("heading", "button") and re.search(r"(?i)^(sign|log) ?in\b", n) for r, n, _ in els)
    return has_password or (sign_in and any(r == "textbox" for r, _, _ in els))


def save_local(kind: str, text: str) -> str:
    d = local_result_dir()
    os.makedirs(d, exist_ok=True)
    sweep_local()
    path = os.path.join(d, f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _video(sess) -> dict:
    t = sess.call("browser_evaluate", {"function": VIDEO_STATE})
    t = t.split("### Result", 1)[-1].split("\n###", 1)[0]      # only the result block (the code echo has braces too)
    m = re.search(r"\{.*\}", t, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}


def navigate(sess, url: str) -> str:
    """Go to an address. After a failed load (an unreachable site), the next navigation can report an error although the
    page then loads (seen live 2026-10-16): try once more, and judge by where the page actually is."""
    try:
        return sess.call("browser_navigate", {"url": url})
    except BrowserError:
        time.sleep(0.5)
        return sess.call("browser_navigate", {"url": url})


def play(sess, query: str) -> dict:
    """Search YouTube, open the first real video, and check that it is playing (position moving). If the page will not
    start it, say so: the owner presses play (site rules: no forced playback tricks)."""
    navigate(sess, "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query))
    vids = []
    for _ in range(4):                                 # results render after the page "loads": wait until videos are listed
        sess.call("browser_wait_for", {"time": 2})
        vids = watch_links(sess.call("browser_snapshot", {}))
        if vids:
            break
    if not vids:
        return {"ok": False, "error": "no video results found on the YouTube results page", "query": query}
    name, _ref, href = vids[0]
    navigate(sess, "https://www.youtube.com" + href)
    sess.call("browser_wait_for", {"time": 4})
    v1 = _video(sess)
    if v1.get("paused"):
        sess.call("browser_press_key", {"key": "k"})            # YouTube's own play/pause key, as the owner would press it
        sess.call("browser_wait_for", {"time": 3})
        v1 = _video(sess)
    sess.call("browser_wait_for", {"time": 2})
    v2 = _video(sess)
    moving = (not v2.get("paused") and not v2.get("none") and float(v2.get("position_s") or 0) > float(v1.get("position_s") or 0))
    want_id = re.search(r"v=([A-Za-z0-9_-]{6,})", href).group(1)
    got = re.search(r"v=([A-Za-z0-9_-]{6,})", v2.get("url") or "")
    same = bool(got) and got.group(1) == want_id                  # the video playing is the one chosen, not another tab's
    ok = moving and same
    return {"ok": ok, "chose": name[:160], "url": v2.get("url") or "", "title": v2.get("title") or "", "playing": moving,
            "same_video": same, "position_s": v2.get("position_s"), "muted": v2.get("muted"),
            "verified_by": "the page's video element: same video id as the chosen result, position moving",
            "error": None if ok else ("a different video is in this tab than the one chosen" if moving and not same else
                                      "the video opened but did not start playing: press play (the site or browser blocked autoplay)")}


def whoami(sess, profile: dict) -> dict:
    """The account the visible browser is signed in to, from the Google Account page itself (masked)."""
    # in a tab of its own, closed afterwards: checking the account must not navigate away from the page being worked on
    sess.call("browser_tabs", {"action": "new", "url": "https://myaccount.google.com/"})
    try:
        sess.call("browser_wait_for", {"time": 2})
        snap = sess.call("browser_snapshot", {})
    finally:
        try:
            sess.call("browser_tabs", {"action": "close"})
        except BrowserError:
            pass
    found = EMAIL.findall(snap)
    emails = list(dict.fromkeys(e.lower() for e in found))            # page order: the signed-in account comes first
    shown = [mask(e) for e in emails][:3]
    expected, want = profile["account"], profile.get("account_id") or ""
    ids = [account_id(e) for e in emails]
    return {"ok": bool(emails), "profile": profile["dir"], "expected": expected, "seen": shown,
            "matches": bool(want) and want in ids, "first_is_expected": bool(ids) and ids[0] == want,
            "verified_by": "myaccount.google.com in the visible browser (compared by account fingerprint, not by the mask)",
            "error": None if emails else "no account shown (signed out, or the page did not load)"}


def act(sess, op: str, url: str, target: str, text: str, prof: dict) -> dict | None:
    """One browser action in an open session (the owner's profile, or an isolated test browser). Elements are found by
    accessible role and name in the page snapshot; a click or typing goes to that exact element's ref."""
    if op == "open":
        t = sess.call("browser_tabs", {"action": "new", "url": url})
        return {"ok": True, "opened": url, "profile": prof.get("dir"), "account": prof.get("account"), **page_meta(t)}
    if op in ("read", "find"):
        t = sess.call("browser_snapshot", {}) if op == "read" else sess.call("browser_find", {"text": target})
        meta = page_meta(t)
        return {"ok": True, **meta, "chars": len(t), "auth_required": auth_required(t, meta.get("url", "")),
                "local_result": save_local("page", t), "note": "the page text stayed on this laptop (local_result)"}
    if op == "tabs":
        t = sess.call("browser_tabs", {"action": "list"})
        return {"ok": True, "tabs": len(re.findall(r"^\s*-\s*\d+:", t, re.M)), "local_result": save_local("tabs", t)}
    if op in ("click", "type"):
        ref = target
        if not REF.match(target or ""):
            snap = sess.call("browser_snapshot", {})
            if op == "type" and auth_required(snap, page_meta(snap).get("url", "")) and re.search(r"(?i)password", target):
                raise BrowserError("Neko does not type into sign-in or password fields: please sign in yourself")
            ref = find_ref(snap, target, CLICKABLE if op == "click" else TYPABLE)
        if op == "click":
            t = sess.call("browser_click", {"target": ref, "element": target or ref})
            return {"ok": True, "clicked": (target or ref)[:80], "ref": ref, **page_meta(t)}
        t = sess.call("browser_type", {"target": ref, "element": target or ref, "text": text})
        return {"ok": True, "typed_chars": len(text), "ref": ref, **page_meta(t)}
    if op == "press":
        t = sess.call("browser_press_key", {"key": text or "Enter"})
        return {"ok": True, "pressed": text or "Enter", **page_meta(t)}
    if op == "play":
        return {**play(sess, target), "profile": prof.get("dir")}
    if op == "media":
        return {"ok": True, **_video(sess)}
    if op == "whoami":
        return whoami(sess, prof)
    return None


def run(p: dict, session_factory=None) -> dict:
    op, url, target, text = str(p.get("op", "")), str(p.get("url", "")), str(p.get("target", "")), str(p.get("text", ""))
    try:
        prof = resolve(str(p.get("profile", "default")))
    except BrowserError as e:
        return {"ok": False, "error": str(e), "needs": "clarification"}
    ready = extension_ready(prof) or session_factory is not None
    if op == "open" and session_factory is None:
        # opening needs no extension: Chrome's own --profile-directory puts it in that profile's window, and the page
        # then exists for the extension's connect prompt (which lists the profile's tabs)
        return open_plain(prof, url)
    if not ready:
        return {"ok": False, "error": f"the Playwright extension is not installed in the {prof['name']} profile ({prof['dir']}); "
                                      f"install it from the Chrome Web Store (id {EXTENSION_ID}) to let Neko read pages, click and play",
                "profile": prof["dir"], "needs": "extension"}
    try:
        with Session(prof, session_factory) as sess:
            out = act(sess, op, url, target, text, prof)
            if out is not None:
                return out
    except runtime.Cancelled:
        raise
    except BrowserError as e:
        return {"ok": False, "error": str(e)[:240]}
    except Exception as e:  # noqa: BLE001 - the kind only: the text could hold page content
        kind = getattr(e, "kind", "")
        hint = " (is the Playwright extension allowed to connect? its connect page opens in Chrome)" if kind in ("handshake", "timeout") else ""
        return {"ok": False, "error": f"browser connection failed: {type(e).__name__} {kind}".strip() + hint}
    return {"ok": False, "error": "unknown browser action"}
