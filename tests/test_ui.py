"""The UI's Python surface, and the parts of the web UI that can be checked
without a browser: every control must be bound to a setting that exists, and
every backend call the pages make must exist on the bridge."""
import json
import re

import pytest

from bgassist.app import Application
from bgassist.llm.mock import MockBackend
from bgassist.settings.schema import Settings
from bgassist.settings.store import SettingsStore
from bgassist.storage import ConversationStore, NullCipher
from bgassist.stt.mock import MockTranscriber
from bgassist.tts.mock import MockTts
from bgassist.ui import icons
from bgassist.ui.bridge import BridgeCore, _relative
from bgassist.ui.window import WEB_DIR


@pytest.fixture()
def application(tmp_path):
    app = Application(
        settings_store=SettingsStore(path=tmp_path / "settings.json"),
        conversations=ConversationStore(tmp_path / "c.db", NullCipher()),
        llm=MockBackend(), tts=MockTts(), transcriber=MockTranscriber(),
        start_engine=False)
    yield app
    app.shutdown()


@pytest.fixture()
def bridge(application):
    return BridgeCore(application)


# -- the bridge -----------------------------------------------------------

def test_snapshot_reports_the_trigger_and_provider(bridge):
    snapshot = bridge.snapshot()
    assert snapshot["trigger"]["words"] == ["computer"]
    assert snapshot["trigger"]["easter_egg"] is True     # 🖖 (D2)
    assert snapshot["state"] == "idle"


