"""First-run migration from the old ``config.json`` and ``OPENAI_API_KEY``.

Nobody should have to set the app up twice. This reads a pre-refactor config
if one is still lying around, maps it onto the new schema, and — if the old
environment variable happens to be visible in this process — offers to move
the key into the keychain, after which the export in the shell profile can be
deleted (§6.2). It is idempotent: it records that it ran and never runs again.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("bgassist.settings.migrate")

#: Old backend names -> new provider names.
_PROVIDERS = {
    "ollama": "ollama",
    "openai_compatible": "openai",
    "openai": "openai",
    "mock": "mock",
}


@dataclass
class MigrationResult:
    ran: bool = False
    source: Optional[str] = None
    applied: List[str] = None
    key_found_in_env: bool = False
    key_env_name: str = ""
    notes: List[str] = None

    def __post_init__(self) -> None:
        self.applied = self.applied or []
        self.notes = self.notes or []


def find_legacy_config(explicit: Optional[Path] = None) -> Optional[Path]:
    from bgassist.platform import paths

    candidates = [explicit] if explicit else paths.legacy_config_candidates()
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def _map(data: Dict) -> Dict[str, object]:
    """Old flat config -> new dotted settings paths."""
    changes: Dict[str, object] = {}
    simple = {
        "trigger_words": "general.trigger_words",
        "language": "listening.language",
        "whisper_model": "listening.whisper_model",
        "compute_type": "listening.compute_type",
        "audio_device": "listening.input_device",
        "samplerate": "listening.samplerate",
        "vad_aggressiveness": "listening.vad_aggressiveness",
        "pre_roll_ms": "listening.pre_roll_ms",
        "end_silence_ms": "listening.end_silence_ms",
        "min_utterance_ms": "listening.min_utterance_ms",
        "max_utterance_ms": "listening.max_utterance_ms",
        "command_end_silence_ms": "listening.command_end_silence_ms",
        "max_command_wait_ms": "listening.max_command_wait_ms",
        "context_seconds": "ai.context_seconds",
        "log_level": "advanced.log_level",
    }
    for old, new in simple.items():
        if old in data and data[old] is not None:
            changes[new] = data[old]

    llm = data.get("llm") or {}
    if llm.get("backend"):
        changes["ai.provider"] = _PROVIDERS.get(str(llm["backend"]).lower(), "openai")
    if llm.get("base_url"):
        changes["ai.base_url"] = llm["base_url"]
    if llm.get("model"):
        changes["ai.model"] = llm["model"]
    if llm.get("timeout_s"):
        changes["ai.timeout_s"] = llm["timeout_s"]

    tts = data.get("tts") or {}
    if tts.get("engine"):
        engine = str(tts["engine"]).lower()
        changes["voice.engine"] = engine if engine != "auto" else "auto"
    if tts.get("rate"):
        changes["voice.rate"] = tts["rate"]
    if tts.get("voice"):
        changes["voice.voice"] = tts["voice"]

    # log_file is deliberately not migrated: logs now live in the OS log
    # directory and rotate (F3, F11).
    return changes


def migrate(store, secrets=None, config_path: Optional[Path] = None,
            environ: Optional[Dict[str, str]] = None,
            move_env_key: bool = True) -> MigrationResult:
    """Run the migration if it has not run before. Safe to call every start."""
    result = MigrationResult()
    if getattr(store.settings.advanced, "migrated_from_config_json", False):
        return result

    environ = os.environ if environ is None else environ
    legacy = find_legacy_config(config_path)
    data: Dict = {}
    if legacy is not None:
        try:
            loaded = json.loads(legacy.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
                result.source = str(legacy)
        except (OSError, ValueError) as exc:
            log.error("could not read legacy config %s: %s", legacy, exc)
            result.notes.append(f"The old config at {legacy} could not be read.")

    changes = _map(data) if data else {}

    # The old config named an environment variable to read the key from; the
    # variable itself is the thing that never survived to a GUI launch (F2).
    key_env = str((data.get("llm") or {}).get("api_key_env") or "OPENAI_API_KEY")
    key = environ.get(key_env, "")
    if key:
        result.key_found_in_env = True
        result.key_env_name = key_env
        if move_env_key and secrets is not None:
            provider = changes.get("ai.provider", store.settings.ai.provider)
            account = "anthropic" if provider == "anthropic" else "openai"
            secrets.set(account, key)
            result.notes.append(
                f"Your API key was copied from ${key_env} into the keychain. "
                f"You can now remove that export from your shell profile.")

    changes["advanced.migrated_from_config_json"] = True
    applied = store.update(changes)
    result.ran = True
    result.applied = [a for a in applied if a != "advanced.migrated_from_config_json"]
    if result.source:
        log.info("migrated %d setting(s) from %s", len(result.applied), result.source)
    return result
