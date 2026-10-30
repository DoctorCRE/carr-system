#!/bin/sh
# Install the tracked ops/launchd templates through config-as-code.
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

for label in "$@"; do
    if [ ! -f "$REPO/ops/launchd/$label.plist" ]; then
        echo "install-agent-via-config.sh: $label is not tracked in ops/launchd/ — refusing to guess" >&2
        exit 1
    fi
done

echo "install-agent-via-config.sh: applying tracked config for: $*"
"$PY" "$CONFIG_AS_CODE" install-launchd "$@" --apply

# Verify, per label, against launchd itself rather than trusting the writer.
status=0
for label in "$@"; do
    if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
        echo "install-agent-via-config.sh: verified $label is loaded"
    else
        # config-as-code deliberately SKIPs labels that do not belong on this
        # machine (shared-state writers on the secondary Mac, unbuilt binaries).
        # That is a correct outcome, not a failure, so say which it is.
        echo "install-agent-via-config.sh: $label is not loaded here — check the SKIP reasons printed above" >&2
        status=1
    fi
done
exit "$status"
