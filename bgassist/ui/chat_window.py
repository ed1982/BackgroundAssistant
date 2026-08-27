"""The chat window (§7): history rail, streaming messages, composer.

Hidden by default and opened from the tray, the optional shortcut, or a
notification (D9). Answers are still spoken; this is where they can also be
read, searched, corrected and continued by typing (D10).
"""
from __future__ import annotations

from bgassist import APP_NAME
from bgassist.ui.window import WebWindow


class ChatWindow(WebWindow):
    page = "chat.html"
    title = APP_NAME
    default_size = (980, 700)
