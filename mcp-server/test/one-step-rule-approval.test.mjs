import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { executeRegisteredTool, ToolError } from '../src/tools.js';

// One step: a taught gate rule whose carrying_control is installed and verified
// goes live on a single approve-rule call, with no separate admit-rule.
const dsn = process.env.CARR_RULE_TEST_DATABASE_URL;
if (dsn) assert.match(dsn, /^postgres(?:ql)?:\/\/[^@/]*@(?:127\.0\.0\.1|localhost):/);

async function fixture(run) {
  const { Client } = (await import('pg')).default;
  const c = new Client({ connectionString: dsn });
  await c.connect();
  try {
    await c.query('begin');
    for (const role of ['carr_authority_joe', 'carr_authority_dell']) {
      await c.query(`do $$ begin
        if not exists (select 1 from pg_roles where rolname='${role}') then create role ${role} login; end if;
      end $$`);
      await c.query(`grant carr_authority to ${role}`);
    }
    const control = `one-step-fixture-${randomUUID()}`;
    await c.query(`insert into ops.enforcement_control_catalog
        (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
      values ($1,'one-step fixture','mcp-server/test/one-step-rule-approval.test.mjs','deny_gate',true,now())`,
      [control]);
    const joe = { ...(await c.query("select id,slug from actor where slug='joe' and kind='human' and active")).rows[0], human: true };
    assert.ok(joe.id, 'Joe actor fixture is missing');
    await c.query('set session authorization carr_authority_joe');
    await c.query("select set_config('carr.acting_actor_slug','joe',true)");
    await run(c, joe, control);
  } finally {
    await c.query('rollback');
    await c.end();
  }
}

const teachGate = (c, joe, control) => executeRegisteredTool(c, joe, 'teach', {
  idempotency_key: randomUUID(), statement: `Synthetic one-step gate rule ${randomUUID()}`,
  human_quote: 'Synthetic approval request', enforcement_home: 'gate', carrying_control: control,
});
const approveArgs = rule_id => ({ rule_id, idempotency_key: randomUUID(), reason: 'Joe approves this rule' });

test('real PostgreSQL: teach a gate rule, then one approve-rule makes it live', { skip: !dsn }, async () => fixture(async (c, joe, control) => {
  const { rule_id } = await teachGate(c, joe, control);
  const args = approveArgs(rule_id);
  const approved = await executeRegisteredTool(c, joe, 'approve-rule', args);
  assert.equal(approved.policy_status, 'active');
  assert.equal(approved.enforcement_status, 'hard_enforced');
  assert.deepEqual(approved.installed_controls, [control]);
  assert.deepEqual(await executeRegisteredTool(c, joe, 'approve-rule', args), { replayed: true, ...approved });
  await c.query('reset session authorization');
  assert.equal((await c.query('select status from rule where id=$1', [rule_id])).rows[0].status, 'active');
  assert.deepEqual((await c.query('select load_layer,packs from ops.rule_load_layer where rule_id=$1', [rule_id])).rows[0],
    { load_layer: 'control', packs: [] });
  assert.deepEqual((await c.query(`select kind from ops.authority_receipt where subject_id=$1 order by kind`, [rule_id]))
    .rows.map(r => r.kind), ['activation', 'admission']);
}));

test('real PostgreSQL: a gate rule whose control is not installed stays proposed', { skip: !dsn }, async () => fixture(async (c, joe) => {
  const { rule_id } = await teachGate(c, joe, `missing-control-${randomUUID()}`);
  await c.query('savepoint refusal');
  await assert.rejects(() => executeRegisteredTool(c, joe, 'approve-rule', approveArgs(rule_id)), /not installed|missing/i);
  await c.query('rollback to savepoint refusal');
  await c.query('reset session authorization');
  assert.equal((await c.query('select status from rule where id=$1', [rule_id])).rows[0].status, 'proposed');
  assert.equal((await c.query('select count(*)::int n from ops.rule_admission where rule_id=$1', [rule_id])).rows[0].n, 0);
}));

test('real PostgreSQL: only Joe authority can approve, never Dell or the writer role', { skip: !dsn }, async () => fixture(async (c, joe, control) => {
  const { rule_id } = await teachGate(c, joe, control);
  for (const [role, refusal] of [['carr_authority_dell', /Joe authority/i], ['carr_writer', /permission denied/i]]) {
    await c.query('reset session authorization');
    await c.query('savepoint refusal');
    await c.query(`set session authorization ${role}`);
    await assert.rejects(() => c.query('select ops.approve_rule($1,null,null,$2,$3)',
      [rule_id, randomUUID(), 'not Joe']), refusal);
    await c.query('rollback to savepoint refusal');
  }
  await c.query('reset session authorization');
  assert.equal((await c.query('select status from rule where id=$1', [rule_id])).rows[0].status, 'proposed');
}));

test('approve-rule refuses an unattended runner before the database', async () => {
  const c = { query: async () => assert.fail('an unattended runner reached the database') };
  await assert.rejects(() => executeRegisteredTool(c, { id: randomUUID(), slug: 'codex', human: false, unattended: true },
    'approve-rule', approveArgs(randomUUID())),
  e => e instanceof ToolError && e.payload.error === 'unattended_runner_authority_not_admitted');
});
