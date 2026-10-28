// WR-000441 LEADS: an unknown sender whose email shows business intent becomes
// one row in the candidate reservoir (claim-card), where a partner promotes it
// with promote-pool or declines it with decline-candidate. This verb never
// creates a party or a lead and never reopens a row a partner already judged.
// The intake's model read (lib/unresolved_email.py) may say what the email
// applies to: an open deal, a known record at a new address, or a new matter.
// That reading is checked against open records, kept in source_row.reading and
// spelled out in score_basis; it files nothing.
import { sha256 } from './sha256.ts';
import { ToolError } from './tool-error.ts';
import type { Actor, Db, VerbInputs, WithEnvelope, WriteEvent } from './verb-contract.ts';

const ADDRESS = /^[a-z0-9._%+'-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+$/;
const SOURCE = 'outlook-intake';
const APPLIES_TO = ['existing_deal', 'existing_party', 'new_matter'] as const;

export function emailLeadProposalTools({ withEnvelope, writeEvent }: { withEnvelope: WithEnvelope; writeEvent: WriteEvent }) {
  const fail = (error: string): never => { throw new ToolError({ error }); };
  // The reading and its score_basis sentence, or null for a proposal without one.
  async function reading(c: Db, args: VerbInputs['propose-email-lead']) {
    const { applies_to: applies, deal, party, evidence_quote: quote, confidence } = args;
    if (applies === undefined) {
      if (deal !== undefined || party !== undefined || quote !== undefined || confidence !== undefined)
        fail('email_lead_input_invalid');
      return null;
    }
    if (typeof confidence !== 'number' || (applies === 'existing_deal') !== (deal !== undefined) ||
        (applies === 'existing_party') !== (party !== undefined))
      fail('email_lead_input_invalid');
    let sentence = ' Possible new lead or matter.';
    if (applies === 'existing_deal') {
      const row = (await c.query(`select name from deal where id=$1 and phase<>'closed' and closed_on is null
        and invoiced_on is null`, [deal])).rows[0];
      if (!row) fail('email_lead_deal_invalid');
      sentence = ` Applies to open deal ${row.name}.`;
    }
    if (applies === 'existing_party') {
      const row = (await c.query(`select display_name from v_ref_index where ref=$1
        and subject_type in ('client','lead','vendor') and not merged`, [party])).rows;
      if (row.length !== 1) fail('email_lead_party_invalid');
      sentence = ` Likely a new address for ${row[0].display_name} (${party}).`;
    }
    return { sentence, value: { applies_to: applies, deal_id: deal?.toLowerCase() ?? null, party_ref: party ?? null,
      evidence_quote: quote ?? null, confidence } };
  }
  return {
    'propose-email-lead': {
      write: true,
      description: 'Queue an unknown email sender with business intent for partner review on the claim card (candidate_pool lane outlook-intake). Exact address only. Optional applies_to records the intake model read of what the email applies to (existing_deal with an open deal UUID, existing_party with a client, lead or vendor ref, or new_matter), with confidence and an optional evidence_quote; it is spelled out in score_basis and files nothing. The server never sees the email, so it checks only the length of the quote: the intake drops any quote that is not literal, and a quote from another caller is unverified. Returns known_party, without writing, when any party already carries the address; returns the existing row when the address was proposed before, whatever a partner decided, adding a first reading (reasoned true) only to a row still in the pool without one. Never creates a party or lead; promote-pool or decline-candidate is the human decision.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { type: 'string', minLength: 1, maxLength: 200 },
        address: { type: 'string', minLength: 3, maxLength: 320 },
        display_name: { type: ['string', 'null'], maxLength: 200 },
        summary: { type: 'string', minLength: 1, maxLength: 300 },
        first_seen_at: { type: 'string', format: 'date-time' },
        message_ref: { type: 'string', minLength: 1, maxLength: 100 },
        applies_to: { type: 'string', enum: [...APPLIES_TO] },
        deal: { type: 'string', format: 'uuid' },
        party: { type: 'string', minLength: 3, maxLength: 40 },
        evidence_quote: { type: 'string', minLength: 10, maxLength: 500 },
        confidence: { type: 'number', minimum: 0, maximum: 1 },
      }, required: ['idempotency_key', 'address', 'summary', 'first_seen_at', 'message_ref'] },
      handler: (c: Db, actor: Actor, args: VerbInputs['propose-email-lead']) => withEnvelope(c, actor, 'propose-email-lead', args, async () => {
        const address = String(args.address || '').trim().toLowerCase();
        const summary = String(args.summary || '').trim();
        const seen = Date.parse(args.first_seen_at);
        if (!ADDRESS.test(address) || !summary || summary.length > 300 || !Number.isFinite(seen) ||
            seen > Date.now() + 300_000 || !String(args.message_ref || '').trim())
          fail('email_lead_input_invalid');
        const read = await reading(c, args);
        const known = (await c.query(
          `select 1 from party p,
             regexp_matches(lower(coalesce(p.email, '')), '([a-z0-9._%+''-]+@[a-z0-9-]+(?:\\.[a-z0-9-]+)+)', 'g') m
           where m[1] = $1 limit 1`, [address])).rows.length > 0;
        if (known) return { ok: true, contract: 'email-lead-proposal.v1', proposed: false, reason: 'known_party' };
        const sourceKey = `email-sha256:${sha256(address)}`;
        const name = String(args.display_name || '').trim() || address;
        const basis = `Inbound email: ${summary}${read?.sentence ?? ''}`;
        const row = (await c.query(
          `insert into candidate_pool (source, source_key, source_row, name, email, score_basis, created_by, updated_by)
           values ($1, $2, $3, $4, $5, $6, $7, $7)
           on conflict (source, source_key) do nothing returning id, status`,
          [SOURCE, sourceKey, JSON.stringify({ message_ref: args.message_ref, first_seen_at: new Date(seen).toISOString(),
            ...(read ? { reading: read.value } : {}) }),
            name, address, basis, actor.id])).rows[0];
        if (!row) {
          const prior = (await c.query(`select id, status, source_row ? 'reading' as reasoned
            from candidate_pool where source=$1 and source_key=$2 for update`, [SOURCE, sourceKey])).rows[0];
          const reasoned = Boolean(read) && prior.status === 'pool' && !prior.reasoned;
          if (reasoned) {
            await c.query(`update candidate_pool set source_row=source_row || $2::jsonb, score_basis=$3,
              version=version+1, updated_by=$4, updated_at=now() where id=$1`,
              [prior.id, JSON.stringify({ reading: read!.value }), basis, actor.id]);
            await writeEvent(c, actor, 'propose-email-lead', 'candidate_pool', prior.id,
              { new: { lane: SOURCE, message_ref: args.message_ref, reading: read!.value.applies_to }, cause: 'ingest_email',
                idempotency_key: args.idempotency_key });
          }
          return { ok: true, contract: 'email-lead-proposal.v1', proposed: false, reason: 'already_in_queue',
            reasoned, pool_id: prior.id, status: prior.status };
        }
        await writeEvent(c, actor, 'propose-email-lead', 'candidate_pool', row.id,
          { new: { lane: SOURCE, message_ref: args.message_ref, ...(read ? { reading: read.value.applies_to } : {}) },
            cause: 'ingest_email', idempotency_key: args.idempotency_key });
        return { ok: true, contract: 'email-lead-proposal.v1', proposed: true, pool_id: row.id, status: row.status };
      }),
    },
  };
}
