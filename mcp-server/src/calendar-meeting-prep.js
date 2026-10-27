// Prepare upcoming Outlook meetings from possible attendee matches to owned open deals.
import { personalScopeForActor, organizationTenantForActor } from "./identity.js";
import { CALENDAR_AGENDA_BOARD, calendarSection, addCalendarDealSuggestions } from "./brief-calendar.js";

export function calendarMeetingPrepTool(ToolError) {
  return {
    write: false,
    description: "Prepare the authenticated partner's next seven days of Outlook meetings with possible open deals, their phase, next step, next date, last touch, and market. Attendee email matches are suggestions, never confirmed meeting-to-deal links. Preserves calendar freshness and attendance warnings. Takes no audience or partner argument.",
    inputSchema: { type: "object", additionalProperties: false, properties: {} },
    handler: async (c, actor, args) => {
      const scope = personalScopeForActor(actor);
      if (scope.status !== "personal") throw new ToolError({ error: "calendar_prep_requires_partner_scope" });
      if (Object.keys(args || {}).length) throw new ToolError({ error: "calendar_prep_context_not_selectable" });
      let section;
      if (c.inTransaction) await c.query("savepoint calendar_prep_snapshot");
      try {
        const { rows } = await c.query(`select snapshot_json from board_snapshot
          where organization_tenant_id=$1 and sponsoring_human_slug=$2 and board_id=$3`,
        [organizationTenantForActor(actor), scope.sponsor, CALENDAR_AGENDA_BOARD]);
        section = calendarSection(rows[0]?.snapshot_json);
        if (c.inTransaction) await c.query("release savepoint calendar_prep_snapshot");
      } catch {
        if (c.inTransaction) {
          await c.query("rollback to savepoint calendar_prep_snapshot");
          await c.query("release savepoint calendar_prep_snapshot");
        }
      }
      if (!section) return { state: "unavailable", reason: "source_unavailable", items: [] };
      if (!section.sources) return section;
      section.match_basis = "attendee_email_possible_match";
      await addCalendarDealSuggestions(c, section, scope.sponsor);
      const ids = [...new Set(section.items.flatMap(item => item.possible_deals.map(deal => deal.id)))];
      if (!ids.length) return section;
      for (const item of section.items) for (const deal of item.possible_deals) deal.prep = { state: "unavailable" };
      if (c.inTransaction) await c.query("savepoint calendar_meeting_prep");
      try {
        const { rows } = await c.query(`select id::text, phase, next_step, next_date::text,
          last_touch::text, market from v_open_deal_room_board
          where id = any($1::uuid[]) and lower(owner)=$2
          order by id limit 1000 /* calendar_meeting_prep */`, [ids, scope.sponsor]);
        if (c.inTransaction) await c.query("release savepoint calendar_meeting_prep");
        const byId = new Map(rows.map(row => [row.id, row]));
        let missing = false;
        for (const item of section.items) for (const deal of item.possible_deals) {
          const row = byId.get(deal.id);
          if (!row) { missing = true; continue; }
          deal.prep = { state: "ready", phase: row.phase, next_step: row.next_step,
            next_date: row.next_date, last_touch: row.last_touch, market: row.market };
        }
        section.sources.meeting_prep = { state: missing ? "partial" : "ready" };
        if (missing) {
          section.state = "partial";
          section.notes.push("Preparation is unavailable for some possible deals. Check the deal record.");
        }
      } catch {
        if (c.inTransaction) {
          await c.query("rollback to savepoint calendar_meeting_prep");
          await c.query("release savepoint calendar_meeting_prep");
        }
        section.state = "partial";
        section.sources.meeting_prep = { state: "unavailable" };
        section.notes.push("Meeting deal preparation could not be checked.");
      }
      return section;
    },
  };
}
