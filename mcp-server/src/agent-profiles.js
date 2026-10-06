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
// STAFFING IS A RECORDED ACT: every change appends a history row naming the
// human whose authority ruled it (server-derived, never caller-supplied) and
// whether they ruled in person or through the standing delegation their
// session carries. The wire receipt rides the SAME transaction, so the
// observatory never learns of a staffing change late or not at all.

import { personalScopeForActor } from "./identity.js";
import { appendRoomTurn, OBSERVATORY_ROOM } from "./partner-room.js";
import { ToolError } from "./tool-error.js";

const PROFILE_STATUSES = ["active", "unstaffed", "parked"];
const KEY_SHAPE = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;

async function rulingIdentity(c, actor) {
  // A verified human partner rules in person. A sponsored session (Joe's or
  // Dell's own credential behind a machine door) rules through standing
  // delegation, attributed to the SPONSOR's human row — resolved through the
  // same definer function doctrine search uses, because carr_reader and
  // carr_writer must never read the actor table directly. Anything without a
  // partner behind it is refused before any write.
  if (actor?.human === true) return { ruledBy: actor.id, basis: "human" };
  const scope = personalScopeForActor(actor);
  if (scope.status !== "personal")
    throw new ToolError({ error: "profile_assignment_refused",
      hint: "staffing a profile takes a human partner or a session a partner sponsors; " +
            "this credential has no partner behind it" });
  const found = await c.query(
    `select retrieval_visibility_actor_id($1) as id
      where retrieval_visibility_actor_id($1) is not null`, [scope.sponsor]);
  if (found.rows.length !== 1)
    throw new ToolError({ error: "profile_assignment_refused",
      reason: "sponsor_not_active_human", sponsor: scope.sponsor });
  return { ruledBy: found.rows[0].id, basis: "standing_delegation" };
}

export function agentProfileTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "read-profiles": {
      description: "The named-agent roster: every persistent agent identity (Builder, Designer, Reviewer, Doc) with its charter as a skills list, the model currently staffing it, its desk if any, and its status (active / unstaffed / parked). The NAME is the thing partners learn; the model underneath is interchangeable staffing detail. Read-only and open — a profile is presentation and routing, never authority.",
      inputSchema: { type: "object", properties: {} },
      handler: async (c) => {
        const r = await c.query(
          `select profile_key, display_name, charter, current_model, current_desk,
                  sponsor_scope, status, version
             from agent_profile order by profile_key`);
        return { ok: true, profiles: r.rows, total: r.rows.length };
      },
    },

    "assign-profile": {
      write: true,
      description: "Staff a named agent profile: set or clear the model behind the name, move its desk, or change its status (active / unstaffed / parked). Every change appends an append-only history row naming the human whose authority ruled it — in person, or through the standing delegation a sponsored session carries — and posts the {\"agent_profile\":...} receipt to the partner-room wire in the same transaction. A profile is NEVER write authority: this changes what the observatory shows and how work is routed, and changes zero permissions. Needs base_version from a fresh read-profiles.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        profile_key: { type: "string", description: "builder | designer | reviewer | doc (extensible)" },
        model: { type: "string", description: "the model now staffing this name; omit or null to unstaff" },
        desk: { type: "string", description: "optional desk carrying this profile" },
        status: { type: "string", enum: PROFILE_STATUSES },
        base_version: { type: "integer", description: "current version from a fresh read-profiles" },
        note: { type: "string", description: "one line of why, kept in the history row" },
      }, required: ["idempotency_key", "profile_key", "status", "base_version"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "assign-profile", args, async () => {
        if (!KEY_SHAPE.test(String(args.profile_key || "")))
          throw new ToolError({ error: "profile_key_invalid" });
        if (!PROFILE_STATUSES.includes(args.status))
          throw new ToolError({ error: "profile_status_invalid", allowed: PROFILE_STATUSES });
        const model = args.model === undefined || args.model === null || args.model === ""
          ? null : String(args.model);
        const desk = args.desk === undefined || args.desk === null || args.desk === ""
          ? null : String(args.desk);
        if (args.status === "active" && model === null)
          throw new ToolError({ error: "profile_active_needs_model",
            hint: "an active profile is a staffed one — name the model, or set status unstaffed/parked" });

        const { ruledBy, basis } = await rulingIdentity(c, actor);

        const updated = await c.query(
          `update agent_profile
              set current_model=$1, current_desk=$2, status=$3,
                  version=version+1, updated_at=now()
            where profile_key=$4 and version=$5
            returning id, profile_key, display_name, charter, current_model,
                      current_desk, sponsor_scope, status, version`,
          [model, desk, args.status, args.profile_key, args.base_version]);
        if (!updated.rows.length) {
          const current = await c.query(
            `select version from agent_profile where profile_key=$1`, [args.profile_key]);
          if (!current.rows.length)
            throw new ToolError({ error: "profile_not_found", profile_key: args.profile_key,
              hint: "read-profiles lists the roster; new profiles are a migration, not a verb" });
          throw new ToolError({ error: "version_conflict",
            current_version: Number(current.rows[0].version),
            hint: "re-read the roster and retry with the fresh version" });
        }
        const profile = updated.rows[0];

        const history = await c.query(
          `insert into agent_profile_assignment
             (profile_id, model, desk, status, ruled_by, ruling_basis, note, idempotency_key)
           values ($1,$2,$3,$4,$5,$6,$7,$8)
           returning id`,
          [profile.id, model, desk, args.status, ruledBy, basis,
           args.note ? String(args.note) : null, args.idempotency_key]);

        await writeEvent(c, actor, "assign-profile", "agent_profile", profile.id, {
          profile_key: profile.profile_key, model, desk, status: args.status,
          ruling_basis: basis, idempotency_key: args.idempotency_key,
        });

        // The wire receipt, in the SAME transaction as the change: the
        // observatory's constraint is that profile truth must reach any feed
        // window, and a receipt that can be lost between a commit and a
        // separate poster is a receipt that will eventually be lost. It lands in
        // the room the panel reads (OBSERVATORY_ROOM).
        const scope = personalScopeForActor(actor);
        const sponsor = actor?.human === true ? actor.slug : scope.sponsor;
        const receipt = await appendRoomTurn(c, {
          room: OBSERVATORY_ROOM, sponsor, seat: "claude", kind: "receipt",
          body: JSON.stringify({ agent_profile: {
            key: profile.profile_key, name: profile.display_name,
            model: profile.current_model, desk: profile.current_desk,
            status: profile.status,
          } }),
          msgId: crypto.randomUUID(),
          originChannel: "mcp", originActor: actor.slug,
        });
        if (receipt.ok !== true)
          throw new ToolError({ error: "profile_receipt_failed", detail: receipt.error });

        return { ok: true, profile, assignment_id: history.rows[0].id,
                 ruled_by: ruledBy, ruling_basis: basis, receipt_seq: receipt.seq };
      }),
    },
  };
}
