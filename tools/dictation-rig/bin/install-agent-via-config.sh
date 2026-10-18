#!/bin/sh
# install-agent-via-config.sh — the ONE way a dictation-rig installer puts a
# LaunchAgent on a machine.
#
# THE DEFECT THIS FIXES, found 2026-08-25 on Dell's Mac. Three rig installers
# (install-capture-bridge.sh, install-consent.sh, install-dictate.sh) copied
# their plist with a bare `cp`, and every plist under tools/dictation-rig/launchd/
# spells Joe's home literally:
#
#     <string>/Users/booko/carr-system/tools/dictation-rig/bin/capture-poll.sh</string>
#     <string>/Users/booko/Recordings</string>
#
# On Joe's Mac that is correct and nothing looks wrong. On any other machine the
# copy lands pointing at a home directory that does not exist, launchctl
# bootstraps it anyway, and the job is silently dead — `install-capture-bridge.sh:
# bootstrapped and verified com.carr.capture-poll` prints on the way past. That
# is exactly what happened here: running the rig installer replaced two agents
# that config-as-code had installed correctly, and `config-as-code.py check`
# went from OK to "TRACKED BUT DIFFERENT" on both.
#
# WHY DELEGATE INSTEAD OF ADDING A `sed`. Substituting the home path would fix
# the paths and leave the second, larger half of the bug in place: the rig's
# copies are also STALE. ops/launchd/ runs these jobs through
# bin/run-scheduled.sh, which is what records a durable run result; the rig
# copies invoke the script directly and record nothing. capture-poll differs by
# 16 lines and dictation-consent by 25 — the rig copy also drops capture-poll's
# --heartbeat-interval throttle. Fixing only the paths would install a job that
# runs but leaves no receipt, which is harder to notice than one that never runs.
#
# config-as-code.py already owns all seven of these labels, already carries the
# portability layer (portable()/concrete() rewrite the home path per machine),
# and is already the thing both Macs run. So the rig installers hand the job to
# it rather than keeping a second, worse copy of the same mechanism. This is the
# same move bin/sync-settings.sh made when repository config became canonical.
#
# The plists under tools/dictation-rig/launchd/ are kept as the rig's own
# reference copies and are no longer installed from.
#
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
"$PY" "$CONFIG_AS_CODE" install --apply

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
