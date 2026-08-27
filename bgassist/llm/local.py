"""Local model servers: Ollama's native API, plus discovery for all of them.

Directly addresses "I haven't figured out how to use the LM Link and exposed
network URL features" (§6.5): Preferences has a **Detect local servers**
button which probes the usual ports and reports what each one says it has, so
nobody has to guess a URL again.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

from bgassist.llm.base import HttpBackend, LLMError
from bgassist.llm.prompts import build_messages

log = logging.getLogger("bgassist.llm.local")


class OllamaBackend(HttpBackend):
    """Ollama's native /api/chat (the OpenAI shim is handled elsewhere)."""

    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434",
                 model: str = "llama3.2", timeout_s: float = 120.0, **kwargs):
        kwargs.pop("api_key", None)
        super().__init__(base_url=base_url, model=model, api_key="",
                         timeout_s=timeout_s, **kwargs)

    def _payload(self, context_text: str, query: str, history, marked: str,
                 stream: bool) -> dict:
        return {
            "model": self.model,
            "messages": build_messages(context_text, query, history=history,
                                       system_prompt=self.system_prompt,
                                       marked_utterance=marked),
            "stream": stream,
            "options": {"temperature": self.temperature},
        }

    def stream(self, context_text: str = "", query: str = "", *,
               history=None, cancel: Optional[threading.Event] = None,
               marked_utterance: str = "") -> Iterator[str]:
        """Ollama streams newline-delimited JSON rather than SSE."""
        url = f"{self.base_url}/api/chat"
        payload = self._payload(context_text, query, history, marked_utterance, True)
        response = self._request(url, payload, stream=True)
        try:
            for raw in response:
                if cancel is not None and cancel.is_set():
                    from bgassist.llm.base import LLMCancelled

                    raise LLMCancelled("cancelled")
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                piece = (event.get("message") or {}).get("content")
                if piece:
                    yield piece
                if event.get("done"):
                    return
        finally:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass

    def ask(self, context_text: str = "", query: str = "", *,
            history=None, cancel: Optional[threading.Event] = None,
            marked_utterance: str = "") -> str:
        url = f"{self.base_url}/api/chat"
        payload = self._payload(context_text, query, history, marked_utterance, False)
        data = self._post_json(url, payload)
        try:
            text = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Unexpected Ollama response: {str(data)[:200]}") from exc
        return (text or "").strip()

    def list_models(self) -> List[str]:
        data = self._get_json(f"{self.base_url}/api/tags")
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            return []
        return sorted(str(m.get("name")) for m in models if m.get("name"))


# -- discovery -----------------------------------------------------------

@dataclass
class LocalServer:
    label: str
    base_url: str
    kind: str                    # "openai_compatible" | "ollama"
    models: List[str] = field(default_factory=list)
    note: str = ""


#: (port, label, path, kind). Ollama is probed on its OpenAI-compatible shim
#: because that path streams tokens the same way everything else does.
KNOWN_PORTS = (
    (1234, "LM Studio", "/v1", "openai_compatible"),
    (11434, "Ollama", "/v1", "openai_compatible"),
    (8080, "llama.cpp server", "/v1", "openai_compatible"),
    (8000, "Local server (8000)", "/v1", "openai_compatible"),
    (5000, "Local server (5000)", "/v1", "openai_compatible"),
    (5001, "Local server (5001)", "/v1", "openai_compatible"),
)

HELP = {
    "LM Studio": ("Developer tab → Start Server. Base URL http://localhost:1234/v1; "
                  "the API key can be anything. Turn on 'Serve on Local Network' "
                  "only if another machine needs to reach it."),
    "Ollama": ("http://localhost:11434/v1 for the OpenAI-compatible shim, or "
               "http://localhost:11434 for the native API. No key needed."),
    "Pinokio": ("Depends on which app you launched — use Detect and read the port "
                "from the app's own UI."),
}


def _probe(host: str, port: int, path: str, timeout: float) -> Optional[List[str]]:
    url = f"http://{host}:{port}{path}/models"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    items = data.get("data") if isinstance(data, dict) else None
    if isinstance(items, list):
        return sorted(str(item.get("id")) for item in items if item.get("id"))
    return []


def detect_local_servers(host: str = "127.0.0.1", timeout: float = 0.6,
                         ports=KNOWN_PORTS) -> List[LocalServer]:
    """Probe the usual local-server ports and report what is actually there."""
    found: List[LocalServer] = []
    for port, label, path, kind in ports:
        models = _probe(host, port, path, timeout)
        if models is None:
            continue
        found.append(LocalServer(
            label=label, base_url=f"http://{host}:{port}{path}", kind=kind,
            models=models, note=HELP.get(label, "")))
    log.info("local server detection found %d server(s)", len(found))
    return found
