"""What is worth saying aloud. The written answer stays complete on screen; the spoken one is short and never reads code,
URLs, tables or long identifiers out loud."""
import re

MAX_SPOKEN = 600


def speakable(text: str, limit: int = MAX_SPOKEN) -> str:
    t = str(text or "")
    t = re.sub(r"```.*?```", " (code omitted) ", t, flags=re.S)         # fenced code
    t = re.sub(r"`([^`]{1,60})`", r"\1", t)                              # short inline code is fine, read as words
    t = re.sub(r"`[^`]+`", " ", t)
    t = re.sub(r"https?://\S+", " a link ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)                      # markdown link -> its label
    t = re.sub(r"^\s*\|.*\|\s*$", " ", t, flags=re.M)                    # table rows
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"[*_~>#]+", " ", t)
    t = re.sub(r"^\s*[-+]\s+", "", t, flags=re.M)
    t = re.sub(r"\b[0-9a-f]{12,}\b", "an identifier", t)                  # task ids, hashes
    t = re.sub(r"[A-Za-z]:\\[^\s]+", "a file path", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[:end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0]) + " The rest is on screen."


def sentences(text: str):
    """Split for streaming synthesis: the first sentence plays while the next is generated."""
    parts = re.split(r"(?<=[.!?।])\s+", text.strip())
    return [p for p in parts if p]
