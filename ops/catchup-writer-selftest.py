#!/usr/bin/env python3
"""Run catch-up with the runtime writer role against synthetic PostgreSQL rows.

The timeline definition and its ACL come from the committed schema contract.
Only ref resolution is reduced to a fixture view; timeline/replay queries run
through the real registered handler and PostgreSQL privilege enforcement.
"""
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group


def main():
    spec = importlib.util.spec_from_file_location("local_pg_ci", ROOT / "ops/local-pg-ci.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    binaries = module.find_postgres_binaries()
    env = module.scrub_cloud_environment(os.environ)
    schema = (ROOT / "db/schema.sql").read_text()

    def definition(kind, name):
        match = re.search(rf"CREATE {kind} public\.{name}\b.*?;", schema, re.S)
        assert match, f"missing schema definition: {name}"
        return match.group()

    with postgres_fixture_group(), DisposablePostgres("carr-catchup-writer-", binaries.pg_ctl, env=env) as fixture:
        root = fixture.root
        data, socket = root / "data", root / "socket"
        socket.mkdir()
        fixture.run([binaries.initdb, "-D", data, "-A", "trust", "-U", "fixture"],
                       check=True, capture_output=True, env=env, timeout=60)
        fixture.run([binaries.pg_ctl, "-D", data, "-l", root / "pg.log", "-o",
                        f"-k {socket} -c listen_addresses=''", "-w", "start"],
                       check=True, capture_output=True, env=env, timeout=60)

        def sql(text):
            result = subprocess.run([binaries.psql, "-X", "-At", "-v", "ON_ERROR_STOP=1",
                                     "-h", socket, "-U", "fixture", "-d", "postgres"],
                                    input=text, text=True, capture_output=True, env=env, timeout=60)
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()

        sql("create role carr_reader; create role carr_writer;")
        for table in ("actor", "activity", "event", "tool_call"):
            sql(definition("TABLE", table))
        sql(definition("VIEW", "v_subject_timeline"))
        grants = re.findall(
            r"^grant [^\n]* on table public\.(?:v_subject_timeline|activity|tool_call) to carr_(?:reader|writer);$",
            schema, re.M | re.I)
        assert grants, "snapshot ACLs were not loaded"
        sql("\n".join(grants))
        sql("""
            create view v_ref_index as select 'vendor'::text subject_type,
              '00000000-0000-0000-0000-000000000011'::uuid subject_id,
              'V-FIX-001'::text ref;
            grant select on v_ref_index to carr_reader, carr_writer;
            insert into actor (id,slug,kind,display_name) values
              ('00000000-0000-0000-0000-000000000001','fixture-actor','human','Fixture Actor'),
              ('00000000-0000-0000-0000-000000000002','other-actor','human','Other Actor');
            insert into activity (id,occurred_at,actor_id,kind,summary,vendor_id) values
              ('00000000-0000-0000-0000-000000000021','2026-01-01T12:00:00Z',
               '00000000-0000-0000-0000-000000000001','meeting','Synthetic calendar meeting',
               '00000000-0000-0000-0000-000000000011');
            insert into tool_call (idempotency_key,verb,actor_id,request_hash,response) values
              ('calcap-fixture','log-activity','00000000-0000-0000-0000-000000000001',
               'fixture','{"activity_id":"00000000-0000-0000-0000-000000000021"}'),
              ('calcap-other-actor','log-activity','00000000-0000-0000-0000-000000000002',
               'fixture','{"activity_id":"00000000-0000-0000-0000-000000000021"}');
            begin read only; set local role carr_reader;
            select summary from v_subject_timeline; rollback;
        """)
        # Located by name so a renumber cannot silently drop the grant under test.
        [migration] = ROOT.glob("migrations/[0-9][0-9][0-9][0-9]_catchup_writer_timeline_read.sql")
        sql(migration.read_text())
        sql(migration.read_text())  # Reapplication must preserve the contract.
        node_env = dict(env, CARR_FIXTURE_PG_SOCKET=str(socket))
        result = subprocess.run(["node", "--input-type=module", "-"], cwd=ROOT,
            env=node_env, text=True, capture_output=True, timeout=60, input="""
            import assert from 'node:assert/strict';
            import pg from './mcp-server/node_modules/pg/lib/index.js';
            import { TOOLS } from './mcp-server/src/tools.js';
            import { connectionRouteForTool } from './mcp-server/src/mcp.js';
            const c = new pg.Client({host:process.env.CARR_FIXTURE_PG_SOCKET,
              user:'fixture', database:'postgres'});
            await c.connect();
            try {
              for (const name of ['catch-me-up','find-and-catch-up','prepare-conversation']) {
                assert.equal(connectionRouteForTool(TOOLS[name]), 'writer_read_only');
                assert.equal(TOOLS[name].write, false);
              }
              await c.query('begin read only');
              await c.query('set local role carr_writer');
              const result = await TOOLS['catch-me-up'].handler(c,
                {id:'00000000-0000-0000-0000-000000000001'}, {ref:'V-FIX-001'});
              assert.equal(result.timeline.length, 1);
              assert.equal(result.timeline[0].summary, 'Synthetic calendar meeting');
              assert.deepEqual(result.calendar_history, [{key:'calcap-fixture',
                activity_id:'00000000-0000-0000-0000-000000000021',
                summary:'Synthetic calendar meeting'}]);
              assert.equal((await c.query('show transaction_read_only')).rows[0].transaction_read_only,'on');
              await c.query('rollback');
              assert.equal((await c.query(`select has_table_privilege('carr_reader',
                'public.v_subject_timeline','SELECT') ok`)).rows[0].ok,true);
            } finally { await c.end(); }
        """)
        assert result.returncode == 0, result.stderr
        print("PASS catch-up returns timeline and actor-bound calendar history under carr_writer; reader access retained")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
