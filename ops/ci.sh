#!/bin/bash
# ci.sh — the ONE check script. Program 2's spine.
#
# RUN UNDER BASH OR NOT AT ALL. The class loop is `for c in $CLASS_ORDER`, which
# depends on bash word-splitting an unquoted string. zsh does not split there,
# so under zsh the loop runs ONCE with c set to the whole list, `check_$c`
# becomes one unknown command, every check is skipped -- and the script still
# reaches its own success line and prints "CI passed - every class green".
# Observed exactly that on 2026-08-13 from `zsh ops/ci.sh`: eight classes
# reported green, zero of them executed. A green CI that ran nothing is the
# worst possible failure for a promotion gate, and the shebang alone does not
# prevent it, because a shebang binds `./ops/ci.sh` and not `zsh ops/ci.sh` or
# `sh ops/ci.sh`. Re-exec rather than error: the caller's intent is always to
# run the checks, so silently doing the right thing beats refusing.
if [ -z "${BASH_VERSION:-}" ]; then
  exec /bin/bash "$0" "$@"
fi
#
# WHY ONE SCRIPT. Program 2's gate is "seeded failures in tests, migration, auth,
# binding, secret and dependency checks each block promotion." That needs the
# same checks to run in two places: a GitHub runner on every push, and Joe's Mac
# at pre-push time. Rule a8c55a47 says a manual path and an automated path doing
# the same job must be the same code. So neither the workflow file nor the git
# hook contains any check logic — both call this, and this is the only place a
# check is defined. A check added here appears in both callers for free, and
# cannot be present in one and missing from the other.
#
# WHY BOTH CALLERS EXIST AT ALL. For most of this file's life the answer was that
# GitHub could not enforce anything: both the branch-protection API and the newer
# rulesets API refused with "Upgrade to GitHub Pro or make this repository
# public" on a private free-plan repo, verified live against both endpoints.
# Joe enabled Pro on 2026-08-13 and ruleset 20824501, "main: CI must be green",
# is now active on main — required status check "ops/ci.sh --strict", plus no
# force-push and no branch deletion. NOTHING in this file changed to make that
# work, which was the point of writing it this way.
#
# THE PRE-PUSH HOOK STILL EARNS ITS PLACE, and is not made redundant by the
# ruleset. It fails in SECONDS on the machine that wrote the change, before a
# runner is ever asked for; the ruleset fails minutes later after a round trip.
# It is bypassable with --no-verify and the ruleset is not, so they cover
# different halves: the hook is the fast accident-stopper, the ruleset is the
# one that cannot be talked out of it.
#
# SKIPPED IS NOT PASSED. Rule 88e9b5eb: "not authorized" and "not possible" are
# different findings and must never be reported as the same one. A check that
# cannot run here (no Postgres on Joe's laptop, no network for an audit) reports
# SKIP, never OK. Under --strict, which CI sets, a SKIP is a FAILURE — because in
# CI everything is available, so a skip there means a check silently stopped
# running. That is the exact shape of the runaway-job finding from 2026-08-13: a
# clean report that was clean because nothing was looking.
#
# Usage:
#   ops/ci.sh                       # every class, local tolerances
#   ops/ci.sh --strict              # a SKIP is a failure (what CI runs)
#   ops/ci.sh --only secret         # one class, by name
#   ops/ci.sh --only secret,pushfloor   # several, comma- or space-separated
#   ops/ci.sh --list                # the classes and what each one is the gate for
#
# CARR_CI_RANGE=<A>..<B> narrows the scopeable classes to the commits in that
# range instead of the whole repository. ops/githooks/pre-push sets it to the
# refs actually being pushed. Unset -- which is what hosted CI does -- means
# full depth, and that is the only mode any merge is judged in.

set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# NO CI RUN PAYS FOR JEV. ops/typesafe_client.py refuses every paid call while
# this is set, and every hook a selftest spawns inherits it. Measured
# 2026-10-04: selftest fixtures run from local CI and pre-push made 20-45% of
# each day's paid Jev attempts, because a fixture that drops TYPESAFE_API_KEY
# from its environment still reaches the credential FILE. Offline injected
# transports are unaffected; only the real paid path is refused.
export CARR_JEV_OFFLINE=1

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3
export CARR_CI_PYTHON="$PY"
# Explicit review preflight/admission uses the same class implementations.
# This opt-in path never adds a full suite to an ordinary pre-push invocation.
case "${1:-}" in
  --review-floor|--review-admit)
    _review_command=collect
    [ "$1" = --review-admit ] && _review_command=admit
    shift
    exec "$PY" "$REPO/ops/local-review-evidence.py" "$_review_command" "$@"
    ;;
esac
# Every gate selftest is an untrusted child: it may exercise a deliberately
# failing fixture, and a hung fixture must not hold the whole CI run forever.
# 120s is below the observed 249–317s full-run budget while leaving room for
# the slowest ordinary selftest and for room-bridge's own 60s suite bound.
CI_SELFTEST_TIMEOUT_SECONDS=120
CI_TIMEOUT_HELPER="$REPO/bin/with-timeout.py"

STRICT=0
ONLY=""
RESULT_FILE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --strict) STRICT=1 ;;
    # A LIST, not a single name. ops/githooks/pre-push selects the fast local
    # floor by naming its classes, which is what keeps rule a8c55a47 intact:
    # the hook chooses from THIS file's classes rather than reimplementing a
    # cheaper suite of its own that could then drift from the one CI runs.
    # Commas are translated to spaces so both spellings work.
    --only)   ONLY="$ONLY $(echo "${2:-}" | tr ',' ' ')"; shift ;;
    --result-file) RESULT_FILE="${2:?--result-file needs a path}"; shift ;;
    --pr-body-file) export CARR_PR_BODY_FILE="${2:?--pr-body-file needs a path}"; shift ;;
    --list)   LIST=1 ;;
    -h|--help) sed -n '1,40p' "$0"; exit 0 ;;
    *) echo "ci.sh: unknown argument: $1" >&2; exit 64 ;;
  esac
  shift
done

# Class -> the doctrine seeded-failure class it is the gate for.
# These names are the vocabulary the acceptance test uses, so a seeded defect can
# be checked against the class that SHOULD have caught it. A defect failing the
# wrong class is a finding, not a pass.
#
# NO ASSOCIATIVE ARRAYS ANYWHERE IN THIS FILE, deliberately. macOS ships bash
# 3.2.57 (2007, the last GPLv2 release) and Joe's Mac has no newer bash; a GitHub
# ubuntu runner has bash 5. `declare -A` is a bash 4 feature, so using it would
# mean this script behaved differently in its two callers — which is the exact
# failure this one-script design exists to prevent. Plain arrays and a case
# statement run identically on both.
# pushfloor is FIRST deliberately. It is the cheapest class and the one the
# local pre-push hook runs, so when a push is going to be refused it is refused
# in the first seconds rather than after the expensive classes have run.
CLASS_ORDER="pushfloor unit types contract gates replay secret dependency migration binding artifact freshness"

class_desc() {
  case "$1" in
    pushfloor)  echo "seeded defect that must not leave the machine (local pre-push floor)" ;;
    unit)       echo "seeded failing unit test" ;;
    freshness)  echo "seeded stale rewrite of a generated config file" ;;
    types)      echo "seeded shape mistake in a data hand-off" ;;
    contract)   echo "seeded auth/schema contract break" ;;
    gates)      echo "seeded enforcement-layer regression" ;;
    replay)     echo "real-fixture gate verdict regression" ;;
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
# A HARD failure is one no known gap may ever suppress. A gap excuses "this
# class is red pending a design ruling"; it must never excuse a SAFETY refusal,
# because those are categorically not the thing anyone is waiting on a ruling
# about. Caught by ops/ci-selftest.py the moment known gaps were added: the
# first version suppressed the loopback guard too, which would have let a DSN
# pointed at Neon pass silently.
HARD_FAILED=0
SKIPPED=0
SKIPPED_CLASSES=""
RAN=0
# Per-class wall-clock, written by the run loop. The 2026-08-23 council's first
# recommendation was a failure taxonomy and class timings, because neither
# existed: the suite crept 249s -> 317s across a week and nobody could say
# which class grew, and 40 CI failures in three days could not be split into
# "real defect" vs "environment skew" without opening forty logs. Plain string,
# not an associative array — see the bash 3.2 note above CLASS_ORDER.
CLASS_TIMINGS=""
CLASS_RESULTS=""
# Coverage a class announced but did not deliver: a suite that answered 78, a
# quarantined check, a probe with no credential. The class still reads OK here
# (that policy is the header's, and unchanged), but its --result-file row says
# partial, so a receipt can never certify checks that did not run.
INCOMPLETE=0
incomplete() { INCOMPLETE=$((INCOMPLETE+1)); }

ok()   { RAN=$((RAN+1)); printf '  \033[32mOK\033[0m    %-11s %s\n' "$1" "${2:-}"; }
bad()  { RAN=$((RAN+1)); printf '  \033[31mFAIL\033[0m  %-11s %s\n' "$1" "${2:-}"; FAILED=$((FAILED+1)); FAILED_CLASSES="$FAILED_CLASSES $1"; }
hard() { bad "$1" "${2:-}"; HARD_FAILED=$((HARD_FAILED+1)); }
skip() { RAN=$((RAN+1)); printf '  \033[33mSKIP\033[0m  %-11s %s\n' "$1" "${2:-}"; SKIPPED=$((SKIPPED+1)); SKIPPED_CLASSES="$SKIPPED_CLASSES $1"; }

run_quiet() {  # run_quiet <logfile> <cmd...>  — capture output, return status
  local log="$1"; shift
  "$@" >"$log" 2>&1
}

# A template is required: macOS mktemp without one ignores $TMPDIR and writes
# under /var/folders, which a sandboxed session cannot touch.
LOGDIR="$(mktemp -d "${TMPDIR:-/tmp}/carr-ci.XXXXXX")"
trap 'rm -rf "$LOGDIR"' EXIT

"$PY" ops/ci-quarantine.py validate || exit 1

# ------------------------------------------- inherited-from-main short-circuit
# 2026-08-22, 01:38-02:17 UTC: six unrelated branches failed the SAME gates-class
# selftest, none of them had touched it, and something merged to main was the
# reason. Because the required check builds refs/pull/N/merge -- the PR head
# merged into CURRENT main -- main's break is inside every open PR's run. Six
# sessions each spent a six-minute run and a session's worth of diagnostic
# tokens reading a trace for a defect none of them wrote.
#
# This is the 2026-08-23 CI-failures council's layer 1, which both chairs marked
# safe and told us to ship regardless of the other two.
#
# THE RUN STAYS RED. It exits 1 below, deliberately and on both chairs' explicit
# instruction: a victim of a broken main must not merge onto that broken main.
# Codex put the kill criterion on exactly this point -- kill any implementation
# under which a skipped or neutral check accidentally satisfies branch
# protection. What changes is the TIME, the billed minutes, and the tokens.
#
# IT ABORTS THE WHOLE RUN, hiding classes that had not run yet. That is
# deliberate too: no verdict computed on a poisoned base is worth having, and
# the branch's own defects surface on the next run once main is green.
#
# AT MOST ONCE PER RUN, on the FIRST failure only. If the first thing to go red
# is the branch's own, the branch owns the run and there is nothing to attribute;
# if it is inherited, we stop there. That bounds the cost at one merge-base
# materialisation, which is what keeps this cheap enough to leave switched on.
#
# The verdict itself is NOT computed here -- ops/inherited-from-main.py re-runs
# the failing check at the merge base, and ops/inherited-from-main-selftest.py
# seeds both directions against a real repository, including the case that must
# never misfire: a branch that broke a check without touching it.
# ------------------------------------------------------------ name the move
# THE RETRY-LOOP FINDING, 2026-08-23 council: the same defect went red two and
# three times in a row because the message said WHAT failed and never what to
# do about it. A session reads "failed: gate-integrity", re-runs it, gets the
# same line, and pushes again. Cluster C -- gate work failing the gate web --
# is near-structurally a first-try red, so this is the class where the cost of
# not naming the move is paid most often.
#
# Only the four co-change moves are named here, because those are the ones the
# failing check CANNOT name itself: each is a demand to edit a DIFFERENT file
# from the one that went red. Everything else already names its own remedy in
# its own output, and repeating it here would only teach people to scroll past.
gates_name_the_move() {  # gates_name_the_move <failed check names...>
  local named=0 f
  for f in "$@"; do
    case "$f" in
      gate-integrity)
        printf '        \033[36mTHE MOVE\033[0m  %s\n' \
          "re-bless IN THE SAME COMMIT (rule c0b38d80): hooks/gate-integrity.py --bless, then commit ops/config/gate-baseline.json together with the gate you edited" >&2
        named=1 ;;
      enforcement-coverage-check|rule-enforcement-map-check)
        printf '        \033[36mTHE MOVE\033[0m  %s\n' \
          "register the control you added: run ops/$f.py directly — it prints the exact row it wants and where it goes" >&2
        named=1 ;;
      mechanism-doctrine-gate)
        printf '        \033[36mTHE MOVE\033[0m  %s\n' \
          "add '# doctrine: <slug>' to the mechanism you ADDED, naming its section in the doctrine store (a slug, never a file path — the markdown renders were retired 2026-08-19)" >&2
        named=1 ;;
      *-selftest.py)
        printf '        \033[36mTHE MOVE\033[0m  %s\n' \
          "$f is the PAIRED suite for the gate of the same name: a gate and its selftest change in the same commit, or the pair has stopped meaning anything" >&2
        named=1 ;;
    esac
  done
  [ "$named" = "1" ] || printf '        \033[36mTHE MOVE\033[0m  %s\n' \
    "each check above names its own remedy in its output — read the failing check's own output above, not just this summary line" >&2
}

