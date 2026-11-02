// Exact merge eligibility is shared by review reads and locked acceptance.
export async function findExactPostCallAction(c, candidate, ownerId) {
  const stepKey = candidate.action_description.trim().replace(/\s+/gu, ' ').toLowerCase();
  return (await c.query(
    `select id,item_bullets,source,description,owner_id,status,due_on::text as due_on from (
           select id,item_bullets,description,owner_id,status,due_on,'next_action' as source,created_at as ordered_at from next_action
             where subject_type='deal' and subject_id=$1 and status='open' and owner_id=$3
               and lower(regexp_replace(btrim(description), '\\s+', ' ', 'g'))=$2
           union all
           select id,item_bullets,description,owner_id,status,due_on,'capture_post_call_action' as source,accepted_at as ordered_at from capture_post_call_action
             where deal_id=$1 and status='open' and owner_id=$3 and item_title is not null
               and lower(regexp_replace(btrim(description), '\\s+', ' ', 'g'))=$2
         ) actions order by ordered_at,id limit 1 /* capture:followup-existing */`,
        [candidate.deal_id, stepKey, ownerId])).rows[0] || null;
}

const normalized = text => text.trim().replace(/\s+/gu, ' ').toLowerCase();
function similar(left, right) {
  if (normalized(left) === normalized(right)) return true;
  const words = text => new Set(text.toLowerCase().match(/[\p{L}\p{N}]+/gu) || []);
  const a = words(left), b = words(right);
  const shared = [...a].filter(word => b.has(word)).length;
  return shared >= 2 && 2 * shared / (a.size + b.size) >= 0.6;
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
    const comparison = { schema_version: 'post-call-action-comparison.v1',
      status: ownerId ? 'no_match' : 'assignee_unavailable', exact_match: null, date_effect: null, possible_matches: [] };
    if (exact) {
      const stated = due_defaulted === false && candidate.due_on != null;
      const resulting = stated ? candidate.due_on : exact.due_on;
      comparison.status = 'exact_match';
      comparison.exact_match = actionSnapshot(exact, candidate.assignee_slug);
      comparison.date_effect = { current_due_on: exact.due_on, proposed_due_on: candidate.due_on,
        resulting_due_on: resulting, changes_due_on: resulting !== exact.due_on,
        reason: candidate.due_on == null ? 'unstated_date' : stated ? 'stated_date' : 'defaulted_date' };
    } else if (ownerId) {
      const actions = (await c.query(`select actions.*, a.slug as owner from (
        select id,owner_id,description,status,due_on::text as due_on,'next_action' as source,created_at as ordered_at
          from next_action where subject_type='deal' and subject_id=$1
        union all
        select id,owner_id,description,status,due_on::text as due_on,'capture_post_call_action' as source,accepted_at as ordered_at
          from capture_post_call_action where deal_id=$1
        ) actions join actor a on a.id=actions.owner_id order by ordered_at,id /* capture:preview-possible */`,
      [candidate.deal_id])).rows;
      comparison.possible_matches = actions.filter(action => similar(action.description, candidate.action_description))
        .map(action => actionSnapshot(action));
      if (comparison.possible_matches.length) comparison.status = 'possible_match';
    }
    result.push({ ...row, existing_action_comparison: comparison });
  }
  return result;
}
