// Load one current Home review without taking ownership of its domain commands.
import { authorizationClassForActor } from './identity.js';

export const HOME_REVIEW_CONTRACT = Object.freeze({
  schema_version: 'home-review.v1',
  read_verb: 'read-home-review',
  reference: ['kind', 'item_id', 'source_version', 'operations'],
  kinds: {
    action: ['complete-one-action'],
    reply_draft: ['edit-email-draft', 'dismiss-email-proposal', 'submit-email-draft'],
    fact_review: ['resolve-fact-review'],
  },
  refusals: ['home_review_not_found', 'home_review_stale', 'home_review_resolved', 'home_review_unsupported', 'human_only_verb_requires_verified_partner'],
  submission: 'Call the named existing domain verb with the displayed arguments, a fresh idempotency key and its required human input. Owners revalidate authority, current versions and semantics. Home has no approval or mutation verb. Refresh Home after a receipt. Retain completion_event_id and version from complete-one-action for reopen-one-action Undo.',
  coverage: 'Only projected open actions, pending reply drafts and pending fact reviews. Processing, notifications, tour feedback and reply recovery are unsupported. Pending call candidates, email next steps, overdue assessments and lead drafts are outside this projection.',
});

export function homeReviewReference(item) {
  const kind = ['next_action', 'capture_post_call_action'].includes(item.source_type) ? 'action'
    : item.source_type === 'email_proposal' ? 'reply_draft'
      : item.source_type === 'fact_review' ? 'fact_review' : null;
  if (!kind || item.source_state !== 'needs_attention') return null;
  return { kind, item_id: item.item_id, source_version: item.source_version,
    operations: [...HOME_REVIEW_CONTRACT.kinds[kind]] };
}

export function homeReviewTools({ ToolError, readTool }) {
  const fail = error => { throw new ToolError({ error }); };
  return {
    'read-home-review': {
      writerConnection: true,
      humanOnly: true,
      description: 'Load one exact actor-scoped Home card at its source_version. Read-only. Returns current detail and arguments for existing domain commands, never a generic approval. Refuses stale, resolved, missing and unsupported cards. Mailbox-owned replies reuse email proposal hydration. Domain writes still require their own current versions and human authority.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        item_id: { type: 'string', minLength: 1, maxLength: 1000 },
        source_version: { type: 'string', minLength: 1, maxLength: 1000 },
      }, required: ['item_id', 'source_version'] },
      handler: async (c, actor, args) => {
        if (authorizationClassForActor(actor) !== 'verified_partner') fail('human_only_verb_requires_verified_partner');
        const current = async () => {
          const snapshot = await readTool(c, actor, 'read-home-inbox-v3', {});
          return snapshot.items.find(item => item.item_id === args.item_id) ?? fail('home_review_not_found');
        };
        const item = await current();
        if (item.source_version !== args.source_version) fail('home_review_stale');
        if (item.source_state === 'done') fail('home_review_resolved');
        const reference = item.review_reference;
        if (!reference) fail('home_review_unsupported');
        let detail, operations;
        if (reference.kind === 'action') {
          if (!item.action_id || !Number.isInteger(item.action_version)) fail('home_review_unsupported');
          detail = { title: item.title, due_on: item.due_on, deal_id: item.deal_id };
          operations = [{ verb: 'complete-one-action', args: { action_id: item.action_id, base_version: item.action_version } }];
        } else if (reference.kind === 'reply_draft') {
          const result = await readTool(c, actor, 'list-email-proposals', { proposal_id: item.source_id });
          detail = result.proposals[0];
          if (!detail) fail('home_review_resolved');
          if (detail.kind !== 'reply_draft' || detail.status !== 'pending') fail('home_review_unsupported');
          const bound = { proposal_id: detail.id, base_version: detail.version };
          operations = [{ verb: 'edit-email-draft', args: bound }, { verb: 'dismiss-email-proposal', args: bound }];
          if (detail.confirm_verb === 'submit-email-draft') operations.push({ verb: 'submit-email-draft', args: {
            ...bound, subject: detail.reply_draft.subject, body: detail.reply_draft.body,
            to: detail.reply_draft.to, cc: detail.reply_draft.cc,
          } });
        } else {
          const [entity_ref, fact_key] = JSON.parse(item.source_id);
          const result = await readTool(c, actor, 'list-fact-review', { entity_ref, limit: 100 });
          detail = result.items.find(field => field.fact_key === fact_key);
          if (!detail?.pending) fail('home_review_resolved');
          operations = [{ verb: 'resolve-fact-review', decisions: ['approve', 'correct', 'reject'], args: {
            entity_ref, fact_key, base_version: detail.canonical_version, review_version: detail.review_version,
          } }];
        }
        if ((await current()).source_version !== args.source_version) fail('home_review_stale');
        return { ok: true, schema_version: 'home-review.v1', reference, detail, operations };
      },
    },
  };
}
