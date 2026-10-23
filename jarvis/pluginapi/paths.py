"""Where JARVIS keeps its data. `JARVIS_DATA_DIR` wins. The old name `JARVIS_DATA_DIR` is still read so a laptop that
has not been migrated keeps finding its files (jarvis/scripts/migrate_data_dir.py moves them); it will be removed after that."""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    for name in ("JARVIS_DATA_DIR", "JARVIS_DATA_DIR"):
        value = os.environ.get(name, "").strip()
        if value:
            return Path(value)
    return Path(r"D:\jarvis\data") if os.name == "nt" else Path.home() / ".jarvis" / "data"
