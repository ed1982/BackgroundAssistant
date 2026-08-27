"""Trigger grammar: an exhaustive table over phrasing x sensitivity (§11)."""
import pytest

from bgassist.core.trigger import (TRIGGER_MARKER, Sensitivity, TriggerParser,
                                   TriggerPosition, normalize_text)


def parser(sensitivity=Sensitivity.BALANCED, words=("computer",)):
    return TriggerParser(list(words), sensitivity=sensitivity)


# -- classification ------------------------------------------------------

CASES = [
    # text, expected position
    ("Computer, what is the answer?", TriggerPosition.LEADING),
    ("computer what time is it", TriggerPosition.LEADING),
    ("Hey computer, what is the answer?", TriggerPosition.LEADING),
    ("Something has happened, Computer, what is the answer?", TriggerPosition.MEDIAL),
    ("Something has happened, what is the answer, Computer?", TriggerPosition.TRAILING),
    ("what is the answer computer", TriggerPosition.TRAILING),
    ("tell me the time, computer, please", TriggerPosition.TRAILING),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_position_classification(text, expected):
    found = parser().find(text)
    assert found is not None
    assert found.position is expected


def test_trailing_dispatches_immediately():
    found = parser().find("what is the current status, computer")
    assert found.position is TriggerPosition.TRAILING
    assert found.dispatch_now is True


def test_leading_waits():
    found = parser().find("computer, what is the current status")
    assert found.dispatch_now is False


def test_bare_trigger_waits_for_the_question():
    found = parser().find("Computer.")
    assert found.position is TriggerPosition.LEADING
    assert found.dispatch_now is False


def test_trigger_twice_uses_the_first_and_still_classifies():
    found = parser().find("computer, computer, what time is it")
    assert found is not None
    assert found.position is TriggerPosition.LEADING


def test_filler_after_trigger_is_not_a_command():
    found = parser().find("what is the time, computer, please")
    assert found.position is TriggerPosition.TRAILING


# -- sensitivity ---------------------------------------------------------

FALSE_POSITIVE = "I told him my computer is broken and he laughed about it"
GENUINE_MEDIAL = "so much has happened, computer, what is the answer"


def test_relaxed_wakes_on_anything():
    assert parser(Sensitivity.RELAXED).parse(FALSE_POSITIVE) is not None


def test_balanced_ignores_mid_sentence_mentions():
    assert parser(Sensitivity.BALANCED).parse(FALSE_POSITIVE) is None


def test_balanced_accepts_a_clause_boundary_trigger():
    assert parser(Sensitivity.BALANCED).parse(GENUINE_MEDIAL) is not None


def test_strict_ignores_all_medial_triggers():
    assert parser(Sensitivity.STRICT).parse(GENUINE_MEDIAL) is None
    assert parser(Sensitivity.STRICT).parse("computer, what time is it") is not None
    assert parser(Sensitivity.STRICT).parse("what time is it, computer") is not None


@pytest.mark.parametrize("sensitivity", list(Sensitivity))
def test_leading_and_trailing_always_accepted(sensitivity):
    p = parser(sensitivity)
    assert p.parse("computer, what is the answer") is not None
    assert p.parse("what is the answer, computer") is not None


# -- word boundaries and aliases -----------------------------------------

def test_word_boundary():
    p = parser()
    assert p.match("computers are great") is None
    assert p.match("my supercomputer hums") is None


def test_aliases():
    p = parser(words=("computer", "compy"))
    assert p.match("hey compy") == "compy"
    assert p.match("hello computer") == "computer"


def test_requires_nonempty_triggers():
    with pytest.raises(ValueError):
        TriggerParser([])
    with pytest.raises(ValueError):
        TriggerParser(["", "   "])


# -- marking and command extraction --------------------------------------

def test_marked_text_replaces_the_trigger():
    found = parser().find("what is the answer, Computer?")
    assert TRIGGER_MARKER in found.marked()
    assert "computer" not in found.marked().lower()


def test_command_for_leading_and_trailing():
    assert parser().find("computer, what time is it").command == "what time is it"
    assert parser().find("what time is it, computer").command == "what time is it"


def test_easter_egg_flag():
    assert parser().is_easter_egg is True
    assert parser(words=("jarvis",)).is_easter_egg is False


def test_normalize():
    assert normalize_text("Hey, COMPUTER!!") == "hey computer"
    assert normalize_text("") == ""


# -- the F15 cases that must stay quiet -----------------------------------

QUIET = [
    "my computer is broken and he laughed",
    "the computer says no",
    "I told him my computer is broken",
    "this computer needs more memory",
    "her computer crashed again yesterday",
]


@pytest.mark.parametrize("text", QUIET)
def test_ordinary_mentions_do_not_wake_at_the_default_sensitivity(text):
    assert parser().parse(text) is None


ADDRESSED = [
    "computer, what is the time",
    "hey computer, what is the time",
    "so, computer, what is the time",
    "what is the time, computer",
    "um, computer, what is the time",
]


@pytest.mark.parametrize("text", ADDRESSED)
def test_being_addressed_always_wakes(text):
    assert parser().parse(text) is not None
