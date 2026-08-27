"""End-to-end audio test: real speech (macOS `say`) through webrtcvad +
segmenter + faster-whisper + wake-word matcher.

Run with:  pytest -m integration
(Downloads the whisper model on first run; needs `say` + `afconvert`, i.e. macOS.)
"""
import shutil
import subprocess
import wave

import pytest

pytestmark = pytest.mark.integration


def _have_audio_chain() -> bool:
    try:
        import faster_whisper  # noqa: F401
        import webrtcvad  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.fixture(scope="module")
def speech_wav(tmp_path_factory):
    if not _have_audio_chain():
        pytest.skip("faster-whisper/webrtcvad not installed")
    if shutil.which("say") is None or shutil.which("afconvert") is None:
        pytest.skip("`say`/`afconvert` (macOS) not available")
    tmp = tmp_path_factory.mktemp("audio")
    aiff = tmp / "speech.aiff"
    wav = tmp / "speech.wav"
    text = "Computer, what is the current status of the ship?"
    subprocess.run(["say", "-o", str(aiff), text], check=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
         str(aiff), str(wav)], check=True)
    return wav


def test_full_audio_chain(speech_wav):
    from starcop.segmenter import UtteranceSegmenter
    from starcop.transcriber import WhisperTranscriber
    from starcop.vad import WebrtcVad, frame_bytes
    from starcop.wakeword import WakeWordMatcher

    vad = WebrtcVad(aggressiveness=2, samplerate=16000)
    segmenter = UtteranceSegmenter(
        vad, frame_ms=30, pre_roll_ms=360, end_silence_ms=700,
        min_utterance_ms=300, max_utterance_ms=30000)
    transcriber = WhisperTranscriber(model_size="base.en", compute_type="int8",
                                     language="en")
    matcher = WakeWordMatcher(["computer"])

    with wave.open(str(speech_wav), "rb") as wf:
        pcm = wf.readframes(wf.getnframes())

    size = frame_bytes(16000, 30)
    texts: list[str] = []

    def handle(utterance) -> None:
        if utterance is not None:
            text = transcriber.transcribe(utterance)
            if text:
                texts.append(text)

    for i in range(0, len(pcm) - size + 1, size):
        handle(segmenter.process_frame(pcm[i:i + size]))
    handle(segmenter.flush())  # end of file: flush the tail

    joined = " ".join(texts).lower()
    assert matcher.match(joined) == "computer", f"trigger not found in {texts!r}"
    assert "status" in joined, f"'status' not recovered: {texts!r}"
