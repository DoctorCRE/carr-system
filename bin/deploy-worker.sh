#!/bin/sh
# deploy-worker.sh — the ONE sanctioned way the CARR MCP Worker ships.
#
# WHY THIS EXISTS (loop #276, 2026-08-09). Production went from 75 verbs to 66
# in the middle of a working session, because `wrangler deploy` was run from a
# checkout sitting on `dictation-phase-b-loop-243` — a branch 49 commits behind
# main whose tools.js simply does not contain nine of them. The entire Deal Room
# read/write surface disappeared (get-deal-room, deal-room-board,
# patch-deal-field, add-deal-note, set-next-step) along with presence-lease, the
# primitive two-writer discipline depends on, plus capture-queue,
# resolve-candidate and resolve-conflict.
#
# NOTHING OBJECTED. wrangler ships whatever tree happens to be checked out, and
# a verb vanishing from production is INVISIBLE until some session happens to
# call it and gets `unknown_tool`. The loss was caught only because an unrelated
# audit had recorded a "75" reading an hour earlier to compare against. That is
# luck, not a control.
#
# TWO GUARDS, because the cause and the class are different problems:
#   PREFLIGHT catches the cause — refuse to ship anything that is not exactly
#   origin/main (or an explicitly approved immutable ancestor), and refuse to
#   ship dirty mcp-server/ or dealroom/ trees (this repo
#   regularly holds another live session's uncommitted work; see rule 308ef1de).
#   COUNT CHECK catches the class — compare the verb count about to ship against
#   the count recorded by the last successful deploy, and refuse on a DROP.
#   The sole exceptions are the already-prepared typed recovery legs
#   (`current_before`, `prior`, `current_after`, or the isolated
#   `restore_only` repair): the matching DB writer must durably prepare that
#   exact step and return its deterministic provider tag before it authorises a
#   temporary drop. Standalone/source deploys and caller flags never waive it.
#
# The count is EXACT, not parsed: it imports the module and reads
# Object.keys(TOOLS).length. A regex over the source undercounts (61 vs the real
# 66 on the branch that caused this), and a guard that reports the wrong number
# is worse than no guard.
#
# Usage:
#   bin/deploy-worker.sh              # preflight, deploy, postflight
#   bin/deploy-worker.sh --check      # preflight only, ship nothing
#   bin/deploy-worker.sh --release-sha <full-40-char-sha>
#       # an immutable release when main moves after readiness was recorded
#   bin/deploy-worker.sh --upload-version
#       # upload a Production candidate without changing traffic
#       # optional --probe-tokens-file <private JSON> rotates only PROBE_TOKENS
#   bin/deploy-worker.sh --promote-version <cloudflare-version-id>
#       # promote that exact ready version to 100% of Production traffic
#   # Production modes and a standalone staging release require the assurance
#   # plan inputs:
#       --performance-budget-ref <immutable-ref> --performance-budget-ms <ms>
#       --recovery-strategy <rollback|forward_fix>
#       --rollback-plan-ref <immutable-runbook-ref>
#
# Per rule a8c55a47, this is the same code the manual path and any automated
# path both run. There is no second way to deploy.
#
# DEPLOY PROVENANCE (Phase 1, 2026-08-13). The deployed Worker could not say
# what code it was running: no Git SHA, no schema range, no policy
# generation, in its responses or its deploy metadata. The only local signal
# was mcp-server/.last-deployed-verb-count, and on 2026-08-13 it sat un-
# bumped for ~2h after a real deploy (a Worker deploy happened ~15:21 while
# the marker's last write was ~13:23) — a verification pass very nearly
# concluded, wrongly, that the code was unshipped. A signal that can go
# silently stale is worse than none.
#
# TWO HALVES to closing it:
#   1. THE SHA IS STAMPED HERE, at deploy time, via `--var GIT_SHA:<sha>` —
#      not a [vars] line in wrangler.toml. wrangler.toml is a tracked file;
#      writing a commit's SHA into it would mean either committing a new
#      value on every deploy (noise, and a race with whatever else is
#      touching main) or leaving it perpetually stale between deploys. A CLI
#      --var is scoped to exactly this invocation, requires no commit, and
#      --keep-vars defaults to false — so a deploy that does NOT pass it
#      (i.e. `wrangler deploy` run directly, bypassing this script) drops
#      GIT_SHA from that version. That is a feature, not a gap: THE VAR
#      BEING ABSENT IS THE HONEST SIGNAL THAT THIS SCRIPT WAS BYPASSED,
#      which is exactly what /release reports (see mcp-server/src/release.js
#      — null value, "not stamped: deployed outside bin/deploy-worker.sh").
#   1b. THE CANDIDATE MANIFEST IS STAMPED THE SAME WAY (standing-rule
#      amendment 9, 2026-09-14) — `--var CANDIDATE_MANIFEST:<jcs>` and
#      `--var CANDIDATE_MANIFEST_DIGEST:<sha256>`, sealed by
#      mcp-server/bin/seal-candidate-manifest.mjs on the same invocation, for the
#      same reason and with the same honest absence when this script is
#      bypassed. The Gate Zero producer binds its candidate to those two stamps
#      because the deployed Worker cannot read a repository at request time.
#   2. THE MARKER WRITE IS PART OF THIS SAME STEP (see postflight below) —
#      it always was, but that only protects a deploy that goes THROUGH this
#      script. Grep the repo: `wrangler deploy` is also called directly
#      (ops/completion-evidence-gate-selftest.py's own test fixture models
#      exactly that as a plausible session action), and a local file cannot
#      detect being bypassed — there is no hook a bypassed script can run.
#      So the marker is necessary-but-not-sufficient, and this script does
#      not pretend otherwise: /release, read live off the deployed Worker
#      (mcp-server/src/index.js), is the AUTHORITATIVE signal now.
#      tools/health-check.py's release check reads /release, not this file,
#      for exactly that reason — this marker remains only as a fast local
#      sanity check for a session that already trusts its own history.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
# Policy, writers and runtime dependencies always resolve from this current
# wrapper root. A typed recovery can select only a detached, exact source root
# for Worker/assets; it never gets to execute that revision's wrapper or tools.
SOURCE_ROOT="$REPO"
WORKER_DIR="$SOURCE_ROOT/mcp-server"
WRANGLER="$REPO/mcp-server/node_modules/.bin/wrangler"
# Same resolution ops/ci.sh and bin/worktree.sh use: prefer the repo venv, fall
# back to whatever python3 is on PATH. The release preflight below needs it.
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"

CHECK_ONLY=0
TARGET_ENV="production"
PINNED_RELEASE=""
VERSION_MODE="ordinary"
PROVIDER="cloudflare-workers"
PROVIDER_VERSION_ID=""
PERFORMANCE_BUDGET_REF=""
PERFORMANCE_BUDGET_MS=""
RECOVERY_STRATEGY=""
ROLLBACK_PLAN_REF=""
REQUESTED_RELEASE_KEY=""
RELEASE_TEST_EVIDENCE=""
RELEASE_SECURITY_EVIDENCE=""
RELEASE_VERIFIER=""
RELEASE_VERIFIER_EVIDENCE=""
PROBE_TOKENS_FILE=""
FOUNDATION_ASSURANCE_STAGING_PROVIDER=""
FOUNDATION_ASSURANCE_STAGING_CANDIDATE_OPERATION=""
FOUNDATION_ASSURANCE_STAGING_REPLACEMENT_RECEIPT=""
FOUNDATION_ASSURANCE_STAGING_REPLACEMENT_SOURCE=""
RECOVERY_ATTEMPT_ID=""
RECOVERY_STEP="standalone"
RECOVERY_PRIOR_RELEASE_KEY=""
STAGING_RECEIPT_KEY=""
EXACT_SOURCE_ROOT=""
EXACT_RUNTIME_LINK=""
WR95_STAGING_VERSION_JSON=""
WR95_STAGING_RELEASE_JSON=""
WR95_FINAL_VERSION_JSON=""
WR95_SEAL_OUTPUT=""
# Filled only from the exact immutable release manifest after preflight.
EXPECTED_PROGRAM6_ACTIONS=""

# A detached recovery source is validated as a clean, exact Git tree and must
# not carry ignored dependencies.  Wrangler still needs the current verified
# runtime to bundle that exact Worker, so the link below is provisioned only
# after source and package-lock validation.  One cleanup hook owns all
# ephemeral files so later receipt-specific traps cannot strand the link.
cleanup_ephemeral() {
  for wr95_tmp in "${WR95_STAGING_VERSION_JSON:-}" "${WR95_STAGING_RELEASE_JSON:-}" \
      "${WR95_FINAL_VERSION_JSON:-}" "${WR95_SEAL_OUTPUT:-}"; do
    if [ -n "$wr95_tmp" ] && [ -f "$wr95_tmp" ]; then
      rm -f "$wr95_tmp"
    fi
  done
  if [ -n "${STAGING_RECEIPT:-}" ] && [ -e "$STAGING_RECEIPT" ]; then
    rm -f "$STAGING_RECEIPT"
  fi
  if [ -n "${EXACT_RUNTIME_LINK:-}" ] && [ -L "$EXACT_RUNTIME_LINK" ] \
      && [ "$(readlink "$EXACT_RUNTIME_LINK")" = "$REPO/mcp-server/node_modules" ]; then
    rm -f "$EXACT_RUNTIME_LINK"
  fi
}
cleanup_on_signal() {
  cleanup_ephemeral
  trap - EXIT HUP INT TERM
  exit "$1"
}
trap cleanup_ephemeral EXIT
trap 'cleanup_on_signal 129' HUP
trap 'cleanup_on_signal 130' INT
trap 'cleanup_on_signal 143' TERM
while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)         CHECK_ONLY=1 ;;
    --env)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --env needs a name" >&2; exit 64; }
      TARGET_ENV="$2"
      shift
      ;;
    --performance-budget-ref)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --performance-budget-ref needs a reference" >&2; exit 64; }
      PERFORMANCE_BUDGET_REF="$2"; shift ;;
    --performance-budget-ms)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --performance-budget-ms needs milliseconds" >&2; exit 64; }
      PERFORMANCE_BUDGET_MS="$2"; shift ;;
    --recovery-strategy)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --recovery-strategy needs an approved strategy" >&2; exit 64; }
      RECOVERY_STRATEGY="$2"; shift ;;
    --rollback-plan-ref)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --rollback-plan-ref needs an immutable runbook reference" >&2; exit 64; }
      ROLLBACK_PLAN_REF="$2"; shift ;;
    --release-sha)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --release-sha needs a full commit SHA" >&2; exit 64; }
      PINNED_RELEASE="$2"
      shift
      ;;
    --release-key)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --release-key needs a canonical key" >&2; exit 64; }
      REQUESTED_RELEASE_KEY="$2"; shift ;;
    --test-evidence)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --test-evidence needs a reference" >&2; exit 64; }
      RELEASE_TEST_EVIDENCE="$2"; shift ;;
    --security-evidence)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --security-evidence needs a reference" >&2; exit 64; }
      RELEASE_SECURITY_EVIDENCE="$2"; shift ;;
    --verifier)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --verifier needs an actor slug" >&2; exit 64; }
      RELEASE_VERIFIER="$2"; shift ;;
    --verifier-evidence)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --verifier-evidence needs a reference" >&2; exit 64; }
      RELEASE_VERIFIER_EVIDENCE="$2"; shift ;;
    --probe-tokens-file)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --probe-tokens-file needs a private JSON path" >&2; exit 64; }
      PROBE_TOKENS_FILE="$2"; shift ;;
    --foundation-assurance-staging-provider)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --foundation-assurance-staging-provider needs an immutable UUID" >&2; exit 64; }
      FOUNDATION_ASSURANCE_STAGING_PROVIDER="$2"; shift ;;
    --foundation-assurance-staging-candidate-operation)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --foundation-assurance-staging-candidate-operation needs an immutable UUID" >&2; exit 64; }
      FOUNDATION_ASSURANCE_STAGING_CANDIDATE_OPERATION="$2"; shift ;;
    --foundation-assurance-staging-replacement-receipt)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --foundation-assurance-staging-replacement-receipt needs an immutable UUID" >&2; exit 64; }
      FOUNDATION_ASSURANCE_STAGING_REPLACEMENT_RECEIPT="$2"; shift ;;
    --foundation-assurance-staging-replacement-source)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --foundation-assurance-staging-replacement-source needs a full SHA" >&2; exit 64; }
      FOUNDATION_ASSURANCE_STAGING_REPLACEMENT_SOURCE="$2"; shift ;;
    --recovery-attempt-id)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --recovery-attempt-id needs a UUID" >&2; exit 64; }
      RECOVERY_ATTEMPT_ID="$2"; shift ;;
    --recovery-step)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --recovery-step needs current_before|prior|current_after|forward_fix|restore_only" >&2; exit 64; }
      RECOVERY_STEP="$2"; shift ;;
    --recovery-prior-release-key)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --recovery-prior-release-key needs a key" >&2; exit 64; }
      RECOVERY_PRIOR_RELEASE_KEY="$2"; shift ;;
    --staging-receipt-idempotency-key)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --staging-receipt-idempotency-key needs a UUID" >&2; exit 64; }
      STAGING_RECEIPT_KEY="$2"; shift ;;
    --internal-exact-source-root)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --internal-exact-source-root needs a path" >&2; exit 64; }
      [ -z "$EXACT_SOURCE_ROOT" ] || { echo "deploy-worker: --internal-exact-source-root may appear once" >&2; exit 64; }
      EXACT_SOURCE_ROOT="$2"; shift ;;
    --upload-version)
      [ "$VERSION_MODE" = "ordinary" ] \
        || { echo "deploy-worker: --upload-version and --promote-version are mutually exclusive" >&2; exit 64; }
      VERSION_MODE="upload"
      ;;
    --promote-version)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --promote-version needs an immutable provider version id" >&2; exit 64; }
      [ "$VERSION_MODE" = "ordinary" ] \
        || { echo "deploy-worker: --upload-version and --promote-version are mutually exclusive" >&2; exit 64; }
      VERSION_MODE="promote"
      PROVIDER_VERSION_ID="$2"
      shift
      ;;
    *) echo "deploy-worker: unknown argument '$1'" >&2; exit 64 ;;
  esac
  shift
done

if [ -n "$PROBE_TOKENS_FILE" ]; then
  [ "$VERSION_MODE" = "upload" ] || { echo "deploy-worker: probe token rotation requires --upload-version" >&2; exit 64; }
  "$PY" - "$PROBE_TOKENS_FILE" <<'PY'
import json, os, stat, sys
path = sys.argv[1]
meta = os.stat(path)
if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_mode & 0o077:
    raise SystemExit("deploy-worker: probe token file must be an owner-only regular file")
value = json.load(open(path))
if set(value) != {"PROBE_TOKENS"}:
    raise SystemExit("deploy-worker: probe token file may bind only PROBE_TOKENS")
tokens = json.loads(value["PROBE_TOKENS"])
if set(tokens) != {"smoke-probe"} or not isinstance(tokens["smoke-probe"], str) or len(tokens["smoke-probe"]) != 64 or any(c not in "0123456789abcdef" for c in tokens["smoke-probe"]):
    raise SystemExit("deploy-worker: invalid smoke-probe token shape")
PY
fi

fail() { echo ""; echo "REFUSED: $1" >&2; echo "" >&2; exit 1; }

# WR126 only ships the inert contract.  A local environment must not turn the
# issuer planner into a live provisioning or activation path, and the checked
# Wrangler manifest must carry the same explicit disabled default.  The actual
# login/secret slots are deliberately not read by this deploy wrapper.
OWNERSHIP_RUNTIME_MODE="${CARR_CANONICAL_OWNERSHIP_RUNTIME_MODE:-disabled}"
[ "$OWNERSHIP_RUNTIME_MODE" = "disabled" ] \
  || fail "canonical ownership issuer runtime is disabled in this source slice."
grep -Eq '^CANONICAL_OWNERSHIP_RUNTIME_MODE[[:space:]]*=[[:space:]]*"disabled"[[:space:]]*$' \
  "$WORKER_DIR/wrangler.toml" \
  || fail "wrangler.toml must keep canonical ownership issuer runtime disabled."

# One builder owns the exact source/environment/assurance preimage for every
# release-manifest reconstruction.  A caller may supply either the complete
# assurance group or none; the admission rules below require the complete group
# for Production and standalone staging releases.
build_release_manifest() {
  BUILD_MANIFEST_SHA="$1"
  BUILD_MANIFEST_ENVIRONMENT="$2"
  if [ -n "$PERFORMANCE_BUDGET_REF" ]; then
    "$PY" "$REPO/tools/release-manifest.py" build --sha "$BUILD_MANIFEST_SHA" \
      --environment "$BUILD_MANIFEST_ENVIRONMENT" \
      --performance-budget-ref "$PERFORMANCE_BUDGET_REF" \
      --performance-budget-ms "$PERFORMANCE_BUDGET_MS" \
      --recovery-strategy "$RECOVERY_STRATEGY" \
      --rollback-plan-ref "$ROLLBACK_PLAN_REF"
  else
    "$PY" "$REPO/tools/release-manifest.py" build --sha "$BUILD_MANIFEST_SHA" \
      --environment "$BUILD_MANIFEST_ENVIRONMENT"
  fi
}

# A temporary verb-count drop is authorized only after the DB writer for the
# exact typed recovery step has durably prepared the attempt and returned its
# deterministic provider tag.  This is deliberately a function shared by the
# preflight shrink gate and the later deployment prepare: the later call is an
# idempotent replay of the same writer input, never a second authorization path.
prepare_typed_recovery_shrink() {
  [ "$TARGET_ENV" = "staging" ] || fail "typed recovery shrink is staging-only."
  [ "$RECOVERY_STEP" != "standalone" ] || fail "standalone deploys cannot authorize verb shrink."
  [ -n "$STAGING_RECEIPT_KEY" ] || fail "typed recovery shrink needs a staging receipt idempotency key."
  printf '%s\n' "$STAGING_RECEIPT_KEY" | grep -Eq \
    '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' \
    || fail "--staging-receipt-idempotency-key must be a lowercase canonical UUID."

  case "$RECOVERY_STEP" in
    current_before|prior|current_after)
      TYPED_RECOVERY_TAG="$("$PY" "$REPO/tools/ops-record.py" staging-attempt prepare \
        --idempotency-key "$STAGING_RECEIPT_KEY" --release-key "$REQUESTED_RELEASE_KEY" \
        --prior-release-key "$RECOVERY_PRIOR_RELEASE_KEY" \
        --recovery-attempt-id "$RECOVERY_ATTEMPT_ID" --recovery-step "$RECOVERY_STEP" \
        --git-sha "$HEAD_SHA" --correlation "$RECOVERY_ATTEMPT_ID" \
        --field expected_provider_tag)" \
        || fail "the typed recovery step was not durably prepared; verb shrink is refused."
      ;;
    restore_only)
      TYPED_RECOVERY_TAG="$("$PY" "$REPO/tools/ops-record.py" staging-restore-only prepare \
        --idempotency-key "$STAGING_RECEIPT_KEY" --release-key "$REQUESTED_RELEASE_KEY" \
        --prior-release-key "$RECOVERY_PRIOR_RELEASE_KEY" \
        --recovery-attempt-id "$RECOVERY_ATTEMPT_ID" --git-sha "$HEAD_SHA" \
        --correlation "$RECOVERY_ATTEMPT_ID" --field expected_provider_tag)" \
        || fail "the typed restore-only repair was not durably prepared; verb shrink is refused."
      ;;
    forward_fix)
      TYPED_RECOVERY_TAG="$("$PY" "$REPO/tools/ops-record.py" staging-forward-fix prepare \
        --idempotency-key "$STAGING_RECEIPT_KEY" --release-key "$REQUESTED_RELEASE_KEY" \
        --git-sha "$HEAD_SHA" --correlation "$STAGING_RECEIPT_KEY" \
        --field expected_provider_tag)" \
        || fail "the typed forward-fix rehearsal was not durably prepared; deploy is refused."
      ;;
    *) fail "standalone deploys cannot authorize verb shrink." ;;
  esac
  printf '%s\n' "$TYPED_RECOVERY_TAG" | grep -Eq '^carr-staging(-forward-fix)?-[0-9a-f]{32}$' \
    || fail "the prepared typed recovery step returned no exact provider tag; verb shrink is refused."
  echo "  !!  shrinking by $LOST verb(s), allowed only by the exact prepared typed recovery step"
}

