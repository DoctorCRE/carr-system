// Produce bounded, attributed deal-update extracts and revision-checked history pages.

import { ToolError } from "./tool-error.ts";
import type { Actor } from "./verb-contract.ts";

type UpdateSource = "activity" | "note" | "event";
export type DealUpdate = {
  source: UpdateSource;
  id: string;
  at: string;
  actor: string;
  text: string;
  original: unknown;
};
type Position = { at: string; source: UpdateSource; id: string };
type Cursor = { version: 1; deal: string; actor: string; revision: string; after: Position };
export type DealUpdateSummaryV1 = {
  schema_version: "deal-update-summary.v1";
  deal_id: string;
  source_revision: string;
  summary: { method: "extractive"; text: string; citations: Position[] };
  coverage: {
    complete: boolean;
    unsupported_events_present: boolean;
    covered_count: number;
    read_count_lower_bound: number;
    sources_read: UpdateSource[];
    sources_missing: UpdateSource[];
    window: { oldest: string; newest: string } | null;
  };
  history: { entries: DealUpdate[]; next_cursor: string | null; order: "at/source/id descending"; revision: string };
};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TIME = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/;
const sources: UpdateSource[] = ["activity", "note", "event"];
function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function position(value: unknown): value is Position & Record<string, unknown> {
  return (
    object(value) &&
    typeof value.at === "string" &&
    TIME.test(value.at) &&
    typeof value.id === "string" &&
    UUID.test(value.id) &&
    sources.some((source) => source === value.source)
  );
}
function decode(value: unknown): Cursor {
  try {
    if (typeof value !== "string" || value.length > 4096) throw new Error();
    const parsed: unknown = JSON.parse(atob(value));
    if (
      !object(parsed) ||
      parsed.version !== 1 ||
      typeof parsed.deal !== "string" ||
      typeof parsed.actor !== "string" ||
      typeof parsed.revision !== "string" ||
      !position(parsed.after)
    )
      throw new Error();
    return { version: 1, deal: parsed.deal, actor: parsed.actor, revision: parsed.revision, after: parsed.after };
  } catch {
    throw new ToolError({ error: "invalid_history_cursor" });
  }
}
function updates(value: unknown): DealUpdate[] {
  if (!Array.isArray(value)) throw new ToolError({ error: "invalid_update_source" });
  return value.map((row: unknown) => {
    if (!position(row) || !object(row) || typeof row.actor !== "string" || typeof row.text !== "string")
      throw new ToolError({ error: "invalid_update_source" });
    return { source: row.source, id: row.id, at: row.at, actor: row.actor, text: row.text, original: row.original };
  });
}
const eventFields = "('phase','owner','attention','next_date','next_step','operating_state','outcome')";
const definitions = [
  { source: "activity", view: "v_deal_room_activity", where: "deal_id=$1", time: "occurred_at", text: "summary" },
  { source: "note", view: "v_deal_room_note", where: "deal_id=$1", time: "created_at", text: "text" },
  {
    source: "event",
    view: "v_deal_room_event",
    where: `subject_id=$1 and field in ${eventFields}`,
    time: "recorded_at",
    text: "field || ': ' || coalesce(old_value::text,'null') || ' → ' || coalesce(new_value::text,'null')",
  },
];
function corpus(continuation: boolean): string {
  return definitions
    .map(
      ({ source, view, where, time, text }) => `(
    select '${source}'::text as source, id, ${time} as stamp,
      to_char(${time} at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') as at,
      actor, ${text} as text, to_jsonb(v) - array['deal_id','subject_id','subject_type'] as original
    from ${view} v where ${where}
    ${continuation ? `and ($3::timestamptz is null or (${time}, '${source}'::text, id) < ($3::timestamptz,$4::text,$5::uuid))` : ""}
    order by ${time} desc, id desc limit ${continuation ? "$2" : "9"}
  )`,
    )
    .join(" union all ");
}
const revisionRows = definitions
  .map(
    ({ source, view, where, time }) => `select '${source}'::text as source, id,
    encode(sha256(convert_to((to_jsonb(v) - '${time}' || jsonb_build_object('${time}',
      to_char(${time} at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')))::text,'UTF8')),'hex') as hash
    from ${view} v where ${where}`,
  )
  .join(" union all ");
