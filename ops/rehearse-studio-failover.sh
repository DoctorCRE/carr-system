#!/bin/bash
set -uo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"${TASK_ROOT}/.venv/bin/python" "${TASK_ROOT}/ops/studio-failover.py" rehearse --target macbook --dry-run
rehearsal_rc=$?
"${TASK_ROOT}/.venv/bin/python" "${TASK_ROOT}/ops/studio-failover-health.py" --reconcile
health_rc=$?
if [ "$rehearsal_rc" -ne 0 ]; then exit "$rehearsal_rc"; fi
exit "$health_rc"
