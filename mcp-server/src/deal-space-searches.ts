// Persist deal briefs and bounded canonical-search runs behind one versioned interface.
import { Validator, type Schema } from '@cfworker/json-schema';
import { organizationTenantForActor } from './identity.js';
import { canExercisePartnerAuthority } from './partner-authority.js';
import { installTourDealAccess } from './tour-deal-access.js';
import { tourPropertySearchTools } from './tour-property-search.js';
import { ToolError } from './tool-error.ts';
import type { Actor, Db, WithEnvelope, WriteEvent, VerbInputs } from './verb-contract.ts';

const uuid = { type: 'string', format: 'uuid' } satisfies Schema;
const object = (properties: Record<string, Schema>) => ({ type: 'object', additionalProperties: false,
  properties, required: Object.keys(properties) } satisfies Schema);
const nullable = (schema: Schema) => ({ anyOf: [schema, { type: 'null' }] } satisfies Schema);
const sourceSchema = nullable(object({ client_id: uuid, source_ref: { type: 'string', minLength: 1, maxLength: 500 },
  observed_at: { type: 'string', format: 'date-time' } }));
const base = { deal_id: uuid, search_id: uuid };

export function dealSpaceSearchTools({ withEnvelope, writeEvent }: { withEnvelope: WithEnvelope; writeEvent: WriteEvent }) {
  const criteria: Schema = { ...tourPropertySearchTools({ withEnvelope, writeEvent, ToolError })['search-tour-properties'].inputSchema, type: 'object' };
  const saveSchema = object({ ...base, idempotency_key: uuid, expected_version: { type: 'integer', minimum: 0 },
    name: { type: 'string', minLength: 1, maxLength: 240 }, status: { enum: ['draft','saved','archived'] },
    criteria, requirements: { type: 'string', maxLength: 8000 }, client_prefill_source: sourceSchema });
  const runSchema = object({ ...base, idempotency_key: uuid, expected_version: { type: 'integer', minimum: 1 } });
  const readSchema = object({ deal_id: uuid, search_id: nullable(uuid) });

  async function authorize(c: Db, actor: Actor, args: object, schema: Schema) {
    if (!canExercisePartnerAuthority(actor)) throw new ToolError({ error: 'human_only' });
    if (!new Validator(schema, '2020-12', false).validate(args).valid) throw new ToolError({ error: 'space_search_input_invalid' });
    await installTourDealAccess(c, actor);
  }
  async function query(c: Db, sql: string, values: unknown[]) {
    try { return (await c.query<{ result: unknown }>(sql, values)).rows[0]?.result; }
    catch (error) {
      if (error instanceof Error && /^space_search_[a-z_]+$/.test(error.message)) throw new ToolError({ error: error.message });
      throw error;
    }
  }
  const response = (result: unknown) => {
    const value = { ok: true, schema_version: 'deal-space-searches.v1', search: result };
    if (!new Validator(spaceSearchResponseSchema, '2020-12', false).validate(value).valid)
      throw new ToolError({ error: 'space_search_response_invalid' });
    return value;
  };
  return {
    'save-deal-space-search-v1': {
      write: true, authorityOnly: true,
      description: 'deal-space-searches.v1. Create with expected_version=0 and a new search_id, or replace a brief at its exact current version. Stores canonical criteria plus full requirements and optional client prefill provenance. Criteria cursor must be null. Drafts and archived briefs cannot run. Restore an archived brief by saving status=saved. Does not query MLS, create a planner, or change a Tour.',
      inputSchema: saveSchema,
      handler: async (c: Db, actor: Actor, args: VerbInputs['save-deal-space-search-v1']) => {
        await authorize(c, actor, args, saveSchema);
        if (!args.name.trim() || args.criteria.cursor !== null || (args.criteria.min_square_feet !== null && args.criteria.max_square_feet !== null
          && args.criteria.min_square_feet > args.criteria.max_square_feet)) throw new ToolError({ error: 'space_search_input_invalid' });
        return withEnvelope(c, actor, 'save-deal-space-search-v1', args, async () => {
          const result = await query(c, 'select ops.save_deal_space_search($1,$2,$3::jsonb) result',
            [organizationTenantForActor(actor), actor.id, JSON.stringify(args)]);
          await writeEvent(c, actor, 'save-deal-space-search-v1', 'deal', args.deal_id,
            { field: 'space_search', new: { search_id: args.search_id }, idempotency_key: args.idempotency_key });
          return response(result);
        });
      },
    },
    'run-deal-space-search-v1': {
      write: true, authorityOnly: true,
      description: 'deal-space-searches.v1. Atomically rerun saved canonical criteria at an exact search version. Records server time, run criteria, up to 100 canonical property IDs and a pagination receipt. Failure preserves prior results. IDs can feed the existing Tour selection cart after review. Does not select properties or mutate a Tour.',
      inputSchema: runSchema,
      handler: async (c: Db, actor: Actor, args: VerbInputs['run-deal-space-search-v1']) => {
        await authorize(c, actor, args, runSchema);
        return withEnvelope(c, actor, 'run-deal-space-search-v1', args, async () => {
          const result = await query(c, 'select ops.run_deal_space_search($1,$2,$3,$4,$5) result',
            [organizationTenantForActor(actor), actor.id, args.deal_id, args.search_id, args.expected_version]);
          await writeEvent(c, actor, 'run-deal-space-search-v1', 'deal', args.deal_id,
            { field: 'space_search_run', new: { search_id: args.search_id }, idempotency_key: args.idempotency_key });
          return response(result);
        });
      },
    },
    'read-deal-space-searches-v1': {
      write: false, writerConnection: true, authorityOnly: true,
      description: 'deal-space-searches.v1. Reopen one saved search by deal and search ID, or list up to 100 newest briefs with search_id=null and explicit truncation. Includes archived briefs. Results become stale after changed criteria or 24 hours; prior run criteria remain visible. Requirements are descriptive and are not executed as filters. No MLS or publication authority.',
      inputSchema: readSchema,
      handler: async (c: Db, actor: Actor, args: VerbInputs['read-deal-space-searches-v1']) => {
        await authorize(c, actor, args, readSchema);
        const result = await query(c, 'select ops.read_deal_space_searches($1,$2,$3,$4) result',
          [organizationTenantForActor(actor), actor.id, args.deal_id, args.search_id]);
        const value = { ok: true, schema_version: 'deal-space-searches.v1', ...parseList(result) };
        if (!new Validator(spaceSearchListResponseSchema, '2020-12', false).validate(value).valid)
          throw new ToolError({ error: 'space_search_response_invalid' });
        return value;
      },
    },
  };
}

