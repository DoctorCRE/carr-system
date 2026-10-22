#!/bin/bash
# Keep command argv intact; the Python runner supplies /dev/null to every child.
set -eu
AGENT_RUN_REPO=$(cd "$(dirname "$0")/.." && pwd)
exec "${CARR_JOB_PYTHON:-python3}" "$AGENT_RUN_REPO/tools/agent-run.py" "$@" </dev/null
