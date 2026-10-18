#!/bin/zsh
# Report per-job outcomes, or the final nightly chain and Worker health.
ENV_FILE="$HOME/.config/carr/healthchecks.env"
[ -f "$ENV_FILE" ] || { echo "hc-ping: $ENV_FILE not found — Healthchecks not configured yet"; exit 78; }
source "$ENV_FILE"

rc=0

ping_outcome() {
  local label="$1" url="$2" step_rc="$3"
  if [ -z "$step_rc" ]; then
    echo "hc-ping: $label outcome not supplied — NOT pinging. The check will go"
    echo "         late on its own, which is the honest signal when nobody knows."
    return 0
  fi
  if [ -z "$url" ]; then
    echo "hc-ping: $label URL is not configured"
    rc=78
    return 0
  fi
  if [ "$step_rc" -eq 0 ]; then
    if curl -fsS -m 15 --retry 3 "$url" > /dev/null; then
      echo "hc-ping: $label OK -> pinged"
    else
      echo "hc-ping: $label ping delivery failed"
      rc=1
    fi
  else
    if curl -fsS -m 15 --retry 3 "$url/fail" > /dev/null; then
      echo "hc-ping: $label exited $step_rc -> pinged /fail (alarming now, not after the grace period)"
    else
      echo "hc-ping: $label failure ping delivery failed"
      rc=1
    fi
  fi
}

case "${1:-}" in
  --nightly)
    ping_outcome "chain" "${HC_PING_CHAIN:-}" "${HC_CHAIN_RC:-}"
    setopt pipefail
    mcp_rc=1
    if curl -fsS -m 15 https://api.doctorcre.com/health |
        python3 -c 'import json, sys; sys.exit(json.load(sys.stdin).get("ok") is not True)' 2>/dev/null; then
      mcp_rc=0
    fi
    ping_outcome "MCP" "${HC_PING_MCP:-}" "$mcp_rc"
    ;;
  "")
    ping_outcome "exports" "${HC_PING_EXPORTS:-}" "${HC_EXPORTS_RC:-}"
    ping_outcome "backup" "${HC_PING_BACKUP:-}" "${HC_BACKUP_RC:-}"
    ;;
  *) echo "usage: bin/hc-ping.sh [--nightly]" >&2; exit 64 ;;
esac

exit $rc
