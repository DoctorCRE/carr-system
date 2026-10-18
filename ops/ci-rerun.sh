#!/bin/sh
# Sanctioned job-only CI retry; admission lives in the Python entrypoint.
set -eu
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 "$TASK_ROOT/ops/ci-rerun.py" "$@"
