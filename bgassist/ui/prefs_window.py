"""The Preferences window (§6.4).

Six tabs — General, AI, Voice, Listening, Privacy, Advanced — in the same web
stack as the chat window, so there is one design language and one place to
maintain it (D3). This is the window that kills F2: you can install a key and
pick a provider without ever opening a terminal.
"""
from __future__ import annotations

from bgassist import APP_NAME
from bgassist.ui.window import WebWindow


class PreferencesWindow(WebWindow):
    page = "prefs.html"
    title = f"{APP_NAME} Preferences"
    default_size = (760, 640)
    minimum_size = (620, 520)
