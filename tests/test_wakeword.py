import pytest

from starcop.wakeword import WakeWordMatcher, normalize_text


def test_normalize():
    assert normalize_text("Hey, COMPUTER!!") == "hey computer"
    assert normalize_text("") == ""


def test_match_case_and_punctuation():
    m = WakeWordMatcher(["computer"])
    assert m.match("Hey, Computer! What time is it?") == "computer"


def test_match_is_word_boundary():
    m = WakeWordMatcher(["computer"])
    assert m.match("computers are great") is None
    assert m.match("my supercomputer hums") is None


def test_match_substring_word_still_wakes():
    # Documented trade-off: any utterance containing the trigger word wakes.
    m = WakeWordMatcher(["computer"])
    assert m.match("the computer is broken") == "computer"


def test_aliases():
    m = WakeWordMatcher(["computer", "compy"])
    assert m.match("hey compy") == "compy"
    assert m.match("hello computer") == "computer"


def test_split_command():
    m = WakeWordMatcher(["computer"])
    assert m.split_command("hey computer what time is it") == (
        "computer", "what time is it")
    assert m.split_command("Computer!") == ("computer", "")
    assert m.split_command("hello there") is None


def test_requires_nonempty_triggers():
    with pytest.raises(ValueError):
        WakeWordMatcher([])
    with pytest.raises(ValueError):
        WakeWordMatcher(["", "   "])
