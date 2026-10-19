#!/usr/bin/env bash
# usage: tools/mq.sh PR... (serial, bounded; a PR behind main is updated before any red verdict,
# because the CI freshness class fails stale heads without GitHub reporting BEHIND)
R=${R:-jbookout/carr-system}
for p in "$@"; do
  for ((i=1; i<=80; i++)); do
    sleep 2
    J=$(gh pr view "$p" -R "$R" --json state,mergeStateStatus,headRefOid,statusCheckRollup) || { echo "MERGEFAIL $p"; break; }
    st=$(echo "$J" | jq -er .state) || { echo "MERGEFAIL $p"; break; }
    ms=$(echo "$J" | jq -er .mergeStateStatus) || { echo "MERGEFAIL $p"; break; }
    sha=$(echo "$J" | jq -er .headRefOid) || { echo "MERGEFAIL $p"; break; }
    [ "$st" = MERGED ] && { echo "MERGED $p (already)"; break; }
    [ "$st" = OPEN ] || { echo "MERGEFAIL $p"; break; }
    [ "$ms" = DIRTY ] && { echo "CONFLICT $p"; break; }
    behind=0
    if [ "$ms" != CLEAN ] && [ "$ms" != BEHIND ]; then
      sleep 2
      behind=$(gh api "repos/$R/compare/main...$sha" -q .behind_by 2>/dev/null)
      [[ "$behind" =~ ^[0-9]+$ ]] || behind=0
    fi
    if [ "$ms" = BEHIND ] || [ "$behind" -gt 0 ]; then
      sleep 2
      gh api -X PUT "repos/$R/pulls/$p/update-branch" -f update_method=merge >/dev/null && echo "UPDATED $p"
      sleep 120
      continue
    fi
    red=$(echo "$J" | jq -r '[.statusCheckRollup[] | select(.conclusion == "FAILURE" or .conclusion == "CANCELLED" or .conclusion == "TIMED_OUT" or .conclusion == "ACTION_REQUIRED" or .conclusion == "STARTUP_FAILURE" or .conclusion == "STALE" or .state == "FAILURE" or .state == "ERROR") | .name // .context] | join("; ")')
    [ -n "$red" ] && { echo "RED $p $sha: $red"; break; }
    pending=$(echo "$J" | jq '[.statusCheckRollup[] | select(.status == "QUEUED" or .status == "IN_PROGRESS" or .status == "WAITING" or .status == "PENDING" or .status == "REQUESTED" or .state == "PENDING")] | length')
    if [ "$ms" = CLEAN ] && [ "$pending" = 0 ]; then
      sleep 2
      gh pr merge "$p" -R "$R" --squash --match-head-commit "$sha" && echo "MERGED $p $sha" || echo "MERGEFAIL $p"
      break
    fi
    sleep 90
  done
  [ "$i" -le 80 ] || echo "MERGEFAIL $p"
done
