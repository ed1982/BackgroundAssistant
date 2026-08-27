from bgassist.core.orchestrator import State
from tests.fakes import FailingLlm, FakeClock, RecordingLlm, RecordingTts, make_orchestrator


def test_idle_no_trigger_never_calls_llm():
    clock = FakeClock()
    p = make_orchestrator(clock=clock, texts=["hello there", "how are you"])
    p.feed_utterance(b"a")
    clock.advance(1.0)
    p.tick()
    p.feed_utterance(b"b")
    clock.advance(10.0)
    p.tick()
    assert p.state is State.IDLE
    assert p.llm.calls == []


def test_idle_transcripts_accumulate_in_buffer():
    clock = FakeClock()
    p = make_orchestrator(clock=clock, texts=["alpha", "beta"])
    p.feed_utterance(b"a")
    clock.advance(1.0)
    p.tick()
    p.feed_utterance(b"b")
    text = p.buffer.recent_text()
    assert "alpha" in text and "beta" in text


def test_trigger_with_command_in_one_utterance():
    clock = FakeClock()
    llm = RecordingLlm(response="It is 1400 hours.")
    p = make_orchestrator(clock=clock, texts=["computer what time is it"], llm=llm)
    p.feed_utterance(b"a")
    assert p.state is State.AWAITING_COMMAND

    clock.advance(1.0)  # < 1.5 s deadline
    p.tick()
    assert llm.calls == []

    clock.advance(0.6)  # now past the deadline
    p.tick()
    assert len(llm.calls) == 1
    context, query = llm.calls[0]
    assert "what time is it" in query
    assert "computer what time is it" in context  # utterance went into the buffer
    assert p.tts.spoken == ["It is 1400 hours."]
    assert p.state is State.IDLE


def test_trigger_then_followup_utterance():
    clock = FakeClock()
    llm = RecordingLlm(response="ok")
    p = make_orchestrator(clock=clock, texts=["computer", "what is the status"], llm=llm)
    p.feed_utterance(b"a")  # bare trigger -> empty command, deadline t+1.5
    clock.advance(0.5)
    p.feed_utterance(b"b")  # extends deadline to t+2.0
    clock.advance(1.0)      # t+1.5 < deadline -> no dispatch yet
    p.tick()
    assert llm.calls == []
    clock.advance(0.6)      # t+2.1 > deadline
    p.tick()
    assert len(llm.calls) == 1
    context, query = llm.calls[0]
    assert "what is the status" in query


def test_retrigger_resets_command():
    clock = FakeClock()
    llm = RecordingLlm(response="ok")
    p = make_orchestrator(clock=clock,
                      texts=["computer old question", "computer new question"],
                      llm=llm)
    p.feed_utterance(b"a")
    clock.advance(0.5)
    p.feed_utterance(b"b")  # re-trigger -> command resets to "new question"
    clock.advance(2.0)
    p.tick()
    assert len(llm.calls) == 1
    context, query = llm.calls[0]
    assert "new question" in query and "old question" not in query


def test_max_wait_forces_dispatch():
    clock = FakeClock()
    llm = RecordingLlm(response="ok")
    texts = ["computer"] + [f"word {i}" for i in range(10)]
    p = make_orchestrator(clock=clock, texts=texts, llm=llm)
    p.feed_utterance(b"a")  # trigger at t0
    for _ in range(6):      # utterances every 2 s keep extending the deadline
        clock.advance(2.0)
        p.feed_utterance(b"x")
    # t = t0+12: deadline is t0+13.5, but the 12 s hard cap fires first.
    p.tick()
    assert len(llm.calls) == 1


def test_llm_error_speaks_fallback():
    clock = FakeClock()
    p = make_orchestrator(clock=clock, texts=["computer hello"], llm=FailingLlm())
    p.feed_utterance(b"a")
    clock.advance(2.0)
    p.tick()
    assert len(p.tts.spoken) == 1
    # The spoken fallback now points at the real fix rather than apologising
    # vaguely, which is what made F2 invisible for so long.
    assert "preferences" in p.tts.spoken[0].lower()
    assert p.state is State.IDLE


