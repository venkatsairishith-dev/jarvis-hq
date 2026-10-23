"""Tests for jarvis.pluginapi: the plugin host runs real plugin packages written to disk, and offers their entries as MCP tools."""
from __future__ import annotations

import asyncio
import json
import sys
import textwrap

import pytest

from jarvis.pluginapi import Err, Ok, PluginBase, SdkError, jarvis_plugin, lifecycle, plugin_entry, timer_interval, tr, ui
from jarvis.pluginapi import host as H
from jarvis.pluginapi.decorators import ENTRY_META_ATTR
from jarvis.pluginapi.log import BraceLogger


def run(coro):
    return asyncio.run(coro)


# ---- results, text, logger -------------------------------------------------------------------------------------------------
def test_ok_and_err_behave_like_results():
    assert Ok(3).unwrap() == 3 and Ok(3).is_ok() and not Ok(3).is_err()
    e = Err(SdkError("no"))
    assert e.is_err() and e.unwrap_or(7) == 7
    with pytest.raises(SdkError, match="no"):
        e.unwrap()
    assert str(SdkError("boom", code="x", detail=None)) == "boom" and SdkError("a", extra=1).context == {"extra": 1}


def test_tr_gives_the_english_default():
    assert tr("entry.x.name", default="Do X") == "Do X" and tr("entry.x.name") == "entry.x.name"


def test_the_logger_fills_brace_placeholders(caplog):
    caplog.set_level("WARNING", logger="jarvis.plugin.t")
    BraceLogger("jarvis.plugin.t").warning("run {} failed: {}", "r1", "TimeoutError")
    BraceLogger("jarvis.plugin.t").warning("odd {} {}", "only-one")        # a wrong placeholder count never raises
    assert caplog.messages[0] == "run r1 failed: TimeoutError" and "only-one" in caplog.messages[1]


def test_decorators_record_what_the_plugin_declares():
    @jarvis_plugin
    class P(PluginBase):
        @ui.action(id="go", label=tr("a", default="Go"))
        @plugin_entry(id="go", name="Go", description="d", input_schema={"type": "object"}, timeout=5, llm_result_fields=["message"])
        async def go(self, **_):
            return Ok({})

        @lifecycle(id="startup")
        async def startup(self, **_):
            return Ok({})

    meta = P.go.__jarvis_entry_meta__
    assert (meta.kind, meta.id, meta.event_type, meta.timeout, meta.llm_result_fields) == ("entry", "go", "plugin_entry", 5, ["message"])
    assert P.go.__jarvis_ui__["label"] == "Go" and getattr(P, "__jarvis_plugin__") is True
    with pytest.raises(ValueError):
        lifecycle(id="nonsense")


def test_a_base_class_works_without_init_as_the_tests_use_it():
    class P(PluginBase):
        pass
    p = object.__new__(P)
    p.logger.warning("fine {}", 1)
    assert p.push_message(text="x") is None                               # no host: said in the log, never raised
    P.plugins = "router"                                                  # a test replaces `plugins` on the class
    assert p.plugins == "router"


