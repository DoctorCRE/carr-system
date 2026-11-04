import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { readFileSync } from 'node:fs';
import { Validator } from '@cfworker/json-schema';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';

let fixture, pool, joe, dell;
const shutdowns = [];
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'doc-request-', snapshot: true });
  pool = new pg.Pool(fixture.connection);
  pool.on('connect', c => shutdowns.push(new Promise(resolve => c.once('end', resolve))));
  for (const slug of ['joe', 'dell']) {
    const actor = (await pool.query("insert into actor(slug,kind,display_name) values($1,'human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug", [slug])).rows[0];
    if (slug === 'joe') joe = { ...actor, human: true }; else dell = { ...actor, human: true };
  }
});
after(async () => {
  try { await pool?.end(); await Promise.all(shutdowns); } finally { await fixture?.close(); }
});
async function setup() {
  const args = { idempotency_key: randomUUID(), conversation_id: randomUUID(), body: 'Synthetic queued ask' };
  await pool.query('insert into ops.doc_conversation(id,title,created_by_actor) values($1,$2,$3)', [args.conversation_id, 'Queued ask test', joe.id]);
  return args;
}
async function submit(args, actor = joe, { verified = actor.slug, direct = false, registered = false } = {}) {
  const c = await pool.connect();
  try {
    await c.query('begin');
    await c.query('set local role carr_writer');
    await c.query("select set_config('carr.acting_actor_slug',$1,true), set_config('carr.verified_human_actor_slug',$2,true)", [actor.slug, verified]);
    const result = direct
      ? (await c.query('select ops.submit_doc_request($1::text,$2::text,$3::text) as result', [args.idempotency_key, args.conversation_id, args.body])).rows[0].result
      : registered ? await executeRegisteredTool(c, actor, 'submit-doc-request', args)
      : await TOOLS['submit-doc-request'].handler(c, actor, args);
    await c.query('commit');
    return result;
  } catch (e) { await c.query('rollback'); throw e; } finally { c.release(); }
}
async function counts(args, n) {
  assert.equal((await pool.query('select count(*)::int n from ops.doc_conversation_turn where msg_id=$1', [args.idempotency_key])).rows[0].n, n);
  assert.equal((await pool.query('select count(*)::int n from ops.doc_request_receipt where idempotency_key=$1', [args.idempotency_key])).rows[0].n, n);
}
function accepted(args) {
  return { ok: true, contract: 'doc-request.v1', status: 'accepted', conversation_id: args.conversation_id, idempotency_key: args.idempotency_key, request_msg_id: args.idempotency_key };
}
function refused(result, reason) {
  assert.equal(result.ok, true);
  assert.equal(result.contract, 'doc-request.v1');
  assert.equal(result.status, 'refused');
  assert.equal(result.reason, reason);
}
test('partner submission stores one ask and contract receipt', async () => {
  const args = await setup();
  const result = await submit(args, joe, { registered: true });
  assert.deepEqual(result, accepted(args));
  const contract = JSON.parse(readFileSync(new URL('../contracts/doc-request.v1.json', import.meta.url)));
  assert.equal(new Validator(contract.$defs.response).validate(result).valid, true);
  await counts(args, 1);
  const turn = (await pool.query('select role,body,origin_actor,origin_channel from ops.doc_conversation_turn where msg_id=$1', [args.idempotency_key])).rows[0];
  assert.deepEqual(turn, { role: 'human', body: args.body, origin_actor: 'joe', origin_channel: 'browser-human' });
});
test('immutable replay and body or conversation mismatch', async () => {
  const args = await setup();
  const result = await submit(args);
  assert.deepEqual(await submit(args), result);
  refused(await submit({ ...args, body: 'Changed' }), 'doc_request_key_reuse');
  const other = await setup();
  refused(await submit({ ...args, conversation_id: other.conversation_id }), 'doc_request_key_reuse');
  await counts(args, 1);
  assert.equal((await pool.query('select count(*)::int n from ops.doc_conversation_turn where conversation_id=$1', [other.conversation_id])).rows[0].n, 0);
  await assert.rejects(pool.query('update ops.doc_request_receipt set body=$1 where idempotency_key=$2', ['Changed', args.idempotency_key]), /immutable/);
});
test('concurrent duplicate submissions store one ask', async () => {
  const args = await setup();
  const results = await Promise.all(Array.from({ length: 4 }, () => submit(args)));
  for (const result of results) assert.deepEqual(result, accepted(args));
  await counts(args, 1);
});
test('distinct keys allocate distinct consecutive turns', async () => {
  const args = await setup();
  const other = { ...args, idempotency_key: randomUUID() };
  await Promise.all([submit(args), submit(other)]);
  await counts(args, 1); await counts(other, 1);
  assert.deepEqual((await pool.query('select sequence from ops.doc_conversation_turn where conversation_id=$1 order by sequence', [args.conversation_id])).rows.map(x => x.sequence), [0, 1]);
});
test('unauthorized and revoked access refuse before writes including replay', async () => {
  const args = await setup();
  refused(await submit(args, dell), 'doc_conversation_not_found');
  await counts(args, 0);
  await pool.query('insert into ops.doc_conversation_grant(conversation_id,grantee_actor,granted_by_actor) values($1,$2,$3)', [args.conversation_id, dell.id, joe.id]);
  await submit(args, dell);
  await pool.query('update ops.doc_conversation_grant set revoked_at=now() where conversation_id=$1', [args.conversation_id]);
  refused(await submit(args, dell), 'doc_conversation_not_found');
  const next = { ...args, idempotency_key: randomUUID() };
  refused(await submit(next, dell), 'doc_conversation_not_found');
  await counts(args, 1); await counts(next, 0);
});
test('inactive and unverified actors and non-partners cannot submit directly', async () => {
  const args = await setup();
  refused(await submit(args, joe, { verified: '', direct: true }), 'doc_request_partner_required');
  await pool.query('update actor set active=false where id=$1', [joe.id]);
  try { refused(await submit(args, joe, { direct: true }), 'doc_request_partner_required'); }
  finally { await pool.query('update actor set active=true where id=$1', [joe.id]); }
  const agent = (await pool.query("insert into actor(slug,kind,display_name) values('request-agent','automation','Synthetic agent') returning id,slug")).rows[0];
  refused(await submit(args, agent, { verified: 'joe', direct: true }), 'doc_request_partner_required');
  await counts(args, 0);
});
test('receipt insert failure rolls back ask and receipt together', async () => {
  const args = await setup();
  await pool.query(`create function ops.test_doc_request_failure() returns trigger language plpgsql as $$ begin raise exception 'synthetic receipt failure'; end $$;
    create trigger test_doc_request_failure before insert on ops.doc_request_receipt for each row execute function ops.test_doc_request_failure()`);
  try { await assert.rejects(submit(args), /synthetic receipt failure/); }
  finally { await pool.query('drop trigger test_doc_request_failure on ops.doc_request_receipt; drop function ops.test_doc_request_failure()'); }
  await counts(args, 0);
  assert.deepEqual(await submit(args), accepted(args));
});
test('partner-only registration and invalid body or key refuse without writes', async () => {
  const tool = TOOLS['submit-doc-request'];
  assert.equal(tool.write, true); assert.equal(tool.humanOnly, true); assert.equal(tool.humanCallerOnly, true);
  assert.equal(tool.authorityOnly, undefined);
  assert.deepEqual(Object.keys(tool.inputSchema.properties).sort(), ['body', 'conversation_id', 'deal_id', 'idempotency_key', 'quote_ids']);
  const args = await setup();
  refused(await submit({ ...args, body: '   ' }), 'doc_conversation_body_invalid');
  refused(await submit({ ...args, idempotency_key: 'invalid' }), 'doc_request_key_invalid');
  await counts(args, 0);
});

