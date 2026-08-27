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

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BGASSIST_HOME", tempfile.mkdtemp(prefix="bgassist-tests-"))
