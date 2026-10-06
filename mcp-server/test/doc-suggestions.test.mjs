import test from 'node:test';
import assert from 'node:assert/strict';
import { docSuggestionTools } from '../src/doc-suggestions.js';

const tools = docSuggestionTools({
  withEnvelope: (_c, _a, _name, _args, fn) => fn(),
  writeEvent: async () => {},
  ToolError: class ToolError extends Error { constructor(value) { super(value.error); this.value = value; } },
});

test('suggestion contracts close caller authority fields and separate read, producer and human decisions', () => {
  const names = Object.keys(tools).sort();
  assert.deepEqual(names, ['complete-doc-suggestion-scan', 'decide-doc-suggestion', 'list-doc-suggestions', 'propose-doc-correction', 'suggest-doc-work']);
  for (const [name, tool] of Object.entries(tools)) {
    assert.equal(tool.inputSchema.additionalProperties, false, name);
    for (const forbidden of ['actor', 'contributor', 'source_at', 'original_text', 'status']) {
      assert.equal(Object.hasOwn(tool.inputSchema.properties, forbidden), false, `${name}: ${forbidden}`);
    }
  }
  assert.equal(tools['suggest-doc-work'].authorityOnly, true);
  assert.equal(tools['complete-doc-suggestion-scan'].authorityOnly, true);
  assert.equal(tools['complete-doc-suggestion-scan'].write, true);
  assert.equal(tools['list-doc-suggestions'].writerConnection, true);
  assert.equal(tools['list-doc-suggestions'].write, undefined);
  for (const name of ['decide-doc-suggestion', 'propose-doc-correction']) {
    assert.equal(tools[name].humanOnly, true);
    assert.equal(tools[name].writerConnection, true);
  }
});

test('a stale decision returns the current record without writing over it', async () => {
  const query = async () => ({ rows: [{ result: { ok: false, reason_id: 'version_conflict', current: { version: 4, polished_text: 'Current' } } }] });
  await assert.rejects(tools['decide-doc-suggestion'].handler({ query }, { id: 'actor' }, {
    suggestion_id: 'a0000000-0000-4000-8000-000000000001', base_version: 3,
    choice: 'dismiss', idempotency_key: 'b0000000-0000-4000-8000-000000000001',
  }), error => error.value?.error === 'version_conflict' && error.value.current?.version === 4);
});
