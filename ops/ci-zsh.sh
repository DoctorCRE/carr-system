#!/usr/bin/env bash
# Install the CI shell through the runner or authenticated package archives.
set -euo pipefail
exec python3 "$(dirname "$0")/ci_zsh.py"
