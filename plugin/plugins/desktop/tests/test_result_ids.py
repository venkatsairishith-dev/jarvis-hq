"""The busy / stop / recover / status answers of the real desktop entries carry ids (task, holder, job, approval, mission) that the
owner and the agent need verbatim; the cleaning at the plugin boundary must not touch them (follow-up to re-review M12). The tasks'
ids here are chosen so that their date+time digits PASS the Luhn check, the case that used to be replaced by a note."""
import asyncio
import json
import secrets

import pytest

from plugin.plugins._shared import screen_text as st
from plugin.plugins.desktop import DesktopPlugin
from plugin.plugins.desktop import coordinator as C
from plugin.plugins.desktop import missions
from plugin.plugins.desktop.tests.test_stop_ownership import Host


def luhn_task_id(offset: int = 0) -> str:
    n = 0
    for sec in range(86400):
        d = f"20261019{sec // 3600:02d}{sec // 60 % 60:02d}{sec % 60:02d}"
        if st._luhn(d):
            if n == offset:
                return f"{d[:8]}-{d[8:]}-{secrets.token_hex(3)}"
            n += 1
    raise AssertionError


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch):
    monkeypatch.delenv(st.FLAG, raising=False)


def test_busy_stop_and_recover_answers_keep_every_id_verbatim(monkeypatch):
    monkeypatch.setattr(C, "STOP_WAIT_S", 0.3)               # the held operation never ends here: do not wait the full 20 s
    tid = luhn_task_id()
    op = C.begin("task", tid)
    hold = C.acquire(tid, op=op)                           # a real lock holder with a Luhn-valid id
    try:
        async def go():
            h = Host()
            busy = (await DesktopPlugin.run_pc_task(h, request="open notepad")).value
            stop = (await DesktopPlugin.stop_pc_task(h)).value
            recover = (await DesktopPlugin.recover_pc_lock(h)).value
            return busy, stop, recover
        busy, stop, recover = asyncio.run(go())
    finally:
        C.release(hold)
        op.finish() if hasattr(op, "finish") else None
    raw_all = json.dumps([busy, stop, recover])
    assert tid in raw_all, "the holder's id must be in the busy / stop / recover answers"
    assert busy["status"] == "busy" and tid in busy["summary"]
    assert "[card number removed]" not in raw_all and "stayed on this PC" not in raw_all
    assert any(d["id"] == tid for d in stop["still_running"] + stop["stopped"]) or tid in json.dumps(stop)


def test_mission_status_keeps_the_mission_and_approval_ids(monkeypatch):
    mid = "m" + luhn_task_id(3)
    aid = secrets.token_hex(8)
    rec = {"id": mid, "goal": "make slides", "status": "waiting_for_permission", "replans": 0, "steps": [], "error": None,
           "question": None, "approval_id": aid, "how": "approve it in the console", "explain": ""}
    monkeypatch.setattr(missions, "latest", lambda: rec)
    monkeypatch.setattr(missions, "load", lambda m: rec)
    got = asyncio.run(DesktopPlugin.mission_status(Host())).value
    assert got["mission"] == mid and got["approval_id"] == aid and got["status"] == "waiting_for_permission"


def test_stop_pc_job_and_pending_approval_ids_survive():
    jid, aid = secrets.token_hex(6), secrets.token_hex(8)
    out = st.clean_result({"id": jid, "state": "cancelled", "note": "stopped", "approvals_denied": [aid], "jobs_cancelled": [jid],
                           "unconfirmed_jobs": [jid], "operation": luhn_task_id(1)})
    assert out["id"] == jid and out["approvals_denied"] == [aid] and out["jobs_cancelled"] == [jid] and out["unconfirmed_jobs"] == [jid]
    assert "stayed" not in json.dumps(out)
