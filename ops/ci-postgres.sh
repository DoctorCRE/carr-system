#!/usr/bin/env bash
# Keep workflow callers stable; provisioning logic lives in tested Python.
exec python3 "$(dirname "$0")/ci-apt.py" --provision-postgres "$@"
