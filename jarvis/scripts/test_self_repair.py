"""self_repair proposes, checks and (only when the owner says so) applies a patch to a branch. No model, no network."""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import self_repair as sr  # noqa: E402

GOOD = """--- a/jarvis/plugins/demo/core.py
+++ b/jarvis/plugins/demo/core.py
@@ -1,2 +1,2 @@
-VALUE = 1
+VALUE = 2
 OTHER = 3
"""


@pytest.fixture()
def repo(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "jarvis" / "plugins" / "demo").mkdir(parents=True)
    (tmp_path / "jarvis" / "plugins" / "demo" / "core.py").write_text("VALUE = 1\nOTHER = 3\n")
    (tmp_path / "jarvis" / "plugins" / "demo" / "test_core.py").write_text("from core import VALUE\n\ndef test_v():\n    assert VALUE == 2\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    return str(tmp_path)


def test_a_small_patch_in_an_allowed_folder_passes(repo):
    assert sr.check_patch(GOOD, repo) == (True, "ok")


@pytest.mark.parametrize("path", ["jarvis/kernel/jarvis_kernel/policy.py", "jarvis/privacy/gateway.py", "jarvis/plugins/x/tests/test_a.py",
                                  "jarvis/scripts/self_repair.py", "jarvis/plugins/approval_thing.py", "README.MD", "jarvis/plugins/../kernel/x.py"])
def test_protected_or_foreign_paths_are_refused(repo, path):
    patch = GOOD.replace("jarvis/plugins/demo/core.py", path)
    ok, why = sr.check_patch(patch, repo)
    assert not ok and ("outside" in why or "protected" in why)


def test_large_or_empty_or_unappliable_patches_are_refused(repo):
    assert sr.check_patch("", repo)[0] is False
    assert sr.check_patch(GOOD + "\n".join("+x" for _ in range(200)), repo)[0] is False
    stale = GOOD.replace("-VALUE = 1", "-VALUE = 9")
    ok, why = sr.check_patch(stale, repo)
    assert not ok and "git does not accept" in why


def test_the_model_answer_is_parsed_through_think_text_and_bad_answers_raise():
    text = '<think>hmm</think>```json\n{"problem": "p", "cause": "c", "confidence": "HIGH", "fix": "f", "files": ["a.py", 3], "patch": ""}\n```'
    out = sr.parse_answer(text)
    assert out["confidence"] == "high" and out["files"] == ["a.py"]
    with pytest.raises(ValueError):
        sr.parse_answer("no json here")


def test_diagnose_saves_a_proposal_and_never_applies(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "DATA", str(tmp_path.parent / (tmp_path.name + "-data")))
    monkeypatch.setattr(sr, "REPO", repo)
    monkeypatch.setattr(sr, "collect", lambda day, since, top: [{"count": 3, "level": "ERROR", "component": "tools", "message": "x <n>", "first": "01:00:00", "last": "02:00:00", "example": "x 1"}])
    seen = {}

    def fake_ask(messages):
        seen["prompt"] = json.dumps(messages)
        return json.dumps({"problem": "x", "cause": "VALUE is wrong", "confidence": "medium", "fix": "set it to 2", "files": ["jarvis/plugins/demo/core.py"], "patch": GOOD})
    p = sr.diagnose("20261024", "00:00", 8, asker=fake_ask)
    assert p["status"] == "proposed" and p["patch_ok"] is True
    assert "~~~~untrusted" in seen["prompt"]                              # the log text is marked as data
    assert open(os.path.join(repo, "jarvis", "plugins", "demo", "core.py")).read().startswith("VALUE = 1")   # nothing was applied
    assert os.path.exists(os.path.join(sr.repairs_dir(), p["id"] + ".patch"))


def test_apply_makes_a_branch_applies_the_patch_and_runs_the_tests(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "DATA", str(tmp_path.parent / (tmp_path.name + "-data")))
    patch_ok, _ = sr.check_patch(GOOD, repo)
    assert patch_ok
    rid = sr.save({"patch": GOOD, "patch_ok": True, "patch_check": "ok", "confidence": "high", "problem": "p"})
    monkeypatch.chdir(repo)
    env_path = os.path.join(repo, "jarvis", "plugins", "demo")
    monkeypatch.setenv("PYTHONPATH", env_path)
    rc = sr.apply(rid, repo)
    assert rc == 0
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert branch == f"selfrepair/{rid}"
    assert open(os.path.join(env_path, "core.py")).read().startswith("VALUE = 2")


def test_apply_refuses_a_dirty_tree(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "DATA", str(tmp_path.parent / (tmp_path.name + "-data")))
    rid = sr.save({"patch": GOOD, "patch_ok": True, "patch_check": "ok", "confidence": "high", "problem": "p"})
    with open(os.path.join(repo, "jarvis", "plugins", "demo", "extra.py"), "w") as f:
        f.write("x = 1\n")
    assert sr.apply(rid, repo) == 2
