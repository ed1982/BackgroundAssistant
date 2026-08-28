#!/usr/bin/env bash
# Build "Background Assistant.app" and a drag-to-install DMG.
#
#   build/build_macos.sh                 build, ad-hoc sign, make the DMG
#   build/build_macos.sh --notarize      also submit to notarytool and staple
#
# Signing is ad-hoc until a Developer ID certificate exists (D4); everything
# else about the build — hardened runtime, entitlements, the notarize step —
# is already in place, so turning it on later is a one-line change.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
# Displayed name, so it has a space in it: quote every use.
APP="dist/Background Assistant.app"
DMG="dist/BackgroundAssistant.dmg"
STAGE="build/dmg-stage"
IDENTITY="${CODESIGN_IDENTITY:--}"          # "-" is ad-hoc
NOTARIZE=0
[[ "${1:-}" == "--notarize" ]] && NOTARIZE=1

# Remove a directory even while Finder is watching it.
#
# The build finishes by opening Finder on dist/, so on the next run Finder is
# sitting in that folder writing .DS_Store back into it. `rm -rf` unlinks
# everything, Finder recreates one file, and the final rmdir fails with
# "Directory not empty". Renaming has no such race: it succeeds atomically
# whatever the folder gains a moment later, and what we then delete is a name
# nothing is watching.
clean_dir() {
  local target="$1"
  [[ -e "$target" ]] || return 0
  local doomed
  doomed="$(mktemp -d "${TMPDIR:-/tmp}/bgassist-clean-XXXXXX")"
  if mv "$target" "$doomed/gone" 2>/dev/null; then
    rm -rf "$doomed" 2>/dev/null || true
  else
    rm -rf "$target" || true
    rmdir "$doomed" 2>/dev/null || true
  fi
}

# The whole build has to work from a fresh clone with nothing set up, because
# the instruction is "double-click this" and anything else is a broken promise.
# So: find a usable interpreter, make the venv, install what is missing.
SUPPORTED_PYTHON=(python3.12 python3.11 python3.10 python3)

find_interpreter() {
  local candidate path
  for candidate in "${SUPPORTED_PYTHON[@]}"; do
    path="$(command -v "$candidate" 2>/dev/null)" || continue
    if "$path" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,10),(3,11),(3,12)) else 1)' 2>/dev/null; then
      printf '%s' "$path"
      return 0
    fi
  done
  return 1
}

if [[ ! -x ".venv/bin/python" ]]; then
  echo "==> First run: setting up .venv"
  # macOS ships Python 3.9, which is too old, and its system interpreter
  # refuses `pip install` anyway (externally managed).
  if ! interpreter="$(find_interpreter)"; then
    echo
    echo "No suitable Python found. Background Assistant needs 3.10, 3.11 or 3.12."
    echo "Install one and run this again:"
    echo "    brew install python@3.12"
    echo "or download it from https://www.python.org/downloads/"
    exit 1
  fi
  echo "using $interpreter"
  "$interpreter" -m venv .venv
fi
PY=".venv/bin/python"

echo "==> Python"
"$PY" -c 'import sys; print(sys.version); assert sys.version_info[:2] in ((3,10),(3,11),(3,12)), sys.version'
"$PY" -c 'import platform; assert platform.machine() == "arm64", f"arm64 only: {platform.machine()}"'

echo "==> Dependencies"
REQUIRED="PySide6, faster_whisper, sounddevice, webrtcvad, keyring, cryptography, platformdirs, numpy, pytest, PyInstaller"
if ! "$PY" -c "import $REQUIRED" 2>/dev/null; then
  echo "installing what is missing — a few minutes, and only the first time"
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install -e ".[dev,macos,build]"
fi
"$PY" -c "import PyInstaller; print('pyinstaller', PyInstaller.__version__)"

echo "==> Icons"
"$PY" tools/make_icons.py --out assets
rm -f assets/icon.icns
iconutil -c icns assets/icon.iconset -o assets/icon.icns

