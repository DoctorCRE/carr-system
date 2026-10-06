// DoctorCRE v5 slice V5-A05 -- the production door onto
// delivery-cadence-a05.v5.js's pure classifiers (migration 0617, sealed as
// SCAC v75 by migration 0618).
//
// Three verbs:
//   cadence-status              read-only, on the WRITER connection in a
//                                `begin read only` transaction
//                                (writerConnection: true). ops.v5_a05_cadence_status
//                                is granted to carr_writer/carr_authority only and
//                                raises 42501 for anyone else, so the stateless
//                                carr_reader route mcp.js gives an undeclared read
//                                could never execute it -- which is exactly how
//                                the 07:00 sweep failed every run before PR #1236's
//                                round-2 review (item 1). Same precedent as
//                                notification-feed and read-session-identity.
//   record-cadence-receipt      write. The one door onto
//                                ops.v5_a05_cadence_receipt, open only to the
//                                system/authority seats (see a05SeatForActor).
//                                Performs no escalation itself.
//   raise-delivery-cadence-alert  write. THE call site that invokes the pure
//                                classifier. Urgency and authority-need are
//                                DERIVED here on the server (deriveEscalationFacts)
//                                from the reason id plus facts this handler read:
//                                the raising seat, the ops.incident row an urgent
//                                reason cites, the server-clock cadence status a
//                                miss cites. Caller booleans are ignored. It writes
//                                a signal_event (the WR-000113 evidence mechanism)
//                                and mints through ops.mint_notification (0617
//                                extends it with p_bypass_quiet_hours and
//                                p_hold_for_morning; it does not duplicate it).
//
// The scheduled sweep (ops/delivery-cadence-a05-sweep.py, no seal owed -- decision
// 05e144eb) runs as the local machine credential (the "system" seat) and is the
// caller that turns a "missed" cadence-status read into a
// raise-delivery-cadence-alert call for "replan on miss". An urgent
// security/data-loss/outward-harm alert is raised by a partner or the system seat
// citing an open production SEV-0/SEV-1 incident.

import { authorizationClassForActor, isKnownPartner, personalScopeForActor } from "./identity.js";
import {
  V5_A05_ORDINARY_REASON_IDS, V5_A05_URGENT_REASON_IDS,
  classifyEscalationReason, deriveEscalationFacts, evaluateEscalationRouting,
} from "./delivery-cadence-a05.v5.js";

const SUBJECT_TYPE = /^[a-z][a-z0-9_]{0,63}$/;
const SUBJECT_REF = /^[a-z0-9][a-z0-9._-]{0,127}$/i;
const INCIDENT_REF = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

// The partner-sponsored local machine credentials (identity.js LOCAL_SPONSOR):
// what `./run.sh call` -- and so the daily sweep and every ops job -- presents.
const SYSTEM_SEAT_SLUGS = Object.freeze(["joe-local", "dell-local"]);

// Which V5-A05 seat an authenticated actor holds. Derived only from fields the
// server's own connection authenticator wrote (identity.js), never from an
// argument: a caller cannot name its seat.
//   authority -- a verified human partner (Joe or Dell) on their own session.
//   system    -- the partner-sponsored local machine credential, arriving
//                through the local-token door with a verified native sponsor.
//   other     -- everything else: model agents (even partner-sponsored ones),
//                Hermes, reviewers, probes, unsponsored agents.
export function a05SeatForActor(actor) {
  if (actor?.human === true && isKnownPartner(actor.slug)) return "authority";
  if (actor?.human === false && SYSTEM_SEAT_SLUGS.includes(actor.slug) &&
      actor.via === "local-token" && actor.native_agent_verified === true &&
      isKnownPartner(actor.sponsoring_human_slug) &&
      authorizationClassForActor(actor) === "sponsored_agent")
    return "system";
  return "other";
}

// R03_SEVERITY-equivalent mapping for V5-A05: the escalation router's
// severity/routing maps onto the SAME closed signal_event severity vocabulary
// (info|warning|critical) record-signal already uses, and mint_notification's
// OWN existing map (critical->failure, warning->action_required, info->no
// notification) is reused unchanged -- see investigation.js R03_SEVERITY.
function signalSeverityFor(routing) {
  if (routing.routing === "deliver_immediately") return "critical";
  if (routing.routing === "batch_for_morning") return "warning";
  return "info"; // no_queue_entry: evidence only, never notified.
}
const MINT_SEVERITY = Object.freeze({ critical: "failure", warning: "action_required" });

