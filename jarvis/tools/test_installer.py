"""Static checks on the one-click installer (Install-JARVIS.ps1) that need no Windows: the pinned downloads it names exist in the lock, it
never reads a secret file, the admin code it installs is exactly the three files that exist, and its braces balance."""
from __future__ import annotations

import json
import re
from pathlib import Path

JARVIS = Path(__file__).resolve().parents[1]
SCRIPT = (JARVIS / "scripts" / "Install-JARVIS.ps1").read_text(encoding="utf-8")


def test_models_it_fetches_are_in_the_lock():
    names = re.search(r"\$want = (.*?)\n\s*\$lock", SCRIPT, re.S).group(1)
    wanted = re.findall(r'"([a-z0-9._-]+)"', names)
    lock = {e["name"]: e for e in json.loads((JARVIS / "tools" / "jarvis.lock.json").read_text(encoding="utf-8"))["entries"]}
    assert len(wanted) >= 6
    for n in wanted:
        assert n in lock, n
        assert lock[n].get("sha256"), f"{n} is unpinned"


def test_the_installer_never_reads_a_secret_file():
    # Existence and size only (Get-Item); no Get-Content / ReadAllText / type on a key or token path.
    for line in SCRIPT.splitlines():
        if re.search(r"(?i)\.key|\.token|secrets", line):
            assert not re.search(r"(?i)Get-Content|ReadAll|\btype\b|Import-Clixml|\[IO\.File\]", line), line
    assert "Get-Item \"$Secrets\\providers\\dahl.key\"" in SCRIPT


def test_admin_code_copied_is_exactly_what_exists():
    files = re.search(r'foreach \(\$f in ([^)]*?)\)', SCRIPT).group(1)
    for f in re.findall(r'"([^"]+\.py)"', files):
        assert (JARVIS / "node" / "jarvis_node" / f).exists(), f
    assert set(re.findall(r'"([^"]+\.py)"', files)) == {"__init__.py", "adminctl.py", "adminsvc.py"}


def test_the_admin_service_runs_isolated_from_a_machine_wide_python():
    assert '"-I"' in SCRIPT and "--scope\", \"machine\"" in SCRIPT and "RunLevel Highest" in SCRIPT
    assert "S-1-5-32-544" in SCRIPT and "inheritance:r" in SCRIPT
    # nothing the elevated task runs lives under D: (a folder ordinary programs can change)
    assert not re.search(r"-Execute \$py -Argument .*D:\\", SCRIPT)


def test_braces_and_parentheses_balance():
    body = re.sub(r"<#.*?#>", "", SCRIPT, flags=re.S)
    body = re.sub(r'"(?:`.|[^"`])*"|\'[^\']*\'', '""', body)          # strings
    body = re.sub(r"(?m)#.*$", "", body)
    assert body.count("{") == body.count("}") and body.count("(") == body.count(")") and body.count("[") == body.count("]")


def test_the_cmd_wrapper_points_at_the_script():
    cmd = (JARVIS.parent / "Install-JARVIS.cmd").read_text(encoding="utf-8")
    assert r"jarvis\scripts\Install-JARVIS.ps1" in cmd and "-ExecutionPolicy Bypass" in cmd
