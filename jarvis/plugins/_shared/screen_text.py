"""Screen- and disk-derived text: the ONE place that decides whether it may leave this PC for a model (audit 2026-10-18, Low:
"OCR / file-name egress").

What counts as screen/disk-derived: OCR text of a window, the text of a page read from the owner's browser, the text of a
window read through accessibility, and the NAMES of files, folders and libraries found in any of those or on disk.

The rule (fail closed everywhere, never a fallback to the raw text):
  1. JARVIS_SCREEN_TEXT_TO_MODEL must be on. Off (the default) means no screen text goes to any model, local or not.
  2. A secret pattern pass (keys, tokens, card numbers, ...) always runs first; text with a private key is withheld whole.
  3. The text may go raw ONLY to a VERIFIED LOCAL model endpoint: the same check that guards the owner's memory
     (main_agent.memory_context.memory_allowed: the loopback llama-swap, never the privacy gateway).
  4. Otherwise it goes out ONLY after the privacy gateway's /redact has hidden private details AND names/places, and the
     gateway's own answer confirmed that names redaction and the PII model were active for this call (the same contract as
     research._gateway_state/_redact). A gateway that is down, degraded, old or unreadable means: withheld.
  5. Anything else, and any error at all, means: withheld, replaced by a short neutral note that holds no part of the text.

Where the consumer of a result cannot be verified (the the face chat model reads plugin results, any plugin can call any
other), the SOURCE applies rule 4 before the text leaves the plugin; a sink that can verify its endpoint (main_agent's agent
slot) applies rules 3 and 4 again. File and directory names are reduced to a class ("a .xlsx file") wherever they are not
free text.

Standard library only; the gateway transport is urllib so a test can put a real HTTP server in its place.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from jarvis.plugins._shared import loopback_http
from jarvis.plugins._shared.untrusted import as_text
from dataclasses import dataclass
from typing import Any

FLAG = "JARVIS_SCREEN_TEXT_TO_MODEL"
WITHHELD = "screen text withheld: no verified private model"
OFF_NOTE = "the text stayed on this PC (set JARVIS_SCREEN_TEXT_TO_MODEL=1 to let the model read it)"
MAX_TEXT = 6000
GATEWAY_TIMEOUT_S = 30.0
SCREEN_KEY = "screen_derived"         # a plugin result that carries screen-derived text says so with this key (value True)


def flag_on() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------- secret patterns (moved here from desktop/core.py)
_SECRET_RULES = (
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("provider_key", re.compile(r"\b(?:gsk_[A-Za-z0-9]{40,}|hf_[A-Za-z0-9]{30,}|AIza[0-9A-Za-z_-]{35}|xox[abpr]-[A-Za-z0-9-]{10,}|"
                                r"(?:sk|rk)_live_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("pin_or_code", re.compile(r"(?i)\b(?:pin|cvv|cvc|otp)\b\s*[=:]?\s*\d{3,8}\b")),
    ("key_assignment", re.compile(r"(?i)\b([A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*)\s*[=:]\s*['\"]?[^\s'\"]{4,}")),
)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----")
# A card number is a run of 13-19 digits (spaces or dashes between groups) standing on its own. NOT "20261019-121346-f83eca" (a task id:
# its date and time digits are 14 digits long and pass the Luhn check about one time in ten, which turned the lock holder's id into
# "[card number removed]"): the run may not touch a letter, digit or dash on either side.
_CARD = re.compile(r"(?<![\w-])\d(?:[ -]?\d){12,18}(?!\w|-\w)")


# A bank account in IBAN form (2 letters, 2 check digits, up to 30 letters/digits, spaces every four allowed), kept only when the ISO 13616 mod-97 check
# passes, so an ordinary code like "GB00FAKE..." is left alone and a real account number never reaches a model (found on the real laptop, 2026-10-25).
_IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}(?![A-Za-z0-9])")


def _iban_ok(raw: str) -> bool:
    s = re.sub(r"\s", "", raw).upper()
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


def _luhn(digits: str) -> bool:
    total, flip = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if flip:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        flip = not flip
    return total % 10 == 0


def scrub_text(text: str) -> tuple[str, list[str]]:
    """Screen text made safe to hand to a model: tokens, keys, passwords and card numbers are replaced, and text that holds a
    private key is withheld whole. Returns (clean text, names of the rules that fired). Rule names only, never the values."""
    text = str(text or "")
    if _PRIVATE_KEY.search(text):
        return "", ["private_key"]
    hits: list[str] = []
    for name, rx in _SECRET_RULES:
        text, n = rx.subn(lambda m, nm=name: f"[{nm} removed]", text)
        if n:
            hits.append(name)

    def card(m):
        digits = re.sub(r"\D", "", m.group(0))
        return "[card number removed]" if 13 <= len(digits) <= 19 and _luhn(digits) else m.group(0)
    text, n = _CARD.subn(card, text)
    if "[card number removed]" in text:
        hits.append("card_number")
    text, n = _IBAN.subn(lambda m: "[bank account removed]" if _iban_ok(m.group(0)) else m.group(0), text)
    if "[bank account removed]" in text:
        hits.append("bank_account")
    return text, hits


# ---------------------------------------------------------------- file and directory names: class only
_WIN_PATH = re.compile(r"(?:(?<!\w)[A-Za-z]:[\\/]|\\\\[\w.$-]+\\)[^\s\"'<>|*?]*")
_POSIX_PATH = re.compile(r"(?<![\w:/.~-])/[\w.@+~-]+(?:/[\w.@+~-]*)*")
_REL_WIN_PATH = re.compile(r"(?<![\w\\/.])(?:[\w()~-][\w.()~-]*\\)+[\w()~-][\w.()~-]*\.[A-Za-z0-9]{1,8}\b")   # Documents\\Taxes\\a.xlsx
_BARE_FILE = re.compile(r"(?<!\w)[\w()\-]+(?:\.[\w()\-]+)*\.(?:xlsx?|docx?|pptx?|pdf|csv|txt|rtf|odt|json|ini|cfg|conf|xml|ya?ml|toml|"
                        r"zip|7z|rar|jpe?g|png|gif|bmp|mp[34]|wav|log|bak|db|sqlite|kdbx|pem|key)\b", re.I)
_EXT = re.compile(r"\.([A-Za-z0-9]{1,8})$")


def file_class(name_or_path: Any) -> str:
    """"a .xlsx file" for any file name or path (the folders and the name itself are dropped); "a folder or file" without an
    extension. Never echoes any part of the input."""
    base = re.split(r"[\\/]", str(name_or_path or "").rstrip("\\/"))[-1]
    m = _EXT.search(base)
    return f"a .{m.group(1).lower()} file" if m else "a folder or file"


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_TRAIL = ".,;:!)]}'\""


def _class_of_path(m) -> str:
    """The class of a matched path, with the punctuation that ended the sentence put back after it."""
    raw = m.group(0)
    core = raw.rstrip(_TRAIL)
    return f"<{file_class(core)}>" + raw[len(core):]


def neutral_paths(text: Any, bare_names: bool = True) -> str:
    """Every Windows/UNC/POSIX path in the text, and (bare_names) every file name with a well-known extension, replaced by its
    class. For error messages, status details and metadata that must name no file; not for free text, which is released
    through release()."""
    s = as_text(text if text is not None else "")             # an object whose __str__ fails, bytes, a cycle: text, never an exception
    s = _WIN_PATH.sub(_class_of_path, s)
    s = _POSIX_PATH.sub(_class_of_path, s)
    s = _REL_WIN_PATH.sub(_class_of_path, s)
    if bare_names:
        s = _BARE_FILE.sub(lambda m: f"<{file_class(m.group(0))}>", s)
    return s


def cut_text(text: str, n: int) -> str:
    """text[:n], but never ending inside a half tag: "[PERSO" would reach the model or the owner as a stray fragment of a placeholder (D73)."""
    if len(text) <= n:
        return text
    head = text[:n]
    i = head.rfind("[")
    return head[:i] if i != -1 and "]" not in head[i:] and n - i <= 40 else head


def clean_message(text: Any, limit: int = 300, emails: bool = True) -> str:
    """A message that may reach a model, a log or the owner's status line: secrets, file names and (emails=True) e-mail addresses
    out, cut short."""
    s, _ = scrub_text(neutral_paths(text))
    if emails:
        s = _EMAIL.sub("[email]", s)
    return cut_text(" ".join(s.split()), limit)


def sanitize_meta(value: Any, skip_keys: tuple[str, ...] = ("url",), _depth: int = 0) -> Any:
    """Strings inside a result that goes to a model (titles, rows, notes) with paths and file names reduced to their class and
    secret patterns removed. Keys named in skip_keys (addresses) are left alone. Numbers and booleans pass unchanged."""
    if _depth > 8:
        return "(too deep)"
    if isinstance(value, str):
        return clean_message(value, limit=max(len(value), 1), emails=False) if value else value      # identity fields keep their e-mail
    if isinstance(value, dict):
        return {k: (v if k in skip_keys else sanitize_meta(v, skip_keys, _depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_meta(v, skip_keys, _depth + 1) for v in value]
    return value


# ---------------------------------------------------------------- the two ways out
@dataclass(frozen=True)
class Released:
    text: str            # what may go out; "" when withheld
    allowed: bool
    via: str             # "none" (nothing to send) | "local" | "gateway" | "withheld"
    note: str = ""       # neutral and non-sensitive: why it was withheld, or how it was cleared


def _withheld(note: str = WITHHELD) -> Released:
    return Released("", False, "withheld", note)


def local_model_ok(base_url: str) -> bool:
    """Whether base_url is a VERIFIED LOCAL model endpoint (the check that guards the owner's memory). Fails closed: if the
    check cannot be loaded or raises, the answer is no."""
    try:
        from jarvis.plugins.main_agent.memory_context import memory_allowed
        return bool(memory_allowed(base_url)[0])
    except Exception:  # noqa: BLE001
        return False


def _gateway_base() -> str:
    try:
        port = int(os.environ.get("JARVIS_PRIVACY_PORT", "48950"))
    except ValueError:
        port = 48950
    return f"http://127.0.0.1:{port}"


def gateway_state() -> tuple[bool, str]:
    """(ready, why not): /health must say ok, ready and a loaded PII model (research._gateway_state's contract). Never raises,
    never echoes a payload."""
    try:
        with loopback_http.urlopen(_gateway_base() + "/health", timeout=3) as r:
            if r.status != 200:
                return False, f"HTTP {r.status}"
            health = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__
    if not isinstance(health, dict) or health.get("ok") is not True:
        return False, "not the privacy gateway"
    if health.get("ready") is not True:
        return False, "degraded"
    if health.get("pii_model") is not True:
        return False, "the PII model is not loaded"
    return True, ""


def gateway_redact(text: str) -> str:
    """The text with private details AND names and places replaced by the privacy gateway. Raises RuntimeError (with a message
    that holds none of the text) unless the gateway's own answer confirms names and the PII model were active for this call."""
    # "restorable" (D73): the gateway keeps the map tag -> real value in its own memory, so the owner's display and voice can get the real values back;
    # a secret is never kept and comes back as [REDACTED:kind]. An older gateway ignores the key and answers as before (plain tags nothing restores).
    req = urllib.request.Request(_gateway_base() + "/redact", data=json.dumps({"text": text, "names": True, "restorable": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with loopback_http.urlopen(req, timeout=GATEWAY_TIMEOUT_S) as r:
            answer = json.loads(r.read().decode("utf-8", "replace"))
        out = answer["text"]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"the privacy gateway could not check the text ({type(e).__name__})") from None
    if not isinstance(answer, dict) or not isinstance(out, str):
        raise RuntimeError("the privacy gateway gave an unusable answer")
    if answer.get("names") is not True or answer.get("deep") is not True or answer.get("ready") is False:
        raise RuntimeError("names or the PII model were not active for this check")
    return out


_TAG = re.compile(r"\[(?:[A-Z_]+_\d+|REDACTED:[a-z_]+|[a-z_ ]{3,24} removed)\]")
TAG_HINT = (" Tags such as [PERSON_1] or [NUMBER_2] stand for real values that stay on this PC: copy a tag exactly as written into your answer and the owner "
            "sees and hears the real value (never guess it, never spell it out). A mark like [REDACTED:kind] or [card number removed] is a secret that is never shown: say that you hid a secret.")
CLEARED_NOTE = "names, places and private details were hidden by the privacy gateway; file names are shown as their class"


def with_tag_hint(note: str, text: str) -> str:
    """The note that travels with cleared screen text; when the text holds gateway tags it also tells the model how to give them back to the owner."""
    return note + TAG_HINT if _TAG.search(text or "") else note


def _before_gateway(text: str) -> str:
    """Paths and file names to their class and e-mail addresses masked, line breaks kept (the gateway sees the text's layout)."""
    return _EMAIL.sub("[email]", neutral_paths(text))


def release(text: Any, *, model_url: str | None = None) -> Released:
    """May this screen/disk-derived text go to a model? model_url is the endpoint it is going to, when the caller can name it
    (a verified local model gets the text raw, after the secret pass); with no model_url, or any other endpoint, it must pass the
    privacy gateway. Never raises; the text is never returned unless it is cleared."""
    try:
        raw = str(text if text is not None else "")
        if not raw.strip():
            return Released("", True, "none")
        if not flag_on():
            return _withheld(OFF_NOTE)
        clean, hits = scrub_text(raw[:MAX_TEXT])
        if "private_key" in hits:
            return _withheld("the text held a private key, so none of it is shown")
        if model_url is not None and local_model_ok(model_url):
            return Released(clean, True, "local", "read by a verified local model")
        ready, why = gateway_state()
        if not ready:
            return _withheld()
        # The gateway hides names, places, numbers and keys; measured with the real Presidio + spaCy (en_core_web_sm) it does NOT
        # hide a file path, a file name or (for an unusual top-level domain) an e-mail address. Those are reduced here first.
        cleared = gateway_redact(_before_gateway(clean))
        return Released(cleared, True, "gateway", with_tag_hint(CLEARED_NOTE, cleared))
    except Exception:  # noqa: BLE001 - any failure at all withholds; the raw text is never the fallback
        return _withheld()


def release_for_request(text: Any) -> Released:
    """D54 (2026-10-26): the text of ONE window, for ONE explicit request of the owner ("solve what is on the screen"). It does NOT look at
    JARVIS_SCREEN_TEXT_TO_MODEL (that switch stays as it is for every other path: this function is called by nothing else than the goal runner's
    solve step, because the owner's request is itself the permission for this window's text), but it keeps every other rule: the secret pass
    (a private key withholds the text whole), and the text is cleared ONLY through the privacy gateway's /redact with its own answer confirming
    names and the PII model were active. A gateway that is down, degraded, old or unreadable, and any error at all, means withheld: the raw
    text is never the fallback and the caller stops and says so."""
    try:
        raw = str(text if text is not None else "")
        if not raw.strip():
            return Released("", True, "none")
        clean, hits = scrub_text(raw[:MAX_TEXT])
        if "private_key" in hits:
            return _withheld("the window held a private key, so none of it was sent")
        ready, why = gateway_state()
        if not ready:
            return _withheld(f"the privacy gateway is not ready ({why}), so the window's text was not sent")
        cleared = gateway_redact(_before_gateway(clean))
        return Released(cleared, True, "gateway", with_tag_hint(CLEARED_NOTE, cleared))
    except Exception:  # noqa: BLE001 - any failure at all withholds
        return _withheld("redaction could not be confirmed, so the window's text was not sent")


def release_local_for_task(text: Any, model_url: str) -> Released:
    """D54 (general rule): what a window shows, for the LOCAL model that chooses the next step of the owner's own task (drive.py). The owner's goal is
    the permission, so JARVIS_SCREEN_TEXT_TO_MODEL is not consulted (it stays as it is for every other path), but the destination is: ONLY a verified
    local model endpoint (the check that guards the owner's memory; never the privacy gateway, never an online provider), after the secret pass. Anything
    else, and any error, means withheld; the raw text never goes anywhere else."""
    try:
        raw = str(text if text is not None else "")
        if not raw.strip():
            return Released("", True, "none")
        clean, hits = scrub_text(raw[:MAX_TEXT])
        if "private_key" in hits:
            return _withheld("the window held a private key, so none of it is shown")
        if not local_model_ok(model_url):
            return _withheld("the model is not a verified local one, so the window's text was not shown to it")
        return Released(clean, True, "local", "read by a verified local model")
    except Exception:  # noqa: BLE001
        return _withheld()


def release_safely(text: Any, **kw) -> Released:
    """release(), looked up at call time, with a second net: if release itself raises (or returns something unexpected) the
    answer is 'withheld', never the text. Every sink calls this one."""
    try:
        out = release(text, **kw)
        if isinstance(out, Released) and (out.allowed or out.via == "withheld"):
            return out
    except Exception:  # noqa: BLE001
        pass
    return _withheld()


def withheld_note(why: str = "") -> str:
    return f"[{why or WITHHELD}]"


def length_only(text: Any) -> str:
    """What a log or an error path may say about a screen text: how long it was, nothing else."""
    return f"{len(str(text or ''))} characters"


# ---------------------------------------------------------------- the plugin boundary: clean_result() / cleaned()
# Re-review M12 (2026-10-18): run_pc_task returned steps[].checked/uncertain/question raw, mission_status returned explain
# ("File: <path>") raw, jarvis_bridge.pending_approvals returned tool/op/target raw. Cleaning field by field missed every field
# nobody thought of, so the boundary decides for EVERY string, and an unlisted key is the strict case:
#   SAFE_RAW_KEYS     identifiers, states, counts and durations, raw only when the value really looks like one (short, plain);
#   NEUTRAL_KEYS      owner-facing sentences the plugin itself wrote (summary, message, error, question, titles ...): clean_message():
#                     secrets, paths and file names (as their class) and e-mail addresses removed, cut short. Names are NOT hidden;
#   every other key   (observed text: checked, explain, text, page ... and any field added later) goes through release():
#                     flag, secret pass, then a verified local model or the privacy gateway (names and places hidden), else a note.
# A short plain token (an id, a stamp, an enum word) is not text: it passes raw under any key unless a secret pattern fires on it.
# Numbers, booleans and null are never text and pass unchanged.
SAFE_RAW_KEYS = frozenset({
    "id", "ids", "n", "tool", "agent", "attempts", "passed", "status", "state", "ok", "count", "counts", "task", "mission", "mission_id",
    "command_id", "approval_id", "run_id", "job", "job_id", "task_id", "seconds", "duration", "ms", "route", "plan_model", "plan_seconds",
    "replans", "bytes", "format", "kind", "op", "action", "level", "mode", "source", "lane", "version", "step", "steps_done", "total",
    "stopped_jobs", "requested", "recovered", "cancelled", "stopped", "ready", "busy", "found", "verified", "outcome_code",
    "language", "lang", "profile", "at", "created", "updated", "finished", "started", "tasks_stopped", "check", "app", "provider",
    "transport", "lanes", "tools_enabled", "enabled", "seconds_left", "token_budget",
    # ids carried by busy / stop / recover / status answers, the lock holder's, jobs' and approvals' (desktop, jarvis_bridge, main_agent)
    "holder", "holder_task", "holder_op", "operation", "operations", "approval", "approvals", "approvals_denied", "approvals_gone",
    "approvals_pending", "approvals_unreachable", "jobs", "jobs_cancelled", "unconfirmed", "unconfirmed_jobs", "missions", "tasks", "run",
    "runs", "held", "still_running", "worker_still_running", "workers", "thread", "pid", "epoch", "beat", "cursor", "replayed",
    "interrupted_runs", "resumable", "rerun", "role", "type", "event_type", "owner", "owner_task", "lock", "stale", "age_s", "idle_s",
    "waited_s", "hours", "worker", "current"})
NEUTRAL_KEYS = frozenset({
    "summary", "message", "messages", "error", "errors", "reason", "how", "question", "prompt", "note", "notes", "goal", "cause", "outcome",
    "evidence", "title", "titles", "label", "labels", "buttons", "window", "windows", "window_title", "name", "description", "page_note",
    "text_note", "hint", "warning", "warnings", "undo", "fix", "why", "source_label",
    # JARVIS bridge: the approval card's own line, its target, and the reply the owner chose to hand back (JARVIS_BRIDGE_SPEAK_REPLY)
    "what", "reply", "target", "tool", "op",
    # labels the plugins write themselves: where a reading came from, how it was obtained, a file as its class
    "source", "basis", "file", "kind_label", "app_name",
    # what the PC-operation registry says about an operation (coordinator.Operation.describe): its last progress line, why it was stopped
    "last", "stop_reason", "lock_release_failed", "events"})
URL_KEYS = frozenset({"url", "href", "link"})
_ID = re.compile(r"[A-Za-z0-9_.:\-]{0,64}")

# The project's OWN identifier formats. A value that FULLY matches one of these is an id, not text, whatever its key and whatever a
# secret scan makes of its digits; a longer string that merely contains one is cleaned like any other text.
ID_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = tuple((n, re.compile(p)) for n, p in (
    ("task", r"\d{8}-\d{6}-[0-9a-f]{6}"),                                         # desktop task: 20261019-121346-f83eca
    ("mission", r"m\d{8}-\d{6}-[0-9a-f]{6}"),                                      # desktop mission: m20261019-121346-f83eca
    ("operation", r"(?:single|step|task|agent-step|mission|job|run|stop)(?:-[a-z0-9_]+){1,6}"),   # task-ab12cd, single-run_pc_action-ab12
    ("hex", r"[0-9a-f]{8,32}"),                                                        # kernel job (12), task (12), approval (16), run id (10), uuid hex (32)
    ("prefixed_hex", r"[a-z]{1,8}-[0-9a-f]{6,16}"),                                    # command c-<12 hex>, bench job-<10 hex>, m-<8 hex>
    ("uuid", r"(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
    ("iso_time", r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:?\d{2})?)?"),
    ("clock", r"\d{2}:\d{2}(?::\d{2})?"),
    ("enum", r"[a-z][a-z0-9_]{0,39}"),                                                 # status / state / kind words
))
_SENSITIVE_KEY = re.compile(r"(?i)token|secret|passw|cookie|auth|api_?key|credential|private")


def is_known_id(text: Any) -> bool:
    """Whether the whole value is one of the project's own id formats (ID_PATTERNS)."""
    t = str(text)
    return len(t) <= 80 and any(rx.fullmatch(t) for _, rx in ID_PATTERNS)
MAX_STRINGS = 3000
MAX_DEPTH = 12
MAX_LIST = 500
STRING_LIMIT = 2000


class _Budget:
    def __init__(self):
        self.strings = 0
        self.calls = 0
        self.released: dict[str, str] = {}


GATEWAY_CALLS = 20                      # distinct strings one result may send to the gateway; the rest is withheld (a large result stays fast)


_TOKEN = re.compile(r"[A-Za-z0-9_.:\-]{1,64}")
_FILELIKE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,6}$")


def _token_like(text: str) -> bool:
    """A short plain token that reads as an identifier or an enum word (an approval id, a backup stamp, 'observed', 'RED'), not as
    prose, a path or a file name: no space, no slash, not ending in a file extension, and either all lower case or holding a digit
    or one of _ . : -. A lone capitalised word (a name) is not a token. A lone lower-case word is (undecidable; see the report)."""
    if not _TOKEN.fullmatch(text) or _FILELIKE.search(text):
        return False
    return text == text.lower() or text == text.upper() or any(c.isdigit() or c in "_.:-" for c in text)


_CLASS_ONLY = re.compile(r"<?(?:an? \.[A-Za-z0-9]{1,8} file|a folder or file)>?")      # file_class() output: names nothing


def _clean_string(text: str, key: str, budget: _Budget, already_released: bool) -> str:
    budget.strings += 1
    if budget.strings > MAX_STRINGS:
        return "(more text omitted)"
    if _CLASS_ONLY.fullmatch(text):
        return text
    sensitive = bool(_SENSITIVE_KEY.search(key))                # a key named token / secret / password ...: never a raw shortcut
    if is_known_id(text) and not sensitive:
        return text                                             # one of the project's own id formats: verbatim, under any key
    plain = scrub_text(text) == (text, [])                      # an id-shaped secret is not an id
    if plain and not sensitive and ((key in SAFE_RAW_KEYS and _ID.fullmatch(text)) or _token_like(text)):
        return text
    if key in URL_KEYS:
        return scrub_text(text)[0][:STRING_LIMIT]
    if key in NEUTRAL_KEYS or already_released:
        return clean_message(text, limit=STRING_LIMIT)
    if text not in budget.released:
        if not text.strip():
            budget.released[text] = text
        elif flag_on() and budget.calls >= GATEWAY_CALLS:
            budget.released[text] = withheld_note("more screen text than one result may send for clearing")
        else:
            budget.calls += 1
            got = release_safely(neutral_paths(text))
            budget.released[text] = got.text if got.allowed else withheld_note(got.note)
    return budget.released[text]


_PLAIN_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,60}")


def _clean_key(k: Any) -> str:
    """A result's dict key. Plain identifier words and known id formats stay; a key that is an e-mail address, a path, a secret or a
    sentence (a dict keyed by file name or by person) goes through the message cleaner like a value."""
    ks = as_text(k)[:200]
    if _PLAIN_KEY.fullmatch(ks) or is_known_id(ks):
        return ks
    return clean_message(ks, 80)


def clean_result(value: Any, *, key: str = "", _budget: _Budget | None = None, _depth: int = 0, _released: bool = False) -> Any:
    """A plugin result made safe to hand to the chat model, the chat push, the run file, a log or the widget (see the block comment
    above for the policy). Always returns a new JSON-compatible value; never raises (anything it cannot read becomes a note)."""
    budget = _budget or _Budget()
    try:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if _depth > MAX_DEPTH:
            return "(nested too deeply)"
        if isinstance(value, str):
            return _clean_string(value, key, budget, _released)
        if isinstance(value, (bytes, bytearray)):
            return "(binary data omitted)"
        if isinstance(value, dict):
            released = _released or value.get(SCREEN_KEY) is True      # the plugin already cleared its screen text for the gateway
            out = {}
            for k, v in list(value.items())[:MAX_LIST]:
                ks = _clean_key(k)
                out[ks] = clean_result(v, key=ks, _budget=budget, _depth=_depth + 1, _released=released)
            return out
        if isinstance(value, (list, tuple, set, frozenset)):
            items = list(value)[:MAX_LIST]
            return [clean_result(v, key=key, _budget=budget, _depth=_depth + 1, _released=_released) for v in items]
        return clean_message(str(value), limit=STRING_LIMIT)
    except Exception:  # noqa: BLE001 - a result that cannot be cleaned is not passed on as it is
        return "(result could not be cleaned)"


def cleaned(fn):
    """Decorator for an async plugin entry (put it directly under @plugin_entry): its Ok(value) goes out through clean_result and
    its Err message through clean_message, so a field nobody listed is cleaned by default."""
    import functools

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        res = await fn(*args, **kwargs)
        try:
            from jarvis.pluginapi import Err, Ok, SdkError
            if isinstance(res, Ok):
                return Ok(clean_result(res.value))
            if isinstance(res, Err):
                err = getattr(res, "error", "")
                msg = clean_message(str(getattr(err, "message", None) or err), limit=600)
                code = getattr(err, "code", None)
                return Err(SdkError(msg, code=code) if code else SdkError(msg))
        except Exception:  # noqa: BLE001
            pass
        return res
    wrapper.__screen_cleaned__ = True
    return wrapper
