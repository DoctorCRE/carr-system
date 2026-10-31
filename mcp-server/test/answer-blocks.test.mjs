import test from 'node:test';
import assert from 'node:assert/strict';
import { validateAnswerBlocks } from '../src/answer-blocks.js';
import { docConversationTools } from '../src/doc-conversation.js';
import { ToolError as PublicToolError } from '../src/tool-error.ts';
import { publicError } from '../src/public-errors.ts';

class ToolError extends Error {
  constructor(fields) { super(fields.error); Object.assign(this, fields); }
}
const conversation = '61700000-0000-4000-8000-000000000001';
const record = '61700000-0000-4000-8000-000000000002';
const table = () => ({ type: 'table.v1', id: 'comps', columns: [
  { key: 'side', label: 'Side', type: 'text' },
  { key: 'rent', label: 'Rent ($/SF/year)', type: 'number' },
], rows: [{ side: { value: 'landlord' }, rent: { value: 32, source: { record_id: record } } }] });

function harness() {
  let stored;
  const c = { query: async (sql, values) => {
    if (sql.includes('append_doc_conversation_turn')) {
      stored = { sequence: 0, role: values[1], body: values[2], msg_id: values[3],
        origin_channel: 'mcp', origin_actor: 'joe', at: '2026-10-30T12:00:00Z',
        ...(values[5] ? { answer_blocks: JSON.parse(values[5]) } : {}) };
      return { rows: [{ appended: { ok: true, sequence: 0, msg_id: values[3] } }] };
    }
    if (sql.includes('doc_conversation_facts')) return { rows: [{ facts: {
      ok: true, identity: { id: conversation }, turns: [stored], latest_sequence: 0,
    } }] };
    throw new Error(`Unexpected query ${sql}`);
  } };
  const tools = docConversationTools({ ToolError, withEnvelope: async (_c, _a, _v, _args, fn) => fn(),
    writeEvent: async () => {} });
  const append = args => tools['add-doc-conversation-turn'].handler(c, { id: record }, {
    idempotency_key: record, conversation_id: conversation, role: 'assistant', body: 'Synthetic comps', ...args,
  });
  const read = () => tools['read-doc-conversation'].handler(c, { id: record }, { conversation_id: conversation });
  return { append, read };
}

test('numeric cells require a record or a versioned canonical fact source', async () => {
  const block = table();
  delete block.rows[0].rent.source;
  assert.throws(() => validateAnswerBlocks([block], ToolError), /answer_blocks_invalid/);
  block.rows[0].rent.source = { fact_id: record };
  assert.throws(() => validateAnswerBlocks([block], ToolError), /answer_blocks_invalid/);
  block.rows[0].rent.source.version = 2;
  assert.doesNotThrow(() => validateAnswerBlocks([block], ToolError));
});

test('row, column, block and payload caps refuse oversize answers', async () => {
  const block = table();
  block.rows = Array.from({ length: 201 }, () => table().rows[0]);
  assert.throws(() => validateAnswerBlocks([block], ToolError), /answer_blocks_invalid/);
  block.rows = [];
  block.columns = Array.from({ length: 21 }, (_, i) => ({ key: `c${i}`, label: 'C', type: 'number' }));
  assert.throws(() => validateAnswerBlocks([block], ToolError), /answer_blocks_invalid/);
  assert.throws(() => validateAnswerBlocks(Array.from({ length: 9 }, table), ToolError), /answer_blocks_invalid/);
  const large = table();
  large.rows = Array.from({ length: 200 }, () => ({ ...table().rows[0], side: { value: 'x'.repeat(2000) } }));
  assert.throws(() => validateAnswerBlocks([large], ToolError), /answer_blocks_invalid/);
});

test('unknown block and column types refuse', async () => {
  assert.throws(() => validateAnswerBlocks([{ ...table(), type: 'table.v2' }], ToolError), /answer_blocks_invalid/);
  const block = table();
  block.columns[1].type = 'money';
  assert.throws(() => validateAnswerBlocks([block], ToolError), /answer_blocks_invalid/);
});

test('plain text answer readback is unchanged', async () => {
  const h = harness();
  await h.append({ body: 'Plain answer' });
  const turn = (await h.read()).turns[0];
  assert.equal(turn.body, 'Plain answer');
  assert.equal(Object.hasOwn(turn, 'answer_blocks'), false);
});

test('a chart references numeric columns of its table and carries no free numbers', async () => {
  const chart = { type: 'chart.v1', id: 'rent_chart', table_id: 'comps', kind: 'bar', x: 'side', y: 'rent' };
  assert.doesNotThrow(() => validateAnswerBlocks([table(), chart], ToolError));
  for (const invalid of [{ ...chart, table_id: 'missing' }, { ...chart, y: 'side' }, { ...chart, values: [32] }]) {
    assert.throws(() => validateAnswerBlocks([table(), invalid], ToolError), /answer_blocks_invalid/);
  }
});

test('typed rows and unique keys refuse malformed answers', async () => {
  const block = table();
  block.rows[0].rent.value = '32';
  assert.throws(() => validateAnswerBlocks([block], ToolError), /answer_blocks_invalid/);
  assert.throws(() => validateAnswerBlocks([table(), table()], ToolError), /answer_blocks_invalid/);
});

test('answer validation refusals have a public error code and useful limits', () => {
  const result = publicError(new PublicToolError({ error: 'answer_blocks_invalid' }), {
    env: { CORRELATION_ID: record },
  });
  assert.equal(result.error, 'answer_blocks_invalid');
  assert.match(result.hint, /200 rows and 20 columns/);
});
