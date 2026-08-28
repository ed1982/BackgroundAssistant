"""API keys in the OS keychain — never in a file, never in a log (§6.2).

``keyring`` talks to the macOS Keychain and the Windows Credential Manager
natively. One account per provider, so several keys coexist and switching
provider does not mean re-entering one.

The ``${ENV_VAR}`` expansion the old config had is **gone**, not extended: a
GUI app launched from Finder or a Login Item inherits launchd's environment,
not your shell's, which is exactly why every request came back 401 (F2). The
environment is still read once, by the migration, to offer to move an existing
key into the keychain.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from bgassist import BUNDLE_ID

log = logging.getLogger("bgassist.settings.secrets")

SERVICE = BUNDLE_ID
DB_KEY_ACCOUNT = "conversation-db-key"


class SecretStoreError(RuntimeError):
    pass


class SecretStore:
    """Keychain-backed key/value store with an in-memory fallback.

    The fallback exists so the app still runs (and the tests still pass) on a
    machine with no keyring backend; it is process-local and never written
    anywhere, so nothing leaks to disk when it is in use.
    """

    def __init__(self, service: str = SERVICE, backend=None):
        self.service = service
        self._memory: Dict[str, str] = {}
        self._backend = backend
        self._backend_ok: Optional[bool] = None if backend is None else True

    # -- backend ---------------------------------------------------------
    def _keyring(self):
        if self._backend is not None:
            return self._backend
        if self._backend_ok is False:
            return None
        try:
            import keyring

            keyring.get_keyring()
            self._backend = keyring
            self._backend_ok = True
        except Exception as exc:  # noqa: BLE001 - no backend on this machine
            log.warning("no system keychain available (%s); keys will be kept in "
                        "memory for this session only", exc)
            self._backend_ok = False
            return None
        return self._backend

    @property
    def available(self) -> bool:
        return self._keyring() is not None

    # -- api -------------------------------------------------------------
    def get(self, account: str) -> str:
        backend = self._keyring()
        if backend is None:
            return self._memory.get(account, "")
        try:
            return backend.get_password(self.service, account) or ""
        except Exception as exc:  # noqa: BLE001 - locked keychain, denied prompt
            log.error("could not read %r from the keychain: %s", account, exc)
            return self._memory.get(account, "")

    def set(self, account: str, secret: str) -> None:
        if secret is None:
            secret = ""
        backend = self._keyring()
        if backend is None:
            self._memory[account] = secret
            return
        try:
            backend.set_password(self.service, account, secret)
        except Exception as exc:  # noqa: BLE001
            log.error("could not write %r to the keychain: %s", account, exc)
            self._memory[account] = secret

    def delete(self, account: str) -> None:
        self._memory.pop(account, None)
        backend = self._keyring()
        if backend is None:
            return
        try:
            backend.delete_password(self.service, account)
        except Exception:  # noqa: BLE001 - absent is fine
            pass

    def has(self, account: str) -> bool:
        return bool(self.get(account))

    def accounts_with_keys(self, accounts: List[str]) -> List[str]:
        return [a for a in accounts if self.has(a)]


class MemorySecretStore(SecretStore):
    """A secret store that never reaches the keychain.

    Used by ``--check`` and ``--smoke``: on macOS an unsigned or ad-hoc signed
    binary asking for a keychain item puts up a modal prompt, which would hang
    an unattended build — and neither mode should be writing to the user's real
    keychain in the first place.
    """

    def __init__(self, service: str = SERVICE):
        super().__init__(service=service, backend=None)
        self._backend_ok = False

    @property
    def available(self) -> bool:
        return False


def display_stub(secret: str) -> str:
    """What the UI is allowed to see: ``sk-…4f2a``, never the key itself."""
    secret = secret or ""
    if not secret:
        return ""
    tail = secret[-4:]
    head = secret[:3] if len(secret) > 8 else ""
    return f"{head}…{tail}" if head else f"…{tail}"
