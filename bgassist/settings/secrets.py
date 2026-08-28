"""API keys in the OS keychain — never in a file, never in a log (§6.2).

``keyring`` talks to the macOS Keychain and the Windows Credential Manager
natively. The ``${ENV_VAR}`` expansion the old config had is **gone**, not
extended: a GUI app launched from Finder or a Login Item inherits launchd's
environment, not your shell's, which is exactly why every request came back
401 (F2). The environment is still read once, by the migration, to offer to
move an existing key into the keychain.

**Everything lives in a single keychain item.** macOS grants access per *item*,
not per application, so "Always Allow" only ever covers the one prompt in front
of you. With a key for each provider plus one for the conversation database,
first run asked three separate times and the button that is supposed to end
that did not. One item, one grant, one prompt — and a fresh install usually
sees none at all, because creating an item does not prompt, only reaching into
an existing one does.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Dict, List, Optional

from bgassist import BUNDLE_ID

log = logging.getLogger("bgassist.settings.secrets")

SERVICE = BUNDLE_ID
DB_KEY_ACCOUNT = "conversation-db-key"

#: The one item everything is kept in.
VAULT_ACCOUNT = "secrets"

#: Where secrets used to live, one item each. Read once, folded into the
#: vault, and then removed — the read is already authorised at that point, so
#: it costs nothing beyond the prompts the old layout was going to ask for
#: anyway.
LEGACY_ACCOUNTS = ("openai", "anthropic", "local", "ollama", "custom",
                   DB_KEY_ACCOUNT)


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
        #: Values the keychain would not keep. For this session they are the
        #: truth, and they take precedence over what the keychain holds.
        self._memory: Dict[str, str] = {}
        self._backend = backend
        self._backend_ok: Optional[bool] = None if backend is None else True
        self._vault: Optional[Dict[str, str]] = None

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

    # -- the vault -------------------------------------------------------
    def _load(self) -> Dict[str, str]:
        """Read the one item, once per process."""
        if self._vault is not None:
            return self._vault
        backend = self._keyring()
        if backend is None:
            self._vault = {}
            return self._vault
        raw = None
        try:
            raw = backend.get_password(self.service, VAULT_ACCOUNT)
        except Exception as exc:  # noqa: BLE001 - locked keychain, denied prompt
            log.error("could not read the keychain: %s", exc)
        if raw:
            try:
                loaded = json.loads(raw)
                if not isinstance(loaded, dict):
                    raise ValueError("not an object")
                self._vault = {str(k): str(v) for k, v in loaded.items()}
                return self._vault
            except ValueError as exc:
                log.error("the stored secrets are unreadable (%s); keeping a "
                          "copy and starting fresh", exc)
                self._quarantine(raw)
        self._vault = {} if raw else self._adopt_legacy(backend)
        return self._vault

    def _quarantine(self, raw: str) -> None:
        """Never overwrite something unreadable without keeping it."""
        try:
            self._keyring().set_password(
                self.service, f"{VAULT_ACCOUNT}.unreadable-{int(time.time())}", raw)
        except Exception:  # noqa: BLE001 - best effort
            log.debug("could not preserve the unreadable secrets", exc_info=True)

    def _adopt_legacy(self, backend) -> Dict[str, str]:
        """Fold the old one-item-per-secret layout into the vault."""
        found: Dict[str, str] = {}
        for account in LEGACY_ACCOUNTS:
            try:
                value = backend.get_password(self.service, account)
            except Exception:  # noqa: BLE001 - absent or refused: move on
                continue
            if value:
                found[account] = value
        if not found:
            return {}
        log.info("moving %d secret(s) into a single keychain item so macOS only "
                 "has to ask once", len(found))
        self._vault = found
        if self._write(found):
            for account in found:
                try:
                    backend.delete_password(self.service, account)
                except Exception:  # noqa: BLE001 - leaving one behind is untidy,
                    # not harmful
                    log.debug("could not remove the old %r item", account)
        return found

    def _write(self, vault: Dict[str, str]) -> bool:
        backend = self._keyring()
        if backend is None:
            return False
        payload = json.dumps(vault, sort_keys=True)
        try:
            backend.set_password(self.service, VAULT_ACCOUNT, payload)
            stored = backend.get_password(self.service, VAULT_ACCOUNT) or ""
        except Exception as exc:  # noqa: BLE001
            log.error("could not write to the keychain: %s", exc)
            return False
        if stored != payload:
            # macOS can refuse a write without raising: an item created under
            # one code signature is not writable by another, which is what an
            # ad-hoc signed build produces on every rebuild.
            log.error("the keychain did not keep the change; holding it for "
                      "this session only")
            return False
        return True

    # -- api -------------------------------------------------------------
    def get(self, account: str) -> str:
        if account in self._memory:
            return self._memory[account]
        return self._load().get(account, "")

    def set(self, account: str, secret: str) -> bool:
        """Store *secret*. True when it will survive a restart."""
        if secret is None:
            secret = ""
        vault = dict(self._load())
        vault[account] = secret
        if self._write(vault):
            self._vault = vault
            self._memory.pop(account, None)
            return True
        self._vault = vault
        self._memory[account] = secret
        return False

    def delete(self, account: str) -> bool:
        self._memory.pop(account, None)
        vault = dict(self._load())
        if account not in vault:
            return True
        vault.pop(account)
        wrote = self._write(vault)
        self._vault = vault
        return wrote

    def has(self, account: str) -> bool:
        return bool(self.get(account))

    def accounts_with_keys(self, accounts: List[str]) -> List[str]:
        return [a for a in accounts if self.has(a)]

    def forget_cache(self) -> None:
        """Drop the cached vault; the next read goes to the keychain."""
        self._vault = None


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

    def set(self, account: str, secret: str) -> bool:
        self._memory[account] = secret or ""
        return False


def display_stub(secret: str) -> str:
    """What the UI is allowed to see: ``sk-…4f2a``, never the key itself."""
    secret = secret or ""
    if not secret:
        return ""
    tail = secret[-4:]
    head = secret[:3] if len(secret) > 8 else ""
    return f"{head}…{tail}" if head else f"…{tail}"
