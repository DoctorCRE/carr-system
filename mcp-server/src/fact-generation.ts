// Prepare a draft LOI comparison from versioned, confirmed deal facts.
import { organizationTenantForActor } from './identity.js';
import { ToolError } from './tool-error.ts';
import type { Actor, Db, VerbInputs, WithEnvelope, WriteEvent } from './verb-contract.ts';

const uuid = { type: 'string', pattern: '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' };
const version = { type: 'integer', minimum: 1, maximum: 0x7ffffffe };
const ROWS = {
  base_rent_per_sf: { row: 9, label: 'Base rent (USD/SF/year)' },
  landlord_ti_per_sf: { row: 15, label: 'Landlord TI (USD/SF)' },
  term_months: { row: 13, label: 'Term (months)' },
  free_rent_months: { row: 14, label: 'Free rent (months)' },
};
type Field = keyof typeof ROWS;
type Reference = { fact_key: Field; prior_version: number; current_version: number };
type Canonical = { id: string; version: number; normalized_value: string; expense_basis: string | null; observation_ids: string[] };
type Observation = { id: string; source: { document_ref: string; content_hash: string | null } };
type Review = { fact_key: Field; pending: boolean; canonical_version: number; review_version: string;
  canonical_history: Canonical[]; observations: Observation[] };
type Snapshot = { fields: Reference[]; reviews: string[] };

function blocked(field: string, reason: string): never {
  throw new ToolError({ error: 'comparison_fact_blocked', field, reason });
}

async function confirmed(c: Db, actor: Actor, deal: string, fields: Reference[], sourceLock: 'share' | 'read') {
  const tenant = organizationTenantForActor(actor);
  if (tenant !== 'carr-internal') throw new ToolError({ error: 'not_in_profile' });
  for (const field of [...fields].sort((a, b) => a.fact_key.localeCompare(b.fact_key)))
    await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))',
      [`canonical-fact:${tenant}:${deal}:${field.fact_key}`]);
  const result = (await c.query('select ops.list_fact_review($1,$2,100,null,true) r', [tenant, deal])).rows[0].r;
  const items: Review[] = result.items;
  const lock = sourceLock === 'share' ? ' for share' : '';
  const policy = (await c.query("select value from system_config where key='facts.comparison_disclosure'" + lock)).rows[0]?.value;
  const lineage = [];
  const edits = [];
  const reviews = [];
  for (const ref of fields) {
    const item = items.find(i => i.fact_key === ref.fact_key);
    if (!item?.canonical_version) blocked(ref.fact_key, 'missing');
    if (item.pending) blocked(ref.fact_key, 'unresolved');
    if (item.canonical_version !== ref.current_version) blocked(ref.fact_key, 'stale_snapshot');
    reviews.push(item.review_version);
    for (const [column, v] of [['B', ref.prior_version], ['C', ref.current_version]] as const) {
      const fact = item.canonical_history.find(f => f.version === v);
      if (!fact) blocked(ref.fact_key, 'missing_version');
      for (const id of fact.observation_ids) {
        const observation = item.observations.find(o => o.id === id);
        if (!observation?.source.content_hash) blocked(ref.fact_key, 'source_not_permitted');
        const source = observation.source;
        // An absent disclosure decision denies use. The policy binds the exact attachment bytes.
        if (policy?.[source.document_ref] !== source.content_hash) blocked(ref.fact_key, 'source_not_permitted');
        const doc = (await c.query(`select id from attachment where id::text=$1 and subject_type='deal'
          and subject_id::text=$2 and sha256=$3 and deleted_at is null${lock}`,
        [source.document_ref, deal, source.content_hash])).rows[0];
        if (!doc) blocked(ref.fact_key, 'source_not_permitted');
      }
      if (!/^(0|[1-9][0-9]{0,29})(\.[0-9]{1,18})?$/.test(fact.normalized_value)) blocked(ref.fact_key, 'invalid_value');
      const basis = fact.expense_basis;
      if (basis !== null && !['nnn', 'modified_gross', 'full_service_gross', 'gross'].includes(basis)) blocked(ref.fact_key, 'invalid_value');
      const where = `cell:LOI Grid!${column}${ROWS[ref.fact_key].row}`;
      edits.push({ where, text: fact.normalized_value + (basis ? ` (${basis})` : '') });
      lineage.push({ where, canonical_id: fact.id, version: fact.version, fact_key: ref.fact_key,
        observation_ids: fact.observation_ids, operation: 'identity' });
    }
    edits.push({ where: `cell:LOI Grid!A${ROWS[ref.fact_key].row}`, text: ROWS[ref.fact_key].label });
  }
  return { edits, lineage, snapshot: { fields, reviews } satisfies Snapshot };
}

function comparisonEdits(edits: { where: string; text: string }[]) {
  return [
    { where: 'cell:LOI Grid!A1', text: 'DRAFT - LOI terms comparison' },
    { where: 'cell:LOI Grid!B4', text: 'Prior - Confirmed deal fact' },
    { where: 'cell:LOI Grid!C4', text: 'Current - Confirmed deal fact' }, ...edits,
  ];
}

