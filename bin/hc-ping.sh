#!/bin/zsh
# Report only the backup or export outcome supplied by its caller.
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
    curl -fsS -m 15 --retry 3 "$url" > /dev/null || rc=1
    echo "hc-ping: $label OK -> pinged"
  else
    curl -fsS -m 15 --retry 3 "$url/fail" > /dev/null || rc=1
    echo "hc-ping: $label exited $step_rc -> pinged /fail (alarming now, not after the grace period)"
  fi
}

ping_outcome "exports" "${HC_PING_EXPORTS:-}" "${HC_EXPORTS_RC:-}"
ping_outcome "backup"  "${HC_PING_BACKUP:-}"  "${HC_BACKUP_RC:-}"

exit $rc
