"""Settings store, secrets and the migration off config.json + $OPENAI_API_KEY."""
import json

import pytest

from bgassist.settings.migrate import migrate
from bgassist.settings.schema import Settings
from bgassist.settings.secrets import SecretStore, display_stub
from bgassist.settings.store import SettingsStore


class MemoryKeyring:
    def __init__(self, fail=False):
        self.data = {}
        self.fail = fail

    def get_keyring(self):
        if self.fail:
            raise RuntimeError("no backend")
        return self

    def get_password(self, service, account):
        return self.data.get((service, account))

    def set_password(self, service, account, secret):
        self.data[(service, account)] = secret

    def delete_password(self, service, account):
        self.data.pop((service, account), None)


@pytest.fixture()
def store(tmp_path):
    return SettingsStore(path=tmp_path / "settings.json")


# -- defaults and validation ---------------------------------------------

def test_defaults(store):
    settings = store.settings
    assert settings.general.trigger_words == ["computer"]
    assert settings.general.sensitivity == "balanced"
    assert settings.general.hotkey == ""          # none by default (D17a)
    assert settings.general.notifications is False
    assert settings.listening.whisper_model == "base.en"
    assert settings.privacy.auto_delete_enabled is False
    assert settings.privacy.transcript_debug is False
    assert settings.ai.provider == "openai"


def test_the_easter_egg_flag_tracks_the_trigger_word(store):
    assert store.settings.general.easter_egg is True
    store.update({"general.trigger_words": ["jarvis"]})
    assert store.settings.general.easter_egg is False


def test_round_trip_through_the_file(tmp_path):
    first = SettingsStore(path=tmp_path / "s.json")
    first.update({"general.trigger_words": ["compy"], "voice.rate": 210})
    second = SettingsStore(path=tmp_path / "s.json")
    assert second.settings.general.trigger_words == ["compy"]
    assert second.settings.voice.rate == 210


def test_bad_values_fall_back_to_defaults(store):
    store.update({"listening.vad_aggressiveness": 99, "voice.rate": -5,
                  "general.sensitivity": "wildly", "ai.provider": "nope"})
    assert store.settings.listening.vad_aggressiveness == 3   # clamped
    assert store.settings.voice.rate == 80                    # clamped
    assert store.settings.general.sensitivity == "balanced"
    assert store.settings.ai.provider == "openai"


def test_empty_trigger_words_are_rejected(store):
    store.update({"general.trigger_words": ["", "  "]})
    assert store.settings.general.trigger_words == ["computer"]


def test_unknown_keys_are_ignored(store):
    assert store.update({"general.nonsense": 1}) == []


def test_a_corrupt_file_is_quarantined_not_lost(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    store = SettingsStore(path=path)
    assert store.settings.general.trigger_words == ["computer"]
    assert list(tmp_path.glob("*.corrupt-*.json"))


def test_partial_files_keep_their_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"voice": {"rate": 200}}))
    settings = SettingsStore(path=path).settings
    assert settings.voice.rate == 200
    assert settings.ai.provider == "openai"


def test_settings_file_is_written_privately(store):
    store.save()
    assert oct(store.path.stat().st_mode)[-3:] == "600"


def test_live_apply_notifies_observers(store):
    seen = []
    store.subscribe(lambda settings, changed: seen.append(changed))
    store.update({"voice.rate": 200})
    assert seen == [["voice.rate"]]


def test_factory_reset(store):
    store.update({"voice.rate": 200})
    store.reset()
    assert store.settings.voice.rate == 185


def test_dict_round_trip():
    settings = Settings()
    assert Settings.from_dict(settings.to_dict()).to_dict() == settings.to_dict()


# -- secrets --------------------------------------------------------------

def test_keys_go_to_the_keychain_not_the_settings_file(store):
    keyring = MemoryKeyring()
    secrets = SecretStore(backend=keyring)
    secrets.set("openai", "sk-secret-value-1234")
    store.save()
    assert "sk-secret-value-1234" not in store.path.read_text()
    assert secrets.get("openai") == "sk-secret-value-1234"


