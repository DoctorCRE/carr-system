// One fixed, ordered AI capability program over the canonical ops.work_request
// lifecycle. Project definitions and mutable state live in Postgres. This module
// owns the server-enforced transition and independent-verification boundary.

export const DEFAULT_PROGRAM = "carr-ai-engineering-suite-v1";

const textPresent = value => typeof value === "string" && value.trim().length > 0;

export function fixedProgramKey(value) {
  return value === undefined || value === null || value === "" || value === DEFAULT_PROGRAM;
}

export function nextProjectState(currentSequence, laterRows) {
  if (!Array.isArray(laterRows) || laterRows.length === 0) return { completeProgram: true, nextSequence: null };
  const next = [...laterRows].sort((a, b) => Number(a.sequence) - Number(b.sequence))[0];
  if (Number(next.sequence) !== Number(currentSequence) + 1) throw new Error("queue_gap");
  if (next.state !== "ready") throw new Error("successor_not_ready");
  return { completeProgram: false, nextSequence: Number(next.sequence) };
}

function programRow(row) {
  if (!row) return null;
  return {
    ref: row.ref, sequence: Number(row.program_ordinal), title: row.title, state: row.state,
    version: Number(row.version), disposition: row.disposition, existing_status: row.existing_status,
    desired_outcome: row.desired_outcome, acceptance_criteria: row.acceptance_criteria || [],
    context: row.project_context || {}, executor_actor: row.executor_actor || null,
    blocker_code: row.blocker_code || null, blocker_detail: row.blocker_detail || null,
    completion_kind: row.completion_kind || null, completion_evidence: row.completion_evidence || null,
    shape_disposition: row.shape_disposition || null,
    shape_fixed_surface_ref: row.shape_fixed_surface_ref || null,
    shape_rationale: row.shape_rationale || null,
  };
}

function requireFixedProgram(args, ToolError) {
  if (!fixedProgramKey(args.program_key)) throw new ToolError({ error: "fixed_capability_program", program_key: args.program_key, allowed: DEFAULT_PROGRAM });
  return DEFAULT_PROGRAM;
}

async function readCurrent(c, lock = false) {
  const suffix = lock ? " for update of w" : "";
  const r = await c.query(`select w.* from ops.work_request w where w.program_key=$1 and w.state <> 'confirmed_closed' order by w.program_ordinal limit 1${suffix}`, [DEFAULT_PROGRAM]);
  return r.rows[0] || null;
}

// A DECLINE IS NOT SUBJECT TO BUILD ORDERING, because a decline is never built.
//
// The program is strictly ordered so a session cannot skip ahead to easy work
// and leave the hard rows behind. That reasoning is about WORK. It does not
// reach a row that will never be built: holding a settled decline behind
// twenty-two build items does not protect the queue, it is what makes the queue
// read fifty deep when twenty-one items are real — the dysfunction the tune-up
// council named, and its ruling that the declines become a permanent register is
// authority for them leaving the build lane.
//
// It cost something concrete. On 2026-08-22 Joe ruled on twelve declines in one
// sitting, five of which needed only his decision, and NOT ONE could be recorded:
// the earliest sat at sequence 24 behind an unbuilt evaluation harness at
// sequence 2. His ruling would have waited weeks on work it does not depend on.
//
// WHAT IS NOT RELAXED. Buildable work stays strictly ordered — the exemption
// tests the row's disposition, not the caller's convenience. The version check is
// unchanged, so a stale read still refuses. The decline still pays the whole
// completion price: a recorded decision, an independent attestor who is not the
// implementer, and the same capsule the database enforces.
async function requireProject(c, ToolError, sequence, baseVersion) {
  const head = await readCurrent(c, true);
  if (!head) throw new ToolError({ error: "capability_program_complete", program_key: DEFAULT_PROGRAM });

  let row = head;
  if (Number(head.program_ordinal) !== Number(sequence)) {
    const other = await c.query(
      `select * from ops.work_request where program_key=$1 and program_ordinal=$2
         and state <> 'confirmed_closed' for update`, [DEFAULT_PROGRAM, sequence]);
    const candidate = other.rows[0];
    if (!candidate || candidate.disposition !== "decline") {
      throw new ToolError({ error: "out_of_order_project", current_sequence: Number(head.program_ordinal), requested_sequence: Number(sequence),
        hint: "only a row already dispositioned decline may be settled out of order; build work stays in sequence" });
    }
    row = candidate;
  }

  if (Number(row.version) !== Number(baseVersion)) throw new ToolError({ error: "version_conflict", current_version: Number(row.version), base_version: Number(baseVersion), resolution: "re-read capability-program and retry against the current project version; never overwrite blind" });
  row.__is_program_head = Number(row.program_ordinal) === Number(head.program_ordinal);
  return row;
}

// Kept so every existing caller keeps its name and its behaviour for build work.

