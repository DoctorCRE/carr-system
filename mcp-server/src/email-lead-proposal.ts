// WR-000441 LEADS: an unknown sender whose email shows business intent becomes
// one row in the candidate reservoir (claim-card). propose-email-lead never
// creates a party or a lead and never reopens a row a partner already judged.
// The intake's model read (lib/unresolved_email.py) may say what the email
// applies to: an open deal, a known record at a new address, or a new matter.
// That reading is checked against open records, kept in source_row.reading and
// spelled out in score_basis; it files nothing.
// The mailbox owner judges the row: confirm-email-lead teaches the record layer
// who the sender is so the next intake run files their mail through the normal
// path, and decline-candidate says no. An email quote is not research, so a
// confirmation never passes through promote-pool's research gate or claims it.
import { derivedIdempotencyKey } from './derived-key.ts';
import { sha256 } from './sha256.ts';
import { ToolError } from './tool-error.ts';
import { mailboxOwnerForActor, partnerAuthoritySlugForActor } from './partner-authority.js';
import type { Actor, Db, DelegatedHandler, VerbInputs, WithEnvelope, WriteEvent } from './verb-contract.ts';

const ADDRESS = /^[a-z0-9._%+'-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+$/;
const SOURCE = 'outlook-intake';
const APPLIES_TO = ['existing_deal', 'existing_party', 'new_matter', 'not_business'] as const;
const CONFIRMABLE: readonly string[] = APPLIES_TO.slice(0, 3);
const PARTICIPANT_ROLE = 'email_contact';

export function emailLeadProposalTools({ withEnvelope, writeEvent, updatePartyContact, newLead }: {
  withEnvelope: WithEnvelope; writeEvent: WriteEvent; updatePartyContact: DelegatedHandler; newLead: DelegatedHandler;
}) {
  const fail = (error: string, extra: Record<string, unknown> = {}): never => { throw new ToolError({ error, ...extra }); };
  async function openDeal(c: Db, id: string) {
    const row = (await c.query(`select id, name from deal where id=$1 and phase<>'closed' and closed_on is null
      and invoiced_on is null`, [id])).rows[0];
    return row ?? fail('email_lead_deal_invalid');
  }
  async function contact(c: Db, ref: string) {
    const rows = (await c.query(`select display_name, party_id from v_ref_index where ref=$1
      and subject_type in ('client','lead','vendor') and not merged`, [ref])).rows;
    return rows.length === 1 ? rows[0] : fail('email_lead_party_invalid');
  }
  // Every party whose email field carries the address; intake matches any address written there.
  async function carriers(c: Db, address: string): Promise<string[]> {
    return (await c.query(`select distinct p.id from party p,
        regexp_matches(lower(coalesce(p.email, '')), '([a-z0-9._%+''-]+@[a-z0-9-]+(?:\\.[a-z0-9-]+)+)', 'g') m
      where m[1] = $1`, [address])).rows.map(row => row.id);
  }
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
    if (applies === 'existing_deal') sentence = ` Applies to open deal ${(await openDeal(c, deal!)).name}.`;
    if (applies === 'existing_party') sentence = ` Likely a new address for ${(await contact(c, party!)).display_name} (${party}).`;
    return { sentence, value: { applies_to: applies, deal_id: deal?.toLowerCase() ?? null, party_ref: party ?? null,
      evidence_quote: quote ?? null, confidence } };
  }
  return {
    'propose-email-lead': {
      write: true,
      description: 'Queue an unknown email sender with business intent for partner review on the claim card (candidate_pool lane outlook-intake). Mailbox ownership derives from authenticated partner authority; ownerless legacy callers remain outside the mailbox read. A not_business reading records only a mailbox-scoped message hash for exclusion counts and creates no candidate. Exact address only. Optional applies_to records the intake model read of what the email applies to (existing_deal with an open deal UUID, existing_party with a client, lead or vendor ref, or new_matter), with confidence and an optional evidence_quote; it is spelled out in score_basis and files nothing. The server never sees the email, so it checks only the length of the quote: the intake drops any quote that is not literal, and a quote from another caller is unverified. Returns known_party, without writing, when any party already carries the address; returns the existing row when the address was proposed before, whatever a partner decided, adding a first reading (reasoned true) only to a row still in the pool without one. Never creates a party or lead; promote-pool or decline-candidate is the human decision.',
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
        const owner = partnerAuthoritySlugForActor(actor);
        if (args.applies_to === 'not_business') {
          if (!owner) fail('email_sender_identity_required');
          if (args.deal !== undefined || args.party !== undefined || args.evidence_quote !== undefined)
            fail('email_lead_input_invalid');
          await c.query(`insert into email_lead_exclusion(mailbox_owner,message_hash) values($1,$2)
            on conflict do nothing`, [owner, sha256(args.message_ref)]);
          return { ok: true, contract: 'email-lead-proposal.v1', proposed: false, reason: 'not_business' };
        }
        const read = await reading(c, args);
        if ((await carriers(c, address)).length) return { ok: true, contract: 'email-lead-proposal.v1', proposed: false, reason: 'known_party' };
        const sourceKey = `email-sha256:${owner ? `${owner}:` : ''}${sha256(address)}`;
        const name = String(args.display_name || '').trim() || address;
        const basis = `Inbound email: ${summary}${read?.sentence ?? ''}`;
        const row = (await c.query(
          `insert into candidate_pool (source, source_key, source_row, name, email, score_basis, created_by, updated_by)
           values ($1, $2, $3, $4, $5, $6, $7, $7)
           on conflict (source, source_key) do nothing returning id, status`,
          [SOURCE, sourceKey, JSON.stringify({ message_ref: args.message_ref, first_seen_at: new Date(seen).toISOString(),
            ...(owner ? { mailbox_owner: owner } : {}), ...(read ? { reading: read.value } : {}) }),
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
    'confirm-email-lead': {
      write: true,
      description: 'The mailbox owner confirms one pending outlook-intake lead proposal from list-email-proposals mode=lead_proposals, with its pool_id and base_version, so the next intake run files the sender\'s mail through the normal path. applies_to defaults to the stored reading and may be overridden: existing_party appends the address to that client, lead or vendor record through update-party-contact; existing_deal attaches the sender to that open deal as an email_contact participant, which the intake reads from v_email_deal_contact; new_matter creates a lead through new-lead (stage defaults to new, owner is the mailbox owner) with source_type email. deal is required for existing_deal and party for existing_party; with the stored outcome they default to its deal or party. A sender party is minted only when no party carries the address: name and email only, never marked researched. The stored evidence quote is untrusted email text, kept as provenance in the event and the new lead\'s source_detail. The row leaves Home as confirmed, or promoted for a new lead. Decline with decline-candidate. Only the mailbox owner, or that partner\'s local agent, may confirm; another mailbox\'s row is not found.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { type: 'string', minLength: 1, maxLength: 200 },
        pool_id: { type: 'string', format: 'uuid' },
        base_version: { type: 'integer', minimum: 1 },
        applies_to: { type: 'string', enum: [...CONFIRMABLE] },
        deal: { type: 'string', format: 'uuid' },
        party: { type: 'string', minLength: 3, maxLength: 40 },
        stage: { type: 'string', minLength: 1, maxLength: 60 },
      }, required: ['idempotency_key', 'pool_id', 'base_version'] },
      handler: async (c: Db, actor: Actor, args: VerbInputs['confirm-email-lead']) => {
        const owner = mailboxOwnerForActor(actor) ?? fail('email_sender_identity_required');
        await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [args.idempotency_key]);
        return withEnvelope(c, actor, 'confirm-email-lead', args, async () => {
          const row = (await c.query(`select id, status, version, name, email, source_row from candidate_pool
            where id=$1 and source=$2 for update`, [args.pool_id, SOURCE])).rows[0];
          if (!row || row.source_row?.mailbox_owner !== owner) fail('email_lead_not_found');
          if (row.status !== 'pool') fail('email_lead_not_pending', { status: row.status });
          if (row.version !== args.base_version) fail('version_conflict', { current_version: row.version });
          const proposed = row.source_row.reading ?? null;
          const applies = args.applies_to ?? proposed?.applies_to;
          const kept = applies === proposed?.applies_to;
          const deal = args.deal?.toLowerCase() ?? (kept && proposed.deal_id ? proposed.deal_id : undefined);
          const party = args.party ?? (kept && proposed.party_ref ? proposed.party_ref : undefined);
          if (!CONFIRMABLE.includes(applies) || (applies === 'existing_deal') !== (deal !== undefined) ||
              (applies === 'existing_party') !== (party !== undefined) || (applies !== 'new_matter' && args.stage !== undefined))
            fail('email_lead_input_invalid');
          const address: string = row.email;
          const carried = await carriers(c, address);
          if (applies === 'new_matter' && carried.length) {
            const role = (await c.query(`select subject_type, ref from v_ref_index where party_id = any($1::uuid[])
              and subject_type in ('client','lead','vendor') and not merged order by subject_type, ref limit 1`, [carried])).rows[0];
            if (role) fail('email_lead_sender_known', { reason: role.subject_type, ref: role.ref });
            const otherDeal = (await c.query(`select d.id, d.name from deal_participant dp join deal d on d.id=dp.deal_id
              where dp.party_id = any($1::uuid[]) and dp.to_at is null and d.phase<>'closed'
                and d.closed_on is null and d.invoiced_on is null limit 1`,
              [carried])).rows[0];
            if (otherDeal) fail('email_lead_sender_known', { reason: 'open_deal', deal_id: otherDeal.id, deal_name: otherDeal.name });
          }
          const provenance = { pool_id: row.id, message_ref: row.source_row.message_ref, mailbox_owner: owner,
            applies_to: applies, proposed_applies_to: proposed?.applies_to ?? null,
            overridden: Boolean(proposed) && (!kept || (deal ?? party ?? null) !== (proposed.deal_id ?? proposed.party_ref ?? null)),
            evidence_quote: proposed?.evidence_quote ?? null, evidence_quote_untrusted: true };
          // The one party the address belongs to: the party that already carries it, or a minimal new one.
          const sender = async (): Promise<string> => {
            if (carried.length > 1) fail('email_lead_address_known');
            if (carried.length) return carried[0];
            const id = (await c.query(`insert into party (kind, name, email, created_by, updated_by)
              values ('person', $1, $2, $3, $3) returning id`, [row.name, address, actor.id])).rows[0].id;
            await writeEvent(c, actor, 'confirm-email-lead', 'party', id, { new: { name: row.name, email: address,
              source: 'email', researched: false, pool_id: row.id }, cause: 'human_stated', idempotency_key: args.idempotency_key });
            return id;
          };
          let partyId: string, dealId: string | null = null, lead: { lead_id: string; ref: string } | null = null;
          if (applies === 'existing_party') {
            const known = await contact(c, party!);
            partyId = known.party_id;
            if (carried.some(id => id !== partyId)) fail('email_lead_address_known');
            if (!carried.length) {
              const current = (await c.query('select email, version from party where id=$1 for update', [partyId])).rows[0];
              await updatePartyContact(c, actor, { idempotency_key: derivedIdempotencyKey('email-lead-party-contact', args.idempotency_key), party,
                base_version: current.version, fields: { email: current.email ? `${current.email}; ${address}` : address },
                source: `outlook-intake email ${row.source_row.message_ref}, confirmed by ${owner}` });
            }
          } else if (applies === 'existing_deal') {
            dealId = (await openDeal(c, deal!)).id as string;
            partyId = await sender();
            await c.query(`insert into deal_participant (deal_id, party_id, role, set_by)
                select $1, $2, $3, $4 where not exists (select 1 from deal_participant
                  where deal_id=$1 and party_id=$2 and role=$3 and to_at is null)`,
              [dealId, partyId, PARTICIPANT_ROLE, actor.id]);
            await writeEvent(c, actor, 'confirm-email-lead', 'deal', dealId, { new: { party_id: partyId,
              role: PARTICIPANT_ROLE, pool_id: row.id }, cause: 'human_stated', idempotency_key: args.idempotency_key });
          } else {
            partyId = await sender();
            const quote = provenance.evidence_quote;
            lead = await newLead(c, actor, { idempotency_key: derivedIdempotencyKey('email-lead-new-lead', args.idempotency_key), party_id: partyId,
              stage: args.stage ?? 'new', owner, source_type: 'email',
              source_detail: `Inbound email to ${owner}'s mailbox, confirmed by ${owner}; `
                + (quote ? `untrusted email text: ${JSON.stringify(quote)}` : 'no literal quote recorded') });
          }
          const status = lead ? 'promoted' : 'confirmed';
          await c.query(`update candidate_pool set status=$2, promoted_lead_id=$3, version=version+1, updated_by=$4,
            updated_at=now() where id=$1`, [row.id, status, lead?.lead_id ?? null, actor.id]);
          await writeEvent(c, actor, 'confirm-email-lead', 'candidate_pool', row.id, { field: 'status',
            old: { status: 'pool' }, new: { status, ...provenance, party_id: partyId, deal_id: dealId,
              lead_id: lead?.lead_id ?? null }, cause: 'human_stated', idempotency_key: args.idempotency_key });
          return { ok: true, contract: 'email-lead-confirmation.v1', pool_id: row.id, status, applies_to: applies,
            party_id: partyId, deal_id: dealId, lead_id: lead?.lead_id ?? null, lead_ref: lead?.ref ?? null };
        });
      },
    },
  };
}
