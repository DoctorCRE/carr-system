#!/bin/bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
if [ "$#" = 1 ] && [ "$1" = --from-work-queue ]; then
  pins=$(python3 "$root/tools/room-bridge/dispatch.py" queue | jq -er '
    if .schema != "room-work-queue/v1" or (.items | type) != "array" then
      error("invalid work queue snapshot")
    else [.items[] | select(.stage == "merging") | .prs[] |
      [.repo, (.number | tostring), .head] | @tsv] | join("\n") end')
  while IFS=$'\t' read -r repo pr sha; do
    [ -n "$repo" ] || continue
    bash "$root/tools/approve-merge.sh" "$repo" "$pr" "$sha"
    sleep 2
  done <<< "$pins"
  exit 0
fi
if [ "$#" != 3 ] || ! [[ "$1" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ && "$2" =~ ^[1-9][0-9]*$ && "$3" =~ ^[0-9a-f]{40}$ ]]; then
  echo 'Usage: tools/approve-merge.sh <owner/repo> <pr> <approved_sha> | --from-work-queue' >&2
  exit 64
fi
repo=$1
pr=$2
approved_sha=$3
snapshot=$(gh pr view "$pr" --repo "$repo" --json state,headRefOid,labels,autoMergeRequest)
if ! jq -e '(.labels | type == "array") and (.labels | all(.name | type == "string"))' <<< "$snapshot" >/dev/null; then
  echo 'Refusing approval: PR label evidence is unreadable.' >&2
  exit 1
fi
if [ "$(jq -r '.state' <<< "$snapshot")" != OPEN ]; then
  echo 'Refusing approval: PR is not open.' >&2
  exit 1
fi
if [ "$(jq -r '.headRefOid' <<< "$snapshot")" != "$approved_sha" ]; then
  echo 'Refusing approval: live head differs from approved SHA.' >&2
  exit 1
fi
if jq -e '.labels | any(.name == "do_not_merge")' <<< "$snapshot" >/dev/null; then
  echo 'Refusing approval: do_not_merge label is present.' >&2
  exit 1
fi
if jq -e '.autoMergeRequest != null' <<< "$snapshot" >/dev/null; then
  exit 0
fi
sleep 2
gh pr merge "$pr" --repo "$repo" --auto --squash --match-head-commit "$approved_sha"
