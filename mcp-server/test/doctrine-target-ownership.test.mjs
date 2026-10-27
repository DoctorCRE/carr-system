// Test ownership restrictions on doctrine section relationships.
import test from 'node:test';
import assert from 'node:assert/strict';
import { executeRegisteredTool, ToolError } from '../src/tools.js';

const joe = { id: '10000000-0000-4000-8000-000000000001', slug: 'joe', human: true };
const dell = { id: '10000000-0000-4000-8000-000000000002', slug: 'dell', human: true };
const sourceId = '20000000-0000-4000-8000-000000000002';
const targetId = '20000000-0000-4000-8000-000000000001';
const receiptId = '50000000-0000-4000-8000-000000000001';

function database(sourceOwner, targetOwner, shared = false) {
  const sections = [targetId, sourceId].map((id, i) => ({ id, document_id: id.replace(/^2/, '4'),
    visibility: shared && i === 0 ? 'shared' : 'personal', owner_actor_id: i ? sourceOwner.id : targetOwner.id,
    content_class: 'reference', current_version: '2', current_revision_id: receiptId }));
  const edges = [], writes = [], queries = [];
  return { writes, queries, async query(text, params = []) {
    const sql = text.replace(/\s+/g, ' ').trim();
    queries.push({ sql, params });
    if (sql.startsWith('select request_hash')) return { rows: [] };
    if (sql.includes('from doctrine_document')) return { rows: sections.map(s => ({ id: s.document_id })) };
    if (sql.includes("where s.status = 'active'"))
      return { rows: sections.filter(s => s.visibility !== 'personal' || s.owner_actor_id === params[0]) };
    if (sql.includes('from doctrine_section')) return { rows: sections.filter(s => s.id === params[0]) };
    if (sql.includes('from doctrine_gate_check')) return { rows: ['target_exists', 'edge_class_allowed',
      'edge_acyclic', 'unresolved_conflict', 'personal_owner_required', 'claim_holder_or_free'].map(key => ({
        check_key: key, impl_key: `gates.${key}`, severity: 'block', config: {},
      })) };
    if (sql.includes('from doctrine_edge_type')) return { rows: [{ acyclic: true }] };
    if (sql.startsWith('with recursive') || sql.includes('from doctrine_claim')) return { rows: [] };
    if (sql.includes('from doctrine_edge')) return { rows: edges.filter(e => params[0].includes(e.target_section_id)) };
    if (sql.startsWith('insert into doctrine_edge ')) edges.push({ source_section_id: params[0],
      target_section_id: params[1], edge_type: params[2], scope: JSON.parse(params[3]) });
    if (/^(insert|update|delete)/.test(sql)) {
      if (!sql.startsWith('insert into tool_call')) writes.push(sql);
      return { rows: [{ id: receiptId, generation: 3 }] };
    }
    throw new Error(`Unhandled synthetic query: ${sql}`);
  } };
}

for (const [author, victim] of [[joe, dell], [dell, joe]]) {
  for (const edge_type of ['OVERRIDES', 'SUPERSEDES', 'EXCEPTION_TO']) {
    test(`${author.slug} cannot ${edge_type} ${victim.slug}'s personal section from an authorized source`, async () => {
      const db = database(author, victim);
      const args = { idempotency_key: `target-${edge_type}`, section_id: sourceId, base_version: 2,
        edges: [{ edge_type, target_section_id: targetId }] };
      await assert.rejects(() => executeRegisteredTool(db, author, 'set-doctrine-refs', args),
        e => e instanceof ToolError && e.payload.error === 'personal_doc_not_owner');
      assert.deepEqual(db.writes, [], 'authorize all semantic targets before replacing refs or running gates');
      const resolved = await executeRegisteredTool(db, victim, 'resolve-doctrine-rules', {});
      assert.deepEqual(resolved.operative.map(s => s.id), [targetId]);
      assert.deepEqual(resolved.exceptions, []);
    });
  }
}

for (const [label, edge_type, shared, targetOwner] of [
  ['citation to foreign personal doctrine', 'DEPENDS_ON', false, dell],
  ['shared override', 'OVERRIDES', true, dell],
  ['own personal override', 'OVERRIDES', false, joe],
]) test(`set-doctrine-refs preserves ${label}`, async () => {
  const db = database(joe, targetOwner, shared);
  const result = await executeRegisteredTool(db, joe, 'set-doctrine-refs', {
    idempotency_key: label, section_id: sourceId, base_version: 2,
    links: [{ target_kind: 'doctrine_section', target_id: targetId }],
    edges: [{ edge_type, target_section_id: targetId }],
  });
  assert.equal(result.ok, true);
});
