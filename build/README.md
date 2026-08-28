# Build

Everything here produces an installable app from a source checkout. Nothing in
this directory is needed to *run* Background Assistant from source — that is
just `python main.py`.

## macOS (arm64)

```
pip install -e '.[dev,macos]' pyinstaller
brew install create-dmg          # optional, for the DMG
build/build_macos.sh             # add --notarize once a certificate exists
```

The script regenerates the icons, runs the tests and the headless check, builds
the `.app` with PyInstaller, signs it with the hardened runtime and the
entitlements QtWebEngine needs, smoke-tests the built binary, and produces a
drag-to-install DMG.

Signing is ad-hoc (`-`) until a Developer ID certificate exists; set
`CODESIGN_IDENTITY` to use a real one. Until then the first launch needs
right-click → Open, which `READ_ME_FIRST.txt` explains inside the DMG.

**Keychain note:** Keychain items are bound to the signing identity, so an
ad-hoc build re-prompts for access whenever the signature changes between
rebuilds. That is expected (spike S6) and goes away with a real certificate.

## Windows

```
pip install -e .[dev,windows] pyinstaller
powershell -ExecutionPolicy Bypass -File build\build_windows.ps1
```

Produces `dist\BackgroundAssistant\` and, with Inno Setup installed, a
per-user installer. An unsigned build triggers a SmartScreen warning on first
run — the same trade-off as macOS.

## Sizes

Expect roughly 400–600 MB installed: PySide6 with WebEngine is about 250 MB,
CTranslate2 and onnxruntime about 80 MB, the bundled speech model about 75 MB,
and a Piper voice about 60 MB. If that proves too large, the model can be
downloaded on first run instead of bundled — `bgassist/stt/whisper.py` already
stores it in the application-support directory rather than in the bundle.
