#!/bin/zsh
# council.sh — THE ONLY SANCTIONED WAY TO CONVENE A COUNCIL.
#
# On 2026-08-09 a council was convened ad hoc with two raw CLI calls. It produced
# good work and it ran GROK AT DEFAULT EFFORT, because nothing said otherwise and
# nothing checked. Joe's instruction that session: councils always run Sol 5.6 at
# high and Grok 4.5 at its top effort.
# (Historical: the pinned ids have since moved, to gpt-6.1-sol and grok-4.7 per
# Joe's 2026-09-29 ruling. bin/council-lib.sh is the only place they live.)
#
# That requirement is NOT written here as an instruction. Rules 14e0408b,
# e313a3ca and 179be4b8 were all ACTIVE, all recited at session start, and all
# violated the same day — prose does not bind. It is COMPILED IN, in
# bin/council-lib.sh, where a session cannot route around it without editing a
# file. Do not shell out to `codex` or `grok` directly for a council; call this.
#
# Model ids, effort pins, the verification limits and the billing check all live
# in council-lib.sh. Read it before changing anything. The short version: a
# model's self-report of its own name or effort is confabulation, so the manifest
# below records what was REQUESTED and says so.
#
# USAGE
#   bin/council.sh <brief-file> [outdir]
#     Fans the SAME brief, byte-for-byte, to both chairs in parallel. One brief
#     pasted identically is the council contract — the chairs cannot call tools,
#     so each must receive the whole thing.
#   bin/council.sh --check
#     Preflight: CLIs present, model available, effort accepted.
#
# EXIT: 0 both seated · 3 one seated (partial) · 4 none · 2 usage.
# A partial council is reported as partial, never presented as a panel
# (rule 2b889e80: no negative finding from a single collection).
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
source "$REPO/bin/council-lib.sh"

die() { print -r -- "council: $*" >&2; exit 2; }

if [ "${1:-}" = "--check" ]; then
  rc=0
  for c in codex grok; do
    command -v "$c" >/dev/null 2>&1 \
      && print -r -- "  ok    $c present" \
      || { print -r -- "  FAIL  $c NOT INSTALLED — that chair cannot sit"; rc=1; }
  done
  if command -v grok >/dev/null 2>&1; then
    grok models 2>&1 | grep -q "$GROK_MODEL" \
      && print -r -- "  ok    grok model $GROK_MODEL available" \
      || { print -r -- "  FAIL  grok model $GROK_MODEL not listed"; rc=1; }
    grok --reasoning-effort "$GROK_EFFORT" --help >/dev/null 2>&1 \
      && print -r -- "  ok    grok effort $GROK_EFFORT accepted" \
      || print -r -- "  WARN  grok effort $GROK_EFFORT rejected — check the scale"
  fi
  print -r -- "  council tier:  codex=$CODEX_MODEL/$CODEX_EFFORT  grok=$GROK_MODEL/$GROK_EFFORT"
  print -r -- "  precheck tier: $PRECHECK_MODEL/$PRECHECK_EFFORT (deliberately lower — see council-lib.sh)"
  exit $rc
fi

BRIEF="${1:-}"
[ -n "$BRIEF" ] || die "usage: council.sh <brief-file> [outdir]  |  --check"
[ -f "$BRIEF" ] || die "brief not found: $BRIEF"

OUT="${2:-$REPO/out/council/$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$OUT" || die "cannot create $OUT"
cp "$BRIEF" "$OUT/brief.md"

print -r -- "council: 2 chairs on $(basename "$BRIEF")"
print -r -- "  codex  $CODEX_MODEL effort=$CODEX_EFFORT"
print -r -- "  grok   $GROK_MODEL effort=$GROK_EFFORT"

run_codex "$OUT/brief.md" "$OUT/codex.md" & pid_c=$!
run_grok  "$OUT/brief.md" "$OUT/grok.md"  & pid_g=$!
wait $pid_c; rc_c=$?
wait $pid_g; rc_g=$?

# A chair that exits 0 but writes nothing did NOT sit. An empty file read as
# agreement is the worst failure mode for a panel whose value is disagreement.
[ -s "$OUT/codex.md" ] || rc_c=1
[ -s "$OUT/grok.md"  ] || rc_g=1

cat > "$OUT/manifest.json" <<JSON
{
  "convened_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "brief": "$(basename "$BRIEF")",
  "_note": "model/effort are what this runner REQUESTED. The remote side does not report back what it honoured, and a model's self-description is confabulation (see council-lib.sh). Not confirmation of what ran.",
  "chairs": [
    {"seat": "codex", "model_requested": "$CODEX_MODEL", "effort_requested": "$CODEX_EFFORT", "exit": $rc_c,
     "bytes": $( [ -f "$OUT/codex.md" ] && wc -c < "$OUT/codex.md" | tr -d ' ' || echo 0 )},
    {"seat": "grok",  "model_requested": "$GROK_MODEL",  "effort_requested": "$GROK_EFFORT",  "exit": $rc_g,
     "bytes": $( [ -f "$OUT/grok.md" ] && wc -c < "$OUT/grok.md" | tr -d ' ' || echo 0 )}
  ]
}
JSON

seated=0
[ $rc_c -eq 0 ] && { print -r -- "  codex: seated"; seated=$((seated+1)); } || print -r -- "  codex: FAILED — $OUT/codex.err"
[ $rc_g -eq 0 ] && { print -r -- "  grok:  seated"; seated=$((seated+1)); } || print -r -- "  grok:  FAILED — $OUT/grok.err"

print -r -- "council: $seated/2 seated -> $OUT"
[ $seated -eq 2 ] && exit 0
[ $seated -eq 1 ] && { print -r -- "PARTIAL COUNCIL — report as one seat, never as a panel."; exit 3; }
exit 4
