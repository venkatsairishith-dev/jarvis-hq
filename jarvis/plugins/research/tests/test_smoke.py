from pathlib import Path


def test_plugin_manifest_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "plugin.toml").read_text(encoding="utf-8")
    assert 'id = "research"' in text
    assert 'entry = "jarvis.plugins.research:ResearchPlugin"' in text
