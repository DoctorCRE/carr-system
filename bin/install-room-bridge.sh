#!/bin/zsh
# install-room-bridge.sh — install the partner-room bridge's launchd agent.
#
# Deliberately narrow, same shape as bin/install-notes-sweep.sh. This installs
# only com.carr.room-bridge, then proves launchd accepted it. It never touches
# any other hook or LaunchAgent — for that, ops/config-as-code.py is the
# broad reconciler and this script is not it.
#
# Usage: ./bin/install-room-bridge.sh [--render-only]

set -eu

RENDER_ONLY=0
if [ "${1:-}" = "--render-only" ]; then
  [ "$#" -eq 1 ] || { print -u2 -- 'install-room-bridge: --render-only takes no arguments'; exit 64; }
  RENDER_ONLY=1
elif [ "$#" -ne 0 ]; then
  print -u2 -- 'usage: install-room-bridge.sh [--render-only]'
  exit 64
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.carr.room-bridge"
SOURCE="$REPO/ops/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
TMP="$(mktemp "${TMPDIR:-/tmp}/carr-room-bridge-plist.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

if [ ! -f "$SOURCE" ]; then
  print -u2 -- "install-room-bridge: missing source plist: $SOURCE"
  exit 1
fi

# The repo template stays portable; only the machine copy contains its actual
# checkout and home paths. plutil validates before anything touches
# LaunchAgents. Escape sed replacement metacharacters so a portable path with
# an ampersand, backslash, or delimiter cannot corrupt the rendered plist.
escape_sed_replacement() {
  print -r -- "$1" | /usr/bin/sed 's/[\\&|]/\\&/g'
}

# macOS ships plutil at /usr/bin; hosted Linux runners do not. Keep one named
# validation seam so both environments reject malformed XML before any
# LaunchAgents directory or launchd state is touched. The override exists only
# for hermetic coverage of the portable fallback; production defaults to auto.
validate_plist() {
  local plist_path="$1"
  local validator="${CARR_ROOM_BRIDGE_PLIST_VALIDATOR-auto}"
  if [ "$validator" = "python" ] || {
       [ "$validator" = "auto" ] && [ ! -x /usr/bin/plutil ]
     }; then
    local python_bin
    python_bin="$(command -v python3 || true)"
    [ -n "$python_bin" ] || return 1
    "$python_bin" - "$plist_path" >/dev/null <<'PY'
import plistlib
import sys

try:
    with open(sys.argv[1], "rb") as handle:
        plistlib.load(handle)
except Exception:
    raise SystemExit(1)
PY
    return $?
  fi
  [ "$validator" = "auto" ] || return 1
  /usr/bin/plutil -lint "$plist_path" >/dev/null 2>&1
}

REPO_REPLACEMENT="$(escape_sed_replacement "$REPO")"
HOME_REPLACEMENT="$(escape_sed_replacement "$HOME")"
/usr/bin/sed -e "s|{{REPO}}|$REPO_REPLACEMENT|g" \
            -e "s|{{HOME}}|$HOME_REPLACEMENT|g" "$SOURCE" > "$TMP"

# Every template token must be resolved before the destination is touched.
# This is deliberately a refusal, not a best-effort install: launchd accepting
# a plist that still names {{REPO}} or {{HOME}} would strand the bridge on the
# next scheduled wake with no useful diagnostic at the call site.
if /usr/bin/grep -Eq '\{\{[^}]+\}\}' "$TMP"; then
  print -u2 -- "install-room-bridge: rendered plist still contains an unresolved template token"
  exit 1
fi
if ! validate_plist "$TMP"; then
  print -u2 -- "install-room-bridge: rendered plist failed validation; refusing installation"
  exit 1
fi

# The selftest uses this render-only seam with fixture HOME values. It writes
# only to stdout and exits before creating LaunchAgents or loading launchd.
if [ "$RENDER_ONLY" -eq 1 ]; then
  /bin/cat "$TMP"
  exit 0
fi

# The bridge's reviewed activation path owns the one-time bootstrap of its
# dedicated Engineering Passport desk.  This is intentionally here—not in a
# normal 60-second bridge cycle—so an operator can inspect the exact desk
# readback before the LaunchAgent gets a chance to seek admitted work.  A
# refusal leaves the existing LaunchAgent untouched.
if ! "$REPO/bin/install-engineering-codex-desk.sh"; then
  print -u2 -- "install-room-bridge: Engineering Codex desk bootstrap refused; LaunchAgent unchanged"
  exit 1
fi
if ! "$REPO/bin/install-claude-desktop-desk.sh"; then
  print -u2 -- "install-room-bridge: Claude Desktop desk bootstrap refused; LaunchAgent unchanged"
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/out"
/usr/bin/install -m 644 "$TMP" "$DEST"

# bootout is intentionally tolerant: a first install has nothing to unload.
/bin/launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "gui/$(id -u)" "$DEST"
/bin/launchctl print "gui/$(id -u)/$LABEL" >/dev/null

print -- "install-room-bridge: installed and loaded $LABEL"
print -- "install-room-bridge: durable log: $REPO/out/room-bridge-launchd.log"
print -- "install-room-bridge: state file: $HOME/.config/carr/room-bridge-state.json"
print -- "install-room-bridge: stop it with: launchctl bootout gui/\$(id -u)/$LABEL"
