"""Compatibility check against the real Harness² (google-research/harness2), pinned, offline, outside this process.

Harness² is not imported into JARVIS. Its model-backed executors (OpenCode, Codex) run with unrestricted network and the
Codex sandbox off (its README, "Web access"), and its benchmarks need Vertex AI credentials, Docker and Node: none of that
belongs on the owner's PC. What JARVIS uses is its method (``ppc.py``). This module only runs Harness²'s own deterministic
local demo (``harness2-demo``: real Propose-Probe-Compose recursion with scripted editors, no model, no credentials) from
a checkout the owner provides, to show that the method JARVIS adapted behaves as upstream does on upstream's own example.

    HARNESS2_SRC=<checkout of PIN["commit"]>   python -m plugin.plugins.workbench.harness2_compat

Before anything runs, the checkout's key files must match the pinned SHA-256 hashes (a changed checkout is refused).
The demo runs as ``python -I`` in a temporary folder with an empty environment and a timeout.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PIN = {
    "repo": "https://github.com/google-research/harness2",
    "commit": "1cfe254e8d25b15f37048c3730134686abe60335",
    "license": "Apache-2.0",
    "files": {
        "harness2/demo.py": "8f338b3254922a8a48d66162f7796dde435d3ef65a7ccd441ef604c81a1d39d4",
        "harness2/cli/demo.py": "f8ba578ff547d1f78666ee222d0ace3fea34d468405c9766d103b856c6272d25",
        "harness2/core/schedule.py": "6e46c5ab1e4ac111818ff3acd6b636e39d650898189ac0c21802a8233ad4cd38",
        "harness2/core/recursion.py": "b299813b5334f2c0f89024a7f14162f0fdd5418ebd4413be93920b5334a204c1",
        "harness2/core/proposer.py": "475ef49d72228e249d7172d793f8362913774412402dfd259ae6e01c6586e175",
        "LICENSE": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    },
}


def verify_checkout(src: Path) -> list[str]:
    problems = []
    for rel, want in PIN["files"].items():
        p = src / rel
        if not p.is_file() or p.is_symlink():
            problems.append(f"{rel} missing")
            continue
        if hashlib.sha256(p.read_bytes()).hexdigest() != want:
            problems.append(f"{rel} differs from the pinned commit")
    return problems


def run_demo(src: Path, k: int = 2, timeout: int = 300) -> dict[str, Any]:
    src = Path(src).resolve()
    problems = verify_checkout(src)
    if problems:
        return {"status": "refused", "problems": problems}
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from harness2.cli.demo import main; "
            "raise SystemExit(main(['--output', sys.argv[2], '--k', sys.argv[3]]))")
    with tempfile.TemporaryDirectory(prefix="h2-demo-") as tmp:
        env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", "")} if sys.platform == "win32" else {}
        try:
            p = subprocess.run([sys.executable, "-I", "-c", code, str(src), str(Path(tmp) / "demo"), str(int(k))],
                               cwd=tmp, env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"status": "failed", "why": f"timed out after {timeout} s"}
        report = Path(tmp) / "demo" / "demo.json"
        data = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
        return {"status": "passed" if p.returncode == 0 and data.get("failed") == 0 else "failed",
                "exit_code": p.returncode, "done": data.get("done"), "failed": data.get("failed"),
                "execution_count": data.get("execution_count"), "editor_calls": data.get("editor_calls"),
                "stdout_tail": p.stdout[-300:], "stderr_tail": p.stderr[-300:]}


if __name__ == "__main__":
    src = os.environ.get("HARNESS2_SRC", "")
    if not src:
        raise SystemExit("set HARNESS2_SRC to a checkout of " + PIN["repo"] + " at " + PIN["commit"])
    print(json.dumps(run_demo(Path(src)), indent=1))
