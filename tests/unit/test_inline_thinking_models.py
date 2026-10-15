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

def _open_only(chunks):
    s = ThinkingStreamStripper(open_tag_only=True)
    out = "".join(s.feed(c) for c in chunks)
    return out + s.flush()


def test_unclosed_think_block_ends_at_the_blank_gap_the_endpoint_leaves():
    """Seen live on Dahl with tools in the request: <think> stays, </think> is dropped, three newlines remain."""
    raw = "<think>The user asks the capital. No tools needed.\n\n\nThe capital of France is Paris."
    for size in (1, 4, 9, 1000):
        chunks = [raw[i:i + size] for i in range(0, len(raw), size)]
        assert _open_only(chunks).strip() == "The capital of France is Paris.", size


def test_closed_block_still_works_in_open_tag_only_mode():
    assert _open_only(["<think>reason</think>\n\nParis."]).strip() == "Paris."


def test_reply_without_think_streams_through_immediately():
    s = ThinkingStreamStripper(open_tag_only=True)
    assert s.feed("Hello there, this is a plain reply") == "Hello there, this is a plain reply"
    assert s.feed(" and more.") == " and more."


def test_reasoning_only_reply_with_no_answer_yields_nothing():
    assert _open_only(["<think>I should call a tool now.\n"]) == ""


def test_a_reasoning_paragraph_break_is_not_the_end():
    raw = "<think>Step one.\n\nStep two.\n\n\nAnswer: Paris."
    assert _open_only([raw]).strip() == "Answer: Paris."


def test_non_streaming_cleaner_handles_an_unclosed_think_block():
    assert strip_thinking_segments("<think>reasoning\n\n\nParis.") == "Paris."
    assert strip_thinking_segments("<think>reasoning only, no gap") == "<think>reasoning only, no gap"
    assert strip_thinking_segments("Plain reply.") == "Plain reply."


def test_untagged_reasoning_before_the_blank_gap_is_dropped_whatever_the_chunking():
    """Seen live in the opening greeting: no <think> tags at all, just reasoning prose, a blank gap, then the real line."""
    raw = ("The user wants me to start a conversation. Looking at the recent context, I was annoyed.\n\n"
           "Let me write something short and natural:\n\n\nThe food I got you didn't go cold, did it?")
    for size in (1, 3, 11, 1000):
        chunks = [raw[i:i + size] for i in range(0, len(raw), size)]
        assert _open_only(chunks).strip() == "The food I got you didn't go cold, did it?", size


def test_a_reply_that_merely_starts_with_a_similar_word_is_never_lost():
    for reply in ("The user is an idiot, obviously.", "Looking at the mess you made, I sigh.", "Let me see... no. Idiot.",
                  "Now there's a thought, and a bad one."):
        assert _open_only([reply[:9], reply[9:]]) == reply, reply                         # no blank gap: released unchanged


def test_ordinary_replies_stream_without_delay():
    s = ThinkingStreamStripper(open_tag_only=True)
    assert s.feed("Paris. Again.") == "Paris. Again."
    s2 = ThinkingStreamStripper(open_tag_only=True)
    assert s2.feed("Idiot, you asked that three times already.") == "Idiot, you asked that three times already."
    s3 = ThinkingStreamStripper(open_tag_only=True)                  # "T..." could still become "The user ...": held briefly, then released
    assert s3.feed("Th") == "" and s3.feed("at was dumb, genius. Try again!") == "That was dumb, genius. Try again!"


def test_the_tagged_shapes_still_work_next_to_the_untagged_one():
    assert _open_only(["<think>reason\n\n\nParis."]).strip() == "Paris."
    assert _open_only(["<think>reason</think>\n\nParis."]).strip() == "Paris."
