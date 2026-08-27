"""The shared QWebEngineView host for the chat and Preferences windows (D3).

The pages are loaded from files inside the bundle and talk to Python over
QWebChannel — no local HTTP server and no open port, which is both simpler and
safer than the usual embedded-web-app arrangement (§7).
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("bgassist.ui.window")

WEB_DIR = Path(__file__).resolve().parent / "web"


class WebWindow:
    """A QMainWindow wrapping one page. Hidden until something opens it (D9)."""

    page = "chat.html"
    title = "BackgroundAssistant"
    default_size = (960, 680)
    minimum_size = (480, 420)

    def __init__(self, application):
        from PySide6.QtCore import QUrl
        from PySide6.QtWebChannel import QWebChannel
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QMainWindow

        from bgassist.ui.bridge import make_web_bridge

        self.app = application
        self.window = QMainWindow()
        self.window.setWindowTitle(self.title)
        self.window.resize(*self.default_size)
        self.window.setMinimumSize(*self.minimum_size)

        self.view = QWebEngineView()
        self.channel = QWebChannel()
        self.bridge = make_web_bridge(application)
        self.channel.registerObject("backend", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self.view.setUrl(QUrl.fromLocalFile(str(WEB_DIR / self.page)))
        self.window.setCentralWidget(self.view)
        self._install_escape()

    def _install_escape(self) -> None:
        """Esc stops whatever is in flight — the always-available escape hatch."""
        from PySide6.QtGui import QKeySequence, QShortcut

        shortcut = QShortcut(QKeySequence("Esc"), self.window)
        shortcut.activated.connect(lambda: self.app.orchestrator.cancel("escape"))
        self._escape = shortcut

    def show_and_raise(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def hide(self) -> None:
        self.window.hide()

    @property
    def visible(self) -> bool:
        return bool(self.window.isVisible())
