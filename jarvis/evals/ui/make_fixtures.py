"""Fixtures for the face render harness (jarvis/evals/ui/render.mjs).

Writes one JSON file with everything the harness draws:
  chat      chat histories (English, Telugu, Hindi, a long tool result, a tool error, an error message, long paths and words)
  ask       the forms the REAL backend builds (jarvis.bridge.ask, ui_patterns, cleaner_ui) on a fake PC: cleanup with sizes, confirm, ...
  timeline  the Timeline views the real flight recorder builds (small, long, many = 59 steps, empty, unavailable)
  preview   the Preview panel's tool results for a pptx deck, a pdf, a png, markdown, code and hostile html
  patterns  the ids in config/ui_patterns.json, so the harness can tell which pattern a screenshot covers

    python make_fixtures.py OUT.json

The three panel generators live next to the face's own checks (jarvis/face/apps/stage-tamagotchi/scripts/*-harness/make_fixtures.py)
and are run as they are, so this harness and those checks always look at the same data. No network, nothing is written outside OUT.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                     # .../jarvis (the package root)
SCRIPTS = ROOT / "face" / "apps" / "stage-tamagotchi" / "scripts"
NOW_MS = 1761550000000
BEACON = "http://127.0.0.1:1"             # nothing listens there; the preview fixtures plant it in hostile content


def user(i: int, t: int, text: str) -> dict:
    return {"id": f"u{i}", "role": "user", "content": text, "createdAt": NOW_MS + t * 1000}


def assistant(i: int, t: int, text: str, slices: list | None = None) -> dict:
    return {"id": f"a{i}", "role": "assistant", "content": text, "slices": slices or [{"type": "text", "text": text}],
            "tool_results": [], "createdAt": NOW_MS + t * 1000, "completedAt": NOW_MS + t * 1000 + 3000}


def call(cid: str, name: str, args: dict) -> dict:
    return {"type": "tool-call", "toolCall": {"toolName": name, "args": json.dumps(args), "toolCallId": cid, "toolCallType": "function"}}


def chat() -> dict:
    long_path = ("D:\\jarvis\\data\\deliverables\\20261027-101500-quarterly-report-final-copy-of-the-board-deck-with-speaker-notes-and-appendix\\"
                 "quarterly-report-final-copy-of-the-board-deck-with-speaker-notes-and-appendix.pptx")
    long_result = "\n".join(
        f"{i:02d}  C:\\Users\\sathish\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\Cache\\Cache_Data\\f_{i:06x}   {1000 + i * 37} KB"
        for i in range(1, 41))
    mixed = [
        user(1, 0, "Hey JARVIS, good morning. What is on my plate today?"),
        assistant(1, 5, "Good morning. You have three things today: the board deck at 11:00, lunch with Ravi at 13:30, and the flight booking you wanted to finish."),
        user(2, 60, "ఈ రోజు నా షెడ్యూల్ తెలుగులో చెప్పు, మరియు ఫ్లైట్ టికెట్ ధరలు కూడా చూడు."),
        assistant(2, 65, "ఈ రోజు మీ షెడ్యూల్: ఉదయం 11 గంటలకు బోర్డ్ డెక్ సమీక్ష, మధ్యాహ్నం 1:30కి రవితో భోజనం. హైదరాబాద్ నుండి ఢిల్లీకి విమాన టికెట్ ధరలు ₹4,850 నుండి మొదలవుతున్నాయి. సాయంత్రం వరకు అత్యంత చౌకైన ఎంపిక ఇండిగో 6E-531."),
        user(3, 120, "अब यही हिंदी में बताओ, और मीटिंग के लिए एक छोटा सारांश बनाओ।"),
        assistant(3, 125, "आज आपकी बैठक सुबह 11 बजे है और दोपहर 1:30 बजे रवि के साथ लंच। हैदराबाद से दिल्ली की उड़ान ₹4,850 से शुरू होती है। मैंने बैठक के लिए तीन बिंदुओं का सारांश तैयार किया है: बजट, समय-सीमा और अगले कदम।"),
        user(4, 180, "Open C:\\Users\\sathish\\Documents\\" + "very-long-folder-name-without-any-spaces-that-keeps-going-" * 2 + "notes.txt"),
        assistant(4, 185, "I opened it. The file is " + long_path + " and a very long Telugu word: అంతర్జాతీయవిమానాశ్రయములలోనిప్రయాణికులసౌకర్యములు, and a Hindi one: अंतरराष्ट्रीयहवाईअड्डोंपरयात्रियोंकीसुविधाएं."),
    ]
    tools = [
        user(1, 0, "Clean up my PC, and then show me the biggest cache folders."),
        assistant(1, 4, "", [
            {"type": "text", "text": "I will scan first. Nothing is deleted until you tick it and press Clean."},
            call("t1", "jarvis_cleaner", {"action": "scan"}),
            {"type": "tool-call-result", "id": "t1", "result": "Scanned. 6 safe rows (3.1 GB) and 4 review rows (5.9 GB). The panel is open."},
            call("t2", "desktop__list_folder", {"path": "AppData/Local/Google/Chrome/Cache"}),
            {"type": "tool-call-result", "id": "t2", "result": long_result},
            {"type": "text", "text": "The browser caches are the biggest safe row. Tick what you want in the panel, then say clean up."},
        ]),
        user(2, 70, "Now save the report to D:\\jarvis\\data\\deliverables"),
        assistant(2, 74, "", [
            call("t3", "deliverables__make_report", {"path": long_path}),
            {"type": "tool-call-result", "id": "t3", "isError": True, "result": "Error: PermissionError: [WinError 5] Access is denied: '" + long_path + "'"},
            {"type": "text", "text": "I could not save it there. Pick another folder and I will try again."},
        ]),
        {"id": "e1", "role": "error", "createdAt": NOW_MS + 90000,
         "content": "Could not reach the privacy gateway (127.0.0.1:48950): connection refused. Check that JARVIS is running, then try again.\n"
                    + json.dumps({"code": "ECONNREFUSED", "address": "127.0.0.1", "port": 48950, "syscall": "connect"})},
    ]
    return {"mixed": mixed, "tools": tools, "waiting": [user(1, 0, "Summarise the board deck for me, please.")], "empty": []}


def run(script: Path, *args: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.json"
        subprocess.run([sys.executable, str(script), str(out), *args], check=True, stdout=subprocess.DEVNULL)
        return json.loads(out.read_text(encoding="utf-8"))


def main() -> int:
    out = Path(sys.argv[1])
    patterns = json.loads((ROOT / "config" / "ui_patterns.json").read_text(encoding="utf-8"))
    ids = list(patterns.get("patterns", {}))
    data = {
        "chat": chat(),
        "ask": run(SCRIPTS / "ask-harness" / "make_fixtures.py"),
        "timeline": run(SCRIPTS / "timeline-harness" / "make_fixtures.py"),
        "preview": run(SCRIPTS / "preview-harness" / "make_fixtures.py", BEACON),
        "patterns": ids,
    }
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
