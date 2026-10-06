#!/bin/zsh
# carr-system drift + output check.
# (1) code drift: every manifest.tsv row, repo file vs live vault copy;
# (2) output drift: vault pipeline outputs vs committed baselines.
# Read-only: runs nothing, changes nothing.
# Diffs: tools/check.sh --code <repo-path>   |   tools/check.sh --out <baseline-name>

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEFAULT_RECOVERY_VAULT="/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI"
RECOVERY=0
REASON=""
RECOVERY_VAULT=""
typeset -a REST
REST=()
while (( $# )); do
  case "$1" in
    --recovery) RECOVERY=1; shift ;;
    --reason) [ $# -ge 2 ] || { echo "check: --reason requires a value" >&2; exit 2; }
              REASON="$2"; shift 2 ;;
    --vault) [ $# -ge 2 ] || { echo "check: --vault requires a value" >&2; exit 2; }
             RECOVERY_VAULT="$2"; shift 2 ;;
    *) REST+=("$1"); shift ;;
  esac
done
set -- "${REST[@]}"

if (( ! RECOVERY )) && [ -n "$RECOVERY_VAULT" ]; then
  echo "check: --vault is recovery-only; pass --recovery" >&2
  exit 2
fi
if (( RECOVERY )); then
  REASON="${REASON:-${CARR_RECOVERY_REASON:-}}"
  [ -n "${REASON//[[:space:]]/}" ] || {
    echo "check: --recovery requires a nonblank --reason" >&2; exit 2;
  }
  VAULT="${RECOVERY_VAULT:-${CARR_VAULT:-$DEFAULT_RECOVERY_VAULT}}"
  echo "CHECK RECOVERY MODE - NONCANONICAL Drive projections - reason: $REASON" >&2
  echo "CHECK RECOVERY MODE - vault: $VAULT" >&2
else
  unset CARR_VAULT
  if [ "${1:-}" = "--code" ] || [ "${1:-}" = "--out" ]; then
    echo "check: --code/--out compare Drive projections and require --recovery --reason <why>" >&2
    exit 2
  fi

  rc=0
  echo "== Code integrity (working tree vs committed canonical repo) =="
  while IFS=$'\t' read -r rp vp; do
    [ -z "$rp" ] && continue
    case "$rp" in \#*) continue ;; esac
    if [ ! -f "$REPO/$rp" ]; then
      echo "  MISSING $rp   (canonical repo path absent)"; rc=1
    elif git -C "$REPO" diff --quiet HEAD -- "$rp"; then
      echo "  OK      $rp"
    else
      echo "  DRIFT   $rp   (uncommitted change vs HEAD; review git diff -- $rp)"; rc=1
    fi
  done < "$REPO/manifest.tsv"

  echo "== Export integrity (canonical export_run receipts) =="
  PY="$REPO/.venv/bin/python"; [ -x "$PY" ] || PY=python3
  "$PY" "$REPO/tools/health-check.py" --section exports || rc=1

  echo "== Lead Board runtime fork =="
  if grep -Fq '# FORKED BY DESIGN 2026-08-11:' "$REPO/manifest.tsv"; then
    echo "  OK      build-lead-board (intentional runtime fork; manifest-governed)"
  else
    echo "  BROKEN  build-lead-board fork lacks its manifest.tsv declaration"; rc=1
  fi

  echo "== Registry integrity (canonical v_export_leads) =="
  if "$PY" "$REPO/tools/registry-audit.py"; then
    echo "  OK      canonical lead registry"
  else
    echo "  BROKEN  canonical lead registry"; rc=1
  fi
  exit $rc
fi

typeset -A OUT
OUT[deal-room-panhandle.html]="$VAULT/DNA/Team/live-boards/deal-room-panhandle.html"
OUT[lead-board.html]="$VAULT/Automation/lead-board.html"
OUT[renewal-radar.json]="$VAULT/Automation/renewal-radar.json"

case "${1:-}" in
  --code) exec diff -u "$VAULT/$(grep -F "$2	" "$REPO/manifest.tsv" | cut -f2)" "$REPO/$2" ;;
  --out)
    if [ ! -f "$REPO/baselines/$2" ]; then
      echo "local baseline copy missing (gitignored, hash-only tracked): baselines/$2"
      echo "regenerate: cp \"${OUT[$2]}\" \"$REPO/baselines/$2\"  [only after confirming today's output is correct]"
      exit 1
    fi
    exec diff -u "$REPO/baselines/$2" "${OUT[$2]}"
    ;;
esac

rc=0
echo "== Code drift (repo vs live vault copy, per manifest.tsv) =="
grep -v '^#' "$REPO/manifest.tsv" | while IFS=$'\t' read -r rp vp; do
  [ -z "$rp" ] && continue
  if [ ! -f "$VAULT/$vp" ]; then
    echo "  MISSING $rp   (vault copy not found: $vp)"; rc=1
  elif diff -q "$REPO/$rp" "$VAULT/$vp" >/dev/null 2>&1; then
    echo "  OK      $rp"
  else
    echo "  DRIFT   $rp   (diff: tools/check.sh --code $rp)"; rc=1
  fi
done

