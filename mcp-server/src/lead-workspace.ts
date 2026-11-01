// Read lead workspace data and validate lifecycle reviews.
import type { Db, VerbInputs } from "./verb-contract.ts";

// The validator checks each field before returning a normalized review.
type StageReview = NonNullable<VerbInputs["update-lead"]["stage_review"]>;
// The existing default is Error; production injects the structured ToolError.
type ErrorConstructor = new (payload: any) => Error;

import { leadAdvice, researchSummary, researchEvidenceSql } from "./lead-follow-up.ts";

import { isCalendarDate } from "./calendar-date.ts";

// Optional authenticated Leads workspace; the legacy board has live callers.
export const LEAD_WORKSPACE_VERSION = "lead-workspace.v1";
export const LEAD_WORKSPACE_SCHEMA = {
  type: "object", additionalProperties: false,
  properties: {
    workspace: { type: "string", enum: ["leads"] },
    lead_id: { type: "string", pattern: "^[0-9a-fA-F-]{36}$" },
    correspondence_cursor: { type: "object", additionalProperties: false, required: ["occurred_at", "id"], properties: {
      occurred_at: { type: "string", format: "date-time" }, id: { type: "string", format: "uuid" },
    } },
    stage_history_cursor: { type: "string", pattern: "^[0-9]{1,19}$" },
  },
};

export async function readLeadWorkspace(c: Db, args: VerbInputs["lead-board"] = {}, ToolError: ErrorConstructor = Error) {
  const cursor = args.correspondence_cursor;
  if ((cursor !== undefined && (!cursor || typeof cursor !== "object" || Array.isArray(cursor) ||
      Object.keys(cursor).length !== 2 ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(cursor.id) ||
      typeof cursor.occurred_at !== "string" ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(cursor.occurred_at) ||
      !isCalendarDate(cursor.occurred_at.slice(0, 10)) || !Number.isFinite(Date.parse(cursor.occurred_at)))) ||
      (args.stage_history_cursor !== undefined && (typeof args.stage_history_cursor !== "string" ||
      !/^[0-9]{1,19}$/.test(args.stage_history_cursor) || BigInt(args.stage_history_cursor) > 9223372036854775807n)) ||
      ((cursor !== undefined || args.stage_history_cursor !== undefined) && (!args.lead_id || args.workspace !== "leads")))
    throw new ToolError({ error: "invalid_history_cursor" });
  // One statement is one PostgreSQL snapshot, also on the stateless HTTP
  // reader. UUID equality stays in PostgreSQL, including mixed-case input.
  const row = (await c.query(`with board as materialized (
      select w.*, p.prospect_client_id,p.prospect_client_version,p.prospect_engagement_status
        from v_lead_workspace w left join v_lead_prospect_engagement p on p.lead_id=w.id
    ), correspondence as materialized (
      select * from v_lead_workspace_correspondence where lead_id=$1::uuid
    ), correspondence_tail as materialized (
      select * from correspondence where $2::timestamptz is null or (occurred_at,id)<($2::timestamptz,$3::uuid)
      order by occurred_at desc,id desc limit 101
    ), correspondence_page as materialized (
      select * from correspondence_tail order by occurred_at desc,id desc limit 100
    ), stage_history as materialized (
      select * from v_lead_workspace_stage_history where lead_id=$1::uuid
    ), stage_tail as materialized (
      select * from stage_history where $4::bigint is null or mutation_order<$4::bigint
      order by mutation_order desc limit 101
    ), stage_page as materialized (
      select * from stage_tail order by mutation_order desc limit 100
    ) select coalesce((select jsonb_agg(to_jsonb(b) order by b.stage_sort,b.score desc nulls last,b.name,b.id)
        from board b),'[]'::jsonb) as leads,
      (select to_jsonb(b)||d.detail||jsonb_build_object(
        'research_evidence',${researchEvidenceSql('b.party_id')},
        'correspondence',coalesce((select jsonb_agg(to_jsonb(a)-'lead_id' order by a.occurred_at desc,a.id desc) from correspondence_page a),'[]'::jsonb),
        'stage_history',coalesce((select jsonb_agg(to_jsonb(e)-'lead_id'||jsonb_build_object('mutation_order',e.mutation_order::text) order by e.mutation_order desc) from stage_page e),'[]'::jsonb),
        'correspondence_page',jsonb_build_object('total',(select count(*) from correspondence),'has_more',(select count(*)>100 from correspondence_tail),
          'next_cursor',case when (select count(*)>100 from correspondence_tail) then
            (select jsonb_build_object('occurred_at',a.occurred_at,'id',a.id) from correspondence_page a order by a.occurred_at,a.id limit 1) end),
        'stage_history_page',jsonb_build_object('total',(select count(*) from stage_history),'has_more',(select count(*)>100 from stage_tail),
          'next_cursor',case when (select count(*)>100 from stage_tail) then
            (select e.mutation_order::text from stage_page e order by e.mutation_order limit 1) end))
        from board b join v_lead_workspace_detail d on d.id=b.id
        where b.id=$1::uuid) as detail`, [args.lead_id || null, cursor?.occurred_at || null, cursor?.id || null, args.stage_history_cursor || null])).rows[0];
  const { research_evidence, ...detail } = row.detail || {};
  return { schema_version: LEAD_WORKSPACE_VERSION, generated_at: new Date().toISOString(),
    leads: row.leads.map(leadAdvice), last_search_at: null, search_run_coverage: "unavailable",
    ...(args.lead_id ? { detail: row.detail ? { ...leadAdvice(detail), research: researchSummary(research_evidence) } : null } : {}) };
}

export function validateStageReview(review: StageReview, fields: object, ToolError: ErrorConstructor) {
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

export async function lockLeadLifecycle(c: Db, leadId: string, clientId: string | null = null) {
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
