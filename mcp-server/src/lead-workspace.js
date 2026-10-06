// Optional authenticated Leads workspace; the legacy board has live callers.
export const LEAD_WORKSPACE_VERSION = "lead-workspace.v1";
export const LEAD_WORKSPACE_SCHEMA = {
  type: "object", additionalProperties: false,
  properties: { workspace: { type: "string", enum: ["leads"] }, lead_id: { type: "string", pattern: "^[0-9a-fA-F-]{36}$" } },
};

export async function readLeadWorkspace(c, args = {}) {
  // One statement is one PostgreSQL snapshot, also on the stateless HTTP
  // reader. UUID equality stays in PostgreSQL, including mixed-case input.
  const row = (await c.query(`with board as materialized (
      select * from v_lead_workspace
    ) select coalesce((select jsonb_agg(to_jsonb(b) order by b.stage_sort,b.score desc nulls last,b.name,b.id)
        from board b),'[]'::jsonb) as leads,
      (select to_jsonb(b)||d.detail from board b join v_lead_workspace_detail d on d.id=b.id
        where b.id=$1::uuid) as detail`, [args.lead_id || null])).rows[0];
  return { schema_version: LEAD_WORKSPACE_VERSION, generated_at: new Date().toISOString(),
    leads: row.leads, last_search_at: null, search_run_coverage: "unavailable",
    ...(args.lead_id ? { detail: row.detail } : {}) };
}

export function validateStageReview(review, fields, ToolError) {
  const allowed = new Set(["reason","evidence_ids","undo_event_id","human_quote"]);
  if (!review || typeof review !== "object" || Array.isArray(review) ||
      Object.keys(review).some(key => !allowed.has(key)) ||
      typeof review.reason !== "string" || !review.reason.trim() || review.reason.length > 1000 ||
      !Array.isArray(review.evidence_ids) || review.evidence_ids.length > 20 ||
      review.evidence_ids.some(id => typeof id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) ||
      (review.human_quote !== undefined && (typeof review.human_quote !== "string" || review.human_quote.length > 1000)) ||
      (review.undo_event_id !== undefined && (typeof review.undo_event_id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(review.undo_event_id))) ||
      !Object.hasOwn(fields,"stage")) throw new ToolError({ error: "stage_review_invalid" });
  if (review.undo_event_id && (Object.keys(fields).length !== 1 || !Object.hasOwn(fields,"stage")))
    throw new ToolError({ error: "undo_stage_only" });
  return { ...review, evidence_ids: [...new Set(review.evidence_ids.map(id => id.toLowerCase()))] };
}

export async function lockLeadLifecycle(c, leadId, clientId = null) {
  // Lead lock is acquired by versionGuard first; then clients by ID, then
  // parties by ID. FOR SHARE protects lifecycle UPDATEs (KEY SHARE does not).
  const clients = (await c.query(`select id,party_id from client where id=$1::uuid
    order by id for share`, [clientId])).rows;
  await c.query(`select p.id from party p where p.id in (
    select party_id from lead where id=$1 union select party_id from client where id=$2::uuid
  ) order by p.id for share`, [leadId,clientId]);
  return { current: (await c.query("select * from v_lead_workspace_lifecycle where id=$1", [leadId])).rows[0],
    target: clients.length ? (await c.query(`select cl.id from client cl join party p on p.id=cl.party_id
      where cl.id=$1 and cl.merged_into is null and p.merged_into is null and p.deleted_at is null`, [clientId])).rows[0] : null };
}