if [ "$VERSION_MODE" != "ordinary" ] && [ "$TARGET_ENV" != "production" ]; then
  fail "provider-version operations are Production-only; staging is a source rehearsal and receives its own build."
fi
# THE UPLOAD FILES ITS OWN RELEASE-CANDIDATE RECORD (standing-rule amendment 9),
# so what that record needs is checked HERE — before a version exists — rather
# than after Cloudflare holds an immutable object nobody can account for.
#
# WHY THE EVIDENCE REFS ARE REQUIRED RATHER THAN OPTIONAL. ops.release's
# an_approved_release_carries_its_evidence constraint exempts `candidate` and
# nothing beyond it. The WR95 path is the sole closed exception: it files the
# exact candidate first, then 0510 atomically attaches the live-derived evidence
# ref while inserting the immutable evidence bytes. The maker is NOT among
# these arguments: migration 0504 derives it from the filing login.
if [ "$VERSION_MODE" = "upload" ]; then
  [ -n "$REQUESTED_RELEASE_KEY" ] \
    || fail "--upload-version files the release-candidate record itself and needs --release-key <canonical key>."
  [ -n "$RELEASE_SECURITY_EVIDENCE" ] \
    || fail "--upload-version needs --security-evidence."
  if [ -n "$FOUNDATION_ASSURANCE_STAGING_PROVIDER" ]; then
    [ -z "$RELEASE_TEST_EVIDENCE" ] \
      || fail "WR95 derives --test-evidence from live acquisition; caller evidence is refused."
    printf '%s\n' "$FOUNDATION_ASSURANCE_STAGING_PROVIDER" | grep -Eq \
      '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' \
      || fail "--foundation-assurance-staging-provider must be a lowercase immutable UUID."
    printf '%s\n' "$FOUNDATION_ASSURANCE_STAGING_CANDIDATE_OPERATION" \
      "$FOUNDATION_ASSURANCE_STAGING_REPLACEMENT_RECEIPT" | grep -Eqv \
      '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' \
      && fail "WR95 live acquisition needs exact staging candidate-operation and replacement-receipt UUIDs."
    printf '%s\n' "$FOUNDATION_ASSURANCE_STAGING_REPLACEMENT_SOURCE" | grep -Eq '^[0-9a-f]{40}$' \
      || fail "WR95 live acquisition needs the exact staging replacement source SHA."
  else
    [ -n "$RELEASE_TEST_EVIDENCE" ] \
      || fail "--upload-version needs --test-evidence unless WR95 live acquisition is selected."
  fi
  if [ -n "$RELEASE_VERIFIER$RELEASE_VERIFIER_EVIDENCE" ]; then
    [ -n "$RELEASE_VERIFIER" ] && [ -n "$RELEASE_VERIFIER_EVIDENCE" ] \
      || fail "--verifier and --verifier-evidence are an atomic pair."
  fi
fi
case "$RECOVERY_STEP" in
  standalone)
    [ -z "$RECOVERY_ATTEMPT_ID$RECOVERY_PRIOR_RELEASE_KEY" ] \
      || fail "standalone staging deploy cannot carry recovery attempt/prior fields."
    ;;
  current_before|prior|current_after|forward_fix|restore_only)
    [ "$TARGET_ENV" = "staging" ] && [ -n "$REQUESTED_RELEASE_KEY" ] \
      || fail "recovery deploy requires staging plus --release-key and --recovery-step."
    if [ "$RECOVERY_STEP" != "forward_fix" ]; then
      [ -n "$RECOVERY_ATTEMPT_ID" ] && [ -n "$RECOVERY_PRIOR_RELEASE_KEY" ] \
        || fail "rollback recovery deploy requires --recovery-attempt-id and --recovery-prior-release-key."
    fi
    if [ "$RECOVERY_STEP" != "forward_fix" ]; then
      printf '%s\n' "$RECOVERY_ATTEMPT_ID" | grep -Eq \
        '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$' \
        || fail "--recovery-attempt-id must be an exact UUID."
      RECOVERY_ATTEMPT_ID="$(printf '%s' "$RECOVERY_ATTEMPT_ID" | tr 'A-F' 'a-f')"
    fi
    ;;
  *) fail "--recovery-step must be standalone|current_before|prior|current_after|forward_fix|restore_only." ;;
esac
if [ -n "$EXACT_SOURCE_ROOT" ]; then
  [ "$VERSION_MODE" = "ordinary" ] && [ "$TARGET_ENV" = "staging" ] \
    && [ "$RECOVERY_STEP" != "standalone" ] && [ -n "$PINNED_RELEASE" ] \
    || fail "an exact source root is internal to a typed staging recovery step."
  SOURCE_ROOT="$("$PY" "$REPO/tools/validate-exact-recovery-source.py" \
    --root "$EXACT_SOURCE_ROOT" --sha "$PINNED_RELEASE")" \
    || fail "the internal exact source root is not the bound clean detached source."
  CURRENT_PACKAGE_LOCK="$REPO/mcp-server/package-lock.json"
  EXACT_PACKAGE_LOCK="$SOURCE_ROOT/mcp-server/package-lock.json"
  [ -f "$CURRENT_PACKAGE_LOCK" ] \
    || fail "the current wrapper dependency lockfile is missing."
  [ -f "$EXACT_PACKAGE_LOCK" ] \
    || fail "the exact recovery source dependency lockfile is missing."
  cmp -s "$CURRENT_PACKAGE_LOCK" "$EXACT_PACKAGE_LOCK" \
    || fail "the exact recovery source dependency lockfile differs from the current verified runtime."
  EXACT_RUNTIME_LINK="$SOURCE_ROOT/mcp-server/node_modules"
  [ -d "$REPO/mcp-server/node_modules" ] \
    || fail "the current verified runtime dependencies are missing."
  if [ -e "$EXACT_RUNTIME_LINK" ] || [ -L "$EXACT_RUNTIME_LINK" ]; then
    fail "the exact recovery source already contains mcp-server/node_modules."
  fi
  ln -s "$REPO/mcp-server/node_modules" "$EXACT_RUNTIME_LINK" \
    || fail "could not provision the current verified runtime for exact recovery bundling."
  [ -L "$EXACT_RUNTIME_LINK" ] \
    && [ "$(readlink "$EXACT_RUNTIME_LINK")" = "$REPO/mcp-server/node_modules" ] \
    || fail "the exact recovery runtime link was not created with the expected target."
  WORKER_DIR="$SOURCE_ROOT/mcp-server"
fi
if [ "$VERSION_MODE" = "ordinary" ] && [ "$TARGET_ENV" = "production" ]; then
  fail "Production source deploy is disabled. Upload an immutable candidate with
  --upload-version, bind that provider version to an approved release, then use
  --promote-version <id>."
fi
if [ "$VERSION_MODE" = "promote" ]; then
  printf '%s\n' "$PROVIDER_VERSION_ID" | grep -Eq \
    '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$' \
    || fail "--promote-version must be the exact immutable Cloudflare UUID."
  PROVIDER_VERSION_ID="$(printf '%s' "$PROVIDER_VERSION_ID" | tr 'A-F' 'a-f')"
fi

cd "$REPO"
[ -x "$WRANGLER" ] || fail "wrangler not found at $WRANGLER (run npm install in mcp-server/)."
[ -x "$PY" ] || fail "python not found; release truth cannot be checked."
if [ "$VERSION_MODE" != "promote" ]; then
  DOCTORCRE_PIN="$SOURCE_ROOT/ops/config/doctorcre-artifact.v1.json"
  if [ -f "$DOCTORCRE_PIN" ]; then
    DOCTORCRE_ROOT="$SOURCE_ROOT/out/doctorcre-artifacts"
    "$PY" "$REPO/tools/release-manifest.py" doctorcre-artifact materialize \
      --pin "$DOCTORCRE_PIN" --root "$DOCTORCRE_ROOT" \
      || fail "the exact DoctorCRE artifact could not be verified and materialized."
    [ -f "$DOCTORCRE_ROOT/current/workspace.html" ] \
      || fail "the materialized DoctorCRE entrypoint is missing."
    echo "  OK  DoctorCRE artifact materialized from the exact CARR pin"
  fi
fi
if [ -n "$PERFORMANCE_BUDGET_REF$PERFORMANCE_BUDGET_MS$RECOVERY_STRATEGY$ROLLBACK_PLAN_REF" ]; then
  [ -n "$PERFORMANCE_BUDGET_REF" ] && [ -n "$PERFORMANCE_BUDGET_MS" ] \
    && [ -n "$RECOVERY_STRATEGY" ] && [ -n "$ROLLBACK_PLAN_REF" ] \
    || fail "performance budget/ref, recovery strategy, and rollback plan ref must be supplied together."
fi
if [ "$TARGET_ENV" = "production" ]; then
  [ -n "$PERFORMANCE_BUDGET_REF" ] \
    || fail "Production performance budget/ref, recovery strategy, and rollback plan ref are required readiness inputs."
fi
if [ "$TARGET_ENV" = "staging" ] && [ "$RECOVERY_STEP" = "standalone" ]; then
  [ -n "$PERFORMANCE_BUDGET_REF" ] \
    || fail "standalone staging release requires performance/recovery assurance; the exact inputs are approval-bound, not deploy defaults."
fi

HEAD_SHA=""
SHIPPING=""

# ---------- preflight 1: exactly origin/main ----------
echo "== preflight =="
if [ "$VERSION_MODE" = "promote" ]; then
  echo "  OK  immutable provider promotion — source checkout and build preflights are skipped"
  echo "      release truth below supplies the SHA bound to $PROVIDER_VERSION_ID"
else
git -C "$REPO" fetch origin main --quiet 2>/dev/null || fail "could not reach origin to verify main."

HEAD_SHA="$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
HEAD_TREE="$(git -C "$SOURCE_ROOT" rev-parse "${HEAD_SHA}^{tree}")"
MAIN_SHA="$(git -C "$SOURCE_ROOT" rev-parse origin/main)"
BRANCH="$(git -C "$SOURCE_ROOT" rev-parse --abbrev-ref HEAD)"

if [ -n "$PINNED_RELEASE" ]; then
  [ "${#PINNED_RELEASE}" -eq 40 ] \
    || fail "--release-sha must be the full immutable 40-character commit SHA."
  PINNED_SHA="$(git -C "$SOURCE_ROOT" rev-parse --verify "${PINNED_RELEASE}^{commit}" 2>/dev/null)" \
    || fail "pinned release SHA does not resolve to a commit."
  [ "$PINNED_RELEASE" = "$PINNED_SHA" ] \
    || fail "--release-sha must be the exact canonical full SHA, not an abbreviation or tag."
  [ "$HEAD_SHA" = "$PINNED_SHA" ] \
    || fail "checkout HEAD does not equal the pinned release SHA."
  git -C "$SOURCE_ROOT" merge-base --is-ancestor "$PINNED_SHA" origin/main \
    || fail "pinned release SHA is not an ancestor of fetched origin/main."
  echo "  OK  pinned release: $PINNED_SHA (ancestor of origin/main $MAIN_SHA)"
elif [ "$TARGET_ENV" != "production" ]; then
  # STAGING IS FOR CODE THAT IS NOT ON MAIN YET — that is the entire point of
  # having it. Requiring origin/main here would mean the only way to rehearse a
  # change is to merge it first, which inverts the purpose of a rehearsal
  # environment. The clean-tree check below still applies, so what ships is
  # always a commit someone can name and return to.
  echo "  OK  env=$TARGET_ENV — branch check skipped on purpose (staging exists to run unmerged code)"
  echo "      shipping $BRANCH @ $HEAD_SHA"
elif [ "$HEAD_SHA" != "$MAIN_SHA" ]; then
  BEHIND="$(git -C "$SOURCE_ROOT" rev-list --count "HEAD..origin/main")"
  AHEAD="$(git -C "$SOURCE_ROOT" rev-list --count "origin/main..HEAD")"
  fail "this checkout is not origin/main.
  branch:  $BRANCH
  HEAD:    $HEAD_SHA
  main:    $MAIN_SHA
  $BEHIND commit(s) behind main, $AHEAD ahead.

  Deploying from here would ship main's absence, not your presence: any verb
  that exists on main but not here is REMOVED from production silently.
  That is loop #276, exactly. Deploy from a clean main checkout or a worktree:
      git worktree add /tmp/deploy-main main"
else
  echo "  OK  HEAD is origin/main ($MAIN_SHA)"
fi

# ---------- preflight 2: nothing uncommitted in the Worker or its assets ----------
# node_modules is excluded BY NAME, not left to .gitignore. mcp-server/.gitignore
# ignores `node_modules/` with a trailing slash, which matches a directory and
# NOT a symlink — and symlinking node_modules from a primary checkout is exactly
# how you deploy from a throwaway worktree without a second npm install. Without
# this exclusion the guard refuses every worktree deploy, and a guard that always
# refuses is a guard everyone learns to skip.
# .last-deployed-verb-count is excluded for a different reason: postflight WRITES
# it, so leaving it in the dirty check means every deploy poisons the next one
# until the file is committed. It is a guard artifact and never ships to the
# Worker. The postflight still tells you to commit it, because an uncommitted
# baseline protects only this checkout.
DIRTY_LIST="$(git -C "$SOURCE_ROOT" status --porcelain -- mcp-server/ dealroom/ \
  | grep -v 'mcp-server/node_modules' \
  | grep -v 'mcp-server/.last-deployed-verb-count' || true)"
DIRTY="$(printf '%s' "$DIRTY_LIST" | grep -c . || true)"
if [ "$DIRTY" != "0" ]; then
  printf '%s\n' "$DIRTY_LIST" >&2
  fail "mcp-server/ or dealroom/ has $DIRTY uncommitted change(s) above.
  Shipping them would put code or assets in production that are in no commit, and this
  repo regularly holds ANOTHER live session's work (rule 308ef1de). Commit
  them deliberately, or deploy from a clean worktree."
fi
echo "  OK  mcp-server/ and dealroom/ are clean"

# ---------- preflight 2b: what this deploy will ATTACH to ----------
# Every other preflight is about the ARTIFACT — right commit, clean tree, no verb
# loss. All three passed on 2026-08-13 while a staging deploy took over all three
# production custom domains, because nothing here looked at ATTACHMENT. This is
# that check. Production is exempt: it is allowed to claim its own hostnames.
if [ "$TARGET_ENV" != "production" ]; then
  PYBIN="$REPO/.venv/bin/python"
  [ -x "$PYBIN" ] || PYBIN=python3
  if ! "$PYBIN" "$REPO/ops/deploy-attachment-check.py" "$WORKER_DIR/wrangler.toml" "$TARGET_ENV"; then
    fail "refusing to deploy env=$TARGET_ENV — see the attachment check above."
  fi
fi

# ---------- preflight 3: verb count did not shrink ----------

# The count moved to ops/verb-count.sh on 2026-08-13 so that CI can run this same
# guard at MERGE time, not only here at DEPLOY time. Same code, two callers
# (rule a8c55a47) — deliberately NOT a second copy that can drift from this one.
SHIPPING="$(sh "$REPO/ops/verb-count.sh" "$WORKER_DIR" 2>/dev/null || true)"

case "$SHIPPING" in
  ''|*[!0-9]*) fail "could not count verbs in $WORKER_DIR/src/tools.js — the import failed.
  Refusing rather than shipping an unmeasured registry." ;;
esac
echo "  OK  registry imports cleanly: $SHIPPING verbs about to ship"

# THE BASELINE COMES FROM THE LEDGER, NOT A FILE (defect d737c09c, 2026-08-16).
# It used to live in mcp-server/.last-deployed-verb-count.<env>, which the
# postflight told you to commit — and committing it fails
# ops/release-manifest-selftest.py, because manifest artifact_paths are
# ['mcp-server','dealroom'] so the file sits inside the digested artifact while
# the digest skips dotfiles. It moved the deployed TREE without moving its
# DIGEST, the exact condition that test guards. Two halves of one control giving
# opposite instructions.
#
# The file was always a DUPLICATE: every deploy already records --verb-count
# into ops.deployment, and since the ledger fix earlier today it does so for
# staging too. Write law 14181e60 settles which copy survives.
PREVIOUS=""
LEDGER_RC=0
# STDERR IS KEPT, and that is the point. This read used to send stderr to
# /dev/null, so ops/last-deployed-verb-count.py could tell us the baseline was
# stale and nobody would ever see it — the same "printed into a log nobody
# opens" shape as the nightly steps that reported skipped for five days
# (defect 3b21767e). stdout is still the number and nothing else.
BASELINE_STDERR="$(mktemp)"
PREVIOUS="$("$PY" "$REPO/ops/last-deployed-verb-count.py" carr-mcp "$TARGET_ENV" 2>"$BASELINE_STDERR")" \
  || LEDGER_RC=$?
