"""The LLM backend interface: streaming, cancellable, testable.

Every backend implements ``stream()``; ``ask()`` is just the stream collected,
so nothing in the app has to care which one it is talking to. Streaming is
what lets speech start on the first sentence instead of the whole answer
(§5.2), and the cancel token is what makes an answer interruptible (F10).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, Iterator, List, Optional, Protocol, Sequence, runtime_checkable

log = logging.getLogger("bgassist.llm")


class LLMError(RuntimeError):
    """Raised when a backend fails (network, HTTP error, bad response)."""


class LLMCancelled(LLMError):
    """The request was cancelled by a new trigger, Stop, or Esc."""


class LLMHttpError(LLMError):
    """An HTTP failure, with the status and body kept for inspection.

    The message is still the human-readable explanation; the structured fields
    exist so a backend can tell "your key is wrong" from "I sent a parameter
    this model does not accept" and do something about the second.
    """

    def __init__(self, message: str, status: int = 0, detail: str = "",
                 url: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.url = url


#: Request parameters we are willing to give up on when a provider refuses
#: them. All of them are preferences; none changes what was asked. Anything
#: not in here (``model``, ``messages``) is a real error worth surfacing.
ADAPTABLE_PARAMETERS = frozenset({
    "temperature", "top_p", "presence_penalty", "frequency_penalty",
    "max_completion_tokens", "max_tokens", "reasoning_effort",
})

#: Some refusals are better answered by renaming than by dropping: servers
#: predating ``max_completion_tokens`` want the older spelling.
PARAMETER_FALLBACKS = {"max_completion_tokens": "max_tokens"}


def unsupported_parameter(error: "LLMHttpError") -> Optional[str]:
    """The name of the parameter a provider objected to, if it named one.

    OpenAI puts it in ``error.param``; others only mention it in the message.
    We do not keep a list of which models accept which parameters, because
    that list is wrong the moment a provider ships a new one — this reads what
    the provider actually said instead.
    """
    if error.status not in (400, 422):
        return None
    detail = error.detail or ""
    message = detail
    try:
        data = json.loads(detail)
    except ValueError:
        data = None
    if isinstance(data, dict):
        body = data.get("error") if isinstance(data.get("error"), dict) else data
        param = body.get("param")
        if isinstance(param, str) and param.strip():
            return param.strip()
        message = str(body.get("message") or detail)
    # Quoted ("Unsupported parameter: 'max_tokens' is not supported") or
    # trailing ("Unrecognized request argument supplied: max_completion_tokens").
    match = re.search(
        r"(?:unsupported|unrecognized|unknown|invalid|extra)[^'\"\n]{0,48}?"
        r"(?:['\"]([A-Za-z0-9_]+)['\"]|:\s*([A-Za-z0-9_]+))",
        message, re.IGNORECASE)
    if match is None:
        return None
    # Whatever is found is only ever acted on if it is in ADAPTABLE_PARAMETERS,
    # so a stray match here cannot make us drop something that matters.
    return match.group(1) or match.group(2)


@runtime_checkable
class LLMBackend(Protocol):
    name: str
    model: str

    def stream(self, context_text: str, query: str, *,
               history: Optional[Sequence[Dict[str, str]]] = None,
               cancel: Optional[threading.Event] = None,
               marked_utterance: str = "") -> Iterator[str]:
        ...

    def ask(self, context_text: str, query: str, *,
            history: Optional[Sequence[Dict[str, str]]] = None,
            cancel: Optional[threading.Event] = None,
            marked_utterance: str = "") -> str:
        ...

    def test_connection(self) -> dict:
        ...

    def list_models(self) -> List[str]:
        ...


class HttpBackend:
    """Shared HTTP plumbing (stdlib only — no extra dependency)."""

    name = "http"

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout_s: float = 120.0, system_prompt: Optional[str] = None,
                 temperature: float = 0.5, max_tokens: int = 400):
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.timeout_s = float(timeout_s)
        self.system_prompt = system_prompt
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        #: Parameters this provider has refused, and what to do about them:
        #: a replacement name, or None to leave them out. Remembered so the
        #: adaptation costs one round trip per process, not one per request.
        self._adaptations: Dict[str, Optional[str]] = {}

    # -- helpers ---------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        return {}

    def _request(self, url: str, payload: dict, *, stream: bool = False):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", **self._headers()},
            method="POST")
        try:
            return urllib.request.urlopen(request, timeout=self.timeout_s)
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001 - best effort detail extraction
                detail = ""
            raise LLMHttpError(self._explain(exc.code, url, detail),
                               status=exc.code, detail=detail, url=url) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise LLMError(f"Could not reach {url}: {exc}") from exc

    @staticmethod
    def _explain(code: int, url: str, detail: str) -> str:
        """Turn an HTTP status into something worth saying out loud.

        F2 was invisible precisely because a 401 was spoken as a generic
        apology; the user never learned it was an authentication problem.
        """
        if code == 401:
            return ("Authentication failed (HTTP 401). Open Preferences → AI and "
                    f"check the API key. [{url}]")
        if code == 403:
            return f"Access denied (HTTP 403) — the key may lack access to this model. [{url}]"
        if code == 404:
            return f"Not found (HTTP 404) — check the base URL and model name. [{url}]"
        if code == 429:
            return f"Rate limited (HTTP 429) — too many requests, or no quota. [{url}]"
        if code >= 500:
            return f"The provider returned HTTP {code}. [{url}] {detail}"
        return f"HTTP {code} from {url}: {detail}"

    # -- adapting to what a provider will actually accept ------------------
    def _adapt(self, payload: dict) -> dict:
        """Apply everything this provider has already told us it dislikes."""
        if not self._adaptations:
            return payload
        adapted = dict(payload)
        for name, replacement in self._adaptations.items():
            if name not in adapted:
                continue
            value = adapted.pop(name)
            if replacement:
                adapted[replacement] = value
        return adapted

    def _learn(self, parameter: str) -> bool:
        """Record how to get past a refusal. False if we cannot."""
        if parameter not in ADAPTABLE_PARAMETERS or parameter in self._adaptations:
            return False
        replacement = PARAMETER_FALLBACKS.get(parameter)
        self._adaptations[parameter] = replacement
        if replacement:
            log.info("%s rejected %r; using %r instead", self.name, parameter,
                     replacement)
        else:
            log.info("%s rejected %r; sending the request without it", self.name,
                     parameter)
        return True

    def _request_adapting(self, url: str, payload: dict, *, stream: bool = False):
        """POST, giving up parameters the provider refuses rather than failing.

        Newer OpenAI models accept only the default temperature; older
        OpenAI-compatible servers want ``max_tokens`` rather than
        ``max_completion_tokens``. Both are preferences, not the question being
        asked, so refusing to answer over one of them is the wrong trade.
        """
        for _attempt in range(len(ADAPTABLE_PARAMETERS) + 1):
            try:
                return self._request(url, self._adapt(payload), stream=stream)
            except LLMHttpError as exc:
                parameter = unsupported_parameter(exc)
                if parameter is None or not self._learn(parameter):
                    raise
        raise LLMError(f"{url} refused every request we could construct")

    def _post_json(self, url: str, payload: dict) -> dict:
        with self._request_adapting(url, payload) as response:
            body = response.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except ValueError as exc:
            raise LLMError(f"Invalid JSON from {url}: {body[:200]!r}") from exc

    def _get_json(self, url: str) -> dict:
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout_s, 15)) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise LLMError(self._explain(exc.code, url, "")) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise LLMError(f"Could not reach {url}: {exc}") from exc

    def _iter_sse(self, response, cancel: Optional[threading.Event]):
        """Yield ``data:`` payloads from a Server-Sent Events response.

        The connection is closed as soon as *cancel* is set, so a cancelled
        answer stops costing tokens immediately rather than running to the
        120 s timeout (F10).
        """
        try:
            for raw in response:
                if cancel is not None and cancel.is_set():
                    raise LLMCancelled("cancelled")
                line = raw.decode("utf-8", "replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    yield json.loads(payload)
                except ValueError:
                    continue
        finally:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass

    # -- interface -------------------------------------------------------
    def ask(self, context_text: str = "", query: str = "", *,
            history=None, cancel: Optional[threading.Event] = None,
            marked_utterance: str = "") -> str:
        chunks: List[str] = []
        for chunk in self.stream(context_text, query, history=history,
                                 cancel=cancel, marked_utterance=marked_utterance):
            chunks.append(chunk)
        return "".join(chunks).strip()

    def stream(self, context_text: str = "", query: str = "", *,
               history=None, cancel: Optional[threading.Event] = None,
               marked_utterance: str = "") -> Iterator[str]:
        raise NotImplementedError

    def test_connection(self) -> dict:
        """Send one tiny request and report what actually happened (§6.5)."""
        started = time.monotonic()
        text = self.ask("", "Reply with the single word: ready.")
        return {
            "ok": True,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "model": self.model,
            "reply": text[:120],
            # Anything this model refused, so Preferences can say why a
            # setting is having no effect rather than leaving it a mystery.
            "ignored": sorted(self._adaptations),
        }

    def list_models(self) -> List[str]:
        return []
