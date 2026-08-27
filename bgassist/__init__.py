"""BackgroundAssistant — an always-on, local-first voice assistant.

Continuously transcribes nearby speech in memory, listens for a configurable
trigger word (default: "computer"), works out what was asked, asks an LLM
(local or cloud), and speaks the answer back.

Nothing you say is written to disk unless you actually triggered an exchange.
"""

__version__ = "0.2.0"
APP_NAME = "BackgroundAssistant"
BUNDLE_ID = "com.edmartin.backgroundassistant"
