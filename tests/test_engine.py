"""Engine lifecycle, backpressure and the F1/F7 regressions."""
import threading
import time

import pytest

from bgassist.audio.ring import AudioRing, BoundedFrameQueue
from bgassist.audio.spotter import ScriptedSpotter
from bgassist.core.orchestrator import State
from bgassist.core.segmenter import UtteranceSegmenter
from bgassist.engine import Engine
from tests.fakes import FakeVad, make_orchestrator

FRAME = b"\x01\x02" * 480


class FakeCapture:
    def __init__(self, queue_frames=200):
        self.queue = BoundedFrameQueue(maxlen=queue_frames)
        self.ring = AudioRing(seconds=8.0)
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def feed(self, frame=FRAME, count=1):
        for _ in range(count):
            self.queue.put(frame)
            self.ring.add(frame)


def build(texts=None, vad_script=(True,) * 4 + (False,) * 6, spotter=None,
          **kwargs):
    orchestrator = make_orchestrator(texts=None, **kwargs)
    segmenter = UtteranceSegmenter(FakeVad(list(vad_script)), frame_ms=30,
                                   pre_roll_ms=60, end_silence_ms=90,
                                   min_utterance_ms=60, max_utterance_ms=3000)

    class Transcriber:
        name = "fake"

        def __init__(self):
            self.texts = list(texts or [])
            self.calls = 0

        def transcribe(self, audio):
            self.calls += 1
            return self.texts.pop(0) if self.texts else ""

    capture = FakeCapture()
    engine = Engine(capture, segmenter, Transcriber(), orchestrator,
                    spotter=spotter, poll_timeout=0.02)
    return engine, capture, orchestrator


# -- F1: Stop and Quit must not raise ------------------------------------

def test_start_stop_ten_times_without_exception():
    """The F1 regression: the old Runner shadowed Thread._stop and every
    stop() raised TypeError: 'Event' object is not callable."""
    engine, _capture, _orch = build()
    for _ in range(10):
        engine.start()
        assert engine.running
        engine.stop(timeout=2.0)
        assert not engine.running


def test_stop_is_idempotent():
    engine, _capture, _orch = build()
    engine.start()
    engine.stop(timeout=2.0)
    engine.stop(timeout=2.0)


def test_engine_has_no_attribute_shadowing_a_thread_internal():
    engine, _c, _o = build()
    assert hasattr(engine, "_stop_event")
    assert not isinstance(getattr(engine, "_stop", None), threading.Event)


# -- end to end through the threads --------------------------------------

def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_audio_flows_through_to_a_dispatch():
    engine, capture, orchestrator = build(texts=["computer what time is it"])
    engine.start()
    try:
        capture.feed(count=12)
        assert _wait_for(lambda: orchestrator.state is not State.IDLE
                         or orchestrator.llm.calls)
        # The command-end deadline is driven by the orchestrator's clock.
        orchestrator.clock.advance(3.0)
        assert _wait_for(lambda: bool(orchestrator.llm.calls))
    finally:
        engine.stop(timeout=2.0)
    assert "what time is it" in orchestrator.llm.calls[0][1]


# -- F7: the segmenter is reset when audio is dropped --------------------

def test_segmenter_is_reset_while_thinking_or_speaking():
    engine, capture, orchestrator = build()
    orchestrator._set_state(State.SPEAKING)
    engine.segmenter._buf = bytearray(FRAME * 3)
    engine.segmenter._frames = 3
    engine.start()
    try:
        capture.feed(count=3)
        assert _wait_for(lambda: engine.segmenter._buf is None)
    finally:
        orchestrator._set_state(State.IDLE)
        engine.stop(timeout=2.0)


def test_tick_still_runs_while_frames_are_being_dropped():
    """The old code `continue`d past tick() on the drop path, so deadlines
    only advanced when the queue happened to be empty."""
    engine, capture, orchestrator = build()
    ticks = []
    orchestrator.tick = lambda now=None: ticks.append(1)
    orchestrator.state = State.SPEAKING
    engine.start()
    try:
        capture.feed(count=5)
        assert _wait_for(lambda: len(ticks) > 3)
    finally:
        engine.stop(timeout=2.0)


# -- §4.3 backpressure ----------------------------------------------------

def test_frame_queue_drops_oldest_and_counts():
    q = BoundedFrameQueue(maxlen=3, log_interval_s=0)
    for i in range(10):
        q.put(bytes([i]))
    assert len(q) == 3
    assert q.dropped == 7
    assert q.get(timeout=0.1) == bytes([7])  # oldest survivor, not the newest


def test_frame_queue_flood_keeps_memory_bounded():
    q = BoundedFrameQueue(maxlen=100)
    for _ in range(100_000):
        q.put(FRAME)
    assert len(q) == 100


def test_utterance_queue_drops_oldest_and_publishes_an_event():
    from bgassist.core import events

    bus = events.RecordingBus()
    engine, _capture, orchestrator = build(bus=bus)
    engine.bus = bus
    for i in range(12):
        engine._offer(bytes([i]))
    assert engine.dropped_utterances > 0
    assert bus.of(events.AudioBacklog)


def test_ring_buffer_keeps_only_the_tail():
    ring = AudioRing(seconds=0.3, frame_ms=30)  # 10 frames
    for i in range(50):
        ring.add(bytes([i]) * 960)
    assert len(ring) == 10
    tail = ring.tail(0.09)  # 3 frames
    assert len(tail) == 3 * 960
    assert tail[0] == 47


# -- the spotter ----------------------------------------------------------

def test_spotter_hit_reaches_the_orchestrator():
    from bgassist.core import events

    bus = events.RecordingBus()
    engine, capture, orchestrator = build(spotter=ScriptedSpotter(hit_frames={0}),
                                          bus=bus)
    engine.start()
    try:
        capture.feed(count=2)
        assert _wait_for(lambda: bool(bus.of(events.TriggerSpotted)))
    finally:
        engine.stop(timeout=2.0)


def test_a_broken_spotter_is_disabled_not_fatal():
    class Boom:
        def process_frame(self, frame):
            raise RuntimeError("onnx exploded")

    engine, capture, _orch = build(spotter=Boom())
    engine.start()
    try:
        capture.feed(count=3)
        assert _wait_for(lambda: engine.spotter is None)
    finally:
        engine.stop(timeout=2.0)


# -- retro-transcription --------------------------------------------------

def test_retro_transcribe_reads_the_ring_buffer():
    engine, capture, _orch = build(texts=["what did you mean computer"])
    capture.feed(count=20)
    assert engine.retro_transcribe(0.3) == "what did you mean computer"


def test_retro_transcribe_is_empty_without_audio():
    engine, _capture, _orch = build()
    assert engine.retro_transcribe(1.0) == ""
