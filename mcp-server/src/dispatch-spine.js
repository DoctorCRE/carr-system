// WR-000119 — the DISPATCH SPINE write pair.
//
// LIBRARY ONLY: no shebang and no main-module construct (the SCAC
// inventory scans for those by substring, so they are described, never spelled).
//
// BOTH verbs declare `writerConnection: true` AND `write: true`. That is the
// OPPOSITE of WR-000117's read pair, which declared the connection and no
// write flag on purpose so mcp-server/src/mcp.js would open `begin read only`.
// These two INSERT -- ops.record_dispatch_link and ops.acknowledge_dispatch
// are volatile at 0531 -- so a read-only transaction would fail them at
// runtime. The read pair's stable declaration is deliberately not copied here.
//
// NEITHER is authorityOnly. The room bridge and the desks call both as the
// signed-in writer, and 0531 grants both to carr_writer AND carr_authority.
//
// NEITHER inputSchema carries an actor, sponsor or tenant property, and
// `additionalProperties: false` with the actor absent is what makes the
// server-side derivation UNFORGEABLE rather than merely absent. The
// hermes-pilot restriction on record-dispatch-link is NOT here: it is raised
// inside the definer at 0531, because a check in this file would be walked
// around entirely by a direct SQL call.
//
// EACH CALL SITE WRITES ONLY WHAT IT OBSERVED FIRST-HAND. The bridge mints the
// link at the moment it appends the turn; the desk acknowledges `received`
// when the turn lands in a window; the acting session acknowledges
// `acknowledged` from inside its own turn. Nothing in this file lets one of
// them speak for another: there is no actor argument to speak with.

const DISPATCH_UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Pure shaper over ops.record_dispatch_link. */
export function dispatchLinkProjection(result, ToolError) {
  if (!result || typeof result !== "object" || result.ok !== true) {
    throw new ToolError({ error: result?.reason_id || "dispatch_link_refused" });
  }
  return {
    ok: true,
    deduplicated: result.deduplicated === true,
    link_id: result.id,
    dispatch_ref: result.dispatch_ref,
    turn_id: result.turn_id,
    turn_msg_id: result.turn_msg_id,
    session_id: result.session_id,
    // The DERIVED writer, echoed back. A caller that wanted a different value
    // here has no argument to put one in.
    written_by: result.written_by,
  };
}

/** Pure shaper over ops.acknowledge_dispatch. */
export function dispatchAckProjection(result, ToolError) {
  if (!result || typeof result !== "object" || result.ok !== true) {
    throw new ToolError({ error: result?.reason_id || "dispatch_ack_refused" });
  }
  return {
    ok: true,
    deduplicated: result.deduplicated === true,
    ack_id: result.id,
    dispatch_ref: result.dispatch_ref,
    stage: result.stage,
    by_actor: result.by_actor,
  };
}

export function dispatchSpineTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "record-dispatch-link": {
      write: true,
      writerConnection: true,
      description: "Record the explicit link between a room turn, named by its msg_id, and the session it was dispatched to, at the moment the turn is appended. Only the server-derived hermes-pilot identity may write one, enforced inside the database function; no argument names an actor.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        dispatch_ref:    { type: "string" },
        turn_msg_id:     { type: "string" },
        session_id:      { type: "string", minLength: 1, maxLength: 200 },
        work_request_id: { type: "string" },
      }, required: ["dispatch_ref", "turn_msg_id", "session_id"] },
      handler: async (c, actor, args) =>
        withEnvelope(c, actor, "record-dispatch-link", args, async () => {
          if (!DISPATCH_UUID.test(String(args.dispatch_ref || ""))) {
            throw new ToolError({ error: "dispatch_ref_invalid",
              hint: "dispatch_ref is a uuid minted by the caller and is the link's identity" });
          }
          if (!DISPATCH_UUID.test(String(args.turn_msg_id || ""))) {
            throw new ToolError({ error: "dispatch_turn_msg_id_invalid",
              hint: "turn_msg_id is the msg_id of the room turn that carried this dispatch" });
          }
          if (args.work_request_id != null &&
              !DISPATCH_UUID.test(String(args.work_request_id))) {
            throw new ToolError({ error: "dispatch_work_request_invalid",
              hint: "work_request_id is a uuid when present and is optional" });
          }
          const r = await c.query(
            "select ops.record_dispatch_link($1::uuid,$2::text,$3::uuid,$4::uuid) as result",
            [args.turn_msg_id, args.session_id, args.work_request_id ?? null,
              args.dispatch_ref]);
          const result = dispatchLinkProjection(r.rows[0]?.result, ToolError);
          if (!result.deduplicated) {
            // The SUBJECT IS THE dispatch_ref, not the bigserial id: event.subject_id
            // is a uuid column, and the dispatch_ref is this row's stable identity
            // anyway -- it is the key both relations join on.
            await writeEvent(c, actor, "record-dispatch-link", "room_dispatch_link",
              result.dispatch_ref, {
                field: "identity",
                new: { link_id: result.link_id, turn_id: result.turn_id,
                  session_id: result.session_id },
                cause: "automation_job",
                idempotency_key: args.dispatch_ref,
              });
          }
          return result;
        }),
    },

    "acknowledge-dispatch": {
      write: true,
      writerConnection: true,
      description: "Append one acknowledgement row for a dispatch at one stage -- received when the turn lands in a desk window, acknowledged when the acting session takes it up. The acting actor is derived by the server, so an acknowledgement is first-hand; no argument names one.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        dispatch_ref: { type: "string" },
        stage:        { type: "string", enum: ["received", "acknowledged"] },
        evidence:     { type: "string", maxLength: 500 },
      }, required: ["dispatch_ref", "stage"] },
      handler: async (c, actor, args) =>
        withEnvelope(c, actor, "acknowledge-dispatch", args, async () => {
          if (!DISPATCH_UUID.test(String(args.dispatch_ref || ""))) {
            throw new ToolError({ error: "dispatch_ref_invalid",
              hint: "dispatch_ref is the uuid the room bridge minted for this dispatch" });
          }
          const r = await c.query(
            "select ops.acknowledge_dispatch($1::uuid,$2::text,$3::text) as result",
            [args.dispatch_ref, args.stage, args.evidence ?? null]);
          const result = dispatchAckProjection(r.rows[0]?.result, ToolError);
          if (!result.deduplicated) {
            await writeEvent(c, actor, "acknowledge-dispatch", "room_dispatch_ack",
              result.dispatch_ref, {
                field: "stage",
                new: { ack_id: result.ack_id, stage: result.stage,
                  by_actor: result.by_actor },
                cause: "automation_job",
                idempotency_key: `${args.dispatch_ref}:${args.stage}`,
              });
          }
          return result;
        }),
    },
  };
}
