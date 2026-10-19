#!/usr/bin/env python3
"""Restore a refreshed snapshot after a migration changes a carried seed."""
import importlib.util
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("shadow", ROOT / "ops/migration-shadow.py")
shadow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shadow)


def main():
    bindir = shadow.find_pg18(None)
    assert bindir, "snapshot rebuild proof requires PostgreSQL 18"
    env = shadow.local_pg.scrub_cloud_environment(os.environ)
    env["LC_ALL"] = "C"
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    with shadow.local_pg.DisposablePostgres("carr-snapshot-rebuild.", bindir / "pg_ctl", env) as fixture:
        data, port = fixture.root / "data", shadow.free_port(55800)
        log = fixture.root / "proof.log"
        fixture.run([bindir / "initdb", "-D", data, "-U", "carr_ci", "--auth=trust",
                     "--encoding=UTF8", "--no-locale"], check=True, capture_output=True)
        fixture.run([bindir / "pg_ctl", "-D", data, "-l", fixture.root / "postgres.log",
                     "-o", f"-h 127.0.0.1 -p {port} -k {fixture.root}", "-w", "start"],
                    check=True, capture_output=True)
        dsn = f"postgres://carr_ci@127.0.0.1:{port}/carr_ci"

        def run(command, label):
            return shadow.run(command, env, label, log)

        def sql(database, statement):
            return run([bindir / "psql", database, "-XAtq", "-v", "ON_ERROR_STOP=1", "-c", statement],
                       "snapshot seed assertion").strip()

        run([bindir / "createdb", "-h", "127.0.0.1", "-p", str(port), "-U", "carr_ci", "carr_ci"], "createdb")
        sql(dsn, "create role neondb_owner")
        run([bindir / "psql", dsn, "-Xq", "-v", "ON_ERROR_STOP=1", "-f", ROOT / "db/schema.sql"], "load committed snapshot")
        # A later seeding migration may update an existing reviewed contract.
        # Its applied ledger prevents replay; the refreshed snapshot owns it.
        sql(dsn, "update ops.job_definition set retry_policy=jsonb_set(retry_policy,'{base_seconds}','31') where key='engineering-slice' and version=1")
        candidate = fixture.root / "candidate.sql"
        run([ROOT / "bin/schema-snapshot.sh", "--from-disposable-local", dsn,
             "--output-candidate", candidate], "refresh snapshot")
        run([bindir / "createdb", "-h", "127.0.0.1", "-p", str(port), "-U", "carr_ci", "rebuilt"], "createdb rebuild")
        rebuilt = dsn.rsplit("/", 1)[0] + "/rebuilt"
        run([bindir / "psql", rebuilt, "-Xq", "-v", "ON_ERROR_STOP=1", "-f", candidate], "restore refreshed snapshot")
        actual = sql(rebuilt, "select retry_policy->>'base_seconds' from ops.job_definition where key='engineering-slice' and version=1")
        assert actual == "31", f"refreshed snapshot lost applied seed update: expected 31, rebuilt {actual}"
        print("PASS: refreshed snapshot preserves a later migration's seed update")
        assert sql(dsn, "select ops.siep_manifest_digest()") == sql(rebuilt, "select ops.siep_manifest_digest()")
        print("PASS: refreshed snapshot preserves the sealed SIEP manifest")
        run([bindir / "psql", rebuilt, "-Xq", "-v", "ON_ERROR_STOP=1", "-f",
             ROOT / "pipelines/staging-isolation-proof.sql"], "rebuild business-data isolation")
        print("PASS: refreshed snapshot excludes business data")


if __name__ == "__main__":
    main()
