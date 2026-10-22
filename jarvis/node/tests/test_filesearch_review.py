"""Review addition: filtering of secret paths happens after the backend yields, so backends must read past the limit."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from jarvis_node.filesearch import Query, ScandirBackend, search  # noqa: E402


def test_dropped_secret_hits_do_not_use_up_the_limit(tmp_path):
    for i in range(30):
        (tmp_path / f"a_{i:02d}.pem").write_text("k", encoding="utf-8")        # sorts first; never returned (secret-looking)
    for i in range(3):
        (tmp_path / f"b_{i}.txt").write_text("ok", encoding="utf-8")
    res = search(Query(text="_", limit=2), [str(tmp_path)], backends=[ScandirBackend()])
    assert [h.name for h in res.hits] == ["b_0.txt", "b_1.txt"] or {h.name for h in res.hits} <= {"b_0.txt", "b_1.txt", "b_2.txt"}
    assert len(res.hits) == 2 and res.truncated is True
    assert all(not h.name.endswith(".pem") for h in res.hits)


def test_exact_fit_is_not_reported_as_truncated(tmp_path):
    for i in range(3):
        (tmp_path / f"b_{i}.txt").write_text("ok", encoding="utf-8")
    res = search(Query(text="b_", limit=3), [str(tmp_path)], backends=[ScandirBackend()])
    assert len(res.hits) == 3 and res.truncated is False
