"""Per-OS locations for settings, logs, data and models.

These use ``APP_ID_NAME``, not the display name: what the app is called in
Finder can change, and a rename must not leave somebody's settings and
conversations behind in a folder nothing looks in any more.

Everything the app writes lives outside the code directory: an installed
``.app`` bundle is read-only and signed, so writing next to the source (as the
old ``config.json`` / ``starcop.log`` did) breaks both packaging and signing.
Fixes F11.

``platformdirs`` is a tiny pure-Python dependency; if it is missing we fall
back to the same conventional locations computed by hand, so the app still
starts.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from bgassist import APP_ID_NAME

_ENV_OVERRIDE = "BGASSIST_HOME"


def _fallback_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_ID_NAME
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_ID_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_ID_NAME


def _fallback_log_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / APP_ID_NAME
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_ID_NAME / "logs"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / APP_ID_NAME / "logs"


def _root() -> Path:
    """The data root, honouring the BGASSIST_HOME override (used by tests)."""
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return Path(override).expanduser()
    try:
        import platformdirs

        return Path(platformdirs.user_data_dir(APP_ID_NAME, appauthor=False))
    except Exception:  # noqa: BLE001 - missing/odd platformdirs must not stop start-up
        return _fallback_data_dir()


def _ensure(path: Path) -> Path:
    """Create *path* (0700) and return it."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:  # pragma: no cover - Windows / odd filesystems
        pass
    return path


def data_dir() -> Path:
    """Settings, conversation database, models."""
    return _ensure(_root())


def log_dir() -> Path:
    """Rotating log files."""
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return _ensure(Path(override).expanduser() / "logs")
    try:
        import platformdirs

        return _ensure(Path(platformdirs.user_log_dir(APP_ID_NAME, appauthor=False)))
    except Exception:  # noqa: BLE001
        return _ensure(_fallback_log_dir())


def models_dir() -> Path:
    """Downloaded speech and voice models."""
    return _ensure(data_dir() / "models")


def settings_file() -> Path:
    return data_dir() / "settings.json"


def conversations_db() -> Path:
    return data_dir() / "conversations.db"


def log_file() -> Path:
    return log_dir() / "backgroundassistant.log"


def legacy_config_candidates() -> list[Path]:
    """Where a pre-refactor ``config.json`` might still be sitting (migration)."""
    here = Path(__file__).resolve().parents[2]
    return [
        here / "config.json",
        Path.home() / "Code" / "git" / "BackgroundAssistant" / "config.json",
        Path.home() / "Code" / "git" / "StarTrekComputer" / "config.json",
    ]


def secure_write(path: Path, text: str) -> None:
    """Write *text* to *path* atomically with 0600 permissions."""
    _ensure(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.chmod(tmp, 0o600)
    except OSError:  # pragma: no cover
        pass
    os.replace(tmp, path)
