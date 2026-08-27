"""The truncation record and barge-in (§5.4, D12a).

The rule under test throughout: **the model's picture of the exchange must
match the user's picture of the exchange.** What was spoken is what is stored
and replayed; what was generated but never heard is not.
"""
import time

import pytest

from bgassist.core.echo import subtract_playback
from bgassist.core.orchestrator import State
from bgassist.llm.prompts import INTERRUPTED_SUFFIX
from bgassist.storage import ConversationStore, NullCipher
from tests.fakes import RecordingTts, StreamingLlm, make_orchestrator


@pytest.fixture()
def store(tmp_path):
    s = ConversationStore(tmp_path / "c.db", NullCipher())
    yield s
    s.close()


def _dispatch(orchestrator, text="computer what is x"):
    orchestrator.on_transcript(text)
    orchestrator.clock.advance(3.0)
    orchestrator.tick()


# -- history carries the prefix, never the full text ----------------------

def test_history_sends_the_spoken_prefix_with_an_interrupted_marker(store):
    conversation = store.create_conversation()
    store.add_message(conversation, "user", "Find out X please")
    store.add_message(conversation, "assistant", "X is defined as a long answer",
                      spoken_upto=len("X is defined as"), interrupted=True)
    store.add_message(conversation, "user", "I'm sorry I meant Y")
    history = store.history(conversation)
    assistant = [t for t in history if t["role"] == "assistant"][0]
    assert assistant["content"] == "X is defined as" + INTERRUPTED_SUFFIX
    assert "long answer" not in assistant["content"]


def test_uninterrupted_answers_are_replayed_whole(store):
    conversation = store.create_conversation()
    store.add_message(conversation, "user", "status")
    store.add_message(conversation, "assistant", "All systems nominal.",
                      spoken_upto=len("All systems nominal."))
    assert store.history(conversation)[-1]["content"] == "All systems nominal."


def test_superseded_questions_are_never_replayed(store):
    conversation = store.create_conversation()
    store.add_message(conversation, "user", "a question never answered",
                      superseded=True)
    store.add_message(conversation, "user", "the real question")
    assert [t["content"] for t in store.history(conversation)] == \
        ["the real question"]


def test_repeated_interruptions_stay_coherent(store):
    conversation = store.create_conversation()
    for i in range(3):
        store.add_message(conversation, "user", f"question {i}")
        store.add_message(conversation, "assistant", f"answer {i} continues on",
                          spoken_upto=len(f"answer {i}"), interrupted=True)
    history = store.history(conversation)
    assert len(history) == 6
    assert all(t["content"].endswith(INTERRUPTED_SUFFIX)
               for t in history if t["role"] == "assistant")
    assert "continues on" not in " ".join(t["content"] for t in history)


# -- the orchestrator writes the record -----------------------------------

def test_interrupting_during_thinking_stores_no_assistant_turn(store):
    """Cut off before a word was said: there is no assistant turn, and the
    question must not be replayed as though it had been answered (§5.4.4)."""
    llm = StreamingLlm()
    tts = RecordingTts(block=True)
    orchestrator = make_orchestrator(llm=llm, tts=tts, conversations=store,
                                     threaded=True)
    _dispatch(orchestrator)
    deadline = time.monotonic() + 3
    while not tts.spoken and time.monotonic() < deadline:
        time.sleep(0.005)
    orchestrator.interrupt(source="test")
    assert orchestrator.responder.wait(3)
    conversation = store.list_conversations()[0]
    messages = store.messages(conversation.id)
    assert [m.role for m in messages] == ["user"]
    assert messages[0].superseded is True
    assert store.history(conversation.id) == []


def test_a_completed_answer_is_stored_whole(store):
    orchestrator = make_orchestrator(llm=StreamingLlm(), conversations=store)
    _dispatch(orchestrator)
    conversation = store.list_conversations()[0]
    roles = [m.role for m in store.messages(conversation.id)]
    assert roles == ["user", "assistant"]
    assistant = store.messages(conversation.id)[1]
    assert assistant.interrupted is False
    assert assistant.spoken_upto == len(assistant.text)


