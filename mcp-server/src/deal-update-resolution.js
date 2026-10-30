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
        case when n.id=(select id from v_deal_current_action where deal_id=$1 and status='open')
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
      (select id from v_deal_current_action where deal_id=$1 and status='open') as action_id from v_deal_room_board b where b.id=$1`, [deal])).rows[0];
  }
  async function reconcilePlan(c, actor, deal, before, resolvedIds, key, verb) {
    let plan = dealNextStepStatus(before?.next_step, before?.next_date);
    if (resolvedIds.includes(before?.action_id)) {
      const next = (await c.query(`select n.description,n.due_on::text as due_on from v_deal_current_action current_action
        join next_action n on n.id=current_action.id
        where current_action.deal_id=$1 and current_action.status='open'`, [deal])).rows[0];
      await c.query('update deal set next_date=$2,updated_by=$3 where id=$1', [deal, next?.due_on ?? null, actor.id]);
      for (const [field, value] of [['next_step', next?.description ?? null], ['next_date', next?.due_on ?? null]])
        await writeEvent(c, actor, verb, 'deal', deal, { field,
          old: { [field]: before[field] }, new: { [field]: value, superseded_action_ids: resolvedIds },
          idempotency_key: key, recorded_at_after_lock: true });
      plan = dealNextStepStatus(next?.description, next?.due_on);
    }
    return plan;
  }
  async function prepare(c, actor, deal, note, text, key, source = {}) {
    const verb = source.verb || 'add-deal-note';
    if (!partnerAuthoritySlugForActor(actor)) return { preserved_action_ids: [] };
    await lockDealField(c, deal, 'next_date');
    await lockDealField(c, deal, 'next_step');
    const before = await currentPlan(c, deal);
    const owners = await partnerOwnerIds(c, actor);
    const all = await actions(c, deal, owners);
    const today = (await c.query('select current_date::text as day')).rows[0].day;
    const overdue = all.filter(a => a.due_on && a.due_on < today);
    if (!overdue.length) return { preserved_action_ids: [], ...dealNextStepStatus(before?.next_step, before?.next_date) };
    await writeEvent(c, actor, verb, 'deal', deal, { field: 'overdue_assessment_pending',
      new: { ...source, note_id: note, update: text, actions: overdue }, idempotency_key: key, recorded_at_after_lock: true });
    for (const action of overdue)
      await writeEvent(c, actor, verb, 'deal', deal, { field: 'overdue_assessment_fallback',
        new: { ...source, action, note_id: note, update: text, resolved: false, review_required: true,
          verdict: 'unavailable', reason: 'awaiting_assessment' }, idempotency_key: key, recorded_at_after_lock: true });
    return { ...dealNextStepStatus(before?.next_step, before?.next_date),
      overdue_assessment: { pending: true, reason: 'awaiting_assessment', items: [] },
      preserved_action_ids: overdue.filter(a => a.source === 'next_action').map(a => a.id) };
  }
  async function judge(c, text, overdue, timeout) {
    const quotes = Object.fromEntries((text.match(/[^.!?\n]+[.!?]?/g) || [text])
      .map(s => s.trim().slice(0, 400)).filter(Boolean).slice(0, 40).map((s, i) => [`quote_${i}`, s]));
    let answers, failure, model = null;
    if (!c.jevApiKey && !c.jevClient) failure = 'missing_credentials';
    else if (text.length > 6000 || overdue.length > 20 || text.length + overdue.reduce((n, a) => n + a.description.length, 0) > 12000) failure = 'assessment_budget_exceeded';
    else try {
      const client = c.jevClient || new TypeSafeClient({ apiKey: c.jevApiKey, defaultModel: 'jev-1.13.0',
        baseURL: 'https://api.typesafe.ai', timeout, retry: { maxRetries: 0 }, compression: false, logLevel: 'off' });
      const questions = {};
      overdue.forEach((a, i) => {
        questions[`action_${i}`] = { type: 'choice', instructions: `${DATA_SENTENCE} Does state.update satisfy state.actions[${i}]?`, criteria: verdicts };
        questions[`quote_${i}`] = { type: 'choice', instructions: `${DATA_SENTENCE} Select the sentence in state.update that best establishes whether state.actions[${i}] was accomplished.`, criteria: quotes };
      });
      const result = await client.systemOne({ state: { update: text, actions: overdue }, questions },
        { signal: AbortSignal.timeout(timeout) });
      answers = result.answers;
      model = typeof result.model === 'string' ? result.model.slice(0, 80) : null;
    } catch (error) {
      failure = error?.status === 429 ? 'jev_budget_exhausted' : 'jev_unavailable';
      return { failure, retryImmediately: error?.status !== 429, retryable: error?.status === 429 || error?.status >= 500 ||
        ['TimeoutError', 'AbortError'].includes(error?.name) || /timeout/i.test(error?.message || '') };
    }
    return { quotes, answers, failure, model, retryable: false };
  }
  async function apply(c, actor, request, judgment) {
    const { subject_id: deal, idempotency_key: key, new_value: { note_id: note, update: text, actions: overdue } } = request;
    const source = request.new_value;
    const verb = source.verb || 'add-deal-note';
    const { quotes, answers, failure, model } = judgment;
    await lockDealField(c, deal, 'next_date');
    await lockDealField(c, deal, 'next_step');
    await assertUnchanged(c, actor, deal, overdue, source.candidate_id);
    const before = await currentPlan(c, deal);

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
        resolved, resolved_at: resolvedAt?.updated_at ?? null, resolved_version: resolvedAt?.version ?? null, review_required: !failure && (verdict === 'unclear' || !!hold), note_id: note, ...(source.candidate_id ? { candidate_id: source.candidate_id } : {}),
        update: text, confidence: valid ? answer.confidence : null };
      await writeEvent(c, actor, verb, 'deal', deal, { field: 'overdue_assessment',
        old: { status: 'open' }, new: assessment, human_quote: quote, cause: 'automation_job', agent_rationale: assessment.reason,
        idempotency_key: key, recorded_at_after_lock: true });
      assessments.push(assessment);
    }
    const resolvedIds = assessments.filter(a => a.resolved).map(a => a.action.id);
    const plan = source.candidate_id ? dealNextStepStatus(before?.next_step, before?.next_date)
      : await reconcilePlan(c, actor, deal, before, resolvedIds, key, verb);
    return { ...plan, overdue_assessment: { reason: failure || null, items: assessments },
      resolved_action_ids: resolvedIds,
      preserved_action_ids: assessments.filter(a => !a.resolved && a.action.source === 'next_action').map(a => a.action.id) };
  }
  async function assertUnchanged(c, actor, deal, overdue, candidateId = null) {
    if (candidateId && (await c.query('select 1 from capture_candidate where id=$1 and undone_at is not null', [candidateId])).rows.length)
      throw new ToolError({ error: 'overdue_action_changed' });
    const owners = await partnerOwnerIds(c, actor);
    const current = await actions(c, deal, owners);
    if (overdue.some(a => !current.some(b => b.id === a.id && b.source === a.source &&
      b.updated_at === a.updated_at && b.version === a.version)))
      throw new ToolError({ error: 'overdue_action_changed' });
  }
  async function process(c, actor, args, result, transact) {
    if (!partnerAuthoritySlugForActor(actor) || !result.deal_id) return result;
    const deadline = Date.now() + 25000;
    let attemptsLeft = 2;
    const requests = await transact(async () => (await c.query(`select * from event
      where field='overdue_assessment_pending' and subject_type='deal' and subject_id=$1
        and (actor_id=$2 or sponsoring_human_slug=$6 or actor_id=(select id from actor where slug=$6)) and organization_tenant_id=$3 and personal_scope=any($4::text[])
        and (idempotency_key=$5 or not exists (
          select 1 from event done where done.field='overdue_assessment_status'
            and done.new_value->>'request_id'=event.id::text
            and (done.new_value->>'status' in ('complete','conflict') or (done.new_value->>'attempt')::int>=3)))
      order by (idempotency_key=$5) desc,mutation_order limit 2`, [result.deal_id, actor.id, organizationTenantForActor(actor), visibleScopes(actor), args.idempotency_key, partnerAuthoritySlugForActor(actor)])).rows);
    for (const request of requests) {
      for (let retry = 0; retry < 2 && attemptsLeft > 0 && Date.now() < deadline; retry++) {
        const claim = await transact(async () => {
          await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [request.id]);
          const states = (await c.query(`select new_value,recorded_at > clock_timestamp()-interval '45 seconds' as leased from event
            where field='overdue_assessment_status' and new_value->>'request_id'=$1
            order by mutation_order desc limit 1`, [request.id])).rows;
          const state = states[0]?.new_value;
          if (state?.status === 'complete') return { cached: state.result };
          if (state?.status === 'conflict') return {cached: {overdue_assessment: {pending: false, reason: state.reason, items: []}}};
          if (state?.status === 'running' && states[0].leased) return {};
          if (state?.attempt >= 3) return {cached: {overdue_assessment: {pending: true, attempts: state.attempt, reason: state.status === 'running' ? 'interrupted' : state.reason, items: []}}};
          try { await assertUnchanged(c, actor, request.subject_id, request.new_value.actions, request.new_value.candidate_id); }
          catch (error) {
            if (error?.payload?.error !== 'overdue_action_changed') throw error;
            await status(c, actor, request, {status: 'conflict', attempt: state?.attempt || 0, reason: 'overdue_action_changed'});
            return {cached: {overdue_assessment: {pending: false, reason: 'overdue_action_changed', items: []}}};
          }
          const attempt = (state?.attempt || 0) + 1;
          await status(c, actor, request, { status: 'running', attempt, reason: 'assessment_in_progress' });
          return { attempt };
        });
        if (claim.cached) {
          if (request.idempotency_key === args.idempotency_key) result = { ...result, ...claim.cached };
          break;
        }
        if (!claim.attempt) break;
        attemptsLeft--;
        const judgment = await judge(c, request.new_value.update, request.new_value.actions, Math.max(1, Math.min(11000, deadline - Date.now())));
        let applied;
        try {
          applied = await transact(async () => {
            await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [request.id]);
            const latest = (await c.query(`select new_value from event where field='overdue_assessment_status'
              and new_value->>'request_id'=$1 order by mutation_order desc limit 1`, [request.id])).rows[0]?.new_value;
            if (latest?.status !== 'running' || latest.attempt !== claim.attempt) return {};
            if (judgment.failure) {
              await status(c, actor, request, {status: 'pending', attempt: judgment.retryable ? claim.attempt : 3, reason: judgment.failure});
              return { overdue_assessment: {pending: true, attempts: claim.attempt, reason: judgment.failure, items: []} };
            }
            const value = await apply(c, actor, request, judgment);
            await status(c, actor, request, {status: 'complete', attempt: claim.attempt, result: value});
            return value;
          });
        } catch (error) {
          if (error?.payload?.error !== 'overdue_action_changed') throw error;
          await transact(() => status(c, actor, request, {status: 'conflict', attempt: claim.attempt, reason: 'overdue_action_changed'}));
          applied = { overdue_assessment: { pending: false, reason: 'overdue_action_changed', items: [] } };
        }
        if (request.idempotency_key === args.idempotency_key) result = { ...result, ...applied };
        if (!judgment.failure || !judgment.retryable || judgment.retryImmediately === false) break;
      }
    }
    return result;
  }
  async function status(c, actor, request, value) {
    await writeEvent(c, actor, request.new_value.verb || 'add-deal-note', 'deal', request.subject_id, {
      field: 'overdue_assessment_status', new: {request_id: request.id, note_id: request.new_value.note_id, ...value},
      idempotency_key: request.idempotency_key, recorded_at_after_lock: true });
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
        select e.id as assessment_id,e.subject_id as deal_id,d.name as deal_name,
          case when e.field='overdue_assessment_fallback' then e.new_value || jsonb_build_object('reason',
            case when state.new_value->>'status'='running' then 'interrupted'
              else coalesce(state.new_value->>'reason','awaiting_assessment') end)
            else e.new_value end as assessment,
          'review-deal-update' as confirm_verb
        from event e join deal d on d.id=e.subject_id
        left join event request on request.subject_id=e.subject_id and request.idempotency_key=e.idempotency_key
          and request.field='overdue_assessment_pending'
        left join lateral (select new_value,recorded_at from event s
          where s.field='overdue_assessment_status' and s.new_value->>'request_id'=request.id::text
          order by s.mutation_order desc limit 1) state on true
        where e.subject_type='deal' and e.field in ('overdue_assessment','overdue_assessment_fallback')
          and (e.field='overdue_assessment' or (
            coalesce(state.new_value->>'status','pending') not in ('complete','conflict')
            and (state.new_value->>'status' in ('pending','failed')
              or coalesce(state.recorded_at,request.recorded_at) <= clock_timestamp()-interval '45 seconds'))
            or (e.field='overdue_assessment_fallback' and state.new_value->>'reason'='partner_review'))
          and e.organization_tenant_id=$1 and e.personal_scope=any($2::text[]) and e.new_value->>'review_required'='true'
          and not exists(select 1 from event newer where newer.subject_id=e.subject_id and newer.field in ('overdue_assessment','overdue_assessment_fallback')
            and newer.new_value#>>'{action,id}'=e.new_value#>>'{action,id}' and newer.mutation_order>e.mutation_order)
          and not exists(select 1 from event r where r.field='overdue_review' and r.new_value->>'assessment_id'=e.id::text)
          and (exists(select 1 from next_action a where a.id=(e.new_value#>>'{action,id}')::uuid and a.status='open' and a.owner_id=any($3::uuid[]))
            or exists(select 1 from capture_post_call_action a where a.id=(e.new_value#>>'{action,id}')::uuid and a.status='open' and a.owner_id=any($3::uuid[])))
        order by e.recorded_at,e.id limit 201`, [organizationTenantForActor(actor), visibleScopes(actor), owners])).rows;
        if (rows.length > 200) throw new ToolError({ error: 'deal_update_reviews_limit_exceeded' });
        return { schema_version: 'deal-update-reviews.v1', proposals: rows };
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
        const event = (await c.query("select subject_id,new_value,idempotency_key,field from event where id=$1 and subject_type='deal' and field in ('overdue_assessment','overdue_assessment_fallback') and organization_tenant_id=$2 and personal_scope=any($3::text[])", [args.assessment_id, organizationTenantForActor(actor), visibleScopes(actor)])).rows[0];
        if (!event) throw new ToolError({ error: 'assessment_not_found' });
        if (event.field === 'overdue_assessment_fallback') {
          const request = (await c.query(`select * from event where subject_id=$1 and idempotency_key=$2
            and field='overdue_assessment_pending'`, [event.subject_id, event.idempotency_key])).rows[0];
          await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [request.id]);
          const state = (await c.query(`select new_value,recorded_at > clock_timestamp()-interval '45 seconds' as leased
            from event where field='overdue_assessment_status' and new_value->>'request_id'=$1
            order by mutation_order desc limit 1`, [request.id])).rows[0];
          if (state?.new_value.status === 'complete' || (state?.new_value.status === 'running' && state.leased))
            throw new ToolError({error: 'assessment_in_progress_or_complete'});
          await status(c, actor, request, {status: 'conflict', attempt: state?.new_value.attempt || 0, reason: 'partner_review'});
        }
        const a = event.new_value;
        const owners = await partnerOwnerIds(c, actor);
        const owned = await c.query(a.action.source === 'next_action'
          ? 'select 1 from next_action where id=$1 and owner_id=any($2::uuid[])'
          : 'select 1 from capture_post_call_action where id=$1 and owner_id=any($2::uuid[])', [a.action.id, owners]);
        if (!owned.rows.length) throw new ToolError({ error: 'not_action_owner' });
        await lockDealField(c, event.subject_id, 'next_date');
        await lockDealField(c, event.subject_id, 'next_step');
        const reviewed = (await c.query("select verb,new_value from event where field='overdue_review' and new_value->>'assessment_id'=$1", [args.assessment_id])).rows[0];
        if (reviewed) {
          if (a.candidate_id && args.decision === 'undo' && reviewed.new_value.decision === 'undo')
            return { ok: true, decision: 'undo', action_id: a.action.id, already: 'undone' };
          throw new ToolError({ error: 'assessment_already_reviewed' });
        }
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
            and ($3::uuid is null or id=$3)
            order by id`, [event.subject_id, event.idempotency_key, a.candidate_id ? args.assessment_id : null])).rows;
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
          if (!a.candidate_id) {
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
        }
        if (args.decision === 'undo') for (const sibling of undoAssessments) await change(c, actor, sibling.action, 'open');
        if (args.decision === 'resolve') {
          const before = await currentPlan(c, event.subject_id);
          await change(c, actor, { ...a.action, updated_at: current.updated_at, version: current.version }, 'done');
          if (!a.candidate_id)
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
  async function preserved(c, deal, key) {
    return (await c.query(`select distinct (e.new_value#>>'{action,id}')::uuid as id
      from event e where e.subject_type='deal' and e.subject_id=$1 and (e.new_value->>'candidate_id' is null or e.idempotency_key=$2) and e.field='overdue_assessment'
        and e.new_value->>'resolved'='false' and e.recorded_at >= current_timestamp - interval '14 days'
        union select distinct (a->>'id')::uuid as id from event e
      cross join lateral jsonb_array_elements(e.new_value->'actions') a
      where e.subject_type='deal' and e.subject_id=$1 and (e.new_value->>'candidate_id' is null or e.idempotency_key=$2) and e.field='overdue_assessment_pending'
        and a->>'source'='next_action' and e.recorded_at >= current_timestamp - interval '14 days'
        and not exists(select 1 from event done where done.field='overdue_assessment_status'
          and done.new_value->>'request_id'=e.id::text and done.new_value->>'status' in ('complete','conflict'))
      /* deal-update:preserved */`, [deal, key])).rows.map(row => row.id);
  }
  async function undoCallAssessments(c, actor, deal, keys, key, verb = 'undo-call-change') {
    const rows = (await c.query(`select e.id,e.new_value from event e
      where e.subject_type='deal' and e.subject_id=$1 and e.field='overdue_assessment'
        and e.idempotency_key=any($2::text[]) and e.new_value->>'resolved'='true'
        and not exists(select 1 from event r where r.field='overdue_review' and r.new_value->>'assessment_id'=e.id::text)
      order by e.id`, [deal, keys])).rows;
    for (const row of rows) {
      const a = row.new_value;
      await change(c, actor, { ...a.action, updated_at: a.resolved_at, version: a.resolved_version }, 'open');
      await writeEvent(c, actor, verb, 'deal', deal, { field: 'overdue_review',
        new: { assessment_id: row.id, decision: 'undo', reason: 'Undid the recorded call change' },
        idempotency_key: key, recorded_at_after_lock: true });
    }
    return rows.map(row => row.new_value.action.id);
  }
  return { prepare, process, tools, preserved, undoCallAssessments };
}
