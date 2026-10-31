// Compare confirmed firm history without implying complete archive or market coverage.
import { organizationTenantForActor } from './identity.js';
import { Validator, type Schema } from '@cfworker/json-schema';
import { ToolError } from './tool-error.ts';
import type { Actor, Db, VerbInputs } from './verb-contract.ts';

type TermReceipt = {
  canonical_fact_id: string; entity_ref: string; fact_key: string; version: number;
  value: string; unit: string; expense_basis: string | null;
  confidence: 'human_review' | 'independent_agreement';
  submarket: string; use_type: string; term_kind: 'counter' | 'executed'; term_date: string;
  effective_from: string; effective_to: string | null; eligible: boolean;
  context_source: { document_ref: string; reviewed_by_actor_id: string; reviewed_at: string };
  sources: { observation_id: string; document_ref: string; content_hash: string | null;
    page_start: number; page_end: number; extractor_version: string; confidence_tier: string; quarantined: boolean }[];
};
type Snapshot = { receipts: TermReceipt[]; coverage: {
  scope: 'recorded_firm_history_only'; history_coverage: 'unknown'; archive_documents: null;
  recorded_documents: number; observations: number; canonical_receipts: number; classified_receipts: number;
  quarantined_extractor_versions: string[];
} };
type Query = VerbInputs['read-market-terms'];
const inputSchema = { type: 'object', additionalProperties: false, properties: {
  submarket: { type: 'string', minLength: 1, maxLength: 200 },
  use_type: { type: 'string', minLength: 1, maxLength: 200 },
  as_of: { type: 'string', format: 'date' },
} } satisfies Schema;
const validator = new Validator(inputSchema, '2020-12', false);

/** Rebuild from one database snapshot. Never persist a stale copy of quarantined evidence. */
export function rebuildMarketTerms(snapshot: Snapshot, query: Query = {}) {
  const groups = new Map<string, TermReceipt[]>();
  for (const receipt of snapshot.receipts) {
    const key = JSON.stringify([receipt.entity_ref, receipt.fact_key, receipt.term_kind]);
    const group = groups.get(key) ?? [];
    group.push(receipt);
    groups.set(key, group);
  }
  const rows = [...groups.values()].flatMap(group => {
    group.sort((a, b) => a.effective_from.localeCompare(b.effective_from) || b.version - a.version);
    const periods = group.filter((receipt, index) => index === 0 || receipt.effective_from !== group[index - 1].effective_from);
    return periods.flatMap((receipt, index) => {
      const next = periods[index + 1]?.effective_from;
      const end = next && (!receipt.effective_to || next < receipt.effective_to) ? next : receipt.effective_to;
      if (!receipt.eligible || (query.submarket && receipt.submarket !== query.submarket)
        || (query.use_type && receipt.use_type !== query.use_type)
        || (query.as_of && (receipt.effective_from > query.as_of || (end && query.as_of >= end)))) return [];
      const { eligible, ...row } = receipt;
      return [{ ...row, effective_to: end, source_coverage: {
        supporting_observations: row.sources.length,
        supporting_documents: new Set(row.sources.map(source => source.document_ref)).size,
        unaffected_observations: row.sources.filter(source => !source.quarantined).length,
      } }];
    });
  }).sort((a, b) => a.entity_ref.localeCompare(b.entity_ref) || a.fact_key.localeCompare(b.fact_key)
    || a.term_kind.localeCompare(b.term_kind) || a.effective_from.localeCompare(b.effective_from));
  return { ok: true, schema_version: 'market-terms.v1', empty: rows.length === 0, rows,
    coverage: { ...snapshot.coverage, coverage_scope: 'tenant_all_recorded_sources',
      unclassified_receipts: snapshot.coverage.canonical_receipts - snapshot.coverage.classified_receipts,
      eligible_receipts: snapshot.receipts.filter(receipt => receipt.eligible).length,
      unsupported_receipts: snapshot.receipts.filter(receipt => !receipt.eligible).length,
      superseded_without_context: snapshot.coverage.classified_receipts - snapshot.receipts.length,
      comparable_rows: rows.length, comparable_deals: new Set(rows.map(row => row.entity_ref)).size },
  };
}

export function marketTermsTools() {
  return {
    'read-market-terms': {
      write: false,
      description: 'Compare confirmed economic terms from recorded firm history, filtered by exact submarket and use_type. Optional as_of selects half-open business effective periods. Returns rows with canonical receipt, counter/executed classification, term date, confidence basis, expense basis and source evidence. Amendments close the prior field period and comparable_deals counts distinct entities, not terms. Tenant-wide source coverage remains explicit even for empty results; archive size and history coverage are unknown. Unclassified receipts lack context; classified receipts are partitioned into eligible, unsupported, and superseded_without_context counts across the tenant, independent of filters and period deduplication. Contextless facts are never guessed into comparables. Quarantine is recomputed on every read; human-reviewed facts survive and rule-promoted facts require an unaffected independent pair. This is firm history, not external market comps.',
      inputSchema,
      handler: async (c: Db, actor: Actor, args: Query) => {
        if (!validator.validate(args).valid) throw new ToolError({ error: 'validation_error' });
        const { rows } = await c.query('select ops.market_terms_snapshot($1) snapshot', [organizationTenantForActor(actor)]);
        return rebuildMarketTerms(rows[0].snapshot, args);
      },
    },
  };
}
