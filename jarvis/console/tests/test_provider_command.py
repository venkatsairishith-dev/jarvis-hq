"""Words -> provider switch request (plan item 19). The parser maps words only; the kernel decides."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from jarvis_console.provider_command import describe, parse  # noqa: E402

KNOWN = {"private": ["local", "vm-llm"], "public": ["groq", "dahl", "openrouter"]}


@pytest.mark.parametrize("text,provider,lane", [
    ("switch to groq", "groq", "public"),
    ("Switch to Groq please", "groq", "public"),
    ("hey jarvis, use dahl", "dahl", "public"),
    ("use groq for the public lane", "groq", "public"),
    ("change the provider to openrouter", "openrouter", "public"),
    ("switch to local", "local", "private"),
    ("use local for the private lane", "local", "private"),
    ("go to the vm-llm", "vm-llm", "private"),
    ("switch the private lane to local", "local", "private"),
    ("make groq the public provider", "groq", "public"),
    ("route everything to dahl", "dahl", "public"),
])
def test_switch_phrases(text, provider, lane):
    assert parse(text, KNOWN) == {"action": "switch", "provider": provider, "lane": lane}


def test_an_explicit_lane_that_does_not_fit_is_passed_on_for_the_kernel_to_refuse():
    # the parser does not judge lanes: it reports what was asked, and the kernel answers 400 (a private lane never gets a public provider)
    assert parse("switch the private lane to groq", KNOWN) == {"action": "switch", "provider": "groq", "lane": "private"}
    assert parse("use local for the public lane", KNOWN) == {"action": "switch", "provider": "local", "lane": "public"}


@pytest.mark.parametrize("text", ["back to automatic", "go back to auto", "switch to automatic", "use automatic provider selection",
                                  "reset the provider", "stop forcing a provider", "hey jarvis back to auto"])
def test_back_to_automatic_covers_both_lanes(text):
    assert parse(text, KNOWN) == {"action": "auto", "lanes": ["private", "public"]}


def test_back_to_automatic_for_one_lane():
    assert parse("back to automatic for the public lane", KNOWN) == {"action": "auto", "lanes": ["public"]}


@pytest.mark.parametrize("text", ["which provider am I on", "what provider are you using", "which model are we using right now",
                                  "what is the current provider", "provider status"])
def test_status_phrases(text):
    assert parse(text, KNOWN) == {"action": "status"}


@pytest.mark.parametrize("text", ["switch to nobody provider", "use the fancy model", "change the provider to sparkly"])
def test_clearly_about_providers_but_unknown_is_reported_not_guessed(text):
    out = parse(text, KNOWN)
    assert out["action"] == "unknown"


@pytest.mark.parametrize("text", [
    "switch to dark mode", "use the calculator", "open notepad", "go to the downloads folder", "switch to the next tab",
    "what is the weather", "use my laptop for a while", "move to the left monitor", "set a timer for ten minutes",
    "send an email to bob", "route me to the bank", "", "   ", "x" * 500,
])
def test_ordinary_requests_are_never_taken_for_a_provider_command(text):
    assert parse(text, KNOWN) is None


def test_a_provider_name_inside_another_word_is_not_a_match():
    assert parse("switch to groqish", KNOWN) is None
    assert parse("use the localhost page", KNOWN) is None


def test_a_name_listed_in_both_lanes_needs_a_lane():
    both = {"private": ["shared"], "public": ["shared", "groq"]}
    assert parse("switch to shared", both)["action"] == "unknown"
    assert parse("switch to shared for the public lane", both) == {"action": "switch", "provider": "shared", "lane": "public"}


def test_describe_says_only_what_the_kernel_said():
    assert describe({"status": "done", "lane": "public", "preferred": "groq"}) == "The public lane is now set to groq."
    assert describe({"status": "done", "lane": "public", "preferred": None}) == "The public lane is back to automatic."
    assert "did not switch" in describe({"error": "no such usable provider in this lane"})
    assert describe(None) == "I could not tell what happened."
