"""Re-review M12: jarvis_bridge.pending_approvals returned tool/op/target raw and task_result returned raw failure text. The entries
run for real against a fake JarvisClient; the undecorated entry reproduces the leak, the entry as the host calls it does not."""
import asyncio
import json

from plugin.plugins._shared.tests.fakes import ALL, C_EMAIL, C_FILE, C_KEY, C_OCR, C_PATH
from plugin.plugins.jarvis_bridge import JarvisBridgePlugin
from plugin.plugins.jarvis_bridge import client as bridge_client


def leaks(value):
    raw = json.dumps(value, default=str, ensure_ascii=False)
    return [c for c in ALL if c in raw]


def test_pending_approvals_are_cleaned(monkeypatch):
    rows = [{"id": "ap1", "tool": f"desktop {C_PATH}", "op": C_KEY, "target": f"{C_FILE} for the owner", "summary": f"send to {C_EMAIL}", "extra_field": f"met {C_OCR}",
             "created": 12.5, "level": "RED"}]

    async def approvals(self):
        return rows
    monkeypatch.setattr(bridge_client.JarvisClient, "approvals", approvals)
    plugin = object.__new__(JarvisBridgePlugin)
    raw = asyncio.run(JarvisBridgePlugin.pending_approvals.__wrapped__(plugin))
    assert leaks(raw.value), "the review's leak must reproduce on the undecorated entry"
    got = asyncio.run(JarvisBridgePlugin.pending_approvals(plugin))
    assert leaks(got.value) == [] and got.value["count"] == 1 and got.value["approvals"][0]["id"] == "ap1"
    assert got.value["approvals"][0]["created"] == 12.5 and got.value["approvals"][0]["level"] == "RED"


def test_task_result_failure_text_is_cleaned(monkeypatch):
    async def commands(self, limit=10):
        return [{"id": "c1", "source": bridge_client.SOURCE, "state": "failed", "error": f"could not open {C_PATH} with {C_KEY} for {C_EMAIL}"}]
    monkeypatch.setattr(bridge_client.JarvisClient, "commands", commands)
    plugin = object.__new__(JarvisBridgePlugin)
    raw = asyncio.run(JarvisBridgePlugin.task_result.__wrapped__(plugin, command_id="c1"))
    assert leaks(raw.value)
    got = asyncio.run(JarvisBridgePlugin.task_result(plugin, command_id="c1"))
    assert leaks(got.value) == [] and got.value["state"] == "failed" and got.value["command_id"] == "c1"


def test_a_bridge_error_message_is_cleaned_too(monkeypatch):
    async def approvals(self):
        raise bridge_client.BridgeError(f"console said no about {C_PATH} ({C_KEY})", 502)
    monkeypatch.setattr(bridge_client.JarvisClient, "approvals", approvals)
    got = asyncio.run(JarvisBridgePlugin.pending_approvals(object.__new__(JarvisBridgePlugin)))
    assert type(got).__name__ == "Err" and leaks(str(got.error)) == []
