"""The cleaner must never alter one of the project's own ids (follow-up to re-review M12, 2026-10-19).

Bug: clean_result()/clean_message() read the digits of a task id "20261019-121346-f83eca" as a 14-digit card number; when they passed
the Luhn check (about one id in ten, and ALL ids made in the same second, which is why a probe in a loop failed 3000 of 3000 and the
lock-holder test only now and then) the holder's id became "[card number removed]" / the screen-text note. Now: (1) a card number may
not touch a letter, digit or dash; (2) a value that FULLY matches one of ID_PATTERNS is verbatim under any key (except token / secret
keys); (3) the id-valued keys are in SAFE_RAW_KEYS. Synthetic data only."""
from __future__ import annotations

import random
import secrets
import uuid

import pytest

from plugin.plugins._shared import screen_text as st
from plugin.plugins._shared.tests.fakes import C_EMAIL, C_FILE, C_KEY, C_OCR, C_PATH

RNG = random.Random(20261019)


def luhn_ok(digits: str) -> bool:
    return st._luhn(digits)


def gen_task() -> str:                                         # coordinator.py: time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    return f"2026{RNG.randint(1, 12):02d}{RNG.randint(1, 28):02d}-{RNG.randint(0, 23):02d}{RNG.randint(0, 59):02d}{RNG.randint(0, 59):02d}-" \
           + secrets.token_hex(3)


GENERATORS = {
    "task": gen_task,
    "mission": lambda: "m" + gen_task(),                                           # missions.py
    "single op": lambda: f"single-{RNG.choice(['run_pc_action', 'open_website', 'check_pc'])}-{secrets.token_hex(3)}",   # desktop/__init__.py
    "task op": lambda: "task-" + secrets.token_hex(3),
    "step op": lambda: f"step-{RNG.choice(['ui', 'open_app', 'job'])}-{secrets.token_hex(2)}",
    "kernel job": lambda: secrets.token_hex(6),                                    # jobs.py: token_hex(6)
    "kernel task": lambda: secrets.token_hex(6),                                   # tasks.py
    "approval": lambda: secrets.token_hex(8),                                      # store.py: token_hex(8)
    "command": lambda: "c-" + secrets.token_hex(6),                                # owner.py
    "bench job": lambda: "job-" + secrets.token_hex(5),
    "run id": lambda: uuid.uuid4().hex[:10],                                       # main_agent
    "uuid": lambda: str(uuid.UUID(int=RNG.getrandbits(128))),
    "uuid hex": lambda: uuid.UUID(int=RNG.getrandbits(128)).hex,
    "memory id": lambda: "m-" + secrets.token_hex(4),
    "iso date": lambda: f"2026-{RNG.randint(1, 12):02d}-{RNG.randint(1, 28):02d}",
    "iso time": lambda: f"2026-10-19T{RNG.randint(0, 23):02d}:{RNG.randint(0, 59):02d}:{RNG.randint(0, 59):02d}Z",
    "clock": lambda: f"{RNG.randint(0, 23):02d}:{RNG.randint(0, 59):02d}:{RNG.randint(0, 59):02d}",
    "state word": lambda: RNG.choice(["running", "waiting_for_permission", "done", "still_running", "cancelled", "stopped", "queued"]),
}
KEYS = ["holder", "task", "op", "operation", "mission", "job", "jobs", "approval_id", "id", "ids", "stopped", "still_running",
        "unconfirmed_jobs", "some_key_nobody_listed", "x"]


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch):
    monkeypatch.delenv(st.FLAG, raising=False)


@pytest.mark.parametrize("kind", sorted(GENERATORS))
def test_property_an_id_in_the_projects_own_format_is_never_altered_under_any_key(kind):
    for _ in range(200):
        v = GENERATORS[kind]()
        assert st.is_known_id(v), (kind, v)
        for key in KEYS:
            assert st.clean_result({key: v})[key] == v, (kind, key, v)
        assert st.clean_result({"list": [v, v], "nest": {"deep": [{"k": v}]}}) == {"list": [v, v], "nest": {"deep": [{"k": v}]}}


def test_every_task_id_survives_even_when_its_digits_pass_the_luhn_check():
    """The regression itself, deterministic: 100 ids whose date+time digits are Luhn-valid (the ones that used to turn into
    '[card number removed]') in a result, in an error message and as a sentence."""
    made = 0
    while made < 100:
        d = f"2026{RNG.randint(1, 12):02d}{RNG.randint(1, 28):02d}{RNG.randint(0, 23):02d}{RNG.randint(0, 59):02d}{RNG.randint(0, 59):02d}"
        if not luhn_ok(d):
            continue
        tid = f"{d[:8]}-{d[8:]}-{secrets.token_hex(3)}"
        made += 1
        assert st.scrub_text(tid)[0] == tid, "the card rule must not read a task id as a card number"
        assert st.clean_result({"holder": tid, "task": tid, "other": tid}) == {"holder": tid, "task": tid, "other": tid}
        assert tid in st.clean_message(f"another PC task is running ({tid}); stop it first or wait")
        assert st.clean_result({"summary": f"another PC task is running ({tid})"})["summary"].count(tid) == 1


def test_a_value_that_only_contains_an_id_or_a_secret_is_still_cleaned():
    tid = "20261019-121346-f83eca"
    out = st.clean_result({"free": f"{tid} then {C_KEY}", "summary": f"{tid} {C_KEY} {C_EMAIL} {C_PATH}", "path": f"{C_PATH}/{tid}",
                           "k": C_KEY, "token": secrets.token_hex(8), "api_token": tid, "password": "hunter2hunter2"})
    raw = str(out)
    assert C_KEY not in raw and C_EMAIL not in raw and C_PATH not in raw and "hunter2hunter2" not in raw
    assert "SYNTH-Owner" not in raw
    assert out["token"] != raw and out["token"].startswith("["), "a key named token never takes the id shortcut"
    assert out["api_token"].startswith("[")


def test_real_card_numbers_are_still_removed():
    for text in ("card 4111 1111 1111 1111 expires", "4111111111111111", "pay with 4111-1111-1111-1111.", "(4111 1111 1111 1111)"):
        clean, hits = st.scrub_text(text)
        assert "4111" not in clean and "card_number" in hits, text
    assert st.scrub_text("order 1234567890123 shipped")[0] == "order 1234567890123 shipped"      # 13 digits, Luhn-invalid: not a card


def test_canaries_in_free_text_fields_are_still_cleaned_next_to_ids():
    tid = "20261019-121346-f83eca"
    rec = {"holder": tid, "task": tid, "status": "busy", "summary": f"busy with {tid}: {C_FILE} {C_EMAIL}",
           "steps": [{"n": 1, "checked": f"saw {C_OCR} in {C_PATH}", "passed": True}], "still_running": [{"id": tid, "kind": "task", "state": "running"}]}
    out = st.clean_result(rec)
    assert out["holder"] == tid and out["task"] == tid and out["still_running"][0]["id"] == tid and out["status"] == "busy"
    raw = str(out)
    for canary in (C_FILE, C_EMAIL, C_OCR, C_PATH):
        assert canary not in raw
    assert tid in out["summary"]


def test_id_patterns_are_explicit_and_each_one_rejects_prose():
    names = [n for n, _ in st.ID_PATTERNS]
    assert names == ["task", "mission", "operation", "hex", "prefixed_hex", "uuid", "iso_time", "clock", "enum"]
    for prose in ("Priyanka Rao", "open the budget", "a .xlsx file", "C:\\Users\\x", "a@b.example", "20261019-121346-f83eca extra", ""):
        assert not st.is_known_id(prose), prose
