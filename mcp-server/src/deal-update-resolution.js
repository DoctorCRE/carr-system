// Assess overdue deal actions against a recorded update and retain reversible evidence.
import { TypeSafeClient } from '@typesafe-ai/sdk';
import { automaticEvidenceHold } from './call-auto-apply.js';
import { DATA_SENTENCE } from './jev-deal-reading.ts';
import { organizationTenantForActor, personalScopeForActor } from './identity.js';
import { partnerAuthoritySlugForActor } from './partner-authority.js';

const verdicts = {
  yes: 'The update explicitly establishes that this overdue action was accomplished.',
  no: 'The update explicitly says the action remains outstanding or describes unrelated progress.',
  unclear: 'The update does not establish whether this action was accomplished. Plans and promises are not completion.',
};
const MIN_RESOLUTION_CONFIDENCE = 0.9;
const probability = n => typeof n === 'number' && Number.isFinite(n) && n >= 0 && n <= 1;

export function dealUpdateResolution({ withEnvelope, writeEvent, partnerOwnerIds, lockDealField, dealNextStepStatus, ToolError }) {
  const visibleScopes = actor => {
    const scope = personalScopeForActor(actor);
    return scope.status === 'personal' ? ['none', `${scope.sponsor}-personal`] : ['none'];
  };
  async function actions(c, deal, owners) {
    return (await c.query(`select n.id, 'next_action' as source, n.description, n.version, n.updated_at::text as updated_at,
        case when n.id=(select id from next_action where subject_type='deal' and subject_id=$1
          and status='open' and item_title is null order by updated_at desc,id desc limit 1)
          then d.next_date::text else n.due_on::text end as due_on
      from next_action n join deal d on d.id=n.subject_id
      where n.subject_type='deal' and n.subject_id=$1 and n.owner_id=any($2::uuid[])
        and n.status='open' and n.item_title is null
      union all select id,'post_call_action',description,null,updated_at::text,due_on::text from capture_post_call_action
      where deal_id=$1 and owner_id=any($2::uuid[]) and status='open' and item_title is null`, [deal, owners])).rows;
  }
  async function change(c, actor, action, status) {
    const ordinary = action.source === 'next_action';
    const table = ordinary ? 'next_action' : 'capture_post_call_action';
    const attribution = ordinary ? ',updated_by=$5' :
      ",completed_at=case when $2='done' then clock_timestamp() else null end";
    const params = [action.id, status, status === 'done' ? 'open' : 'done', action.updated_at];
    if (ordinary) params.push(actor.id, action.version);
    const row = (await c.query(`update ${table} set status=$2,updated_at=clock_timestamp()${attribution}
      where id=$1 and status=$3 and updated_at=$4::timestamptz${ordinary ? ' and version=$6' : ''}
      returning updated_at::text as updated_at${ordinary ? ',version' : ''}`, params)).rows[0];
    if (!row) throw new ToolError({ error: 'overdue_action_changed' });
    return row;
  }
  async function currentPlan(c, deal) {
    return (await c.query(`select b.next_step,b.next_date::text as next_date,
      (select id from next_action where subject_type='deal' and subject_id=$1 and status='open' and item_title is null
        order by updated_at desc,id desc limit 1) as action_id from v_deal_room_board b where b.id=$1`, [deal])).rows[0];
  }
  async function reconcilePlan(c, actor, deal, before, resolvedIds, key, verb) {
    let plan = dealNextStepStatus(before?.next_step, before?.next_date);
    if (resolvedIds.includes(before?.action_id)) {
      const next = (await c.query(`select description,due_on::text as due_on from next_action
        where subject_type='deal' and subject_id=$1 and status='open' and item_title is null
        order by updated_at desc,id desc limit 1`, [deal])).rows[0];
      await c.query('update deal set next_date=$2,updated_by=$3 where id=$1', [deal, next?.due_on ?? null, actor.id]);
      for (const [field, value] of [['next_step', next?.description ?? null], ['next_date', next?.due_on ?? null]])
        await writeEvent(c, actor, verb, 'deal', deal, { field,
          old: { [field]: before[field] }, new: { [field]: value, superseded_action_ids: resolvedIds },
          idempotency_key: key, recorded_at_after_lock: true });
      plan = dealNextStepStatus(next?.description, next?.due_on);
    }
    return plan;
  }
  async function assess(c, actor, deal, note, text, key) {
    if (!partnerAuthoritySlugForActor(actor)) return { preserved_action_ids: [] };
    await lockDealField(c, deal, 'next_date');
    await lockDealField(c, deal, 'next_step');
    const before = await currentPlan(c, deal);
    const owners = await partnerOwnerIds(c, actor);
    const all = await actions(c, deal, owners);
    const today = (await c.query('select current_date::text as day')).rows[0].day;
    const overdue = all.filter(a => a.due_on && a.due_on < today);
    if (!overdue.length) return { preserved_action_ids: [], ...dealNextStepStatus(before?.next_step, before?.next_date) };
    const quotes = Object.fromEntries((text.match(/[^.!?\n]+[.!?]?/g) || [text])
      .map(s => s.trim().slice(0, 400)).filter(Boolean).slice(0, 40).map((s, i) => [`quote_${i}`, s]));
    let answers, failure, model = null;
    if (!c.jevApiKey && !c.jevClient) failure = 'missing_credentials';
    else if (text.length > 6000 || overdue.length > 20 || text.length + overdue.reduce((n, a) => n + a.description.length, 0) > 12000) failure = 'assessment_budget_exceeded';
    else try {
      const client = c.jevClient || new TypeSafeClient({ apiKey: c.jevApiKey, defaultModel: 'jev-1.13.0',
        baseURL: 'https://api.typesafe.ai', timeout: 1100, retry: { maxRetries: 0 }, compression: false, logLevel: 'off' });
      const questions = {};
      overdue.forEach((a, i) => {
        questions[`action_${i}`] = { type: 'choice', instructions: `${DATA_SENTENCE} Does state.update satisfy state.actions[${i}]?`, criteria: verdicts };
        questions[`quote_${i}`] = { type: 'choice', instructions: `${DATA_SENTENCE} Select the sentence in state.update that best establishes whether state.actions[${i}] was accomplished.`, criteria: quotes };
      });
      const result = await client.systemOne({ state: { update: text, actions: overdue }, questions },
        { signal: AbortSignal.timeout(1100) });
      answers = result.answers;
      model = typeof result.model === 'string' ? result.model.slice(0, 80) : null;
    } catch (error) { failure = error?.status === 429 ? 'jev_budget_exhausted' : 'jev_unavailable'; }
    const assessments = [];
    for (const [i, action] of overdue.entries()) {
      const answer = answers?.[`action_${i}`], quoteAnswer = answers?.[`quote_${i}`];
      const distribution = answer?.probabilities;
      const valid = answer?.type === 'choice' && Object.hasOwn(verdicts, answer.choice) &&
        probability(answer.confidence) && distribution && Object.keys(distribution).length === 3 &&
        Object.keys(verdicts).every(k => probability(distribution[k])) &&
        Math.abs(Object.values(distribution).reduce((a, b) => a + b, 0) - 1) < 0.02 &&
        Object.keys(verdicts).every(k => distribution[answer.choice] >= distribution[k]) &&
        quoteAnswer?.type === 'choice' && Object.hasOwn(quotes, quoteAnswer.choice);
      const verdict = valid ? answer.choice : 'unavailable';
      const quote = valid ? quotes[quoteAnswer.choice] : null;
      const hold = failure || (!valid ? 'invalid_jev_answer' :
        answer.confidence < MIN_RESOLUTION_CONFIDENCE || distribution[verdict] < MIN_RESOLUTION_CONFIDENCE ? 'low_confidence' :
          automaticEvidenceHold({ needs_review: verdict === 'unclear' ? 'unclear' : null }, quote));
      const resolved = verdict === 'yes' && !hold;
      const resolvedAt = resolved ? await change(c, actor, action, 'done') : null;
      const assessment = { action, verdict, quote, model, reason: hold || `${verdicts[verdict]} “${quote}”`,
        resolved, resolved_at: resolvedAt?.updated_at ?? null, resolved_version: resolvedAt?.version ?? null, review_required: !failure && (verdict === 'unclear' || !!hold), note_id: note,
        update: text, confidence: valid ? answer.confidence : null };
      await writeEvent(c, actor, 'add-deal-note', 'deal', deal, { field: 'overdue_assessment',
        old: { status: 'open' }, new: assessment, human_quote: quote, cause: 'automation_job', agent_rationale: assessment.reason,
        idempotency_key: key, recorded_at_after_lock: true });
      assessments.push(assessment);
    }
    const resolvedIds = assessments.filter(a => a.resolved).map(a => a.action.id);
    const plan = await reconcilePlan(c, actor, deal, before, resolvedIds, key, 'add-deal-note');
    return { ...plan, overdue_assessment: { reason: failure || null, items: assessments },
      resolved_action_ids: resolvedIds,
      preserved_action_ids: assessments.filter(a => !a.resolved && a.action.source === 'next_action').map(a => a.action.id) };
  }
  const tools = {
    'read-deal-update-reviews': {
      writerConnection: true,
      description: 'Read pending content-based overdue-resolution proposals for Home. Uncertain updates never close an action. Resolve or dismiss with review-deal-update; resolved assessments offer undo through the same verb.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {} },
      handler: async (c, actor) => {
        if (!partnerAuthoritySlugForActor(actor)) throw new ToolError({ error: 'partner_required' });
        const owners = await partnerOwnerIds(c, actor);
        const rows = (await c.query(`
        select e.id as assessment_id,e.subject_id as deal_id,d.name as deal_name,e.new_value as assessment,
          'review-deal-update' as confirm_verb
        from event e join deal d on d.id=e.subject_id
        where e.subject_type='deal' and e.field='overdue_assessment' and e.organization_tenant_id=$1 and e.personal_scope=any($2::text[]) and e.new_value->>'review_required'='true'
          and not exists(select 1 from event newer where newer.subject_id=e.subject_id and newer.field=e.field
            and newer.new_value#>>'{action,id}'=e.new_value#>>'{action,id}' and newer.mutation_order>e.mutation_order)
          and not exists(select 1 from event r where r.field='overdue_review' and r.new_value->>'assessment_id'=e.id::text)
          and (exists(select 1 from next_action a where a.id=(e.new_value#>>'{action,id}')::uuid and a.status='open' and a.owner_id=any($3::uuid[]))
            or exists(select 1 from capture_post_call_action a where a.id=(e.new_value#>>'{action,id}')::uuid and a.status='open' and a.owner_id=any($3::uuid[])))
        order by e.recorded_at,e.id limit 201`, [organizationTenantForActor(actor), visibleScopes(actor), owners])).rows;
        if (rows.length > 200) throw new ToolError({ error: 'deal_update_reviews_limit_exceeded' });
        const resolutions = (await c.query(`select e.id as assessment_id,e.subject_id as deal_id,d.name as deal_name,
          e.new_value as assessment,e.recorded_at::text as resolved_on,e.idempotency_key
          from event e join deal d on d.id=e.subject_id
          where e.subject_type='deal' and e.field='overdue_assessment' and e.organization_tenant_id=$1
            and e.personal_scope=any($2::text[]) and e.new_value->>'resolved'='true'
            and e.recorded_at >= current_timestamp - interval '7 days'
            and not exists(select 1 from event r where r.field='overdue_review' and r.new_value->>'assessment_id'=e.id::text)
            and (exists(select 1 from next_action a where a.id=(e.new_value#>>'{action,id}')::uuid and a.owner_id=any($3::uuid[]))
              or exists(select 1 from capture_post_call_action a where a.id=(e.new_value#>>'{action,id}')::uuid and a.owner_id=any($3::uuid[])))
          order by e.recorded_at desc,e.id desc`, [organizationTenantForActor(actor), visibleScopes(actor), owners])).rows;
        const automatic_resolutions = [];
        for (const row of resolutions) {
          const siblings = (await c.query(`select new_value from event where subject_type='deal' and subject_id=$1
            and idempotency_key=$2 and field='overdue_assessment' and new_value->>'resolved'='true'`, [row.deal_id, row.idempotency_key])).rows;
          let undo_possible = true;
          for (const {new_value: value} of siblings) {
            const current = (await c.query(value.action.source === 'next_action'
              ? 'select status,updated_at::text as updated_at,version from next_action where id=$1 and owner_id=any($2::uuid[])'
              : 'select status,updated_at::text as updated_at from capture_post_call_action where id=$1 and owner_id=any($2::uuid[])',
            [value.action.id, owners])).rows[0];
            if (current?.status !== 'done' || current.updated_at !== value.resolved_at ||
                (value.action.source === 'next_action' && current.version !== value.resolved_version)) undo_possible = false;
          }
          const fields = (await c.query(`select idempotency_key,new_value from event where subject_type='deal' and subject_id=$1
            and field in ('next_date','next_step') order by recorded_at desc,id desc`, [row.deal_id])).rows;
          const ownFields = fields.filter(field => field.idempotency_key === row.idempotency_key);
          if ((row.assessment.action.source === 'next_action' || ownFields.length) && fields[0]?.idempotency_key !== row.idempotency_key) undo_possible = false;
          const replacement = ownFields.find(field => field.new_value.next_action_id)?.new_value.next_action_id;
          if (replacement && !(await c.query("select 1 from next_action where id=$1 and status='open' and version=1", [replacement])).rows.length) undo_possible = false;
          automatic_resolutions.push({ assessment_id: row.assessment_id, deal_id: row.deal_id, deal_name: row.deal_name,
            action_title: row.assessment.action.description, quote: row.assessment.quote, resolved_at: row.resolved_on, undo_possible });
        }
        return { schema_version: 'deal-update-reviews.v1', proposals: rows, automatic_resolutions };
      },
    },
    'review-deal-update': {
      write: true, humanOnly: true,
      description: 'Resolve or dismiss one Home overdue-update proposal, or undo a recorded automatic resolution. Requires partner authority and a reason. Undo reopens the exact action and restores the prior plan only while that update is still the latest plan change. Newer work refuses undo.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { type: 'string' }, assessment_id: { type: 'string', format: 'uuid' },
        decision: { type: 'string', enum: ['resolve', 'dismiss', 'undo'] }, reason: { type: 'string', minLength: 1 },
      }, required: ['idempotency_key', 'assessment_id', 'decision', 'reason'] },
      handler: (c, actor, args) => withEnvelope(c, actor, 'review-deal-update', args, async () => {
        if (!partnerAuthoritySlugForActor(actor)) throw new ToolError({ error: 'partner_required' });
        if (!['resolve', 'dismiss', 'undo'].includes(args.decision) || !args.reason?.trim())
          throw new ToolError({ error: 'invalid_review' });
        const event = (await c.query("select subject_id,new_value,idempotency_key from event where id=$1 and subject_type='deal' and field='overdue_assessment' and organization_tenant_id=$2 and personal_scope=any($3::text[])", [args.assessment_id, organizationTenantForActor(actor), visibleScopes(actor)])).rows[0];
        if (!event) throw new ToolError({ error: 'assessment_not_found' });
        const a = event.new_value;
        const owners = await partnerOwnerIds(c, actor);
        const owned = await c.query(a.action.source === 'next_action'
          ? 'select 1 from next_action where id=$1 and owner_id=any($2::uuid[])'
          : 'select 1 from capture_post_call_action where id=$1 and owner_id=any($2::uuid[])', [a.action.id, owners]);
        if (!owned.rows.length) throw new ToolError({ error: 'not_action_owner' });
        await lockDealField(c, event.subject_id, 'next_date');
        await lockDealField(c, event.subject_id, 'next_step');
        if ((await c.query("select 1 from event where field='overdue_review' and new_value->>'assessment_id'=$1", [args.assessment_id])).rows.length)
          throw new ToolError({ error: 'assessment_already_reviewed' });
        if (args.decision === 'undo' ? !a.resolved : !a.review_required)
          throw new ToolError({ error: 'invalid_review_decision' });
        const current = (await c.query(a.action.source === 'next_action'
          ? 'select updated_at::text as updated_at,version from next_action where id=$1 and owner_id=any($2::uuid[]) for update'
          : 'select updated_at::text as updated_at from capture_post_call_action where id=$1 and owner_id=any($2::uuid[]) for update', [a.action.id, owners])).rows[0];
        if (current?.updated_at !== (args.decision === 'undo' ? a.resolved_at : a.action.updated_at) ||
            (a.action.source === 'next_action' && current?.version !== (args.decision === 'undo' ? a.resolved_version : a.action.version)))
          throw new ToolError({ error: 'overdue_action_changed' });
        const undoAssessments = [];
        if (args.decision === 'undo') {
          const siblings = (await c.query(`select id,new_value from event where subject_type='deal'
            and subject_id=$1 and idempotency_key=$2 and field='overdue_assessment' and new_value->>'resolved'='true'
            order by id`, [event.subject_id, event.idempotency_key])).rows;
          for (const sibling of siblings) {
            const value = sibling.new_value;
            const row = (await c.query(value.action.source === 'next_action'
              ? 'select updated_at::text as updated_at,version from next_action where id=$1 and owner_id=any($2::uuid[]) for update'
              : 'select updated_at::text as updated_at from capture_post_call_action where id=$1 and owner_id=any($2::uuid[]) for update',
            [value.action.id, owners])).rows[0];
            if (row?.updated_at !== value.resolved_at ||
                (value.action.source === 'next_action' && row?.version !== value.resolved_version))
              throw new ToolError({ error: 'overdue_action_changed' });
            undoAssessments.push({ id: sibling.id, action: { ...value.action, updated_at: row.updated_at, version: row.version } });
          }
          const latest = (await c.query(`select idempotency_key from event where subject_type='deal' and subject_id=$1
            and field in ('next_date','next_step') order by recorded_at desc,id desc limit 1`, [event.subject_id])).rows[0];
          if (a.action.source === 'next_action' && latest?.idempotency_key !== event.idempotency_key) throw new ToolError({ error: 'newer_change_exists' });
          const fields = (await c.query(`select field,old_value,new_value from event where subject_type='deal'
            and subject_id=$1 and idempotency_key=$2 and field in ('next_date','next_step')
            order by recorded_at,id`, [event.subject_id, event.idempotency_key])).rows;
          if (fields.length && latest?.idempotency_key !== event.idempotency_key)
            throw new ToolError({ error: 'newer_change_exists' });
          const replacement = fields.find(row => row.new_value.next_action_id)?.new_value.next_action_id;
          if (replacement && !(await c.query(`update next_action set status='dropped',updated_by=$2
            where id=$1 and status='open' and version=1 returning id`, [replacement, actor.id])).rows.length)
            throw new ToolError({ error: 'newer_change_exists' });
          for (const field of ['next_step', 'next_date']) {
            const original = fields.find(row => row.field === field);
            if (!original) continue;
            if (field === 'next_date') await c.query('update deal set next_date=$2,updated_by=$3 where id=$1',
              [event.subject_id, original.old_value.next_date, actor.id]);
            await writeEvent(c, actor, 'review-deal-update', 'deal', event.subject_id, { field,
              old: fields.filter(row => row.field === field).at(-1).new_value, new: original.old_value,
              idempotency_key: args.idempotency_key, recorded_at_after_lock: true });
          }
        }
        if (args.decision === 'undo') for (const sibling of undoAssessments) await change(c, actor, sibling.action, 'open');
        if (args.decision === 'resolve') {
          const before = await currentPlan(c, event.subject_id);
          await change(c, actor, { ...a.action, updated_at: current.updated_at, version: current.version }, 'done');
          await reconcilePlan(c, actor, event.subject_id, before, [a.action.id], args.idempotency_key, 'review-deal-update');
        }
        for (const assessmentId of args.decision === 'undo' ? undoAssessments.map(row => row.id) : [args.assessment_id])
          await writeEvent(c, actor, 'review-deal-update', 'deal', event.subject_id, { field: 'overdue_review',
            new: { assessment_id: assessmentId, decision: args.decision, reason: args.reason },
            idempotency_key: args.idempotency_key, human_quote: args.reason, recorded_at_after_lock: true });
        return { ok: true, decision: args.decision, action_id: a.action.id };
      }),
    },
  };
  async function preserved(c, deal) {
    return (await c.query(`select distinct (e.new_value#>>'{action,id}')::uuid as id
      from event e where e.subject_type='deal' and e.subject_id=$1 and e.field='overdue_assessment'
        and e.new_value->>'resolved'='false' and e.recorded_at >= current_timestamp - interval '14 days'
        /* deal-update:preserved */`, [deal])).rows.map(row => row.id);
  }
  return { assess, tools, preserved };
}
