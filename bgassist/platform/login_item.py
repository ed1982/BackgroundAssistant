"""Launch at login (D13): SMAppService on macOS, the Run key on Windows.

No auto-update and no installer daemon — just a checkbox in Preferences that
either registers the app with the OS or does not.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from bgassist import APP_NAME

log = logging.getLogger("bgassist.platform.login_item")

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def supported() -> bool:
    return sys.platform == "darwin" or sys.platform.startswith("win")


def _app_path() -> Optional[Path]:
    """The bundle or executable to launch. None when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None


# -- macOS ---------------------------------------------------------------

def _macos_set(enabled: bool) -> bool:
    try:
        from ServiceManagement import SMAppService  # type: ignore
    except ImportError:
        log.info("SMAppService is unavailable; falling back to a LaunchAgent")
        return _launch_agent_set(enabled)
    try:
        service = SMAppService.mainAppService()
        if enabled:
            ok, error = service.registerAndReturnError_(None)
        else:
            ok, error = service.unregisterAndReturnError_(None)
        if not ok:
            log.error("could not change the login item: %s", error)
        return bool(ok)
    except Exception as exc:  # noqa: BLE001 - pyobjc surface varies by version
        log.error("SMAppService failed (%s); falling back to a LaunchAgent", exc)
        return _launch_agent_set(enabled)


def _agent_plist() -> Path:
    return (Path.home() / "Library" / "LaunchAgents" /
            f"com.edmartin.{APP_NAME.lower()}.plist")


def _launch_agent_set(enabled: bool) -> bool:
    """macOS 12 fallback: a plain LaunchAgent plist."""
    plist = _agent_plist()
    if not enabled:
        try:
            plist.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:  # pragma: no cover
            log.error("could not remove %s: %s", plist, exc)
            return False
        return True

    target = _app_path()
    if target is None:
        log.warning("running from source: launch at login needs a built app")
        return False
    program = str(target)
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.edmartin.{APP_NAME.lower()}</string>
  <key>ProgramArguments</key><array><string>{program}</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
</dict>
</plist>
""", encoding="utf-8")
    return True


# -- Windows -------------------------------------------------------------

def _windows_set(enabled: bool) -> bool:
    try:
        import winreg  # type: ignore
    except ImportError:  # pragma: no cover - not Windows
        return False
    target = _app_path()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enabled:
                if target is None:
                    log.warning("running from source: launch at login needs a "
                                "built executable")
                    return False
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{target}"')
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError as exc:  # pragma: no cover
        log.error("could not update the Run key: %s", exc)
        return False


# -- public --------------------------------------------------------------

def set_enabled(enabled: bool) -> bool:
    if sys.platform == "darwin":
        return _macos_set(enabled)
    if sys.platform.startswith("win"):
        return _windows_set(enabled)
    log.info("launch at login is not supported on this platform")
    return False


def is_enabled() -> bool:
    if sys.platform == "darwin":
        try:
            from ServiceManagement import SMAppService  # type: ignore

            return int(SMAppService.mainAppService().status()) == 1
        except Exception:  # noqa: BLE001
            return _agent_plist().exists()
    if sys.platform.startswith("win"):
        try:
            import winreg  # type: ignore

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, APP_NAME)
            return True
        except Exception:  # noqa: BLE001
            return False
    return False
