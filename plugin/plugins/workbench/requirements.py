"""Source-grounded requirements from a project's segments.

Every item cites the segment(s) it came from (source file, line / message / timestamp / page, and the exact quote). Nothing
is added that no source says: the rule pass only labels sentences that exist, and a model pass (optional, local model
only) may only add items whose quote is found word for word in the segment it cites.

Kinds: request (what the client asks for), commitment (what the owner promised), preference, deadline, constraint,
question (asked and not answered in the sources). Each deliverable the sources mention gets an entry with the items that
concern it. Then:
- conflicts: two sources give different values for the same thing (a deadline, a budget, a count, a colour said both
  wanted and not wanted, or a later "actually / instead" change); both are listed, none is picked silently;
- questions for the owner: what is missing for a deliverable (no deadline, no format or size, no budget anywhere), which
  speaker is the owner, an audio language that was not confirmed, client questions with no reply;
- ignored instructions: text addressed to an AI ("ignore your instructions", "JARVIS must send...") is listed as
  evidence and never treated as a request.

English is covered by the rules. Hindi and Telugu (native script and common romanised words) are recognised for needs,
deadlines and budgets with a short cue list; those items are marked ``needs_translation_review`` because no
translation is done here, and the quote stays in the original language.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

from . import safety

# ---------------------------------------------------------------- cues
_REQ = re.compile(
    r"(?i)\b(i|we)\s*(?:'d|would)?\s*(need|want|would like|like to have|require|are looking for|am looking for|expect|"
    r"must have|have to have)\b|\b(can|could|would|will)\s+you\b|\bplease\b|\bmake sure\b|\bit (?:must|should|needs? to)\b"
    r"|\b(?:needs?|must|should|has to|have to)\s+(?:be|have|include|show|use|look|contain|cover|support|work|run|mention)\b"
    r"|^\s*(?:create|design|build|write|prepare|draft|make|add|include|update|redo|change|use|send|share|give|show|"
    r"put|keep|remove|translate|research|find|list|compare|summari[sz]e)\b")
_REQ_INDIC = re.compile(r"(चाहिए|करना है|करना होगा|भेज|बनाइए|बनाना|दीजिए|कीजिए|కావాలి|చేయండి|పంపండి|ఇవ్వండి|తయారు|చేయాలి)"
                        r"|(?i:\b(chahiye|banana hai|bana do|bhej do|kar do|kavali|kaavali|cheyyandi|cheyandi|pampandi|"
                        r"ivvandi|cheyali)\b)")
_COMMIT = re.compile(r"(?i)\b(i|we)\s*(?:'ll|will|shall|am going to|are going to|can|'m going to)\s+(?!not\b)\w+"
                     r"|\blet me\b|\b(i|we)\s+(?:promise|commit)\b|\bwill (?:send|share|deliver|get back|do|have)\b")
_PREF = re.compile(r"(?i)\b(prefer(?:ably|red|s)?|(?:i|we) (?:really )?(?:like|love|hate|dislike)|(?:don't|do not) like|"
                   r"rather|instead of|avoid|style|tone|look and feel|vibe|minimal(?:ist)?|modern|clean|elegant|bold|"
                   r"colou?rs?|fonts?|theme)\b")
_CONSTRAINT = re.compile(r"(?i)\b(budget|cost|price|fee|rate|must not|mustn't|cannot|can't|shouldn't|should not|"
                         r"no more than|not more than|at most|maximum|max|minimum|at least|only|within|limit|"
                         r"format|resolution|size|dimensions?|pages?|words?|slides?|language)\b"
                         r"|[₹$€£]\s?\d|\b(?:rs\.?|inr|usd|eur)\s?\d|\d\s?(?:rs|inr|usd|k|lakh|lakhs|crore)\b"
                         r"|(बजट|बजेट|బడ్జెట్)")
_MONTHS = "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
_DAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun"
_DEADLINE_DATE = re.compile(
    r"(?i)\b(?:by|before|until|till|on|due|deadline|latest by|no later than)\s+(?:the\s+)?"
    r"(?:(?:" + _DAYS + r")\b|tomorrow|today|tonight|eod|end of (?:the )?(?:day|week|month)|next\s+\w+|this\s+\w+|"
    r"\d{1,2}(?:st|nd|rd|th)?\s+(?:" + _MONTHS + r")|(?:" + _MONTHS + r")\s+\d{1,2}(?:st|nd|rd|th)?|"
    r"\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?|\d{4}-\d{2}-\d{2})"
    r"|\bin\s+(?:\d+|a|one|two|three)\s+(?:days?|weeks?|months?)\b"
    r"|(कल तक|परसों तक|రేపు\s*లోపు|రేపు|(?i:\bkal tak\b|\brepu\b))")
_DEADLINE_CUE = re.compile(r"(?i)\bdeadline\b|\bdue\b|\basap\b|\burgent(?:ly)?\b|(तक|లోపు|లోగా|(?i:\blopu\b|\bloga\b))")
VAGUE_DEADLINES = {"deadline", "due", "asap", "urgent", "urgently", "तक", "లోపు", "లోగా", "lopu", "loga"}


class _Deadline:
    """search() returns the specific date if there is one, else a bare cue ("asap", "due")."""

    @staticmethod
    def search(text: str):
        return _DEADLINE_DATE.search(text) or _DEADLINE_CUE.search(text)


_DEADLINE = _Deadline()
_QUESTION_START = re.compile(r"(?i)^\s*(what|which|when|where|who|whom|whose|why|how|is|are|do|does|did|can|could|"
                             r"would|will|should|shall|may|have|has)\b")
_CHANGE = re.compile(r"(?i)\b(actually|instead|change (?:it|that|the)|scratch that|on second thought|not .{1,30} but|"
                     r"rather than|switch to|let'?s go with|update:)\b")
_SENT = re.compile(r"(?<=[.!?।])\s+|\n+")

DELIVERABLES: list[tuple[str, str]] = [
    ("presentation", r"\b(presentation|pitch deck|deck|slides?|ppt|powerpoint|keynote)\b|प्रेजेंटेशन|प्रस्तुति|ప్రెజెంటేషన్|ప్రజెంటేషన్"),
    ("logo", r"\b(logo|logotype|brand mark|emblem)\b|लोगो|లోగో"),
    ("website", r"\b(website|web site|landing page|web page|webpage|homepage|site)\b|वेबसाइट|వెబ్‌?సైట్"),
    ("spreadsheet", r"\b(spreadsheet|excel|sheet|tracker|budget sheet|xlsx|csv)\b"),
    ("content_calendar", r"\b(content calendar|posting schedule|editorial calendar)\b"),
    ("social_posts", r"\b(instagram|social media|posts?|reels?|stories|carousel|linkedin post|tweets?)\b"),
    ("print_design", r"\b(poster|flyer|brochure|banner|business cards?|leaflet|menu card|invitation)\b"),
    ("video", r"\b(video|reel edit|animation|explainer)\b"),
    ("app_or_code", r"\b(app|application|dashboard|script|automation|chatbot|plugin|api|software|tool)\b"),
    ("research", r"\b(market research|competitor (?:analysis|research)|research|survey analysis|study)\b"),
    ("email_or_message", r"\b(email|newsletter|mail|message to|whatsapp message|announcement)\b"),
    ("report", r"\b(report|proposal|brief|document|doc|pdf|whitepaper|case study|article|blog|copy|content|write-?up|summary)\b"),
]
_DELIV_RE = [(name, re.compile(rx, re.I)) for name, rx in DELIVERABLES]
# What a deliverable needs before it can be made well; a missing one becomes a question.
NEEDS: dict[str, list[tuple[str, re.Pattern, str]]] = {
    "presentation": [("size", re.compile(r"(?i)\b\d+\s*(?:slides?|pages?)\b|\bslides?\b.{0,20}\b\d+\b"),
                      "How many slides should the presentation have?"),
                     ("audience", re.compile(r"(?i)\b(audience|investors?|customers?|team|board|students?|clients?)\b"),
                      "Who is the presentation for?")],
    "logo": [("style", _PREF, "What style, colours or references should the logo follow?"),
             ("format", re.compile(r"(?i)\b(svg|png|vector|ai|eps|pdf|jpg)\b"), "Which file formats does the client need for the logo?")],
    "website": [("pages", re.compile(r"(?i)\b(\d+\s+pages|home|about|contact|services|gallery|shop|blog|classes|menu|pricing|faq)\b"),
                 "Which pages should the website have?"),
                ("hosting", re.compile(r"(?i)\b(domain|hosting|host|wordpress|shopify|wix|webflow)\b"),
                 "Is there a domain or hosting, or a platform the website must use?")],
    "report": [("length", re.compile(r"(?i)\b\d+\s*(?:pages?|words?)\b|\b(short|brief|one[- ]page|detailed)\b"),
                "How long should the document be?")],
    "social_posts": [("count", re.compile(r"(?i)\b\d+\s*(?:posts?|reels?|stories)\b"), "How many posts are needed?"),
                     ("platform", re.compile(r"(?i)\b(instagram|facebook|linkedin|twitter|x\.com|youtube|whatsapp)\b"),
                      "Which platform are the posts for?")],
    "spreadsheet": [("columns", re.compile(r"(?i)\b(columns?|fields?|rows?|track|tabs?)\b"), "What should the sheet track (columns)?")],
}
_COLOURS = r"red|blue|green|yellow|orange|purple|violet|pink|black|white|grey|gray|gold|silver|brown|beige|maroon|navy|teal|cyan|magenta"
_COLOUR_RE = re.compile(rf"(?i)\b(no|not|avoid|without|don't use|do not use|instead of|rather than)?\s*(?:\w+\s+){{0,2}}?\b({_COLOURS})\b")
_AMOUNT = re.compile(r"(?i)(?:[₹$€£]|\b(?:rs\.?|inr|usd|eur)\b)\s?([\d,]+(?:\.\d+)?)\s*(k|lakh|lakhs|crore)?"
                     r"|([\d,]+(?:\.\d+)?)\s*(k|lakh|lakhs|crore)?\s*(?:rs\.?|rupees|inr|usd|dollars|eur)\b")
_COUNT = re.compile(r"(?i)\b(\d{1,4})\s*(slides?|pages?|posts?|reels?|words?|seconds?|minutes?|screens?|logos?|options?|concepts?|revisions?)\b")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text or "") if s and s.strip()]


def _role(speaker: str, owner_names: list[str], speakers: set[str]) -> str:
    sp = speaker.strip().lower()
    if not sp:
        return "source"
    for n in owner_names:
        n = n.strip().lower()
        if n and (sp == n or sp.startswith(n + " ") or n.startswith(sp + " ") or sp == n.split()[0]):
            return "owner"
    if sp in ("me", "you (owner)", "owner"):
        return "owner"
    return "client" if owner_names or len(speakers) > 1 else "source"


def _deliverable_types(text: str) -> list[str]:
    found = [name for name, rx in _DELIV_RE if rx.search(text)]
    if "content_calendar" in found:
        found = [f for f in found if f not in ("spreadsheet", "social_posts")]
    if "report" in found and len(found) > 1:              # "document"/"content" is generic; a concrete type wins
        found.remove("report")
    return found


def _amount_value(m: re.Match) -> float | None:
    num, mult = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    try:
        v = float(num.replace(",", ""))
    except (AttributeError, ValueError):
        return None
    scale = {"k": 1e3, "lakh": 1e5, "lakhs": 1e5, "crore": 1e7}.get((mult or "").lower(), 1)
    return v * scale


def _ref_text(seg: dict[str, Any]) -> str:
    r = seg.get("ref") or {}
    parts = [seg.get("source", "")]
    if "message" in r:
        parts.append(f"msg {r['message']}")
    if "time" in r and r["time"]:
        parts.append(str(r["time"]))
    if "page" in r:
        parts.append(f"p.{r['page']}")
    if "row" in r:
        parts.append(f"row {r['row']}")
    if "line" in r and "message" not in r and "time" not in r:
        parts.append(f"line {r['line']}")
    return ", ".join(str(p) for p in parts if p != "")


def evidence_of(seg: dict[str, Any], quote: str) -> dict[str, Any]:
    return {"segment": seg["id"], "source": seg.get("source"), "ref": seg.get("ref") or {}, "where": _ref_text(seg),
            "quote": safety.safe_for_output(quote, 400), "speaker": seg.get("speaker", "")}


def extract(segments: list[dict[str, Any]], owner_names: list[str] | None = None,
            answers: dict[str, str] | None = None, model_items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    owner_names = [n for n in (owner_names or []) if n.strip()]
    speakers = {s.get("speaker", "").strip().lower() for s in segments if s.get("speaker", "").strip()}
    roles = {sp: _role(sp, owner_names, speakers) for sp in speakers}
    items: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    context: list[dict[str, Any]] = []
    asked: list[tuple[int, dict[str, Any], str, str]] = []           # (position, segment, sentence, role)

    def add(kind: str, seg: dict[str, Any], sent: str, role: str, **extra: Any) -> dict[str, Any]:
        item = {"id": f"R{len(items) + 1}", "kind": kind, "text": safety.safe_for_output(sent, 600), "role": role,
                "speaker": seg.get("speaker", ""), "lang": seg.get("lang", "en"), "evidence": [evidence_of(seg, sent)]}
        if seg.get("lang") in ("hi", "te", "hi-Latn", "te-Latn", "mixed"):
            item["needs_translation_review"] = True
        if seg.get("audio"):
            item["from_audio"] = True
            if not seg.get("language_confirmed", True):
                item["needs_translation_review"] = True
        item.update(extra)
        items.append(item)
        return item

    for pos, seg in enumerate(segments):
        role = roles.get(seg.get("speaker", "").strip().lower(), "source") if seg.get("speaker") else "source"
        if seg.get("to_ai"):
            ignored.append(evidence_of(seg, seg.get("text", "")))
            continue
        for sent in _sentences(seg.get("text", "")):
            if re.match(r"^\s*#{1,6}\s", sent):              # a document heading: a title, not a request
                continue
            if safety.addressed_to_ai(sent):
                ignored.append(evidence_of(seg, sent))
                continue
            types = _deliverable_types(sent)
            deadline = _DEADLINE.search(sent)
            amounts = [v for v in (_amount_value(m) for m in _AMOUNT.finditer(sent)) if v is not None]
            counts = [(int(m.group(1)), m.group(2).lower().rstrip("s")) for m in _COUNT.finditer(sent)]
            change = bool(_CHANGE.search(sent))
            is_q = sent.rstrip().endswith("?") or (bool(_QUESTION_START.match(sent)) and not _REQ.search(sent))
            extra = {k: v for k, v in (("deliverables", types), ("deadline", deadline.group(0) if deadline else None),
                                       ("amounts", amounts), ("counts", counts), ("changes_earlier", change)) if v}
            if is_q:
                if role != "owner" and (types or _REQ.search(sent)):      # "can you make a logo?" is a request, not a question
                    add("request", seg, sent, role, **extra)
                else:
                    asked.append((pos, seg, sent, role))
                continue
            if role == "owner":
                if _COMMIT.search(sent):
                    add("commitment", seg, sent, role, **extra)
                elif deadline or amounts:
                    add("constraint" if amounts else "deadline", seg, sent, role, said_by_owner=True, **extra)
                continue
            if _REQ.search(sent) or _REQ_INDIC.search(sent):
                add("request", seg, sent, role, **extra)
            elif _PREF.search(sent):
                add("preference", seg, sent, role, **extra)
            elif deadline:
                add("deadline", seg, sent, role, **extra)
            elif _CONSTRAINT.search(sent) or amounts or counts:
                add("constraint", seg, sent, role, **extra)
            elif types and role in ("client", "source"):
                add("request", seg, sent, role, implied=True, **extra)
            elif role == "source" and not seg.get("audio") and len(sent) > 20:
                context.append(evidence_of(seg, sent))           # a fact from a document: background, not a requirement

    _inherit_deliverables(items, segments)
    accepted_model, rejected_model = _merge_model_items(model_items or [], segments, items)

    # unanswered questions: a question by one side with no later message from the other side
    for pos, seg, sent, role in asked:
        later = [s for s in segments[pos + 1:pos + 6] if s.get("source") == seg.get("source") and s.get("speaker") and
                 roles.get(s.get("speaker", "").strip().lower()) not in (role, None, "source")]
        if not later and role in ("client", "owner"):
            add("question", seg, sent, role, asked_by=role, unanswered=True)

    deliverables = _deliverables(items)
    conflicts = _conflicts(items, deliverables)
    questions = _questions(items, deliverables, conflicts, segments, owner_names, roles, answers or {})
    _annotate(items, conflicts)
    sensitive = sensitive_segments(segments)
    return {
        "generated": round(time.time(), 3),
        "speakers": {sp: r for sp, r in roles.items()},
        "items": items, "deliverables": deliverables, "conflicts": conflicts, "questions": questions,
        "ignored_instructions": ignored, "context": context[:200], "sensitive": sensitive,
        "model_items": {"accepted": accepted_model, "rejected": rejected_model},
        "method": ("rule-based labelling of sentences that exist in the sources" +
                   (", plus model-suggested items whose quotes were found in the cited segment" if accepted_model else "")),
        "limits": ["Requests phrased in unusual ways can be missed: read the sources list too.",
                   "Hindi and Telugu are recognised by a short cue list and are not translated (items are marked).",
                   "Audio has no speaker separation: recorded requests are attributed to the recording."],
        "stats": {"segments": len(segments), "items": len(items), "ignored_instructions": len(ignored),
                  "conflicts": len(conflicts), "open_questions": sum(1 for q in questions if q["status"] == "open")},
    }


def _inherit_deliverables(items: list[dict[str, Any]], segments: list[dict[str, Any]]) -> None:
    """"It should have Home, Classes and Contact pages" right after "we need a landing page": the same speaker, the same
    source, within two messages. Marked ``deliverable_inferred`` so the owner can see it was inferred, not said."""
    pos = {s["id"]: i for i, s in enumerate(segments) if "id" in s}
    last: dict[tuple[str, str], tuple[int, list[str]]] = {}
    for it in items:
        if it["role"] == "owner" or not it["evidence"]:
            continue
        ev = it["evidence"][0]
        key = (str(ev.get("source")), it.get("speaker", ""))
        at = pos.get(ev["segment"], -10)
        if it.get("deliverables"):
            last[key] = (at, it["deliverables"])
        elif (it["kind"] in ("request", "preference") and not it.get("amounts") and key in last
              and 0 <= at - last[key][0] <= 2):                # a budget or other project-wide constraint is never inherited
            it["deliverables"] = list(last[key][1])
            it["deliverable_inferred"] = True


def _merge_model_items(model_items: list[dict[str, Any]], segments: list[dict[str, Any]],
                       items: list[dict[str, Any]]) -> tuple[int, list[dict[str, str]]]:
    """A model's suggestion is kept only if its quote is in the segment it cites (whitespace-insensitive)."""
    by_id = {s["id"]: s for s in segments if "id" in s}
    accepted, rejected = 0, []
    for m in model_items[:200]:
        if not isinstance(m, dict):
            continue
        seg = by_id.get(str(m.get("segment") or ""))
        quote = " ".join(str(m.get("quote") or "").split())
        kind = str(m.get("kind") or "")
        if kind not in ("request", "preference", "deadline", "constraint", "commitment"):
            rejected.append({"why": "unknown kind"})
            continue
        if not seg or len(quote) < 8 or quote.lower() not in " ".join(seg.get("text", "").split()).lower():
            rejected.append({"why": "quote not found in the cited segment"})
            continue
        if seg.get("to_ai") or safety.addressed_to_ai(quote):
            rejected.append({"why": "the cited text is addressed to an AI"})
            continue
        if any(seg["id"] == e["segment"] and quote.lower() in it["text"].lower() for it in items for e in it["evidence"]):
            continue                                               # the rules already have it
        items.append({"id": f"R{len(items) + 1}", "kind": kind, "text": safety.safe_for_output(quote, 600),
                      "role": "client" if kind != "commitment" else "owner", "speaker": seg.get("speaker", ""),
                      "lang": seg.get("lang", "en"), "evidence": [evidence_of(seg, quote)], "suggested_by_model": True,
                      **({"deliverables": _deliverable_types(quote)} if _deliverable_types(quote) else {})})
        accepted += 1
    return accepted, rejected


