r"""Where the laptop's JARVIS workspace folder is (the one that holds the cache and outputs folders).

It used to be "three levels above this package" (<workspace>\hq\node\jarvis_node). Since JARVIS lives in <repo>\jarvis (the
repository root holds the .git folder), that count is one too short, and a silent mistake here would move the folder
delivered files are written to. This resolves the workspace the same way in both layouts, and JARVIS_HOME overrides it.
"""

from __future__ import annotations

import os


def home_for(subsystem_root: str, env_home: str | None = None) -> str:
    """The workspace folder for a JARVIS subsystem folder (the folder that contains `node`, `kernel`, `config`)."""
    if env_home:
        return os.path.realpath(env_home)
    root = os.path.realpath(subsystem_root)
    parent = os.path.dirname(root)
    # Inside the repository the subsystem is `<repo>\jarvis`, and the repository root holds `.git`.
    if os.path.basename(root).lower() == "jarvis" and os.path.exists(os.path.join(parent, ".git")):
        root = parent
    return os.path.dirname(root)


def jarvis_home() -> str:
    subsystem_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    return home_for(subsystem_root, os.environ.get("JARVIS_HOME", "").strip() or None)


def local_result_dir() -> str:
    """outputs\\local-only: results that must stay on this laptop (screen text, page text). Read once by the JARVIS plugin."""
    return os.path.join(os.path.realpath(os.environ.get("JARVIS_OUTPUTS") or os.path.join(jarvis_home(), "outputs")), "local-only")


def sweep_local(max_age_s: float = 600) -> int:
    """Nothing in local-only outlives ten minutes, even if a reader never came (a killed job, a test): screen and page
    text is not kept. Called on every write there. Returns how many entries were removed."""
    import shutil
    import time as _t
    d, n = local_result_dir(), 0
    try:
        entries = list(os.scandir(d))
    except OSError:
        return 0
    for e in entries:
        try:
            if _t.time() - e.stat().st_mtime > max_age_s:
                shutil.rmtree(e.path) if e.is_dir() else os.remove(e.path)
                n += 1
        except OSError:
            pass
    return n
