# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for BackgroundAssistant (macOS and Windows).

Written for PyInstaller 6: no cipher, no ``a.zipfiles``, ``PYZ(a.pure)``.

onedir rather than onefile: a one-file bundle unpacks ~500 MB to a temporary
directory on every launch, which is unusable for something that starts at
login. arm64 only on macOS — universal2 would need universal wheels for
ctranslate2 and onnxruntime, which are not reliably available (§8.1).
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT))
from bgassist import __version__  # noqa: E402

datas = [
    (str(ROOT / "bgassist" / "ui" / "web"), "bgassist/ui/web"),
    (str(ROOT / "assets" / "tray"), "assets/tray"),
]
binaries = []
hiddenimports = ["keyring.backends.macOS", "keyring.backends.Windows",
                 "keyring.backends.SecretService"]

# The speech stack ships model files and native libraries that PyInstaller
# cannot find by following imports alone.
for package in ("faster_whisper", "ctranslate2", "sounddevice", "webrtcvad",
                "onnxruntime", "tokenizers", "av", "huggingface_hub"):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
        datas += package_datas
        binaries += package_binaries
        hiddenimports += package_hidden
    except Exception as exc:  # noqa: BLE001 - an optional package may be absent
        print(f"spec: skipping {package}: {exc}")

# Optional extras: bundled if installed, absent otherwise.
for package in ("openwakeword", "piper"):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
        datas += package_datas
        binaries += package_binaries
        hiddenimports += package_hidden
    except Exception:  # noqa: BLE001
        pass

# A bundled voice, if one has been downloaded into assets/voices.
voices = ROOT / "assets" / "voices"
if voices.is_dir():
    datas.append((str(voices), "assets/voices"))

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Only obviously-unused third-party trees. Excluding PySide6 submodules
    # by name interferes with its own hook's collection, which is not worth
    # the megabytes it would save.
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "notebook"],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="BackgroundAssistant",
    debug=False,
    strip=False,
    upx=False,               # UPX breaks code signing
    console=False,           # no terminal window on Windows
    target_arch="arm64" if sys.platform == "darwin" else None,
    icon=str(ROOT / "assets" / ("icon.icns" if sys.platform == "darwin"
                                else "icon.ico")),
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, name="BackgroundAssistant",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="BackgroundAssistant.app",
        icon=str(ROOT / "assets" / "icon.icns"),
        bundle_identifier="com.edmartin.backgroundassistant",
        version=__version__,
        info_plist={
            "LSUIElement": True,            # menu-bar only, no Dock icon
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": __version__,
            "NSMicrophoneUsageDescription":
                "BackgroundAssistant listens for your trigger word so it can "
                "answer questions. Speech is transcribed on this Mac and is not "
                "written to disk unless you asked a question.",
            "NSSpeechRecognitionUsageDescription":
                "Used only if you choose Apple speech recognition in Preferences "
                "instead of the built-in Whisper model.",
        },
    )
