#!/bin/bash
# Run the repository's canonical CI classes and report their verified results.
if [ -z "${BASH_VERSION:-}" ]; then
  exec /bin/bash "$0" "$@"
fi

set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"


COMMON_DIR="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || COMMON_DIR="$REPO/.git"
PY="$(dirname "$COMMON_DIR")/.venv/bin/python"
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
MIGRATION_PART=all
GATE_SHARD=1
GATE_SHARDS=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --strict) STRICT=1 ;;
    --only)   ONLY="$ONLY $(echo "${2:-}" | tr ',' ' ')"; shift ;;
    --gate-shard)
      case "${2:-}" in
        1/2) GATE_SHARD=1; GATE_SHARDS=2 ;;
        2/2) GATE_SHARD=2; GATE_SHARDS=2 ;;
        *) echo "ci.sh: gate shard must be 1/2 or 2/2" >&2; exit 64 ;;
      esac
      shift ;;
    --migration-part) MIGRATION_PART="${2:-}"; shift ;;
    --list)   LIST=1 ;;
    --gate-programs) GATE_PROGRAMS=1 ;;
    -h|--help) echo "Usage: ops/ci.sh [--strict] [--only classes] [--migration-part all|core|proofs|gates|acceptance|fixtures|reconstruction|equivalence|shadow] [--gate-shard 1/2|2/2] [--list] [--gate-programs]"; exit 0 ;;
    *) echo "ci.sh: unknown argument: $1" >&2; exit 64 ;;
  esac
  shift
done

case "$MIGRATION_PART" in
  all|core|proofs|gates|acceptance|fixtures|reconstruction|equivalence|shadow) ;;
  *) echo "ci.sh: unknown migration part" >&2; exit 64 ;;
esac

CLASS_ORDER="pushfloor unit types gates secret dependency migration binding artifact freshness hxprobe"

class_desc() {
  case "$1" in
    hxprobe) echo "vendored HxStore reader tests and release build with locked dependencies" ;;
    freshness) echo "integration index has no unresolved merge entries" ;;
    pushfloor)  echo "seeded defect that must not leave the machine (local pre-push floor)" ;;
    unit)       echo "seeded failing unit test" ;;
    types)      echo "seeded shape mistake in a data hand-off" ;;
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

# Per-program evidence (WR-000542): one row per executed program, as
# class, log name, exit, start, end. Only names and numbers, never output or
# environment values. CARR_CI_PROGRAM_RECORDS names the JSON that
# ops/ci-evidence.py writes at exit; it is consumed here so a nested ci.sh run
# (selftest fixtures) cannot overwrite this run's record.
PROGRAM_RECORDS="${CARR_CI_PROGRAM_RECORDS:-}"
unset CARR_CI_PROGRAM_RECORDS
record_program() {  # record_program <name> <exit> <start>
  printf '%s\t%s\t%s\t%s\t%s\n' "${_class_name:-}" "$1" "$2" "$3" "${EPOCHREALTIME:-$(date +%s)}" \
    >>"$LOGDIR/programs.tsv"
}

run_quiet() {  # run_quiet <logfile> <cmd...>  — capture output, return status
  local log="$1" rc start="${EPOCHREALTIME:-$(date +%s)}"; shift
  "$@" >"$log" 2>&1
  rc=$?
  printf '%s\n' "$rc" >"$log.rc"
  record_program "$(basename "$log" .log)" "$rc" "$start"
  return "$rc"
}

