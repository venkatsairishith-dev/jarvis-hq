import json
import unittest

from jarvis_kernel.router.translate import (
    StreamTranslator, Unsupported, UpstreamError,
    anthropic_to_openai, openai_to_anthropic,
)

PNG = "data:image/png;base64,AAAA"


class RequestTests(unittest.TestCase):
    def test_text_and_system_merge(self):
        out = openai_to_anthropic({
            "model": "m",
            "messages": [
                {"role": "system", "content": "be brief"},
                {"role": "developer", "content": [{"type": "text", "text": "be kind"}]},
                {"role": "user", "content": "hi"},
                {"role": "user", "content": [{"type": "input_text", "text": "there"}]},
            ],
            "stop": "END", "temperature": 0.5, "top_p": 0.9,
        })
        self.assertEqual(out["system"], "be brief\n\nbe kind")
        self.assertEqual(out["max_tokens"], 4096)
        self.assertEqual(out["stop_sequences"], ["END"])
        self.assertEqual(out["temperature"], 0.5)
        self.assertEqual(out["messages"], [{"role": "user", "content": [
            {"type": "text", "text": "hi"}, {"type": "text", "text": "there"}]}])

    def test_drop_sampling_and_max_tokens(self):
        out = openai_to_anthropic({"model": "m", "max_completion_tokens": 7,
                                   "max_tokens": 9, "temperature": 1, "top_p": 1,
                                   "messages": [{"role": "user", "content": "x"}]},
                                  drop_sampling=True)
        self.assertEqual(out["max_tokens"], 7)
        self.assertNotIn("temperature", out)
        self.assertNotIn("top_p", out)

    def test_first_turn_is_user(self):
        out = openai_to_anthropic({"model": "m", "messages": [
            {"role": "assistant", "content": "hello"}]})
        self.assertEqual(out["messages"][0]["role"], "user")
        self.assertEqual(out["messages"][1]["role"], "assistant")

    def test_images(self):
        out = openai_to_anthropic({"model": "m", "messages": [{"role": "user", "content": [
            {"type": "text", "text": "see"},
            {"type": "image_url", "image_url": {"url": PNG}},
            {"type": "image_url", "image_url": {"url": "https://x.test/a.png"}},
        ]}]})
        c = out["messages"][0]["content"]
        self.assertEqual(c[1], {"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": "AAAA"}})
        self.assertEqual(c[2], {"type": "image", "source": {
            "type": "url", "url": "https://x.test/a.png"}})
        for bad in ("http://x.test/a.png", "file:///a.png", "data:image/png,AAAA"):
            with self.assertRaises(Unsupported):
                openai_to_anthropic({"model": "m", "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": bad}}]}]})

    def test_unknown_part_type(self):
        with self.assertRaises(Unsupported):
            openai_to_anthropic({"model": "m", "messages": [{"role": "user", "content": [
                {"type": "input_audio", "input_audio": {}}]}]})

    def test_tools_and_tool_choice(self):
        tools = [{"type": "function", "function": {
            "name": "f", "description": "d", "parameters": {"type": "object"}}}]
        base = {"model": "m", "tools": tools,
                "messages": [{"role": "user", "content": "x"}]}
        out = openai_to_anthropic(base)
        self.assertEqual(out["tools"], [{"name": "f", "description": "d",
                                         "input_schema": {"type": "object"}}])
        for tc, want in (("auto", {"type": "auto"}), ("none", {"type": "none"}),
                         ("required", {"type": "any"}),
                         ({"type": "function", "function": {"name": "f"}},
                          {"type": "tool", "name": "f"})):
            self.assertEqual(openai_to_anthropic(dict(base, tool_choice=tc))["tool_choice"], want)
        with self.assertRaises(Unsupported):
            openai_to_anthropic(dict(base, tool_choice="bogus"))

    def test_tool_call_with_parallel_results(self):
        out = openai_to_anthropic({"model": "m", "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "f", "arguments": '{"x": 1}'}},
                {"id": "b", "type": "function", "function": {"name": "g", "arguments": ""}},
            ]},
            {"role": "tool", "tool_call_id": "a", "content": "ra"},
            {"role": "tool", "tool_call_id": "b", "content": [{"type": "text", "text": "rb"}]},
            {"role": "user", "content": "thanks"},
        ]})
        m = out["messages"]
        self.assertEqual(len(m), 3)
        self.assertEqual(m[1]["content"], [
            {"type": "tool_use", "id": "a", "name": "f", "input": {"x": 1}},
            {"type": "tool_use", "id": "b", "name": "g", "input": {}}])
        self.assertEqual(m[2]["role"], "user")
        self.assertEqual(m[2]["content"], [
            {"type": "tool_result", "tool_use_id": "a", "content": "ra"},
            {"type": "tool_result", "tool_use_id": "b", "content": "rb"},
            {"type": "text", "text": "thanks"}])

    def test_invalid_tool_arguments(self):
        for args in ("{not json", "[1]"):
            with self.assertRaises(Unsupported):
                openai_to_anthropic({"model": "m", "messages": [
                    {"role": "user", "content": "x"},
                    {"role": "assistant", "tool_calls": [
                        {"id": "a", "type": "function",
                         "function": {"name": "f", "arguments": args}}]}]})


