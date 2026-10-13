"""Where the laptop's JARVIS workspace folder is (the one that holds `cache\` and `outputs\`).

It used to be "three levels above this package" (`<workspace>\\hq\\node\\jarvis_node`). Since the repository root became
N.E.K.O and JARVIS moved to `<repo>\\jarvis`, that count is one too short, and a silent mistake here would move the folder
delivered files are written to. This resolves the workspace the same way in both layouts, and `JARVIS_HOME` overrides it.
"""

from __future__ import annotations

import os


def home_for(subsystem_root: str, env_home: str | None = None) -> str:
    """The workspace folder for a JARVIS subsystem folder (the folder that contains `node`, `kernel`, `config`)."""
    if env_home:
        return os.path.realpath(env_home)
    root = os.path.realpath(subsystem_root)
    parent = os.path.dirname(root)
    # Inside the N.E.K.O-based repository the subsystem is `<repo>\jarvis` next to N.E.K.O's own `plugin\` folder.
    if os.path.basename(root).lower() == "jarvis" and os.path.isdir(os.path.join(parent, "plugin")):
        root = parent
    return os.path.dirname(root)


def jarvis_home() -> str:
    subsystem_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    return home_for(subsystem_root, os.environ.get("JARVIS_HOME", "").strip() or None)
