// Real registered handler, envelope and event writes on disposable PostgreSQL.
// Committed race fixtures stay only in the loopback database CI discards.
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { TOOLS } from '../src/tools.js';
import { setWriterActorContext } from '../src/mcp.js';

const DSN = process.env.CARR_INVOICE_TEST_DATABASE_URL || '';
const REQUIRED = process.env.CARR_INVOICE_TEST_REQUIRED === '1';
const VERB = 'record-commission-receipt';
const enabled = { skip: !DSN && !REQUIRED, timeout: 20000 };

async function connect() {
  assert.ok(DSN, 'receipt transaction proof requires a disposable database');
  assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(DSN).hostname),
    'receipt transaction proof refuses a non-loopback database');
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  await client.query("set statement_timeout='10s'");
  return client;
}

async function fixture(c, patch = {}) {
  const a = (await c.query("select id from actor where slug='joe'")).rows[0];
  assert.ok(a, 'disposable schema must seed the Joe actor');
  const actor = { id: a.id, slug: 'joe', human: true, via: 'invoice-test', correlation_id: randomUUID() };
  const party = (await c.query(`insert into party(kind,name,created_by,updated_by)
    values('org',$1,$2,$2) returning id`, [`Invoice Test ${randomUUID()}`, actor.id])).rows[0];
  const client = (await c.query(`insert into client(party_id,created_by,updated_by)
    values($1,$2,$2) returning id`, [party.id, actor.id])).rows[0];
  const deal = (await c.query(`insert into deal(client_id,name,deal_type,phase,outcome,closed_on,lane,won_value,created_by,updated_by)
    values($1,'Synthetic Invoice Test','renewal','closed','won',current_date-10,'territory',987654,$2,$2) returning id`, [client.id, actor.id])).rows[0];
  const commission = (await c.query(`insert into commission(deal_id,gross_amount,status,invoiced_on,due_on,created_by)
    values($1,1000,$2,current_date-5,current_date+10,$3) returning id,version,
    to_jsonb(current_date)#>>'{}' as today`, [deal.id, patch.status || 'invoiced', actor.id])).rows[0];
  if (patch.noInvoiceDate) await c.query('update commission set invoiced_on=null where id=$1', [commission.id]);
  if (patch.noInvoiceDate) commission.version += 1;
  const installment = (await c.query(`insert into commission(deal_id,gross_amount,status,created_by)
    values($1,2000,'expected',$2) returning id`, [deal.id, actor.id])).rows[0];
  return { actor, deal, installment, commission, args: { idempotency_key: randomUUID(),
    commission_id: commission.id, base_version: commission.version, received_on: commission.today } };
}

async function begin(c, actor) {
  await c.query('begin');
  await c.query('set local role carr_writer');
  await setWriterActorContext(c, actor);
}
async function call(c, f, args = f.args, query = c) {
  await begin(c, f.actor);
  try {
    const result = await TOOLS[VERB].handler(query, f.actor, args);
    await c.query('commit');
    return result;
  } catch (error) {
    await c.query('rollback');
    throw error;
  }
}
async function durable(c, f) {
  const commission = (await c.query(`select status,version,gross_amount::text,
    to_jsonb(received_on)#>>'{}' as received_on from commission where id=$1`, [f.commission.id])).rows[0];
  const event = (await c.query('select * from event where idempotency_key=$1', [f.args.idempotency_key])).rows;
  const envelope = (await c.query('select * from tool_call where idempotency_key=$1', [f.args.idempotency_key])).rows;
  return { commission, event, envelope };
}
async function assertUntouched(c, f) {
  const state = await durable(c, f);
  assert.equal(state.commission.status, 'invoiced');
  assert.equal(state.commission.version, f.commission.version);
  assert.equal(state.commission.received_on, null);
  assert.equal(state.event.length, 0);
  assert.equal(state.envelope.length, 0);
}
async function waitForLock(observer, pid) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const row = (await observer.query('select wait_event_type from pg_stat_activity where pid=$1', [pid])).rows[0];
    if (row?.wait_event_type === 'Lock') return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.fail('overlapping receipt call did not wait on a database lock');
}

