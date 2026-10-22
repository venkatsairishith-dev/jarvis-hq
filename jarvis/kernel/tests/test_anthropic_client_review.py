"""Review additions: a key with control characters never reaches the library, and upstream sizes are bounded."""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jarvis_kernel.router import anthropic_client as AC
from jarvis_kernel.router.translate import UpstreamError

BODY = {"model": "claude-sonnet-5-5", "messages": [{"role": "user", "content": "hi"}]}


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


class FakeOpener:
    def __init__(self, payload):
        self.payload, self.calls = payload, 0

    def open(self, req, timeout=None):
        self.calls += 1
        return FakeResp(self.payload)


class KeyAndSizes(unittest.TestCase):
    def test_keys_with_control_characters_are_refused_before_anything_is_sent_and_never_echoed(self):
        for bad in ("sk-ant-SECRETPART\nX-Injected: 1", "sk-ant-SECRETPART\r\n", "sk-ant-SECRET\0PART", "sk-ant-SECRÉT", ""):
            opener = FakeOpener(b"{}")
            client = AC.AnthropicClient(opener=opener)
            for call in (lambda: client.chat(BODY, bad), lambda: list(client.chat_stream(BODY, bad))):
                with self.assertRaises(AC.ClientError) as cm:
                    call()
                self.assertNotIn("SECRET", str(cm.exception))
            self.assertEqual(opener.calls, 0)

    def test_an_oversized_non_streaming_reply_is_refused(self):
        old = AC.MAX_RESPONSE
        AC.MAX_RESPONSE = 100
        try:
            with self.assertRaises(UpstreamError):
                AC.AnthropicClient(opener=FakeOpener(b'{"content": [], "pad": "' + b"x" * 500 + b'"}')).chat(BODY, "sk-ant-key123456")
        finally:
            AC.MAX_RESPONSE = old

    def test_an_oversized_stream_line_is_refused(self):
        old = AC.MAX_LINE
        AC.MAX_LINE = 100
        try:
            with self.assertRaises(UpstreamError):
                list(AC.AnthropicClient(opener=FakeOpener(b"data: " + b"x" * 500 + b"\n\n")).chat_stream(BODY, "sk-ant-key123456"))
        finally:
            AC.MAX_LINE = old


if __name__ == "__main__":
    unittest.main()
