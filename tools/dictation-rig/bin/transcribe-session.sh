#!/bin/sh
# transcribe-session.sh — quill's on_stop hook entry point for the CARR
# dictation rig (Phase A, spec: specs/dictation-rig-build-plan-2026-08-07.md).
#
# Quill invokes this as:
#   /bin/sh -c '<cmd> "$0"' <session_dir>
# which, once sh substitutes its own positional parameters, runs this script
# as: transcribe-session.sh <session_dir> — so the session directory arrives
# as our own $1, exactly like any normal shell invocation.
#
# This script does the thinnest possible amount of work itself: resolve where
# it lives (so it works regardless of which checkout/branch quill was pointed
# at — the dictation-rig worktree during build, or the durable main-repo path
# once merged), activate nothing global (no venv, no shell profile —
# transcribe_session.py is pure stdlib Python 3 on purpose, so plain python3
# is always enough), and hand off to transcribe_session.py. It logs a START
# line before the handoff and a FINISH/ERROR line after, so a hang or a crash
# inside the Python step is never silent — even if transcribe_session.py
# itself never gets far enough to write to the log on its own.
#
# Never loads vendor/quill/ source, never touches ~/.config/quill, never
# touches launchd — this script is invoked BY quill, it does not configure it.
#
# WO-4 capture bridge status pings (added 2026-08-08): "transcribing" right
# before the python step, "failed" with a short detail on its non-zero
# exit. A successful weekly-call distillation hands sanitized proposals to the
# bridge's publish command; it reports "distilling" and leaves every proposal
# pending for Joe or Dell. Routed through capture-bridge.py, which is a clean no-op
# (exit 0, one log line) whenever the rig isn't provisioned or the worker
# is unreachable — these calls can never change this script's own exit code.

set -u

SESSION_DIR="${1:-}"

# Resolve our own location from $0 (our real argv[0] when this process was
# exec'd — not to be confused with quill's use of $0 in ITS sh -c wrapper,
# which is a different shell one level up). Requires being invoked with an
# absolute path, which is how quill's on_stop hook is configured.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
TOOL_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)
PY_ENTRY="$TOOL_DIR/bin/transcribe_session.py"
BRIDGE_ENTRY="$TOOL_DIR/bin/capture-bridge.py"

log_line() {
    # $1 = message. Falls back to stderr if the session dir is unusable, so
    # this never crashes silently even when there is nowhere to log to.
    ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    if [ -n "$SESSION_DIR" ] && [ -d "$SESSION_DIR" ]; then
        printf '%s %s\n' "$ts" "$1" >> "$SESSION_DIR/transcribe.log" 2>/dev/null \
            || printf '%s %s\n' "$ts" "$1" >&2
    else
        printf '%s %s\n' "$ts" "$1" >&2
    fi
}

if [ -z "$SESSION_DIR" ]; then
    log_line "ERROR transcribe-session.sh: no session directory argument"
    exit 2
fi

if [ ! -d "$SESSION_DIR" ]; then
    log_line "ERROR transcribe-session.sh: session directory does not exist: $SESSION_DIR"
    exit 2
fi

log_line "START transcribe-session.sh session_dir=$SESSION_DIR entry=$PY_ENTRY"

if [ ! -f "$PY_ENTRY" ]; then
    log_line "ERROR transcribe-session.sh: transcribe_session.py not found at $PY_ENTRY"
    exit 3
fi

PYTHON_BIN=$(command -v python3 2>/dev/null || true)
if [ -z "$PYTHON_BIN" ]; then
    log_line "ERROR transcribe-session.sh: python3 not found on PATH"
    exit 4
fi

# Best-effort status ping; see header note. Backgrounded onto capture-bridge.py's
# own no-op/never-break contract, so a missing file or a down worker here is
# silent and harmless — checked defensively anyway before spending a python3
# invocation on it.
if [ -f "$BRIDGE_ENTRY" ]; then
    "$PYTHON_BIN" "$BRIDGE_ENTRY" status "$SESSION_DIR" transcribing >/dev/null 2>&1 || true
fi

# No exec here on purpose: we want to log FINISH/ERROR after the Python step
# returns, which a process replacement would make impossible.
"$PYTHON_BIN" "$SCRIPT_DIR/../../../lib/call_settings.py" "$PYTHON_BIN" "$PY_ENTRY" "$SESSION_DIR"
rc=$?

if [ "$rc" -eq 0 ]; then
    if [ -f "$BRIDGE_ENTRY" ]; then
        "$PYTHON_BIN" "$BRIDGE_ENTRY" publish "$SESSION_DIR" >/dev/null 2>&1 || true
    fi
    log_line "FINISH transcribe-session.sh session_dir=$SESSION_DIR"
else
    log_line "ERROR transcribe-session.sh: transcribe_session.py exited rc=$rc session_dir=$SESSION_DIR"
    if [ -f "$BRIDGE_ENTRY" ]; then
        "$PYTHON_BIN" "$BRIDGE_ENTRY" status "$SESSION_DIR" failed "transcribe_session.py rc=$rc" >/dev/null 2>&1 || true
    fi
fi

exit "$rc"
