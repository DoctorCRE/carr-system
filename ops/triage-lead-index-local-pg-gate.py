#!/usr/bin/env python3
# ci: db-gate
# ci: db-gate-before-isolated
"""Prove audit #2182 Q1, Q3 and Q4 on a snapshot-loaded disposable PostgreSQL 18."""
import argparse
from datetime import date, datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group


def synthetic_id(label):
    return uuid.UUID(hashlib.md5(label.encode()).hexdigest())


def nodes(plan):
    yield plan
    for child in plan.get('Plans', []):
        yield from nodes(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='origin/main')
    parser.add_argument('--slice', choices=('Q3', 'Q1', 'Q4', 'all'), default='all')
    parser.add_argument('--baseline', action='store_true', help='assert access paths before pending migrations')
    parser.add_argument('--evidence', type=Path, help='write before/after EXPLAIN JSON')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('shadow', ROOT / 'ops/migration-shadow.py')
    shadow = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shadow)
    bins = shadow.find_pg18(None)
    assert bins is not None, 'PostgreSQL 18 server binaries required'
    clean = shadow.local_pg.scrub_cloud_environment(os.environ)
    clean['PATH'] = str(bins) + os.pathsep + clean.get('PATH', '')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    evidence = {'base': subprocess.check_output(['git', 'rev-parse', args.base], cwd=ROOT, text=True).strip(),
                'rows_per_table': 20000, 'plans': {}, 'checks': []}
    failures = []
    with postgres_fixture_group(), DisposablePostgres('triage-lead-index-', bins / 'pg_ctl', env=clean) as fixture:
        data = fixture.root / 'data'
        fixture.run([bins / 'initdb', '-D', data, '-U', 'carr_ci', '-A', 'trust', '--no-locale'],
                    capture_output=True, check=True, timeout=60)
        fixture.run([bins / 'pg_ctl', '-D', data, '-l', fixture.root / 'postgres.log', '-o',
                     f'-h 127.0.0.1 -p {port} -k {fixture.root}', '-w', 'start'],
                    capture_output=True, check=True, timeout=60)
        dsn = f'postgresql://carr_ci@127.0.0.1:{port}/postgres'
        import psycopg
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute('create role neondb_owner')
            base = fixture.root / 'base.sql'
            base.write_bytes(subprocess.check_output(['git', 'show', f'{args.base}:db/schema.sql'], cwd=ROOT))
            loaded = subprocess.run([str(bins / 'psql'), '-Xq', '-v', 'ON_ERROR_STOP=1', '-d', dsn, '-f', str(base)],
                                    env=clean, capture_output=True, text=True, timeout=180)
            assert loaded.returncode == 0, loaded.stderr
            conn.execute("set statement_timeout='120s'")
            conn.execute((ROOT / 'test/fixtures/triage-lead-indexes.sql').read_text())
            prior_indexes = {row[0] for row in conn.execute("select indexname from pg_indexes where schemaname='public'")}
            queries = {
                'Q3 next_action': "select id from next_action where status='open' and due_on is not null and due_on<=current_date and (hold_until is null or hold_until<=current_date)",
                'Q3 post_call_action': "select id from capture_post_call_action where status='open' and due_on is not null and due_on<=current_date",
                'Q3 critical_date': "select id from critical_date where status='open' and due_on<=current_date+14",
                'Q3 inbox': "select id from ingest_inbox where status='new'",
                'Q1 correspondence': "select id,lead_id,kind,occurred_at,summary,detail,source,connected from activity where lead_id=md5('index-lead-1')::uuid order by occurred_at desc,id desc limit 100",
                'Q1 history': "select * from v_lead_stage_transition where lead_id=md5('index-lead-1')::uuid order by mutation_order desc limit 100",
                'Q1 latest': "select distinct on (lead_id) lead_id,event_id from v_lead_stage_transition order by lead_id,mutation_order desc",
            }
            party = "md5('index-vendor-party-1')::uuid"
            queries.update({
                'Q4 coverage': f"select case when bool_and(deal_history_verified_at is not null) then min(deal_history_verified_at) end from vendor where party_id={party} and merged_into is null",
                'Q4 lock': f"select id from vendor where party_id={party} and merged_into is null order by id for update",
                'Q4 referrals': f"select deal_id,referred_by,occurred_on from party_link_deal where referred_by={party} order by deal_id",
            })
            projection = subprocess.run(['node', '--input-type=module', '-e',
                "import {vendorRelationshipJoin} from './mcp-server/src/vendor-relationship.js'; console.log(vendorRelationshipJoin)"],
                cwd=ROOT, env=clean, capture_output=True, text=True, timeout=60)
            assert projection.returncode == 0, projection.stderr
            projection_sql = "select v.id,vr.payload from vendor v join party p on p.id=v.party_id " + projection.stdout
            queries['Q4 directory projection'] = projection_sql + f" where v.party_id={party} and v.merged_into is null order by v.id"
            queries['Q4 detail projection'] = projection_sql + " where v.id=md5('index-vendor-1')::uuid"
            queries = {name: sql for name, sql in queries.items() if args.slice == 'all' or name.startswith(args.slice)}
            queries['triage view'] = 'select * from v_today_triage order by due_on nulls last,item_kind,id'
            queries['lead detail'] = "select * from v_lead_workspace_detail where id=md5('index-lead-1')::uuid"
            captured = subprocess.run(['node', '--input-type=module', '-e', '''
                import {TOOLS} from './mcp-server/src/tools.js';
                const queries = [];
                await TOOLS['today-triage'].handler({query: async (sql,params) => {
                    queries.push({sql,params}); return {rows: []};
                }}, {slug: 'joe'}, {owners: 'all',limit: 50});
                console.log(JSON.stringify(queries));
            '''], cwd=ROOT, env=clean, capture_output=True, text=True, timeout=60)
            assert captured.returncode == 0, captured.stderr
            handler, = json.loads(captured.stdout)
            queries['triage handler'] = re.sub(r'\$(\d+)', lambda m:
                psycopg.sql.Literal(handler['params'][int(m[1])-1]).as_string(conn), handler['sql'])

            def capture(ratio, side):
                conn.execute('analyze')
                results = {}
                for name, sql in queries.items():
                    results[name] = conn.execute(sql).fetchall()
                    if name.startswith('Q') or name == 'triage handler':
                        plan = conn.execute('explain(analyze,buffers,format json) ' + sql).fetchone()[0][0]
                        evidence['plans'][f'{ratio} {side} {name}'] = plan
                        if ratio == 'selective' and side == 'after' and name.startswith('Q'):
                            scans = list(nodes(plan['Plan']))
                            if any(n['Node Type'] == 'Seq Scan' and
                                   (not name.startswith('Q4') or n.get('Relation Name') in
                                    ('vendor', 'party_link_deal')) for n in scans):
                                failures.append(f'{name} still scans unrelated history')
                return results

            before = capture('selective', 'before')
            if not args.baseline:
                applied = subprocess.run([sys.executable, str(ROOT / 'tools/migrate.py'), '--apply', '--yes'],
                                         cwd=ROOT, env={**clean, 'DATABASE_URL': dsn},
                                         capture_output=True, text=True, timeout=600)
                assert applied.returncode == 0, applied.stdout + applied.stderr
            after = capture('selective', 'after')
            for name in queries:
                ordered = not name.startswith('Q3')
                assert (before[name] == after[name] if ordered else set(before[name]) == set(after[name])), name
                evidence['checks'].append(f'{name} unchanged, {len(after[name])} rows')
            if args.slice in ('all', 'Q3'):
                for name, prefix, excluded in (
                    ('Q3 next_action', 'next', {2, 3, 4}),
                    ('Q3 post_call_action', 'post', {3, 4}),
                    ('Q3 critical_date', 'date', {2}),
                    ('Q3 inbox', 'inbox', set()),
                ):
                    expected = {synthetic_id(f'index-{prefix}-{n}') for n in range(1, 101) if n not in excluded}
                    assert {row[0] for row in after[name]} == expected, name
            if args.slice in ('all', 'Q4'):
                selected = sorted(synthetic_id(f'index-vendor-{n}') for n in range(1, 5))
                assert after['Q4 lock'] == [(value,) for value in selected]
                assert after['Q4 coverage'] == [(None,)]
                assert after['Q4 referrals'] == [(synthetic_id('index-deal'), synthetic_id('index-vendor-party-1'), date(2020, 1, 2))]
                assert [row[0] for row in after['Q4 directory projection']] == selected
                for _, payload in after['Q4 directory projection'] + after['Q4 detail projection']:
                    assert payload['coverage_verified_at'] is None
                    assert (payload['deals_referred'], payload['deals_worked'], payload['won'], payload['lost']) == (1, 1, 0, 0)
                with conn.transaction():
                    conn.execute("update vendor set deal_history_verified_at='2020-01-03 UTC' where id=md5('index-vendor-3')::uuid")
                    verified = conn.execute(queries['Q4 detail projection']).fetchone()[1]
                    assert datetime.fromisoformat(verified['coverage_verified_at']) == datetime(2020, 1, 1, tzinfo=timezone.utc)
                    conn.execute("update vendor set deal_history_verified_at=null where id in (select id from vendor where party_id=" + party + " and merged_into is null)")
                    assert conn.execute(queries['Q4 coverage']).fetchall() == [(None,)]
                    raise psycopg.Rollback()
            if args.slice in ('all', 'Q1'):
                expected = sorted(range(1, 121), key=lambda n: (n // 2, synthetic_id(f'index-activity-{n}')), reverse=True)[:100]
                assert [row[0] for row in after['Q1 correspondence']] == [synthetic_id(f'index-activity-{n}') for n in expected]
                assert [row[0] for row in after['Q1 history']] == [synthetic_id(f'index-event-{n}') for n in range(120, 20, -1)]
                assert dict(after['Q1 latest']) == {synthetic_id(f'index-lead-{n}'): synthetic_id(f'index-event-{n*120}') for n in (1, 2)}
            if args.slice in ('all', 'Q3'):
                with conn.transaction():
                    conn.execute("update next_action set status='open' where status<>'open' and description='Synthetic action' and id not in (select id from next_action order by id limit 2000)")
                    conn.execute("update capture_post_call_action set status='open',completed_at=null where status<>'open'")
                    conn.execute("update critical_date set status='open' where status<>'open'")
                    conn.execute("update ingest_inbox set status='new' where status<>'new'")
                dense_after = capture('mostly-open', 'after')
                with conn.transaction():
                    new_names = {row[0] for row in conn.execute("select indexname from pg_indexes where schemaname='public'")} - prior_indexes
                    for name in new_names:
                        conn.execute(psycopg.sql.SQL('drop index public.{}').format(psycopg.sql.Identifier(name)))
                    dense_before = capture('mostly-open', 'before')
                    for name in queries:
                        assert (dense_before[name] == dense_after[name] if not name.startswith('Q3') else set(dense_before[name]) == set(dense_after[name])), name
                    raise psycopg.Rollback()
                evidence['checks'].append('mostly-open results unchanged; sequential scans allowed')
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(evidence, indent=2) + '\n')
    assert not failures, '; '.join(failures)
    print(f"PASS {args.slice}: {len(queries)} unchanged query results, selective access paths, synthetic expectations, fixture teardown")


if __name__ == '__main__':
    main()
