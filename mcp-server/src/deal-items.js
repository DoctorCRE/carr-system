import { isCalendarDate } from './calendar-date.js';
export const DEAL_ITEMS_SCHEMA = 'deal-items.v1';

const GROUPS = [
  { title: 'Vendor Introductions', matches: /\bvendor introductions?\b|\b(?:lender|attorney).{0,60}\bintro(?:duction)?s?\b|\bintro(?:duction)?s?.{0,60}\b(?:lender|attorney)\b/i },
  { title: 'Prelim Market Study', matches: /\b(?:prelim(?:inary)? market study|market study|purchase sweep)\b/i },
  { title: 'ETL not completed', matches: /\bETL\b.*\b(?:awaiting|pending|not complet(?:ed|e)|unsigned|sent)\b/i },
];

function factKind(text) {
  if (/\b(awaiting|pending|not completed|sent|signed)\b/i.test(text)) return 'status';
  if (/\b(do not|raise|rather than|suggest|before|should)\b/i.test(text)) return 'suggestion';
  return 'promised';
}

const FACT_BREAK = /\s*;\s*|\n+|(?<!\b(?:Dr|Mr|Ms|St|Ave)\.)(?<=[.!?])\s+(?=[A-Z])/u;
export function shortFacts(text) {
  return String(text || '').split(FACT_BREAK).flatMap(part => {
    const chunks = [];
    let rest = part.trim();
    while (rest.length > 240) {
      const at = rest.lastIndexOf(' ', 240);
      if (at < 1) break;
      chunks.push(rest.slice(0, at));
      rest = rest.slice(at + 1).trim();
    }
    return [...chunks, rest].filter(Boolean);
  });
}

function numberedItems(text, due_on, source_ref) {
  const markers = [...text.matchAll(/\((\d+)\)\s*([A-Z][A-Z ]+?)(?=\s*[,—:-])/gu)];
  if (markers.length < 2) return null;
  const shared = text.slice(0, markers[0].index).trim();
  const sharedDate = shared.match(/\b\d{4}-\d{2}-\d{2}\b/u)?.[0] || null;
  const titles = { INTRODUCTIONS: 'Vendor Introductions', PACKAGE: 'Prelim Market Study', ETL: 'ETL not completed' };
  return markers.map((marker,index) => {
    const heading = marker[2].trim();
    const body = text.slice(marker.index + marker[0].length, markers[index + 1]?.index ?? text.length)
      .replace(/^\s*[,—:-]\s*/u,'').trim();
    const bullets = shared ? [{ date: sharedDate, kind: 'promised', text: shared }] : [];
    for (const sentence of body.split(FACT_BREAK)) {
      for (const fact of sentence.split(/\s*[—;]\s*|,\s+(?!\d)/u).flatMap(shortFacts)) {
        bullets.push({ date: fact.match(/\b\d{4}-\d{2}-\d{2}\b/u)?.[0] || null, kind: factKind(fact), text: fact.trim() });
      }
    }
    return { title: titles[heading] || heading.split(/\s+/u).slice(0,4).join(' '), due_on, status: 'open', source_ref, bullets };
  });
}

export function splitDealNextStep(text, due_on = null, source_ref = 'next_step') {
  const numbered = numberedItems(String(text || ''), due_on, source_ref);
  if (numbered) return numbered;
  const items = new Map();
  let current = null;
  for (const raw of shortFacts(text)) {
    const fact = raw.replace(/^\s*(?:[-*]|\d+[).])\s*/u, '').trim();
    if (!fact) continue;
    const group = GROUPS.find(g => g.matches.test(fact));
    const continues = current === 'Prelim Market Study' && /^(?:walkthrough|\d+[ -]+\d+ exam rooms|~?\$|within|radius)\b/i.test(fact)
      || current === 'ETL not completed' && /^(?:raise|rather than|discuss).{0,60}\b(?:package|call|separately)\b/i.test(fact);
    const title = group?.title || (continues ? current : fact.replace(/[:!?].*$/u, '').replace(/[.]$/u, '').split(/\s+/u).slice(0, 4).join(' '));
    current = title.split(/\s+/u).length < 2 ? 'Deal follow-up' : title;
    if (!items.has(current)) items.set(current, { title: current, due_on, status: 'open', source_ref, bullets: [] });
    const date = fact.match(/\b\d{4}-\d{2}-\d{2}\b/u)?.[0] || null;
    items.get(current).bullets.push({ date, kind: factKind(fact), text: fact });
  }
  return [...items.values()];
}

