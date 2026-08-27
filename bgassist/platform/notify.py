"""Optional desktop notifications for spoken answers (open question 7).

Off by default: this is an ambient app and a banner for every answer would be
noise. When it is on, clicking the notification opens the chat window.
"""
from __future__ import annotations

import logging
from typing import Optional

from bgassist import APP_NAME

log = logging.getLogger("bgassist.platform.notify")


class Notifier:
    def __init__(self, enabled: bool = False, tray=None):
        self.enabled = bool(enabled)
        self.tray = tray

    def notify(self, title: str, body: str) -> None:
        if not self.enabled:
            return
        tray = self.tray
        if tray is None:
            return
        try:
            from PySide6.QtWidgets import QSystemTrayIcon

            tray.showMessage(title or APP_NAME, body[:200],
                             QSystemTrayIcon.MessageIcon.Information, 6000)
        except Exception:  # noqa: BLE001 - notifications are never important
            log.debug("could not post a notification", exc_info=True)