def test_several_provider_keys_coexist():
    secrets = SecretStore(backend=MemoryKeyring())
    secrets.set("openai", "sk-a")
    secrets.set("anthropic", "sk-ant-b")
    assert secrets.get("openai") == "sk-a"
    assert secrets.get("anthropic") == "sk-ant-b"
    assert secrets.accounts_with_keys(["openai", "anthropic", "local"]) == \
        ["openai", "anthropic"]


def test_the_ui_only_ever_sees_a_stub():
    assert display_stub("sk-abcdefghijklmnop4f2a") == "sk-…4f2a"
    assert display_stub("") == ""


def test_a_missing_keychain_degrades_to_memory_only():
    secrets = SecretStore(backend=None)
    secrets._backend_ok = False
    secrets.set("openai", "sk-x")
    assert secrets.get("openai") == "sk-x"
    assert secrets.available is False


def test_deleting_a_key():
    secrets = SecretStore(backend=MemoryKeyring())
    secrets.set("openai", "sk-x")
    secrets.delete("openai")
    assert secrets.get("openai") == ""


# -- migration ------------------------------------------------------------

LEGACY = {
    "trigger_words": ["compy"],
    "whisper_model": "small.en",
    "command_end_silence_ms": 1200,
    "llm": {"backend": "openai_compatible", "base_url": "https://api.openai.com/v1",
            "model": "gpt-5-mini", "api_key_env": "OPENAI_API_KEY"},
    "tts": {"engine": "say", "rate": 200},
    "log_file": "starcop.log",
    "log_level": "DEBUG",
}


def _legacy_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(LEGACY))
    return path


def test_migration_maps_the_old_config(tmp_path, store):
    result = migrate(store, SecretStore(backend=MemoryKeyring()),
                     config_path=_legacy_file(tmp_path), environ={})
    assert result.ran
    settings = store.settings
    assert settings.general.trigger_words == ["compy"]
    assert settings.listening.whisper_model == "small.en"
    assert settings.listening.command_end_silence_ms == 1200
    assert settings.ai.provider == "openai"
    assert settings.ai.model == "gpt-5-mini"
    assert settings.voice.engine == "say"
    assert settings.advanced.log_level == "DEBUG"


def test_migration_moves_the_environment_key_into_the_keychain(tmp_path, store):
    secrets = SecretStore(backend=MemoryKeyring())
    result = migrate(store, secrets, config_path=_legacy_file(tmp_path),
                     environ={"OPENAI_API_KEY": "sk-from-the-shell"})
    assert secrets.get("openai") == "sk-from-the-shell"
    assert result.key_found_in_env
    assert any("shell profile" in note for note in result.notes)


def test_migration_does_not_carry_the_old_log_path_over(tmp_path, store):
    migrate(store, SecretStore(backend=MemoryKeyring()),
            config_path=_legacy_file(tmp_path), environ={})
    assert "starcop.log" not in json.dumps(store.settings.to_dict())


def test_migration_is_idempotent(tmp_path, store):
    secrets = SecretStore(backend=MemoryKeyring())
    path = _legacy_file(tmp_path)
    migrate(store, secrets, config_path=path, environ={})
    store.update({"general.trigger_words": ["computer"]})
    second = migrate(store, secrets, config_path=path, environ={})
    assert second.ran is False
    assert store.settings.general.trigger_words == ["computer"]


def test_migration_with_no_old_config_still_marks_itself_done(store):
    result = migrate(store, SecretStore(backend=MemoryKeyring()),
                     config_path=None, environ={})
    assert result.ran
    assert store.settings.advanced.migrated_from_config_json is True


def test_env_expansion_is_gone(tmp_path, store):
    """The ${ENV} mechanism that produced F2 was removed, not extended."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm": {"model": "${SOME_VAR}"}}))
    migrate(store, SecretStore(backend=MemoryKeyring()), config_path=path,
            environ={"SOME_VAR": "expanded"})
    assert store.settings.ai.model == "${SOME_VAR}"  # stored verbatim, never expanded


def test_no_test_can_reach_the_system_keychain():
    """The guard in conftest.py, asserted so it cannot be quietly removed.

    Without it, any test building an Application without an explicit secret
    store reads, writes and deletes entries in the developer's own Keychain —
    under the same account the shipped app keeps their API key in. It hid on
    Linux, where the keyring package is usually absent, and surfaced on macOS
    by reading back a real key.
    """
    assert SecretStore().available is False
    assert SecretStore().get("openai") == ""