export function capabilityProgramTools({ withEnvelope, writeEvent, ToolError }) {
  const projectFields = { idempotency_key: { type: "string" }, program_key: { type: "string", const: DEFAULT_PROGRAM }, sequence: { type: "integer" }, base_version: { type: "integer" }, human_quote: { type: "string" } };
  return {
    // WHY A CANCEL EXISTS AT ALL, and why it took a stuck row to notice.
    //
    // Every other verb here moves work FORWARD. start, begin, prepare, attest
    // and complete each assume the session that opened will eventually close.
    // Nothing expressed the ordinary case where it does not: a session opens a
    // build session, freezes a candidate, and then the reason for the work goes
    // away — the decision it was carrying is reversed, the row is reprioritised,
    // or the session simply dies.
    //
    // IT COST A REAL ROW. On 2026-08-24 a session took WR-AI-038, the
    // transformer row, as far as `verification` with a frozen decline candidate
    // and stopped. On 2026-08-26 Joe ruled that the whole suite stays in place
    // and no further declines are to be recorded, which made finishing that
    // candidate forbidden — and there was no other move. A check across all 183
    // deployed verbs found nothing that could release it. The row could be
    // neither completed nor abandoned, and the partial unique index
    // capability_one_open_session_per_request meant its dead session also blocked
    // any future attempt on the same row. One unfinished session had made the row
    // permanently unworkable.
    //
    // WHAT IT DOES NOT DO, because a cancel is the cheapest verb to make
    // dangerous. It never touches a confirmed_closed row: requireProject only
    // returns rows that are not closed, so a settled close cannot be undone by
    // calling this instead of arguing with the immutability trigger. It never
    // deletes evidence — the session is moved to 'cancelled' and keeps its
    // candidate, and any attestation already recorded against it stays exactly
    // where it is, because what happened did happen. It records a REASON and
    // refuses without one, which is the same rule the canonical machine applies
    // to every other side exit.
    //
    // THE SESSION ID IS RESOLVED, NOT SUPPLIED, and that is deliberate. Its
    // siblings take capability_agent_session_id because the caller is expected
    // to be holding the session it just created. A cancel is used by whoever
    // finds the wreck, and the session id of a non-head row is not exposed by any
    // read verb — demanding it would make the verb unusable in exactly the
    // situation it exists for. The partial unique index guarantees at most one
    // open session per row, so there is nothing to disambiguate, and base_version
    // still refuses a stale read. Passing the id is allowed and is verified when
    // given.
    "cancel-capability-session": {
      write: true,
      description: "Abandon the one open build session on a capability project and return that project to ready, so a row left mid-flight can be worked again. It records a reason, keeps the cancelled session and every attestation already made, and can never reopen a confirmed_closed project.",
      inputSchema: { type: "object", additionalProperties: false, properties: { ...projectFields, reason: { type: "string" }, capability_agent_session_id: { type: "string" } }, required: ["idempotency_key", "sequence", "base_version", "reason"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "cancel-capability-session", args, async () => {
        requireFixedProgram(args, ToolError);
        if (!textPresent(args.reason)) throw new ToolError({ error: "capability_cancel_reason_required", hint: "every side exit records a reason; say what stopped this work" });
        const row = await requireProject(c, ToolError, args.sequence, args.base_version);

        const CANCELLABLE = ["claimed", "in_progress", "verification"];
        if (!CANCELLABLE.includes(row.state)) throw new ToolError({ error: "invalid_state_transition", from: row.state, to: "ready", required_from: CANCELLABLE, hint: "only a project actually in flight has a session to cancel" });

        const open = await c.query(`select * from ops.capability_agent_session where work_request_id=$1 and state not in ('completed','cancelled') for update`, [row.id]);
        const session = open.rows[0];
        if (!session) throw new ToolError({ error: "no_open_capability_session", work_request: row.ref, hint: "this project is in flight with no open session; nothing to cancel" });
        if (args.capability_agent_session_id && args.capability_agent_session_id !== session.id) throw new ToolError({ error: "capability_agent_session_not_for_current_project", session_id: args.capability_agent_session_id, open_session_id: session.id });

        const cancelled = (await c.query(`update ops.capability_agent_session set state='cancelled', cancelled_at=now(), version=version+1 where id=$1 returning *`, [session.id])).rows[0];
        const updated = (await c.query(`update ops.work_request set state='ready', executor_actor=null, claimed_at=null, started_at=null, updated_at=now(), version=version+1 where id=$1 returning *`, [row.id])).rows[0];
        await writeEvent(c, actor, "cancel-capability-session", "ops_work_request", row.id, { field: "state", old: { state: row.state, capability_agent_session_id: session.id, executor_actor: row.executor_actor }, new: { state: "ready", cancelled_capability_agent_session_id: session.id, reason: args.reason }, human_quote: args.human_quote || null, idempotency_key: args.idempotency_key });
        return { ok: true, project: programRow(updated), cancelled_session: { id: cancelled.id, state: cancelled.state, previous_state: session.state, candidate_kind: cancelled.candidate_kind, candidate_fingerprint: cancelled.candidate_fingerprint }, reason: args.reason, advanced: false };
      }),
    },
  };
}
