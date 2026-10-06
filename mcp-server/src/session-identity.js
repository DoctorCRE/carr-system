// WR-000117 — the session-identity READ PAIR.
//
// LIBRARY ONLY: no shebang and no main-module construct (the SCAC
// inventory scans for those by substring, so they are described, never spelled).
//
// BOTH verbs declare `writerConnection: true` and NO write flag. That is what
// makes mcp-server/src/mcp.js open `begin read only` for them, which is the
// transaction ops.session_identity_facts and ops.session_dispatch_history are
// written for -- both are `stable` at 0529 for exactly that reason. The
// functions resolve the caller's own actor from a transaction-local setting
// only the writer path installs; on the reader connection they would resolve
// nobody.
//
// NEITHER is authorityOnly. The Control Room calls both as the signed-in
// partner, who holds no sponsor-scoped authority binding, and an authority-only
// door is one the product cannot open however correct its SQL is. 0529 grants
// both to carr_writer AND carr_authority.
//
// NEITHER inputSchema carries an actor property, and `additionalProperties:
// false` with the actor absent is what makes the server-side derivation
// UNFORGEABLE rather than merely absent.

/** Pure shaper over ops.session_identity_facts. */
export function sessionIdentityProjection(facts, ToolError) {
  if (!facts || typeof facts !== "object" || facts.ok !== true) {
    throw new ToolError({ error: "session_identity_unavailable" });
  }
  return {
    ok: true,
    // THE COUNT COMPARISON, carried through to the caller. An empty list with
    // permission_filtered true is a filtered answer; an empty list with it
    // false is an empty system, and the product must be able to tell them
    // apart (V5-UX-C12 clause 2).
    permission_filtered: facts.permission_filtered === true,
    total_seen: facts.total_seen ?? 0,
    total_returned: facts.total_returned ?? 0,
    sessions: (Array.isArray(facts.sessions) ? facts.sessions : []).map(row => ({
      canonical_session_id: row.canonical_session_id,
      surface: row.surface,
      display_name: row.display_name,
      // Never "human": no relation anywhere stores a human-typed alias, so a
      // surface must not be able to read this name as one a human chose.
      alias_source: row.alias_source ?? "derived",
      parent_session_id: row.parent_session_id ?? null,
      parent_known: row.parent_known === true,
      native_host_id: row.native_host_id ?? null,
      native_host_supported: row.native_host_supported === true,
      work_state: row.work_state,
      work_state_evidence: row.work_state_evidence,
      last_observed_at: row.last_observed_at,
      observation_source: row.observation_source,
      project_affinity: row.project_affinity ?? null,
      latest_cwd: row.latest_cwd ?? null,
      latest_model_id: row.latest_model_id ?? null,
      attempt_count: row.attempt_count ?? 0,
      latest_attempt_ref: row.latest_attempt_ref ?? null,
    })),
  };
}

/** Pure shaper over ops.session_dispatch_history. */
export function sessionDispatchProjection(facts, ToolError) {
  if (!facts || typeof facts !== "object" || facts.ok !== true) {
    throw new ToolError({
      error: facts?.reason_id || "session_dispatch_history_unavailable" });
  }
  return {
    ok: true,
    session_id: facts.session_id,
    parent_session_id: facts.parent_session_id ?? null,
    permission_filtered: facts.permission_filtered === true,
    total_seen: facts.total_seen ?? 0,
    total_returned: facts.total_returned ?? 0,
    more: facts.more === true,
    next_cursor: facts.next_cursor ?? null,
    // WR-000119: NO LONGER THE NAMED GAP. 0531 gave the two missing stages a
    // substrate, so these three are read off the newest dispatch that carries
    // them rather than returned as constants -- and null still means null:
    // a silent desk leaves received null with the per-dispatch reason saying
    // which silence it is, never a value inferred from an adjacent row.
    received: facts.received ?? null,
    acknowledged: facts.acknowledged ?? null,
    stage_unavailable_reason: facts.stage_unavailable_reason ?? null,
    events: (Array.isArray(facts.events) ? facts.events : []).map(row => ({
      event_id: row.event_id,
      at: row.at,
      stage: row.stage,
      stage_evidence: row.stage_evidence,
      rationale: row.rationale ?? null,
      from_seat: row.from_seat ?? null,
      to_seat: row.to_seat ?? null,
      sponsor: row.sponsor ?? null,
      room_id: row.room_id ?? null,
      session_id: row.session_id,
      parent_session_id: row.parent_session_id ?? null,
      attempt_ref: row.attempt_ref ?? null,
      superseded_by: row.superseded_by ?? null,
      work_request_ref: row.work_request_ref ?? null,
      // PER DISPATCH, and the two nulls must never collapse. link_source says
      // whether this dispatch was proved by a room_dispatch_link row or merely
      // inferred from the body; stage_unavailable_reason says not_acknowledged
      // where a link exists with no ack row and no_dispatch_spine ONLY where
      // no link row exists at all.
      link_source: row.link_source ?? null,
      dispatch_ref: row.dispatch_ref ?? null,
      stage_unavailable_reason: row.stage_unavailable_reason ?? null,
    })),
  };
}

export function sessionIdentityTools({ ToolError }) {
  return {
    "read-session-identity": {
      writerConnection: true,
      description: "Look up sessions by name or ID and return canonical identity, parent lineage, native-host id, work state and freshness, filtered to what the acting actor may see. The acting actor is derived by the server; no argument names one.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        query: { type: "string", minLength: 1, maxLength: 200 },
        limit: { type: "integer", minimum: 1, maximum: 50 },
        include_closed: { type: "boolean" },
      }, required: [] },
      handler: async (c, _a, args) => {
        const r = await c.query(
          "select ops.session_identity_facts($1::text,$2::integer,$3::boolean) as facts",
          [args.query ?? null, args.limit ?? null, args.include_closed ?? null]);
        return sessionIdentityProjection(r.rows[0]?.facts, ToolError);
      },
    },

    "read-dispatch-history": {
      writerConnection: true,
      description: "Return the dispatch history for one session, newest first, with each stage carrying the evidence it came from and any stage this substrate cannot prove returned as unavailable rather than inferred.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        session_id: { type: "string", minLength: 1, maxLength: 200 },
        cursor: { type: "string", minLength: 1, maxLength: 500 },
        limit: { type: "integer", minimum: 1, maximum: 100 },
      }, required: ["session_id"] },
      handler: async (c, _a, args) => {
        const r = await c.query(
          "select ops.session_dispatch_history($1::text,$2::text,$3::integer) as facts",
          [args.session_id, args.cursor ?? null, args.limit ?? null]);
        return sessionDispatchProjection(r.rows[0]?.facts, ToolError);
      },
    },
  };
}
