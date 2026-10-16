#!/bin/bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec /bin/bash "$0" "$@"
fi

set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

export CARR_JEV_OFFLINE=1

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3
export CARR_CI_PYTHON="$PY"
CI_SELFTEST_TIMEOUT_SECONDS=120
CI_TIMEOUT_HELPER="$REPO/bin/with-timeout.py"

if [ "${1:-}" = "--doc-drift" ]; then
  shift
  exec "${CARR_DOC_DRIFT_PYTHON:-$PY}" "$REPO/scripts/doc-drift/ci.py" "$@"
fi

STRICT=0
ONLY=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --strict) STRICT=1 ;;
    --only)   ONLY="$ONLY $(echo "${2:-}" | tr ',' ' ')"; shift ;;
    --list)   LIST=1 ;;
    -h|--help) echo "Usage: ops/ci.sh [--strict] [--only classes] [--list]"; exit 0 ;;
    *) echo "ci.sh: unknown argument: $1" >&2; exit 64 ;;
  esac
  shift
done

CLASS_ORDER="pushfloor unit types contract gates secret dependency migration binding artifact freshness"

class_desc() {
  case "$1" in
    pushfloor)  echo "seeded defect that must not leave the machine (local pre-push floor)" ;;
    unit)       echo "seeded failing unit test" ;;
    freshness)  echo "seeded stale rewrite of a generated config file" ;;
    types)      echo "seeded shape mistake in a data hand-off" ;;
    contract)   echo "seeded auth/schema contract break" ;;
    gates)      echo "seeded enforcement-layer regression" ;;
    secret)     echo "seeded credential in the tree" ;;
    dependency) echo "seeded unpinned or vulnerable dependency" ;;
    migration)  echo "seeded bad migration / trigger permission" ;;
    binding)    echo "seeded binding or config drift" ;;
    artifact)   echo "seeded artifact mismatch (verb loss)" ;;
    *)          echo "" ;;
  esac
}

if [ "${LIST:-0}" = "1" ]; then
  echo "ci.sh check classes — each is the gate for one doctrine seeded-failure class:"
  for c in $CLASS_ORDER; do printf '  %-11s %s\n' "$c" "$(class_desc "$c")"; done
  exit 0
fi

FAILED=0
FAILED_CLASSES=""
SKIPPED=0
SKIPPED_CLASSES=""
RAN=0
CLASS_TIMINGS=""

ok()   { RAN=$((RAN+1)); printf '  \033[32mOK\033[0m    %-11s %s\n' "$1" "${2:-}"; }
bad()  { RAN=$((RAN+1)); printf '  \033[31mFAIL\033[0m  %-11s %s\n' "$1" "${2:-}"; FAILED=$((FAILED+1)); FAILED_CLASSES="$FAILED_CLASSES $1"; }
skip() { RAN=$((RAN+1)); printf '  \033[33mSKIP\033[0m  %-11s %s\n' "$1" "${2:-}"; SKIPPED=$((SKIPPED+1)); SKIPPED_CLASSES="$SKIPPED_CLASSES $1"; }

run_quiet() {  # run_quiet <logfile> <cmd...>  — capture output, return status
  local log="$1"; shift
  "$@" >"$log" 2>&1
}

LOGDIR="$(mktemp -d "${TMPDIR:-/tmp}/carr-ci.XXXXXX")"
trap 'rm -rf "$LOGDIR"' EXIT

"$PY" ops/ci-quarantine.py validate || exit 1

INHERIT_ASKED=0
inherited_abort() {
  [ "$INHERIT_ASKED" = "0" ] || return 0
  INHERIT_ASKED=1
  "$PY" ops/inherited-from-main.py
}

fail_tail() {  # fail_tail <logfile>
  local log="$1" lines window
  lines="$(wc -l < "$log" 2>/dev/null | tr -d ' ')"
  [ -n "$lines" ] || lines=0
  window="$(mktemp "${TMPDIR:-/tmp}/carr-ci-tail.XXXXXX")"
  if [ "$lines" -lt 200 ]; then cat "$log" >"$window" 2>/dev/null
  else tail -80 "$log" >"$window" 2>/dev/null; fi
  if "$PY" ops/ci-secret-scan.py --redact <"$window" >"$window.redacted" 2>/dev/null; then
    cat "$window.redacted" >&2
  else
    printf '        %s\n' "gate output WITHHELD: ops/ci-secret-scan.py --redact could not run, and unredacted child output is never printed. The captured log is at $log." >&2
  fi
  rm -f "$window" "$window.redacted"
}

check_unit() {
  local failed_pkgs=""
  for pkg in mcp-server control-room workspace practice-plugin; do
    [ -f "$pkg/package.json" ] || continue
    local unit_env=""
    [ "$pkg" = "mcp-server" ] && unit_env="F03_PARITY_REQUIRE_PYTHON=1"
    if ! run_quiet "$LOGDIR/unit-$pkg.log" env $unit_env npm --prefix "$pkg" test; then
      failed_pkgs="$failed_pkgs $pkg"
      echo "--- $pkg ---" >&2
      tail -25 "$LOGDIR/unit-$pkg.log" >&2
    fi
    "$PY" ops/ci-quarantine.py report "$LOGDIR/unit-$pkg.log"
  done
  if [ -n "$failed_pkgs" ]; then
    bad unit "node suites failed:$failed_pkgs"
  else
    ok unit "Node package suites pass"
  fi
}

check_freshness() {
  if [ ! -f ops/stale-config-check.py ]; then
    skip freshness "ops/stale-config-check.py not present"
    return
  fi
  if run_quiet "$LOGDIR/freshness.log" "$PY" ops/stale-config-check.py; then
    ok freshness "no generated config rewritten from a stale base"
  else
    cat "$LOGDIR/freshness.log" >&2
    bad freshness "this branch would overwrite a generated file it never saw"
  fi
}

check_types() {
  if [ ! -x bin/type-check.sh ]; then
    skip types "bin/type-check.sh not present"
    return
  fi
  local rc
  run_quiet "$LOGDIR/types.log" ./bin/type-check.sh
  rc=$?
  if [ "$rc" -eq 0 ]; then
    ok types "mypy clean across the repo's Python"
  elif [ "$rc" -eq 78 ]; then
    local tcpy
    tcpy="$( { [ -x "$REPO/.venv/bin/python" ] && "$REPO/.venv/bin/python" -c 'import sys;print("%d.%d"%sys.version_info[:2])'; } 2>/dev/null || python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo "unknown" )"
    case "$tcpy" in
      3.[0-9]|unknown)
        skip types "mypy excluded by the requirements pin on Python $tcpy (pin needs >= 3.10) — accepted machine state" ;;
      *)
        skip types "mypy not installed on Python $tcpy (pinned in requirements.lock, so CI always has it)" ;;
    esac
  else
    tail -25 "$LOGDIR/types.log" >&2
    bad types "mypy found shape mistakes — see the errors above"
  fi
}

