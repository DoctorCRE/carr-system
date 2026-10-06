#!/bin/zsh
# cutover-watch.sh — the launchd-run half of the control-plane cutover watch.
#
# WHY THIS EXISTS. Session-scheduled wakeups only fire while a Claude
# conversation is alive and idle. The control plane worked all day 2026-08-27
# while nothing reported it; Joe had to ask twice. Rule 1f3a7372: an
# unattended run logs its findings to the record before it ends. Rule
# 847f9995: no unattended path may depend on an interactive credential. This
# closes that gap: launchd wakes it on a fixed schedule (no live session
# required), it loads only the narrow jobs-ledger credential (never db.env,
# never an authority credential), and it reports through the same record and
# notification paths the rest of this Mac's local agents already use.
#
# WHAT THIS DOES. Delegates the actual ledger query and record write to
# tools/cutover-watch.py, which compares the current control-plane state —
# completed jobs whose receipts are not yet accepted, jobs newly
# dead-lettered, and the acceptance table — against a sentinel of what it
# last reported (out/cutover-watch/last-report.json). A quiet run (nothing
# changed) stays silent and exits 0. A real change writes ONE loop update to
# open loop #532 (through the same run.sh call path every other bin/ script
# uses to reach a verb — see tools/cutover-watch.py's own header) and
# notifies Joe locally the SAME way bin/cc-version-sentinel.sh does: an
# osascript display notification, not a second mechanism invented here.
#
# WHAT THIS NEVER DOES. It never accepts, promotes, disables, or dispatches
# anything. Acceptance is a directed authority act and stays in an attended
# session; this agent only observes and reports.
#
# RISK COLOR: YELLOW. Read-only ledger queries under the narrow jobs
# credential (never an authority DSN); on a genuine change it makes exactly
# one write call — update-loop on #532, nothing wider — and raises one local
# notification. No verb here accepts, promotes, disables, or dispatches
# anything.
#
# Usage: ./bin/cutover-watch.sh [--dry-run]

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${CARR_CUTOVER_WATCH_STATE_DIR:-$REPO/out/cutover-watch}"
SENTINEL="$STATE_DIR/last-report.json"
LOG="${CARR_CUTOVER_WATCH_LOG:-$REPO/out/cutover-watch.log}"
NOTIFY_COMMAND="${CARR_CUTOVER_WATCH_NOTIFY_COMMAND:-/usr/bin/osascript}"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

mkdir -p "$REPO/out" "$STATE_DIR"
say() { print -r -- "$(date -u +%FT%TZ) cutover-watch $*" >> "$LOG" }

# THE NARROW LOADER, NOT db.env. carr_load_routine_db_env is safe to source —
# it never evaluates ~/.config/carr/db.env — and reads only the one key named
# below. Nothing else in that file, if it even exists, ever reaches this
# process's environment.
source "$REPO/bin/routine-credential-env.sh"
carr_clear_routine_db_env
if ! carr_load_routine_db_env CARR_DB_JOBS_URL; then
  rc=$?
  say "FAIL routine credential load failed (exit $rc)"
  exit 78
fi
if [ -z "${CARR_DB_JOBS_URL:-}" ]; then
  say "SKIP CARR_DB_JOBS_URL not provisioned — nothing to watch the ledger with"
  exit 78
fi

PYTHON="$REPO/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  say "FAIL repo venv missing at $PYTHON"
  exit 78
fi

CHECKER="$REPO/tools/cutover-watch.py"
if [ ! -f "$CHECKER" ]; then
  say "FAIL missing $CHECKER"
  exit 78
fi

# A HELPER FAILURE MUST SURFACE AS A VISIBLE FAIL, NOT AS `set -eu` SILENTLY
# ABORTING THE SCRIPT MID-RUN: this file is invoked via bin/run-scheduled.sh
# specifically so its exit code is durably recorded, and an abort with no
# say() line first would defeat that. Capturing via `||` keeps `set -eu` from
# firing before the say/exit pair runs.
#
# Only CARR_DB_JOBS_URL crosses into the child; every other credential stays
# out, including anything ambient in this shell's own environment.
# `&& rc=0 || rc=$?`, not a bare assignment: under `set -e` a failing command
# substitution used as a plain assignment aborts the script right there,
# before the exit code can be inspected — the same trap cc-version-sentinel.sh
# guards against on its own helper call. This form always reaches the case
# statement below with the real exit code in $rc.
OUTPUT="$(env -i HOME="$HOME" PATH="$PATH" LANG="${LANG:-C}" TMPDIR="${TMPDIR:-/tmp}" \
  CARR_DB_JOBS_URL="$CARR_DB_JOBS_URL" \
  "$PYTHON" "$CHECKER" --sentinel "$SENTINEL" 2>&1)" && rc=0 || rc=$?

# Print the finding to the process's own stdout (what run-scheduled.sh /
# launchd routes to out/cutover-watch.out) — "print the finding" is the rule
# on an unreachable record layer, and a command-substitution capture alone
# would otherwise swallow it.
print -r -- "$OUTPUT"
print -r -- "$OUTPUT" | while IFS= read -r line; do
  say "$line"
done

FIRST_LINE="${OUTPUT%%$'\n'*}"

case "$rc" in
  0)
    if [[ "$FIRST_LINE" == "STATUS: CHANGE"* ]]; then
      MSG="${FIRST_LINE#STATUS: CHANGE }"
      # AppleScript string literal: escape backslash first, then the quote,
      # so a receipt_ref or workflow key that happens to carry either cannot
      # break out of the display-notification string.
      MSG="${MSG//\\/\\\\}"
      MSG="${MSG//\"/\\\"}"
      if [ "$DRY" -eq 1 ]; then
        print -r -- "would notify: $MSG"
      else
        "$NOTIFY_COMMAND" -e "display notification \"$MSG\" with title \"Cutover watch\" subtitle \"loop #532 updated\"" \
          >/dev/null 2>&1 || say "WARN notification failed ($NOTIFY_COMMAND)"
      fi
      say "OK change reported and Joe notified"
    else
      say "OK no change"
    fi
    exit 0
    ;;
  78)
    say "SKIP config problem reported by tools/cutover-watch.py"
    exit 78
    ;;
  *)
    say "FAIL tools/cutover-watch.py reported a real error (exit $rc)"
    exit 1
    ;;
esac
