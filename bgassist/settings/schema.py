"""Typed settings: defaults, validation, and the shape the UI talks to.

Everything the Preferences window can change lives here (§6.4). Two things
are deliberately *not* here: API keys, which live in the Keychain and never
touch this file (§6.2), and anything derived from the machine, which is
discovered at runtime.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Dict, List, Optional

from bgassist.llm.prompts import DEFAULT_SYSTEM_PROMPT

SENSITIVITIES = ("relaxed", "balanced", "strict")
STT_ENGINES = ("whisper", "apple")
TTS_ENGINES = ("auto", "piper", "system", "say", "pyttsx3", "mock")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
PROVIDERS = ("openai", "anthropic", "local", "ollama", "custom", "mock")


def _clamp(value, low, high, default):
    try:
        value = type(default)(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


@dataclass
class GeneralSettings:
    trigger_words: List[str] = field(default_factory=lambda: ["computer"])
    sensitivity: str = "balanced"           # relaxed | balanced | strict
    launch_at_login: bool = False
    hotkey: str = ""                        # unset out of the box (D17a)
    notifications: bool = False             # spoken answers are enough (Q7)
    autostart_listening: bool = True

    def validate(self) -> None:
        words = [w.strip() for w in (self.trigger_words or []) if str(w).strip()]
        self.trigger_words = words or ["computer"]
        if self.sensitivity not in SENSITIVITIES:
            self.sensitivity = "balanced"

    @property
    def easter_egg(self) -> bool:
        """🖖 beside the trigger word when it is exactly "computer" (D2)."""
        return any(w.strip().lower() == "computer" for w in self.trigger_words)


@dataclass
class AiSettings:
    provider: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    timeout_s: float = 120.0
    temperature: float = 0.5
    max_tokens: int = 400
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    context_seconds: float = 120.0
    auto_title: bool = True

    def validate(self) -> None:
        if self.provider not in PROVIDERS:
            self.provider = "openai"
        self.timeout_s = _clamp(self.timeout_s, 5.0, 600.0, 120.0)
        self.temperature = _clamp(self.temperature, 0.0, 2.0, 0.5)
        self.max_tokens = _clamp(self.max_tokens, 32, 4096, 400)
        self.context_seconds = _clamp(self.context_seconds, 0.0, 900.0, 120.0)
        if not str(self.system_prompt or "").strip():
            self.system_prompt = DEFAULT_SYSTEM_PROMPT


@dataclass
class VoiceSettings:
    engine: str = "auto"
    voice: Optional[str] = None
    rate: int = 185
    chime: bool = True
    speak_typed_answers: bool = False
    output_device: Optional[Any] = None

    def validate(self) -> None:
        if self.engine not in TTS_ENGINES:
            self.engine = "auto"
        self.rate = _clamp(self.rate, 80, 400, 185)


@dataclass
class ListeningSettings:
    input_device: Optional[Any] = None
    stt_engine: str = "whisper"
    whisper_model: str = "base.en"
    compute_type: str = "int8"
    language: Optional[str] = "en"
    vad_aggressiveness: int = 2
    pre_roll_ms: int = 360
    end_silence_ms: int = 700
    min_utterance_ms: int = 300
    max_utterance_ms: int = 30000
    command_end_silence_ms: int = 1500
    max_command_wait_ms: int = 12000
    samplerate: int = 16000
    spotter_enabled: bool = False           # gated on the S2 spike
    barge_in: bool = True                   # D12

    def validate(self) -> None:
        if self.stt_engine not in STT_ENGINES:
            self.stt_engine = "whisper"
        self.vad_aggressiveness = _clamp(self.vad_aggressiveness, 0, 3, 2)
        self.pre_roll_ms = _clamp(self.pre_roll_ms, 0, 2000, 360)
        self.end_silence_ms = _clamp(self.end_silence_ms, 200, 3000, 700)
        self.min_utterance_ms = _clamp(self.min_utterance_ms, 60, 5000, 300)
        self.max_utterance_ms = _clamp(self.max_utterance_ms, 1000, 120000, 30000)
        self.command_end_silence_ms = _clamp(
            self.command_end_silence_ms, 200, 10000, 1500)
        self.max_command_wait_ms = _clamp(
            self.max_command_wait_ms, 1000, 60000, 12000)
        if self.samplerate not in (8000, 16000, 32000, 48000):
            self.samplerate = 16000


@dataclass
class PrivacySettings:
    #: Conversations are kept until you delete them (D5). Auto-delete is an
    #: opt-in convenience, off by default (open question 5).
    auto_delete_enabled: bool = False
    retention_days: int = 90
    #: "Debug: include transcripts in logs" — off, warns when enabled, and
    #: switches itself off after 24 hours (§6.3).
    transcript_debug: bool = False
    transcript_debug_until: float = 0.0
    encrypt_conversations: bool = True

    def validate(self) -> None:
        self.retention_days = _clamp(self.retention_days, 1, 3650, 90)


@dataclass
class AdvancedSettings:
    log_level: str = "INFO"
    first_run_complete: bool = False
    migrated_from_config_json: bool = False
    settings_version: int = 1

    def validate(self) -> None:
        level = str(self.log_level or "INFO").upper()
        self.log_level = level if level in LOG_LEVELS else "INFO"


@dataclass
class Settings:
    general: GeneralSettings = field(default_factory=GeneralSettings)
    ai: AiSettings = field(default_factory=AiSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    listening: ListeningSettings = field(default_factory=ListeningSettings)
    privacy: PrivacySettings = field(default_factory=PrivacySettings)
    advanced: AdvancedSettings = field(default_factory=AdvancedSettings)

    # -- serialisation ---------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Settings":
        """Build from a (possibly partial, possibly hostile) dict.

        Unknown keys are ignored and bad values fall back to their default, so
        a hand-edited or half-migrated file can never stop the app starting.
        """
        settings = cls()
        for section in fields(cls):
            values = (data or {}).get(section.name)
            if not isinstance(values, dict):
                continue
            target = getattr(settings, section.name)
            known = {f.name: f for f in fields(target)} if is_dataclass(target) else {}
            for key, value in values.items():
                if key in known:
                    setattr(target, key, value)
        settings.validate()
        return settings

    def validate(self) -> "Settings":
        for section in fields(self):
            target = getattr(self, section.name)
            validator = getattr(target, "validate", None)
            if callable(validator):
                validator()
        return self

    # -- dotted access, used by the settings bridge -----------------------
    def get(self, path: str, default=None):
        section, _, key = path.partition(".")
        target = getattr(self, section, None)
        if target is None or not key:
            return target if target is not None else default
        return getattr(target, key, default)

    def set(self, path: str, value) -> bool:
        section, _, key = path.partition(".")
        target = getattr(self, section, None)
        if target is None or not key or not hasattr(target, key):
            return False
        setattr(target, key, value)
        self.validate()
        return True
