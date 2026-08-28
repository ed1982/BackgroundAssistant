"""Background Assistant — an always-on, local-first voice assistant.

Continuously transcribes nearby speech in memory, listens for a configurable
trigger word (default: "computer"), works out what was asked, asks an LLM
(local or cloud), and speaks the answer back.

Nothing you say is written to disk unless you actually triggered an exchange.
"""

__version__ = "0.2.0"

#: What people see: the menu bar, the window titles, the app in Applications.
APP_NAME = "Background Assistant"

#: What the machine sees, and what must never change: the support and log
#: directories, the Windows Run key, the LaunchAgent label. Renaming the app in
#: Finder should not orphan somebody's settings and conversations, so the
#: display name and the identity are deliberately separate.
APP_ID_NAME = "BackgroundAssistant"
BUNDLE_ID = "com.edmartin.backgroundassistant"
