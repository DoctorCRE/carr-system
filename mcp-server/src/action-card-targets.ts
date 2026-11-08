// Resolve exact partner-owned cards without scanning Home or changing source records.
import { parseActionLinkRef, buildActionLink } from './action-links.js';
import { authorizationClassForActor } from './identity.js';
import { Validator, type Schema } from '@cfworker/json-schema';
import responseSchema from '../contracts/action-link-resolve.v2.json' with { type: 'json' };
import type { Actor, Db } from './verb-contract.ts';

type Kind = 'post_call_review' | 'feedback_event';
type Target = { kind: Kind; id: string; version: string };
type Operation = { action: string; verb: string; args: Record<string, unknown> };
type Card = { kind: 'reply_draft' | Kind; id: string; source_version: number | string;
  state: 'pending_approval' | 'pending_review' | 'stale' | 'retired' | 'resolved';
  detail?: Record<string, unknown> | null; actions: Operation[] };
type Response = { schema_version: 'action-link-resolve.v2'; ref: string; card: Card };
type Source = { source_version: string; state: 'pending_review' | 'resolved' | 'retired'; detail: Record<string, unknown> };
type Dependencies = {
  ToolError: new (payload: { error: string }) => Error;
  readTool(c: Db, actor: Actor, verb: string, args: Record<string, unknown>): Promise<unknown>;
};
const validator = new Validator(responseSchema as unknown as Schema);
export function parseActionCardResponse(value: unknown): Response {
  if (!validator.validate(value).valid) throw new Error('action_card_response_invalid');
  const response = value as Response;
  const card = response.card;
  if (card.kind !== 'reply_draft') {
    const identity = card.kind === 'post_call_review' ? 'session_id' : 'event_id';
    if (card.state === 'retired' ? card.detail !== null : !card.detail || card.detail[identity] !== card.id)
      throw new Error('action_card_source_mismatch');
  }
  for (const operation of card.actions) {
    if (card.kind === 'reply_draft'
      ? operation.args.proposal_id !== card.id || operation.args.base_version !== card.source_version
      : operation.args.session_id !== card.id) throw new Error('action_card_operation_mismatch');
  }
  return response;
}
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const VERSION = /^[A-Za-z0-9_-]{1,32}$/;
const CODES = { post_call_review: 'cr', feedback_event: 'fe' } as const;

export function buildActionCardRef(target: Target): string {
  if (!['post_call_review', 'feedback_event'].includes(target.kind) || !UUID.test(target.id) || !VERSION.test(target.version))
    throw new Error('action_link_input_invalid');
  const encoded = btoa(`${CODES[target.kind]}:${target.id}:${target.version}`).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `al1_${encoded}`;
}
function parse(ref: string): Target | null {
  const match = /^al1_([A-Za-z0-9_-]{1,120})$/.exec(ref);
  if (!match) return null;
  let identity: string;
  try { identity = atob(match[1].replace(/-/g, '+').replace(/_/g, '/')); } catch { return null; }
  const [code, id, version, ...rest] = identity.split(':');
  const kind = code === CODES.post_call_review ? 'post_call_review' : code === CODES.feedback_event ? 'feedback_event' : null;
  if (!kind || rest.length) return null;
  const target: Target = { kind, id, version };
  try { return buildActionCardRef(target) === ref ? target : null; } catch { return null; }
}

export function actionCardTargetTools({ ToolError, readTool }: Dependencies) {
  const refuse = (error = 'action_link_not_found'): never => { throw new ToolError({ error }); };
  return {
    'resolve-action-link-v2': {
      humanOnly: true,
      humanCallerOnly: true,
      writerConnection: true,
      description: 'Read one exact reply draft, post-call review or feedback event for its verified human owner. No Home scan or mutation. Foreign and unknown targets refuse identically. Stale and retired targets offer no actions.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        ref: { type: 'string', maxLength: 124 },
      }, required: ['ref'] },
      handler: async (c: Db, actor: Actor, args: { ref: string }): Promise<Response> => {
        if (authorizationClassForActor(actor) !== 'verified_partner') refuse('human_only_verb_requires_verified_partner');
        const target = parse(args.ref);
        if (target) {
          const row = (await c.query<{target: Source | null}>('select ops.resolve_action_card($1::uuid,$2::text,$3::uuid) as target',
            [actor.id, target.kind, target.id])).rows[0]?.target;
          if (!row) return refuse();
          const state = row.state === 'retired' ? 'retired' : row.source_version !== target.version ? 'stale' : row.state;
          return parseActionCardResponse({ schema_version: 'action-link-resolve.v2', ref: args.ref, card: {
            kind: target.kind, id: target.id, source_version: row.source_version, state,
            detail: state === 'retired' ? null : row.detail,
            actions: state === 'pending_review' ? [{ action: 'review', verb: 'review-call-outcome', args: { session_id: target.id } }] : [],
          } });
        }
        const reply = parseActionLinkRef(args.ref) as {kind: string; id: string; owner: string; version: number} | null;
        if (!reply || reply.kind !== 'reply_draft' || reply.owner !== actor.slug) return refuse();
        const row = (await c.query<{version: number; status: string; source_state: string}>(`select p.version,p.status,
          coalesce(a.email_context#>>'{lifecycle,state}','live') as source_state
          from email_proposal p join activity a on a.id=p.activity_id
          where p.id=$1::uuid and p.owner=$2 and p.kind='reply_draft'`, [reply.id, actor.slug])).rows[0];
        if (!row) return refuse();
        if (row.source_state !== 'live' || row.status !== 'pending') {
          return parseActionCardResponse({ schema_version: 'action-link-resolve.v2', ref: args.ref, card: {
            kind: 'reply_draft', id: reply.id, source_version: row.version,
            state: row.source_state !== 'live' ? 'retired' : 'resolved', actions: [],
          } });
        }
        const current = buildActionLink({ ...reply, version: row.version });
        const result = parseActionCardResponse(await readTool(c, actor, 'resolve-action-link', { ref: current.ref }));
        if (row.version !== reply.version) return parseActionCardResponse({ ...result, ref: args.ref, card: { ...result.card, state: 'stale', actions: [] } });
        return result;
      },
    },
  };
}
