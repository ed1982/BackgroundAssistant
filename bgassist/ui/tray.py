"""The menu-bar item: status, state icon, and the way into everything else.

Importing this module does not require PySide6; ``create_tray()`` builds the Qt
objects. State changes arrive from worker threads and are marshalled onto the
GUI thread through a queued Qt signal, which is the only safe way to touch
widgets from another thread.
"""
from __future__ import annotations

import logging
from typing import Tuple

from bgassist import APP_NAME, __version__
from bgassist.core import events
from bgassist.ui import icons

log = logging.getLogger("bgassist.ui.tray")

STATE_LABELS = {
    "idle": "Listening",
    "awaiting_command": "Listening for your question…",
    "thinking": "Thinking…",
    "speaking": "Speaking…",
}


def create_tray(application) -> Tuple[object, object]:
    """Build (QApplication, QSystemTrayIcon) wired to *application*."""
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtGui import QAction, QIcon, QPixmap
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    class Bridge(QObject):
        state_changed = Signal(str)
        error_occurred = Signal(str)
        backlog = Signal(int)

    qt_app = QApplication.instance() or QApplication([])
    qt_app.setQuitOnLastWindowClosed(False)  # a menu-bar app has no windows
    qt_app.setApplicationName(APP_NAME)
    bridge = Bridge()

    def icon_for(state: str) -> QIcon:
        pixmap = QPixmap()
        pixmap.loadFromData(icons.tray_icon_bytes(state, 44), "PNG")
        icon = QIcon(pixmap)
        icon.setIsMask(True)  # template image: macOS tints it for us
        return icon

    tray = QSystemTrayIcon()
    tray.setIcon(icon_for("idle"))

    menu = QMenu()
    status_action = QAction("Starting…", tray)
    status_action.setEnabled(False)
    menu.addAction(status_action)

    trigger_action = QAction("", tray)
    trigger_action.setEnabled(False)
    menu.addAction(trigger_action)
    menu.addSeparator()

    def refresh_trigger_label() -> None:
        general = application.settings.general
        words = ", ".join(general.trigger_words)
        # 🖖 beside the trigger word when it is exactly "computer" (D2).
        suffix = "  🖖" if general.easter_egg else ""
        trigger_action.setText(f"Say “{words}”{suffix}")

    def set_status(text: str) -> None:
        status_action.setText(text)
        tray.setToolTip(f"{APP_NAME} — {text}")

    def on_state(value: str) -> None:
        set_status(STATE_LABELS.get(value, value))
        tray.setIcon(icon_for(value))

    def on_error(message: str) -> None:
        set_status(f"⚠ {message}")

    def on_backlog(dropped: int) -> None:
        set_status(f"⚠ Audio is backing up ({dropped} frames dropped)")

    bridge.state_changed.connect(on_state, Qt.ConnectionType.QueuedConnection)
    bridge.error_occurred.connect(on_error, Qt.ConnectionType.QueuedConnection)
    bridge.backlog.connect(on_backlog, Qt.ConnectionType.QueuedConnection)

    application.bus.subscribe(
        events.StateChanged, lambda e: bridge.state_changed.emit(e.state))
    application.bus.subscribe(
        events.ErrorOccurred, lambda e: bridge.error_occurred.emit(e.message))
    application.bus.subscribe(
        events.AudioBacklog, lambda e: bridge.backlog.emit(e.dropped_frames))

    # -- actions ---------------------------------------------------------
    chat_action = QAction("Open chat…", tray)
    chat_action.triggered.connect(application.show_chat)
    menu.addAction(chat_action)

    prefs_action = QAction("Preferences…", tray)
    prefs_action.triggered.connect(application.show_preferences)
    menu.addAction(prefs_action)
    menu.addSeparator()

    listen_action = QAction("Stop listening", tray)

    def refresh_listen_label() -> None:
        listen_action.setText("Stop listening" if application.listening
                              else "Start listening")

    def toggle_listening() -> None:
        try:
            if application.listening:
                application.stop_listening()
                set_status("Stopped")
            else:
                application.start_listening()
                set_status(STATE_LABELS["idle"])
        except Exception as exc:  # noqa: BLE001 - mic permission errors land here
            log.exception("could not change the listening state")
            set_status(f"⚠ {exc}")
        refresh_listen_label()

    listen_action.triggered.connect(toggle_listening)
    menu.addAction(listen_action)

    stop_action = QAction("Stop speaking", tray)
    stop_action.triggered.connect(lambda: application.orchestrator.cancel("stop"))
    menu.addAction(stop_action)
    menu.addSeparator()

    about_action = QAction(f"{APP_NAME} {__version__}", tray)
    about_action.setEnabled(False)
    menu.addAction(about_action)

    quit_action = QAction("Quit", tray)

    def quit_app() -> None:
        application.shutdown()
        qt_app.quit()

    quit_action.triggered.connect(quit_app)
    menu.addAction(quit_action)

    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: application.show_chat()
        if reason == QSystemTrayIcon.ActivationReason.Trigger else None)

    refresh_trigger_label()
    refresh_listen_label()
    set_status(STATE_LABELS.get(application.orchestrator.state.value, "Ready"))
    tray.show()

    application.settings_store.subscribe(
        lambda settings, changed: refresh_trigger_label())

    # The optional global shortcut (D17a): dormant unless one has been set.
    from bgassist.platform.hotkey import GlobalHotkey

    hotkey = GlobalHotkey(application.show_chat)
    hotkey.set_sequence(application.settings.general.hotkey, parent=menu)
    application.settings_store.subscribe(
        lambda settings, changed: hotkey.set_sequence(settings.general.hotkey,
                                                      parent=menu)
        if "general.hotkey" in changed else None)
    tray._hotkey = hotkey  # keep it alive

    return qt_app, tray