case "$LEDGER_RC" in
  0)
    if [ -n "$PREVIOUS" ] && [ "$SHIPPING" -lt "$PREVIOUS" ]; then
      LOST=$((PREVIOUS - SHIPPING))
      if [ "$RECOVERY_STEP" = "standalone" ] || [ "$TARGET_ENV" != "staging" ]; then
        fail "this deploy would REMOVE $LOST verb(s) from $TARGET_ENV.
  last deployed: $PREVIOUS
  about to ship: $SHIPPING

  Source and standalone deploys cannot waive the verb-loss guard. Only an
  exact prepared typed staging recovery step may temporarily shrink."
      fi
      prepare_typed_recovery_shrink
    else
      echo "  OK  no verb loss (last deployed to $TARGET_ENV: $PREVIOUS)"
    fi
    if grep -q 'STALE BASELINE' "$BASELINE_STDERR" 2>/dev/null; then
      echo "  !!  the number above is STALE and the guard above is weaker than it looks:" >&2
      sed 's/^/      /' "$BASELINE_STDERR" >&2
    fi
    ;;
  3)
    echo "  --  no previous deployment recorded for $TARGET_ENV; this run establishes the baseline"
    ;;
  *)
    # FAIL CLOSED, and no escape flag. Not caution: the Worker being deployed
    # needs Postgres for every verb it serves, so a deploy attempted while the
    # ledger is unreachable ships something that cannot work anyway. Refusing
    # costs nothing real, and a loss guard that cannot check must never wave a
    # deploy through — that is exactly how loop #276 happened.
    fail "could not read the previous verb count from the ledger (exit $LEDGER_RC).
  The verb-loss guard cannot run, so this deploy is refused rather than shipped blind.
  Diagnose with: .venv/bin/python ops/last-deployed-verb-count.py carr-mcp $TARGET_ENV
  If Postgres is down, the Worker you are about to ship cannot serve a verb anyway."
    ;;
esac
rm -f "$BASELINE_STDERR"
fi

# record_deployment <state> <correlation> [identity-readback] — attach what just
# shipped to the release that authorised it.
#
# ONE FUNCTION, THREE CALL SITES, because the three outcomes of a deploy are
# genuinely different facts and the ledger has to hold whichever one happened.
# complete carries the final read-back and means the suite proved the Worker;
# one explicit verifying receipt carries the earlier machine identity read-back,
# while every other verifying state means the suite could not prove completion.
# failed means a check ran and the Worker answered wrongly. Recording all three
# as one state, or recording only the happy one, is how a deploy history starts
# reading greener than the deploys were.
#
# A routine/non-Production ledger miss remains loud but non-fatal: the code has
# already shipped. Program 5 Production assurance is stricter. The durable live
# identity receipt and the final complete release are acceptance outcomes, so
# their absence must return non-zero even though the traffic change already
# happened. The recovery instructions below say exactly what remains to record.
record_deployment() {
  rd_state="$1"
  rd_corr="${2:-}"
  rd_readback_kind="${3:-}"
  rd_must_record=0
  if [ "$TARGET_ENV" = "production" ] \
      && { [ "$rd_state" = "complete" ] \
           || [ "$rd_readback_kind" = "identity-readback" ]; }; then
    rd_must_record=1
  fi
  if [ ! -x "$PY" ] || [ ! -f "$REPO/tools/ops-record.py" ]; then
    echo "  !! deployment assurance recorder is unavailable after traffic changed."
    [ "$rd_must_record" = "0" ] || return 1
    return 0
  fi

  set +e
  # The first read-back is the machine-verified serving identity. Complete adds
  # a later receipt after golden/performance pass. Both are real observations;
  # ordinary verifying states must not manufacture a timestamp.
  if [ "$rd_state" = "complete" ] \
      || [ "$rd_readback_kind" = "identity-readback" ]; then
    rd_read_back="--read-back-at now"
  else
    rd_read_back=""
  fi
  rd_provider_args=""
  if [ "$TARGET_ENV" = "production" ]; then
    rd_provider_args="--provider $PROVIDER --provider-version-id $PROVIDER_VERSION_ID"
  fi
  rd_verb_args=""
  [ -n "$SHIPPING" ] && rd_verb_args="--verb-count $SHIPPING"
  rd_evidence_ref="${DEPLOYMENT_EVIDENCE_REF:-bin/smoke-and-record.sh#${rd_corr:-unknown}}"
  # A release whose upload applied a Durable Object migration says so, with the
  # tag and the version that applied it, on every deployment row it writes.
  [ -z "${DO_MIGRATION_EVIDENCE:-}" ] || rd_evidence_ref="$rd_evidence_ref;$DO_MIGRATION_EVIDENCE"
  rd_failure_args=""
  if [ "$rd_state" = "failed" ]; then
    rd_failure_args="--failure-class ${DEPLOYMENT_FAILURE_CLASS:-golden_workflow_failed}"
  fi
  # shellcheck disable=SC2086
  "$PY" "$REPO/tools/ops-record.py" deployment \
    --service carr-mcp --environment "$TARGET_ENV" --state "$rd_state" \
    --git-sha "$HEAD_SHA" $rd_provider_args $rd_verb_args \
    ${rd_corr:+--correlation "$rd_corr"} \
    ${RELEASE_KEY:+--release-key "$RELEASE_KEY"} \
    ${rd_state:+--source-kind wrapper} --source-ref bin/deploy-worker.sh \
    $rd_read_back \
    --verification-evidence-ref "$rd_evidence_ref" \
    $rd_failure_args \
    >/dev/null 2>&1
  rd_rc=$?
  set -e
  if [ "$rd_rc" -ne 0 ]; then
    echo "  !! the deploy shipped but its ledger row did NOT record (exit $rd_rc)."
    echo "     Record it by hand so the release keeps its deployment:"
    echo "       .venv/bin/python tools/ops-record.py deployment --service carr-mcp \\"
    echo "         --environment $TARGET_ENV --state $rd_state --git-sha $HEAD_SHA \\"
    [ "$TARGET_ENV" != "production" ] || \
      echo "         --provider $PROVIDER --provider-version-id $PROVIDER_VERSION_ID \\"
    echo "         --release-key ${RELEASE_KEY:-<key>} --source-kind wrapper \\"
    echo "         --source-ref bin/deploy-worker.sh \\"
    [ -z "$rd_read_back" ] || echo "         $rd_read_back \\"
    echo "         --verification-evidence-ref $rd_evidence_ref"
    [ "$rd_must_record" = "0" ] || return 1
  else
    echo "  recorded this deploy as $rd_state against release ${RELEASE_KEY:-<none>}"
  fi

  # AND CLOSE THE RELEASE, when the deploy actually landed and was verified.
  # Without this the release sits at `approved` while its own deployment reads
  # `complete` — observed on the first real release, 2026-08-16, where the
  # manifest view showed a landed deploy against a release still waiting to
  # ship. Only the complete path closes it: `verifying` means nothing was
  # proven and `failed` means the opposite, and neither is a finished release.
  if [ "$rd_state" = "complete" ] && [ -n "$RELEASE_KEY" ]; then
    set +e
    # Approval owns the independent verifier pair immutably.  Completion adds
    # deployment/performance evidence through its own gates; it must not try to
    # replace that approval-bound verifier with the golden-suite runner.
    "$PY" "$REPO/tools/ops-record.py" release complete --key "$RELEASE_KEY" >/dev/null 2>&1
    rc_rel=$?
    set -e
    if [ "$rc_rel" -eq 0 ]; then
      echo "  release $RELEASE_KEY is complete"
    else
      echo "  !! the deploy landed but release $RELEASE_KEY did not close (exit $rc_rel)."
      echo "     Close it by hand so the release and its deployment stop disagreeing:"
      echo "       .venv/bin/python tools/ops-record.py release complete --key $RELEASE_KEY"
      [ "$TARGET_ENV" != "production" ] || return 1
    fi
  fi
  return 0
}

# ---------- preflight 4: release truth (P0-1) ----------
# THE DEPLOY IS THE MOMENT THE MANIFEST HAS TO EXIST, not a step somebody
# performs beside it. Every earlier preflight asks whether the ARTIFACT is
# right; this one asks whether anyone APPROVED it, and it is the wrapper half
# of P0-1. The database half (migration 0130) refuses a production deployment
# naming an unapproved or expired release, so this check exists to fail EARLY
# and legibly rather than half way through a wrangler run.
#
# It builds the manifest from the SHA about to ship — the same tools/
# release-manifest.py any human or CI run would use, never a second copy of the
# digest decision (rule a8c55a47) — and hands the plan hash to the require
# check, so an approval given to a DIFFERENT plan is caught here by name.
#
# NOT ENFORCED IS SAID OUT LOUD. Where DATABASE_URL is absent, or 0130 has not
# been applied, the require check prints exactly that and returns success. A
# wrapper that refused on the database's behalf would be claiming a protection
# the database is not providing, and any other deploy path would bypass it
# anyway. Silence is the only outcome ruled out.
RELEASE_KEY="$REQUESTED_RELEASE_KEY"
RELEASE_PLAN_HASH=""
RELEASE_MANIFEST=""
if [ "$VERSION_MODE" = "promote" ]; then
  echo ""
  echo "== preflight: immutable release truth =="
  set +e
  RELEASE_BINDING="$("$PY" "$REPO/tools/ops-record.py" release locate \
    --environment production --provider "$PROVIDER" \
    --provider-version-id "$PROVIDER_VERSION_ID")"
  REQUIRE_RC=$?
  set -e
  [ "$REQUIRE_RC" -eq 0 ] \
    || fail "no unique recorded candidate binds Production to $PROVIDER:$PROVIDER_VERSION_ID."
  # Production provider lookup returns exactly `<release-key> <git-sha>` so
  # promotion provenance comes from the recorded immutable object, not HEAD.
  set -- $RELEASE_BINDING
  [ "$#" -eq 2 ] \
    || fail "release truth returned no exact release/SHA binding for $PROVIDER_VERSION_ID."
  RELEASE_KEY="$1"
  HEAD_SHA="$2"
  printf '%s\n' "$HEAD_SHA" | grep -Eq '^[0-9A-Fa-f]{40}$' \
    || fail "recorded release $RELEASE_KEY has no canonical git SHA."
  echo "  recorded release: $RELEASE_KEY"
  echo "  provider version: $PROVIDER_VERSION_ID"
  echo "  recorded git SHA: $HEAD_SHA"

  # The first exact UUID lookup reveals the SHA the candidate binds. Recompute
  # the evidence from that git object without uploading or building a Worker,
  # bind the same canonical provider UUID, then record technical readiness
  # using that exact immutable plan if it has not already been recorded.
  PROMOTION_SOURCE_MANIFEST="$(mktemp "${TMPDIR:-/tmp}/carr-promotion-source-manifest.XXXXXX")"
  PROMOTION_BOUND_MANIFEST="$(mktemp "${TMPDIR:-/tmp}/carr-promotion-bound-manifest.XXXXXX")"
  if ! build_release_manifest "$HEAD_SHA" production > "$PROMOTION_SOURCE_MANIFEST"; then
    fail "release evidence cannot be rebuilt from git SHA $HEAD_SHA."
  fi
  if ! "$PY" "$REPO/tools/release-manifest.py" bind-provider \
      --manifest "$PROMOTION_SOURCE_MANIFEST" --provider "$PROVIDER" \
      --provider-version-id "$PROVIDER_VERSION_ID" > "$PROMOTION_BOUND_MANIFEST"; then
    fail "provider identity cannot be rebound to recomputed evidence."
  fi
  RELEASE_MANIFEST="$PROMOTION_BOUND_MANIFEST"
  RELEASE_PLAN_HASH="$("$PY" "$REPO/tools/release-manifest.py" plan-hash \
    --manifest "$RELEASE_MANIFEST")"
  [ -n "$RELEASE_PLAN_HASH" ] \
    || fail "recomputed provider-bound evidence produced no plan hash."
  if ! "$PY" "$REPO/tools/ops-record.py" release require \
      --sha "$HEAD_SHA" --environment production --provider "$PROVIDER" \
      --provider-version-id "$PROVIDER_VERSION_ID" \
      --plan-hash "$RELEASE_PLAN_HASH" >/dev/null 2>&1; then
    RELEASE_READY_IDEMPOTENCY="$("$PY" -c 'import sys,uuid; print(uuid.uuid5(uuid.UUID("b8912ba6-4df7-44f3-9d7c-151edcc4372d"),sys.argv[1]+"\0"+sys.argv[2]))' "$RELEASE_KEY" "$RELEASE_PLAN_HASH")"
    "$PY" "$REPO/tools/ops-record.py" release ready \
      --key "$RELEASE_KEY" --plan-hash "$RELEASE_PLAN_HASH" \
      --idempotency-key "$RELEASE_READY_IDEMPOTENCY" >/dev/null \
      || fail "technical release readiness needs a fresh typed staging recovery rehearsal; traffic was not changed."
  fi
  set +e
  RECONFIRMED_BINDING="$("$PY" "$REPO/tools/ops-record.py" release require \
    --sha "$HEAD_SHA" --environment production --provider "$PROVIDER" \
    --provider-version-id "$PROVIDER_VERSION_ID" \
    --plan-hash "$RELEASE_PLAN_HASH")"
  REQUIRE_RC=$?
  set -e
  if [ "$REQUIRE_RC" -ne 0 ]; then
    if "$PY" "$REPO/tools/ops-record.py" release reopen \
        --key "$RELEASE_KEY" --plan-hash "$RELEASE_PLAN_HASH" >/dev/null 2>&1; then
      fail "readiness became stale. The release is reopened for the typed staging recovery rehearsal; rerun promotion after fresh recovery evidence. Traffic was not changed."
    fi
    fail "readiness does not match the recomputed SHA/provider/version plan."
  fi
  [ "$RECONFIRMED_BINDING" = "$RELEASE_BINDING" ] \
    || fail "release binding changed between UUID resolution and final readiness check."
  echo "  recomputed plan: $RELEASE_PLAN_HASH"
elif [ "$RECOVERY_STEP" != "standalone" ]; then
  echo ""
  echo "== preflight: typed recovery release truth =="
  echo "  candidate release: $RELEASE_KEY"
  echo "  prior release: $RECOVERY_PRIOR_RELEASE_KEY"
  echo "  recovery attempt/step: $RECOVERY_ATTEMPT_ID / $RECOVERY_STEP"
  RELEASE_MANIFEST="$(mktemp "${TMPDIR:-/tmp}/carr-recovery-release-manifest.XXXXXX")"
  build_release_manifest "$HEAD_SHA" staging > "$RELEASE_MANIFEST" \
    || fail "recovery release evidence cannot be rebuilt from git SHA $HEAD_SHA."
elif [ -f "$REPO/tools/release-manifest.py" ]; then
  echo ""
  echo "== preflight: release truth =="
  RELEASE_MANIFEST="$(mktemp "${TMPDIR:-/tmp}/carr-release-manifest.XXXXXX")"
  if build_release_manifest "$HEAD_SHA" "$TARGET_ENV" \
       > "$RELEASE_MANIFEST" 2>/dev/null; then
    RELEASE_PLAN_HASH="$("$PY" "$REPO/tools/release-manifest.py" plan-hash \
                          --manifest "$RELEASE_MANIFEST" 2>/dev/null)"
    echo "  manifest built for ${HEAD_SHA} — plan ${RELEASE_PLAN_HASH:-unknown}"
  else
    echo "  !! could not build the release manifest for $HEAD_SHA"
  fi

  if [ "$VERSION_MODE" = "upload" ]; then
    [ -n "$RELEASE_PLAN_HASH" ] \
      || fail "the release manifest did not produce a plan hash; version upload refused."
    echo "  upload may proceed; technical readiness binds the immutable version after its evidence is recorded"
  else
    set +e
    RELEASE_KEY="$("$PY" "$REPO/tools/ops-record.py" release require \
                     --sha "$HEAD_SHA" --environment "$TARGET_ENV" \
                     --plan-hash "$RELEASE_PLAN_HASH")"
    REQUIRE_RC=$?
    set -e
    if [ "$REQUIRE_RC" -eq 3 ]; then
      fail "no exact release readiness for $HEAD_SHA in $TARGET_ENV. The reason and the exact
commands are printed above."
    fi
    [ -n "$RELEASE_KEY" ] && echo "  ready release: $RELEASE_KEY"
  fi
fi

# Read the expected Program 6 posture from the manifest reconstructed from the
# exact release SHA, never from this checkout or a hard-coded activation bit.
# Its source config fingerprint is in the approval preimage; this turns the
# serving readback into a comparison against the reviewed immutable plan.
[ -n "${RELEASE_MANIFEST:-}" ] && [ -s "$RELEASE_MANIFEST" ] \
  || fail "release manifest is required to bind the Program 6 serving posture."
EXPECTED_PROGRAM6_ACTIONS="$("$PY" "$REPO/tools/release-manifest.py" \
  program6-posture --manifest "$RELEASE_MANIFEST")" \
  || fail "release manifest has no valid Program 6 posture."
case "$EXPECTED_PROGRAM6_ACTIONS" in
  enabled|disabled) ;;
  *) fail "release manifest returned an invalid Program 6 posture." ;;
esac
EXPECTED_SCHEMA_HIGHEST_MIGRATION="$("$PY" -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["schema_highest_migration"])' \
  "$RELEASE_MANIFEST")" \
  || fail "release manifest has no declared schema highest migration."
EXPECTED_SCHEMA_APPLIED_COUNT="$("$PY" -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["schema_applied_count"])' \
  "$RELEASE_MANIFEST")" \
  || fail "release manifest has no declared schema applied count."
EXPECTED_SCHEMA_LEDGER_SHA256="$("$PY" -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["schema_ledger_sha256"])' \
  "$RELEASE_MANIFEST")" \
  || fail "release manifest has no declared schema ledger digest."
printf '%s' "$EXPECTED_SCHEMA_HIGHEST_MIGRATION" \
  | grep -Eq '^[0-9]{4}[a-z]?_[a-z0-9_.-]+\.sql$' \
  || fail "release manifest declared an invalid schema highest migration."
printf '%s' "$EXPECTED_SCHEMA_APPLIED_COUNT" \
  | grep -Eq '^[1-9][0-9]*$' \
  || fail "release manifest declared an invalid schema applied count."
printf '%s' "$EXPECTED_SCHEMA_LEDGER_SHA256" | grep -Eq '^sha256:[0-9a-f]{64}$' \
  || fail "release manifest declared an invalid schema ledger digest."

if [ "$CHECK_ONLY" = "1" ]; then
  echo ""
  echo "check only — nothing deployed."
  exit 0
fi

# Cost admission binds the release wrapper before any Wrangler operation.  The
# preflights above are the evidence; a direct Wrangler call remains outside the
# sanctioned path and is independently refused by the command gate.
"$PY" "$REPO/ops/platform-metering-gate.py" --gate cloudflare-worker-release \
  --release-preflight-green \
  --performance-budget-ref "${PERFORMANCE_BUDGET_REF:-${RELEASE_PLAN_HASH:-staging-release-preflight}}" \
  --release-candidate-count 1 >/dev/null \
  || fail "cloudflare-worker-release metering admission refused."

# THE SEALER IS IMPORTED, NOT EXECUTED, and that is a registry fact rather than a
# preference: ops/scac-mutation-inventory.mjs enumerates every tracked file with a
# shebang or a command-line main as a script ENTRYPOINT, and an entrypoint is an
# ingress whose admission only a sealed registry successor may perform. So
# mcp-server/bin/seal-candidate-manifest.mjs carries neither, and this one
# evaluation imports `sealCandidateManifest` and prints the field it is asked for.
# Every input travels as an environment variable, so no path or revision is ever
# spliced into the evaluated source.
seal_candidate_field() {
  CARR_SEALER="$SEALER" CARR_SEAL_REPO="$SOURCE_ROOT" CARR_SEAL_REV="$HEAD_SHA" \
  CARR_SEAL_FIELD="$1" node --input-type=module -e '
    const sealer = await import(new URL("file://" + process.env.CARR_SEALER).href);
    const sealed = sealer.sealCandidateManifest(process.env.CARR_SEAL_REPO,
                                                process.env.CARR_SEAL_REV);
    process.stdout.write(process.env.CARR_SEAL_FIELD === "digest"
      ? sealed.digest : sealed.manifest_text);
  '
}

