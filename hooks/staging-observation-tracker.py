#!/usr/bin/env python3
"""staging-observation-tracker.py — Pre+PostToolUse Bash: attribute by
OBSERVATION, not by tool type.

WHY. staging-attribution-gate.py (decision 9e1f83c2) answers "did THIS
session write this file" purely from the session transcript's own
Write/Edit/MultiEdit tool_use records. That is ground truth for the
harness's own file-editing tools, but it is blind to a tracked file changed
by a SCRIPT this session ran through Bash — a pipeline run, a shell
redirect, `sed -i`, a migration, the nightly chain regenerating a render.
Those leave no Write/Edit/MultiEdit record, so the file reads as "not
written by this session" and staging it is refused — even though a standing
rule (bc9188b4) requires every unattended run, scheduled ones included, to
commit its own work. Tonight's nightly chain could be refused staging its
own regenerated output.

THE FIX: attribute by OBSERVATION. This one script runs on BOTH sides of
every Bash call this session makes (it is wired twice in ops/config/
hooks.json: once as a PreToolUse hook and once as a PostToolUse hook on the
same "Bash" matcher, and branches on payload["hook_event_name"]):

  PreToolUse  — snapshot `git status --porcelain` for the repo, keyed to
                THIS EXACT tool_use_id, before the command runs.
  PostToolUse — snapshot again after the command runs, diff against the
                matching pre-snapshot for that same tool_use_id, and credit
                every TRACKED path that went from clean-or-absent to dirty
                as OBSERVED-DIRTY for this session. Accumulates across the
                whole session in out/staging-observed/<session_id>.json.

staging-attribution-gate.py unions this observed set with its own
transcript scan (own_written_paths() UNION observed_dirty_paths()) when it
decides whether a staged path belongs to this session.

THE PostToolUse PAYLOAD WAS VERIFIED BEFORE COMMITTING TO THIS DESIGN, not
assumed: `strings` on the installed Claude Code binary
(cli.js-equivalent) shows the exact PreToolUse/PostToolUse hook-input
construction —

    PreToolUse:  {...mh(...), hook_event_name:"PreToolUse",
                  tool_name, tool_input, tool_use_id}
    PostToolUse: {...mh(...), hook_event_name:"PostToolUse",
                  tool_name, tool_input, tool_response, tool_use_id,
                  duration_ms}

where mh(...) supplies {session_id, transcript_path, cwd, prompt_id,
permission_mode, agent_id, agent_type, effort}. Two things that finding
settles: (1) `tool_use_id` is present and IDENTICAL on both the Pre and the
Post call for one invocation, which is exactly the correlation key this
design needs and was not assumed; (2) this tracker never needs to parse
`tool_response` (Bash stdout/stderr) at all, because it computes the actual
post-command git state itself via a real `git status` call rather than
trying to infer it from command output — which sidesteps needing to know
that field's exact shape.

WHY DIFF AGAINST THE PER-CALL PRE-SNAPSHOT, NOT SESSION START. A path
already dirty from another session's in-flight edit before this session's
first Bash call must NEVER become "observed" just because this session later
runs an unrelated Bash command in the same shared tree — that would reopen
exactly the incident staging-attribution-gate.py exists to close. Diffing
against the immediately-prior (per-call) snapshot means only a path that
changed STATE as a direct result of the command just run gets credited,
never a path that was already dirty and stayed exactly as dirty.

SCOPE: only Bash calls whose cwd resolves inside this repo. A brand-new
untracked path ("??") is never credited here — staging-attribution-gate.py
already allows those unconditionally, for the same reason its own docstring
gives, so crediting them here would be redundant, not additive.

FAILS OPEN AND SILENT on both sides, like every hook in this file: this
tracker never blocks a tool call (it holds no gate logic at all) and never
surfaces an error to the session. Losing an observation only costs
staging-attribution-gate.py one honest "not attributed, name it or override"
refusal on that one path later — the safe direction, and recoverable by the
override envelope that already exists.

Fixtures: ops/staging-observation-tracker-selftest.py
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(REPO, "out", "staging-observed")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    DEBUG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    DEBUG = os.path.join(REPO, "out", "hook-guard.log")
MAX_PENDING = 200  # defensive cap: a killed session must not leak forever

# ── WHAT THIS TRACKER DID TO THE DISK, 2026-08-27 ────────────────────────────
# On 2026-08-27 out/staging-observed held 152 orphaned temp files over 1MB
# apiece — about 3.2GB — beside per-session state files, one of them 110MB. The
# machine filled up and no new worktree could be created. Three separate
# mechanisms, each bounded below; the first was the wasted bytes, the second
# made every one of them large, the third let them accumulate forever.
#
# 1. ORPHANED TEMP FILES. write_state_unlocked() writes through
#    tempfile.mkstemp + os.replace, which is the right way to write a file
#    atomically. Its `finally` unlinks the temp on the failure path, but a
#    session KILLED between mkstemp and os.replace runs no finally — SIGKILL,
#    a panic and a reboot all skip it — and leaves the temp behind under a
#    name nothing ever looks at again. Every write path now sweeps its own
#    siblings first (sweep_temp_orphans below).
#
# 2. THE PRE-SNAPSHOTS WERE MOSTLY UNTRACKED CHURN. `pending` held up to
#    MAX_PENDING full `git status --porcelain --untracked-files=all` maps. In
#    the 4.4MB file measured that day, 6504 of a snapshot's 6506 entries were
#    "??" — .claude/exec-clones/ and friends — repeated across 23 in-flight
#    calls. tracked_only() drops them at store time, which is OUTPUT-
#    EQUIVALENT rather than a tradeoff: the PostToolUse loop consults `before`
#    only for paths whose AFTER code is not "??", and for such a path a stored
#    "??" and a missing entry both compare unequal to the after code, so both
#    credit it identically. The selftest asserts that equivalence directly.
#
# 3. NOTHING BOUNDED THE FILE IN BYTES. MAX_PENDING bounds the number of
#    snapshots, not their size, so a tree with a large dirty set still grows
#    without limit — which is what a 110MB observation file is. _bound_state()
#    adds a byte budget, evicting OLDEST pending first and never touching
#    `observed`, which is the product and stays small (a set of tracked repo
#    paths, ~1.5KB in that same file).
#
# Losing a pending snapshot costs exactly what a missing pre-snapshot already
# costs in the PostToolUse branch below: credit nothing for that one call. That
# is the fail-safe direction this file already takes everywhere.
#
# The other half of the fix is not here and cannot be: a session that dies
# leaves its LAST state file behind forever, and no live write path can reap a
# session that is gone. bin/nightly.sh prunes those (ops/staging-observed-prune.py).
TEMP_PREFIX = ".staging-observed-"
TEMP_ORPHAN_MAX_AGE_S = 3600     # an atomic write takes milliseconds; an hour
                                 # old is abandoned, never in flight
MAX_STATE_BYTES = 4 * 1024 * 1024

# WHICH TREE THIS SNAPSHOTS, and it was the wrong one for every worktree session.
# This hook is wired by its absolute CANONICAL path, so `REPO` above is always
# ~/carr-system however deep in a worktree the session actually is — and the
# credit set it builds is consumed by staging-attribution-gate.py, which was
# reading the same wrong tree, so the two agreed with each other and disagreed
# with reality. Canonical's porcelain never lists anything under a worktree
# (`.claude/worktrees/` is gitignored there), so a worktree session's observed
# set was assembled from CANONICAL's unrelated churn: paths it never touched,
# and none of the paths it did.
#
# Council recommendation 1, 2026-08-23 process audit. The resolver is shared with
# staging-attribution-gate.py and git-writer-gate.py precisely so the snapshot
# and the gate that reads it cannot drift apart again (rule a8c55a47).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from worktree_scope import target_tree  # noqa: E402


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        with open(DEBUG, "a") as fh:
            fh.write(f"{now()} staging-observation-tracker {msg}\n")
    except Exception:
        pass


def _safe_session(session_id):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (session_id or "unknown"))


def state_path(session_id):
    return os.path.join(STATE_DIR, f"{_safe_session(session_id)}.json")


def read_state_unlocked(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("observed", [])
            data.setdefault("pending", {})
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"observed": [], "pending": {}}


def sweep_temp_orphans(dirpath, max_age_s=TEMP_ORPHAN_MAX_AGE_S, now_s=None):
    """Delete abandoned mkstemp leftovers beside the state files. Returns the
    number removed.

    Age is the whole safety argument. These temps are siblings of OTHER
    sessions' in-flight writes, and this hook holds only its own session's
    lock, so it must never remove one a live write is about to os.replace into
    place. A write here is a json.dump of a few megabytes at most — under a
    second. An hour is four orders of magnitude of headroom, and matches the
    same reasoning bin/nightly.sh applies to its own .nightly-stderr.* captures.

    Never raises: a housekeeping failure must not change what this hook
    observes, and the caller wraps it besides.
    """
    removed = 0
    cutoff = (time.time() if now_s is None else now_s) - max_age_s
    try:
        names = os.listdir(dirpath)
    except OSError:
        return 0
    for name in names:
        if not name.startswith(TEMP_PREFIX):
            continue
        candidate = os.path.join(dirpath, name)
        try:
            if os.path.getmtime(candidate) >= cutoff:
                continue
            os.unlink(candidate)
            removed += 1
        except OSError:
            continue                    # gone under us, or not ours to remove
    return removed


def tracked_only(status):
    """The half of a porcelain snapshot the Post-side diff can actually use.

    See mechanism 2 in the block at the top of this file: dropping "??" here is
    output-equivalent, not a sampling tradeoff.
    """
    return {path: code for path, code in status.items() if code != "??"}


def _bound_state(data):
    """Keep one session's state file bounded in COUNT and in BYTES.

    Evicts oldest-inserted pending snapshots first (dicts preserve insertion
    order), and never evicts `observed` — that is the thing this hook exists to
    produce, and it is small.
    """
    pending = data.get("pending")
    if not isinstance(pending, dict):
        return
    keys = list(pending)
    if len(keys) > MAX_PENDING:
        for stale_key in keys[: len(keys) - MAX_PENDING]:
            pending.pop(stale_key, None)
        keys = list(pending)
    sizes = [(key, len(json.dumps(pending[key]))) for key in keys]
    total = sum(size for _, size in sizes)
    for key, size in sizes:
        if total <= MAX_STATE_BYTES:
            break
        pending.pop(key, None)
        total -= size


def write_state_unlocked(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _bound_state(data)
    try:
        sweep_temp_orphans(os.path.dirname(path))
    except Exception as exc:            # housekeeping is never load-bearing
        dlog(f"temp-orphan sweep failed: {exc}")
    fd, tmp = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as fh:
            # NOT sort_keys=True, and the reason is load-bearing rather than
            # cosmetic. Both caps below evict OLDEST-FIRST, which they read off
            # dict insertion order — and insertion order only survives a
            # round-trip through this file if the file preserves it. Sorting
            # here re-ordered `pending` by call id, which is random, so after
            # any reload "oldest" meant "alphabetically first". Caught by the
            # byte-cap fixture, which read its own three entries back in the
            # wrong order. `observed` is a sorted list built by the caller, so
            # the file stays deterministic where determinism was the point.
            json.dump(data, fh)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def locked_state(session_id):
    path = state_path(session_id)
    os.makedirs(STATE_DIR, exist_ok=True)
    lock_path = path + ".lock"
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = read_state_unlocked(path)
        try:
            yield data
        finally:
            write_state_unlocked(path, data)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def porcelain_status(repo=REPO):
    """{repo-relative-posix-path: 2-char XY status} for the whole tree."""
    try:
        out = subprocess.run(
            ["git", "-C", repo, "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, timeout=20,
        ).stdout
    except Exception:
        return {}
    status = {}
    for line in out.splitlines():
        if len(line) <= 3:
            continue
        code = line[:2]
        rest = line[3:].strip()
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        status[rest.strip('"')] = code
    return status


def in_repo(cwd, repo=REPO):
    try:
        path = os.path.realpath(os.path.expanduser(cwd or ""))
        # realpath the comparison root too: on macOS /var is a symlink to
        # /private/var, so an un-resolved `repo` (REPO itself is built from
        # abspath, not realpath, and a caller/fixture may pass a raw
        # tempfile.mkdtemp() path) would never equal a realpath'd cwd even
        # when they name the same directory.
        repo_real = os.path.realpath(os.path.expanduser(repo or ""))
    except Exception:
        return False
    return path == repo_real or path.startswith(repo_real + os.sep)


def handle(payload, repo=REPO):
    """Pure-ish core, given a decoded payload. Returns nothing; only side
    effect is the per-session state file. Split out from main() so the
    selftest can drive it directly against a fixture repo without spawning a
    subprocess for every case."""
    if (payload.get("tool_name") or payload.get("toolName")) != "Bash":
        return
    if not in_repo(payload.get("cwd"), repo):
        return
    session_id = payload.get("session_id") or payload.get("sessionId") or "unknown"
    call_id = payload.get("tool_use_id") or payload.get("toolUseId") or ""
    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""

    # The tree this call is about. Pre and Post derive it from the same command
    # and the same cwd, so the two snapshots are always of the same checkout —
    # which is the property the whole before/after diff rests on.
    ti = payload.get("tool_input") or payload.get("toolInput") or {}
    cmd = ti.get("command", "") if isinstance(ti, dict) else ""
    tree = target_tree(cmd, payload.get("cwd"), repo=repo) or repo

    if event == "PreToolUse":
        # tracked_only: store the half of the snapshot the Post side can use.
        # The MAX_PENDING trim that used to sit here moved into _bound_state(),
        # which runs on every write instead of only on this branch — so a
        # session that is only ever popping entries still gets bounded, and
        # there is one place to read for "what keeps this file small".
        snapshot = tracked_only(porcelain_status(tree))
        with locked_state(session_id) as data:
            if call_id:
                data["pending"][call_id] = snapshot
        return

    if event == "PostToolUse":
        after = porcelain_status(tree)
        with locked_state(session_id) as data:
            before = data["pending"].pop(call_id, None) if call_id else None
            if before is None:
                # No matching pre-snapshot -- harness restarted mid-call, no
                # tool_use_id on this build, or this Bash call somehow never
                # fired the Pre side. Fail SAFE: credit nothing rather than
                # guess, exactly like every other gate in this file.
                dlog(f"no pre-snapshot for call_id={call_id!r} session={session_id!r}")
                return
            observed = set(data["observed"])
            for path, code in after.items():
                if code == "??":
                    continue  # brand-new file; staging-attribution-gate.py
                              # already allows these unconditionally
                if before.get(path) != code:
                    observed.add(path)
            data["observed"] = sorted(observed)
        return


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    try:
        handle(payload)
    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
    sys.exit(0)  # this tracker never blocks; it only ever observes


if __name__ == "__main__":
    main()
