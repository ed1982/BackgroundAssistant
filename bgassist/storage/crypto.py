"""AES-256-GCM for message bodies, with the key in the OS keychain (§6.3).

Chosen over SQLCipher because it needs no native build and no PyInstaller
gymnastics: ``cryptography`` ships wheels for both platforms we target.

Only message bodies and context snapshots are encrypted. Titles and timestamps
stay in the clear so the history list renders without decrypting everything —
a deliberate, documented trade-off.
"""
from __future__ import annotations

import base64
import logging
import os
import secrets as _secrets
from typing import Optional

log = logging.getLogger("bgassist.storage.crypto")

NONCE_BYTES = 12
KEY_BYTES = 32
_PREFIX = b"v1"


class CryptoUnavailable(RuntimeError):
    """The ``cryptography`` package is not installed."""


class NullCipher:
    """No encryption. Used only when the user has turned it off, or when the
    crypto library is missing — and the Privacy tab says so out loud."""

    name = "none"
    encrypted = False

    def encrypt(self, plaintext: str) -> bytes:
        return (plaintext or "").encode("utf-8")

    def decrypt(self, blob: bytes) -> str:
        if blob is None:
            return ""
        return bytes(blob).decode("utf-8", "replace")


class AesGcmCipher:
    """AES-256-GCM. Every record gets a fresh random nonce."""

    name = "aes-256-gcm"
    encrypted = True

    def __init__(self, key: bytes):
        if len(key) != KEY_BYTES:
            raise ValueError(f"key must be {KEY_BYTES} bytes")
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:
            raise CryptoUnavailable(
                "the 'cryptography' package is required for encrypted "
                "conversations") from exc
        self._aesgcm = AESGCM(key)

    def encrypt(self, plaintext: str) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        blob = self._aesgcm.encrypt(nonce, (plaintext or "").encode("utf-8"), _PREFIX)
        return _PREFIX + nonce + blob

    def decrypt(self, blob: bytes) -> str:
        if not blob:
            return ""
        blob = bytes(blob)
        if not blob.startswith(_PREFIX):
            # Written before encryption was enabled: read it as plain text.
            return blob.decode("utf-8", "replace")
        nonce = blob[len(_PREFIX):len(_PREFIX) + NONCE_BYTES]
        payload = blob[len(_PREFIX) + NONCE_BYTES:]
        plaintext = self._aesgcm.decrypt(nonce, payload, _PREFIX)
        return plaintext.decode("utf-8")


def generate_key() -> bytes:
    return _secrets.token_bytes(KEY_BYTES)


def load_or_create_key(secret_store, account: str = "conversation-db-key") -> bytes:
    """Fetch the database key from the keychain, creating it the first time."""
    existing = secret_store.get(account)
    if existing:
        try:
            key = base64.b64decode(existing)
            if len(key) == KEY_BYTES:
                return key
            log.error("stored database key has the wrong length; generating a new one")
        except Exception:  # noqa: BLE001 - corrupt entry
            log.error("stored database key is unreadable; generating a new one")
    key = generate_key()
    secret_store.set(account, base64.b64encode(key).decode("ascii"))
    return key


class FileKeyStore:
    """Last-resort key storage: one 0600 file in the data directory.

    Used only when there is no system keychain at all. It is weaker than the
    Keychain and the Privacy tab says so — but it is the difference between
    "your old conversations are unreadable after a restart" and not, because a
    keychain-less :class:`SecretStore` keeps its keys in memory only.
    """

    location = "file"

    def __init__(self, path=None):
        if path is None:
            from bgassist.platform import paths

            path = paths.data_dir() / "dbkey"
        self.path = path

    def get(self, account: str) -> str:
        try:
            return self.path.read_text(encoding="ascii").strip()
        except OSError:
            return ""

    def set(self, account: str, secret: str) -> None:
        from bgassist.platform import paths

        paths.secure_write(self.path, secret)


def make_cipher(secret_store=None, enabled: bool = True,
                account: str = "conversation-db-key"):
    """The cipher the conversation store should use, or a Null one if it cannot.

    Never raises: a machine without ``cryptography`` still gets a working app,
    and Preferences → Privacy reports the real status rather than claiming
    encryption that is not happening.
    """
    if not enabled or secret_store is None:
        return NullCipher()
    if not getattr(secret_store, "available", True):
        # No keychain: an in-memory key would be lost on quit, taking every
        # stored conversation with it.
        log.warning("no system keychain; the conversation key will be kept in a "
                    "private file instead — see Preferences → Privacy")
        secret_store = FileKeyStore()
    try:
        cipher = AesGcmCipher(load_or_create_key(secret_store, account))
        cipher.key_location = getattr(secret_store, "location", "keychain")
        return cipher
    except (CryptoUnavailable, ValueError) as exc:
        log.error("conversation encryption is unavailable (%s); storing in the "
                  "clear — see Preferences → Privacy", exc)
        return NullCipher()


def encryption_status(cipher) -> dict:
    return {"encrypted": bool(getattr(cipher, "encrypted", False)),
            "algorithm": getattr(cipher, "name", "unknown"),
            "key_location": getattr(cipher, "key_location", "none")}


def key_fingerprint(key: Optional[bytes]) -> str:  # pragma: no cover - display only
    if not key:
        return ""
    import hashlib

    return hashlib.sha256(key).hexdigest()[:8]
