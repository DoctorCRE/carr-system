#!/usr/bin/env python3
"""Restore a refreshed snapshot after a migration changes a carried seed."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from git_env import fixture_env

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
        source = fixture.root / "source"
        shadow.run(["git", "clone", "--quiet", "--shared", ROOT, source], fixture_env(env),
                   "clone snapshot source", log)
        shutil.copyfile(ROOT / "bin/schema-snapshot.sh", source / "bin/schema-snapshot.sh")
        def apply_pending(database):
            shadow.run([sys.executable, source / "tools/migrate.py", "--apply", "--yes"],
                       {**env, "DATABASE_URL": database}, "apply pending migrations", log)

        apply_pending(dsn)
        baseline = fixture.root / "baseline.sql"
        run([source / "bin/schema-snapshot.sh", "--from-disposable-local", dsn,
             "--output-candidate", baseline], "refresh pending-migration baseline")
        admin = dsn.rsplit("/", 1)[0] + "/postgres"
        sql(admin, "alter database carr_ci rename to seeded_baseline")
        run([bindir / "createdb", "-h", "127.0.0.1", "-p", str(port), "-U", "carr_ci", "carr_ci"],
            "createdb first independent regeneration")
        run([bindir / "psql", dsn, "-Xq", "-v", "ON_ERROR_STOP=1", "-f", baseline],
            "restore pending-migration baseline")
        migration = source / "migrations/9999_snapshot_regeneration_probe.sql"
        migration.write_text("create table public.snapshot_regeneration_probe(id integer primary key);\n")
        checksum = hashlib.sha256(migration.read_bytes()).hexdigest()

        def apply_probe(database):
            run([bindir / "psql", database, "-Xq", "-v", "ON_ERROR_STOP=1", "-f", migration],
                "apply new migration")
            sql(database, "insert into public.schema_migrations(filename,sha256) values "
                f"('{migration.name}','{checksum}')")

        apply_probe(dsn)
        # A later seeding migration may update an existing reviewed contract.
        # Its applied ledger prevents replay; the refreshed snapshot owns it.
        sql(dsn, "update ops.job_definition set retry_policy=jsonb_set(retry_policy,'{base_seconds}','31') where key='engineering-slice' and version=1")
        candidate = fixture.root / "candidate.sql"
        run([source / "bin/schema-snapshot.sh", "--from-disposable-local", dsn,
             "--output-candidate", candidate], "refresh snapshot")
        run([bindir / "createdb", "-h", "127.0.0.1", "-p", str(port), "-U", "carr_ci", "reapplied"],
            "createdb independent regeneration")
        reapplied = dsn.rsplit("/", 1)[0] + "/reapplied"
        run([bindir / "psql", reapplied, "-Xq", "-v", "ON_ERROR_STOP=1", "-f", baseline],
            "reload pending-migration baseline")
        apply_probe(reapplied)
        sql(reapplied, "update ops.job_definition set retry_policy=jsonb_set(retry_policy,'{base_seconds}','31') where key='engineering-slice' and version=1")
        applied_time = f"select applied_at from public.schema_migrations where filename='{migration.name}'"
        assert sql(dsn, applied_time) != sql(reapplied, applied_time), "fixture must reapply at different times"
        print("PASS: independent rebuilds apply the new migration at different times")
        admin = dsn.rsplit("/", 1)[0] + "/postgres"
        sql(admin, "alter database carr_ci rename to first_build")
        sql(admin, "alter database reapplied rename to carr_ci")
        second = fixture.root / "second.sql"
        run([source / "bin/schema-snapshot.sh", "--from-disposable-local", dsn,
             "--output-candidate", second], "repeat snapshot")
        assert candidate.read_bytes() == second.read_bytes(), "independent regenerations must be byte-identical"
        print("PASS: independent regenerations with a new migration are byte-identical")
        def ledger(snapshot):
            head = b"COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;\n"
            rows = snapshot.read_bytes().split(head)[1].split(b"\\.\n", 1)[0]
            return dict((parts[0], parts[1:]) for row in rows.split(b"\n") if row
                        for parts in [row.split(b"\t")])

        original = ledger(ROOT / "db/schema.sql")
        generated = ledger(candidate)
        assert all(generated[name] == row for name, row in original.items())
        print("PASS: recorded migration checksums and applied times survive regeneration")
        assert generated[migration.name.encode()] == [checksum.encode(), b"1970-01-01 00:00:00+00"]
        print("PASS: a new migration has a deterministic snapshot apply time")
        run([bindir / "createdb", "-h", "127.0.0.1", "-p", str(port), "-U", "carr_ci", "rebuilt"], "createdb rebuild")
        rebuilt = dsn.rsplit("/", 1)[0] + "/rebuilt"
        run([bindir / "psql", rebuilt, "-Xq", "-v", "ON_ERROR_STOP=1", "-f", candidate], "restore refreshed snapshot")
        run([bindir / "psql", rebuilt, "-Xq", "-v", "ON_ERROR_STOP=1", "-f",
             ROOT / "ops/release-schema-provenance-canary.sql"], "release schema provenance canary")
        print("PASS: release schema provenance accepts the regenerated ledger and rejects mismatches")
        actual = sql(rebuilt, "select retry_policy->>'base_seconds' from ops.job_definition where key='engineering-slice' and version=1")
        assert actual == "31", f"refreshed snapshot lost applied seed update: expected 31, rebuilt {actual}"
        print("PASS: refreshed snapshot preserves a later migration's seed update")
        siep_rows = ("select md5(string_agg(to_jsonb(w)::text, ',' order by w.ref)) from ops.work_request w"
                     " where w.program_key='carr-system-integrity-elimination-v1'")
        assert sql(dsn, siep_rows) == sql(rebuilt, siep_rows)
        print("PASS: refreshed snapshot preserves the SIEP program work requests")
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
        sql(dsn, "alter table public.snapshot_fixture_seed add column new_contract text default 'synthetic'")
        try:
            seeds.selected_rows(seed_repo, source_psql)
        except ValueError as exc:
            assert "unreviewed source columns: new_contract" in str(exc)
        else:
            raise AssertionError("a later column silently escaped seed verification")
        print("PASS: a newly seeded column requires a reviewed carry or omission")


if __name__ == "__main__":
    main()
