"""The trigger-scope corpus (§5.1 decision B, §11).

Scope — *what was actually asked* — is delegated to the model (D8), so it
cannot be asserted by equality. What can be asserted, and is asserted here, is
the contract we hand the model: the marker is placed where the name was spoken,
the words on both sides survive intact, and we never slice the question up
ourselves. That is the whole point of the change: it removes a category of
string-surgery bugs (F16) rather than adding a cleverer knife.

The corpus is reviewed by hand and re-run whenever the prompt changes.
"""
import json
from pathlib import Path

import pytest

from bgassist.core.trigger import TRIGGER_MARKER, TriggerParser
from bgassist.llm.prompts import DEFAULT_SYSTEM_PROMPT, build_messages

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "trigger_scope.json").read_text())
CASES = CORPUS["cases"]


def parser():
    return TriggerParser(["computer"])


@pytest.mark.parametrize("case", CASES, ids=[c["utterance"][:40] for c in CASES])
def test_the_marked_transcript_is_what_the_corpus_says(case):
    found = parser().find(case["utterance"])
    assert found is not None
    assert found.position.value == case["position"]
    assert found.marked() == case["marked"]


@pytest.mark.parametrize("case", CASES, ids=[c["utterance"][:40] for c in CASES])
def test_nothing_the_user_said_is_thrown_away(case):
    found = parser().find(case["utterance"])
    marked = found.marked()
    for fragment in case["must_keep"]:
        assert fragment in marked, f"{fragment!r} was lost from {marked!r}"


@pytest.mark.parametrize("text", CORPUS["must_not_wake"])
def test_the_quiet_corpus_stays_quiet(text):
    assert parser().parse(text) is None


def test_the_prompt_explains_the_marker():
    """If the marker is not explained, the model will comment on it."""
    assert TRIGGER_MARKER in DEFAULT_SYSTEM_PROMPT
    prompt = DEFAULT_SYSTEM_PROMPT.lower()
    assert "either side of it" in prompt, "the question can be on either side"
    assert "never mention the marker" in prompt


def test_the_prompt_is_paid_for_on_every_request():
    """It is sent with every question, so it has a budget. This is not
    tidiness: a paragraph nobody needs is billed again each time somebody asks
    the time."""
    assert len(DEFAULT_SYSTEM_PROMPT) < 1000, len(DEFAULT_SYSTEM_PROMPT)


def test_the_prompt_forbids_being_helpful_at_the_conversation_s_expense():
    """The assistant speaks into a conversation between other people. An extra
    useful fact is not a bonus there, it is an interruption."""
    prompt = DEFAULT_SYSTEM_PROMPT.lower()
    assert "only what was asked" in prompt
    assert "fewest words" in prompt
    for forbidden in ("no caveats", "no second fact", "no offer of"):
        assert forbidden in prompt, forbidden
    # And it must still answer completely, not tersely.
    assert "answer it fully" in prompt


def test_the_marked_utterance_reaches_the_model_verbatim():
    found = parser().find("Something has happened, what is the answer, Computer?")
    messages = build_messages(context_text="[10:00]  earlier line",
                              query=found.command,
                              marked_utterance=found.marked())
    user = messages[-1]["content"]
    assert found.marked() in user
    assert "earlier line" in user


def test_history_comes_before_the_current_question():
    messages = build_messages(
        "ctx", "now", history=[{"role": "user", "content": "before"},
                               {"role": "assistant", "content": "answered"}])
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == "before"


def test_an_empty_command_still_asks_something_sensible():
    messages = build_messages("[10:00]  chatter", "")
    assert "said your name and nothing else" in messages[-1]["content"]
