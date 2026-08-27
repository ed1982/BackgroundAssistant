"""An optional global shortcut for the chat window (D17a).

No hotkey is registered out of the box — nothing should collide with
Spotlight or anyone's muscle memory on a fresh install. This module ships
dormant and only does anything once a shortcut is set in Preferences.
"""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("bgassist.platform.hotkey")


class GlobalHotkey:
    """Wraps QKeySequence-based registration, tolerating its absence."""

    def __init__(self, on_activated: Callable[[], None]):
        self.on_activated = on_activated
        self._shortcut = None
        self.sequence = ""

    @property
    def active(self) -> bool:
        return self._shortcut is not None

    def set_sequence(self, sequence: str, parent=None) -> bool:
        """Register *sequence* (e.g. "Ctrl+Shift+Space"). Empty unregisters."""
        self.clear()
        sequence = (sequence or "").strip()
        self.sequence = sequence
        if not sequence:
            return True
        try:
            from PySide6.QtGui import QKeySequence, QShortcut
        except ImportError:  # pragma: no cover - no Qt in the test environment
            log.info("Qt is unavailable; the global shortcut is inactive")
            return False
        try:
            shortcut = QShortcut(QKeySequence(sequence), parent)
            shortcut.setContext(3)  # Qt.ApplicationShortcut
            shortcut.activated.connect(self.on_activated)
            self._shortcut = shortcut
            log.info("registered the chat shortcut %s", sequence)
            return True
        except Exception as exc:  # noqa: BLE001 - a bad sequence is user input
            log.error("could not register the shortcut %r: %s", sequence, exc)
            return False

    def clear(self) -> None:
        if self._shortcut is not None:
            try:
                self._shortcut.setEnabled(False)
                self._shortcut.deleteLater()
            except Exception:  # noqa: BLE001
                pass
            self._shortcut = None