test('registered dispatcher rejects machines and caller-controlled provenance before database use', async () => {
  const args = await setup();
  const c = { query() { throw new Error('unexpected database access'); } };
  await assert.rejects(executeRegisteredTool(c, { id: randomUUID(), slug: 'codex', human: false }, 'submit-doc-request', args), /human_only_verb_requires_verified_partner/);
  await assert.rejects(executeRegisteredTool(c, joe, 'submit-doc-request', { ...args, role: 'assistant' }));
  await assert.rejects(executeRegisteredTool(c, joe, 'submit-doc-request', { ...args, actor: 'dell' }));
  await counts(args, 0);
});
test('cross-actor key collision refuses instead of replaying another actor receipt', async () => {
  const args = await setup();
  await pool.query('insert into ops.doc_conversation_grant(conversation_id,grantee_actor,granted_by_actor) values($1,$2,$3)', [args.conversation_id, dell.id, joe.id]);
  await submit(args);
  refused(await submit(args, dell), 'doc_request_key_reuse');
  await counts(args, 1);
});
test('writer cannot edit receipts or call the authority append; reader cannot submit', async () => {
  const privileges = (await pool.query("select has_table_privilege('carr_writer','ops.doc_request_receipt','insert') i, has_table_privilege('carr_writer','ops.doc_request_receipt','update') u, has_function_privilege('carr_writer','ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid,jsonb)','execute') a, has_function_privilege('carr_reader','ops.submit_doc_request(text,text,text)','execute') r")).rows[0];
  assert.deepEqual(privileges, { i: false, u: false, a: false, r: false });
});

