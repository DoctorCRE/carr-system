#!/bin/zsh
# Inspect admission and delivery audits, export classifications, or install delivery tags.
# --apply installs reviewed delivery tags and reads both audits back.
# --export refreshes the admission-classification snapshot without a backfill.

set -eu
REPO="${0:A:h:h}"
LOG="$REPO/out/sync-rule-admission-prod.log"
mkdir -p "$REPO/out"

APPLY=0
EXPORT=0
while (( $# )); do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --export) EXPORT=1; shift ;;
    *) print -u2 "unknown argument: $1"; exit 2 ;;
  esac
done
if (( APPLY )) && (( EXPORT )); then
  print -u2 "REFUSED: pass --apply or --export, not both — run --apply first, then --export separately so the export reflects what just landed"
  exit 2
fi

stamp() { print -r -- "$(date -u +%FT%TZ) sync-rule-admission-prod $*" >> "$LOG" }

MAP="ops/config/rule-enforcement-map.json"

if (( APPLY )); then
  dirty=$(cd "$REPO" && git status --porcelain -- "$MAP")
  if [[ -n "$dirty" ]]; then
    stamp "REFUSED uncommitted map: ${dirty//$'\n'/ · }"
    print -u2 "REFUSED: $MAP is uncommitted — commit the reviewed map first."
    print -u2 "$dirty"
    exit 1
  fi
fi

# Same non-interactive auth and same project pin as the GitHub production deployment workflow.
if [ -z "${NEON_API_KEY:-}" ] && [ -f "$HOME/.config/carr/db.env" ]; then
  . "$REPO"/bin/routine-credential-env.sh
  carr_require_sourceable_db_env "sync-rule-admission-prod" || exit $?
  set -a; . "$HOME/.config/carr/db.env"; set +a
fi
if [ -z "${NEON_API_KEY:-}" ]; then
  stamp "WARN no NEON_API_KEY — falling back to interactive neonctl auth"
  print -u2 "note: NEON_API_KEY is not set, so this needs a browser login if the"
  print -u2 "      saved neonctl session has expired."
fi

NEON_PROJECT_PRODUCTION="steep-field-48688294"
NEONCTL="$REPO/mcp-server/node_modules/.bin/neonctl"
[[ -x "$NEONCTL" ]] || NEONCTL="neonctl"

DSN="$("$NEONCTL" connection-string production \
        --project-id "$NEON_PROJECT_PRODUCTION" \
        --role-name neondb_owner 2>/tmp/sync-rule-admission-neonctl.err)"
if [[ -z "$DSN" ]]; then
  reason="$(head -1 /tmp/sync-rule-admission-neonctl.err 2>/dev/null)"
  rm -f /tmp/sync-rule-admission-neonctl.err
  stamp "FAIL no DSN from neonctl: ${reason:-no error text}"
  print -u2 "could not derive the production owner DSN from neonctl (logged)."
  print -u2 "neonctl said: ${reason:-nothing at all}"
  exit 1
fi
rm -f /tmp/sync-rule-admission-neonctl.err

PY="$REPO/.venv/bin/python"

if (( EXPORT )); then
  print "== exporting production's rule-admission classification =="
  set +e
  DATABASE_URL="$DSN" "$PY" "$REPO/tools/export-rule-admission.py"
  erc=$?
  set -e
  if (( erc == 0 )); then
    stamp "OK export refreshed ops/config/rule-admission-export.v1.json"
    print ""
    print "commit ops/config/rule-admission-export.v1.json, then run"
    print "  ./.venv/bin/python ops/rule-classification-parity-check.py"
    print "to compare it against ops/config/rule-enforcement-map.json."
  else
    stamp "FAIL export rc=$erc"
    print -u2 "the export failed; ops/config/rule-admission-export.v1.json is unchanged."
  fi
  exit $erc
fi

if (( ! APPLY )); then
  print "== read-only: ops/rule-admission-audit.py against production =="
  set +e
  DATABASE_URL="$DSN" "$PY" "$REPO/ops/rule-admission-audit.py"
  rc=$?
  set -e
  if (( rc == 0 )); then
    stamp "OK read-only: production admission contract is complete"
  else
    stamp "GAP read-only rc=$rc"
    print ""
    print "production's admission contract is incomplete (counts above)."
    print "  missing        = active rules with no admission row at all"
    print "  needs_revision = admitted with a control that is not installed"
    print "  incomplete     = admitted without applicability/projection/reachability"
    print "Admission backfill is retired; inspect the rule records for the gaps above."
  fi
  print ""
  print "== read-only: ops/rule-delivery-audit.py against production =="
  set +e
  DATABASE_URL="$DSN" "$PY" "$REPO/ops/rule-delivery-audit.py"
  drc=$?
  set -e
  if (( drc == 0 )); then
    stamp "OK read-only: production delivery tags are complete"
  elif (( drc == 2 )); then
    # A DIFFERENT FINDING WITH A DIFFERENT REMEDY, and saying so is rule 88e9b5eb.
    stamp "BLOCKED read-only delivery: the delivery tables are absent"
    print ""
    print "the delivery tables are not in this database yet, so there is nothing"
    print "to audit. Apply migration 0291 (./the GitHub production deployment workflow), then re-run."
  else
    stamp "GAP read-only delivery rc=$drc"
    print ""
    print "production's DELIVERY tags are incomplete (counts above)."
    print "  untagged   = active rules no load layer covers — omitted by omission"
    print "  orphaned   = a tag naming a rule that is no longer active"
    print "  wildcarded = a '*' pack, which is the current behaviour in a scoping costume"
    print "  emptypack  = a pack no active rule is in — a trigger that loads nothing"
    print "Re-run with --apply once $MAP tags every active rule."
  fi
  # WRITTEN AS AN IF RATHER THAN `(( )) && rc=$drc`. Under `set -e` that form is
  # the last command of its own && list, so a FALSE test — the ordinary case
  # where the admission half already failed — exits the script on the spot and
  # the delivery finding never reaches the operator.
  if (( rc == 0 )); then rc=$drc; fi
  exit $rc
fi

print "== installing the reviewed delivery tags (layer0 / control / pack) =="
set +e
DATABASE_URL="$DSN" "$PY" "$REPO/tools/sync-rule-load-layers.py"
rc=$?
set -e
if (( rc != 0 )); then
  stamp "FAIL delivery tags rc=$rc"
  print -u2 "the delivery tags refused or failed; the delivery tags did not complete."
  print -u2 "if it named an active rule with no reviewed delivery tag, that rule is"
  print -u2 "the finding: extend rule_load_layers in $MAP, have it"
  print -u2 "reviewed, commit, and re-run."
  exit $rc
fi

print ""
print "== reading production back through ops/rule-admission-audit.py =="
set +e
DATABASE_URL="$DSN" "$PY" "$REPO/ops/rule-admission-audit.py"
rc=$?
set -e
if (( rc == 0 )); then
  stamp "OK admission readback"
  print "every active rule in production carries a complete admission contract."
else
  stamp "FAIL admission audit rc=$rc"
  print -u2 "The admission audit reports gaps. Read the counts above:"
  print -u2 "they name which of the four failure shapes is left."
fi

print ""
print "== reading production back through ops/rule-delivery-audit.py =="
set +e
DATABASE_URL="$DSN" "$PY" "$REPO/ops/rule-delivery-audit.py"
drc=$?
set -e
if (( drc == 0 )); then
  stamp "OK delivery tags applied and verified"
  print "every active rule in production now carries a reviewed delivery tag."
  print "Check ops.rule_delivery_policy and standing-context for the active delivery mode."
else
  stamp "FAIL delivery applied but audit rc=$drc"
  print -u2 "THE TAGS LANDED AND THE DELIVERY AUDIT STILL DISAGREES. The counts above"
  print -u2 "name which shape is left."
fi
if (( rc == 0 )); then rc=$drc; fi
exit $rc