echo "==> Tests"
# Not the integration test: it downloads a whisper model on a clean machine,
# and a packaging run should not depend on the network. Run it yourself with
#   .venv/bin/python -m pytest -m integration
"$PY" -m pytest -q -m "not integration"

echo "==> Headless check"
"$PY" main.py --check

echo "==> PyInstaller"
# A mounted disk image keeps a handle on its own backing file, and hdiutil
# refuses to overwrite a volume that is currently attached — so let go of the
# last build's DMG before deleting and rebuilding it.
hdiutil detach "/Volumes/Background Assistant" -quiet 2>/dev/null || true
clean_dir build/work
clean_dir dist
"$PY" -m PyInstaller --clean --noconfirm --workpath build/work --distpath dist \
    build/backgroundassistant.spec

echo "==> Signing (identity: $IDENTITY)"
# Nested code first, then the bundle: --deep is documented as unreliable for
# anything with frameworks in it, and QtWebEngine brings several.
find "$APP/Contents" \( -name "*.dylib" -o -name "*.so" \) -print0 |
  xargs -0 -n1 codesign --force --timestamp=none --options runtime \
      --entitlements build/entitlements.plist --sign "$IDENTITY" 2>/dev/null || true
for helper in "$APP/Contents/Frameworks/QtWebEngineCore.framework/Helpers/QtWebEngineProcess.app"; do
  [[ -d "$helper" ]] && codesign --force --options runtime --timestamp=none \
      --entitlements build/entitlements.plist --sign "$IDENTITY" "$helper"
done
codesign --force --deep --options runtime --timestamp=none \
    --entitlements build/entitlements.plist \
    --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
echo "entitlements as signed:"
codesign -d --entitlements - "$APP" 2>&1 | tail -n +2 || true

echo "==> Smoke test of the built app"
"$APP/Contents/MacOS/BackgroundAssistant" --check
"$APP/Contents/MacOS/BackgroundAssistant" --smoke

echo "==> DMG"
rm -f "$DMG"
made_dmg=0
if command -v create-dmg >/dev/null 2>&1; then
  if create-dmg \
      --volname "Background Assistant" \
      --window-size 560 380 \
      --icon-size 96 \
      --icon "Background Assistant.app" 140 180 \
      --app-drop-link 400 180 \
      --add-file "Read Me First.txt" build/READ_ME_FIRST.txt 280 320 \
      "$DMG" "$APP"; then
    made_dmg=1
  else
    echo "create-dmg failed; falling back to hdiutil"
  fi
fi
if [[ $made_dmg -eq 0 ]]; then
  # hdiutil is always present, needs no Homebrew, and still gives the
  # drag-to-Applications layout that matters.
  hdiutil detach "/Volumes/Background Assistant" -quiet 2>/dev/null || true
  clean_dir "$STAGE"
  mkdir -p "$STAGE"
  cp -R "$APP" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  cp build/READ_ME_FIRST.txt "$STAGE/Read Me First.txt"
  hdiutil create -volname "Background Assistant" -srcfolder "$STAGE" \
      -ov -format UDZO -quiet "$DMG"
  clean_dir "$STAGE"
fi
codesign --force --sign "$IDENTITY" "$DMG" || true

if [[ $NOTARIZE -eq 1 ]]; then
  if [[ "$IDENTITY" == "-" ]]; then
    echo "!! --notarize needs a Developer ID certificate; set CODESIGN_IDENTITY."
    exit 1
  fi
  echo "==> Notarising"
  xcrun notarytool submit "$DMG" --keychain-profile "notary" --wait
  xcrun stapler staple "$DMG"
fi

echo
echo "Built: $ROOT/$APP"
echo "       $ROOT/$DMG"
du -sh "$APP" | awk '{print "App size:  " $1}'
du -sh "$DMG" | awk '{print "DMG size:  " $1}'
