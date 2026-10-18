#!/bin/bash
# Dispatch stdin to a registered Model Room desk, with the ordinary receipt.
set -euo pipefail

if [ $# -ne 1 ] || [[ "$1" == -* ]]; then
  echo "usage: bin/remote-claude.sh DESK < prompt" >&2
  exit 2
fi
repo=$(cd "$(dirname "$0")/.." && pwd)
exec python3 "$repo/tools/room-bridge/dispatch.py" send "$1" -
