"""System-tray UI (PySide6).

Importing this module does not require PySide6; call ``create_tray_app()``
to build the Qt objects. State changes arrive from the worker thread and
are marshalled to the GUI thread via a queued Qt signal.
"""
from __future__ import annotations

import logging
from typing import Callable, Tuple

log = logging.getLogger("starcop.app")

_STATE_LABELS = {
    "idle": "Idle — listening",
    "awaiting_command": "Awaiting command…",
    "thinking": "Thinking…",
    "speaking": "Speaking…",
}


def create_tray_app(pipeline, start_listening: Callable[[], None],
                    stop_all: Callable[[], None],
                    autostart: bool = True) -> Tuple["object", "object"]:
    """Build (QApplication, QSystemTrayIcon) wired to the pipeline.

    *start_listening* starts capture + worker; *stop_all* stops both.
    Returns the app and tray so the caller can run ``app.exec()``.
    """
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

    class _Bridge(QObject):
        state_changed = Signal(str)

    app = QApplication.instance() or QApplication([])
    bridge = _Bridge()

    tray = QSystemTrayIcon()
    tray.setToolTip("Star Trek Computer")
    # No bundled icon asset: use a standard style icon (zero binary files).
    tray.setIcon(app.style().standardIcon(
        QStyle.StandardPixmap.SP_ComputerIcon))

    menu = QMenu()
    status_action = QAction("Status: starting…", tray)
    status_action.setEnabled(False)
    menu.addAction(status_action)
    menu.addSeparator()

    listening = {"flag": False}

    def _set_status(text: str) -> None:
        status_action.setText(f"Status: {text}")

    def _on_state(state) -> None:
        label = _STATE_LABELS.get(getattr(state, "value", str(state)), str(state))
        _set_status(label)

    bridge.state_changed.connect(_on_state)
    pipeline.on_state_change = lambda s: bridge.state_changed.emit(  # noqa: B023
        getattr(s, "value", str(s)))

    def _start() -> None:
        if listening["flag"]:
            return
        try:
            start_listening()
            listening["flag"] = True
            start_action.setText("Stop listening")
        except Exception as exc:  # noqa: BLE001 - surface mic/permission errors
            log.exception("failed to start listening")
            _set_status(f"Error: {exc}")

    def _stop() -> None:
        if not listening["flag"]:
            return
        stop_all()
        listening["flag"] = False
        start_action.setText("Start listening")
        _set_status("Stopped")

    start_action = QAction("Start listening", tray)
    start_action.triggered.connect(
        lambda: _stop() if listening["flag"] else _start())
    menu.addAction(start_action)

    def _speak_test() -> None:
        try:
            pipeline.tts.speak("Aye aye, captain. All systems operational.")
        except Exception as exc:  # noqa: BLE001
            log.error("speak test failed: %s", exc)

    test_action = QAction("Speak test", tray)
    test_action.triggered.connect(_speak_test)
    menu.addAction(test_action)

    quit_action = QAction("Quit", tray)

    def _quit() -> None:
        stop_all()
        app.quit()

    quit_action.triggered.connect(_quit)
    menu.addAction(quit_action)

    tray.setContextMenu(menu)
    _set_status(_STATE_LABELS.get(pipeline.state.value, pipeline.state.value))
    tray.show()

    if autostart:
        _start()

    return app, tray
