#!/usr/bin/env python3
"""ops/partner-ping-window-selftest.py — the buzz window must never swallow a
weekday buzz, and must never query the database when nobody is awake to act.

WHY THE WINDOW EXISTS (2026-08-18 cloud-usage audit). partner-ping wakes every
120 seconds, and its query was the last thing holding the Neon compute awake
around the clock — autosuspend needs five idle minutes and never got them. The
window skips the QUERY (never the wake, never the job) outside weekdays
07:00–19:00 local, on the same reasoning rule 236ca227 already states for the
partners themselves: weekends are off, and at 3am a buzz reaches nobody.

WHAT THIS FILE IS DEFENDING, and it is the promise rather than the saving:
Joe's 2026-08-03 ruling was that Dell must not "wait until my claude reads the
team board at some random time in the future" — a two-minute interrupt during
the working day. Every weekday-hours check below asserts that promise is
untouched. If a future tightening of the window breaks one of them, it has
taken back something a partner was told he had, and that is exactly the change
that should not pass quietly.

NO CLOCK MOCKING: in_buzz_window() takes the moment as an argument, so these
are real datetimes through the real function — no freezing, no monkeypatching.
The --scheduled flag's own plumbing is exercised as a subprocess against the
real script, with no DATABASE_URL at all: outside the window the script must
exit 0 having never asked for a connection, and inside it must reach the point
where it demands one. That difference is the whole behavior, and it is
observable without a database precisely because the guard runs first.

RUN IT:
    .venv/bin/python ops/partner-ping-window-selftest.py
"""
import importlib.util
import os
import subprocess
import sys
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(REPO, "pipelines", "partner_ping.py")

spec = importlib.util.spec_from_file_location("partner_ping", SCRIPT)
if spec is None or spec.loader is None:
    sys.exit("cannot import pipelines/partner_ping.py")
pp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pp)

FAILED: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(detail="\n        {detail}", failures=FAILED)


def main() -> int:
    print("partner-ping-window-selftest — the workday promise is kept, the "
          "night and weekend query is not made\n")

    # ── the promise: every weekday working hour buzzes ──────────────────────
    # 2026-08-17 is a Monday, so +0..+4 walks Monday through Friday.
    for day in range(5):
        for hour in (7, 9, 12, 15, 18):
            when = datetime(2026, 8, 17 + day, hour, 30)
            ok, why = pp.in_buzz_window(when)
            check(f"weekday {when:%a} {hour:02d}:30 buzzes (Joe's 2-minute "
                  f"promise, 2026-08-03)", ok, why)

    # ── the boundaries, named exactly rather than approximately ─────────────
    check("07:00 sharp is INSIDE — partners routinely start before 8",
          pp.in_buzz_window(datetime(2026, 8, 17, 7, 0))[0])
    check("06:59 is outside",
          not pp.in_buzz_window(datetime(2026, 8, 17, 6, 59))[0])
    check("18:59 is still inside",
          pp.in_buzz_window(datetime(2026, 8, 17, 18, 59))[0])
    check("19:00 sharp is outside — the window is half-open, so 19:xx never "
          "sneaks in", not pp.in_buzz_window(datetime(2026, 8, 17, 19, 0))[0])
    check("3am is outside", not pp.in_buzz_window(datetime(2026, 8, 17, 3, 0))[0])

    # ── the weekend, which is doctrine and not a preference ─────────────────
    for day, name in ((22, "Saturday"), (23, "Sunday")):
        ok, why = pp.in_buzz_window(datetime(2026, 8, day, 12, 0))
        check(f"{name} noon does NOT query (rule 236ca227: weekends are off "
              f"for both partners)", not ok, why)
        check(f"...and says so in ordinary words", "weekend" in why, why)

    # ── the reason is always human-readable, never a bare code ─────────────
    for when in (datetime(2026, 8, 17, 3, 0), datetime(2026, 8, 22, 12, 0)):
        _, why = pp.in_buzz_window(when)
        check(f"a skip at {when:%a %H:%M} explains itself in the log",
              len(why) > 10 and any(c.isalpha() for c in why), why)

    # ── the flag's plumbing, against the real script, with NO database ──────
    # No DATABASE_URL anywhere: outside the window the script must exit 0
    # having never wanted one; inside, it must demand one. Nothing else can
    # produce that pair of outcomes, which is what makes this a real test of
    # the guard's POSITION (before psycopg) and not just its arithmetic.
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["CARR_PARTNER"] = "joe"

    proc = subprocess.run([sys.executable, SCRIPT, "--scheduled"],
                          capture_output=True, text=True, env=env,
                          cwd=REPO, timeout=60)
    now_ok, now_why = pp.in_buzz_window()
    if now_ok:
        check("running --scheduled INSIDE the window reaches the database "
              "demand (proving the guard did not swallow a workday run)",
              proc.returncode != 0 and "DATABASE_URL" in proc.stderr,
              f"rc={proc.returncode} err={proc.stderr[:200]!r} ({now_why})")
    else:
        check("running --scheduled OUTSIDE the window exits 0 without ever "
              "asking for a connection",
              proc.returncode == 0 and "skip:" in proc.stdout
              and "DATABASE_URL" not in proc.stderr,
              f"rc={proc.returncode} out={proc.stdout[:200]!r} ({now_why})")

    # WITHOUT the flag there is no window at all — a human running this by
    # hand at 11pm still gets an answer, which is why the guard is opt-in.
    proc = subprocess.run([sys.executable, SCRIPT],
                          capture_output=True, text=True, env=env,
                          cwd=REPO, timeout=60)
    check("without --scheduled the window never applies — the manual path is "
          "always answered, whatever the hour",
          proc.returncode != 0 and "DATABASE_URL" in proc.stderr,
          f"rc={proc.returncode} err={proc.stderr[:200]!r}")

    # ── the plist really carries the flag ──────────────────────────────────
    # The guard is inert unless the scheduled entry point passes it, so the
    # saving is only real if the deployed argv says so.
    plist = open(os.path.join(REPO, "ops", "launchd",
                              "com.carr.partner-ping.plist"), encoding="utf-8").read()
    check("the launchd plist passes --scheduled, so the window is actually "
          "in force for the scheduled job",
          "<string>--scheduled</string>" in plist)

    print()
    if FAILED:
        print(f"FAILED {len(FAILED)} check(s):")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
