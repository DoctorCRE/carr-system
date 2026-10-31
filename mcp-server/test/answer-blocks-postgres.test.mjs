import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { readMigration } from './migration-source.testhelper.mjs';
import { docConversationTools } from '../src/doc-conversation.js';

const bin = ['/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin']
  .find(dir => existsSync(path.join(dir, 'initdb')));
const id = n => `61700000-0000-4000-8000-${String(n).padStart(12, '0')}`;
class ToolError extends Error {
  constructor(fields) { super(fields.error); Object.assign(this, fields); }
}

test('recorded proposed-rent comps persist through the sole append door and read back on PG18',
  { skip: !bin && 'PostgreSQL 18 unavailable' }, async t => {
    const fixture = await acquireDisposablePostgres({ prefix: 'answer-blocks-', pgCtl: path.join(bin, 'pg_ctl') });
    let c;
    t.after(async () => { await c?.end(); await fixture.close(); });
    const data = path.join(fixture.root, 'data');
    await fixture.run(path.join(bin, 'initdb'), ['-D', data, '-U', 'fixture', '--auth=trust', '--no-locale']);
    await fixture.run(path.join(bin, 'pg_ctl'), ['-D', data, '-l', path.join(fixture.root, 'server.log'),
      '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
    c = new pg.Client({ host: fixture.root, user: 'fixture', database: 'postgres' });
    await c.connect();
    const snapshot = readFileSync(new URL('../../db/schema.sql', import.meta.url), 'utf8');
    await c.query(`create schema ops;
      create role carr_reader; create role carr_writer; create role carr_jobs; create role carr_authority;`);
    for (const name of ['public.actor', 'ops.doc_conversation', 'ops.doc_conversation_turn',
      'ops.doc_conversation_grant', 'public.negotiation_round']) {
      const ddl = snapshot.match(new RegExp(`CREATE TABLE ${name.replace('.', '\\.')} \\([\\s\\S]*?\\n\\);`))?.[0];
      assert.ok(ddl, name);
      await c.query(ddl);
    }
    await c.query(`alter table actor add primary key (id);
      alter table ops.doc_conversation add primary key (id);
      alter table ops.doc_conversation_turn add unique (msg_id);
      alter table ops.doc_conversation_turn alter column id add generated always as identity;
      alter table ops.doc_conversation_turn drop column if exists answer_blocks;
      create function ops.portfolio_writer_actor_id() returns uuid language sql as
        $$ select '${id(1)}'::uuid $$;`);
    const old = readMigration('0520_doc_conversation_store.sql');
    await c.query(old.slice(old.indexOf('create or replace function ops.append_doc_conversation_turn('),
      old.indexOf('comment on function ops.append_doc_conversation_turn(')));
    await c.query(readMigration('1112_doc_answer_blocks.sql').split('do $answer_grants$')[0]);
    const view = snapshot.match(/CREATE VIEW public\.v_deal_room_negotiation AS[\s\S]*?;/)?.[0];
    assert.ok(view);
    await c.query(view);
    await c.query(`insert into actor(id,slug,kind,display_name) values ($1,'synthetic-author','automation','Synthetic');
      `, [id(1)]);
    await c.query('insert into ops.doc_conversation(id,title,created_by_actor) values ($1,$2,$3)',
      [id(2), 'Synthetic comps', id(1)]);
    await c.query(`insert into negotiation_round(id,deal_id,round_no,side,proposed_on,rate_amount,rate_basis,created_by)
      values ($1,$2,1,'landlord','2026-10-01',2.5,'usd_sf_mo',$3)`, [id(3), id(4), id(1)]);
    const tools = docConversationTools({ ToolError,
      withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {} });
    const actor = { id: id(1) };
    const args = { conversation_id: id(2), idempotency_key: id(5), msg_id: id(6),
      role: 'assistant', body: 'Recorded proposed-rent comparisons', comps_deals: [id(4)] };
    const append = () => tools['add-doc-conversation-turn'].handler(c, actor, args);
    assert.equal((await append()).deduplicated, false);
    assert.equal((await append()).deduplicated, true);
    const read = await tools['read-doc-conversation'].handler(c, actor, { conversation_id: id(2) });
    assert.equal(read.turns.length, 1);
    assert.deepEqual(read.turns[0].answer_blocks[0].rows, [{ deal: { value: id(4) },
      side: { value: 'landlord' }, rent: { value: 30, source: { record_id: id(3) } } }]);
    args.comps_deals = [id(7)];
    await assert.rejects(append(), /doc_conversation_msg_id_reuse/);
    await tools['add-doc-conversation-turn'].handler(c, actor, { ...args,
      msg_id: id(8), idempotency_key: id(9), comps_deals: undefined, body: 'Plain text' });
    const text = await tools['read-doc-conversation'].handler(c, actor, { conversation_id: id(2) });
    assert.equal(text.turns[1].body, 'Plain text');
    assert.equal(Object.hasOwn(text.turns[1], 'answer_blocks'), false);
  });