# ---------- seal the candidate manifest (standing-rule amendment 9) ----------
#
# WHY THIS STEP EXISTS. The Gate Zero producer used to derive the candidate it
# judges by reading `.git` at request time, inside the Worker. The deployed
# Worker has no checkout — Cloudflare serves the bundled modules over a read-only
# virtual filesystem and supplies no `.git` — so that derivation could only ever
# refuse in production. The work belongs where a checkout exists, which is here.
#
# WHAT IS STAMPED, and it is stamped exactly the way GIT_SHA already is: two more
# `--var` values on the SAME wrangler invocation, scoped to this upload, absent
# from any deploy that bypasses this script. `CANDIDATE_MANIFEST` is the sealed
# manifest's own JCS text — the candidate tree id, the file count and byte
# length, the digest of the blob ids the revision sealed, the digest of those
# blobs' contents, and the environment-manifest and fixture-set digests —
# and `CANDIDATE_MANIFEST_DIGEST` is its digest, computed by the same recipe. The
# producer re-digests the manifest and refuses unless the two agree, so a var
# edited after the seal is a refusal rather than a signature.
#
# THE REVISION IS $HEAD_SHA, which is whatever this run is actually deploying:
# the checkout's own HEAD on an ordinary deploy, and the SHA the approver signed
# on a promotion. The sealer resolves it in the object store and fails visibly if
# that object is not present, so a manifest is never sealed for a revision this
# machine cannot read.
#
# SKIPPED ON A PROMOTION, deliberately: an immutable provider promotion uploads
# no new version, so there is no invocation to stamp — the vars the version
# carries are the ones its own upload wrote.
CANDIDATE_MANIFEST=""
CANDIDATE_MANIFEST_DIGEST=""
LEGACY_PRIOR_WITHOUT_CANDIDATE_STAMP=0
# Both stamp components first entered canonical main in this reviewed commit.
# Keep independently named boundaries so a future split introduction cannot be
# collapsed into file absence, which a later source can manufacture by delete.
BUILD_STAMP_INTRODUCTION_SHA="ab9678a86f427e8f9e5d1f75597a21b920630995"
CANDIDATE_SEALER_INTRODUCTION_SHA="ab9678a86f427e8f9e5d1f75597a21b920630995"
exact_source_predates_candidate_stamps() {
  [ "$HEAD_SHA" != "$BUILD_STAMP_INTRODUCTION_SHA" ] \
    && [ "$HEAD_SHA" != "$CANDIDATE_SEALER_INTRODUCTION_SHA" ] \
    && git -C "$SOURCE_ROOT" merge-base --is-ancestor \
      "$HEAD_SHA" "$BUILD_STAMP_INTRODUCTION_SHA" \
    && git -C "$SOURCE_ROOT" merge-base --is-ancestor \
      "$HEAD_SHA" "$CANDIDATE_SEALER_INTRODUCTION_SHA"
}
prepare_candidate_stamps() {
  [ "$VERSION_MODE" != "promote" ] || return 0
  SEALER="$WORKER_DIR/bin/seal-candidate-manifest.mjs"
  if [ ! -f "$SEALER" ]; then
    if [ "$VERSION_MODE" = "ordinary" ] && [ "$TARGET_ENV" = "staging" ] \
        && [ "$RECOVERY_STEP" = "prior" ] && [ -n "$EXACT_SOURCE_ROOT" ] \
        && [ ! -e "$WORKER_DIR/src/build-stamp.js" ] \
        && exact_source_predates_candidate_stamps; then
      LEGACY_PRIOR_WITHOUT_CANDIDATE_STAMP=1
      echo "  legacy prior source predates the candidate-stamp contract; candidate stamps omitted"
      return 0
    fi
    fail "the candidate sealer is missing at $SEALER; the Gate Zero producer would ship unable to name its candidate."
  fi
  CANDIDATE_MANIFEST="$(seal_candidate_field manifest)" \
    || fail "could not seal the candidate manifest for $HEAD_SHA."
  CANDIDATE_MANIFEST_DIGEST="$(seal_candidate_field digest)" \
    || fail "could not digest the sealed candidate manifest for $HEAD_SHA."
  [ -n "$CANDIDATE_MANIFEST" ] && [ -n "$CANDIDATE_MANIFEST_DIGEST" ] \
    || fail "the candidate sealer produced an empty manifest or digest for $HEAD_SHA."
  echo "  sealed candidate manifest $CANDIDATE_MANIFEST_DIGEST for $HEAD_SHA"
}
prepare_candidate_stamps

deploy_staging_worker() {
  if [ "$LEGACY_PRIOR_WITHOUT_CANDIDATE_STAMP" = "1" ]; then
    "$WRANGLER" deploy --env "$TARGET_ENV" --var "GIT_SHA:$HEAD_SHA" \
      --tag "$DEPLOY_TAG"
  else
    "$WRANGLER" deploy --env "$TARGET_ENV" --var "GIT_SHA:$HEAD_SHA" \
      --var "CANDIDATE_MANIFEST:$CANDIDATE_MANIFEST" \
      --var "CANDIDATE_MANIFEST_DIGEST:$CANDIDATE_MANIFEST_DIGEST" --tag "$DEPLOY_TAG"
  fi
}

# ---------- pending Durable Object migration (BEGIN do-migration block) ----------
#
# WHY. Production ships through `wrangler versions upload` + an exact
# `versions deploy <id>@100`, and wrangler 4.137 REFUSES `versions upload` while
# the Worker has a Durable Object migration it has not applied. Cloudflare's own
# documentation: a Durable Object lifecycle change "can only be applied via
# `wrangler deploy`". The first release whose wrangler.toml adds a [[migrations]]
# tag (PR #1244, `v1-workflow-census-anchor`) would therefore stop the unattended
# release pipeline at its upload step, every time, with no retry able to help.
#
# WHAT HAPPENS INSTEAD, in --upload-version only (staging already uses plain
# deploy and is untouched):
#   1. READ the applied tag from the same Cloudflare metadata wrangler reads
#      (ops/worker-do-migration.py documents the endpoint and the decision).
#      Unknown is REFUSED before anything is uploaded or deployed.
#   2. Nothing pending: the ordinary upload runs exactly as before.
#   3. Pending, and still before anything moves:
#      a. PRODUCTION'S ATTACHMENTS. A plain deploy re-publishes the top-level
#         custom domains (wrangler replaces the whole set and, outside a TTY,
#         overrides existing origins and DNS records) and the workers.dev
#         setting. Production's live set is read and must already equal what
#         the deploy would publish; any difference is REFUSED, never applied.
#      b. STAGING GOES FIRST. The exact release SHA is deployed to the staging
#         Worker with plain `wrangler deploy --env staging` (after the same
#         attachment check every staging deploy passes), staging's applied tag
#         is re-read and must now be the declared latest, and staging /release
#         must read back the exact SHA, staging version, Program 6 posture and
#         schema. A tag is applied once, so a staging that ALREADY carried the
#         tag is accepted only when a durable per-tag receipt (kept outside the
#         pipeline's release worktree, which is deleted) proves it was applied
#         with the migration steps wrangler.toml declares now.
#   4. Staging green: apply it to Production the documented way, a
#      `wrangler deploy` of this exact release SHA with the same GIT_SHA /
#      candidate-manifest stamps (and probe-token secret) the upload would carry.
#      The moment it returns, the applied tag is re-read and the change is
#      written to ops.settings_change (the control-plane change log), a marker
#      line tells the pipeline whether the migration is applied or possibly
#      applied, and Production /release is read back against that exact
#      provider version.
#   5. THE MIGRATION DEPLOY'S VERSION IS THIS RELEASE'S CANDIDATE. No second
#      version is uploaded: the version now serving is the one the candidate
#      record names, the typed staging rehearsal rehearses, and promotion
#      verifies (identity read-back, golden suite, performance gate, ledger).
#
# WHAT CANNOT MOVE EARLIER, and why. The typed staging rehearsal's database
# writer (ops.prepare_staging_forward_fix_rehearsal) requires a Production
# candidate naming a provider version, and ops.deployment_requires_a_live_approval
# refuses any Production ops.deployment row before technical readiness, which
# needs that rehearsal. No Production provider version can exist before the
# migration deploy moves traffic. So for a migration release the typed rehearsal,
# the golden suite, the performance gate and the first ops.deployment row all
# come AFTER Production has moved; the precheck above is every check that needs
# no uploaded version, and ops.settings_change plus the candidate record are the
# database's account of the move until promotion writes its rows. The production
# golden suite is deliberately NOT pointed at staging: it asserts production data
# fixtures, and the staging database holds invented ones.
#
# ROLLBACK, also out loud. Cloudflare blocks rollback to any version from before
# a Durable Object lifecycle change. Once the tag is applied the recovery is
# forward fix only (the pipeline lane's recovery_strategy already is); a failure
# message below says which side of that line the Worker is on.
DO_MIGRATION_HELPER="$REPO/ops/worker-do-migration.py"
DO_MIGRATION_RECEIPT_DIR="$REPO/out/deploy-worker"
DO_MIGRATION_RELEASE_URL="https://api.doctorcre.com/release"
DO_MIGRATION_EVIDENCE=""
DO_MIGRATION_ROLLBACK_NOTE=""
# Set only when the migration deploy's own version became this release's candidate.
DO_MIGRATION_VERSION_ID=""
# What every later refusal in the upload branch says about traffic.
DO_TRAFFIC_CLAUSE="traffic was not changed"
DO_SCRIPT=""
DO_STEPS_DIGEST=""
DO_STATE_DIR=""
DO_REFUSAL=""
DO_DB_RECORD=""
DO_STAGING_PRECHECK=""
DO_STAGING_REASON=""
DO_STAGING_OLD_TAG=""
DO_STAGING_TAG_MOVED=""
DO_STAGING_VERSION_ID=""
DO_STAGING_DEPLOY_EXIT=""

# GET https://api.cloudflare.com/client/v4/accounts/<acct>/<path> into <out>.
# The bearer token is the one wrangler itself resolves (CLOUDFLARE_API_TOKEN or
# its OAuth login). It travels only through a pipe into curl's --config stdin:
# never argv, never a file, never a log.
do_migration_cf_get() {
  dmf_json="$("$WRANGLER" auth token --json 2>/dev/null)" || return 1
  dmf_token="$(printf '%s' "$dmf_json" | "$PY" -c '
import json, sys
try:
    value = json.load(sys.stdin)
except ValueError:
    raise SystemExit(1)
if not isinstance(value, dict):
    raise SystemExit(1)
token = value.get("token")
if value.get("type") not in ("api_token", "oauth") or not isinstance(token, str) or not token.strip():
    raise SystemExit(1)
print(token.strip())
')" || return 1
  printf 'header = "Authorization: Bearer %s"\n' "$dmf_token" \
    | curl --config - --fail --silent --show-error --max-time 30 --max-filesize 1048576 \
        -o "$3" "https://api.cloudflare.com/client/v4/accounts/$1/$2" \
        2>/dev/null
}

# Prints the plan JSON for environment $1 (production = the top-level Worker).
# Exit 0 = determined, 3 = applied tag unknown, other = the config is unusable.
do_migration_plan() {
  dmp_env="$1"
  dmp_target="$("$PY" "$DO_MIGRATION_HELPER" target \
    --config "$WORKER_DIR/wrangler.toml" --env "$dmp_env")" || return 2
  dmp_fields="$(printf '%s' "$dmp_target" | "$PY" -c '
import json, sys
t = json.load(sys.stdin)
print(t["account_id"], t["script"], len(t["declared_tags"]))')" || return 2
  set -- $dmp_fields
  [ "$#" -eq 3 ] || return 2
  if [ "$3" = "0" ]; then
    # Nothing declared: nothing can be pending, and no read is needed.
    "$PY" "$DO_MIGRATION_HELPER" plan --config "$WORKER_DIR/wrangler.toml" \
      --env "$dmp_env" --services-json /dev/null
    return $?
  fi
  dmp_services="$(mktemp "${TMPDIR:-/tmp}/carr-do-services.XXXXXX")" || return 3
  chmod 600 "$dmp_services"
  if ! do_migration_cf_get "$1" "workers/services/$2" "$dmp_services"; then
    rm -f "$dmp_services"
    echo "  the Worker's service metadata could not be read with wrangler's credential" >&2
    return 3
  fi
  set +e
  "$PY" "$DO_MIGRATION_HELPER" plan --config "$WORKER_DIR/wrangler.toml" \
    --env "$dmp_env" --services-json "$dmp_services"
  dmp_rc=$?
  set -e
  rm -f "$dmp_services"
  return "$dmp_rc"
}

do_migration_field() {
  printf '%s' "$1" | "$PY" -c '
import json, sys
v = json.load(sys.stdin).get(sys.argv[1])
print("none" if v is None else (" ".join(v) if isinstance(v, list) else str(v).lower() if isinstance(v, bool) else v))' "$2"
}

# The durable per-tag receipts live beside the MAIN checkout, never inside the
# release worktree the pipeline deletes after every run.
do_migration_state_dir() {
  if [ -n "${CARR_DO_MIGRATION_STATE_DIR:-}" ]; then
    printf '%s\n' "$CARR_DO_MIGRATION_STATE_DIR"
    return 0
  fi
  dsd_common="$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 1
  [ -n "$dsd_common" ] || return 1
  printf '%s\n' "$(dirname "$dsd_common")/out/deploy-worker/do-migration-tags"
}

# do_migration_write_receipt <old> <new> <pending> <version> <state> <deploy-exit|none> <readback>
do_migration_write_receipt() {
  mkdir -p "$DO_MIGRATION_RECEIPT_DIR"
  DO_STAGING_PRECHECK="$DO_STAGING_PRECHECK" DO_STAGING_REASON="$DO_STAGING_REASON" \
  DO_STAGING_OLD_TAG="$DO_STAGING_OLD_TAG" DO_STAGING_TAG_MOVED="$DO_STAGING_TAG_MOVED" \
  DO_STAGING_VERSION_ID="$DO_STAGING_VERSION_ID" DO_STAGING_DEPLOY_EXIT="$DO_STAGING_DEPLOY_EXIT" \
  DO_STEPS_DIGEST="$DO_STEPS_DIGEST" DO_REFUSAL="$DO_REFUSAL" DO_DB_RECORD="$DO_DB_RECORD" \
  "$PY" -c '
import datetime, json, os, sys
keys = ("git_sha", "script", "old_tag", "new_tag", "pending_tags", "migration_version_id",
        "state", "deploy_exit", "readback")
row = dict(zip(keys, sys.argv[2:]))
row["pending_tags"] = row["pending_tags"].split()
row["old_tag"] = None if row["old_tag"] == "none" else row["old_tag"]
row["migration_version_id"] = row["migration_version_id"] or None
# none = the Production migration deploy never ran.
row["deploy_exit"] = None if row["deploy_exit"] == "none" else int(row["deploy_exit"])
row["schema"] = "carr-worker-do-migration-receipt.v1"
row["source_ref"] = "bin/deploy-worker.sh"
row["applied_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
env = os.environ
row["steps_digest"] = env.get("DO_STEPS_DIGEST") or None
row["refusal"] = env.get("DO_REFUSAL") or None
row["db_record"] = env.get("DO_DB_RECORD") or "not-run"
moved = env.get("DO_STAGING_TAG_MOVED")
staging_exit = env.get("DO_STAGING_DEPLOY_EXIT")
row["staging_precheck"] = {
    "state": env.get("DO_STAGING_PRECHECK") or "not-run",
    "reason": env.get("DO_STAGING_REASON") or None,
    "old_tag": None if env.get("DO_STAGING_OLD_TAG") in (None, "", "none") else env["DO_STAGING_OLD_TAG"],
    # null = staging was never re-read after a deploy, so movement is unknown.
    "tag_moved": True if moved == "true" else False if moved == "false" else None,
    "deploy_exit": int(staging_exit) if staging_exit else None,
    "version_id": env.get("DO_STAGING_VERSION_ID") or None,
}
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps(row, sort_keys=True) + "\n")
' "$DO_MIGRATION_RECEIPT_DIR/do-migration-$HEAD_SHA.json" "$HEAD_SHA" "$DO_SCRIPT" "$@"
  echo "  migration receipt: $DO_MIGRATION_RECEIPT_DIR/do-migration-$HEAD_SHA.json ($5)"
}

# do_migration_readback <version> <environment> <release-url>
do_migration_readback() {
  dmr_attempts="${CARR_READBACK_ATTEMPTS:-12}"
  dmr_sleep="${CARR_READBACK_SLEEP:-5}"
  dmr_n=0
  while [ "$dmr_n" -lt "$dmr_attempts" ]; do
    dmr_n=$((dmr_n + 1))
    if dmr_body="$(curl --fail --silent --show-error --max-time 30 \
          "$3" 2>/dev/null)" \
       && printf '%s' "$dmr_body" | "$PY" "$REPO/ops/verify-worker-release.py" \
          --environment "$2" --sha "$HEAD_SHA" --provider "$PROVIDER" \
          --provider-version-id "$1" \
          --expected-program6-actions "$EXPECTED_PROGRAM6_ACTIONS" \
          --expected-schema-highest-migration "$EXPECTED_SCHEMA_HIGHEST_MIGRATION" \
          --expected-schema-applied-count "$EXPECTED_SCHEMA_APPLIED_COUNT" >/dev/null 2>&1; then
      echo "  OK  $2 serves $HEAD_SHA / $1 (read-back attempt $dmr_n)"
      return 0
    fi
    if [ "$dmr_n" -lt "$dmr_attempts" ]; then
      sleep "$dmr_sleep"
    fi
  done
  return 1
}

# Refuse before Production is touched, with the reason in the receipt.
do_migration_refuse_untouched() {
  DO_REFUSAL="$1"
  do_migration_write_receipt "$DO_OLD_TAG" "$DO_NEW_TAG" "$DO_PENDING_TAGS" "" not_applied none not-run
  fail "$2
  Production was not touched: no production deploy, no upload, no candidate.
  Production traffic was not changed."
}

do_migration_staging_refuse() {
  DO_STAGING_PRECHECK="failed"
  DO_STAGING_REASON="$1"
  do_migration_refuse_untouched "staging precheck: $1" \
    "the staging precheck for Durable Object migration $DO_NEW_TAG failed: $1."
}

