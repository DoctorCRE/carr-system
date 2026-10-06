#!/usr/bin/env python3
"""Exercise pending-attempt aging against a private throwaway PostgreSQL cluster."""
import json
import os
from pathlib import Path
import importlib.util
import sys
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
            create table public.tool_call(idempotency_key text, verb text, actor_id uuid, response jsonb);
        """)
        migration = sorted((ROOT / 'migrations').glob('*_jev_attempt*.sql'))[-1]
        sql(migration.read_text())
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
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
