"""Local Team logic without any model: the transport is replaced by fakes that record what would be sent."""
import pytest

from plugin.plugins.local_team import core

REAL_LOADED_MODELS = core.loaded_models


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch):
    """Unit tests never look at the real llama-swap or the real free memory: every model counts as loaded."""
    monkeypatch.setattr(core, "loaded_models", lambda get=None: {"general", "code", "math", "router-llm"})


def test_loaded_models_reads_aliases_from_status():
    listing = {"data": [{"id": "ling-tiny", "status": {"value": "ready"}, "meta": {"llamaswap": {"aliases": ["general"]}}},
                        {"id": "qwen-coder-1.5b", "status": {"value": "unloaded"}, "meta": {"llamaswap": {"aliases": ["code"]}}}]}
    assert REAL_LOADED_MODELS(get=lambda url: listing) == {"ling-tiny", "general"}


class FakePost:
    def __init__(self, router_word="general", answer="done", adviser_plan="1. do it"):
        self.calls = []
        self.router_word, self.answer, self.adviser_plan = router_word, answer, adviser_plan

    jev = None        # (choice, probability) the fake decision model returns; None = service down

    def __call__(self, url, body, headers=None, timeout=0):
        self.calls.append((url, body, headers))
        if url == core.JEV_URL:
            if self.jev is None:
                raise core.TeamError("down")
            c, p = self.jev
            return {"answers": {"q": {"type": "choice", "choice": c, "probabilities": {c: p}, "confidence": 0.1}}}
        if body.get("grammar"):
            return {"choices": [{"message": {"content": self.router_word}}]}
        return {"choices": [{"message": {"content": self.answer}}], "timings": {"predicted_per_second": 80.0}}

    def stream(self, url, body, headers=None, timeout=0):
        self.calls.append((url, body, headers))
        return "<think>hmm</think>" + self.adviser_plan


@pytest.mark.parametrize("task,want", [
    ("write a python function that reverses a string", "code"),
    ("fix this traceback: KeyError 'id'", "code"),
    ("what is 17 * 23 + 4?", "math"),
    ("solve the equation 2x + 3 = 11", "math"),
])
def test_rules_route(task, want):
    assert core.rule_route(task) == want


def test_jev_decides_when_sure():
    post = FakePost(router_word="general")
    post.jev = ("math", 0.69)
    spec, how = core.route("my bill was split between three friends, who owes what", post)
    assert spec == "math" and how.startswith("jev")
    q = post.calls[0][1]["questions"]["q"]
    assert q["type"] == "choice" and set(q["criteria"]) == {"code", "math", "general"}
    assert len(post.calls) == 1                  # the generalist was not asked


def test_unsure_jev_falls_back_to_the_generalist():
    post = FakePost(router_word="general")
    post.jev = ("code", 0.42)
    assert core.route("why does my laptop get hot when gaming", post) == ("general", "router model")


def test_router_model_only_when_rules_cannot_tell():
    post = FakePost(router_word="math")          # Jev down
    spec, how = core.route("my bill was split between three friends, who owes what", post)
    assert (spec, how) == ("math", "router model")
    body = post.calls[1][1]
    assert body["grammar"] == core.ROUTER_GRAMMAR and body["model"] == core.ROUTER_MODEL
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_router_failure_falls_back_to_general():
    def broken(*a, **k):
        raise core.TeamError("down")
    assert core.route("tell me a story", broken) == ("general", "default")


def test_ask_local_general_turns_thinking_off_and_strips_reasoning():
    post = FakePost(router_word="general", answer="<think>long</think>The sky is blue.")
    r = core.ask_local("why is the sky blue", post=post)
    body = post.calls[-1][1]
    assert body["model"] == "general" and body["chat_template_kwargs"] == {"enable_thinking": False}
    assert r["answer"] == "The sky is blue." and r["left_this_pc"] is False


def test_unclosed_think_with_gap():
    assert core.strip_reasoning("<think>planning...\n\n\nAnswer here") == "Answer here"


def test_adviser_refuses_a_direct_public_url():
    post = FakePost()
    with pytest.raises(core.TeamError, match="privacy gateway"):
        core.advise("my password = x", "https://inference.dahl.global/v1", "m", "k", stream=post.stream)
    assert post.calls == []                      # nothing was sent


def test_adviser_goes_through_gateway_streamed_and_strips_think():
    post = FakePost(adviser_plan="1. Step one")
    a = core.advise("task", "http://127.0.0.1:48950/v1", "m", "k", stream=post.stream,
                    gateway_preview=lambda t: {"kinds": {"PASSWORD": 1}})
    assert a == {"plan": "1. Step one", "hidden_from_adviser": {"PASSWORD": 1}}
    assert post.calls[0][2] == {"Authorization": "Bearer k"}


