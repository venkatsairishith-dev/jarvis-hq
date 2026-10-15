"""Measure how well the redactor hides secrets that have no cue word, and how often it flags ordinary messages.

    D:\\jarvis\\tools\\privacy-venv\\Scripts\\python.exe jarvis\\privacy\\eval_pii.py

Windows. Local only: needs the GLiNER-PII model files and the Jev-style decision model (OpenJev on 127.0.0.1:8091); no
public model is called. These messages were never used to choose the settings (all values are made up).
Result 2026-10-16: patterns only 0/12 hidden, 0/12 flagged; + GLiNER 12/12, 12/12; + GLiNER + Jev 12/12, 3/12.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from redactor import Mapping, Redactor, load_pii_model  # noqa: E402

PRIVATE = [
    ("my hotstar password is the same as gmail, Kiran@1990", "Kiran@1990"),
    ("Note it down: safe code 4-8-15-16", "4-8-15-16"),
    ("electricity consumer number 110234567890 for the bill payment", "110234567890"),
    ("the vpn login is k.rao and the passcode Blue7Whale#", "Blue7Whale#"),
    ("my voter id is XYZ1234567", "XYZ1234567"),
    ("the zoom passcode for tomorrow is 739201", "739201"),
    ("use token sk_live_51HxYzAbCdEf12345678 for stripe", "sk_live_51HxYzAbCdEf12345678"),  # gitleaks:allow (made up)
    ("Grandma's pension account is 62034511987 in SBI Ameerpet.", "62034511987"),
    ("ATM pin 9921, card in the drawer", "9921"),
    ("ssh key passphrase: correct horse battery staple", "correct horse battery staple"),
    ("the alarm disarm code is 1357", "1357"),
    ("my LIC policy number is 512345678", "512345678"),
]
CLEAN = [
    "The train leaves at 6:45 from platform 3.",
    "Buy 3 litres of milk and 500 g of paneer.",
    "Rohit scored 87 runs in the match yesterday.",
    "What is the square root of 144?",
    "I need to renew my passport next year, what documents do I need?",
    "Remind me that the dentist appointment is on the 21st.",
    "How do I reset a forgotten Gmail password?",
    "Book a table for 4 at 8 pm at Paradise.",
    "The room is 12 by 14 feet, how many tiles do I need?",
    "My code throws an IndexError on line 42.",
    "Convert 250 dollars to rupees.",
    "Tell me a joke about cats.",
]


def mostly_hidden(out: str, secret: str) -> bool:
    """At least 80% of the secret's characters are gone (a model span can miss an edge character)."""
    if secret in out:
        return False
    return not any(len(part) >= 0.2 * len(secret) and part in out for part in secret.split() if len(secret.split()) > 1)


def run(label, red):
    hid = [mostly_hidden(red.redact(t, Mapping()), s) for t, s in PRIVATE]
    flagged = [c for c in CLEAN if red.redact(c, Mapping()) != c]
    print(f"{label:26s} hidden {sum(hid)}/{len(PRIVATE)}  ordinary flagged {len(flagged)}/{len(CLEAN)}")
    for (t, s), h in zip(PRIVATE, hid):
        if not h:
            print(f"    missed: {t}")
    for c in flagged:
        print(f"    flagged: {c}")
    return sum(hid), len(flagged)


def main():
    model = load_pii_model()
    if model is None:
        print("GLiNER-PII model not installed; nothing to measure")
        return 2
    run("patterns only", Redactor(names=False, model=None))
    run("patterns + GLiNER", Redactor(names=False, model=model, verifier=None))
    full = Redactor(names=False, model=model)
    h, f = run("patterns + GLiNER + Jev", full)
    print("decision checks:", full.stats)
    return 0 if h == len(PRIVATE) and f <= 3 and not full.stats["jev_unreachable"] else 1


if __name__ == "__main__":
    sys.exit(main())