def _deliverables(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for it in items:
        if it["kind"] == "question" and it.get("asked_by") == "owner":
            continue
        for t in it.get("deliverables") or []:
            d = out.setdefault(t, {"id": "", "type": t, "requirements": [], "evidence": [], "deadlines": [], "counts": []})
            d["requirements"].append(it["id"])
            d["evidence"].extend(it["evidence"][:1])
            if it.get("deadline") and it["role"] == "owner":
                d.setdefault("commitments", []).append({"text": it["deadline"], "item": it["id"]})
            elif it.get("deadline"):
                d["deadlines"].append({"text": it["deadline"], "item": it["id"]})
            for c in it.get("counts") or []:
                d["counts"].append({"count": c[0], "unit": c[1], "item": it["id"]})
    rows = list(out.values())
    # items with no deliverable word that sit next to one (same segment) belong to it; general ones apply to all
    for i, d in enumerate(rows, 1):
        d["id"] = f"D{i}"
        d["requested"] = any(next((x for x in items if x["id"] == r), {}).get("kind") == "request" for r in d["requirements"])
    general = [it["id"] for it in items if not it.get("deliverables") and it["kind"] in ("preference", "constraint", "deadline")]
    for d in rows:
        d["applies_general"] = general
    return [d for d in rows if d["requested"] or any(
        next((x for x in items if x["id"] == r), {}).get("role") == "owner" for r in d["requirements"])]


def _norm_deadline(text: str) -> str:
    return re.sub(r"(?i)^(by|before|until|till|on|due|deadline|latest by|no later than)\s+(the\s+)?", "", text.strip()).lower()


def _conflicts(items: list[dict[str, Any]], deliverables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []

    def add(topic: str, ids: list[str], values: list[str], note: str) -> None:
        conflicts.append({"id": f"C{len(conflicts) + 1}", "topic": topic, "items": ids, "values": values, "note": note,
                          "status": "open"})

    by_id = {it["id"]: it for it in items}
    for d in deliverables:
        dl = {}
        for x in d["deadlines"]:
            dl.setdefault(_norm_deadline(x["text"]), []).append(x["item"])
        specific = {k: v for k, v in dl.items() if k not in VAGUE_DEADLINES}
        if len(specific) > 1:
            add(f"{d['type']} deadline", sorted({i for v in specific.values() for i in v}, key=lambda s: int(s[1:])), list(specific),
                "The sources give different dates; confirm which one holds.")
        per_unit: dict[str, dict[int, list[str]]] = {}
        for c in d["counts"]:
            per_unit.setdefault(c["unit"], {}).setdefault(c["count"], []).append(c["item"])
        for unit, vals in per_unit.items():
            if len(vals) > 1:
                add(f"{d['type']} {unit} count", sorted({i for v in vals.values() for i in v}, key=lambda s: int(s[1:])),
                    [f"{k} {unit}s" for k in vals], "Different numbers were mentioned; confirm the final one.")
    budgets: dict[float, list[str]] = {}
    for it in items:
        if it.get("amounts") and re.search(r"(?i)budget|cost|price|fee|pay|बजट|బడ్జెట్", it["text"]) and it["role"] != "owner":
            for a in it["amounts"]:
                budgets.setdefault(a, []).append(it["id"])
    if len(budgets) > 1:
        add("budget", sorted({i for v in budgets.values() for i in v}, key=lambda s: int(s[1:])),
            [f"{v:,.0f}" for v in budgets], "More than one budget figure was given; confirm which one applies.")
    colour_pos: dict[str, list[str]] = {}
    colour_neg: dict[str, list[str]] = {}
    for it in items:
        if it["role"] == "owner":
            continue
        for m in _COLOUR_RE.finditer(it["text"]):
            (colour_neg if m.group(1) else colour_pos).setdefault(m.group(2).lower(), []).append(it["id"])
    for c in sorted(set(colour_pos) & set(colour_neg)):
        add(f"colour {c}", sorted(set(colour_pos[c] + colour_neg[c]), key=lambda s: int(s[1:])), [f"use {c}", f"avoid {c}"],
            f"{c} is both asked for and ruled out.")
    in_conflict = {i for c in conflicts for i in c["items"]}
    for it in items:
        if it.get("changes_earlier") and it["role"] != "owner" and it["id"] not in in_conflict:
            earlier = [x["id"] for x in items if x["id"] != it["id"] and int(x["id"][1:]) < int(it["id"][1:])
                       and x["role"] != "owner" and set(x.get("deliverables") or []) & set(it.get("deliverables") or [])]
            if earlier:
                add("changed request", earlier[-2:] + [it["id"]], [by_id[e]["text"][:80] for e in earlier[-2:]] + [it["text"][:80]],
                    "A later message changes an earlier one; the later one probably holds, but confirm.")
    return conflicts


def _questions(items, deliverables, conflicts, segments, owner_names, roles, answers) -> list[dict[str, Any]]:
    qs: list[dict[str, Any]] = []

    def add(qid_key: str, text: str, why: str, blocks: list[str], refs: list[str] | None = None) -> None:
        qid = f"Q{len(qs) + 1}"
        answer = answers.get(qid_key) or answers.get(qid)
        qs.append({"id": qid, "key": qid_key, "text": text, "why": why, "blocks": blocks, "refs": refs or [],
                   "status": "answered" if answer else "open", **({"answer": safety.safe_for_output(answer, 500)} if answer else {})})

    if not owner_names and len(roles) >= 2:
        add("owner_identity", f"Which of these speakers is you: {', '.join(sorted(roles))[:200]}?",
            "Without it, your own promises cannot be told apart from the client's requests.", [d["id"] for d in deliverables])
    if any(s.get("audio") and not s.get("language_confirmed", True) for s in segments):
        add("audio_language", "Which language is the recording in: English, Telugu or Hindi?",
            "It was transcribed with the English model; the wrong model gives a wrong transcript.", [d["id"] for d in deliverables])
    all_text = " ".join(it["text"] for it in items)
    has_budget = bool(re.search(r"(?i)budget|[₹$€£]\s?\d|\brs\.?\s?\d|rupees|बजट|బడ్జెట్", all_text))
    for d in deliverables:
        texts = " ".join(next((x["text"] for x in items if x["id"] == r), "") for r in d["requirements"] + d["applies_general"])
        if not [x for x in d["deadlines"] if _norm_deadline(x["text"]) not in VAGUE_DEADLINES]:
            add(f"{d['type']}:deadline", f"When is the {d['type'].replace('_', ' ')} due?",
                "No date was found in the sources for it.", [d["id"]], d["requirements"][:3])
        for key, rx, question in NEEDS.get(d["type"], []):
            if not rx.search(texts):
                add(f"{d['type']}:{key}", question, f"The sources do not say ({key}).", [d["id"]], d["requirements"][:3])
    if deliverables and not has_budget:
        add("budget", "Was a budget or price agreed?", "No budget figure was found in the sources.", [])
    for c in conflicts:
        add(f"conflict:{c['topic']}", f"Conflict on {c['topic']}: {' vs '.join(c['values'][:3])}. Which one holds?",
            c["note"], [d["id"] for d in deliverables if set(c["items"]) & set(d["requirements"])], c["items"])
    for it in items:
        if it["kind"] == "question" and it.get("asked_by") == "client":
            add(f"client_question:{it['evidence'][0]['segment']}", f"The client asked: \"{it['text'][:160]}\". What is the answer?",
                "No reply was found in the sources.", [], [it["id"]])
    for c in conflicts:
        q = next((q for q in qs if q["key"] == f"conflict:{c['topic']}"), None)
        if q and q["status"] == "answered":
            c["status"] = "resolved by the owner"
            c["resolution"] = q.get("answer", "")
    return qs


def to_markdown(req: dict[str, Any], project_name: str = "") -> str:
    def ev(it: dict[str, Any]) -> str:
        return "; ".join(f"[{e['where']}]" for e in it["evidence"])

    lines = [f"# Requirements{': ' + project_name if project_name else ''}", "",
             f"_Method: {req['method']}. Every line cites where it was said._", ""]
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for it in req["items"]:
        by_kind.setdefault(it["kind"], []).append(it)
    titles = [("request", "Client requests"), ("preference", "Preferences"), ("deadline", "Deadlines"),
              ("constraint", "Constraints"), ("commitment", "Your commitments"), ("question", "Questions with no reply")]
    for kind, title in titles:
        rows = by_kind.get(kind) or []
        if not rows:
            continue
        lines += [f"## {title}", ""]
        for it in rows:
            flags = []
            if it.get("needs_translation_review"):
                flags.append("needs translation review")
            if it.get("suggested_by_model"):
                flags.append("model-suggested, quote verified")
            if it.get("implied"):
                flags.append("implied")
            if it.get("confidence") and it["confidence"] != "high":
                flags.append(f"confidence {it['confidence']}")
            if str(it.get("validation_status", "")).startswith("in conflict"):
                flags.append(it["validation_status"])
            lines.append(f"- **{it['id']}** {it['text']} {ev(it)}{' _(' + ', '.join(flags) + ')_' if flags else ''}")
        lines.append("")
    if req["deliverables"]:
        lines += ["## Deliverables", ""]
        for d in req["deliverables"]:
            dl = ", ".join(x["text"] for x in d["deadlines"]) or "no date found"
            promised = ", ".join(x["text"] for x in d.get("commitments") or [])
            lines.append(f"- **{d['id']} {d['type'].replace('_', ' ')}**: requirements {', '.join(d['requirements'])}; "
                         f"client deadline: {dl}" + (f"; you promised: {promised}" if promised else ""))
        lines.append("")
    if req["conflicts"]:
        lines += ["## Conflicts", ""]
        for c in req["conflicts"]:
            lines.append(f"- **{c['id']} {c['topic']}** ({c['status']}): {' vs '.join(c['values'])}; items {', '.join(c['items'])}. {c['note']}")
        lines.append("")
    open_q = [q for q in req["questions"] if q["status"] == "open"]
    if open_q:
        lines += ["## Questions for you", ""]
        lines += [f"- **{q['id']}** {q['text']} _{q['why']}_" for q in open_q]
        lines.append("")
    if req.get("sensitive"):
        lines += ["## Sensitive information found (kinds and places only)", ""]
        lines += [f"- [{x['where']}] {', '.join(x['kinds'])}" for x in req["sensitive"][:40]]
        lines.append("")
    if req.get("context"):
        lines += ["## Background from the documents (not requirements)", ""]
        lines += [f"- {e['quote'][:240]} [{e['where']}]" for e in req["context"][:40]]
        lines.append("")
    if req["ignored_instructions"]:
        lines += ["## Text addressed to an AI (ignored, not a request)", ""]
        lines += [f"- [{e['where']}] {e['quote'][:200]}" for e in req["ignored_instructions"]]
        lines.append("")
    lines += ["## Limits", ""] + [f"- {x}" for x in req["limits"]]
    return "\n".join(lines) + "\n"


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<![\w+])(?:\+?\d[\d -]{8,14}\d)(?!\d)")
_IDNUM = re.compile(r"(?i)\b(?:pan|aadhaar|aadhar|passport|gstin?|ifsc|account (?:no|number))\b")


def sensitive_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Where personal or secret data appears: the KIND and the place only, never the value."""
    from . import safety
    out = []
    for s in segments:
        text = s.get("text", "")
        kinds = []
        if _EMAIL.search(text):
            kinds.append("email address")
        if _PHONE.search(text):
            kinds.append("phone number")
        if _IDNUM.search(text):
            kinds.append("identity or bank number mentioned")
        _, secrets_hit = safety.scrub(text)
        kinds += [f"secret-like text ({h})" for h in secrets_hit]
        if kinds:
            out.append({"segment": s.get("id"), "source": s.get("source"), "where": _ref_text(s), "kinds": kinds})
    return out[:200]


def _annotate(items: list[dict[str, Any]], conflicts: list[dict[str, Any]]) -> None:
    """basis (stated / inferred / model-suggested), confidence with reasons, validation status, sensitive flag."""
    by_conflict: dict[str, dict[str, Any]] = {}
    for c in conflicts:
        for i in c["items"]:
            by_conflict.setdefault(i, c)
    for it in items:
        reasons = []
        basis = "model-suggested (quote verified)" if it.get("suggested_by_model") else \
            "inferred" if (it.get("implied") or it.get("deliverable_inferred")) else "stated"
        conf = "high" if basis == "stated" else "medium" if basis.startswith("model") else "low"
        if it.get("needs_translation_review"):
            conf, reasons = "low", reasons + ["not English: not translated or word-checked"]
        if it.get("from_audio"):
            conf = "low" if conf != "high" else "medium"
            reasons.append("from a speech transcript")
        if basis == "inferred":
            reasons.append("inferred from nearby messages, not said in this sentence")
        c = by_conflict.get(it["id"])
        status = "unconfirmed"
        if c is not None:
            status = "resolved by the owner's answer" if c["status"].startswith("resolved") else f"in conflict ({c['id']})"
            if c["status"] == "open":
                conf = "low"
                reasons.append(f"conflicts with {', '.join(x for x in c['items'] if x != it['id'])}")
        it.update(basis=basis, confidence=conf, confidence_reasons=reasons, validation_status=status,
                  normalized=" ".join(it["text"].split()).rstrip(".")[:300],
                  sensitive=bool(_EMAIL.search(it["text"]) or _PHONE.search(it["text"])))


ModelSuggest = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
