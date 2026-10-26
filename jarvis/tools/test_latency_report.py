"""D60: the latency report reads the content-free timing log and prints p50/p90 per phase."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import latency_report as L  # noqa: E402


def face(t, turn, phase, ms, **kw):
    return {"t": t, "src": "face", "turn": turn, "phase": phase, "ms": ms, **kw}


def one_turn(t0, turn, scale=1.0, tool="desktop__do_goal"):
    return [face(t0, turn, "sent", 0), face(t0, turn, "ack", 40), face(t0, turn, "model_first", int(3000 * scale), round=1),
            face(t0, turn, "tool_start", int(3200 * scale), tool=tool, round=1), face(t0, turn, "tool_end", int(8200 * scale), tool=tool, round=1),
            face(t0, turn, "model_first", int(10000 * scale), round=2), face(t0, turn, "first_token", int(10000 * scale), round=2),
            face(t0, turn, "final_token", int(10500 * scale)), face(t0, turn, "done", int(10500 * scale))]


def write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_percentiles_by_rank():
    assert L.percentile([], 0.5) == 0
    assert L.percentile([7], 0.9) == 7
    assert L.percentile(list(range(1, 11)), 0.9) == 9
    assert L.summarise([1, 2, 3]) == {"n": 3, "p50": 2, "p90": 3, "max": 3}


def test_turns_are_grouped_and_phases_summarised(tmp_path):
    rows = one_turn(100, 1) + one_turn(200, 2, 2.0) + one_turn(300, 1, 0.5)          # turn number 1 again: the app restarted
    rep = L.build_report(sorted(rows, key=lambda r: r["t"]))
    f = rep["face"]
    assert f["turns"] == 3
    assert f["phases"]["done"]["n"] == 3 and f["phases"]["done"]["p50"] == 10500 and f["phases"]["done"]["max"] == 21000
    assert f["phases"]["ack"] == {"n": 3, "p50": 40, "p90": 40, "max": 40}
    assert f["model_rounds"]["p50"] == 2
    assert f["tool_run_ms"]["desktop__do_goal"]["n"] == 3 and f["tool_run_ms"]["desktop__do_goal"]["p50"] == 5000
    assert f["tool_decision_ms"]["p50"] == 200
    assert f["kinds"]["done_with_tools"]["n"] == 3


def test_a_chat_only_turn_and_a_quick_turn_are_told_apart():
    chat = [face(1, 1, "sent", 0), face(1, 1, "model_first", 900, round=1), face(1, 1, "first_token", 900, round=1), face(1, 1, "done", 2500)]
    quick = [face(2, 2, "sent", 0), face(2, 2, "ack", 30), face(2, 2, "quick_start", 31), face(2, 2, "quick_end", 2600), face(2, 2, "first_token", 2610), face(2, 2, "done", 2620)]
    f = L.face_report(chat + quick)
    assert f["kinds"]["first_token_no_tool"]["n"] == 2
    assert f["kinds"]["done_chat_only"]["p50"] == 2500 and f["kinds"]["done_quick"]["p50"] == 2620


def test_gateway_lines_by_provider_and_by_kind_of_round():
    rows = [{"t": i, "src": "gateway", "provider": "dahl", "after": "user" if i % 2 else "tool", "redact_ms": 5 + i, "queue_ms": 0, "first_ms": 1000 * i, "total_ms": 2000 * i,
             "req_bytes": 8000 + i, "cache_hits": 10, "cache_misses": 1} for i in range(1, 11)]
    g = L.gateway_report(rows)
    assert set(g) == {"dahl", "dahl / after user", "dahl / after tool"}
    assert g["dahl"]["first_ms"]["n"] == 10 and g["dahl"]["first_ms"]["p50"] == 5000 and g["dahl"]["first_ms"]["p90"] == 9000
    assert g["dahl / after tool"]["total_ms"]["n"] == 5
    assert g["dahl"]["cache_hits"]["p50"] == 10


def test_the_log_reader_skips_broken_lines_reads_the_rotated_file_and_filters_by_age(tmp_path):
    log = tmp_path / "latency.jsonl"
    write(Path(str(log) + ".1"), [face(10, 1, "sent", 0)])
    log.write_text(json.dumps(face(20, 1, "done", 5)) + "\nnot json\n[1,2]\n" + json.dumps({"src": "face"}) + "\n" + json.dumps(face(990, 2, "sent", 0)) + "\n", encoding="utf-8")
    assert [r["t"] for r in L.read_lines(log)] == [10, 20, 990]
    assert [r["t"] for r in L.read_lines(log, since_s=100, now=1000)] == [990]
    assert L.read_lines(tmp_path / "missing.jsonl") == []
    assert L.parse_since("2h") == 7200 and L.parse_since("30m") == 1800 and L.parse_since("90") == 90 and L.parse_since(None) == 0


def test_the_text_report_names_targets_and_says_so_when_empty(tmp_path):
    text = L.render(L.build_report(one_turn(1, 1)))
    assert "Chat turns" in text and "done" in text and "target p50 15.0 s, p90 30.0 s  OK" in text
    assert "OVER" in L.render(L.build_report(one_turn(1, 1, 5.0)))
    assert "No timing lines yet" in L.render(L.build_report([]))


def test_save_and_compare_roundtrip(tmp_path, capsys):
    log_a, log_b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    write(log_a, one_turn(1, 1, 4.0))
    write(log_b, one_turn(1, 1, 1.0))
    a, b = tmp_path / "before.json", tmp_path / "after.json"
    assert L.main(["--log", str(log_a), "--save", str(a), "--json"]) == 0
    assert L.main(["--log", str(log_b), "--save", str(b), "--json"]) == 0
    capsys.readouterr()
    assert L.main(["--compare", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "done" in out and "42.0 s -> 10.5 s" in out


def test_health_is_optional_and_a_down_gateway_is_not_an_error():
    assert L.read_health("http://127.0.0.1:9/health", timeout=0.3) is None