export function validateDealItem(item, ErrorType = TypeError) {
  const invalid = field => { throw new ErrorType({ error: 'invalid_deal_item', field }); };
  if (typeof item.title !== 'string' || item.title.trim().split(/\s+/u).length < 2 || item.title.trim().split(/\s+/u).length > 4 || item.title.length > 100) invalid('title');
  if (item.due_on != null && !isCalendarDate(item.due_on)) invalid('due_on');
  if (typeof item.source_ref !== 'string' || !item.source_ref.trim() || item.source_ref.length > 500) invalid('source_ref');
  if (!Array.isArray(item.bullets) || !item.bullets.length || item.bullets.length > 60) invalid('bullets');
  for (const b of item.bullets) {
    if (!b || !['promised', 'call', 'suggestion', 'status'].includes(b.kind) || typeof b.text !== 'string' || !b.text.trim() || b.text.length > 240 || b.text.split(FACT_BREAK).length > 1) invalid('bullets');
    if (b.date != null && !isCalendarDate(b.date)) invalid('bullets.date');
  }
  return { title: item.title.trim(), due_on: item.due_on || null,
    source_ref: item.source_ref.trim(), bullets: item.bullets.map(b => ({ date: b.date || null, kind: b.kind, text: b.text.trim() })) };
}

export async function readDealItems(c, dealId) {
  return (await c.query(`select id, item_title as title, to_jsonb(due_on)#>>'{}' as due_on,
      status, item_bullets as bullets, source_ref from v_deal_room_action
      where deal_id=$1 and item_title is not null and status <> 'dropped'
      order by created_order, id /* deal-items:read */`, [dealId])).rows;
}

