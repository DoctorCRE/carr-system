#!/usr/bin/env python3
"""ops/nightly-exports-retry-guard-selftest.py — proves bin/nightly-exports-retry.sh's
lock-race guard (#1241 review round 5) is correct at exact clock boundaries,
via a FIXED, INJECTED clock — not a wall-clock run. A single live 00:24 UTC
run is weak evidence for a midnight-rollover edge (Jev noul 0.19 on that
claim); this asserts the decision at three deliberately chosen moments.

THE BUG THIS GUARDS AGAINST. bin/nightly.sh's per-run archive under
out/nightly-runs/ is written ONLY at chain exit (carr_chain_exit), never
progressively while the chain runs. If a Mac sleeps through both the 02:05
scheduled nightly fire and this job's own daytime fire, launchd can fire both
missed jobs on the same wake. If the retry's own "latest archive" read still
shows only a stale (pre-boundary) archive at that moment — because tonight's
nightly hasn't finished archiving yet, or hasn't even started — and the retry
proceeded anyway, it could call carr_take_lock nightly BEFORE bin/nightly.sh
gets there. Lock ownership in bin/run-lock.sh is first-come-first-served, so
the retry would WIN and the real nightly chain would exit as a "duplicate",
skipping the WHOLE night's chain — a strictly worse outcome than the OneDrive
defect the retry exists to fix.

THE GUARD proves the retry only ever considers taking the lock when a nightly
run has ACTUALLY COMPLETED (successfully or not) since the most recent
scheduled 02:05-local fire. Three scenarios, each with a controlled CARR_NOW
and a single fabricated archive file:

  same_day             — a normal afternoon retry after last night's 02:xx
                          run completed and did not land OK: the guard must
                          NOT block a genuine retry.
  after_midnight_before_0205
                        — NOW is after UTC midnight but before tonight's own
                          02:05 fire; the relevant boundary is YESTERDAY's
                          02:05, and yesterday's completed (successful) run
                          satisfies it: the guard must resolve against that
                          run, not treat the odd hour as "nothing to retry".
  missed_night          — the latest archive predates even the last
                          scheduled 02:05 by a full day (nightly has not
                          completed since) — including, structurally, the
                          exact race window where tonight's chain is
                          currently running and simply has not archived yet.
                          The guard must SKIP without ever calling
                          carr_take_lock, rather than "catching up".

SAFETY. Every subprocess run is fully isolated from this machine's real
operational state: CARR_NIGHTLY_OUT_DIR points log/archive/marker paths at a
temp dir, CARR_LOCK_DIR points the shared "nightly" lock at a temp dir (so a
scenario that DOES pass the guard and reaches carr_take_lock never touches
the real lock a live nightly chain might hold), CARR_ROUTINE_DB_ENV_FILE
points at a path that does not exist (so a real ~/.config/carr/db.env on this
Mac is never read, even if one exists), and CARR_NIGHTLY_RETRY_DRY_RUN makes
record() log instead of writing to the real operational ledger. No scenario
here is constructed to reach the real `./run.sh export` call at all — every
one resolves to a SKIP (or, for same_day, a same-service "no exporter
credential" failure recorded before any export attempt) — but the isolation
holds regardless.

Run: python3 ops/nightly-exports-retry-guard-selftest.py
"""

import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "nightly-exports-retry.sh"

checked = 0
failed = 0


def ok(cond, label):
    global checked, failed
    checked += 1
    if cond:
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}")


def epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def archive_filename(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return f"nightly-{dt.strftime('%Y%m%dT%H%M%S')}Z.log"


def run_scenario(*, now_iso: str, archive_iso: str, archive_status: str, label: str) -> str:
    """Runs the real script against one fabricated archive under a fixed,
    injected clock, fully isolated from real machine state. Returns the
    captured out/nightly.log content."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        out_dir = tmp_path / "out"
        runlog_dir = out_dir / "nightly-runs"
        runlog_dir.mkdir(parents=True)
        archive_path = runlog_dir / archive_filename(archive_iso)
        pad = "    " if archive_status == "OK" else "  "
        archive_path.write_text(
            f"{archive_iso}{pad}{archive_status}{pad}exports (6 targets -> OneDrive)\n",
            encoding="utf-8",
        )
        lock_dir = tmp_path / "locks"
        missing_db_env = tmp_path / "no-such-db.env"

        env = {
            "PATH": "/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin",
            # Pinned so "local" time in the script's plain `date` calls equals
            # UTC, matching how the scenario timestamps below are chosen — the
            # guard's actual local/UTC distinction is exercised by the 02:05
            # boundary arithmetic itself, not by this test depending on
            # whatever timezone happens to be set on the machine running it.
            "TZ": "UTC",
            "CARR_NOW": str(epoch(now_iso)),
            "CARR_NIGHTLY_OUT_DIR": str(out_dir),
            "CARR_NIGHTLY_RETRY_DRY_RUN": "1",
            "CARR_LOCK_DIR": str(lock_dir),
            "CARR_ROUTINE_DB_ENV_FILE": str(missing_db_env),
            "HOME": str(tmp_path),  # belt-and-suspenders: no real ~/.config/carr either
        }
        proc = subprocess.run(
            ["zsh", str(SCRIPT)],
            cwd=str(REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        log_path = out_dir / "nightly.log"
        log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        if proc.returncode not in (0, 1) and "DRY-RUN" not in log:
            print(f"  ({label}: unexpected exit {proc.returncode}; stderr: {proc.stderr[:400]})")
        return log


def main() -> int:
    # ── same_day: a genuine retry-needed situation must NOT be blocked ──────
    log = run_scenario(
        now_iso="2026-09-25T15:00:00Z",
        archive_iso="2026-09-25T02:07:03Z",  # completed AFTER today's 02:05 boundary
        archive_status="FAIL",
        label="same_day",
    )
    ok("RETRY tonight's exports step did not report OK" in log,
       "same_day: a completed, failed nightly run past the boundary triggers RETRY, not SKIP")
    ok("no nightly run has completed since the last scheduled 02:05" not in log,
       "same_day: the boundary guard does not fire when the boundary is satisfied")

    # ── after_midnight_before_0205: the boundary must be YESTERDAY's 02:05 ──
    log = run_scenario(
        now_iso="2026-09-25T01:30:00Z",  # after UTC midnight, before today's own 02:05
        archive_iso="2026-09-24T02:07:03Z",  # yesterday's completed, healthy run
        archive_status="OK",
        label="after_midnight_before_0205",
    )
    ok("no nightly run has completed since the last scheduled 02:05" not in log,
       "after_midnight_before_0205: yesterday's completed run satisfies the (correctly "
       "computed, not-yet-rolled-over) boundary — this is the exact rollover edge a "
       "TODAY_UTC-only check used to get wrong")
    ok("tonight's exports step already landed OK" in log,
       "after_midnight_before_0205: resolves against yesterday's real (OK) outcome, "
       "not a spurious skip")

    # ── missed_night: no completion since the boundary — must SKIP, no lock ─
    log = run_scenario(
        now_iso="2026-09-26T15:00:00Z",
        archive_iso="2026-09-24T02:07:03Z",  # two days stale — predates today's 02:05
        archive_status="FAIL",
        label="missed_night",
    )
    ok("no nightly run has completed since the last scheduled 02:05" in log,
       "missed_night: a stale archive (predating the last scheduled 02:05) is refused, "
       "not treated as tonight's outcome")
    # NOTE: every line say() writes is tagged "RETRY" (the log's own line
    # label, like nightly.sh's step names), so "RETRY" itself always appears —
    # the actual decision phrase is what must be absent.
    ok("exports step did not report OK" not in log
       and "no exports outcome line at all" not in log,
       "missed_night: never reaches a RETRY decision")
    ok("LOCKED" not in log and "STALE" not in log and "WEDGED" not in log,
       "missed_night: never touches the shared nightly lock at all — this is the exact "
       "race window (nightly may still be starting or mid-flight) the guard exists to "
       "keep the retry out of")

    # ── static shape assertions ──────────────────────────────────────────────
    src = SCRIPT.read_text(encoding="utf-8")
    ok("CARR_NOW" in src and "carr_now_epoch" in src,
       "the script itself supports the injected-clock test hook")
    ok("CARR_NIGHTLY_OUT_DIR" in src, "output paths are test-isolable")
    ok("CARR_NIGHTLY_RETRY_DRY_RUN" in src, "record() is test-isolable from the real ledger")

    print(f"\nnightly-exports-retry-guard-selftest: {checked - failed}/{checked} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
