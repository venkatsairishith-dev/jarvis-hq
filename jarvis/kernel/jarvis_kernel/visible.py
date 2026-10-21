"""Making invisible text VISIBLE on approval cards (re-review M1, 2026-10-19).

An approval card is what the owner reads before he types his PIN. Text can say one thing to a reader and another to the machine:
tag characters (U+E0000..E007F) spell a hidden sentence; a right-to-left override reorders what is shown; a zero-width space
splits a word or a host name so a filter does not see it; a soft hyphen, a variation selector or a combining grapheme joiner
adds nothing visible; a Cyrillic letter looks like a Latin one. The server (not each screen) now builds the card from a
VISIBLE FORM of every string: each invisible, format or control character is written out as <U+200B>, counted per field and per
card, and the card is flagged. The approval's digest still binds the original characters (it is computed from the action, never
from the card), so what is approved is exactly what was asked for; only what is SHOWN changes.

Three outcomes (`status`): "clean" (nothing invisible), "notice" or "warning" (the owner is told; he may approve), and
"blocked" (the card cannot be approved: Kernel.approve and the owner route refuse with 409, like a cut-off card).
 - blocked: any character that is never benign (tag, bidi, zero-width space, word joiner, BOM, soft hyphen, control, private use,
   unassigned, filler, ...) in any field; any invisible character at all in a strict field (a target, a URL, a path, a name);
   a ZWJ/ZWNJ/variation selector/odd space that is not in a legitimate place; more than BENIGN_MAX benign ones in one card.
 - benign only in a free-text field (a prompt, instructions, typed text, an email body, a patch): ZWNJ/ZWJ between Indic letters
   (Hindi, Telugu, Tamil ...), ZWNJ between Persian/Arabic letters, ZWJ inside an emoji sequence, variation selector 16 after an
   emoji or a keycap base, ideographic variation selectors after a CJK character, and non-ASCII spaces (no-break space ...).
   A character inside a token that looks like a URL, host name, path or e-mail address is never benign.
 - warning: a host name that mixes scripts in one label, or that is made of look-alike letters for an ASCII name (chаse.com with a
   Cyrillic a). The owner approves it only with the extra acknowledgement the console asks for.
"""
import re
import unicodedata

BENIGN_MAX = 100                                       # more benign invisible characters than this on one card: blocked
_FREE_KEYS = {"prompt", "instructions", "criteria", "text", "root_cause", "patch", "content", "body", "message", "note", "subject",
              "description", "query", "goal", "request", "plan", "summary"}
_TOKEN = re.compile(r"[^\s\"'<>()\[\]{},;|]+")
_HOSTISH = re.compile(r"://|[\w-]\.[\w-]|[/\\@]")

_ALWAYS = {0x00AD, 0x034F, 0x061C, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180E, 0x180F, 0x200B, 0x200E, 0x200F,
           0x2028, 0x2029, 0x2060, 0x2800, 0x3164, 0xFEFF, 0xFFA0}
_ALWAYS |= set(range(0x202A, 0x202F)) | set(range(0x2061, 0x2065)) | set(range(0x2066, 0x206A)) | set(range(0x206A, 0x2070))
_ALWAYS |= set(range(0xFFF9, 0xFFFC)) | set(range(0xE0000, 0xE0080)) | set(range(0xE0100, 0xE01F0))
_BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A)) | {0x200E, 0x200F, 0x061C}
_SPACES = {0x00A0, 0x1680, 0x202F, 0x205F, 0x3000} | set(range(0x2000, 0x200B))


def is_invisible(ch: str) -> bool:
    o = ord(ch)
    if ch in "\n\t":
        return False
    if o in _ALWAYS or o in _SPACES or 0xFE00 <= o <= 0xFE0F or o in (0x200C, 0x200D):
        return True
    return unicodedata.category(ch) in ("Cf", "Cc", "Co", "Cs", "Cn", "Zl", "Zp")


def _indic(ch: str) -> bool:
    return 0x0900 <= ord(ch) <= 0x0DFF


def _arabic(ch: str) -> bool:
    o = ord(ch)
    return 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F or 0xFB50 <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFF


def _emojiish(ch: str) -> bool:
    return ord(ch) >= 0x1F000 or unicodedata.category(ch) == "So"


def _cjk(ch: str) -> bool:
    o = ord(ch)
    return 0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0x20000 <= o <= 0x2FFFF


def _benign(s: str, i: int) -> bool:
    """May the invisible character at s[i] stand in ordinary free text? (Only when nothing in `_ALWAYS` applies.)"""
    ch, o = s[i], ord(s[i])
    start, end = i, i
    while start > 0 and not s[start - 1].isspace():
        start -= 1
    while end + 1 < len(s) and not s[end + 1].isspace():
        end += 1
    if _HOSTISH.search("".join(c for c in s[start:end + 1] if not is_invisible(c))):
        return False                                    # an invisible character inside a URL, host, path or address is never benign
    if o in _SPACES:
        return True
    j = i - 1
    while j >= 0 and 0xFE00 <= ord(s[j]) <= 0xFE0F:     # look back over variation selectors for the real neighbour
        j -= 1
    k = i + 1
    while k < len(s) and 0xFE00 <= ord(s[k]) <= 0xFE0F:
        k += 1
    prev = s[j] if j >= 0 else ""
    nxt = s[k] if k < len(s) else ""
    if o == 0x200D:                                     # zero width joiner
        return bool(prev and nxt and ((_indic(prev) and _indic(nxt)) or (_emojiish(prev) and _emojiish(nxt))))
    if o == 0x200C:                                     # zero width non-joiner
        return bool(prev and nxt and ((_indic(prev) and _indic(nxt)) or (_arabic(prev) and _arabic(nxt))))
    if o in (0xFE0F, 0xFE0E):                           # text/emoji presentation
        return bool(prev) and (_emojiish(prev) or prev in "#*0123456789©®‼⁉™ℹ" or 0x2190 <= ord(prev) <= 0x2BFF)
    if 0xFE00 <= o <= 0xFE0D:                           # ideographic variation selectors
        return bool(prev) and _cjk(prev)
    return False


