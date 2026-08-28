#!/usr/bin/env bash
# Run the macOS build with everything captured to build/build.log.
#
# Used when the build is driven from somewhere that cannot watch the terminal:
# the log is the interface, and the last line says how it ended.
cd "$(dirname "$0")/.."
LOG="build/build.log"
: > "$LOG"
{
  echo "=== build started $(date '+%Y-%m-%d %H:%M:%S') ==="
  if bash build/build_macos.sh "$@"; then
    echo "=== BUILD OK $(date '+%H:%M:%S') ==="
  else
    echo "=== BUILD FAILED (exit $?) $(date '+%H:%M:%S') ==="
  fi
} 2>&1 | tee -a "$LOG"