def test_the_context_snapshot_that_was_sent_is_kept(store):
    orchestrator = make_orchestrator(llm=StreamingLlm(), conversations=store)
    orchestrator.on_transcript("the warp core is making a noise")
    _dispatch(orchestrator, "computer what is that")
    user = store.messages(store.list_conversations()[0].id)[0]
    assert "warp core is making a noise" in user.context


def test_unspoken_remainder_is_available_for_the_disclosure(store):
    conversation = store.create_conversation()
    message = store.add_message(conversation, "assistant",
                                "Spoken part. Unspoken part.",
                                spoken_upto=len("Spoken part."), interrupted=True)
    stored = store.messages(conversation)[0]
    assert stored.spoken_text == "Spoken part."
    assert stored.unspoken_text.strip() == "Unspoken part."
    assert message.id == stored.id


# -- barge-in state machine ----------------------------------------------

def test_the_trigger_word_while_speaking_interrupts():
    tts = RecordingTts(block=True)
    orchestrator = make_orchestrator(llm=StreamingLlm(), tts=tts, threaded=True)
    _dispatch(orchestrator)
    deadline = time.monotonic() + 3
    while orchestrator.state is not State.SPEAKING and time.monotonic() < deadline:
        time.sleep(0.005)
    orchestrator.on_transcript("computer stop that and tell me the time")
    assert orchestrator.responder.wait(3)
    assert tts.stopped >= 1


def test_we_ignore_the_spotter_when_we_are_the_one_saying_the_word():
    """Self-echo suppression by content (§5.4.2, layer 2)."""
    orchestrator = make_orchestrator(llm=StreamingLlm(), threaded=True)
    orchestrator._set_state(State.SPEAKING)
    orchestrator.responder.current_chunk = "The computer is on deck five."
    cancelled = []
    orchestrator.responder.cancel = lambda *a, **k: cancelled.append(1)
    orchestrator.on_spotter_trigger(confidence=0.9)
    assert cancelled == []


def test_the_spotter_does_interrupt_when_we_are_not_saying_it():
    orchestrator = make_orchestrator(llm=StreamingLlm(), threaded=True)
    orchestrator._set_state(State.SPEAKING)
    orchestrator.responder.current_chunk = "Deck five is quiet."
    cancelled = []
    orchestrator.responder.cancel = lambda *a, **k: cancelled.append(1)
    orchestrator.on_spotter_trigger(confidence=0.9)
    assert cancelled == [1]


def test_barge_in_can_be_turned_off():
    orchestrator = make_orchestrator(llm=StreamingLlm(), threaded=True,
                                     barge_in=False)
    orchestrator._set_state(State.SPEAKING)
    cancelled = []
    orchestrator.responder.cancel = lambda *a, **k: cancelled.append(1)
    orchestrator.on_transcript("computer stop")
    assert cancelled == []


# -- retro-transcription --------------------------------------------------

def test_retro_transcription_recovers_the_words_before_the_trigger():
    """"…what did you mean, Computer?" must keep its question (§5.4.1)."""
    heard = "the warp core is stable what did you mean computer"
    orchestrator = make_orchestrator(
        llm=StreamingLlm(), threaded=True,
        retro_transcribe=lambda seconds: heard)
    orchestrator._set_state(State.SPEAKING)
    orchestrator.responder.current_chunk = "the warp core is stable"
    orchestrator.responder.cancel = lambda *a, **k: None
    seen = []
    orchestrator.on_transcript = lambda text, ts=None: seen.append(text)
    orchestrator.interrupt(source="spotter")
    assert seen == ["what did you mean computer"]


def test_subtracting_our_own_playback():
    heard = "All systems are nominal captain computer what is the time"
    spoken = "All systems are nominal, captain."
    assert subtract_playback(heard, spoken) == "computer what is the time"


def test_subtraction_leaves_unrelated_speech_alone():
    assert subtract_playback("computer what is the time",
                             "Deck five is quiet.") == "computer what is the time"


def test_subtraction_survives_a_misrecognised_word():
    heard = "all systems are normal captain computer stop"
    spoken = "All systems are nominal, captain."
    assert subtract_playback(heard, spoken) == "all systems are normal captain computer stop" \
        or "computer stop" in subtract_playback(heard, spoken)
