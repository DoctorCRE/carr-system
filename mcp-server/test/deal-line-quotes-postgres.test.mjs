import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { dealFileTools } from '../src/deal-files.js';
import { postgresDatabase } from './helpers/postgres-database.mjs';

const migration = new URL('../../migrations/1143_deal_line_quotes.sql', import.meta.url);

test('line quotes preserve exact versions, ranges and access across retries', async () => {
  const db = await postgresDatabase('line-quotes-');
  const c = db.client;
  const deal = randomUUID(), doc = randomUUID(), joe = randomUUID(), dell = randomUUID();
  const hash = 'a'.repeat(64), text = 'Lease 😀\nBase rent is $24 per SF.\nTerm is 60 months.';
  const call = async (fn, args) => (await c.query(`select ops.${fn}(${args.map((_, i) => '$' + (i + 1)).join(',')}) r`, args)).rows[0].r;
  try {
    await c.query(`create schema ops;
      create function ops.scac_current_grant_contract() returns jsonb language sql immutable
        set search_path=pg_catalog as 'select ''{"secdef_execute":[]}''::jsonb';
      create role carr_reader; create role carr_writer; create role carr_authority; create role dot_reader; create role carr_jobs;
      create table public.actor(id uuid primary key,slug text,kind text,active boolean default true);
      create table public.party(id uuid primary key,merged_into uuid,deleted_at timestamptz);
      create table public.client(id uuid primary key,party_id uuid,merged_into uuid);
      create table public.deal(id uuid primary key,client_id uuid,owner text,version integer);
      create table public.attachment(id uuid primary key,subject_type text,subject_id uuid,sha256 text,deleted_at timestamptz);`);
    const guard = await readFile(new URL('../../migrations/1142_tour_deal_access.sql', import.meta.url), 'utf8');
    await c.query(guard.slice(guard.indexOf('create function'), guard.indexOf('create function ops.tour_target_visible')));
    await c.query("insert into actor values($1,'joe','human',true),($2,'dell','human',true)", [joe, dell]);
    await c.query("insert into party(id) values($1)", [deal]);
    await c.query("insert into client(id,party_id) select id,id from party");
    await c.query("insert into deal values($1,$1,'joe',1)", [deal]);
    await c.query("insert into attachment values($1,'deal',$2,$3,null)", [doc, deal, hash]);
    await c.query("select set_config('carr.acting_actor_slug','joe',false),set_config('carr.verified_human_actor_slug','joe',false)");
    await c.query("select set_config('carr.tour_deal_access',$1,false)", [JSON.stringify({slug:'joe',deals:{[deal]:{owner:'joe',version:1}}})]);
    if (process.argv.includes('--baseline')) {
      await assert.rejects(call('register_deal_quote_text', [doc, randomUUID(), hash, text]), {code:'42883'});
      assert.fail('Baseline has no exact-version quote storage');
    }
    await c.query(await readFile(migration, 'utf8'));
    assert.equal((await c.query("select jsonb_array_length(ops.scac_current_grant_contract()->'secdef_execute') n")).rows[0].n,5);
    const key = randomUUID();
    const source = await call('register_deal_quote_text', [doc, key, hash, text]);
    assert.equal(source.version, 1);
    assert.deepEqual(await call('register_deal_quote_text', [doc, key, hash, text]), source);
    assert.equal((await call('register_deal_quote_text', [doc, key, hash, text+'x'])).error, 'key_reuse');
    const start = Array.from(text).indexOf('B'), quoteText = 'Base rent is $24 per SF.';
    const args = {snapshot_id:source.snapshot_id,start,end:start+Array.from(quoteText).length,quoted_text:quoteText,comment:'Please confirm this exact rent.'};
    const saveKey = randomUUID();
    const saved = await call('save_deal_line_quote', [deal,saveKey,args]);
    assert.equal(saved.ok,true);
    assert.deepEqual(await call('save_deal_line_quote', [deal,saveKey,args]), saved);
    assert.equal((await call('save_deal_line_quote', [deal,saveKey,{...args,comment:'changed'}])).error,'key_reuse');
    assert.equal((await call('save_deal_line_quote', [deal,randomUUID(),{...args,start:start+1}])).error,'invalid_quote');
    assert.equal((await call('save_deal_line_quote', [deal,randomUUID(),{...args,end:null}])).error,'invalid_quote');
    const api = dealFileTools({});
    const partner = {id:joe,slug:'joe',human:true};
    const readText = await api['read-deal-quote-text'].handler(c,partner,{deal_id:deal,document_id:doc,content_hash:hash});
    assert.equal(readText.body,text);
    assert.equal(readText.offset_unit,'unicode_code_point');
    assert.equal(readText.text_hash.length,64);
    assert.deepEqual(await api['save-deal-line-quote'].handler(c,partner,{deal_id:deal,idempotency_key:saveKey,...args}),saved);
    assert.equal((await call('save_deal_line_quote',[deal,randomUUID(),{...args,comment:' '.repeat(4000)+'x'}])).error,'invalid_quote');
    assert.equal((await call('register_deal_quote_text',[doc,randomUUID(),hash,'x'.repeat(200001)])).error,'invalid_text_snapshot');
    const raceKey = randomUUID();
    const race = await Promise.all(Array.from({length:3},async () => {
      const other = new pg.Client(c.connectionParameters);
      await other.connect();
      try {
        await other.query("begin");
        await other.query("select set_config('carr.acting_actor_slug','joe',false),set_config('carr.verified_human_actor_slug','joe',false)");
        const result = await api['save-deal-line-quote'].handler(other,partner,{deal_id:deal,idempotency_key:raceKey,...args});
        await other.query('commit');
        return result;
      } finally { await other.end(); }
    }));
    assert.equal(new Set(race.map(r=>r.quote_id)).size,1);
    assert.equal(race[0].ok,true);
    const listed = await call('list_deal_line_quotes',[deal]);
    assert.equal(listed.quotes.length,2);
    listed.quotes = listed.quotes.filter(q=>q.id===saved.quote_id);
    assert.equal(listed.quotes[0].quoted_text,quoteText);
    assert.equal(listed.quotes[0].comment,args.comment);
    assert.equal(listed.quotes[0].author_id,joe);
    assert.equal(listed.quotes[0].version_changed,false);
    await c.query('update attachment set sha256=$1 where id=$2',['b'.repeat(64),doc]);
    const newer = await call('register_deal_quote_text',[doc,randomUUID(),'b'.repeat(64),'New rent is $25.']);
    assert.equal(newer.version,2);
    const historical = (await call('list_deal_line_quotes',[deal])).quotes.find(q=>q.id===saved.quote_id);
    assert.equal(historical.version_changed,true);
    assert.equal(historical.version_label,'from version 1');
    assert.equal(historical.quoted_text,quoteText);
    assert.equal(historical.snapshot_id,source.snapshot_id);
    const detachKey = randomUUID();
    const detached = await call('detach_deal_line_quote',[deal,saved.quote_id,detachKey]);
    assert.equal(detached.ok,true);
    assert.deepEqual(await call('detach_deal_line_quote',[deal,saved.quote_id,detachKey]),detached);
    assert.equal((await call('list_deal_line_quotes',[deal])).quotes.find(q=>q.id===saved.quote_id).state,'detached');
    for (let i=0;i<101;i++) {
      const receipt = await call('save_deal_line_quote',[deal,randomUUID(),{...args,comment:'Pagination '+i}]);
      assert.equal(receipt.ok,true);
    }
    const page = await api['list-deal-line-quotes'].handler(c,partner,{deal_id:deal});
    assert.equal(page.quotes.length,100);
    assert.equal(page.more,true);
    const next = await api['list-deal-line-quotes'].handler(c,partner,{deal_id:deal,cursor:page.next_cursor});
    assert.equal(next.quotes.length,3);
    assert.equal(next.more,false);
    assert.equal(new Set([...page.quotes,...next.quotes].map(q=>q.id)).size,103);
    await c.query("update deal set owner='dell',version=2 where id=$1",[deal]);
    for (const [fn,params] of [ ['list_deal_line_quotes',[deal]],['save_deal_line_quote',[deal,saveKey,args]],['detach_deal_line_quote',[deal,saved.quote_id,detachKey]] ]) {
      assert.equal((await call(fn,params)).error,'deal_not_found');
    }
    await c.query("update deal set owner='joe',version=1 where id=$1",[deal]);
    await c.query("select set_config('carr.verified_human_actor_slug','',false)");
    assert.equal((await call('save_deal_line_quote',[deal,saveKey,args])).error,'deal_not_found');
    await c.query("select set_config('carr.verified_human_actor_slug','joe',false)");
    await c.query('update attachment set deleted_at=now() where id=$1',[doc]);
    assert.equal((await call('list_deal_line_quotes',[deal])).quotes.length,0);
    assert.equal((await call('read_deal_quote_text',[deal,doc,hash])).error,'document_not_found');
    for (const role of ['carr_reader','carr_writer','dot_reader']) {
      await c.query(`set role ${role}`);
      await assert.rejects(c.query('select * from ops.deal_line_quote'),{code:'42501'});
      await c.query('reset role');
    }
  } finally { await db.close(); }
});