# A plain Production deploy re-publishes the top-level custom domains and the
# workers.dev setting. Refuse unless Production already has exactly those.
do_migration_attachment_check() {
  echo ""
  echo "== Durable Object migration: Production attachments =="
  dac_account="$(do_migration_field "$DO_TARGET" account_id)"
  dac_domains="$(mktemp "${TMPDIR:-/tmp}/carr-do-domains.XXXXXX")"
  dac_subdomain="$(mktemp "${TMPDIR:-/tmp}/carr-do-subdomain.XXXXXX")"
  if ! do_migration_cf_get "$dac_account" "workers/domains?service=$DO_SCRIPT" "$dac_domains" \
      || ! do_migration_cf_get "$dac_account" "workers/scripts/$DO_SCRIPT/subdomain" "$dac_subdomain"; then
    rm -f "$dac_domains" "$dac_subdomain"
    do_migration_refuse_untouched "production attachments unreadable" \
      "Production's custom domains and workers.dev setting could not be read, so what the
  migration deploy would change on them is UNKNOWN."
  fi
  set +e
  "$PY" "$DO_MIGRATION_HELPER" attachments --config "$WORKER_DIR/wrangler.toml" \
    --domains-json "$dac_domains" --subdomain-json "$dac_subdomain" >/dev/null
  dac_rc=$?
  set -e
  rm -f "$dac_domains" "$dac_subdomain"
  case "$dac_rc" in
    0) echo "  OK  Production's custom domains and workers.dev setting already equal what the deploy publishes" ;;
    4) do_migration_refuse_untouched "production attachments differ" \
         "a plain wrangler deploy would change Production's hostnames or workers.dev setting
  (printed above). Reconcile wrangler.toml with Production first; the migration
  release does not change attachments as a side effect." ;;
    *) do_migration_refuse_untouched "production attachments not comparable" \
         "Production's attachments could not be compared with what the deploy publishes
  (exit $dac_rc; printed above)." ;;
  esac
}

do_migration_staging_precheck() {
  echo ""
  echo "== Durable Object migration: staging precheck =="
  DO_STAGING_PRECHECK="running"
  DO_STATE_DIR="$(do_migration_state_dir)" \
    || do_migration_staging_refuse "the durable migration receipt directory could not be located"
  dsp_host="$("$PY" "$REPO/tools/ops-record.py" staging-target --field host 2>/dev/null)" \
    || do_migration_staging_refuse "the checked-in staging target is not exact"
  dsp_name="$("$PY" "$REPO/tools/ops-record.py" staging-target --field worker_name 2>/dev/null)" \
    || do_migration_staging_refuse "the checked-in staging target is not exact"
  dsp_script="$("$PY" "$DO_MIGRATION_HELPER" target --config "$WORKER_DIR/wrangler.toml" --env staging \
    | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["script"])' 2>/dev/null)" \
    || do_migration_staging_refuse "wrangler.toml has no usable [env.staging] Worker"
  [ -n "$dsp_host" ] && [ "$dsp_name" = "$dsp_script" ] \
    || do_migration_staging_refuse "wrangler.toml's staging Worker ($dsp_script) is not the checked-in staging target ($dsp_name)"
  # The same guard every staging deploy passes: a staging deploy must never
  # attach to Production's hostnames (the 2026-08-13 routes incident).
  "$PY" "$REPO/ops/deploy-attachment-check.py" "$WORKER_DIR/wrangler.toml" staging \
    || do_migration_staging_refuse "the staging attachment check refused"
  set +e
  dsp_before="$(do_migration_plan staging)"
  dsp_rc=$?
  set -e
  [ "$dsp_rc" -eq 0 ] \
    || do_migration_staging_refuse "staging's applied migration tag could not be determined"
  [ "$(do_migration_field "$dsp_before" latest_tag)" = "$DO_NEW_TAG" ] \
    || do_migration_staging_refuse "staging and Production declare different latest migration tags"
  [ "$(do_migration_field "$dsp_before" steps_digest)" = "$DO_STEPS_DIGEST" ] \
    || do_migration_staging_refuse "staging and Production declare different migration steps"
  DO_STAGING_OLD_TAG="$(do_migration_field "$dsp_before" applied_tag)"
  if [ "$DO_STAGING_OLD_TAG" = "$DO_NEW_TAG" ]; then
    # A tag is applied ONCE. Staging carrying it proves nothing about the steps
    # it was applied with, unless a durable receipt for this tag says so.
    set +e
    dsp_check="$("$PY" "$DO_MIGRATION_HELPER" tag-receipt check --dir "$DO_STATE_DIR" \
      --script "$dsp_script" --tag "$DO_NEW_TAG" --digest "$DO_STEPS_DIGEST")"
    dsp_check_rc=$?
    set -e
    if [ "$dsp_check_rc" -ne 0 ]; then
      dsp_why="$(do_migration_field "$dsp_check" reason 2>/dev/null || echo "the receipt check failed (exit $dsp_check_rc)")"
      do_migration_staging_refuse "staging already carries $DO_NEW_TAG, but no durable receipt proves it was applied with the steps wrangler.toml declares now ($dsp_why); a tag is applied only once, so changed steps need a new tag"
    fi
    echo "  staging already carries $DO_NEW_TAG, applied with these exact steps (durable receipt)"
  fi
  echo "  staging applied: $DO_STAGING_OLD_TAG; deploying $HEAD_SHA to staging"
  set +e
  dsp_output="$("$WRANGLER" deploy --env staging --var "GIT_SHA:$HEAD_SHA" \
    --var "CANDIDATE_MANIFEST:$CANDIDATE_MANIFEST" \
    --var "CANDIDATE_MANIFEST_DIGEST:$CANDIDATE_MANIFEST_DIGEST" \
    --message "carr do-migration staging precheck $DO_NEW_TAG $HEAD_SHA" 2>&1)"
  DO_STAGING_DEPLOY_EXIT=$?
  set -e
  printf '%s\n' "$dsp_output"
  [ "$DO_STAGING_DEPLOY_EXIT" -eq 0 ] \
    || do_migration_staging_refuse "the staging deploy exited $DO_STAGING_DEPLOY_EXIT"
  DO_STAGING_VERSION_ID="$(printf '%s\n' "$dsp_output" \
    | sed -nE 's/^.*Current Version ID:[[:space:]]*([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}).*$/\1/p' \
    | tail -n 1 | tr 'A-F' 'a-f')"
  [ -n "$DO_STAGING_VERSION_ID" ] \
    || do_migration_staging_refuse "the staging deploy printed no version id"
  set +e
  dsp_after="$(do_migration_plan staging)"
  dsp_rc=$?
  set -e
  [ "$dsp_rc" -eq 0 ] \
    || do_migration_staging_refuse "staging's migration tag could not be re-read after the deploy"
  dsp_after_tag="$(do_migration_field "$dsp_after" applied_tag)"
  if [ "$dsp_after_tag" != "$DO_NEW_TAG" ]; then
    DO_STAGING_TAG_MOVED="false"
    do_migration_staging_refuse "staging's migration tag did not move to $DO_NEW_TAG (it reports $dsp_after_tag)"
  fi
  if [ "$DO_STAGING_OLD_TAG" = "$DO_NEW_TAG" ]; then
    DO_STAGING_TAG_MOVED="false"
  else
    DO_STAGING_TAG_MOVED="true"
    echo "  OK  staging's migration tag moved: $DO_STAGING_OLD_TAG -> $DO_NEW_TAG"
    "$PY" "$DO_MIGRATION_HELPER" tag-receipt write --dir "$DO_STATE_DIR" --script "$dsp_script" \
      --tag "$DO_NEW_TAG" --digest "$DO_STEPS_DIGEST" --sha "$HEAD_SHA" \
      --version-id "$DO_STAGING_VERSION_ID" --environment staging >/dev/null \
      || do_migration_staging_refuse "staging applied $DO_NEW_TAG, but its durable receipt could not be written to $DO_STATE_DIR"
  fi
  do_migration_readback "$DO_STAGING_VERSION_ID" staging "https://$dsp_host/release" \
    || do_migration_staging_refuse "staging /release did not read back $HEAD_SHA / $DO_STAGING_VERSION_ID with the expected posture and schema"
  DO_STAGING_PRECHECK="passed"
  echo "  OK  staging precheck passed; the Production migration deploy may run"
}

# The database's account of the Production move, written the moment the deploy
# returns. ops.deployment cannot hold it yet (it refuses a Production row before
# technical readiness), so it goes to ops.settings_change, the log of changes to
# control planes this system does not own.
do_migration_record_change() {
  set +e
  "$PY" "$REPO/tools/ops-record.py" settings-change --kind worker-do-migration \
    --target "cloudflare-workers:$DO_SCRIPT:production" --outcome "$1" --reason "$2" \
    --session "deploy-worker:$HEAD_SHA" \
    --command "wrangler deploy --message 'carr do-migration $DO_NEW_TAG $HEAD_SHA'" \
    --environment production >/dev/null 2>&1
  dmc_rc=$?
  set -e
  if [ "$dmc_rc" -eq 0 ]; then
    DO_DB_RECORD="recorded"
    echo "  recorded in ops.settings_change: worker-do-migration $1"
  else
    DO_DB_RECORD="failed"
    echo "  !!  the Production change was NOT recorded in ops.settings_change (exit $dmc_rc)" >&2
  fi
}

apply_pending_do_migration() {
  set +e
  DO_PLAN="$(do_migration_plan production)"
  do_rc=$?
  set -e
  case "$do_rc" in
    0) ;;
    3) fail "the target Worker's applied Durable Object migration tag could not be determined,
  so whether a migration is pending is UNKNOWN. Refusing before any upload or
  deploy; Production traffic was not changed. The reason is printed above." ;;
    *) fail "wrangler.toml does not describe a usable Durable Object migration target (exit $do_rc); traffic was not changed." ;;
  esac
  DO_PENDING="$(do_migration_field "$DO_PLAN" pending)"
  DO_OLD_TAG="$(do_migration_field "$DO_PLAN" applied_tag)"
  DO_NEW_TAG="$(do_migration_field "$DO_PLAN" latest_tag)"
  DO_PENDING_TAGS="$(do_migration_field "$DO_PLAN" pending_tags)"
  if [ "$DO_PENDING" != "true" ]; then
    echo "  OK  no pending Durable Object migration (applied: $DO_OLD_TAG, declared latest: $DO_NEW_TAG)"
    return 0
  fi
  DO_SCRIPT="$(do_migration_field "$DO_PLAN" script)"
  DO_STEPS_DIGEST="$(do_migration_field "$DO_PLAN" steps_digest)"
  DO_TARGET="$("$PY" "$DO_MIGRATION_HELPER" target --config "$WORKER_DIR/wrangler.toml" --env production)"
  echo ""
  echo "== Durable Object migration =="
  echo "  pending: $DO_PENDING_TAGS (applied: $DO_OLD_TAG; steps $DO_STEPS_DIGEST)"
  echo "  versions upload cannot apply it; staging first, then a deploy of $HEAD_SHA (100% of traffic)"
  # WR95 live acquisition binds evidence to the staging version it names; the
  # staging precheck would replace that version under it.
  [ -z "${FOUNDATION_ASSURANCE_STAGING_PROVIDER:-}" ] \
    || do_migration_refuse_untouched "wr95 with a pending migration" \
      "WR95 live acquisition binds the named staging version, which the Durable Object
  migration's staging precheck would replace. Ship the migration in an ordinary
  release first, then run WR95 against the migrated staging Worker."
  do_migration_attachment_check
  do_migration_staging_precheck
  echo ""
  echo "== Durable Object migration: Production =="
  if [ -n "${PROBE_TOKENS_FILE:-}" ]; then
    set -- --secrets-file "$PROBE_TOKENS_FILE"
  else
    set --
  fi
  set +e
  DO_DEPLOY_OUTPUT="$("$WRANGLER" deploy "$@" --var "GIT_SHA:$HEAD_SHA" \
    --var "CANDIDATE_MANIFEST:$CANDIDATE_MANIFEST" \
    --var "CANDIDATE_MANIFEST_DIGEST:$CANDIDATE_MANIFEST_DIGEST" \
    --message "carr do-migration $DO_NEW_TAG $HEAD_SHA" 2>&1)"
  DO_DEPLOY_RC=$?
  set -e
  printf '%s\n' "$DO_DEPLOY_OUTPUT"
  DO_VERSION_ID="$(printf '%s\n' "$DO_DEPLOY_OUTPUT" \
    | sed -nE 's/^.*Current Version ID:[[:space:]]*([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}).*$/\1/p' \
    | tail -n 1 | tr 'A-F' 'a-f')"

  # Which side of the rollback line is the Worker on? Read it, do not assume.
  set +e
  DO_AFTER="$(do_migration_plan production)"
  do_after_rc=$?
  set -e
  DO_AFTER_TAG="unknown"
  [ "$do_after_rc" -ne 0 ] || DO_AFTER_TAG="$(do_migration_field "$DO_AFTER" applied_tag)"
  if [ "$DO_AFTER_TAG" = "$DO_NEW_TAG" ]; then
    DO_OUTCOME="applied"
  elif [ "$DO_DEPLOY_RC" -ne 0 ] && [ "$DO_AFTER_TAG" = "$DO_OLD_TAG" ]; then
    DO_OUTCOME="not_applied"
  else
    # Unreadable, or a deploy that returned 0 without moving the tag: the new
    # code may be serving either way.
    DO_OUTCOME="unknown"
  fi
  DO_FORWARD_FIX="Cloudflare blocks rollback to any version from before a Durable Object
  lifecycle change, so recovery is FORWARD FIX ONLY: do not promote a
  pre-migration version; fix forward through a PR and let the next release ship it."
  # The pipeline reads exactly one of these into its run record and its
  # fix-forward dispatch.
  case "$DO_OUTCOME" in
    applied) echo "DO migration applied: tag=$DO_NEW_TAG from=$DO_OLD_TAG version=${DO_VERSION_ID:-unknown}"
      do_migration_record_change applied "Durable Object migration $DO_NEW_TAG applied to Production by the release deploy of $HEAD_SHA (version ${DO_VERSION_ID:-unknown}, previous tag $DO_OLD_TAG); 100% of traffic moved; recovery is forward fix only" ;;
    unknown) echo "DO migration possibly applied: tag=$DO_NEW_TAG from=$DO_OLD_TAG version=${DO_VERSION_ID:-unknown}"
      do_migration_record_change failed "Durable Object migration $DO_NEW_TAG deploy of $HEAD_SHA exited $DO_DEPLOY_RC and the applied tag reads $DO_AFTER_TAG: POSSIBLY APPLIED, treat as moved; recovery is forward fix only" ;;
    not_applied)
      do_migration_record_change failed "Durable Object migration $DO_NEW_TAG deploy of $HEAD_SHA exited $DO_DEPLOY_RC; the tag is still $DO_OLD_TAG, so the previous version is still serving" ;;
  esac

  if [ "$DO_OUTCOME" = "not_applied" ]; then
    do_migration_write_receipt "$DO_OLD_TAG" "$DO_NEW_TAG" "$DO_PENDING_TAGS" "$DO_VERSION_ID" \
      not_applied "$DO_DEPLOY_RC" not-run
    fail "the migration deploy exited $DO_DEPLOY_RC and the Worker still reports tag $DO_OLD_TAG:
  the migration was NOT applied and the previous version is still serving.
  Nothing needs rolling back; no version was uploaded and no candidate was filed."
  fi
  if [ "$DO_OUTCOME" = "unknown" ]; then
    do_migration_write_receipt "$DO_OLD_TAG" "$DO_NEW_TAG" "$DO_PENDING_TAGS" "$DO_VERSION_ID" \
      unknown "$DO_DEPLOY_RC" not-run
    fail "the migration deploy exited $DO_DEPLOY_RC and the Worker reports tag $DO_AFTER_TAG
  (wanted $DO_NEW_TAG). Treat the migration as possibly applied. $DO_FORWARD_FIX"
  fi
  if [ "$DO_DEPLOY_RC" -ne 0 ] || [ -z "$DO_VERSION_ID" ]; then
    do_migration_write_receipt "$DO_OLD_TAG" "$DO_NEW_TAG" "$DO_PENDING_TAGS" "$DO_VERSION_ID" \
      applied_unverified "$DO_DEPLOY_RC" not-run
    fail "the migration deploy exited $DO_DEPLOY_RC (version id: ${DO_VERSION_ID:-none printed}),
  but the Worker now reports tag $DO_NEW_TAG: the migration WAS applied and
  $HEAD_SHA may be serving. $DO_FORWARD_FIX"
  fi
  if ! do_migration_readback "$DO_VERSION_ID" "$TARGET_ENV" "$DO_MIGRATION_RELEASE_URL"; then
    do_migration_write_receipt "$DO_OLD_TAG" "$DO_NEW_TAG" "$DO_PENDING_TAGS" "$DO_VERSION_ID" \
      applied_unverified 0 mismatch
    fail "the migration $DO_NEW_TAG is applied, but Production /release did not read back
  $HEAD_SHA / $DO_VERSION_ID. $DO_FORWARD_FIX"
  fi
  do_migration_write_receipt "$DO_OLD_TAG" "$DO_NEW_TAG" "$DO_PENDING_TAGS" "$DO_VERSION_ID" \
    applied_verified 0 identity-ok
  [ "$DO_DB_RECORD" = "recorded" ] \
    || fail "Production serves $HEAD_SHA / $DO_VERSION_ID with migration $DO_NEW_TAG applied, but the
  change could not be recorded in ops.settings_change. The release stops here rather
  than continue unrecorded; record it with tools/ops-record.py settings-change
  --kind worker-do-migration --outcome applied. $DO_FORWARD_FIX"
  DO_MIGRATION_EVIDENCE="do-migration=$DO_NEW_TAG@$DO_VERSION_ID"
  DO_MIGRATION_VERSION_ID="$DO_VERSION_ID"
  DO_TRAFFIC_CLAUSE="Production ALREADY serves $DO_VERSION_ID after the Durable Object migration deploy; recovery is forward fix only"
  echo "  the migration deploy's version $DO_VERSION_ID is this release's candidate; no second version is uploaded"
}

# Promotion: annotate this release's deployment receipts with the migration its
# upload applied, from the receipt that upload wrote for the same SHA.
load_do_migration_receipt() {
  dml_file="$DO_MIGRATION_RECEIPT_DIR/do-migration-$HEAD_SHA.json"
  [ -f "$dml_file" ] || return 0
  if ! dml_json="$("$PY" "$DO_MIGRATION_HELPER" receipt --file "$dml_file" --sha "$HEAD_SHA")"; then
    echo "  !!  a Durable Object migration receipt exists for $HEAD_SHA but is invalid;" >&2
    echo "      this promotion's receipts will not name the migration: $dml_file" >&2
    return 0
  fi
  dml_state="$(do_migration_field "$dml_json" state)"
  [ "$dml_state" != "not_applied" ] || return 0
  DO_MIGRATION_EVIDENCE="do-migration=$(do_migration_field "$dml_json" new_tag)@$(do_migration_field "$dml_json" migration_version_id)"
  DO_MIGRATION_ROLLBACK_NOTE="  This release applied Durable Object migration $(do_migration_field "$dml_json" new_tag):
  Cloudflare blocks promoting any version from before it, so recovery is forward fix."
  echo "  Durable Object migration applied by this release's upload: $DO_MIGRATION_EVIDENCE ($dml_state)"
}
# ---------- (END do-migration block) ----------

