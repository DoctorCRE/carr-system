#!/usr/bin/env python3
"""degraded-mode-exercise.py — cut the record layer off and check that nothing
lies.

PROGRAM 4'S RECOVERY HALF asks for "outage/degraded-mode exercises". This is
that exercise for the outage the system is most exposed to: the record layer
unreachable. Neon is a hosted database behind a network, the credential lives in
one file on one Mac, and every observability surface built this year reads from
it. An outage there is not exotic — it is a laptop on hotel wifi.

THE ONE PROPERTY UNDER TEST: WHEN THE LEDGER CANNOT BE READ, NOTHING REPORTS
ALL-CLEAR. Degrading is fine. Refusing is fine. Falling back to a lesser source
is fine when the fallback says so. The single unacceptable outcome is a surface
that prints something reassuring on the strength of data it never got, because
that is worse than the outage: the outage stops when the network comes back, and
a false green stops when somebody eventually notices.

That failure mode has a track record here rather than being hypothetical. The
`smoke` freshness row read green for days while nothing wrote its heartbeat. The
schedule-drift row read a trimmed log and reported a six-hour drift that never
happened, twice, on two different days. Both were surfaces answering confidently
from something they had not actually read.

HOW THE OUTAGE IS SIMULATED. Every component is run with its database
environment pointed at 127.0.0.1 port 1 — a port that refuses instantly rather
than hanging, so the whole exercise finishes in seconds instead of accumulating
connect timeouts. This is a REAL connection attempt to a REAL closed socket, not
a mock: the components take exactly the code path they take on a Mac whose
credential has not loaded or whose network is gone.

WHAT THIS EXERCISE DOES NOT COVER, said plainly rather than left implied:
  * a database that ACCEPTS connections and then answers slowly or wrongly —
    a refused connection is the easy outage, and a degraded-but-answering one
    is harder and is not simulated here;
  * the Cloudflare Worker, which has its own credential path and is not run by
    this exercise;
  * bin/nightly.sh's own ledger-off handling, which is real (it sets LEDGER_OFF
    and says so once) but cannot be exercised without running a whole nightly
    chain against production.

Exit 0 when every component degraded honestly, 1 when any of them lied.
"""
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PY = os.path.join(REPO, ".venv", "bin", "python")
if not os.access(PY, os.X_OK):
    PY = sys.executable

# Phrases that would mean a surface reported all-clear. If any appears while the
# ledger is unreachable, that surface is claiming to know something it could not
# have read.
FALSE_GREEN = [
    "Everything that should be reporting is reporting",
    "no drift — every source agrees",
    "all checks passed",
]

FAILED: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)


def blackout_env() -> dict:
    """The environment of a Mac whose ledger is out of reach.

    Port 1 on loopback refuses immediately. Every DSN variable is overridden
    rather than removed, because an absent variable and a dead endpoint are
    different failures and this exercise is about the second one — a component
    that only handles 'no credential' would pass a test that unset them.
    """
    env = dict(os.environ)
    dead = "postgresql://nobody@127.0.0.1:1/nothing"
    for var in ("DATABASE_URL", "CARR_DB_JOBS_URL", "CARR_DB_EXPORTER_URL",
                "BACKUP_DATABASE_URL", "LIVENESS_DATABASE_URL"):
        env[var] = dead
    return env


def run(args: list[str], timeout: int = 180):
    return subprocess.run(args, capture_output=True, text=True,
                          env=blackout_env(), cwd=REPO, timeout=timeout)


def no_false_green(label: str, out: str) -> None:
    for phrase in FALSE_GREEN:
        if phrase in out:
            check(f"{label}: does not report all-clear while blind", False,
                  f"printed {phrase!r} with the ledger unreachable")
            return
    check(f"{label}: does not report all-clear while blind", True)


def main() -> int:
    print("degraded-mode exercise — the record layer is unreachable, and the "
          "question is whether anything lies about it\n")

    # ── 1. a scheduled job must be completely unaffected ─────────────────────
    # The wrapper is in the path of every launchd job on this Mac. If an
    # unreachable ledger could change a job's behaviour, the observability layer
    # would have become a new way for the business to break.
    print("bin/run-scheduled.sh — the job itself must not notice")
    p = run([os.path.join(REPO, "bin", "run-scheduled.sh"),
             "carr-blackout-probe", "degraded.probe",
             "/bin/sh", "-c", "echo WORK_HAPPENED; exit 0"])
    check("a job still runs and still succeeds", p.returncode == 0,
          f"rc={p.returncode}")
    check("its output still reaches the caller", "WORK_HAPPENED" in p.stdout,
          repr(p.stdout[:120]))
    p = run([os.path.join(REPO, "bin", "run-scheduled.sh"),
             "carr-blackout-probe", "degraded.probe",
             "/bin/sh", "-c", "exit 7"])
    check("a failing job still reports its own exit code, not the recorder's",
          p.returncode == 7, f"rc={p.returncode}")

    # ── 2. the liveness reader must refuse rather than reassure ──────────────
    print("\nops/edge-liveness.py — the off-Mac watchdog")
    p = run([PY, os.path.join(REPO, "ops", "edge-liveness.py")])
    check("refuses with EX_CONFIG (78) instead of guessing", p.returncode == 78,
          f"rc={p.returncode} out={p.stdout[:160]}")
    no_false_green("edge-liveness", p.stdout)
    check("says out loud that it read nothing",
          "nothing read, nothing claimed" in p.stdout, p.stdout[:160])

    # ── 3. the liveness PROBE must not invent three outages ──────────────────
    # The failure this guards is specific: three servers whose results cannot be
    # recorded are an unreachable ledger, and reporting them as three simultaneous
    # outages would alarm every ten minutes on any Mac with a cold credential.
    print("\nbin/probe-keepalive.py — the KeepAlive probe")
    p = run([PY, os.path.join(REPO, "bin", "probe-keepalive.py")])
    check("returns EX_CONFIG (78) rather than reporting outages it cannot record",
          p.returncode == 78, f"rc={p.returncode}")
    check("names the unreachable ledger as the cause",
          "unreachable ledger" in p.stdout, p.stdout[-200:])

    # ── 5. the health check must name its degraded source ────────────────────
    # This is the one with history. The schedule-drift row fell back to a trimmed
    # log twice and reported a drift that never happened, so the fallback is only
    # acceptable while it labels itself.
    print("\ntools/health-check.py — the surface that got this wrong twice")
    p = run([PY, os.path.join(REPO, "tools", "health-check.py")], timeout=600)
    drift = [ln for ln in p.stdout.splitlines() if "nightly-record-layer" in ln]
    check("the schedule-drift row still answers", bool(drift), p.stdout[:200])
    if drift:
        line = drift[0]
        check("...and it does NOT claim the job ledger as its source",
              "job ledger" not in line, line[:220])
        check("...and it labels the fallback as untrustworthy",
              "gets trimmed" in line or "suspicion" in line, line[:220])

    print()
    if FAILED:
        print(f"DEGRADED-MODE EXERCISE FAILED — {len(FAILED)} surface(s) did not "
              f"degrade honestly:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("Every surface degraded honestly: each one refused, or fell back to a "
          "source it named. None reported all-clear on data it could not read.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
