// Future obligations are linked at the registered execution seam, after the verb's
// existing validation and effect, in its caller-owned transaction.
import { randomUUID } from 'node:crypto';
import { organizationTenantForActor } from './identity.js';
import { partnerAuthoritySlugForActor } from './partner-authority.js';
import { ToolError } from './tool-error.ts';

// No prose classifier: these records can carry a commitment, so every successful
// write is captured. Factual observations and terminal dispositions are excluded.
export const FUTURE_WORK_WRITES = Object.freeze({
  'add-loop': 'loop', 'update-loop': 'loop',
  'log-decision': 'decision', 'update-decision': 'decision',
  'record-finding': 'finding', 'record-defect': 'defect',
  'set-next-action': 'action', 'set-next-step': 'action', 'log-outreach': 'action',
  'confirm-email-next-step': 'action',
  'add-deal-note': 'action', 'resolve-candidate': 'capture', 'resolve-post-call-candidate': 'post_call_action',
  'record-deal-item': 'deal_item', 'instruct-deal-item': 'deal_item', 'backfill-deal-items': 'deal_item',
  'add-critical-date': 'critical_date', 'open-campaign': 'campaign',
  'add-industry-event': 'industry_event', 'update-industry-event': 'industry_event',
  'write-doctrine-section': 'convention', 'change-doctrine-sections': 'convention',
  'ask-board-question': 'board_question', 'revise-board-question': 'board_question',
  'publish-board-snapshot': 'board_task',
  'request-checklist-document': 'requirement',
  'propose-retrieval-concept': 'retrieval_proposal', 'propose-retrieval-phrase': 'retrieval_proposal',
  'propose-retrieval-mapping': 'retrieval_proposal', 'propose-retrieval-retirement': 'retrieval_proposal',
  'suggest-doc-work': 'doc_suggestion', 'propose-doc-correction': 'doc_correction',
  'propose-meeting-action': 'meeting_action', 'decide-meeting-action': 'meeting_action',
  'record-email-proposal': 'email_proposal', 'propose-email-lead': 'email_lead',
  'triage-incident': 'incident',
});
export const workRequestLinkSchema = { type: 'string', minLength: 1, maxLength: 100,
  description: 'Existing Work Request for this future work. Omit to capture and link one automatically.' };

function records(verb, args, result, actor) {
  const kind = FUTURE_WORK_WRITES[verb];
  if (!kind || result?.ok === false) return [];
  if (['set-next-step','set-next-action','log-outreach','add-deal-note'].includes(verb) && !result.next_action_id) return [];
  if (verb === 'resolve-candidate' && !args.accept) return [];
  if (verb === 'resolve-post-call-candidate' && result.status !== 'confirmed') return [];
  if (kind === 'email_lead' && !result.pool_id) return [];
  if (verb === 'record-email-proposal' && !result.proposal_id) return [];
  if (verb === 'decide-meeting-action' && args.decision === 'decline') return [];
  if (kind === 'board_task') return Object.entries(args.snapshot.tasks || {})
    .filter(([, task]) => task && typeof task === 'object' && !Array.isArray(task) && !['done','failed','superseded'].includes(task.status))
    .map(([id, task]) => ({ ref: `board_task:${partnerAuthoritySlugForActor(actor)}:${args.board_id}:${id}`, title: task.title || id, source: task, work_request: typeof task.work_request === 'string' ? task.work_request : undefined }));
  if (kind === 'deal_item' && Array.isArray(result.items)) return result.items
    .filter(item => item.status === 'open').map(item => ({ref:`deal_item:${item.id}`,title:item.title,source:item}));
  if (kind === 'convention') {
    // Resolve the document in SQL: callers can pass either its slug or UUID.
    return (result.sections || [result]).filter(row => row.status !== 'retired')
      .map(row => ({ ref: `convention:${row.section_id}`, section_id: row.section_id,
        title: args.title || args.items?.find(item => item.section_key === row.section_key)?.title || row.section_key || args.section_key, source: args }));
  }
  const id = ['capture','post_call_action'].includes(kind) ? args.candidate_id
    : kind === 'board_question' ? `${partnerAuthoritySlugForActor(actor)}:${args.board_id}:${args.question_id}`
    : result.loop_id || result.decision_id || result.flag_id || result.defect_id || result.next_action_id
      || result.item?.id || result.request?.id || result.question?.id || result.proposal?.id
      || result.proposal_id || result.pool_id || result.critical_date_id || result.campaign_id || result.event?.id || args.item_id
      || (kind === 'incident' && args.ref) || result.suggestion_id || result.suggestion?.id
      || (kind === 'meeting_action' && `${args.meeting_id}:${result.action?.action_number || result.action_number || args.action_number || args.dedupe_key || args.idempotency_key}`)
      || result.ref || args.idempotency_key;
  return [{ ref: `${kind}:${id}`, work_request: kind === 'capture' ? result.result?.work_request : undefined, title: args.title || args.summary || args.prompt || args.description
    || args.polished_text || args.proposed_text || args.next_action || args.row_key || args.text || args.name || args.next_step || args.goal || args.body || args.defect_class || args.kind || verb, source: args }];
}

