#!/bin/zsh
# local-briefs.sh — rebuild the two repo-bound heartbeat outputs, locally.
#
# WHY THIS EXISTS (2026-08-04, loop #181, Joe's go). The daily heartbeat IS
# scheduled — in Cowork, which a local session cannot enumerate — and it fires
# every morning. But brief-pack and review-queue had not been rebuilt since
# 2026-08-02 15:18, an ad-hoc session run, while Monday 2026-08-03 was a full
# business day on which other scheduled work landed normally. So the trigger runs
# and these two jobs do not.
#
# THE LEADING HYPOTHESIS, and it is why the fix is a LOCAL job rather than a
# second heartbeat: a Cowork session cannot reach ~/carr-system, so it cannot
# execute `run.sh brief-pack` at all. CLAUDE.md already states a cloud session may
# see no repos; the salesforce and costar SOPs restrict themselves to local Claude
# Code for the same reason. IT IS UNTESTED — nobody has read that task's own run
# history — so this job is deliberately narrow: it fixes the OUTPUT gap without
# asserting the cause, and it is not a replacement for the heartbeat.
#
# THIS IS COMPLEMENTARY, NOT A DUPLICATE. The Cowork heartbeat does the analysis
# half — surfacing due loops, the staleness sweep, the ack clocks. This job only
# rebuilds the two artifacts that need the repo. If the Cowork side is later
# proven able to run them, retire this rather than leaving both.
#
# WEEKDAYS ONLY, before the morning heartbeat, matching the standing weekend
# stand-down: Sat/Sun are not workdays for Joe or Dell and Monday absorbs.
#
# SKIP-not-FAIL on missing env, per house convention.
#
# NORMAL MODE maintains only the canonical review queue. The legacy brief-pack
# files are recovery diagnostics, not a delivery surface: `morning-brief` is the
# record-native reader. Keeping the review-queue launchd cadence does not grant
# normal mode permission to recreate those retired files or write Drive.
REPO="$HOME/carr-system"
LOG="$REPO/out/local-briefs.log"
[ -f "$HOME/.config/carr/db.env" ] || { echo "$(date -u +%FT%TZ) SKIP no db.env" >> "$LOG"; exit 78; }
cd "$REPO" || exit 1

RECOVERY=0
if [ "${1:-}" = "--recovery" ]; then
  RECOVERY=1
  [ -n "${CARR_RECOVERY_REASON:-}" ] || {
    echo "local-briefs: --recovery requires CARR_RECOVERY_REASON" >&2
    exit 2
  }
elif [ -n "${1:-}" ]; then
  echo "usage: bin/local-briefs.sh [--recovery]" >&2
  exit 2
fi

rc=0
brief_ok=0
if [ "$RECOVERY" -eq 1 ]; then
  echo "$(date -u +%FT%TZ) RECOVERY brief-pack reason=${CARR_RECOVERY_REASON}" >> "$LOG"
  if ./run.sh brief-pack --quiet --recovery >> "$LOG" 2>&1; then
    echo "$(date -u +%FT%TZ) OK brief-pack recovery" >> "$LOG"
    brief_ok=1
  else
    echo "$(date -u +%FT%TZ) FAIL brief-pack recovery rc=$?" >> "$LOG"; rc=1
  fi
else
  # The live composite is read on demand through `morning-brief`. Do not create
  # local lookalikes in normal mode: an unavailable source must stay visibly
  # unavailable, never become a plausible stale file.
  brief_ok=1
  echo "$(date -u +%FT%TZ) SKIP legacy brief-pack; canonical reader=morning-brief" >> "$LOG"
fi

if ./run.sh review-queue >> "$LOG" 2>&1; then
  echo "$(date -u +%FT%TZ) OK review-queue" >> "$LOG"
else
  echo "$(date -u +%FT%TZ) FAIL review-queue rc=$?" >> "$LOG"; rc=1
fi

# today.md USED TO BE WRITTEN HERE, and the doctrine cutoff retired it on
# 2026-08-20. The block concatenated three brief-pack sections into
# 00_Context/today.md in the vault because out/ is gitignored and the call list
# had no reader — three renewal windows had expired unread while the shortlist
# sat in a folder nobody opened.
#
# THAT PROBLEM IS SOLVED BY VERBS NOW, which is the only reason this is safe to
# remove. All three sections are served live:
#   the one thing        today-triage
#   renewal windows      today-triage (the T-6 and T-3 lease-event rows)
#   the claim card       claim-card, deployed to production 2026-08-20, 141 verbs
#
# The claim card is why this waited. promote-pool and decline-candidate each tell
# the caller to read the row from v_claim_card first, and until claim-card
# shipped, the markdown card was the only reader that existed anywhere — pulling
# the file before the verb would have left both write verbs naming a surface
# nothing could reach. Verified against the live Worker before this line was
# deleted: claim-card returned the same 9,778 claimable and 388 needs-contact
# totals this block used to print.
#
# Normal mode no longer builds the brief-pack sections at all. health-check.py
# therefore does not use their mtimes; `morning-brief` reports each canonical
# input as ready, empty, or unavailable at read time.

# Known defect, tracked as loop #182: review-queue's touches lane reports 0 while
# ingest_inbox holds real rows, because the read fails and the handler discards
# the error message. A zero here is not yet trustworthy.

tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit $rc