export function factGenerationTools({ withEnvelope, writeEvent }: { withEnvelope: WithEnvelope; writeEvent: WriteEvent }) {
  return {
    'prepare-terms-comparison': {
      write: true,
      description: 'Prepare a draft LOI grid through the existing document fill plan. Select prior and current confirmed canonical versions on one deal. Pass current versions from list-fact-review. Only referenced fields must be settled. Source disclosure requires facts.comparison_disclosure to map each attachment UUID to its permitted SHA-256; absent decisions deny use. Stores internal lineage in the document event. No sends. Rent schedules remain out of scope.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: uuid, deal_id: uuid,
        fields: { type: 'array', minItems: 1, maxItems: 4, items: { type: 'object', additionalProperties: false,
          properties: { fact_key: { type: 'string', enum: Object.keys(ROWS) }, prior_version: version, current_version: version },
          required: ['fact_key', 'prior_version', 'current_version'] } },
      }, required: ['idempotency_key', 'deal_id', 'fields'] },
      handler: async (c: Db, actor: Actor, args: VerbInputs['prepare-terms-comparison']) => {
        if (new Set(args.fields.map(f => f.fact_key)).size !== args.fields.length
          || args.fields.some(f => f.prior_version > f.current_version)) throw new ToolError({ error: 'validation_error' });
        const data = await confirmed(c, actor, args.deal_id, args.fields, 'share');
        return withEnvelope(c, actor, 'prepare-terms-comparison', args, async () => {
          const template = (await c.query("select * from doc_template where slug='loi-grid' and active for share")).rows[0];
          if (!template || template.field_map.unreviewed || template.field_map.template_kind !== 'xlsx')
            throw new ToolError({ error: 'comparison_template_unavailable' });
          const deal = (await c.query('select client_id from deal where id=$1', [args.deal_id])).rows[0];
          if (!deal) throw new ToolError({ error: 'not_found' });
          const document = (await c.query(`insert into document(template_id,deal_id,client_id,prepared_by,sent_status,note)
            values($1,$2,$3,$4,'draft',$5) returning id`,
          [template.id, args.deal_id, deal.client_id, actor.id, 'Confirmed-fact LOI comparison draft.'])).rows[0];
          await writeEvent(c, actor, 'prepare-terms-comparison', 'deal', args.deal_id, {
            field: 'document', new: { document_id: document.id, lineage: data.lineage,
              snapshot: { ...data.snapshot, template_version: template.template_version, source_path: template.source_path } },
            idempotency_key: args.idempotency_key,
          });
          return { ok: true, schema_version: 'terms-comparison.v1', document_id: document.id,
            template: { slug: template.slug, name: 'LOI comparison', source_path: template.source_path,
              template_version: template.template_version, kind: 'xlsx', output_kinds: ['working', 'pdf'] },
            basename: 'LOI-comparison-DRAFT', deal: { id: args.deal_id },
            status: 'draft', owed: [], partial: [], carried: [],
            edits: comparisonEdits(data.edits), remaining: ['Rent schedule'], human_gate: 'Draft for review. Nothing sent.' };
        });
      },
    },
    'validate-terms-comparison': {
      write: false, writerConnection: true,
      description: 'Validate the stored comparison snapshot before and after export. Promotions or new unresolved evidence invalidate it. Returns no internal lineage or source identifiers.',
      inputSchema: { type: 'object', additionalProperties: false, properties: { document_id: uuid }, required: ['document_id'] },
      handler: async (c: Db, actor: Actor, args: VerbInputs['validate-terms-comparison']) => {
        if (organizationTenantForActor(actor) !== 'carr-internal') throw new ToolError({ error: 'not_in_profile' });
        const doc = (await c.query(`select d.deal_id,e.new_value->'snapshot' snapshot
          from document d join doc_template t on t.id=d.template_id
          join event e on e.verb='prepare-terms-comparison' and e.new_value->>'document_id'=d.id::text
          where d.id=$1 and t.slug='loi-grid' and d.sent_status='draft'`, [args.document_id])).rows[0];
        if (!doc) throw new ToolError({ error: 'not_found' });
        const snapshot = doc.snapshot;
        if (!Array.isArray(snapshot?.fields) || !Array.isArray(snapshot?.reviews)) throw new ToolError({ error: 'invalid_snapshot' });
        const current = await confirmed(c, actor, doc.deal_id, snapshot.fields, 'read');
        if (JSON.stringify(current.snapshot.fields) !== JSON.stringify(snapshot.fields) || JSON.stringify(current.snapshot.reviews) !== JSON.stringify(snapshot.reviews)) throw new ToolError({ error: 'stale_snapshot' });
        const template = (await c.query("select source_path,template_version,field_map from doc_template where slug='loi-grid' and active")).rows[0];
        if (!template || template.field_map.unreviewed || template.field_map.template_kind !== 'xlsx'
          || template.template_version !== snapshot.template_version || template.source_path !== snapshot.source_path)
          throw new ToolError({ error: 'comparison_template_unavailable' });
        return { ok: true, schema_version: 'terms-comparison-validation.v1', status: 'draft',
          edits: comparisonEdits(current.edits), template: { source_path: template.source_path, template_version: template.template_version },
          basename: 'LOI-comparison-DRAFT' };
      },
    },
  };
}
