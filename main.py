#!/usr/bin/env python3
"""Star Trek Computer — entry point.

Usage:
  python main.py                     run the background assistant (tray icon)
  python main.py --selftest [WAV]    run a WAV through the real audio chain
                                     (VAD + whisper + wake word) and print what
                                     would be sent to the LLM / spoken
  python main.py --smoke             build the tray app without audio, then exit
  python main.py --list-devices      list input devices and exit

Options:
  --config PATH                      path to config.json (default: ./config.json)
"""
from __future__ import annotations

import argparse
import logging
import sys
import wave
from typing import Optional

from starcop import __version__
from starcop.config import Config, load_config

log = logging.getLogger("starcop.main")


def setup_logging(cfg: Config) -> None:
    handlers = [logging.StreamHandler(sys.stdout)]
    if cfg.log_file:
        try:
            handlers.append(logging.FileHandler(cfg.log_file, encoding="utf-8"))
        except OSError as exc:
            print(f"warning: cannot open log file {cfg.log_file}: {exc}",
                  file=sys.stderr)
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


def build_pipeline(cfg: Config, llm=None, tts=None):
    """Wire transcriber + LLM + TTS + wake word + buffer into a Pipeline."""
    from starcop.llm import make_llm
    from starcop.pipeline import Pipeline
    from starcop.transcriber import WhisperTranscriber
    from starcop.transcript import TranscriptBuffer
    from starcop.tts import make_tts
    from starcop.wakeword import WakeWordMatcher

    transcriber = WhisperTranscriber(model_size=cfg.whisper_model,
                                     compute_type=cfg.compute_type,
                                     language=cfg.language)
    return Pipeline(
        transcriber=transcriber,
        llm=llm if llm is not None else make_llm(cfg.llm),
        tts=tts if tts is not None else make_tts(cfg.tts),
        wakeword=WakeWordMatcher(cfg.trigger_words),
        buffer=TranscriptBuffer(max_seconds=cfg.context_seconds, max_chars=4000),
        cfg=cfg,
    )


def run_selftest(cfg: Config, wav_path: Optional[str]) -> int:
    """Feed a WAV file through the real VAD + segmenter + whisper + wake word.

    Uses mock LLM/TTS by default so no Ollama or speakers are required;
    prints exactly what would be sent to the LLM and spoken.
    """
    from starcop.llm import MockBackend
    from starcop.segmenter import UtteranceSegmenter
    from starcop.tts import MockTts
    from starcop.vad import WebrtcVad, frame_bytes

    if not wav_path:
        print("usage: python main.py --selftest <file.wav>")
        return 2

    pipeline = build_pipeline(cfg, llm=MockBackend(), tts=MockTts())
    vad = WebrtcVad(aggressiveness=cfg.vad_aggressiveness, samplerate=cfg.samplerate)
    segmenter = UtteranceSegmenter(
        vad, frame_ms=30, pre_roll_ms=cfg.pre_roll_ms,
        end_silence_ms=cfg.end_silence_ms, min_utterance_ms=cfg.min_utterance_ms,
        max_utterance_ms=cfg.max_utterance_ms)

    with wave.open(wav_path, "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            print(f"error: {wav_path} must be mono 16-bit PCM WAV "
                  f"(got channels={wf.getnchannels()} width={wf.getsampwidth()})")
            return 2
        if wf.getframerate() != cfg.samplerate:
            print(f"error: {wav_path} must be {cfg.samplerate} Hz "
                  f"(got {wf.getframerate()}); convert with afconvert/ffmpeg")
            return 2
        pcm = wf.readframes(wf.getnframes())

    size = frame_bytes(cfg.samplerate, 30)
    now = [0.0]

    def clock() -> float:  # deterministic: advances per audio frame
        return now[0]

    pipeline.clock = clock

    print(f"selftest: {wav_path} ({len(pcm)} bytes, {cfg.samplerate} Hz)")
    for i in range(0, len(pcm) - size + 1, size):
        frame = pcm[i:i + size]
        now[0] += 30 / 1000.0
        utterance = segmenter.process_frame(frame)
        if utterance is not None:
            pipeline.feed_utterance(utterance)
        pipeline.tick(now[0])

    # End of file: flush any in-progress utterance.
    tail = segmenter.flush()
    if tail is not None:
        pipeline.feed_utterance(tail)

    # Force any pending command to dispatch.
    now[0] += cfg.max_command_wait_ms / 1000.0 + 1.0
    pipeline.tick(now[0])

    print("\n--- transcript ---")
    for line in (pipeline.buffer.recent_text() or "(nothing transcribed)").splitlines():
        print(line)

    mock_llm: MockBackend = pipeline.llm  # type: ignore[assignment]
    if not mock_llm.calls:
        print("\nno wake word detected — nothing would be sent to the LLM")
        return 1

    print("\n--- LLM calls ---")
    for context, query in mock_llm.calls:
        print(f"query:   {query!r}")
        print("context:\n" + (context or "(empty)"))

    mock_tts: MockTts = pipeline.tts  # type: ignore[assignment]
    print("\n--- would speak ---")
    for text in mock_tts.spoken:
        print(text)
    return 0


