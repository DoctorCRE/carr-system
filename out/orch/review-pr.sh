#!/bin/zsh
# One free-first independent review handoff. The worker publishes its verdict.
set -eu
S=${0:A:h}
R=${S:h:h}
export PATH=/Users/booko/carr-system/out/orch/bin:$PATH
python3 "$R/bin/dot-review.py" --orch "$S" request "$1" "$2"
# A queued review is asynchronous; pr-loop must stop rather than enqueue it repeatedly.
exit 20
