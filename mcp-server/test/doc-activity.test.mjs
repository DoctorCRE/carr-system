import test from 'node:test';
import assert from 'node:assert/strict';
import { docActivityEntry, docActivityTools } from '../src/doc-activity.js';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';
import { connectionRouteForTool } from '../src/mcp.js';
import { CURRENT_REGISTRY_VERSION, frozenInventory } from '../../ops/scac-mutation-inventory.mjs';
import { SCAC_MUTATION_REGISTRY_VERSION, registeredOperation } from '../src/mutation-registry.js';
const tools = docActivityTools({ ToolError: class extends Error { constructor(p) { super(p.error); this.payload = p; } } });
const actor = { slug: 'joe', human: true };
const event = { id: '10000000-0000-4000-8000-000000000001', recorded_at: '2026-10-01T15:00:00Z',
  occurred_at: '2026-10-01T14:00:00Z', subject_id: '20000000-0000-4000-8000-000000000001', subject_type: 'deal',
  actor_name: 'Doc', partner: 'joe', record_name: 'Demo Practice', verb: 'patch-deal-field', field: 'phase',
  old_value: { phase: 'research' }, new_value: { phase: 'legal' }, has_old_value: true, is_latest: true,
  agent_rationale: 'Demo accepted proposal', human_quote: null };

