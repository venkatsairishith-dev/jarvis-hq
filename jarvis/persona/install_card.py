"""Install or update the JARVIS character card in N.E.K.O (plan item 14). Windows or Linux; N.E.K.O must be stopped (neko-stop.ps1).

    python jarvis/persona/install_card.py                     dry run: says what would change
    python jarvis/persona/install_card.py --apply             backs characters.json up to <data>/backups/jarvis-card-<stamp>/, then writes
    python jarvis/persona/install_card.py --apply --activate  also makes JARVIS the current character

Why offline: N.E.K.O's character API refuses reserved fields, and the custom character prompt is one (`_reserved.system_prompt`).
The card is written with N.E.K.O's own helpers (stable character id and uid, reserved-field layout), the same way its card import
does, and N.E.K.O picks it up at the next start. An existing JARVIS card keeps its ids, voice and avatar; its profile and prompt
are replaced, and any persona preset on it is removed (presets put the "act without considering dangers" text back).
Other characters are never changed.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.character_fields import CHARACTER_RESERVED_FIELDS  # noqa: E402  (N.E.K.O's own field list and helpers)
from utils.config_manager.reserved_schema import (  # noqa: E402
    assign_new_character_uid, delete_reserved, ensure_catgirl_character_id, get_reserved, set_reserved)

CARD = Path(__file__).resolve().parent / "jarvis_card.json"
DEFAULT_DATA = os.environ.get("NEKO_STORAGE_SELECTED_ROOT", r"D:\jarvis\N.E.K.O-data" if os.name == "nt" else str(Path.home() / "N.E.K.O-data"))
NEKO_PORT = 48911
CATGIRLS, CURRENT = "猫娘", "当前猫娘"                   # N.E.K.O's internal keys
DANGER_LINE = "without considering its potential dangers"


class InstallError(RuntimeError):
    pass


def load_card(path: Path = CARD) -> dict:
    card = json.loads(path.read_text(encoding="utf-8"))
    prompt = card.get("system_prompt", "")
    if not card.get("name") or not isinstance(card.get("profile"), dict) or not prompt:
        raise InstallError("card needs name, profile and system_prompt")
    if DANGER_LINE in prompt or "<NEKO_PERSONA_SELECTION>" in prompt:
        raise InstallError("card prompt contains N.E.K.O preset text")
    for placeholder in ("{LANLAN_NAME}", "{MASTER_NAME}"):
        if placeholder not in prompt:
            raise InstallError(f"card prompt lacks {placeholder}")
    return card


def neko_running(port: int = NEKO_PORT) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def apply_card(characters: dict, card: dict, activate: bool = False) -> tuple[dict, list[str]]:
    """Return (new characters dict, list of changes). Pure: the input is not modified."""
    out = copy.deepcopy(characters)
    catgirls = out.setdefault(CATGIRLS, {})
    if not isinstance(catgirls, dict):
        raise InstallError("characters.json has no character map")
    name = card["name"]
    changes = []
    existing = catgirls.get(name)
    if existing is None:
        entry: dict = {}
        used = {get_reserved(c, "character_id", default="") for c in catgirls.values() if isinstance(c, dict)}
        ensure_catgirl_character_id(entry, used_ids=used)
        assign_new_character_uid(entry)
        changes.append(f"create character {name}")
    else:
        entry = existing
        keep = {"_reserved", *CHARACTER_RESERVED_FIELDS}                 # ids, voice, avatar (also legacy flat copies) stay
        kept = {k: v for k, v in entry.items() if k in keep}
        old_fields = sorted(k for k in entry if k not in keep)
        entry.clear()
        entry.update(kept)
        changes.append(f"update character {name} (profile fields replaced: {', '.join(old_fields) or 'none'})")
        if get_reserved(entry, "persona_override", default=None) is not None:
            delete_reserved(entry, "persona_override")
            changes.append("remove its persona preset")
    for k, v in card["profile"].items():
        entry[k] = v
    if get_reserved(entry, "system_prompt", default=None) != card["system_prompt"]:
        set_reserved(entry, "system_prompt", card["system_prompt"])
        changes.append("set its character prompt (replaces N.E.K.O's default prompt)")
    catgirls[name] = entry
    if activate and out.get(CURRENT) != name:
        changes.append(f"make {name} the current character (was {out.get(CURRENT) or 'none'})")
        out[CURRENT] = name
    return out, changes


def backup(data: Path, files: list[Path]) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = data / "backups" / f"jarvis-card-{stamp}"
    n = 1
    while bak.exists():                                       # never reuse (and so never overwrite) an earlier backup
        bak = data / "backups" / f"jarvis-card-{stamp}-{n}"
        n += 1
    bak.mkdir(parents=True)
    for f in files:
        shutil.copy2(f, bak / f.name)
    return bak


def write_atomic(path: Path, obj: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=DEFAULT_DATA, help="N.E.K.O data folder (NEKO_STORAGE_SELECTED_ROOT)")
    ap.add_argument("--card", default=str(CARD))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--activate", action="store_true", help="also make JARVIS the current character")
    args = ap.parse_args(argv)
    data = Path(args.data)
    path = data / "config" / "characters.json"
    try:
        card = load_card(Path(args.card))
        characters = json.loads(path.read_text(encoding="utf-8"))
        new, changes = apply_card(characters, card, args.activate)
    except (InstallError, OSError, ValueError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    for c in changes:
        print(("" if args.apply else "would ") + c)
    if not args.apply:
        print("dry run: nothing written (add --apply)")
        return 0
    if neko_running():
        print("refused: N.E.K.O is running; stop it first (neko-stop.ps1), then run this again", file=sys.stderr)
        return 2
    bak = backup(data, [path])
    write_atomic(path, new)
    print(f"backup: {bak}")
    print(f"wrote {path}; start N.E.K.O again (neko-start.ps1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
