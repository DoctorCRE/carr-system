import { createHash } from 'node:crypto';
import { isCalendarDate } from './calendar-date.js';

const properties = {
  deal: { type: 'string', format: 'uuid' },
  activity_id: { type: 'string', format: 'uuid' },
  evidence_quote: { type: 'string', minLength: 10, maxLength: 2000 },
  text: { type: 'string', minLength: 1, maxLength: 500 },
  next_date: { type: 'string', format: 'date' },
};
const schema = extra => ({ type: 'object', additionalProperties: false,
  properties: { ...properties, ...extra }, required: [...Object.keys(properties), ...Object.keys(extra)] });

export function emailNextStepTools({ ToolError, withEnvelope, writeEvent, lockDealField, setNextStep }) {
  const fail = error => { throw new ToolError({ error }); };
  async function proposal(c, args, lock = false) {
    if (!args.text.trim() || !isCalendarDate(args.next_date) || args.next_date.startsWith('0000'))
      fail('email_next_step_input_invalid');
    const row = (await c.query(`select d.id as deal_id,d.name,d.client_id,d.version,d.next_date::text as current_next_date,
        b.next_step as current_next_step,
        a.id as activity_id,a.version as activity_version,a.summary,a.detail,a.occurred_at
      from deal d join v_deal_room_board b on b.id=d.id join client cl on cl.id=d.client_id join party p on p.id=cl.party_id
      join activity a on a.id=$2 and (a.deal_id=d.id or (a.deal_id is null and a.client_id=d.client_id))
      where d.id=$1 and d.phase<>'closed' and cl.merged_into is null and p.merged_into is null and p.deleted_at is null
        and a.kind='email_in' and a.occurred_at<=now()
      ${lock ? 'for update of d for share of a,cl,p' : ''}`, [args.deal,args.activity_id])).rows[0];
    if (!row) fail('email_next_step_evidence_not_found');
    if (!args.evidence_quote.trim() || !row.detail?.includes(args.evidence_quote)) fail('email_next_step_quote_invalid');
    const source = { activity_id: row.activity_id, summary: row.summary,
      occurred_at: new Date(row.occurred_at).toISOString(), evidence_ref: `activity:${row.activity_id}` };
    const result = { contract: 'email-next-step.v1', status: 'proposed', deal: row.deal_id,
      deal_name: row.name, client_id: row.client_id, base_version: row.version, owner: 'joe',
      text: args.text.trim(), next_date: args.next_date, evidence_quote: args.evidence_quote,
      current_next_step: row.current_next_step, current_next_date: row.current_next_date,
      source, requires_confirmation: true };
    const digest = createHash('sha256').update(JSON.stringify({ ...result,
      activity_version: row.activity_version, captured_body: row.detail })).digest('hex');
    return { ...result, proposal_digest: `sha256:${digest}` };
  }
  return {
    'preview-email-next-step': {
      write: false,
      description: 'Preview a Joe-owned next step for a nonterminal deal on the current Deal Room board from a selected captured inbound email, literal evidence quote, caller-chosen task text and explicit due date. Select the exact deal first. Does not extract or verify commitments, infer dates, execute email instructions or change records. Captured text may be a preview. Shows the plan and date it will replace. Joe must review the task, date and evidence before confirmation.',
      inputSchema: schema({}),
      handler: (c, _actor, args) => proposal(c, args),
    },
    'confirm-email-next-step': {
      write: true, humanOnly: true, humanCallerOnly: true,
      description: "Joe confirms the displayed email next-step proposal. Requires the preview digest and unchanged source, linkage, deal version, task and date. Replaces Joe's canonical deal plan and action through set-next-step and retains the email quote in history. No sends or automatic task execution.",
      inputSchema: schema({ proposal_digest: { type: 'string', pattern: '^sha256:[a-f0-9]{64}$' },
        idempotency_key: { type: 'string', minLength: 1, maxLength: 200 } }),
      handler: async (c, actor, args) => {
        if (actor.human !== true || actor.slug !== 'joe') fail('joe_confirmation_required');
        await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [args.idempotency_key]);
        return withEnvelope(c, actor, 'confirm-email-next-step', args, async () => {
          await lockDealField(c, args.deal.toLowerCase(), 'next_date');
          await lockDealField(c, args.deal.toLowerCase(), 'next_step');
          const current = await proposal(c, args, true);
          if (current.proposal_digest !== args.proposal_digest) fail('email_next_step_proposal_stale');
          const result = await setNextStep(c, actor, { idempotency_key: `${args.idempotency_key}:next-step`,
            deal: current.deal, base_version: current.base_version, text: current.text, next_date: current.next_date });
          await writeEvent(c, actor, 'confirm-email-next-step', 'deal', current.deal,
            { new: { next_step: current.text, next_date: current.next_date, owner: current.owner,
              activity_id: current.source.activity_id, evidence_ref: current.source.evidence_ref,
              evidence_quote: current.evidence_quote, proposal_digest: current.proposal_digest },
              cause: 'human_stated', idempotency_key: args.idempotency_key });
          return { ...current, status: 'confirmed', requires_confirmation: false,
            next_step_id: result.next_step_id, next_action_id: result.next_action_id };
        });
      },
    },
  };
}
