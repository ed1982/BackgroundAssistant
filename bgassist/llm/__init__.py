"""LLM backends and the factory that picks one from settings.

Providers offered in Preferences (D7): OpenAI, Claude, a local server
(LM Studio / Ollama / llama.cpp / Pinokio), or a custom OpenAI-compatible URL.
"""
from __future__ import annotations

import logging
from typing import Optional

from bgassist.llm.anthropic import AnthropicBackend
from bgassist.llm.base import HttpBackend, LLMBackend, LLMCancelled, LLMError
from bgassist.llm.local import KNOWN_PORTS, LocalServer, OllamaBackend, detect_local_servers
from bgassist.llm.mock import MockBackend
from bgassist.llm.openai import OpenAICompatibleBackend
from bgassist.llm.prompts import DEFAULT_SYSTEM_PROMPT, build_messages, history_from_messages

log = logging.getLogger("bgassist.llm")

#: Model ids that are certainly not something to hold a conversation with.
#: OpenAI's /v1/models returns everything the account can reach — embeddings,
#: speech, images, moderation — and a picker offering
#: "text-embedding-3-small" as an assistant is worse than no picker.
_NOT_CHAT = ("embed", "whisper", "tts", "audio", "dall-e", "dalle", "image",
             "moderation", "rerank", "clip", "sora", "transcribe", "realtime",
             "guard", "safety", "bge-", "-e5", "nomic", "codex", "davinci",
             "babbage", "similarity", "search-")

#: Families worth putting at the top. Absence is not disqualifying — anything
#: unrecognised is still offered, just lower down — so a new model release
#: does not disappear from the list until someone updates this tuple.
_CHAT_FAMILIES = ("gpt-", "chatgpt", "o1", "o3", "o4", "claude", "llama",
                  "mistral", "mixtral", "qwen", "gemma", "phi", "deepseek",
                  "command", "granite", "hermes", "yi-", "glm", "kimi",
                  "nemotron", "olmo", "vicuna", "zephyr", "dolphin", "wizard",
                  "solar", "aya", "exaone", "minicpm", "internlm", "smol")


def rank_models(ids, generous: bool = False):
    """Split model ids into (conversational, everything else).

    Nothing is hidden — the second list is still offered — because a provider
    can ship a model faster than anyone updates a list of prefixes. This only
    decides what appears at the top.

    *generous* is for local servers, where the ids are whatever the person
    named their own files and almost all of them are chat models.
    """
    chat, other = [], []
    for model in sorted({str(i) for i in ids if i}):
        lowered = model.lower()
        if any(token in lowered for token in _NOT_CHAT):
            other.append(model)
        elif (generous or any(f in lowered for f in _CHAT_FAMILIES)
                or "instruct" in lowered or "chat" in lowered):
            chat.append(model)
        else:
            other.append(model)
    return chat, other

__all__ = [
    "AnthropicBackend", "HttpBackend", "LLMBackend", "LLMCancelled", "LLMError",
    "LocalServer", "KNOWN_PORTS", "MockBackend", "OllamaBackend",
    "OpenAICompatibleBackend", "DEFAULT_SYSTEM_PROMPT", "build_messages",
    "history_from_messages", "detect_local_servers", "make_llm", "rank_models",
    "PRESETS",
]

#: Provider presets shown in the Preferences dropdown. ``keyring_account`` is
#: the account name each provider's key is stored under, so several keys can
#: coexist and switching provider does not mean re-entering one (Q6).
PRESETS = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "needs_key": True,
        "keyring_account": "openai",
        "backend": "openai_compatible",
    },
    "anthropic": {
        "label": "Claude (Anthropic)",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-4-5",
        "needs_key": True,
        "keyring_account": "anthropic",
        "backend": "anthropic",
    },
    "local": {
        "label": "Local server",
        "base_url": "http://localhost:1234/v1",
        "model": "",
        "needs_key": False,
        "keyring_account": "local",
        "backend": "openai_compatible",
    },
    "ollama": {
        "label": "Ollama (native API)",
        "base_url": "http://localhost:11434",
        "model": "llama3.2",
        "needs_key": False,
        "keyring_account": "ollama",
        "backend": "ollama",
    },
    "custom": {
        "label": "Custom (OpenAI-compatible)",
        "base_url": "",
        "model": "",
        "needs_key": True,
        "keyring_account": "custom",
        "backend": "openai_compatible",
    },
}


def make_llm(cfg, api_key: str = "", system_prompt: Optional[str] = None):
    """Instantiate the configured backend.

    *cfg* is anything with ``provider``/``backend``, ``base_url``, ``model``
    and ``timeout_s`` attributes — the settings object in production, a simple
    namespace in tests. The API key is passed in rather than read from the
    environment: that mechanism is exactly what produced F2 and has been
    removed (§6.2).
    """
    name = str(getattr(cfg, "provider", "") or getattr(cfg, "backend", "")
               or "openai").lower()
    preset = PRESETS.get(name, {})
    backend = preset.get("backend", name)
    base_url = getattr(cfg, "base_url", "") or preset.get("base_url", "")
    model = getattr(cfg, "model", "") or preset.get("model", "")
    timeout_s = float(getattr(cfg, "timeout_s", 120.0) or 120.0)
    extra = {
        "system_prompt": system_prompt or getattr(cfg, "system_prompt", None),
        "temperature": float(getattr(cfg, "temperature", 0.5) or 0.5),
        "max_tokens": int(getattr(cfg, "max_tokens", 400) or 400),
    }

    if backend == "mock":
        return MockBackend()
    if backend == "ollama":
        return OllamaBackend(base_url=base_url, model=model, timeout_s=timeout_s,
                             **extra)
    if backend == "anthropic":
        return AnthropicBackend(base_url=base_url, model=model, api_key=api_key,
                                timeout_s=timeout_s, **extra)
    if backend in ("openai_compatible", "openai", "local", "custom"):
        return OpenAICompatibleBackend(base_url=base_url, model=model,
                                       api_key=api_key, timeout_s=timeout_s,
                                       **extra)
    raise ValueError(f"Unknown LLM backend: {backend!r}")
