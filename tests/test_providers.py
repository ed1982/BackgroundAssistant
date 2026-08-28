"""Provider backends: Claude, local servers, discovery, and error messages.

The error messages get their own tests because the vaguest possible one is
what made F2 invisible: five separate 401s, each spoken as "I'm sorry, I could
not process that."
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bgassist.llm import PRESETS, make_llm
from bgassist.llm.anthropic import AnthropicBackend
from bgassist.llm.base import LLMError
from bgassist.llm.local import KNOWN_PORTS, OllamaBackend, detect_local_servers


class _Handler(BaseHTTPRequestHandler):
    status = 200
    payload = None
    stream_events = None

    def _send(self, body: bytes, content_type="application/json"):
        self.send_response(self.status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.last_path = self.path
        body = json.dumps(self.payload or {"data": [{"id": "a-model"}]}).encode()
        self._send(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.last = {
            "path": self.path,
            "body": json.loads(self.rfile.read(length) or b"{}"),
            "headers": {k.lower(): v for k, v in self.headers.items()},
        }
        if self.stream_events is not None:
            self.send_response(self.status)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event in self.stream_events:
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._send(json.dumps(self.payload or {}).encode())

    def log_message(self, *args):
        pass

    def handle_error(self, *args):
        # Cancelling a stream closes the socket mid-response, which is the
        # point of the test; the server need not complain about it.
        pass


@pytest.fixture()
def server():
    _Handler.status = 200
    _Handler.payload = None
    _Handler.stream_events = None
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()


# -- Claude ---------------------------------------------------------------

def test_anthropic_uses_its_own_shape(server):
    _Handler.payload = {"content": [{"type": "text", "text": "Understood."}]}
    backend = AnthropicBackend(base_url=f"{server.base}/v1", model="claude-x",
                               api_key="sk-ant-key", timeout_s=5)
    assert backend.ask("ctx", "hello") == "Understood."
    request = server.last
    assert request["path"] == "/v1/messages"
    assert request["headers"]["x-api-key"] == "sk-ant-key"
    assert request["headers"]["anthropic-version"]
    # The system prompt is a top-level field, not a message.
    assert "system" in request["body"]
    assert all(m["role"] != "system" for m in request["body"]["messages"])


def test_anthropic_streams_content_block_deltas(server):
    _Handler.stream_events = [
        {"type": "message_start"},
        {"type": "content_block_delta", "delta": {"text": "All "}},
        {"type": "content_block_delta", "delta": {"text": "systems nominal."}},
        {"type": "message_stop"},
    ]
    backend = AnthropicBackend(base_url=f"{server.base}/v1", model="claude-x",
                               api_key="k", timeout_s=5)
    assert list(backend.stream("", "hi")) == ["All ", "systems nominal."]


def test_anthropic_reports_a_bad_shape(server):
    _Handler.payload = {"unexpected": True}
    backend = AnthropicBackend(base_url=f"{server.base}/v1", timeout_s=5)
    with pytest.raises(LLMError):
        backend.ask("", "hi")


# -- Ollama ---------------------------------------------------------------

def test_ollama_lists_its_models(server):
    _Handler.payload = {"models": [{"name": "llama3.2"}, {"name": "qwen"}]}
    backend = OllamaBackend(base_url=server.base, timeout_s=5)
    assert backend.list_models() == ["llama3.2", "qwen"]
    assert server.last_path == "/api/tags"


def test_ollama_streams_newline_delimited_json(server):
    class NdjsonHandler(_Handler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for chunk in ("Hello ", "there."):
                self.wfile.write(
                    json.dumps({"message": {"content": chunk}}).encode() + b"\n")
            self.wfile.write(json.dumps({"done": True}).encode() + b"\n")

    srv = HTTPServer(("127.0.0.1", 0), NdjsonHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        backend = OllamaBackend(base_url=f"http://127.0.0.1:{srv.server_address[1]}",
                                timeout_s=5)
        assert list(backend.stream("", "hi")) == ["Hello ", "there."]
    finally:
        srv.shutdown()


# -- error messages -------------------------------------------------------

@pytest.mark.parametrize("status,expected", [
    (401, "Preferences"),
    (403, "Access denied"),
    (404, "base URL"),
    (429, "Rate limited"),
    (503, "503"),
])
def test_http_failures_say_what_to_do_about_them(server, status, expected):
    _Handler.status = status
    _Handler.payload = {"error": "nope"}
    backend = OllamaBackend(base_url=server.base, timeout_s=5)
    with pytest.raises(LLMError) as exc:
        backend.ask("", "hi")
    assert expected in str(exc.value)


def test_a_401_names_authentication_not_a_vague_apology(server):
    """This is the message F2 needed and never produced."""
    _Handler.status = 401
    backend = OllamaBackend(base_url=server.base, timeout_s=5)
    with pytest.raises(LLMError) as exc:
        backend.ask("", "hi")
    message = str(exc.value).lower()
    assert "authentication" in message and "api key" in message


def test_an_unreachable_server_says_so():
    backend = OllamaBackend(base_url="http://127.0.0.1:1", timeout_s=1)
    with pytest.raises(LLMError) as exc:
        backend.ask("", "hi")
    assert "could not reach" in str(exc.value).lower()


# -- discovery ------------------------------------------------------------

def test_detection_finds_a_server_that_answers(server):
    port = server.server_address[1]
    found = detect_local_servers(ports=[(port, "LM Studio", "", "openai_compatible")])
    assert len(found) == 1
    assert found[0].models == ["a-model"]
    assert found[0].base_url.endswith(str(port))


def test_detection_ignores_ports_with_nothing_on_them():
    assert detect_local_servers(
        ports=[(1, "Nothing", "/v1", "openai_compatible")], timeout=0.2) == []


def test_the_usual_ports_are_covered():
    ports = {port for port, _label, _path, _kind in KNOWN_PORTS}
    assert {1234, 11434, 8080, 8000, 5000}.issubset(ports)


# -- presets --------------------------------------------------------------

def test_every_preset_builds_a_backend():
    from types import SimpleNamespace

    for name, preset in PRESETS.items():
        backend = make_llm(SimpleNamespace(provider=name, base_url=preset["base_url"],
                                           model=preset["model"], timeout_s=5),
                           api_key="k")
        assert hasattr(backend, "stream") and hasattr(backend, "ask")


def test_each_provider_stores_its_key_under_its_own_account():
    accounts = [preset["keyring_account"] for preset in PRESETS.values()]
    assert len(accounts) == len(set(accounts))


def test_the_system_prompt_from_settings_reaches_the_backend():
    from types import SimpleNamespace

    backend = make_llm(SimpleNamespace(provider="openai", base_url="http://x",
                                       model="m", timeout_s=5),
                       api_key="k", system_prompt="be terse")
    assert backend.system_prompt == "be terse"


# -- adapting to what a provider will actually accept ---------------------

class _FussyHandler(_Handler):
    """Refuses one named parameter the way OpenAI does, then answers."""

    refuse = "temperature"
    seen: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).seen.append(body)
        if self.refuse and self.refuse in body:
            payload = {"error": {
                "message": f"Unsupported value: '{self.refuse}' does not support "
                           f"{body[self.refuse]} with this model. Only the default "
                           f"(1) value is supported.",
                "type": "invalid_request_error",
                "param": self.refuse,
                "code": "unsupported_value"}}
            data = json.dumps(payload).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        data = json.dumps(
            {"choices": [{"message": {"content": "All systems nominal."}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def fussy():
    _FussyHandler.seen = []
    _FussyHandler.refuse = "temperature"
    srv = HTTPServer(("127.0.0.1", 0), _FussyHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    yield srv
    srv.shutdown()


def _backend(base, **kwargs):
    from bgassist.llm.openai import OpenAICompatibleBackend

    return OpenAICompatibleBackend(base_url=base, model="gpt-5-mini",
                                   api_key="k", timeout_s=5, **kwargs)


def test_a_refused_temperature_is_dropped_and_the_question_still_answered(fussy):
    """Newer OpenAI models accept only the default temperature. Refusing to
    answer over a preference is the wrong trade."""
    backend = _backend(fussy.base)
    assert backend.ask("", "status") == "All systems nominal."
    assert "temperature" in _FussyHandler.seen[0]
    assert "temperature" not in _FussyHandler.seen[1]


def test_the_lesson_is_remembered_for_later_requests(fussy):
    backend = _backend(fussy.base)
    backend.ask("", "first")
    before = len(_FussyHandler.seen)
    backend.ask("", "second")
    # One request, not two: we no longer send what we know will be refused.
    assert len(_FussyHandler.seen) == before + 1
    assert "temperature" not in _FussyHandler.seen[-1]


def test_a_refused_token_limit_is_renamed_rather_than_abandoned(fussy):
    """Servers predating max_completion_tokens want the older spelling; the
    cap is worth keeping."""
    _FussyHandler.refuse = "max_completion_tokens"
    backend = _backend(fussy.base)
    assert backend.ask("", "status") == "All systems nominal."
    assert "max_completion_tokens" in _FussyHandler.seen[0]
    assert _FussyHandler.seen[1]["max_tokens"] == _FussyHandler.seen[0][
        "max_completion_tokens"]


def test_streaming_adapts_before_the_first_token(fussy):
    backend = _backend(fussy.base)
    # The fussy server answers non-streaming JSON, which the SSE reader simply
    # finds no events in — what matters is that it got past the 400.
    list(backend.stream("", "status"))
    assert "temperature" not in _FussyHandler.seen[-1]


def test_a_refusal_we_cannot_answer_is_still_an_error(fussy):
    """Dropping 'model' would be nonsense, so that one is reported."""
    _FussyHandler.refuse = "model"
    backend = _backend(fussy.base)
    with pytest.raises(LLMError):
        backend.ask("", "status")
    assert len(_FussyHandler.seen) == 1  # no retry storm


def test_the_error_carries_the_status_and_body(server):
    from bgassist.llm.base import LLMHttpError

    _Handler.status = 429
    backend = OllamaBackend(base_url=server.base, timeout_s=5)
    with pytest.raises(LLMHttpError) as exc:
        backend.ask("", "hi")
    assert exc.value.status == 429


def test_the_connection_test_reports_what_the_model_ignored(fussy):
    """A control that quietly does nothing is worse than one that says so."""
    backend = _backend(fussy.base)
    result = backend.test_connection()
    assert result["ok"] is True
    assert result["ignored"] == ["temperature"]


# -- reasoning models that spend the whole budget before answering --------

class _ReasoningHandler(_Handler):
    """Answers only when given room to think first.

    This is what a gpt-5 or o-series model does: hidden reasoning is billed
    against max_completion_tokens, so a budget sized for one spoken sentence
    is gone before a word is written, and the completion comes back empty with
    finish_reason "length".
    """

    threshold = 2000
    seen: list = []
    stream_mode = False

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).seen.append(body)
        room = body.get("max_completion_tokens", 0) >= self.threshold
        if self.stream_mode:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if room:
                for chunk in ("The West Bank ", "is a territory."):
                    self.wfile.write(
                        f"data: {json.dumps({'choices': [{'delta': {'content': chunk}}]})}\n\n".encode())
            self.wfile.write(
                f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop' if room else 'length'}]})}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        payload = {"choices": [{
            "message": {"content": "The West Bank is a territory." if room else ""},
            "finish_reason": "stop" if room else "length"}]}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def reasoning():
    _ReasoningHandler.seen = []
    _ReasoningHandler.stream_mode = False
    srv = HTTPServer(("127.0.0.1", 0), _ReasoningHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    yield srv
    srv.shutdown()


def test_an_empty_answer_from_a_reasoning_model_is_retried_with_room(reasoning):
    """'I have nothing to report' was the app faithfully reporting an empty
    completion. The completion was empty because the model had no budget left
    after thinking."""
    backend = _backend(reasoning.base, max_tokens=400)
    assert backend.ask("", "what is the West Bank") == "The West Bank is a territory."
    assert _ReasoningHandler.seen[0]["max_completion_tokens"] == 400
    assert _ReasoningHandler.seen[1]["max_completion_tokens"] >= 2000


def test_the_larger_budget_is_remembered(reasoning):
    backend = _backend(reasoning.base, max_tokens=400)
    backend.ask("", "first")
    before = len(_ReasoningHandler.seen)
    backend.ask("", "second")
    assert len(_ReasoningHandler.seen) == before + 1  # no second attempt needed
    assert _ReasoningHandler.seen[-1]["max_completion_tokens"] >= 2000


def test_streaming_retries_before_a_single_token_is_spoken(reasoning):
    _ReasoningHandler.stream_mode = True
    backend = _backend(reasoning.base, max_tokens=400)
    chunks = list(backend.stream("", "what is the West Bank"))
    assert "".join(chunks) == "The West Bank is a territory."
    # Exactly once, so nothing could ever be spoken twice.
    assert chunks.count("The West Bank ") == 1


def test_a_genuinely_empty_answer_is_not_retried_for_ever(reasoning):
    _ReasoningHandler.threshold = 10 ** 9  # never satisfied
    try:
        backend = _backend(reasoning.base, max_tokens=400)
        assert backend.ask("", "hello") == ""
        assert len(_ReasoningHandler.seen) == 2  # one growth, then it gives up
    finally:
        _ReasoningHandler.threshold = 2000


def test_reasoning_effort_is_asked_for_and_dropped_when_refused(fussy):
    """A voice assistant is waited on out loud, so ask for the cheapest
    reasoning that answers — and stop asking models that have never heard
    of it."""
    _FussyHandler.refuse = "reasoning_effort"
    backend = _backend(fussy.base)
    assert backend.ask("", "status") == "All systems nominal."
    assert _FussyHandler.seen[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in _FussyHandler.seen[1]


# -- the model picker ------------------------------------------------------

def test_models_are_grouped_not_filtered():
    """/v1/models returns everything the account can reach — embeddings,
    speech, images. Offering "text-embedding-3-small" as an assistant is
    worse than no picker; hiding an unrecognised model is worse still, since
    a provider ships new ones faster than anyone updates a prefix list."""
    from bgassist.llm import rank_models

    chat, other = rank_models([
        "gpt-4o-mini", "gpt-5-mini", "o4-mini", "chatgpt-4o-latest",
        "text-embedding-3-small", "whisper-1", "dall-e-3", "tts-1-hd",
        "omni-moderation-latest", "brand-new-model-nobody-has-heard-of"])
    assert chat == ["chatgpt-4o-latest", "gpt-4o-mini", "gpt-5-mini", "o4-mini"]
    assert "text-embedding-3-small" in other
    assert "whisper-1" in other
    # Not hidden, just not first.
    assert "brand-new-model-nobody-has-heard-of" in other


def test_a_local_server_is_given_the_benefit_of_the_doubt():
    """Local ids are whatever the person named their own files."""
    from bgassist.llm import rank_models

    chat, other = rank_models(
        ["lmstudio-community/Meta-Llama-3.1-8B-Instruct", "my-finetune-v2",
         "nomic-embed-text"], generous=True)
    assert "my-finetune-v2" in chat
    assert other == ["nomic-embed-text"]


def test_ranking_is_stable_and_deduplicated():
    from bgassist.llm import rank_models

    chat, _other = rank_models(["gpt-4o", "gpt-4o", "gpt-3.5-turbo", None, ""])
    assert chat == ["gpt-3.5-turbo", "gpt-4o"]
