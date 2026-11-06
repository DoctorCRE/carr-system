// Exact merge eligibility is shared by review reads and locked acceptance.
export async function findExactPostCallAction(c, candidate, ownerId) {
  const stepKey = candidate.action_description.trim().replace(/\s+/gu, ' ').toLowerCase();
  return (await c.query(
    `select id,item_bullets,source,description,owner_id,status,due_on::text as due_on from (
           select id,item_bullets,description,owner_id,status,due_on,'next_action' as source,created_at as ordered_at from next_action
             where subject_type='deal' and subject_id=$1 and status='open' and owner_id=$3
               and lower(btrim(regexp_replace(description, $4, ' ', 'g')))=$2
           union all
           select id,item_bullets,description,owner_id,status,due_on,'capture_post_call_action' as source,accepted_at as ordered_at from capture_post_call_action
             where deal_id=$1 and status='open' and owner_id=$3 and item_title is not null
               and lower(btrim(regexp_replace(description, $4, ' ', 'g')))=$2
         ) actions order by ordered_at,id limit 1 /* capture:followup-existing */`,
        [candidate.deal_id, stepKey, ownerId, '[\u0009-\u000d \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+'])).rows[0] || null;
}

const actionSnapshot = (row, owner = row.owner) => ({
  id: row.id, source: row.source, owner, description: row.description, status: row.status, due_on: row.due_on,
});

export async function withPostCallActionComparisons(c, rows) {
  const result = [];
  for (const { due_defaulted, ...row } of rows) {
    if (row.kind !== 'assigned_action') { result.push(row); continue; }
    const candidate = { deal_id: row.deal_id, assignee_slug: row.payload.assignee,
      action_description: row.payload.action, due_on: row.payload.due_on, due_defaulted };
    const ownerId = (await c.query('select retrieval_visibility_actor_id($1) as id /* capture:preview-assignee */',
      [candidate.assignee_slug])).rows[0]?.id;
    const exact = ownerId ? await findExactPostCallAction(c, candidate, ownerId) : null;
    const comparison = { schema_version: 'post-call-action-comparison.v2',
      status: ownerId ? 'no_match' : 'assignee_unavailable', exact_match: null, date_effect: null, related_open_tasks: [], related_open_task_total: null };
    if (exact) {
      const stated = due_defaulted === false && candidate.due_on != null;
      const resulting = stated ? candidate.due_on : exact.due_on;
      comparison.status = 'exact_match';
      comparison.exact_match = actionSnapshot(exact, candidate.assignee_slug);
      comparison.date_effect = { current_due_on: exact.due_on, proposed_due_on: candidate.due_on,
        resulting_due_on: resulting, changes_due_on: resulting !== exact.due_on,
        reason: candidate.due_on == null ? 'unstated_date' : stated ? 'stated_date' : 'defaulted_date' };
    } else if (ownerId) {
      const actions = (await c.query(`select actions.*, a.slug as owner, count(*) over () as total from (
        select id,owner_id,description,status,due_on::text as due_on,'next_action' as source,created_at as ordered_at
          from next_action where subject_type='deal' and subject_id=$1 and status='open'
        union all
        select id,owner_id,description,status,due_on::text as due_on,'capture_post_call_action' as source,accepted_at as ordered_at
          from capture_post_call_action where deal_id=$1 and status='open'
        ) actions join actor a on a.id=actions.owner_id order by ordered_at desc nulls last, actions.id desc limit 5 /* capture:preview-related-open */`,
      [candidate.deal_id])).rows;
      comparison.related_open_task_total = Number(actions[0]?.total ?? 0);
      comparison.related_open_tasks = actions.map(action => actionSnapshot(action));
      if (comparison.related_open_tasks.length) comparison.status = 'related_open_tasks';
    }
    result.push({ ...row, existing_action_comparison: comparison });
  }
  return result;
}
