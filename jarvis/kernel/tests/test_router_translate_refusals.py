"""Review addition: request features Anthropic cannot honour are refused, not silently dropped."""
import unittest

from jarvis_kernel.router.translate import Unsupported, openai_to_anthropic

BASE = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}


class Refusals(unittest.TestCase):
    def test_features_that_cannot_be_honoured_raise(self):
        for extra in ({"response_format": {"type": "json_object"}}, {"response_format": {"type": "json_schema", "json_schema": {}}},
                      {"response_format": "json"}, {"n": 2}, {"logit_bias": {"50256": -100}}, {"functions": [{"name": "f"}]},
                      {"function_call": "auto"}, {"logprobs": True}, {"audio": {"voice": "x"}}, {"modalities": ["text", "audio"]}):
            with self.subTest(extra=extra), self.assertRaises(Unsupported):
                openai_to_anthropic({**BASE, **extra})

    def test_harmless_values_still_pass(self):
        out = openai_to_anthropic({**BASE, "response_format": {"type": "text"}, "n": 1, "logprobs": False})
        self.assertEqual(out["messages"][0]["content"][0]["text"], "hi")


if __name__ == "__main__":
    unittest.main()
