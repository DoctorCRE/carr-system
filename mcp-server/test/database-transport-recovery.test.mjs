import test from 'node:test';
import assert from 'node:assert/strict';
import { neonConfig, Pool } from '@neondatabase/serverless';
import { callTool, dispatch } from '../src/mcp.js';
import { authenticatedIdentity } from '../src/identity.js';

const env = { DATABASE_URL_READER: 'postgres://fixture@synthetic.invalid/fixture',
  DATABASE_URL_WRITER: 'postgres://fixture@synthetic.invalid/fixture' };
const actor = () => authenticatedIdentity.connectionForGrant({ slug: 'joe' });
const timeline = [{ entry_kind: 'activity', summary: 'Synthetic call' }];
const subject = { type: 'client', id: '10000000-0000-4000-8000-000000000002' };
const handshake = status => new Error('Uncaught TypeError: Failed to establish the WebSocket connection: ' +
  `expected server to reply with HTTP status code 101 (switching protocols), but received ${status} instead.`);
const selected = () => Response.json({ fields: [{ name: 'number', dataTypeID: 25 }],
  rows: [['SYN-1']], rowCount: 1, command: 'SELECT', rowAsArray: true });

function fetchFixture(t, fetch) {
  const previous = neonConfig.fetchFunction;
  t.after(() => { neonConfig.fetchFunction = previous; });
  neonConfig.fetchFunction = fetch;
  t.mock.method(console, 'error', () => {});
}

async function board(delegated) {
  const request = new Request('https://synthetic.example/mcp', { method: 'POST',
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: delegated
      ? { name: 'call-verb', arguments: { verb: 'loop-board', args: {} } }
      : { name: 'loop-board', arguments: {} } }) });
  const response = await dispatch(request, { DATABASE_URL_READER: env.DATABASE_URL_READER }, {}, actor());
  const body = await response.json();
  return { result: JSON.parse(body.result.content[0].text), isError: body.result.isError };
}

for (const delegated of [false, true]) {
  for (const status of [500, 520, 522, 524, 525]) {
    test(`${delegated ? 'wrapped' : 'direct'} read recovers from Neon HTTP ${status}`, async t => {
      let attempts = 0;
      fetchFixture(t, async () => ++attempts === 1
        ? new Response(`error code: ${status}`, { status }) : selected());
      const answer = await board(delegated);
      assert.notEqual(answer.isError, true);
      assert.deepEqual(answer.result, { count: 1, loops: [{ number: 'SYN-1' }] });
      assert.equal(attempts, 2);
    });
  }
}

test('read recovers from a rejected Neon fetch', async t => {
  let attempts = 0;
  fetchFixture(t, async () => {
    if (++attempts === 1) throw new Error('Network connection lost.');
    return selected();
  });
  assert.deepEqual((await board(false)).result, { count: 1, loops: [{ number: 'SYN-1' }] });
  assert.equal(attempts, 2);
});

test('persistent HTTP failure remains a redacted failure after three attempts', async t => {
  let attempts = 0;
  fetchFixture(t, async () => { attempts++; return new Response('synthetic private diagnostics', { status: 524 }); });
  const answer = await board(false);
  assert.equal(answer.isError, true);
  assert.equal(answer.result.error, 'unhandled_verb_failure');
  assert.doesNotMatch(JSON.stringify(answer), /synthetic private diagnostics/);
  assert.equal(attempts, 3);
});

for (const error of [
  { message: 'canceling statement due to statement timeout', code: '57014' },
  { message: 'Server error (HTTP status 524): synthetic SQL text', code: '23514' },
]) test(`read does not retry PostgreSQL ${error.code}`, async t => {
  let attempts = 0;
  fetchFixture(t, async () => { attempts++; return Response.json(error, { status: 400 }); });
  assert.equal((await board(false)).isError, true);
  assert.equal(attempts, 1);
});

function poolFixture(t, { failures = [], queryFailure = null } = {}) {
  let attempts = 0, ends = 0, releases = 0;
  const statements = [];
  const query = async text => {
    statements.push(text);
    if (queryFailure && text === queryFailure.statement) throw queryFailure.error;
    if (text.startsWith('select id from actor')) return { rows: [{ id: '10000000-0000-4000-8000-000000000001' }] };
    if (text.includes('session_user::text')) return { rows: [{ session_principal: 'carr_writer',
      current_principal: 'carr_writer', privilege_bundle: 'carr_writer', member_carr_writer: true,
      member_carr_jobs: false, member_carr_authority: false, backend_pid: 9 }] };
    if (text.includes('from v_ref_index')) return { rows: [{ subject_id: subject.id }] };
    if (text.includes('from v_subject_timeline')) return { rows: timeline };
    if (text.includes('from tool_call t join activity')) return { rows: [] };
    if (/^(begin|commit|rollback)|set_config\(/.test(text)) return { rows: [] };
    throw new Error(`Unexpected fixture SQL ${text}`);
  };
  t.mock.method(Pool.prototype, 'connect', async () => {
    const failure = failures[attempts++];
    if (failure) throw failure;
    return { query, release() { releases++; } };
  });
  t.mock.method(Pool.prototype, 'end', async () => { ends++; });
  return { statements, counts: () => ({ attempts, ends, releases }) };
}

for (const status of [520, 522, 525]) {
  test(`actor-scoped read reconnects after handshake ${status} before its transaction`, async t => {
    const fixture = poolFixture(t, { failures: [handshake(status)] });
    const result = await callTool(env, actor(), 'catch-me-up', { ref: 'C-1' });
    assert.deepEqual(result, { subject, timeline, calendar_history: [] });
    assert.deepEqual(fixture.counts(), { attempts: 2, ends: 0, releases: 1 });
    assert.equal(fixture.statements.filter(sql => sql.startsWith('begin')).length, 1);
  });
}

test('failed connection acquisition closes its pool after the bounded retries', async t => {
  const error = handshake(522);
  const fixture = poolFixture(t, { failures: [error, error, error] });
  await assert.rejects(callTool(env, actor(), 'catch-me-up', { ref: 'C-1' }), e => e === error);
  assert.deepEqual(fixture.counts(), { attempts: 3, ends: 1, releases: 0 });
  assert.deepEqual(fixture.statements, []);
});

for (const statement of ['begin', 'commit']) {
  test(`transaction transport failure at ${statement} is never replayed`, async t => {
    const error = new Error('Connection terminated unexpectedly');
    const fixture = poolFixture(t, { queryFailure: { statement, error } });
    const name = statement === 'begin' ? 'add-loop' : 'catch-me-up';
    const args = statement === 'begin'
      ? { idempotency_key: 'synthetic-idempotency', kind: 'idea', owner: 'Joe', body: 'Synthetic idea' }
      : { ref: 'C-1' };
    await assert.rejects(callTool(env, actor(), name, args), e => e === error);
    assert.deepEqual(fixture.counts(), { attempts: 1, ends: 0, releases: 1 });
    assert.equal(fixture.statements.filter(sql => sql.startsWith('begin')).length, 1);
  });
}
