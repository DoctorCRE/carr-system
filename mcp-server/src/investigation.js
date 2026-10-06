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
const R03_LINK_SEGMENT = /^[A-Za-z0-9._~-]{1,200}$/;

function deepLinkForSignal(row) {
  if (row.subject_type === "doc_conversation" && R03_LINK_SEGMENT.test(String(row.subject_ref || "")))
    return `/doc-conversations/${row.subject_ref}`;
  return `/signals/${row.id}`;
}

function sameJson(left, right) {
  return JSON.stringify(canonicalJson(left)) === JSON.stringify(canonicalJson(right));
}

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
  const R03_SEVERITY = Object.freeze({ critical: "failure", warning: "action_required" });

  async function mintNotificationGuarded(c, params) {
    await c.query("savepoint carr_r03_mint");
    try {
      const minted = await c.query(
        "select ops.mint_notification($1::text,$2::uuid,$3::text,$4::text," +
        "$5::text,$6::text,$7::text,$8::text,$9::text) as minted",
        [params.event_source, params.event_ref, params.subject_type, params.subject_ref,
         params.reason, params.severity, params.deep_link, params.dedupe_key,
         params.recipient_slug]);
      await c.query("release savepoint carr_r03_mint");
      return minted.rows[0].minted;
    } catch (error) {
      // The signal row is the product; the notification is a courtesy. Roll back
      // ONLY the mint.
      await c.query("rollback to savepoint carr_r03_mint");
      await c.query("release savepoint carr_r03_mint");
      return { ok: true, minted: false, reason_id: "notification_mint_unavailable",
        detail: String(error?.message || error).slice(0, 160) };
    }
  }

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
    "next-signals": {
      description: "Read deterministic analytical signals awaiting investigation. These rows already crossed a numeric threshold in code; this verb never asks an LLM to discover whether a signal exists.",
      inputSchema: { type: "object", properties: {
        status: { type: "string", enum: ["open", "claimed", "resolved", "dismissed"] },
        signal_kind: { type: "string" },
        limit: { type: "integer", minimum: 1, maximum: 100 },
      } },
      handler: async (c, _actor, args) => {
        await require0098(c);
        const limit = Math.min(Math.max(Number(args.limit || 25), 1), 100);
        const r = await c.query(
          `select * from v_signal_queue
            where status=$1 and ($2::text is null or signal_kind=$2)
            order by case severity when 'critical' then 1 when 'warning' then 2 else 3 end,
                     detected_at desc limit $3`,
          [args.status || "open", args.signal_kind || null, limit]);
        return { ok: true, count: r.rows.length, signals: r.rows };
      },
    },

    "record-signal": {
      write: true,
      description: "Record a signal produced by deterministic code. Requires observed and threshold numbers plus evidence refs. Never use an LLM judgment as the detector. producer+signal_key deduplicates scheduled reruns across sessions.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        producer: { type: "string" }, signal_key: { type: "string" },
        signal_kind: { type: "string" }, subject_type: { type: "string" },
        subject_ref: { type: "string" }, metric_name: { type: "string" },
        observed_value: { type: "number" }, baseline_value: { type: ["number", "null"] },
        threshold_value: { type: "number" },
        comparison: { type: "string", enum: ["gt", "gte", "lt", "lte", "delta_abs_gte"] },
        severity: { type: "string", enum: ["info", "warning", "critical"] },
        detected_at: { type: "string" }, evidence_refs: { type: "array", items: { type: "string" }, minItems: 1 },
        payload: { type: "object" }, idempotency_key: { type: "string" },
      }, required: ["producer", "signal_key", "signal_kind", "subject_type", "subject_ref",
        "metric_name", "observed_value", "threshold_value", "comparison", "severity",
        "detected_at", "evidence_refs", "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-signal", args, async () => {
        await require0098(c);
        const invalid = validateSignal(args);
        if (invalid) throw new ToolError(invalid);
        const inserted = await c.query(
          `insert into signal_event
             (producer,signal_key,signal_kind,subject_type,subject_ref,metric_name,
              observed_value,baseline_value,threshold_value,comparison,severity,detected_at,
              evidence_refs,payload,created_by)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
           on conflict (producer,signal_key) do nothing returning *`,
          [args.producer, args.signal_key, args.signal_kind, args.subject_type, args.subject_ref,
           args.metric_name, args.observed_value, args.baseline_value ?? null,
           args.threshold_value, args.comparison, args.severity, args.detected_at,
           JSON.stringify(args.evidence_refs), JSON.stringify(args.payload || {}), actor.id]);
        let row = inserted.rows[0];
        if (!row) {
          const prior = await c.query(
            `select * from signal_event where producer=$1 and signal_key=$2`,
            [args.producer, args.signal_key]);
          const existing = prior.rows[0];
          const same = existing.signal_kind === args.signal_kind &&
            existing.subject_type === args.subject_type && existing.subject_ref === args.subject_ref &&
            existing.metric_name === args.metric_name &&
            Number(existing.observed_value) === args.observed_value &&
            (existing.baseline_value === null ? args.baseline_value == null
              : Number(existing.baseline_value) === args.baseline_value) &&
            Number(existing.threshold_value) === args.threshold_value &&
            existing.comparison === args.comparison && existing.severity === args.severity &&
            new Date(existing.detected_at).toISOString() === new Date(args.detected_at).toISOString() &&
            sameJson(existing.evidence_refs, args.evidence_refs) &&
            sameJson(existing.payload, args.payload || {});
          if (!same) throw new ToolError({ error: "signal_key_reuse",
            producer: args.producer, signal_key: args.signal_key,
            hint: "reuse a signal key only for the identical deterministic observation" });
          return { ok: true, duplicate: true, signal: prior.rows[0] };
        }
        await writeEvent(c, actor, "record-signal", "signal", row.id, {
          field: "threshold_crossing",
          new: { signal_kind: row.signal_kind, metric_name: row.metric_name,
            observed_value: row.observed_value, threshold_value: row.threshold_value,
            evidence_refs: args.evidence_refs },
          cause: "automation_job",
          idempotency_key: args.idempotency_key,
        });
        // THE DEDUPE KEY IS DERIVED HERE, from the STORED row and nothing else.
        // A run reporting progress reuses its signal_key, the verb returns
        // duplicate:true above and never reaches this branch, so a replay mints
        // nothing without any extra logic.
        const dedupeKey = `signal:${row.producer}:${row.signal_key}`;
        const severity = R03_SEVERITY[row.severity];
        // The recipient. personalScopeForActor is already imported at the top of
        // this file and already runs this exact resolution for
        // open-investigation. It is computed from the AUTHENTICATED ACTOR, never
        // from args -- and record-signal's inputSchema is
        // additionalProperties:false, so a caller cannot even name it.
        const scope = personalScopeForActor(actor);
        let notification = { ok: true, minted: false, reason_id: "no_sponsoring_partner" };
        if (!severity) {
          // info mints NOTHING: the notification severity vocabulary has no
          // informational value, which is the second half of R03-NO-PROGRESS-SPAM.
          notification = { ok: true, minted: false, reason_id: "severity_not_notifiable" };
        } else if (scope.status === "personal") {
          notification = await mintNotificationGuarded(c, {
            event_source: "signal_event",
            event_ref: row.id,
            subject_type: row.subject_type,
            subject_ref: row.subject_ref,
            reason: `${row.signal_kind}: ${row.metric_name} crossed ${row.threshold_value}`.slice(0, 500),
            severity,
            deep_link: deepLinkForSignal(row),
            dedupe_key: dedupeKey,
            recipient_slug: scope.sponsor,
          });
        }
        return { ok: true, duplicate: false, signal: row, notification };
      }),
    },

    "open-investigation": {
      write: true,
      description: "Claim one deterministic signal and make the verified human sponsor its single reasoning owner. Codex, Claude, or another runtime may resume the work for that sponsor. A signal can have only one open investigation.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        signal_id: { type: "string" }, objective: { type: "string" },
        max_depth: { type: "integer", minimum: 1, maximum: 6 },
        idempotency_key: { type: "string" },
      }, required: ["signal_id", "objective", "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "open-investigation", args, async () => {
        await require0098(c);
        const signal = await c.query(`select * from signal_event where id=$1 for update`, [args.signal_id]);
        if (!signal.rows.length) throw new ToolError({ error: "signal_not_found", signal_id: args.signal_id });
        if (!["open", "claimed"].includes(signal.rows[0].status))
          throw new ToolError({ error: "signal_not_investigable", status: signal.rows[0].status });
        const existing = await c.query(
          `select id, owner_actor_id from investigation_run where signal_id=$1 and status='open'`,
          [args.signal_id]);
        if (existing.rows.length)
          throw new ToolError({ error: "signal_already_claimed", investigation: existing.rows[0] });
        const ownerId = await reasoningOwnerId(c, actor);
        const created = await c.query(
          `insert into investigation_run (signal_id,objective,owner_actor_id,max_depth)
           values ($1,$2,$3,$4) returning *`,
          [args.signal_id, args.objective, ownerId, args.max_depth || 3]);
        await c.query(`update signal_event set status='claimed' where id=$1`, [args.signal_id]);
        const row = created.rows[0];
        await writeEvent(c, actor, "open-investigation", "investigation", row.id, {
          field: "status", new: { status: "open", signal_id: args.signal_id,
            objective: args.objective, max_depth: row.max_depth },
          cause: actor.human ? "human_stated" : "automation_job",
          idempotency_key: args.idempotency_key,
        });
        return { ok: true, investigation: row };
      }),
    },

    "investigation-neighborhood": {
      description: "List only the active allowlisted hypothesis edges available from one node kind for this investigation's signal type. The caller may not invent an edge or arbitrary SQL test.",
      inputSchema: { type: "object", properties: {
        run_id: { type: "string" }, from_kind: { type: "string" },
      }, required: ["run_id", "from_kind"] },
      handler: async (c, _actor, args) => {
        await require0098(c);
        const run = await c.query(
          `select r.id, r.status, s.signal_kind from investigation_run r
             join signal_event s on s.id=r.signal_id where r.id=$1`, [args.run_id]);
        if (!run.rows.length) throw new ToolError({ error: "investigation_not_found", run_id: args.run_id });
        const routes = await c.query(
          `select route_key,from_kind,relation,to_kind,test_verb,input_contract,minimum_effect
             from diagnostic_route
            where active and from_kind=$1 and (signal_kind is null or signal_kind=$2)
            order by route_key`, [args.from_kind, run.rows[0].signal_kind]);
        return { ok: true, run: run.rows[0], routes: routes.rows };
      },
    },

    "open-investigation-branch": {
      write: true,
      description: "Open one hypothesis branch from an allowlisted diagnostic route. Only the investigation owner may choose branches. Depth is computed by the server and cannot exceed the run budget.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        run_id: { type: "string" }, parent_branch_id: { type: ["string", "null"] },
        route_key: { type: "string" }, hypothesis: { type: "string" },
        test_input: { type: "object" }, idempotency_key: { type: "string" },
      }, required: ["run_id", "route_key", "hypothesis", "test_input", "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "open-investigation-branch", args, async () => {
        await require0098(c);
        const run = await ownedOpenRun(c, actor, args.run_id, true);
        let depth = 1;
        let expectedFrom = run.signal_kind;
        if (args.parent_branch_id) {
          const parent = await c.query(
            `select b.depth,b.status,r.to_kind from investigation_branch b
               join diagnostic_route r on r.route_key=b.route_key
              where b.id=$1 and b.run_id=$2`, [args.parent_branch_id, args.run_id]);
          if (!parent.rows.length) throw new ToolError({ error: "parent_branch_not_found" });
          if (parent.rows[0].status !== "verified")
            throw new ToolError({ error: "parent_branch_not_verified", status: parent.rows[0].status });
          depth = Number(parent.rows[0].depth) + 1;
          expectedFrom = parent.rows[0].to_kind;
        }
        if (depth > Number(run.max_depth))
          throw new ToolError({ error: "investigation_depth_exceeded", depth, max_depth: Number(run.max_depth) });
        const route = await c.query(
          `select * from diagnostic_route where route_key=$1 and active
             and from_kind=$2 and (signal_kind is null or signal_kind=$3)`,
          [args.route_key, expectedFrom, run.signal_kind]);
        if (!route.rows.length)
          throw new ToolError({ error: "route_not_allowed", route_key: args.route_key, from_kind: expectedFrom });
        const created = await c.query(
          `insert into investigation_branch
             (run_id,parent_branch_id,route_key,depth,hypothesis,test_input,opened_by)
           values ($1,$2,$3,$4,$5,$6,$7) returning *`,
          [args.run_id, args.parent_branch_id || null, args.route_key, depth,
           args.hypothesis, JSON.stringify(args.test_input), actor.id]);
        const row = created.rows[0];
        await writeEvent(c, actor, "open-investigation-branch", "investigation", args.run_id, {
          field: "branch", new: { branch_id: row.id, route_key: row.route_key,
            depth: row.depth, hypothesis: row.hypothesis },
          cause: actor.human ? "human_stated" : "automation_job",
          idempotency_key: args.idempotency_key,
        });
        return { ok: true, branch: row, test_verb: route.rows[0].test_verb,
          input_contract: route.rows[0].input_contract };
      }),
    },

    "record-branch-evidence": {
      write: true,
      description: "Attach a worker evidence packet to an open branch. The packet is deliberately limited to scope, exact query/tool, raw facts, evidence refs, uncertainty, exclusions, or an explicit nothing-found result. Recommendations are refused.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        branch_id: { type: "string" }, scope: { type: "string" },
        query_or_tool: { type: "string" }, raw_facts: { type: "array" },
        evidence_refs: { type: "array", items: { type: "string" } },
        uncertainty: { type: ["string", "null"] }, nothing_found: { type: "boolean" },
        exclusions: { type: "array" }, idempotency_key: { type: "string" },
      }, required: ["branch_id", "scope", "query_or_tool", "raw_facts", "evidence_refs", "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-branch-evidence", args, async () => {
        await require0098(c);
        const invalid = validateEvidencePacket(args);
        if (invalid) throw new ToolError(invalid);
        const branch = await c.query(
          `select b.id,b.run_id,b.status,r.status as run_status
             from investigation_branch b join investigation_run r on r.id=b.run_id
            where b.id=$1`, [args.branch_id]);
        if (!branch.rows.length) throw new ToolError({ error: "branch_not_found", branch_id: args.branch_id });
        if (branch.rows[0].status !== "open" || branch.rows[0].run_status !== "open")
          throw new ToolError({ error: "branch_closed", branch_status: branch.rows[0].status,
            run_status: branch.rows[0].run_status });
        const created = await c.query(
          `insert into investigation_evidence
             (branch_id,contributor_id,scope,query_or_tool,raw_facts,evidence_refs,
              uncertainty,nothing_found,exclusions)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9) returning *`,
          [args.branch_id, actor.id, args.scope, args.query_or_tool,
           JSON.stringify(args.raw_facts), JSON.stringify(args.evidence_refs),
           args.uncertainty || null, !!args.nothing_found, JSON.stringify(args.exclusions || [])]);
        await writeEvent(c, actor, "record-branch-evidence", "investigation", branch.rows[0].run_id, {
          field: "evidence", new: { branch_id: args.branch_id, evidence_id: created.rows[0].id,
            nothing_found: !!args.nothing_found, evidence_refs: args.evidence_refs },
          cause: "automation_job",
          idempotency_key: args.idempotency_key,
        });
        return { ok: true, evidence: created.rows[0] };
      }),
    },

    "adjudicate-investigation-branch": {
      write: true,
      description: "Owner-only branch judgment after evidence is present. Marks the hypothesis verified, rejected, pruned, or inconclusive and records why. Workers cannot call this on someone else's investigation.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        branch_id: { type: "string" },
        status: { type: "string", enum: ["verified", "rejected", "pruned", "inconclusive"] },
        adjudication: { type: "string" }, effect_size: { type: ["number", "null"] },
        idempotency_key: { type: "string" },
      }, required: ["branch_id", "status", "adjudication", "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "adjudicate-investigation-branch", args, async () => {
        await require0098(c);
        const branch = await c.query(
          `select b.*,r.owner_actor_id,r.status as run_status,dr.minimum_effect
             from investigation_branch b join investigation_run r on r.id=b.run_id
             join diagnostic_route dr on dr.route_key=b.route_key
            where b.id=$1 for update of b`, [args.branch_id]);
        if (!branch.rows.length) throw new ToolError({ error: "branch_not_found", branch_id: args.branch_id });
        const row = branch.rows[0];
        const ownerId = await reasoningOwnerId(c, actor);
        if (row.owner_actor_id !== ownerId) throw new ToolError({ error: "investigation_owner_only" });
        if (row.status !== "open" || row.run_status !== "open")
          throw new ToolError({ error: "branch_closed", branch_status: row.status, run_status: row.run_status });
        const evidence = await c.query(
          `select count(*)::int as count from investigation_evidence where branch_id=$1`, [args.branch_id]);
        if (!evidence.rows[0].count) throw new ToolError({ error: "branch_evidence_required" });
        if (args.status === "verified" && row.minimum_effect !== null &&
            (args.effect_size === undefined || args.effect_size === null ||
             Number(args.effect_size) < Number(row.minimum_effect)))
          throw new ToolError({ error: "minimum_effect_not_met", minimum_effect: row.minimum_effect,
            effect_size: args.effect_size ?? null,
            hint: "prune or reject this path, or provide the measured effect" });
        const updated = await c.query(
          `update investigation_branch set status=$2,effect_size=$3,adjudication=$4,
             adjudicated_by=$5,adjudicated_at=now() where id=$1 returning *`,
          [args.branch_id, args.status, args.effect_size ?? null, args.adjudication, actor.id]);
        await writeEvent(c, actor, "adjudicate-investigation-branch", "investigation", row.run_id, {
          field: "branch_status", old: { branch_id: row.id, status: "open" },
          new: { branch_id: row.id, status: args.status, effect_size: args.effect_size ?? null,
            adjudication: args.adjudication }, idempotency_key: args.idempotency_key,
          cause: actor.human ? "human_stated" : "automation_job",
        });
        return { ok: true, branch: updated.rows[0] };
      }),
    },

    "get-investigation": {
      description: "Read the complete durable reasoning trace for one investigation: signal, owner, branches, worker evidence packets, adjudications, and termination.",
      inputSchema: { type: "object", properties: { run_id: { type: "string" } }, required: ["run_id"] },
      handler: async (c, _actor, args) => {
        await require0098(c);
        const run = await c.query(`select * from v_investigation where id=$1`, [args.run_id]);
        if (!run.rows.length) throw new ToolError({ error: "investigation_not_found", run_id: args.run_id });
        const branches = await c.query(
          `select b.*,dr.from_kind,dr.relation,dr.to_kind,dr.test_verb,
             coalesce((select jsonb_agg(jsonb_build_object(
               'id',e.id,'contributor',a.slug,'scope',e.scope,'query_or_tool',e.query_or_tool,
               'raw_facts',e.raw_facts,'evidence_refs',e.evidence_refs,'uncertainty',e.uncertainty,
               'nothing_found',e.nothing_found,'exclusions',e.exclusions,'recorded_at',e.recorded_at)
               order by e.recorded_at) from investigation_evidence e
               join actor a on a.id=e.contributor_id where e.branch_id=b.id), '[]'::jsonb) as evidence
             from investigation_branch b join diagnostic_route dr on dr.route_key=b.route_key
            where b.run_id=$1 order by b.depth,b.opened_at`, [args.run_id]);
        return { ok: true, investigation: run.rows[0], branches: branches.rows };
      },
    },

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