export function dealItemTools({ withEnvelope, writeEvent, resolveSubject, ToolError, canExercisePartnerAuthority, partnerAuthoritySlugForActor }) {
  const base = { idempotency_key: { type: 'string' }, deal: { type: 'string' } };
  const bullet = { type: 'object', additionalProperties: false,
    properties: { date: { type: ['string', 'null'], format: 'date' }, kind: { type: 'string', enum: ['promised', 'call', 'suggestion', 'status'] }, text: { type: 'string', minLength: 1, maxLength: 240 } }, required: ['kind', 'text'] };
  const command = (name, description, extra, required, run) => ({ write: true, description,
    inputSchema: { type: 'object', additionalProperties: false, properties: { ...base, ...extra }, required: ['idempotency_key', 'deal', ...required] },
    handler: async (c, actor, args) => {
      if (!canExercisePartnerAuthority(actor)) throw new ToolError({ error: 'partner_required' });
      if (!args.idempotency_key) throw new ToolError({ error: 'missing_idempotency_key' });
      await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [args.idempotency_key]);
      return withEnvelope(c, actor, name, args, async () => {
        const s = await resolveSubject(c, args.deal);
        if (s.type !== 'deal') throw new ToolError({ error: 'not_a_deal' });
        await c.query("select pg_advisory_xact_lock(hashtextextended('deal-items:' || $1,0))", [s.id]);
        return run(c, actor, args, s.id);
      });
    },
  });
  const record = async (c, actor, dealId, item, key, verb) => {
    const value = validateDealItem(item, ToolError);
    const owner = (await c.query('select id from actor where slug=$1 and active', [partnerAuthoritySlugForActor(actor)])).rows[0];
    if (!owner) throw new ToolError({ error: 'owner_not_provisioned' });
    const added = (await c.query(`insert into next_action(subject_type,subject_id,owner_id,due_on,description,created_by,updated_by,item_title,item_bullets,source_ref,created_at)
      values ('deal',$1,$2,$3,$4,$5,$5,$4,$6::jsonb,$7,clock_timestamp())
      on conflict (subject_id,source_ref,item_title) where subject_type='deal' and item_title is not null do nothing
      returning id /* deal-items:record */`, [dealId, owner.id, value.due_on, value.title, actor.id, JSON.stringify(value.bullets), value.source_ref])).rows;
    if (added.length) await writeEvent(c, actor, verb, 'deal', dealId, { field: 'deal_item', new: { item_id: added[0].id, ...value }, idempotency_key: key });
    const itemRow = (await readDealItems(c, dealId)).find(i => i.source_ref === value.source_ref && i.title === value.title);
    return { item: itemRow, rows_changed: added.length };
  };
  const find = async (c, dealId, itemId) => {
    for (const [table, dealColumn] of [['next_action', 'subject_id'], ['capture_post_call_action', 'deal_id']]) {
      const row = (await c.query(`select id,status,item_bullets as bullets from ${table}
        where id=$1 and ${dealColumn}=$2 and item_title is not null ${table === 'next_action' ? "and subject_type='deal'" : ''}
        for update`, [itemId, dealId])).rows[0];
      if (row) return { ...row, table };
    }
    throw new ToolError({ error: 'deal_item_not_found', item_id: itemId });
  };
  return {
    'record-deal-item': command('record-deal-item', 'Add one sourced deal checklist item. It never replaces sibling actions. Repeating source_ref and title returns the existing item.',
      { title: { type: 'string', minLength: 1, maxLength: 100 }, due_on: { type: ['string', 'null'], format: 'date' }, bullets: { type: 'array', minItems: 1, maxItems: 60, items: bullet }, source_ref: { type: 'string', minLength: 1, maxLength: 500 } }, ['title', 'bullets', 'source_ref'],
      async (c, actor, args, dealId) => ({ ok: true, item_schema_version: DEAL_ITEMS_SCHEMA, ...await record(c, actor, dealId, args, args.idempotency_key, 'record-deal-item') })),
    'backfill-deal-items': command('backfill-deal-items', 'One-time conversion of an open deal current next_step into stored checklist items through the normal audited writer. Keeps source notes intact and is idempotent across keys.', {}, [],
      async (c, actor, args, dealId) => {
        const prior = (await c.query("select id from event where subject_type='deal' and subject_id=$1 and verb='backfill-deal-items' and field='deal_items_conversion' limit 1", [dealId])).rows;
        if (prior.length) return { ok: true, item_schema_version: DEAL_ITEMS_SCHEMA, rows_changed: 0, items: await readDealItems(c, dealId) };
        const row = (await c.query(`select b.next_step, to_jsonb(b.next_date)#>>'{}' as due_on,
          (select id from next_action where subject_type='deal' and subject_id=b.id and item_title is null
            and status='open' and description=b.next_step order by updated_at desc,id desc limit 1) as source_action_id,
          (select id::text from deal_note where deal_id=b.id and kind='next_step' order by created_at desc,id desc limit 1) as source_id
          from v_deal_room_board b where b.id=$1 and b.outcome is null /* deal-items:backfill-source */`, [dealId])).rows[0];
        if (!row) throw new ToolError({ error: 'open_deal_required' });
        const source = `next_step:${row.source_action_id || row.source_id || dealId}`;
        let changed = 0;
        for (const item of splitDealNextStep(row.next_step, row.due_on, source)) changed += (await record(c, actor, dealId, item, args.idempotency_key, 'backfill-deal-items')).rows_changed;
        if (changed) {
          if (row.source_action_id) changed += (await c.query("update next_action set status='dropped',updated_by=$2 where id=$1 and status='open' returning id", [row.source_action_id, actor.id])).rows.length;
          await writeEvent(c, actor, 'backfill-deal-items', 'deal', dealId, {
            field: 'deal_items_conversion', old: { next_step: row.next_step, source_action_id: row.source_action_id },
            new: { source_ref: source, schema_version: DEAL_ITEMS_SCHEMA }, idempotency_key: args.idempotency_key,
          });
        }
        return { ok: true, item_schema_version: DEAL_ITEMS_SCHEMA, rows_changed: changed, items: await readDealItems(c, dealId) };
      }),
    'instruct-deal-item': command('instruct-deal-item', 'Record a partner instruction for exactly one open deal item. This records a request and never claims the requested work was executed.',
      { item_id: { type: 'string', format: 'uuid' }, text: { type: 'string', minLength: 1, maxLength: 240 } }, ['item_id', 'text'],
      async (c, actor, args, dealId) => {
        const row = await find(c, dealId, args.item_id);
        if (row.status !== 'open') throw new ToolError({ error: 'deal_item_not_open' });
        const text = typeof args.text === 'string' ? args.text.trim() : '';
        if (!text || text.length > 240) throw new ToolError({ error: 'instruction_required' });
        const facts = shortFacts(text).map(text => ({ date: null, kind: 'status', text }));
        await c.query(`update ${row.table} set item_bullets=item_bullets ||
          (select jsonb_agg(b || jsonb_build_object('date',current_date::text)) from jsonb_array_elements($2::jsonb) b),
          updated_at=clock_timestamp() where id=$1`, [row.id, JSON.stringify(facts)]);
        await writeEvent(c, actor, 'instruct-deal-item', 'deal', dealId, { field: 'deal_item_instruction', new: { item_id: row.id, text, status: 'recorded' }, human_quote: text, idempotency_key: args.idempotency_key });
        return { ok: true, item_id: row.id, instruction_recorded: true, executed: false };
      }),
    'complete-deal-item': command('complete-deal-item', 'Mark exactly one deal checklist item done. Other items, due dates, and the deal next_step stay unchanged.',
      { item_id: { type: 'string', format: 'uuid' } }, ['item_id'],
      async (c, actor, args, dealId) => {
        const row = await find(c, dealId, args.item_id);
        if (row.status === 'dropped') throw new ToolError({ error: 'deal_item_not_open' });
        if (row.status === 'done') return { ok: true, item_id: row.id, status: 'done', rows_changed: 0 };
        await c.query(`update ${row.table} set status='done', updated_at=clock_timestamp() ${row.table === 'capture_post_call_action' ? ',completed_at=clock_timestamp()' : ',updated_by=$2'} where id=$1`, row.table === 'next_action' ? [row.id, actor.id] : [row.id]);
        await writeEvent(c, actor, 'complete-deal-item', 'deal', dealId, { field: 'deal_item', new: { item_id: row.id, status: 'done' }, idempotency_key: args.idempotency_key });
        return { ok: true, item_id: row.id, status: 'done', rows_changed: 1 };
      }),
  };
}
