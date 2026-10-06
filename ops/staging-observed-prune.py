#!/usr/bin/env python3
"""staging-observed-prune.py — the half of the disk fix no live write path can do.

WHAT FILLED THE DISK (2026-08-27). out/staging-observed held 152 orphaned temp
files over 1MB apiece, about 3.2GB, beside per-session observation files, one of
them 110MB. The machine ran out of room and no new worktree could be created.
Cleared by hand that day; this and the write-path sweep in
hooks/staging-observation-tracker.py are what make the clearing structural.

WHY BOTH HALVES ARE NEEDED, and why neither is enough alone:

  THE HOOK sweeps temp orphans before each of its own writes, which is the only
  place that can catch one PROMPTLY — the tracker fires twice per Bash call, so
  a leftover is normally gone within minutes of the next session's first
  command. But a hook only runs while a session runs. It cannot reap the state
  file of a session that has ENDED, and that file is exactly what nothing else
  will ever touch again: the last write is the one that keeps its own bytes.

  THIS STEP reaps by absence-of-activity, which is a question only something
  outside the sessions can ask. It also catches temp orphans on a machine that
  went quiet — if no session runs a Bash command for a week, the hook's sweep
  never fires either, and 3.2GB sits there for a week.

WHY MTIME AND NOT A LIVENESS CHECK. There is no session registry to consult and
a session id is not a pid, so "is this session alive" is not answerable here.
What IS answerable is when its state file was last written, and the tracker
writes on EVERY Bash call in the session — so an untouched file means an
inactive session, whether it exited cleanly, was killed, or is merely idle. The
48-hour default makes idleness an unlikely reading: a session idle for two days
whose file is reaped loses its accumulated observations, and the cost of that is
one honest "not attributed" refusal downstream, which is the same fail-safe
direction the tracker itself takes when a pre-snapshot is missing.

READ-ONLY ON ANYTHING IT DOES NOT RECOGNISE. It removes exactly three shapes,
all of them written by the tracker: `.staging-observed-*` temps, `<session>.json`
state files, and their `<session>.json.lock` companions. Anything else in that
directory is left alone and reported, rather than being swept because it
happened to be in a directory named for something else.

EXIT 0 EVEN WHEN IT REMOVES NOTHING, and even when the directory is absent. A
housekeeping step that reddens the nightly chain teaches people to stop reading
the chain — the failure bin/nightly.sh's own comments name three times. A real
error (an unreadable directory) exits 1, because that IS something to look at.

Fixtures: ops/staging-observed-prune-selftest.py
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "staging-observation-tracker.py")


def _load_tracker():
    """Import the hook for its TEMP_PREFIX and sweep, rather than restating them.

    The prefix and the age rule are the contract between the writer and this
    reaper. A second copy of either is a drift waiting to happen — and this
    repo has paid for exactly that shape before (rule a8c55a47: the snapshot
    and the gate that reads it share their resolver so they cannot disagree).
    """
    spec = importlib.util.spec_from_file_location("staging_observation_tracker", HOOK)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {HOOK}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def prune(state_dir, session_max_age_s, temp_max_age_s, tracker, dry_run=False,
          now_s=None):
    """Returns a report dict. Never raises for an absent directory."""
    now_s = time.time() if now_s is None else now_s
    report = {"state_dir": state_dir, "exists": os.path.isdir(state_dir),
              "temps_removed": 0, "sessions_removed": 0, "locks_removed": 0,
              "bytes_reclaimed": 0, "sessions_kept": 0, "unrecognised": []}
    if not report["exists"]:
        return report

    names = sorted(os.listdir(state_dir))
    temp_cutoff = now_s - temp_max_age_s
    session_cutoff = now_s - session_max_age_s

    def remove(path, key):
        report["bytes_reclaimed"] += _size(path)
        report[key] += 1
        if not dry_run:
            try:
                os.unlink(path)
            except OSError:
                pass

    for name in names:
        path = os.path.join(state_dir, name)
        if not os.path.isfile(path):
            continue
        if name.startswith(tracker.TEMP_PREFIX):
            try:
                if os.path.getmtime(path) < temp_cutoff:
                    remove(path, "temps_removed")
            except OSError:
                pass
            continue
        if name.endswith(".json.lock"):
            continue                    # handled with the session it belongs to
        if not name.endswith(".json"):
            report["unrecognised"].append(name)
            continue
        try:
            idle = os.path.getmtime(path) < session_cutoff
        except OSError:
            continue
        if not idle:
            report["sessions_kept"] += 1
            continue
        remove(path, "sessions_removed")
        # The lock goes with its session, and only with its session: an
        # orphaned lock file is harmless (flock on a fresh file costs nothing)
        # but it is still one inode per session that has ever run here.
        lock = path + ".lock"
        if os.path.isfile(lock):
            remove(lock, "locks_removed")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state-dir",
                    default=os.path.join(REPO, "out", "staging-observed"))
    ap.add_argument("--session-max-age-hours", type=float, default=48.0,
                    help="remove a session's observation file after this long "
                         "with no write to it (default: 48)")
    ap.add_argument("--temp-max-age-minutes", type=float, default=60.0,
                    help="remove an abandoned .staging-observed-* temp after "
                         "this long (default: 60)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be removed and remove nothing")
    args = ap.parse_args(argv)

    try:
        tracker = _load_tracker()
    except Exception as exc:
        print(f"staging-observed-prune: cannot load the tracker it shares its "
              f"contract with: {exc}", file=sys.stderr)
        return 1

    try:
        report = prune(args.state_dir,
                       args.session_max_age_hours * 3600.0,
                       args.temp_max_age_minutes * 60.0,
                       tracker, dry_run=args.dry_run)
    except OSError as exc:
        print(f"staging-observed-prune: {exc}", file=sys.stderr)
        return 1

    if not report["exists"]:
        print(f"staging-observed-prune: no {args.state_dir} — nothing to prune")
        return 0

    mb = report["bytes_reclaimed"] / (1024.0 * 1024.0)
    verb = "would remove" if args.dry_run else "removed"
    print(f"staging-observed-prune: {verb} {report['temps_removed']} temp "
          f"orphan(s) older than {args.temp_max_age_minutes:g}m, "
          f"{report['sessions_removed']} observation file(s) idle over "
          f"{args.session_max_age_hours:g}h "
          f"(+{report['locks_removed']} lock file(s)); "
          f"{mb:.1f}MB reclaimed; {report['sessions_kept']} active file(s) kept")
    if report["unrecognised"]:
        # Named, not swept. Something else writing into this directory is a
        # finding, and a reaper that quietly deleted it would erase the evidence.
        print("staging-observed-prune: left alone (not written by the tracker): "
              + ", ".join(report["unrecognised"][:10]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
