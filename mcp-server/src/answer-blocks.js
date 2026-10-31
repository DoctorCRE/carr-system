// Validate sourced answer tables and chart views on canonical Doc turns.
import { Validator } from '@cfworker/json-schema';
import schema from './answer-blocks.v1.schema.json' with { type: 'json' };

export const answerBlocksSchema = schema;
const validator = new Validator(schema, '2020-12', false);

export function validateAnswerBlocks(blocks, ToolError) {
  const refuse = () => { throw new ToolError({ error: 'answer_blocks_invalid' }); };
  if (new TextEncoder().encode(JSON.stringify(blocks)).length > 131072 || !validator.validate(blocks).valid) refuse();
  const tables = new Map();
  const ids = new Set();
  for (const block of blocks) {
    if (ids.has(block.id)) refuse();
    ids.add(block.id);
    if (block.type !== 'table.v1') continue;
    const columns = new Map(block.columns.map(column => [column.key, column.type]));
    if (columns.size !== block.columns.length) refuse();
    for (const row of block.rows) {
      if (Object.keys(row).length !== columns.size) refuse();
      for (const [key, cell] of Object.entries(row)) {
        const type = columns.get(key);
        if (!type) refuse();
        if (cell.value === null) continue;
        if (type === 'date') {
          if (typeof cell.value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(cell.value)
            || !Number.isFinite(Date.parse(cell.value))
            || new Date(cell.value).toISOString().slice(0, 10) !== cell.value) refuse();
        } else if (typeof cell.value !== ({ text: 'string', number: 'number', boolean: 'boolean' })[type]
          || (type === 'number' && !Number.isFinite(cell.value))) refuse();
      }
    }
    tables.set(block.id, columns);
  }
  for (const chart of blocks.filter(block => block.type === 'chart.v1')) {
    const columns = tables.get(chart.table_id);
    if (!columns?.has(chart.x) || columns.get(chart.y) !== 'number' || chart.x === chart.y) refuse();
  }
}

export async function recordedCompsTable(c, dealIds) {
  const { rows } = await c.query(
    `select id, deal_id, side, rate_norm_sf_yr
       from v_deal_room_negotiation
      where deal_id = any($1::uuid[]) and rate_norm_sf_yr is not null
      order by proposed_on desc, id limit 200`, [dealIds]);
  return { type: 'table.v1', id: 'comps', columns: [
    { key: 'deal', label: 'Deal', type: 'text' },
    { key: 'side', label: 'Side', type: 'text' },
    { key: 'rent', label: 'Proposed rent ($/SF/year)', type: 'number' },
  ], rows: rows.map(row => ({ deal: { value: row.deal_id }, side: { value: row.side },
    rent: { value: Number(row.rate_norm_sf_yr), source: { record_id: row.id } },
  })) };
}
