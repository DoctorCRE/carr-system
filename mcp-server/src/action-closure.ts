// Close partner-directed actions and critical dates with recorded reasons.
import { organizationTenantForActor } from './identity.js';
import { ToolError } from './tool-error.ts';
import type { Actor, Db, VerbInputs, WithEnvelope, WriteEvent } from './verb-contract.ts';

type ActionRow = {
  id: string;
  subject_type: 'deal' | 'client' | 'lead' | 'vendor';
  subject_id: string;
  owner_id: string;
  status: 'open' | 'done' | 'dropped';
  version: number;
};

type CriticalDateRow = {
  id: string;
  deal_id: string;
  status: 'open' | 'passed' | 'cleared' | 'dropped' | 'done';
  version: number;
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function actionClosureTools({ withEnvelope, writeEvent, canExercisePartnerAuthority }: {
  withEnvelope: WithEnvelope;
  writeEvent: WriteEvent;
  canExercisePartnerAuthority: (actor: Actor) => boolean;
}) {
  return {
    'complete-one-action': {
      write: true,
      humanOnly: true,
      description: "Complete exactly one open Home action by ID, including a call-created task. Requires partner authority and the action's current base_version. Leaves sibling actions and the deal plan unchanged. Refuses stale, closed, missing, or other-tenant actions. Retries reuse the same idempotency_key.",
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { type: 'string' },
        action_id: { type: 'string', format: 'uuid' },
        base_version: { type: 'integer', minimum: 1 },
      }, required: ['idempotency_key', 'action_id', 'base_version'] },
      handler: async (c: Db, actor: Actor, args: VerbInputs['complete-one-action']) => {
        if (!canExercisePartnerAuthority(actor)) throw new ToolError({ error: 'partner_required' });
        if (!args.idempotency_key) throw new ToolError({ error: 'missing_idempotency_key' });
        await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [args.idempotency_key]);
        return withEnvelope(c, actor, 'complete-one-action', args, async () => {
          if (typeof args.action_id !== 'string' || !UUID.test(args.action_id))
            throw new ToolError({ error: 'invalid_action_id' });
          if (!Number.isSafeInteger(args.base_version) || args.base_version < 1)
            throw new ToolError({ error: 'invalid_base_version' });
          const tenant = organizationTenantForActor(actor);
          const matches = (await c.query<Pick<ActionRow, 'subject_type' | 'subject_id'> & { source: 'next_action' | 'capture_post_call_action' }>(
            `select subject_type,subject_id,'next_action' as source
               from next_action where id=$1 and organization_tenant_id=$2
             union all
             select 'deal',deal_id,'capture_post_call_action'
               from capture_post_call_action where id=$1 and organization_tenant_id=$2`,
            [args.action_id, tenant])).rows;
          if (matches.length !== 1) throw new ToolError({ error: 'action_not_found' });
          const match = matches[0];
          if (match.subject_type === 'deal')
            await c.query("select pg_advisory_xact_lock(hashtextextended('deal-items:' || $1,0))", [match.subject_id]);
          const row = (await c.query<Pick<ActionRow, 'id' | 'owner_id' | 'status' | 'version'> & { description: string }>(
            `select id,owner_id,status,version,description from ${match.source}
              where id=$1 and organization_tenant_id=$2 for update`, [args.action_id, tenant])).rows[0];
          if (!row) throw new ToolError({ error: 'action_not_found' });
          if (row.version !== args.base_version)
            throw new ToolError({ error: 'version_conflict', current_version: row.version });
          if (row.status !== 'open') throw new ToolError({ error: 'action_not_open', status: row.status });
          const postCall = match.source === 'capture_post_call_action';
          const saved = (await c.query<{ version: number }>(
            `update ${match.source} set status='done',${postCall ? 'completed_at=clock_timestamp()' : 'updated_by=$2'}
              where id=$1 returning version`, postCall ? [row.id] : [row.id, actor.id])).rows[0];
          await writeEvent(c, actor, 'complete-one-action', match.subject_type, match.subject_id, {
            field: 'status', old: { status: 'open', owner_id: row.owner_id, version: row.version },
            new: { [postCall ? 'post_call_action_id' : 'next_action_id']: row.id,
              [postCall ? 'post_call_action' : 'next_action']: row.description, status: 'done', outcome: null, owner_id: row.owner_id, version: saved.version },
            idempotency_key: args.idempotency_key,
          });
          const event = (await c.query<{ id: string }>(
            "select id from event where verb='complete-one-action' and idempotency_key=$1 and organization_tenant_id=$2",
            [args.idempotency_key, tenant])).rows[0];
          return { ok: true, action_id: row.id, status: 'done', version: saved.version, completion_event_id: event.id };
        });
      },
    },
    'reopen-one-action': {
      write: true,
      humanOnly: true,
      description: "Undo one exact complete-one-action event. Requires partner authority, action_id, completion_event_id and the unchanged completion base_version. Refuses later edits, owner changes, unrelated events, closed or missing tasks and other tenants. Retries reuse the same idempotency_key and replay the original receipt.",
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { type: 'string' },
        action_id: { type: 'string', format: 'uuid' },
        completion_event_id: { type: 'string', format: 'uuid' },
        base_version: { type: 'integer', minimum: 1 },
      }, required: ['idempotency_key', 'action_id', 'completion_event_id', 'base_version'] },
      handler: async (c: Db, actor: Actor, args: VerbInputs['reopen-one-action']) => {
        if (!canExercisePartnerAuthority(actor)) throw new ToolError({ error: 'partner_required' });
        if (!args.idempotency_key) throw new ToolError({ error: 'missing_idempotency_key' });
        await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [args.idempotency_key]);
        return withEnvelope(c, actor, 'reopen-one-action', args, async () => {
          if (typeof args.action_id !== 'string' || !UUID.test(args.action_id))
            throw new ToolError({ error: 'invalid_action_id' });
          if (typeof args.completion_event_id !== 'string' || !UUID.test(args.completion_event_id))
            throw new ToolError({ error: 'invalid_completion' });
          if (!Number.isSafeInteger(args.base_version) || args.base_version < 1)
            throw new ToolError({ error: 'invalid_base_version' });
          const tenant = organizationTenantForActor(actor);
          const matches = (await c.query<{ source: 'next_action' | 'capture_post_call_action'; subject_type: string; subject_id: string }>(
            `select 'next_action' as source,subject_type,subject_id from next_action
              where id=$1 and organization_tenant_id=$2
             union all select 'capture_post_call_action','deal',deal_id from capture_post_call_action
              where id=$1 and organization_tenant_id=$2`, [args.action_id, tenant])).rows;
          if (matches.length !== 1) throw new ToolError({ error: 'action_not_found' });
          const match = matches[0];
          if (match.subject_type === 'deal')
            await c.query("select pg_advisory_xact_lock(hashtextextended('deal-items:' || $1,0))", [match.subject_id]);
          const postCall = match.source === 'capture_post_call_action';
          const row = (await c.query<Pick<ActionRow, 'id' | 'owner_id' | 'status' | 'version' | 'subject_type' | 'subject_id'>>(
            `select id,owner_id,status,version,${postCall ? "'deal' as subject_type,deal_id as subject_id" : 'subject_type,subject_id'}
               from ${match.source} where id=$1 and organization_tenant_id=$2 for update`, [args.action_id, tenant])).rows[0];
          if (!row) throw new ToolError({ error: 'action_not_found' });
          if (row.version !== args.base_version)
            throw new ToolError({ error: 'version_conflict', current_version: row.version });
          if (row.status !== 'done') throw new ToolError({ error: 'invalid_completion', status: row.status });
          const event = (await c.query<{ id: string }>(
            `select id from event where id=$1 and organization_tenant_id=$2
              and verb='complete-one-action' and field='status' and subject_type=$3 and subject_id=$4
              and old_value->>'status'='open' and new_value->>'status'='done'
              and new_value->>$5=$6 and new_value->>'owner_id'=$7
              and new_value->>'version'=$8 and old_value->>'owner_id'=$7`,
            [args.completion_event_id, tenant, row.subject_type, row.subject_id,
              postCall ? 'post_call_action_id' : 'next_action_id', row.id, row.owner_id, String(row.version)])).rows[0];
          if (!event) throw new ToolError({ error: 'invalid_completion' });
          const saved = (await c.query<{ version: number }>(
            `update ${match.source} set status='open',${postCall ? 'completed_at=null' : 'updated_by=$2'}
              where id=$1 returning version`, postCall ? [row.id] : [row.id, actor.id])).rows[0];
          await writeEvent(c, actor, 'reopen-one-action', row.subject_type, row.subject_id, {
            field: 'status', old: { action_id: row.id, status: 'done', owner_id: row.owner_id, version: row.version },
            new: { action_id: row.id, status: 'open', owner_id: row.owner_id, version: saved.version,
              completion_event_id: event.id }, idempotency_key: args.idempotency_key,
          });
          return { ok: true, action_id: row.id, status: 'open', version: saved.version, completion_event_id: event.id };
        });
      },
    },
    'drop-action': {
      write: true,
      humanOnly: true,
      description: "Drop one open next action of any owner, including a retired seat, without creating a replacement. Requires Joe/Dell partner authority, the action's current base_version, and a reason recorded in subject history. Refuses missing, non-open, or changed actions. Reuse the same idempotency_key for retries.",
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { type: 'string' },
        action_id: { type: 'string', format: 'uuid' },
        base_version: { type: 'integer', minimum: 1 },
        reason: { type: 'string', minLength: 1 },
      }, required: ['idempotency_key', 'action_id', 'base_version', 'reason'] },
      handler: async (c: Db, actor: Actor, args: VerbInputs['drop-action']) => {
        if (!canExercisePartnerAuthority(actor)) throw new ToolError({ error: 'partner_required' });
        if (!args.idempotency_key) throw new ToolError({ error: 'missing_idempotency_key' });
        await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [args.idempotency_key]);
        return withEnvelope(c, actor, 'drop-action', args, async () => {
          if (typeof args.action_id !== 'string' || !UUID.test(args.action_id))
            throw new ToolError({ error: 'invalid_action_id' });
          if (!Number.isSafeInteger(args.base_version) || args.base_version < 1)
            throw new ToolError({ error: 'invalid_base_version' });
          if (typeof args.reason !== 'string' || !args.reason.trim())
            throw new ToolError({ error: 'invalid_reason' });
          const row = (await c.query<ActionRow>(
            'select id,subject_type,subject_id,owner_id,status,version from next_action where id=$1 for update',
            [args.action_id])).rows[0];
          if (!row) throw new ToolError({ error: 'action_not_found', action_id: args.action_id });
          if (row.status !== 'open') throw new ToolError({ error: 'action_not_open', action_id: row.id, status: row.status });
          if (row.version !== args.base_version)
            throw new ToolError({ error: 'version_conflict', action_id: row.id, current_version: row.version });
          const saved = (await c.query<{ version: number }>(
            "update next_action set status='dropped',updated_by=$2 where id=$1 returning version", [row.id, actor.id])).rows[0];
          await writeEvent(c, actor, 'drop-action', row.subject_type, row.subject_id, {
            field: 'status',
            old: { next_action_id: row.id, owner_id: row.owner_id, status: row.status, version: row.version },
            new: { next_action_id: row.id, owner_id: row.owner_id, status: 'dropped', version: saved.version, reason: args.reason },
            human_quote: args.reason, idempotency_key: args.idempotency_key,
          });
          return { ok: true, action_id: row.id, status: 'dropped', version: saved.version };
        });
      },
    },
    'resolve-critical-date': {
      write: true,
      humanOnly: true,
      description: "Resolve one open critical date as passed, dropped, or done. Requires Joe/Dell partner authority and a reason recorded in deal history. Refuses missing or already closed dates. Reuse the same idempotency_key for retries.",
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { type: 'string' },
        critical_date_id: { type: 'string', format: 'uuid' },
        resolution: { type: 'string', enum: ['passed', 'dropped', 'done'] },
        reason: { type: 'string', minLength: 1 },
      }, required: ['idempotency_key', 'critical_date_id', 'resolution', 'reason'] },
      handler: async (c: Db, actor: Actor, args: VerbInputs['resolve-critical-date']) => {
        if (!canExercisePartnerAuthority(actor)) throw new ToolError({ error: 'partner_required' });
        if (!args.idempotency_key) throw new ToolError({ error: 'missing_idempotency_key' });
        await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [args.idempotency_key]);
        return withEnvelope(c, actor, 'resolve-critical-date', args, async () => {
          if (typeof args.critical_date_id !== 'string' || !UUID.test(args.critical_date_id))
            throw new ToolError({ error: 'invalid_critical_date_id' });
          if (!['passed', 'dropped', 'done'].includes(args.resolution))
            throw new ToolError({ error: 'invalid_resolution' });
          if (typeof args.reason !== 'string' || !args.reason.trim())
            throw new ToolError({ error: 'invalid_reason' });
          const row = (await c.query<CriticalDateRow>(
            'select id,deal_id,status,version from critical_date where id=$1 for update',
            [args.critical_date_id])).rows[0];
          if (!row) throw new ToolError({ error: 'critical_date_not_found', critical_date_id: args.critical_date_id });
          if (row.status !== 'open')
            throw new ToolError({ error: 'critical_date_not_open', critical_date_id: row.id, status: row.status });
          const saved = (await c.query<{ version: number }>(
            'update critical_date set status=$2,updated_by=$3 where id=$1 returning version',
            [row.id, args.resolution, actor.id])).rows[0];
          await writeEvent(c, actor, 'resolve-critical-date', 'deal', row.deal_id, {
            field: 'status',
            old: { critical_date_id: row.id, status: row.status, version: row.version },
            new: { critical_date_id: row.id, status: args.resolution, version: saved.version, reason: args.reason },
            human_quote: args.reason, idempotency_key: args.idempotency_key,
          });
          return { ok: true, critical_date_id: row.id, status: args.resolution, version: saved.version };
        });
      },
    },
  };
}
