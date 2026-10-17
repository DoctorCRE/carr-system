#!/usr/bin/env python3
"""ops/restore-rehearse-record-selftest.py — the acceptance test for
bin/restore-rehearse.sh's evidence recording (Program 4).

THE GAP THIS CLOSES. Doctrine's exit bar for the weekly restore drill is
"recurring isolated restore from actual artifact, timed, integrity checked,
application verified, and RECORDED." Until now the script proved restorability
into a throwaway Neon branch and printed PASS/FAIL — and that PASS/FAIL died
with the terminal the moment the scheduled task's session ended. Nothing made
it durable. ops/config/services.json already carried a restore-rehearse-weekly
row and it read "last seen never" — registered, never observed.

WHAT record_rehearsal() DOES, restated here because this suite exists to prove
it: called from bin/restore-rehearse.sh's own EXIT trap (cleanup(), which runs
on every exit path — a die() in preflight, a failed branch or restore, a failed
verification, or the ordinary PASS/FAIL at the bottom), it writes ONE ops.run
row through tools/ops-record.py — service restore-rehearse-weekly, key
restore.rehearsal, state succeeded/failed, a failure_class bucket chosen from
two checkpoints the script already passes through (never from the exit code,
which is always a bare 1 here), and a --detail line carrying the dump's own
date/size and the measured decrypt+restore+verify duration — so the row answers
"how long does restore take and from which artifact" without archaeology.
--preflight and --verify-only both return before anything is created and must
record NOTHING, because neither one rehearses anything.

NO MOCK, SAME DISCIPLINE AS ops/run-scheduled-selftest.py. This suite drives
the REAL bin/restore-rehearse.sh as a real subprocess and reads its REAL
stdout/stderr — never a stand-in script, never a monkeypatched ops-record.py.
What it cannot afford is a real Neon branch and a real production read on every
push, so the script carries ONE test-only hook,
CARR_RESTORE_REHEARSE_SELFTEST=1 (never set by a scheduled task, launchd, or a
real "Run Now"), which short-circuits AFTER argument parsing and RECORD_
REHEARSAL's decision but BEFORE phase 0 touches anything — fixture env vars
then stand in for a dump having been selected, production's baseline having
been read, and phase 4's verification having produced a summary, and the
script's OWN cleanup()/record_rehearsal()/parse_rehearse_summary() run for
real from there. This is the same shape as bin/refresh-rules.sh's
CARR_REFRESH_RULES_EXPORT_CMD hook: the expensive, network/DB-touching middle
is substituted; the code under test is not.

THE PROVENANCE LINE IS THE TESTED SURFACE. record_rehearsal() prints
  evidence: state=... exit_code=... [failure_class=...] started_at=...
            detail: ...
BEFORE it ever attempts the database write — independent of whether the write
lands — precisely so this suite (and a human reading a scheduled task's
transcript) can see exactly what was derived from a fixture without needing a
reachable database. This suite asserts against THAT line, the same way
ops/run-scheduled-selftest.py asserts against out/run-scheduled.log's line.

TIER 1 (always runs; no DB, no credential; a fixture CARR_DB_JOBS_URL is
deliberately unreachable so the recorder's own write always fails LOUD on stderr, proving
that failure never touches the rehearsal's own exit code). Covers: the three
failure_class buckets, the detail line's shape (dump, bytes, date, pct,
tables, rows, duration), state=succeeded on exit 0, --preflight and
--verify-only recording nothing even when the fixture would otherwise produce
a full row, and the exit code passing through untouched in every case.

TIER 2 (a genuine restore rehearsal, run only when explicitly requested) is
deliberately NOT part of this file — it costs a real Neon branch and several
minutes, which is wrong for something CI runs on every push. That live proof
belongs to a human running `./run.sh restore-rehearse` once and reading the
row back with `tools/ops-record.py trace <correlation>`.

RUN IT:
    python3 ops/restore-rehearse-record-selftest.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import fnmatch
from migration_source import migration_names, read_migration

import os
import re
import subprocess
import tempfile
from typing import Optional

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from lib.loadpy import load_module_from_path  # noqa: E402

SCRIPT = os.path.join(REPO, "bin", "restore-rehearse.sh")
OPS_RECORD = load_module_from_path("restore_rehearse_ops_record",
                                    os.path.join(REPO, "tools", "ops-record.py"))
DEAD_DSN = "postgresql://carr_jobs:probe@127.0.0.1:1/nonexistent"
ROUTINE_ENV_TMP = tempfile.TemporaryDirectory()
ROUTINE_ENV_FILE = Path(ROUTINE_ENV_TMP.name) / "db.env"
ROUTINE_ENV_FILE.write_text(
    "CARR_DB_JOBS_URL='postgresql://carr_jobs@127.0.0.1:1/nothing'\n",
    encoding="utf-8",
)
ROUTINE_ENV_FILE.chmod(0o600)

FAILED: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)
    return bool(cond)


def unreachable_env(extra: Optional[dict] = None) -> dict:
    """The environment a Mac has before its DB credential loads — port 1 on
    loopback refuses instantly rather than hanging.

    TWO BELTS, because the fixture file alone was not one. The restore wrapper
    loads the 0600 no-eval fixture above through its production credential
    adapter (bin/routine-credential-env.sh, which honours
    CARR_ROUTINE_DB_ENV_FILE), and that much was always true. But
    tools/ops-record.py does not read that variable: its own _load_db_env()
    reads ~/.config/carr/db.env directly and fills any name still unset by
    setdefault. Every path that reached the recorder WITHOUT going through the
    adapter therefore fell through to the developer's real credential —
    exactly what this docstring used to promise could not happen.

    Not hypothetical. Measured 2026-08-19, this suite and
    ops/key-recovery-test-selftest.py had written 206 fabricated rows into
    PRODUCTION's ops.run against restore-rehearse-weekly, 66 of them carrying
    this file's own source_ref. Unlike the tier-1 suites fixed on 2026-08-18,
    whose carr-selftest-* keys production has never registered, this service IS
    registered, so nothing refused them: its entire production health signal
    was fixture exhaust, and an incident reading "restore.rehearsal failed" has
    been open since 2026-08-17 against a rehearsal that never ran. Same shape
    as open loop #450's radar-weekly finding. Nothing is deleted here; the
    purge is that loop's ruling to make.

    So: SET every name ops-record.py's own credential_names() lists to a dead
    port, never delete one, and keep the fixture file for the adapter path. The
    username stays carr_jobs so routine mode's credential-shape check passes
    and what fails is the CONNECTION, not the credential's spelling.
    """
    env = dict(os.environ)
    for name in OPS_RECORD.credential_names():
        env[name] = DEAD_DSN
    env["CARR_ROUTINE_DB_ENV_FILE"] = str(ROUTINE_ENV_FILE)
    env.pop("PGSERVICE", None)
    if extra:
        env.update(extra)
    return env


def drive(fixture: dict, args: Optional[list] = None) -> subprocess.CompletedProcess:
    """Run the REAL script with the REAL test hook. `fixture` supplies the
    CARR_RESTORE_REHEARSE_SELFTEST_* env vars (values coerced to str); `args`
    are extra CLI flags such as --preflight or --verify-only."""
    env = unreachable_env({"CARR_RESTORE_REHEARSE_SELFTEST": "1"})
    for k, v in fixture.items():
        env[f"CARR_RESTORE_REHEARSE_SELFTEST_{k}"] = str(v)
    return subprocess.run(
        [SCRIPT, *(args or [])], capture_output=True, text=True,
        timeout=60, env=env, cwd=REPO)


def evidence_lines(proc: subprocess.CompletedProcess) -> str:
    """The two-line provenance block record_rehearsal() prints on stdout
    before it ever attempts the database write — the tested surface."""
    lines = [ln for ln in proc.stdout.splitlines()
              if ln.strip().startswith("evidence:") or ln.strip().startswith("detail:")]
    return "\n".join(lines)


def field(text: str, name: str) -> str:
    m = re.search(rf"\b{re.escape(name)}=(\S*)", text)
    return m.group(1) if m else ""


def tier1() -> None:
    print("\nTIER 1 — record_rehearsal() derives the right row from a fixture, "
          "and a database that cannot be reached never touches the rehearsal's "
          "own result")

    check("the script exists and is executable",
          os.access(SCRIPT, os.X_OK), SCRIPT)
    if not os.access(SCRIPT, os.X_OK):
        return
    source = Path(SCRIPT).read_text(encoding="utf-8")
    restore_filter = re.search(r"^RESTORE_FILTER='([^']+)'$", source, re.MULTILINE)
    check("restore portability filter removes only the pre-created public schema",
          restore_filter is not None
          and "CREATE SCHEMA public;" in restore_filter.group(1)
          and restore_filter.group(1).count("CREATE SCHEMA") == 1,
          "the fresh target already has public; ops and every other schema must still restore")
    extension_names = set(re.findall(
        r"create extension if not exists ([a-z0-9_]+)", source.lower()
    ))
    migration_extensions = set()
    for migration in ((Path(REPO) / "migrations") / name for name in migration_names((Path(REPO) / "migrations").parent) if fnmatch.fnmatchcase(name, "*.sql")):
        migration_extensions.update(re.findall(
            r"create extension if not exists ([a-z0-9_]+)",
            read_migration(migration.parent.parent, migration.name).lower(),
        ))
    check("restore bootstraps every extension prerequisite declared by migrations",
          extension_names == migration_extensions == {"pg_trgm", "pgcrypto"},
          f"restore={sorted(extension_names)} migrations={sorted(migration_extensions)}")

    # ── a good run: state=succeeded, no invented failure_class ──────────────
    proc = drive({
        "EXIT": 0, "DUMP": "/tmp/carr-20260101.sql.age", "BYTES": 123456,
        "STAMP": "20260101", "PROD_TABLES": 67, "PROD_ROWS": 50000,
        "SUMMARY": "pct=99.8 rest_rows=49900 rest_tables=67 prod_rows=50000 prod_tables=67",
    })
    ev = evidence_lines(proc)
    check("a fixture success exits 0 (the recorder's own failure below never "
          "changes it)", proc.returncode == 0, f"got {proc.returncode}")
    check("state=succeeded", field(ev, "state") == "succeeded", ev)
    check("no failure_class is invented on a success",
          "failure_class=" not in ev, ev)
    check("exit_code is recorded as 0", field(ev, "exit_code") == "0", ev)
    check("detail carries the dump's basename",
          "dump=carr-20260101.sql.age" in ev, ev)
    check("detail carries the dump's byte count", "123456B" in ev, ev)
    check("detail carries the dump's own date", "taken 20260101" in ev, ev)
    check("detail carries the restored percentage from REHEARSE_SUMMARY, not "
          "a second computation", "restored 99.8%" in ev, ev)
    check("detail carries table and row counts",
          "67/67 tables" in ev and "49900/50000 rows" in ev, ev)
    check("detail names the measured phases", "decrypt+restore+verify took" in ev, ev)
    check("the recorder was genuinely unreachable and said so loudly on stderr",
          "EVIDENCE WARNING" in proc.stderr and "NOT recorded" in proc.stderr,
          proc.stderr[:400])
    check("...and the rehearsal's own exit code is STILL 0 despite that",
          proc.returncode == 0, f"got {proc.returncode}")

    # ── bucket 1: preflight_failed — died before a dump or a production count ──
    proc = drive({"EXIT": 1, "DIE_REASON": "no age identity present"})
    ev = evidence_lines(proc)
    check("preflight_failed: no dump, no production baseline -> this bucket",
          field(ev, "failure_class") == "preflight_failed", ev)
    check("preflight_failed: state=failed", field(ev, "state") == "failed", ev)
    check("preflight_failed: detail carries the die() reason verbatim",
          "no age identity present" in ev, ev)
    check("preflight_failed: the child's own exit code (1) still passes through",
          proc.returncode == 1, f"got {proc.returncode}")

    # ── bucket 2: restore_failed — production read OK, restore itself died ──
    proc = drive({
        "EXIT": 1, "DUMP": "/tmp/carr-20260201.sql.age", "BYTES": 999,
        "PROD_TABLES": 67, "DIE_REASON": "could not create the rehearsal branch",
    })
    ev = evidence_lines(proc)
    check("restore_failed: a production baseline but no REHEARSE_SUMMARY -> "
          "this bucket", field(ev, "failure_class") == "restore_failed", ev)
    check("restore_failed: detail carries the dump AND the die() reason",
          "dump=carr-20260201.sql.age" in ev
          and "could not create the rehearsal branch" in ev, ev)

    # ── bucket 3: verification_failed — the restore ran, the compare failed ──
    proc = drive({
        "EXIT": 1, "DUMP": "/tmp/carr-20260301.sql.age", "BYTES": 999,
        "PROD_TABLES": 67, "PROD_ROWS": 50000,
        "SUMMARY": "pct=40.0 rest_rows=20000 rest_tables=30 prod_rows=50000 prod_tables=67",
    })
    ev = evidence_lines(proc)
    check("verification_failed: a REHEARSE_SUMMARY present alongside a nonzero "
          "exit -> this bucket",
          field(ev, "failure_class") == "verification_failed", ev)
    check("verification_failed: detail carries the real (bad) percentage, not "
          "a rounded-up or hidden one", "restored 40.0%" in ev, ev)
    check("verification_failed: detail carries the restored/production table "
          "and row counts", "30/67 tables" in ev and "20000/50000 rows" in ev, ev)

    # ── --preflight and --verify-only rehearse nothing, so they RECORD ──────
    #    nothing — proven by combining each flag with a fixture that would
    #    otherwise produce a full succeeded row, and asserting NEITHER the
    #    provenance line NOR the failure warning ever appears.
    full_fixture = {
        "EXIT": 0, "DUMP": "/tmp/carr-20260401.sql.age", "BYTES": 1,
        "PROD_TABLES": 1, "PROD_ROWS": 1,
        "SUMMARY": "pct=100.0 rest_rows=1 rest_tables=1 prod_rows=1 prod_tables=1",
    }
    for flag in ("--preflight", "--verify-only"):
        proc = drive(full_fixture, args=[flag])
        check(f"{flag} + a would-record fixture: exits 0 (the flag's own exit, "
              "not a side effect of the fixture)",
              proc.returncode == 0, f"got {proc.returncode}")
        check(f"{flag} + a would-record fixture: prints NO evidence line — "
              f"{flag} rehearses nothing, so nothing is recorded",
              "evidence:" not in proc.stdout, proc.stdout[-400:])
        check(f"{flag} + a would-record fixture: prints NO evidence warning "
              "either — record_rehearsal() returned before ever trying",
              "EVIDENCE WARNING" not in proc.stderr, proc.stderr[:400])

    # ── the fixture hook itself is inert without the enabling var ───────────
    # A real invocation never sets CARR_RESTORE_REHEARSE_SELFTEST, so a stray
    # CARR_RESTORE_REHEARSE_SELFTEST_EXIT alone (e.g. a leaked env var) must
    # never short-circuit the real script. Proven by omitting the enabling
    # var and confirming the run proceeds into real phase-0 preflight instead
    # of exiting immediately on the fixture's EXIT value.
    env = unreachable_env({"CARR_RESTORE_REHEARSE_SELFTEST_EXIT": "0"})
    env.pop("CARR_RESTORE_REHEARSE_SELFTEST", None)
    proc = subprocess.run([SCRIPT, "--preflight"], capture_output=True,
                           text=True, timeout=60, env=env, cwd=REPO)
    check("a fixture var without the enabling CARR_RESTORE_REHEARSE_SELFTEST "
          "is inert — the real script still runs its own phase 0",
          "phase 0: preflight" in proc.stdout, proc.stdout[:400])


def main() -> int:
    print("restore-rehearse-record-selftest — bin/restore-rehearse.sh must "
          "record one honest ops.run row per real rehearsal, and --preflight/"
          "--verify-only must record none")
    tier1()
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
