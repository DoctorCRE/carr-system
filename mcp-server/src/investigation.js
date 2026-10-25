// Model-independent investigation control plane (migration 0098).
// Deterministic jobs establish signals; one owner controls hypotheses and
// adjudication; workers can contribute only scoped evidence packets.

import { personalScopeForActor } from "./identity.js";

function canonicalJson(value) {
  if (Array.isArray(value)) return value.map(canonicalJson);
  if (value && typeof value === "object")
    return Object.keys(value).sort().reduce((out, key) => {
      out[key] = canonicalJson(value[key]);
      return out;
    }, {});
  return value;
}

// WR-000113: a notification's deep_link is a RELATIVE PATH ONLY -- never a
// scheme, a host, a query or a token. 0521's column check enforces the shape;
// the permission it implies is re-authorised at follow time by the session
// resolution dealroom-web.js runs on every request.

export function validateSignal(args) {
  const numeric = ["observed_value", "threshold_value"];
  for (const key of numeric) {
    if (typeof args[key] !== "number" || !Number.isFinite(args[key]))
      return { error: "non_numeric_signal", field: key };
  }
  if (args.baseline_value !== undefined && args.baseline_value !== null &&
      (typeof args.baseline_value !== "number" || !Number.isFinite(args.baseline_value)))
    return { error: "non_numeric_signal", field: "baseline_value" };
  if (!Array.isArray(args.evidence_refs) || !args.evidence_refs.length)
    return { error: "signal_evidence_required" };
  const observed = args.observed_value;
  const threshold = args.threshold_value;
  const crossed = args.comparison === "gt" ? observed > threshold
    : args.comparison === "gte" ? observed >= threshold
    : args.comparison === "lt" ? observed < threshold
    : args.comparison === "lte" ? observed <= threshold
    : args.comparison === "delta_abs_gte" && typeof args.baseline_value === "number"
      ? Math.abs(observed - args.baseline_value) >= threshold
      : false;
  if (!crossed) return { error: "threshold_not_crossed", comparison: args.comparison };
  return null;
}

export function validateEvidencePacket(args) {
  const facts = Array.isArray(args.raw_facts) ? args.raw_facts : [];
  const refs = Array.isArray(args.evidence_refs) ? args.evidence_refs : [];
  if (!args.nothing_found && (!facts.length || !refs.length))
    return { error: "evidence_packet_incomplete",
      hint: "return raw_facts and evidence_refs, or set nothing_found=true explicitly" };
  if (Object.prototype.hasOwnProperty.call(args, "recommendation"))
    return { error: "distributed_judgment_refused",
      hint: "workers return scoped facts; the investigation owner adjudicates" };
  return null;
}

