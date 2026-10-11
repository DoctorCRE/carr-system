#!/bin/zsh
set -eu
S=${0:A:h}
R=${S:h:h}
export CARR_JEV_WORKER=off
exec python3 "$R/bin/pr-review-loop.py" "$@"
