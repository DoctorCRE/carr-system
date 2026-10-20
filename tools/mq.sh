#!/usr/bin/env bash
# usage: R=owner/repo tools/mq.sh PR...
#        tools/mq.sh --queue owner/repo queue-file log-file
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
exec python3 "$ROOT/tools/merge_queue/main.py" submit "$@"
