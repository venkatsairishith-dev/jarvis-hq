"""Safe mode (D71): ONE switch that makes JARVIS fall back to its shipped defaults. It can only make JARVIS more careful, never less.

`active()` is True when the environment variable JARVIS_SAFE_MODE is 1/true/yes/on, or when the flag file `safe_mode.json` exists in the data folder
(D:\\jarvis\\data on the laptop, JARVIS_DATA_DIR wins). The file is made and removed by the owner at a console (jarvis/tools/safe_mode_ctl.py), never by a
model, a plugin or the face: this module only READS, and nothing under plugins/, bridge/ or the face imports the tool that writes. The file's name is in the
kernel's SAFETY_COMPONENTS and the push gate's PROTECTED list, so an agent that tries to write or delete it is held for the owner.

What the places that ask `active()` do when it is True (each one only turns something OFF or ignores something LEARNED; none turns anything on):
  lessons (plugins/desktop/lessons.py)      learned routes are ignored (not deleted) and nothing new is learned
  site playbooks (browser_playbooks.py)     only the shipped playbooks are read; no repair is saved
  auto-click rules (kernel policy, node)    the owner's list of buttons JARVIS clicks itself is not used: those clicks wait for the owner
  per-site browser grants                   not used: a site needs its card and PIN again (plugin client, kernel lookup)
  UI Automation worker (node/uia.py)        off: the PowerShell scripts serve, as before D68
  speed shortcuts (desktop/quick.py)        off: a request goes the long way, through the model
  local-model preference (privacy gateway)  off: the provider chain keeps its configured order
All safety gates (the kernel's policy, effect classes, the PIN, the sensitive-site and secret rules, the privacy gateway's redaction) stay exactly as they are.

This file exists in four places that are deployed apart and import nothing from each other: jarvis/safe_mode.py, jarvis/node/jarvis_node/safe_mode.py,
jarvis/kernel/jarvis_kernel/safe_mode.py and jarvis/privacy/safe_mode.py. They are byte for byte the same; jarvis/tools/test_safe_mode.py fails if they differ.
Standard library only.
"""
from __future__ import annotations

import os
from pathlib import Path

FLAG_NAME = "safe_mode.json"
_ON = ("1", "true", "yes", "on")


def data_dir() -> Path:
    env = os.environ.get("JARVIS_DATA_DIR", "").strip()
    if env:
        return Path(env)
    return Path(r"D:\jarvis\data") if os.name == "nt" else Path.home() / ".jarvis" / "data"


def flag_path() -> Path:
    env = os.environ.get("JARVIS_SAFE_MODE_FILE", "").strip()
    return Path(env) if env else data_dir() / FLAG_NAME


def active() -> bool:
    """True when safe mode is on. Cheap (one stat), safe to call on every decision. An unreadable folder counts as off, an existing file as on."""
    if os.environ.get("JARVIS_SAFE_MODE", "").strip().lower() in _ON:
        return True
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("JARVIS_SAFE_MODE_FILE", "").strip():
        return False                       # a test run never reads the owner's real flag (a test that wants the file names its own)
    try:
        return flag_path().exists()
    except OSError:
        return False
