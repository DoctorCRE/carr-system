#!/bin/zsh
# doctrine: tailscale-start-at-login
set -eu
CARR_TS_REPO="${0:A:h:h}"
CARR_TS_PY="$CARR_TS_REPO/.venv/bin/python"
[[ -x "$CARR_TS_PY" ]] || CARR_TS_PY=python3
exec "$CARR_TS_PY" "$CARR_TS_REPO/ops/tailscale_health.py" --recover
