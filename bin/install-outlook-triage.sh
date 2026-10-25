#!/bin/zsh
# install-outlook-triage.sh — install only com.carr.outlook-triage.
#
# A narrow installer like install-notes-sweep.sh: ops/config-as-code.py install
# would also rewrite every unrelated hook and LaunchAgent. The job restarts
# Outlook, so it requires local settings to enable triage, and it never
# loads the job: it prints the bootstrap command for the operator to run. The
# launchd log is created 0600 first so launchd never creates it world-readable.
#
# Usage: ./bin/install-outlook-triage.sh

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY="$REPO/.venv/bin/python"
"$PY" -m lib.outlook_settings service triage
GUIDE="$("$PY" -m lib.outlook_settings field folder_guide)"
LABEL="com.carr.outlook-triage"
SOURCE="$REPO/ops/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
TMP="$(mktemp -t carr-outlook-triage-plist)"
trap 'rm -f "$TMP"' EXIT

if [ ! -f "$SOURCE" ]; then
  print -u2 -- "install-outlook-triage: missing source plist: $SOURCE"
  exit 1
fi
if [ ! -s "$GUIDE" ]; then
  print -u2 -- "install-outlook-triage: missing private folder guide: $GUIDE"
  exit 1
fi
if [ "$(/usr/bin/stat -f %Lp "$GUIDE")" != 600 ]; then
  print -u2 -- "install-outlook-triage: the folder guide names clients, so it must be mode 0600 (chmod 600 it); the job refuses it otherwise"
  exit 1
fi

"$PY" -m lib.outlook_settings render "$SOURCE" > "$TMP"
/usr/bin/plutil -lint "$TMP" >/dev/null

LOG="$REPO/out/outlook-triage-launchd.log"
mkdir -p "$HOME/Library/LaunchAgents" "$REPO/out"
[ -e "$LOG" ] || /usr/bin/install -m 600 /dev/null "$LOG"
/bin/chmod 600 "$LOG"
/usr/bin/install -m 644 "$TMP" "$DEST"

print -- "install-outlook-triage: installed $DEST; NOT loaded"
print -- "install-outlook-triage: to load it (first fire at the next 19:15), run:"
print -- "  /bin/launchctl bootstrap gui/$(id -u) $DEST"
print -- "install-outlook-triage: private report: $REPO/out/outlook-triage/"
