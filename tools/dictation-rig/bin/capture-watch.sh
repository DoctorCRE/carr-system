#!/bin/bash
# capture-watch.sh — CARR dictation rig, WO-4 capture-bridge claim side.
#
# Run by launchd (com.carr.capture-watch) with WatchPaths on ~/Recordings,
# same pattern as consent-watch.sh, and safe to run by hand for testing.
# Each invocation scans the recordings root for session directories that
# have not yet been claimed (no .capture.json) and have not already been
# ingested (no ingested.json), waits briefly for the consent announcement
# to land, then hands the actual claim to capture-bridge.py.
#
# CONSENT-BEFORE-CLAIM, structurally: /capture/claim on the worker side
# refuses any request with no consent.announced_at (400). Rather than let
# every early claim attempt bounce off that 400, this script waits up to
# CONSENT_WAIT_STEPS * CONSENT_POLL_INTERVAL (20s) for announcement.json to
# appear — consent-watch.sh writes it within seconds of session start —
# before calling capture-bridge.py claim at all. A session that never gets
# announced (consent-watch down, clip asset missing) is simply never
# claimed on this pass; sitting unclaimed is the safe failure mode, never a
# claim without consent proof.
#
# Backlog guard: session directories older than BACKLOG_AGE_SECONDS are
# skipped unless a prior claim attempt is still pending, so a sleeping Mac
# never wakes up and mass-claims a pile of old sessions in one
# WatchPaths firing.
#
# Env:
#   CARR_RECORDINGS_DIR  — override the recordings root (default: ~/Recordings)
#
# Empty queues succeed. Failed children remain visible after the full sweep.
#
# Never touches vendor/quill, ~/.config/quill, launchd, or git.

set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
TOOL_DIR="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
BRIDGE_PY="$TOOL_DIR/bin/capture-bridge.py"

RECORDINGS_DIR="${CARR_RECORDINGS_DIR:-$HOME/Recordings}"
LOG_FILE="$HOME/Library/Logs/capture-bridge.log"

CONSENT_WAIT_STEPS=40        # 40 * 0.5s = 20s
CONSENT_POLL_INTERVAL=0.5
BACKLOG_AGE_SECONDS=86400    # 24h — never mass-claim an old backlog

log_line() {
    local ts
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    local log_dir
    log_dir="$(dirname "$LOG_FILE")"
    mkdir -p "$log_dir" 2>/dev/null || true
    printf '%s %s\n' "$ts" "$1" >> "$LOG_FILE" 2>/dev/null || true
}

# Session directories are named yyyy.MM.dd-HHmm (quill's own convention,
# also relied on by purge-recordings.sh). BSD date (-j -f) is macOS-only —
# fine, this whole rig is a single-Mac tool. Empty output means the name
# didn't parse, which the caller treats as "not a session dir of ours".
session_epoch() {
    date -j -f '%Y.%m.%d-%H%M' "$1" '+%s' 2>/dev/null
}

wait_for_announcement() {
    # $1 = session dir. Returns 0 once announcement.json exists, 1 if it
    # never showed up inside the wait budget.
    local marker="$1/announcement.json"
    local i=0
    while [ "$i" -lt "$CONSENT_WAIT_STEPS" ]; do
        [ -f "$marker" ] && return 0
        sleep "$CONSENT_POLL_INTERVAL"
        i=$((i + 1))
    done
    [ -f "$marker" ]
}

main() {
    if [ ! -d "$RECORDINGS_DIR" ]; then
        exit 0
    fi

    if [ ! -f "$BRIDGE_PY" ]; then
        log_line "ERROR capture-bridge.py missing at $BRIDGE_PY"
        exit 1
    fi

    local PYTHON_BIN
    PYTHON_BIN="$(command -v python3 2>/dev/null || true)"
    if [ -z "$PYTHON_BIN" ]; then
        log_line "ERROR python3 not found on PATH"
        exit 1
    fi

    local now_epoch
    now_epoch="$(date -u '+%s')"

    local dir scan_rc=0 child_rc
    for dir in "$RECORDINGS_DIR"/*/; do
        [ -d "$dir" ] || continue
        dir="${dir%/}"
        local name
        name="$(basename "$dir")"

        local epoch
        epoch="$(session_epoch "$name")"
        [ -n "$epoch" ] || continue   # not a recognizable session dir

        local age=$((now_epoch - epoch))
        if [ "$age" -gt "$BACKLOG_AGE_SECONDS" ] && [ ! -f "$dir/.capture-request.json" ] && [ ! -f "$dir/.capture-claim-pending" ]; then
            continue
        fi

        [ -f "$dir/.capture.json" ] && continue
        [ -f "$dir/ingested.json" ] && continue

        if ! wait_for_announcement "$dir"; then
            log_line "ERROR claim no-announcement session=$name (waited 20s)"
            scan_rc=1
            continue
        fi

        # A failed prerequisite has no consented request/nonce yet.
        if ! touch "$dir/.capture-claim-pending"; then
            log_line "ERROR claim could not preserve retry session=$name"
            scan_rc=1
            continue
        fi
        "$PYTHON_BIN" "$BRIDGE_PY" claim "$dir"
        child_rc=$?
        if [ "$child_rc" -eq 0 ]; then
            rm -f "$dir/.capture-claim-pending" || child_rc=1
        fi
        if [ "$child_rc" -ne 0 ] && { [ "$scan_rc" -eq 0 ] || [ "$scan_rc" -eq 78 ]; }; then
            scan_rc="$child_rc"
        fi
    done

    exit "$scan_rc"
}

main "$@"
