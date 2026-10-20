#!/bin/zsh
# install-outlook-triage.sh — install only com.carr.outlook-triage.
#
# A narrow installer like install-notes-sweep.sh: ops/config-as-code.py install
# would also rewrite every unrelated hook and LaunchAgent. The job restarts
# Outlook, so it installs only where the services catalog places it, and it is
# loaded without running: the first fire is the next 19:15.
#
# Usage: ./bin/install-outlook-triage.sh

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.carr.outlook-triage"
SOURCE="$REPO/ops/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
TMP="$(mktemp -t carr-outlook-triage-plist)"
trap 'rm -f "$TMP"' EXIT

if [ ! -f "$SOURCE" ]; then
  print -u2 -- "install-outlook-triage: missing source plist: $SOURCE"
  exit 1
fi
if [ ! -s "$HOME/.config/carr/outlook-nightly-sweep.folders.md" ]; then
  print -u2 -- "install-outlook-triage: missing private folder guide: ~/.config/carr/outlook-nightly-sweep.folders.md"
  exit 1
fi
if ! (cd "$REPO" && "$REPO/.venv/bin/python" -c \
    "import sys; from lib import machine_role; from lib.launchd_scope import allowed_on_machine; sys.exit(0 if allowed_on_machine('$LABEL.plist', machine_role.is_primary('$REPO')) else 1)" \
    2>/dev/null); then
  print -u2 -- "install-outlook-triage: this Mac's role does not run $LABEL (see ops/config/services.json runs_on)"
  exit 1
fi

/usr/bin/sed -e "s|{{REPO}}|$REPO|g" -e "s|{{HOME}}|$HOME|g" "$SOURCE" > "$TMP"
/usr/bin/plutil -lint "$TMP" >/dev/null

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/out"
/usr/bin/install -m 644 "$TMP" "$DEST"

/bin/launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "gui/$(id -u)" "$DEST"
/bin/launchctl print "gui/$(id -u)/$LABEL" >/dev/null

print -- "install-outlook-triage: installed and loaded $LABEL (first fire 19:15)"
print -- "install-outlook-triage: private report: $REPO/out/outlook-triage/"