def test_tts_failure_still_returns_idle():
    clock = FakeClock()
    p = make_orchestrator(clock=clock, texts=["computer hello"],
                      tts=RecordingTts(fail=True))
    p.feed_utterance(b"a")
    clock.advance(2.0)
    p.tick()  # must not raise
    assert p.state is State.IDLE


def test_state_change_callback_sequence():
    clock = FakeClock()
    seen: list[str] = []
    p = make_orchestrator(clock=clock, texts=["computer hi"])
    p.on_state_change = lambda s: seen.append(s.value)
    p.feed_utterance(b"a")
    clock.advance(2.0)
    p.tick()
    assert seen == ["awaiting_command", "thinking", "speaking", "idle"]


def test_reset_returns_to_idle():
    clock = FakeClock()
    p = make_orchestrator(clock=clock, texts=["computer hi"])
    p.feed_utterance(b"a")
    assert p.state is State.AWAITING_COMMAND
    p.reset()
    assert p.state is State.IDLE
    assert p.command_parts == []


def test_empty_transcript_ignored():
    clock = FakeClock()
    p = make_orchestrator(clock=clock, texts=[""])  # transcriber returns ""
    p.feed_utterance(b"a")
    clock.advance(5.0)
    p.tick()
    assert p.state is State.IDLE
    assert len(p.buffer) == 0


# -- push to talk (D10) ---------------------------------------------------

def test_push_to_talk_bypasses_the_trigger_word():
    clock = FakeClock()
    llm = RecordingLlm(response="ok")
    p = make_orchestrator(clock=clock, llm=llm)
    p.begin_push_to_talk()
    p.on_transcript("what is the time in tokyo")
    p.on_transcript("and in berlin")
    p.end_push_to_talk()
    assert len(llm.calls) == 1
    assert llm.calls[0][1] == "what is the time in tokyo and in berlin"


def test_releasing_push_to_talk_with_nothing_said_does_nothing():
    p = make_orchestrator(llm=RecordingLlm())
    p.begin_push_to_talk()
    p.end_push_to_talk()
    assert p.llm.calls == []
    assert p.state is State.IDLE


def test_push_to_talk_speech_still_reaches_the_ambient_buffer():
    p = make_orchestrator(llm=RecordingLlm())
    p.begin_push_to_talk()
    p.on_transcript("a held question")
    assert "a held question" in p.buffer.recent_text()
    p.end_push_to_talk()


# -- F8: transcription failures are surfaced, not swallowed ---------------

def test_a_transient_transcription_failure_is_reported_and_listening_continues():
    from bgassist.core import events
    from bgassist.stt.base import TranscriptionFailed
    from tests.fakes import RaisingTranscriber

    bus = events.RecordingBus()
    p = make_orchestrator(bus=bus)
    p.transcriber = RaisingTranscriber(TranscriptionFailed("bad frame"))
    p.feed_utterance(b"x")
    errors = bus.of(events.ErrorOccurred)
    assert errors and errors[0].fatal is False
    assert p.state is State.IDLE


def test_a_missing_model_is_reported_as_something_to_act_on():
    from bgassist.core import events
    from bgassist.stt.base import ModelUnavailable
    from tests.fakes import RaisingTranscriber

    bus = events.RecordingBus()
    p = make_orchestrator(bus=bus)
    p.transcriber = RaisingTranscriber(ModelUnavailable("model gone"))
    p.feed_utterance(b"x")
    error = bus.of(events.ErrorOccurred)[0]
    assert error.fatal is True
    assert "Preferences" in error.message


def test_an_unexpected_transcriber_error_is_still_not_fatal():
    from tests.fakes import RaisingTranscriber

    p = make_orchestrator()
    p.transcriber = RaisingTranscriber(RuntimeError("something odd"))
    p.feed_utterance(b"x")   # must not raise
    assert p.state is State.IDLE
