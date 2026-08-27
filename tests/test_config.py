import json

from starcop.config import load_config


def test_defaults():
    cfg = load_config("/nonexistent/config.json")
    assert cfg.trigger_words == ["computer"]
    assert cfg.whisper_model == "base.en"
    assert cfg.llm.backend == "ollama"
    assert cfg.tts.engine == "auto"


def test_file_merge(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"trigger_words": ["compy"],
                             "llm": {"model": "phi3"}}))
    cfg = load_config(str(p))
    assert cfg.trigger_words == ["compy"]
    assert cfg.llm.model == "phi3"
    assert cfg.llm.backend == "ollama"  # default preserved


def test_env_expansion(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"llm": {"api_key_env": "MY_KEY"}}))
    monkeypatch.setenv("MY_KEY", "sekrit")
    cfg = load_config(str(p))
    assert cfg.llm.api_key == "sekrit"


def test_bad_file_falls_back(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not json")
    cfg = load_config(str(p))
    assert cfg.trigger_words == ["computer"]


def test_unknown_keys_ignored(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"bogus_key": 1}))
    cfg = load_config(str(p))
    assert cfg.whisper_model == "base.en"
