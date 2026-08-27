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
