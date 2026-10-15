"""Visual themes for decks and reports, and the rule that picks one for a topic.

Three design directions (one per kind of topic), all with fonts that ship with Windows so measuring and rendering agree.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
FONT_FILES = {  # family -> (regular, bold) file names used to measure text
    "Segoe UI": ("segoeui.ttf", "segoeuib.ttf"),
    "Segoe UI Semibold": ("seguisb.ttf", "seguisb.ttf"),
    "Georgia": ("georgia.ttf", "georgiab.ttf"),
    "Bahnschrift": ("bahnschrift.ttf", "bahnschrift.ttf"),
    "Calibri": ("calibri.ttf", "calibrib.ttf"),
    "Nirmala UI": ("Nirmala.ttf", "NirmalaB.ttf"),
}


@dataclass(frozen=True)
class Theme:
    name: str
    label: str
    bg: str
    surface: str
    text: str
    muted: str
    accent: str
    accent2: str
    heading_font: str
    body_font: str
    dark: bool
    transition: str
    chart: tuple = field(default_factory=tuple)


THEMES = {
    "midnight": Theme("midnight", "Midnight (technology, security, data)", bg="0B1220", surface="15203A", text="E8EEF8",
                      muted="9AABC6", accent="38BDF8", accent2="A78BFA", heading_font="Segoe UI Semibold",
                      body_font="Segoe UI", dark=True, transition="fade",
                      chart=("38BDF8", "A78BFA", "34D399", "F472B6", "FBBF24", "94A3B8")),
    "paper": Theme("paper", "Paper (reports, business, research)", bg="FBFAF6", surface="FFFFFF", text="1F2937",
                   muted="6B7280", accent="B45309", accent2="0F766E", heading_font="Georgia", body_font="Segoe UI",
                   dark=False, transition="fade",
                   chart=("B45309", "0F766E", "1D4ED8", "9D174D", "4D7C0F", "6B7280")),
    "vivid": Theme("vivid", "Vivid (learning, creative, travel)", bg="FFFFFF", surface="F5F3FF", text="111827",
                   muted="6B7280", accent="7C3AED", accent2="F43F5E", heading_font="Bahnschrift", body_font="Segoe UI",
                   dark=False, transition="push",
                   chart=("7C3AED", "F43F5E", "0EA5E9", "F59E0B", "10B981", "64748B")),
}
DEFAULT_THEME = "paper"

_KEYWORDS = {
    "midnight": ("security", "cyber", "hack", "network", "cloud", "data", "software", "code", "coding", "programming",
                 "ai", "machine learning", "llm", "tech", "engineering", "linux", "devops", "blockchain", "robot"),
    "paper": ("report", "business", "finance", "market", "history", "policy", "research", "law", "economics",
              "health", "medical", "strategy", "analysis", "comparison", "audit", "annual"),
    "vivid": ("learn", "learning", "education", "course", "kids", "student", "design", "marketing", "creative",
              "travel", "food", "music", "fun", "workshop", "beginner", "guide", "roadmap", "study"),
}


def choose_theme(text: str, requested: str | None = None) -> Theme:
    """An explicit theme wins; otherwise the theme whose topic words appear most in the title and topic text."""
    if requested and requested in THEMES:
        return THEMES[requested]
    low = (text or "").lower()
    scores = {name: sum(len(re.findall(rf"\b{re.escape(k)}", low)) for k in words) for name, words in _KEYWORDS.items()}
    best = max(scores, key=lambda n: (scores[n], n == DEFAULT_THEME))
    return THEMES[best] if scores[best] > 0 else THEMES[DEFAULT_THEME]


def font_path(family: str, bold: bool = False) -> str | None:
    files = FONT_FILES.get(family)
    if not files:
        return None
    path = os.path.join(FONT_DIR, files[1 if bold else 0])
    return path if os.path.isfile(path) else None
