#!/bin/zsh
# Register the queue-only Claude background -> Desktop delivery target.

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DESK_CWD="${CARR_CLAUDE_DESKTOP_CWD:-$REPO}"

exec /usr/bin/env python3 "$REPO/tools/room-bridge/desk_cli.py" register claude-desktop \
  --kind claude-desktop \
  --model opus \
  --effort high \
  --permission-mode dontAsk \
  --cwd "$DESK_CWD" \
  --seat claude \
  --profile reviewer
