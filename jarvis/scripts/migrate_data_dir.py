"""Move JARVIS's data from the folders of the N.E.K.O era to the new layout (decision D33). Windows or Linux; standard library only.

    python migrate_data_dir.py                              dry run: says what would be copied
    python migrate_data_dir.py --apply                      copies it
    python migrate_data_dir.py --old D:\\jarvis\\N.E.K.O-data --new D:\\jarvis\\data --outputs D:\\jarvis\\outputs

What moves (nothing is deleted from the old folders, and a file that already exists in the new place is never overwritten):

    <old>\\neko                  -> <new>\\plugins          plugin state: PC task records, missions, monitor, decider skills
    <old>\\muse                  -> <new>\\muse             watches, inbox, ideas, tasks
    <old>\\main_agent_runs       -> <new>\\main_agent_runs  the main agent's run files
    <old>\\main_agent_learning   -> <new>\\main_agent_learning
    <old>\\repair-backups        -> <new>\\repair-backups
    <old>\\logs                  -> <new>\\logs
    <outputs>\\neko\\deliverables -> <outputs>\\deliverables
    <outputs>\\neko\\research     -> <outputs>\\research
    <outputs>\\neko\\<other>      -> <outputs>\\documents    (documents written by doc_tools and anything else in that folder)

Left where they are, because only N.E.K.O used them: <old>\\config, <old>\\memory, <old>\\characters.json, its logs of its own servers.
Stop the services first (jarvis-stop.ps1). Afterwards set nothing: the new defaults are JARVIS_DATA_DIR = D:\\jarvis\\data.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

DATA_MOVES = (("neko", "plugins"), ("muse", "muse"), ("main_agent_runs", "main_agent_runs"), ("main_agent_learning", "main_agent_learning"),
              ("repair-backups", "repair-backups"), ("logs", "logs"))
OUTPUT_FOLDERS = (("deliverables", "deliverables"), ("research", "research"))
LEFT = ("config", "memory", "characters.json")


def plan(old: Path, new: Path, outputs: Path) -> list[tuple[Path, Path]]:
    """(source, destination) pairs for every file that would be copied."""
    pairs: list[tuple[Path, Path]] = []

    def walk(src: Path, dst: Path, skip: tuple[str, ...] = ()):
        if not src.exists():
            return
        for f in sorted(p for p in src.rglob("*") if p.is_file()):
            rel = f.relative_to(src)
            if rel.parts and rel.parts[0] in skip:
                continue
            pairs.append((f, dst / rel))
    for src_name, dst_name in DATA_MOVES:
        walk(old / src_name, new / dst_name)
    legacy_out = outputs / "neko"
    for src_name, dst_name in OUTPUT_FOLDERS:
        walk(legacy_out / src_name, outputs / dst_name)
    walk(legacy_out, outputs / "documents", skip=tuple(n for n, _ in OUTPUT_FOLDERS))
    return pairs


def apply(pairs: list[tuple[Path, Path]]) -> tuple[int, int]:
    """Copy each file unless the destination exists. Returns (copied, skipped because it already existed)."""
    copied = skipped = 0
    for src, dst in pairs:
        if dst.exists():
            skipped += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    return copied, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--old", default=r"D:\jarvis\N.E.K.O-data")
    ap.add_argument("--new", default=r"D:\jarvis\data")
    ap.add_argument("--outputs", default=r"D:\jarvis\outputs")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    old, new, outputs = Path(a.old), Path(a.new), Path(a.outputs)
    if not old.exists() and not (outputs / "neko").exists():
        print("nothing to migrate: neither the old data folder nor outputs\\neko exists")
        return 0
    if old.resolve() == new.resolve():
        print("the old and the new folder are the same")
        return 2
    pairs = plan(old, new, outputs)
    left = [n for n in LEFT if (old / n).exists()]
    print(f"{len(pairs)} file(s) to copy from {old} and {outputs / 'neko'}")
    if left:
        print("left in place (only N.E.K.O used them): " + ", ".join(left))
    if not a.apply:
        for src, dst in pairs[:20]:
            print(f"  {src} -> {dst}")
        if len(pairs) > 20:
            print(f"  ... and {len(pairs) - 20} more")
        print("dry run: nothing copied. Run again with --apply.")
        return 0
    copied, skipped = apply(pairs)
    print(f"copied {copied}, left {skipped} that already existed. The old folders were not touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
