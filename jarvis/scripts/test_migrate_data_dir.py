"""jarvis/scripts/migrate_data_dir.py: copies the old layout to the new one, never overwrites and never deletes."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("migrate_data_dir", Path(__file__).with_name("migrate_data_dir.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


def write(p: Path, text: str = "x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def layout(tmp: Path):
    old, new, out = tmp / "old", tmp / "new", tmp / "outputs"
    write(old / "neko" / "pc_tasks" / "t1.json", "task")
    write(old / "neko" / "decider_skills" / "v1.json")
    write(old / "muse" / "watches.json")
    write(old / "main_agent_runs" / "r1.json")
    write(old / "config" / "core_config.json", "n.e.k.o only")
    write(out / "neko" / "deliverables" / "d1" / "deck.pptx")
    write(out / "neko" / "research" / "r1" / "report.pdf")
    write(out / "neko" / "note.md", "a document")
    return old, new, out


def test_a_dry_run_copies_nothing(tmp_path, capsys):
    old, new, out = layout(tmp_path)
    assert M.main(["--old", str(old), "--new", str(new), "--outputs", str(out)]) == 0
    assert not new.exists() and "dry run" in capsys.readouterr().out


def test_apply_copies_to_the_new_names_and_leaves_the_old_folders_alone(tmp_path):
    old, new, out = layout(tmp_path)
    assert M.main(["--old", str(old), "--new", str(new), "--outputs", str(out), "--apply"]) == 0
    assert (new / "plugins" / "pc_tasks" / "t1.json").read_text() == "task"
    assert (new / "plugins" / "decider_skills" / "v1.json").exists() and (new / "muse" / "watches.json").exists()
    assert (new / "main_agent_runs" / "r1.json").exists()
    assert (out / "deliverables" / "d1" / "deck.pptx").exists() and (out / "research" / "r1" / "report.pdf").exists()
    assert (out / "documents" / "note.md").exists() and not (out / "documents" / "deliverables").exists()
    assert not (new / "config").exists()                                      # only N.E.K.O used it
    assert (old / "neko" / "pc_tasks" / "t1.json").exists() and (out / "neko" / "note.md").exists()   # nothing deleted


def test_a_file_that_already_exists_is_never_overwritten(tmp_path):
    old, new, out = layout(tmp_path)
    write(new / "muse" / "watches.json", "newer")
    M.main(["--old", str(old), "--new", str(new), "--outputs", str(out), "--apply"])
    assert (new / "muse" / "watches.json").read_text() == "newer"
    copied, skipped = M.apply(M.plan(old, new, out))
    assert copied == 0 and skipped > 0                                         # a second run changes nothing


def test_nothing_to_migrate_is_said_plainly(tmp_path, capsys):
    assert M.main(["--old", str(tmp_path / "a"), "--new", str(tmp_path / "b"), "--outputs", str(tmp_path / "c")]) == 0
    assert "nothing to migrate" in capsys.readouterr().out
