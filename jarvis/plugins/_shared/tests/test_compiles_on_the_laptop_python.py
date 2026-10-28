"""Every plugin file must compile under the Python that runs it. The laptop's agent-venv is Python 3.11 (plugin host, face tool server, these tests); the cloud
sandbox is newer, where a backslash inside an f-string expression is legal. desktop/taskrun.py had one (merge of 2026-10-28) and failed to import on the laptop,
which main_agent's outcome-registry test was the first to notice. This test fails the same way wherever the suite runs on 3.11."""
import pathlib

JARVIS = pathlib.Path(__file__).resolve().parents[3]


def test_every_plugin_and_pluginapi_file_compiles():
    bad = []
    for top in ("plugins", "pluginapi", "bridge"):
        for p in (JARVIS / top).rglob("*.py"):
            if "venv" in p.parts or "node_modules" in p.parts:
                continue
            try:
                compile(p.read_bytes(), str(p), "exec")
            except SyntaxError as e:
                bad.append(f"{p.relative_to(JARVIS)}:{e.lineno}: {e.msg}")
    assert not bad, "\n".join(bad)
