#!/bin/bash
# Calendar capture entry point.  Normal scheduled operation is EventKit ->
# record-layer touches; the published Outlook ICS feed is a Drive projection and
# is available only for a deliberate, reasoned recovery exercise.
set -u

REPO="${CARR_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
RECOVERY=0
RECOVERY_REASON=""

usage() {
  echo "usage: bin/fetch-calendar.sh [--recovery --reason WHY]" >&2
}

if [ "$#" -eq 0 ]; then
  :
elif [ "$#" -eq 3 ] && [ "$1" = "--recovery" ] && [ "$2" = "--reason" ] && [ -n "$3" ]; then
  RECOVERY=1
  RECOVERY_REASON="$3"
else
  usage
  exit 64
fi

if [ "$RECOVERY" -eq 0 ]; then
  # Do not let an ambient Drive root leak into the canonical child process.
  unset CARR_VAULT
  CAPTURE="$REPO/bin/calendar-eventkit-capture.sh"
  if [ ! -x "$CAPTURE" ]; then
    echo "MISSING_CANONICAL_SEAM: calendar EventKit-to-record capture at $CAPTURE" >&2
    exit 78
  fi
  exec "$CAPTURE"
fi

echo "RECOVERY NONCANONICAL: published Outlook ICS -> Drive; reason=$RECOVERY_REASON" >&2
URL="https://outlook.office365.com/owa/calendar/ed7aa2ebb2c647a8b5556340251a4ce7@carr.us/bbea9ec4a2c64895ad6559029407f6a05027337759467795791/S-1-8-2301065025-2974764972-3930401816-3405722231/reachcalendar.ics"
VAULT="${CARR_VAULT:-$HOME/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI}"
DIR="$VAULT/DNA/Team"
OUT="$DIR/calendar-latest.ics"
LOG="$DIR/calendar-fetch.log"

ICS="$(curl -sS -f -m 60 "$URL")"
rc=$?
if [ "$rc" -eq 0 ] && [ -n "$ICS" ]; then
  printf '%s\n' "$ICS" > "$OUT"
  lines="$(printf '%s' "$ICS" | wc -l | tr -d ' ')"
  printf '%s RECOVERY OK %s lines\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$lines" >> "$LOG"
else
  printf '%s RECOVERY FAIL curl exit %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$rc" >> "$LOG"
  exit "$rc"
fi