check_contract() {
  local failures=""
  if [ -f workspace/contracts/validate.mjs ]; then
    run_quiet "$LOGDIR/contract-workspace.log" node workspace/contracts/validate.mjs \
      || { failures="$failures workspace/contracts"; tail -20 "$LOGDIR/contract-workspace.log" >&2; }
  fi
  for t in control-room/test/contracts.test.mjs control-room/test/work-request-projection.test.mjs; do
    [ -f "$t" ] || continue
    run_quiet "$LOGDIR/contract-$(basename "$t").log" node --test "$t" \
      || { failures="$failures $t"; tail -20 "$LOGDIR/contract-$(basename "$t").log" >&2; }
  done
  if [ -f ops/capture-verb-reachability.py ]; then
    run_quiet "$LOGDIR/contract-capture-verb.log" "$PY" ops/capture-verb-reachability.py
    crc=$?
    if [ "$crc" -eq 78 ]; then
        printf '        \033[33mnot run\033[0m  capture-verb-reachability — %s\n' \
        "$(tail -1 "$LOGDIR/contract-capture-verb.log" 2>/dev/null)" >&2
    elif [ "$crc" -ne 0 ]; then
      failures="$failures ops/capture-verb-reachability.py"
      tail -20 "$LOGDIR/contract-capture-verb.log" >&2
    fi
  fi
  if [ -n "$failures" ]; then
    bad contract "contract checks failed:$failures"
  else
    ok contract "workspace + control-room contracts validate"
  fi
}

tree_fingerprint() {
  printf '%s\n' "$(git rev-parse HEAD 2>/dev/null)"
  git status --porcelain --untracked-files=no 2>/dev/null
  git diff --cached --name-only 2>/dev/null
}

