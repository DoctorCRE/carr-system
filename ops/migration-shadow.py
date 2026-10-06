#!/usr/bin/env python3
"""migration-shadow.py — run pending migrations against production's structure, then
prove db/schema.sql is exactly what they produce.

THE RUN, all on a throwaway loopback cluster this script creates and removes:

  1. initdb a PostgreSQL 18 cluster (production's major: a 17 server drops the
     named NOT NULL constraints production carries, so its dump can never
     match production's own).
  2. Load the BASE branch's db/schema.sql — production's committed structure,
     grants and applied-migration ledger. No business data exists anywhere in
     this run and no provider credential is read.
  3. tools/migrate.py --apply: every migration not in that ledger applies, in
     order, exactly as the release pipeline would apply it to production.
  4. bin/schema-snapshot.sh --from-disposable-local dumps the result.
  5. Compare with the committed db/schema.sql. Normalize ledger apply times,
     the admitted work-request sequence, control verification/update times and
     SCAC seal/registration times. Structure, seed content, ledger membership
     and checksums must match.

A mismatch means the reference schema is stale or was not produced from the
migrations. --write replaces db/schema.sql with the candidate (the regeneration
path); --artifact-dir keeps a copy for CI to upload.

Exit 0 match (or written) · 1 stale/different · 2 a step failed · 3 PostgreSQL 18
is not available here (ops/ci.sh reports a SKIP; --strict turns that into a fail).

  ops/migration-shadow.py                       # base = merge-base with origin/$GITHUB_BASE_REF or origin/main
  ops/migration-shadow.py --write               # regenerate db/schema.sql
  ops/migration-shadow.py --base <ref> [--pg-bin-dir DIR] [--artifact-dir DIR]
"""
# doctrine: scripted-release-pipeline
from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType

REPO = pathlib.Path(__file__).resolve().parent.parent
SNAPSHOT = "db/schema.sql"
PG_MAJOR = "18"
PG_BIN_CANDIDATES = (
    f"/usr/lib/postgresql/{PG_MAJOR}/bin",
    f"/opt/homebrew/opt/postgresql@{PG_MAJOR}/bin",
    f"/usr/local/opt/postgresql@{PG_MAJOR}/bin",
)
STEP_TIMEOUT = 1200