test('frozen uppercase identifiers echo exactly for the app consumer and replay unchanged', async () => {
  const args = await setup();
  args.conversation_id = args.conversation_id.toUpperCase();
  args.idempotency_key = args.idempotency_key.toUpperCase();
  assert.deepEqual(await submit(args), accepted(args));
  assert.deepEqual(await submit(args), accepted(args));
  refused(await submit({ ...args, idempotency_key: args.idempotency_key.toLowerCase() }), 'doc_request_key_reuse');
  await counts(args, 1);
});

for (const firstWriter of ['ask', 'answer']) {
  test(`concurrent ${firstWriter} first serializes queued ask and assistant answer`, async () => {
    const args = await setup();
    await submit({ ...args, idempotency_key: randomUUID(), body: 'Previous synthetic ask' });
    const answerArgs = { conversation_id: args.conversation_id, idempotency_key: randomUUID(),
      msg_id: randomUUID(), role: 'assistant', body: 'Synthetic answer' };
    const clients = { ask: await pool.connect(), answer: await pool.connect() };
    let pending;
    try {
      for (const [writer, c] of Object.entries(clients)) {
        await c.query('begin');
        await c.query(`set local role ${writer === 'ask' ? 'carr_writer' : 'carr_authority'}`);
        await c.query("set local statement_timeout = '10s'");
        await c.query("select set_config('carr.acting_actor_slug','joe',true), set_config('carr.verified_human_actor_slug','joe',true)");
      }
      const write = writer => executeRegisteredTool(clients[writer], joe,
        writer === 'ask' ? 'submit-doc-request' : 'add-doc-conversation-turn', writer === 'ask' ? args : answerArgs);
      const secondWriter = firstWriter === 'ask' ? 'answer' : 'ask';
      const first = await write(firstWriter);
      pending = write(secondWriter).then(result => ({ result }), error => ({ error }));
      let blocked = false;
      for (let attempt = 0; attempt < 500; attempt++) {
        const row = (await pool.query('select $1::int = any(pg_blocking_pids($2)) as blocked',
          [clients[firstWriter].processID, clients[secondWriter].processID])).rows[0];
        if (row.blocked) { blocked = true; break; }
        await new Promise(resolve => setTimeout(resolve, 10));
      }
      assert.equal(blocked, true, 'second writer must wait for the first transaction');
      await clients[firstWriter].query('commit');
      const second = await pending;
      if (second.error) throw second.error;
      await clients[secondWriter].query('commit');
      const ask = firstWriter === 'ask' ? first : second.result;
      const answer = firstWriter === 'answer' ? first : second.result;
      assert.deepEqual(ask, accepted(args));
      assert.equal(answer.ok, true);
      await counts(args, 1);
      assert.deepEqual((await pool.query('select sequence,role,body from ops.doc_conversation_turn where conversation_id=$1 order by sequence',
        [args.conversation_id])).rows, [
        { sequence: 0, role: 'human', body: 'Previous synthetic ask' },
        ...(firstWriter === 'ask'
          ? [{ sequence: 1, role: 'human', body: args.body }, { sequence: 2, role: 'assistant', body: answerArgs.body }]
          : [{ sequence: 1, role: 'assistant', body: answerArgs.body }, { sequence: 2, role: 'human', body: args.body }]),
      ]);
    } finally {
      // Release the first transaction before awaiting a possibly blocked second writer.
      await clients[firstWriter].query('rollback');
      await pending;
      for (const c of Object.values(clients)) { await c.query('rollback'); c.release(); }
    }
  });
}

