#!/bin/sh
# Install only the requested labels through the canonical renderer in ops/.
# Rig copies used machine-specific paths and omitted the durable receipt wrapper.
# Usage: install-agent-via-config.sh <label> [<label>...]
set -eu

TOOL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$TOOL_DIR/../.." && pwd)"
CONFIG_AS_CODE="$REPO/ops/config-as-code.py"
VENV_PY="$REPO/.venv/bin/python"

[ "$#" -ge 1 ] || { echo "install-agent-via-config.sh: need at least one label" >&2; exit 2; }

if [ ! -f "$CONFIG_AS_CODE" ]; then
    echo "install-agent-via-config.sh: $CONFIG_AS_CODE not found — is this the canonical checkout?" >&2
    exit 1
fi

if [ -x "$VENV_PY" ]; then
    PY="$VENV_PY"
elif command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
else
    echo "install-agent-via-config.sh: no python3 available to run config-as-code" >&2
    exit 1
fi

echo "install-agent-via-config.sh: applying tracked config for: $*"
exec "$PY" "$CONFIG_AS_CODE" install-launchd --apply "$@"
