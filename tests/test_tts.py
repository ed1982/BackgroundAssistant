import subprocess
import sys
import types
from types import SimpleNamespace

import pytest

from bgassist.tts import MockTts, Pyttsx3Tts, SayTts, TtsError, make_tts

INSTALLED = ["Alex", "Daniel", "Samantha", "Tessa"]


@pytest.fixture(autouse=True)
def _installed_voices(monkeypatch):
    """Never shell out to `say -v ?` in a test, and never depend on which
    voices this particular machine happens to have."""
    monkeypatch.setattr(SayTts, "_voice_cache", list(INSTALLED))
    yield
    SayTts._voice_cache = None


def test_say_command(monkeypatch):
    calls: list = []

    class FakeProc:
        def __init__(self, cmd):
            calls.append(cmd)

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    t = SayTts(rate=170, voice="Samantha")
    t.speak("hello there")
    assert calls[0] == ["say", "-r", "170", "-v", "Samantha", "hello there"]


def test_say_spawn_failure_raises(monkeypatch):
    def boom(cmd, **kw):
        raise OSError("no say")

    monkeypatch.setattr(subprocess, "Popen", boom)
    with pytest.raises(TtsError):
        SayTts().speak("x")


def test_say_nonzero_exit_raises(monkeypatch):
    class FakeProc:
        def __init__(self, cmd):
            pass

        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    with pytest.raises(TtsError):
        SayTts().speak("x")


def test_an_uninstalled_voice_falls_back_instead_of_going_silent(monkeypatch):
    """`say -v Pippa` on a Mac without Pippa exits non-zero, which used to
    surface as "TTS failed" and total silence — losing the assistant's voice
    over a preference."""
    calls: list = []
    monkeypatch.setattr(subprocess, "Popen",
                        lambda cmd, **kw: _FakeProc(cmd, calls))
    engine = SayTts(rate=180, voice="Pippa")   # a Siri voice: never available
    assert engine.voice == "Tessa"             # the preferred default
    engine.speak("hello")
    assert "-v" in calls[0] and "Tessa" in calls[0]


def test_automatic_prefers_tessa():
    assert SayTts(voice=None).voice == "Tessa"


def test_automatic_falls_through_to_whatever_is_installed(monkeypatch):
    monkeypatch.setattr(SayTts, "_voice_cache", ["Fred", "Alex"])
    assert SayTts(voice=None).voice is None    # let `say` pick


class _FakeProc:
    def __init__(self, cmd, calls):
        calls.append(cmd)

    def wait(self, timeout=None):
        return 0


def test_say_stop_kills_inflight():
    class FakeProc:
        def __init__(self):
            self.killed = False

        def poll(self):
            return None  # still running

        def kill(self):
            self.killed = True

    proc = FakeProc()
    t = SayTts()
    t._proc = proc
    t.stop()
    assert proc.killed


def test_mock_tts_records():
    t = MockTts()
    t.speak("a")
    t.speak("b")
    assert t.spoken == ["a", "b"]


def test_make_tts_selection(monkeypatch):
    """The platform is passed in rather than patched onto the real sys module:
    that patch is global, and the standard library reads sys.platform too —
    which is how this test used to send shutil.which() down the Windows path
    on a Mac."""
    created: dict = {}

    class FakePyttsx3:
        def __init__(self, rate=185, voice=None):
            created["made"] = True

    monkeypatch.setattr("bgassist.tts.Pyttsx3Tts", FakePyttsx3)
    make_tts(SimpleNamespace(engine="auto", rate=1, voice=None), platform="win32")
    assert created.get("made")

    assert isinstance(
        make_tts(SimpleNamespace(engine="auto", rate=1, voice=None),
                 platform="darwin"), SayTts)
    assert isinstance(make_tts(SimpleNamespace(engine="mock", rate=1, voice=None)),
                      MockTts)
    with pytest.raises(ValueError):
        make_tts(SimpleNamespace(engine="wat", rate=1, voice=None))


def test_a_broken_optional_engine_never_leaves_the_app_mute(monkeypatch):
    """Piper is optional. Whatever it does on the way out — a missing voice, a
    missing binary, an exception from the depths of shutil — the app still
    gets something that can speak."""
    import bgassist.tts.piper as piper_module

    class Exploding:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("something unexpected in an optional dependency")

    monkeypatch.setattr(piper_module, "PiperTts", Exploding)
    engine = make_tts(SimpleNamespace(engine="auto", rate=180, voice=None),
                      platform="darwin")
    assert isinstance(engine, SayTts)


def test_pyttsx3_threaded_speak(monkeypatch):
    """Queueing logic with a stubbed pyttsx3 module (no real SAPI5/NSSpeech)."""

    class FakeEngine:
        def __init__(self):
            self.said = []

        def setProperty(self, k, v):
            pass

        def getProperty(self, k):
            return []

        def say(self, text):
            self.said.append(text)

        def runAndWait(self):
            pass

    engine = FakeEngine()
    fake_mod = types.ModuleType("pyttsx3")
    fake_mod.init = lambda: engine
    monkeypatch.setitem(sys.modules, "pyttsx3", fake_mod)

    t = Pyttsx3Tts(rate=150)
    t.speak("one")
    t.speak("two")
    assert engine.said == ["one", "two"]


def test_pyttsx3_init_failure_raises(monkeypatch):
    fake_mod = types.ModuleType("pyttsx3")

    def broken_init():
        raise RuntimeError("no sapi5 here")

    fake_mod.init = broken_init
    monkeypatch.setitem(sys.modules, "pyttsx3", fake_mod)
    with pytest.raises(TtsError):
        Pyttsx3Tts(rate=150)