test('assistant rollback releases allocation for the queued ask', async () => {
  const args = await setup();
  const c = await pool.connect();
  try {
    await c.query('begin');
    await c.query('set local role carr_authority');
    await c.query("select set_config('carr.acting_actor_slug','joe',true), set_config('carr.verified_human_actor_slug','joe',true)");
    const result = await executeRegisteredTool(c, joe, 'add-doc-conversation-turn', {
      conversation_id: args.conversation_id, idempotency_key: randomUUID(), msg_id: randomUUID(),
      role: 'assistant', body: 'Synthetic rolled back answer',
    });
    assert.equal(result.ok, true);
    await c.query('rollback');
    assert.deepEqual(await submit(args), accepted(args));
    assert.deepEqual((await pool.query('select sequence,role from ops.doc_conversation_turn where conversation_id=$1',
      [args.conversation_id])).rows, [{ sequence: 0, role: 'human' }]);
  } finally { await c.query('rollback'); c.release(); }
});

async function quoteSetup() {
  const args = await setup();
  const party = (await pool.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic quote partner',$1,$1) returning id", [joe.id])).rows[0].id;
  await pool.query("insert into client_status(slug,label) values('fixture_quote','Synthetic') on conflict do nothing");
  const client = (await pool.query("insert into client(party_id,status,created_by,updated_by) values($1,'fixture_quote',$2,$2) returning id", [party,joe.id])).rows[0].id;
  args.deal_id = (await pool.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic quote deal',$1,'purchase','site_selection','joe',$2,$2) returning id", [client,joe.id])).rows[0].id;
  const doc = (await pool.query("insert into attachment(subject_type,subject_id,r2_key,filename,mime,sha256,bytes,created_by) values('deal',$1,gen_random_uuid()::text,'lease.txt','text/plain',$2,40,$3) returning id", [args.deal_id,'a'.repeat(64),joe.id])).rows[0].id;
  const snapshot = randomUUID();
  await pool.query("insert into ops.deal_quote_text(id,document_id,deal_id,content_hash,version,body,actor_slug,idempotency_key) values($1,$2,$3,$4,1,'Exact clause 😀','joe',$5)", [snapshot,doc,args.deal_id,'a'.repeat(64),randomUUID()]);
  const quotes = [];
  for (const comment of ['Confirm rent.', 'Confirm term.']) {
    const id = randomUUID();
    await pool.query("insert into ops.deal_line_quote(id,deal_id,snapshot_id,start_offset,end_offset,quoted_text,comment,author_id,idempotency_key,request,result) values($1,$2,$3,0,14,'Exact clause 😀',$4,$5,$6,'{}','{}')", [id,args.deal_id,snapshot,comment,joe.id,randomUUID()]);
    quotes.push({quote_id:id,quoted_text:'Exact clause 😀',comment});
  }
  args.quote_ids = quotes.map(q=>q.quote_id);
  return {args,quotes};
}
async function readAsk(args) {
  const c = await pool.connect();
  try {
    await c.query('begin');
    await c.query('set local role carr_writer');
    await c.query("select set_config('carr.acting_actor_slug','joe',true),set_config('carr.verified_human_actor_slug','joe',true)");
    return await TOOLS['read-doc-conversation'].handler(c,joe,{conversation_id:args.conversation_id});
  } finally { await c.query('rollback'); c.release(); }
}
test('quote ask freezes ordered exact clauses and comments in acceptance and read', async()=> {
  const {args,quotes} = await quoteSetup();
  const result = await submit(args,joe,{registered:true});
  assert.equal(result.status,'accepted');
  const contract = JSON.parse(readFileSync(new URL('../contracts/doc-request.v1.json', import.meta.url)));
  assert.equal(new Validator(contract.$defs.response).validate(result).valid,true);
  assert.deepEqual(result.line_quotes,quotes);
  assert.equal(result.deal_id,args.deal_id);
  const turn = (await readAsk(args)).turns[0];
  assert.deepEqual(turn.line_quotes,quotes);
  assert.equal(turn.deal_id,args.deal_id);
  assert.ok(turn.body.includes(quotes[0].quoted_text));
  assert.ok(turn.body.includes(quotes[1].comment));
});
test('quote ask immutable replay never consumes later edits saves or detaches', async()=> {
  const {args,quotes} = await quoteSetup();
  const result = await submit(args);
  assert.deepEqual(result.line_quotes,quotes);
  await pool.query(`insert into ops.deal_line_quote(deal_id,snapshot_id,start_offset,end_offset,quoted_text,comment,author_id,idempotency_key,request,result)
    select deal_id,snapshot_id,start_offset,end_offset,quoted_text,'Later save',author_id,$2,request,result
    from ops.deal_line_quote where id=$1`,[args.quote_ids[0],randomUUID()]);
  await pool.query("update ops.deal_line_quote set quoted_text='Edited',comment='Edited' where id=$1",[args.quote_ids[0]]);
  await pool.query("insert into ops.deal_line_quote_detach values($1,$2,$3,'{}')",[joe.id,randomUUID(),args.quote_ids[1]]);
  assert.deepEqual(await submit(args),result);
  assert.deepEqual((await readAsk(args)).turns[0].line_quotes,quotes);
  refused(await submit({...args,quote_ids:[args.quote_ids[0]]}),'doc_request_key_reuse');
  refused(await submit({...args,deal_id:randomUUID()}),'doc_request_deal_not_found');
});
test('quote selections refuse foreign detached missing duplicate and oversized IDs',async()=> {
  const {args} = await quoteSetup();
  for (const quote_ids of [[randomUUID()],[args.quote_ids[0],args.quote_ids[0]],Array.from({length:21},()=>randomUUID())]) {
    refused(await submit({...args,quote_ids}),'doc_request_quotes_invalid');
  }
  refused(await submit({...args,deal_id:undefined}),'doc_request_quotes_invalid');
  const other = await quoteSetup();
  refused(await submit({...args,quote_ids:other.args.quote_ids}),'doc_request_quotes_invalid');
  await pool.query("insert into ops.deal_line_quote_detach values($1,$2,$3,'{}')",[joe.id,randomUUID(),args.quote_ids[0]]);
  refused(await submit(args),'doc_request_quotes_invalid');
  await counts(args,0);
});
test('quote ask reads submissions and replays refuse revoked deal access',async()=> {
  const {args,quotes} = await quoteSetup();
  assert.deepEqual((await submit(args)).line_quotes,quotes);
  await pool.query("update deal set owner='dell',version=version+1 where id=$1",[args.deal_id]);
  refused(await submit(args),'doc_request_deal_not_found');
  refused(await submit({...args,idempotency_key:randomUUID()}),'doc_request_deal_not_found');
  await assert.rejects(readAsk(args),/doc_conversation_not_found/);
});
test('concurrent asks on one deal own independent frozen selections',async()=> {
  const {args,quotes} = await quoteSetup();
  const requests = quotes.map(q=>({...args,idempotency_key:randomUUID(),quote_ids:[q.quote_id]}));
  const results = await Promise.all(requests.map(r=>submit(r)));
  assert.deepEqual(results.map(r=>r.line_quotes),quotes.map(q=>[q]));
  assert.equal((await readAsk(args)).turns.length,2);
});
test('cancelled submission rolls back frozen quotes and retry accepts once',async()=> {
  const {args,quotes} = await quoteSetup();
  await counts(args,0);
  const c = await pool.connect();
  try {
    await c.query('begin');
    await c.query('set local role carr_writer');
    await c.query("select set_config('carr.acting_actor_slug','joe',true),set_config('carr.verified_human_actor_slug','joe',true)");
    assert.deepEqual((await TOOLS['submit-doc-request'].handler(c,joe,args)).line_quotes,quotes);
    await c.query('rollback');
  } finally { c.release(); }
  await counts(args,0);
  assert.deepEqual((await submit(args)).line_quotes,quotes);
  await counts(args,1);
});

test('combined ask and frozen quote body refuses overflow without partial writes',async()=> {
  const {args} = await quoteSetup();
  args.body = 'x'.repeat(20000);
  refused(await submit(args),'doc_conversation_body_invalid');
  await counts(args,0);
});
test('direct SQL quote entry validates selection shape and requires current deal decision',async()=> {
  const {args} = await quoteSetup();
  const c = await pool.connect();
  try {
    await c.query('begin');
    await c.query('set local role carr_writer');
    await c.query("select set_config('carr.acting_actor_slug','joe',true),set_config('carr.verified_human_actor_slug','joe',true)");
    const call = async quotes => (await c.query('select ops.submit_doc_request($1,$2,$3,$4::uuid,$5::jsonb) r',
      [args.idempotency_key,args.conversation_id,args.body,args.deal_id,JSON.stringify(quotes)])).rows[0].r;
    for (const value of [null,{},[null],['invalid'],[args.quote_ids[0],args.quote_ids[0].toUpperCase()]]) {
      refused(await call(value),'doc_request_quotes_invalid');
    }
    refused(await call(args.quote_ids),'doc_request_deal_not_found');
    await c.query('rollback');
  } finally { c.release(); }
  await counts(args,0);
});
