// WR-000441 LEADS: an unknown sender whose email shows business intent becomes
// one row in the candidate reservoir (claim-card), where a partner promotes it
// with promote-pool or declines it with decline-candidate. This verb never
// creates a party or a lead and never reopens a row a partner already judged.
import { sha256 } from './sha256.ts';
import { ToolError } from './tool-error.ts';

const ADDRESS = /^[a-z0-9._%+'-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+$/;
const SOURCE = 'outlook-intake';

export function emailLeadProposalTools({ withEnvelope, writeEvent }) {
  const fail = error => { throw new ToolError({ error }); };
  return {
    'propose-email-lead': {
      write: true,
      description: 'Queue an unknown email sender with business intent for partner review on the claim card (candidate_pool lane outlook-intake). Exact address only. Returns known_party, without writing, when any party already carries the address; returns the existing row when the address was proposed before, whatever a partner decided. Never creates a party or lead; promote-pool or decline-candidate is the human decision.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { type: 'string', minLength: 1, maxLength: 200 },
        address: { type: 'string', minLength: 3, maxLength: 320 },
        display_name: { type: ['string', 'null'], maxLength: 200 },
        summary: { type: 'string', minLength: 1, maxLength: 300 },
        first_seen_at: { type: 'string', format: 'date-time' },
        message_ref: { type: 'string', minLength: 1, maxLength: 100 },
      }, required: ['idempotency_key', 'address', 'summary', 'first_seen_at', 'message_ref'] },
      handler: (c, actor, args) => withEnvelope(c, actor, 'propose-email-lead', args, async () => {
        const address = String(args.address || '').trim().toLowerCase();
        const summary = String(args.summary || '').trim();
        const seen = Date.parse(args.first_seen_at);
        if (!ADDRESS.test(address) || !summary || summary.length > 300 || !Number.isFinite(seen) ||
            seen > Date.now() + 300_000 || !String(args.message_ref || '').trim())
          fail('email_lead_input_invalid');
        const known = (await c.query(
          `select 1 from party p,
             regexp_matches(lower(coalesce(p.email, '')), '([a-z0-9._%+''-]+@[a-z0-9-]+(?:\\.[a-z0-9-]+)+)', 'g') m
           where m[1] = $1 limit 1`, [address])).rows.length > 0;
        if (known) return { ok: true, contract: 'email-lead-proposal.v1', proposed: false, reason: 'known_party' };
        const sourceKey = `email-sha256:${sha256(address)}`;
        const name = String(args.display_name || '').trim() || address;
        const row = (await c.query(
          `insert into candidate_pool (source, source_key, source_row, name, email, score_basis, created_by, updated_by)
           values ($1, $2, $3, $4, $5, $6, $7, $7)
           on conflict (source, source_key) do nothing returning id, status`,
          [SOURCE, sourceKey, JSON.stringify({ message_ref: args.message_ref, first_seen_at: new Date(seen).toISOString() }),
            name, address, `Inbound email: ${summary}`, actor.id])).rows[0];
        if (!row) {
          const prior = (await c.query('select id, status from candidate_pool where source=$1 and source_key=$2',
            [SOURCE, sourceKey])).rows[0];
          return { ok: true, contract: 'email-lead-proposal.v1', proposed: false, reason: 'already_in_queue',
            pool_id: prior.id, status: prior.status };
        }
        await writeEvent(c, actor, 'propose-email-lead', 'candidate_pool', row.id,
          { new: { lane: SOURCE, message_ref: args.message_ref }, cause: 'ingest_email',
            idempotency_key: args.idempotency_key });
        return { ok: true, contract: 'email-lead-proposal.v1', proposed: true, pool_id: row.id, status: row.status };
      }),
    },
  };
}
