// Prove optional checklist storage recovery against synthetic PostgreSQL relations.
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { readDealChecklist, CHECKLIST_SOURCES } from '../src/deal-checklist.ts';

const deal = { id: '11111111-1111-4111-8111-111111111111', type: 'renewal' };
test('optional storage failures do not abort the caller transaction', async t => {
  const bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/local/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin'].find(p => existsSync(`${p}/initdb`));
  assert.ok(bin, 'disposable PostgreSQL binaries required');
  const fixture = await acquireDisposablePostgres({ prefix: 'checklist-recovery-', pgCtl: `${bin}/pg_ctl` });
  let c;
  try {
    await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '-A', 'trust']);
    await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -c listen_addresses=''`, '-w', 'start']);
    c = new pg.Client({ host: fixture.root, user: 'carr_ci', database: 'postgres' });
    await c.connect();
    await c.query('create table independent_deal(id uuid primary key)');
    await c.query('insert into independent_deal values($1)', [deal.id]);
    const reset = async () => {
      await c.query('drop schema if exists checklist_fixture cascade; create schema checklist_fixture; set search_path to checklist_fixture,public');
      await c.query('create table doctrine_section(id uuid,status text,body_hash text); create table v_deal_room_commission(deal_id uuid,commission_id text,status text,received_on date)');
      for (const s of CHECKLIST_SOURCES) await c.query("insert into doctrine_section values($1,'active',$2)", [s.section_id,s.content_hash]);
    };
    for (const transactional of [true, false]) {
      for (const stage of ['doctrine','commissions','requests-available','requests']) {
        await t.test(`${stage}, transaction=${transactional}`, async () => {
          await reset();
          if (stage === 'doctrine') await c.query('drop table doctrine_section');
          if (stage === 'commissions') await c.query('drop table v_deal_room_commission');
          if (transactional) { await c.query('begin'); await c.query('savepoint caller_owned'); }
          const client = { inTransaction: transactional, query: async (sql, args) => {
            if (sql.includes(`checklist:${stage}`) && stage.startsWith('requests')) {
              if (stage === 'requests' && sql.includes('requests-available')) return { rows: [{ relation: 'v_requirement_request' }] };
              return c.query('select * from absent_optional_storage');
            }
            return c.query(sql,args);
          } };
          try {
            assert.equal((await readDealChecklist(client,deal)).template_state,'unavailable');
            assert.equal((await c.query('select 1 as usable')).rows[0].usable,1);
            assert.equal((await c.query('select id from independent_deal')).rows[0].id,deal.id);
            if (transactional) await c.query('rollback to savepoint caller_owned');
          } finally { if (transactional) await c.query('rollback'); }
        });
      }
    }
    await t.test('ready and source_changed release only their owned scope', async () => {
      await reset(); await c.query('begin'); c.inTransaction = true;
      try {
        const result = await readDealChecklist(c,deal);
        assert.equal(result.template_state,'current');
        assert.equal(result.requests_state,'unavailable');
        await c.query("update doctrine_section set status='retired'");
        assert.equal((await readDealChecklist(c,deal)).template_state,'source_changed');
        await c.query('select 1');
      } finally { await c.query('rollback'); c.inTransaction = false; }
    });
    await t.test('stored requests retain received status and commission evidence', async () => {
      await reset();
      await c.query(`create table v_requirement_request(id text,row_key text,owner text,due_on date,
        document_type text,satisfied boolean,receipts jsonb,status text,fulfilled_at text,fulfilled_by text,
        evidence_attachment_id text,evidence_filename text,organization_tenant_id text,deal_id uuid)`);
      await c.query(`insert into v_requirement_request values('request','requirements','joe','2026-01-01',
        'requirements',false,'[]','fulfilled',null,null,'attachment','Synthetic.pdf','carr-internal',$1)`, [deal.id]);
      await c.query("insert into v_deal_room_commission values($1,'paid','received','2026-01-01')", [deal.id]);
      await c.query('begin'); c.inTransaction = true;
      try {
        const result = await readDealChecklist(c,deal);
        assert.equal(result.requests_state,'current');
        const items = result.phases.flatMap(phase => phase.items);
        assert.equal(items.find(item => item.id === 'requirements').status,'received');
        assert.equal(items.find(item => item.id === 'requirements').request.satisfied,false);
        assert.equal(items.find(item => item.id === 'commission_received').evidence_ref,'commission:paid');
        await c.query('select 1');
      } finally { await c.query('rollback'); c.inTransaction = false; }
    });
    for (const code of ['42501','XX000']) await t.test(`propagate ${code}`, async () => {
      await reset(); await c.query('begin');
      const client = { inTransaction: true, query: (sql,args) => sql.includes('checklist:') ? c.query(`do $$ begin raise exception using errcode='${code}', message='synthetic failure'; end $$`) : c.query(sql,args) };
      try { await assert.rejects(readDealChecklist(client,deal), error => error.code === code); await c.query('select 1'); }
      finally { await c.query('rollback'); }
    });
  } finally { try { await c?.end(); } finally { await fixture.close(); } }
});
