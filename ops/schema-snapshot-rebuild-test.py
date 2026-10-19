#!/usr/bin/env python3
"""Restore a refreshed snapshot after a migration changes a carried seed."""
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("shadow", ROOT / "ops/migration-shadow.py")
assert spec is not None and spec.loader is not None
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
        seeds = shadow.seeds
        seed_repo = fixture.root / "declarations"
        (seed_repo / "ops/config").mkdir(parents=True)
        declaration = {
            "table": "public.snapshot_fixture_seed", "migration": "9999_snapshot_fixture.sql",
            "where": "seed_key='reviewed'", "order_by": "seed_key", "exact_rows": 1,
            "columns": ["seed_key", "value"],
        }
        config = {"carried_subset": {"snapshot_fixture_seed": "synthetic reviewed fixture only"},
                  "renders": [declaration]}
        (seed_repo / "ops/config/snapshot-seed-coverage.json").write_text(json.dumps(config))
        ddl = "create table public.snapshot_fixture_seed(seed_key text primary key, value text not null)"
        sql(dsn, ddl)
        sql(rebuilt, ddl)
        sql(dsn, "insert into public.snapshot_fixture_seed values ('reviewed', E'quote'' newline\\nbackslash\\\\'), ('runtime','excluded synthetic row')")
        source_psql = [str(bindir / "psql"), dsn]
        rebuilt_psql = [str(bindir / "psql"), rebuilt]
        assert seeds.selected_rows(seed_repo, source_psql) == []
        print("PASS: a future seed remains pending outside the source ledger")
        for database in (dsn, rebuilt):
            sql(database, "insert into public.schema_migrations(filename,sha256) values ('9999_snapshot_fixture.sql',repeat('0',64))")
        for statement in seeds.render(seeds.selected_rows(seed_repo, source_psql)):
            sql(rebuilt, statement)
        seeds.compare(seed_repo, source_psql, rebuilt_psql)
        assert sql(rebuilt, "select count(*) from public.snapshot_fixture_seed") == "1"
        print("PASS: one declaration restores a different seed table and excludes unreviewed rows")
        sql(rebuilt, "update public.snapshot_fixture_seed set value='corrupt fixture'")
        try:
            seeds.compare(seed_repo, source_psql, rebuilt_psql)
        except ValueError as exc:
            assert "snapshot_fixture_seed" in str(exc) and "9999_snapshot_fixture.sql" in str(exc)
        else:
            raise AssertionError("candidate verification accepted changed seed content")
        print("PASS: rebuild verification names a changed seed and its migration")


if __name__ == "__main__":
    main()