def _load(name: str, path: pathlib.Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load("migration_safety_gate", REPO / "ops" / "migration-safety-gate.py")
local_pg = _load("local_pg_ci", REPO / "ops" / "local-pg-ci.py")
# The one operational value ops/schema-snapshot-currentness.py already admits.
WORK_REQUEST_SEQUENCE_VALUE = re.compile(
    _load("schema_snapshot_currentness", REPO / "ops" / "schema-snapshot-currentness.py")
    .WORK_REQUEST_SEQUENCE_VALUE.pattern.decode(), re.MULTILINE)


# ── comparison ─────────────────────────────────────────────────────────────

SEED_TIME_FIELDS = {
    "ops.scac_mutation_registry_version": "sealed_at",
    "ops.scac_mutation_registry_entry": "registered_at",
}
SQL_JSON = re.compile(r"'((?:[^']|'')*)'::jsonb")


def normalize_seed_times(snapshot: str) -> str:
    lines = []
    for line in snapshot.splitlines():
        table = next((t for t in SEED_TIME_FIELDS if line.startswith("insert into " + t + " ")), None)
        if table:
            def normalize(match):
                value = json.loads(match[1].replace("''", "'"))
                rows = value if isinstance(value, list) else [value]
                field = SEED_TIME_FIELDS[table]
                for row in rows:
                    if isinstance(row, dict) and isinstance(row.get(field), str):
                        row[field] = "<operational-time>"
                return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'::jsonb"
            line = SQL_JSON.sub(normalize, line)
        elif line.startswith("insert into ops.enforcement_control_catalog "):
            # Only the two trailing operational fields in this admitted seed.
            if re.search(r",verified_at,updated_at\) values ", line):
                line = re.sub(r"'[^']+'::timestamptz(?=,'[^']+'::timestamptz\)|\))",
                              "'<operational-time>'::timestamptz", line)
        lines.append(line)
    return "\n".join(lines) + "\n"


def _structure(snapshot: str) -> str | None:
    """The snapshot with its ledger rows removed and the admitted sequence value
    normalised. None when there is no complete ledger section."""
    m = gate.LEDGER_HEAD.search(snapshot)
    if not m:
        return None
    end = snapshot.find("\n\\.\n", m.end() - 1)
    if end < 0:
        return None
    text = snapshot[:m.end()] + "\n<ledger rows>" + snapshot[end:]
    text = normalize_seed_times(text)
    return WORK_REQUEST_SEQUENCE_VALUE.sub(
        "select pg_catalog.setval('ops.work_request_ref_seq', <current>, true);", text)


def differences(committed: str, candidate: str, limit: int = 80) -> list[str]:
    """Human-readable differences between the committed snapshot and the shadow
    candidate; empty when they describe the same schema and ledger."""
    out: list[str] = []
    rows_c, rows_n = gate.ledger_rows(committed), gate.ledger_rows(candidate)
    struct_c, struct_n = _structure(committed), _structure(candidate)
    if rows_c is None or struct_c is None:
        return [f"committed {SNAPSHOT} has no complete schema_migrations ledger"]
    if rows_n is None or struct_n is None:
        return ["shadow candidate has no complete schema_migrations ledger"]
    for name in sorted(set(rows_n) - set(rows_c)):
        out.append(f"ledger: {name} applied in the shadow but is not in the committed {SNAPSHOT}")
    for name in sorted(set(rows_c) - set(rows_n)):
        out.append(f"ledger: {name} is in the committed {SNAPSHOT} but did not apply in the shadow")
    for name in sorted(set(rows_c) & set(rows_n)):
        if rows_c[name] != rows_n[name]:
            out.append(f"ledger: {name} checksum differs")
    if struct_c != struct_n:
        diff = list(difflib.unified_diff(struct_c.splitlines(), struct_n.splitlines(),
                                         f"committed {SNAPSHOT}", "shadow candidate",
                                         n=1, lineterm=""))
        out.append(f"structure: {sum(1 for d in diff if d[:1] in '+-') - 2} changed line(s)")
        out.extend(diff[:limit])
    return out


def newly_applied(before: str, after: str) -> list[str]:
    rows_b, rows_a = gate.ledger_rows(before) or {}, gate.ledger_rows(after) or {}
    return sorted(set(rows_a) - set(rows_b))


# ── the run ────────────────────────────────────────────────────────────────

class StepFailed(RuntimeError):
    pass


class ShutdownFailed(StepFailed):
    pass


def find_pg18(explicit: str | None) -> pathlib.Path | None:
    dirs = [explicit] if explicit else (
        [os.environ["CARR_SHADOW_PG_BIN_DIR"]] if os.environ.get("CARR_SHADOW_PG_BIN_DIR")
        else [*PG_BIN_CANDIDATES, *os.environ.get("PATH", "").split(os.pathsep)])
    for d in dirs:
        path = pathlib.Path(d).resolve()
        initdb = path / "initdb"
        if not all((path / b).is_file() for b in ("initdb", "postgres", "pg_ctl", "psql", "createdb")):
            continue
        version = subprocess.run([initdb, "--version"], capture_output=True, text=True).stdout
        if f"PostgreSQL) {PG_MAJOR}." in version:
            return path
    return None


def default_base() -> str:
    ref = (os.environ.get("GITHUB_BASE_REF") or "").strip()
    target = f"origin/{ref}" if ref else "origin/main"
    return subprocess.run(["git", "merge-base", "HEAD", target], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def free_port(start: int = 55700) -> int:
    for port in range(start, start + 200):
        if local_pg.port_is_available(port):
            return port
    raise StepFailed("no free loopback port for the shadow cluster")


def run(cmd: list, env: dict[str, str], label: str, log: pathlib.Path) -> str:
    with log.open("a") as fh:
        fh.write(f"$ {label}\n")
        result = subprocess.run([str(c) for c in cmd], cwd=REPO, env=env, capture_output=True,
                                text=True, timeout=STEP_TIMEOUT)
        fh.write(result.stdout + result.stderr)
    if result.returncode:
        tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-25:])
        raise StepFailed(f"{label} failed:\n{tail}")
    return result.stdout


