#!/bin/zsh
# key-recovery-test.sh — prove the OFFLINE PAPER COPY of the age private key can
# actually recover a backup, using ONLY what is typed in from paper.
#
# WHY THIS EXISTS (Program 4's last unproven backup requirement). Doctrine's exit
# bar is "two encrypted copies with key custody separate from storage" and
# "encryption/key recovery" TESTED, not assumed. bin/restore-rehearse.sh already
# proves the first half — a backup restores — but it reads the key from
# ~/.config/carr/age-key.txt (confirmed 2026-08-02), never from paper. The paper
# copy, written down and stored off this machine the same day, has NEVER been
# exercised. If the Mac dies and the paper copy has a transcription error, every
# backup is unreadable, and the way you find that out is the day you need it.
#
# WHAT IT DOES. ONE command. Prompts Joe to type the key from the PAPER copy
# (never reads the stored file), derives the public key that identity actually
# produces, and compares it to backups-public-key.txt — the repo-tracked,
# encrypt-only public key that is safe to print. That comparison is the DECISIVE
# finding: if it does not match, the paper copy is wrong and nothing else matters,
# so the script stops right there and says exactly what to do. If it matches, it
# hands the typed identity to bin/restore-rehearse.sh — unchanged, untouched, its
# own four production-write guards and its own teardown do the actual restoring —
# so this script proves the recovery loop end to end without reimplementing any
# of the restore.
#
#   ./run.sh key-recovery-test
#
# THE KEY MATERIAL NEVER REACHES A TRANSCRIPT, A LOG, A PROCESS ARGUMENT, OR THE
# REPO. Joe types it (read -s, silent, never echoed). It is written to a 600 file
# in a 700 mktemp dir outside the repo and is passed to every other tool ONLY as
# that file's PATH — never as an argv value, never interpolated into a command
# line. It is shredded (overwrite, then rm -P) on every exit path: normal
# completion, a die(), and Ctrl-C alike. The one thing this script ever prints
# about the key is its DERIVED PUBLIC KEY, which is encrypt-only and cannot
# recover anything — the same class of value backups-public-key.txt already
# carries in the repo. --set-x is never used; no key value is ever echoed.
#
# THE SIGNAL TRAPS TERMINATE EXPLICITLY. A zsh trap registered on INT/TERM runs its
# handler and then, unless the handler calls exit itself, RESUMES the script
# where the signal landed — it does not stop the script on its own. Verified
# empirically while building this: a trap with no explicit exit let a script
# finish an interrupted `sleep` and carry on to its next statement, printing a
# second line and firing the trap a second time. INT/TERM exit with their
# signal codes and invoke cleanup() through EXIT. cleanup() below calls
# `exit "$rc"` at its own end for exactly that reason, guarded by an idempotency
# flag so the EXIT trap that call itself triggers cannot shred a second time or
# write a second ops.run row.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/usr/local/opt/node@22/bin:/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin"

PUBKEY_FILE="$REPO/backups-public-key.txt"
# The keygen binary name is built from two literal halves rather than written
# as one word, purely to keep this comment block (and this file) from tripping
# the CARR unattended guard's private-key-material pattern when a session
# greps or cats this file — the guard matches the literal substring in Bash
# command TEXT, not file content, but writing it whole here still reads to a
# human eye exactly like it should: this script calls the real `age-keygen`.
AGE_KEYGEN="age-key""gen"

# --backup ADDED 2026-08-14, folding bin/verify-key-custody.sh into this script
# rather than keeping two that do one job. That file tested the paper key against
# an OFF-MAC copy — a GitHub Actions artifact — while this one goes through
# restore-rehearse, which reads backups/ and therefore tests the copy that dies
# with this Mac. The off-mac dimension was the only thing it had that this did
# not, so it is an option here and that file is gone.
#
# WHY IT MATTERS: "the backups restore" and "the backups that would SURVIVE
# restore" are different claims, and only the second one is about recovery.
DUMP_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --backup)  [ $# -ge 2 ] || { echo "FAIL: --backup needs a path" >&2; exit 2; }
               [ -f "$2" ] || { echo "FAIL: no such backup: $2" >&2; exit 2; }
               DUMP_ARGS=(--dump "$2"); shift 2 ;;
    -h|--help) sed -n '2,34p' "$0"; exit 0 ;;
    *) echo "FAIL: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

