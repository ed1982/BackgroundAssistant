from starcop.pipeline import State

from tests.fakes import FakeClock, FailingLlm, RecordingLlm, RecordingTts, make_pipeline


def test_idle_no_trigger_never_calls_llm():
    clock = FakeClock()
    p = make_pipeline(clock=clock, texts=["hello there", "how are you"])
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
    p = make_pipeline(clock=clock, texts=["alpha", "beta"])
    p.feed_utterance(b"a")
    clock.advance(1.0)
    p.tick()
    p.feed_utterance(b"b")
    text = p.buffer.recent_text()
    assert "alpha" in text and "beta" in text


def test_trigger_with_command_in_one_utterance():
    clock = FakeClock()
    llm = RecordingLlm(response="It is 1400 hours.")
    p = make_pipeline(clock=clock, texts=["computer what time is it"], llm=llm)
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
    p = make_pipeline(clock=clock, texts=["computer", "what is the status"], llm=llm)
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
    p = make_pipeline(clock=clock,
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
    p = make_pipeline(clock=clock, texts=texts, llm=llm)
    p.feed_utterance(b"a")  # trigger at t0
    for _ in range(6):      # utterances every 2 s keep extending the deadline
        clock.advance(2.0)
        p.feed_utterance(b"x")
    # t = t0+12: deadline is t0+13.5, but the 12 s hard cap fires first.
    p.tick()
    assert len(llm.calls) == 1


def test_llm_error_speaks_fallback():
    clock = FakeClock()
    p = make_pipeline(clock=clock, texts=["computer hello"], llm=FailingLlm())
    p.feed_utterance(b"a")
    clock.advance(2.0)
    p.tick()
    assert len(p.tts.spoken) == 1
    assert "sorry" in p.tts.spoken[0].lower()
    assert p.state is State.IDLE


def test_tts_failure_still_returns_idle():
    clock = FakeClock()
    p = make_pipeline(clock=clock, texts=["computer hello"],
                      tts=RecordingTts(fail=True))
    p.feed_utterance(b"a")
    clock.advance(2.0)
    p.tick()  # must not raise
    assert p.state is State.IDLE


def test_state_change_callback_sequence():
    clock = FakeClock()
    seen: list[str] = []
    p = make_pipeline(clock=clock, texts=["computer hi"])
    p.on_state_change = lambda s: seen.append(s.value)
    p.feed_utterance(b"a")
    clock.advance(2.0)
    p.tick()
    assert seen == ["awaiting_command", "thinking", "speaking", "idle"]


def test_reset_returns_to_idle():
    clock = FakeClock()
    p = make_pipeline(clock=clock, texts=["computer hi"])
    p.feed_utterance(b"a")
    assert p.state is State.AWAITING_COMMAND
    p.reset()
    assert p.state is State.IDLE
    assert p.command_parts == []


def test_empty_transcript_ignored():
    clock = FakeClock()
    p = make_pipeline(clock=clock, texts=[""])  # transcriber returns ""
    p.feed_utterance(b"a")
    clock.advance(5.0)
    p.tick()
    assert p.state is State.IDLE
    assert len(p.buffer) == 0