INHERIT_ASKED=0
inherited_abort() {  # inherited_abort <check-name> <cmd...> -- never returns if inherited
  [ "$INHERIT_ASKED" = "0" ] || return 0
  INHERIT_ASKED=1
  [ -f ops/inherited-from-main.py ] || return 0
  local name="$1"; shift
  local verdict rc
  # SPLIT ASSIGNMENT, not `local verdict="$(...)"`. local is itself a command and
  # its own exit status would overwrite the substitution's, so the one-line form
  # reads 0 from every verdict and short-circuits on all three of them.
  verdict="$("$PY" ops/inherited-from-main.py --check "$name" -- "$@" 2>&1)"; rc=$?
  if [ "$rc" -eq 1 ]; then
    # Worth saying out loud: the merge base is clean, so this red is the
    # branch's own. That is half of naming the move.
    printf '        \033[33mattribution\033[0m  %s\n' "$verdict" >&2
    return 0
  fi
  if [ "$rc" -ne 0 ]; then
    # rc=2 is "cannot tell", and it used to return in silence -- so a run that
    # took this path looked exactly like a run where the probe never fired, and
    # the widened replay tail that rc=0/rc=1 print was never reached. The full
    # run still proceeds, unchanged; it just says why no attribution appears.
    printf '        \033[33mattribution\033[0m  %s\n' \
      "the inherited-from-main probe could not tell whether main fails $name too; no attribution, the full run proceeds" >&2
    return 0
  fi
  bad gates "INHERITED FROM MAIN: $name"
  echo "$verdict" >&2
  echo
  echo "ci-timing:${CLASS_TIMINGS}"
  echo "ci-failed-classes:${FAILED_CLASSES}"
  echo "ci-inherited-from-main:$name"
  echo
  echo "CI FAILED — a break this branch did not cause. Still red, still unmergeable:"
  echo "main is broken, and merging onto a broken main is what this refuses to do."
  echo "Classes after gates were not run; they are not worth computing on this base."
  exit 1
}

# A FAILING GATE'S OUTPUT IS THE DIAGNOSIS, so print enough of it to contain the
# failing line. `tail -12` was narrower than the suites this class reports on:
# tools/room-bridge/test_engineering_dispatch_adapter_unit.py prints one ok line
# per check across 32 checks, so on 2026-09-11 the twelve-line window showed the
# last eleven ok lines and the exclusion summary while the FAIL itself sat above
# the window -- a hosted-Linux-only red whose cause was unreadable from the log,
# through two full CI rounds. Whole log when it is short enough to read, else the
# last 80 lines. Nothing else about failure handling changes.
#
# AND IT IS REDACTED, because widening the window widened the exposure with it.
# This prints a CHILD PROCESS'S captured stdout and stderr -- up to a whole gate
# log -- into a CI log that outlives the run and that more people can read than
# can read the tree. Twelve lines of that was already a hole; eighty, or the
# whole file, is a bigger one. The redaction is ops/ci-secret-scan.py's own
# --redact filter over its own PATTERNS list, NOT a pattern set written here: a
# second list drifts from the first, and the drift is invisible because each
# side looks correct alone. A pattern added to the scanner now protects this
# print too, with nothing to remember.
#
# FAIL-CLOSED, and this is the one place that trade goes that way. If the filter
# cannot run, the window is WITHHELD and the log path is named instead. Printing
# unredacted child output because the redactor was missing would publish the
# credential to argue that a diagnosis is more important than not publishing it;
# the log is still on disk and the reader is told exactly where.
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

# ---------------------------------------------------------------- unit
# F03_PARITY_REQUIRE_PYTHON=1, ON THE mcp-server SUITE ONLY. The V5-F03
# cross-language parity suite (mcp-server/test/f03-design-contract-parity.test.mjs)
# drives the portable Python validator over the same corpus as the server one,
# and every one of its cross-language cases SKIPS when no Python 3.9+ interpreter
# can be found. Unset, that is a skip inside a suite whose runner reports pass, so
# it never reaches this file's SKIPPED accounting and --strict cannot see it: a
# runner that lost its interpreter would keep printing a green unit class while
# the half of the parity claim that compares the two validators stopped running.
# SKIPPED IS NOT PASSED, per the header. The variable is the suite's own
# documented escape from that — set, a missing interpreter FAILS instead of
# skipping, which is correct here because CI has Python by construction.
# Deliberately scoped to this one command: control-room's and workspace's
# environment, every other class, and the semantics of every test on a machine
# that HAS Python are all unchanged.
check_unit() {
  local failed_pkgs=""
  for pkg in mcp-server control-room workspace practice-plugin; do
    [ -f "$pkg/package.json" ] || continue
    local unit_env=""
    [ "$pkg" = "mcp-server" ] && unit_env="F03_PARITY_REQUIRE_PYTHON=1"
    # shellcheck disable=SC2086  # one deliberate assignment, or nothing at all
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

# ---------------------------------------------------------------- types
# THE TRIPWIRE MOVES TO THE DOOR (2026-08-14). bin/type-check.sh existed since
# 2026-08-06 and ran in exactly one place: the 2am chain, AFTER the code had
# already merged. So the only way a shape mistake was ever reported was as a red
# nightly the next morning, and only if somebody read past the seven rows the
# nightly report was told to read. It sat red from 08-08 to 08-10 with nobody
# told, and on 08-14 — inside two hours of being cleared to zero — three
# separate sessions merged files that put it back to 12, then 1. Every one was
# the same two idioms, and every one was invisible until the chain ran.
#
# A check that only runs after merge cannot keep main green; it can only report
# that main is not. Running it here means a shape mistake blocks its own PR, and
# the nightly step passes by construction rather than by anyone remembering.
#
# SAME SCRIPT as the manual and nightly paths (rule a8c55a47) — bin/type-check.sh
# holds the file list and the lenient mypy.ini, so there is nothing here to drift
# out of step with them.
# ---------------------------------------------------------------- freshness
# THE LOST UPDATE, which review cannot catch and a merge conflict never reports.
# Two branches that both rewrite a GENERATED file do not conflict — the second
# silently replaces the first. It happened twice on 2026-08-14: an older copy of
# the type-check class reverted the newer one, and a stale gate hash was restored
# over a fresh bless, leaving main shipping a gate whose recorded hash did not
# match its own file until somebody re-blessed by hand. Each branch passed CI
# alone; the damage only existed once combined.
#
# LAST in CLASS_ORDER deliberately. It is the only class that reports on the
# branch's RELATIONSHIP to main rather than on the tree's contents, so a real
# defect in the code should surface before a "you are behind" message.
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
    # 78 = EX_CONFIG, and bin/type-check.sh's own header names the contract:
    # "Exit 78 (EX_CONFIG) when mypy is absent anywhere: the nightly chain reads
    # 78". This branch was lost when two sessions built this class in parallel
    # on 2026-08-14 and the one WITHOUT it merged second (#65 over #60), so a
    # machine with no mypy read `bad types — mypy found shape mistakes`, which
    # is both a false failure and a false explanation. It matters beyond tidiness
    # because ops/githooks/pre-push runs this script: on a fresh clone, or on
    # Dell's Mac before the venv exists, every push would be refused and the
    # stated reason would send the reader hunting for type errors that are not
    # there. CI on the runner is unaffected — mypy is pinned in requirements.lock.
    # NAME THE ACCEPTED STATE. requirements.txt pins mypy to python_version
    # >= "3.10", so on an older interpreter mypy is not missing by accident, it
    # is excluded by the pin. Printing the version keeps that visible instead of
    # letting a standing machine state read as a vague absence — and keeps the
    # DIFFERENT case (a 3.10+ machine that simply has not installed mypy) legible
    # as the real problem it is. ops/ci-selftest.py holds the same boundary as
    # MYPY_PIN_MIN_PYTHON and fails if the two drift apart.
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

# ---------------------------------------------------------------- contract
# Contract validators run as their own class even though the node suites also
# execute some of them. Overlap is deliberate: doctrine wants an auth/schema
# contract break to block INDEPENDENTLY, so it gets a gate that names it.
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
  # THE CONSUMER-SIDE RECEIPT, and it belongs in THIS class rather than in gates
  # (WR-000006, 2026-08-23 council). gates is repository content only — no
  # machine state, no network, no database — which is what makes a bare runner
  # and Joe's Mac agree. This probe is the opposite by design: it calls the
  # DEPLOYED verb to prove a normal session can still file a block, which is the
  # dead end that cost hours on 2026-08-23. Putting a network call in gates would
  # make that class SKIP on every runner, and a check that always skips is
  # decorative.
  #
  # 78 IS HONOURED HERE for the same reason the gates loop honours it: a runner
  # has no local token and no database credential, and a red mark for "not
  # configured" teaches people to scroll past. The reason is printed every run,
  # so reduced coverage is visible rather than silent.
  if [ -f ops/capture-verb-reachability.py ]; then
    run_quiet "$LOGDIR/contract-capture-verb.log" "$PY" ops/capture-verb-reachability.py
    crc=$?
    if [ "$crc" -eq 78 ]; then
      incomplete
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
  # Tracked modifications and the staged index. Untracked files are excluded
  # deliberately: out/ is gitignored and every class in this file writes logs
  # there, so counting them would fail the class on its own bookkeeping.
  git status --porcelain --untracked-files=no 2>/dev/null
  git diff --cached --name-only 2>/dev/null
}

