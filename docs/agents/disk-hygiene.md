# Disk hygiene

`ops/disk-reaper.py` cleans retained checkouts, scratch, worktrees, test runs,
and staged retirements on each Mac. It makes no model calls. Its policy lives in
`ops/config/disk-reaper.v1.json`. Inspection is the default:

```sh
python3 ops/disk-reaper.py
```

Inspect `out/disk-reaper.jsonl`. Each run appends candidates, keep reasons,
removed paths, allocated bytes removed, process signals, errors, and free space.
`would_free_bytes` totals eligible bytes in inspection mode. `kept_by_reason`
groups preserved candidates by reason with `bytes`, `count`, and
`unmeasured_count`. Unscanned candidates contribute to the count but not the
byte estimate; their missing sizes remain visible in `unmeasured_count`.
Allocated bytes are an estimate from `st_blocks`, not a claim about APFS snapshots
or the volume's physical recovery. The before and after free-space readings show
the volume's actual result.

The orchestrator owns the first live run. After reviewing the candidates, run:

```sh
python3 ops/disk-reaper.py --execute
```

## Retention and protection

| Location | Removal policy |
| --- | --- |
| `/private/tmp/room-codex-*` | Immediately after every matching dispatch job is released, with clean, published Git work |
| Other Git scratch in `/private/tmp` | 24 hours since the latest file or index write |
| `.claude/worktrees`, sibling `carr-system-fix-*`, `~/worktrees`, `~/orch-wt`, `~/.cache/wt-*`, `out/wt-*`, `out/orch/wt-dirty/*` | Immediately for closed or merged PRs, otherwise after three idle days |
| `out/orch/e2e-*` | Keep the newest two complete trees in the e2e series |
| `out/release-pipeline/runs/*` | Keep the newest two runs |
| Release pipeline worktrees | Worktree policy, with cleared trees retained seven days |
| `_to_delete` contents | Seven days since the latest write, including nested worktrees |
| Session-registered scratch under `/private/tmp` or repository `out` | At Stop or SessionEnd, after the same safety checks |

The default config covers the three canonical repositories on both machines.
It deliberately targets release runs and worktrees, leaving release state and
lock files outside the candidate patterns. Bare, unidentified non-Git temporary
directories remain protected until their creator registers them.

Every removal vetoes uncommitted tracked changes, untracked work, local stashes,
and commits absent from refreshed remote evidence. Independent clones check all
local branches, tags, and stashes. Linked worktrees share a stash store, so a
stash protects only the worktree whose current branch matches the entry's
`On <branch>:` or `WIP on <branch>:` prefix. For a detached HEAD, the stash's
base commit must match that HEAD. Linked worktrees check their HEAD because
their common repository retains the other branches. A deleted upstream after a squash merge
requires a matching PR head fetched from GitHub's retained pull ref. A newer
local tip still vetoes removal.

Held or running dispatch jobs veto removal. An open PR updated within 24 hours
protects its checkout. An open PR's body, changed file paths, comments, and review
bodies protect referenced run artifacts. Failed GitHub queries preserve the
affected candidate. The protected list starts with
`/private/tmp/doctorcre-staging-release-1014` and
`/private/tmp/carr-v1-agent-control-1015`. Protection also covers containing and
contained paths.

The process census checks cwd, open files, and command arguments. Incomplete
native evidence preserves the directory. An ended dispatch or registered job
can authorize TERM for an old process reparented to PID 1 whose cwd remains in
the eligible directory. Active sessions, managed services, current executors,
PID reuse, and an unproved job end prevent signaling. A surviving process vetoes
removal. The reaper does not use SIGKILL. Symlinks are never traversed for scanning
or recursive deletion.

## Maintenance locks

The existing `lib/branch_retirement.py` fleet runner holds
`out/worktree-reap.lock` across fetching, PR-history classification, process
collection, and fleet actions. SessionStart and watchdog launches can overlap
that long section. This explains contention in the code path; it does not prove
which process held a particular historical lock.

