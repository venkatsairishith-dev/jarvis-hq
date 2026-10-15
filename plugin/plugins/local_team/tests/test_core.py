"""Local Team logic without any model: the transport is replaced by fakes that record what would be sent."""
import pytest

from plugin.plugins.local_team import core


class FakePost:
    def __init__(self, router_word="general", answer="done", adviser_plan="1. do it"):
        self.calls = []
        self.router_word, self.answer, self.adviser_plan = router_word, answer, adviser_plan

    def __call__(self, url, body, headers=None, timeout=0):
        self.calls.append((url, body, headers))
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


def test_router_model_only_when_rules_cannot_tell():
    post = FakePost(router_word="math")
    spec, how = core.route("my bill was split between three friends, who owes what", post)
    assert (spec, how) == ("math", "router model")
    body = post.calls[0][1]
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


def test_solve_uses_plan_then_local_specialist():
    post = FakePost(answer="def rev(s): return s[::-1]")
    adviser = lambda t: core.advise(t, "http://127.0.0.1:48950/v1", "m", "", stream=post.stream)
    r = core.solve("write a python function that reverses a string", adviser=adviser, post=post)
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


def test_empty_and_too_long_tasks_refused():
    with pytest.raises(core.TeamError):
        core.ask_local("", post=FakePost())
    with pytest.raises(core.TeamError, match="too long"):
        core.ask_local("x" * (core.MAX_TASK + 1), post=FakePost())
