#!/bin/sh
# Install the loopback-only Deal Room companion for Quill meeting mode.
#
# Risk color RED, human initiated: a click starts a client-visible recording
# announcement and capture. Nothing starts on a timer or without that click.
# The LaunchAgent only keeps the idle local control bridge available.
set -eu

TOOL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$TOOL_DIR/../.." && pwd)"
LABEL="com.carr.call-mode"
SOURCE="$REPO/ops/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
TMP="$(mktemp -t carr-call-mode-plist)"
trap 'rm -f "$TMP"' EXIT

[ -f "$SOURCE" ] || { echo "missing $SOURCE" >&2; exit 1; }
[ -x "$TOOL_DIR/bin/call-mode.py" ] || chmod +x "$TOOL_DIR/bin/call-mode.py"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed -e "s|{{REPO}}|$REPO|g" -e "s|{{HOME}}|$HOME|g" "$SOURCE" > "$TMP"
cp "$TMP" "$DEST"

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
launchctl print "gui/$(id -u)/$LABEL" >/dev/null

tries=0
while [ "$tries" -lt 20 ]; do
    if /usr/bin/curl -fsS --max-time 1 http://127.0.0.1:4682/api/state >/dev/null 2>&1; then
        echo "Call Mode ready at http://127.0.0.1:4682"
        exit 0
    fi
    tries=$((tries + 1))
    sleep 0.2
done

echo "Call Mode agent loaded but the health check did not answer; see $HOME/Library/Logs/carr-call-mode.log" >&2
exit 1