const MINT_SIGNATURE = "ops.mint_notification(text,uuid,text,text,text,text,text,text,text,boolean,boolean)";

export function deliveryCadenceA05Tools({ withEnvelope, writeEvent, ToolError }) {
  async function require0617(c) {
    const r = await c.query(
      `select to_regprocedure('ops.v5_a05_cadence_status(text,text)') is not null as status_fn,
              to_regprocedure('ops.v5_a05_record_cadence_receipt(text,text,uuid)') is not null as record_fn,
              to_regprocedure('${MINT_SIGNATURE}') is not null as mint_fn`);
    const s = r.rows[0];
    if (s.status_fn && s.record_fn && s.mint_fn) return;
    throw new ToolError({ error: "migration_not_applied",
      migration: "0617_delivery_cadence_a05", present: s,
      hint: "apply migration 0617 before using V5-A05 verbs; nothing was written" });
  }

  function assertSubject(args) {
    if (typeof args.subject_type !== "string" || !SUBJECT_TYPE.test(args.subject_type))
      throw new ToolError({ error: "invalid_subject_type", subject_type: args.subject_type });
    if (typeof args.subject_ref !== "string" || !SUBJECT_REF.test(args.subject_ref))
      throw new ToolError({ error: "invalid_subject_ref", subject_ref: args.subject_ref });
  }

  async function cadenceStatus(c, args) {
    const r = await c.query("select ops.v5_a05_cadence_status($1::text,$2::text) as status",
      [args.subject_type, args.subject_ref]);
    return r.rows[0].status;
  }

  async function incidentFacts(c, incidentRef) {
    if (incidentRef === undefined || incidentRef === null) return null;
    if (typeof incidentRef !== "string" || !INCIDENT_REF.test(incidentRef))
      throw new ToolError({ error: "invalid_incident_ref", incident_ref: incidentRef });
    const r = await c.query(
      `select ref, state, severity, environment, duplicate_of_id::text as duplicate_of_id
         from ops.incident where ref = $1`, [incidentRef]);
    if (!r.rows.length)
      throw new ToolError({ error: "incident_not_found", incident_ref: incidentRef,
        hint: "an urgent V5-A05 alert must cite an incident the server can read; open it with open-incident first" });
    const row = r.rows[0];
    return { ref: row.ref, state: row.state, severity: row.severity,
      environment: row.environment, duplicate_of_id: row.duplicate_of_id ?? null };
  }

  async function mintNotificationGuarded(c, params) {
    await c.query("savepoint carr_v5_a05_mint");
    try {
      const minted = await c.query(
        "select ops.mint_notification($1::text,$2::uuid,$3::text,$4::text," +
        "$5::text,$6::text,$7::text,$8::text,$9::text,$10::boolean,$11::boolean) as minted",
        [params.event_source, params.event_ref, params.subject_type, params.subject_ref,
         params.reason, params.severity, params.deep_link, params.dedupe_key,
         params.recipient_slug, params.bypass_quiet_hours === true,
         params.hold_for_morning === true]);
      await c.query("release savepoint carr_v5_a05_mint");
      return minted.rows[0].minted;
    } catch (error) {
      await c.query("rollback to savepoint carr_v5_a05_mint");
      await c.query("release savepoint carr_v5_a05_mint");
      return { ok: true, minted: false, reason_id: "notification_mint_unavailable",
        detail: String(error?.message || error).slice(0, 160) };
    }
  }

  return {
    "cadence-status": {
      write: false,
      // Review round 2, item 1: without this the verb routes to carr_reader,
      // which 0617 denies EXECUTE on ops.v5_a05_cadence_status -- the daily
      // sweep's only read could never succeed in production.
      writerConnection: true,
      description: "Read-only V5-A05 cadence status for one subject: current, missed, or no_receipt_on_record, mirroring evaluateCadenceReceipt over ops.v5_a05_cadence_receipt. Runs in a read-only transaction on the writer connection. Takes no `now` argument -- the server clock is the only clock this reads (excluded_scope: clock reset).",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        subject_type: { type: "string" }, subject_ref: { type: "string" },
      }, required: ["subject_type", "subject_ref"] },
      handler: async (c, _actor, args) => {
        await require0617(c);
        assertSubject(args);
        return cadenceStatus(c, args);
      },
    },

    // Review round 2, item 4: a receipt is a bare check-in today (no
    // Completion Register producer exists to cite -- migration 0617's header),
    // and a bare check-in resets the 14-day clock. The door is therefore held
    // to the same system/authority seats that may raise urgent alerts: a model
    // agent, bot or reviewer cannot silence a miss by checking in.
    "record-cadence-receipt": {
      write: true,
      description: "Record that a subject's V5-A05 assurance cadence checked in. Only a verified partner or the partner-sponsored local machine credential may record one; any other seat is refused. Inserts one append-only row into ops.v5_a05_cadence_receipt, expiring 14 days from now. Performs no escalation -- raise-delivery-cadence-alert and the daily sweep own that. Evidence is computed server-side by ops.v5_a05_record_cadence_receipt itself (no caller-supplied evidence can reset the clock). Disclosed gap (migration 0617's header comment): no Completion Register producer exists yet for any V5-A05 subject, so a receipt is a seat-restricted bare check-in and its evidence records that gap rather than a fabricated outcome-row foreign key.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        subject_type: { type: "string" }, subject_ref: { type: "string" },
      }, required: ["idempotency_key", "subject_type", "subject_ref"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-cadence-receipt", args, async () => {
        await require0617(c);
        assertSubject(args);
        const seat = a05SeatForActor(actor);
        if (seat === "other")
          throw new ToolError({ error: "cadence_receipt_requires_system_or_authority_seat", seat,
            hint: "a cadence receipt resets the 14-day miss clock; only a partner or the local machine credential may record one" });
        const r = await c.query(
          "select ops.v5_a05_record_cadence_receipt($1::text,$2::text,$3::uuid) as receipt",
          [args.subject_type, args.subject_ref, args.idempotency_key]);
        const receipt = r.rows[0].receipt;
        if (receipt.deduplicated !== true) {
          await writeEvent(c, actor, "record-cadence-receipt", "v5_a05_cadence_receipt", receipt.receipt_id, {
            field: "issued", new: { subject_type: args.subject_type, subject_ref: args.subject_ref,
              expires_at: receipt.expires_at, replan_of: receipt.replan_of, seat },
            cause: actor.human ? "human_stated" : "automation_job",
            idempotency_key: args.idempotency_key,
          });
        }
        return { ok: true, ...receipt };
      }),
    },

    // Review finding 8 (Opus adversarial review of PR #1236, round 1): a miss
    // must leave a durable miss record, not only notify. The signal_event
    // insert below is that durable record -- it is written unconditionally
    // once the raise is accepted, before the notify branch runs, upserted
    // idempotently (on conflict do nothing) rather than ever overwritten or
    // deleted, and its signal_kind carries the exact reason_id, so the miss
    // survives regardless of whether the notification mints, dedupes, or
    // fails. Disclosed gap: the finding also asked for the miss to "degrade
    // rollout state." No rollout-state, release-health, or deployment-health
    // concept exists anywhere in this repository today for a V5 delivery-
    // program miss to degrade -- this PR does not invent one.
    "raise-delivery-cadence-alert": {
      write: true,
      description: "Raise a V5-A05 escalation candidate. The server, not the caller, decides urgency and whether Joe's authority is needed, from reason_id plus facts it reads itself. Urgent reasons (security_incident, data_loss, outward_harm) are accepted only from a verified partner or the partner-sponsored local machine credential AND only when incident_ref names an open, non-duplicate production incident at SEV-0 or SEV-1; they deliver immediately and bypass quiet hours. cadence_miss_replan_required is accepted only when the server-clock cadence status for the subject reads missed; it and decision_required need Joe's authority and are held for the morning window (never pushed outside it). delivery_blocker and review_blocker are recorded as evidence only and never notify. requires_joe_authority and unresolved_intent are accepted for compatibility but IGNORED -- no caller-supplied field can raise routing. A refused raise writes nothing. An accepted raise always writes a durable signal_event row before any notification branch runs.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        reason_id: { type: "string", enum: [...V5_A05_URGENT_REASON_IDS, ...V5_A05_ORDINARY_REASON_IDS] },
        subject_type: { type: "string" }, subject_ref: { type: "string" },
        incident_ref: { type: "string", description: "the ops.incident ref an urgent reason cites; required for security_incident, data_loss and outward_harm" },
        requires_joe_authority: { type: "boolean", description: "ignored: the server derives authority-need" },
        unresolved_intent: { type: "boolean", description: "ignored: the server derives unresolved intent" },
        detail: { type: "string" },
      }, required: ["idempotency_key", "reason_id", "subject_type", "subject_ref"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "raise-delivery-cadence-alert", args, async () => {
        await require0617(c);
        assertSubject(args);

        // Contract check first, so an unknown reason fails as a contract
        // violation before any fact is read.
        const urgency = classifyEscalationReason(args.reason_id);
        const seat = a05SeatForActor(actor);
        const incident = urgency === "urgent" ? await incidentFacts(c, args.incident_ref) : null;
        const cadence = args.reason_id === "cadence_miss_replan_required"
          ? await cadenceStatus(c, args) : null;
        const facts = deriveEscalationFacts({
          reason_id: args.reason_id, seat, incident,
          cadence_status: cadence ? cadence.status : null,
        });
        if (!facts.ok) {
          const { schema_version: _s, policy_version: _p, effects: _e, ok: _ok, refusal_id, ...detail } = facts;
          throw new ToolError({ error: refusal_id, ...detail,
            hint: "V5-A05 derives urgency and authority-need on the server; nothing was written" });
        }
        const ignored = ["requires_joe_authority", "unresolved_intent"].filter(key => key in args);

        // THE ACTUAL PRODUCTION CALL SITE for the pure V5-A05 classifier, fed
        // ONLY the server derivation above.
        const routing = evaluateEscalationRouting({
          reason_id: args.reason_id,
          requires_joe_authority: facts.requires_joe_authority,
          unresolved_intent: facts.unresolved_intent,
          quiet_now: false, // this verb does not read quiet-hours itself; mint_notification does, from the preference row, at mint time.
        });
        const severity = signalSeverityFor(routing);
        const signalKey = `v5-a05:${args.reason_id}:${args.subject_type}:${args.subject_ref}:${args.idempotency_key}`;

        const inserted = await c.query(
          `insert into signal_event
             (producer,signal_key,signal_kind,subject_type,subject_ref,metric_name,
              observed_value,baseline_value,threshold_value,comparison,severity,detected_at,
              evidence_refs,payload,created_by)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
           on conflict (producer,signal_key) do nothing returning *`,
          ["v5-a05-delivery-cadence", signalKey, args.reason_id, args.subject_type, args.subject_ref,
           "urgent_harm_or_authority_occurrence", 1, null, 1, "gte", severity, new Date().toISOString(),
           JSON.stringify([`v5-a05:reason:${args.reason_id}`,
             ...(incident ? [`ops.incident:${incident.ref}`] : [])]),
           JSON.stringify({ routing: routing.routing, wakes_joe: routing.wakes_joe,
             requires_joe_authority: routing.requires_joe_authority,
             unresolved_intent: routing.unresolved_intent, seat,
             verified_by: facts.verified_by, detail: args.detail || null }),
           actor.id]);
        let row = inserted.rows[0];
        let duplicate = false;
        if (!row) {
          duplicate = true;
          row = (await c.query(`select * from signal_event where producer=$1 and signal_key=$2`,
            ["v5-a05-delivery-cadence", signalKey])).rows[0];
        } else {
          await writeEvent(c, actor, "raise-delivery-cadence-alert", "signal", row.id, {
            field: "threshold_crossing",
            new: { signal_kind: row.signal_kind, reason_id: args.reason_id, routing: routing.routing, seat },
            cause: actor.human ? "human_stated" : "automation_job",
            idempotency_key: args.idempotency_key,
          });
        }

        const dedupeKey = `signal:${row.producer}:${row.signal_key}`;
        const mintSeverity = MINT_SEVERITY[row.severity];
        const scope = personalScopeForActor(actor);
        let notification = { ok: true, minted: false, reason_id: "no_sponsoring_partner" };
        if (duplicate) {
          notification = { ok: true, minted: false, reason_id: "duplicate_alert" };
        } else if (!mintSeverity) {
          notification = { ok: true, minted: false, reason_id: "severity_not_notifiable" };
        } else if (scope.status === "personal") {
          notification = await mintNotificationGuarded(c, {
            event_source: "signal_event", event_ref: row.id,
            subject_type: row.subject_type, subject_ref: row.subject_ref,
            reason: `V5-A05 ${args.reason_id}: ${routing.routing}`.slice(0, 500),
            severity: mintSeverity, deep_link: `/signals/${row.id}`, dedupe_key: dedupeKey,
            recipient_slug: scope.sponsor,
            bypass_quiet_hours: routing.bypasses_quiet_hours === true,
            hold_for_morning: routing.batched === true,
          });
        }

        return { ok: true, duplicate, signal: row, routing, derivation: facts, seat,
          ignored_caller_assertions: ignored, notification };
      }),
    },
  };
}
