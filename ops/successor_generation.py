"""Render a successor from current-main SQL and a disposable catalog readback."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile

from git_env import scrubbed_env
from successor_ownership import validate_outputs, JSON_ARTIFACTS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import postgres_fixture_group, DisposablePostgres
from scac_mutation_db_inventory import project, summarize, project_role_authority


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    payload = value if isinstance(value, str) else canonical(value)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("successor SQL template drifted at " + before[:80])
    return text.replace(before, after)


def jsonb_key_order(value):
    """Match PostgreSQL jsonb's length-then-byte key order for catalog readback."""
    if isinstance(value, dict):
        return {key: jsonb_key_order(value[key]) for key in sorted(value, key=lambda key: (len(key.encode()), key.encode()))}
    if isinstance(value, list):
        return [jsonb_key_order(item) for item in value]
    return value


def render_sql(template, predecessor, rows, baseline, entry_set, dependencies):
    """Extend history while replacing only the current, generated frontier."""
    previous = predecessor["number"]
    current = previous + 1
    old_version = f"scac-mutation-registry.v{previous}"
    new_version = f"scac-mutation-registry.v{current}"
    old_catalog = json.dumps(jsonb_key_order(predecessor["catalog"]), separators=(",", ":"))
    new_catalog = json.dumps(baseline, separators=(",", ":"))
    new_digest = digest({"schema_version": new_version, "rows": rows, "db_catalog_baseline": baseline})
    count = len(rows) + sum(baseline[key]["count"] for key in ("secdef_execute", "relation_dml", "column_dml"))
    start = template.index("\ndrop trigger scac_mutation_registry_version_sealed")
    sql = template[start + 1:]
    sql = sql.replace(f"_v{previous}", f"_v{current}")
    sql = sql.replace(old_version, new_version)
    sql = sql.replace(f"scac-db-catalog-projection.v{previous}", f"scac-db-catalog-projection.v{current}")
    for marker in ("current", "live_at_seal"):
        sql = sql.replace(f"v{previous - 1}_{marker}", f"v{previous}_{marker}")
    sql = sql.replace(f"snapshot_v{previous - 1}", f"snapshot_v{previous}")
    sql = sql.replace(predecessor["digest"], new_digest)
    sql = sql.replace(predecessor["entry_set"], entry_set)
    shifted_catalog = {**predecessor['catalog'], 'projection_version': f'scac-db-catalog-projection.v{current}'}
    def catalog_literal(match):
        try:
            value = json.loads(match[1])
        except ValueError:
            return match[0]
        return "'" + new_catalog + "'" if value == shifted_catalog else match[0]
    sql = re.sub(r"'(\{[^'\n]*\})'", catalog_literal, sql)
    sql = sql.replace(f"<>{predecessor['entry_count']}", f"<>{count}")
    sql = sql.replace(f"<>{predecessor['source_count']}", f"<>{len(rows)}")
    sql = sql.replace(f",{predecessor['entry_count']},{predecessor['source_count']},", f",{count},{len(rows)},")
    comparisons = {}
    for key in ("secdef_execute", "relation_dml", "column_dml", "role_authority"):
        old, new = predecessor["catalog"][key], baseline[key]
        operator = '=' if key == 'role_authority' else '<>'
        join = 'and' if key == 'role_authority' else 'or'
        before = f"observed_count{operator}{old['count']} {join} observed_digest{operator}'{old['digest']}'"
        after = f"observed_count{operator}{new['count']} {join} observed_digest{operator}'{new['digest']}'"
        if sql.count(before) != 1:
            raise ValueError(f"successor SQL category comparison drifted: {key}")
        comparisons[before] = after
    sql = re.sub('|'.join(re.escape(value) for value in comparisons),
                 lambda match: comparisons[match[0]], sql)
    old, new = predecessor["catalog"]["runtime_dml_grants"], baseline["runtime_dml_grants"]
    sql = replace_once(sql,
        f"(grant_snapshot->>'entry_count')::integer={old['count']} and\n    grant_snapshot->>'grant_digest'='{old['digest']}'",
        f"(grant_snapshot->>'entry_count')::integer={new['count']} and\n    grant_snapshot->>'grant_digest'='{new['digest']}'")
    versions = ",".join(f"'scac-mutation-registry.v{i}'" for i in range(1, previous + 1))
    corrupt = versions.rsplit(",", 1)[0] + f",'{new_version}'"
    if sql.count(corrupt) != 2:
        raise ValueError("successor SQL template version lists drifted")
    sql = sql.replace(corrupt, versions + f",'{new_version}'")
    for before, after in (
        (f"  (registry_version='{new_version}' and registry_digest='{new_digest}'));", f"  (registry_version='{old_version}' and registry_digest='{predecessor['digest']}') or\n  (registry_version='{new_version}' and registry_digest='{new_digest}'));"),
        (f"    when '{new_version}' then '{new_digest}' end;", f"    when '{old_version}' then '{predecessor['digest']}'\n    when '{new_version}' then '{new_digest}' end;"),
        (f"    when '{new_version}' then '{new_catalog}'::jsonb end;", f"    when '{old_version}' then '{old_catalog}'::jsonb\n    when '{new_version}' then '{new_catalog}'::jsonb end;"),
        (f"ops.scac_mutation_registry_v{current}_seal_available()) then", f"ops.scac_mutation_registry_v{previous}_seal_available() and ops.scac_mutation_registry_v{current}_seal_available()) then"),
        (f"         or (r.registry_version='{new_version}' and r.registry_digest='{new_digest}'))", f"         or (r.registry_version='{old_version}' and r.registry_digest='{predecessor['digest']}')\n         or (r.registry_version='{new_version}' and r.registry_digest='{new_digest}'))"),
        (f"     or not ops.scac_mutation_registry_v{current}_seal_available()", f"     or not ops.scac_mutation_registry_v{previous}_seal_available()\n     or not ops.scac_mutation_registry_v{current}_seal_available()"),
    ):
        sql = replace_once(sql, before, after)
    seed = [{**row, "entry_digest": digest(row)} for row in rows]
    sql, n = re.subn(r"(\$[a-zA-Z0-9_]+_source\$)\[.*?\]\1", lambda m: m[1] + json.dumps(seed, separators=(",", ":"), ensure_ascii=False) + m[1], sql, count=1)
    if n != 1:
        raise ValueError("successor SQL template source seed drifted")
    checks = "\n".join(
        f"  if not exists(select 1 from public.schema_migrations where filename='{path['filename'] if isinstance(path, dict) else path.name}' and sha256='{hashlib.sha256(path['sql'].encode() if isinstance(path, dict) else path.read_bytes()).hexdigest()}') then raise exception 'Successor dependency drifted: {path['filename'] if isinstance(path, dict) else path.name}'; end if;"
        for path in dependencies)
    preflight = ("do $rehome_preflight$\nbegin\n" + checks +
        f"\n  if not ops.scac_mutation_registry_v{previous}_seal_available() then raise exception 'Successor predecessor seal unavailable'; end if;\nend $rehome_preflight$;\n\n")
    header = (
        "-- GENERATED by ops/successor_generation.py. Review; never hand-edit.\n"
        "-- rollback: forward-only — sealed registry history is immutable; recover with a new successor.\n"
        f"-- lock-review: registry versions contain one row per seal ({current} after this successor); policy epochs are append-only authority metadata. CHECK scans preserve pinned history without rewriting business rows; the sanctioned runner bounds lock acquisition and statement duration.\n"
    )
    return header + preflight + sql


