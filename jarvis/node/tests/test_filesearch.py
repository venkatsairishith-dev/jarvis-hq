"""filesearch: query filters, backends (scandir, es.exe through a fake script), fallback, ranking, secret and root safety."""
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_node import filesearch as fs   # noqa: E402
from jarvis_node.filesearch import BadQuery, EsBackend, Query, ScandirBackend, WindowsSearchBackend  # noqa: E402


def _w(path, data="x", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return str(path)


@pytest.fixture
def tree(tmp_path):
    r = tmp_path / "root"
    _w(str(r / "report.txt"), "a" * 10, 1000)
    _w(str(r / "report_old.md"), "a" * 500, 2000)
    _w(str(r / "sub" / "notes.TXT"), "a" * 100, 3000)
    _w(str(r / "sub" / "deep" / "deeper" / "buried.txt"), "a", 4000)
    os.makedirs(r / "folder_report")
    return r


def names(res):
    return sorted(h.name for h in res.hits)


def run(q, root, **kw):
    return fs.search(q, [str(root)], backends=[ScandirBackend()], **kw)


def test_substring_case_insensitive_and_sensitive(tree):
    assert "notes.TXT" in names(run(Query("NOTES"), tree))
    assert names(run(Query("NOTES", case_sensitive=True), tree)) == []


def test_extensions_kind_sizes_times(tree):
    assert names(run(Query(extensions=("txt",), kind="file"), tree)) == ["buried.txt", "notes.TXT", "report.txt"]
    assert names(run(Query("report", kind="dir"), tree)) == ["folder_report"]
    assert names(run(Query("report", kind="file", min_size=100), tree)) == ["report_old.md"]
    assert names(run(Query("report", kind="file", max_size=100), tree)) == ["report.txt"]
    assert names(run(Query(kind="file", modified_after=2500), tree)) == ["buried.txt", "notes.TXT"]
    assert names(run(Query(kind="file", modified_before=1500), tree)) == ["report.txt"]


def test_glob_and_regex(tree):
    assert names(run(Query("report*.*", mode="glob", kind="file"), tree)) == ["report.txt", "report_old.md"]
    assert names(run(Query(r"^report(_old)?\.(txt|md)$", mode="regex"), tree)) == ["report.txt", "report_old.md"]


def test_invalid_queries():
    with pytest.raises(BadQuery):
        Query("(", mode="regex")
    with pytest.raises(BadQuery):
        Query("a" * 201, mode="regex")
    with pytest.raises(BadQuery):
        Query("a\x00b")
    assert Query(limit=99999).limit == 1000


def test_ignore_list(tmp_path):
    r = tmp_path / "root"
    for d in ("node_modules", ".git", "__pycache__", "$RECYCLE.BIN", "System Volume Information", ".jarvis-trash", "AppData/Local/Temp", "ok"):
        _w(str(r / d / "target.txt"))
    assert [h.path for h in run(Query("target"), r).hits] == [str(r / "ok" / "target.txt")]
    _w(str(r / "NODE_MODULES" / "x" / "target2.txt"))
    assert run(Query("target2"), r).hits == []


def test_depth_limit(tree):
    assert "buried.txt" not in names(run(Query("buried", max_depth=3), tree))
    assert "buried.txt" in names(run(Query("buried", max_depth=4), tree))
    assert names(run(Query("txt", max_depth=1), tree)) == ["report.txt"]


def test_truncation(tree):
    res = run(Query("", kind="file", limit=2), tree)
    assert res.truncated and len(res.hits) == 2
    res = run(Query("", kind="file", limit=100), tree)
    assert not res.truncated and len(res.hits) == 4


def test_link_out_of_roots_not_followed(tmp_path):
    outside = tmp_path / "outside"
    _w(str(outside / "outside_hit.txt"))
    root = tmp_path / "root"
    _w(str(root / "inside.txt"))
    link = str(root / "lnk")
    try:
        if os.name == "nt":
            p = subprocess.run(["cmd", "/c", "mklink", "/J", link, str(outside)], capture_output=True)
            if p.returncode != 0:
                pytest.skip("cannot create a junction")
        else:
            os.symlink(str(outside), link)
    except OSError:
        pytest.skip("cannot create a link")
    res = run(Query("_hit"), root)
    assert res.hits == []
    res = run(Query("inside"), root)
    assert names(res) == ["inside.txt"]


def test_secret_paths_never_returned(tmp_path):
    r = tmp_path / "root"
    _w(str(r / "id_ed25519"))
    _w(str(r / ".env"))
    _w(str(r / ("jarvis" + "-secrets") / "notes_ok.txt"))
    _w(str(r / "plain_ok.txt"))
    res = run(Query(""), r)
    got = names(res)
    assert "plain_ok.txt" in got
    for bad in ("id_ed25519", ".env", "notes_ok.txt"):
        assert bad not in got
    assert all(("jarvis" + "-secrets") not in h.path for h in res.hits)


def test_allow_predicate(tree):
    res = run(Query("report", kind="file"), tree, allow=lambda p: "old" not in p)
    assert names(res) == ["report.txt"]


def _fake_es(tmp_path, lines, body=None, log=None):
    script = tmp_path / "fake_es.py"
    script.write_text(body or (
        "import sys\n"
        f"open({str(log or tmp_path / 'argv.txt')!r}, 'w').write(repr(sys.argv[1:]))\n"
        f"print({chr(10).join(lines)!r})\n"))
    return [sys.executable, str(script)]


def test_es_backend_args_and_parsing(tree, tmp_path):
    ghost = str(tree / "ghost.txt")
    exe = _fake_es(tmp_path, [str(tree / "report.txt"), ghost, str(tree / "sub" / "notes.TXT"), r"C:\elsewhere\report.txt"])
    be = EsBackend(exe)
    assert be.available()
    res = fs.search(Query("-rf report"), [str(tree)], backends=[be])
    assert res.backends_tried == ["es"]
    assert names(res) == []              # "-rf report" is not a substring of any name; text was still passed after "--"
    argv = eval((tmp_path / "argv.txt").read_text())
    assert argv[-2:] == ["--", "-rf report"]
    res = fs.search(Query("report"), [str(tree)], backends=[be])
    assert names(res) == ["report.txt"]         # vanished and out-of-root lines dropped, notes.TXT fails the filter
    h = res.hits[0]
    assert h.backend == "es" and h.size == 10 and h.modified == 1000


def test_es_missing_not_available(tmp_path):
    assert not EsBackend(str(tmp_path / "nope" / "es.exe")).available()
    assert not EsBackend([sys.executable, str(tmp_path / "nope.py")]).available()


def test_es_timeout_falls_back(tree, tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "ES_TIMEOUT_S", 0.5)
    exe = _fake_es(tmp_path, [], body="import time\ntime.sleep(30)\n")
    t = time.time()
    res = fs.search(Query("report", kind="file"), [str(tree)], backends=[EsBackend(exe), ScandirBackend()])
    assert time.time() - t < 20
    assert res.backends_tried == ["es", "scandir"]
    assert res.skipped and res.skipped[0][0] == "es" and "timed out" in res.skipped[0][1]
    assert names(res) == ["report.txt", "report_old.md"]


def test_failing_backend_falls_back_with_reason(tree):
    class Boom:
        name = "boom"
        def available(self): return True
        def search(self, q, roots): raise RuntimeError("kaput")
    res = fs.search(Query("buried"), [str(tree)], backends=[Boom(), WindowsSearchBackend(), ScandirBackend()])
    assert names(res) == ["buried.txt"]
    assert res.backends_tried == ["boom", "scandir"]
    assert res.skipped[0] == ("boom", "RuntimeError: kaput")
    assert res.skipped[1][0] == "windows-search"


def test_windows_search_stub():
    b = WindowsSearchBackend()
    assert not b.available()
    with pytest.raises(fs.Unavailable):
        list(b.search(Query(), []))


def test_ranking(tmp_path):
    r = tmp_path / "root"
    _w(str(r / "a_foo_b.txt"), mtime=5000)
    _w(str(r / "foo_new.txt"), mtime=4000)
    _w(str(r / "foo_old.txt"), mtime=1000)
    _w(str(r / "foo"), mtime=100)
    res = run(Query("foo", kind="file"), r)
    assert [h.name for h in res.hits] == ["foo", "foo_new.txt", "foo_old.txt", "a_foo_b.txt"]


def test_no_shell_string(tree, tmp_path, monkeypatch):
    calls = []
    real = subprocess.run

    def spy(cmd, *a, **kw):
        calls.append((cmd, kw))
        return real(cmd, *a, **kw)
    monkeypatch.setattr(fs.subprocess, "run", spy)
    exe = _fake_es(tmp_path, [str(tree / "report.txt")])
    fs.search(Query("report"), [str(tree)], backends=[EsBackend(exe)])
    assert calls and all(isinstance(c, list) and not kw.get("shell") for c, kw in calls)
    assert "--" in calls[0][0]
