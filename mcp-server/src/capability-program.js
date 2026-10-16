// One fixed, ordered AI capability program over the canonical ops.work_request
// lifecycle. Project definitions and mutable state live in Postgres. This module
// owns the server-enforced transition and independent-verification boundary.

export const COMPLETION_KINDS = Object.freeze(["built", "extended", "adopted", "declined"]);
export const DEFAULT_PROGRAM = "carr-ai-engineering-suite-v1";

const textPresent = value => typeof value === "string" && value.trim().length > 0;
const uuid = value => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);

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

function permittedCompletion(disposition, kind) {
  if (kind === "declined") return true;
  return ({ build: "built", extend: "extended", adopt: "adopted" })[disposition] === kind;
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

function sessionBrief(row) {
  if (!row) return null;
  const p = programRow(row); const c = p.context;
  return [
    `This card may already have code. completed!=closed is not permission to rebuild. Run ops/built_unclosed.py before writing.`,
    `Build the current CARR AI Engineering Suite project #${p.sequence}: ${p.title}.`,
    `Canonical Work Request: ${p.ref}; base_version: ${p.version}; disposition: ${p.disposition}; state: ${p.state}.`,
    `Outcome: ${p.desired_outcome}`, `Scope: ${c.scope || "Use the canonical Work Request scope."}`,
    `Non-goals: ${(c.non_goals || []).join("; ") || "None recorded."}`,
    `Prerequisites: ${(c.prerequisites || []).join("; ") || "None."}`,
    `First deliverable: ${c.first_deliverable || "Follow the acceptance criteria."}`,
    `Acceptance: ${(p.acceptance_criteria || []).join("; ")}`,
    `Rollback/exit: ${c.rollback_exit || "Stop and report; do not broaden scope."}`,
    `Data/risk: ${c.data_risk || "Unknown — stop before using sensitive data."}; effort: ${c.effort || "unknown"}.`,
    `Existing evidence: ${(c.evidence || []).join("; ") || "none recorded"}.`,
    `Completion meaning: ${c.completion_definition || "accepted evidence and independent verification"}.`,
    p.shape_disposition === "required"
      ? `Implementation shape: required; read the current decision with read-work-shape ${p.ref}.`
      : p.shape_disposition === "not_required"
        ? `Implementation shape: fixed at ${p.shape_fixed_surface_ref}; rationale: ${p.shape_rationale}`
        : "Implementation shape: UNDECIDED. This request cannot be claimed until a qualified seat records required or not_required with set-work-shape-disposition.",
    "Work in an isolated worktree. Write acceptance tests first. Use the cheapest qualified executor. Never merge, deploy, mutate Production, communicate externally, spend, or mark this project complete from the build session. Return a candidate for an independent actor to attest; only a separate human-governed completion can slide the queue.",
  ].join("\n");
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

async function readCurrentSession(c, workRequest) {
  if (!workRequest) return null;
  const r = await c.query(
    `select id, state from ops.capability_agent_session
      where work_request_id=$1 and state not in ('completed','cancelled')`,
    [workRequest.id],
  );
  const session = r.rows[0];
  return session ? { id: session.id, state: session.state } : null;
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
const requireCurrent = requireProject;

async function loadSession(c, ToolError, sessionId, workRequestId) {
  if (!uuid(sessionId)) throw new ToolError({ error: "capability_agent_session_not_found", session_id: sessionId });
  const r = await c.query(`select s.*, a.slug as executor_slug from ops.capability_agent_session s join actor a on a.id=s.executor_actor_id where s.id=$1 for update of s`, [sessionId]);
  const session = r.rows[0];
  if (!session || session.work_request_id !== workRequestId) throw new ToolError({ error: "capability_agent_session_not_for_current_project", session_id: sessionId });
  return session;
}

const completionEvidenceSchema = {
  type: "object", additionalProperties: false,
  properties: {
    // These legacy fields keep invalid old callers legible. The handler rejects
    // them as authoritative evidence and reads the frozen candidate instead.
    artifact_ref: { type: "string" }, acceptance_test_refs: { type: "array", items: { type: "string" } },
    independent_verifier_ref: { type: "string" }, decision_ref: { type: "string" }, rollback_ref: { type: "string" },
    candidate_fingerprint: { type: "string" },
  },
};

export function capabilityProgramTools({ withEnvelope, writeEvent, ToolError }) {
  const projectFields = { idempotency_key: { type: "string" }, program_key: { type: "string", const: DEFAULT_PROGRAM }, sequence: { type: "integer" }, base_version: { type: "integer" }, human_quote: { type: "string" } };
  return {
    "capability-program": {
      write: false,
      description: "Read the one fixed CARR AI Engineering Suite. The first not-confirmed-closed item is current; blocked, failed, needs-Joe and verification rows remain current and can never be skipped. This read grants no build, merge, deploy, Production or external authority.",
      inputSchema: { type: "object", additionalProperties: false, properties: { program_key: { type: "string", const: DEFAULT_PROGRAM }, sequence: { type: "integer" }, include_all: { type: "boolean" }, summary: { type: "boolean", description: "Payload budget: headline numbers, the current item in full, and per-item only {ref, sequence, title, state, disposition, executor_actor}. Absent/false returns today's full rows." } } },
      handler: async (c, _actor, args) => {
        requireFixedProgram(args, ToolError);
        const rows = await c.query(`select * from ops.work_request where program_key=$1 order by program_ordinal`, [DEFAULT_PROGRAM]);
        if (!rows.rows.length) throw new ToolError({ error: "capability_program_not_found", program_key: DEFAULT_PROGRAM });
        // A PROPOSED DECLINE IS NOT THE NEXT THING TO BUILD, and it is not done
        // either. 29 of this program's 51 rows carry disposition 'decline' while
        // sitting in state 'ready' — a contradiction the schema permits. Nothing
        // records that any of those declines was ever DECIDED: shape disposition,
        // decider, decision timestamp, triage classification and triaging actor
        // are null on all 29. The word decline is the plan's proposal, and each
        // row's own completion definition writes it as a condition rather than an
        // outcome: "Declined with owner decision and independent review",
        // "Declined unless Projects 21 and 22 expose a measured runtime
        // bottleneck". So they can be neither built nor closed here.
        //
        // WHAT THAT COST BEFORE THIS. `current` walked program_ordinal and would
        // hand a session a proposed decline as its next build item, and the
        // headline read "1 of 51" while only 22 of those rows were ever
        // buildable. The queue looked five times deeper than it was, which is
        // part of why this program has looked stalled.
        //
        // NOT SKIPPED SILENTLY. They are counted and named separately, and
        // program_complete stays FALSE while any remain — a program that
        // declared itself complete with 29 undecided rows in it would be the
        // same false-green this system keeps finding.
        const isProposedDecline = row =>
          row.disposition === "decline" && row.state !== "confirmed_closed"
          && row.state !== "declined";
        const openRows = rows.rows.filter(row => row.state !== "confirmed_closed");
        const proposedDeclines = openRows.filter(isProposedDecline);
        const current = openRows.find(row => !isProposedDecline(row)) || null;

        // TWO DEFINITIONS OF "CURRENT" HAD QUIETLY COME APART, and this names the
        // gap rather than picking a winner.
        //
        // `current` above deliberately SKIPS proposed declines, for the good
        // reason written just above: a session asking what to build next must not
        // be handed a row nobody has decided. The CLOSE path does not skip. It
        // takes the first row that is not confirmed_closed, in program order, and
        // refuses anything else with out_of_order_project.
        //
        // Those agreed while every decline sat behind the buildable work. They
        // stop agreeing at ordinal 14, which is the first proposed decline, and
        // the queue is at ordinal 2. So after twelve more closes a session reads
        // `current` as the row after the decline, builds it, calls complete on
        // that sequence, and is refused by a verb naming a DIFFERENT sequence it
        // was never shown. The queue jams and it reads as a broken close verb
        // rather than as two answers to one question.
        //
        // The comment above says the declines "can be neither built nor closed
        // here". The first half is still true. The second stopped being true when
        // migration 0281 gave the completion contract a decline branch: a decline
        // with a recorded owner decision and an independent attestation closes
        // now. So the close sequence really does run through them.
        //
        // Both fields ship, because they answer different questions and a reader
        // needs both: what should I build, and what must close next for the
        // program to advance. Naming only one of them is what produced this.
        const nextToClose = openRows[0] || null;
        const closeIsBlockedByDecline = Boolean(
          nextToClose && current && nextToClose.ref !== current.ref);
        const declineCounts = {
          buildable_total: rows.rows.filter(row => !isProposedDecline(row)).length,
          proposed_declines_awaiting_a_decision: proposedDeclines.length,
          proposed_decline_refs: proposedDeclines.map(row => row.ref),
          next_to_close_ref: nextToClose ? nextToClose.ref : null,
          next_to_close_sequence: nextToClose ? Number(nextToClose.program_ordinal) : null,
          close_sequence_blocked_by_proposed_decline: closeIsBlockedByDecline,
          ...(closeIsBlockedByDecline ? { close_sequence_note:
            `the close path takes ${nextToClose.ref} next, a proposed decline; `
            + `${current.ref} is the next BUILDABLE row and completing it will be refused `
            + "as out of order until the decline ahead of it is decided and closed" } : {}),
        };
        // RESOLVE THE SEQUENCE BEFORE EITHER MODE RETURNS. This sat below the
        // summary early-return until now, so `sequence` naming a project that
        // does not exist threw capability_project_not_found on the full path and
        // returned requested:null under summary — two answers to one bad input,
        // from a flag whose whole promise is that it is the same read on a
        // budget. The null was the worse half: summary sets `requested` to the
        // current item when no sequence is passed, and `current` is null once
        // the program completes, so a caller could not tell "no such project"
        // from "program finished". A payload budget may drop fields; it may not
        // change what the verb means.
        const requested = args.sequence === undefined ? current : rows.rows.find(row => Number(row.program_ordinal) === Number(args.sequence));
        if (args.sequence !== undefined && !requested) throw new ToolError({ error: "capability_project_not_found", program_key: DEFAULT_PROGRAM, sequence: args.sequence });
        // PAYLOAD BUDGET. The full rows carry desired_outcome prose, acceptance
        // criteria and completion evidence for every project; a queue scan that
        // only needs where the program stands should not pay for 51 of them.
        // The current item stays in FULL — it is the one a session acts on.
        if (args.summary) {
          const brief = row => row ? {
            ref: row.ref, sequence: Number(row.program_ordinal), title: row.title,
            state: row.state, disposition: row.disposition,
            executor_actor: row.executor_actor || null,
          } : null;
          return {
            program_key: DEFAULT_PROGRAM, total: rows.rows.length,
            completed: rows.rows.filter(row => row.state === "confirmed_closed").length,
            ...declineCounts,
            program_complete: !current && !proposedDeclines.length,
            current: programRow(current),
            requested: brief(requested || null),
            projects: rows.rows.map(brief),
            hint: "summary mode: per-project rows are {ref, sequence, title, state, disposition, executor_actor}; re-read without summary (or with sequence) for any item in full.",
          };
        }
        const capability_session = await readCurrentSession(c, current);
        // The completed count is confirmed_closed attestations only — it is NOT
        // a count of code on disk. A Work Request can have artifacts already
        // merged to main and still sit at state != confirmed_closed because
        // nobody ran prepare/attest/complete. landed_in_repo and built_unclosed
        // are null here because the Worker cannot stat the repo; the local hook
        // (session-brief.py / close-before-open-gate.py) fills them. The hint
        // names the local path so a session does not read 0/51 as "nothing is
        // built" and start a rebuild of work that already landed.
        return {
          program_key: DEFAULT_PROGRAM, total: rows.rows.length,
          completed: rows.rows.filter(row => row.state === "confirmed_closed").length,
          ...declineCounts,
          program_complete: !current && !proposedDeclines.length,
          current: programRow(current),
          requested: programRow(requested), capability_session,
          session_brief: sessionBrief(requested),
          landed_in_repo: null, built_unclosed: [],
          hint: "completed counts confirmed_closed only; code on main is not this number. Run ops/built_unclosed.py / read session-brief CLOSE-BEFORE-BUILD. "
              + "total counts every row; buildable_total excludes proposed declines, which are rows whose disposition is decline and whose decline nobody has recorded as decided. "
              + "They are never offered as current and never counted as done, and program_complete stays false while any remain.",
          projects: args.include_all ? rows.rows.map(programRow) : undefined,
        };
      },
    },
    "complete-capability-project": {
      write: true,
      description: "Close only the current project from its frozen candidate plus a stored independent PASS attestation, then expose exactly the next ordered Work Request. It accepts no caller-supplied artifact, test or verifier references, and an executor cannot self-complete.",
      inputSchema: { type: "object", additionalProperties: false, properties: { ...projectFields, capability_agent_session_id: { type: "string" }, completion_kind: { type: "string", enum: COMPLETION_KINDS }, completion_evidence: completionEvidenceSchema }, required: ["idempotency_key","sequence","base_version","capability_agent_session_id","completion_kind","completion_evidence"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "complete-capability-project", args, async () => {
        requireFixedProgram(args, ToolError);
        const row = await requireCurrent(c, ToolError, args.sequence, args.base_version);
        if (row.state !== "verification") throw new ToolError({ error: "invalid_state_transition", from: row.state, to: "confirmed_closed", required_from: "verification" });
        if (!args.capability_agent_session_id) throw new ToolError({ error: "unbound_completion_evidence", hint: "completion needs the persisted capability_agent_session_id; arbitrary references never close this queue" });
        const supplied = args.completion_evidence || {};
        if (Object.keys(supplied).some(k => k !== "candidate_fingerprint"))
          throw new ToolError({ error: "caller_supplied_completion_evidence_forbidden", hint: "artifact, test, decision and verifier references were frozen during prepare; pass only the persisted candidate fingerprint" });
        const session = await loadSession(c, ToolError, args.capability_agent_session_id, row.id);
        if (session.state !== "verification" || session.candidate_kind !== args.completion_kind || !permittedCompletion(row.disposition, args.completion_kind)) throw new ToolError({ error: "prepared_candidate_does_not_match_completion", candidate_kind: session.candidate_kind, completion_kind: args.completion_kind });
        if (!textPresent(supplied.candidate_fingerprint) || supplied.candidate_fingerprint !== session.candidate_fingerprint)
          throw new ToolError({ error: "completion_candidate_fingerprint_mismatch", hint: "re-read the persisted capability session; completion cannot substitute a candidate" });
        if (actor.id === session.executor_actor_id) throw new ToolError({ error: "capability_self_completion_forbidden", capability_agent_session_id: session.id });
        // NO `for update` HERE, and that omission is the whole reason this queue could
        // never close. ops.capability_verification is immutable by design: migration
        // 0127 puts a before-update-or-delete trigger on it, and carr_writer is granted
        // only `insert, select`. PostgreSQL requires UPDATE privilege to take a row
        // lock, so `select ... for update` against this table raised permission denied
        // on EVERY completion attempt and surfaced to the caller as a bare
        // `internal error`. That is why the AI Engineering Suite read 0 of 51 complete
        // while six of its projects were demonstrably finished — the close verb had
        // never once run to the end.
        //
        // The lock was never needed. requireCurrent() already takes `for update of w`
        // on the queue-head work_request and loadSession() takes `for update of s` on
        // the session, so two concurrent completions of the same project serialize on
        // rows this role may actually lock. Granting UPDATE here instead would widen
        // the write surface of a deliberately append-only evidence table to buy a lock
        // that adds nothing (rule 5409731b).
        //
        // Proven by ops/capability-completion-gate.py, which fires as carr_writer
        // rather than as the owner. Migration 0127's own proof block ran as the
        // migration role, which is exactly why it never saw this.
        const pass = (await c.query(`select * from ops.capability_verification where build_session_id=$1 and work_request_id=$2 and outcome='pass' and candidate_fingerprint=$3 and verifier_actor_id <> $4 order by attested_at desc limit 1`, [session.id, row.id, session.candidate_fingerprint, session.executor_actor_id])).rows[0];
        if (!pass) throw new ToolError({ error: "independent_capability_pass_required", capability_agent_session_id: session.id });
        const persistedEvidence = { candidate: session.candidate_evidence, attestation: { id: pass.id, verifier_actor_id: pass.verifier_actor_id, verification_evidence_ref: pass.verification_evidence_ref, source_ref: pass.source_ref, attested_at: pass.attested_at, candidate_fingerprint: pass.candidate_fingerprint } };
        const updated = (await c.query(`update ops.work_request set state='confirmed_closed', completion_kind=$2, completion_evidence=$3::jsonb, verification_accepted_at=now(), verification_evidence_ref=$4, closed_at=now(), updated_at=now(), version=version+1 where id=$1 returning *`, [row.id, args.completion_kind, JSON.stringify(persistedEvidence), pass.id])).rows[0];
        await c.query(`update ops.capability_agent_session set state='completed', completed_at=now(), version=version+1 where id=$1`, [session.id]);
        await writeEvent(c, actor, "complete-capability-project", "ops_work_request", row.id, { field: "state", old: { state: "verification", capability_agent_session_id: session.id }, new: { state: "confirmed_closed", completion_kind: args.completion_kind, attestation_id: pass.id, candidate_fingerprint: session.candidate_fingerprint }, human_quote: args.human_quote || null, idempotency_key: args.idempotency_key });
        const later = await c.query(`select * from ops.work_request where program_key=$1 and program_ordinal>$2 order by program_ordinal limit 1 for update`, [DEFAULT_PROGRAM, row.program_ordinal]);
        const next = nextProjectState(row.program_ordinal, later.rows.map(r => ({ sequence: Number(r.program_ordinal), state: r.state })));
        const nextRow = later.rows[0] || null;
        // THE HEAD ONLY MOVES WHEN THE HEAD CLOSED. Settling a decline out of
        // order must not announce its neighbour as current — the real head has
        // not moved, and writing that it has would put a false "you are up next"
        // in the timeline of a row nobody has reached.
        if (nextRow && row.__is_program_head !== false) await writeEvent(c, actor, "complete-capability-project", "ops_work_request", nextRow.id, { field: "program_head", old: { current: false }, new: { current: true, predecessor_ref: row.ref }, idempotency_key: args.idempotency_key });
        return { ok: true, completed_project: programRow(updated), program_complete: next.completeProgram, next_project: programRow(nextRow), next_session_brief: sessionBrief(nextRow) };
      }),
    },
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

        // THE SHAPE GATE WOULD REFUSE THIS AT THE DATABASE, so it is checked
        // here where the message can say what to do. ops.work_request_shape_gate
        // rejects any row entering 'ready' without a shape disposition, and it
        // also freezes the shape columns once a row leaves the pre-build states,
        // so this verb cannot set one on the row's behalf.
        if (!row.shape_disposition) throw new ToolError({ error: "work_shape_disposition_required", work_request: row.ref, resolution: "this project carries no shape disposition, so it cannot return to ready; record one with set-work-shape-disposition first" });

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
