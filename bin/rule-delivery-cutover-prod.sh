#!/bin/zsh
# The sole Production caller for ops.set_rule_delivery_mode.  Dry-run by
# default; --apply verifies the current source map, delivery tags, reviewed
# curation, installed hooks and exact Joe authority before changing policy.
set -eu
REPO="${0:A:h:h}"
APPLY=0
MODE=""
REASON=""
while (( $# )); do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --mode) MODE="$2"; shift 2 ;;
    --reason) REASON="$2"; shift 2 ;;
    *) print -u2 "unknown argument: $1"; exit 2 ;;
  esac
done
[[ "$MODE" == "shadow" || "$MODE" == "enforced" ]] || {
  print -u2 "--mode must be shadow or enforced"; exit 2; }
[[ -n "$REASON" ]] || { print -u2 "--reason is required"; exit 2; }

if (( APPLY )); then
  dirty=$(cd "$REPO" && git status --porcelain -- \
    ops/config/rule-delivery-activation-overlay.v1.json \
    ops/config/rule-enforcement-map.json \
    hooks/rule-pack-drift-gate.py lib/rule_delivery_shadow.py \
    ops/rule-delivery-cutover.py ops/rule-delivery-shadow-eligibility.py \
    ops/rule-delivery-shadow-ledger.py ops/rule-delivery-shadow-watch.py \
    bin/rule-delivery-cutover-prod.sh bin/rule-delivery-shadow-ledger-prod.sh \
    migrations/0317_atomic_rule_delivery_cutover.sql \
    migrations/0452_siep02_rule_delivery_authority.sql)
  [[ -z "$dirty" ]] || { print -u2 "REFUSED: cutover source is uncommitted"; exit 1; }
fi

if [[ -z "${CARR_DB_AUTHORITY_JOE_URL:-}" && -f "$HOME/.config/carr/db.env" ]]; then
  . "$REPO"/bin/routine-credential-env.sh
  carr_require_sourceable_db_env "rule-delivery-cutover-prod" || exit $?
  set -a; . "$HOME/.config/carr/db.env"; set +a
fi
[[ -n "${CARR_DB_AUTHORITY_JOE_URL:-}" ]] || {
  print -u2 "REFUSED: CARR_DB_AUTHORITY_JOE_URL is required"; exit 78; }
args=(--mode "$MODE" --reason "$REASON" --installed-repo "$HOME/carr-system")
(( APPLY )) && args+=(--apply)
DATABASE_URL="$CARR_DB_AUTHORITY_JOE_URL" \
  "$REPO/.venv/bin/python" "$REPO/ops/rule-delivery-cutover.py" "${args[@]}"