export async function linkFutureWork(c, actor, verb, args, result, writeEvent) {
  const tenant = organizationTenantForActor(actor);
  const links = [];
  for (const record of records(verb, args, result, actor)) {
    if (verb === 'resolve-candidate' && !(await c.query(
      "select 1 from capture_candidate where id=$1 and kind='next_step'", [args.candidate_id])).rows.length) continue;
    if (record.section_id && !(await c.query(`select 1 from doctrine_section s join doctrine_document d on d.id=s.document_id
      where s.id=$1 and d.slug='team-memory' and s.status='active' and s.section_key like 'conventions/proposed/%'`, [record.section_id])).rows.length) continue;
    const ref = record.ref;
    let requested = args.work_request || record.work_request;
    if (FUTURE_WORK_WRITES[verb] === 'incident') {
      const existing = (await c.query(`select l.ref from ops.incident_link l join ops.incident i on i.id=l.incident_id
        where i.ref=$1 and l.kind='work_request' order by l.ref limit 1`, [args.ref])).rows[0];
      if (existing && requested && requested !== existing.ref) throw new ToolError({error:'future_work_link_conflict',work_request:existing.ref});
      requested ||= existing?.ref;
    }
    await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [`future-work:${tenant}:${ref}`]);
    const prior = (await c.query(`select w.ref from ops.future_work_link f join ops.work_request w on w.id=f.work_request_id
      where f.organization_tenant_id=$1 and f.source_ref=$2`, [tenant,ref])).rows[0];
    if (prior) {
      if (requested && requested !== prior.ref) throw new ToolError({error:'future_work_link_conflict',work_request:prior.ref});
      links.push({source_ref:ref,work_request:prior.ref});
      continue;
    }
    // The source receipt is kept as provenance, without a foreign key that
    // would prevent the existing tool-call retention job from expiring it.
    await c.query(`insert into ops.future_work_link(organization_tenant_id,source_ref,source_verb,source_key)
      values($1,$2,$3,$4)`,[tenant,ref,verb,args.idempotency_key]);
    let wr;
    if (requested) {
      wr = (await c.query('select id,ref from ops.work_request where ref=$1 and organization_tenant_id=$2', [requested,tenant])).rows[0];
      if (!wr) throw new ToolError({error:'work_request_not_found',work_request:requested});
    } else {
      const kind = FUTURE_WORK_WRITES[verb];
      const persisted = {
        loop: ['select to_jsonb(r) as record from loop_item r where id=$1', result.loop_id],
        decision: ["select to_jsonb(r) as record from event r where subject_type='decision' and verb='log-decision' and subject_id=$1", result.decision_id],
        finding: ['select to_jsonb(r) as record from record_flag r where id=$1', result.flag_id],
        defect: ['select to_jsonb(r) as record from defect r where id=$1', result.defect_id],
        action: ['select to_jsonb(r) as record from next_action r where id=$1', result.next_action_id],
        incident: ['select to_jsonb(r) as record from ops.incident r where ref=$1', args.ref],
        capture: ['select to_jsonb(r) as record from capture_candidate r where id=$1', args.candidate_id],
        post_call_action: ['select to_jsonb(r) as record from capture_post_call_candidate r where id=$1', args.candidate_id],
      }[kind];
      const source = persisted ? (await c.query(persisted[0], [persisted[1]])).rows[0]?.record || record.source : record.source;
      const title = String(source.action_description || source.title || source.new_value?.title || source.payload?.title || source.payload?.text
        || source.polished_text || source.proposed_text || source.summary || source.description || source.actual || source.body
        || source.name || source.label || source.phrase || source.prompt || source.goal || record.title).trim().slice(0,200) || verb;
      const situation = JSON.stringify({ verb, source_ref:ref, record:source, result });
      wr = (await c.query(`select * from ops.capture_sourced_work_request($1,$2,$3,$4::jsonb,null,null,$5::uuid)`,
        [`record:${ref}`,title,`Complete the future work recorded at ${ref}.\nSource record (verbatim JSON):\n${situation}`,
          JSON.stringify([{id:'RECORDED-WORK',text:`Complete the recorded work: ${title}`}]),randomUUID()])).rows[0];
      if (!wr) throw new ToolError({error:'work_request_capture_refused'});
      await writeEvent(c,actor,'report-problem','ops_work_request',wr.id,{field:'state',new:{state:'captured',source_ref:ref},idempotency_key:args.idempotency_key});
    }
    await c.query(`update ops.future_work_link set work_request_id=$3
      where organization_tenant_id=$1 and source_ref=$2`,[tenant,ref,wr.id]);
    if (FUTURE_WORK_WRITES[verb] === 'incident') await c.query(`insert into ops.incident_link(incident_id,kind,ref,note)
      select id,'work_request',$2,'Linked by future-work capture' from ops.incident where ref=$1
      on conflict(incident_id,kind,ref) do nothing`,[args.ref,wr.ref]);
    links.push({source_ref:ref,work_request:wr.ref});
  }
  return links.length ? {...result,work_request:links[0].work_request,work_request_links:links} : result;
}
