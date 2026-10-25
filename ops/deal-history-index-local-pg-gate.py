#!/usr/bin/env python3
# ci: db-gate
# ci: db-gate-before-isolated
"""Prove schema audit Q2 preserves deal history and indexes selective SQL reads."""
import argparse
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_bin_dir, postgres_fixture_group


def nodes(plan):
    yield plan
    for child in plan.get('Plans', []):
        yield from nodes(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', action='store_true')
    args = parser.parse_args()
    bins = postgres_bin_dir()
    clean = {'PATH': str(bins) + ':/usr/bin:/bin', 'LC_ALL': 'C'}
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    with postgres_fixture_group(), DisposablePostgres('deal-history-', bins / 'pg_ctl', env=clean) as fixture:
        data = fixture.root / 'data'
        fixture.run([bins / 'initdb', '-D', data, '-U', 'carr_ci', '-A', 'trust', '--no-locale'],
                    capture_output=True, check=True, timeout=60)
        fixture.run([bins / 'pg_ctl', '-D', data, '-l', fixture.root / 'postgres.log', '-o',
                     f'-h 127.0.0.1 -p {port} -k {fixture.root}', '-w', 'start'],
                    capture_output=True, check=True, timeout=60)

        def sql(statement):
            result = subprocess.run([str(bins / 'psql'), '-XAtq', '-v', 'ON_ERROR_STOP=1',
                                     '-h', str(fixture.root), '-p', str(port), '-U', 'carr_ci',
                                     '-d', 'postgres'], input=statement, env=clean,
                                    capture_output=True, text=True, timeout=120)
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()

        sql("""
create table actor(id uuid primary key,slug text);
insert into actor select lpad(to_hex(n),32,'0')::uuid,'synthetic-'||n from generate_series(1,20000) n;
create table next_action(id uuid primary key,subject_type text,subject_id uuid,owner_id uuid,
 description text,due_on date,status text,updated_at timestamptz,hold_until date,item_title text,
 item_bullets jsonb,source_ref text,created_at timestamptz);
create table capture_post_call_action (like next_action including all);
alter table capture_post_call_action rename subject_id to deal_id;
alter table capture_post_call_action rename created_at to accepted_at;
create table critical_date(id uuid primary key,deal_id uuid,kind text,due_on date,note text,source text,status text);
insert into next_action select lpad(to_hex(n),32,'0')::uuid,
 case when n=13 then 'lead' else 'deal' end,lpad(to_hex(case when n<=13 then 1 else n end),32,'0')::uuid,
 lpad(to_hex(n),32,'0')::uuid,'Synthetic action',date '2020-01-01'+n%5,
 (array['open','done','dropped'])[1+n%3],timestamptz '2020-01-01 UTC'+n*interval '1 second',
 date '2099-01-01',case when n%2=0 then 'Synthetic item' end,'[]','synthetic-'||n,
 timestamptz '2020-01-01 UTC'+(n/2)*interval '1 second' from generate_series(1,20000) n;
insert into capture_post_call_action select lpad(to_hex(ordinal+30000),32,'0')::uuid,subject_type,
 case when id=lpad('d',32,'0')::uuid then lpad('2',32,'0')::uuid else subject_id end,
 owner_id,description,due_on,status,updated_at,hold_until,item_title,item_bullets,source_ref,created_at from next_action action
 cross join lateral (select ('x'||right(replace(action.id::text,'-',''),8))::bit(32)::int ordinal) number;
insert into critical_date select id,subject_id,'synthetic',due_on,null,'stated',status from next_action where subject_type='deal';
""")
        for filename, pattern in (
            ('0079_deal_room_api.sql', r'create view v_deal_room_critical_date as.*?;'),
            ('0936_deal_alert_items.sql', r'create or replace view v_deal_room_action as.*?;'),
        ):
            sql(re.search(pattern, (ROOT / 'migrations' / filename).read_text(), re.S).group())
        for filename in ('0936_deal_alert_items.sql', '0962_triage_lead_read_indexes.sql'):
            for index in re.findall(r'create (?:unique )?index .*?;', (ROOT / 'migrations' / filename).read_text(), re.S):
                if re.search(r' on (?:public\.)?(next_action|capture_post_call_action|critical_date)\b', index):
                    sql(index)
        selected = "deal_id=lpad('1',32,'0')::uuid"
        queries = {
            'dates': f"select id,kind,to_jsonb(due_on)#>>'{{}}' as due_on,note,source,status from v_deal_room_critical_date where {selected} order by due_on,id",
            'history': f"select * from v_deal_room_action where {selected} order by created_order,id",
            'open actions': f"select * from v_deal_room_action where {selected} and status='open' order by deal_id,updated_at desc,id desc limit 10001",
            'checklist': f"select * from v_deal_room_action where {selected} and item_title is not null and status<>'dropped' order by deal_id,created_order,id limit 10001",
        }

        def capture():
            sql('analyze;')
            return {name: json.loads(sql(f"select coalesce(jsonb_agg(row_to_json(q)), '[]') from ({query}) q"))
                    for name, query in queries.items()}

        before = capture()
        if not args.baseline:
            migration, = (ROOT / 'migrations').glob('*_deal_history_read_indexes.sql')
            sql('begin;\n' + migration.read_text() + '\ncommit;')
        after = capture()
        assert before == after, 'index build changed returned history'
        assert [len(after[name]) for name in queries] == [12, 24, 8, 8]
        expected = sorted([(n//2, n) for n in range(1,13)] + [(n//2, n+30000) for n in range(1,13)])
        assert [row['id'] for row in after['history']] == [str(uuid.UUID(int=n)) for _, n in expected]
        assert [row['id'] for row in after['dates']] == [str(uuid.UUID(int=n)) for n in sorted(range(1,13), key=lambda n: (n%5,n))]
        for name, chosen in (('open actions', (3,6,9,12)), ('checklist', (4,6,10,12))):
            assert {row['id'] for row in after[name]} == {str(uuid.UUID(int=n+offset)) for n in chosen for offset in (0,30000)}
        failures = []
        for name, query in queries.items():
            plan = json.loads(sql('explain(format json) ' + query))[0]['Plan']
            scans = [node for node in nodes(plan) if node.get('Relation Name') in
                     {'critical_date', 'next_action', 'capture_post_call_action'}]
            assert scans, name
            if any(node['Node Type'] == 'Seq Scan' for node in scans):
                failures.append(name)
        assert not failures, f'selective reads scan unrelated history: {failures}'
    print('PASS Q2: four exact result comparisons, expected mixed histories, four selective plans, teardown')


if __name__ == '__main__':
    main()