def test_a_typed_question_runs_and_is_stored(bridge, application):
    bridge.send("what is the time")
    application.orchestrator.responder.wait(5)
    conversations = bridge.list_conversations()
    assert conversations
    messages = bridge.load_conversation(conversations[0]["id"])["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["source"] == "typed"


def test_the_bridge_never_hands_out_a_key(bridge, application):
    application.secrets.set("openai", "sk-super-secret-value")
    settings = bridge.get_settings()
    payload = json.dumps(settings, ensure_ascii=False)
    assert "sk-super-secret-value" not in payload
    assert settings["_meta"]["keys"]["openai"] == "sk-…alue"


def test_setting_a_key_rebuilds_the_backend(bridge, application):
    assert bridge.set_api_key("openai", "sk-new-key") is True
    assert application.secrets.get("openai") == "sk-new-key"


def test_test_connection_reports_success(bridge):
    assert bridge.test_connection()["ok"] is True


def test_test_connection_reports_the_real_error(application):

    application._llm_override = None
    application.settings_store.update({"ai.provider": "custom",
                                       "ai.base_url": "http://127.0.0.1:1/v1"})
    result = BridgeCore(application).test_connection()
    assert result["ok"] is False
    assert "127.0.0.1" in result["error"]


def test_update_settings_rejects_nonsense(bridge):
    assert bridge.update_settings({"general.nonsense": 1}) == []


def test_restore_the_default_persona(bridge, application):
    application.settings_store.update({"ai.system_prompt": "be terse"})
    restored = bridge.restore_system_prompt()
    assert "ship's computer" in restored
    assert application.settings.ai.system_prompt == restored


def test_delete_everything_clears_conversations_and_keys(bridge, application):
    bridge.send("hello")
    application.orchestrator.responder.wait(5)
    application.secrets.set("openai", "sk-x")
    bridge.delete_everything()
    assert application.conversations.count()["conversations"] == 0
    assert application.secrets.get("openai") == ""


def test_export_conversation_as_markdown(bridge, application):
    bridge.send("hello")
    application.orchestrator.responder.wait(5)
    conversation = bridge.list_conversations()[0]["id"]
    assert "**You**" in bridge.export_conversation(conversation)


def test_relative_times_read_naturally():
    assert _relative(1000.0, now=1000.0) == "just now"
    assert _relative(1000.0, now=1000.0 + 120) == "2 min ago"
    assert _relative(1000.0, now=1000.0 + 7200) == "2 h ago"


# -- the web assets -------------------------------------------------------

def _read(name: str) -> str:
    return (WEB_DIR / name).read_text(encoding="utf-8")


def test_every_page_asset_is_present():
    for name in ("chat.html", "chat.css", "chat.js", "prefs.html", "prefs.css",
                 "prefs.js", "app.css", "vendor/markdown.js"):
        assert (WEB_DIR / name).exists(), name


def test_every_preferences_control_binds_to_a_real_setting():
    settings = Settings()
    paths = set(re.findall(r'data-setting="([^"]+)"', _read("prefs.html")))
    assert paths, "no controls found — the parser is wrong"
    for path in sorted(paths):
        section, _, key = path.partition(".")
        assert hasattr(settings, section), path
        assert hasattr(getattr(settings, section), key), path


def test_every_settings_section_is_reachable_from_the_ui():
    """A setting nobody can change is a setting nobody knows about."""
    html = _read("prefs.html")
    for section in ("general", "ai", "voice", "listening", "privacy", "advanced"):
        assert f'data-pane="{section}"' in html


def test_the_pages_call_only_methods_the_bridge_has():
    known = {name for name in dir(BridgeCore) if not name.startswith("_")}
    camel = {"".join([p if i == 0 else p.title()
                      for i, p in enumerate(name.split("_"))]) for name in known}
    for page in ("chat.js", "prefs.js"):
        for call in set(re.findall(r"backend\.(\w+)\(", _read(page))):
            if call in ("stateChanged", "tokenStreamed", "answerFinished",
                        "conversationsChanged", "errorOccurred", "utteranceHeard",
                        "connectionTested", "serversDetected"):
                continue
            assert call in camel, f"{page} calls backend.{call}()"


def test_no_remote_origins_are_referenced():
    """A bundled app must work offline and the CSP forbids remote origins."""
    for name in ("chat.html", "prefs.html", "chat.js", "prefs.js", "app.css",
                 "chat.css", "prefs.css"):
        text = _read(name)
        assert "http://" not in text.replace("http://localhost", "")
        assert "https://" not in text
        for host in ("cdnjs", "unpkg", "jsdelivr", "googleapis", "//cdn"):
            assert host not in text.lower()


def test_the_pages_declare_a_restrictive_policy():
    for name in ("chat.html", "prefs.html"):
        assert "Content-Security-Policy" in _read(name)
        assert "default-src 'none'" in _read(name)


# -- icons ----------------------------------------------------------------

def test_every_tray_state_renders_a_template_png(tmp_path):
    written = icons.write_tray_icons(tmp_path)
    assert set(icons.STATES).issubset({k.replace("@2x", "") for k in written})
    for path in written.values():
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_template_icons_are_black_and_alpha_only():
    """macOS tints template images; any colour in them would be wrong."""
    import struct
    import zlib

    png = icons.render(16, "idle", template=True)
    start = png.index(b"IDAT") + 4
    length = struct.unpack(">I", png[start - 8:start - 4])[0]
    raw = zlib.decompress(png[start:start + length])
    stride = 16 * 4 + 1
    for row in range(16):
        line = raw[row * stride + 1:(row + 1) * stride]
        for pixel in range(16):
            r, g, b, _a = line[pixel * 4:pixel * 4 + 4]
            assert (r, g, b) == (0, 0, 0)


def test_the_states_differ_from_each_other():
    rendered = {state: icons.render(32, state) for state in icons.STATES}
    assert len(set(rendered.values())) == len(icons.STATES)


def test_the_icon_ladder_and_ico_are_written(tmp_path):
    written = icons.write_app_icons(tmp_path)
    assert (tmp_path / "icon.iconset").is_dir()
    assert any(p.name == "icon_1024x1024.png" for p in written)
    ico = icons.write_ico(tmp_path / "icon.ico")
    assert ico.read_bytes()[:4] == b"\x00\x00\x01\x00"
