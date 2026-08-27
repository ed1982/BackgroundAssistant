import subprocess
import sys
import types
from types import SimpleNamespace

import pytest

from starcop.tts import MockTts, Pyttsx3Tts, SayTts, TtsError, make_tts


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
    created: dict = {}

    class FakePyttsx3:
        def __init__(self, rate=185, voice=None):
            created["made"] = True

    monkeypatch.setattr("starcop.tts.Pyttsx3Tts", FakePyttsx3)
    monkeypatch.setattr("starcop.tts.sys.platform", "win32")
    make_tts(SimpleNamespace(engine="auto", rate=1, voice=None))
    assert created.get("made")

    monkeypatch.setattr("starcop.tts.sys.platform", "darwin")
    assert isinstance(make_tts(SimpleNamespace(engine="auto", rate=1, voice=None)),
                      SayTts)
    assert isinstance(make_tts(SimpleNamespace(engine="mock", rate=1, voice=None)),
                      MockTts)
    with pytest.raises(ValueError):
        make_tts(SimpleNamespace(engine="wat", rate=1, voice=None))


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