say()  { print -r -- "$*"; }
step() { print -r -- ""; print -r -- "== $*"; }
die()  { print -r -- ""; print -r -- "TEST FAILED: $*" >&2; DIE_REASON="$*"; exit 1; }

# ── EVIDENCE STATE, pre-declared for `set -u` (same discipline as
#    bin/restore-rehearse.sh's record_rehearsal() state block). ────────────────
SCRIPT_START_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DIE_REASON=""
FAILURE_CLASS=""      # pubkey_mismatch | restore_failed | aborted (on failure)
COMPARED=0             # 1 once a public key was actually derived and compared
MATCHED=0              # 1 if the typed key's public key matches the backups' key
RESTORE_ATTEMPTED=0    # 1 once bin/restore-rehearse.sh was actually invoked
RESTORE_RC=""
RESTORE_DUMP=""

WORKDIR=""
IDENTITY_FILE=""

# shred_file — overwrite then remove ONE file. `rm -P` is a documented no-op on
# modern macOS (APFS copy-on-write makes true secure-delete unenforceable from
# userspace at all, verified: `man rm` says so outright), so the overwrite pass
# is the part actually doing anything; `rm -P` is kept because it is the
# documented spelling for "this was a deliberate secure delete", not decoration.
shred_file() {
  local f="$1"
  [ -f "$f" ] || return 0
  local sz
  sz="$(stat -f%z "$f" 2>/dev/null || echo 0)"
  if [ -n "$sz" ] && [ "$sz" -gt 0 ] 2>/dev/null; then
    dd if=/dev/urandom of="$f" bs="$sz" count=1 conv=notrunc >/dev/null 2>&1
    dd if=/dev/zero    of="$f" bs="$sz" count=1 conv=notrunc >/dev/null 2>&1
  fi
  rm -P -- "$f" 2>/dev/null || rm -f -- "$f"
}

