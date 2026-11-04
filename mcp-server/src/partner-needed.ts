// Resolve and acknowledge current partner-needed review cards.
import type { Actor, Db, WithEnvelope } from './verb-contract.ts';
import type { ToolError as ErrorType } from './tool-error.ts';
import { homeReviewReference } from './home-review.js';

const eventSchema = { type: 'string', format: 'uuid' };
export function partnerNeededTools({ withEnvelope, ToolError }: {
  withEnvelope: WithEnvelope; ToolError: typeof ErrorType;
}) {
  const unwrap = (result: { ok?: boolean; error?: string }) => {
    if (!result?.ok) throw new ToolError({ error: result?.error || 'invalid_input' });
    return result;
  };
  return {
    'read-partner-needed-card': {
      writerConnection: true,
      humanOnly: true,
      description: 'Resolve one pushed event for its authenticated recipient against current canonical sources, even beyond the Home snapshot. Returns actionable, resolved or unavailable. Never marks a push read.',
      inputSchema: { type: 'object', additionalProperties: false, properties: { event_id: eventSchema }, required: ['event_id'] },
      handler: async (c: Db, _actor: Actor, args: { event_id: string }) => {
        const result = (await c.query('select ops.read_partner_needed_card($1::uuid) result', [args.event_id])).rows[0].result;
        unwrap(result);
        if (result.item) result.item.review_reference = homeReviewReference(result.item);
        return result;
      },
    },
    'acknowledge-partner-review': {
      write: true,
      humanOnly: true,
      writerConnection: true,
      description: 'Acknowledge only the recipient’s current informational tour feedback revision. Call outcome review remains review-call-outcome. Does not send a push.',
      inputSchema: { type: 'object', additionalProperties: false, properties: { event_id: eventSchema, idempotency_key: eventSchema }, required: ['event_id','idempotency_key'] },
      handler: (c: Db, actor: Actor, args: { event_id: string; idempotency_key: string }) =>
        withEnvelope(c, actor, 'acknowledge-partner-review', args, async () => unwrap(
          (await c.query('select ops.acknowledge_partner_review($1::uuid,$2::uuid) result', [args.event_id,args.idempotency_key])).rows[0].result)),
    },
  };
}