test('registered receipt sequential replay and changed-payload reuse preserve one attributed receipt', enabled, async () => {
  const c = await connect();
  try {
    const f = await fixture(c);
    const first = await call(c, f);
    assert.deepEqual(await call(c, f), { replayed: true, ...first });
    await assert.rejects(call(c, f, { ...f.args, received_on: '2026-01-01' }), e => e.payload?.error === 'key_reuse');
    const state = await durable(c, f);
    assert.equal(state.commission.version, f.args.base_version + 1);
    assert.equal(state.commission.status, 'received');
    assert.equal(state.commission.received_on, f.args.received_on);
    assert.equal(state.commission.gross_amount, '1000.00');
    assert.equal(state.event.length, 1);
    assert.equal(state.envelope.length, 1);
    assert.equal(state.event[0].actor_id, f.actor.id);
    assert.equal(state.event[0].subject_id, f.deal.id);
    assert.equal(state.event[0].new_value.commission_id, f.commission.id);
    assert.equal(state.event[0].new_value.received_on, f.args.received_on);
    assert.deepEqual(state.envelope[0].response, first);
    assert.equal((await c.query('select won_value::text as value from deal where id=$1', [f.deal.id])).rows[0].value, '987654.00');
    assert.equal((await c.query('select status,version from commission where id=$1', [f.installment.id])).rows[0].status, 'expected');
  } finally { await c.end(); }
});

test('overlapping first receipt calls replay after the first transaction commits', enabled, async () => {
  const observer = await connect(), a = await connect(), b = await connect();
  let pending;
  try {
    const f = await fixture(observer);
    await begin(a, f.actor);
    const first = await TOOLS[VERB].handler(a, f.actor, f.args);
    await begin(b, f.actor);
    const pid = (await b.query('select pg_backend_pid() as pid')).rows[0].pid;
    pending = TOOLS[VERB].handler(b, f.actor, f.args).then(result => ({ result }), error => ({ error }));
    await waitForLock(observer, pid);
    const beforeCommit = await durable(observer, f);
    assert.equal(beforeCommit.envelope.length, 0);
    assert.equal(beforeCommit.commission.status, 'invoiced');
    await a.query('commit');
    const second = await pending;
    if (second.error) throw second.error;
    assert.deepEqual(second.result, { replayed: true, ...first });
    await b.query('commit');
    const state = await durable(observer, f);
    assert.equal(state.event.length, 1);
    assert.equal(state.envelope.length, 1);
    assert.equal(state.commission.version, f.args.base_version + 1);
  } finally {
    await a.query('rollback');
    if (pending) await pending;
    await b.query('rollback');
    await Promise.all([a.end(), b.end(), observer.end()]);
  }
});

for (const target of ['event', 'tool_call']) {
  test(`a database failure inserting ${target} rolls back the registered receipt and all audit rows`, enabled, async () => {
    const c = await connect();
    try {
      const f = await fixture(c);
      let updated = false, failed = false;
      const passthrough = { query: async (sql, values) => {
        if (sql.includes('update commission')) {
          const result = await c.query(sql, values); updated = true; return result;
        }
        if (sql.includes(`insert into ${target} (`)) {
          assert.equal(updated, true, 'failure must happen after the commission update');
          failed = true;
          await c.query('select 1/0');
        }
        return c.query(sql, values);
      } };
      await assert.rejects(call(c, f, f.args, passthrough), e => e.code === '22012');
      assert.equal(failed, true);
      await assertUntouched(c, f);
    } finally { await c.end(); }
  });
}

test('missing commission and invoiced entries without an invoice date refuse without durable writes', enabled, async () => {
  const c = await connect();
  try {
    for (const noInvoiceDate of [false, true]) {
      const f = await fixture(c, { noInvoiceDate });
      const args = noInvoiceDate ? f.args : { ...f.args, commission_id: randomUUID() };
      await assert.rejects(call(c, f, args), e => e.payload?.error === (noInvoiceDate ? 'invoice_not_unpaid' : 'invoice_not_found'));
      await assertUntouched(c, f);
    }
  } finally { await c.end(); }
});

test('financial callers retain separate date errors and deal invoice null-clearing behavior', enabled, async () => {
  const c = await connect();
  try {
    const f = await fixture(c);
    for (const date of ['2026-02-30', f.commission.today, null]) {
      const version = (await c.query('select version from deal where id=$1', [f.deal.id])).rows[0].version;
      await begin(c, f.actor);
      try {
        const args = { idempotency_key: randomUUID(), deal: f.deal.id, base_version: version, fields: { invoiced_on: date } };
        if (date === '2026-02-30') {
          await assert.rejects(TOOLS['update-deal'].handler(c, f.actor, args), e => e.payload?.error === 'invalid_invoiced_on');
          await c.query('rollback');
        } else {
          await TOOLS['update-deal'].handler(c, f.actor, args);
          await c.query('commit');
          assert.equal((await c.query("select to_jsonb(invoiced_on)#>>'{}' as day from deal where id=$1", [f.deal.id])).rows[0].day, date);
        }
      } catch (error) { await c.query('rollback'); throw error; }
    }
    await assert.rejects(call(c, f, { ...f.args, received_on: null }), e => e.payload?.error === 'invalid_received_on');
    await assertUntouched(c, f);
  } finally { await c.end(); }
});
