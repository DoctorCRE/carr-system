#!/usr/bin/env python3
# ci: db-gate
# ci: db-gate-before-isolated
# doctrine: engineering-workflow-sop
"""Census acceptance against an owned disposable PostgreSQL fixture only."""
import importlib.util
import json
from pathlib import Path
import socket
import shutil
import subprocess
import sys

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    census = load("census_pg", ROOT / "ops/table-usage-census.py")
    local = load("census_local_pg", ROOT / "ops/local-pg-ci.py")
    bins = local.find_postgres_binaries()
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node is required for the census CLI fixture")
    env = {"PATH": f"{Path(node).parent}:/usr/bin:/bin", "LC_ALL": "C"}
    with postgres_fixture_group(), DisposablePostgres("carr-census-", bins.pg_ctl, env) as fixture:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        data = fixture.root / "data"
        commands = [
            [bins.initdb, "-D", data, "-U", "carr_ci", "--auth=trust", "--no-locale"],
            [bins.pg_ctl, "-D", data, "-l", fixture.root / "postgres.log", "-o",
             f"-h 127.0.0.1 -p {port} -k {fixture.root}", "-w", "start"],
        ]
        for command in commands:
            fixture.run(command, capture_output=True, check=True, timeout=60)
        dsn = f"host=127.0.0.1 port={port} user=carr_ci dbname=postgres"
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("create schema ops")
            conn.execute("select pg_stat_reset()")
            conn.execute("create schema excluded")
            conn.execute("create table public.census_busy(id integer)")
            conn.execute("create table ops.census_empty(id integer)")
            conn.execute("create table public.census_unknown(id integer)")
            conn.execute("create extension pg_trgm")
            conn.execute("create table public.census_extension_owned(id integer)")
            conn.execute("alter extension pg_trgm add table public.census_extension_owned")
            conn.execute("create table excluded.census_hidden(id integer)")
            conn.execute("create table ops.census_partitioned(id integer) partition by range(id)")
            conn.execute("create table ops.census_child partition of ops.census_partitioned for values from (0) to (10)")
            conn.execute("insert into public.census_busy values (1), (2), (3)")
            conn.execute("update public.census_busy set id=4 where id=3")
            conn.execute("delete from public.census_busy where id=4")
            conn.execute("vacuum analyze public.census_busy")
            conn.execute("vacuum analyze ops.census_empty")
            conn.execute("create function ops.census_read() returns bigint language sql as 'select count(*) from public.census_busy'")
            conn.execute("create function ops.census_dynamic(text) returns void language plpgsql as $$begin execute $1; end$$")
            conn.execute("select pg_stat_force_next_flush()")
        with psycopg.connect(dsn) as conn:
            snapshot = census.collect_catalog(conn)
        rows = {f"{row['schema']}.{row['name']}": row for row in snapshot["tables"]}
        assert "public.census_extension_owned" in snapshot["backup_tables"]
        assert "public.census_extension_owned" in rows, rows.keys()
        assert len(rows) == 6, rows.keys()
        busy = rows["public.census_busy"]
        assert (busy["inserts"], busy["updates"], busy["deletes"]) == (3, 1, 1), busy
        assert busy["estimated_rows"] == 2, busy
        assert busy["total_bytes"] > 0 and busy["last_vacuum"] and busy["last_analyze"], busy
        assert busy["stats_reset"] is not None and snapshot["transaction_read_only"] == "on"
        assert rows["public.census_unknown"]["estimated_rows"] == -1
        assert set(snapshot["backup_tables"]) == set(rows)
        refs, unresolved = census.scan_references(ROOT, snapshot["tables"], [], [], snapshot["functions"])
        assert any(item.get("function") == "ops.census_read()" for item in refs["public.census_busy"])
        assert any(item.get("function") == "ops.census_dynamic(text)" for item in unresolved)
        report = census.build_report(snapshot["tables"], references=refs,
                                     unresolved=unresolved, backup_tables=set(snapshot["backup_tables"]))
        assert report["counts"] == {"active": 1, "quiet": 5, "empty-and-unreferenced": 0}, report["counts"]
        artifact = fixture.root / "out/census.json"
        census.write_report(report, artifact)
        assert json.loads(artifact.read_text())["counts"] == report["counts"]
        cli = subprocess.run([sys.executable, ROOT / "ops/table-usage-census.py"],
                             cwd=ROOT, env={**env, "DATABASE_URL_READER": dsn},
                             capture_output=True, text=True, timeout=60)
        assert cli.returncode == 0, cli.stderr
        generated = json.loads((ROOT / "out/table-usage-census.json").read_text())
        assert generated["counts"]["active"] == 1 and len(generated["tables"]) == 6
        assert generated["provenance"]["transaction_read_only"] == "on"
        assert generated["provenance"]["inventory_entries"] > 0
        print(census.render_summary(report))
        print("PASS: 17 PostgreSQL fixture checks; owned cluster teardown follows")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"fixture command failed with exit {exc.returncode}") from None
