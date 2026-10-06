#!/bin/bash
# install-consent.sh — installs the CARR dictation-rig consent-announcement
# LaunchAgent.
#
# This script is what the orchestrator runs under supervision, after this
# tool has merged into the canonical checkout at the durable path the plist
# references. It is NOT run as part of the build pass that produced it —
# building the files and installing them are separate, deliberately gated
# steps.
#
# What it does:
#   1. Warns if consent-watch.sh is not in the checkout yet.
#   2. Hands the LaunchAgent to config-as-code, which owns this label, carries
#      the per-machine home-path rewrite, and wraps the job in
#      bin/run-scheduled.sh so a failure leaves a durable run result.
#
# Safe to re-run: config-as-code is idempotent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
TOOL_DIR="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
REPO="$(cd "$TOOL_DIR/../.." >/dev/null 2>&1 && pwd)"

LABEL="com.carr.dictation-consent"
CONSENT_SCRIPT="$REPO/tools/dictation-rig/bin/consent-watch.sh"

if [ ! -f "$CONSENT_SCRIPT" ]; then
    echo "install-consent.sh: WARNING — $CONSENT_SCRIPT does not exist yet." >&2
    echo "If this tool has not merged into the canonical checkout yet," >&2
    echo "launchd will fail to run the job even though bootstrap succeeds." >&2
    echo "Merge first, then install." >&2
fi

# Canonical plists provide portable paths and the durable receipt wrapper.
sh "$TOOL_DIR/bin/install-agent-via-config.sh" "$LABEL"