# ---------------------------------------------------------------- gates
# The enforcement layer's own regression suites. These are the checks that caught
# the ledger-sweep scope gate swallowing its own headline trigger, so they are a
# promotion gate in their own right rather than developer convenience.
check_gates() {
  local failures="" count=0 skiplist="" gates_timed_out=0

  # EVERY GATE FIRED IN THIS CLASS IS A FIXTURE, and saying so once here is what
  # keeps the enforcement numbers honest. A selftest drives a real gate with an
  # attack payload; the gate then writes its ordinary log line. Before this
  # marker existed those lines went to out/hook-guard.log beside live refusals,
  # which is how that file reached 134,595 DENY events that the 2026-08-23
  # council could not use: an upper bound with an unknown amount of test traffic
  # in it. hooks/hook_meter.py reads this variable, children inherit it, and the
  # fixture traffic lands in out/fixtures/ instead. It changes no verdict —
  # only which file the line is written to.
  export CARR_HOOK_FIXTURE=1

  # THE INVOKING TREE MUST SURVIVE ITS OWN TEST SUITE, and on 2026-08-14 it did
  # not. A selftest built a throwaway repository with tempfile.mkdtemp and called
  # git with cwd=<temp>, which git ignored entirely because GIT_DIR — exported
  # into every hook, inherited straight through to here — OVERRIDES cwd. Every
  # fixture commit landed on the LIVE checkout: local main six commits ahead of
  # origin on a tree of THREE files, five commits named "seed", all 818 paths
  # staged as additions.
  #
  # Three layers now stand between that and this line. ops/githooks/pre-push
  # strips the variables before invoking CI; ops/git_env.py gives every selftest
  # its own scrubber; ops/selftest-git-isolation-check.py (in the inventory list
  # below) fails when a git-fixture selftest is not wired to it. This is the
  # fourth, and it is the only one that does not depend on knowing HOW a test
  # might reach the real tree: it simply reads the tree's identity before and
  # after, and fails the class if the suite moved it.
  #
  # Council recommendation 1, 2026-08-23 process audit, is explicit that this is
  # the acceptance criterion rather than a nicety: "the canonical HEAD, index,
  # and file set remain unchanged". Cost is two git invocations for the whole
  # class. NOTE the scope word: this reads the tree ci.sh was INVOKED in, which
  # under pre-push is the session's own worktree — never a hardcoded canonical
  # path — so it makes exactly the assertion the same recommendation demands
  # everywhere else.
  local tree_before; tree_before="$(tree_fingerprint)"

  # Exceptions come from ops/config/ci-check-scope.json and are ANNOUNCED, never
  # applied silently. Quarantine executes through ci-quarantine.py; a local_only one
  # is skipped only where its dependency genuinely cannot exist (a runner has no
  # Google Drive vault). Both print their reason on every single run, so the
  # coverage this class actually delivers is visible in the output rather than
  # buried in a config file nobody opens.
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

  # BOTH NAMING STYLES ARE COLLECTED, deliberately. This globbed only
  # tools/test-*.py, so three underscore-named tests — test_displacement_turn_-
  # filter.py, test_staging_recovery_rehearsal.py and
  # test_validate_exact_recovery_source.py — sat in the tree from their commits
  # until 2026-08-27 and executed exactly zero times. Same failure shape as the
  # uncollected shell tests below: something that looks like coverage and is not.
  # The repo's script convention is the hyphen, but test_foo.py is what pytest's
  # own default discovery convention produces, so the underscore arrives by
  # habit and will keep arriving; matching both is cheaper than renaming files
  # forever and leaves no pointer to a moved path dangling. What actually keeps
  # this honest is not the pattern but the invariant behind it:
  # ops/ci-selftest.py's test_every_test_file_in_the_tree_is_collected reads
  # these globs back out of this file and fails if ANY test-shaped file in the
  # tree is matched by none of them.
  # THE PYTHON SUITES RUN IN A BOUNDED PARALLEL POOL (2026-09-02). Measured
  # sequentially this loop cost 495s on the canary's biggest class: ten suites
  # carry ~235s of subprocess work and the other ~330 average 0.8s of
  # interpreter start-up each. Three 4-way parallel sweeps of all 341 suites
  # finished in 125s with zero new failures, no cross-suite collisions, and a
  # byte-clean tree fingerprint (evidence in the PR that landed this).
  # Execution is parallel; REPORTING stays sequential in stable name order
  # below, so the output contract, the exit-78 skip handling, inherited_abort,
  # and the timeout classing are unchanged. Suites must already be isolated —
  # the git-isolation selftest and the class-level tree fingerprint enforce
  # that — and each still runs under the same per-suite timeout helper.
  # Pool width: hosted runners have 4 vCPUs; CARR_CI_GATE_JOBS overrides.
  # SUBDIRECTORIES ARE NOT REACHED BY A ONE-LEVEL GLOB, and that is how
  # tools/room-bridge/ came to hold fifteen test files that no loop in this
  # file matched. Twelve *_unit.py suites and test_activation_reliability.py
  # pass on this interpreter today and had never been asked at a merge gate;
  # they are named below. The two *_live.py suites are NOT collected on
  # purpose: they boot a real Claude desk and a real Codex app-server, and
  # ops/ci-selftest.py's UNCOLLECTED_BY_DECISION carries the written reason
  # for each. Widening this line is the fix for today; the durable half is
  # that selftest, which now WALKS the tree at any depth and fails on any
  # test-shaped file that neither a glob here nor that list accounts for. So
  # the next tools/somewhere/deeper/test_x.py turns a gate red asking to be
  # decided, rather than sitting in the tree looking like coverage.
  local eligible=""
  for t in ops/*-selftest.py tools/test-*.py tools/test_*.py tools/*-selftest.py \
           tools/room-bridge/test_*_unit.py \
           tools/room-bridge/test_activation_reliability.py; do
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
  # THE POOLED CAP SCALES WITH THE POOL (2026-09-02). The 120s per-suite cap
  # was calibrated for a suite running alone; four suites sharing four hosted
  # vCPUs stretch wall time roughly by the pool width, and the first canary
  # after the pool landed proved it: ci-selftest (27s alone) was starved past
  # 120s, killed, and its nested runs leaked children that then collided with
  # the migration class (run 33634323225). Scaling by width keeps the cap's
  # meaning — "several times slower than the slowest honest run is a hang" —
  # at every pool size, and width 1 restores today's exact 120s.
  local pooled_timeout=$(( CI_SELFTEST_TIMEOUT_SECONDS * ${CARR_CI_GATE_JOBS:-4} ))
  "$PY" ops/ci-quarantine.py snapshot >"$LOGDIR/source-identity.json" || { hard gates "source identity unreadable"; return; }
  export CI_TIMEOUT_HELPER CI_SELFTEST_TIMEOUT_SECONDS LOGDIR PY pooled_timeout
  # -n1 with the path as $1, NOT -I{}: BSD xargs -I substitutes into the whole
  # script and refuses with "command line cannot be assembled, too long".
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
    # EXIT 78 IS "NOT CONFIGURED HERE", NOT A FAILURE. It is EX_CONFIG, and it is
    # already the repo's convention: bin/type-check.sh's header states it and the
    # types class above honours it. This loop counted every nonzero the same, so
    # a selftest that correctly DECLINED to run for want of a local dependency
    # read as a red gate on any machine lacking it, and the only way
    # past it was CARR_SKIP_CI on every push. Deliberately narrow: ONLY 78 skips,
    # the reason is printed every run, and every other nonzero still fails.
    # ops/ci-selftest.py seeds both cases and fails if this widens.
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

  # SHELL TESTS, run under their own shebang rather than "$PY". The loop above
  # globs only *.py and hands every match to the Python interpreter, so a shell
  # test dropped into tools/ was collected by nobody and executed by nothing —
  # it would sit in the tree looking like coverage while never running once.
  # Some things under test here ARE shell (mcp-server/smoke-reads.sh), and a
  # Python wrapper around them would only shell out to the same script.
  # Everything else is identical to the loop above: same exclusion scope, same
  # counting, same captured log and the same fail_tail print on failure.
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
  # gate-integrity is the baseline check itself: a gate edited without a
  # re-bless in the same commit (rule c0b38d80) fails here.
  #
  # --strict IS LOAD-BEARING AND WAS MISSING UNTIL 2026-08-14. Without it this
  # call could never fail: gate-integrity returns 0 on every path including its
  # failure path, deliberately, because it is also a SessionStart hook and a
  # boot check that wedges a session is worse than the drift it reports. So
  # this line announced a baseline check it never performed. Pull request #102,
  # cut before #99 merged, then overwrote main's baseline and dropped two gates
  # from it with CI green the whole way. --strict fails on repository-content
  # findings only (a hash that does not match, a blessed gate gone, a gate
  # nothing blessed) and never on machine state a runner legitimately lacks.
  #
  # THIS IS THE ONLY LAYER THAT CAN CATCH THAT OVERWRITE. The pre-commit
  # co-change check cannot: the clobber happens inside GitHub's merge, where no
  # local hook runs.
  run_quiet "$LOGDIR/gate-integrity.log" "$PY" hooks/gate-integrity.py --strict \
    || { inherited_abort gate-integrity "$PY" hooks/gate-integrity.py --strict
         failures="$failures gate-integrity"; tail -12 "$LOGDIR/gate-integrity.log" >&2; }

  # THE THREE INVENTORY CHECKS, run against THIS REPO rather than a synthetic
  # tree. The selftest loop above runs their *-selftest.py, and every one of
  # those builds its own fixture tree on purpose — that measures the CHECK and
  # says nothing whatever about the actual inventory. So all three could pass
  # their suites while the real files contradicted each other, and that is not
  # hypothetical: on 2026-08-15 ops/audit-queue-freshness-check.py FAILED on
  # main's own tree while CI reported green, because nothing here ever ran it.
  #
  # Same shape as gate-integrity --strict directly above: repository content
  # only, no machine state, no network, no database. Each carries its own
  # escape hatch for a genuinely mid-flight tree and names its own remedy in
  # its own output, so none is repeated here.
  #
  # mechanism-doctrine-gate is in this list rather than a class of its own
  # because it is the same kind of check: repository content only, no machine
  # state and no database. It is the compiled half of "knowledge ships with the
  # mechanism" (open loop 504) — a newly ADDED gate, hook, launchd job or
  # scheduled task must name the doctrine section explaining it. It reads the
  # change against origin/main, so it is a no-op on a branch that adds none of
  # those, which is nearly every branch. Editing an existing mechanism is
  # deliberately out of scope.
  # rule-enforcement-map-check JOINED THIS LIST 2026-08-23, and the reason is a
  # merged mistake. hooks/gate-integrity.py runs it too, but classifies it an
  # ENVIRONMENT problem that --strict deliberately does not fail CI on, because
  # it was once "the vault-backed rule map" and a bare runner had no vault. The
  # markdown-render cutoff on 2026-08-19 retired that dependency: the check now
  # reads one repository file and passes under `env -i`, which is the same
  # standard every other check in this loop meets. Left where it was, it caught
  # a real defect — rule 3fa422b7 categorized session_task_rail without the
  # matching override — only at the NEXT session's boot, after the change had
  # merged with CI green. Here it catches it before the merge.
  # scheduler-cutover-coverage-gate JOINED 2026-08-23. Same kind again:
  # repository content only, no machine state and no database — it reads the
  # launchd declarations and the cutover registry. It is a RATCHET rather than a
  # threshold, because coverage stands at three of twenty-three and a check that
  # fails every run is one people learn to scroll past. It fails only when a job
  # that HAD a workflow binding loses it.
  # rule-load-layer-check JOINED 2026-08-23, alongside the delivery tags it
  # validates. Same kind again: one repository file, no machine state, no
  # database. It is the check that stops the scoping change from becoming the
  # thing the council warned about — a wildcard tag, an untagged rule, or a
  # `control` claim on a rule nothing can actually refuse would all ship a
  # smaller boot payload that silently drops law, and the failure is invisible
  # afterwards because a session that boots with fewer rules looks exactly like
  # one that boots correctly.
  # reachability-check JOINED 2026-08-23, from the completion-integrity council's
  # fix D: it is the one question none of the others ask — whether anything
  # CALLS a declared control.
  # selftest-git-isolation-check JOINED 2026-08-23, council recommendation 1.
  # rule-classification-parity-check JOINED here (WR-000019 slice S10). Same
  # kind again: repository content only, no machine state and no database — it
  # compares ops/config/rule-enforcement-map.json against the committed
  # ops/config/rule-admission-export.v1.json (refreshed only by a human running
  # bin/sync-rule-admission-prod.sh --export against production) and fails on
  # any rule the two sides classify structurally differently. It does not
  # require full coverage between the files, so it stays cheap and honest on a
  # freshly-seeded, mostly-empty export exactly as it will on a fully synced one.
  # boot-budget-check and sync-core-rule-ids --check JOINED HERE (WR-000019 slice
  # S11, boot diet). Same kind again: repository content only, no machine
  # state, no database. boot-budget-check reads CLAUDE.md, the connector's
  # initialize instructions block in mcp-server/src/mcp.js, and a committed
  # snapshot of the standing-context payload (ops/config/boot-budget-core-
  # fixture.v1.json, refreshed by hand -- this check has no database to call
  # standing-context with) against ops/config/boot-budget.v1.json's ceiling,
  # and fails the push the same way an overage would go unnoticed otherwise:
  # silently, at the next session's boot. sync-core-rule-ids --check is the parity
  # gate for mcp-server/src/core-rule-ids.js against ops/config/rule-
  # triage.v1.json's `home: "core"` set -- the generated module doctrine.js
  # reads because a Cloudflare Worker has no filesystem at request time.
  # sync-rule-boot-classes --check JOINED 2026-09-26 (gated rule boot): the same
  # parity shape for mcp-server/src/rule-boot-classes.js against
  # ops/config/rule-classes.v1.json, plus the rule boot's token budget (fails
  # naming the largest always-on rules; never truncates).
  # rule-route-coverage JOINED 2026-09-26 (100%-recall rule delivery). Same
  # kind again: repository files only. It fails when an active rule has no
  # delivery route, a corpus rule is missing from ops/config/rule-routes.v1.json,
  # or a trigger names a verb, tool or gate that cannot fire.
  # check-eval-receipt JOINED 2026-09-29 (eval-gate): a change to a surface
  # registered in evals/surfaces.json carries evals/<surface>/receipt.json or a
  # reasoned no-eval line in the PR body. Enforced only in a pull_request run,
  # where GITHUB_EVENT_PATH carries the body; elsewhere a missing receipt is
  # advisory and a malformed one still fails. Procedure: evals/README.md.
  # migration-order-gate JOINED 2026-10-03, after 0757, the 0769/0770 pair and
  # 0783 each merged below a number main had already released and stopped every
  # worker release at staging-prepare ("partial candidate ledger is not an exact
  # source prefix"). Same kind: repository content only — it compares this
  # tree's migrations/ against origin/$GITHUB_BASE_REF (origin/main locally) and
  # refuses any ADDED number not strictly above the base maximum. Because strict
  # status checks force update-branch before merge, and update-branch raises
  # `synchronize`, a PR that fell behind main re-runs this and turns red until
  # renumbered. An unreadable base exits 2, which fails like any nonzero.
  # migration-safety-gate JOINED 2026-10-05 (gap #18): every ADDED migration
  # carries a rollback note in its header and declares any destructive or
  # long-locking statement (expand-contract / lock-review line), and
  # db/schema.sql's ledger names every migration with its runner checksum, so the
  # reference schema cannot fall behind the migrations again. Static, no DB; the
  # migration class proves the snapshot by actually building it.
  for inv in enforcement-coverage-check audit-queue-freshness-check map-row-evidence-check \
             rule-enforcement-map-check rule-load-layer-check rule-classification-parity-check \
             reachability-check selftest-git-isolation-check \
             drive-dependency-inventory drive-retirement-readiness-gate \
             mechanism-doctrine-gate scheduler-cutover-coverage-gate \
             boot-budget-check sync-core-rule-ids rule-route-coverage \
             sync-rule-boot-classes check-eval-receipt migration-order-gate \
             migration-safety-gate check-jev-conformance; do
    [ -f "ops/$inv.py" ] || continue
    local inv_args=()
    case "$inv" in
      sync-core-rule-ids|sync-rule-boot-classes) inv_args=(--check) ;;
    esac
    if [ "$inv" = check-eval-receipt ] && [ -n "${CARR_PR_BODY_FILE:-}" ]; then
      inv_args=(--pr-body-file "$CARR_PR_BODY_FILE")
    fi
    # bash 3.2 treats an empty array as unset under `set -u`.
    run_quiet "$LOGDIR/gate-$inv.log" "$PY" "ops/$inv.py" ${inv_args[@]+"${inv_args[@]}"} \
      || { inherited_abort "$inv" "$PY" "ops/$inv.py" ${inv_args[@]+"${inv_args[@]}"}
           failures="$failures $inv"; tail -12 "$LOGDIR/gate-$inv.log" >&2; }
  done

  # Did the suite move the tree it was invoked in? See tree_fingerprint() above.
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
    gates_name_the_move $failures
  elif [ -n "$skiplist" ]; then
    incomplete
    # Deliberately NOT a plain OK. The class ran with reduced coverage, and the
    # summary line says so — an exception that reads as a clean pass is how a
    # bounded check gets mistaken for a complete one.
    ok gates "$count suites + baseline integrity · NOT RUN:$skiplist"
  else
    ok gates "$count selftest suites + baseline integrity"
  fi
}

# ---------------------------------------------------------------- replay
# The real-fixture replay runs in an independent hosted job. PR1546 spent
# 295s here after the gate suites, then exhausted the job budget in cleanup.
# Keep the same replay, watchdog and strict failure handling in local CI.
check_replay() {
  export CARR_HOOK_FIXTURE=1
  local tree_before="$(tree_fingerprint)"
  if [ -f ops/gate-replay.py ]; then
    "$PY" "$CI_TIMEOUT_HELPER" 900 "$PY" ops/gate-replay.py >"$LOGDIR/gate-gate-replay.log" 2>&1
    local rrc=$?
    if [ "$rrc" -eq 78 ]; then
      skip replay "NOT CONFIGURED (exit 78): gate-replay.py"
      printf '        \033[33mnot run\033[0m  %s — NOT CONFIGURED (exit 78): %s\n' \
        gate-replay.py "$(tail -1 "$LOGDIR/gate-gate-replay.log" 2>/dev/null)" >&2
    elif [ "$rrc" -ne 0 ]; then
      inherited_abort gate-replay "$PY" ops/gate-replay.py
      bad replay "gate-replay failed (exit $rrc)"; tail -40 "$LOGDIR/gate-gate-replay.log" >&2
    else
      # A pass prints its invocation count, runtime and verdict totals, so the
      # hosted log shows the replay ran and how long it took.
      grep -E '^gate-replay: [0-9]+ invocations|^  verdicts: |^gate-replay: OK' \
        "$LOGDIR/gate-gate-replay.log" >&2
      ok replay "real-fixture verdicts match the committed snapshot"
    fi
  else
    skip replay "ops/gate-replay.py not present"
  fi

  if [ "$(tree_fingerprint)" != "$tree_before" ]; then
    bad replay "tree-mutated-by-replay"
  fi
}

# ---------------------------------------------------------------- secret
check_secret() {
  # SCOPE IS A FLAG ON ONE SCANNER, never a second scanner (rule a8c55a47).
  # Unset CARR_CI_RANGE -- hosted CI -- reads every tracked file, which is the
  # depth a merge is judged at. Set, it reads only the blobs the range
  # introduces, which is O(what is being pushed) rather than O(repo): 1,514
  # files read on this tree today versus the handful in a normal branch.
  local scope_args="" scope_words="tracked files"
  if [ -n "${CARR_CI_RANGE:-}" ]; then
    scope_args="--range $CARR_CI_RANGE"
    scope_words="blobs pushed in $CARR_CI_RANGE"
  fi
  # shellcheck disable=SC2086  # scope_args is a deliberate two-word argument
  if run_quiet "$LOGDIR/secret.log" "$PY" ops/ci-secret-scan.py $scope_args; then
    ok secret "no shaped credential in $scope_words"
  else
    cat "$LOGDIR/secret.log" >&2
    bad secret "credential-shaped content found in $scope_words"
  fi
}

# THE LOCAL PRE-PUSH FLOOR — gates-audit council 2026-08-23, amended by the
# ci-failures council 2026-08-24.
#
# WHY IT IS LESS THAN THE WHOLE SUITE. ops/githooks/pre-push used to run all ten
# classes on every push: minutes on one contended Mac, paid by each of the ~20
# sessions pushing from it, and paid again by hosted CI. That was correct when
# written on 2026-08-13, and the reason is in that file — the repo was private on
# a free plan, a red hosted run could not BLOCK anything, so the hook WAS the
# merge envelope. GitHub Pro closed that gap on 2026-08-14: `ops/ci.sh --strict`
# is the required check on main and nothing merges red. The full local run became
# defence-in-depth paid for in the scarcest resource here.
#
# WHY IT IS MORE THAN "OWNER AND SECRETS", which is the amendment and it came
# from this branch's own failure. The first hosted run of the pull request that
# REMOVED the local suite went red on types and gates — exactly the two classes
# the local suite would have caught in seconds. The ci-failures council took that
# as its exhibit and ruled: ship the fast subset only with a diff-scoped floor
# that PREDICTS the classes that actually go red. Pure fast-push optimises the
# 82% of pushes that were green anyway and inflates the 18% that were not.
#
# So this class is a PREDICTOR, not a duplicate suite. It runs what the diff
# implicates and nothing else:
#
#   typed Python touched      -> mypy, CI's own binary and config, on the
#                                changed files
#   a gate surface touched    -> the gate-impact closure: each touched gate's
#                                paired selftest, the enforcement-coverage
#                                check, the baseline co-change check, and the
#                                mechanism-doctrine check when the diff adds a
#                                mechanism
#   impact not classifiable   -> fall back to the FULL gates class locally.
#                                Codex's chair asked for this explicitly and the
#                                direction is deliberate: an unclassifiable gate
#                                change is the case where guessing is worst, so
#                                it buys the 222 seconds rather than assume.
#   always                    -> baseline integrity, path shape, isolation
#
# NOT HERE, on purpose: unit, contract, dependency, binding, artifact, and the
# 252-suite gates web. Zero first-try failures between them across the council's
# classified 30-run window. They all still run hosted, strict, as the gate on
# the merge. That is the wall-clock win and it is the whole point.
#
# EVERY FAILURE NAMES THE MOVE. Also the council's, and it is why each branch
# below prints a remedy rather than only a verdict: a red that says "gates
# failed" and stops has handed the reader a search, not an answer.
check_pushfloor() {
  local failures="" ran=""
  local _floor_t0; _floor_t0="$(date +%s)"

  # The diff this push actually carries. Without a range there is nothing to
  # scope to, so the predictors stay silent rather than inventing a whole-repo
  # verdict — the always-on checks below still run.
  local changed=""
  if [ -n "${CARR_CI_RANGE:-}" ]; then
    changed="$(git diff --name-only --diff-filter=ACMR "$CARR_CI_RANGE" 2>/dev/null || true)"
    # Updating an existing branch imports main's admitted files into the
    # pushed range. Predict only files whose final content/mode differs from
    # main; those identical to main cannot be this branch's change. Keep the
    # original range on an unreadable main, and never narrow CARR_CI_RANGE:
    # the secret scanner must still inspect every blob this push carries.
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

  # ── always: the checks whose failure is far more expensive to find later ──
  run_quiet "$LOGDIR/pushfloor-gate-integrity.log" \
    "$PY" hooks/gate-integrity.py --strict \
    || { tail -12 "$LOGDIR/pushfloor-gate-integrity.log" >&2
         floor_fail gate-integrity \
           "a gate moved without its baseline. Re-bless what you changed and stage it in the SAME commit: python3 hooks/gate-integrity.py --bless <gate> && git add ops/config/gate-baseline.json"; }

  # A single `git ls-files` + pattern match against the whole tracked tree —
  # cheaper than anything else in this class, and the one check that must
  # never be skippable by scoping to $changed: a client deliverable that came
  # back through a revert or a rebase is a violation whether or not THIS push
  # touched deliverables/ (WR-000049, Joe's 2026-09-03 public-repo ruling).
  run_quiet "$LOGDIR/pushfloor-no-client-deliverables.log" \
    "$PY" ops/no-client-deliverables-gate.py \
    || { tail -12 "$LOGDIR/pushfloor-no-client-deliverables.log" >&2
         floor_fail no-client-deliverables \
           "a client-deliverable path is tracked. git rm it — this repo is public and must carry no client record."; }

  if [ -n "$changed" ] && [ -f ops/githooks/path-hygiene-check.py ]; then
    local added
    added="$(git diff --name-only --diff-filter=ACR "$CARR_CI_RANGE" 2>/dev/null || true)"
    # Filename admission depends on whether the path already exists on main,
    # independently of whether this branch changed that file's contents.
    local main_paths=""
    if main_paths="$(git ls-tree -r --name-only origin/main 2>/dev/null)"; then
      added="$(LC_ALL=C comm -23 \
        <(printf '%s\n' "$added" | LC_ALL=C sort -u) \
        <(printf '%s\n' "$main_paths" | LC_ALL=C sort -u))"
    fi
    if [ -n "$added" ]; then
      # shellcheck disable=SC2086
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
      '^(mcp-server/src/|hooks/completion-evidence-gate\.py$)'; then
    ran="$ran registry-coverage"
    run_quiet "$LOGDIR/pushfloor-registry-coverage.log" \
      "$PY" ops/completion-evidence-gate-selftest.py --registry-only \
      || { tail -12 "$LOGDIR/pushfloor-registry-coverage.log" >&2
           floor_fail registry-coverage \
             "classify the registry's new writes in the completion gate and retain read exclusions"; }
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

  # ── predictor: typed Python ──────────────────────────────────────────────
  # CI's `types` class is `mypy pipelines tools exporters lib generators shared
  # fill-engine bin hooks ops` under the repo's mypy.ini. This is the SAME binary
  # and the same config, narrowed to the changed files, so a rule cannot hold in
  # one place and not the other (rule a8c55a47).
  #
  # HONEST LIMIT, and the council said so too (codex: "medium on whether
  # changed-file mypy alone is sufficient"): a change that breaks a caller it
  # does not itself touch is invisible here and dies hosted instead. This is a
  # predictor, not the class.
  local changed_py=""
  if [ -n "$changed" ]; then
    changed_py="$(printf '%s\n' "$changed" | grep -E '\.py$' || true)"
  fi
  if [ -n "$changed_py" ]; then
    local existing_py=""
    local f
    for f in $changed_py; do
      [ -f "$f" ] && existing_py="$existing_py $f"
    done
    if [ -n "$existing_py" ]; then
      local type_rc=0
      # shellcheck disable=SC2086
      run_quiet "$LOGDIR/pushfloor-types.log" ./bin/type-check.sh --files $existing_py || type_rc=$?
      if [ "$type_rc" -eq 78 ]; then
        incomplete
        printf '        \033[33mnot run\033[0m  types — mypy absent; the hosted types class still covers this\n' >&2
      else
        ran="$ran types"
        if [ "$type_rc" -ne 0 ]; then
          tail -20 "$LOGDIR/pushfloor-types.log" >&2
          floor_fail types "mypy on this push's changed files. Iterate with: ./bin/type-check.sh --files$existing_py"
        fi
      fi
    fi
  fi

  # A registry successor changes the active version returned by the inventory
  # graph. The 2026-09-22 v41 successor reached hosted unit before this exact
  # assertion ran locally, costing a full strict-CI cycle. These two focused
  # tests take under a second together and keep the local floor bounded.
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

  # ── predictor: the gate-impact closure ───────────────────────────────────
  local gate_surface=""
  if [ -n "$changed" ]; then
    gate_surface="$(printf '%s\n' "$changed" | grep -E '^(hooks/[^/]+\.py|ops/ci\.sh|ops/githooks/|ops/config/(gate-baseline|rule-enforcement-map|delegation-gate-hook|codex-hooks|model-floors)\.json|ops/[^/]+-selftest\.py)' || true)"
  fi
  if [ -n "$gate_surface" ]; then
    ran="$ran gate-closure"
    local unclassified="" touched_gate base paired

    # Each touched gate's PAIRED selftest. A gate with no pair is the
    # unclassifiable case: we cannot predict its blast radius, so we stop
    # guessing and buy the full class below.
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

    # A touched selftest runs itself, whatever it is paired to.
    for paired in $(printf '%s\n' "$gate_surface" | grep -E '^ops/[^/]+-selftest\.py$' || true); do
      [ -f "$paired" ] || continue
      run_quiet "$LOGDIR/pushfloor-$(basename "$paired").log" "$PY" "$paired" \
        || { tail -15 "$LOGDIR/pushfloor-$(basename "$paired").log" >&2
             floor_fail "$(basename "$paired")" "run it alone: .venv/bin/python $paired"; }
    done

    # The isolation checks are part of the CLOSURE, not the always-on set. They
    # answer "can a selftest reach the real repository" and "does pre-push still
    # strip the git environment before it runs CI" — both of which can only have
    # changed if this push touched a hook, a selftest or ci.sh, which is exactly
    # the condition guarding this block. Running them on a push that edits a
    # markdown file was 4 seconds of asking a question whose answer could not
    # have moved.
    local iso ibase
    for iso in ops/hook-env-isolation-selftest.py ops/selftest-git-isolation-check.py; do
      [ -f "$iso" ] || continue
      ibase="$(basename "$iso")"
      run_quiet "$LOGDIR/pushfloor-$ibase.log" "$PY" "$iso" \
        || { tail -12 "$LOGDIR/pushfloor-$ibase.log" >&2
             floor_fail "$ibase" "run it alone to iterate: .venv/bin/python $iso"; }
    done

    if [ -f ops/enforcement-coverage-check.py ]; then
      run_quiet "$LOGDIR/pushfloor-enforcement-coverage.log" \
        "$PY" ops/enforcement-coverage-check.py \
        || { tail -15 "$LOGDIR/pushfloor-enforcement-coverage.log" >&2
             floor_fail enforcement-coverage \
               "a blessed gate is named by no control. Add it to the enforcement map, or record it in ops/config/enforcement-coverage-backlog.json with a reason."; }
    fi

    # The mechanism-doctrine check reads the change against origin/main, so it
    # is a no-op on a branch that adds no gate, hook, launchd job or scheduled
    # task — which is nearly every branch.
    if [ -f ops/mechanism-doctrine-gate.py ]; then
      run_quiet "$LOGDIR/pushfloor-mechanism-doctrine.log" \
        "$PY" ops/mechanism-doctrine-gate.py \
        || { tail -15 "$LOGDIR/pushfloor-mechanism-doctrine.log" >&2
             floor_fail mechanism-doctrine \
               "a new mechanism must name the doctrine section explaining it (open loop 504 — knowledge ships with the mechanism)."; }
    fi

    # A TOUCHED GATE WITH NO PAIRED SELFTEST IS NAMED, NOT PAID FOR LOCALLY.
    #
    # This branch used to run the WHOLE gates class here. That is a class-scale
    # suite on the push path, and the floor is the only thing between a session
    # and --no-verify, which disables this hook entirely — so an expensive floor
    # costs the cheap checks above it too.
    #
    # Nothing stopped being checked: `gates` is a REQUIRED status check on main
    # (ops/ci.sh --strict), so the class still runs hosted on this exact push
    # before anything merges. Only the payment moved. Name the gate, name the
    # command for anyone who wants the class now, and name the durable fix.
    #
    # Silent when the class is already selected: hosted runs every class, so the
    # note would only advise running something already running.
    if [ -n "$unclassified" ] && [ -n "$ONLY" ] && ! selected gates; then
      ran="$ran gates-deferred"
      incomplete
      printf '        \033[33mdeferred\033[0m   gates — no paired selftest for:%s — the full class runs hosted (required check on main)\n' \
        "$unclassified" >&2
      printf '                   run it locally now: ops/ci.sh --only gates · durable fix: add ops/<gate>-selftest.py\n' >&2
    fi
  fi

  # THE FLOOR'S OWN WALL-CLOCK, printed every run. The council's kill metric for
  # this design is not an opinion about speed — it is the rate at which sessions
  # start passing --no-verify, and that rises when the floor gets slow. A number
  # nobody can see cannot be the thing a ruling turns on, so it is stated here
  # and the class summary carries it.
  local _floor_secs=$(( $(date +%s) - _floor_t0 ))
  if [ -n "$failures" ]; then
    bad pushfloor "failed:$failures (${_floor_secs}s)"
  else
    # The summary names what actually RAN, not a fixed list. It said "baseline,
    # path shape, isolation" even after isolation moved into the diff-scoped
    # closure, which is a green line describing work that did not happen.
    ok pushfloor "${_floor_secs}s · baseline, path shape${ran:+ +}${ran}"
  fi
}

# ---------------------------------------------------------------- dependency
check_dependency() {
  # NOT ${STRICT:+--strict}: STRICT is "0" or "1", and :+ expands on any non-empty
  # value, so "0" would have passed --strict too. Caught while testing the skip path.
  local strictflag=""
  [ "$STRICT" = "1" ] && strictflag="--strict"
  # THE SECOND HALF OF THE SAME DOCTRINE CLASS, IN SHADOW. ci-dep-check.py below
  # reads the LOCKFILES; it never reads an install COMMAND, so a tree with a
  # perfect lock can still install around it (`npm install` re-resolves the lock
  # it was handed; `pip install -r requirements.txt` takes the loose declaration
  # instead of the pinned output beside it). ops/frozen-install-check.py closes
  # that half and belongs in this class rather than a new one.
  #
  # IT RUNS AND REPORTS; IT DOES NOT BLOCK, AND THAT IS DELIBERATE. `ops/ci.sh
  # --strict` is the required status check on main, so failing the class here
  # would make this a REQUIRED hosted gate the moment it merged. Slice R05's
  # authority is explicitly build-only: hosted and required activation belongs to
  # the master plan's CI/CD hardening owner, and the contract change asking for
  # it is out/repo-hygiene-program/r05-contract-change-draft.md. Flipping it is
  # one line — move the `frozen_rc` test into the verdict below — and that line
  # is that owner's to write, not this slice's.
  #
  # SHADOW IS NOT SILENCE. It runs on every local and hosted run and prints what
  # it found, because the failure shape this repo keeps rediscovering is a
  # control that exists, is correct, and is connected to nothing.
  local frozen_rc=0
  run_quiet "$LOGDIR/dep-frozen.log" "$PY" ops/frozen-install-check.py || frozen_rc=$?
  local frozen_note
  if [ "$frozen_rc" -eq 0 ]; then
    frozen_note="$(tail -1 "$LOGDIR/dep-frozen.log")"
  else
    cat "$LOGDIR/dep-frozen.log" >&2
    printf '        \033[33mSHADOW\033[0m  frozen-install-check reports unfrozen install(s) — NOT blocking; activation is the CI/CD hardening owner (see out/repo-hygiene-program/r05-contract-change-draft.md)\n' >&2
    frozen_note="frozen installs: SHADOW FAIL (not blocking) — see above"
  fi

  if run_quiet "$LOGDIR/dep.log" "$PY" ops/ci-dep-check.py $strictflag; then
    ok dependency "$(tail -1 "$LOGDIR/dep.log"); $frozen_note"
  else
    cat "$LOGDIR/dep.log" >&2
    bad dependency "see above; $frozen_note"
  fi
}

# ---------------------------------------------------------------- migration
# HARD SAFETY PROPERTY: this refuses any DSN whose host is not loopback. The
# check applies 130 forward migrations to a throwaway database; pointed at Neon
# it would apply them to the live record layer. Making localhost a structural
# requirement means no environment variable, typo or copied DSN can aim it at
# production. There is no override flag on purpose.
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
    *) hard migration "REFUSED: CARR_CI_DATABASE_URL is not loopback. This applies every migration; it runs against a throwaway only."
       return ;;
  esac
  # WHAT THIS ASKS, changed 2026-08-13. It used to replay all 121 migrations
  # against an empty database, which CANNOT WORK and is also the wrong question.
  # Several migrations are data backfills carrying guards like "remapped ZERO
  # deals — stop and report, do not force"; those guards are correct, they catch
  # a backfill that silently did nothing to production, and they assert on
  # business data an empty database legitimately does not have.
  #
  # It now loads db/schema.sql — production's committed structure plus its
  # applied-migration ledger — and applies whatever is PENDING on top. That is
  # the question worth gating a change on: does the migration in this diff apply
  # cleanly to the database we actually have? Replaying history tests history.
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
    # SAY WHICH FAILURE THIS IS. "The committed structure is not loadable" is a
    # serious claim and was, until 2026-08-22, also what a session saw for the
    # entirely ordinary case of pointing CARR_CI_DATABASE_URL at a database that
    # ALREADY HAS the schema. This class loads db/schema.sql from scratch, so a
    # second run against the same database always fails on "already exists" —
    # and the message accused the repository instead. A session chasing that on
    # its own hand-built cluster hit it repeatedly in one night.
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

  # The timeline verbs run as carr_writer; prove that role can read every view
  # their real handlers touch (the 42501 that failed r-2026-10-03-01).
  if ! CARR_WRITER_READ_TEST_DATABASE_URL="$dsn" run_quiet "$LOGDIR/migration-writer-read-route.log" \
      node --test mcp-server/test/catch-me-up-writer-route.test.mjs; then
    tail -30 "$LOGDIR/migration-writer-read-route.log" >&2
    bad migration "timeline verbs cannot read their views as carr_writer"
    return
  fi

  # Tour Operations carries database-owned rights, identity, route, digest,
  # ACL, and append-only invariants that cannot be proved by text-shape tests.
  # The DoctorCRE v5 portfolio proof joins the same loop for the same reason:
  # its digest recomputation, append-only guards and post-acceptance freeze are
  # database behaviour, and a regex over the migration would prove none of it.
  # Run every slice's transaction-scoped acceptance proof on the same
  # disposable database after pending migrations apply. Each proof rolls back
  # every fixture row and must be independently green.
  _mstep migrate
  if ! CARR_INVOICE_TEST_DATABASE_URL="$dsn" CARR_INVOICE_TEST_REQUIRED=1 \
       run_quiet "$LOGDIR/invoice-tracker-transaction.log" \
       node --test mcp-server/test/invoice-tracker-transaction.test.mjs; then
    tail -30 "$LOGDIR/invoice-tracker-transaction.log" >&2
    bad migration "the registered invoice receipt replay and atomic rollback proof failed"
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

  # V5-J103: consent is read-only and limited to each partner's own carr.us
  # mailbox, drafts can never dispatch, receipts keep provenance, and no runtime
  # role can write a read receipt yet.
  if ! run_quiet "$LOGDIR/governed-correspondence-store-postgres.log" \
       "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
       -f mcp-server/test/governed-correspondence-store-postgres.sql; then
    tail -30 "$LOGDIR/governed-correspondence-store-postgres.log" >&2
    bad migration "V5-J103 governed correspondence store PostgreSQL acceptance failed"
    return
  fi

  # V5-F01 record homes, source authority and document identity: both SQL
  # fixtures (each on its own template copy of this database) and the nine
  # registered verbs end to end as carr_writer and the authority login. It
  # commits only into the copies it creates and drops them on the way out.
  if [ -f mcp-server/test/record-source-authority-live-pg.v5.test.mjs ]; then
    if ! DATABASE_URL="$dsn" CARR_F01_DB_REQUIRED=1 PSQL="$psql_bin" \
         run_quiet "$LOGDIR/record-source-authority-live-pg.log" \
         node --test mcp-server/test/record-source-authority-live-pg.v5.test.mjs; then
      tail -40 "$LOGDIR/record-source-authority-live-pg.log" >&2
      bad migration "V5-F01 record-source-authority PostgreSQL acceptance failed"
      return
    fi
  fi

  # 0732: the F01/J102/RW02 actor gate as the Worker's REAL login shapes
  # (app_writer in carr_writer, app_reader in carr_reader), not the NOLOGIN
  # bundles every other fixture impersonates. It also covers the negative cast:
  # exporter, owner-shaped, jobs-holding and unrelated logins stay refused, and
  # the reader cannot write. One rolled-back transaction, role creation
  # included.
  if [ -f mcp-server/test/f01-login-bundle-membership-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/f01-login-bundle-membership-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/f01-login-bundle-membership-postgres.sql; then
      tail -30 "$LOGDIR/f01-login-bundle-membership-postgres.log" >&2
      bad migration "the F01 login-bundle membership proof (0732) failed"
      return
    fi
  fi

  # V5-F05: the typed contract binder and actor-scoped universe census need a
  # real database. The unit class exercises the runtime adapter with a fake
  # client; this lane proves append-only persistence, idempotent replay,
  # server-derived rule identity/provenance, and explicit missing-rule census
  # against the migrated schema.
  if [ -f mcp-server/test/rule-context-runtime-postgres.test.mjs ]; then
    if ! DATABASE_URL="$dsn" CARR_F05_DB_REQUIRED=1 \
         run_quiet "$LOGDIR/rule-context-runtime-postgres.log" \
         node --test mcp-server/test/rule-context-runtime-postgres.test.mjs; then
      tail -40 "$LOGDIR/rule-context-runtime-postgres.log" >&2
      bad migration "V5-F05 rule-context PostgreSQL acceptance failed"
      return
    fi
  fi

  # Continuity bindings and append-only records need actual PostgreSQL proof.
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

  # THE GATE ZERO CANDIDATE-KEY RACE, added 2026-09-13 (PR 1014 correction).
  # ops.gate_zero_record_read_only_outcome collapses a retry onto the row that
  # exists, and whether it does so under CONCURRENCY is a property of one unique
  # index and two transactions -- a mock can show what the handler sends and
  # nothing about that. Two connections, one candidate, one durable row; the
  # earlier lookup-then-insert shape fails this with a unique_violation, which is
  # what makes it a falsifier rather than a demonstration.
  #
  # IT COMMITS, deliberately: an uncommitted race proves nothing, and the record
  # is append-only by trigger so the rows stay. This class already builds and
  # removes a throwaway database, which is the only place that is acceptable --
  # and the proof refuses a DSN that is not loopback on its own account.
  #
  # NOTHING AFTER THIS POINT READS ops.gate_zero_read_only_outcome, so it runs
  # last among the proofs rather than first.
  # THE GATE ZERO SEAT BOUNDARY, added 2026-09-14 (PR 1014 third correction).
  # Standing-rule amendment 9: seat-only write is enforced by CONNECTION ROLE.
  # Whether carr_writer is refused, whether the dedicated login role is admitted,
  # and whether re-granting carr_writer turns the privilege proof red are three
  # properties of real grants and a real session_user -- none of which a mock can
  # show. It runs BEFORE the race proof: it creates the dedicated login role that
  # proof now needs, and its own mutation control restores the revoke before it
  # returns, which it asserts rather than assumes.
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

  # WR-000110. The V5-F02 program-controller seam boundary: which connection
  # role may record which fact kind, and whether the column-scoped SELECT grants
  # actually reach the writer bundle the admission door connects as. Both are
  # properties of real grants and a real session_user, which no mock can show.
  if [ -f mcp-server/test/program-controller-census-role-boundary.v5.test.mjs ]; then
    if ! DATABASE_URL="$dsn" CARR_PROGRAM_CONTROLLER_DB_REQUIRED=1 \
         run_quiet "$LOGDIR/program-controller-role-boundary.log" \
         node --test mcp-server/test/program-controller-census-role-boundary.v5.test.mjs; then
      tail -30 "$LOGDIR/program-controller-role-boundary.log" >&2
      bad migration "the program-controller seam connection-role boundary proof failed"
      return
    fi
  fi

  # The measured catalog-delta proof: exactly two secdef rows for the privileged
  # writer and none for public, no relation or column DML row naming any of the
  # seven new tables, and four rows for the one v29 registration function.
  if [ -f mcp-server/test/program-controller-census-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/program-controller-census-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/program-controller-census-postgres.sql; then
      tail -30 "$LOGDIR/program-controller-census-postgres.log" >&2
      bad migration "the program-controller seam catalog and ledger proof failed"
      return
    fi
  fi

  # WR-000111/112/113: the three acceptance suites that need a real database.
  # Each one SKIPS in the unit class and is REQUIRED here, so a silent skip in
  # the lane that can actually prove them is a failure rather than a pass.
  # WR-000117 joins the same loop for the same reason: its identity cases mint
  # TWO actors and compare two answers, which only a database can do.
  # WR-000119 joins it too: its centre case mints a link with NO ack beside a
  # dispatch with no link at all and asserts the two nulls are different, which
  # is a claim about rows and not about a shaper.
  # V5-UX-B11 Meeting Mode joins it: two devices, one row, one acceptance and
  # one processing owner are claims about real locks on separate connections.
  # V5-A05 delivery-cadence joins it: the review that shipped it required a
  # real-DB proof rather than the unit-stub coverage that let the
  # morning-brief grant gap and the caller-evidence gap both through once.
  # amend-closed-loop joins it: the append-only correction trail (loop_amendment,
  # migration 0702) and trg_touch_row's version bump are claims about rows a
  # fake client cannot make honestly.
  # V5-D01 action-class-successor-registry joins it: the status CHECK
  # constraint, the append-only immutability trigger and the unconditional
  # gate function are claims about real rows and real constraints a fake
  # client cannot make honestly.
  # V5-A03 joins it: complete eleven-dimension submissions, non-shrinking
  # regression evidence, role/session separation, the two-round ceiling and
  # stronger adjudication are all transactional claims over append-only rows.
  # V5-A02 rule-enforcement coverage joins it: its first review found every
  # SQL logic mutant surviving a FakeDb suite, so the coverage function, the
  # Joe-authority fallback writer and their append-only guards are proved here
  # on real rows as the real principals, and a skip is a failure.
  # V5-RW02 joins it: the evidence store's replay projection, its typed
  # conflict and its write-time refusal of malformed or double-counted
  # readback evidence only exist on real rows as carr_writer.
  for proof in cost-ledger-projection.v5 doc-conversation notifications session-identity dispatch-spine meeting-mode delivery-cadence-a05-tools amend-closed-loop-postgres action-class-successor-registry-postgres independent-review-cycle-postgres a02-rule-enforcement-postgres salesforce-reconciliation-rw02-postgres salesforce-read-run-store-rw02-postgres vendor-directory-postgres relationship-network-postgres; do
    if [ -f "mcp-server/test/$proof.test.mjs" ]; then
      if ! DATABASE_URL="$dsn" CARR_COST_LEDGER_DB_REQUIRED=1 \
           CARR_DOC_CONVERSATION_DB_REQUIRED=1 CARR_R03_DB_REQUIRED=1 \
           CARR_SESSION_IDENTITY_DB_REQUIRED=1 CARR_DISPATCH_SPINE_DB_REQUIRED=1 \
           CARR_MEETING_MODE_DB_REQUIRED=1 CARR_AMEND_CLOSED_LOOP_DB_REQUIRED=1 \
           CARR_ACTION_CLASS_SUCCESSOR_DB_REQUIRED=1 \
           CARR_V5_A03_DB_REQUIRED=1 \
           CARR_A02_RULE_COVERAGE_DB_REQUIRED=1 \
           CARR_RW02_DB_REQUIRED=1 \
           CARR_VENDOR_DIRECTORY_DB_REQUIRED=1 CARR_RELATIONSHIP_DB_REQUIRED=1 \
           run_quiet "$LOGDIR/$proof-db.log" \
           node --test "mcp-server/test/$proof.test.mjs"; then
        tail -30 "$LOGDIR/$proof-db.log" >&2
        bad migration "the $proof database acceptance proof failed"
        return
      fi
    fi
  done

  # WR-000111: the producer cost ledger's role-boundary and catalog proof --
  # the compare-and-swap refusal from TWO direct authority callers, the refusal
  # row that writes zero entries, and the revoked direct writes.
  if [ -f mcp-server/test/producer-cost-ledger-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/producer-cost-ledger-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/producer-cost-ledger-postgres.sql; then
      tail -30 "$LOGDIR/producer-cost-ledger-postgres.log" >&2
      bad migration "the producer cost ledger admission and grant proof failed"
      return
    fi
  fi

  # WR-000112: server-derived attribution and the access list, proved on the
  # connection each grant actually names.
  if [ -f mcp-server/test/doc-conversation-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/doc-conversation-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/doc-conversation-postgres.sql; then
      tail -30 "$LOGDIR/doc-conversation-postgres.log" >&2
      bad migration "the Doc conversation attribution and access-list proof failed"
      return
    fi
  fi

  # WR-000114: the three Doc conversation write doors, proved where only a
  # database can prove them -- the creator rule raised INSIDE the definer under
  # a second acting-actor context (a check written in the JavaScript handler is
  # bypassed by exactly this call), the revoke that stamps rather than deletes,
  # and the execute grants on the connection each verb actually arrives on.
  if [ -f mcp-server/test/doc-conversation-write-doors-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/doc-conversation-write-doors-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/doc-conversation-write-doors-postgres.sql; then
      tail -30 "$LOGDIR/doc-conversation-write-doors-postgres.log" >&2
      bad migration "the Doc conversation write-door creator and grant proof failed"
      return
    fi
  fi

  # WR-000115: the Doc conversation list door, proved where only a database can
  # prove it -- the returned SET following the acting-actor context set directly
  # rather than any argument, the three-part cursor surviving a pin that lands
  # between page one and page two (the case a two-part cursor fails), and the
  # execute grant on the exact argument types.
  if [ -f mcp-server/test/doc-conversation-list-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/doc-conversation-list-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/doc-conversation-list-postgres.sql; then
      tail -30 "$LOGDIR/doc-conversation-list-postgres.log" >&2
      bad migration "the Doc conversation list attribution, paging and grant proof failed"
      return
    fi
  fi

  # WR-000113: the mint's grant from both sides, the recipient resolver on the
  # WRITER login, the no-sponsor no-op, quiet hours and the status boundary.
  if [ -f mcp-server/test/r03-notifications-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/r03-notifications-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/r03-notifications-postgres.sql; then
      tail -30 "$LOGDIR/r03-notifications-postgres.log" >&2
      bad migration "the R03 notification mint, recipient and status proof failed"
      return
    fi
  fi

  # V5-R03 action-needed producer: recipient routing, recovery standing,
  # deferred exclusion and replay dedupe in a rolled-back transaction.
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

  # WR-000116: defaults without an insert, the compare-and-swap, the first save,
  # the replay, the two named refusals and quiet_now across a midnight
  # wrap-around in a zone the proof's own SQL chooses.
  if [ -f mcp-server/test/notification-preferences-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/notification-preferences-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/notification-preferences-postgres.sql; then
      tail -30 "$LOGDIR/notification-preferences-postgres.log" >&2
      bad migration "the notification preference read, write and quiet-hours proof failed"
      return
    fi
  fi

  # WR-000117: the permission filter asserted PER BOOK across four books with
  # four different owner columns, the count comparison, a root distinguished
  # from an unrecorded parent, the age rule computed from each row's own
  # timestamp, the two proven dispatch stages with the named unavailable
  # reason, an opaque cursor that neither skips nor repeats, and no row count
  # moved by either read.
  if [ -f mcp-server/test/session-identity-postgres.sql ]; then
    if ! run_quiet "$LOGDIR/session-identity-postgres.log" \
         "$psql_bin" -X -v ON_ERROR_STOP=1 -d "$dsn" \
         -f mcp-server/test/session-identity-postgres.sql; then
      tail -30 "$LOGDIR/session-identity-postgres.log" >&2
      bad migration "the session identity projection, filter and dispatch proof failed"
      return
    fi
  fi

  # WR-000119: the dispatch spine proved where only a database can prove it --
  # append-only by GRANT rather than by convention, one link per assignment and
  # none for a turn never assigned, the hermes-pilot restriction raised inside
  # the definer, a dangling ack refused by the reference, a second ack of one
  # stage refused by the unique constraint, all four stages evidenced in one
  # answer, proved distinguished from body_match, not_acknowledged distinguished
  # from no_dispatch_spine per row, and public.partner_room_turn never written.
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

  # THE TAGGED OUTCOME DIGEST, added 2026-09-13 (the third release candidate's
  # refusal, finding 2). r7's amended receipt_payload_digest_rule declares the
  # payload digest of consumer-gate-receipt.v1 as digest(["consumer-gate-receipt.v1",
  # receipt]) and states that a plain digest over the receipt does not satisfy
  # it. Whether ops.gate_zero_outcome_digest() and artifact-trust.js produce the
  # SAME BYTES over that preimage is a question about two canonicalizers, one in
  # plpgsql; a mock that called the JS one would agree with it by construction and
  # prove nothing. It runs after the race proof because it records a row.
  if [ -f mcp-server/test/gate-zero-outcome-digest-tagged.test.mjs ]; then
    if ! DATABASE_URL="$dsn" CARR_GATE_ZERO_RACE_REQUIRED=1 \
         run_quiet "$LOGDIR/gate-zero-digest-tagged.log" \
         node --test mcp-server/test/gate-zero-outcome-digest-tagged.test.mjs; then
      tail -30 "$LOGDIR/gate-zero-digest-tagged.log" >&2
      bad migration "the Gate Zero tagged outcome-digest cross-implementation proof failed"
      return
    fi
  fi

  # The real release recorder files service candidates with carr_jobs provenance.
  # This proof also checks that the historical Gate Zero reader accepts only a
  # separately authenticated human-authority row. Both paths use real grants
  # and a real manifest on a disposable database.
  if [ -f mcp-server/test/gate-zero-candidate-authority-filing.test.mjs ]; then
    if ! DATABASE_URL="$dsn" CARR_GATE_ZERO_RACE_REQUIRED=1 \
         run_quiet "$LOGDIR/gate-zero-candidate-filing.log" \
         node --test mcp-server/test/gate-zero-candidate-authority-filing.test.mjs; then
      tail -30 "$LOGDIR/gate-zero-candidate-filing.log" >&2
      bad migration "the service-candidate and historical Gate Zero provenance proof failed"
      return
    fi
  fi

  # THE GRANTS CANARY, added 2026-08-14. The snapshot is pg_dump --no-acl, so
  # for months this class built a database where the app roles existed and held
  # NOTHING — has_table_privilege() false for every table, every role — and ran
  # green anyway, because nothing ever asked. PR #75 paid for that: verifying
  # carr_writer's insert on lead meant replaying 0001-0004 by hand. The CARR
  # GRANTS section in db/schema.sql now carries the privileges; this asks the
  # LIVE database whether they actually attached, which no text check on the
  # snapshot can prove. One positive per grant shape, plus 0117's negative —
  # the interlock where the ABSENT column is the point. A flattening bug that
  # turned column grants into table grants would pass every positive and be
  # caught only there.
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
    # grep -c prints "0" AND exits nonzero on no match, so `|| echo 0` printed
    # a second zero. `|| :` keeps the count grep already printed.
    n="$(grep -c '^applying ' "$LOGDIR/migration.log" 2>/dev/null || :)"

    # THE TRIGGER-READ CHECK (rule 5409731b). The canary above proves the
    # declared grants attached; this asks the question that broke set-lead for
    # five days in production: can the role that WRITES a table actually SELECT
    # every table the trigger on it READS? An invoker-rights trigger runs as the
    # caller, and grants never fire for the owner, so no rehearsal as owner can
    # see it — which is why this lives here, against the built database, rather
    # than in the gates class against a synthetic tree.
    _mstep grants
    if ! CARR_CI_DATABASE_URL="$dsn" run_quiet "$LOGDIR/migration-triggers.log" \
         "$PY" ops/trigger-grant-check.py; then
      tail -25 "$LOGDIR/migration-triggers.log" >&2
      bad migration "a trigger reads a table its firing role cannot select"
      return
    fi

    # Migration 0176 deliberately creates its FK-bound scheduler surface
    # registry empty.  The real authority sync validates the complete
    # checked-in inventory and writes it only after job definitions exist.
    # Do that real sync on this disposable database before acceptance gates;
    # copied seed SQL would bypass exactly the ordering this class must prove.
    _mstep triggers
    if ! DATABASE_URL="$dsn" run_quiet "$LOGDIR/migration-control-plane-sync.log" \
         "$PY" tools/control-plane.py sync; then
      tail -30 "$LOGDIR/migration-control-plane-sync.log" >&2
      bad migration "control-plane registry sync failed after migrations"
      return
    fi

    # ── THE DATABASE ACCEPTANCE GATES ────────────────────────────────────────
    # Three of these existed before this loop did and NOTHING RAN ANY OF THEM.
    # ops/program3-trace-gate.py opens by calling itself "the acceptance test
    # for Program 3", written before the thing it tests — and its only stated
    # runner was a db-tap command a human types. A gate nobody runs is a
    # document with assertions in it (rule ab814a26: a rule ships with its
    # enforcement, and recitation is not enforcement).
    #
    # This class already stands up the one thing they need: a throwaway
    # database with the committed schema loaded and every pending migration
    # applied. So they run here, on every proposed change, against real
    # Postgres. MOST gates roll back everything they write, which is why
    # running them repeatedly costs nothing.
    #
    # ONE DOES NOT, AND THE BLANKET CLAIM THAT USED TO STAND HERE WAS FALSE
    # (2026-08-22, found while building the snapshot authority-membership
    # gate). ops/calendar-prebrief-projection-local-pg-gate.py creates twelve
    # login roles — including carr_authority_joe and carr_authority_dell — and
    # COMMITS. That is deliberate and it is not a leak: the gate proves a
    # concurrency property with two real peer connections authenticating as
    # different roles and racing in threads, which cannot be done inside one
    # transaction, and it hard-refuses to run unless the database is a
    # dedicated disposable carr_ci superuser database.
    #
    # THE CONSEQUENCE IS WHAT MATTERS TO ANYONE WRITING A GATE. The glob below
    # is alphabetical, so every gate ordered after `calendar-prebrief-p...`
    # runs on a substrate where both human authority login roles already exist
    # and already hold the carr_authority bundle. A gate that asserts an
    # authority privilege there is asserting it under conditions it did not
    # establish and cannot see — the same shape as a proof that passes because
    # it happens to run as the owner. Establish the role state you depend on
    # inside your own rolled-back transaction rather than inheriting whatever
    # the gate before you left; ops/snapshot-authority-membership-gate.py
    # manufactures absence that way for exactly this reason, after an earlier
    # revision of it reported differently depending on its position.
    #
    # SELF-REGISTERING, by the marker `# ci: db-gate` in the file itself. A new
    # gate is wired by writing it, not by remembering to edit this list.
    #
    # WHAT COUNTS AS A DB-GATE, and why the old test was wrong (open loop 503,
    # item 1, 2026-08-22). This used to flag any ops/*-gate.py MENTIONING the
    # string DATABASE_URL. Two files mention it without ever consuming it:
    # ops/p1-rebuild-gate.py and ops/p1-integration-gate.py only SET it in the
    # environment of subprocesses they point at a Neon branch they created
    # themselves. So both were named "not run" on every single push — a true
    # sentence reached by a wrong test, for gates that could never take CI's
    # throwaway DSN in the first place.
    #
    # THE COST OF GETTING IT WRONG THIS WAY. A yellow line that prints on every
    # push and names something CI genuinely cannot fix is invisible within a
    # week, and it is invisible in exactly the way the comment above warns
    # about. The heuristic was importing a gate's real problem into the wrong
    # lane and then shrugging at it in colour.
    #
    # So the test is now an actual READ of a DSN from the environment. Checked
    # against all 32 gates when this was written, it agrees with the `# ci:
    # db-gate` marker exactly: 25 marked, 25 reading, no gate on either side
    # alone. That agreement is what makes the two rules below safe.
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
        # A gate that reads a DSN and carries no marker really is unrun, and
        # this is now a FAILURE rather than a line in the margin. The whole
        # lesson of the three gates that prompted this loop is that nobody
        # reads a warning; the only reliable way to make an unrun gate visible
        # is to stop the push that added it.
        db_gate_unmarked="$db_gate_unmarked $(basename "$g")"
      elif grep -q '^# ci: runs-outside-ci' "$g"; then
        # Declared, not forgotten. Printed with its stated reason so the state
        # is legible rather than merely quiet — a gate that runs nowhere should
        # say so out loud every time, in its own words.
        db_gate_declared="$db_gate_declared\n            $(basename "$g"): $(sed -n 's/^# ci: runs-outside-ci *— *//p' "$g" | head -1)"
      fi
    done
    if ! DATABASE_URL="$dsn" "$PY" ops/ci-migration-gates.py --logdir "$LOGDIR" $db_gates; then
      db_gate_failures="isolated/serial DB gate runner (see named failures above)"
    fi
    _mstep gates
    # Two greppable lines, same contract as ci-timing: which STEP of this class
    # grew, and which GATE PROGRAM grew. Seconds, sorted slowest first. Added
    # 2026-09-01 when the class went 77s -> 652s on hosted runners in one day
    # while reproducing at 48s on a Mac, and nothing in the hosted log could
    # say which of the ~60 silent children was responsible.
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

    # THE SHADOW RUN (gap #18, 2026-10-05). Once db/schema.sql must carry every
    # migration, nothing is pending above it, so the load above cannot test a
    # new migration. ops/migration-shadow.py starts from the BASE branch's
    # snapshot (production's structure) on its own PostgreSQL 18 cluster,
    # applies this change's migrations, and requires the committed snapshot to
    # be exactly the result. PostgreSQL 18 because production runs 18; a 17
    # server drops production's named NOT NULL constraints from the dump.
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

# ---------------------------------------------------------------- binding
# Worker config and installed-hook wiring. config-as-code compares the live
# Claude Code settings against the repo's declared baseline; on a runner there is
# no live settings file, so that half skips and the wrangler half still runs.
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
  # A NEW DEFECT CLASS (DoctorCRE V5-R02 review, PR #1245, 2026-09-24): code
  # that bundles clean and passes every Node-side test, but throws at MODULE
  # LOAD in workerd because it called a Node-only API (fileURLToPath,
  # execFileSync) at top level. Nothing above this line ever runs the Worker
  # in a Worker runtime, so nothing above catches it. bin/worker-boot-check.sh
  # boots the real Worker in local workerd (same wrangler binary
  # bin/deploy-worker.sh ships with) and asks the dependency-free /healthz
  # route for a 200 -- proof the module graph finished loading, not proof of
  # correctness (mcp-server's own test suite owns that). Skipped, not failed,
  # when wrangler's npm install has not happened here: this is the SAME
  # posture as the mypy skip below for a machine that has not installed a
  # pinned dependency, and a hard failure here would refuse every push on a
  # machine that has simply never run `npm install` in mcp-server/.
  if [ -x mcp-server/node_modules/.bin/wrangler ]; then
    if ! run_quiet "$LOGDIR/binding-worker-boot.log" ./bin/worker-boot-check.sh; then
      problems="$problems worker-boot"
      tail -40 "$LOGDIR/binding-worker-boot.log" >&2
    fi
  else
    printf '        \033[33mskip\033[0m  worker-boot-check — wrangler not installed (run npm install in mcp-server/)\n' >&2
  fi
  # CONFIG-AS-CODE IS SCOPED TO BRANCHES THAT ARE ACTUALLY IN THAT BUSINESS.
  #
  # This check compares the LIVE MACHINE — ~/.claude/settings.json and the
  # installed launchd plists — against the repo's declarations. That makes it
  # the one check here whose subject is not the branch. Any session installing
  # a job on this Mac puts every OTHER session's push into drift, and the
  # drifting branch cannot fix it: capturing the machine into an unrelated pull
  # request means committing somebody else's in-flight work, which rule
  # 308ef1de forbids and the git-writer gate blocks outright. The only remaining
  # move was CARR_SKIP_CI=1, which skips the nine checks that WERE working.
  #
  # Observed twice on 2026-08-22, hours apart, on two different jobs, neither
  # belonging to the branch being pushed. Same class the council ruled that day:
  # a machine-global condition may open a loop, never veto unrelated work — the
  # same reasoning that scoped tools/next-migration.py to the caller's own tree.
  #
  # So: drift still RUNS and is still printed in full on every push, because a
  # silent drift is how five gates were off for a day in August. It fails the
  # class only when this branch touches the declarations it is about. A branch
  # that does change them is squarely in this business and must reconcile.
  if [ -f "$HOME/.claude/settings.json" ]; then
    if ! run_quiet "$LOGDIR/binding-config.log" "$PY" ops/config-as-code.py; then
      # OWNED, NOT MERELY ADJACENT. This used to ask whether the branch had
      # touched ANY declaration path and, if so, make every drifting item on the
      # machine fatal — including items in the other family that nobody on this
      # branch had seen. On 2026-08-22 a one-word comment repair to a launchd
      # file (a double hyphen inside an XML comment, which strict parsers refuse
      # and plutil accepts) made two unrelated scheduled tasks another session
      # had installed into that branch's problem, and it could not fix them
      # without committing somebody else's in-flight work. The matching rule
      # lives in ops/config_drift_ownership.py so this shell and its selftest ask
      # the same question (rule a8c55a47).
      local cfg_changed cfg_owned
      cfg_changed="$(git diff --name-only origin/main...HEAD 2>/dev/null || true)"
      tail -15 "$LOGDIR/binding-config.log" >&2
      # shellcheck disable=SC2086
      cfg_owned="$("$PY" ops/config_drift_ownership.py $cfg_changed \
        < "$LOGDIR/binding-config.log" 2>/dev/null || true)"
      if [ -n "$cfg_owned" ]; then
        problems="$problems config-as-code"
        printf '        this branch touches the drifting declaration(s), so they are yours:\n%s\n' "$cfg_owned" >&2
      else
        machine_drift="yes"
        # SAY WHAT IS ACTUALLY TRUE. This line used to read "this branch changes
        # no declaration", which was true only while the test was directory-wide.
        # A branch CAN now touch a declaration and still own none of the drift,
        # and printing the old sentence there would be a comment left above a
        # changed line — the exact shape that let five nightly steps report
        # "skipped" for days while their explanatory text said they ran.
        printf '        \033[33mnot fatal here\033[0m  config-as-code drift is on the MACHINE and this branch touches\n' >&2
        printf '        none of the drifting declarations above. Reported, not vetoed —\n' >&2
        printf '        run `ops/config-as-code.py pull` on a branch that owns the change.\n' >&2
      fi
    fi
  fi
  if [ -n "$problems" ]; then
    bad binding "drift:$problems"
  elif [ -n "$machine_drift" ]; then
    # NEVER say the wiring matches when it does not. A class that reports a
    # drift and then prints "installed wiring matches repo" is the false-green
    # shape this repo has been bitten by repeatedly; the scoping decides whether
    # drift BLOCKS THIS BRANCH, never whether it is true.
    ok binding "worker config declared; MACHINE config-as-code drift reported above, not owned by this branch"
  else
    ok binding "worker config declared; installed wiring matches repo"
  fi
}