function parseList(value: unknown): { searches: unknown[]; truncated: boolean } {
  if (typeof value !== 'object' || value === null || !('searches' in value) || !Array.isArray(value.searches)
    || !('truncated' in value) || typeof value.truncated !== 'boolean') throw new ToolError({ error: 'space_search_response_invalid' });
  return { searches: value.searches, truncated: value.truncated };
}

// Canonical search owns filter validation. This response describes the persisted snapshot.
const searchSchema = object({ id: uuid, deal_id: uuid, version: { type: 'integer', minimum: 1 },
  name: { type: 'string' }, status: { enum: ['draft','saved','archived'] }, state: { enum: ['draft','saved','stale','archived'] },
  criteria: { type: 'object' }, requirements: { type: 'string' }, client_prefill_source: sourceSchema,
  last_run_at: nullable({ type: 'string', format: 'date-time' }), run_criteria: nullable({ type: 'object' }),
  result_property_ids: { type: 'array', maxItems: 100, uniqueItems: true, items: uuid }, has_more: { type: 'boolean' },
  next_cursor: nullable({ type: 'string' }), updated_at: { type: 'string', format: 'date-time' } });
export const spaceSearchResponseSchema = object({ ok: { const: true }, schema_version: { const: 'deal-space-searches.v1' }, search: searchSchema });
export const spaceSearchListResponseSchema = object({ ok: { const: true }, schema_version: { const: 'deal-space-searches.v1' },
  searches: { type: 'array', maxItems: 100, items: searchSchema }, truncated: { type: 'boolean' } });