check_gates() {
  local failures="" count=0 skiplist="" gates_timed_out=0
  export CARR_HOOK_FIXTURE=1

  local tree_before; tree_before="$(tree_fingerprint)"

  local scope="ops/config/ci-check-scope.json"
  excluded_reason() {  # excluded_reason <basename> -> reason, or empty
    [ -f "$scope" ] || return 0
    "$PY" - "$scope" "$1" "${CARR_CI_PORTABLE_ONLY:-0}" <<'PYEOF'
import json, sys
scope, name, portable = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
d = json.load(open(scope))
if portable:
    for e in d.get("local_only", []):
        if e["check"] == name:
            print("LOCAL-ONLY: " + e["reason"]); sys.exit(0)
PYEOF
  }

  local eligible=""
  for t in ops/*-selftest.py tools/test-*.py tools/test_*.py tools/*-selftest.py \
           tools/room-bridge/test_*_unit.py \
           tools/room-bridge/test_activation_reliability.py \
           test/doc_drift/test_*.py test/doc_drift_product/test_*.py; do
    [ -f "$t" ] || continue
    local base; base="$(basename "$t")"
    local why; why="$(excluded_reason "$base")"
    if [ -n "$why" ]; then
      skiplist="$skiplist $base"
      printf '        \033[33mnot run\033[0m  %s — %s\n' "$base" "$why" >&2
      continue
    fi
    count=$((count+1))
    eligible="$eligible $t"
  done
  local pooled_timeout=$(( CI_SELFTEST_TIMEOUT_SECONDS * ${CARR_CI_GATE_JOBS:-4} ))
  "$PY" ops/ci-quarantine.py snapshot >"$LOGDIR/source-identity.json" || { bad gates "source identity unreadable"; return; }
  export CI_TIMEOUT_HELPER CI_SELFTEST_TIMEOUT_SECONDS LOGDIR PY pooled_timeout
  printf '%s\n' $eligible | xargs -P "${CARR_CI_GATE_JOBS:-4}" -n1 bash -c '
    b="$(basename "$1")"
    "$PY" ops/ci-quarantine.py run --print-log --test "$1" --log "$LOGDIR/gate-$b.log" \
      --identity-file "$LOGDIR/source-identity.json" -- \
      "$PY" "$CI_TIMEOUT_HELPER" "$pooled_timeout" "$PY" "$1" \
      >"$LOGDIR/gate-$b.report" 2>&1
    echo $? >"$LOGDIR/gate-$b.rc"
  ' _
  local grc
  for t in $eligible; do
    base="$(basename "$t")"
    grc="$(cat "$LOGDIR/gate-$base.rc" 2>/dev/null || echo 1)"
    cat "$LOGDIR/gate-$base.report"
    if [ "$grc" -eq 124 ]; then
      failures="$failures TIMEOUT:$base"
      gates_timed_out=1
      printf '        \033[31mTIMEOUT\033[0m  %s — exceeded %ss (pool-scaled); aborting remaining gate selftests\n' \
        "$base" "$pooled_timeout" >&2
      tail -12 "$LOGDIR/gate-$base.log" >&2
      break
    elif [ "$grc" -eq 78 ]; then
      skiplist="$skiplist $base"
      printf '        \033[33mnot run\033[0m  %s — NOT CONFIGURED (exit 78): %s\n' \
        "$base" "$(tail -1 "$LOGDIR/gate-$base.log" 2>/dev/null)" >&2
    elif [ "$grc" -ne 0 ]; then
      inherited_abort "$base" "$PY" "$t"
      failures="$failures $base"; fail_tail "$LOGDIR/gate-$base.log"
    fi
  done

  if [ "$gates_timed_out" -eq 0 ]; then for t in tools/test-*.sh tools/test_*.sh; do
    [ -f "$t" ] || continue
    local sbase; sbase="$(basename "$t")"
    local swhy; swhy="$(excluded_reason "$sbase")"
    if [ -n "$swhy" ]; then
      skiplist="$skiplist $sbase"
      printf '        \033[33mnot run\033[0m  %s — %s\n' "$sbase" "$swhy" >&2
      continue
    fi
    count=$((count+1))
    "$PY" ops/ci-quarantine.py run --print-log --test "$t" --log "$LOGDIR/gate-$sbase.log" \
      --identity-file "$LOGDIR/source-identity.json" -- \
      "$PY" "$CI_TIMEOUT_HELPER" "$CI_SELFTEST_TIMEOUT_SECONDS" "$t"
    grc=$?
    if [ "$grc" -eq 124 ]; then
      failures="$failures TIMEOUT:$sbase"
      gates_timed_out=1
      printf '        \033[31mTIMEOUT\033[0m  %s — exceeded %ss; aborting remaining gate selftests\n' \
        "$sbase" "$CI_SELFTEST_TIMEOUT_SECONDS" >&2
      tail -12 "$LOGDIR/gate-$sbase.log" >&2
      break
    elif [ "$grc" -ne 0 ]; then
      inherited_abort "$sbase" "$t"
      failures="$failures $sbase"; fail_tail "$LOGDIR/gate-$sbase.log"
    fi
  done; fi
  run_quiet "$LOGDIR/safety-hooks.log" "$PY" hooks/gate-integrity.py --strict \
    || { inherited_abort gate-integrity "$PY" hooks/gate-integrity.py --strict
         failures="$failures gate-integrity"; tail -12 "$LOGDIR/safety-hooks.log" >&2; }

  "$PY" ops/boot-budget-check.py

  for inv in audit-queue-freshness-check \
             rule-load-layer-check rule-classification-parity-check \
             selftest-git-isolation-check \
             scheduler-cutover-coverage-gate \
             sync-core-rule-ids rule-route-coverage \
             sync-rule-boot-classes migration-order-gate \
             migration-safety-gate check-jev-conformance door-reachability-check; do
    [ -f "ops/$inv.py" ] || continue
    local inv_args=()
    case "$inv" in
      sync-core-rule-ids|sync-rule-boot-classes) inv_args=(--check) ;;
    esac
    run_quiet "$LOGDIR/gate-$inv.log" "$PY" "ops/$inv.py" ${inv_args[@]+"${inv_args[@]}"} \
      || { inherited_abort "$inv" "$PY" "ops/$inv.py" ${inv_args[@]+"${inv_args[@]}"}
           failures="$failures $inv"; tail -12 "$LOGDIR/gate-$inv.log" >&2; }
  done

  if [ "$(tree_fingerprint)" != "$tree_before" ]; then
    failures="$failures tree-mutated-by-selftests"
    {
      echo "  A selftest in this class CHANGED THE WORKING TREE it was invoked in."
      echo "  HEAD, tracked-file state, or the index differs from before the suite ran."
      echo "  This is the 2026-08-14 failure mode: git exports GIT_DIR into hooks and"
      echo "  it OVERRIDES cwd, so a fixture built with cwd=<tempdir> and no scrub"
      echo "  writes into whatever repository GIT_DIR names."
      echo "  Find the culprit with: ops/selftest-git-isolation-check.py"
      echo "  Recover with a mixed reset; do not commit anything from this tree until"
      echo "  you have read what changed:"
      git status --porcelain --untracked-files=no 2>/dev/null | head -20
    } >&2
  fi

  if [ -n "$failures" ]; then
    bad gates "failed:$failures"
  elif [ -n "$skiplist" ]; then
    ok gates "$count suites + safety hook installation · NOT RUN:$skiplist"
  else
    ok gates "$count selftest suites + safety hook installation"
  fi
}

check_secret() {
  local leak_args="--staged"
  if [ "${GITHUB_EVENT_NAME:-}" = "pull_request" ]; then
    leak_args="--ci"
  elif [ -n "${CARR_CI_RANGE:-}" ]; then
    leak_args="--range $CARR_CI_RANGE"
  fi
  for t in security/test_*.py; do
    if ! run_quiet "$LOGDIR/leak-guard-tests.log" "$PY" "$t"; then
      cat "$LOGDIR/leak-guard-tests.log" >&2
      bad secret "leak guard qualification failed"
      return
    fi
  done
  if ! run_quiet "$LOGDIR/leak-guard.log" "$PY" security/leak_guard.py $leak_args \
      ${CARR_PUSH_RECEIVER:+--remote "$CARR_PUSH_RECEIVER"}; then
    cat "$LOGDIR/leak-guard.log" >&2
    bad secret "secret or client data in introduced Git blobs"
    return
  fi
  local scope_args="" scope_words="tracked files"
  if [ -n "${CARR_CI_RANGE:-}" ]; then
    scope_args="--range $CARR_CI_RANGE"
    scope_words="blobs pushed in $CARR_CI_RANGE"
  fi
  if run_quiet "$LOGDIR/secret.log" "$PY" ops/ci-secret-scan.py $scope_args; then
    ok secret "no shaped credential in $scope_words"
  else
    cat "$LOGDIR/secret.log" >&2
    bad secret "credential-shaped content found in $scope_words"
  fi
}

check_pushfloor() {
  local failures="" ran=""
  local _floor_t0; _floor_t0="$(date +%s)"

  local changed=""
  if [ -n "${CARR_CI_RANGE:-}" ]; then
    changed="$(git diff --name-only --diff-filter=ACMR "$CARR_CI_RANGE" 2>/dev/null || true)"
    local main_changed=""
    if main_changed="$(git diff --name-only origin/main HEAD 2>/dev/null)"; then
      changed="$(LC_ALL=C comm -12 \
        <(printf '%s\n' "$changed" | LC_ALL=C sort -u) \
        <(printf '%s\n' "$main_changed" | LC_ALL=C sort -u))"
    fi
  fi

  floor_fail() {  # floor_fail <name> <remedy>
    failures="$failures $1"
    printf '        \033[31m%s\033[0m — %s\n' "$1" "$2" >&2
  }

  run_quiet "$LOGDIR/pushfloor-no-client-deliverables.log" \
    "$PY" ops/no-client-deliverables-gate.py \
    || { tail -12 "$LOGDIR/pushfloor-no-client-deliverables.log" >&2
         floor_fail no-client-deliverables \
           "a client-deliverable path is tracked. git rm it — this repo is public and must carry no client record."; }

  if [ -n "$changed" ] && [ -f ops/githooks/path-hygiene-check.py ]; then
    local added
    added="$(git diff --name-only --diff-filter=ACR "$CARR_CI_RANGE" 2>/dev/null || true)"
    local main_paths=""
    if main_paths="$(git ls-tree -r --name-only origin/main 2>/dev/null)"; then
      added="$(LC_ALL=C comm -23 \
        <(printf '%s\n' "$added" | LC_ALL=C sort -u) \
        <(printf '%s\n' "$main_paths" | LC_ALL=C sort -u))"
    fi
    if [ -n "$added" ]; then
      run_quiet "$LOGDIR/pushfloor-path-hygiene.log" \
        "$PY" ops/githooks/path-hygiene-check.py --paths $added \
        || { tail -12 "$LOGDIR/pushfloor-path-hygiene.log" >&2
             floor_fail path-hygiene \
               "rename the path before it is published — depth or draft/final naming (rule 0e22e34a). After the push it needs a history rewrite."; }
    fi
  fi

  if [ -n "$changed" ] && printf '%s\n' "$changed" | grep -Eq \
      '^(migrations/|tools/migration_number_contract\.py$|ops/migration-order-gate\.py$)'; then
    ran="$ran migration-structure"
    run_quiet "$LOGDIR/pushfloor-migration-structure.log" \
      "$PY" ops/migration-order-gate.py \
      || { tail -15 "$LOGDIR/pushfloor-migration-structure.log" >&2
           floor_fail migration-structure \
             "reserve a forward migration number and repair every reference before push"; }
  fi

  if [ -n "$changed" ] && printf '%s\n' "$changed" | grep -Eq \
      '(^ops/ci\.sh$|(^|/)(test[^/]*|[^/]*selftest[^/]*)\.(py|sh|mjs|js)$)'; then
    ran="$ran test-collection"
    run_quiet "$LOGDIR/pushfloor-test-collection.log" \
      "$PY" ops/ci-selftest.py --collection-only \
      || { tail -12 "$LOGDIR/pushfloor-test-collection.log" >&2
           floor_fail test-collection \
             "include the new test in CI's collection or its named decision exception"; }
  fi

  local changed_py=""
  if [ -n "$changed" ]; then
    changed_py="$(printf '%s\n' "$changed" | grep -E '\.py$' || true)"
  fi
  if [ -n "$changed_py" ]; then
    local type_rc=0
    run_quiet "$LOGDIR/pushfloor-types.log" ./bin/type-check.sh || type_rc=$?
    if [ "$type_rc" -eq 78 ]; then
      printf '        \033[33mnot run\033[0m  types — mypy absent; the hosted types class still covers this\n' >&2
    else
      ran="$ran types"
      if [ "$type_rc" -ne 0 ]; then
        tail -20 "$LOGDIR/pushfloor-types.log" >&2
        floor_fail types "run the configured type check: ./bin/type-check.sh"
      fi
    fi
  fi

  if [ -n "$changed" ] && printf '%s\n' "$changed" | grep -Eq \
      '^(migrations/[0-9]+_.*scac.*\.sql|mcp-server/src/scac-mutation-registry\.v[0-9]+\.generated\.js|ops/config/scac-registry-.*\.json)$'; then
    ran="$ran scac-atlas"
    run_quiet "$LOGDIR/pushfloor-scac-atlas.log" node --test \
      mcp-server/test/atlas-inventory-graph-read.test.mjs \
      mcp-server/test/atlas-inventory-graph-web.test.mjs \
      || { tail -18 "$LOGDIR/pushfloor-scac-atlas.log" >&2
           floor_fail scac-atlas \
             "active registry changed; update the Atlas version contract or repair its reader before push"; }
  fi

  local gate_surface=""
  if [ -n "$changed" ]; then
    gate_surface="$(printf '%s\n' "$changed" | grep -E '^(hooks/[^/]+\.py|ops/ci\.sh|ops/githooks/|ops/config/(delegation-gate-hook|codex-hooks|model-floors)\.json|ops/[^/]+-selftest\.py)' || true)"
  fi
  if [ -n "$gate_surface" ]; then
    ran="$ran gate-closure"
    local unclassified="" touched_gate base paired

    for touched_gate in $(printf '%s\n' "$gate_surface" | grep -E '^hooks/[^/]+\.py$' | grep -v -- '-selftest\.py$' || true); do
      base="$(basename "$touched_gate" .py)"
      paired="ops/$base-selftest.py"
      if [ -f "$paired" ]; then
        run_quiet "$LOGDIR/pushfloor-$base-selftest.log" "$PY" "$paired" \
          || { tail -15 "$LOGDIR/pushfloor-$base-selftest.log" >&2
               floor_fail "$base-selftest" \
                 "the gate you changed fails its own acceptance test: .venv/bin/python $paired"; }
      else
        unclassified="$unclassified $base"
      fi
    done

    for paired in $(printf '%s\n' "$gate_surface" | grep -E '^ops/[^/]+-selftest\.py$' || true); do
      [ -f "$paired" ] || continue
      run_quiet "$LOGDIR/pushfloor-$(basename "$paired").log" "$PY" "$paired" \
        || { tail -15 "$LOGDIR/pushfloor-$(basename "$paired").log" >&2
             floor_fail "$(basename "$paired")" "run it alone: .venv/bin/python $paired"; }
    done

    local iso ibase
    for iso in ops/hook-env-isolation-selftest.py ops/selftest-git-isolation-check.py; do
      [ -f "$iso" ] || continue
      ibase="$(basename "$iso")"
      run_quiet "$LOGDIR/pushfloor-$ibase.log" "$PY" "$iso" \
        || { tail -12 "$LOGDIR/pushfloor-$ibase.log" >&2
             floor_fail "$ibase" "run it alone to iterate: .venv/bin/python $iso"; }
    done


    if [ -n "$unclassified" ] && [ -n "$ONLY" ] && ! selected gates; then
      ran="$ran gates-deferred"
        printf '        \033[33mdeferred\033[0m   gates — no paired selftest for:%s — the full class runs hosted (required check on main)\n' \
        "$unclassified" >&2
      printf '                   run it locally now: ops/ci.sh --only gates · durable fix: add ops/<gate>-selftest.py\n' >&2
    fi
  fi

  local _floor_secs=$(( $(date +%s) - _floor_t0 ))
  if [ -n "$failures" ]; then
    bad pushfloor "failed:$failures (${_floor_secs}s)"
  else
    ok pushfloor "${_floor_secs}s · baseline, path shape${ran:+ +}${ran}"
  fi
}

check_dependency() {
  local strictflag=""
  [ "$STRICT" = "1" ] && strictflag="--strict"
  if run_quiet "$LOGDIR/dep.log" "$PY" ops/ci-dep-check.py $strictflag; then
    ok dependency "$(tail -1 "$LOGDIR/dep.log")"
  else
    cat "$LOGDIR/dep.log" >&2
    bad dependency "see above"
  fi
}

check_migration() {
  if ! run_quiet "$LOGDIR/migration-structure.log" "$PY" ops/migration-order-gate.py; then
    tail -15 "$LOGDIR/migration-structure.log" >&2
    bad migration "migration ordering or slot collision failed before database work; reserve a forward number"
    return
  fi
  local dsn="${CARR_CI_DATABASE_URL:-}"
  if [ -z "$dsn" ]; then
    skip migration "no CARR_CI_DATABASE_URL (CI provides a throwaway Postgres)"
    return
  fi
  case "$dsn" in
    *@localhost:*|*@localhost/*|*@127.0.0.1:*|*@127.0.0.1/*) ;;
    *) bad migration "REFUSED: CARR_CI_DATABASE_URL is not loopback. This applies every migration; it runs against a throwaway only."
       return ;;
  esac
  local psql_bin=""
  for c in psql /opt/homebrew/opt/libpq/bin/psql /usr/local/opt/libpq/bin/psql; do
    if command -v "$c" >/dev/null 2>&1; then psql_bin="$c"; break; fi
  done
  if [ -z "$psql_bin" ]; then
    skip migration "no psql on PATH to load db/schema.sql"
    return
  fi
  if [ ! -f db/schema.sql ]; then
    skip migration "db/schema.sql absent — regenerate with bin/schema-snapshot.sh"
    return
  fi

  local _mt0 _mt; _mt0="$(date +%s)"; MIGRATION_STEP_TIMINGS=""
  _mstep() { _mt="$(date +%s)"; MIGRATION_STEP_TIMINGS="$MIGRATION_STEP_TIMINGS $1=$(( _mt - _mt0 ))s"; _mt0="$_mt"; }
  if ! PGOPTIONS='--client-min-messages=warning' run_quiet "$LOGDIR/migration-load.log" \
       "$psql_bin" -v ON_ERROR_STOP=1 -q -d "$dsn" -f db/schema.sql; then
    tail -25 "$LOGDIR/migration-load.log" >&2
    if grep -qE 'already exists' "$LOGDIR/migration-load.log"; then
      bad migration "this database already carries the schema — it is not a fresh one. \
This class loads db/schema.sql from scratch, so it needs a database nobody has loaded yet. \
The supported lane builds and removes one for you: ./run.sh local-db-ci --class migration \
(add --port N if the default is taken by another session)."
    else
      bad migration "db/schema.sql did not load — the committed structure is not loadable"
    fi
    return
  fi

  _mstep load
  if ! DATABASE_URL="$dsn" run_quiet "$LOGDIR/migration.log" "$PY" tools/migrate.py --apply --yes; then
    tail -30 "$LOGDIR/migration.log" >&2
    bad migration "a pending migration did not apply to the committed schema"
    return
  fi

  if ! CARR_RULE_TEST_DATABASE_URL="$dsn" run_quiet "$LOGDIR/migration-rule-supersession.log" \
      node --test mcp-server/test/find-rule-supersedes.test.mjs; then
    tail -30 "$LOGDIR/migration-rule-supersession.log" >&2
    bad migration "rule lookup or atomic teach supersession database proof failed"
    return
  fi

  if ! CARR_WRITER_READ_TEST_DATABASE_URL="$dsn" run_quiet "$LOGDIR/migration-writer-read-route.log" \
      node --test mcp-server/test/catch-me-up-writer-route.test.mjs; then
    tail -30 "$LOGDIR/migration-writer-read-route.log" >&2
    bad migration "timeline verbs cannot read their views as carr_writer"
    return
  fi

  _mstep migrate
  local definer_pg_proof definer_pg_log
  for definer_pg_proof in \
    mcp-server/test/security-definer-search-path-postgres.sql \
    mcp-server/test/definer-temp-substitution-postgres.sql \
    mcp-server/test/completion-tenant-barriers-postgres.sql; do
    definer_pg_log="$LOGDIR/$(basename "$definer_pg_proof" .sql).log"
    if ! run_quiet "$definer_pg_log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" -f "$definer_pg_proof"; then
      tail -30 "$definer_pg_log" >&2
      bad migration "SECURITY DEFINER PostgreSQL acceptance failed: $definer_pg_proof"
      return
    fi
  done
  local dot_proof_shards="${CARR_DOT_PROOF_SHARDS:-3}"
  if ! DATABASE_URL="$dsn" "$PY" ops/ci-migration-gates.py --logdir "$LOGDIR" --psql "$psql_bin" \
       --sql-proof mcp-server/test/definer-hardening-catalog-postgres.sql \
       --sql-proof "mcp-server/test/dot-security-definers-postgres.sql:$dot_proof_shards"; then
    bad migration "SECURITY DEFINER PostgreSQL acceptance failed: definer-hardening-catalog or dot-security-definers (named above)"
    return
  fi
  if ! CARR_INVOICE_TEST_DATABASE_URL="$dsn" CARR_INVOICE_TEST_REQUIRED=1 \
       run_quiet "$LOGDIR/invoice-tracker-transaction.log" \
       node --test mcp-server/test/invoice-tracker-transaction.test.mjs; then
    tail -30 "$LOGDIR/invoice-tracker-transaction.log" >&2
    bad migration "the registered invoice receipt and atomic rollback proof failed"
    return
  fi
  local tour_pg_proof tour_pg_log
  for tour_pg_proof in \
    mcp-server/test/tour-operations-slice2-postgres.sql \
    mcp-server/test/tour-property-identity-jurisdiction-postgres.sql \
    mcp-server/test/tour-domain-route-cheat-sheet-postgres.sql \
    mcp-server/test/tour-delivery-data-plane-postgres.sql \
    mcp-server/test/tour-client-share-allowlist-postgres.sql \
    mcp-server/test/assurance-health-store-postgres.sql \
    mcp-server/test/work-portfolio-postgres.sql \
    mcp-server/test/local-deals-postgres.sql \
    mcp-server/test/invoice-tracker-postgres.sql; do
    [ -f "$tour_pg_proof" ] || continue
    tour_pg_log="$LOGDIR/$(basename "$tour_pg_proof" .sql).log"
    if ! run_quiet "$tour_pg_log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" -f "$tour_pg_proof"; then
      tail -30 "$tour_pg_log" >&2
      bad migration "Tour Operations PostgreSQL acceptance failed: $tour_pg_proof"
      return
    fi
  done

  if ! run_quiet "$LOGDIR/model-role-store-postgres.log" \
       "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
       -f mcp-server/test/model-role-store-postgres.sql; then
    tail -30 "$LOGDIR/model-role-store-postgres.log" >&2
    bad migration "Model Room role-store PostgreSQL acceptance failed"
    return
  fi

  if ! run_quiet "$LOGDIR/governed-correspondence-store-postgres.log" \
       "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
       -f mcp-server/test/governed-correspondence-store-postgres.sql; then
    tail -30 "$LOGDIR/governed-correspondence-store-postgres.log" >&2
    bad migration "V5-J103 governed correspondence store PostgreSQL acceptance failed"
    return
  fi

  if [ -f mcp-server/test/record-source-authority-live-pg.v5.test.mjs ]; then
    if ! DATABASE_URL="$dsn" CARR_F01_DB_REQUIRED=1 PSQL="$psql_bin" \
         run_quiet "$LOGDIR/record-source-authority-live-pg.log" \
         node --test mcp-server/test/record-source-authority-live-pg.v5.test.mjs; then
      tail -40 "$LOGDIR/record-source-authority-live-pg.log" >&2
      bad migration "V5-F01 record-source-authority PostgreSQL acceptance failed"
      return
    fi
  fi

  if [ -f mcp-server/test/f01-login-bundle-membership-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/f01-login-bundle-membership-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/f01-login-bundle-membership-postgres.sql; then
      tail -30 "$LOGDIR/f01-login-bundle-membership-postgres.log" >&2
      bad migration "the F01 login-bundle membership proof (0732) failed"
      return
    fi
  fi

  if [ -f mcp-server/test/rule-context-runtime-postgres.test.mjs ]; then
    if ! DATABASE_URL="$dsn" CARR_F05_DB_REQUIRED=1 \
         run_quiet "$LOGDIR/rule-context-runtime-postgres.log" \
         node --test mcp-server/test/rule-context-runtime-postgres.test.mjs; then
      tail -40 "$LOGDIR/rule-context-runtime-postgres.log" >&2
      bad migration "V5-F05 rule-context PostgreSQL acceptance failed"
      return
    fi
  fi

  if ! run_quiet "$LOGDIR/codex-continuity-postgres.log" \
       "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
       -f mcp-server/test/codex-continuity-postgres.sql; then
    tail -30 "$LOGDIR/codex-continuity-postgres.log" >&2
    bad migration "Codex continuity PostgreSQL acceptance failed"
    return
  fi
  if ! run_quiet "$LOGDIR/claude-continuity-postgres.log" \
       "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
       -f mcp-server/test/claude-continuity-postgres.sql; then
    tail -30 "$LOGDIR/claude-continuity-postgres.log" >&2
    bad migration "Claude continuity PostgreSQL acceptance failed"
    return
  fi

  if [ -f mcp-server/test/gate-zero-outcome-role-boundary.test.mjs ]; then
    if ! DATABASE_URL="$dsn" CARR_GATE_ZERO_RACE_REQUIRED=1 \
         run_quiet "$LOGDIR/gate-zero-role-boundary.log" \
         node --test mcp-server/test/gate-zero-outcome-role-boundary.test.mjs; then
      tail -30 "$LOGDIR/gate-zero-role-boundary.log" >&2
      bad migration "the Gate Zero producer seat/connection-role boundary proof failed"
      return
    fi
  fi

  if [ -f mcp-server/test/foundation-assurance-oracle-role-boundary.v5.test.mjs ]; then
    if ! DATABASE_URL="$dsn" CARR_FOUNDATION_ASSURANCE_DB_REQUIRED=1 \
         run_quiet "$LOGDIR/foundation-assurance-role-boundary.log" \
         node --test mcp-server/test/foundation-assurance-oracle-role-boundary.v5.test.mjs; then
      tail -30 "$LOGDIR/foundation-assurance-role-boundary.log" >&2
      bad migration "the foundation-assurance oracle connection-role boundary proof failed"
      return
    fi
  fi

  if [ -f mcp-server/test/program-controller-census-role-boundary.v5.test.mjs ]; then
    if ! DATABASE_URL="$dsn" CARR_PROGRAM_CONTROLLER_DB_REQUIRED=1 \
         run_quiet "$LOGDIR/program-controller-role-boundary.log" \
         node --test mcp-server/test/program-controller-census-role-boundary.v5.test.mjs; then
      tail -30 "$LOGDIR/program-controller-role-boundary.log" >&2
      bad migration "the program-controller seam connection-role boundary proof failed"
      return
    fi
  fi

  if [ -f mcp-server/test/program-controller-census-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/program-controller-census-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/program-controller-census-postgres.sql; then
      tail -30 "$LOGDIR/program-controller-census-postgres.log" >&2
      bad migration "the program-controller seam catalog and ledger proof failed"
      return
    fi
  fi

  for proof in cost-ledger-projection.v5 doc-conversation notifications session-identity dispatch-spine meeting-mode delivery-cadence-a05-tools amend-closed-loop-postgres action-class-successor-registry-postgres independent-review-cycle-postgres a02-rule-enforcement-postgres salesforce-reconciliation-rw02-postgres salesforce-read-run-store-rw02-postgres vendor-directory-postgres relationship-network-postgres approve-rule-legacy-adoption-postgres; do
    if [ -f "mcp-server/test/$proof.test.mjs" ]; then
      if ! DATABASE_URL="$dsn" CARR_COST_LEDGER_DB_REQUIRED=1 \
           CARR_DOC_CONVERSATION_DB_REQUIRED=1 CARR_R03_DB_REQUIRED=1 \
           CARR_SESSION_IDENTITY_DB_REQUIRED=1 CARR_DISPATCH_SPINE_DB_REQUIRED=1 \
           CARR_MEETING_MODE_DB_REQUIRED=1 CARR_AMEND_CLOSED_LOOP_DB_REQUIRED=1 \
           CARR_ACTION_CLASS_SUCCESSOR_DB_REQUIRED=1 \
           CARR_V5_A03_DB_REQUIRED=1 \
           CARR_A02_RULE_COVERAGE_DB_REQUIRED=1 \
           CARR_RW02_DB_REQUIRED=1 \
           CARR_APPROVE_RULE_ADOPTION_DB_REQUIRED=1 \
           CARR_VENDOR_DIRECTORY_DB_REQUIRED=1 CARR_RELATIONSHIP_DB_REQUIRED=1 \
           run_quiet "$LOGDIR/$proof-db.log" \
           node --test "mcp-server/test/$proof.test.mjs"; then
        tail -30 "$LOGDIR/$proof-db.log" >&2
        bad migration "the $proof database acceptance proof failed"
        return
      fi
    fi
  done

  if [ -f mcp-server/test/producer-cost-ledger-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/producer-cost-ledger-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/producer-cost-ledger-postgres.sql; then
      tail -30 "$LOGDIR/producer-cost-ledger-postgres.log" >&2
      bad migration "the producer cost ledger admission and grant proof failed"
      return
    fi
  fi

  if [ -f mcp-server/test/doc-conversation-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/doc-conversation-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/doc-conversation-postgres.sql; then
      tail -30 "$LOGDIR/doc-conversation-postgres.log" >&2
      bad migration "the Doc conversation attribution and access-list proof failed"
      return
    fi
  fi

  if [ -f mcp-server/test/doc-conversation-write-doors-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/doc-conversation-write-doors-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/doc-conversation-write-doors-postgres.sql; then
      tail -30 "$LOGDIR/doc-conversation-write-doors-postgres.log" >&2
      bad migration "the Doc conversation write-door creator and grant proof failed"
      return
    fi
  fi

  if [ -f mcp-server/test/doc-conversation-list-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/doc-conversation-list-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/doc-conversation-list-postgres.sql; then
      tail -30 "$LOGDIR/doc-conversation-list-postgres.log" >&2
      bad migration "the Doc conversation list attribution, paging and grant proof failed"
      return
    fi
  fi

  if [ -f mcp-server/test/r03-notifications-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/r03-notifications-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/r03-notifications-postgres.sql; then
      tail -30 "$LOGDIR/r03-notifications-postgres.log" >&2
      bad migration "the R03 notification mint, recipient and status proof failed"
      return
    fi
  fi

  if [ -f mcp-server/test/r03-loop-queue-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/r03-loop-queue-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -P format=csv -d "$dsn" \
         -f mcp-server/test/r03-loop-queue-postgres.sql; then
      tail -30 "$LOGDIR/r03-loop-queue-postgres.log" >&2
      bad migration "the R03 loop producer and dedupe proof failed"
      return
    fi
    if ! awk -F, '
      $0=="r03_recovered_health" { phase=1; next }
      $0=="r03_actor_mismatch_health" { phase=2; next }
      $1=="joe" && phase==1 {
        recovered=($2==3 && $3==3 && $4==3 && $6==0 && $7==0 &&
                   $8==0 && $9==1 && $10==0 && index($15,"No open")==1)
        phase=0
      }
      $1=="joe" && phase==2 {
        mismatch=($2==4 && $3==3 && $4==3 && $6==1 && $7==1 &&
                  $8==1 && $9==1 && $10==0 && index($15,"On missing")==1)
        phase=0
      }
      END { exit !(recovered && mismatch) }
    ' "$LOGDIR/r03-loop-queue-postgres.log"; then
      tail -30 "$LOGDIR/r03-loop-queue-postgres.log" >&2
      bad migration "R03 standing report conflates historical and unresolved failures"
      return
    fi
  fi

  if [ -f mcp-server/test/notification-preferences-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/notification-preferences-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/notification-preferences-postgres.sql; then
      tail -30 "$LOGDIR/notification-preferences-postgres.log" >&2
      bad migration "the notification preference read, write and quiet-hours proof failed"
      return
    fi
  fi

  if [ -f mcp-server/test/session-identity-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/session-identity-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/session-identity-postgres.sql; then
      tail -30 "$LOGDIR/session-identity-postgres.log" >&2
      bad migration "the session identity projection, filter and dispatch proof failed"
      return
    fi
  fi

  if [ -f mcp-server/test/dispatch-spine-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/dispatch-spine-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/dispatch-spine-postgres.sql; then
      tail -30 "$LOGDIR/dispatch-spine-postgres.log" >&2
      bad migration "the dispatch spine link, acknowledgement and per-dispatch reason proof failed"
      return
    fi
  fi

  if [ -f mcp-server/test/foundation-assurance-minimum-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/foundation-assurance-minimum-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/foundation-assurance-minimum-postgres.sql; then
      tail -30 "$LOGDIR/foundation-assurance-minimum-postgres.log" >&2
      bad migration "the foundation-assurance candidate/evidence atomic binding proof failed"
      return
    fi
  fi

  if [ -f mcp-server/test/gate-zero-outcome-record-race.test.mjs ]; then
    if ! DATABASE_URL="$dsn" CARR_GATE_ZERO_RACE_REQUIRED=1 \
         run_quiet "$LOGDIR/gate-zero-outcome-race.log" \
         node --test mcp-server/test/gate-zero-outcome-record-race.test.mjs; then
      tail -30 "$LOGDIR/gate-zero-outcome-race.log" >&2
      bad migration "the Gate Zero outcome candidate-key race proof failed"
      return
    fi
  fi

  if [ -f mcp-server/test/gate-zero-outcome-digest-tagged.test.mjs ]; then
    if ! DATABASE_URL="$dsn" CARR_GATE_ZERO_RACE_REQUIRED=1 \
         run_quiet "$LOGDIR/gate-zero-digest-tagged.log" \
         node --test mcp-server/test/gate-zero-outcome-digest-tagged.test.mjs; then
      tail -30 "$LOGDIR/gate-zero-digest-tagged.log" >&2
      bad migration "the Gate Zero tagged outcome-digest cross-implementation proof failed"
      return
    fi
  fi

  if [ -f mcp-server/test/gate-zero-candidate-authority-filing.test.mjs ]; then
    if ! DATABASE_URL="$dsn" CARR_GATE_ZERO_RACE_REQUIRED=1 \
         run_quiet "$LOGDIR/gate-zero-candidate-filing.log" \
         node --test mcp-server/test/gate-zero-candidate-authority-filing.test.mjs; then
      tail -30 "$LOGDIR/gate-zero-candidate-filing.log" >&2
      bad migration "the service-candidate and historical Gate Zero provenance proof failed"
      return
    fi
  fi

  _mstep tour
  if run_quiet "$LOGDIR/migration-grants.log" "$psql_bin" -X -v ON_ERROR_STOP=1 -Atq -d "$dsn" -c "
    do \$\$ begin
      if not has_table_privilege('carr_writer', 'public.lead', 'insert') then
        raise exception 'carr_writer cannot insert into lead (the PR #75 case)'; end if;
      if not has_schema_privilege('carr_jobs', 'ops', 'usage') then
        raise exception 'carr_jobs lacks usage on schema ops (0115)'; end if;
      if not has_column_privilege('carr_jobs', 'ops.incident', 'state', 'update') then
        raise exception 'carr_jobs cannot update incident.state (0117)'; end if;
      if has_column_privilege('carr_jobs', 'ops.incident', 'resolved_at', 'update') then
        raise exception 'carr_jobs can update incident.resolved_at — 0117 interlock broken'; end if;
      if not has_function_privilege('carr_reader', 'public.state_as_of(text, uuid, timestamptz)', 'execute') then
        raise exception 'carr_reader cannot execute state_as_of (0106)'; end if;
      if not pg_has_role('carr_exporter', 'carr_reader', 'member') then
        raise exception 'carr_exporter lost its carr_reader bundle (0006)'; end if;
    end \$\$;"; then
    local n
    n="$(grep -c '^applying ' "$LOGDIR/migration.log" 2>/dev/null || :)"

    _mstep grants
    if ! CARR_CI_DATABASE_URL="$dsn" run_quiet "$LOGDIR/migration-triggers.log" \
         "$PY" ops/trigger-grant-check.py; then
      tail -25 "$LOGDIR/migration-triggers.log" >&2
      bad migration "a trigger reads a table its firing role cannot select"
      return
    fi

    _mstep triggers
    if ! DATABASE_URL="$dsn" run_quiet "$LOGDIR/migration-control-plane-sync.log" \
         "$PY" tools/control-plane.py sync; then
      tail -30 "$LOGDIR/migration-control-plane-sync.log" >&2
      bad migration "control-plane registry sync failed after migrations"
      return
    fi

    local dsn_read='environ\.get\("(CARR_CI_)?DATABASE_URL"|environ\["(CARR_CI_)?DATABASE_URL"\]|getenv\("(CARR_CI_)?DATABASE_URL"'
    local db_gate_failures="" db_gate_count=0 db_gate_unmarked="" db_gate_declared=""
    _mstep sync
    local db_gates=""
    for g in ops/*-gate.py; do
      [ -f "$g" ] || continue
      if grep -q '^# ci: db-gate' "$g"; then
        db_gate_count=$((db_gate_count+1))
        db_gates="$db_gates $g"
      elif grep -qE "$dsn_read" "$g"; then
        db_gate_unmarked="$db_gate_unmarked $(basename "$g")"
      elif grep -q '^# ci: runs-outside-ci' "$g"; then
        db_gate_declared="$db_gate_declared\n            $(basename "$g"): $(sed -n 's/^# ci: runs-outside-ci *— *//p' "$g" | head -1)"
      fi
    done
    if ! DATABASE_URL="$dsn" "$PY" ops/ci-migration-gates.py --logdir "$LOGDIR" $db_gates; then
      db_gate_failures="isolated/serial DB gate runner (see named failures above)"
    fi
    _mstep gates
    echo "migration-step-timing:${MIGRATION_STEP_TIMINGS}"
    if [ -n "$db_gate_declared" ]; then
      printf '        \033[33moutside CI\033[0m  gate(s) declared to run elsewhere:%b\n' \
        "$db_gate_declared" >&2
    fi
    if [ -n "$db_gate_unmarked" ]; then
      bad migration "gate(s) read a database URL but carry no \`# ci: db-gate\` marker, so nothing runs them:$db_gate_unmarked (add the marker, or declare \`# ci: runs-outside-ci — why\`)"
      return
    fi
    if [ -n "$db_gate_failures" ]; then
      bad migration "database acceptance gate(s) failed:$db_gate_failures"
      return
    fi

    local shadow_rc=0 shadow_note
    "$PY" ops/migration-shadow.py ${CARR_SHADOW_ARTIFACT_DIR:+--artifact-dir "$CARR_SHADOW_ARTIFACT_DIR"} \
      > "$LOGDIR/migration-shadow.log" 2>&1 || shadow_rc=$?
    _mstep shadow
    case "$shadow_rc" in
      0) shadow_note="; $(sed -n 's/^migration-shadow: \([0-9]* pending migration(s) applied\).*/\1/p' "$LOGDIR/migration-shadow.log" | head -1) on a PostgreSQL 18 shadow and db/schema.sql matches" ;;
      3) if [ "$STRICT" = "1" ]; then
           cat "$LOGDIR/migration-shadow.log" >&2
           bad migration "the migration shadow needs PostgreSQL 18 server binaries (CI installs postgresql-18)"
           return
         fi
         shadow_note="; migration shadow not run (no PostgreSQL 18 here)" ;;
      *) tail -60 "$LOGDIR/migration-shadow.log" >&2
         bad migration "migration shadow: pending migrations did not apply over the base snapshot, or db/schema.sql is not what they produce"
         return ;;
    esac

    ok migration "committed schema loads; ${n:-0} pending migration(s) apply; app-role grants verified live; trigger reads granted; $db_gate_count db acceptance gate program(s) pass (each program reports its own assertions)$shadow_note"
  else
    tail -15 "$LOGDIR/migration-grants.log" >&2
    bad migration "the app roles' grants did not survive into the built database"
  fi
}

