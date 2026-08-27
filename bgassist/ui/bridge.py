"""The Python side of the QWebChannel API the two web views call.

No local HTTP server and no open port: the pages are loaded from the bundle
and talk to this object directly, which is both simpler and safer (§7).

Everything crossing this boundary is deliberate. In particular an API key
never does: the UI receives a display stub (``sk-…4f2a``) and posts a new key
only on save.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from bgassist import APP_NAME, __version__
from bgassist.core import events

log = logging.getLogger("bgassist.ui.bridge")


class BridgeCore:
    """The whole API surface, free of Qt so it can be unit-tested.

    :class:`WebBridge` below is the thin QObject wrapper that exposes these
    methods as slots.
    """

    def __init__(self, application):
        self.app = application
        self.settings_store = application.settings_store

    # -- state ------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        settings = self.app.settings
        general = settings.general
        return {
            "app": {"name": APP_NAME, "version": __version__},
            "state": self.app.orchestrator.state.value,
            "listening": self.app.listening,
            "trigger": {
                "words": list(general.trigger_words),
                "easter_egg": general.easter_egg,   # 🖖 (D2)
            },
            "provider": {
                "name": settings.ai.provider,
                "model": settings.ai.model,
            },
            "error": self.app.last_error,
        }

    # -- conversations ----------------------------------------------------
    def list_conversations(self, query: str = "") -> List[Dict[str, Any]]:
        store = self.app.conversations
        conversations = store.search(query) if query else store.list_conversations()
        return [{
            "id": c.id,
            "title": c.title or "Untitled",
            "updated": c.updated_ts,
            "relative": _relative(c.updated_ts),
        } for c in conversations]

    def load_conversation(self, conversation_id: int) -> Dict[str, Any]:
        store = self.app.conversations
        conversation = store.get_conversation(conversation_id)
        if conversation is None:
            return {"id": None, "messages": []}
        return {
            "id": conversation.id,
            "title": conversation.title,
            "messages": [self._message(m) for m in store.messages(conversation_id)],
        }

    @staticmethod
    def _message(message) -> Dict[str, Any]:
        return {
            "id": message.id,
            "role": message.role,
            "ts": message.ts,
            "text": message.spoken_text,
            # The remainder of an interrupted answer, shown dimmed behind a
            # disclosure (§5.4.1). Honest, and occasionally useful.
            "unspoken": message.unspoken_text,
            "interrupted": message.interrupted,
            # A question cut off before a word was spoken shows as cancelled
            # rather than vanishing, and is never sent to the model (§5.4.4).
            "superseded": message.superseded,
            "context": message.context,
            "source": message.source,
        }

    def new_conversation(self) -> int:
        return self.app.conversations.create_conversation()

    def delete_conversation(self, conversation_id: int) -> bool:
        self.app.conversations.delete_conversation(conversation_id)
        return True

    def rename_conversation(self, conversation_id: int, title: str) -> bool:
        self.app.conversations.set_title(conversation_id, title)
        return True

    def export_conversation(self, conversation_id: int) -> str:
        return self.app.conversations.export_markdown(conversation_id)

    # -- asking -----------------------------------------------------------
    def send(self, text: str) -> bool:
        """A typed question. Shares one conversation with voice (D10)."""
        self.app.orchestrator.ask_text(text)
        return True

    def push_to_talk(self, enable: bool) -> bool:
        """Listen on demand, bypassing the wake word (D10)."""
        if enable:
            self.app.orchestrator.reset()
            if not self.app.listening:
                self.app.start_listening()
            self.app.orchestrator.push_to_talk = True
        else:
            self.app.orchestrator.push_to_talk = False
        return True

    def stop(self) -> bool:
        self.app.orchestrator.cancel("stop")
        return True

    def speak_again(self, conversation_id: int, message_id: int) -> bool:
        for message in self.app.conversations.messages(conversation_id):
            if message.id == message_id:
                try:
                    self.app.tts.speak(message.spoken_text)
                except Exception as exc:  # noqa: BLE001
                    log.error("speak again failed: %s", exc)
                return True
        return False

    # -- settings ---------------------------------------------------------
    def get_settings(self) -> Dict[str, Any]:
        from bgassist.llm import PRESETS
        from bgassist.settings.secrets import display_stub
        from bgassist.storage.crypto import encryption_status

        data = self.app.settings.to_dict()
        accounts = {name: preset["keyring_account"]
                    for name, preset in PRESETS.items()}
        data["_meta"] = {
            "providers": [{"id": name, **preset} for name, preset in PRESETS.items()],
            "keys": {name: display_stub(self.app.secrets.get(account))
                     for name, account in accounts.items()},
            "keychain_available": self.app.secrets.available,
            "encryption": encryption_status(self.app.conversations.cipher),
            "voices": _voices(),
            "input_devices": _devices("input"),
            "output_devices": _devices("output"),
            "whisper_models": list(_whisper_models()),
            "data_dir": str(_data_dir()),
            "log_dir": str(_log_dir()),
            "version": __version__,
            "counts": self.app.conversations.count(),
            "login_item_supported": _login_item_supported(),
        }
        return data

    def update_settings(self, changes: Dict[str, Any]) -> List[str]:
        if isinstance(changes, str):
            changes = json.loads(changes)
        return self.settings_store.update(changes)

    def set_api_key(self, provider: str, key: str) -> bool:
        """Write-only: the key goes straight to the keychain (§6.2)."""
        if not key:
            return False
        self.app.set_api_key(provider, key)
        self.app.llm = self.app.build_llm()
        self.app.orchestrator.llm = self.app.llm
        self.app.orchestrator.responder.llm = self.app.llm
        return True

    def clear_api_key(self, provider: str) -> bool:
        from bgassist.llm import PRESETS

        account = PRESETS.get(provider, {}).get("keyring_account", provider)
        self.app.secrets.delete(account)
        return True

    def test_connection(self) -> Dict[str, Any]:
        """One tiny request, reporting what actually happened (§6.5)."""
        try:
            llm = self.app.build_llm()
            result = llm.test_connection()
            result["ok"] = True
            return result
        except Exception as exc:  # noqa: BLE001 - the message is the point
            return {"ok": False, "error": str(exc)}

    def detect_servers(self) -> List[Dict[str, Any]]:
        from bgassist.llm import detect_local_servers

        return [{"label": s.label, "base_url": s.base_url, "kind": s.kind,
                 "models": s.models, "note": s.note}
                for s in detect_local_servers()]

    def list_models(self) -> List[str]:
        try:
            return self.app.build_llm().list_models()
        except Exception as exc:  # noqa: BLE001
            log.info("could not list models: %s", exc)
            return []

    def restore_system_prompt(self) -> str:
        from bgassist.llm.prompts import DEFAULT_SYSTEM_PROMPT

        self.settings_store.update({"ai.system_prompt": DEFAULT_SYSTEM_PROMPT})
        return DEFAULT_SYSTEM_PROMPT

    def preview_voice(self, voice: Optional[str] = None) -> bool:
        try:
            from bgassist.tts import make_tts

            settings = self.app.settings.voice
            engine = make_tts(type("V", (), {
                "engine": settings.engine, "rate": settings.rate,
                "voice": voice or settings.voice})())
            engine.speak("All systems are operating within normal parameters.")
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("voice preview failed: %s", exc)
            return False

    # -- privacy ----------------------------------------------------------
    def delete_all_conversations(self) -> bool:
        self.app.conversations.delete_all()
        return True

    def delete_everything(self) -> bool:
        """Conversations, settings and keys. The nuclear option in Privacy."""
        from bgassist.llm import PRESETS

        self.app.conversations.delete_all()
        for preset in PRESETS.values():
            self.app.secrets.delete(preset["keyring_account"])
        self.app.secrets.delete("conversation-db-key")
        self.settings_store.reset()
        return True

    def reveal(self, what: str = "logs") -> bool:
        import subprocess
        import sys

        target = _log_dir() if what == "logs" else _data_dir()
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            elif sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("could not reveal %s: %s", target, exc)
            return False

    def export_settings(self) -> str:
        return json.dumps(self.app.settings.to_dict(), indent=2, sort_keys=True)

    def factory_reset(self) -> bool:
        self.settings_store.reset()
        return True


# -- helpers --------------------------------------------------------------

def _relative(ts: float, now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    delta = max(0.0, now - ts)
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)} min ago"
    if delta < 86400:
        return f"{int(delta // 3600)} h ago"
    if delta < 7 * 86400:
        return f"{int(delta // 86400)} d ago"
    return time.strftime("%d %b", time.localtime(ts))


def _voices() -> List[str]:
    try:
        from bgassist.tts import available_voices

        return available_voices()
    except Exception:  # noqa: BLE001
        return []


def _devices(kind: str) -> List[str]:
    try:
        from bgassist.audio.capture import list_devices, list_output_devices

        return list_devices() if kind == "input" else list_output_devices()
    except Exception:  # noqa: BLE001 - no audio stack here
        return []


def _whisper_models():
    from bgassist.stt.whisper import MODEL_SIZES

    return MODEL_SIZES


def _data_dir():
    from bgassist.platform import paths

    return paths.data_dir()


def _log_dir():
    from bgassist.platform import paths

    return paths.log_dir()


def _login_item_supported() -> bool:
    from bgassist.platform import login_item

    return login_item.supported()


# -- the Qt wrapper -------------------------------------------------------

def make_web_bridge(application):
    """A QObject exposing :class:`BridgeCore` to JavaScript over QWebChannel."""
    from PySide6.QtCore import QObject, Signal, Slot

    core = BridgeCore(application)

    class WebBridge(QObject):
        stateChanged = Signal(str)
        tokenStreamed = Signal(str)
        answerFinished = Signal(str)
        conversationsChanged = Signal(str)
        errorOccurred = Signal(str)
        utteranceHeard = Signal(str)

        def __init__(self) -> None:
            super().__init__()
            self.core = core
            bus = application.bus
            bus.subscribe(events.StateChanged,
                          lambda e: self.stateChanged.emit(e.state))
            bus.subscribe(events.TokenStreamed,
                          lambda e: self.tokenStreamed.emit(e.text))
            bus.subscribe(events.AnswerFinished,
                          lambda e: self.answerFinished.emit(json.dumps({
                              "text": e.text, "spoken_upto": e.spoken_upto,
                              "interrupted": e.interrupted,
                              "conversation_id": e.conversation_id})))
            bus.subscribe(events.ConversationsChanged,
                          lambda e: self.conversationsChanged.emit(
                              json.dumps({"id": e.conversation_id,
                                          "reason": e.reason})))
            bus.subscribe(events.ErrorOccurred,
                          lambda e: self.errorOccurred.emit(json.dumps({
                              "message": e.message, "detail": e.detail})))
            bus.subscribe(events.UtteranceHeard,
                          lambda e: self.utteranceHeard.emit(e.text))

        # Every slot returns JSON so the JS side has one calling convention.
        @Slot(result=str)
        def snapshot(self) -> str:
            return json.dumps(core.snapshot())

        @Slot(str, result=str)
        def listConversations(self, query: str) -> str:
            return json.dumps(core.list_conversations(query))

        @Slot(int, result=str)
        def loadConversation(self, conversation_id: int) -> str:
            return json.dumps(core.load_conversation(conversation_id))

        @Slot(result=int)
        def newConversation(self) -> int:
            return core.new_conversation()

        @Slot(int, result=bool)
        def deleteConversation(self, conversation_id: int) -> bool:
            return core.delete_conversation(conversation_id)

        @Slot(int, str, result=bool)
        def renameConversation(self, conversation_id: int, title: str) -> bool:
            return core.rename_conversation(conversation_id, title)

        @Slot(int, result=str)
        def exportConversation(self, conversation_id: int) -> str:
            return core.export_conversation(conversation_id)

        @Slot(str, result=bool)
        def send(self, text: str) -> bool:
            return core.send(text)

        @Slot(bool, result=bool)
        def pushToTalk(self, enable: bool) -> bool:
            return core.push_to_talk(enable)

        @Slot(result=bool)
        def stop(self) -> bool:
            return core.stop()

        @Slot(int, int, result=bool)
        def speakAgain(self, conversation_id: int, message_id: int) -> bool:
            return core.speak_again(conversation_id, message_id)

        @Slot(result=str)
        def getSettings(self) -> str:
            return json.dumps(core.get_settings())

        @Slot(str, result=str)
        def updateSettings(self, changes_json: str) -> str:
            return json.dumps(core.update_settings(json.loads(changes_json)))

        @Slot(str, str, result=bool)
        def setApiKey(self, provider: str, key: str) -> bool:
            return core.set_api_key(provider, key)

        @Slot(str, result=bool)
        def clearApiKey(self, provider: str) -> bool:
            return core.clear_api_key(provider)

        @Slot(result=str)
        def testConnection(self) -> str:
            return json.dumps(core.test_connection())

        @Slot(result=str)
        def detectServers(self) -> str:
            return json.dumps(core.detect_servers())

        @Slot(result=str)
        def listModels(self) -> str:
            return json.dumps(core.list_models())

        @Slot(result=str)
        def restoreSystemPrompt(self) -> str:
            return core.restore_system_prompt()

        @Slot(str, result=bool)
        def previewVoice(self, voice: str) -> bool:
            return core.preview_voice(voice or None)

        @Slot(result=bool)
        def deleteAllConversations(self) -> bool:
            return core.delete_all_conversations()

        @Slot(result=bool)
        def deleteEverything(self) -> bool:
            return core.delete_everything()

        @Slot(str, result=bool)
        def reveal(self, what: str) -> bool:
            return core.reveal(what)

        @Slot(result=str)
        def exportSettings(self) -> str:
            return core.export_settings()

        @Slot(result=bool)
        def factoryReset(self) -> bool:
            return core.factory_reset()

    return WebBridge()
