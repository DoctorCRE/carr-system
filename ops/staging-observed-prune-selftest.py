#!/usr/bin/env python3
"""staging-observed-prune-selftest.py — fixtures for ops/staging-observed-prune.py.

Every case builds its own temp state directory and stamps mtimes explicitly, so
nothing here depends on wall-clock timing, on how long the suite takes to run,
or on the real out/staging-observed a live session is writing to right now.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRUNE = os.path.join(REPO, "ops", "staging-observed-prune.py")

spec = importlib.util.spec_from_file_location("staging_observed_prune", PRUNE)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
tracker = mod._load_tracker()

HOUR = 3600.0
DAY = 24 * HOUR


def make_state_dir():
    return tempfile.mkdtemp(prefix="sop-state-")


def write_at(path, content, age_s):
    with open(path, "w") as fh:
        fh.write(content)
    stamp = time.time() - age_s
    os.utime(path, (stamp, stamp))
    return path


def session_file(state_dir, name, age_s, with_lock=False):
    path = write_at(os.path.join(state_dir, f"{name}.json"),
                    json.dumps({"observed": ["a.py"], "pending": {}}), age_s)
    if with_lock:
        write_at(path + ".lock", "", age_s)
    return path


def run_prune(state_dir, dry_run=False, session_hours=48.0, temp_minutes=60.0):
    return mod.prune(state_dir, session_hours * HOUR, temp_minutes * 60.0,
                     tracker, dry_run=dry_run)


def case_aged_temp_orphans_removed_fresh_ones_kept():
    """THE 3.2GB SHAPE: 152 abandoned `.staging-observed-*` temps over 1MB
    apiece filled this Mac on 2026-08-27. A FRESH temp is another session's
    in-flight atomic write and must survive -- a reaper that took those would
    turn a disk-space fix into data loss."""
    state_dir = make_state_dir()
    try:
        aged = write_at(os.path.join(state_dir, tracker.TEMP_PREFIX + "aged"),
                        "x" * 4096, 3 * HOUR)
        fresh = write_at(os.path.join(state_dir, tracker.TEMP_PREFIX + "fresh"),
                         "x" * 4096, 60.0)
        report = run_prune(state_dir)
        ok = (not os.path.exists(aged) and os.path.exists(fresh)
              and report["temps_removed"] == 1
              and report["bytes_reclaimed"] == 4096)
        if not ok:
            print(f"       report={report!r} aged_gone={not os.path.exists(aged)} "
                  f"fresh_kept={os.path.exists(fresh)}")
        return ok
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)


def case_idle_session_files_removed_active_ones_kept():
    """The half no live write path can do: a hook only runs while a session
    runs, so the state file of a session that has ENDED is precisely the one
    nothing will ever touch again. Idleness is read off mtime because the
    tracker writes on EVERY Bash call, so an untouched file means an inactive
    session -- and a session still working must never lose its observations."""
    state_dir = make_state_dir()
    try:
        idle = session_file(state_dir, "long-gone", 5 * DAY, with_lock=True)
        active = session_file(state_dir, "still-working", 2 * HOUR, with_lock=True)
        report = run_prune(state_dir)
        ok = (not os.path.exists(idle) and not os.path.exists(idle + ".lock")
              and os.path.exists(active) and os.path.exists(active + ".lock")
              and report["sessions_removed"] == 1
              and report["locks_removed"] == 1
              and report["sessions_kept"] == 1)
        if not ok:
            print(f"       report={report!r}")
        return ok
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)


def case_boundary_is_the_configured_age_not_a_fixed_one():
    """The two ages are separate knobs and each must actually be consulted.
    A file idle for 30 hours survives the 48h default and does not survive a
    24h run -- which is the difference between a knob and a decoration."""
    state_dir = make_state_dir()
    try:
        session_file(state_dir, "thirty-hours-idle", 30 * HOUR)
        default_run = run_prune(state_dir, dry_run=True)
        tighter_run = run_prune(state_dir, dry_run=True, session_hours=24.0)
        ok = (default_run["sessions_removed"] == 0
              and tighter_run["sessions_removed"] == 1)
        if not ok:
            print(f"       default={default_run!r} tighter={tighter_run!r}")
        return ok
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)


def case_dry_run_removes_nothing_but_reports_everything():
    state_dir = make_state_dir()
    try:
        aged = write_at(os.path.join(state_dir, tracker.TEMP_PREFIX + "aged"),
                        "x" * 100, 3 * HOUR)
        idle = session_file(state_dir, "long-gone", 5 * DAY)
        report = run_prune(state_dir, dry_run=True)
        ok = (os.path.exists(aged) and os.path.exists(idle)
              and report["temps_removed"] == 1
              and report["sessions_removed"] == 1
              and report["bytes_reclaimed"] > 0)
        if not ok:
            print(f"       report={report!r} aged_kept={os.path.exists(aged)} "
                  f"idle_kept={os.path.exists(idle)}")
        return ok
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)


def case_foreign_files_named_never_swept():
    """A reaper that quietly deleted whatever else it found in this directory
    would erase the evidence that something else is writing there. Anything
    outside the three shapes the tracker writes is reported and left."""
    state_dir = make_state_dir()
    try:
        stranger = write_at(os.path.join(state_dir, "somebody-elses-notes.txt"),
                            "not ours", 9 * DAY)
        report = run_prune(state_dir)
        ok = (os.path.exists(stranger)
              and report["unrecognised"] == ["somebody-elses-notes.txt"]
              and report["sessions_removed"] == 0)
        if not ok:
            print(f"       report={report!r} stranger_kept={os.path.exists(stranger)}")
        return ok
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)


def case_absent_directory_is_exit_zero_not_a_failure():
    """A housekeeping step that reddens the nightly chain is a check people
    learn to skip -- the failure bin/nightly.sh's own comments name three
    times. A machine that has never run the tracker has no directory, and
    that is not a finding."""
    state_dir = os.path.join(make_state_dir(), "never-created")
    p = subprocess.run([sys.executable, PRUNE, "--state-dir", state_dir],
                       capture_output=True, text=True, timeout=60)
    ok = p.returncode == 0 and "nothing to prune" in p.stdout
    if not ok:
        print(f"       rc={p.returncode} stdout={p.stdout!r} stderr={p.stderr!r}")
    return ok


def case_main_end_to_end_prunes_and_exits_zero():
    """The real entry point, argument parsing included -- proves the wiring
    bin/nightly.sh actually calls, not just the in-process prune()."""
    state_dir = make_state_dir()
    try:
        aged = write_at(os.path.join(state_dir, tracker.TEMP_PREFIX + "aged"),
                        "x" * 2048, 3 * HOUR)
        idle = session_file(state_dir, "long-gone", 5 * DAY)
        active = session_file(state_dir, "still-working", 1 * HOUR)
        p = subprocess.run([sys.executable, PRUNE, "--state-dir", state_dir],
                           capture_output=True, text=True, timeout=60)
        ok = (p.returncode == 0
              and not os.path.exists(aged) and not os.path.exists(idle)
              and os.path.exists(active)
              and "1 temp orphan(s)" in p.stdout
              and "1 observation file(s)" in p.stdout)
        if not ok:
            print(f"       rc={p.returncode} stdout={p.stdout!r} stderr={p.stderr!r}")
        return ok
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)


def case_nightly_chain_actually_calls_it():
    """A prune nothing runs is the 3.2GB again in a month. bin/nightly.sh must
    both LAUNCH it as a step and list it in the preflight surface check --
    ops/launchd-plist-parity.py sat uncalled for nine days after being written
    for a real hole, and this file is the same shape of thing."""
    with open(os.path.join(REPO, "bin", "nightly.sh")) as fh:
        chain = fh.read()
    launched = 'step "staging-observed prune' in chain and \
               "ops/staging-observed-prune.py" in chain
    # The preflight list and the step call are different lines; a rename that
    # updates one and not the other is exactly what this asserts against.
    preflight = chain.count("ops/staging-observed-prune.py") >= 2

    # AND IT RUNS BEFORE THE BACKUP. The first cut of this step sat beside the
    # hook telemetry rollup near the bottom of the chain, because both price
    # the enforcement stack -- one in latency, one in bytes. Tidy, and the
    # wrong order: the encrypted backup is the LAST step and it writes a dump,
    # so a full disk failed the backup and then freed 3.2GB immediately
    # afterwards. Reclamation has to precede the steps that need the space,
    # and "it is in the file somewhere" does not say that.
    prune_at = chain.index('step "staging-observed prune')
    backup_at = chain.index('step "encrypted backup')
    before_backup = prune_at < backup_at
    ok = launched and preflight and before_backup
    if not ok:
        print(f"       launched={launched} preflight_listed={preflight} "
              f"before_backup={before_backup}")
    return ok


CASES = [
    ("aged-temp-orphans-removed-fresh-ones-kept", case_aged_temp_orphans_removed_fresh_ones_kept),
    ("idle-session-files-removed-active-ones-kept", case_idle_session_files_removed_active_ones_kept),
    ("boundary-is-the-configured-age", case_boundary_is_the_configured_age_not_a_fixed_one),
    ("dry-run-removes-nothing-but-reports-everything", case_dry_run_removes_nothing_but_reports_everything),
    ("foreign-files-named-never-swept", case_foreign_files_named_never_swept),
    ("absent-directory-is-exit-zero", case_absent_directory_is_exit_zero_not_a_failure),
    ("main-end-to-end-prunes-and-exits-zero", case_main_end_to_end_prunes_and_exits_zero),
    ("nightly-chain-actually-calls-it", case_nightly_chain_actually_calls_it),
]


def main():
    if not os.path.exists(PRUNE):
        print(f"FAIL: {PRUNE} not found")
        return 1
    passed = failed = 0
    bad = []
    for name, fn in CASES:
        try:
            ok = fn()
        except Exception as exc:
            ok = False
            print(f"       ({name} raised: {type(exc).__name__}: {exc})")
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        if not ok:
            bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:48} verdict={'as expected' if ok else 'WRONG'}")
    print()
    print(f"staging-observed-prune-selftest: {passed}/{passed + failed} passed")
    if bad:
        print("FAILURES: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
