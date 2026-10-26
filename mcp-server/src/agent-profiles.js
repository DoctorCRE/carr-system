// Named agent profiles (loop 520; Joe's ruling 2026-08-22; concept at
// out/council/20260821-tuneup/agent-profiles-concept.md, settled to order
// level). The NAME persists — Builder, Designer, Reviewer, Doc — and the
// model behind it is interchangeable staffing detail, visible underneath.
//
// THE HARD BOUNDARY, enforced by absence: a profile is presentation and
// routing, NEVER write authority. Nothing in this module (or anywhere else)
// may read a profile row into a permission decision; no verb accepts a
// caller-claimed profile as permission; swapping a profile's model changes
// zero permissions. Desks stay transport, actors stay authority, profiles
// are the human-facing layer over both.
//
import { integerFields } from "./safe-integer.ts";

function profileRecord(row) {
  return integerFields(row, ["version"], "profile_version_invalid");
}

export function agentProfileTools() {
  return {
    "read-profiles": {
      description: "The named-agent roster: every persistent agent identity (Builder, Designer, Reviewer, Doc) with its charter as a skills list, the model currently staffing it, its desk if any, and its status (active / unstaffed / parked). The NAME is the thing partners learn; the model underneath is interchangeable staffing detail. Read-only and open — a profile is presentation and routing, never authority.",
      inputSchema: { type: "object", properties: {} },
      handler: async (c) => {
        const r = await c.query(
          `select profile_key, display_name, charter, current_model, current_desk,
                  sponsor_scope, status, version
             from agent_profile order by profile_key`);
        return { ok: true, profiles: r.rows.map(profileRecord), total: r.rows.length };
      },
    },

  };
}
