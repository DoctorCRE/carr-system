#!/bin/zsh
set -u

EX_USAGE=64

HEARTBEAT_INTERVAL=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --heartbeat-interval)
      if [ "$#" -lt 2 ]; then
        print -ru2 -- "usage: --heartbeat-interval requires a value"
        exit $EX_USAGE
      fi
      HEARTBEAT_INTERVAL="$2"; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done

if [ $# -lt 3 ]; then
  print -ru2 -- "usage: bin/run-scheduled.sh [--heartbeat-interval SECONDS] <service-key> <run-key> <command> [args...]"
  print -ru2 -- "  e.g. bin/run-scheduled.sh nightly-record-layer rules.refresh /bin/zsh {{REPO}}/bin/refresh-rules.sh"
  exit $EX_USAGE
fi

SERVICE="$1"; shift
RUN_KEY="$1"; shift

REPO="${0:A:h:h}"

unset CARR_RUN_SCHEDULED_XPC_SERVICE_NAME
unset CARR_RUN_SCHEDULED_LAUNCHD_PID
unset CARR_RUN_SCHEDULED_FLEET_SELF_CLAIM
wrapper_pid=$$
fleet_self_proof_refused=0
if [ "$SERVICE" = "fleet-sync" ] \
    && [ "${XPC_SERVICE_NAME:-}" = "com.carr.fleet-sync" ]; then
  export CARR_RUN_SCHEDULED_FLEET_SELF_CLAIM=1
fi
fleet_loaded_wrapper=0
if [ "${CARR_RUN_SCHEDULED_FLEET_SELF_CLAIM:-}" = 1 ] \
    && launchctl print "gui/$UID/com.carr.fleet-sync" 2>/dev/null \
       | grep -Eq "^[[:space:]]*pid = ${wrapper_pid}[[:space:]]*$"; then
  fleet_loaded_wrapper=1
fi
fleet_tuple_valid=0
if [ "$RUN_KEY" = "fleet.sync" ] \
    && [ "$#" -eq 2 ] \
    && [ "$1" = "/bin/zsh" ] \
    && [ "${2:A}" = "$REPO/bin/fleet-sync.sh" ]; then
  fleet_tuple_valid=1
fi
if [ "${CARR_RUN_SCHEDULED_FLEET_SELF_CLAIM:-}" = 1 ]; then
  if [ "$fleet_loaded_wrapper" -eq 1 ] && [ "$fleet_tuple_valid" -eq 1 ]; then
    export CARR_RUN_SCHEDULED_XPC_SERVICE_NAME=com.carr.fleet-sync
    export CARR_RUN_SCHEDULED_LAUNCHD_PID=$wrapper_pid
  else
    fleet_self_proof_refused=1
  fi
fi

LOG="$REPO/out/run-scheduled.log"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

STARTED="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

if [ "$fleet_self_proof_refused" -eq 1 ]; then
  print -ru2 -- "run-scheduled: ACTIVE-SELF PROOF REFUSED — launchd names this as"
  print -ru2 -- "    com.carr.fleet-sync, but the loaded wrapper PID and exact"
  print -ru2 -- "    fleet-sync/fleet.sync canonical command tuple do not all agree"
  print -ru2 -- "    remedy: run the sanctioned external config-as-code install to restore"
  print -ru2 -- "    and reload the canonical plist; supplied child was not executed"
  rc=1
else
  "$@"
  rc=$?
fi

ENDED="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

case $rc in
  0)         state=succeeded ;;
  78)        state=skipped   ;;
  124|137)   state=timed_out ;;
  143)       state=cancelled ;;
  *)         state=failed    ;;
esac

fclass=()
case $state in
  failed|timed_out) fclass=(--failure-class "exit_$rc") ;;
esac

corr=()
[ -n "${CARR_CORRELATION_ID:-}" ] && corr=(--correlation "$CARR_CORRELATION_ID")

mkdir -p "$REPO/out" 2>/dev/null

STATE_DIR="${CARR_RUN_SCHEDULED_STATE_DIR:-$REPO/out/run-scheduled-state}"

STATE_FILE="$STATE_DIR/$SERVICE.$RUN_KEY.last-success"
should_record=1
if [ "$state" = "succeeded" ] && [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null; then
  mkdir -p "$STATE_DIR" 2>/dev/null
  if [ -f "$STATE_FILE" ]; then
    last="$(cat "$STATE_FILE" 2>/dev/null || print -r -- 0)"
    now_epoch="$(date -u +%s)"
    elapsed=$(( now_epoch - last ))
    [ "$elapsed" -lt "$HEARTBEAT_INTERVAL" ] && should_record=0
  fi
fi

if [ "$should_record" -eq 1 ]; then
  set -A argv "$PY" "$REPO/tools/ops-spool.py" run \
    --service "$SERVICE" --key "$RUN_KEY" --state "$state" \
    --exit-code "$rc" --started-at "$STARTED" --ended-at "$ENDED" \
    --source-kind wrapper --source-ref bin/run-scheduled.sh \
    --detail "$RUN_KEY exited $rc" "${fclass[@]}" "${corr[@]}"
  "${argv[@]}" >> "$LOG" 2>&1
  recorder_exit=$?
  record_action=recorded
  if [ "$recorder_exit" -eq 0 ] && [ "$state" = "succeeded" ] && [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null; then
    mkdir -p "$STATE_DIR" 2>/dev/null
    date -u +%s > "$STATE_FILE" 2>/dev/null
  fi
else
  argv=()
  recorder_exit=throttled
  record_action=throttled
fi

if [ "$state" != "succeeded" ] && [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null; then
  rm -f "$STATE_FILE" 2>/dev/null
fi

print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ') run-scheduled key=$RUN_KEY service=$SERVICE child_exit=$rc state=$state record_action=$record_action recorder_exit=$recorder_exit argv=${argv[*]}" >> "$LOG"

exit $rc