LOGDIR="$(mktemp -d "${RUNNER_TEMP:-/tmp}/carr-ci.XXXXXX")" || exit 1
trap '
  rc=$?
  if [ -n "$PROGRAM_RECORDS" ] && ! "$PY" "$REPO/ops/ci-evidence.py" programs --rows "$LOGDIR/programs.tsv" \
      --output "$PROGRAM_RECORDS" >/dev/null; then
    printf "ci-program-records: NOT WRITTEN to %s\n" "$PROGRAM_RECORDS" >&2
  fi
  if [ "$rc" -eq 0 ]; then
    rm -rf "$LOGDIR"
  else
    for status in "$LOGDIR"/*.rc; do
      [ -f "$status" ] || continue
      log="${status%.rc}"
      case "$log" in *.log) ;; *) log="$log.log" ;; esac
      [ "$(cat "$status")" != 0 ] || rm -f "$log" "$status"
    done
    if [ "${GITHUB_ACTIONS:-}" = true ]; then
      for log in "$LOGDIR"/*.log; do
        [ -f "$log" ] || continue
        "$PY" ops/ci-secret-scan.py --redact <"$log" >"$log.redacted" || rm -f "$log.redacted"
      done
    fi
    printf "ci-retained-logs: %s\n" "$LOGDIR" >&2
  fi
  exit "$rc"
' EXIT

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

check_hxprobe() {
  if run_quiet "$LOGDIR/hxprobe.log" bash -c \
      'cd "$1/vendor/hxprobe" && cargo test --locked && cargo build --release --locked' _ "$REPO"; then
    cat "$LOGDIR/hxprobe.log"
    ok hxprobe "locked Rust tests and release build passed"
  else
    fail_tail "$LOGDIR/hxprobe.log"
    bad hxprobe "locked Rust tests or release build failed"
  fi
}

check_unit() {
  if run_quiet "$LOGDIR/unit.log" "$PY" ops/ci-unit.py; then
    cat "$LOGDIR/unit.log"
    ok unit "required Node package checks executed"
  else
    fail_tail "$LOGDIR/unit.log"
    bad unit "required Node package checks failed or did not execute"
  fi
}

check_freshness() {
  local unresolved
  if ! unresolved="$(git ls-files --unmerged)"; then
    bad freshness "integration index could not be read"
  elif [ -n "$unresolved" ]; then
    bad freshness "integration tree has unresolved merge entries"
  else
    ok freshness "integration index has no unresolved merge entries"
  fi
}

check_types() {
  if ! run_quiet "$LOGDIR/worker-types.log" npm --prefix mcp-server run typecheck; then
    tail -25 "$LOGDIR/worker-types.log" >&2
    bad types "Worker verb contract types failed"
    return
  fi
  if [ ! -x bin/type-check.sh ]; then
    skip types "bin/type-check.sh not present"
    return
  fi
  local rc
  run_quiet "$LOGDIR/types.log" ./bin/type-check.sh
  rc=$?
  if [ "$rc" -eq 0 ]; then
    ok types "Worker verb contracts and Python types pass"
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

tree_fingerprint() {
  printf '%s\n' "$(git rev-parse HEAD 2>/dev/null)"
  git status --porcelain --untracked-files=no 2>/dev/null
  git diff --cached --name-only 2>/dev/null
}

check_gates() {
  export CARR_HOOK_FIXTURE=1
  local rc summary
  "$PY" ops/ci-gates.py --logdir "$LOGDIR" --result "$LOGDIR/gates-result" \
    --python "$PY" --timeout-helper "$CI_TIMEOUT_HELPER" --timeout "$CI_SELFTEST_TIMEOUT_SECONDS" \
    --shard "$GATE_SHARD" --shards "$GATE_SHARDS"
  rc=$?
  summary="$(cat "$LOGDIR/gates-result" 2>/dev/null)"
  [ -n "$summary" ] || { bad gates "gate runner exited $rc without a verdict"; return; }
  if [ "$rc" -eq 0 ]; then ok gates "$summary"; else bad gates "$summary"; fi
}

gate_programs() {
  "$PY" ops/ci-gates.py --gate-programs
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
  local verdict rc=0 deferred=()
  if [ -n "$ONLY" ] && ! selected gates; then deferred=(--gates-deferred); fi
  verdict="$("$PY" ops/ci-pushfloor.py --logdir "$LOGDIR" ${deferred[@]+"${deferred[@]}"})" || rc=$?
  if [ "$rc" -eq 0 ] && [ -n "$verdict" ]; then
    ok pushfloor "$verdict"
  else
    bad pushfloor "${verdict:-push floor runner exited $rc without a verdict}"
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
  local result="$LOGDIR/migration-result" rc=0 kind message reported=0 reported_bad=0
  local strict_arg=""
  [ "$STRICT" = 1 ] && strict_arg="--strict"
  : >"$result"
  "$PY" ops/ci-migration.py --logdir "$LOGDIR" --result "$result" --python "$PY" --part "$MIGRATION_PART" --prior-failed "$FAILED" $strict_arg || rc=$?
  while IFS=$'\t' read -r kind message; do
    case "$kind" in
      ok|skip) "$kind" migration "$message"; reported=1 ;;
      bad) bad migration "$message"; reported=1; reported_bad=1 ;;
      timing) printf '%s\n' "$message" ;;
    esac
  done <"$result"
  if [ "$reported" = 0 ] || { [ "$rc" != 0 ] && [ "$reported_bad" = 0 ]; }; then
    bad migration "migration runner exited $rc without a verdict"
  fi
}

check_binding() {
  local problems=""
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
  fi
  if [ -n "$problems" ]; then
    bad binding "drift:$problems"
  elif [ ! -x mcp-server/node_modules/.bin/wrangler ]; then
    skip binding "worker boot not checked; wrangler not installed (run npm ci in mcp-server/)"
  else
    ok binding "worker config declared; worker boot passed"
  fi
}

check_artifact() {
  local shipping base
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
  base="$(git merge-base HEAD origin/main 2>/dev/null || true)"
  [ "$base" != "$(git rev-parse HEAD)" ] || base="$(git rev-parse -q --verify 'HEAD^' || true)"
  if [ -z "$base" ]; then
    skip artifact "$shipping verbs counted, but no base commit; the shrink comparison did not run"
  elif sh ops/verb-loss-vs-base.sh "$REPO" "$base" >"$LOGDIR/artifact-count.log" 2>"$LOGDIR/artifact.log"; then
    ok artifact "$shipping verbs; loss checked by name against base ${base:0:12} and committed retirements"
  else
    cat "$LOGDIR/artifact.log" >&2
    bad artifact "verb loss against base ${base:0:12} does not match the committed retirement ledger"
  fi
}

if [ "${GATE_PROGRAMS:-0}" = "1" ]; then
  gate_programs
  exit 0
fi

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
  # Classes the local push path covers: the floor, and the secret class's scans,
  # which the pre-push hook runs itself once per push.
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
