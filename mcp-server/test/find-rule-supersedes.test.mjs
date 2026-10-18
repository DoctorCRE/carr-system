import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { TOOLS, executeRegisteredTool, ToolError } from '../src/tools.js';
import { requiresAuthorityConnection } from '../src/mcp.js';

const dsn=process.env.CARR_RULE_TEST_DATABASE_URL;
// Refuse unsafe configuration before any database test can construct a client.
if (dsn) assert.match(dsn,/^postgres(?:ql)?:\/\/[^@/]*@(?:127\.0\.0\.1|localhost):/);

const oldId = 'aabbccdd-0000-4000-8000-000000000001';
const newId = 'aabbccdd-0000-4000-8000-000000000002';
const machine = { id: '11111111-1111-4111-8111-111111111111', slug: 'codex', human: false };
const human = { ...machine, slug: 'joe', human: true };
const teachArgs = (supersedes = oldId) => ({ idempotency_key: randomUUID(),
  statement: 'Synthetic revised scope', human_quote: 'Synthetic correction',
  enforcement_home: 'core', ...(supersedes ? { supersedes } : {}) });
function store(status = 'proposed') {
  const calls = [], replay = new Map();
  return { calls, query: async (sql,p = []) => {
    calls.push({sql,p});
    if (sql.includes('select request_hash, response')) return { rows: replay.has(p[0]) ? [replay.get(p[0])] : [] };
    if (sql.includes('select id, status from rule')) return { rows: [{ id: oldId, status }] };
    if (sql.includes('insert into rule')) return { rows: [{ id: newId, personal_to: null }] };
    if (sql.includes('ops.retire_superseded_rule')) { status = 'retired'; return { rows: [{ result: { ok:true,
      rule_id:oldId,status,reason:`superseded by ${newId}`,superseded_by:newId } }] }; }
    if (sql.includes('insert into tool_call')) replay.set(p[0], { request_hash:p[3], response:JSON.parse(p[4]) });
    return { rows: [] };
  }};
}
test('lookup has the same read-only authority flags as standing-context', () => {
  for (const flag of ['write','humanOnly','authorityOnly','writerConnection'])
    assert.equal(TOOLS['find-rule'][flag], TOOLS['standing-context'][flag]);
  assert.equal(TOOLS['find-rule'].inputSchema.properties.limit.default,20);
});
test('lookup validates empty input and bounds before querying', async () => {
  const c=store();
  for (const args of [{text:'  '},{text:'phrase',limit:0},{text:'phrase',limit:101}])
    await assert.rejects(()=>TOOLS['find-rule'].handler(c,machine,args),ToolError);
  assert.equal(c.calls.length,0);
});
test('teach atomically requests retirement and replay never retires twice', async () => {
  const c=store(), args=teachArgs();
  const first=await TOOLS.teach.handler(c,machine,args);
  assert.equal(first.status,'proposed');
  assert.equal(first.supersedes,oldId);
  assert.equal(first.retirement.reason,`superseded by ${newId}`);
  assert.match(c.calls.find(x=>x.sql.includes('select id, status')).sql,/for update/);
  assert.deepEqual(await TOOLS.teach.handler(c,machine,args),{replayed:true,...first});
  assert.equal(c.calls.filter(x=>x.sql.includes('ops.retire_superseded_rule')).length,1);
});
test('machine cannot supersede an active rule, even with a human sponsor', async () => {
  const c=store('active');
  await assert.rejects(()=>TOOLS.teach.handler(c,{...machine,sponsoring_human_slug:'joe'},teachArgs()),
    e=>e instanceof ToolError && e.payload.error==='human_only');
  assert.equal(c.calls.some(x=>x.sql.includes('insert into rule')),false);
});
test('ordinary teach keeps its writer route; human supersession uses retirement authority', async () => {
  assert.equal(requiresAuthorityConnection(TOOLS.teach,machine,teachArgs()),false);
  assert.equal(requiresAuthorityConnection(TOOLS.teach,human,teachArgs()),true);
  assert.equal(requiresAuthorityConnection(TOOLS.teach,human,teachArgs(null)),false);
  const c=store(); const result=await TOOLS.teach.handler(c,machine,teachArgs(null));
  assert.equal(result.retirement,null);
  assert.equal(c.calls.some(x=>x.sql.includes('ops.retire_superseded_rule')),false);
});

