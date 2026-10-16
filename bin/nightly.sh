#!/bin/zsh
# Each job records its own outcome. A failure never suppresses a later job.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin"
run_canary() {
  [ "${CARR_CONTROL_PLANE_MODE:-}" = "canary" ] || {
    print -ru2 -- "nightly canary requires explicit control-plane canary mode"; return 64; }
  [ -n "${CARR_NIGHTLY_CANARY_ROOT:-}" ] || {
    print -ru2 -- "nightly canary requires a parent-selected output root"; return 64; }
  local key value
  while IFS='=' read -r key value; do
    case "$key" in
      DATABASE_URL|BACKUP_DATABASE_URL|PG*|CARR_VAULT|CARR_ONEDRIVE_DEALS|CARR_EXPORT_LIVE|CARR_DRIVE_RECOVERY|CARR_ROUTINE_DB_ENV_FILE|CARR_INGEST_URL|CARR_AI_ROUTE_PRIMARY_URL|CARR_AI_ROUTE_SECONDARY_URL|CARR_GMAIL_APP_PASSWORD|CARR_AGE_IDENTITY|CARR_DB_*|*TOKEN*|*SECRET*|*PASSWORD*|*API_KEY*|*PROVIDER_URL*|*EXPORTER_URL*|*BACKUP_URL*)
        print -ru2 -- "nightly canary refused ambient live capability: $key"; return 78 ;;
    esac
  done < <(env)
  local base="$REPO/out/canary/nightly-record-layer" root="$CARR_NIGHTLY_CANARY_ROOT"
  case "$root" in "$base"/*) ;; *) print -ru2 -- "nightly canary output root escapes dedicated canary directory"; return 64 ;; esac
  [[ "$root" != *'/../'* && "$root" != *'/./'* && "$root" != "$base" ]] || {
    print -ru2 -- "nightly canary output root is not a direct run directory"; return 64; }
  local component
  for component in "$REPO/out" "$REPO/out/canary" "$base"; do
    [ ! -L "$component" ] || { print -ru2 -- "nightly canary output root crosses a symlink"; return 64; }
  done
  local py="$REPO/.venv/bin/python"
  [ -x "$py" ] || py=python3
  local snapshot marker
  snapshot="$(cat)"
  [ -n "$snapshot" ] || { print -ru2 -- "nightly canary requires a protected parent snapshot"; return 78; }
  marker="$(print -rn -- "$snapshot" | "$py" "$REPO/pipelines/availability_matcher.py" --canary)" || return $?
  case "$marker" in
    'availability-matcher: canary-result '*) print -r -- "nightly canary result: ${marker#availability-matcher: canary-result }" ;;
    *) print -ru2 -- "nightly canary child emitted no registered aggregate"; return 1 ;;
  esac
}

case "${1:-}" in
  --preflight) /bin/zsh -n "$0"; exit $? ;;
  --canary) run_canary; exit $? ;;
  --exports) ;;
  "") ;;
  *) print -ru2 -- "usage: bin/nightly.sh [--preflight|--canary|--exports]"; exit 64 ;;
esac
unset CARR_VAULT CARR_EXPORT_LIVE CARR_DRIVE_RECOVERY
cd "$REPO" || exit 1
mkdir -p "$REPO/out"
LOG="$REPO/out/nightly.log"
source "$REPO/bin/run-lock.sh"
SERVICE=nightly-record-layer
[ "${1:-}" = --exports ] && SERVICE=nightly-exports-daytime-retry
carr_take_lock "$SERVICE" || exit 0
trap 'carr_release_lock' EXIT
trap 'exit 143' INT TERM HUP
source "$REPO/bin/routine-credential-env.sh"
carr_clear_routine_db_env
carr_load_routine_db_env CARR_DB_JOBS_URL CARR_DB_EXPORTER_URL CARR_DB_BACKUP_URL || exit $?
CARR_DB_BACKUP_URL="${CARR_DB_BACKUP_URL:-}"
CARR_DB_EXPORTER_URL="${CARR_DB_EXPORTER_URL:-}"
source "$REPO/bin/step-timeout.zsh"

LAST_STEP_RC=0
NIGHTLY_RC=0
step() {
  local label="$1"; shift
  local key="nightly.$(print -r -- "${label%% \(*}" | tr 'A-Z ' 'a-z-' | tr -cd 'a-z0-9.-')"
  carr_step_timeout_prefix "$(carr_step_timeout_for "$label")"
  carr_routine_exec /bin/zsh "$REPO/bin/run-scheduled.sh" "$SERVICE" "$key" \
    "${CARR_STEP_TIMEOUT_ARGV[@]}" "$@" >> "$LOG" 2>&1
  LAST_STEP_RC=$?
  [ "$LAST_STEP_RC" -eq 0 ] || NIGHTLY_RC="$LAST_STEP_RC"
  return 0
}

if [ "${1:-}" = --exports ]; then
  step "onedrive pre-publish wake" ./.venv/bin/python ops/onedrive-prepublish-wake.py
  step "exports" caffeinate -i -s env CARR_EXPORT_LIVE=1 ./run.sh export
  step "export ping" env HC_EXPORTS_RC="$LAST_STEP_RC" ./bin/hc-ping.sh
  step "consumers" ./run.sh all
  step "lead promote" ./run.sh lead-promote
  step "graph" ./run.sh graph
  step "section index" ./run.sh section-index
  step "system graph" ./run.sh graph-system
  exit 0
fi

step "encrypted backup" env CARR_DB_BACKUP_URL="$CARR_DB_BACKUP_URL" ./bin/backup-dump.sh
step "backup ping" env HC_BACKUP_RC="$LAST_STEP_RC" ./bin/hc-ping.sh
step "recovery point" ./.venv/bin/python ops/recovery-point.py
step "credential mint" ./.venv/bin/python ops/credential-mint.py
step "credential health" ./.venv/bin/python ops/credential-health.py --nightly
step "cadence engine" ./.venv/bin/python pipelines/cadence_engine.py --apply
step "availability matcher" ./.venv/bin/python pipelines/availability_matcher.py
step "fetch allowlist" ./.venv/bin/python ops/fetch-allowlist.py
step "corpus push" ./.venv/bin/python tools/corpus-sync.py --push
step "vendor level drift" ./.venv/bin/python ops/vendor-level-drift-check.py
step "system costs" ./run.sh costs --publish --alerts
step "Claude continuity spool drain" ./.venv/bin/python tools/health-check.py --section claude-continuity-spool
step "Grok authentication health" ./.venv/bin/python tools/health-check.py --section grok-session
step "health breach response" ./run.sh health --act
step "defect bridge" ./.venv/bin/python ops/defect-bridge.py
portability_root="${CARR_EXPORT_HOME:-$HOME/Library/CloudStorage/OneDrive-CARR,Inc/Joe's Folder/CARR AI}"
step "portability mirror" env DATABASE_URL="$CARR_DB_EXPORTER_URL" .venv/bin/python pipelines/doctrine_mirror.py \
  --out "$portability_root/Backups/portability-mirror" --also "$HOME/carr-system/out/mirror"
step "calendar archive" ./bin/archive-calendar.sh
step "mail capture" ./bin/mail-capture.sh
step "lead stages" python3 ./bin/lead-stage-job.py
step "verb probe" ./.venv/bin/python ops/nightly-verb-probe.py
step "pr board" ./run.sh pr-board
step "nightly ping" env HC_CHAIN_RC="$NIGHTLY_RC" ./bin/hc-ping.sh --nightly
exit 0
