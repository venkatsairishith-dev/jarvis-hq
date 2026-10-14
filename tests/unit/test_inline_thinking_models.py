"""Models that put their reasoning inline in paired <think> tags on every reply must not show or speak it."""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from config.providers import always_inlines_thinking, leaks_thinking_in_content
from utils.llm_client import ThinkingStreamStripper, strip_thinking_segments


def test_minimax_m2_family_is_recognised_and_other_models_are_not():
    for m in ("MiniMaxAI/MiniMax-M2.7", "minimax-m2", "MiniMax-M2.5-Flash"):
        assert always_inlines_thinking(m) is True
    for m in ("qwen3-vl-plus", "gpt-4o-mini", "zai-org/GLM-5.3-Flash", "deepseek-chat", "", None):
        assert always_inlines_thinking(m) is False


def test_the_existing_qwen_rule_is_unchanged():
    assert leaks_thinking_in_content("qwen3.6-plus") is True
    assert leaks_thinking_in_content("MiniMaxAI/MiniMax-M2.7") is False        # still not a thinking-on hybrid
    assert leaks_thinking_in_content("qwen3-vl-plus") is False


def _stream(chunks):
    s = ThinkingStreamStripper()
    out = "".join(s.feed(c) for c in chunks)
    return (out + s.flush()).strip()


def test_a_minimax_style_stream_is_cleaned_chunk_by_chunk_including_split_tags():
    chunks = ["<th", "ink>The user asks for the capital. ", "It is Paris, a short sentence.</", "think>", "Paris is the ", "capital of France."]
    assert _stream(chunks) == "Paris is the capital of France."


def test_a_reply_without_thinking_passes_through_untouched():
    assert _stream(["Hello ", "there."]) == "Hello there."


def test_non_streaming_replies_are_cleaned_too():
    assert strip_thinking_segments("<think>reasoning here</think>Paris.") == "Paris."