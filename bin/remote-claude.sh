#!/bin/bash
# Enqueue stdin for a registered Model Room desk and return its durable queued receipt.
set -euo pipefail

if [ $# -ne 1 ] || [[ "$1" == -* ]]; then
  echo "usage: bin/remote-claude.sh DESK < prompt" >&2
  exit 2
fi
repo=$(cd "$(dirname "$0")/.." && pwd)
exec python3 "$repo/tools/room-bridge/dispatch.py" send "$1" -
