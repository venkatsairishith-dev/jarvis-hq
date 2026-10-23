"""Add slide transitions and entrance animations to a built deck with OfficeCLI, then validate it against the schema.

python-pptx has no animation API, and hand-written timing XML is fragile; OfficeCLI (Apache-2.0, pinned v1.0.152,
checksum-verified) writes PowerPoint's own timing structures. Animations run automatically one after another
("after previous"), so the slideshow plays without extra clicks. LibreOffice shows simpler versions of some effects.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import tools

EFFECTS = {"fade": {"effect": "fade"}, "wipe": {"effect": "wipe"}, "zoom": {"effect": "zoom"}, "fly": {"effect": "fly"}}


def commands(slide_count: int, transition: str, anims: list[dict], title_slides: set[int]) -> list[dict]:
    # Slides advance on click (advanceClick, the PowerPoint default; no auto-advance timer is written). In one live
    # LibreOffice show the deck was on slide 2 after 15 s, but the PC was in use, so a click cannot be ruled out: unverified.
    cmds = [{"command": "set", "path": f"/slide[{n}]",
             "props": {"transition": "fade" if n in title_slides else transition, "advanceClick": "true"}}
            for n in range(1, slide_count + 1)]
    seen: dict[int, int] = {}
    for a in anims:
        k = seen.get(a["slide"], 0)
        seen[a["slide"]] = k + 1
        parent = f"/slide[{a['slide']}]/{'chart' if a['kind'] == 'chart' else 'shape'}[@id={a['id']}]"
        props = {**EFFECTS.get(a["effect"], EFFECTS["fade"]), "class": "entrance",
                 "trigger": "withPrevious" if k == 0 else "afterPrevious",
                 "duration": "600" if a["kind"] == "chart" else "450", "delay": "250" if k == 0 else "120"}
        cmds.append({"command": "add", "parent": parent, "type": "animation", "props": props})
    return cmds


def apply(deck: Path, slide_count: int, transition: str, anims: list[dict], title_slides: set[int]) -> dict:
    cmds = commands(slide_count, transition, anims, title_slides)
    inp = deck.with_name("animation-commands.json")
    inp.write_text(json.dumps(cmds), encoding="utf-8")
    r = tools.officecli(["batch", str(deck), "--input", str(inp), "--json"], cwd=str(deck.parent))
    release(deck)
    try:
        res = json.loads(r.stdout or "{}")
    except ValueError:
        res = {}
    summary = (res.get("data") or {}).get("summary") or {}
    failed = [x.get("output") or x.get("error") for x in (res.get("data") or {}).get("results", []) if not x.get("success")]
    return {"exit": r.returncode, "requested": len(cmds), "succeeded": summary.get("succeeded", 0),
            "failed": failed[:10], "transitions": slide_count, "animations": len(anims)}


def release(deck: Path) -> None:
    """OfficeCLI can leave a background "resident" process holding the file open (seen: it blocked deleting the deck).
    Closing flushes and ends it; harmless when none is running."""
    try:
        tools.officecli(["close", str(deck)], cwd=str(deck.parent), timeout=60)
    except tools.ToolError:
        pass


def validate(deck: Path) -> dict:
    r = tools.officecli(["validate", str(deck), "--json"], cwd=str(deck.parent))
    release(deck)
    try:
        res = json.loads(r.stdout or "{}")
    except ValueError:
        res = {}
    msgs = [w.get("message", "") for w in res.get("warnings", []) if "[Schema]" in w.get("message", "")]
    return {"valid": r.returncode == 0 and bool(res.get("success")), "exit": r.returncode, "errors": msgs[:10]}
