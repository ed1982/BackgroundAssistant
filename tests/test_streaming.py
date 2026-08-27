"""Streaming, sentence chunking and cancellation (§5.2, §5.5)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bgassist.core.responder import AnswerRequest, SpeechResponder
from bgassist.llm.base import LLMCancelled
from bgassist.llm.openai import OpenAICompatibleBackend
from bgassist.tts.chunker import sentence_chunks, split_sentences
from tests.fakes import RecordingLlm, RecordingTts, StreamingLlm


# -- chunking -------------------------------------------------------------

def test_sentences_are_emitted_as_they_complete():
    tokens = ["The warp core ", "is stable. ", "Output is at ninety ",
              "per cent. ", "No further action."]
    assert list(sentence_chunks(tokens)) == [
        "The warp core is stable. ",
        "Output is at ninety per cent. ",
        "No further action.",
    ]


def test_abbreviations_do_not_split_a_sentence():
    assert split_sentences("Ask Dr. Crusher. She knows.") == [
        "Ask Dr. Crusher. ", "She knows."]


def test_very_short_sentences_are_merged():
    """A stray "Yes." should not become its own utterance with a gap after it."""
    chunks = list(sentence_chunks(["Yes. ", "Here is the longer explanation "
                                   "that follows it. "]))
    assert chunks[0].startswith("Yes. Here is")


def test_a_single_sentence_is_spoken_verbatim():
    assert list(sentence_chunks(["It is 1400 hours."])) == ["It is 1400 hours."]


# -- SSE parsing ----------------------------------------------------------

class _SseHandler(BaseHTTPRequestHandler):
    chunks = ["Hello ", "there. ", "All is well."]

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.last_body = json.loads(self.rfile.read(length) or b"{}")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in self.chunks:
            payload = {"choices": [{"delta": {"content": chunk}}]}
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args):
        pass


@pytest.fixture()
def sse_server():
    srv = HTTPServer(("127.0.0.1", 0), _SseHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def test_openai_streaming_yields_content_deltas(sse_server):
    backend = OpenAICompatibleBackend(
        base_url=f"http://127.0.0.1:{sse_server.server_address[1]}/v1",
        model="gpt-x", api_key="k", timeout_s=5)
    assert list(backend.stream("", "hi")) == ["Hello ", "there. ", "All is well."]
    assert sse_server.last_body["stream"] is True


def test_streaming_stops_when_the_cancel_token_is_set(sse_server):
    backend = OpenAICompatibleBackend(
        base_url=f"http://127.0.0.1:{sse_server.server_address[1]}/v1",
        model="gpt-x", timeout_s=5)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(LLMCancelled):
        list(backend.stream("", "hi", cancel=cancel))


# -- the responder --------------------------------------------------------

def test_responder_speaks_sentence_by_sentence():
    tts = RecordingTts()
    responder = SpeechResponder(StreamingLlm(), tts)
    result = responder.submit(AnswerRequest(query="hello"))
    assert tts.spoken == ["First sentence. ", "Second sentence. ",
                          "Third sentence."]
    assert result.spoken_upto == len(result.text)
    assert not result.interrupted


def test_responder_works_with_a_non_streaming_backend():
    tts = RecordingTts()
    responder = SpeechResponder(RecordingLlm("All systems nominal."), tts)
    responder.submit(AnswerRequest(query="status"))
    assert tts.spoken == ["All systems nominal."]


def test_cancel_mid_sentence_does_not_credit_that_sentence_as_heard():
    """Granularity is one sentence: a chunk that was killed part-way through
    does not count towards spoken_upto (§5.4.1)."""
    import time

    llm = StreamingLlm()
    tts = RecordingTts(block=True)  # the first speak() blocks until stop()
    responder = SpeechResponder(llm, tts, threaded=True)
    responder.submit(AnswerRequest(query="hello"))
    deadline = time.monotonic() + 3
    while not tts.spoken and time.monotonic() < deadline:
        time.sleep(0.005)
    assert tts.spoken, "speech never started"
    responder.cancel()
    assert responder.wait(3)
    result = responder.last_result
    assert result.interrupted
    assert result.spoken_upto == 0
    assert result.text.startswith("First sentence.")
    assert tts.stopped >= 1


def test_cancel_after_a_sentence_keeps_that_sentence_as_the_prefix():
    """The other half of the same rule: sentences that finished were heard."""
    import time

    class TwoStepTts(RecordingTts):
        def __init__(self):
            super().__init__()
            self.second = threading.Event()

        def speak(self, text):
            self.spoken.append(text)
            if len(self.spoken) >= 2:
                self.second.set()
                self._release.wait(timeout=5)
                self._release.clear()

    tts = TwoStepTts()
    responder = SpeechResponder(StreamingLlm(), tts, threaded=True)
    responder.submit(AnswerRequest(query="hello"))
    assert tts.second.wait(3)
    responder.cancel()
    assert responder.wait(3)
    result = responder.last_result
    assert result.interrupted
    assert result.spoken_upto == len("First sentence. ")
    assert result.spoke_anything


def test_a_second_submit_cancels_the_first():
    llm = StreamingLlm()
    responder = SpeechResponder(llm, RecordingTts(), threaded=True)
    responder.submit(AnswerRequest(query="one"))
    responder.wait(3)
    responder.submit(AnswerRequest(query="two"))
    responder.wait(3)
    assert len(llm.calls) == 2


def test_provider_errors_are_spoken_usefully_not_vaguely():
    from bgassist.core.responder import ERROR_SPEECH
    from tests.fakes import FailingLlm

    tts = RecordingTts()
    responder = SpeechResponder(FailingLlm(), tts)
    result = responder.submit(AnswerRequest(query="hello"))
    assert result.error
    assert tts.spoken == [ERROR_SPEECH]


def test_tts_failure_does_not_stop_the_answer():
    tts = RecordingTts(fail=True)
    responder = SpeechResponder(StreamingLlm(), tts)
    result = responder.submit(AnswerRequest(query="hello"))
    assert result.text
    assert result.spoken_upto == 0  # nothing was actually heard


def test_speak_false_still_produces_text():
    tts = RecordingTts()
    responder = SpeechResponder(StreamingLlm(), tts)
    result = responder.submit(AnswerRequest(query="hello", speak=False))
    assert tts.spoken == []
    assert result.text.startswith("First sentence.")
