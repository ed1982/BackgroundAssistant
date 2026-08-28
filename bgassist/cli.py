"""Command line entry point.

    backgroundassistant                 run the assistant (menu-bar app)
    backgroundassistant --selftest WAV  run a WAV through the real audio chain
    backgroundassistant --check         wire everything up with fakes, no audio
    backgroundassistant --smoke         build the tray UI, then exit
    backgroundassistant --list-devices  list input devices
    backgroundassistant --doctor        report what is installed and configured

``--selftest`` is the single most useful debugging tool in the repo, so it has
grown rather than shrunk: it now prints the trigger classification and can
assert an expected phrasing, and ``--check`` gives the same end-to-end
confidence with no microphone, no model and no network at all.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import wave
from typing import Optional

from bgassist import APP_NAME, __version__

log = logging.getLogger("bgassist.cli")


def _setup_logging(level: str = "INFO") -> None:
    from bgassist.logging_setup import setup_logging

    setup_logging(level)


# -- self test ------------------------------------------------------------

def run_selftest(wav_path: Optional[str], expect: Optional[str] = None,
                 model: Optional[str] = None) -> int:
    """Feed a WAV through the real VAD, segmenter, Whisper and trigger grammar.

    Mock LLM and TTS, so no provider and no speakers are needed; prints exactly
    what would be sent and spoken.
    """
    from bgassist.app import Application
    from bgassist.audio.vad import WebrtcVad, frame_bytes
    from bgassist.core.segmenter import UtteranceSegmenter
    from bgassist.llm.mock import MockBackend
    from bgassist.tts.mock import MockTts

    if not wav_path:
        print("usage: backgroundassistant --selftest <file.wav>")
        return 2

    application = Application(llm=MockBackend(), tts=MockTts(),
                              start_engine=False)
    listening = application.settings.listening
    if model:
        listening.whisper_model = model
    from bgassist.stt.whisper import WhisperTranscriber

    transcriber = WhisperTranscriber(model_size=listening.whisper_model,
                                     compute_type=listening.compute_type,
                                     language=listening.language)
    orchestrator = application.orchestrator
    orchestrator.transcriber = transcriber

    segmenter = UtteranceSegmenter(
        WebrtcVad(aggressiveness=listening.vad_aggressiveness,
                  samplerate=listening.samplerate),
        frame_ms=30, pre_roll_ms=listening.pre_roll_ms,
        end_silence_ms=listening.end_silence_ms,
        min_utterance_ms=listening.min_utterance_ms,
        max_utterance_ms=listening.max_utterance_ms)

    with wave.open(wav_path, "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            print(f"error: {wav_path} must be mono 16-bit PCM WAV "
                  f"(got channels={wf.getnchannels()} width={wf.getsampwidth()})")
            return 2
        if wf.getframerate() != listening.samplerate:
            print(f"error: {wav_path} must be {listening.samplerate} Hz "
                  f"(got {wf.getframerate()}); convert with afconvert/ffmpeg")
            return 2
        pcm = wf.readframes(wf.getnframes())

    size = frame_bytes(listening.samplerate, 30)
    now = [0.0]
    orchestrator.clock = lambda: now[0]

    heard: list = []
    classifications: list = []
    original = orchestrator.on_transcript

    def observe(text: str, ts=None) -> None:
        heard.append(text)
        found = orchestrator.trigger.find(text)
        if found is not None:
            classifications.append(
                (text, found.position.value,
                 "accepted" if orchestrator.trigger.accepts(found) else "ignored",
                 found.marked()))
        original(text, ts)

    orchestrator.on_transcript = observe

    print(f"selftest: {wav_path} ({len(pcm)} bytes, {listening.samplerate} Hz)")
    for i in range(0, len(pcm) - size + 1, size):
        now[0] += 30 / 1000.0
        utterance = segmenter.process_frame(pcm[i:i + size])
        if utterance is not None:
            orchestrator.feed_utterance(utterance)
        orchestrator.tick(now[0])

    tail = segmenter.flush()
    if tail is not None:
        orchestrator.feed_utterance(tail)
    now[0] += listening.max_command_wait_ms / 1000.0 + 1.0
    orchestrator.tick(now[0])
    orchestrator.responder.wait(30)

    print("\n--- transcript ---")
    print(orchestrator.buffer.recent_text() or "(nothing transcribed)")

    print("\n--- trigger ---")
    if not classifications:
        print("no trigger word detected")
    for _text, position, verdict, marked in classifications:
        print(f"{position:8} {verdict:8} {marked}")

    llm = application.llm
    if not llm.calls:
        print("\nnothing would be sent to the model")
        return 1

    print("\n--- would be sent ---")
    for context, query in llm.calls:
        print(f"query:   {query!r}")
        print("context:\n" + (context or "(empty)"))

    print("\n--- would speak ---")
    for text in application.tts.spoken:
        print(text)

    if expect:
        actual = classifications[0][1] if classifications else "none"
        if actual != expect:
            print(f"\nFAIL: expected a {expect} trigger, classified {actual}")
            return 1
        print(f"\nOK: classified as {actual}, as expected")
    return 0


# -- headless wiring check ------------------------------------------------

def run_check() -> int:
    """Drive the whole app with fakes: no microphone, no model, no network.

    This is the check that runs anywhere — including a Linux CI box — and still
    exercises the trigger grammar, the orchestrator, the responder, the
    conversation store and the settings bridge end to end.
    """
    import tempfile

    from bgassist.app import Application
    from bgassist.llm.mock import MockBackend
    from bgassist.settings.secrets import MemorySecretStore
    from bgassist.storage import ConversationStore, NullCipher
    from bgassist.stt.mock import MockTranscriber
    from bgassist.tts.mock import MockTts
    from bgassist.ui.bridge import BridgeCore

    failures: list = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if condition else 'FAIL'}  {label}"
              + (f"  — {detail}" if detail and not condition else ""))
        if not condition:
            failures.append(label)

    with tempfile.TemporaryDirectory() as tmp:
        from pathlib import Path

        from bgassist.settings.store import SettingsStore

        store = SettingsStore(path=Path(tmp) / "settings.json")
        conversations = ConversationStore(Path(tmp) / "c.db", NullCipher())
        application = Application(
            settings_store=store, conversations=conversations,
            secrets=MemorySecretStore(),
            llm=MockBackend(response="All systems nominal."), tts=MockTts(),
            transcriber=MockTranscriber(), start_engine=False)
        orchestrator = application.orchestrator

        print(f"{APP_NAME} {__version__} — headless check\n")

        print("trigger grammar")
        for text, expected in (("computer what is the time", "leading"),
                               ("what is the time, computer", "trailing"),
                               ("something happened, computer, what is the time", "medial")):
            found = orchestrator.trigger.find(text)
            check(f"{expected:8} {text!r}",
                  found is not None and found.position.value == expected)
        check("mid-sentence mention ignored at the default sensitivity",
              orchestrator.trigger.parse("my computer is broken today") is None)

        print("\nspoken exchange")
        orchestrator.on_transcript("the warp core is making a noise")
        orchestrator.on_transcript("what is that noise, computer")
        orchestrator.responder.wait(10)
        check("a trailing trigger dispatched without waiting",
              bool(application.llm.calls))
        check("the answer was spoken", bool(application.tts.spoken))
        check("the ambient line went to the model as context",
              "warp core" in (application.llm.calls[0][0] if application.llm.calls
                              else ""))

        print("\nconversation store")
        conversation = conversations.list_conversations()
        check("an exchange was stored", bool(conversation))
        if conversation:
            messages = conversations.messages(conversation[0].id)
            check("both turns were stored", len(messages) == 2)
            check("the context snapshot was kept",
                  bool(messages and messages[0].context))

        print("\ntyped exchange")
        bridge = BridgeCore(application)
        bridge.send("and what about the deflector")
        orchestrator.responder.wait(10)
        check("a typed question reached the model", len(application.llm.calls) >= 2)
        check("history was replayed to the model",
              len(conversations.history(conversation[0].id)) >= 2
              if conversation else False)

        print("\ninterruption")
        orchestrator._set_state(orchestrator.state.__class__.SPEAKING)
        orchestrator.responder.current_chunk = "a long answer in progress"
        orchestrator.interrupt(source="check")
        check("interrupting returns to idle",
              orchestrator.state.value == "idle")

        print("\nsettings and secrets")
        applied = store.update({"general.trigger_words": ["compy"]})
        check("settings save", applied == ["general.trigger_words"])
        check("the trigger was rebuilt live",
              orchestrator.trigger.triggers == ["compy"])
        settings_json = store.path.read_text()
        application.secrets.set("openai", "sk-check-value")
        check("no key is written to the settings file",
              "sk-check-value" not in settings_json)
        check("the settings bridge answers",
              bool(json.loads(json.dumps(bridge.get_settings()))["_meta"]))

        print("\nprivacy")
        from bgassist.logging_setup import redacting_filter

        check("transcripts are blocked from the log by default",
              not redacting_filter().transcripts_allowed)

        application.shutdown()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: " + ", ".join(failures))
        return 1
    print("all checks passed")
    return 0


# -- doctor ---------------------------------------------------------------

def run_doctor() -> int:
    """Report what is installed and where everything lives."""
    from bgassist.platform import paths
    from bgassist.settings.secrets import SecretStore
    from bgassist.settings.store import SettingsStore

    print(f"{APP_NAME} {__version__}")
    print(f"python        {sys.version.split()[0]} ({sys.platform})")
    for module in ("PySide6", "faster_whisper", "sounddevice", "webrtcvad",
                   "keyring", "cryptography", "platformdirs", "numpy",
                   "openwakeword", "piper"):
        try:
            __import__(module)
            print(f"{module:14}installed")
        except ImportError:
            print(f"{module:14}-")
    print(f"data          {paths.data_dir()}")
    print(f"logs          {paths.log_dir()}")
    print(f"models        {paths.models_dir()}")
    store = SettingsStore()
    settings = store.settings
    print(f"provider      {settings.ai.provider} · {settings.ai.model}")
    print(f"trigger       {', '.join(settings.general.trigger_words)}"
          + ("  (spock)" if settings.general.easter_egg else ""))
    secrets = SecretStore()
    print(f"keychain      {'available' if secrets.available else 'unavailable'}")
    accounts = secrets.accounts_with_keys(["openai", "anthropic", "local",
                                           "ollama", "custom"])
    print(f"keys stored   {', '.join(accounts) if accounts else 'none'}")
    return 0


# -- smoke ----------------------------------------------------------------

def run_smoke() -> int:
    """Build the tray UI without touching the microphone, then quit.

    Runs against a throwaway data directory and an in-memory secret store: a
    smoke test must not write into the user's real settings, and an ad-hoc
    signed binary reaching for a keychain item would put up a modal prompt and
    hang the build that is running it.
    """
    import os
    import tempfile

    with tempfile.TemporaryDirectory(prefix="bgassist-smoke-") as tmp:
        os.environ["BGASSIST_HOME"] = tmp

        from PySide6.QtCore import QTimer

        from bgassist.app import Application
        from bgassist.llm.mock import MockBackend
        from bgassist.settings.secrets import MemorySecretStore
        from bgassist.stt.mock import MockTranscriber
        from bgassist.tts.mock import MockTts
        from bgassist.ui.tray import create_tray

        application = Application(secrets=MemorySecretStore(),
                                  llm=MockBackend(), tts=MockTts(),
                                  transcriber=MockTranscriber(),
                                  start_engine=False)
        qt_app, _tray = create_tray(application)
        QTimer.singleShot(800, qt_app.quit)
        rc = qt_app.exec()
        application.shutdown()
    print("smoke ok: the tray was built and the event loop ran")
    return rc


# -- main -----------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="backgroundassistant",
        description=f"{APP_NAME} — an always-on voice assistant")
    parser.add_argument("--selftest", nargs="?", const="__none__", metavar="WAV",
                        help="run a WAV through the audio chain and exit")
    parser.add_argument("--expect", choices=("leading", "medial", "trailing"),
                        help="with --selftest: assert the trigger classification")
    parser.add_argument("--model", help="with --selftest: override the whisper model")
    parser.add_argument("--check", action="store_true",
                        help="run the headless end-to-end check and exit")
    parser.add_argument("--smoke", action="store_true",
                        help="build the tray UI without audio, then exit")
    parser.add_argument("--doctor", action="store_true",
                        help="report what is installed and configured")
    parser.add_argument("--list-devices", action="store_true",
                        help="list input devices and exit")
    parser.add_argument("--log-level", default=None,
                        help="override the configured log level")
    parser.add_argument("--version", action="version",
                        version=f"{APP_NAME} {__version__}")
    args = parser.parse_args(argv)

    _setup_logging(args.log_level or "INFO")

    if args.list_devices:
        from bgassist.audio.capture import list_devices

        for line in list_devices():
            print(line)
        return 0
    if args.doctor:
        return run_doctor()
    if args.check:
        return run_check()
    if args.selftest is not None:
        wav = None if args.selftest == "__none__" else args.selftest
        return run_selftest(wav, expect=args.expect, model=args.model)
    if args.smoke:
        return run_smoke()

    from bgassist.app import build_application

    application = build_application()
    from bgassist.logging_setup import setup_logging

    setup_logging(application.settings.advanced.log_level,
                  allow_transcripts=application.settings.privacy.transcript_debug)
    log.info("starting %s v%s", APP_NAME, __version__)
    try:
        return application.run()
    finally:
        application.shutdown()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