The disk reaper reuses that module's Git commands, persistence, audit append,
and maintenance lock, plus the existing worktree inventory and orphan process
parsers. It takes the shared maintenance lock only for worktree removal. A held
lock preserves that worktree, records `lsof` holder evidence when available,
and allows unrelated scratch and artifact cleanup to continue. It never deletes
the lock inode or guesses that a held flock is stale. Git removal uses no force
flag and retains the local branch. Branch and PR retirement remain owned by the
existing branch janitor.

## Disk pressure

Each execute invocation measures free space and runs cleanup in the same pass.
Below either 15% free or 50 decimal GB free, any remaining pressure writes
`out/disk-pressure.flag` and a WARN row to `out/disk-pressure-health.json` and
`out/disk-pressure-health.jsonl`. The row names the orchestrator, dispatch
admission hold, remediation command, verification, and automatic clear condition.
A later successful execute invocation clears the flag only when both thresholds
pass. Inspection never arms or clears the flag.

The flag is the contract for the separately owned dispatch change in PR 1429.
This change does not make the current dispatcher consume it. The scheduled
job samples pressure every two hours; an explicit invocation samples immediately.

## Register session scratch

Record ownership when creating a directory, using the session's native ID:

```sh
python3 hooks/session-scratch-reap.py --session "$CODEX_SESSION_ID" --register /private/tmp/my-scratch
```

The hook stores ownership with the directory's device and inode in
`out/disk-scratch-registry.json`, under the reaper's lock. A different session
cannot take over an existing registration. A replacement directory does not
inherit the prior owner's deletion permission. The periodic reaper also records
new directories, using dispatch or session-directory ownership when available.
Unknown ownership remains unknown.

The adapter accepts native JSON events on stdin. Wire the same command to Stop
and SessionEnd through the client hook installer:

```sh
python3 "$HOME/carr-system/hooks/session-scratch-reap.py"
```

Stop and SessionEnd delete only scratch registered to that `session_id`. They
skip the periodic age delay while retaining every safety check. Explicit
`scratch_paths` in a creation event also register ownership. Optional PostToolUse
invocation recognizes successful standalone `mktemp -d` output. Chained shell
commands and arbitrary existing paths need explicit registration. The adapter
returns successfully if cleanup cannot proceed, and reports the preservation
reason to stderr.

Hook registration is an installation step. This PR does not change client
settings, hook declarations, the gate baseline, or `dispatch.py`.

## Install on both Macs

After merge and canonical checkout synchronization, run the existing installer
on the Studio and MacBook:

```sh
python3 ops/config-as-code.py install
python3 ops/config-as-code.py install --apply
```

The installer renders `{{REPO}}` in
`ops/launchd/com.carr.disk-reaper.plist`, installs the LaunchAgent, and verifies
registration. Its calendar fires at minute 07 every even hour. It uses
`StartCalendarInterval`, Homebrew's PATH, the canonical Python environment,
background priority, and no sidebar or model session. No job is installed or
started by the builder's fixture tests.

After the first scheduled run, inspect the latest JSONL run and pressure health
row. Confirm the recorded timestamp advances on the next scheduled firing.
If a Mac cannot collect `ps`, `launchctl`, or `lsof`, candidates stay on disk
with a reason until the native permission or collection failure is resolved.

## Verify changes

```sh
python3 ops/disk-reaper-selftest.py
python3 ops/branch-janitor-selftest.py
python3 ops/worktree-self-plumb-selftest.py
python3 ops/orphan-reaper-selftest.py
python3 ops/pg-orphan-reap-selftest.py
python3 tools/test-worktree-sweep.py
python3 ops/selftest-temp-cleanup-selftest.py
python3 ops/launchd-plist-portable-selftest.py
python3 ops/launchd-calendar-selftest.py
```

The new selftests execute deletion only inside automatically removed temporary
fixtures with real Git repositories. The native process test skips when the host
sandbox denies its census. Process parsing and preservation still have portable
fixture tests. The orchestrator's first live run supplies native fleet evidence.