def probe_sql(sql):
    """Install and seed inside a savepoint; measure, then roll it back."""
    sql = sql[sql.index("drop trigger scac_mutation_registry_version_sealed"):]
    sql, n = re.subn(r"(?ms)^do (\$[a-zA-Z0-9_]*\$).*?^end \1;\n", "", sql)
    if n != 3:
        raise ValueError("successor SQL template verification blocks drifted")
    return sql


@contextmanager
def disposable_database(repo):
    spec = importlib.util.spec_from_file_location("successor_local_pg", ROOT / "ops/local-pg-ci.py")
    local = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = local
    spec.loader.exec_module(local)
    binaries = local.find_postgres_binaries()
    env = local.scrub_cloud_environment(os.environ)
    env["PATH"] = f"{binaries.psql.parent}{os.pathsep}{env.get('PATH', '')}"
    env["LC_ALL"] = "C"
    fixture = DisposablePostgres("successor-postgres-", binaries.pg_ctl, env)
    root = fixture.root
    data = root / "data"
    with postgres_fixture_group():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        def run(args, child_env=None):
            result = fixture.run(args, cwd=repo, env=child_env or env, capture_output=True, timeout=600)
            if result.returncode:
                (root / "failure.log").write_bytes(result.stderr + result.stdout)
                raise ValueError(f"disposable database {Path(str(args[0])).name} failed; diagnostics retained at {root / 'failure.log'}")
        try:
            run([binaries.initdb, "-D", data, "-U", "carr_ci", "--auth=trust", "--encoding=UTF8", "--no-locale"])
            run([binaries.pg_ctl, "-D", data, "-l", root / "postgres.log", "-o", f"-h 127.0.0.1 -k {root} -p {port}", "-w", "start"])
            run([binaries.createdb, "-h", "127.0.0.1", "-p", port, "-U", "carr_ci", "carr_ci"])
            dsn = f"postgres://carr_ci@127.0.0.1:{port}/carr_ci"
            run([binaries.psql, dsn, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c", "create role neondb_owner"])
            yield dsn, env, run
        finally:
            fixture.close()


def predecessor_rows(repo, version):
    result = subprocess.run(['node', '--input-type=module', '-e',
        "import {frozenInventory} from './ops/scac-mutation-inventory.mjs';process.stdout.write(JSON.stringify(frozenInventory(process.argv[1])));", version],
        cwd=repo, env=scrubbed_env(), capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError('canonical frozen predecessor inventory refused generation')
    return json.loads(result.stdout)


def regenerate(repo, plan, domain_paths, successor_path, predecessor_path):
    import psycopg
    validate_outputs(repo, [*JSON_ARTIFACTS, str(successor_path.relative_to(repo)),
        'mcp-server/src/scac-mutation-registry.current.generated.js', '.git/successor-runtime.json'])
    with disposable_database(repo) as (dsn, env, run):
        snapshot = subprocess.check_output(["git", "show", f"{plan['base']}:db/schema.sql"], cwd=repo, env=scrubbed_env(), timeout=120)
        with tempfile.NamedTemporaryFile(prefix="successor-base-", suffix=".sql") as baseline:
            baseline.write(snapshot)
            baseline.flush()
            run(["psql", dsn, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", baseline.name])
        last_main = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", plan["base"], "--", "migrations"], cwd=repo, env=scrubbed_env()).decode().splitlines()
        last_main = sorted(Path(p).name for p in last_main if p.endswith(".sql"))[-1]
        python = ROOT / ".venv/bin/python"
        run([python if python.is_file() else sys.executable, repo / "tools/migrate.py", "--apply", "--yes", "--through", last_main], dict(env, DATABASE_URL=dsn))
        result = subprocess.run(["node", "--input-type=module", "-e", "import {fullInventory} from './ops/scac-mutation-inventory.mjs'; import {TOOLS} from './mcp-server/src/tools.js'; process.stdout.write(JSON.stringify(fullInventory(TOOLS)));"], cwd=repo, env=env, capture_output=True, timeout=120)
        if result.returncode:
            log = repo / '.git/source-inventory-error.log'
            log.write_bytes(result.stderr)
            raise ValueError(f"live source inventory refused generation; diagnostics retained at {log}")
        rows = json.loads(result.stdout)
        with psycopg.connect(dsn) as conn:
            for path in domain_paths:
                conn.execute(path.read_text())
                conn.execute("insert into public.schema_migrations(filename,sha256) values (%s,%s)", (path.name, hashlib.sha256(path.read_bytes()).hexdigest()))
            version = f"scac-mutation-registry.v{plan['registry_predecessor']}"
            old = conn.execute("select registry_digest,entry_count,source_entry_count,catalog_projection,entry_set_digest from ops.scac_mutation_registry_version where registry_version=%s", (version,)).fetchone()
            if old is None:
                raise ValueError("current-main predecessor absent from disposable replay")
            predecessor = dict(number=plan["registry_predecessor"], digest=old[0], entry_count=old[1], source_count=old[2], catalog=old[3], entry_set=old[4])
            baseline = {**old[3], "projection_version": f"scac-db-catalog-projection.v{plan['registry_successor']}"}
            template = predecessor_path.read_text()
            dependencies = [predecessor_path, *domain_paths]
            conn.execute("savepoint successor_probe")
            probe = render_sql(template, predecessor, rows, baseline, old[4], dependencies)
            conn.execute(probe_sql(probe))
            projected = project(conn.cursor())
            baseline.update(summarize(projected)["categories"])
            role = project_role_authority(conn.cursor())
            baseline["role_authority"] = {k: role[k] for k in ("count", "digest")}
            grants = conn.execute("select ops.scac_runtime_dml_grant_snapshot()").fetchone()[0]
            baseline["runtime_dml_grants"] = {"count": grants["entry_count"], "digest": grants["grant_digest"]}
            new_version = f"scac-mutation-registry.v{plan['registry_successor']}"
            entry_set = conn.execute("select 'sha256:'||encode(public.digest(convert_to(string_agg(entry_digest,',' order by ingress_key collate \"C\"),'UTF8'),'sha256'),'hex') from ops.scac_mutation_registry_entry where registry_version=%s", (new_version,)).fetchone()[0]
            conn.execute("rollback to savepoint successor_probe")
            sql = render_sql(template, predecessor, rows, baseline, entry_set, dependencies)
            conn.execute(sql)
            if conn.execute(f"select ops.scac_mutation_catalog_v{plan['registry_successor']}_current(),ops.scac_mutation_registry_v{plan['registry_successor']}_seal_available()").fetchone() != (True, True):
                raise ValueError("regenerated successor seal failed disposable readback")
            conn.execute("insert into public.schema_migrations(filename,sha256) values (%s,%s)", (successor_path.name, hashlib.sha256(sql.encode()).hexdigest()))
        writer = subprocess.run(["python3", repo / "tools/integration_candidate.py", "--write", str(successor_path)], input=sql.encode(), cwd=repo, env=env, capture_output=True, timeout=120)
        if writer.returncode:
            raise ValueError("integration allocator refused successor SQL publication")
        payload = {"expectedSqlDigest": hashlib.sha256(sql.encode()).hexdigest(), "rows": rows, "catalog": baseline, "entrySetDigest": entry_set,
            "domainMigration": [{"filename": path.name, "sql": path.read_text(),
                **({"successor_filename": successor_path.name} if index == len(domain_paths)-1 else {})}
                for index, path in enumerate(domain_paths)]}
        payload_path = repo / '.git/successor-runtime.json'
        payload_path.write_text(json.dumps(payload, separators=(',', ':'), ensure_ascii=False))
        render = """import {createHash} from 'node:crypto';
import {appendSuccessor} from './ops/registry-chain.mjs';
import {writeIntegratedArtifact} from './ops/integration-generation.mjs';
import {readFileSync,writeFileSync} from 'node:fs';
const p=JSON.parse(readFileSync(process.argv[1],'utf8'));
const result=appendSuccessor(p);
if(createHash('sha256').update(result.sql).digest('hex')!==p.expectedSqlDigest) throw new Error('chain SQL differs from disposable readback');
await writeIntegratedArtifact('mcp-server/src/scac-mutation-registry.current.generated.js',result.runtime);
writeFileSync('ops/config/scac-registry-chain.json',JSON.stringify(result.chain,null,2)+'\\n');
writeFileSync('ops/config/scac-registry-source-inventory-fixtures.v1.json',JSON.stringify(result.fixture,null,2)+'\\n');
writeFileSync('ops/config/scac-registry-full-entry-set-seals.json',JSON.stringify(result.seals,null,2)+'\\n');
"""
        result = subprocess.run(["node", "--input-type=module", "-e", render, str(payload_path)], cwd=repo, env=env, capture_output=True, timeout=120)
        if result.returncode:
            raise ValueError("registry chain refused successor publication")
        return {"version": new_version, "catalog": baseline, "entry_set": entry_set, "source_count": len(rows), "entry_count": len(rows) + sum(baseline[k]["count"] for k in ("secdef_execute", "relation_dml", "column_dml"))}