# ---- the host ----------------------------------------------------------------------------------------------------------------
PLUGIN_A = '''
import asyncio
from jarvis.pluginapi import Err, Ok, PluginBase, SdkError, jarvis_plugin, lifecycle, plugin_entry, timer_interval

@jarvis_plugin
class Alpha(PluginBase):
    started = []
    ticks = []

    @lifecycle(id="startup")
    async def startup(self, **_):
        Alpha.started.append("up")
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        Alpha.started.append("down")
        return Ok({})

    @timer_interval(id="tick", seconds=0.05)
    async def tick(self, **_):
        Alpha.ticks.append(1)

    @plugin_entry(id="echo", name="Echo", description="Echo a word back.",
                  input_schema={"type": "object", "properties": {"word": {"type": "string", "description": "The word."},
                                                                  "times": {"type": "integer"}}, "required": ["word"]},
                  llm_result_fields=["said"])
    async def echo(self, word="", times=1, **_):
        return Ok({"said": word * times, "secret": "not for the model"})

    @plugin_entry(id="slow", timeout=0.1)
    async def slow(self, **_):
        await asyncio.sleep(2)
        return Ok({})

    @plugin_entry(id="boom")
    async def boom(self, **_):
        raise RuntimeError("provider said: key sk-LEAK")

    @plugin_entry(id="plain")
    async def plain(self, **_):
        return {"just": "a dict"}

    @plugin_entry(id="bad")
    async def bad(self, **_):
        return Err(SdkError("it did not work"))

    @plugin_entry(id="ask_beta")
    async def ask_beta(self, **_):
        res = await self.plugins.call_entry_json("beta:hello", {"name": "Ana"}, timeout=5)
        return Ok({"beta_said": res.value["greeting"]}) if isinstance(res, Ok) else res

    @plugin_entry(id="loop")
    async def loop(self, **_):
        res = await self.plugins.call_entry_json("alpha:loop", {})
        return res

    @plugin_entry(id="speak")
    async def speak(self, **_):
        self.push_message(source="alpha", parts=[{"type": "text", "text": "ready"}], priority=6, coalesce_key="k")
        self.push_message(source="alpha", parts=[{"type": "text", "text": "ready again"}], priority=6, coalesce_key="k")
        return Ok({})
'''
PLUGIN_B = '''
from jarvis.pluginapi import Ok, PluginBase, jarvis_plugin, plugin_entry

@jarvis_plugin
class Beta(PluginBase):
    @plugin_entry(id="hello", input_schema={"type": "object", "properties": {"name": {"type": "string"}}})
    async def hello(self, name="", **_):
        return Ok({"greeting": f"hello {name}"})
'''
PLUGIN_BROKEN = "raise ImportError('this plugin is broken')\n"
PLUGIN_SYNC = '''
from jarvis.pluginapi import Ok, PluginBase, jarvis_plugin, plugin_entry

@jarvis_plugin
class Syn(PluginBase):
    @plugin_entry(id="x")
    def x(self, **_):
        return Ok({})
'''


def make_plugin(root, pid, source, cls, enabled=True):
    folder = root / pid
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text(textwrap.dedent(source), encoding="utf-8")
    (folder / "plugin.toml").write_text(f'[plugin]\nid = "{pid}"\nname = "{pid.title()}"\ndescription = "{pid} plugin"\n'
                                        f'entry = "jt_plugins.{pid}:{cls}"\n[plugin_runtime]\nenabled = {str(enabled).lower()}\n', encoding="utf-8")


@pytest.fixture
def plugins_dir(tmp_path):
    root = tmp_path / "jt_plugins"
    root.mkdir()
    (root / "__init__.py").write_text("")
    make_plugin(root, "alpha", PLUGIN_A, "Alpha")
    make_plugin(root, "beta", PLUGIN_B, "Beta")
    make_plugin(root, "broken", PLUGIN_BROKEN, "Nope")
    make_plugin(root, "syn", PLUGIN_SYNC, "Syn")
    make_plugin(root, "off", PLUGIN_B, "Beta", enabled=False)
    sys.path.insert(0, str(tmp_path))
    yield root
    sys.path.remove(str(tmp_path))
    for name in [m for m in sys.modules if m == "jt_plugins" or m.startswith("jt_plugins.")]:
        del sys.modules[name]


def started(plugins_dir, **kw):
    host = H.PluginHost(plugins_dir, **kw)
    host.load()
    run(host.start())
    return host


def test_the_host_loads_good_plugins_and_records_broken_ones(plugins_dir):
    host = started(plugins_dir)
    status = {pid: p.status for pid, p in host.loaded.items()}
    assert status == {"alpha": "running", "beta": "running", "broken": "failed", "syn": "failed"}     # "off" is disabled: not loaded
    assert "this plugin is broken" in host.loaded["broken"].error and "async def" in host.loaded["syn"].error
    assert [d["id"] for d in host.describe() if d["status"] == "running"] == ["alpha", "beta"]
    assert run(host.router.list_ids(enabled=True)).value == ["alpha", "beta"]


