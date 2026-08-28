"""The composition root: everything is built here and injected everywhere else.

Nothing below this file reaches out for its own dependencies, which is why the
engine is testable without a microphone, the orchestrator without a network,
and the UI without either.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from bgassist import APP_NAME, __version__
from bgassist.core import events
from bgassist.core.orchestrator import Orchestrator, State
from bgassist.core.transcript import TranscriptBuffer
from bgassist.core.trigger import TriggerParser

log = logging.getLogger("bgassist.app")


@dataclass
class EngineConfig:
    """The flat view of settings the orchestrator actually reads."""

    command_end_silence_ms: int = 1500
    max_command_wait_ms: int = 12000
    context_seconds: float = 120.0
    barge_in: bool = True
    auto_title: bool = True
    speak_typed_answers: bool = False

    @classmethod
    def from_settings(cls, settings) -> "EngineConfig":
        return cls(
            command_end_silence_ms=settings.listening.command_end_silence_ms,
            max_command_wait_ms=settings.listening.max_command_wait_ms,
            context_seconds=settings.ai.context_seconds,
            barge_in=settings.listening.barge_in,
            auto_title=settings.ai.auto_title,
            speak_typed_answers=settings.voice.speak_typed_answers,
        )


class Application:
    """Owns the long-lived objects and rebuilds them when settings change."""

    def __init__(self, settings_store=None, secrets=None, conversations=None,
                 llm=None, tts=None, transcriber=None, bus=None,
                 start_engine: bool = True):
        from bgassist.settings.secrets import SecretStore
        from bgassist.settings.store import SettingsStore

        self.bus = bus or events.EventBus()
        self.settings_store = settings_store or SettingsStore()
        self.secrets = secrets if secrets is not None else SecretStore()
        self.start_engine = start_engine

        self._llm_override = llm
        self._tts_override = tts
        self._transcriber_override = transcriber

        self.conversations = conversations or self._build_conversations()
        self.buffer = TranscriptBuffer(
            max_seconds=max(30.0, self.settings.ai.context_seconds))
        self.trigger = self._build_trigger()
        self.llm = llm or self.build_llm()
        self.tts = tts or self._build_tts()
        self.transcriber = transcriber or self._build_transcriber()

        self.orchestrator = Orchestrator(
            llm=self.llm, tts=self.tts, trigger=self.trigger, buffer=self.buffer,
            cfg=EngineConfig.from_settings(self.settings), bus=self.bus,
            conversations=self.conversations, transcriber=self.transcriber,
            threaded_responder=True)

        self.capture = None
        self.engine = None
        self.chime = None
        self.notifier = None
        self.tray = None
        self._chat_window = None
        self._prefs_window = None
        self.last_error = ""

        self.settings_store.subscribe(self._on_settings_changed)
        self.bus.subscribe(events.TriggerSpotted, self._on_trigger_spotted)
        self.bus.subscribe(events.ErrorOccurred, self._on_error)
        self.bus.subscribe(events.AnswerFinished, self._on_answer_finished)

    # -- settings ---------------------------------------------------------
    @property
    def settings(self):
        return self.settings_store.settings

    def api_key(self, provider: Optional[str] = None) -> str:
        from bgassist.llm import PRESETS

        provider = provider or self.settings.ai.provider
        account = PRESETS.get(provider, {}).get("keyring_account", provider)
        return self.secrets.get(account)

    def set_api_key(self, provider: str, key: str) -> bool:
        """Store the key. False when it will not survive a restart."""
        from bgassist.llm import PRESETS

        account = PRESETS.get(provider, {}).get("keyring_account", provider)
        return bool(self.secrets.set(account, key))

    # -- builders ---------------------------------------------------------
    def _build_conversations(self):
        from bgassist.storage import ConversationStore
        from bgassist.storage.crypto import make_cipher

        cipher = make_cipher(self.secrets,
                             enabled=self.settings.privacy.encrypt_conversations)
        return ConversationStore(cipher=cipher)

    def _build_trigger(self) -> TriggerParser:
        general = self.settings.general
        return TriggerParser(general.trigger_words, sensitivity=general.sensitivity)

    def build_llm(self):
        from bgassist.llm import make_llm

        if self._llm_override is not None:
            return self._llm_override
        return make_llm(self.settings.ai, api_key=self.api_key(),
                        system_prompt=self.settings.ai.system_prompt)

    def _build_tts(self):
        from bgassist.tts import make_tts

        if self._tts_override is not None:
            return self._tts_override
        try:
            return make_tts(self.settings.voice)
        except Exception as exc:  # noqa: BLE001 - never fail to start over a voice
            log.error("could not build the voice engine (%s); using a silent one", exc)
            from bgassist.tts.mock import MockTts

            return MockTts()

    def _build_transcriber(self):
        if self._transcriber_override is not None:
            return self._transcriber_override
        listening = self.settings.listening
        if listening.stt_engine == "apple":
            try:
                from bgassist.stt.apple import AppleTranscriber

                return AppleTranscriber(language=listening.language or "en-US",
                                        samplerate=listening.samplerate)
            except Exception as exc:  # noqa: BLE001 - documented fallback (D6)
                log.warning("Apple speech recognition unavailable (%s); using "
                            "Whisper", exc)
        from bgassist.stt.whisper import WhisperTranscriber

        return WhisperTranscriber(model_size=listening.whisper_model,
                                  compute_type=listening.compute_type,
                                  language=listening.language)

    # -- listening --------------------------------------------------------
    def start_listening(self) -> None:
        from bgassist.audio.capture import AudioCapture
        from bgassist.audio.spotter import make_spotter
        from bgassist.audio.vad import WebrtcVad
        from bgassist.core.segmenter import UtteranceSegmenter
        from bgassist.engine import Engine

        if self.engine is not None and self.engine.running:
            return
        listening = self.settings.listening
        self.capture = AudioCapture(samplerate=listening.samplerate, frame_ms=30,
                                    device=listening.input_device)
        self.capture.start()
        segmenter = UtteranceSegmenter(
            WebrtcVad(aggressiveness=listening.vad_aggressiveness,
                      samplerate=listening.samplerate),
            frame_ms=30, pre_roll_ms=listening.pre_roll_ms,
            end_silence_ms=listening.end_silence_ms,
            min_utterance_ms=listening.min_utterance_ms,
            max_utterance_ms=listening.max_utterance_ms)
        spotter = make_spotter(self.settings.general,
                               enabled=listening.spotter_enabled)
        self.engine = Engine(self.capture, segmenter, self.transcriber,
                             self.orchestrator, spotter=spotter, bus=self.bus)
        self.orchestrator.retro_transcribe = self.engine.retro_transcribe
        self.engine.start()

    def stop_listening(self) -> None:
        if self.engine is not None:
            self.engine.stop(timeout=3.0)
            self.engine = None
        if self.capture is not None:
            self.capture.stop()
            self.capture = None

    @property
    def listening(self) -> bool:
        return self.engine is not None and self.engine.running

    def shutdown(self) -> None:
        """Stop everything. Must not raise — this runs on Quit (F1)."""
        try:
            self.stop_listening()
        except Exception:  # noqa: BLE001
            log.exception("stopping the engine failed")
        try:
            self.orchestrator.cancel(reason="quit")
        except Exception:  # noqa: BLE001
            log.exception("cancelling the answer failed")
        for name in ("stop", "shutdown"):
            method = getattr(self.tts, name, None)
            if callable(method):
                try:
                    method()
                except Exception:  # noqa: BLE001
                    pass
        try:
            self.conversations.close()
        except Exception:  # noqa: BLE001
            pass

    # -- reactions --------------------------------------------------------
    def _on_settings_changed(self, settings, changed) -> None:
        keys = set(changed)
        log.info("settings changed: %s", ", ".join(sorted(keys)) or "(all)")
        if any(k.startswith("general.") for k in keys) or "*" in keys:
            self.trigger = self._build_trigger()
            self.orchestrator.trigger = self.trigger
        if any(k.startswith("ai.") for k in keys) or "*" in keys:
            self.llm = self.build_llm()
            self.orchestrator.llm = self.llm
            self.orchestrator.responder.llm = self.llm
        if any(k.startswith("voice.") for k in keys) or "*" in keys:
            self.tts = self._build_tts()
            self.orchestrator.tts = self.tts
            self.orchestrator.responder.tts = self.tts
            if self.chime is not None:
                self.chime.enabled = settings.voice.chime
        self.orchestrator.cfg = EngineConfig.from_settings(settings)
        self.buffer.max_seconds = max(30.0, settings.ai.context_seconds)
        if self.notifier is not None:
            self.notifier.enabled = settings.general.notifications
        listening_changed = any(k.startswith("listening.") for k in keys) or "*" in keys
        if listening_changed and self.listening:
            # Capture, VAD and the model all come from these values, so the
            # only honest way to apply them live is to restart the engine.
            self.stop_listening()
            self.transcriber = self._build_transcriber()
            self.orchestrator.transcriber = self.transcriber
            self.start_listening()
        if "general.launch_at_login" in keys:
            from bgassist.platform import login_item

            login_item.set_enabled(settings.general.launch_at_login)
        if "advanced.log_level" in keys:
            logging.getLogger().setLevel(
                getattr(logging, settings.advanced.log_level, logging.INFO))
        if "privacy.transcript_debug" in keys:
            from bgassist.logging_setup import redacting_filter

            if settings.privacy.transcript_debug:
                redacting_filter().allow_transcripts()
            else:
                redacting_filter().deny_transcripts()

    def _on_trigger_spotted(self, event) -> None:
        if self.chime is not None and self.orchestrator.state is State.IDLE:
            self.chime.play()

    def _on_error(self, event) -> None:
        self.last_error = event.message

    def _on_answer_finished(self, event) -> None:
        if self.notifier is not None and event.text.strip():
            self.notifier.notify(APP_NAME, event.text.strip())
        privacy = self.settings.privacy
        if privacy.auto_delete_enabled:
            try:
                self.conversations.purge_older_than(privacy.retention_days)
            except Exception:  # noqa: BLE001
                log.exception("auto-delete failed")

    # -- Qt ---------------------------------------------------------------
    def run(self) -> int:
        """Build the tray UI and run the Qt event loop."""
        from bgassist.audio.chime import Chime
        from bgassist.platform.notify import Notifier
        from bgassist.ui.tray import create_tray

        self.chime = Chime(enabled=self.settings.voice.chime)
        qt_app, tray = create_tray(self)
        self.tray = tray
        self.notifier = Notifier(enabled=self.settings.general.notifications,
                                 tray=tray)
        if self.start_engine and self.settings.general.autostart_listening:
            try:
                self.start_listening()
            except Exception as exc:  # noqa: BLE001 - surfaced in the tray
                log.exception("could not start listening")
                self.last_error = str(exc)
        log.info("%s v%s ready", APP_NAME, __version__)
        return qt_app.exec()

    # -- windows ----------------------------------------------------------
    def show_chat(self) -> None:
        from bgassist.ui.chat_window import ChatWindow

        if self._chat_window is None:
            self._chat_window = ChatWindow(self)
        self._chat_window.show_and_raise()

    def show_preferences(self) -> None:
        from bgassist.ui.prefs_window import PreferencesWindow

        if self._prefs_window is None:
            self._prefs_window = PreferencesWindow(self)
        self._prefs_window.show_and_raise()


def build_application(**kwargs) -> Application:
    """Build the app, running the first-run migration before anything else."""
    from bgassist.settings.migrate import migrate

    application = Application(**kwargs)
    result = migrate(application.settings_store, application.secrets)
    if result.notes:
        for note in result.notes:
            log.info("migration: %s", note)
    return application
