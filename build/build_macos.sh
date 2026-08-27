#!/usr/bin/env bash
# Build BackgroundAssistant.app and a drag-to-install DMG.
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
APP="dist/BackgroundAssistant.app"
DMG="dist/BackgroundAssistant.dmg"
IDENTITY="${CODESIGN_IDENTITY:--}"          # "-" is ad-hoc
NOTARIZE=0
[[ "${1:-}" == "--notarize" ]] && NOTARIZE=1

echo "==> Python"
python3 -c 'import sys; assert sys.version_info[:2] in ((3,10),(3,11),(3,12)), sys.version'

echo "==> Icons"
python3 tools/make_icons.py --out assets
iconutil -c icns assets/icon.iconset -o assets/icon.icns

echo "==> Tests"
python3 -m pytest -q

echo "==> Headless check"
python3 main.py --check

echo "==> PyInstaller"
rm -rf build/work dist
pyinstaller --clean --noconfirm --workpath build/work --distpath dist \
    build/backgroundassistant.spec

echo "==> Signing (identity: $IDENTITY)"
codesign --force --deep --options runtime --timestamp \
    --entitlements build/entitlements.plist \
    --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

echo "==> Smoke test of the built app"
"$APP/Contents/MacOS/BackgroundAssistant" --check
"$APP/Contents/MacOS/BackgroundAssistant" --smoke

echo "==> DMG"
if command -v create-dmg >/dev/null 2>&1; then
  rm -f "$DMG"
  create-dmg \
      --volname "BackgroundAssistant" \
      --window-size 560 380 \
      --icon-size 96 \
      --icon "BackgroundAssistant.app" 140 180 \
      --app-drop-link 400 180 \
      --add-file "Read Me First.txt" build/READ_ME_FIRST.txt 280 320 \
      "$DMG" "$APP"
else
  echo "create-dmg is not installed (brew install create-dmg); packaging a zip instead"
  (cd dist && zip -qry BackgroundAssistant.zip BackgroundAssistant.app)
fi

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
echo "Built: $APP"
[[ -f "$DMG" ]] && echo "       $DMG"
du -sh "$APP" | awk '{print "Size:  " $1}'