def test_entries_are_called_with_checks_and_without_leaking_errors(plugins_dir):
    host = started(plugins_dir)
    r = host.router
    assert run(r.call_entry("alpha:echo", {"word": "ab", "times": 2})).value["said"] == "abab"
    assert "word" in run(r.call_entry("alpha:echo", {})).error.message                      # a required argument is missing
    assert run(r.call_entry("alpha:nope", {})).error.code == "not_found"
    assert run(r.call_entry("ghost:x", {})).error.code == "not_found"
    assert run(r.call_entry("broken:x", {})).error.code == "not_found"                      # a failed plugin has no entries
    err = run(r.call_entry("alpha:boom", {})).error
    assert err.message == "alpha:boom failed (RuntimeError)" and "LEAK" not in err.message  # only the type leaves
    assert run(r.call_entry("alpha:slow", {})).error.code == "timeout"
    assert run(r.call_entry("alpha:plain", {})).value == {"just": "a dict"}                  # a plain return becomes Ok
    assert run(r.call_entry("alpha:bad", {})).error.message == "it did not work"
    assert run(r.call_entry_json("alpha:plain", {})).value == {"just": "a dict"}


def test_plugins_call_each_other_and_a_loop_stops(plugins_dir):
    host = started(plugins_dir)
    assert run(host.router.call_entry("alpha:ask_beta", {})).value == {"beta_said": "hello Ana"}
    looped = run(host.router.call_entry("alpha:loop", {}))
    assert looped.is_err() and looped.error.code == "too_deep"


def test_lifecycle_and_timers_run_and_stop(plugins_dir):
    async def go():
        host = H.PluginHost(plugins_dir)
        host.load()
        await host.start()
        await asyncio.sleep(0.3)
        await host.stop()
        n = len(host.loaded["alpha"].cls.ticks)
        await asyncio.sleep(0.2)
        return host, n
    host, ticks = run(go())
    cls = host.loaded["alpha"].cls
    assert cls.started == ["up", "down"] and ticks >= 2 and len(cls.ticks) == ticks             # no tick after stop
    assert host.loaded["alpha"].status == "stopped"


def test_messages_from_plugins_wait_in_the_outbox_and_a_newer_one_replaces_an_unread_one(plugins_dir):
    host = started(plugins_dir)
    run(host.router.call_entry("alpha:speak", {}))
    got = host.outbox.take()
    assert [m["text"] for m in got] == ["ready again"] and got[0]["from"] == "alpha" and got[0]["priority"] == 6
    assert host.outbox.take() == []                                                           # read once


def test_only_and_exclude_choose_plugins(plugins_dir):
    assert list(started(plugins_dir, only={"beta"}).loaded) == ["beta"]
    assert "alpha" not in started(plugins_dir, exclude={"alpha"}).loaded


# ---- the plugins' entries as MCP tools -----------------------------------------------------------------------------------------
def test_entries_become_tools_with_their_schema_and_the_model_sees_only_allowed_fields(plugins_dir):
    from mcp.server.mcpserver import MCPServer
    host = started(plugins_dir)
    server = MCPServer("t")
    names = H.register_tools(server, host, exclude={"beta"})
    assert "alpha__echo" in names and not any(n.startswith("beta__") for n in names)
    tools = {t.name: t for t in run(server.list_tools())}
    schema = tools["alpha__echo"].input_schema if hasattr(tools["alpha__echo"], "input_schema") else tools["alpha__echo"].inputSchema
    assert schema["required"] == ["word"] and schema["properties"]["word"]["description"] == "The word."
    assert {"type": "integer"} in schema["properties"]["times"]["anyOf"]                    # optional: an integer or nothing
    out = run(server.call_tool("alpha__echo", {"word": "hi"}))
    payload = json.loads(out.content[0].text)
    assert payload == {"ok": True, "result": {"said": "hi"}}                                  # "secret" is not shown to the model
    failed = json.loads(run(server.call_tool("alpha__boom", {})).content[0].text)
    assert failed["ok"] is False and "LEAK" not in failed["error"]


def test_the_planner_catalogue_lists_running_plugins_and_their_entries(plugins_dir):
    host = started(plugins_dir)
    listing = {p["id"]: p for p in host.router.list_plugins()}
    assert listing["broken"]["entries"] == [] and listing["broken"]["status"] == "failed"
    echo = next(e for e in listing["alpha"]["entries"] if e["id"] == "echo")
    assert echo["description"] == "Echo a word back." and echo["input_schema"]["required"] == ["word"]
