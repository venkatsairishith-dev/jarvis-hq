"""Gateway request sizing: oversized histories are never sprayed across every provider; the agent gets a 413 it can
act on (compress) instead of a 502 it retries for minutes."""
import json
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from jarvis_kernel import sizing
from jarvis_kernel.gateway import Provider
from test_llm_proxy import P, TOOL_REPLY, upstream


def history(tokens):
    """A chat history of about *tokens* input tokens (by the gateway's own estimate)."""
    return {"messages": [{"role": "user", "content": "x" * int(tokens * sizing.CHARS_PER_TOKEN)}]}


class Units(unittest.TestCase):
    def test_classify_and_markers(self):
        self.assertEqual(sizing.classify(413, ""), "too_large")
        self.assertEqual(sizing.classify(400, '{"error":{"message":"This model\'s maximum context length is 65536 tokens"}}'), "too_large")
        self.assertEqual(sizing.classify(400, '{"error":{"message":"invalid tool schema"}}'), "other")
        self.assertEqual(sizing.classify(400, '{"error":{"code":400,"message":"request (6358 tokens) exceeds the available context '
                                              'size (4096 tokens)","type":"exceed_context_size_error"}}'), "too_large")   # measured, llama.cpp
        self.assertEqual(sizing.classify(429, "Request too large for model on tokens per minute (TPM)"), "too_large")
        self.assertEqual(sizing.classify(429, "rate limit reached, retry in 20s"), "rate_limited")
        self.assertEqual(sizing.classify(503, ""), "other")

    def test_estimate_counts_tools_and_messages(self):
        small = sizing.estimate_tokens({"messages": [{"role": "user", "content": "hi"}]})
        tools = sizing.estimate_tokens({"messages": [{"role": "user", "content": "hi"}], "tools": [{"function": {"name": "x" * 700}}]})
        self.assertLess(small, 20); self.assertGreater(tools, small + 190)


class Gateway(P):
    def sized(self, name, limit, priority, url=None):
        return Provider(name, "private", url or self.up_url, None, ["any"], priority=priority, max_input_tokens=limit)

    def status(self, body):
        try:
            self.call(body); return 200, None
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_providers_too_small_are_skipped_not_called(self):
        self.proxy([self.sized("small", 8000, 1), self.sized("big", 64000, 2)])
        self.call(history(500)); self.assertEqual(self.seen[-1][0], "good")           # fits the first provider
        n = len(self.seen)
        out, _ = self.call(history(20000))
        self.assertEqual(json.loads(out), TOOL_REPLY)
        self.assertEqual(len(self.seen), n + 1)                                       # one call: the big provider only
        skipped = [json.loads(r[0]) for r in self.k.db.execute("SELECT payload FROM audit WHERE kind='agent_model_skipped'")]
        self.assertEqual(skipped[-1]["providers"], ["small"])

    def test_nothing_fits_gives_one_fast_413_and_no_provider_is_called(self):
        self.proxy([self.sized("small", 8000, 1), self.sized("big", 64000, 2)])
        code, err = self.status(history(70000))
        self.assertEqual(code, 413); self.assertEqual(err["error"]["code"], "context_length_exceeded")
        self.assertIn("64000", err["error"]["message"])
        self.assertEqual(self.seen, [])

    def test_providers_refusing_for_size_end_in_413_not_502(self):
        a, a_url = upstream("a", self.seen, status=413, err_body=b'{"error":{"message":"Request too large"}}')
        b, b_url = upstream("b", self.seen, status=400, err_body=b'{"error":{"message":"maximum context length exceeded"}}')
        self.servers += [a, b]
        self.proxy([self.sized("a", None, 1, a_url), self.sized("b", None, 2, b_url)])   # limits unknown: tried, refused
        code, err = self.status(history(3000))
        self.assertEqual(code, 413)
        self.assertEqual([n for n, _, _ in self.seen], ["a", "b"])                  # each tried once, no same-provider retry

    def test_all_rate_limited_gives_429_with_retry_after(self):
        a, a_url = upstream("a", self.seen, status=429, err_body=b'{"error":{"message":"rate limit"}}')
        self.servers.append(a)
        self.proxy([self.sized("a", None, 1, a_url)])
        try:
            self.call(history(100)); code, retry = 200, None
        except urllib.error.HTTPError as e:
            code, retry = e.code, e.headers.get("Retry-After")
        self.assertEqual((code, retry), (429, "60"))

    def test_a_used_up_daily_quota_benches_the_provider_instead_of_asking_it_every_time(self):
        q, q_url = upstream("quota", self.seen, status=429,
                            err_body=b'{"error":{"message":"Rate limit exceeded: free-models-per-day. Add 10 credits"}}')
        self.servers.append(q)
        self.proxy([self.sized("quota", 64000, 1, q_url), self.sized("small", 8000, 2)])
        self.call(history(500)); self.call(history(500))
        self.assertEqual([n for n, _, _ in self.seen], ["quota", "good", "good"])    # asked once, then rested
        self.assertEqual(self.status(history(20000))[0], 429)                        # only the rested one could fit it
        self.now[0] += 3601
        self.status(history(500)); self.assertEqual([n for n, _, _ in self.seen][-2:], ["quota", "good"])   # asked again after its rest

    def test_other_failures_still_give_502(self):
        self.proxy([self.sized("bad", None, 1, self.bad_url)])
        self.assertEqual(self.status(history(100))[0], 502)

    def test_input_plus_output_limits_shrink_the_answer_budget(self):
        groq = Provider("groq", "private", self.up_url, None, ["any"], priority=1, max_total_tokens=8000)
        self.proxy([groq])
        self.call(history(5000)); body = self.seen[-1][1]
        self.assertTrue(2980 <= body["max_tokens"] <= 3000, body["max_tokens"])     # 8000 - ~5000, not the full 4096
        self.call(dict(history(1000), max_tokens=500)); self.assertEqual(self.seen[-1][1]["max_tokens"], 500)
        self.assertEqual(self.status(history(7500))[0], 413)                          # less than 1024 left for an answer

    def test_models_endpoint_reports_the_lane_context_length(self):
        self.proxy([self.sized("small", 8000, 1), self.sized("big", 64000, 2)])
        r = urllib.request.Request(self.url + "/v1/models", headers={"Authorization": "Bearer " + self.tok})
        m = json.loads(urllib.request.urlopen(r).read())["data"][0]
        self.assertEqual((m["id"], m["context_length"]), ("jarvis", 64000))


if __name__ == "__main__":
    unittest.main()