# ---------- deploy ----------
echo ""
echo "== deploy =="
cd "$WORKER_DIR"
# -- provider-version upload --
if [ "$VERSION_MODE" = "upload" ]; then
  if [ -n "$FOUNDATION_ASSURANCE_STAGING_PROVIDER" ]; then
    echo "== WR95 staging evidence target =="
    WR95_STAGING_VERSION_JSON="$(mktemp "${TMPDIR:-/tmp}/wr95-staging-version.XXXXXX")"
    WR95_STAGING_RELEASE_JSON="$(mktemp "${TMPDIR:-/tmp}/wr95-staging-release.XXXXXX")"
    "$WRANGLER" versions view "$FOUNDATION_ASSURANCE_STAGING_PROVIDER" --env staging --json \
      > "$WR95_STAGING_VERSION_JSON" 2>/dev/null \
      || fail "the named staging provider version is unavailable."
    STAGING_TARGET_HOST="$($PY "$REPO/tools/ops-record.py" staging-target --field host)" \
      || fail "the canonical staging host is unavailable."
    curl -fsS --max-time 30 "https://$STAGING_TARGET_HOST/release" \
      > "$WR95_STAGING_RELEASE_JSON" 2>/dev/null \
      || fail "the staging release readback is unavailable."
    "$PY" -c 'import json,sys
version=json.load(open(sys.argv[1])); live=json.load(open(sys.argv[2])); wanted=sys.argv[3]; sha=sys.argv[4]
def contains(value, needle):
  if isinstance(value,dict): return any(contains(v,needle) for v in value.values())
  if isinstance(value,list): return any(contains(v,needle) for v in value)
  return value==needle
if not contains(version,wanted): raise SystemExit("provider detail did not name the exact staging UUID")
if (live.get("git_sha") or {}).get("value")!=sha or (live.get("worker_version") or {}).get("id")!=wanted or (live.get("env") or {}).get("value")!="staging": raise SystemExit("staging /release binding differs")' \
      "$WR95_STAGING_VERSION_JSON" "$WR95_STAGING_RELEASE_JSON" \
      "$FOUNDATION_ASSURANCE_STAGING_PROVIDER" "$HEAD_SHA" \
      || fail "the named staging provider is not the exact serving source."
    rm -f "$WR95_STAGING_VERSION_JSON" "$WR95_STAGING_RELEASE_JSON"
    echo "  verified exact staging provider $FOUNDATION_ASSURANCE_STAGING_PROVIDER"
  fi
  # A pending Durable Object migration would make `versions upload` refuse;
  # apply it (or refuse when the applied tag is unknown) before the upload.
  # When it applies one, the migration deploy's own version IS the candidate.
  apply_pending_do_migration
  if [ -n "$DO_MIGRATION_VERSION_ID" ]; then
    PROVIDER_VERSION_ID="$DO_MIGRATION_VERSION_ID"
  else
    set +e
    if [ -n "$PROBE_TOKENS_FILE" ]; then
      set -- --secrets-file "$PROBE_TOKENS_FILE"
    else
      set --
    fi
    VERSION_UPLOAD_OUTPUT="$("$WRANGLER" versions upload "$@" --var "GIT_SHA:$HEAD_SHA" \
      --var "CANDIDATE_MANIFEST:$CANDIDATE_MANIFEST" \
      --var "CANDIDATE_MANIFEST_DIGEST:$CANDIDATE_MANIFEST_DIGEST" 2>&1)"
    VERSION_UPLOAD_RC=$?
    set -e
    printf '%s\n' "$VERSION_UPLOAD_OUTPUT"
    [ "$VERSION_UPLOAD_RC" -eq 0 ] || fail "Cloudflare version upload failed."
    PROVIDER_VERSION_ID="$(printf '%s\n' "$VERSION_UPLOAD_OUTPUT" \
      | sed -nE 's/^.*Worker Version ID:[[:space:]]*([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}).*$/\1/p' \
      | tail -n 1 | tr 'A-F' 'a-f')"
    [ -n "$PROVIDER_VERSION_ID" ] \
      || fail "Cloudflare uploaded a version but returned no parseable immutable version id; traffic was not changed."
  fi
  if [ -n "$FOUNDATION_ASSURANCE_STAGING_PROVIDER" ]; then
    WR95_FINAL_VERSION_JSON="$(mktemp "${TMPDIR:-/tmp}/wr95-final-version.XXXXXX")"
    "$WRANGLER" versions view "$PROVIDER_VERSION_ID" --json \
      > "$WR95_FINAL_VERSION_JSON" 2>/dev/null \
      || fail "the final uploaded provider version is unavailable for secret-binding verification."
    "$PY" -c 'import json,sys
value=json.load(open(sys.argv[1])); wanted=sys.argv[2]
def bound(v):
  if isinstance(v,dict):
    if v.get("name")==wanted and "secret" in str(v.get("type","")).lower(): return True
    return any(bound(x) for x in v.values())
  if isinstance(v,list): return any(bound(x) for x in v)
  return False
if not bound(value): raise SystemExit("required secret binding is absent")' \
      "$WR95_FINAL_VERSION_JSON" "DATABASE_URL_FOUNDATION_ASSURANCE_WRITER" \
      || fail "the final provider version does not carry DATABASE_URL_FOUNDATION_ASSURANCE_WRITER."
    rm -f "$WR95_FINAL_VERSION_JSON"
    WR95_FINAL_VERSION_JSON=""
    echo "  verified final provider secret binding (name/type only)"
  fi
  BOUND_RELEASE_MANIFEST="$(mktemp "${TMPDIR:-/tmp}/carr-bound-release-manifest.XXXXXX")"
  if ! "$PY" "$REPO/tools/release-manifest.py" bind-provider \
      --manifest "$RELEASE_MANIFEST" --provider "$PROVIDER" \
      --provider-version-id "$PROVIDER_VERSION_ID" > "$BOUND_RELEASE_MANIFEST"; then
    fail "the uploaded version could not be bound into its release manifest; $DO_TRAFFIC_CLAUSE."
  fi
  RELEASE_MANIFEST="$BOUND_RELEASE_MANIFEST"
  RELEASE_PLAN_HASH="$("$PY" "$REPO/tools/release-manifest.py" plan-hash \
    --manifest "$RELEASE_MANIFEST")"
  [ -n "$RELEASE_PLAN_HASH" ] \
    || fail "the provider-bound release manifest has no plan hash; $DO_TRAFFIC_CLAUSE."
  echo ""
  if [ -n "$DO_MIGRATION_VERSION_ID" ]; then
    echo "Durable Object migration release — Production ALREADY serves this version"
    echo "  (the migration deploy moved 100% of traffic; the candidate, typed staging"
    echo "  rehearsal and promotion below all name this same version)"
  else
    echo "uploaded only — Production traffic was not changed"
  fi
  echo "  provider: $PROVIDER"
  echo "  provider version: $PROVIDER_VERSION_ID"
  echo "  git SHA: $HEAD_SHA"
  echo "  plan hash: $RELEASE_PLAN_HASH"
  echo ""
  # ---------- the release-candidate record (standing-rule amendment 9) ------
  #
  # THE WRAPPER FILES IT, NOT A HUMAN RUNNING A PRINTED LINE. What stood here
  # was an `echo` of the command somebody ought to run next, which made the
  # record's maker whatever that person typed — and the Gate Zero producer reads
  # its SUBJECT MAKER out of this row. A printed instruction is not provenance:
  # the row has to be written by the thing that made the candidate, on a
  # credential, in the same run that uploaded it.
  #
  # THE MAKER IS NOT AN ARGUMENT HERE. tools/ops-record.py refuses --maker
  # outright and names no maker column in the insert; migration 0504's trigger
  # records the login role the connection authenticated as and derives the maker
  # from it, so this wrapper cannot name a maker even by mistake. What it supplies
  # is the exact target: the key, the environment, the provider and its immutable
  # version id, and the provider-bound manifest technical readiness will bind.
  #
  # The exact carr_jobs service login files it. Migration 0504 records that
  # login, derives maker_actor=carr_jobs, and correctly reports
  # maker_authority_verified=false. This row claims no Joe authorship or approval.
  # The typed readiness receipt later binds independent technical evidence.
  # Without the scoped service credential, ops-record.py refuses before insert.
  # The line printed below still reports what the database recorded rather than
  # what anyone intended.
  echo "== release candidate record =="
  if [ -n "$FOUNDATION_ASSURANCE_STAGING_PROVIDER" ]; then
    WR95_SEAL_OUTPUT="$(mktemp "${TMPDIR:-/tmp}/wr95-foundation-seal.XXXXXX")"
    WR95_EVIDENCE_IDEMPOTENCY="$($PY -c 'import sys,uuid; print(uuid.uuid5(uuid.UUID("d5fe8cc5-32f8-4f09-9aa8-f14466b65146"),sys.argv[1]+"\0"+sys.argv[2]))' "$RELEASE_KEY" "$PROVIDER_VERSION_ID")"
    if ! "$PY" "$REPO/tools/ops-record.py" release candidate \
      --key "$RELEASE_KEY" --service carr-mcp --environment production \
      --provider "$PROVIDER" --provider-version-id "$PROVIDER_VERSION_ID" \
      --manifest "$RELEASE_MANIFEST" \
      --security-evidence "$RELEASE_SECURITY_EVIDENCE" \
      ${RELEASE_VERIFIER:+--verifier "$RELEASE_VERIFIER"} \
      ${RELEASE_VERIFIER_EVIDENCE:+--verifier-evidence "$RELEASE_VERIFIER_EVIDENCE"} \
      >/dev/null; then
      fail "the uploaded WR95 version could not be filed as a release candidate; evidence acquisition was not started."
    fi
    if ! node "$REPO/mcp-server/bin/seal-foundation-assurance-evidence.mjs" \
      --source-sha "$HEAD_SHA" --source-tree "$HEAD_TREE" \
      --staging-provider-version "$FOUNDATION_ASSURANCE_STAGING_PROVIDER" \
      --final-provider-version "$PROVIDER_VERSION_ID" --release-key "$RELEASE_KEY" \
      --staging-origin "https://$STAGING_TARGET_HOST" \
      --staging-candidate-operation-id "$FOUNDATION_ASSURANCE_STAGING_CANDIDATE_OPERATION" \
      --staging-replacement-receipt-id "$FOUNDATION_ASSURANCE_STAGING_REPLACEMENT_RECEIPT" \
      --staging-replacement-source-sha "$FOUNDATION_ASSURANCE_STAGING_REPLACEMENT_SOURCE" \
      --idempotency-key "$WR95_EVIDENCE_IDEMPOTENCY" > "$WR95_SEAL_OUTPUT"; then
      fail "the WR95 candidate was filed, but live evidence acquisition/storage failed; $DO_TRAFFIC_CLAUSE, and readiness remains impossible."
    fi
    RELEASE_TEST_EVIDENCE="$($PY -c 'import json,sys; x=json.load(open(sys.argv[1])); print(x["evidence_ref"])' "$WR95_SEAL_OUTPUT")" \
      || fail "the WR95 evidence store returned no sealed evidence reference."
    rm -f "$WR95_SEAL_OUTPUT"
    WR95_SEAL_OUTPUT=""
  else
    "$PY" "$REPO/tools/ops-record.py" release candidate \
      --key "$RELEASE_KEY" --service carr-mcp --environment production \
      --provider "$PROVIDER" --provider-version-id "$PROVIDER_VERSION_ID" \
      --manifest "$RELEASE_MANIFEST" \
      --test-evidence "$RELEASE_TEST_EVIDENCE" \
      --security-evidence "$RELEASE_SECURITY_EVIDENCE" \
      ${RELEASE_VERIFIER:+--verifier "$RELEASE_VERIFIER"} \
      ${RELEASE_VERIFIER_EVIDENCE:+--verifier-evidence "$RELEASE_VERIFIER_EVIDENCE"} \
      >/dev/null \
      || fail "the uploaded version could not be filed as a release candidate ($DO_TRAFFIC_CLAUSE). No readiness can name $PROVIDER_VERSION_ID until the record exists."
  fi
  echo "  filed release candidate $RELEASE_KEY for $HEAD_SHA"
  echo "  test evidence: $RELEASE_TEST_EVIDENCE"
  echo "  maker: recorded by the database from the filing login, not asserted"
  echo ""
  echo "Before promotion, use the typed staging wrapper to record the exact recovery strategy:"
  echo "  rollback: bin/deploy-worker.sh --env staging --recovery-step current_before|prior|current_after ..."
  echo "  forward_fix: bin/deploy-worker.sh --env staging --recovery-step forward_fix --release-key <key> ..."
  echo "The wrapper writes the typed rehearsal evidence required for readiness."
  exit 0
fi

# -- provider-version promotion --
if [ "$VERSION_MODE" = "promote" ]; then
  load_do_migration_receipt
  "$WRANGLER" versions deploy "${PROVIDER_VERSION_ID}@100" --yes
else
# -- ordinary source deploy --
  # Ordinary source deployment is staging/rehearsal only. Production reaches
  # this file solely through upload followed by exact immutable promotion.
  [ "$TARGET_ENV" != "production" ] \
    || fail "Production cannot rebuild source during promotion."
  if [ "$TARGET_ENV" = "staging" ]; then
    STAGING_TARGET_HOST="$("$PY" "$REPO/tools/ops-record.py" staging-target --field host)" \
      || fail "checked-in staging target config is not exact."
    [ -n "$STAGING_RECEIPT_KEY" ] \
      || STAGING_RECEIPT_KEY="$(uuidgen | tr 'A-Z' 'a-z')"
    printf '%s\n' "$STAGING_RECEIPT_KEY" | grep -Eq \
      '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' \
      || fail "--staging-receipt-idempotency-key must be a lowercase canonical UUID."
    if [ "$RECOVERY_STEP" = "standalone" ]; then
      CARR_CORRELATION_ID="${CARR_CORRELATION_ID:-$(uuidgen | tr 'A-Z' 'a-z')}"
    elif [ "$RECOVERY_STEP" = "forward_fix" ]; then
      CARR_CORRELATION_ID="$STAGING_RECEIPT_KEY"
    else
      [ -z "${CARR_CORRELATION_ID:-}" ] || [ "$CARR_CORRELATION_ID" = "$RECOVERY_ATTEMPT_ID" ] \
        || fail "ambient CARR_CORRELATION_ID differs from the recovery attempt."
      CARR_CORRELATION_ID="$RECOVERY_ATTEMPT_ID"
    fi
    export CARR_CORRELATION_ID

    staging_attempt() {
      attempt_action="$1"; shift
      if [ "$RECOVERY_STEP" = "restore_only" ]; then
        "$PY" "$REPO/tools/ops-record.py" staging-restore-only "$attempt_action" \
          --idempotency-key "$STAGING_RECEIPT_KEY" --release-key "$RELEASE_KEY" \
          --prior-release-key "$RECOVERY_PRIOR_RELEASE_KEY" \
          --recovery-attempt-id "$RECOVERY_ATTEMPT_ID" --git-sha "$HEAD_SHA" \
          --correlation "$CARR_CORRELATION_ID" "$@"
      elif [ "$RECOVERY_STEP" = "forward_fix" ]; then
        "$PY" "$REPO/tools/ops-record.py" staging-forward-fix "$attempt_action" \
          --idempotency-key "$STAGING_RECEIPT_KEY" --release-key "$RELEASE_KEY" \
          --git-sha "$HEAD_SHA" --correlation "$CARR_CORRELATION_ID" "$@"
      elif [ "$RECOVERY_STEP" = "standalone" ]; then
        "$PY" "$REPO/tools/ops-record.py" staging-attempt "$attempt_action" \
          --idempotency-key "$STAGING_RECEIPT_KEY" --release-key "$RELEASE_KEY" \
          --recovery-step standalone --git-sha "$HEAD_SHA" \
          --correlation "$CARR_CORRELATION_ID" "$@"
      else
        "$PY" "$REPO/tools/ops-record.py" staging-attempt "$attempt_action" \
          --idempotency-key "$STAGING_RECEIPT_KEY" --release-key "$RELEASE_KEY" \
          --prior-release-key "$RECOVERY_PRIOR_RELEASE_KEY" \
          --recovery-attempt-id "$RECOVERY_ATTEMPT_ID" \
          --recovery-step "$RECOVERY_STEP" --git-sha "$HEAD_SHA" \
          --correlation "$CARR_CORRELATION_ID" "$@"
      fi
    }
    DEPLOY_TAG="$(staging_attempt prepare --field expected_provider_tag)" \
      || fail "the exact staging deployment attempt was not durably prepared."
    [ -n "$DEPLOY_TAG" ] || fail "the prepared staging attempt returned no provider tag."
    if [ -n "${TYPED_RECOVERY_TAG:-}" ] && [ "$DEPLOY_TAG" != "$TYPED_RECOVERY_TAG" ]; then
      fail "the idempotent typed recovery prepare returned a different provider tag."
    fi
    ATTEMPT_CLAIM_FIELD="deploy_claimed"
    [ "$RECOVERY_STEP" != "restore_only" ] || ATTEMPT_CLAIM_FIELD="mutation_claimed"
    [ "$RECOVERY_STEP" != "forward_fix" ] || ATTEMPT_CLAIM_FIELD="mutation_claimed"
    ATTEMPT_DEPLOY_CLAIMED="$(staging_attempt prepare --field "$ATTEMPT_CLAIM_FIELD")" \
      || fail "the prepared staging attempt claim state could not be read."
    if [ "$RECOVERY_STEP" = "restore_only" ]; then
      # The controller may record an `unknown` repair result only after this
      # append-only attempt exists.  A preflight refusal has no repair row and
      # must remain only the failed source-step evidence.
      echo "  restore-only attempt prepared"
    fi

    verify_staging_receipt_file() {
      receipt_file="$1"
      live_provider_version="$("$PY" "$REPO/tools/ops-record.py" \
        staging-readback-verify --file "$receipt_file" --git-sha "$HEAD_SHA" \
        --provider-tag "$DEPLOY_TAG" --expected-program6-actions "$EXPECTED_PROGRAM6_ACTIONS" \
        --field provider_version_id)" || return 1
      provider_versions="$(mktemp "${TMPDIR:-/tmp}/carr-staging-versions.XXXXXX")" \
        || return 1
      chmod 600 "$provider_versions"
      if ! "$WRANGLER" versions list --env "$TARGET_ENV" --json > "$provider_versions" 2>/dev/null; then
        rm -f "$provider_versions"
        return 1
      fi
      if ! "$PY" "$REPO/tools/ops-record.py" staging-provider-version \
          --file "$provider_versions" --provider-tag "$DEPLOY_TAG" \
          --live-version-id "$live_provider_version" >/dev/null; then
        rm -f "$provider_versions"
        return 1
      fi
      rm -f "$provider_versions"
    }

    record_staging_receipt_file() {
      receipt_file="$1"
      verify_staging_receipt_file "$receipt_file" || return 1
      if [ "$RECOVERY_STEP" = "restore_only" ]; then
        "$PY" "$REPO/tools/ops-record.py" staging-restore-only result \
          --idempotency-key "$STAGING_RECEIPT_KEY" --status succeeded \
          --git-sha "$HEAD_SHA" --expected-provider-tag "$DEPLOY_TAG" \
          --expected-program6-actions "$EXPECTED_PROGRAM6_ACTIONS" \
          --staging-readback-file "$receipt_file"
      elif [ "$RECOVERY_STEP" = "forward_fix" ]; then
        provider_versions="$(mktemp "${TMPDIR:-/tmp}/carr-staging-forward-fix-versions.XXXXXX")" || return 1
        chmod 600 "$provider_versions"
        "$WRANGLER" versions list --env "$TARGET_ENV" --json > "$provider_versions" 2>/dev/null || { rm -f "$provider_versions"; return 1; }
        "$PY" "$REPO/tools/ops-record.py" staging-forward-fix result \
          --idempotency-key "$STAGING_RECEIPT_KEY" --git-sha "$HEAD_SHA" \
          --expected-provider-tag "$DEPLOY_TAG" --expected-program6-actions "$EXPECTED_PROGRAM6_ACTIONS" \
          --manifest "$RELEASE_MANIFEST" --staging-readback-file "$receipt_file" \
          --provider-versions-file "$provider_versions"
        result_rc=$?
        rm -f "$provider_versions"
        return "$result_rc"
      elif [ "$RECOVERY_STEP" = "standalone" ]; then
        "$PY" "$REPO/tools/ops-record.py" deployment --service carr-mcp \
          --environment staging --state complete --git-sha "$HEAD_SHA" \
          --correlation "$CARR_CORRELATION_ID" --source-kind wrapper \
          --source-ref bin/deploy-worker.sh --read-back-at now \
          --release-key "$RELEASE_KEY" --idempotency-key "$STAGING_RECEIPT_KEY" \
          --expected-provider-tag "$DEPLOY_TAG" --recovery-step standalone \
          --expected-program6-actions "$EXPECTED_PROGRAM6_ACTIONS" \
          --staging-readback-file "$receipt_file"
      else
        "$PY" "$REPO/tools/ops-record.py" deployment --service carr-mcp \
          --environment staging --state complete --git-sha "$HEAD_SHA" \
          --correlation "$CARR_CORRELATION_ID" --source-kind wrapper \
          --source-ref bin/deploy-worker.sh --read-back-at now \
          --release-key "$RELEASE_KEY" --idempotency-key "$STAGING_RECEIPT_KEY" \
          --expected-provider-tag "$DEPLOY_TAG" --recovery-step "$RECOVERY_STEP" \
          --expected-program6-actions "$EXPECTED_PROGRAM6_ACTIONS" \
          --recovery-attempt-id "$RECOVERY_ATTEMPT_ID" \
          --prior-release-key "$RECOVERY_PRIOR_RELEASE_KEY" \
          --staging-readback-file "$receipt_file"
      fi
    }

    # Resume before provider mutation. If a prior process reached Cloudflare but
    # lost its client/DB response, the exact serving tag+UUID is recorded/replayed
    # and Wrangler is never called again. A claimed attempt whose tag is not
    # serving is deliberately stuck for operator recovery; automatic redeploy
    # would turn an ambiguous crash into a second provider mutation.
    STAGING_DEPLOY_RECOVERED=0
    RECOVERY_READBACK="$(mktemp "${TMPDIR:-/tmp}/carr-staging-resume.XXXXXX")"
    chmod 600 "$RECOVERY_READBACK"
    if curl --fail --silent --show-error --max-time 30 --max-filesize 65536 \
         "https://$STAGING_TARGET_HOST/release" > "$RECOVERY_READBACK" 2>/dev/null; then
      if [ "$ATTEMPT_DEPLOY_CLAIMED" = "true" ] && \
         record_staging_receipt_file "$RECOVERY_READBACK" >/dev/null 2>&1; then
        STAGING_DEPLOY_RECOVERED=1
      elif [ "$ATTEMPT_DEPLOY_CLAIMED" = "false" ] && \
           verify_staging_receipt_file "$RECOVERY_READBACK" >/dev/null 2>&1; then
        rm -f "$RECOVERY_READBACK"
        fail "the unclaimed deterministic provider tag already exists; refusing tag recreation"
      fi
    fi
    rm -f "$RECOVERY_READBACK"
    if [ "$STAGING_DEPLOY_RECOVERED" = 1 ]; then
      echo "  recovered exact serving staging tag; provider deploy skipped"
    else
      DEPLOY_CLAIM_FIELD="deploy_allowed"
      [ "$RECOVERY_STEP" != "restore_only" ] || DEPLOY_CLAIM_FIELD="mutation_allowed"
      [ "$RECOVERY_STEP" != "forward_fix" ] || DEPLOY_CLAIM_FIELD="mutation_allowed"
      DEPLOY_ALLOWED="$(staging_attempt claim --field "$DEPLOY_CLAIM_FIELD")" \
        || fail "the prepared staging attempt could not be claimed."
      [ "$DEPLOY_ALLOWED" = "true" ] \
        || fail "deployment already claimed but its exact tag is not serving; refusing redeploy"
      deploy_staging_worker
    fi
  else
    "$WRANGLER" deploy --env "$TARGET_ENV" --var "GIT_SHA:$HEAD_SHA" \
      --var "CANDIDATE_MANIFEST:$CANDIDATE_MANIFEST" \
      --var "CANDIDATE_MANIFEST_DIGEST:$CANDIDATE_MANIFEST_DIGEST"
  fi
