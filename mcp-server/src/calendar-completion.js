import { createHash } from 'node:crypto';
import { ToolError } from './tool-error.ts';
import { calendarSection, CALENDAR_AGENDA_BOARD } from './brief-calendar.js';
import { organizationTenantForActor } from './identity.js';
import { partnerAuthoritySlugForActor } from './partner-authority.js';

const hash = value => createHash('sha256').update(value).digest('hex');
const fail = () => { throw new ToolError({ error:'calendar_completion_review_required' }); };
export function completedReview(review, revision, now = Date.now()) {
  if (!review || review.conflict || review.time_changed || review.disposition === 'canceled' || !/^[a-f0-9]{64}$/.test(revision ?? '') || review.source_revision !== revision
      || !/^[a-f0-9]{64}$/.test(review.canonical_event_identity ?? '')
      || review.canonical_event_identity.slice(0,16) !== review.key
      || !(Date.parse(review.start) < Date.parse(review.end) && Date.parse(review.end) <= now)
      || !Array.isArray(review.attendees) || review.attendees.length !== review.attendee_count) fail();
  return review;
}
export function resolvedCalendarRoles(review, contacts) {
  const emails = [...new Set([review.organizer, ...review.attendees].map(p => p?.email?.trim().toLowerCase()).filter(Boolean))];
  const refs = new Set(), unresolved = [];
  for (const email of emails) {
    const rows = contacts.filter(row => row.address === email);
    const parties = new Set(rows.map(row => row.contact_snapshot?.party_id));
    if (!rows.length || rows.some(row => !row.exact || !row.contact_snapshot) || parties.size !== 1) {
      unresolved.push(hash(email));
      continue;
    }
    for (const row of rows) refs.add(row.ref);
  }
  return { refs:[...refs].sort(), unresolved };
}

// The only activity-writing seam. WR634 lineage can replace its request shape.
async function writeRoleActivity(c, actor, execute, event, target, review) {
  const key = hash(`calendar-role:${event}:${target.subject_type}:${target.subject_id}`);
  const idempotency_key = `${key.slice(0,8)}-${key.slice(8,12)}-4${key.slice(13,16)}-a${key.slice(17,20)}-${key.slice(20,32)}`;
  return execute(c, actor, 'log-activity', { idempotency_key, ref:target.subject_id,
    kind:'meeting', summary:review.title, occurred_at:review.end });
}

export async function confirmCalendarCompletion(c, actor, key, revision, execute) {
  if (actor.human !== true) fail();
  await c.query("set local lock_timeout='5s'");
  await c.query("set local statement_timeout='10s'");
  const tenant = organizationTenantForActor(actor), partner = partnerAuthoritySlugForActor(actor);
  await c.query("select pg_advisory_xact_lock(hashtextextended($1,0))", [`calendar-completion:${tenant}:${partner}:${key}`]);
  const snapshot = await c.query(`select snapshot_json from board_snapshot where organization_tenant_id=$1
    and sponsoring_human_slug=$2 and board_id=$3 for share`,[tenant,partner,CALENDAR_AGENDA_BOARD]);
  const review = completedReview(calendarSection(snapshot.rows[0]?.snapshot_json)?.follow_up_reviews?.find(row=>row.key===key), revision);
  const contacts = (await execute(c, actor, 'email-match-contacts', {})).contacts;
  const resolved = resolvedCalendarRoles(review, contacts);
  const targets = (await c.query(`select distinct subject_type,subject_id::text from v_ref_index
    where (ref=any($1::text[]) or subject_id::text=any($1::text[]))
    and subject_type in ('client','lead','vendor') order by subject_type,subject_id::text`,[resolved.refs])).rows;
  const saved = (await c.query(`select outcomes from calendar_completion where organization_tenant_id=$1
    and sponsoring_human_slug=$2 and event_identity=$3`,[tenant,partner,review.canonical_event_identity])).rows[0];
  const outcomes = saved?.outcomes ?? [];
  for (const target of targets) {
    if (outcomes.some(row=>row.role===target.subject_type && row.target===target.subject_id)) continue;
    const result = await writeRoleActivity(c,actor,execute,review.canonical_event_identity,target,review);
    outcomes.push({role:target.subject_type,target:target.subject_id,activity_id:result.activity_id,status:'applied'});
  }
  await c.query(`insert into calendar_completion (organization_tenant_id,sponsoring_human_slug,event_identity,
    event_key,source_revision,disposition,resolved_attendees,unresolved_attendees,outcomes,confirmed_by)
    values($1,$2,$3,$4,$5,'confirmed-complete',$6::jsonb,$7::jsonb,$8::jsonb,$9)
    on conflict(organization_tenant_id,sponsoring_human_slug,event_identity) do update set
    source_revision=excluded.source_revision,resolved_attendees=excluded.resolved_attendees,
    unresolved_attendees=excluded.unresolved_attendees,outcomes=excluded.outcomes`,
    [tenant,partner,review.canonical_event_identity,key,revision,JSON.stringify(targets),JSON.stringify(resolved.unresolved),JSON.stringify(outcomes),actor.id]);
  return {disposition:'confirmed-complete',source_revision:revision,occurred_at:review.end,outcomes,unresolved_attendees:resolved.unresolved};
}