test('exact event identity, original entry, known inverse and missing facts are preserved', () => {
  const row = docActivityEntry(event);
  assert.equal(row.undo.event_id, event.id); assert.equal(row.before, 'research');
  assert.equal(row.evidence.reason, event.agent_rationale); assert.equal(row.evidence.at, event.occurred_at);
  assert.equal(docActivityEntry({ ...event, is_latest: false }).undo.state, 'superseded');
  assert.equal(docActivityEntry({ ...event, has_old_value: false }).undo.state, 'unavailable');
  assert.equal(docActivityEntry({ ...event, verb: 'revert-deal-field' }).undo.state, 'undone');
  assert.equal(docActivityEntry({ ...event, field: null, irreversible: true }).undo.state, 'irreversible');
  assert.equal(docActivityEntry({ ...event, field: null, subject_type: 'lead', verb: 'create-lead', agent_rationale: null }).why, null);
  const safe = docActivityEntry({ ...event, old_value: { phase: { __sensitive_ref: 'hidden' } } });
  assert.equal(safe.before, null); assert.ok(!JSON.stringify(safe).includes('hidden'));
  assert.equal(safe.undo.state, 'unavailable');
});
test('closed read schema, runtime input checks, dates and authority cannot be supplied', async () => {
  const tool = tools['read-doc-activity'];
  assert.equal(tool.inputSchema.additionalProperties, false); assert.equal(tool.write, undefined);
  for (const args of [null, [], { cursor: null }, { cursor: [] }, { actor: 'dell' }, { partner: '' }, { partner: 'other' }, { record_type: "x' or true" },
    { limit: 0 }, { limit: 101 }, { since: 'yesterday' }, { cursor: { at: 'bad', id: event.id } },
    { since: '2026-10-02T00:00:00Z', until: '2026-10-01T00:00:00Z' }]) {
    await assert.rejects(tool.handler({ query() { assert.fail('invalid input reached database'); } }, actor, args), /input_invalid/);
  }
});
test('activity successor adds only its reader and preserves every historical source contract', () => {
  const before = frozenInventory('scac-mutation-registry.v106');
  const after = frozenInventory('scac-mutation-registry.v107');
  assert.equal(after.length, before.length + 1);
  assert.deepEqual(after.filter(row => row.ingress_key !== 'mcp-tool:read-doc-activity'), before);
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, CURRENT_REGISTRY_VERSION);
  const entry = registeredOperation('read-doc-activity');
  assert.equal(entry.write, false);
  assert.equal(after.find(row => row.ingress_key === entry.ingress_key).principal_mode, 'authenticated_registered_principal');
});
test('feed selects the scoped read-only writer route', () => {
  assert.equal(connectionRouteForTool(TOOLS['read-doc-activity']), 'writer_read_only');
});
test('registered feed reaches the database', async () => {
  await assert.rejects(executeRegisteredTool({ query() { throw new Error('query reached'); } }, actor,
    'read-doc-activity', {}), /query reached/);
});
test('feed rejects unsupported inverse values rather than advertising Undo', () => {
  assert.equal(docActivityEntry({ ...event, field: 'attention', old_value: { attention: 'true' } }).undo.state, 'unavailable');
});
test('harmless scalar evidence survives for fields without an inverse', () => {
  const row = docActivityEntry({ ...event, subject_type: 'party', field: 'name',
    old_value: { name: 'Synthetic A' }, new_value: { name: 'Synthetic B' } });
  assert.equal(row.before, 'Synthetic A'); assert.equal(row.after, 'Synthetic B');
  assert.equal(row.undo.state, 'unavailable');
});
test('invalid JSON types and calendar timestamps refuse before any query', async () => {
  const dates = ['2026-02-30T00:00:00Z', '2026-02-29T00:00:00Z', '2026-10-01T00:00:00',
    '2026-10-01', '2026-10-01T24:00:00Z', '2026-10-01T00:00:00+24:00',
    '2026-10-01T00:00:00.1234567Z', '2026-10-01T00:00:00-00:00'];
  const invalid = [{ record_type: ['deal'] }, { record_type: null }, { limit: null },
    { cursor: { at: event.recorded_at, id: [event.id] } },
    { cursor: { id: event.id } }, ...dates.flatMap(at => [{ since: at }, { until: at }, { cursor: { at, id: event.id } }])];
  for (const args of invalid) {
    await assert.rejects(tools['read-doc-activity'].handler({ query() { assert.fail('queried invalid input'); } }, actor, args),
      /input_invalid/, JSON.stringify(args));
  }
});
test('valid fractional intervals and timezone offsets retain microsecond ordering', async () => {
  const c = { async query(sql) { return { rows: sql.includes(':clock') ? [{ as_of: event.recorded_at }] : [] }; } };
  for (const [since, until] of [
    ['2026-10-01T00:00:00.123100Z', '2026-10-01T00:00:00.123900Z'],
    ['2024-02-29T00:00:00+01:00', '2024-02-28T23:00:00.000001Z'],
  ]) assert.equal((await tools['read-doc-activity'].handler(c, actor, { since, until })).ok, true);
});
test('parameterized tenant/personal scope, cause classification and keyset paging cover every type', async () => {
  const calls = [];
  const c = { async query(sql, params) {
    calls.push({ sql, params });
    if (sql.includes(':types')) return { rows: [{ subject_type: 'deal' }, { subject_type: 'draft' }] };
    if (sql.includes(':clock')) return { rows: [{ as_of: event.recorded_at }] };
    return { rows: [event, { ...event, id: '10000000-0000-4000-8000-000000000002' }] };
  } };
  const args = { partner: 'dell', record_type: 'draft', limit: 1,
    cursor: { at: event.recorded_at, id: event.id }, since: '2026-10-01T00:00:00Z' };
  const result = await tools['read-doc-activity'].handler(c, actor, args);
  assert.deepEqual(result.record_types, ['deal', 'draft']); assert.equal(result.entries.length, 1);
  assert.deepEqual(result.next_cursor, { at: event.recorded_at, id: event.id });
  assert.equal(result.schema_version, 'doc-activity.v1');
  assert.deepEqual(calls[1].params, ['carr-internal', 'joe-personal', 'dell', 'draft', args.since, null, event.recorded_at, event.id, 2]);
  assert.match(calls[1].sql, /personal_scope='none' or e.personal_scope=\$2/);
  assert.match(calls[1].sql, /e.cause in/); assert.ok(!calls[1].sql.includes('human_stated'));
  assert.match(calls[1].sql, /order by e.recorded_at desc,e.id desc/);
  assert.match(calls[1].sql, /not exists.*select 1 from event newer/s);
});