test('real PostgreSQL: a proposed replacement cannot supersede itself',
  {skip:!dsn}, async () => {
  const {Client}=(await import('pg')).default;
  const c=new Client({connectionString:dsn}); await c.connect();
  const actorId=randomUUID(), replacement=randomUUID(), slug=`fixture-${actorId}`;
  try {
    await c.query('begin');
    await c.query("insert into actor(id,slug,kind,display_name) values($1,$2,'automation','Synthetic self-supersession actor')",
      [actorId,slug]);
    await c.query("select set_config('carr.acting_actor_slug',$1,true)",[slug]);
    await c.query('set local role carr_writer');
    await c.query('insert into rule(id,statement,taught_by,supersedes) values($1,$2,$3,$1)',
      [replacement,'Synthetic self-supersession',actorId]);
    await c.query('savepoint refusal');
    await assert.rejects(()=>c.query('select ops.retire_superseded_rule($1,$2)',[replacement,randomUUID()]),
      /cannot supersede itself/);
    await c.query('rollback to savepoint refusal');
    await c.query('reset role');
    assert.equal((await c.query('select status from rule where id=$1',[replacement])).rows[0].status,'proposed');
    assert.equal((await c.query('select count(*)::int as n from ops.rule_retirement_receipt where rule_id=$1',
      [replacement])).rows[0].n,0);
  } finally { await c.query('rollback'); await c.end(); }
});
test('real PostgreSQL: updating a committed replacement cannot withdraw its predecessor',
  {skip:!dsn}, async () => {
  const {Client}=(await import('pg')).default;
  const c=new Client({connectionString:dsn}); await c.connect();
  const actorId=randomUUID(), old=randomUUID(), replacement=randomUUID();
  const slug=`fixture-${actorId}`;
  try {
    await c.query('begin');
    await c.query("insert into actor(id,slug,kind,display_name) values($1,$2,'automation','Synthetic provenance actor')",
      [actorId,slug]);
    await c.query('insert into rule(id,statement,taught_by) values($1,$2,$3)',
      [old,'Synthetic committed predecessor',actorId]);
    await c.query('insert into rule(id,statement,taught_by,supersedes) values($1,$2,$3,$4)',
      [replacement,'Synthetic committed replacement',actorId,old]);
    await c.query('commit');
    await c.query('begin');
    await c.query("select set_config('carr.acting_actor_slug',$1,true)",[slug]);
    await c.query('set local role carr_writer');
    await c.query('update rule set statement=$1 where id=$2', ['Synthetic later update',replacement]);
    await c.query('savepoint refusal');
    await assert.rejects(()=>c.query('select ops.retire_superseded_rule($1,$2)',[replacement,randomUUID()]),
      /newly taught proposed replacement/);
    await c.query('rollback to savepoint refusal');
    await c.query('reset role');
    assert.equal((await c.query('select status from rule where id=$1',[old])).rows[0].status,'proposed');
    assert.equal((await c.query('select count(*)::int as n from ops.rule_retirement_receipt where rule_id=$1',
      [old])).rows[0].n,0);
  } finally {
    await c.query('rollback');
    // Only these committed, disposable synthetic fixtures need cleanup.
    await c.query('delete from rule where id=any($1::uuid[])',[[replacement,old]]);
    await c.query('delete from actor where id=$1',[actorId]);
    await c.end();
  }
});
test('real PostgreSQL: human supersession executes the full teach envelope under authority',
  {skip:!dsn}, async () => {
  const {Client}=(await import('pg')).default;
  const c=new Client({connectionString:dsn}); await c.connect();
  try {
    await c.query('begin');
    const joe=(await c.query("select id from actor where slug='joe'")).rows[0];
    const actor={id:joe.id,slug:'joe',human:true};
    const old=randomUUID();
    await c.query("select set_config('carr.acting_actor_slug','joe',true)");
    await c.query("select set_config('carr.verified_human_actor_slug','joe',true)");
    await c.query('insert into rule(id,statement,taught_by) values($1,$2,$3)',
      [old,'Synthetic human predecessor',joe.id]);
    assert.equal(requiresAuthorityConnection(TOOLS.teach,actor,teachArgs(old)),true);
    await c.query('set local role carr_authority');
    const args=teachArgs(old);
    const result=await executeRegisteredTool(c,actor,'teach',args);
    assert.equal(result.retirement.status,'retired');
    assert.deepEqual(await executeRegisteredTool(c,actor,'teach',args),{replayed:true,...result});
    await c.query('reset role');
    assert.equal((await c.query('select status from rule where id=$1',[old])).rows[0].status,'retired');
    assert.equal((await c.query('select state from ops.guidance_intake where source_ref=$1',
      [`rule:${result.rule_id}`])).rows[0].state,'captured');
  } finally { await c.query('rollback'); await c.end(); }
});
test('real PostgreSQL: proposed phrase lookup, literal wildcards, retirement, replay and rollback',
  {skip:!dsn},async () => {
  const {Client}=(await import('pg')).default; const c=new Client({connectionString:dsn}); await c.connect();
  const actorId=randomUUID(), old=randomUUID(), phrase=`Synthetic phrase ${randomUUID()}`;
  const actor={...machine,id:actorId};
  try {
    await c.query('begin');
    await c.query("insert into actor(id,slug,kind,display_name) values($1,$2,'automation','Synthetic test actor')",
      [actorId,`fixture-${actorId}`]);
    await c.query("select set_config('carr.acting_actor_slug',$1,true)",[`fixture-${actorId}`]);
    await c.query('insert into rule(id,statement,human_quote,taught_by) values($1,$2,$3,$4)',
      [old,`${phrase} 100%_literal ${'x'.repeat(250)}`,'Synthetic quote',actorId]);
    const privateId=randomUUID();
    await c.query('insert into rule(id,statement,taught_by,personal_to) values($1,$2,$3,$3)',
      [privateId,`${phrase} private`,actorId]);
    await c.query('set local role carr_reader');
    const found=await executeRegisteredTool(c,actor,'find-rule',{text:phrase.toUpperCase()});
    await c.query('reset role');
    const row=found.rules.find(r=>r.id===old); assert.ok(row);
    assert.equal(found.rules.some(r=>r.id===privateId),false);
    assert.equal(row.short_id,old.slice(0,8)); assert.equal(row.status,'proposed');
    assert.equal(row.version,1); assert.ok(row.created_at); assert.deepEqual(row.scope,{});
    assert.equal(row.statement.length,200);
    assert.ok((await TOOLS['find-rule'].handler(c,actor,{text:`${phrase.split(' ').at(-1)} Synthetic`})).rules.some(r=>r.id===old));
    assert.ok((await TOOLS['find-rule'].handler(c,actor,{text:'100%_literal'})).rules.some(r=>r.id===old));
    assert.equal((await TOOLS['find-rule'].handler(c,actor,{text:phrase,status:'active'})).rules.length,0);
    const args={...teachArgs(old.slice(0,8)),statement:`${phrase} revised`};
    await c.query('set local role carr_writer');
    const result=await executeRegisteredTool(c,actor,'teach',args);
    await c.query('reset role');
    const prior=(await c.query('select status,version,statement from rule where id=$1',[old])).rows[0];
    assert.equal(prior.status,'retired'); assert.equal(prior.version,2);
    const receipt=(await c.query('select reason,superseded_by from ops.rule_retirement_receipt where rule_id=$1',[old])).rows[0];
    assert.equal(receipt.reason,`superseded by ${result.rule_id}`); assert.equal(receipt.superseded_by,result.rule_id);
    assert.equal((await c.query('select supersedes,status from rule where id=$1',[result.rule_id])).rows[0].supersedes,old);
    assert.deepEqual(await TOOLS.teach.handler(c,actor,args),{replayed:true,...result});
    assert.equal((await TOOLS['find-rule'].handler(c,actor,{text:phrase,status:'retired'})).rules.length,1);
    const second=randomUUID(), failureKey=randomUUID();
    await c.query('insert into rule(id,statement,taught_by) values($1,$2,$3)',[second,phrase,actorId]);
    await c.query(`insert into ops.authority_receipt
      (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
      select $1,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs
        from ops.authority_receipt where idempotency_key=$2`,
      [`retirement:${failureKey}`,`retirement:${args.idempotency_key}`]);
    await c.query('savepoint failure');
    await assert.rejects(()=>TOOLS.teach.handler(c,actor,{...teachArgs(second),idempotency_key:failureKey}),
      e=>e.code==='23505');
    await c.query('rollback to savepoint failure');
    assert.equal((await c.query('select status from rule where id=$1',[second])).rows[0].status,'proposed');
    assert.equal((await c.query('select count(*)::int as n from rule where taught_by=$1',[actorId])).rows[0].n,4);
    assert.equal((await c.query('select count(*)::int as n from ops.rule_retirement_receipt where rule_id=$1',[second])).rows[0].n,0);

  } finally { await c.query('rollback'); await c.end(); }
});
