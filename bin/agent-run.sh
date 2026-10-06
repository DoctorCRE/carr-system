#!/bin/bash
# Keep command argv intact; the Python runner supplies /dev/null to every child.
set -eu
AGENT_RUN_REPO=$(cd "$(dirname "$0")/.." && pwd)
exec "${CARR_JOB_PYTHON:-python3}" "$AGENT_RUN_REPO/tools/job-watchdog.py" run "$@" </dev/null
