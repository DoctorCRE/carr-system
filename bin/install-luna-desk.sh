#!/bin/zsh
# Register the queue-only Codex subscription desk for automatic work.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec /usr/bin/env python3 "$REPO/tools/room-bridge/dispatch.py" register luna-desk \
  --kind codex-session \
  --family luna \
  --effort low \
  --sandbox workspace-write \
  --cwd "$REPO"
