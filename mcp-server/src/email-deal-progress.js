import { createHash } from 'node:crypto';

const CONTRACT = 'email-deal-progress.v1';
const loi = /\b(?:LOI|letter of intent)\b/i;
const leaseTypes = ['startup', 'relocation', 'expansion', 'additional_office', 'lease', 'renewal'];
const transitions = new Map([
  ['research', { toPhase: 'site_selection', rules: [{ instrument: /\btours?\b/i,
    additionalEvidence: [/\b(?:scheduled|confirmed|set)\b/i] }] }],
  ['site_selection', { toPhase: 'negotiation', rules: [{ instrument: loi, additionalEvidence: [] }] }],
  ['negotiation', { toPhase: 'legal', rules: [
    { instrument: loi, additionalEvidence: [/\b(?:signed|executed)\b/i] },
    { instrument: /\blease\b/i, excludedInstrument: loi, dealTypes: leaseTypes,
      additionalEvidence: [/\b(?:sent|issued|circulated|out for (?:legal )?review)\b/i] },
  ] }],
  ['legal', { toPhase: 'closing', rules: [{ instrument: /\blease\b/i, excludedInstrument: loi,
    dealTypes: leaseTypes, additionalEvidence: [/\b(?:signed|executed)\b/i] }] }],
]);
const properties = {
  deal: { type: 'string', format: 'uuid' },
  activity_id: { type: 'string', format: 'uuid' },
  evidence_quote: { type: 'string', minLength: 10, maxLength: 2000 },
};
const schema = extra => ({ type: 'object', additionalProperties: false,
  properties: { ...properties, ...extra }, required: [...Object.keys(properties), ...Object.keys(extra)] });

export function emailDealProgressTools({ ToolError, withEnvelope, writeEvent, lockDealField, updateDeal }) {
  const fail = error => { throw new ToolError({ error }); };
  async function proposal(c, args, lock = false) {
    const row = (await c.query(`select d.id as deal_id,d.name,d.client_id,d.phase,d.version,d.deal_type,
        a.id as activity_id,a.version as activity_version,a.kind,a.summary,a.detail,a.occurred_at
      from deal d join client cl on cl.id=d.client_id join party p on p.id=cl.party_id
      join activity a on a.id=$2 and (a.deal_id=d.id or (a.deal_id is null and a.client_id=d.client_id))
      where d.id=$1 and cl.merged_into is null and p.merged_into is null and p.deleted_at is null
        and a.kind='email_in' and a.occurred_at<=now()
      ${lock ? 'for update of d for share of a,cl,p' : ''}`, [args.deal,args.activity_id])).rows[0];
    if (!row) fail('email_deal_evidence_not_found');
    const transition = transitions.get(row.phase);
    const eligible = transition?.rules.filter(rule =>
      (!rule.dealTypes || rule.dealTypes.includes(row.deal_type)) &&
      rule.additionalEvidence.every(pattern => pattern.test(args.evidence_quote)));
    if (!eligible?.length) fail('email_deal_phase_not_eligible');
    if (!row.detail?.includes(args.evidence_quote) || !eligible.some(rule =>
        rule.instrument.test(args.evidence_quote) && !rule.excludedInstrument?.test(args.evidence_quote)))
      fail('email_deal_quote_invalid');
    const source = { activity_id: row.activity_id, summary: row.summary,
      occurred_at: new Date(row.occurred_at).toISOString(), evidence_ref: `activity:${row.activity_id}` };
    const result = { contract: CONTRACT, status: 'proposed', deal: row.deal_id, deal_name: row.name,
      client_id: row.client_id, base_version: row.version, from_phase: row.phase, to_phase: transition.toPhase,
      evidence_quote: args.evidence_quote, source, requires_confirmation: true };
    const digest = createHash('sha256').update(JSON.stringify({ ...result,
      activity_version: row.activity_version, captured_body: row.detail })).digest('hex');
    return { ...result, proposal_digest: `sha256:${digest}` };
  }
  return {
    'confirm-email-deal-progress': {
      write: true, humanOnly: true, humanCallerOnly: true,
      description: 'Joe confirms the displayed tour-set, LOI-receipt, signed-LOI, lease-out or executed-lease proposal. Requires the preview digest and unchanged source text, linkage and deal version. Moves research to site selection for a scheduled or confirmed tour, or site selection to negotiation for LOI receipt, or negotiation to legal for a signed or executed LOI or a lease sent for review on a lease or renewal deal, or legal to closing for a signed or executed lease on a lease or renewal deal, through update-deal and retains the email quote in deal history. No sends or automatic phase moves.',
      inputSchema: schema({ proposal_digest: { type: 'string', pattern: '^sha256:[a-f0-9]{64}$' },
        idempotency_key: { type: 'string', minLength: 1, maxLength: 200 } }),
      handler: async (c, actor, args) => {
        if (actor.human !== true || actor.slug !== 'joe') fail('joe_confirmation_required');
        await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [args.idempotency_key]);
        return withEnvelope(c, actor, 'confirm-email-deal-progress', args, async () => {
          await lockDealField(c, args.deal.toLowerCase(), 'phase');
          const current = await proposal(c, args, true);
          if (current.proposal_digest !== args.proposal_digest) fail('email_deal_proposal_stale');
          await updateDeal(c, actor, { idempotency_key: `${args.idempotency_key}:deal-update`,
            deal: current.deal, base_version: current.base_version, fields: { phase: current.to_phase } });
          await writeEvent(c, actor, 'confirm-email-deal-progress', 'deal', current.deal,
            { old: { phase: current.from_phase }, new: { phase: current.to_phase,
              activity_id: current.source.activity_id, evidence_ref: current.source.evidence_ref,
              evidence_quote: current.evidence_quote, proposal_digest: current.proposal_digest },
              cause: 'human_stated', idempotency_key: args.idempotency_key });
          return { ...current, status: 'confirmed', requires_confirmation: false };
        });
      },
    },
    'preview-email-deal-progress': {
      write: false,
      description: 'Propose research to site selection for a scheduled or confirmed tour, or site selection to negotiation for LOI receipt, or negotiation to legal for a signed or executed LOI or a lease sent for review on a lease or renewal deal, or legal to closing for a signed or executed lease on a lease or renewal deal, from a selected captured inbound email and a literal evidence quote. Select the exact deal first. Captured text may be a preview. This does not classify, execute email instructions, or change a record. Lease-out and lease-execution quotes must exclude LOI wording. Joe must review whether the quote proves a tour is set, LOI receipt, LOI signing, lease review or lease execution. Scheduling, delivery, signing or execution wording is a review cue, not proof of the milestone.',
      inputSchema: schema({}),
      handler: (c, _actor, args) => proposal(c, args),
    },
  };
}
