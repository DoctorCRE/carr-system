// Test rule lookup and replacement safeguards, including PostgreSQL behavior.
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { TOOLS, executeRegisteredTool, ToolError } from '../src/tools.js';

const dsn=process.env.CARR_RULE_TEST_DATABASE_URL;
// Refuse unsafe configuration before any database test can construct a client.
if (dsn) assert.match(dsn,/^postgres(?:ql)?:\/\/[^@/]*@(?:127\.0\.0\.1|localhost):/);

const oldId = 'aabbccdd-0000-4000-8000-000000000001';
const newId = 'aabbccdd-0000-4000-8000-000000000002';
const machine = { id: '11111111-1111-4111-8111-111111111111', slug: 'codex', human: false };
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
test('real PostgreSQL: proposed phrase lookup and literal wildcards',
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

  } finally { await c.query('rollback'); await c.end(); }
});
