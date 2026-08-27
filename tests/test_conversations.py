"""The conversation store: encryption, continuation, search, retention."""
import sqlite3
import time

import pytest

from bgassist.settings.secrets import SecretStore
from bgassist.storage import ConversationStore, NullCipher
from bgassist.storage.crypto import AesGcmCipher, generate_key, load_or_create_key, make_cipher


@pytest.fixture()
def key():
    return generate_key()


@pytest.fixture()
def store(tmp_path, key):
    s = ConversationStore(tmp_path / "c.db", AesGcmCipher(key))
    yield s
    s.close()


def test_round_trip(store):
    conversation = store.create_conversation()
    store.add_message(conversation, "user", "what is the warp core status")
    store.add_message(conversation, "assistant", "It is stable.")
    texts = [m.text for m in store.messages(conversation)]
    assert texts == ["what is the warp core status", "It is stable."]


def test_the_database_is_unreadable_without_the_key(tmp_path, key):
    path = tmp_path / "c.db"
    store = ConversationStore(path, AesGcmCipher(key))
    conversation = store.create_conversation()
    store.add_message(conversation, "user", "a private medical question")
    store.close()
    raw = sqlite3.connect(str(path)).execute("SELECT body FROM messages").fetchone()[0]
    assert b"private" not in bytes(raw)
    assert b"medical" not in bytes(raw)


def test_the_wrong_key_does_not_return_plaintext(tmp_path, key):
    path = tmp_path / "c.db"
    store = ConversationStore(path, AesGcmCipher(key))
    conversation = store.create_conversation()
    store.add_message(conversation, "user", "a private question")
    store.close()
    other = ConversationStore(path, AesGcmCipher(generate_key()))
    try:
        assert "private" not in other.messages(conversation)[0].text
    finally:
        other.close()


def test_context_snapshots_are_encrypted_too(tmp_path, key):
    path = tmp_path / "c.db"
    store = ConversationStore(path, AesGcmCipher(key))
    conversation = store.create_conversation()
    store.add_message(conversation, "user", "q", context="ambient chatter here")
    store.close()
    raw = sqlite3.connect(str(path)).execute("SELECT context FROM messages").fetchone()[0]
    assert b"ambient" not in bytes(raw)


def test_titles_stay_plaintext_so_the_list_renders_cheaply(tmp_path, key):
    path = tmp_path / "c.db"
    store = ConversationStore(path, AesGcmCipher(key))
    conversation = store.create_conversation()
    store.set_title(conversation, "Warp core status")
    store.close()
    row = sqlite3.connect(str(path)).execute("SELECT title FROM conversations").fetchone()
    assert row[0] == "Warp core status"


# -- the ten-minute continuation rule ------------------------------------

def test_a_recent_conversation_continues(store):
    first = store.current_conversation(now=1000.0)
    store.add_message(first, "user", "hello", ts=1000.0)
    assert store.current_conversation(now=1500.0) == first


def test_an_old_conversation_starts_a_new_one(store):
    first = store.current_conversation(now=1000.0)
    store.add_message(first, "user", "hello", ts=1000.0)
    assert store.current_conversation(now=1000.0 + 601) != first


# -- titles, search, delete, export --------------------------------------

def test_search_matches_titles_and_bodies(store):
    a = store.create_conversation()
    store.set_title(a, "Warp core")
    store.add_message(a, "user", "nothing relevant")
    b = store.create_conversation()
    store.add_message(b, "user", "tell me about dilithium")
    assert [c.id for c in store.search("warp")] == [a]
    assert [c.id for c in store.search("dilithium")] == [b]
    assert len(store.search("")) == 2


def test_delete_one_and_delete_all(store):
    a = store.create_conversation()
    store.add_message(a, "user", "x")
    b = store.create_conversation()
    store.add_message(b, "user", "y")
    store.delete_conversation(a)
    assert [c.id for c in store.list_conversations()] == [b]
    store.delete_all()
    assert store.count() == {"conversations": 0, "messages": 0}


def test_export_markdown_shows_the_interruption(store):
    conversation = store.create_conversation()
    store.add_message(conversation, "user", "what is X")
    store.add_message(conversation, "assistant", "X is a thing. More detail.",
                      spoken_upto=len("X is a thing."), interrupted=True)
    text = store.export_markdown(conversation)
    assert "X is a thing." in text
    assert "interrupted" in text


def test_optional_auto_delete_purges_old_conversations(store):
    now = time.time()
    old = store.create_conversation(now=now - 100 * 86400)
    store.add_message(old, "user", "ancient", ts=now - 100 * 86400)
    recent = store.create_conversation(now=now)
    store.add_message(recent, "user", "today", ts=now)
    assert store.purge_older_than(90, now=now) == 1
    assert [c.id for c in store.list_conversations()] == [recent]


# -- key management -------------------------------------------------------

def test_the_key_is_created_once_and_reused():
    secrets = SecretStore(backend=_MemoryKeyring())
    first = load_or_create_key(secrets)
    assert load_or_create_key(secrets) == first
    assert len(first) == 32


def test_make_cipher_falls_back_loudly_rather_than_failing():
    cipher = make_cipher(None, enabled=True)
    assert isinstance(cipher, NullCipher)
    assert cipher.encrypted is False


class _MemoryKeyring:
    def __init__(self):
        self.data = {}

    def get_keyring(self):
        return self

    def get_password(self, service, account):
        return self.data.get((service, account))

    def set_password(self, service, account, secret):
        self.data[(service, account)] = secret

    def delete_password(self, service, account):
        self.data.pop((service, account), None)


def test_without_a_keychain_the_key_survives_a_restart(tmp_path, monkeypatch):
    """A keychain-less machine keeps its key in a private file rather than in
    memory, which would make every stored conversation unreadable on quit."""
    from bgassist.storage.crypto import FileKeyStore

    secrets = SecretStore(backend=None)
    secrets._backend_ok = False
    monkeypatch.setattr("bgassist.storage.crypto.FileKeyStore",
                        lambda path=None: FileKeyStore(tmp_path / "dbkey"))
    first = make_cipher(secrets, enabled=True)
    blob = first.encrypt("a private thing")
    second = make_cipher(secrets, enabled=True)
    assert second.decrypt(blob) == "a private thing"
    assert first.key_location == "file"
    assert oct((tmp_path / "dbkey").stat().st_mode)[-3:] == "600"