check_binding() {
  local problems="" machine_drift=""
  run_quiet "$LOGDIR/binding-agent-boot.log" "$PY" ops/agent-boot-contract.py \
    || { problems="$problems agent-boot"; cat "$LOGDIR/binding-agent-boot.log" >&2; }
  if [ -f mcp-server/wrangler.toml ]; then
    run_quiet "$LOGDIR/binding-wrangler.log" node -e '
      const fs=require("fs"), t=fs.readFileSync("mcp-server/wrangler.toml","utf8");
      const need=["name","main","compatibility_date"];
      const missing=need.filter(k=>!new RegExp("^\\s*"+k+"\\s*=","m").test(t));
      if(missing.length){console.error("wrangler.toml missing: "+missing.join(", "));process.exit(1);}
      if(/DATABASE_URL\s*=/.test(t)){console.error("wrangler.toml declares a DATABASE_URL inline; it belongs in a secret");process.exit(1);}
    ' || { problems="$problems wrangler.toml"; cat "$LOGDIR/binding-wrangler.log" >&2; }
  fi
  if [ -x mcp-server/node_modules/.bin/wrangler ]; then
    if ! run_quiet "$LOGDIR/binding-worker-boot.log" ./bin/worker-boot-check.sh; then
      problems="$problems worker-boot"
      tail -40 "$LOGDIR/binding-worker-boot.log" >&2
    fi
  else
    printf '        \033[33mskip\033[0m  worker-boot-check — wrangler not installed (run npm install in mcp-server/)\n' >&2
  fi
  if [ -f "$HOME/.claude/settings.json" ]; then
    if ! run_quiet "$LOGDIR/binding-config.log" "$PY" ops/config-as-code.py; then
      local cfg_changed cfg_owned
      cfg_changed="$(git diff --name-only origin/main...HEAD 2>/dev/null || true)"
      tail -15 "$LOGDIR/binding-config.log" >&2
      cfg_owned="$("$PY" ops/config_drift_ownership.py $cfg_changed \
        < "$LOGDIR/binding-config.log" 2>/dev/null || true)"
      if [ -n "$cfg_owned" ]; then
        problems="$problems config-as-code"
        printf '        this branch touches the drifting declaration(s), so they are yours:\n%s\n' "$cfg_owned" >&2
      else
        machine_drift="yes"
        printf '        \033[33mnot fatal here\033[0m  config-as-code drift is on the MACHINE and this branch touches\n' >&2
        printf '        none of the drifting declarations above. Reported, not vetoed —\n' >&2
        printf '        run `ops/config-as-code.py pull` on a branch that owns the change.\n' >&2
      fi
    fi
  fi
  if [ -n "$problems" ]; then
    bad binding "drift:$problems"
  elif [ -n "$machine_drift" ]; then
    ok binding "worker config declared; MACHINE config-as-code drift reported above, not owned by this branch"
  else
    ok binding "worker config declared; installed wiring matches repo"
  fi
}

