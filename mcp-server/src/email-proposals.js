const LIMIT = 200;
const KINDS = {
  phase_move: { preview: 'preview-email-deal-progress', confirm: 'confirm-email-deal-progress',
    args: () => ({}) },
  next_step: { preview: 'preview-email-next-step', confirm: 'confirm-email-next-step',
    args: row => ({ text: row.task_text, next_date: row.next_date, owner: row.owner }) },
};
const instruction = /\b(?:create|add|open|make|set)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:task|to-?do|next step|reminder)\b|\b(?:move|advance|mark|update|change|set|close)\s+(?:this|the|that|our)\s+(?:deal|record|crm|file|phase|stage)\b|\bignore\s+(?:all|any|previous|prior|the)\b|\b(?:assistant|chatbot|AI agent|automation|system prompt)\b/i;
const sender = summary => /^Email from (.+?): (.*)$/s.exec(summary ?? '');

export function emailProposalTools({ ToolError, withEnvelope, writeEvent, preview }) {
  const fail = (error, extra = {}) => { throw new ToolError({ error, ...extra }); };
  const previewArgs = row => ({ deal: row.deal_id, activity_id: row.activity_id,
    evidence_quote: row.evidence_quote, ...KINDS[row.kind].args(row) });
  return {
    'record-email-proposal': {
      write: true,
      description: 'Store one pending email proposal for Home review: a phase move or a Joe or Dell next step, with a literal quote from a captured inbound email on the selected deal and the router confidence. The matching preview must accept the evidence first, and quotes that instruct the record layer are refused. Re-recording the same kind for the same deal and email returns the stored proposal. Never confirms, changes the deal or sends anything; Joe confirms through confirm-email-deal-progress or confirm-email-next-step.',
      inputSchema: { type: 'object', additionalProperties: false,
        required: ['idempotency_key', 'kind', 'deal', 'activity_id', 'evidence_quote', 'confidence'],
        properties: {
          idempotency_key: { type: 'string', minLength: 1, maxLength: 200 },
          kind: { type: 'string', enum: Object.keys(KINDS) },
          deal: { type: 'string', format: 'uuid' },
          activity_id: { type: 'string', format: 'uuid' },
          evidence_quote: { type: 'string', minLength: 10, maxLength: 2000 },
          confidence: { type: 'number', minimum: 0, maximum: 1 },
          text: { type: 'string', minLength: 1, maxLength: 500 },
          next_date: { type: 'string', format: 'date' },
          owner: { type: 'string', enum: ['joe', 'dell'] },
        } },
      handler: (c, actor, args) => withEnvelope(c, actor, 'record-email-proposal', args, async () => {
        if (!KINDS[args.kind]) fail('email_proposal_kind_invalid');
        if (typeof args.confidence !== 'number' || !(args.confidence >= 0 && args.confidence <= 1))
          fail('email_proposal_confidence_invalid');
        const step = args.kind === 'next_step';
        if (step !== ['text', 'next_date'].every(key => args[key] !== undefined) ||
            (!step && args.owner !== undefined)) fail('email_proposal_fields_invalid');
        if (instruction.test(args.evidence_quote)) fail('email_proposal_evidence_is_instruction');
        const row = { kind: args.kind, deal_id: args.deal.toLowerCase(), activity_id: args.activity_id.toLowerCase(),
          evidence_quote: args.evidence_quote, task_text: step ? args.text.trim() : null,
          next_date: step ? args.next_date : null, owner: step ? args.owner || 'joe' : null };
        const shown = await preview(c, actor, KINDS[row.kind].preview, previewArgs(row));
        const stored = (await c.query(`insert into email_proposal
            (kind,deal_id,activity_id,evidence_quote,task_text,next_date,owner,confidence,created_by)
          values ($1,$2,$3,$4,$5,$6,$7,$8,$9)
          on conflict (kind,deal_id,activity_id) do update set kind=excluded.kind
          returning id,status,version`, [row.kind, row.deal_id, row.activity_id, row.evidence_quote,
          row.task_text, row.next_date, row.owner, args.confidence, actor.id])).rows[0];
        return { ok: true, proposal_id: stored.id, status: stored.status, version: stored.version,
          proposal_digest: shown.proposal_digest, requires_confirmation: true };
      }),
    },
    'list-email-proposals': {
      write: false,
      description: 'List pending email proposals for Home across deals, oldest first: kind (phase_move or next_step), deal, confidence, source email sender, subject, quote and activity_id, and a fresh preview with its proposal_digest. Confirm with confirm_verb and the preview fields; dismiss with dismiss-email-proposal and the version. A proposal the deal no longer supports shows preview null and preview_error. Refuses above 200 pending proposals instead of truncating.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {} },
      handler: async (c, actor) => {
        const { rows } = await c.query(`select p.id,p.version,p.kind,p.deal_id,d.name as deal_name,
            p.activity_id,p.evidence_quote,p.task_text,p.next_date::text as next_date,p.owner,
            p.confidence::float8 as confidence,a.summary,a.occurred_at
          from email_proposal p join deal d on d.id=p.deal_id join activity a on a.id=p.activity_id
          where p.status='pending' order by p.created_at,p.id limit ${LIMIT + 1}`);
        if (rows.length > LIMIT) fail('email_proposals_limit_exceeded', { limit: LIMIT });
        const proposals = [];
        for (const row of rows) {
          let shown = null, previewError = null;
          try { shown = await preview(c, actor, KINDS[row.kind].preview, previewArgs(row)); }
          catch (error) { if (!(error instanceof ToolError)) throw error; previewError = error.payload?.error ?? 'preview_failed'; }
          const named = sender(row.summary);
          proposals.push({ id: row.id, version: row.version, kind: row.kind, deal: row.deal_id,
            deal_name: row.deal_name, confidence: row.confidence,
            source: { activity_id: row.activity_id, sender: named?.[1] ?? null, subject: named?.[2] ?? row.summary,
              evidence_quote: row.evidence_quote, occurred_at: new Date(row.occurred_at).toISOString() },
            confirm_verb: KINDS[row.kind].confirm, preview: shown, preview_error: previewError });
        }
        return { schema_version: 'email-proposals.v1', proposals, send: false };
      },
    },
    'dismiss-email-proposal': {
      write: true, humanOnly: true, humanCallerOnly: true,
      description: 'Joe or Dell dismisses one pending email proposal at the version they read, with a reason kept in deal history. The router never re-creates a dismissed proposal for the same deal, email and kind. If Joe later confirms the same proposal through its confirm verb, the confirmation wins and the row becomes confirmed with the dismissal kept as a note. Changes nothing else.',
      inputSchema: { type: 'object', additionalProperties: false,
        required: ['idempotency_key', 'proposal_id', 'base_version', 'reason'],
        properties: {
          idempotency_key: { type: 'string', minLength: 1, maxLength: 200 },
          proposal_id: { type: 'string', format: 'uuid' },
          base_version: { type: 'integer', minimum: 1 },
          reason: { type: 'string', minLength: 1, maxLength: 500 },
        } },
      handler: (c, actor, args) => {
        if (actor.human !== true || !['joe', 'dell'].includes(actor.slug)) fail('partner_dismissal_required');
        return withEnvelope(c, actor, 'dismiss-email-proposal', args, async () => {
          if (!args.reason?.trim()) fail('email_proposal_reason_required');
          const row = (await c.query(`select id,kind,deal_id,activity_id,status,version from email_proposal
            where id=$1 for update`, [args.proposal_id])).rows[0];
          if (!row) fail('email_proposal_not_found');
          if (row.status !== 'pending') fail('email_proposal_not_pending', { status: row.status });
          if (row.version !== args.base_version)
            fail('version_conflict', { current_version: row.version, hint: 'surface this to the human and re-read; NEVER auto-retry' });
          await c.query(`update email_proposal set status='dismissed',version=version+1,resolved_by=$2,
            resolved_at=now(),resolution_reason=$3 where id=$1`, [row.id, actor.id, args.reason.trim()]);
          await writeEvent(c, actor, 'dismiss-email-proposal', 'deal', row.deal_id, {
            new: { proposal_id: row.id, kind: row.kind, activity_id: row.activity_id, reason: args.reason.trim() },
            cause: 'human_stated', idempotency_key: args.idempotency_key });
          return { ok: true, proposal_id: row.id, status: 'dismissed', version: row.version + 1 };
        });
      },
    },
  };
}

export async function resolveEmailProposal(c, actor, kind, deal, activityId) {
  const row = (await c.query(`update email_proposal set status='confirmed',version=version+1,
      resolved_by=$4,resolved_at=now(),resolution_reason=case when status='dismissed'
        then left('Confirmed by Joe after dismissal: '||resolution_reason,500) end
    where kind=$1 and deal_id=$2 and activity_id=$3 and status in ('pending','dismissed') returning id`,
  [kind, deal.toLowerCase(), activityId.toLowerCase(), actor.id])).rows[0];
  return row?.id ?? null;
}
