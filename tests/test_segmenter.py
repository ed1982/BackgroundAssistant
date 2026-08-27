from starcop.segmenter import UtteranceSegmenter

from tests.fakes import FakeVad

# One 30 ms frame of 16 kHz int16 mono = 960 bytes.
FRAME = b"\x01\x02" * 480


def make_seg(vad, **kw):
    defaults = dict(frame_ms=30, pre_roll_ms=60, end_silence_ms=90,
                    min_utterance_ms=60, max_utterance_ms=300)
    defaults.update(kw)
    return UtteranceSegmenter(vad, **defaults)


def test_no_speech_no_utterance():
    seg = make_seg(FakeVad([False] * 50))
    for _ in range(50):
        assert seg.process_frame(FRAME) is None


def test_utterance_ends_after_silence_and_includes_pre_roll():
    # frame0 unvoiced (pre-roll), frames1-2 voiced, then silence.
    vad = FakeVad([False, True, True] + [False] * 10)
    seg = make_seg(vad)
    results = [seg.process_frame(FRAME) for _ in range(13)]
    utterances = [r for r in results if r is not None]
    assert len(utterances) == 1
    # buf = pre-roll (1 frame) + 2 voiced + 3 silence frames = 6 frames
    assert len(utterances[0]) == 6 * len(FRAME)


def test_short_blip_dropped():
    # 1 voiced frame + 3 endpointing silence frames = 4 frames < min (6).
    vad = FakeVad([True] + [False] * 10)
    seg = make_seg(vad, min_utterance_ms=180)  # 6 frames
    results = [seg.process_frame(FRAME) for _ in range(11)]
    assert all(r is None for r in results)


def test_max_length_flush():
    vad = FakeVad([True] * 100)
    seg = make_seg(vad, max_utterance_ms=300)  # 10 frames
    results = []
    for _ in range(30):
        r = seg.process_frame(FRAME)
        if r is not None:
            results.append(r)
    # Continuous speech over the cap gets chunked; the first flush fires
    # exactly at the cap.
    assert len(results) >= 1
    assert len(results[0]) == 10 * len(FRAME)


def test_reset_discards_in_progress():
    vad = FakeVad([True] * 50)
    seg = make_seg(vad, max_utterance_ms=10_000)
    for _ in range(5):
        seg.process_frame(FRAME)
    seg.reset()
    # After reset the next voiced frame starts a fresh utterance.
    results = [seg.process_frame(FRAME) for _ in range(50)]
    assert all(r is None for r in results)  # still below max length


def test_flush_returns_in_progress_utterance():
    vad = FakeVad([True] * 10)
    seg = make_seg(vad, max_utterance_ms=10_000)
    for _ in range(5):
        assert seg.process_frame(FRAME) is None  # no silence -> never ends
    tail = seg.flush()
    assert tail is not None and len(tail) == 5 * len(FRAME)
    assert seg.flush() is None  # nothing left


def test_flush_drops_short_blip():
    vad = FakeVad([True] * 10)
    seg = make_seg(vad, min_utterance_ms=180)  # 6 frames
    for _ in range(2):
        seg.process_frame(FRAME)
    assert seg.flush() is None


def test_invalid_frame_ms():
    import pytest

    with pytest.raises(ValueError):
        UtteranceSegmenter(FakeVad([True]), frame_ms=15)