def shadow(base: str, bindir: pathlib.Path, work: pathlib.Path) -> tuple[str, str, list[str]]:
    """Return (base snapshot, candidate snapshot, migrations applied)."""
    log = work / "shadow.log"
    env = local_pg.scrub_cloud_environment(os.environ)
    env["LC_ALL"] = "C"
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    base_snapshot = subprocess.run(["git", "show", f"{base}:{SNAPSHOT}"], cwd=REPO,
                                   capture_output=True, text=True, check=True).stdout
    (work / "base.sql").write_text(base_snapshot)
    data, port = work / "data", free_port()
    dsn = f"postgres://carr_ci@127.0.0.1:{port}/carr_ci"
    python = REPO / ".venv/bin/python"
    python = python if python.is_file() else pathlib.Path(sys.executable)

    run([bindir / "initdb", "-D", data, "-U", "carr_ci", "--auth=trust", "--encoding=UTF8",
         "--no-locale"], env, "initdb", log)
    try:
        run([bindir / "pg_ctl", "-D", data, "-l", work / "postgres.log",
             "-o", f"-h 127.0.0.1 -p {port} -k {work}", "-w", "start"], env, "pg_ctl start", log)
        run([bindir / "createdb", "-h", "127.0.0.1", "-p", str(port), "-U", "carr_ci", "carr_ci"],
            env, "createdb", log)
        run([bindir / "psql", dsn, "-v", "ON_ERROR_STOP=1", "-qc", "create role neondb_owner;"],
            env, "create neondb_owner", log)
        run([bindir / "psql", dsn, "-v", "ON_ERROR_STOP=1", "-q", "-f", work / "base.sql"],
            {**env, "PGOPTIONS": "--client-min-messages=warning"}, f"load {base[:12]}:{SNAPSHOT}", log)
        run([python, REPO / "tools/migrate.py", "--apply", "--yes"], {**env, "DATABASE_URL": dsn},
            "tools/migrate.py --apply", log)
        candidate = work / "candidate.sql"
        run([REPO / "bin/schema-snapshot.sh", "--from-disposable-local", dsn,
             "--output-candidate", candidate], env, "bin/schema-snapshot.sh", log)
    finally:
        try:
            stopped = subprocess.run([bindir / "pg_ctl", "-D", data, "-m", "fast", "-w", "stop"],
                                     env=env, capture_output=True, timeout=60)
            status = subprocess.run([bindir / "pg_ctl", "-D", data, "status"],
                                    env=env, capture_output=True, timeout=30)
        except (subprocess.TimeoutExpired, OSError):
            raise ShutdownFailed(f"shutdown unavailable or timed out; cluster retained at {work}") from None
        if stopped.returncode or status.returncode != 3:
            raise ShutdownFailed(f"shutdown not verified; cluster retained at {work}")
    text = candidate.read_text()
    return base_snapshot, text, newly_applied(base_snapshot, text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default=None, help="ref whose db/schema.sql is the starting point")
    parser.add_argument("--pg-bin-dir", default=None)
    parser.add_argument("--write", action="store_true", help=f"replace {SNAPSHOT} with the candidate")
    parser.add_argument("--artifact-dir", default=None, help="copy the candidate here")
    args = parser.parse_args(argv)

    bindir = find_pg18(args.pg_bin_dir)
    if bindir is None:
        print(f"migration-shadow: UNAVAILABLE — no PostgreSQL {PG_MAJOR} server binaries "
              f"(initdb, pg_ctl, psql, createdb). Production runs {PG_MAJOR}; a different "
              f"server major cannot reproduce its snapshot. Set CARR_SHADOW_PG_BIN_DIR or "
              f"--pg-bin-dir.", file=sys.stderr)
        return 3
    try:
        base = args.base or default_base()
    except subprocess.CalledProcessError as exc:
        print(f"migration-shadow: cannot resolve the base: {exc.stderr.strip()}", file=sys.stderr)
        return 2

    work = pathlib.Path(tempfile.mkdtemp(prefix="carr-migration-shadow."))
    retain = False
    try:
        try:
            _, candidate, applied = shadow(base, bindir, work)
        except (StepFailed, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            retain = isinstance(exc, ShutdownFailed)
            print(f"migration-shadow: FAILED against {base[:12]}'s production structure — {exc}",
                  file=sys.stderr)
            return 2
        print(f"migration-shadow: {len(applied)} pending migration(s) applied cleanly on "
              f"PostgreSQL {PG_MAJOR} over {base[:12]}:{SNAPSHOT}"
              + (": " + ", ".join(applied) if applied else ""))
        if args.artifact_dir:
            dest = pathlib.Path(args.artifact_dir)
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(work / "candidate.sql", dest / "schema.sql")
        if args.write:
            (REPO / SNAPSHOT).write_text(candidate)
            print(f"migration-shadow: wrote {SNAPSHOT} from the shadow candidate")
            return 0
        diffs = differences((REPO / SNAPSHOT).read_text(), candidate)
    finally:
        if not retain:
            shutil.rmtree(work)
    if not diffs:
        print(f"migration-shadow: OK — committed {SNAPSHOT} is exactly what the migrations produce")
        return 0
    print(f"migration-shadow: {SNAPSHOT} is STALE — it is not what base + migrations produce:",
          file=sys.stderr)
    for line in diffs:
        print(f"  {line}", file=sys.stderr)
    print(f"  Fix: ops/migration-shadow.py --write (PostgreSQL {PG_MAJOR}), or commit the "
          f"schema.sql artifact this CI job uploaded.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