# ---------------------------------------------------------------- artifact
# The loop #276 guard, moved earlier in the pipeline. At deploy time this same
# helper refuses a shrink; here it refuses a MERGE that would cause one, so the
# verb loss is caught before it is a release.
check_artifact() {
  local shipping marker
  if ! run_quiet "$LOGDIR/doctorcre-artifact.log" "$PY" \
      tools/release-manifest.py doctorcre-artifact verify \
      --pin ops/config/doctorcre-artifact.v1.json; then
    tail -20 "$LOGDIR/doctorcre-artifact.log" >&2
    bad artifact "the exact DoctorCRE release artifact did not match its CARR pin"
    return
  fi
  shipping="$(sh ops/verb-count.sh "$REPO/mcp-server" 2>"$LOGDIR/artifact.log")"
  if [ -z "$shipping" ]; then
    cat "$LOGDIR/artifact.log" >&2
    bad artifact "registry did not import — refusing to call it unmeasured-but-fine"
    return
  fi
  # THE BASELINE COMES FROM THE LEDGER, NOT A FILE — defect d737c09c, 2026-08-16.
  # This line read mcp-server/.last-deployed-verb-count until 2026-08-21, five
  # days after the deploy stopped writing it. The file is gitignored and nothing
  # creates it any more, so `marker` was always empty, the shrink comparison was
  # never once performed, and the class still printed OK: "no loss against
  # deployed none" — a loss guard asserting no loss having compared against
  # nothing. The shell also reported the failed redirect on every run, because
  # `2>/dev/null` silences tr and not the `<` that fails before tr starts.
  #
  # ops/last-deployed-verb-count.py is the one query, and its header names this
  # caller: "the deploy asks this, and so can CI or a human. A second copy of the
  # query in shell would be free to drift." This is that drift, closed.
  local rc=0
  marker="$("$PY" ops/last-deployed-verb-count.py carr-mcp production 2>/dev/null)" || rc=$?
  case "$rc" in
    0)
      if [ -n "$marker" ] && [ "$shipping" -lt "$marker" ]; then
        bad artifact "would REMOVE $((marker - shipping)) verb(s): deployed $marker, tree has $shipping"
      else
        ok artifact "$shipping verbs, no loss against deployed $marker"
      fi
      ;;
    3)
      ok artifact "$shipping verbs; no prior deployment recorded, this would set the baseline"
      ;;
    78)
      # NO LEDGER CREDENTIAL. In the GitHub runner that is BY DESIGN — ci.yml
      # grants a throwaway loopback Postgres and sets CARR_CI_PORTABLE_ONLY=1,
      # and no production credential belongs on a PR runner. Saying SKIP there
      # would fail every pull request under --strict, so the portable runner gets
      # a named exclusion instead. Anywhere else a missing credential is a real
      # gap in coverage and reports SKIP, which --strict escalates. Either way
      # this NEVER claims "no loss" — bin/deploy-worker.sh runs the same guard
      # against the same ledger and fails closed, so the deploy is where a shrink
      # is actually stopped.
      if [ "${CARR_CI_PORTABLE_ONLY:-0}" = "1" ]; then
        incomplete
        ok artifact "$shipping verbs; shrink guard not run here (portable runner has no ledger credential — the deploy enforces it)"
      else
        skip artifact "$shipping verbs counted, but no ledger credential — the shrink comparison did not run"
      fi
      ;;
    *)
      bad artifact "could not read the deployed verb count from the ledger (exit $rc) — refusing to call it unmeasured-but-fine"
      ;;
  esac
}

