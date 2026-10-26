#!/bin/zsh
# Each job records its own outcome. A failure never suppresses a later job.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin"
case "${1:-}" in
  --preflight) /bin/zsh -n "$0"; exit $? ;;
  --exports) ;;
  "") ;;
  *) print -ru2 -- "usage: bin/nightly.sh [--preflight|--exports]"; exit 64 ;;
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
  case "$LAST_STEP_RC" in
    0|78) ;;
    *) [ "$NIGHTLY_RC" -ne 0 ] || NIGHTLY_RC="$LAST_STEP_RC" ;;
  esac
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
  exit "$NIGHTLY_RC"
fi

step "encrypted backup" env CARR_DB_BACKUP_URL="$CARR_DB_BACKUP_URL" ./bin/backup-dump.sh
step "backup ping" env HC_BACKUP_RC="$LAST_STEP_RC" ./bin/hc-ping.sh
step "recovery point" ./.venv/bin/python ops/recovery-point.py
step "deal checklist reminders" ./.venv/bin/python tools/deal-checklist-reminders.py --apply
step "cadence engine" ./.venv/bin/python pipelines/cadence_engine.py --apply
step "availability matcher" ./.venv/bin/python pipelines/availability_matcher.py
step "fetch allowlist" ./.venv/bin/python ops/fetch-allowlist.py
step "corpus push" ./.venv/bin/python tools/corpus-sync.py --push
step "vendor level drift" ./.venv/bin/python ops/vendor-level-drift-check.py
step "system costs" ./run.sh costs --publish --alerts
step "Grok authentication health" ./.venv/bin/python tools/health-check.py --section grok-session
step "health breach response" ./run.sh health --act
step "runtime error intake" node ops/runtime-error-watch.mjs
portability_root="${CARR_EXPORT_HOME:-$HOME/Library/CloudStorage/OneDrive-CARR,Inc/Joe's Folder/CARR AI}"
step "portability mirror" env DATABASE_URL="$CARR_DB_EXPORTER_URL" .venv/bin/python pipelines/doctrine_mirror.py \
  --out "$portability_root/Backups/portability-mirror" --also "$HOME/carr-system/out/mirror"
step "calendar archive" ./bin/archive-calendar.sh
step "mail capture" ./bin/mail-capture.sh
step "lead stages" python3 ./bin/lead-stage-job.py
step "verb probe" ./.venv/bin/python ops/nightly-verb-probe.py
step "nightly ping" env HC_CHAIN_RC="$NIGHTLY_RC" ./bin/hc-ping.sh --nightly
exit "$NIGHTLY_RC"
