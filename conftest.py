"""Root conftest.

Puts the project root on sys.path so tests can import ``bgassist`` and
``tests.fakes``, and points every test at a throwaway data directory so no
test can ever touch the real settings file, log or conversation database.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BGASSIST_HOME", tempfile.mkdtemp(prefix="bgassist-tests-"))


@pytest.fixture(autouse=True, scope="session")
def _never_touch_the_real_keychain():
    """No test may reach the operating system's keychain. Ever.

    This is not caution, it is a bug fix. Without it, any test that builds an
    Application without passing a secret store gets a real SecretStore — which
    on macOS reads, writes and deletes entries in the *user's own* Keychain,
    under the very account the shipped app stores their API key in. On Linux
    the keyring package is usually absent, so the whole class of mistake hides
    until someone runs the suite on the machine it can damage.

    An explicitly injected fake backend still works; only the system one is
    unreachable.
    """
    from bgassist.settings.secrets import SecretStore

    original = SecretStore._keyring
    SecretStore._keyring = lambda self: self._backend
    try:
        yield
    finally:
        SecretStore._keyring = original
