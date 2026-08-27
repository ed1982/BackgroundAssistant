"""The settings file: JSON in the app-support directory, observable (F11).

Live-apply is the point of the observer list: changing the trigger word or the
voice in Preferences must take effect without a restart, so every component
that cares subscribes and re-reads what it needs.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from bgassist.settings.schema import Settings

log = logging.getLogger("bgassist.settings.store")

Observer = Callable[[Settings, List[str]], None]


class SettingsStore:
    """Loads, validates, saves and broadcasts :class:`Settings`."""

    def __init__(self, path: Optional[Path] = None, autoload: bool = True):
        if path is None:
            from bgassist.platform import paths

            path = paths.settings_file()
        self.path = Path(path)
        self._lock = threading.RLock()
        self._observers: List[Observer] = []
        self.settings = Settings()
        if autoload:
            self.load()

    # -- load / save -----------------------------------------------------
    def load(self) -> Settings:
        with self._lock:
            data = None
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError("settings root must be a JSON object")
                except (OSError, ValueError) as exc:
                    log.error("settings file %s is unreadable (%s); starting from "
                              "defaults and keeping a copy", self.path, exc)
                    self._quarantine()
                    data = None
            self.settings = Settings.from_dict(data)
            return self.settings

    def _quarantine(self) -> None:
        """Keep a corrupt file rather than silently overwriting it."""
        try:
            backup = self.path.with_suffix(f".corrupt-{int(time.time())}.json")
            shutil.copy2(self.path, backup)
        except OSError:  # pragma: no cover - best effort
            pass

    def save(self, changed: Optional[List[str]] = None) -> None:
        from bgassist.platform import paths

        with self._lock:
            self.settings.validate()
            payload = json.dumps(self.settings.to_dict(), indent=2, sort_keys=True)
            paths.secure_write(self.path, payload)
        self._notify(changed or [])

    # -- mutation --------------------------------------------------------
    def update(self, changes: dict, save: bool = True) -> List[str]:
        """Apply ``{"section.key": value}`` changes; returns the keys applied."""
        applied: List[str] = []
        with self._lock:
            for path, value in (changes or {}).items():
                if self.settings.set(path, value):
                    applied.append(path)
                else:
                    log.warning("ignoring unknown setting %r", path)
            if save and applied:
                self.settings.validate()
                from bgassist.platform import paths

                payload = json.dumps(self.settings.to_dict(), indent=2,
                                     sort_keys=True)
                paths.secure_write(self.path, payload)
        if applied:
            self._notify(applied)
        return applied

    def reset(self) -> Settings:
        """Factory reset (Preferences → Advanced)."""
        with self._lock:
            self.settings = Settings()
        self.save(["*"])
        return self.settings

    # -- observers -------------------------------------------------------
    def subscribe(self, observer: Observer) -> Callable[[], None]:
        with self._lock:
            self._observers.append(observer)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._observers.remove(observer)
                except ValueError:  # pragma: no cover
                    pass

        return unsubscribe

    def _notify(self, changed: List[str]) -> None:
        with self._lock:
            observers = list(self._observers)
            settings = self.settings
        for observer in observers:
            try:
                observer(settings, changed)
            except Exception:  # noqa: BLE001 - an observer must not break saving
                log.exception("settings observer failed")

    # -- convenience -----------------------------------------------------
    def __getattr__(self, item):
        # store.ai, store.general, … read through to the current settings.
        if item.startswith("_"):
            raise AttributeError(item)
        settings = self.__dict__.get("settings")
        if settings is not None and hasattr(settings, item):
            return getattr(settings, item)
        raise AttributeError(item)
