#!/usr/bin/env bash
# Run the macOS build with everything captured to build/build.log.
#
# Used when the build is driven from somewhere that cannot watch the terminal:
# the log is the interface, and the last line says how it ended. Exits with the
# build's own status, so a wrapper can act on it.
cd "$(dirname "$0")/.."
LOG="build/build.log"
: > "$LOG"

echo "=== build started $(date '+%Y-%m-%d %H:%M:%S') ===" | tee -a "$LOG"
bash build/build_macos.sh "$@" 2>&1 | tee -a "$LOG"
status=${PIPESTATUS[0]}
if [[ $status -eq 0 ]]; then
  echo "=== BUILD OK $(date '+%H:%M:%S') ===" | tee -a "$LOG"
else
  echo "=== BUILD FAILED (exit $status) $(date '+%H:%M:%S') ===" | tee -a "$LOG"
fi
exit "$status"
