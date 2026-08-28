#!/usr/bin/env bash
# Double-click this in Finder to build the app and the DMG.
#
# Terminal opens to show progress — there is nothing to type. When it finishes,
# Finder opens with the DMG selected; double-click that and drag the app into
# Applications. After the first launch you never need any of this again.
cd "$(dirname "$0")"

bash build/run_build.sh
status=$?

echo
if [[ -f dist/BackgroundAssistant.dmg ]]; then
  echo "The DMG is ready. Opening it in Finder…"
  open -R dist/BackgroundAssistant.dmg
else
  echo "No DMG was produced. The whole run is in build/build.log."
fi

echo
read -r -p "Press return to close this window. " _
exit "$status"