# ---------------------------------------------------------------- run
echo "carr-system CI — $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"
[ "$STRICT" = "1" ] && echo "  strict: a SKIP counts as a failure"
echo

# EVERY name is validated, not just the first. A typo inside a list would
# otherwise select the classes it spelled right and silently drop the rest --
# a suite reporting green because nothing was looking, which is the exact shape
# of the 2026-08-13 runaway-job finding this file already guards elsewhere.
for _want in $ONLY; do
  if [ -z "$(class_desc "$_want")" ]; then
    echo "ci.sh: no such class: $_want (try --list)" >&2
    exit 64
  fi
done

# Does $1 appear in the (space-separated) $ONLY list? Padded on both sides so
# "secret" cannot match a class named "secrets".
selected() {
  case " $ONLY " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

for c in $CLASS_ORDER; do
  if [ -n "$ONLY" ] && ! selected "$c"; then continue; fi
  # Snapshot the class name: check functions reuse the global `c` as their own
  # loop variable (check_migration's psql probe at least), so after "check_$c"
  # returns, $c may name whatever that inner loop ended on. The very first CI
  # run of the timing line proved it, printing `psql=32s` where migration
  # belonged. Restoring `c` also keeps anything after this loop honest.
  _class_name="$c"
  _class_t0="$(date +%s)"
  _class_failed=$FAILED
  _class_skipped=$SKIPPED
  _class_ran=$RAN
  _class_incomplete=$INCOMPLETE
  "check_$c"
  _class_status=passed
  [ "$SKIPPED" -gt "$_class_skipped" ] && _class_status=partial
  [ "$INCOMPLETE" -gt "$_class_incomplete" ] && _class_status=partial
  [ "$FAILED" -gt "$_class_failed" ] && _class_status=refused
  CLASS_RESULTS="$CLASS_RESULTS$_class_name $_class_status $((RAN - _class_ran))
"
  CLASS_TIMINGS="$CLASS_TIMINGS $_class_name=$(( $(date +%s) - _class_t0 ))s"
  c="$_class_name"
done

# A receipt consumer reads these measured outcomes, never the final exit code
# alone (known gaps can exit zero while a class remains red).
if [ -n "$RESULT_FILE" ]; then
  "$PY" - "$RESULT_FILE" "$STRICT" "$CLASS_RESULTS" <<'PYRESULT'
import json, os, sys, tempfile
from pathlib import Path
target = Path(sys.argv[1]).resolve()
rows = [line.split() for line in sys.argv[3].splitlines() if line.strip()]
data = {"schema": "carr-ci-result/v1", "strict": sys.argv[2] == "1",
        "classes": [{"name": name, "status": status, "checks": int(count)}
                    for name, status, count in rows]}
fd, temp = tempfile.mkstemp(dir=target.parent, prefix=".ci-result-")
with os.fdopen(fd, "w") as out:
    json.dump(data, out)
os.replace(temp, target)
PYRESULT
  [ "$?" -eq 0 ] || exit 2
fi

# One greppable line each, every run, pass or fail — this is the raw material
# for the failure taxonomy and the duration budget. `ci-timing` answers "which
# class grew" across runs without opening a single job log; the two class lists
# split every red run into FAIL (a defect, or local/CI skew if the same tree
# passed at pre-push) vs SKIP (environment, promoted to failure under --strict)
# at grep speed. FAILED_CLASSES is printed here, before the known-gaps block
# below rewrites FAILED, so the line always names the raw result.
echo
echo "ci-timing:${CLASS_TIMINGS}"
[ -n "$FAILED_CLASSES" ]  && echo "ci-failed-classes:${FAILED_CLASSES}"
[ -n "$SKIPPED_CLASSES" ] && echo "ci-skipped-classes:${SKIPPED_CLASSES}"

# THE CONFIRM-OR-KILL LINE for the 2026-08-23 push-path change, and the reason
# it is emitted here rather than measured by hand later.
#
# That change moved eight of the ten classes off the local push path and left
# them hosted-only. The council attached one acceptance test to it: count, over
# the first week, the hosted failures that the OLD local-full run would have
# caught before the push. A small count means the relocation was free and the
# minutes were sediment. A large one means local-full was doing real work and
# the change should be reverted -- and that decision is worthless if the number
# has to be reconstructed from memory or from forty job logs.
#
# So every red run states its own verdict, in one greppable line, at the moment
# it has the facts. `relocated` names classes that used to be caught locally and
# now are not; `floor` names classes the local hook still runs, whose failure
# means the floor let something past rather than that the relocation cost
# anything. ops/push-floor-telemetry.py adds these up across runs.
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

# KNOWN GAPS. A class that is red because a DESIGN RULING is outstanding, not
# because of a bug someone could fix. It still runs, it still prints its failure
# in full, and it is still counted and named below — it just does not take the
# exit code down while the ruling is pending.
#
# WHY THIS EXISTS. On 2026-08-13 seven consecutive pushes each emailed Joe a CI
# failure for the same blocked-on-him migration question. A gate that cries every
# push about something the reader cannot act on is one they learn to filter, and a
# filtered gate has stopped being a gate. The noise, not the redness, is the
# hazard.
#
# EVERY GAP EXPIRES, and that is the whole safety of the mechanism. Past its date
# it becomes a hard failure again whether or not anyone remembers it, so this can
# never quietly become a permanent exemption. ops/ci-selftest.py enforces that
# every entry HAS an expiry and that an expired one is honoured.
if [ "$FAILED" -gt 0 ] && [ "$HARD_FAILED" -eq 0 ] && [ -f ops/config/ci-check-scope.json ]; then
  GAP_REPORT="$("$PY" - ops/config/ci-check-scope.json "$FAILED_CLASSES" <<'PYEOF'
import json, sys, datetime
scope, failed = sys.argv[1], sys.argv[2].split()
gaps = {g["class"]: g for g in json.load(open(scope)).get("known_gaps", [])}
today = datetime.date.today().isoformat()
covered, live = [], []
for c in failed:
    g = gaps.get(c)
    if g and g.get("expires", "") > today:
        covered.append(c)
        print(f"KNOWNGAP\t{c}\t{g['expires']}\t{g.get('loop','')}\t{g['reason']}")
    else:
        live.append(c)
        if g:
            print(f"EXPIRED\t{c}\t{g.get('expires','no expiry')}")
print("REMAINING\t" + str(len(live)))
PYEOF
)"
  echo "$GAP_REPORT" | while IFS=$'\t' read -r tag c exp loop reason; do
    case "$tag" in
      KNOWNGAP) printf '  \033[33mKNOWN GAP\033[0m  %s — blocked on a ruling, not a bug. Expires %s (then this fails hard again).\n             Open loop #%s: %s\n' "$c" "$exp" "$loop" "$reason" ;;
      EXPIRED)  printf '  \033[31mEXPIRED GAP\033[0m %s — its exemption ran out on %s. Failing hard, as designed.\n' "$c" "$exp" ;;
    esac
  done
  REMAINING="$(echo "$GAP_REPORT" | awk -F'\t' '$1=="REMAINING"{print $2}')"
  if [ "${REMAINING:-$FAILED}" = "0" ]; then
    echo
    echo "CI: $FAILED class(es) red, all of them known gaps awaiting a ruling. Not counted as failure."
    exit 0
  fi
  FAILED="${REMAINING:-$FAILED}"
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