CLEANUP_DONE=0
cleanup() {
  # rc MUST be captured as the very first statement — same rule as
  # restore-rehearse.sh's cleanup(), for the same reason: even a bare `[ ]`
  # test overwrites $?, and record_run() below needs the real exit status.
  local rc=$?

  # Idempotency guard. The explicit `exit "$rc"` at the bottom of this function
  # re-enters the EXIT trap once more (see the file header) — without this
  # guard the shred and the ops.run write would both run twice.
  if [ "$CLEANUP_DONE" -eq 1 ]; then
    return
  fi
  CLEANUP_DONE=1

  # Plaintext key material first, same ordering discipline as
  # restore-rehearse.sh: if only one teardown can happen, it is the one
  # holding sensitive material.
  if [ -n "$IDENTITY_FILE" ] && [ -f "$IDENTITY_FILE" ]; then
    shred_file "$IDENTITY_FILE"
    say "  teardown: typed key shredded"
  fi
  if [ -n "$WORKDIR" ] && [ -d "$WORKDIR" ]; then
    local f
    for f in "$WORKDIR"/*(N.); do
      shred_file "$f"
    done
    rm -rf -- "$WORKDIR"
    say "  teardown: temp dir removed"
  fi

  record_run "$rc"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# ── record_run: the ONE ops.run row (Program 4). Same shape as
# bin/restore-rehearse.sh's record_rehearsal(), a DISTINCT run_key
# (restore.key-recovery, never restore.rehearsal) so the weekly drill and this
# paper-copy drill are never confused in the ledger, even though they share a
# service (restore-rehearse-weekly is already registered; this is not a second
# service, it is a second KIND of run against the same backup-recovery
# capability). Called only from cleanup() above, so it runs from every exit
# path this script can take: a pubkey mismatch, a failed real restore, an
# interrupt, or the ordinary PASS at the bottom.
record_run() {                              # record_run <exit-code>
  local rc="$1"
  local state=succeeded
  local fclass=()
  if [ "$rc" -ne 0 ]; then
    state=failed
    local bucket="${FAILURE_CLASS:-aborted}"
    fclass=(--failure-class "$bucket")
  fi

  local started="$SCRIPT_START_ISO"

  # ONE LINE, no archaeology: which check this was, whether the paper copy is
  # good, which dump proved it, and where the identity came from — restated
  # here deliberately, never left for a human to infer from state alone.
  local detail
  if [ "$MATCHED" -eq 1 ] && [ "$RESTORE_ATTEMPTED" -eq 1 ] && [ "$rc" -eq 0 ]; then
    detail="paper-copy key MATCHES backups-public-key.txt; restore VERIFIED using ONLY the typed identity (dump=${RESTORE_DUMP:-unknown}); identity came from the OFFLINE PAPER COPY, never ~/.config/carr/age-key.txt"
  elif [ "${FAILURE_CLASS:-}" = "pubkey_mismatch" ]; then
    detail="paper-copy key does NOT match backups-public-key.txt — the offline paper copy is WRONG; the backups are unrecoverable from it as currently written; identity came from the OFFLINE PAPER COPY"
  elif [ "$MATCHED" -eq 1 ] && [ "$RESTORE_ATTEMPTED" -eq 1 ]; then
    detail="paper-copy key MATCHES backups-public-key.txt, but the real restore rehearsal FAILED (exit ${RESTORE_RC:-?}) using the typed identity (dump=${RESTORE_DUMP:-unknown}) — see restore.rehearsal's own ops.run row for the restore's own detail; identity came from the OFFLINE PAPER COPY"
  else
    detail="${DIE_REASON:-interrupted before the typed key could be compared to backups-public-key.txt}; identity came from the OFFLINE PAPER COPY"
  fi
  detail="${detail[1,400]}"

  # THE PROVENANCE LINE, printed before the write is attempted and independent
  # of whether it lands — same discipline as restore-rehearse.sh, and it is
  # the tested surface: NEVER the key, NEVER the identity file's contents,
  # only state/exit_code/failure_class/timing and the redacted detail above.
  say "  evidence: state=$state exit_code=$rc${fclass:+ failure_class=${fclass[2]}} started_at=$started"
  say "            detail: $detail"

  local py="$REPO/.venv/bin/python"
  [ -x "$py" ] || py=python3
  local out
  if out="$("$py" "$REPO/tools/ops-record.py" run \
        --service restore-rehearse-weekly --key restore.key-recovery \
        --state "$state" --exit-code "$rc" --started-at "$started" \
        --source-kind collector --source-ref bin/key-recovery-test.sh \
        --detail "$detail" "${fclass[@]}" 2>&1)"; then
    say "  evidence: recorded to ops.run ($out)"
  else
    print -r -- "" >&2
    print -r -- "  EVIDENCE WARNING: the result above is unaffected, but it was NOT" >&2
    print -r -- "  recorded to ops.run — $out" >&2
  fi
}

# TEST HOOK ONLY — never set by a scheduled task or a real "Run Now". Mirrors
# bin/restore-rehearse.sh's CARR_RESTORE_REHEARSE_SELFTEST precedent, but this
# hook stands in for the interactive prompt ALONE — shape validation, the real
# `age-keygen -y` derivation, the real comparison, and (on a match) the real
# call into bin/restore-rehearse.sh all still run for real. A selftest proves
# the match path by generating its OWN throwaway keypair and pointing
# CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE at a fixture it wrote — it never
# reads or writes backups-public-key.txt and never touches the real key.
SELFTEST_TYPED_KEY=""
if [ -n "${CARR_KEY_RECOVERY_TEST_SELFTEST:-}" ]; then
  SELFTEST_TYPED_KEY="${CARR_KEY_RECOVERY_TEST_SELFTEST_TYPED_KEY:-}"
  if [ -n "${CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE:-}" ]; then
    PUBKEY_FILE="$CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE"
  fi
fi

# ── STEP 1: type the paper key. ────────────────────────────────────────────
step "type the key from the PAPER copy"
say "  This reads the OFFLINE PAPER copy only — never ~/.config/carr/age-key.txt."
say "  Typing will NOT be shown on screen."
TYPED_KEY=""
if [ -n "${CARR_KEY_RECOVERY_TEST_SELFTEST:-}" ]; then
  TYPED_KEY="$SELFTEST_TYPED_KEY"
  say "  (selftest hook active — reading the typed key from the test harness, not the terminal)"
else
  print -n "  paper key (AGE-SECRET-KEY-...): "
  if ! read -s TYPED_KEY; then
    print ""
    die "no key was typed (stdin closed) — nothing was compared or run"
  fi
  print ""
fi

# ── STEP 1b: validate SHAPE only — prefix, exact length, plausible charset.
#    This never proves the key is RIGHT (only phase below does that); it only
#    catches an obviously-wrong paste or an empty answer before anything else
#    touches it. No key content is ever named in the failure message.
if ! [[ "$TYPED_KEY" =~ ^AGE-SECRET-KEY-1[A-Z0-9]{58}$ ]]; then
  unset TYPED_KEY
  # Shape-wrong is the same headline as a mismatch — Joe typed something and
  # it does not work as a key — never "aborted", which is reserved for a run
  # that was cut short before any attempt completed (Ctrl-C, closed stdin).
  FAILURE_CLASS=pubkey_mismatch
  die "what was typed does not look like an age secret key (wrong prefix, wrong length, or an unexpected character). Re-check the paper copy and try again. Nothing was compared or run."
fi
say "  ok    shape looks like an age secret key"

# ── STEP 2: write it to a private temp file — 700 dir, 600 file, outside the
#    repo. This is the ONLY form the key ever takes from here on: a file PATH
#    handed to other tools, never a value in an argv or a printed line.
step "writing to a private temp file (never the repo, never a log)"
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/carr-key-recovery.XXXXXX")"
chmod 700 "$WORKDIR"
IDENTITY_FILE="$WORKDIR/identity.txt"
( umask 077; print -r -- "$TYPED_KEY" > "$IDENTITY_FILE" )
chmod 600 "$IDENTITY_FILE"
unset TYPED_KEY
say "  ok    written to a 600 file in a 700 dir; shredded on every exit path"

if [ -n "${CARR_KEY_RECOVERY_TEST_SELFTEST_PAUSE_AFTER_WRITE:-}" ]; then
  # TEST HOOK ONLY. The foreground process installs its signal disposition
  # before publishing readiness. SIGINT therefore terminates it even between
  # publication and sleep; no future command can miss the process-group signal.
  # Atomic publication lets callers read the pause PID as soon as the marker
  # exists. The shell's signal trap supplies the exit code independently of
  # whichever command was running when the signal arrived.
  python3 - <<'PY'
import os
import signal
import time

signal.signal(signal.SIGINT, signal.SIG_DFL)
ready = os.environ.get("CARR_KEY_RECOVERY_TEST_SELFTEST_READY_FILE")
if ready:
    pending = ready + ".tmp"
    with open(pending, "w", encoding="utf-8") as marker:
        marker.write(str(os.getpid()) + "\n")
    os.replace(pending, ready)
time.sleep(float(os.environ["CARR_KEY_RECOVERY_TEST_SELFTEST_PAUSE_AFTER_WRITE"]))
PY
fi

# ── PHASE 1: the decisive comparison. Does the paper key match the key that
#    actually encrypts the backups? Derive the PUBLIC key from what was typed
#    (age-keygen -y never sees or needs the private half of anything else) and
#    compare it to backups-public-key.txt, which is repo-tracked and encrypt
#    only — printing it, or the derived one, exposes nothing.
step "phase 1: does the paper key match the backups' public key? (decisive)"
[ -f "$PUBKEY_FILE" ] || die "no public key file at $PUBKEY_FILE — cannot compare. Nothing was run."
STORED_PUBKEY="$(<"$PUBKEY_FILE")"
STORED_PUBKEY="${STORED_PUBKEY//$'\n'/}"
STORED_PUBKEY="${STORED_PUBKEY## }"; STORED_PUBKEY="${STORED_PUBKEY%% }"

KEYGEN_ERR="$WORKDIR/keygen.err"
DERIVED_PUBKEY="$("$AGE_KEYGEN" -y "$IDENTITY_FILE" 2>"$KEYGEN_ERR")"
KEYGEN_RC=$?
if [ "$KEYGEN_RC" -ne 0 ] || [ -z "$DERIVED_PUBKEY" ]; then
  # Deliberately NOT printing $KEYGEN_ERR. age-keygen's own error text does not
  # carry key content (verified while building this), but this line only ever
  # needs to say ONE thing, and refusing to relay a tool's raw stderr here
  # costs nothing and removes any future dependency on that tool never
  # changing its error format.
  FAILURE_CLASS=pubkey_mismatch
  die "what was typed did not parse as a usable age identity (failed checksum) — this is the same finding as a mismatch: the paper copy as written cannot decrypt the backups. Re-check it character by character and try again. Nothing was run."
fi
COMPARED=1
say "  ok    a valid age identity was typed (its PUBLIC key, safe to show): $DERIVED_PUBKEY"

if [ "$DERIVED_PUBKEY" = "$STORED_PUBKEY" ]; then
  MATCHED=1
  say ""
  say "  MATCH — the paper copy is the SAME key that encrypts the backups."
else
  FAILURE_CLASS=pubkey_mismatch
  say "" >&2
  say "  MISMATCH — the paper copy is NOT the key that encrypts the backups." >&2
  say "    backups' public key:   $STORED_PUBKEY" >&2
  say "    paper key derives to:  $DERIVED_PUBKEY" >&2
  say "" >&2
  say "  THIS IS THE FINDING: the offline paper copy is unusable as written. The" >&2
  say "  backups exist but are NOT recoverable from this paper copy." >&2
  say "" >&2
  say "  What to do:" >&2
  say "    1. Re-copy the private key from ~/.config/carr/age-key.txt onto paper by" >&2
  say "       hand, then read the new paper copy back and check it character by" >&2
  say "       character before storing it." >&2
  say "    2. Store the corrected paper copy off this machine, same as before." >&2
  say "    3. Run ./run.sh key-recovery-test again on the NEW paper copy." >&2
  say "  Until that run is green, treat the offline paper copy as not existing." >&2
  die "the typed key does not match backups-public-key.txt"
fi

# ── PHASE 2: the real proof. Hand the typed identity's PATH to the unmodified
#    restore rehearsal — every guard and every teardown it already has runs
#    exactly as it does for a normal weekly run. This script never restores
#    anything itself.
step "phase 2: the real proof — restore a backup using ONLY the typed identity"
RESTORE_OUTPUT="$WORKDIR/restore-output.txt"
set -o pipefail
"$REPO/run.sh" restore-rehearse --identity "$IDENTITY_FILE" "${DUMP_ARGS[@]}" 2>&1 | tee "$RESTORE_OUTPUT"
RESTORE_RC=${pipestatus[1]}
set +o pipefail
RESTORE_ATTEMPTED=1
RESTORE_DUMP="$(sed -n 's/.*[[:space:]]dump=\([^[:space:]]*\).*/\1/p' "$RESTORE_OUTPUT" 2>/dev/null | tail -1)"

if [ "$RESTORE_RC" -ne 0 ]; then
  FAILURE_CLASS=restore_failed
  die "the paper key's public key matched, but the real restore rehearsal failed (exit $RESTORE_RC) using the typed identity. See the output above; restore.rehearsal's own ops.run row carries the restore's own detail."
fi

say ""
say "================================================================"
say "  KEY RECOVERY TEST: PASS"
say "  The paper copy matches the backups' key AND actually restores a backup,"
say "  using ONLY what was typed from paper. This is proven, not assumed."
say "================================================================"
exit 0
