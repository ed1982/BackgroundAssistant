import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from starcop.llm import (LLMError, MockBackend, OllamaBackend,
                         OpenAICompatibleBackend, build_messages, make_llm)


def test_build_messages_with_query():
    msgs = build_messages("14:00  hi", "what time is it")
    assert msgs[0]["role"] == "system"
    assert "ship's computer" in msgs[0]["content"].lower()
    user = msgs[1]["content"]
    assert "Recent conversation" in user and "what time is it" in user


def test_build_messages_empty_query():
    msgs = build_messages("14:00  hi", "")
    assert "only called your name" in msgs[1]["content"]


class _Handler(BaseHTTPRequestHandler):
    status = 200
    response_payload = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.last_request = {
            "path": self.path,
            "body": body,
            "auth": self.headers.get("Authorization"),
        }
        payload = (self.response_payload
                   or {"message": {"content": "All systems nominal."}})
        data = json.dumps(payload).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # silence request logging
        pass


@pytest.fixture()
def server():
    _Handler.status = 200
    _Handler.response_payload = None
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def test_ollama_backend(server):
    b = OllamaBackend(base_url=f"http://127.0.0.1:{server.server_address[1]}",
                      model="llama3.2", timeout_s=5)
    out = b.ask("ctx line", "status?")
    assert out == "All systems nominal."
    req = server.last_request
    assert req["path"] == "/api/chat"
    assert req["body"]["model"] == "llama3.2"
    assert req["body"]["stream"] is False
    assert req["body"]["messages"][0]["role"] == "system"


def test_openai_backend(server):
    _Handler.response_payload = {"choices": [{"message": {"content": "Aye aye."}}]}
    b = OpenAICompatibleBackend(
        base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
        model="gpt-x", api_key="k-123", timeout_s=5)
    assert b.ask("", "hi") == "Aye aye."
    req = server.last_request
    assert req["path"] == "/v1/chat/completions"
    assert req["auth"] == "Bearer k-123"


def test_http_error_raises_llmerror(server):
    _Handler.status = 500
    b = OllamaBackend(base_url=f"http://127.0.0.1:{server.server_address[1]}",
                      timeout_s=5)
    with pytest.raises(LLMError):
        b.ask("", "hi")


def test_unreachable_raises_llmerror():
    b = OllamaBackend(base_url="http://127.0.0.1:1", timeout_s=1)
    with pytest.raises(LLMError):
        b.ask("", "hi")


def test_mock_backend_records():
    m = MockBackend(response="canned")
    assert "canned" in m.ask("ctx", "q")
    assert m.calls == [("ctx", "q")]


def test_make_llm_dispatch():
    assert isinstance(make_llm(SimpleNamespace(backend="mock")), MockBackend)
    assert isinstance(
        make_llm(SimpleNamespace(backend="ollama", base_url="http://x",
                                 model="m", timeout_s=1)), OllamaBackend)
    assert isinstance(
        make_llm(SimpleNamespace(backend="openai_compatible", base_url="http://x",
                                 model="m", api_key="", timeout_s=1)),
        OpenAICompatibleBackend)
    with pytest.raises(ValueError):
        make_llm(SimpleNamespace(backend="nope"))
