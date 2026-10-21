#!/usr/bin/env python3
"""Exercise pending-attempt aging against a private throwaway PostgreSQL cluster."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import migration_names, read_migration
import json
import os
import importlib.util
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group


def main():
    spec = importlib.util.spec_from_file_location('local_pg_ci', ROOT / 'ops/local-pg-ci.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    found = module.find_postgres_binaries()
    binaries = {name: str(getattr(found, name)) for name in ('initdb', 'pg_ctl', 'psql')}
    env = module.scrub_cloud_environment(os.environ)
    with postgres_fixture_group(), DisposablePostgres('carr-jev-aging-', binaries['pg_ctl'], env=env) as fixture:
        root = fixture.root
        data = root / 'data'
        socket = root / 'socket'
        socket.mkdir()
        fixture.run([binaries['initdb'], '-D', str(data), '-A', 'trust', '-U', 'fixture'],
                       check=True, capture_output=True, env=env, timeout=60)
        fixture.run([binaries['pg_ctl'], '-D', str(data), '-l', str(root / 'pg.log'),
                        '-o', f"-k {socket} -c listen_addresses=''", '-w', 'start'],
                       check=True, capture_output=True, env=env, timeout=60)
        def sql(text):
            result = subprocess.run([binaries['psql'], '-X', '-At', '-v', 'ON_ERROR_STOP=1',
                '-h', str(socket), '-U', 'fixture', '-d', 'postgres'], input=text,
                text=True, capture_output=True, check=True, env=env, timeout=60)
            return result.stdout.strip()
        sql("""create schema ops;
            create table ops.jev_call_receipt(receipt_id uuid, recorded_at timestamptz,
                idempotency_key text, actor_id uuid, model_answered text, usage jsonb);
            create table public.tool_call(idempotency_key text primary key, verb text, actor_id uuid, response jsonb);
            create unique index on ops.jev_call_receipt(idempotency_key);
        """)
        audit_source = read_migration(ROOT, '0803_jev_attempt_aging.sql')
        sql(audit_source)
        sql("""insert into ops.jev_call_receipt
            select ('00000000-0000-0000-0000-' || lpad(i::text,12,'0'))::uuid,
                clock_timestamp() - case i when 1 then interval '30 minutes' else interval '2 hours' end,
                i::text, '00000000-0000-0000-0000-000000000099', 'jev-attempt-pending', null
            from generate_series(1,3) i;
            insert into public.tool_call select idempotency_key, 'ask-jev-attempt', actor_id,
                jsonb_build_object('receipt_id',receipt_id,'cache_hit',idempotency_key='3')
                from ops.jev_call_receipt;
        """)
        usage = json.loads(sql("select ops.jev_call_receipt_integrity()->'daily_usage';"))
        assert usage['pending_attempts'] == 1, usage
        assert usage['abandoned_attempts'] == 1, usage
        assert usage['abandon_after_seconds'] == 3600, usage
        assert usage['unknown'] == 1, usage
        assert usage['calls'] == 0 and usage['input_tokens'] == 0, usage
        print('PASS pending under one hour; abandoned over one hour; settled excluded')
        sql("""insert into ops.jev_call_receipt
            select ('00000000-0000-0000-0000-' || lpad(i::text,12,'0'))::uuid,
                case when i in (8,9) then now() - interval '2 hours' else now() end, i::text,
                '00000000-0000-0000-0000-000000000099',
                case when i in (8,9) then 'jev-attempt-pending' else 'synthetic-answer' end,
                case i when 4 then '{"input_tokens":10}'::jsonb
                       when 7 then '{"input_tokens":"invalid"}'::jsonb end
            from generate_series(4,11) i;
            insert into public.tool_call
            select idempotency_key,
                case when idempotency_key in ('8','9','10') then 'ask-jev-attempt' else 'ask-jev' end,
                case when idempotency_key='8' then md5('wrong-actor')::uuid else actor_id end,
                jsonb_build_object('receipt_id',
                    case when idempotency_key='9' then md5('wrong-receipt')::uuid else receipt_id end,
                    'cache_hit',idempotency_key in ('5','8','9'))
            from ops.jev_call_receipt where idempotency_key in ('4','5','6','7','8','9','10');
        """)
        sql("""insert into ops.jev_call_receipt
            select md5('receipt-' || i)::uuid, now() - interval '2 days',
                md5('attempt-' || i), md5('actor')::uuid, 'jev-attempt-pending', null
            from generate_series(1,288000) i;
            insert into public.tool_call
            select idempotency_key, 'ask-jev-attempt', actor_id,
                jsonb_build_object('receipt_id',receipt_id,'cache_hit',false)
            from ops.jev_call_receipt where length(idempotency_key) = 32;
            insert into public.tool_call
            select md5('unrelated-' || i), 'synthetic-other', md5('actor')::uuid,
                jsonb_build_object('synthetic',repeat('x',300))
            from generate_series(1,1000000) i;
            analyze;
        """)
        before = json.loads(sql('select ops.jev_call_receipt_integrity();'))
        for name in sorted(migration_names(ROOT)):
            if name.endswith(('_jev_attempt_settlement_lookup.sql', '_jev_receipt_audit_lookup.sql')):
                sql(read_migration(ROOT, name))
        sql('analyze public.tool_call;')
        settlement = 'select count(*) ' + audit_source.split('into v_pending, v_abandoned ')[1].split(';')[0]
        settlement_plan = json.loads(sql(
            'explain (analyze,buffers,format json,timing off) ' + settlement))
        assert 'tool_call_jev_settlement_idx' in json.dumps(settlement_plan), settlement_plan
        assert int(sql(settlement)) == 288004
        print('PASS canonical settlement query uses partial index and preserves 288004 unsettled attempts')
        plan = json.loads(sql('explain (analyze,buffers,format json,timing off) select ops.jev_call_receipt_integrity();'))[0]['Plan']
        blocks = plan['Shared Hit Blocks'] + plan['Shared Read Blocks']
        # Bound the audit by receipt work; unrelated ledger traffic must not add a full-table scan.
        budget = int(sql("select (8 * pg_relation_size('ops.jev_call_receipt')) / current_setting('block_size')::int;"))
        assert blocks <= budget, f'audit touched {blocks} buffers, budget {budget}'
        audit = json.loads(sql('select ops.jev_call_receipt_integrity();'))
        before.pop('checked_at')
        audit.pop('checked_at')
        assert audit == before, (before, audit)
        print('PASS every canonical audit field is identical before and after the index')
        assert audit['receipts_total'] == 288011, audit
        assert audit['receipts_without_tool_call']['count'] == 4, audit
        assert set(audit['receipts_without_tool_call']['receipt_ids']) == {
            f'00000000-0000-0000-0000-{i:012}' for i in (8,9,10,11)}, audit
        assert audit['daily_usage']['pending_attempts'] == 1, audit
        assert audit['daily_usage']['abandoned_attempts'] == 288003, audit
        assert audit['daily_usage']['calls'] == 2 and audit['daily_usage']['input_tokens'] == 10, audit
        assert audit['daily_usage']['unknown'] == 5, audit
        print(f'PASS full audit with 288011 receipts and 1288010 ledger rows: {blocks} buffers, budget {budget}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