fi

# Production promotion is not verified by Wrangler accepting the command. Read
# the serving Worker identity back from its fixed Production endpoint and refuse
# to call any later golden success Complete unless every immutable field agrees.
if [ "$TARGET_ENV" = "production" ]; then
  CARR_CORRELATION_ID="${CARR_CORRELATION_ID:-$(uuidgen | tr 'A-Z' 'a-z')}"
  export CARR_CORRELATION_ID
  LIVE_RELEASE_URL="https://api.doctorcre.com/release"
  echo ""
  echo "== postflight: Production release identity =="
  # THE READ-BACK RETRIES, and that is the whole point of this block. It used to
  # read /release exactly once, the instant Wrangler returned, and turn anything
  # it saw into a permanent verdict. A Cloudflare promotion is not globally
  # consistent the moment the upload command exits, so a single immediate read
  # can legitimately observe the PREVIOUS identity — and the old code wrote that
  # observation into the production ledger as state=failed,
  # failure_class=production_readback_mismatch, then exited 1.
  #
  # That is what happened to release.eed.prod.v3 on 2026-08-19 at 23:36:47Z. Joe
  # had approved it eleven seconds earlier. The deploy recorded a hard failure
  # while the Worker it had just promoted was serving correctly: read back later
  # the same night, /release returned the exact identity that deploy expected,
  # git_sha 7c7e1bd12946 and worker version 8f622345-c4df-4469-9676-dc4425c0cf45,
  # with 140 verbs. Nothing was wrong with the deploy. The verifier looked too
  # early, once, and its answer was permanent.
  #
  # The cost was not the wrong row alone. ops/last-deployed-verb-count.py takes
  # its baseline from the newest COMPLETE production row, so a false failure
  # freezes that baseline: it sat at the 130 verbs of 2026-08-16 while production
  # served 140, and a later deploy shipping 131 would have passed the guard while
  # dropping nine live verbs.
  #
  # A STABLE mismatch still fails, which is the property worth keeping. Retrying
  # costs a minute and cannot turn a genuinely wrong identity into a right one —
  # if the Worker is serving another commit, every attempt sees that commit and
  # the failed row is recorded exactly as before. This only removes the race.
  LIVE_READBACK_ATTEMPTS="${CARR_READBACK_ATTEMPTS:-12}"
  LIVE_READBACK_SLEEP="${CARR_READBACK_SLEEP:-5}"
  LIVE_RELEASE_OK=0
  LIVE_RELEASE_ATTEMPT=0
  while [ "$LIVE_RELEASE_ATTEMPT" -lt "$LIVE_READBACK_ATTEMPTS" ]; do
    LIVE_RELEASE_ATTEMPT=$((LIVE_RELEASE_ATTEMPT + 1))
    set +e
    LIVE_RELEASE_JSON="$(curl --fail --silent --show-error --max-time 30 \
      "$LIVE_RELEASE_URL" 2>&1)"
    LIVE_RELEASE_RC=$?
    set -e
    if [ "$LIVE_RELEASE_RC" -eq 0 ]; then
      set +e
      printf '%s' "$LIVE_RELEASE_JSON" | \
        "$PY" "$REPO/ops/verify-worker-release.py" \
          --environment production --sha "$HEAD_SHA" --provider "$PROVIDER" \
          --provider-version-id "$PROVIDER_VERSION_ID" \
          --expected-program6-actions "$EXPECTED_PROGRAM6_ACTIONS" \
          --expected-schema-highest-migration "$EXPECTED_SCHEMA_HIGHEST_MIGRATION" \
          --expected-schema-applied-count "$EXPECTED_SCHEMA_APPLIED_COUNT" >/dev/null 2>&1
      LIVE_VERIFY_RC=$?
      set -e
      if [ "$LIVE_VERIFY_RC" -eq 0 ]; then
        LIVE_RELEASE_OK=1
        break
      fi
    fi
    if [ "$LIVE_RELEASE_ATTEMPT" -lt "$LIVE_READBACK_ATTEMPTS" ]; then
      echo "  attempt $LIVE_RELEASE_ATTEMPT of $LIVE_READBACK_ATTEMPTS did not yet" \
           "observe the approved identity; waiting ${LIVE_READBACK_SLEEP}s"
      sleep "$LIVE_READBACK_SLEEP"
    fi
  done
  if [ "$LIVE_RELEASE_OK" -eq 1 ] && [ "$LIVE_RELEASE_ATTEMPT" -gt 1 ]; then
    echo "  read-back settled on attempt $LIVE_RELEASE_ATTEMPT of $LIVE_READBACK_ATTEMPTS"
  fi
  if [ "$LIVE_RELEASE_OK" -ne 1 ] && [ "$LIVE_RELEASE_RC" -ne 0 ]; then
    echo "  Production /release could not be read after $LIVE_RELEASE_ATTEMPT attempt(s)" \
         "(curl exit $LIVE_RELEASE_RC)." >&2
    DEPLOYMENT_EVIDENCE_REF="$LIVE_RELEASE_URL#unavailable"
    DEPLOYMENT_FAILURE_CLASS="production_readback_unavailable"
    record_deployment verifying "$CARR_CORRELATION_ID"
    exit 1
  fi
  if [ "$LIVE_RELEASE_OK" -ne 1 ]; then
    # Re-run the verifier once WITHOUT suppressing it, so the operator sees which
    # fields disagreed rather than only that something did. The loop above
    # silenced it because a first attempt that has not settled yet is expected
    # noise, not a finding.
    printf '%s' "$LIVE_RELEASE_JSON" | \
      "$PY" "$REPO/ops/verify-worker-release.py" \
        --environment production --sha "$HEAD_SHA" --provider "$PROVIDER" \
        --provider-version-id "$PROVIDER_VERSION_ID" \
        --expected-program6-actions "$EXPECTED_PROGRAM6_ACTIONS" \
        --expected-schema-highest-migration "$EXPECTED_SCHEMA_HIGHEST_MIGRATION" \
        --expected-schema-applied-count "$EXPECTED_SCHEMA_APPLIED_COUNT" || true
    echo "  Production /release is malformed or does not match the approved identity," \
         "still, after $LIVE_RELEASE_ATTEMPT attempt(s) over" \
         "$((LIVE_RELEASE_ATTEMPT * LIVE_READBACK_SLEEP))s. This is a settled" \
         "disagreement, not a propagation race." >&2
    DEPLOYMENT_EVIDENCE_REF="$LIVE_RELEASE_URL#identity-mismatch"
    DEPLOYMENT_FAILURE_CLASS="production_readback_mismatch"
    record_deployment failed "$CARR_CORRELATION_ID"
    exit 1
  fi
  LIVE_RELEASE_VERIFIED=1
  echo "  OK  serving Production identity matches $HEAD_SHA / $PROVIDER_VERSION_ID"

  # THE PROMOTED BUILD'S VERB COUNT, taken from the identity read-back that just
  # passed (defect 4077b653). Promote mode skips every source preflight — that is
  # correct, there is no source tree to trust for an immutable provider version —
  # but preflight 3 was the ONLY place SHIPPING was ever set, so every Production
  # promotion recorded verb_count NULL. Since Production source deploys are
  # disabled outright, promotion is the only way Production ships, and the
  # verb-loss baseline could therefore never advance again: it sat at 143 from
  # 2026-08-20 while Production served 146, and a promotion shipping 144 would
  # have passed the guard while dropping two live verbs. That is loop #276 with
  # the guard watching a frozen number.
  #
  # THE LIVE ENDPOINT IS THE RIGHT SOURCE HERE, not a checkout: it is what is
  # actually serving, and this exact JSON has just been verified to carry the
  # expected SHA, provider version, environment and Program 6 posture. Reading a
  # tree would answer a different question.
  #
  # A count that cannot be read does NOT fail the deploy. Production is already
  # serving by this point; refusing would leave a live deployment unrecorded,
  # which is worse than a missing number. It says so out loud instead, naming the
  # consequence rather than passing silently.
  if [ "$VERSION_MODE" = "promote" ]; then
    SHIPPING="$(printf '%s' "$LIVE_RELEASE_JSON" | "$PY" -c '
import json, sys
try:
    n = json.load(sys.stdin).get("verb_count")
except Exception:
    sys.exit(1)
if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
    sys.exit(1)
print(n)
' 2>/dev/null || true)"
    case "$SHIPPING" in
      ''|*[!0-9]*)
        SHIPPING=""
        echo "  !!  the promoted build's verb count could not be read from the" >&2
        echo "      identity read-back, so this deploy records none. The NEXT" >&2
        echo "      deploy's verb-loss guard will measure against an older row" >&2
        echo "      (defect 4077b653)." >&2 ;;
      *)
        echo "  OK  promoted build serves $SHIPPING verbs — recorded as the next deploy's baseline" ;;
    esac
  fi
  # Persist the exact live identity before any later check can claim it measured
  # this promotion. The performance receipt uses this same correlation, and the
  # database refuses a pre-seeded result with no prior read-back journey.
  DEPLOYMENT_EVIDENCE_REF="$LIVE_RELEASE_URL#identity-ok"
  if ! record_deployment verifying "$CARR_CORRELATION_ID" identity-readback; then
    echo "  Production is serving, but its durable identity receipt is missing." >&2
    exit 1
  fi
fi

# ---------- postflight ----------
# THE MARKER WRITE IS PART OF THE SAME STEP THAT DEPLOYS, deliberately: there
# is no separate "record the deploy" invocation to forget or skip. This is
# necessary but not sufficient (see the DEPLOY PROVENANCE note above) — a
# `wrangler deploy` run outside this script writes nothing here, which is
# exactly why /release, not this file, is the signal a verification pass
# should trust.
echo ""
echo "== postflight =="
# NO BASELINE FILE IS WRITTEN, and nothing is left for a human to commit. The
# baseline is the --verb-count this run records into ops.deployment below, which
# the next deploy reads back through ops/last-deployed-verb-count.py. The file
# this used to write could not be committed at all: it sits inside the digested
# artifact while the digest skips dotfiles, so committing it failed
# ops/release-manifest-selftest.py (defect d737c09c).
if [ -n "$SHIPPING" ]; then
  echo "  shipping $SHIPPING verbs — the baseline for the next deploy is the"
  echo "  ledger row recorded below, not a file"
else
  echo "  promoted immutable $PROVIDER version $PROVIDER_VERSION_ID"
fi
echo "  stamped GIT_SHA=$HEAD_SHA into this deploy (see /release)"
if [ -n "$CANDIDATE_MANIFEST_DIGEST" ]; then
  echo "  stamped CANDIDATE_MANIFEST_DIGEST=$CANDIDATE_MANIFEST_DIGEST (Gate Zero's candidate)"
fi
echo ""
echo "  Verify live before you walk away: call list-verbs from a session and"
echo "  confirm it reports $SHIPPING. A deploy that returns success and a"
echo "  registry that answers are two different claims (rule c53beeaa)."
# VERIFY THE ENVIRONMENT YOU JUST SHIPPED, NOT THE ONE YOU DIDN'T. This line
# hardcoded the production URL for every target, so a `--env staging` deploy
# ended by instructing the operator to curl api.doctorcre.com and look for the
# staging commit there. Following that literally means either a false alarm (the
# sha does not match, because production is correctly still production) or, far
# worse, a false all-clear read off the wrong Worker entirely. Pointing a staging
# verification at a production hostname is the exact confusion the 2026-08-13
# routes incident was made of, and it survived in the script that ships the fix.
#
# /release now carries an `env` field, so the check is two-sided: the sha proves
# WHICH BUILD answered and env proves WHICH DEPLOYMENT did. Neither alone is
# enough — during the incident every other field was plausible or identical.
# wrangler prints the deployed trigger URL above and its output is not captured
# here, so the non-production branch NAMES that line rather than reconstructing a
# hostname it would have to keep in sync with wrangler.toml. Being vague about
# which URL is better than being precise about the wrong one.
if [ "$TARGET_ENV" = "production" ]; then
  VERIFY_URL="https://api.doctorcre.com/release"
else
  VERIFY_URL="<the workers.dev URL printed above>/release — NOT api.doctorcre.com, that is production"
fi
echo "  Then curl $VERIFY_URL and confirm BOTH:"
echo "    git_sha.value is $HEAD_SHA   (which build answered)"
echo "    env.value is \"$TARGET_ENV\"                        (which deployment answered)"
if [ "$TARGET_ENV" = "production" ]; then
  echo "    worker_version.id is $PROVIDER_VERSION_ID  (which immutable provider version answered)"
fi
echo "  That pair is the authoritative provenance check, not this file. env matters"
echo "  because git_sha and schema are IDENTICAL across environments by design."