def _marker(ch: str) -> str:
    return f"<U+{ord(ch):04X}>"


def _script(ch: str) -> str:
    try:
        return unicodedata.name(ch).split()[0]
    except ValueError:
        return "UNKNOWN"


def host_flags(text: str) -> list:
    """Host names inside `text` that mix scripts in one label or are made of look-alike letters for an ASCII name."""
    from .policy import _BARE_URL, _SCHEME, _host_of, _skeleton, _split_authority, fold_text
    out = []
    for tok in _TOKEN.findall(text):
        raw = tok.replace("\\", "/")
        parsed = _split_authority(raw)
        if parsed is not None:
            host = _host_of(parsed[0])[0]
        else:
            m = _BARE_URL.match(raw)
            if not m:
                continue
            host = m.group(1)
        host = "".join(c for c in host if not is_invisible(c)).strip("[]")
        if host.isascii():
            continue
        for label in host.split("."):
            letters = [c for c in label if c.isalpha()]
            scripts = {_script(c) for c in letters}
            if len(scripts) > 1:
                out.append(f"host name mixes scripts in one part ({', '.join(sorted(scripts))}): {_visible_plain(host)}")
                break
        else:
            skel = _skeleton(fold_text(host))
            if skel != fold_text(host) and skel.isascii():
                out.append(f"host name uses look-alike letters for the ASCII name {skel[:60]}: {_visible_plain(host)}")
    return out


def _visible_plain(s: str) -> str:
    return "".join(_marker(c) if is_invisible(c) else c for c in s)[:80]


class Report:
    """Collects, over one card, what was invisible where."""
    def __init__(self):
        self.fields, self.hostile, self.benign, self.flags, self.error = {}, 0, 0, [], False

    def text(self, s: str, path: str, strict: bool = False) -> str:
        """The visible form of `s` (what a card shows); records the counts under `path`."""
        try:
            s = s.replace("\r\n", "\n")
            if s.isascii() and all(not is_invisible(c) for c in s):
                return s
            out, hostile, benign, tags = [], 0, 0, []
            for i, ch in enumerate(s):
                if not is_invisible(ch):
                    out.append(ch)
                    continue
                if not strict and ord(ch) not in _ALWAYS and _benign(s, i):
                    benign += 1
                else:
                    hostile += 1
                    if 0xE0020 <= ord(ch) < 0xE007F:
                        tags.append(chr(ord(ch) - 0xE0000))
                out.append(_marker(ch))
            if hostile or benign:
                f = self.fields.setdefault(path, {"invisible_chars": 0, "has_invisible": True, "hostile": 0})
                f["invisible_chars"] += hostile + benign
                f["hostile"] += hostile
                self.hostile += hostile
                self.benign += benign
            if tags:
                self.flags.append(f"{path}: tag characters spell hidden text: {''.join(tags)[:120]!r}")
            if any(ord(c) in _BIDI for c in s):
                self.flags.append(f"{path}: contains right-to-left or direction-control characters (the text may be reordered)")
            return "".join(out)
        except Exception:                                   # the visible form could not be produced: the card cannot be approved
            self.error = True
            return "<unreadable text>"

    def hosts(self, s: str, path: str):
        try:
            self.flags += [f"{path}: {m}" for m in host_flags(s)]
        except Exception:
            self.error = True

    @property
    def warned(self) -> bool:
        return any("host name" in f for f in self.flags)

    def status(self) -> str:
        if self.error or self.hostile or self.benign > BENIGN_MAX:
            return "blocked"
        if self.warned:
            return "warning"
        return "notice" if self.benign else "clean"

    def result(self) -> dict:
        """{} when nothing was found, else the report stored on the card."""
        if not (self.fields or self.flags or self.error):
            return {}
        return {"status": self.status(), "invisible_chars": self.hostile + self.benign, "hostile": self.hostile, "benign": self.benign,
                "has_invisible": bool(self.hostile + self.benign), "fields": self.fields, "flags": self.flags[:10],
                "note": "invisible and control characters are shown as <U+XXXX>; the approval binds the original characters"}


def strict_path(path: str) -> bool:
    """Fields where no invisible character is ever acceptable: targets, URLs, paths, names, files, ids."""
    parts = [p for p in path.split(".") if not p.isdigit()]
    if not parts:
        return True
    if parts[0] == "card" or (len(parts) > 1 and parts[0] == "params" and parts[1] == "args"):
        return parts[-1] in {"url", "path", "file", "files", "uri", "host", "to", "recipient", "email", "name", "target"}
    return parts[-1] not in _FREE_KEYS


def neutralize(s: str) -> str:
    """Text for a plain-text channel (Telegram, a terminal): hostile invisible characters (tag, bidi, zero-width, control
    including the ESC of ANSI sequences) are written out as <U+XXXX>; benign joiners in Hindi/Telugu/emoji text stay as they are.
    ANSI escape sequences lose their ESC and so cannot recolour, move or clear anything."""
    try:
        out = []
        for i, ch in enumerate(s):
            if is_invisible(ch) and (ord(ch) in _ALWAYS or not _benign(s, i)):
                out.append(_marker(ch))
            else:
                out.append(ch)
        return "".join(out)
    except Exception:
        return "<unreadable text>"