def run_smoke(cfg: Config) -> int:
    """Build the tray app without touching the mic, run the event loop briefly."""
    from PySide6.QtCore import QTimer

    from starcop.app import create_tray_app
    from starcop.llm import MockBackend
    from starcop.tts import MockTts

    pipeline = build_pipeline(cfg, llm=MockBackend(), tts=MockTts())

    def _disabled_start() -> None:
        raise RuntimeError("smoke mode: audio disabled")

    app, _tray = create_tray_app(
        pipeline,
        start_listening=_disabled_start,
        stop_all=lambda: None,
        autostart=False,
    )
    QTimer.singleShot(800, app.quit)
    rc = app.exec()
    print("smoke ok: tray app constructed and event loop ran")
    return rc


def run_app(cfg: Config) -> int:
    """Run the full background assistant with tray UI."""
    from starcop.app import create_tray_app
    from starcop.audio import AudioCapture
    from starcop.runner import Runner
    from starcop.segmenter import UtteranceSegmenter
    from starcop.vad import WebrtcVad

    pipeline = build_pipeline(cfg)
    state: dict = {"capture": None, "runner": None}

    def start_listening() -> None:
        capture = AudioCapture(samplerate=cfg.samplerate, frame_ms=30,
                               device=cfg.audio_device)
        capture.start()
        vad = WebrtcVad(aggressiveness=cfg.vad_aggressiveness,
                        samplerate=cfg.samplerate)
        segmenter = UtteranceSegmenter(
            vad, frame_ms=30, pre_roll_ms=cfg.pre_roll_ms,
            end_silence_ms=cfg.end_silence_ms, min_utterance_ms=cfg.min_utterance_ms,
            max_utterance_ms=cfg.max_utterance_ms)
        runner = Runner(capture, segmenter, pipeline)
        state["capture"] = capture
        state["runner"] = runner
        runner.start()

    def stop_all() -> None:
        if state["runner"] is not None:
            state["runner"].stop(timeout=2.0)
            state["runner"] = None
        # Kill in-flight speech so quitting is immediate.
        tts_stop = getattr(pipeline.tts, "stop", None)
        if callable(tts_stop):
            try:
                tts_stop()
            except Exception:  # noqa: BLE001 - best effort shutdown
                pass
        if state["capture"] is not None:
            state["capture"].stop()
            state["capture"] = None

    app, _tray = create_tray_app(pipeline, start_listening, stop_all,
                                 autostart=True)
    return app.exec()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Star Trek Computer — background wake-word voice assistant")
    parser.add_argument("--config", help="path to config.json (default: ./config.json)")
    parser.add_argument("--selftest", nargs="?", const="__none__", metavar="WAV",
                        help="run a WAV through the audio chain and exit")
    parser.add_argument("--smoke", action="store_true",
                        help="build the tray app without audio, then exit")
    parser.add_argument("--list-devices", action="store_true",
                        help="list input devices and exit")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg)

    if args.list_devices:
        from starcop.audio import list_devices

        for line in list_devices():
            print(line)
        return 0

    if args.selftest is not None:
        wav = None if args.selftest == "__none__" else args.selftest
        return run_selftest(cfg, wav)

    if args.smoke:
        return run_smoke(cfg)

    log.info("starting Star Trek Computer v%s", __version__)
    return run_app(cfg)


if __name__ == "__main__":
    sys.exit(main())
