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

export async function recordedCompsTable(c, dealIds, ToolError) {
  // Deal Room shares every deal with both partners, so it applies no partner filter.
  const { rows: [result] } = await c.query(
    `with requested as (select distinct unnest($1::uuid[]) as id),
     missing as (select id from requested except select id from public.deal),
     comps as (
       select id, deal_id, side, rate_norm_sf_yr
       from public.v_deal_room_negotiation
       where deal_id = any($1::uuid[]) and rate_norm_sf_yr is not null
       order by proposed_on desc, id limit 201
     )
     select exists(select 1 from missing) as missing,
       coalesce((select jsonb_agg(to_jsonb(comps)) from comps), '[]'::jsonb) as comps`, [dealIds]);
  if (result.missing) throw new ToolError({ error: 'answer_blocks_invalid' });
  refuseOverflow(result.comps, ToolError);
  return { type: 'table.v1', id: 'comps', columns: [
    { key: 'deal', label: 'Deal', type: 'text' },
    { key: 'side', label: 'Side', type: 'text' },
    { key: 'rent', label: 'Proposed rent ($/SF/year)', type: 'number' },
  ], rows: result.comps.map(row => ({ deal: { value: row.deal_id }, side: { value: row.side },
    rent: { value: answerNumber(row.rate_norm_sf_yr, ToolError), source: { record_id: row.id } },
  })) };
}

function refuseOverflow(rows, ToolError) {
  if (rows.length > 200) throw new ToolError({ error: 'answer_blocks_invalid',
    hint: 'More than 200 rows match. Narrow the submarket/use_type/as_of filter or select fewer comps deals.' });
}

function decimalIdentity(value) {
  const [mantissa, exponent = '0'] = String(value).toLowerCase().split('e');
  const negative = mantissa.startsWith('-');
  const [integer, fraction = ''] = mantissa.replace(/^[+-]/, '').split('.');
  const digits = (integer + fraction).replace(/^0+/, '');
  if (!digits) return '0';
  const coefficient = digits.replace(/0+$/, '');
  const scale = BigInt(exponent) - BigInt(fraction.length) + BigInt(digits.length - coefficient.length);
  return `${negative ? '-' : ''}${coefficient}:${scale}`;
}

function answerNumber(value, ToolError) {
  if (value === null) return null;
  if ((typeof value !== 'string' && typeof value !== 'number')
    || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(String(value))
    || !Number.isFinite(Number(value)) || Math.abs(Number(value)) > Number.MAX_SAFE_INTEGER
    || decimalIdentity(value) !== decimalIdentity(Number(value))) {
    throw new ToolError({ error: 'answer_blocks_invalid', hint: 'A recorded value cannot be represented safely as a numeric answer cell.' });
  }
  return Number(value);
}

export function recordedMarketTermsTable(study, ToolError) {
  refuseOverflow(study.rows, ToolError);
  if (study.rows.some(row => typeof row.canonical_fact_id !== 'string'
    || !Number.isSafeInteger(row.version) || row.version < 1)) throw new ToolError({ error: 'answer_blocks_invalid' });
  const columns = [
    ['entity', 'Recorded entity', 'text'], ['fact', 'Term', 'text'],
    ['kind', 'Counter/executed classification', 'text'], ['date', 'Term date', 'date'],
    ['value', 'Recorded firm-history market terms', 'number'], ['unit', 'Unit', 'text'],
    ['expense_basis', 'Expense basis', 'text'], ['submarket', 'Submarket', 'text'],
    ['use_type', 'Use type', 'text'], ['effective_from', 'Effective from', 'date'],
    ['effective_to', 'Effective to (exclusive)', 'date'], ['coverage', 'Coverage caveat', 'text'],
  ].map(([key, label, type]) => ({ key, label, type }));
  const table = { type: 'table.v1', id: 'market_terms', columns, rows: study.rows.map(row => ({
    entity: { value: row.entity_ref }, fact: { value: row.fact_key }, kind: { value: row.term_kind },
    date: { value: row.term_date }, value: { value: answerNumber(row.value, ToolError),
      source: { fact_id: row.canonical_fact_id, version: row.version } },
    unit: { value: row.unit }, expense_basis: { value: row.expense_basis },
    submarket: { value: row.submarket }, use_type: { value: row.use_type },
    effective_from: { value: row.effective_from }, effective_to: { value: row.effective_to },
    coverage: { value: 'Recorded firm history only; archive and history coverage unknown.' },
  })) };
  validateAnswerBlocks([table], ToolError);
  return table;
}

export function answerChart(table, kind, ToolError) {
  const market = table.id === 'market_terms';
  if (market && (table.rows.some(row => !row.unit.value || !row.expense_basis.value)
    || new Set(table.rows.map(row => JSON.stringify([
    row.fact.value, row.unit.value, row.expense_basis.value,
  ]))).size > 1)) throw new ToolError({ error: 'answer_blocks_invalid',
    hint: 'Use a table for mixed fact kinds, units or expense bases. A chart needs one comparable series.' });
  return { type: 'chart.v1', id: `${table.id}_chart`, table_id: table.id, kind,
    x: market ? 'entity' : 'deal', y: market ? 'value' : 'rent' };
}
