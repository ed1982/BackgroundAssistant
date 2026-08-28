"""The packaging configuration, checked without building anything.

A build takes minutes and needs a Mac; these assertions catch the mistakes
that would otherwise only show up there — a missing entitlement, a data file
that is not collected, a plist that would put a Dock icon on a menu-bar app.
"""
import plistlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"


def read(name: str) -> str:
    return (BUILD / name).read_text(encoding="utf-8")


def test_the_build_files_are_all_present():
    for name in ("backgroundassistant.spec", "build_macos.sh", "build_windows.ps1",
                 "entitlements.plist", "Info.plist.template", "installer.iss",
                 "READ_ME_FIRST.txt", "README.md"):
        assert (BUILD / name).exists(), name


def test_the_entitlements_cover_the_microphone_and_chromiums_jit():
    """QtWebEngine embeds Chromium; without these three a signed build crashes
    the moment a window opens (§8.1)."""
    data = plistlib.loads((BUILD / "entitlements.plist").read_bytes())
    for key in ("com.apple.security.device.audio-input",
                "com.apple.security.cs.allow-jit",
                "com.apple.security.cs.allow-unsigned-executable-memory",
                "com.apple.security.cs.disable-library-validation"):
        assert data.get(key) is True, key


def test_the_bundle_is_a_menu_bar_app_that_explains_the_microphone():
    template = read("Info.plist.template").replace("__VERSION__", "1.0")
    data = plistlib.loads(template.encode())
    assert data["LSUIElement"] is True            # no Dock icon
    assert data["CFBundleIdentifier"] == "com.edmartin.backgroundassistant"
    assert data["LSMinimumSystemVersion"] == "13.0"   # SMAppService needs 13
    assert "listens for your trigger word" in data["NSMicrophoneUsageDescription"]


def test_the_spec_collects_what_import_analysis_cannot_find():
    spec = read("backgroundassistant.spec")
    for package in ("faster_whisper", "ctranslate2", "sounddevice", "webrtcvad"):
        assert package in spec, package
    assert "bgassist/ui/web" in spec, "the web UI must be bundled"
    assert "assets/tray" in spec, "the tray icons must be bundled"
    assert "keyring.backends" in spec, "keyring backends are loaded dynamically"


def test_the_spec_does_not_compress_the_binaries():
    """UPX and code signing do not coexist."""
    assert "upx=False" in read("backgroundassistant.spec")


def test_the_spec_targets_arm64_on_macos():
    assert 'target_arch="arm64"' in read("backgroundassistant.spec")


def test_the_build_script_runs_the_tests_and_the_check_before_shipping():
    script = read("build_macos.sh")
    assert "pytest" in script
    assert "--check" in script
    assert "--smoke" in script
    assert "--options runtime" in script
    assert "entitlements.plist" in script


def test_the_build_script_is_executable():
    assert (BUILD / "build_macos.sh").stat().st_mode & 0o111


def test_the_windows_installer_is_per_user():
    installer = read("installer.iss")
    assert "PrivilegesRequired=lowest" in installer
    assert "CurrentVersion\\Run" in installer     # launch at login (D13)


def test_the_read_me_first_explains_gatekeeper():
    text = read("READ_ME_FIRST.txt")
    assert "right-click" in text.lower()
    assert "microphone" in text.lower()


def test_the_version_is_consistent_everywhere():
    from bgassist import __version__

    pyproject = (ROOT / "pyproject.toml").read_text()
    assert f'version = "{__version__}"' in pyproject
    assert __version__ in read("installer.iss")


def test_the_gitignore_keeps_private_and_generated_files_out():
    ignored = (ROOT / ".gitignore").read_text()
    for pattern in ("config.json", "*.log", "*.db", "settings.json",
                    "assets/tray/", ".venv/"):
        assert pattern in ignored, pattern


def test_no_generated_icon_is_committed():
    """The icon is drawn by tools/make_icons.py, not stored as a binary."""
    import subprocess

    result = subprocess.run(["git", "ls-files", "assets"], cwd=ROOT,
                            capture_output=True, text=True)
    if result.returncode != 0:  # pragma: no cover - not a git checkout
        pytest.skip("not a git checkout")
    assert result.stdout.strip() == ""


def test_the_icon_generator_runs(tmp_path):
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_icons.py"),
         "--out", str(tmp_path)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "icon.iconset" / "icon_512x512.png").exists()
    # iconutil refuses a set containing anything it does not recognise.
    from bgassist.ui.icons import ICONSET

    assert {p.name for p in (tmp_path / "icon.iconset").glob("*")} == \
        {name for name, _size in ICONSET}
    assert (tmp_path / "icon.ico").exists()
    assert (tmp_path / "tray" / "idle.png").exists()


def test_the_project_metadata_declares_the_optional_extras():
    pyproject = (ROOT / "pyproject.toml").read_text()
    for extra in ("piper", "spotter", "windows", "macos", "dev"):
        assert re.search(rf"^{extra} = ", pyproject, re.M), extra
    for dependency in ("keyring", "cryptography", "platformdirs", "PySide6"):
        assert dependency in pyproject, dependency


def test_the_smoke_and_check_modes_never_reach_the_real_keychain():
    """An ad-hoc signed binary asking for a keychain item puts up a modal
    prompt, which would hang the build that is running it — and neither mode
    should be writing to the user's real keychain anyway."""
    source = (ROOT / "bgassist" / "cli.py").read_text()
    for mode in ("def run_check", "def run_smoke"):
        body = source.split(mode, 1)[1].split("\ndef ", 1)[0]
        assert "MemorySecretStore()" in body, mode


def test_the_smoke_mode_uses_a_throwaway_data_directory():
    source = (ROOT / "bgassist" / "cli.py").read_text()
    body = source.split("def run_smoke", 1)[1].split("\ndef ", 1)[0]
    assert "BGASSIST_HOME" in body
