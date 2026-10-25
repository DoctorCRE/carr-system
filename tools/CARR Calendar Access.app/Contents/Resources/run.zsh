#!/bin/zsh
# The bundle's executable. This bundle exists only so the calendar read has a
# BUNDLE IDENTITY, which is what macOS requires before it will show a permission
# prompt at all. A bare python binary has none, so the request returns denied
# WITHOUT prompting and nothing ever appears in Privacy & Security — which is
# exactly what happened, and was misread as a denial, on 2026-08-13.
#
# It logs rather than prints because `open` gives a bundle no terminal.
#   open -a "tools/CARR Calendar Access.app"                -> coverage probe
#   open -a "tools/CARR Calendar Access.app" --args dump    -> attendee dump
REPO="${CARR_REPO:-$HOME/carr-system}"
case "${1:-probe}" in
  dump)  SCRIPT="$REPO/tools/calendar-attendee-dump.py" ;;
  *)     SCRIPT="$REPO/tools/calendar-attendee-probe.py" ;;
esac
OUTPUT_ROOT="${2:-${CARR_CALENDAR_OUTPUT_ROOT:-$REPO/out}}"
LOG="$OUTPUT_ROOT/calendar-access.log"
mkdir -p "$OUTPUT_ROOT"
{
  print -r -- "--- $(date -u +%FT%TZ) CARR Calendar Access (${1:-probe}) ---"
  CARR_CALENDAR_OUTPUT_ROOT="$OUTPUT_ROOT" "$REPO/.venv/bin/python" "$SCRIPT" 2>&1
  print -r -- "exit=$?"
} >> "$LOG"
