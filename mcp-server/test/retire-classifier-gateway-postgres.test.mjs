import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import pg from 'pg';
import { acquirePostgresFixtureGroup, acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';

const bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin']
  .find(candidate => existsSync(path.join(candidate, 'initdb')));
const migration = new URL('../../migrations/0935_retire_jev_gateway.sql', import.meta.url);

test('retiring the classifier gateway removes its store and functions while preserving unrelated records and publication members',
  { skip: !bin && 'PostgreSQL unavailable' }, async () => {
    const release = await acquirePostgresFixtureGroup();
    let fixture, client;
    try {
      fixture = await acquireDisposablePostgres({ prefix: 'retire-classifier-', pgCtl: path.join(bin, 'pg_ctl') });
      const data = path.join(fixture.root, 'data');
      await fixture.run(path.join(bin, 'initdb'), ['-D', data, '-U', 'fixture', '--auth=trust', '--no-locale']);
      await fixture.run(path.join(bin, 'pg_ctl'), ['-D', data, '-l', path.join(fixture.root, 'server.log'),
        '-o', `-k ${fixture.root} -h '' -c wal_level=logical`, '-w', 'start']);
      client = new pg.Client({ host: fixture.root, user: 'fixture', database: 'postgres' });
      await client.connect();
      await client.query(`create schema ops;
        create table public.unrelated_record (id integer primary key);
        insert into public.unrelated_record values (7);
        create table public.tool_call (verb text, idempotency_key text, actor_id uuid, response jsonb);
        create table ops.jev_call_receipt (receipt_id uuid);
        insert into ops.jev_call_receipt values ('10000000-0000-4000-8000-000000000001');
        create index tool_call_jev_receipt_idx on public.tool_call (verb);
        create index tool_call_jev_settlement_idx on public.tool_call (idempotency_key);
        create function ops.jev_call_receipt_append_only() returns trigger language plpgsql as $$ begin return new; end $$;
        create trigger jev_call_receipt_append_only before update or delete on ops.jev_call_receipt
          for each row execute function ops.jev_call_receipt_append_only();
        create function ops.jev_call_receipt_integrity() returns jsonb language sql as $$ select '{}'::jsonb $$;
        create function ops.read_jev_call_receipts(text,timestamptz,integer,text) returns jsonb language sql as $$ select '{}'::jsonb $$;
        create function ops.record_jev_call_receipt(text,text,text[],text[],text,text,text,text,text,text,jsonb,jsonb,uuid,text,text)
          returns table(receipt_id uuid,recorded_at timestamptz,replayed boolean) language sql
          as $$ select null::uuid,null::timestamptz,false $$;
        create publication fixture_publication for table ops.jev_call_receipt, public.unrelated_record;`);
      // Install the actual pre-removal contract, without business data or credentials.
      const previous = readFileSync(new URL('../../migrations/0907_approve_rule_trusted_search_path.sql', import.meta.url), 'utf8');
      await client.query(previous.slice(previous.indexOf('CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract()')));
      await client.query(readFileSync(migration, 'utf8'));
      assert.equal((await client.query("select to_regclass('ops.jev_call_receipt') name")).rows[0].name, null);
      assert.deepEqual((await client.query("select proname from pg_proc join pg_namespace n on n.oid=pronamespace where n.nspname='ops' and proname like '%jev%'")).rows, []);
      assert.deepEqual((await client.query('select * from public.unrelated_record')).rows, [{ id: 7 }]);
      assert.deepEqual((await client.query("select tablename from pg_publication_tables where pubname='fixture_publication'")).rows,
        [{ tablename: 'unrelated_record' }]);
      assert.equal((await client.query("select count(*)::integer n from pg_indexes where indexname in ('tool_call_jev_receipt_idx','tool_call_jev_settlement_idx')")).rows[0].n, 0);
      const expected = JSON.parse(readFileSync(migration, 'utf8').match(/\$current_grants\$([\s\S]*?)\$current_grants\$/)[1]);
      assert.deepEqual((await client.query('select ops.scac_current_grant_contract() value')).rows[0].value, expected);
      const retired = expected.retired_secdef_execute.filter(row => /ops\.(?:jev_call_receipt_integrity|read_jev_call_receipts|record_jev_call_receipt)\(/.test(row.signature));
      assert.equal(retired.length, 5, 'all retired Jev execute grants remain accounted for by the sealed catalog');
    } finally {
      await client?.end();
      await fixture?.close();
      await release();
    }
  });