check_artifact() {
  local shipping marker
  if ! run_quiet "$LOGDIR/doctorcre-artifact.log" "$PY" \
      tools/release-manifest.py doctorcre-artifact verify \
      --pin ops/config/doctorcre-artifact.v1.json; then
    tail -20 "$LOGDIR/doctorcre-artifact.log" >&2
    bad artifact "the exact DoctorCRE release artifact did not match its CARR pin"
    return
  fi
  shipping="$(sh ops/verb-count.sh "$REPO/mcp-server" --check-retirements 0 2>"$LOGDIR/artifact.log")"
  if [ -z "$shipping" ]; then
    cat "$LOGDIR/artifact.log" >&2
    bad artifact "registry import or retirement validation failed"
    return
  fi
  local rc=0
  marker="$("$PY" ops/last-deployed-verb-count.py carr-mcp production --registry 2>"$LOGDIR/artifact-baseline.log")" || rc=$?
  case "$rc" in
    0)
      if ! sh ops/verb-count.sh "$REPO/mcp-server" --check-retirements "$marker" >"$LOGDIR/artifact-count.log" 2>"$LOGDIR/artifact.log"; then
        cat "$LOGDIR/artifact.log" >&2
        bad artifact "verb loss does not match the committed retirement ledger"
      else
        ok artifact "$shipping verbs; loss checked by name against deployed source and committed retirements"
      fi
      ;;
    3)
      ok artifact "$shipping verbs; no prior deployment recorded, this would set the baseline"
      ;;
    78)
      if [ "${CARR_CI_PORTABLE_ONLY:-0}" = "1" ]; then
            ok artifact "$shipping verbs; shrink guard not run here (portable runner has no ledger credential — the deploy enforces it)"
      else
        skip artifact "$shipping verbs counted, but no ledger credential — the shrink comparison did not run"
      fi
      ;;
    *)
      cat "$LOGDIR/artifact-baseline.log" >&2
      bad artifact "could not read the deployed verb registry from the ledger (exit $rc) — refusing to call it unmeasured-but-fine"
      ;;
  esac
}

