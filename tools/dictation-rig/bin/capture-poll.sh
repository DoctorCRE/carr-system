#!/bin/bash
# capture-poll.sh — CARR dictation rig, WO-4 capture-bridge poll side.
#
# Run by launchd (com.carr.capture-poll) on a 5-minute calendar schedule, and
# safe to run by hand for testing. Each invocation scans the recordings
# root for session directories that are claimed (.capture.json present) but
# not yet ingested (no ingested.json), and asks capture-bridge.py to check
# whether the worker has produced a meeting_record for each one.
#
# capture-bridge.py poll owns the actual gate — ingested.json is written
# only once meeting_record is real (see purge-recordings.sh's marker
# contract, which purges audio/transcripts off that same marker). This
# script is only the sweep; it carries none of that logic itself.
#
# Env:
#   CARR_RECORDINGS_DIR  — override the recordings root (default: ~/Recordings)
#
# Exit code is always 0, matching consent-watch.sh / capture-watch.sh.
#
# Never touches vendor/quill, ~/.config/quill, launchd, or git.

set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
TOOL_DIR="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
BRIDGE_PY="$TOOL_DIR/bin/capture-bridge.py"

RECORDINGS_DIR="${CARR_RECORDINGS_DIR:-$HOME/Recordings}"
LOG_FILE="$HOME/Library/Logs/capture-bridge.log"

log_line() {
    local ts
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    local log_dir
    log_dir="$(dirname "$LOG_FILE")"
    mkdir -p "$log_dir" 2>/dev/null || true
    printf '%s %s\n' "$ts" "$1" >> "$LOG_FILE" 2>/dev/null || true
}

main() {
    if [ ! -d "$RECORDINGS_DIR" ]; then
        exit 0
    fi

    if [ ! -f "$BRIDGE_PY" ]; then
        log_line "ERROR capture-bridge.py missing at $BRIDGE_PY"
        exit 0
    fi

    local PYTHON_BIN
    PYTHON_BIN="$(command -v python3 2>/dev/null || true)"
    if [ -z "$PYTHON_BIN" ]; then
        log_line "ERROR python3 not found on PATH"
        exit 0
    fi

    local dir
    for dir in "$RECORDINGS_DIR"/*/; do
        [ -d "$dir" ] || continue
        dir="${dir%/}"

        [ -f "$dir/.capture.json" ] || continue
        [ -f "$dir/ingested.json" ] && continue

        "$PYTHON_BIN" "$BRIDGE_PY" poll "$dir"
    done

    exit 0
}

main "$@"
