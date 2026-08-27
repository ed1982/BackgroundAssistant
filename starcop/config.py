"""Configuration loading and defaults for Star Trek Computer.

Resolution order: built-in defaults <- config.json (deep-merged).
String values may contain ${ENV_VAR} references which are expanded at load
time (used for API keys so secrets never live in the config file).
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("starcop.config")

DEFAULTS: Dict[str, Any] = {
    "trigger_words": ["computer"],
    "language": "en",
    "whisper_model": "base.en",
    "compute_type": "int8",
    "audio_device": None,
    "samplerate": 16000,
    "vad_aggressiveness": 2,
    "pre_roll_ms": 360,
    "end_silence_ms": 700,
    "min_utterance_ms": 300,
    "max_utterance_ms": 30000,
    "command_end_silence_ms": 1500,
    "max_command_wait_ms": 12000,
    "context_seconds": 120,
    "llm": {
        "backend": "ollama",
        "base_url": "http://localhost:11434",
        "model": "llama3.2",
        "api_key_env": "OPENAI_API_KEY",
        "timeout_s": 120,
    },
    "tts": {
        "engine": "auto",
        "rate": 185,
        "voice": None,
    },
    "log_file": "starcop.log",
    "log_level": "INFO",
}

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


@dataclass
class LlmConfig:
    backend: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "llama3.2"
    api_key_env: str = "OPENAI_API_KEY"
    timeout_s: float = 120.0

    @property
    def api_key(self) -> str:
        """API key read from the environment variable named by api_key_env."""
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""


@dataclass
class TtsConfig:
    engine: str = "auto"  # auto | say | pyttsx3 | mock
    rate: int = 185
    voice: Optional[str] = None


@dataclass
class Config:
    trigger_words: List[str] = field(default_factory=lambda: ["computer"])
    language: Optional[str] = "en"
    whisper_model: str = "base.en"
    compute_type: str = "int8"
    audio_device: Optional[Any] = None  # sounddevice index or name
    samplerate: int = 16000
    vad_aggressiveness: int = 2
    pre_roll_ms: int = 360
    end_silence_ms: int = 700
    min_utterance_ms: int = 300
    max_utterance_ms: int = 30000
    command_end_silence_ms: int = 1500
    max_command_wait_ms: int = 12000
    context_seconds: float = 120.0
    llm: LlmConfig = field(default_factory=LlmConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    log_file: str = "starcop.log"
    log_level: str = "INFO"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        known = {f for f in cls.__dataclass_fields__ if f not in ("llm", "tts")}
        kwargs = {k: v for k, v in data.items() if k in known}
        llm = LlmConfig(
            **{k: v for k, v in data.get("llm", {}).items()
               if k in LlmConfig.__dataclass_fields__}
        )
        tts = TtsConfig(
            **{k: v for k, v in data.get("tts", {}).items()
               if k in TtsConfig.__dataclass_fields__}
        )
        return cls(llm=llm, tts=tts, **kwargs)


def load_config(path: Optional[str] = None) -> Config:
    """Load config from a JSON file (if present), deep-merged over defaults.

    When *path* is None, looks for config.json next to the project root.
    A missing or invalid file falls back to defaults (with a log message).
    """
    data: Dict[str, Any] = {}
    candidates: List[str] = []
    if path:
        candidates.append(path)
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(here, "..", "config.json"))

    for candidate in candidates:
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if not isinstance(loaded, dict):
                    raise ValueError("config root must be a JSON object")
                data = loaded
                log.info("Loaded config from %s", candidate)
            except (OSError, ValueError) as exc:
                log.error("Could not read config %s (%s); using defaults", candidate, exc)
                data = {}
            break

    if path and not os.path.isfile(path):
        log.warning("Config file %s not found; using defaults", path)

    merged = _expand_env(_deep_merge(DEFAULTS, data))
    return Config.from_dict(merged)