# ── POSTFLIGHT GOLDEN-WORKFLOW RUN, added 2026-08-14 (Phase 1 Program 3) ──────
#
# Everything above this line is an INSTRUCTION TO A HUMAN, and instructions to
# humans are the thing Program 3 exists to stop relying on. smoke-reads.sh has
# carried "RUN THIS AFTER EVERY WORKER DEPLOY" in its header since it was
# written; the Program 0 inventory then found bin/smoke-and-record.sh among the
# scripts with no caller anywhere. A post-deploy check that depends on somebody
# remembering is a check whose coverage is unknown.
#
# PRODUCTION ONLY, AND THAT IS NOT LAZINESS. smoke-reads.sh defaults to the
# production API and a staging deploy prints its workers.dev trigger URL from
# wrangler rather than from anything this script holds — so pointing the suite at
# a staging deploy would mean reconstructing a hostname this file would have to
# keep in sync with wrangler.toml. Aiming post-deploy verification at the wrong
# hostname is precisely what the 2026-08-13 routes incident was made of, and the
# comment block above already refuses to do it for the human. It refuses here too.
# For a staging deploy the printed instruction stands.
#
# The deploy and the check share ONE correlation id, which is the entire point:
# a deploy that breaks a read verb now leaves a deployment and a failed check
# under one id instead of two unrelated facts in two places.
measure_release_response_ms() {
  _measure_url="$1"
  _measure_rows=""
  for _measure_sample in 1 2 3 4 5; do
    if ! _measure_row="$(curl -sS -o /dev/null -w '%{time_pretransfer} %{time_total}' \
        --max-time 30 "$_measure_url")"; then
      return 1
    fi
    _measure_rows="${_measure_rows}${_measure_row}
"
  done
  printf '%s' "$_measure_rows" | "$PY" -c '
import math
import sys
rows = [line.split() for line in sys.stdin.read().splitlines() if line.strip()]
if len(rows) != 5 or any(len(row) != 2 for row in rows):
    raise SystemExit(2)
try:
    values = [(float(total) - float(pretransfer)) * 1000
              for pretransfer, total in rows]
except ValueError:
    raise SystemExit(2)
if any(not math.isfinite(value) or value <= 0 for value in values):
    raise SystemExit(2)
print(max(1, max(round(value) for value in values)))
'
}

if [ "$TARGET_ENV" = "production" ] && [ ! -x "$REPO/bin/smoke-and-record.sh" ]; then
  [ "${LIVE_RELEASE_VERIFIED:-0}" = "1" ] || {
    DEPLOYMENT_EVIDENCE_REF="https://api.doctorcre.com/release#not-verified"
    DEPLOYMENT_FAILURE_CLASS="production_readback_unavailable"
    record_deployment verifying "$CARR_CORRELATION_ID"
    exit 1
  }
  echo "  golden workflow suite is unavailable after a verified identity read-back." >&2
  DEPLOYMENT_EVIDENCE_REF="$LIVE_RELEASE_URL#identity-ok;golden-workflow-unavailable"
  record_deployment verifying "$CARR_CORRELATION_ID"
  exit 1
fi

if [ "$TARGET_ENV" = "production" ] && [ -x "$REPO/bin/smoke-and-record.sh" ]; then
  [ "${LIVE_RELEASE_VERIFIED:-0}" = "1" ] || {
    DEPLOYMENT_EVIDENCE_REF="https://api.doctorcre.com/release#not-verified"
    DEPLOYMENT_FAILURE_CLASS="production_readback_unavailable"
    record_deployment verifying "$CARR_CORRELATION_ID"
    exit 1
  }
  echo ""
  echo "== postflight: golden workflow suite =="
  CARR_CORRELATION_ID="${CARR_CORRELATION_ID:-$(uuidgen | tr 'A-Z' 'a-z')}"
  export CARR_CORRELATION_ID
  CARR_ENV="$TARGET_ENV"
  export CARR_ENV
  echo "  correlation $CARR_CORRELATION_ID"
  DEPLOYMENT_EVIDENCE_REF="$LIVE_RELEASE_URL#identity-ok;bin/smoke-and-record.sh#$CARR_CORRELATION_ID"
  DEPLOYMENT_FAILURE_CLASS="golden_workflow_failed"
  SUITE_STARTED_MS="$("$PY" -c 'import time; print(time.monotonic_ns() // 1_000_000)')"
  if "$REPO/bin/smoke-and-record.sh"; then
    echo "  golden workflow suite PASSED against the deploy you just shipped."
    SUITE_ELAPSED_MS=$(( $("$PY" -c 'import time; print(time.monotonic_ns() // 1_000_000)') - SUITE_STARTED_MS ))

    # THE BUDGET MEASURES A REQUEST, NOT THE SUITE. This used to clock
    # bin/smoke-and-record.sh end to end — 33 workflow tests against the live
    # Worker over the network — and compare that to PERFORMANCE_BUDGET_MS, whose
    # approved value on every production release is 1000. A remote suite cannot
    # finish inside one second, so the gate failed EVERY promotion after
    # everything real had passed: measured 2026-08-20 promoting claim-card,
    # "33 passed, 0 failed" immediately followed by "FAIL 229615ms exceeds
    # approved 1000ms", and the deploy recorded failed on that alone.
    #
    # That is not a cosmetic complaint. ops.deployment is what the verb-loss
    # guard reads for its baseline, so every good promotion booked as failed
    # left the newest success further behind reality — production was serving
    # 141 verbs while the ledger's newest success still said 130, wide enough
    # for a deploy shipping 131 to pass the guard and drop ten live verbs.
    #
    # So measure what the budget is actually about: how long production takes to
    # answer after curl has completed DNS, TCP and TLS setup. Five separate
    # requests retain independent Worker samples; subtracting time_pretransfer
    # from time_total removes client connection setup without warming away a
    # Worker cold start. Slowest of five samples, not the mean — a budget met on
    # average and blown one call in five is not met. On 2026-09-05 total times
    # reached 1546ms while the measured response portions were 173-440ms.
    if ! PERFORMANCE_ELAPSED_MS="$(measure_release_response_ms "$LIVE_RELEASE_URL")"; then
      DEPLOYMENT_EVIDENCE_REF="$LIVE_RELEASE_URL#performance-sampling-unavailable"
      DEPLOYMENT_FAILURE_CLASS="performance_gate_unavailable"
      record_deployment verifying "$CARR_CORRELATION_ID"
      exit 1
    fi
    echo "  slowest of 5 live responses after connection setup: ${PERFORMANCE_ELAPSED_MS}ms (suite took ${SUITE_ELAPSED_MS}ms, not gated)"
    PERFORMANCE_EVIDENCE_REF="$LIVE_RELEASE_URL#performance-$CARR_CORRELATION_ID"
    set +e
    "$PY" "$REPO/ops/performance-budget-gate.py" \
        --elapsed-ms "$PERFORMANCE_ELAPSED_MS" --budget-ms "$PERFORMANCE_BUDGET_MS" \
        --budget-ref "$PERFORMANCE_BUDGET_REF" --evidence-ref "$PERFORMANCE_EVIDENCE_REF"
    PERFORMANCE_GATE_RC=$?
    set -e
    if [ "$PERFORMANCE_GATE_RC" -eq 1 ]; then
      # A measured breach is a real failed release-bound check, not a made-up
      # recovery receipt. Record it before the deployment failure so the
      # immediate-incident path receives both linked facts.
      if ! "$PY" "$REPO/tools/ops-record.py" run --kind check --service carr-mcp \
          --key performance.release --state failed --environment production \
          --release-key "$RELEASE_KEY" --budget-ms "$PERFORMANCE_BUDGET_MS" \
          --duration-ms "$PERFORMANCE_ELAPSED_MS" --correlation "$CARR_CORRELATION_ID" \
          --source-kind wrapper --source-ref bin/deploy-worker.sh \
          --evidence-ref "$PERFORMANCE_EVIDENCE_REF" \
          --failure-class performance_budget_exceeded \
          --detail "slowest of 5 live requests ${PERFORMANCE_ELAPSED_MS}ms exceeds ${PERFORMANCE_BUDGET_REF}; golden suite ${SUITE_ELAPSED_MS}ms, not gated"; then
        DEPLOYMENT_EVIDENCE_REF="$PERFORMANCE_EVIDENCE_REF"
        DEPLOYMENT_FAILURE_CLASS="performance_evidence_unrecorded"
        record_deployment verifying "$CARR_CORRELATION_ID"
        exit 1
      fi
      DEPLOYMENT_EVIDENCE_REF="$PERFORMANCE_EVIDENCE_REF"
      DEPLOYMENT_FAILURE_CLASS="performance_budget_exceeded"
      record_deployment failed "$CARR_CORRELATION_ID"
      exit 1
    fi
    if [ "$PERFORMANCE_GATE_RC" -ne 0 ]; then
      DEPLOYMENT_EVIDENCE_REF="$PERFORMANCE_EVIDENCE_REF"
      DEPLOYMENT_FAILURE_CLASS="performance_gate_unavailable"
      record_deployment verifying "$CARR_CORRELATION_ID"
      exit 1
    fi
    if ! "$PY" "$REPO/tools/ops-record.py" run --kind check --service carr-mcp \
        --key performance.release --state succeeded --environment production \
        --release-key "$RELEASE_KEY" --budget-ms "$PERFORMANCE_BUDGET_MS" \
        --duration-ms "$PERFORMANCE_ELAPSED_MS" --correlation "$CARR_CORRELATION_ID" \
        --source-kind wrapper --source-ref bin/deploy-worker.sh \
        --evidence-ref "$PERFORMANCE_EVIDENCE_REF" \
        --detail "slowest of 5 live requests ${PERFORMANCE_ELAPSED_MS}ms within ${PERFORMANCE_BUDGET_REF}; golden suite ${SUITE_ELAPSED_MS}ms, not gated"; then
      DEPLOYMENT_EVIDENCE_REF="$PERFORMANCE_EVIDENCE_REF"
      DEPLOYMENT_FAILURE_CLASS="performance_evidence_unrecorded"
      record_deployment verifying "$CARR_CORRELATION_ID"
      exit 1
    fi
    DEPLOYMENT_EVIDENCE_REF="$PERFORMANCE_EVIDENCE_REF;golden-workflow-ok"
    if ! record_deployment complete "$CARR_CORRELATION_ID"; then
      echo "  Production shipped, but Program 5 assurance did not complete." >&2
      exit 1
    fi
  else
    smoke_rc=$?
    if [ "$smoke_rc" -eq 78 ]; then
      echo "  golden workflow suite SKIPPED — no probe token configured on this machine."
      echo "  This deploy is UNVERIFIED by the suite. See the provisioning runbook in"
      echo "  mcp-server/smoke-reads.sh. Not treated as a deploy failure."
      # VERIFYING, NOT COMPLETE. The completion bar in 0115 is a read-back, and a
      # skipped suite produced none. Recording this as complete would be the
      # single most expensive lie this script could tell.
      record_deployment verifying "$CARR_CORRELATION_ID"
      exit 1
    else
      echo ""
      echo "  ***  GOLDEN WORKFLOW SUITE FAILED (exit $smoke_rc) AFTER THIS DEPLOY.  ***"
      echo "  The Worker shipped and is answering wrongly, or the probe credential is"
      echo "  refused. Read the output above, and read the failed check together with"
      echo "  this deploy as ONE journey rather than two unrelated facts:"
      echo "      .venv/bin/python tools/ops-record.py trace $CARR_CORRELATION_ID"
      echo "  Roll back by approving the prior immutable version, then running"
      echo "  bin/deploy-worker.sh --promote-version <approved-prior-version-id>."
      [ -z "$DO_MIGRATION_ROLLBACK_NOTE" ] || echo "$DO_MIGRATION_ROLLBACK_NOTE"
      record_deployment failed "$CARR_CORRELATION_ID"
      exit 1
    fi
  fi
fi

# ── THE LEDGER IS NOT THE SMOKE SUITE ────────────────────────────────────────
# Defect cb65fc17, 2026-08-16: the first approved release in this system's
# history shipped to staging, read back clean by hand, and wrote NO row to
# ops.deployment. All three record_deployment call sites sit inside the block
# above, so a staging deploy reached none of them.
#
# THE PRODUCTION-ONLY GUARD ABOVE IS CORRECT AND STAYS. smoke-reads.sh defaults
# to the production API, and aiming post-deploy verification at a reconstructed
# staging hostname is what the 2026-08-13 routes incident was made of.
#
# WHAT WAS WRONG IS THE NESTING. Running the golden suite and recording THAT A
# DEPLOY HAPPENED are different facts. The second is the Program 3 job ledger,
# which Program 5's promotion path reads, and a staging deploy leaving no row is
# indistinguishable from a staging deploy that never ran — which is exactly how
# this went unnoticed until someone went looking for something else.
#
# `verifying`, NEVER `complete`, and the word is already defined above for this
# case: the suite could not run, so nothing was proven. Migration 0115 refuses
# `complete` without a read-back and would reject the lie anyway. Automating the
# staging read-back so this could honestly say `complete` is Program 5 work
# (production read-back is one of its bullets) and is deliberately not smuggled
# in here.
if [ "$TARGET_ENV" = "staging" ]; then
  echo ""
  echo "== postflight: deployment ledger =="
  CARR_CORRELATION_ID="${CARR_CORRELATION_ID:-$(uuidgen | tr 'A-Z' 'a-z')}"
  export CARR_CORRELATION_ID
  echo "  correlation $CARR_CORRELATION_ID"
  # The host, account and Worker name are resolved together from checked-in
  # semantic config. The response is capped and only typed scalar fields cross
  # into the database-owned receipt writer.
  STAGING_HOST="${STAGING_TARGET_HOST:-$("$PY" "$REPO/tools/ops-record.py" staging-target --field host)}" \
    || fail "checked-in staging target config is not exact."
  STAGING_RECEIPT="$(mktemp "${TMPDIR:-/tmp}/carr-staging-release.XXXXXX")"
  chmod 600 "$STAGING_RECEIPT"
  STAGING_OK=0
  # Cloudflare's staging route can continue serving the prior immutable Worker
  # briefly after Wrangler returns. Attempt 9625fafe proved the old three-read
  # window was too short: the provider version was created with the exact
  # prepared tag, but all three /release reads still saw the prior tag and the
  # typed recovery stopped at VERIFYING. Retry the same typed readback inside a
  # fixed 60-second wall-clock budget. The only ambient controls are bounded
  # below, and cannot turn a readback into an unbounded wait.
  STAGING_READBACK_DEADLINE_SECONDS=60
  STAGING_READBACK_CURL_MAX_SECONDS=15
  STAGING_READBACK_ATTEMPTS="${CARR_STAGING_READBACK_ATTEMPTS:-12}"
  STAGING_READBACK_SLEEP_SECONDS="${CARR_STAGING_READBACK_SLEEP_SECONDS:-5}"
  case "$STAGING_READBACK_ATTEMPTS" in
    ''|*[!0-9]*|0) fail "staging readback attempts must be a positive integer" ;;
  esac
  case "$STAGING_READBACK_SLEEP_SECONDS" in
    ''|*[!0-9]*|0) fail "staging readback sleep must be a positive integer" ;;
  esac
  [ "$STAGING_READBACK_ATTEMPTS" -gt 0 ] \
    || fail "staging readback attempts must be a positive integer"
  [ "$STAGING_READBACK_SLEEP_SECONDS" -gt 0 ] \
    || fail "staging readback sleep must be a positive integer"
  [ "$STAGING_READBACK_ATTEMPTS" -le 12 ] \
    || fail "staging readback attempts exceed the 12-attempt safety bound"
  [ "$STAGING_READBACK_SLEEP_SECONDS" -le 5 ] \
    || fail "staging readback sleep exceeds the 5-second safety bound"
  STAGING_READBACK_STARTED_MS="$("$PY" -c 'import time; print(time.monotonic_ns() // 1_000_000)')"
  STAGING_READBACK_DEADLINE_MS=$((STAGING_READBACK_STARTED_MS + STAGING_READBACK_DEADLINE_SECONDS * 1000))
  STAGING_READBACK_ATTEMPT=0
  record_staging_receipt() {
    record_staging_receipt_file "$STAGING_RECEIPT" >/dev/null
  }
  while [ "$STAGING_READBACK_ATTEMPT" -lt "$STAGING_READBACK_ATTEMPTS" ]; do
    STAGING_READBACK_NOW_MS="$("$PY" -c 'import time; print(time.monotonic_ns() // 1_000_000)')"
    STAGING_READBACK_REMAINING_MS=$((STAGING_READBACK_DEADLINE_MS - STAGING_READBACK_NOW_MS))
    [ "$STAGING_READBACK_REMAINING_MS" -gt 0 ] || break
    STAGING_READBACK_ATTEMPT=$((STAGING_READBACK_ATTEMPT + 1))
    STAGING_READBACK_CURL_MAX_MS=$((STAGING_READBACK_CURL_MAX_SECONDS * 1000))
    [ "$STAGING_READBACK_CURL_MAX_MS" -le "$STAGING_READBACK_REMAINING_MS" ] || \
      STAGING_READBACK_CURL_MAX_MS="$STAGING_READBACK_REMAINING_MS"
    STAGING_READBACK_CURL_MAX="$("$PY" -c 'import sys; print(f"{int(sys.argv[1]) / 1000:.3f}")' "$STAGING_READBACK_CURL_MAX_MS")"
    if [ -n "$STAGING_HOST" ] && curl --fail --silent --show-error --max-time "$STAGING_READBACK_CURL_MAX" --max-filesize 65536 \
         "https://$STAGING_HOST/release" > "$STAGING_RECEIPT" 2>/dev/null && \
       record_staging_receipt; then
      STAGING_OK=1; break
    fi
    STAGING_READBACK_NOW_MS="$("$PY" -c 'import time; print(time.monotonic_ns() // 1_000_000)')"
    STAGING_READBACK_REMAINING_MS=$((STAGING_READBACK_DEADLINE_MS - STAGING_READBACK_NOW_MS))
    if [ "$STAGING_READBACK_REMAINING_MS" -gt 0 ] && \
       [ "$STAGING_READBACK_ATTEMPT" -lt "$STAGING_READBACK_ATTEMPTS" ]; then
      STAGING_READBACK_SLEEP_MS=$((STAGING_READBACK_SLEEP_SECONDS * 1000))
      [ "$STAGING_READBACK_SLEEP_MS" -le "$STAGING_READBACK_REMAINING_MS" ] || \
        STAGING_READBACK_SLEEP_MS="$STAGING_READBACK_REMAINING_MS"
      STAGING_READBACK_SLEEP="$("$PY" -c 'import sys; print(f"{int(sys.argv[1]) / 1000:.3f}")' "$STAGING_READBACK_SLEEP_MS")"
      echo "  staging readback attempt $STAGING_READBACK_ATTEMPT of $STAGING_READBACK_ATTEMPTS did not yet observe the exact typed identity; waiting ${STAGING_READBACK_SLEEP}s"
      sleep "$STAGING_READBACK_SLEEP"
    fi
  done
  rm -f "$STAGING_RECEIPT"
  STAGING_RECEIPT=""
  if [ "$STAGING_OK" = 1 ]; then
    echo "  recorded immutable staging /release receipt for $CARR_CORRELATION_ID"
  else
    echo "  staging /release identity was not durably verified; recording VERIFYING only." >&2
    record_deployment verifying "$CARR_CORRELATION_ID"
    exit 1
  fi
elif [ "$TARGET_ENV" != "production" ]; then
  CARR_CORRELATION_ID="${CARR_CORRELATION_ID:-$(uuidgen | tr 'A-Z' 'a-z')}"
  export CARR_CORRELATION_ID
  record_deployment verifying "$CARR_CORRELATION_ID"
fi
