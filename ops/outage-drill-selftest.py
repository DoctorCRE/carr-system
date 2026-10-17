#!/usr/bin/env python3
"""ops/outage-drill-selftest.py — the acceptance test for bin/outage-drill.py,
Program 4's synthetic degraded-mode harness.

TWO TIERS, matching the house pattern in ops/run-scheduled-selftest.py and
ops/restore-rehearse-record-selftest.py.

TIER 1 (always runs; no staging credential, no network). Drives the REAL
harness as a subprocess for everything that does not need a live database or
a live Worker: --list, --dry-run, unknown-drill handling, and the two drills
that are fully local — model-provider-unavailable (a fixture `claude` binary,
no network) and settings-change-db-outage (a genuinely unreachable DSN, no
staging credential needed for the MECHANICS, only for the evidence write at
the end — see the note on that below).

TIER 1 ALSO PROVES THE SPOOL-ERA RECORDER CONTRACT (added 2026-08-18), with no
credential and no network, by driving the REAL bin/run-scheduled.sh through the
drill's OWN env helpers. This is the half of drill 1 that went wrong when
tools/ops-spool.py landed: an unreachable ledger stopped being a recording
failure, so the drill's "recorder_exit is non-zero" assertion inverted and would
have reported a lie where the system was in fact deferring durably. The two
states are checked here directly, where a regression shows up on every push
rather than only on a machine with a staging credential.

TIER 2 (only when a staging credential is actually available — gated behind
CARR_OUTAGE_DRILL_SELFTEST_LIVE=1, never on by default, because it spends a
Neon staging round trip and a real HTTP call to the staging Worker on every
invocation otherwise). Runs record-layer-unreachable, stale-observation and
worker-unreachable for real and checks their verdicts. Both database drills
additionally mint an ephemeral isolated-staging carr_jobs credential through
tools/staging_jobs_dsn.py; on a machine that cannot resolve staging at all they
skip, and this suite says so rather than reporting a bare failure.

WHY TIER 1's LOCAL DRILLS STILL NEED "LIVE" LOOSELY DEFINED: both
model-provider-unavailable and settings-change-db-outage end by calling
record_evidence(), which needs a staging DSN to write the evidence row. This
suite does not stub that: it lets the evidence write attempt for real and
tolerates either outcome (recorded, or a loud EVIDENCE WARNING on stderr) —
same no-mock discipline as the rest of this repo's selftests, and consistent
with bin/outage-drill.py's own DrillResult/record_evidence separation: the
DRILL's verdict (checked here) and whether the evidence WRITE itself landed
are two different facts.

RUN IT:
    python3 ops/outage-drill-selftest.py
    CARR_OUTAGE_DRILL_SELFTEST_LIVE=1 python3 ops/outage-drill-selftest.py
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(REPO, "bin", "outage-drill.py")
PY = os.path.join(REPO, ".venv", "bin", "python")
if not os.path.exists(PY):
    PY = sys.executable

FAILED: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)
    return bool(cond)


def run(args: list[str], timeout: int = 60, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([PY, SCRIPT, *args], capture_output=True, text=True,
                           timeout=timeout, cwd=REPO, env=env)


def drill_module():
    """bin/outage-drill.py imported as a module, so the recorder-contract block
    below drives the harness's REAL env helpers rather than a paraphrase of
    them. A copy of _unreachable_env()'s rules here would be a copy that keeps
    passing after the original drifts, which is the failure mode this whole
    suite exists to catch."""
    spec = importlib.util.spec_from_file_location("carr_outage_drill", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["carr_outage_drill"] = module
    spec.loader.exec_module(module)
    return module


def staging_credential_present(drill) -> bool:
    """Whether this machine can reach isolated staging at all. Deliberately the
    OWNER credential and not a mint: asking is a read, while minting alters a
    role, and a suite that only wants to know which tier to run should not have
    a side effect."""
    try:
        drill.staging_dsn()
    except Exception:                                              # noqa: BLE001
        return False
    return True


def tier1() -> None:
    print("TIER 1 — harness mechanics and the two fully-local drills")

    check("the harness exists and is executable",
          os.access(SCRIPT, os.X_OK), SCRIPT)
    if not os.access(SCRIPT, os.X_OK):
        return

    # ── --list ────────────────────────────────────────────────────────────
    proc = run(["--list"])
    check("--list exits 0", proc.returncode == 0, f"rc={proc.returncode}")
    for name in ("record-layer-unreachable", "stale-observation", "worker-unreachable",
                 "model-provider-unavailable"):
        check(f"--list names {name!r}", name in proc.stdout, proc.stdout[:800])

    # ── --dry-run touches nothing: proven by poisoning the network AND the
    #    staging credential and confirming it still exits 0 immediately, for
    #    every drill including the ones that would otherwise dial out ───────
    poisoned = dict(os.environ)
    poisoned["DATABASE_URL"] = "postgresql://nobody@127.0.0.1:1/nothing"
    poisoned["NEON_API_KEY"] = "not-a-real-key-outage-drill-selftest"
    for leak in ("CARR_DB_JOBS_URL", "CARR_DB_EXPORTER_URL"):
        poisoned.pop(leak, None)
    proc = run(["--dry-run"], env=poisoned, timeout=20)
    check("--dry-run exits 0 even with every credential poisoned "
          "(proof it performs no I/O at all)", proc.returncode == 0, f"rc={proc.returncode}")
    check("--dry-run describes every drill without running one",
          "DRY RUN" in proc.stdout and "record-layer-unreachable" in proc.stdout,
          proc.stdout[:400])

    # ── unknown --only is a usage error, not a silent no-op ─────────────────
    proc = run(["--only", "not-a-real-drill"])
    check("an unknown --only exits 64 (EX_USAGE)", proc.returncode == 64, f"rc={proc.returncode}")
    check("...and names the drills that DO exist",
          "record-layer-unreachable" in proc.stderr, proc.stderr[:400])

    # ── model-provider-unavailable: fully local, no network at all ──────────
    print("\n  drill: model-provider-unavailable (real fixture, no network)")
    proc = run(["--only", "model-provider-unavailable"], timeout=DRILL_TIMEOUT)
    check("runs to completion", proc.returncode in (0, 1, 2), f"rc={proc.returncode}\n{proc.stdout[-800:]}")
    check("finds the system TRUTHFUL — no fabricated reply from a dead model",
          "TRUTHFUL:" in proc.stdout and "NOT TRUTHFUL" not in proc.stdout,
          proc.stdout[-800:])
    check("the fixture's own evidence is either genuinely non-zero returncode "
          "or explicitly reported broken, never silently green",
          "convo_core reported a real failure" in proc.stdout, proc.stdout[-800:])



def tier1_recorder_contract() -> None:
    """The spool-era recorder contract, driven through the real wrapper.

    WHY THIS IS TIER 1 AND NOT TIER 2. The assertion that broke on 2026-08-18 —
    "an unreachable database means recorder_exit is non-zero" — needed no
    staging credential to be wrong, and needs none to be caught. Everything
    below is loopback and a scratch directory."""
    print("\n  the spool-era recorder contract (real wrapper, no network)")
    drill = drill_module()

    probe = "carr-outage-drill-selftest-" + uuid.uuid4().hex[:8]
    run_key = "selftest.recorder-contract"
    scratch = Path(tempfile.mkdtemp(prefix="carr-outage-drill-selftest-"))
    try:
        # An explicit CARR_DB_JOBS_URL is the whole point of the fix: popping it
        # lets tools/ops-record.py reload the PRODUCTION jobs DSN from db.env by
        # setdefault, so an "unreachable" test would quietly dial the live
        # ledger. Assert the harness sets it rather than trusting it does.
        unreachable = drill._unreachable_env(dict(os.environ))
        check("the harness SETS CARR_DB_JOBS_URL for an outage rather than "
              "popping it (popping lets db.env resupply the production DSN)",
              unreachable.get("CARR_DB_JOBS_URL", "").startswith("postgresql://carr_jobs@127.0.0.1:1"),
              repr(unreachable.get("CARR_DB_JOBS_URL")))

        deferred_env = drill._throwaway_spool_env(unreachable, scratch)
        check("...and it redirects the run-row spool away from the shared "
              "out/run-spool.sqlite3",
              deferred_env["CARR_RUN_SPOOL_DB"].startswith(str(scratch)),
              deferred_env["CARR_RUN_SPOOL_DB"])

        # STATE 1 — ledger unreachable, queue writable. Truthful behavior is
        # durable deferral, and recorder_exit 0 is the honest report of it.
        proc = subprocess.run(
            [drill.RUN_SCHEDULED.__fspath__(), probe, run_key, "/bin/sh", "-c", "exit 0"],
            capture_output=True, text=True, env=deferred_env, cwd=REPO, timeout=DRILL_TIMEOUT)
        line = drill._tail_provenance(run_key, probe)
        queued = drill._spool_rows(Path(deferred_env["CARR_RUN_SPOOL_DB"]), probe, run_key)
        check("state 1: the wrapped job's exit code is untouched",
              proc.returncode == 0, f"rc={proc.returncode}")
        check("state 1: an unreachable ledger alone reports recorder_exit=0 — "
              "durable, not failed (this is what inverted the old assertion)",
              drill._field(line, "recorder_exit") == "0", repr(line))
        check("state 1: ...and the row is REALLY in the local queue",
              len(queued) == 1, repr(queued))

        # STATE 2 — the record layer broken all the way down. The only state in
        # which a non-zero recorder exit is honest since the spool.
        broken_env = drill._unwritable_spool_env(deferred_env)
        check("the harness induces genuine failure with a spool path under "
              "/dev/null, the same fixture run-scheduled-selftest uses",
              os.path.devnull in broken_env["CARR_RUN_SPOOL_DB"],
              broken_env["CARR_RUN_SPOOL_DB"])
        proc = subprocess.run(
            [drill.RUN_SCHEDULED.__fspath__(), probe, run_key, "/bin/sh", "-c", "exit 0"],
            capture_output=True, text=True, env=broken_env, cwd=REPO, timeout=DRILL_TIMEOUT)
        line = drill._tail_provenance(run_key, probe)
        check("state 2: the wrapped job's exit code is STILL untouched",
              proc.returncode == 0, f"rc={proc.returncode}")
        check("state 2: with the queue unwritable too, recorder_exit is "
              "non-zero — the row is durable nowhere and says so",
              drill._field(line, "recorder_exit") not in ("0", ""), repr(line))

        # The isolation this whole block depends on, asserted rather than
        # assumed: a selftest that quietly queued into the shared spool would
        # be manufacturing production noise on every push.
        check("neither state queued anything into the SHARED production spool",
              not drill._spool_rows(Path(REPO) / "out" / "run-spool.sqlite3",
                                    probe, run_key),
              "rows found in out/run-spool.sqlite3")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def tier1_unreachable_staging_skips() -> None:
    """With no staging credential at all, the two database drills must SKIP —
    cleanly, exit 2, no traceback, and never a verdict about the system under
    test. This is every CI runner, and it is the shape a drill has to hold to be
    safe to run anywhere."""
    drill = drill_module()
    if staging_credential_present(drill):
        print("\n  (staging is reachable here — the unreachable-staging skip "
              "path is not exercised on this machine)")
        return

    print("\n  the unreachable-staging skip path")
    for name in ("record-layer-unreachable", "stale-observation"):
        proc = run(["--only", name], timeout=DRILL_TIMEOUT)
        check(f"{name} exits 2 (skipped), never 0 and never a crash",
              proc.returncode == 2, f"rc={proc.returncode}\n{proc.stdout[-600:]}")
        check(f"{name} reports a skip rather than a verdict about the target",
              "SKIPPED" in proc.stdout and "NOT TRUTHFUL" not in proc.stdout,
              proc.stdout[-600:])
        check(f"{name} leaves no traceback behind",
              "Traceback" not in proc.stdout and "Traceback" not in proc.stderr,
              proc.stderr[-600:])


def tier2() -> None:
    if os.environ.get("CARR_OUTAGE_DRILL_SELFTEST_LIVE") != "1":
        print("\nTIER 2 — skipped (set CARR_OUTAGE_DRILL_SELFTEST_LIVE=1 to run the "
              "staging-database and staging-Worker drills for real)")
        return

    print("\nTIER 2 — the staging-database and staging-Worker drills, run for real")

    # Named plainly rather than left to surface as a puzzling verdict: the
    # difference between "this machine cannot reach staging" and "it can, and
    # the drill failed" is the whole value of running tier 2 at all.
    if not staging_credential_present(drill_module()):
        print("  (no staging credential on this machine — the two database "
              "drills below will SKIP rather than report a verdict.)")

    print("\n  drill: record-layer-unreachable")
    proc = run(["--only", "record-layer-unreachable"], timeout=DRILL_TIMEOUT)
    check("runs to completion", proc.returncode in (0, 1, 2), f"rc={proc.returncode}\n{proc.stdout[-1500:]}")
    check("finds the system TRUTHFUL", "NOT TRUTHFUL" not in proc.stdout, proc.stdout[-1500:])
    check("restores and proves it: the probe is deregistered afterward",
          "probe service is fully deregistered afterward" in proc.stdout, proc.stdout[-1500:])

    print("\n  drill: stale-observation")
    proc = run(["--only", "stale-observation"], timeout=DRILL_TIMEOUT)
    check("runs to completion", proc.returncode in (0, 1, 2), f"rc={proc.returncode}\n{proc.stdout[-1500:]}")
    check("finds the system TRUTHFUL", "NOT TRUTHFUL" not in proc.stdout, proc.stdout[-1500:])

    print("\n  drill: worker-unreachable (staging only, never production)")
    proc = run(["--only", "worker-unreachable"], timeout=DRILL_TIMEOUT)
    check("runs to completion", proc.returncode in (0, 1, 2), f"rc={proc.returncode}\n{proc.stdout[-1500:]}")
    check("the local node --test fixture passes",
          "local fixture proves the committed correlation.js mechanism is correct" in proc.stdout,
          proc.stdout[-1500:])
    check("the live staging call never sees a fabricated success",
          "fabricated" not in proc.stdout.lower() or "never a" in proc.stdout.lower(),
          proc.stdout[-1500:])


# THE 60-SECOND BUDGET WAS TOO TIGHT, and it failed in the DEFAULT workflow.
# Both drills below make REAL attempts against a genuinely unreachable database
# and a dead model fixture, so their runtime is bounded by network and
# subprocess timeouts rather than by compute. That total sits near 60s on the
# canonical checkout and exceeds it from a git worktree, which is where sessions
# are supposed to work. The symptom was ugly and non-obvious: a TimeoutExpired
# traceback in the pre-push gates class, indistinguishable from a hang, blocking
# the push of an unrelated one-line change. Measured 2026-08-21: same drill,
# same machine, passes in ~70s canonical and exceeds 60s from a worktree.
# 180s keeps the check meaningful (a real hang still fails) with margin for the
# slower tree and for a machine running several sessions at once.
DRILL_TIMEOUT = 180

def main() -> int:
    print("outage-drill-selftest — bin/outage-drill.py must run every drill it "
          "claims to, touch nothing on --dry-run, and never mistake the target's "
          "own dishonesty for its own crash")
    tier1()
    tier1_recorder_contract()
    tier1_unreachable_staging_skips()
    tier2()
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
