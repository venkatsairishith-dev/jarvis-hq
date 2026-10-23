"""Write the JARVIS card into the face (jarvis/face) as its built-in character prompt. Any OS.

    python jarvis/persona/face_prompt.py           writes the prompt into the face's English base.yaml
    python jarvis/persona/face_prompt.py --check   exits 1 when the face's prompt differs from the card

The card (jarvis_card.json) stays the one source. The face's built-in card reads its description from the i18n key
`base.prompt.prefix`; that block is replaced with the card's system prompt, with the face's placeholders filled in
({ASSISTANT_NAME} -> JARVIS, {OWNER_NAME} -> the owner) and vue-i18n's special characters escaped. The rest of base.yaml
(the avatar's expression tokens) is kept as it is.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CARD = HERE / "jarvis_card.json"
BASE_YAML = HERE.parent / "face" / "packages" / "i18n" / "src" / "locales" / "en" / "base.yaml"
PREFIX_KEY = "  prefix: |\n"
I18N_SPECIAL = "{}@$|"                                  # vue-i18n message syntax; each is written as {'x'}


DANGER_LINE = "without considering its potential dangers"      # a preset some chat frontends add: a card must never carry it


class CardError(RuntimeError):
    pass


def load_card(path: Path = CARD) -> dict:
    """The card, checked: it needs a name, a profile and a prompt with the two name placeholders, and no preset that tells the
    model to ignore dangers."""
    card = json.loads(Path(path).read_text(encoding="utf-8"))
    prompt = card.get("system_prompt", "")
    if not card.get("name") or not isinstance(card.get("profile"), dict) or not prompt:
        raise CardError("card needs name, profile and system_prompt")
    if DANGER_LINE in prompt or "<JARVIS_PERSONA_SELECTION>" in prompt:
        raise CardError("card prompt contains a preset text")
    for placeholder in ("{ASSISTANT_NAME}", "{OWNER_NAME}"):
        if placeholder not in prompt:
            raise CardError(f"card prompt lacks {placeholder}")
    return card


def render(card: dict) -> str:
    """The card's prompt as plain text for the face."""
    text = card["system_prompt"].replace("{ASSISTANT_NAME}", "JARVIS")
    text = text.replace("{OWNER_NAME}'s", "the owner's").replace("{OWNER_NAME}", "the owner")
    text = re.sub(r"(^|[.!?] |- )the owner", lambda m: m.group(1) + "The owner", text, flags=re.M)
    leftover = re.findall(r"\{[A-Z_]+\}", text)
    if leftover:
        raise ValueError(f"unfilled placeholders in the card: {sorted(set(leftover))}")
    return text


def escape_i18n(text: str) -> str:
    return "".join(f"{{'{ch}'}}" if ch in I18N_SPECIAL else ch for ch in text)


def yaml_block(text: str) -> str:
    lines = escape_i18n(text).rstrip("\n").split("\n")
    return PREFIX_KEY + "".join(("    " + line if line else "") + "\n" for line in lines) + "\n"


def replace_prefix(yaml_text: str, block: str) -> str:
    start = yaml_text.index(PREFIX_KEY)
    m = re.compile(r"^  \S", re.M).search(yaml_text, start + len(PREFIX_KEY))
    end = m.start() if m else len(yaml_text)
    return yaml_text[:start] + block + yaml_text[end:]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    card = json.loads(CARD.read_text(encoding="utf-8"))
    current = BASE_YAML.read_text(encoding="utf-8")
    wanted = replace_prefix(current, yaml_block(render(card)))
    if args.check:
        if wanted != current:
            print("the face's built-in prompt differs from jarvis_card.json: run jarvis/persona/face_prompt.py")
            return 1
        print("the face's built-in prompt matches the card")
        return 0
    if wanted != current:
        BASE_YAML.write_text(wanted, encoding="utf-8", newline="\n")
        print(f"wrote the JARVIS prompt into {BASE_YAML}")
    else:
        print("already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
