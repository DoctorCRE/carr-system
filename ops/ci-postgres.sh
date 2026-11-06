#!/usr/bin/env bash
# Keep pinned consumers stable with one bounded acquisition.
exec timeout --kill-after=5s 240s python3 "$(dirname "$0")/ci-apt.py" --provision-postgres "$@"