export function investigationTools({ withEnvelope, writeEvent, ToolError }) {
  async function require0098(c) {
    const r = await c.query(
      `select to_regclass('public.signal_event') is not null as signals,
              to_regclass('public.investigation_run') is not null as runs,
              to_regclass('public.diagnostic_route') is not null as routes`);
    const s = r.rows[0];
    if (s.signals && s.runs && s.routes) return;
    throw new ToolError({ error: "migration_not_applied",
      migration: "0098_investigation_control_plane", present: s,
      hint: "apply migration 0098 before using investigation verbs; nothing was written" });
  }

  async function reasoningOwnerId(c, actor) {
    const scope = personalScopeForActor(actor);
    if (scope.status !== "personal")
      throw new ToolError({ error: "sponsored_owner_required",
        hint: "an investigation must belong to a verified CARR partner, not a model runtime" });
    const owner = await c.query(`select id from actor where slug=$1 and active`, [scope.sponsor]);
    if (!owner.rows.length)
      throw new ToolError({ error: "investigation_owner_unavailable", owner: scope.sponsor });
    return owner.rows[0].id;
  }

  // WR-000113: the production mint site.
  //
  // THE BOUND. record-signal runs inside ONE transaction the dispatcher owns
  // (mcp.js:707 begin, :730 commit, :733 rollback on any throw), so the mint is
  // inside that transaction and any error inside it aborts the whole thing
  // (25P02) -- a JS try/catch alone does NOT save the signal insert, as
  // tools.js:350-357 says in the repository's own words. So the call is wrapped
  // in a SAVEPOINT, the work-request-intake.js:34-57 idiom: a mint that cannot
  // mint leaves the signal row committed.
  //
  // ONLY the mint's own statement is inside the savepoint. The signal insert and
  // the existing writeEvent call are not; widening it would hide a real failure
  // of the verb's own work.

  async function ownedOpenRun(c, actor, runId, lock = false) {
    const r = await c.query(
      `select r.*, s.signal_kind, s.subject_type, s.subject_ref
         from investigation_run r join signal_event s on s.id=r.signal_id
        where r.id=$1 ${lock ? "for update of r" : ""}`,
      [runId]);
    if (!r.rows.length) throw new ToolError({ error: "investigation_not_found", run_id: runId });
    const run = r.rows[0];
    const ownerId = await reasoningOwnerId(c, actor);
    if (run.owner_actor_id !== ownerId)
      throw new ToolError({ error: "investigation_owner_only", owner_actor_id: run.owner_actor_id });
    if (run.status !== "open")
      throw new ToolError({ error: "investigation_closed", status: run.status });
    return run;
  }

  return {

    "close-investigation": {
      write: true,
      description: "Owner-only termination. Refuses an empty trace or any open branch and requires the strongest rejected alternative, so a conclusion cannot hide unfinished work or confirmation bias.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        run_id: { type: "string" }, status: { type: "string", enum: ["completed", "abandoned"] },
        conclusion: { type: "string" }, confidence: { type: "number", minimum: 0, maximum: 1 },
        strongest_alternative: { type: "string" }, alternative_disposition: { type: "string" },
        termination_reason: { type: "string", enum: ["root_cause_found", "budget_exhausted",
          "insufficient_evidence", "signal_invalid", "superseded"] },
        idempotency_key: { type: "string" },
      }, required: ["run_id", "status", "conclusion", "confidence", "strongest_alternative",
        "alternative_disposition", "termination_reason", "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "close-investigation", args, async () => {
        await require0098(c);
        const run = await ownedOpenRun(c, actor, args.run_id, true);
        const branches = await c.query(
          `select count(*)::int as total,count(*) filter (where status='open')::int as open
             from investigation_branch where run_id=$1`, [args.run_id]);
        if (!branches.rows[0].total) throw new ToolError({ error: "investigation_trace_empty" });
        if (branches.rows[0].open)
          throw new ToolError({ error: "investigation_has_open_branches", count: branches.rows[0].open });
        const updated = await c.query(
          `update investigation_run set status=$2,conclusion=$3,confidence=$4,
             strongest_alternative=$5,alternative_disposition=$6,termination_reason=$7,closed_at=now()
           where id=$1 returning *`,
          [args.run_id, args.status, args.conclusion, args.confidence,
           args.strongest_alternative, args.alternative_disposition, args.termination_reason]);
        await c.query(`update signal_event set status=$2 where id=$1`,
          [run.signal_id, args.termination_reason === "signal_invalid" ? "dismissed" : "resolved"]);
        await writeEvent(c, actor, "close-investigation", "investigation", args.run_id, {
          field: "status", old: { status: "open" },
          new: { status: args.status, conclusion: args.conclusion, confidence: args.confidence,
            termination_reason: args.termination_reason,
            strongest_alternative: args.strongest_alternative,
            alternative_disposition: args.alternative_disposition },
          cause: actor.human ? "human_stated" : "automation_job",
          idempotency_key: args.idempotency_key,
        });
        return { ok: true, investigation: updated.rows[0] };
      }),
    },
  };
}
