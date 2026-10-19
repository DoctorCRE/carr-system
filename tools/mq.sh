#!/usr/bin/env bash
# usage: R=owner/repo tools/mq.sh PR...
#        tools/mq.sh --queue owner/repo queue-file log-file
# Queue entries are PR numbers, one per line. Terminal verdicts leave the queue.
R=${R:-jbookout/carr-system}
Q=
if [ "${1:-}" = --queue ]; then
  [ "$#" = 4 ] || { echo 'usage: tools/mq.sh --queue owner/repo queue-file log-file' >&2; exit 2; }
  R=$2; Q=$3; LOG=$4
  [ -f "$Q" ] || { echo "Queue file does not exist: $Q" >&2; exit 2; }
  touch "$LOG" || exit 1
else
  [ "$#" -gt 0 ] || { echo 'usage: R=owner/repo tools/mq.sh PR...' >&2; exit 2; }
  for p in "$@"; do
    [[ "$p" =~ ^[0-9]+$ ]] || { echo "Invalid PR number: $p" >&2; exit 2; }
  done
fi
[[ "$R" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+$ ]] && [[ "$R" != */. && "$R" != */.. ]] || { echo "Invalid repository: $R" >&2; exit 2; }

# The acquisition mutex prevents two stale-lock reapers from deleting a new owner.
LOCK=$(python3 - "$R" "$$" <<'PY'
import fcntl, os, shutil, sys, time
from pathlib import Path
repo, pid = sys.argv[1], int(sys.argv[2])
root = Path(os.environ.get('CARR_MQ_LOCK_ROOT', f'/tmp/carr-mq-{os.getuid()}'))
owner, name = repo.lower().split('/')
root = root / owner
root.mkdir(mode=0o700, parents=True, exist_ok=True)
lock = root / (name + '.lock')
with (root / (lock.name + '.guard')).open('a') as guard:
    fcntl.flock(guard, fcntl.LOCK_EX)
    if lock.exists():
        try:
            owner = int((lock / 'pid').read_text())
        except (OSError, ValueError):
            owner = None
        if owner is not None and owner > 0:
            try:
                os.kill(owner, 0)
            except ProcessLookupError:
                pass
            except PermissionError:
                sys.exit(f'tools/mq.sh already running for {repo} (pid {owner})')
            else:
                sys.exit(f'tools/mq.sh already running for {repo} (pid {owner})')
        shutil.rmtree(lock)
    lock.mkdir()
    (lock / 'pid').write_text(str(pid) + '\n')
    (lock / 'start').write_text(str(time.time()) + '\n')
print(lock)
PY
) || exit 1
cleanup() {
  python3 - "$LOCK" "$$" <<'PY'
import fcntl, shutil, sys
from pathlib import Path
lock = Path(sys.argv[1])
with lock.with_name(lock.name + '.guard').open('a') as guard:
    fcntl.flock(guard, fcntl.LOCK_EX)
    if lock.exists() and (lock / 'pid').read_text().strip() == sys.argv[2]:
        shutil.rmtree(lock)
PY
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

merge_one() {
  local p i J st ms sha behind red pending
  p=$1
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
}

if [ -n "$Q" ]; then
  for ((idle=0; idle<720; )); do
    p=$(grep -m1 -E '^[0-9]+$' "$Q")
    if [ -z "$p" ]; then idle=$((idle+1)); sleep 60; continue; fi
    idle=0
    # Do not run merge_one in a subshell: the lock's PID owns every GitHub call.
    merge_one "$p" > "$LOCK/result" 2>&1
    out=$(grep -E '^(MERGED|RED|CONFLICT|MERGEFAIL)' "$LOCK/result" | tail -1)
    printf '%s %s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$R" "${out:-MERGEFAIL $p}" | tee -a "$LOG" || exit 1
    python3 - "$Q" "$p" <<'PY' || exit 1
import fcntl, sys
with open(sys.argv[1], 'r+') as queue:
    fcntl.flock(queue, fcntl.LOCK_EX)
    lines = queue.readlines()
    queue.seek(0)
    queue.writelines(line for line in lines if line.strip() != sys.argv[2])
    queue.truncate()
PY
    rm -f "$LOCK/result"
    sleep 5
  done
else
  for p in "$@"; do merge_one "$p"; done
fi
