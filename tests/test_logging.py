"""Nothing you say reaches the disk (F3), and it is written once (F13).

The old default was ``log_level: INFO`` with ``log.info("heard: %r", text)``
in the pipeline and no rotation, so 131 KB of verbatim private conversation
accumulated in the project folder. This is the test that keeps that from
coming back.
"""
import logging

import pytest

from bgassist.logging_setup import (
    RedactingFilter,
    redacting_filter,
    setup_logging,
    transcript_log,
)
from tests.fakes import FakeClock, RecordingLlm, RecordingTts, make_orchestrator

PRIVATE = "the biopsy result was benign and the argument was about money"


def _flush():
    for handler in logging.getLogger().handlers:
        handler.flush()


@pytest.fixture()
def log_path(tmp_path):
    path = tmp_path / "logs" / "app.log"
    setup_logging("DEBUG", log_file=path, console=False)
    yield path
    logging.getLogger().handlers = []


def test_a_full_session_writes_no_transcript_to_the_log(log_path):
    """The §11 redaction test: run a whole mock session, then read the log."""
    orchestrator = make_orchestrator(
        texts=[PRIVATE, f"computer {PRIVATE}"],
        llm=RecordingLlm("Understood."), tts=RecordingTts())
    orchestrator.feed_utterance(b"ambient")
    orchestrator.feed_utterance(b"triggered")
    orchestrator.clock.advance(3.0)
    orchestrator.tick()
    assert orchestrator.llm.calls, "the session did not actually run"

    _flush()
    written = log_path.read_text(encoding="utf-8")
    assert written, "nothing was logged at all, so this proves nothing"
    for word in ("biopsy", "benign", "argument", "money"):
        assert word not in written


def test_transcript_records_are_dropped_by_default(log_path):
    transcript_log("heard: %r", PRIVATE)
    _flush()
    assert "biopsy" not in log_path.read_text()


def test_the_debug_toggle_lets_transcripts_through(log_path):
    redacting_filter().allow_transcripts(seconds=60)
    try:
        transcript_log("heard: %r", PRIVATE)
        _flush()
        assert "biopsy" in log_path.read_text()
    finally:
        redacting_filter().deny_transcripts()


def test_the_debug_toggle_switches_itself_off_after_24_hours():
    clock = FakeClock(start=0.0)
    f = RedactingFilter(clock=clock)
    f.allow_transcripts()
    assert f.transcripts_allowed
    clock.advance(24 * 3600 - 1)
    assert f.transcripts_allowed
    clock.advance(2)
    assert not f.transcripts_allowed


def test_api_keys_are_scrubbed_from_any_record(log_path):
    logging.getLogger("bgassist.test").error(
        "request failed with key sk-abcdefghijklmnopqrstuvwxyz")
    _flush()
    written = log_path.read_text()
    assert "sk-abcdefghijklmnop" not in written
    assert "[redacted]" in written


def test_bearer_tokens_are_scrubbed_too(log_path):
    logging.getLogger("bgassist.test").error(
        "headers: Bearer abcdef1234567890")
    _flush()
    assert "abcdef1234567890" not in log_path.read_text()


def _our_handlers():
    """Root handlers we installed (pytest adds capture handlers of its own)."""
    import logging.handlers

    return [h for h in logging.getLogger().handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
            or type(h) is logging.StreamHandler]


def test_the_log_rotates_and_is_written_once(log_path):
    handlers = _our_handlers()
    assert len(handlers) == 1, "console + file would write every line twice (F13)"
    handler = handlers[0]
    assert handler.maxBytes == 1024 * 1024
    assert handler.backupCount == 3


def test_the_log_file_is_private(log_path):
    logging.getLogger("bgassist.test").info("hello")
    assert oct(log_path.stat().st_mode)[-3:] == "600"


def test_setup_is_idempotent(tmp_path):
    path = tmp_path / "a.log"
    setup_logging("INFO", log_file=path, console=False)
    setup_logging("INFO", log_file=path, console=False)
    assert len(_our_handlers()) == 1
    logging.getLogger().handlers = []