def test_post_stream_joins_sse_deltas(monkeypatch):
    lines = [b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n', b"\n", b'data: {"choices":[{"delta":{"content":"lo"}}]}\n',
             b"data: [DONE]\n"]

    class Resp:
        def __enter__(self):
            return iter(lines)

        def __exit__(self, *a):
            return False
    seen = {}

    def fake_open(req, timeout=0):
        seen["body"] = req.data
        return Resp()
    monkeypatch.setattr(core.urllib.request, "urlopen", fake_open)
    assert core.post_stream("http://127.0.0.1:48950/v1/chat/completions", {"model": "m"}) == "Hello"
    assert b'"stream": true' in seen["body"]


def test_post_stream_reports_a_budget_spent_on_reasoning(monkeypatch):
    lines = [b'data: {"choices":[{"delta":{"content":"<think>is this allowed..."}}]}\n',
             b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n', b"data: [DONE]\n"]

    class Resp:
        def __enter__(self):
            return iter(lines)

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda req, timeout=0: Resp())
    with pytest.raises(core.TeamError, match="token budget"):
        core.post_stream("http://127.0.0.1:48950/v1/chat/completions", {"model": "m"})


def test_jev_skips_the_adviser_for_a_direct_task():
    post = FakePost(answer="Paris")
    post.jev = ("direct", 0.70)                  # p_deep 0.30
    called = []
    r = core.solve("what is the capital of France", adviser=lambda t: called.append(t) or {"plan": "x"}, post=post)
    assert called == [] and r["adviser_used"] is False and "direct task" in r["note"]


def test_jev_calls_the_adviser_for_a_deep_task():
    post = FakePost(answer="plan done")
    post.jev = ("deep", 0.82)
    r = core.solve("design an offline-first sync architecture", adviser=lambda t: {"plan": "1. x"}, post=post)
    assert r["adviser_used"] is True and r["adviser_decision"].startswith("jev")


def test_use_adviser_never_and_bad_value():
    post = FakePost(answer="ok")
    r = core.solve("design a system", adviser=lambda t: {"plan": "1. x"}, post=post, use_adviser="never")
    assert r["adviser_used"] is False
    with pytest.raises(core.TeamError):
        core.solve("x", adviser=None, post=post, use_adviser="sometimes")


def test_solve_uses_plan_then_local_specialist():
    post = FakePost(answer="def rev(s): return s[::-1]")
    adviser = lambda t: core.advise(t, "http://127.0.0.1:48950/v1", "m", "", stream=post.stream)
    r = core.solve("write a python function that reverses a string", adviser=adviser, post=post, use_adviser="always")
    assert r["specialist"] == "code" and r["adviser_used"] is True
    last = post.calls[-1][1]
    assert last["model"] == "code" and "Guidance from a senior adviser" in last["messages"][0]["content"]


def test_solve_without_adviser_thinks_locally():
    post = FakePost(router_word="general", answer="ok")
    def no_adviser(t):
        raise core.TeamError("not via gateway")
    r = core.solve("tell me a story about a cat", adviser=no_adviser, post=post)
    assert r["adviser_used"] is False and "adviser skipped" in r["note"]
    assert post.calls[-1][1]["chat_template_kwargs"] == {"enable_thinking": True}


def test_memory_guard():
    ok, _ = core.memory_ok("general", free_gb=16.0, loaded=set())
    assert ok
    ok, why = core.memory_ok("general", free_gb=5.0, loaded=set())
    assert not ok and "Not enough free memory" in why
    ok, _ = core.memory_ok("general", free_gb=1.0, loaded={"general"})      # already loaded: nothing more needed
    assert ok


def test_ask_local_refuses_when_memory_is_short(monkeypatch):
    monkeypatch.setattr(core, "loaded_models", lambda get=None: set())
    monkeypatch.setattr(core, "memory_ok", lambda m, free_gb=None, loaded=None: (False, "Not enough free memory"))
    with pytest.raises(core.TeamError, match="Not enough free memory"):
        core.ask_local("why is the sky blue", "general", post=FakePost())


def test_empty_and_too_long_tasks_refused():
    with pytest.raises(core.TeamError):
        core.ask_local("", post=FakePost())
    with pytest.raises(core.TeamError, match="too long"):
        core.ask_local("x" * (core.MAX_TASK + 1), post=FakePost())