echo "carr-system CI — $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"
[ "$STRICT" = "1" ] && echo "  strict: a SKIP counts as a failure"
echo

for _want in $ONLY; do
  if [ -z "$(class_desc "$_want")" ]; then
    echo "ci.sh: no such class: $_want (try --list)" >&2
    exit 64
  fi
done

selected() {
  case " $ONLY " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

for c in $CLASS_ORDER; do
  if [ -n "$ONLY" ] && ! selected "$c"; then continue; fi
  _class_name="$c"
  _class_t0="$(date +%s)"
  "check_$c"
  CLASS_TIMINGS="$CLASS_TIMINGS $_class_name=$(( $(date +%s) - _class_t0 ))s"
  c="$_class_name"
done


echo
echo "ci-timing:${CLASS_TIMINGS}"
[ -n "$FAILED_CLASSES" ]  && echo "ci-failed-classes:${FAILED_CLASSES}"
[ -n "$SKIPPED_CLASSES" ] && echo "ci-skipped-classes:${SKIPPED_CLASSES}"

if [ -n "$FAILED_CLASSES" ]; then
  FLOOR_CLASSES="pushfloor secret"
  _floor=""; _relocated=""
  for f in $FAILED_CLASSES; do
    case " $FLOOR_CLASSES " in
      *" $f "*) _floor="$_floor $f" ;;
      *)        _relocated="$_relocated $f" ;;
    esac
  done
  echo "ci-floor-verdict: floor:${_floor:- none} relocated:${_relocated:- none}"
fi

echo
if [ "$FAILED" -gt 0 ]; then
  echo "CI FAILED — $FAILED class(es) failed, $SKIPPED skipped"
  exit 1
fi
if [ "$SKIPPED" -gt 0 ] && [ "$STRICT" = "1" ]; then
  echo "CI FAILED — $SKIPPED class(es) SKIPPED under --strict."
  echo "In CI every check is available, so a skip means a check stopped running."
  exit 1
fi
if [ "$SKIPPED" -gt 0 ]; then
  echo "CI passed — $SKIPPED class(es) skipped locally (they run in CI under --strict)"
else
  echo "CI passed — every class green"
fi
exit 0