class ResponseTests(unittest.TestCase):
    def test_text_tool_thinking_usage(self):
        r = anthropic_to_openai({
            "id": "msg_1", "stop_reason": "tool_use",
            "content": [{"type": "thinking", "thinking": "hmm"},
                        {"type": "text", "text": "a"}, {"type": "text", "text": "b"},
                        {"type": "mystery", "x": 1},
                        {"type": "tool_use", "id": "t1", "name": "f", "input": {"k": "v"}}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}, "mod")
        ch = r["choices"][0]
        self.assertEqual(r["object"], "chat.completion")
        self.assertEqual(r["model"], "mod")
        self.assertEqual(ch["message"]["content"], "ab")
        self.assertEqual(ch["message"]["tool_calls"], [{
            "id": "t1", "type": "function",
            "function": {"name": "f", "arguments": '{"k": "v"}'}}])
        self.assertEqual(ch["finish_reason"], "tool_calls")
        self.assertEqual(r["usage"], {"prompt_tokens": 10, "completion_tokens": 5,
                                      "total_tokens": 15})

    def test_stop_reasons(self):
        for sr, want in (("end_turn", "stop"), ("stop_sequence", "stop"),
                         ("max_tokens", "length"), ("tool_use", "tool_calls"),
                         ("refusal", "content_filter")):
            r = anthropic_to_openai({"id": "i", "stop_reason": sr,
                                     "content": [{"type": "text", "text": "t"}]}, "m")
            self.assertEqual(r["choices"][0]["finish_reason"], want)


def ev(name, **data):
    return name, json.dumps(dict(data, type=name))


class StreamTests(unittest.TestCase):
    def chunk(self, delta, finish=None, usage=None):
        o = {"id": "msg_1", "object": "chat.completion.chunk", "created": 1,
             "model": "mod",
             "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if usage:
            o["usage"] = usage
        return "data: " + json.dumps(o) + "\n\n"

    def test_full_transcript(self):
        events = [
            ev("message_start", message={"id": "msg_1", "model": "mod",
                                         "usage": {"input_tokens": 12, "output_tokens": 1}}),
            ev("content_block_start", index=0, content_block={"type": "thinking", "thinking": ""}),
            ev("content_block_delta", index=0, delta={"type": "thinking_delta", "thinking": "x"}),
            ev("content_block_stop", index=0),
            ev("content_block_start", index=1, content_block={"type": "text", "text": ""}),
            ev("content_block_delta", index=1, delta={"type": "text_delta", "text": "Hel"}),
            ev("ping"),
            ev("content_block_delta", index=1, delta={"type": "text_delta", "text": "lo"}),
            ev("content_block_stop", index=1),
            ev("content_block_start", index=2, content_block={
                "type": "tool_use", "id": "tu_1", "name": "f", "input": {}}),
            ev("content_block_delta", index=2, delta={"type": "input_json_delta", "partial_json": ""}),
            ev("content_block_delta", index=2, delta={"type": "input_json_delta", "partial_json": '{"a"'}),
            ev("content_block_delta", index=2, delta={"type": "input_json_delta", "partial_json": ': 1}'}),
            ev("content_block_stop", index=2),
            ev("brand_new_event", foo=1),
            ev("message_delta", delta={"stop_reason": "tool_use"}, usage={"output_tokens": 30}),
            ev("message_stop"),
        ]
        t = StreamTranslator(created=1)
        out = []
        for name, data in events:
            out.extend(t.feed(name, data))
        tc = lambda **f: {"tool_calls": [f]}
        expected = [
            self.chunk({"role": "assistant", "content": ""}),
            self.chunk({"content": "Hel"}),
            self.chunk({"content": "lo"}),
            self.chunk(tc(index=0, id="tu_1", type="function",
                          function={"name": "f", "arguments": ""})),
            self.chunk(tc(index=0, function={"arguments": ""})),
            self.chunk(tc(index=0, function={"arguments": '{"a"'})),
            self.chunk(tc(index=0, function={"arguments": ': 1}'})),
            self.chunk({}, "tool_calls", {"prompt_tokens": 12, "completion_tokens": 30,
                                          "total_tokens": 42}),
            "data: [DONE]\n\n",
        ]
        self.assertEqual(out, expected)
        self.assertEqual(t.pings, 1)

    def test_error_event(self):
        t = StreamTranslator()
        with self.assertRaises(UpstreamError):
            t.feed("error", json.dumps({"type": "error", "error": {
                "type": "overloaded_error", "message": "Overloaded"}}))

    def test_unknown_event_and_block(self):
        t = StreamTranslator(created=1)
        self.assertEqual(t.feed("whatever", "{}"), [])
        t.feed(*ev("message_start", message={"id": "m", "model": "x"}))
        self.assertEqual(t.feed(*ev("content_block_start", index=0,
                                    content_block={"type": "server_tool_use"})), [])
        self.assertEqual(t.feed(*ev("content_block_delta", index=0,
                                    delta={"type": "text_delta", "text": "no"})), [])
        self.assertEqual(t.feed(*ev("content_block_delta", index=0,
                                    delta={"type": "weird_delta"})), [])


if __name__ == "__main__":
    unittest.main()