echo "== Video pipeline drift (repo vs ~/Movies runtime, per manifest-video.tsv) =="
VIDEO="${CARR_VIDEO_PIPE:-$HOME/Movies/CARR Video Pipeline}"
if [ -d "$VIDEO" ]; then
  grep -v '^#' "$REPO/manifest-video.tsv" | while IFS=$'\t' read -r rp vp; do
    [ -z "$rp" ] && continue
    if [ ! -f "$VIDEO/$vp" ]; then
      echo "  MISSING $rp   (runtime copy not found: $vp)"; rc=1
    elif diff -q "$REPO/$rp" "$VIDEO/$vp" >/dev/null 2>&1; then
      echo "  OK      $rp"
    else
      echo "  DRIFT   $rp   (diff: diff -u \"$VIDEO/$vp\" \"$REPO/$rp\")"; rc=1
    fi
  done
else
  echo "  SKIP    video pipeline not present at $VIDEO"
fi

echo "== Output drift (vault output vs committed baseline) =="
# PII-bearing baselines (lead-board.html, deal-room-panhandle.html, renewal-radar.json)
# moved to hash-only tracking under ORDER 42b (2026-08-06; renewal-radar.json added
# 2026-09-26): the full file stays LOCAL and untracked (.gitignore); only its sha256
# is committed, in baselines/SHA256SUMS. All three entries in $OUT below are now
# hash-only, so this loop's non-hash-only branch is currently unused for them.
# writing-lint.txt has no PII; it is not in $OUT and is checked separately below,
# still full-file-tracked.
typeset -A BASEHASH
if [ -f "$REPO/baselines/SHA256SUMS" ]; then
  while IFS= read -r line; do
    case "$line" in
      \#*|'') continue ;;
    esac
    BASEHASH[${line##*  }]="${line%%  *}"
  done < "$REPO/baselines/SHA256SUMS"
fi
for f in ${(k)OUT}; do
  if [ -n "${BASEHASH[$f]:-}" ]; then
    if [ ! -f "${OUT[$f]}" ]; then
      echo "  MISSING $f   (vault output not found: ${OUT[$f]})"; rc=1
    else
      livehash="$(shasum -a 256 "${OUT[$f]}" | cut -d' ' -f1)"
      if [ "$livehash" = "${BASEHASH[$f]}" ]; then
        echo "  OK      $f"
      elif [ -f "$REPO/baselines/$f" ]; then
        echo "  CHANGED $f   (hash mismatch vs baselines/SHA256SUMS; diff: tools/check.sh --out $f)"; rc=1
      else
        echo "  CHANGED $f   (hash mismatch vs baselines/SHA256SUMS; local baseline copy missing —"
        echo "          regenerate: cp \"${OUT[$f]}\" \"$REPO/baselines/$f\"  [only after confirming the new output is correct])"
        rc=1
      fi
    fi
  else
    if diff -q "$REPO/baselines/$f" "${OUT[$f]}" >/dev/null 2>&1; then
      echo "  OK      $f"
    else
      echo "  CHANGED $f   (diff: tools/check.sh --out $f)"; rc=1
    fi
  fi
done

# The record-native generator and cloud-only shared helper are a deliberate
# runtime fork (manifest.tsv, 2026-08-11), not twin copies.  The declaration is
# the governing contract for a split that is required by their different
# credential and source boundaries.
echo "== Lead Board runtime fork =="
if grep -Fq '# FORKED BY DESIGN 2026-08-11:' "$REPO/manifest.tsv"; then
  echo "  OK      build-lead-board (intentional runtime fork; manifest-governed)"
else
  echo "  BROKEN  build-lead-board fork lacks its manifest.tsv declaration"; rc=1
fi

# writing-lint fixtures (2026-07-25). The linter encodes DNA/writing-rules.md, so a
# rule edit that quietly starts flagging good CRE prose (or stops catching a tell)
# is the failure mode that would get it ignored. The two fixtures are the contract.
echo "== writing-lint fixtures (false-positive + detection guard) =="
if [ -f "$REPO/baselines/writing-lint.txt" ]; then
  tmp="$(mktemp)"
  { echo "# writing-lint baseline. Regenerate: tools/writing-lint-baseline.sh"
    echo "## clean-email.txt --surface email  (must stay 0 HARD)"
    python3 "$REPO/tools/writing-lint.py" "$REPO/tools/fixtures/clean-email.txt" --surface email | tail -n +2
    echo
    echo "## dirty-social.txt --surface social"
    python3 "$REPO/tools/writing-lint.py" "$REPO/tools/fixtures/dirty-social.txt" --surface social | tail -n +2
  } > "$tmp" 2>/dev/null
  if diff -q "$REPO/baselines/writing-lint.txt" "$tmp" >/dev/null 2>&1; then
    echo "  OK      writing-lint (fixtures match baseline)"
  else
    echo "  CHANGED writing-lint — findings moved. Review, then tools/writing-lint-baseline.sh"
    echo "          (diff: diff -u baselines/writing-lint.txt <(tools/writing-lint-baseline.sh >/dev/null; cat baselines/writing-lint.txt))"
    rc=1
  fi
  rm -f "$tmp"
else
  echo "  MISSING writing-lint baseline — run tools/writing-lint-baseline.sh"; rc=1
fi

# Registry integrity (2026-07-27). Both of that day's defects — lane 3's columns dropped
# (#100) and L-164/165/166 reassigned on top of live rows (#101) — ran for weeks behind a
# clean-looking weekly digest because nothing resolved a pointer end to end. This does.
echo "== Registry integrity (pointer rot, occupant drift, schema, ledger) =="
if python3 "$REPO/tools/registry-audit.py" --recovery --reason "$REASON" \
     --vault "$VAULT" >/dev/null 2>&1; then
  echo "  OK      lead-registry (every pointer resolves)"
else
  echo "  BROKEN  lead-registry — run: ./run.sh registry-audit"; rc=1
fi
exit $rc