const SQL = `with revision_rows as (${revisionRows}), summary_rows as (${corpus(false)}), page_rows as (${corpus(true)})
  select (select encode(sha256(convert_to(coalesce(string_agg(source || ':' || hash,',' order by source, id),''),'UTF8')),'hex')
      from revision_rows) as revision,
    exists(select 1 from v_deal_room_deal where id=$1) as exists,
    exists(select 1 from v_deal_room_event where subject_id=$1 and (field is null or field not in ${eventFields})) as unsupported_events_present,
    (select coalesce(jsonb_agg(to_jsonb(s) - 'stamp' order by stamp desc, source desc, id desc),'[]'::jsonb)
      from (select * from summary_rows order by stamp desc, source desc, id desc limit 9) s) as summary_entries,
    (select coalesce(jsonb_agg(to_jsonb(p) - 'stamp' order by stamp desc, source desc, id desc),'[]'::jsonb)
      from (select * from page_rows order by stamp desc, source desc, id desc limit $2) p) as entries`;

export function dealUpdateSummaryTools() {
  return {
    "read-deal-update-summary-v1": {
      write: false,
      description:
        "Authenticated deal-update-summary.v1 read. Bounded attributed extracts of activity summaries, notes and domain field changes, with exact original history. No inference or model calls. Complete only when every supported update fits the extract budget. Cursor binds actor/deal and supported source content; relevant arrivals, edits or deletions require refresh. Unrelated writes and read auditing preserve continuation.",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        properties: {
          deal_id: { type: "string", format: "uuid" },
          page_size: { type: "integer", minimum: 1, maximum: 100 },
          cursor: { type: "string", maxLength: 4096 },
        },
        required: ["deal_id"],
      },
      handler: async (
        c: { query(sql: string, params: unknown[]): Promise<{ rows: unknown[] }> },
        actor: Actor,
        args: { deal_id: string; page_size?: number; cursor?: string },
      ): Promise<DealUpdateSummaryV1> => {
        const size = args.page_size ?? 50;
        if (!UUID.test(args.deal_id) || !Number.isInteger(size) || size < 1 || size > 100)
          throw new ToolError({ error: "invalid_summary_request" });
        if (!actor.id) throw new ToolError({ error: "unauthenticated" });
        const cursor = args.cursor === undefined ? null : decode(args.cursor);
        if (cursor && (cursor.deal !== args.deal_id || cursor.actor !== actor.id))
          throw new ToolError({ error: "cursor_scope_mismatch" });
        const result = await c.query(SQL, [
          args.deal_id,
          size + 1,
          cursor?.after.at ?? null,
          cursor?.after.source ?? null,
          cursor?.after.id ?? null,
        ]);
        const row: unknown = result.rows[0];
        if (
          !object(row) ||
          typeof row.revision !== "string" ||
          typeof row.exists !== "boolean" ||
          typeof row.unsupported_events_present !== "boolean"
        )
          throw new ToolError({ error: "invalid_update_source" });
        if (!row.exists) throw new ToolError({ error: "not_found" });
        if (cursor && cursor.revision !== row.revision)
          throw new ToolError({ error: "stale_history_cursor", refresh_required: true });
        const summaryRows = updates(row.summary_entries);
        const page = updates(row.entries);
        const cited: DealUpdate[] = [];
        const clauses: string[] = [];
        let budget = 1200;
        for (const entry of summaryRows.slice().reverse()) {
          const clause = `[${entry.source}:${entry.id}] ${entry.text}`;
          if (clause.length > budget) continue;
          cited.push(entry);
          clauses.push(clause);
          budget -= clause.length + 1;
        }
        const entries = page.slice(0, size);
        const last = entries.at(-1);
        const next: Cursor | null =
          page.length > size && last
            ? {
                version: 1,
                actor: actor.id,
                deal: args.deal_id,
                revision: row.revision,
                after: { at: last.at, source: last.source, id: last.id },
              }
            : null;
        return {
          schema_version: "deal-update-summary.v1",
          deal_id: args.deal_id,
          source_revision: row.revision,
          summary: {
            method: "extractive",
            text: clauses.length
              ? `Recorded source statements, oldest first: ${clauses.join(" ")}`
              : summaryRows.length
                ? "Updates exceed the extract budget. Open source history."
                : "No recorded updates.",
            citations: cited.map(({ at, source, id }) => ({ at, source, id })),
          },
          coverage: {
            complete: !row.unsupported_events_present && summaryRows.length < 9 && cited.length === summaryRows.length,
            unsupported_events_present: row.unsupported_events_present,
            covered_count: cited.length,
            read_count_lower_bound: summaryRows.length,
            sources_read: [...sources],
            sources_missing: [],
            window: cited.length ? { oldest: cited[0]?.at ?? "", newest: cited.at(-1)?.at ?? "" } : null,
          },
          history: {
            entries,
            next_cursor: next ? btoa(JSON.stringify(next)) : null,
            order: "at/source/id descending",
            revision: row.revision,
          },
        };
      },
    },
  };
}
