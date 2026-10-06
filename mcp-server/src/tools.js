import { invoiceTrackerTools } from "./invoice-tracker.js";
import { isCalendarDate } from "./calendar-date.js";
import { bindReferralDeal } from "./relationship-network.js";
import { trustedOverride, dealEvidenceEntries, requireRelationshipPartner, mergeRelationshipFields } from "./vendor-relationship.js";
import { readLeadWorkspace, LEAD_WORKSPACE_SCHEMA, validateStageReview, lockLeadLifecycle } from "./lead-workspace.js";
// CARR MCP tool registry — Wave 1 verbs (tool-contracts-2026-07-30.md §2).
// Every write runs the envelope: idempotency replay via tool_call, actor from
// the verified token (never the payload), base_version conflicts ask and never
// auto-retry, every accepted write lands its event row, plausibility bands
// confirm instead of block. NO SEND VERB EXISTS.
// Descriptions are poka-yoke docstrings (contracts §5): what, when, edge cases.
// The doctrine store's verbs (P2, decision 82a2fb62) live in doctrine.js as a
// factory over this file's envelope machinery, merged at the bottom.
import { doctrineTools } from "./doctrine.js";
import { situationRetrievalTools } from "./situation-retrieval.js";
import { investigationTools } from "./investigation.js";
import { docConversationTools } from "./doc-conversation.js";
import { docSuggestionTools } from "./doc-suggestions.js";
import { docActivityTools } from "./doc-activity.js";
import { whatsNewTools } from "./whats-new.js";
import { MEETING_MODE_WRITE_VERBS, meetingModeTools } from "./meeting-mode.js";
import { notificationTools } from "./notifications.js";
import { deliveryCadenceA05Tools } from "./delivery-cadence-a05-tools.js";
import { sessionIdentityTools } from "./session-identity.js";
import { dispatchSpineTools } from "./dispatch-spine.js";
import { capabilityProgramTools } from "./capability-program.js";
import { workShapeTools } from "./work-shape.js";
import { workRequestIntakeTools } from "./work-request-intake.js";
import { workPortfolioTools } from "./work-portfolio.js";
import { leaseTermComparisonTools } from "./lease-term-comparison.js";
import { partnerRoomTools } from "./partner-room.js";
import { agentProfileTools } from "./agent-profiles.js";
import { botBriefTools } from "./bot-brief.js";
import { memoryTools } from "./memory.js";
import { codexContinuityTools } from "./codex-continuity.js";
import { claudeContinuityTools } from "./claude-continuity.js";
import { incidentTools } from "./incident.js";
import { evidenceActivationTools } from "./evidence-activation.js";
import { resourceObservationTools } from "./resource-observation.v5.js";
import { invoiceAutomation } from "./invoice-automation.js";
import { leadAutomationTools } from "./lead-automation.js";
import { jevCallReceiptTools } from "./jev-call-receipt.js";
import { workflowCutoverTools } from "./workflow-cutover.v5.js";
import { engineeringRuntimeTools } from "./engineering-runtime.js";
import { tourRightsProjectionTools } from "./tour-rights-projection.js";
import { tourPropertyJurisdictionTools } from "./tour-property-jurisdiction.js";
import { tourDomainTools } from "./tour-domain.js";
import { tourPropertySearchTools } from "./tour-property-search.js";
import { tourSharingTools } from "./tour-sharing.js";
import { tourMapPromotionTools } from "./tour-map-promotion.js";
import { actionClassSuccessorRegistryTools } from "./action-class-successor-registry.v5.js";
import { tourArtifactTools } from "./tour-artifacts.js";
import { DEAL_ROOM_FIELDS, assertDealRoomField, stripDealPlaceholders } from "./dealroom.js";
import { authenticatedIdentity, authorizationClassForActor, organizationTenantForActor,
         permittedActionOwnerSlugs, personalScopeForActor } from "./identity.js";
import { canExercisePartnerAuthority, partnerAuthoritySlugForActor } from "./partner-authority.js";
import { assertRegisteredOperation, mutationManifestIdentity, MutationRegistryRefusal } from "./mutation-registry.js";
import { assertGateZeroReceipt, deriveGateZeroProducerSeat, gateZeroOracleSeatLane,
         gateZeroOutcomeCandidateDigest, gateZeroOutcomeDigest,
         GATE_ZERO_RECEIPT_SCHEMA } from "./gate-zero-outcome-store.v5.js";
import { GATE_ZERO_WRITER_SECRET_NAME } from "./gate-zero-seat-connection.v5.js";
import {
  assertFoundationAssuranceOracleSeat,
  foundationAssuranceOracleLane,
} from "./foundation-assurance-minimum-registration.v5.js";
// THE RECEIPT'S ONE SOURCE. The gate's producer seam is bound to Step A's
// zero-argument producer, so this is how a receipt enters the write path: by
// being produced, inside this call, from rows three ruled readers took from
// three ruled stores. There is no other import that could supply one and no
// argument that could carry one.
import { emitGateZeroOutcome } from "./gate-zero-assurance.v5.js";
import { readRuleEnforcementCoverage } from "./lifecycle-assurance.v5.js";
import { benchmarkAcceptanceStoreTools } from "./benchmark-acceptance-store.v5.js";
import { modelRoleStoreTools } from "./model-role-store.v5.js";
import { recordSourceAuthorityStoreTools } from "./record-source-authority-store.v5.js";
// V5-J102's healthcare CRE lifecycle door: every verb on the writer connection
// through mcp.js's setWriterActorContext, which sets the actor and the
// server-verified sponsor the store and SQL writers derive attribution from.
import { creLifecycleStoreTools } from "./cre-lifecycle-store.v5.js";
import { salesforceReconciliationStoreTools } from
  "./salesforce-reconciliation-store-rw02.v5.js";
// V5-RW02 safe stops: the server-side run-outcome ledger behind the
// 5-consecutive-clean counter, and the humanOnly consent revocation.
import { salesforceReadRunStoreTools } from "./salesforce-read-run-store-rw02.v5.js";
import { foundationAssuranceMinimumTools } from
  "./foundation-assurance-minimum-producer.v5.js";
// V5-S01's live door: the settled global boundaries evaluated at the dispatch
// seam below (shadow until Joe approves enforcement), and their read projection.
import { V5BoundaryDoorRefusal, globalBoundariesDoorTools, passBoundaryDoor } from
  "./global-boundaries-door.v5.js";
import { journeyOneClockDoorTools } from "./journey-one-clock-door.v5.js";
import { governedCorrespondenceStoreTools } from "./governed-correspondence-store.v5.js";
import { assuranceHealthStoreTools } from "./assurance-health-store.v5.js";
import { completeSetReviewA03StoreTools } from "./independent-review-cycle-store.v5.js";
import { ruleContextRuntimeTools } from "./rule-context-runtime.v5.js";
import { systemWorkTools } from "./system-work-census.v5.js";
import { BOARD_ANSWER_WRITE_VERBS, boardAnswerTools } from "./board-answers.js";
import { scheduleBoardTools } from "./schedule-board.js";
export { canExercisePartnerAuthority, partnerAuthoritySlugForActor };

// ---------- envelope helpers ----------

// Defined in a leaf module so modules tools.js depends on can throw typed,
// self-naming failures too; re-exported here so existing imports keep working.
import { ToolError } from "./tool-error.js";
export { ToolError };
import { parksADecision, classifyLoopText, needsDecider, loopRowText,
         ESCALATION_REASON, BLOCKER_DECIDER_REASON } from "./verb-gate-checks.js";

// DEFECT 2, HALF (b) (found 2026-08-13, decision 7026246b): a write whose bad
// input reaches the database raw (an enum this file never learned to validate,
// a foreign key nobody checked first) throws a driver error, not a ToolError —
// and mcp.js's top-level catch used to flatten EVERY non-ToolError into a bare
// {"error":"internal error"} with no field, no constraint, no allowed values.
// Half (a) closes the specific gap (marker, domain, above); this is the
// backstop for every OTHER enum/FK this file has not yet learned to check —
// Postgres SQLSTATE class 23 (integrity_constraint_violation) always names the
// constraint and usually the column, so that much can be surfaced honestly
// without ever touching the connection string (which lives in env bindings,
// never in a query or its error — nothing here reads or forwards env).
// Deliberately narrow: anything outside class 23 (a real connection or driver
// fault) returns null and falls through to the generic handler unchanged,
// because this is a translator for bad input, not a catch-all.
const PG_VIOLATION_KIND = Object.freeze({
  "23514": "check_violation",       // e.g. marker/kind fails its CHECK
  "23503": "foreign_key_violation", // e.g. domain names no row in loop_domain
  "23505": "unique_violation",
  "23502": "not_null_violation",
});
const CONNSTR_RE = /\b\w+:\/\/[^\s'"]+/gi; // defense in depth; pg errors don't carry one today
function redact(s) {
  return typeof s === "string" ? s.replace(CONNSTR_RE, "[redacted]") : s;
}

// THE TWO CLASSES THAT COST THE MOST ON 2026-08-21, neither of which is a
// constraint violation, so neither was translated and both reached the caller
// as a bare "internal error" naming nothing:
//
//   42501 insufficient_privilege — complete-capability-project took a row lock
//   on an append-only table the serving role holds only insert/select on. Row
//   locks need UPDATE. Every completion since the verb shipped died here, which
//   is why the AI Engineering Suite read 0 complete of 51 while six of its
//   projects were finished. Hours went into it because the error named nothing.
//
//   22P02 invalid_text_representation — a short id or a "#213"-style reference
//   reaching a uuid column. Hit twice in one session, on close-loop's successor
//   field and on update-decision, both of which document the human-readable
//   form in their own hints. Two defects filed under one class.
//
// A permission or a type mismatch is not less actionable than a CHECK
// violation, and it is far more likely to be a bug in the verb rather than in
// the caller's arguments — which is exactly why hiding it is expensive.
const PG_FAULT_KIND = Object.freeze({
  "42501": "insufficient_privilege",
  "22P02": "invalid_text_representation",
  "42703": "undefined_column",
  "42P01": "undefined_table",
  "42883": "undefined_function",
});
const PG_FAULT_HINT = Object.freeze({
  "42501": "the database refused this to the role the server connects as — the grant is missing, " +
           "or the statement takes a row lock (select ... for update) on a table this role may only " +
           "read. Read the handler's SQL before assuming a server fault; do not widen the grant " +
           "without checking whether the lock is needed at all.",
  "22P02": "a value did not parse as its column's type — most often a short id, a '#123' reference " +
           "or a name where a uuid is required. Check which form this field actually accepts; the " +
           "verb's own description may name a friendlier form than the code accepts.",
  "42703": "the statement names a column that does not exist — a schema change and this code have drifted.",
  "42P01": "the statement names a table that does not exist — a migration is missing on this database.",
  "42883": "the statement calls a function that does not exist — a migration is missing on this database.",
});

// A CHECK THAT SPANS MORE THAN ONE COLUMN REPORTS `column: null`, and 219 of
// this database's constraints do. Postgres is not being unhelpful -- it cannot
// name one column for a rule about several -- but the caller is then told that
// a rule broke without being told which of their inputs broke it, which is the
// difference between a refusal they can act on and a dead end. Measured
// 2026-09-18: 219 multi-column constraints, of which 86 leave the field
// completely unidentifiable and 212 leave the required fix unknowable.
//
// The catalog knows the answer. pg_constraint.conkey holds exactly the columns
// the rule is about, and pg_get_constraintdef prints the rule itself, so one
// lookup keyed on the constraint name the error already carries turns
// `column: null` into the list of fields involved plus the condition they must
// satisfy. That is a read of the system catalogs only -- no row of anyone's
// data is touched -- and it runs on the connection the failed statement was
// already using, after its rollback, so it costs no new connection.
const CONSTRAINT_COLUMNS_SQL = `
  select t.relname as table_name,
         pg_get_constraintdef(c.oid) as definition,
         coalesce(array_agg(a.attname order by a.attname)
                    filter (where a.attname is not null), '{}') as columns
    from pg_constraint c
    join pg_class t on t.oid = c.conrelid
    left join pg_attribute a
      on a.attrelid = c.conrelid and a.attnum = any(c.conkey) and not a.attisdropped
   where c.conname = $1
   group by t.relname, c.oid
   limit 1`;

/** Add the columns and the rule text to a constraint refusal, when we can.
 *
 * Deliberately best-effort: the refusal is already correct and already useful
 * without this, so a catalog lookup that fails must NOT replace a precise
 * refusal with a database error about the lookup. Any failure returns the
 * refusal untouched.
 */
export async function describeConstraint(client, refusal) {
  const name = refusal?.payload?.constraint;
  if (!client || typeof name !== "string" || !name) return refusal;
  let row;
  try {
    const out = await client.query(CONSTRAINT_COLUMNS_SQL, [name]);
    row = out?.rows?.[0];
  } catch {
    return refusal;   // see the doc comment: never trade a good refusal for this
  }
  if (!row) return refusal;
  const columns = Array.isArray(row.columns) ? row.columns : [];
  if (columns.length === 0) return refusal;
  refusal.payload.table = refusal.payload.table || row.table_name || null;
  refusal.payload.columns = columns;
  refusal.payload.rule = redact(row.definition) || null;
  // Only overwrite the hint when the original was the unhelpful case: a rule
  // broke and no field was named. A single-column violation already told the
  // caller which field, and that hint is better than this one.
  if (!refusal.payload.column) {
    refusal.payload.hint =
      `this rule is about ${columns.length} fields together (${columns.join(", ")}), which is why ` +
      `no single field is named: the database cannot attribute a multi-column rule to one column. ` +
      `\`rule\` is the exact condition your values must satisfy. Check the combination, not each ` +
      `field on its own -- each may be individually valid.`;
  }
  return refusal;
}

export function pgConstraintError(e) {
  const code = e && e.code;
  if (typeof code !== "string") return null;
  if (PG_VIOLATION_KIND[code]) {
    return new ToolError({ error: "invalid_field_value", violation: PG_VIOLATION_KIND[code],
      constraint: e.constraint || null, table: e.table || null, column: e.column || null,
      detail: redact(e.detail) || null,
      hint: "a value failed a database constraint — check it against this verb's documented " +
            "enum or required fields; this is the constraint the value actually violated, not a stack trace" });
  }
  if (PG_FAULT_KIND[code]) {
    return new ToolError({ error: "database_refused_the_statement", fault: PG_FAULT_KIND[code],
      sqlstate: code,
      table: e.table || null, column: e.column || null,
      message: redact(e.message) || null,
      detail: redact(e.detail) || null,
      hint: PG_FAULT_HINT[code] });
  }
  return null;
}

// [ORDER 34 review, blocker 1] The old array-replacer form of JSON.stringify
// FILTERED nested keys instead of canonicalizing them — links[]/building/spaces
// payloads hashed as empty, so a corrected retry under a reused key replayed
// stale data silently. canon() deep-sorts instead. For FLAT args (every
// historical call that replays, incl. the frozen smoke probes) the output
// string — sorted top-level keys — is byte-identical to the old form, so
// stored hashes stay valid. A historical NESTED-args row would key_reuse
// loudly on replay rather than lie quietly; that trade is deliberate.
function canon(v) {
  if (Array.isArray(v)) return v.map(canon);
  if (v && typeof v === "object")
    return Object.keys(v).sort().reduce((o, k) => {
      if (v[k] !== undefined) o[k] = canon(v[k]);
      return o;
    }, {});
  return v;
}

async function requestHash(args) {
  const data = new TextEncoder().encode(JSON.stringify(canon(args)));
  const d = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, "0")).join("");
}

const TOUR_DOMAIN_SERIALIZED_WRITES = new Set([
  "create-tour-domain",
  "append-tour-route-version",
  "prepare-tour-route-version",
  "append-tour-route-stop",
  "append-tour-route-stop-transition",
  "accept-tour-route-version",
  "append-tour-cheat-sheet-revision",
  "restore-tour-cheat-sheet-revision",
  "append-tour-selection-cart-version",
  "record-tour-map-promotion-receipt",
  "issue-tour-share-grant",
  "rotate-tour-share-grant",
  "revoke-tour-share-grant",
  "request-tour-pdf-render",
  "record-tour-pdf-render-result",
  "record-tour-pdf-human-review",
]);

export function auditIdentity(actor) {
  const scope = personalScopeForActor(actor);
  return {
    organization_tenant_id: organizationTenantForActor(actor),
    sponsoring_human_slug: scope.status === "personal" ? scope.sponsor : null,
    personal_scope: scope.status === "personal" ? `${scope.sponsor}-personal` : "none",
    authorization_class: actor.authorization_class || authorizationClassForActor(actor),
    // Program 4 Gap A2 (2026-08-14, defect cae5be2e): the x-correlation-id of the
    // Worker request that produced this write, set on the actor object by
    // mcp.js's dispatch() from env.CORRELATION_ID (correlation.js). null for any
    // caller that reaches a write handler without going through dispatch() —
    // tests, and anything constructing an actor object by hand.
    correlation_id: actor.correlation_id || null,
  };
}

async function withEnvelope(client, actor, verb, args, fn) {
  const key = args.idempotency_key;
  if (!key) throw new ToolError({ error: "missing_idempotency_key",
    hint: "generate a UUID per intended action; retries reuse the SAME key" });
  const identity = auditIdentity(actor);
  // SIEP-11: replay authority is the exact operation manifest, never the bare
  // caller key. Binding the canonical operation and server-derived principal
  // makes cross-verb, cross-actor, cross-client, cross-sponsor, and cross-tenant
  // reuse fail as key_reuse before a stored response can be returned. The
  // session/token/epoch fields are added by SIEP-12/17/21; their absence here
  // cannot widen authority because this manifest is monotonic and deny-only.
  const hash = await requestHash({
    manifest_version: "scac-application-mutation.v1",
    ...mutationManifestIdentity(),
    operation: verb,
    principal: {
      actor_id: actor.id,
      actor_slug: actor.slug,
      human: actor.human === true,
      via: actor.via || null,
      client_id: actor.client_id || null,
      sponsoring_human_slug: identity.sponsoring_human_slug,
      authorization_class: identity.authorization_class,
      organization_tenant_id: identity.organization_tenant_id,
    },
    args: { ...args, idempotency_key: undefined },
  });
  // Versioned writes need same-key serialization before their replay read:
  // otherwise two first calls can both see no tool_call row, and the loser
  // reports a version conflict instead of the promised replay.
  // Keep this scoped until the shared envelope's existing fake-client suites
  // are migrated to model the extra query for every historical write verb.
  if (verb === "record-commission-receipt" || ["record-lead-contact", "advance-leads", "approve-lead-draft", "approve-lead-move", "undo-lead-move", "record-deal-invoice", "undo-invoice-close"].includes(verb) || verb === "teach" || verb === "claim-lead" || verb === "link-lead-client" || (verb === "update-lead" && args.stage_review) || verb === "whats-new" || verb === "write-work-shape" || verb === "set-work-shape-disposition" || verb === "report-problem" || verb === "review-and-triage" || verb === "answer-work-request-for-joe" || verb === "decline-work-request" || verb === "supersede-work-request" || verb === "propose-ready-plan" || verb === "review-heavy-build-plan" || verb === "accept-ready-plan" || verb === "propose-ready-plan-amendment" || verb === "accept-ready-plan-amendment" || verb === "acknowledge-ready-plan-amendment" || verb === "propose-outcome-feedback" || verb === "accept-outcome-feedback" || verb === "record-executed-lease" || verb === "observe-memory" || verb === "promote-memory" || verb === "correct-memory" || verb === "forget-memory" || verb === "register-engineering-slice-plan" || verb === "admit-engineering-slice" || verb === "review-engineering-slice" || verb === "append-tour-rights-receipt" || verb === "revoke-tour-rights-receipt" || verb === "append-tour-source-evidence" || verb === "append-tour-field-assertion" || verb === "create-tour-public-projection-draft" || verb === "seal-tour-public-projection" || verb === "append-tour-property-identifier-assertion" || verb === "append-tour-coordinate-candidate" || verb === "append-tour-entrance-verification-receipt" || verb === "codex-checkpoint" || verb === "codex-record-event" || verb === "ask-jev" || TOUR_DOMAIN_SERIALIZED_WRITES.has(verb) || BOARD_ANSWER_WRITE_VERBS.has(verb) || MEETING_MODE_WRITE_VERBS.includes(verb))
    await client.query("select pg_advisory_xact_lock(hashtextextended($1, 0))", [key]);
  const prior = await client.query("select request_hash, response from tool_call where idempotency_key=$1", [key]);
  if (prior.rows.length) {
    if (prior.rows[0].request_hash !== hash) throw new ToolError({ error: "key_reuse" });
    return { replayed: true, ...prior.rows[0].response };          // A1: replay, no second write
  }
  const result = await fn();                                        // inside the open transaction
  await client.query(
    `insert into tool_call (idempotency_key, verb, actor_id, request_hash, response, via, client_id,
       organization_tenant_id, sponsoring_human_slug, personal_scope, authorization_class, correlation_id)
     values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`,
    [key, verb, actor.id, hash, JSON.stringify(result), actor.via || null, actor.client_id || null,
     identity.organization_tenant_id, identity.sponsoring_human_slug, identity.personal_scope,
     identity.authorization_class, identity.correlation_id]);
  return result;
}

async function writeEvent(client, actor, verb, subjectType, subjectId, fields = {}) {
  const identity = auditIdentity(actor);
  const allowedCauses = new Set(["human_stated", "human_correction", "ingest_email",
    "ingest_calendar", "ingest_webhook", "import_migration", "import_salesforce",
    "automation_job", "learning_job", "system"]);
  // THE DEFAULT USED TO BE 'human_stated' UNCONDITIONALLY, and it made the column
  // a lie. Measured 2026-08-13: 2,822 of 3,946 events read human_stated, including
  // every row written by an automated sweep — 173 research findings, 109 org
  // consolidations, 38 measurement pulls, and this run's own defect records, none
  // of which a human stated. A provenance column that says "a human said this"
  // about a nightly job is worse than an absent one, because a reader trusts it.
  //
  // DERIVED FROM WHO IS WRITING, not from an optimistic default. An explicit cause
  // from the caller still wins, because a verb that knows it is replaying an email
  // or a Salesforce import knows better than this rule does. Otherwise: a write
  // carrying the partner's verbatim words is human-stated by definition — that is
  // the intent signal the write-provenance ruling settled on — and a write from a
  // non-human actor with no quote is an automation job, which is what it is.
  //
  // HISTORY IS NOT REWRITTEN. The 2,822 wrong rows stay wrong. Backfilling an
  // audit trail so a metric reads better is the one repair that would be worse
  // than the defect: the log's value is that it records what happened, including
  // that this column was unreliable before today.
  // THE ACTOR'S human FLAG IS NOT THE DISCRIMINATOR, and trying it first is how
  // this fix was nearly shipped wrong. A scheduled unattended run authenticates as
  // Joe — his OAuth grant, his slug, human:true — so keying on the actor recorded
  // a 2am cron as "a human said this", which is the same lie in a new place. There
  // IS no transport signal separating "Joe decided this" from "the agent decided
  // this"; the write-provenance ruling settled that, and this rule obeys it.
  //
  // So the only honest signal is the one that ruling named: the partner's verbatim
  // words. A write carrying them is human-stated because a session cannot invent a
  // quote without writing a false sentence a human would recognise. A write without
  // them is an automation job, whichever account authenticated — and that is the
  // stricter, more truthful reading, because Joe never types into this database.
  // He tells Claude and Claude writes.
  //
  // An explicit cause from the caller still wins: a verb replaying an email or a
  // Salesforce import knows better than this rule does.
  let cause;
  if (allowedCauses.has(fields.cause)) {
    cause = fields.cause;
  } else if (fields.human_quote && String(fields.human_quote).trim()) {
    cause = "human_stated";
  } else {
    cause = "automation_job";
  }
  await client.query(
    `insert into event (occurred_at, recorded_at, actor_id, verb, subject_type, subject_id, field,
       old_value, new_value, cause, human_quote, agent_rationale, idempotency_key, via, client_id,
       organization_tenant_id, sponsoring_human_slug, personal_scope, authorization_class, correlation_id)
     values (coalesce($1::timestamptz, now()),
       case when $19::boolean then clock_timestamp() else now() end,
       $2, $3, $4, $5, $6, $7, $8, '${cause}', $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)`,
    [fields.occurred_at || null, actor.id, verb, subjectType, subjectId, fields.field || null,
     fields.old ? JSON.stringify(fields.old) : null, fields.new ? JSON.stringify(fields.new) : null,
     fields.human_quote || null, fields.agent_rationale || null, fields.idempotency_key || null,
     actor.via || null, actor.client_id || null, identity.organization_tenant_id,
     identity.sponsoring_human_slug, identity.personal_scope, identity.authorization_class,
     identity.correlation_id, fields.recorded_at_after_lock === true]);
}

const INDUSTRY_EVENT_KINDS = ["conference", "association_meeting", "trade_show", "networking"];
const INDUSTRY_EVENT_ATTENDANCE_INTENTS = ["considering", "plan_to_attend", "not_attending"];
const INDUSTRY_EVENT_STATUSES = ["planned", "attended", "skipped", "cancelled"];
const PARTNER_SLUGS = ["joe", "dell"];

const LEAD_SCORE_FIELDS = {
  score: { type: ["integer", "null"], minimum: 0, maximum: 100 },
  score_reason: { type: ["string", "null"] },
  owner: { type: "string", enum: PARTNER_SLUGS },
};

function validateLeadScoreFields(fields) {
  if (Object.hasOwn(fields, "score") && fields.score !== null &&
      (!Number.isInteger(fields.score) || fields.score < 0 || fields.score > 100))
    throw new ToolError({ error: "invalid_score", hint: "score must be an integer from 0 to 100, or null" });
  if (Object.hasOwn(fields, "score_reason") && fields.score_reason !== null && typeof fields.score_reason !== "string")
    throw new ToolError({ error: "invalid_score_reason", hint: "score_reason must be a string, or null" });
  if (Object.hasOwn(fields, "owner") && !PARTNER_SLUGS.includes(fields.owner))
    throw new ToolError({ error: "invalid_owner", valid: PARTNER_SLUGS });
}

async function resolveLeadOwner(client, slug) {
  const owner = (await client.query("select id,slug,display_name from actor where slug=$1 and active", [slug])).rows[0];
  if (!owner) throw new ToolError({ error: "actor_not_provisioned", slug });
  return { owner: owner.slug, owner_id: owner.id, owner_label: owner.display_name };
}

function industryEventText(value, field, { optional = false, max = 1000 } = {}) {
  if ((value === undefined || value === null || value === "") && optional) return null;
  if (typeof value !== "string" || !value.trim() || value.trim().length > max)
    throw new ToolError({ error: "industry_event_text_invalid", field });
  return value.trim();
}

function industryEventTimestamp(value, field) {
  const text = industryEventText(value, field, { max: 80 });
  if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(text) || Number.isNaN(Date.parse(text)))
    throw new ToolError({ error: "industry_event_timestamp_invalid", field,
      hint: "send an RFC3339 timestamp with an explicit timezone offset" });
  return text;
}

function industryEventUrl(value) {
  if (value === undefined || value === null || value === "") return null;
  let url;
  try { url = new URL(String(value)); } catch {
    throw new ToolError({ error: "industry_event_url_invalid" });
  }
  if (!/^https?:$/.test(url.protocol))
    throw new ToolError({ error: "industry_event_url_invalid", hint: "use an http or https URL" });
  return url.toString();
}

function industryEventValue(value, field, allowed) {
  if (!allowed.includes(value))
    throw new ToolError({ error: "industry_event_value_invalid", field, allowed });
  return value;
}

function validateIndustryEventFields(args, { partial = false } = {}) {
  const required = partial ? [] : ["title", "organizer", "kind", "starts_at", "ends_at", "source"];
  for (const field of required) industryEventText(args[field], field);
  const clean = {};
  if (!partial || Object.hasOwn(args, "title")) clean.title = industryEventText(args.title, "title", { max: 500 });
  if (!partial || Object.hasOwn(args, "organizer")) clean.organizer = industryEventText(args.organizer, "organizer", { max: 500 });
  if (!partial || Object.hasOwn(args, "kind")) clean.kind = industryEventValue(args.kind, "kind", INDUSTRY_EVENT_KINDS);
  if (!partial || Object.hasOwn(args, "starts_at")) clean.starts_at = industryEventTimestamp(args.starts_at, "starts_at");
  if (!partial || Object.hasOwn(args, "ends_at")) clean.ends_at = industryEventTimestamp(args.ends_at, "ends_at");
  if (clean.starts_at && clean.ends_at && Date.parse(clean.ends_at) <= Date.parse(clean.starts_at))
    throw new ToolError({ error: "industry_event_time_order_invalid", hint: "ends_at must be after starts_at" });
  if (!partial || Object.hasOwn(args, "location")) clean.location = industryEventText(args.location, "location", { optional: true, max: 500 });
  if (!partial || Object.hasOwn(args, "is_virtual")) {
    if (args.is_virtual !== undefined && typeof args.is_virtual !== "boolean")
      throw new ToolError({ error: "industry_event_virtual_invalid" });
    clean.is_virtual = args.is_virtual === undefined ? false : args.is_virtual;
  }
  if (!partial || Object.hasOwn(args, "url")) clean.url = industryEventUrl(args.url);
  if (!partial || Object.hasOwn(args, "relevance_note")) clean.relevance_note = industryEventText(args.relevance_note, "relevance_note", { optional: true, max: 2000 });
  if (!partial || Object.hasOwn(args, "attendance_intent"))
    clean.attendance_intent = args.attendance_intent === undefined ? "considering" : industryEventValue(args.attendance_intent, "attendance_intent", INDUSTRY_EVENT_ATTENDANCE_INTENTS);
  if (!partial || Object.hasOwn(args, "owner_partner")) clean.owner_partner = industryEventValue(args.owner_partner, "owner_partner", PARTNER_SLUGS);
  if (!partial || Object.hasOwn(args, "status"))
    clean.status = args.status === undefined ? "planned" : industryEventValue(args.status, "status", INDUSTRY_EVENT_STATUSES);
  if (!partial || Object.hasOwn(args, "source")) clean.source = industryEventText(args.source, "source", { max: 2000 });
  return clean;
}

function industryEventId(value) {
  if (typeof value !== "string" || !UUID_RE.test(value))
    throw new ToolError({ error: "industry_event_id_invalid" });
  return value;
}

// [defect 18b12fda-b79c-43a1-86c4-51b9623e12fd, 2026-08-14] THE VIOLATION WAS OURS.
// add-party (kind='org', name='Wagtail Lodge Resort') refused twice with
// unique_violation on party_org_identity_uniq while a read-only tap of the same
// database found zero matching rows — because the collision was with the verb's
// OWN uncommitted work. The call carried org_name restating the org itself, so
// org_party_id() minted the org inside the open transaction, the main insert then
// asserted the same normalised identity a second time, the index refused, and the
// rollback erased both rows. Deterministic under fresh keys, invisible in the data.
//
// The guard below is IDENTITY-BASED and asks the database's own org_identity_key()
// — never a JS re-implementation, per that function's comment ("EXTEND this
// function rather than invent a second normalisation rule"). An org_name with a
// genuinely different identity stays legal on an org row: party.org_id is how a
// parent/sub-org structure (a national account over its franchisees) is expressed
// — see reassign-deal. Only the self-reference is dropped, and the caller is told.
// Shared by add-party and add-premises' new_party path (rule a8c55a47: two paths
// doing the same job must be the same code).
async function employerOrgId(c, actorId, kind, name, orgName) {
  if (!orgName) return { orgId: null, selfNamed: false };
  if (kind === "org") {
    const k = await c.query(
      "select org_identity_key($1) = org_identity_key($2) as same_org", [orgName, name]);
    if (k.rows[0]?.same_org) return { orgId: null, selfNamed: true };
  }
  const o = await c.query("select org_party_id($1,$2) as id", [orgName, actorId]);
  return { orgId: o.rows[0].id, selfNamed: false };
}

// The residual collision: an existing LIVE org that slipped the similarity guard
// (or was force_new'd past it) still trips party_org_identity_uniq on the insert.
// That refusal is correct — the index's comment says a same-name collision is
// resolved by disambiguating the NAME, never by weakening the key — but a raw
// unique_violation names an index, not a next step. Run the insert under a
// SAVEPOINT, and on this one constraint roll back to it and hand back the
// surviving row so the caller can reuse it or rename theirs.
//
// The savepoint is load-bearing, not ceremony: after any SQL error the enclosing
// transaction is aborted (25P02) and every later statement — including
// withEnvelope's own tool_call insert — would fail until a rollback. Catching the
// error in JS and simply querying on (as new-deal's 23505 mapper does) trades one
// opaque error for another.
async function insertOrgPartyGuarded(c, savepoint, insertSql, insertParams, name) {
  await c.query(`savepoint ${savepoint}`);
  try {
    return { row: (await c.query(insertSql, insertParams)).rows[0] };
  } catch (e) {
    if (e.code !== "23505" || e.constraint !== "party_org_identity_uniq") throw e;
    await c.query(`rollback to savepoint ${savepoint}`);
    const existing = await c.query(
      `select id, name, email, city from party
        where kind='org' and merged_into is null and deleted_at is null
          and org_identity_key(name) = org_identity_key($1)`, [name]);
    return { conflict: existing.rows };
  }
}

// Research is evidence, not a checkbox supplied after the write.  These
// validators run before every intake write that turns a contact into a client
// or vendor (and before a directly-created contact party).  The resulting
// evidence is then persisted as a `verified` finding in the same envelope.
function researchEvidence(raw, requiredFields, gate) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw))
    throw new ToolError({ error: "research_evidence_required", gate,
      hint: "research before creating this contact; pass typed HTTPS sources, per-field source indexes, and discrepancies[] (empty when none)" });
  const sources = Array.isArray(raw.sources) ? raw.sources.map((source, index) => {
    if (!source || typeof source !== "object" || Array.isArray(source))
      throw new ToolError({ error: "research_source_invalid", gate, source_index: index });
    let url;
    try { url = new URL(String(source.url || "")); }
    catch { throw new ToolError({ error: "research_source_invalid", gate, source_index: index }); }
    const observed = new Date(String(source.observed_at || ""));
    if (url.protocol !== "https:" || Number.isNaN(observed.getTime()) || observed.getTime() > Date.now() + 300000)
      throw new ToolError({ error: "research_source_invalid", gate, source_index: index,
        hint: "each source needs an HTTPS URL and a non-future observed_at timestamp" });
    return { url: url.toString(), observed_at: observed.toISOString() };
  }) : [];
  const links = raw.field_evidence;
  const linked = links && typeof links === "object" && !Array.isArray(links) ? links : {};
  const validLinks = field => Array.isArray(linked[field]) && linked[field].length > 0 &&
    linked[field].every(i => Number.isInteger(i) && i >= 0 && i < sources.length);
  const missing = requiredFields.filter(field => !validLinks(field));
  if (!sources.length || missing.length || !Array.isArray(raw.discrepancies))
    throw new ToolError({ error: "research_evidence_incomplete", gate,
      missing_fields: missing, has_sources: sources.length > 0,
      hint: "link every required field to one or more typed source indexes and pass discrepancies[] even when it is empty" });
  return { sources, checked_fields: requiredFields, field_evidence: linked,
    discrepancies: raw.discrepancies };
}

const RESEARCH_EVIDENCE_SCHEMA = {
  type: "object",
  required: ["sources", "field_evidence", "discrepancies"],
  properties: {
    sources: { type: "array", items: { type: "object", required: ["url", "observed_at"],
      properties: { url: { type: "string" }, observed_at: { type: "string" } } } },
    field_evidence: { type: "object" },
    discrepancies: { type: "array" },
  },
};

async function stampResearch(c, actor, partyId, evidence) {
  await c.query(
    `insert into record_flag (subject_type,subject_id,kind,value,source,created_by)
     values ('party',$1,'verified',$2,$3,$4)`,
    [partyId, JSON.stringify({ found: true, checked_fields: evidence.checked_fields,
      field_evidence: evidence.field_evidence, sources: evidence.sources,
      discrepancies: evidence.discrepancies, epistemic_status: "source_backed" }),
     evidence.sources.map(source => `${source.url} observed ${source.observed_at}`).join(" | "), actor.id]);
}

function preferredMergeSurvivor(rows) {
  if (!Array.isArray(rows) || rows.length !== 2) return null;
  const score = row => [row.has_business_ref ? 1 : 0,
    Number(row.verified_identity_fields || 0), Number(row.linked_records || 0)];
  const [a, b] = rows;
  const as = score(a), bs = score(b);
  for (let i = 0; i < as.length; i += 1) {
    if (as[i] !== bs[i]) return as[i] > bs[i] ? a : b;
  }
  return new Date(a.created_at).getTime() <= new Date(b.created_at).getTime() ? a : b;
}

// [loop #278] The ONE place a decision gets mirrored onto the record it governs.
// log-decision calls it at creation and update-decision calls it after the fact, so
// attaching late and attaching at the time produce byte-identical rows — a manual path
// and an automated path that do the same job must be the same code (rule a8c55a47).
//
// `about` takes a single ref or several. Several is not a nicety: the real population
// includes rulings like the 2026-08-06 vendor merges, one ruling settling V-GC-001,
// V-MSC-024 and T-004 together, which a single-ref parameter can only record a third of.
//
// Re-attaching is safe. A mirror already written for the same (subject, decision) pair
// is skipped, not duplicated, so calling update-decision twice does not stack pointers
// on a timeline.
async function mirrorDecision(client, actor, d) {
  const refs = (Array.isArray(d.about) ? d.about : [d.about])
    .map(r => String(r || "").trim()).filter(Boolean);
  if (!refs.length) return [];

  // Resolve EVERY ref before writing anything: a bad ref in position three must not
  // leave two pointers behind from positions one and two.
  const seen = new Map();
  for (const ref of refs) {
    const s = await resolveSubject(client, ref);
    seen.set(`${s.type}:${s.id}`, { ...s, ref });   // dedupe refs naming one record
  }

  const attached = [];
  for (const s of seen.values()) {
    // A RETRACTED pointer does not count as already-attached. Re-attaching after a
    // detach-decision writes a fresh live pointer and leaves the retracted one standing,
    // so the timeline shows the whole history — attached, retracted, attached again —
    // rather than quietly resurrecting a row somebody deliberately struck through.
    const dup = await client.query(
      `select 1 from event
        where subject_type = $1 and subject_id = $2 and field = 'decision'
          and new_value->>'decision_id' = $3
          and coalesce((new_value->>'retracted')::boolean, false) = false limit 1`,
      [s.type, s.id, d.decision_id]);
    if (dup.rows.length) { attached.push({ ...s, already: true }); continue; }

    await writeEvent(client, actor, "log-decision", s.type, s.id, {
      occurred_at: d.occurred_at || null,
      field: "decision",
      new: { summary: d.title, decision_id: d.decision_id, decision_event_id: d.decision_event_id },
      human_quote: d.human_quote || null,
      agent_rationale: d.rationale || null,
      idempotency_key: d.idempotency_key,
    });
    attached.push({ ...s, already: false });
  }
  return attached;
}

// Pure decision logic for the optimistic-lock check, isolated from the DB
// round trip so it is unit-testable without a connection. THE BUG THIS FIXES
// (found 2026-08-13, decision 7026246b): the old check was `current !==
// baseVersion`, a STRICT comparison with no coercion. `current` always comes
// back a genuine JS number (the loop_item.version column is `int`, and both
// the Worker's and local-verb's Pool driver parse int4 to Number), but
// `baseVersion` arrives verbatim from the caller's JSON payload — and MCP
// tool-call arguments are never validated against inputSchema server-side
// (see callTool in mcp.js: `rpc.params?.arguments` is passed straight
// through). A caller that sent base_version as the JSON STRING "1" instead
// of the number 1 — e.g. copying a value that had been rendered as text —
// produced `1 !== "1"` => true: a false version_conflict on a loop that had
// just been created and never touched again, reproduced twice on 2026-08-13
// (loop #350, base_version 1, current_version 1). Coercing both sides to
// Number before comparing fixes this without weakening the check: a REAL
// mismatch (e.g. 1 vs 3) still differs after coercion.
export function compareVersion(current, baseVersion) {
  if (baseVersion === undefined || baseVersion === null)
    return { ok: false, kind: "missing_base_version" };
  const cv = Number(current);
  const bv = Number(baseVersion);
  if (!Number.isFinite(bv))
    return { ok: false, kind: "invalid_base_version" };
  if (cv !== bv) return { ok: false, kind: "conflict" };
  return { ok: true };
}

// THE OTHER HALF OF THE SAME DEFECT (found 2026-08-13, loop 353). compareVersion
// above fixed ONE field, base_version, against mistyped arrival. The cause it
// names — "MCP tool-call arguments are never validated against inputSchema
// server-side" — was never field-specific, and leaving it at one field meant the
// next mistyped argument was only a matter of which verb got called.
//
// It got called. `teach` decides a rule's SCOPE with `args.personal ? ... : null`
// (loose truthiness) and echoes it back with `args.personal === true` (strict).
// A boolean that arrived as the STRING "false" is truthy at the first line and
// false at the second, so the verb stored a SHARED rule as PERSONAL while
// reporting personal_requested:false. That is not a cosmetic disagreement: scope
// decides WHO a taught rule binds, the response said the caller got what it
// asked for, and only a hand comparison of two exported files caught it. Twice
// in one session.
//
// Fixing teach alone would have been the same mistake a second time: a sweep
// found 17 sites reading a declared boolean or number with loose truthiness or
// bare arithmetic, SEVEN of which write the wrong value straight to the database
// (drift_critical on add-loop and update-loop, also_listing_side on add-premises,
// found and internal on record-finding, close on score-campaign), and eight more
// that silently skip a dedup or plausibility gate. So the coercion happens ONCE,
// at the choke point every verb passes through, and no handler has to remember.
//
// STRICTLY SCHEMA-DRIVEN, NEVER VALUE-SNIFFING. Only a property whose declared
// type is exactly "boolean", "integer" or "number" is touched. A field declared
// "string" is never inspected, so free text that happens to read "true" (a
// human_quote, a note, a rationale) is untouchable by construction. A union
// (oneOf, anyOf, or type given as an array) is skipped rather than guessed at —
// log-decision's `about` takes string OR array, and patch-deal-field has
// nullable strings.
//
// IT THROWS RATHER THAN GUESSING. "true"/"false" and numeric strings map
// cleanly; anything else in a typed field is a caller error and now fails
// loudly, in the same spirit as invalid_base_version. Silently leaving an
// unmappable value in place is what produced this defect in the first place.
//
// Recursive, because two of the affected flags are not top-level: add-premises
// carries also_listing_side inside ownership[] and force_new one level deeper
// inside ownership[].new_party.
// ── THE MARKUP WRITE DOOR (2026-08-14) ───────────────────────────────────────
// ops/store-markup-scan.py has been catching this AFTER the fact for weeks: a
// caller composes several long fields as one block of text, a field swallows its
// own closing tag, and every parameter after that tag is stored NULL. Six active
// shared rules carried it on 2026-08-13, the oldest four days old, five with a
// partner's verbatim quote absorbed into the rule statement.
//
// A detector cannot un-write a NULL, and this record layer refuses to edit a
// closed row on purpose — "a closed loop is history" — so damage that lands and
// is then closed is permanent. The write door is the only place it can actually
// be stopped, which is here.
//
// CORRUPTION vs MENTION is the same structural test ops/store-markup-scan.py's
// classify() uses, deliberately, because two doors disagreeing about one row is
// worse than either rule alone: the field swallowed ITS OWN closing tag, or it
// carries a bare marker that ate whatever should have followed. Markers quoted
// in backticks on one line are prose ABOUT the defect — the rule documenting it,
// the loops that tracked the cleanup, this comment — and must stay writable.
const TOOL_CALL_MARKERS = ["<parameter", "</parameter", "<invoke", "</invoke"];

export function looksLikeToolCallMarkup(field, value) {
  if (typeof value !== "string" || value === "") return false;
  // close_outcome's own closing tag is </outcome>; the scan strips the close_
  // prefix the same way, and the two must not disagree about the same row.
  const ownCloser = `</${String(field).replace(/^close_/, "")}>`;
  if (value.includes(ownCloser)) return true;
  for (const marker of TOOL_CALL_MARKERS) {
    let idx = value.indexOf(marker);
    while (idx !== -1) {
      const before = value.lastIndexOf("`", idx);
      const after = value.indexOf("`", idx);
      const quoted = before !== -1 && after !== -1
        && !value.slice(before, after).includes("\n");
      if (!quoted) return true;
      idx = value.indexOf(marker, idx + 1);
    }
  }
  return false;
}

// REQUIRED ARGUMENTS, ENFORCED AT THE DOOR (2026-08-14).
//
// Every verb declares `required` in its inputSchema and, until this existed,
// nothing checked it. mcp.js hands rpc.params.arguments through untouched and
// the local CLI path does the same, so a required field that was misspelled or
// simply absent arrived as undefined and the handler ran regardless.
//
// WHAT THAT PRODUCED, measured live rather than imagined. search-doctrine builds
// websearch_to_tsquery('english', undefined), which Postgres does not treat as
// an error — it matches nothing:
//
//     search-doctrine {"query":"HIPAA"}  ->  ok:true, hits:[], total:0
//     search-doctrine {}                 ->  ok:true, hits:[], total:0
//     search-doctrine {"q":"HIPAA"}      ->  20 hits
//
// A call with NO ARGUMENTS AT ALL came back clean and empty.
//
// AN EMPTY RESULT IS INDISTINGUISHABLE FROM A GENUINE ABSENCE, which is what
// makes this worse than a crash. On 2026-08-14 a session searched doctrine for a
// settled council ruling, got total:0, concluded the ruling did not exist, and
// filed a defect claiming the doctrine read path was broken. The ruling was
// there; the parameter name was wrong. Rule c53beeaa already says an ok:true
// confirms the call PARSED and never that the values landed — this enforces that
// at the boundary instead of hoping each of a hundred handlers remembers.
//
// It sits beside coerceArgsToSchema deliberately, per the same 2026-08-13 ruling
// that put coercion at the choke point rather than in seventeen handlers.
//
// PRESENCE, not truthiness: `false` and `0` are arguments a caller meant. `null`
// and `""` are what an unfilled template produces and carry no instruction.
//
// The near-miss hint is not decoration. The observed failure was a caller who
// HAD the schema and still sent `query` for `q`, so the error names the field it
// wanted and echoes the unrecognised keys that were sent instead.
export function assertRequiredArgs(schema, args) {
  const required = schema && Array.isArray(schema.required) ? schema.required : null;
  if (!required || !required.length) return args;
  const bag = (args && typeof args === "object" && !Array.isArray(args)) ? args : {};
  // A field the schema DECLARES nullable (type ["string","null"], or an anyOf /
  // oneOf branch of type "null") takes null as a real answer: "no appointment",
  // "no prior route version". Refusing it made such verbs uncallable, since the
  // key is required AND its only honest value was null (append-tour-route-stop,
  // append-tour-route-stop-transition, search-tour-properties, 2026-09-23).
  // Absent keys and "" stay missing for every field.
  const props = (schema && schema.properties) || {};
  const nullable = (k) => {
    const p = props[k];
    if (!p || typeof p !== "object") return false;
    if (p.type === "null" || (Array.isArray(p.type) && p.type.includes("null"))) return true;
    return [p.anyOf, p.oneOf].some((alts) => Array.isArray(alts) && alts.some((a) => a && a.type === "null"));
  };
  const missing = required.filter((k) => {
    const v = bag[k];
    if (v === null && nullable(k)) return false;
    return v === undefined || v === null || v === "";
  });
  if (!missing.length) return args;
  const known = new Set(Object.keys(schema.properties || {}));
  const unrecognised = Object.keys(bag).filter((k) => !known.has(k));
  const payload = {
    error: "missing_required",
    missing,
    hint: `this verb requires ${missing.map((m) => JSON.stringify(m)).join(", ")}`,
  };
  if (unrecognised.length) {
    payload.unrecognised = unrecognised;
    payload.hint += `; it received ${unrecognised.map((u) => JSON.stringify(u)).join(", ")}` +
      `, which it does not accept — check the argument NAME against the schema before` +
      ` concluding the verb is broken or the answer is empty`;
  }
  throw new ToolError(payload);
}


export function coerceArgsToSchema(schema, args, path = "") {
  if (!schema || !args || typeof args !== "object" || Array.isArray(args)) return args;
  const props = schema.properties;
  if (!props) return args;
  for (const [key, spec] of Object.entries(props)) {
    if (!spec || !Object.prototype.hasOwnProperty.call(args, key)) continue;
    const v = args[key];
    if (v === undefined || v === null) continue;
    const where = path ? `${path}.${key}` : key;
    // Refused BEFORE any coercion or type branch: a leaked tag makes the value
    // wrong whatever its declared type, and the fields this lands in (body,
    // source_note, outcome) are plain strings that would otherwise sail through
    // untouched. The error names the field and the fix, because the caller that
    // does this is mid-way through composing several long fields and needs to be
    // told which one ran into the next.
    if (looksLikeToolCallMarkup(key, v)) {
      throw new ToolError({
        error: "tool_call_markup_in_value", field: where,
        hint: `${where} contains tool-call markup (a leaked </${String(key).replace(/^close_/, "")}> `
          + `or <parameter …> tag). The field ran into the one after it, and every field `
          + `after the leak would be stored empty. Pass each field as its own argument `
          + `rather than composing them as one block of text, then retry. Prose ABOUT `
          + `this defect is allowed when the markers are quoted in backticks.`,
      });
    }
    // A union is ambiguous by design; coercing one would destroy the other.
    if (spec.oneOf || spec.anyOf || Array.isArray(spec.type)) continue;
    if (spec.type === "boolean") {
      if (typeof v === "boolean") continue;
      if (typeof v === "string") {
        const s = v.trim().toLowerCase();
        if (s === "true") { args[key] = true; continue; }
        if (s === "false") { args[key] = false; continue; }
      }
      throw new ToolError({ error: "invalid_boolean", field: where, got: typeof v === "string" ? v : typeof v,
        hint: `${where} is declared boolean; pass true or false, not a quoted or numeric value` });
    }
    if (spec.type === "integer" || spec.type === "number") {
      if (typeof v === "number") continue;
      if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v.trim()))) {
        args[key] = Number(v.trim());
        continue;
      }
      throw new ToolError({ error: "invalid_number", field: where, got: typeof v === "string" ? v : typeof v,
        hint: `${where} is declared ${spec.type}; pass a number` });
    }
    if (spec.type === "object") { coerceArgsToSchema(spec, v, where); continue; }
    // A JSON-STRING ARRAY IS STILL AN ARRAY ARGUMENT, and until 2026-08-15 it fell
    // straight through here uncoerced, because this branch required Array.isArray
    // BEFORE it would look at anything. What that cost, live: doctrine-sections
    // measured `.length` on an ~80-character string and answered
    // "batch_too_large, max 50" for a batch of two, while a single id slipped
    // under the limit and died casting a string to uuid[]. claim-doctrine-sections
    // iterated the same string into characters, so no session could claim a
    // doctrine section and the single-writer write path was down for hours.
    //
    // Parsed HERE rather than in the handlers, per the 2026-08-13 ruling that put
    // coercion at the choke point instead of in seventeen of them. Every verb
    // taking an array gets this, not only the three that happened to be caught.
    let value = v;
    if (spec.type === "array" && typeof value === "string") {
      const text = value.trim();
      if (text.startsWith("[")) {
        try {
          const parsed = JSON.parse(text);
          if (Array.isArray(parsed)) { args[key] = value = parsed; }
        } catch { /* leave it; the handler's own validation refuses it by name */ }
      }
    }
    if (spec.type === "array" && Array.isArray(value) && spec.items) {
      value.forEach((item, i) => coerceArgsToSchema(spec.items, item, `${where}[${i}]`));
    }
  }
  return args;
}

// AUTO-REBASE FOR A PURE VERSION-NUMBER RACE (WR-000019 slice S6, CONFLICT
// TIERING). version_conflict is deliberately "ask the human, never auto-retry"
// (rule 14181e60) whenever the intervening write touched a field THIS call is
// also about to touch — that is a real collision and must surface. But most
// verbs bump `version` on every write regardless of which columns changed, so
// two callers editing DISJOINT fields (Joe corrects `city` while a nightly
// sweep updates `salesforce_id`) still collide on version alone even though
// nothing they wrote actually conflicts. That shape is mechanical bookkeeping,
// not a judgment call, and this is where it is caught.
//
// touchedFields is OPT IN and defaults to null, which preserves every existing
// caller's exact behaviour (14 sites call this with 3 args; none of them
// change). Only a caller that can name, up front, exactly which columns its
// own write is about to set may opt in — passing the wrong set would let a
// real collision rebase silently, so this is deliberately per-verb, not a
// blanket default. update-deal is the first (and, for now, only) caller wired
// this way: its `fields{}` PATCH already computes the exact touched-column set
// before it needs a version at all.
export function disjointFromIntervening(touchedFields, interveningEvents) {
  if (!Array.isArray(touchedFields) || !touchedFields.length) return false;
  if (!interveningEvents.length) return false; // nothing intervened at all — not a race, just a stale read of nothing
  const touched = new Set(touchedFields);
  return interveningEvents.every(row => !row.field || !touched.has(row.field));
}

async function versionGuard(client, table, id, baseVersion, touchedFields = null) {
  // Every write handler runs inside mcp.js's writer transaction.  Locking the
  // row makes the optimistic check real: a concurrent writer waits, then sees
  // the incremented version instead of letting two same-version writes through.
  // Query text UNCHANGED from before this fix (still exactly
  // "select version from <table> where id=$1 for update") — existing fakes in
  // this suite (loop-owner-repair.test.mjs) match on it verbatim, and the new
  // logic below only needs a second read on the rare conflict path.
  const r = await client.query(`select version from ${table} where id=$1 for update`, [id]);
  if (!r.rows.length) throw new ToolError({ error: "not_found", table, id });
  const current = r.rows[0].version;
  const cmp = compareVersion(current, baseVersion);
  if (cmp.kind === "missing_base_version")
    throw new ToolError({ error: "missing_base_version", current_version: current,
      hint: "read the record first; pass its version back as base_version" });
  if (cmp.kind === "invalid_base_version")
    throw new ToolError({ error: "invalid_base_version", got: baseVersion, current_version: current,
      hint: "base_version must be the integer version from a fresh read, not a non-numeric value" });
  if (!cmp.ok) {
    // Exclude the record's OWN creation event from the "intervening" list.
    // A caller holding any base_version >= 1 has, by construction, already
    // read the record after it existed — its birth is not news to them, so
    // citing it as an intervening event is misleading regardless of the fix
    // above. created_at and the creation event's recorded_at are written in
    // the same transaction (both default to now()), so they are exactly
    // equal; `recorded_at > created_at` keeps every REAL subsequent edit and
    // drops only that one founding row. Fetched here, lazily, only on the
    // conflict path, rather than folded into the query above.
    const created = await client.query(`select created_at from ${table} where id=$1`, [id]);
    const ev = await client.query(
      `select a.slug as actor, e.verb, e.field, e.old_value, e.new_value, e.recorded_at
       from event e join actor a on a.id=e.actor_id
       where e.subject_id=$1 and e.recorded_at > $2 order by e.recorded_at desc limit 5`,
      [id, created.rows[0]?.created_at ?? null]);
    // TRIVIAL RACE: every intervening event's field lies outside this call's
    // own touched set. Rebase transparently onto the row this transaction
    // already holds locked (current is fresh and safe to act on — the `for
    // update` above means nobody else can move it again until this
    // transaction commits) and hand the caller a receipt instead of a refusal.
    // SAME-FIELD OR UNDECLARED: unchanged, still asks the human.
    if (disjointFromIntervening(touchedFields, ev.rows)) {
      return { version: current, rebased: true, rebase_receipt: {
        from_base_version: Number(baseVersion), rebased_to_version: current,
        disjoint_intervening_events: ev.rows.map(row => ({
          actor: row.actor, verb: row.verb, field: row.field, recorded_at: row.recorded_at })),
        hint: "version advanced from a write to a different field; re-applied against the current row with no human confirmation needed",
      } };
    }
    throw new ToolError({ error: "version_conflict", current_version: current,
      intervening_events: ev.rows,
      hint: "surface this to the human and re-read; NEVER auto-retry" });
  }
  return { version: current, rebased: false, rebase_receipt: null };
}

async function config(client, key, fallback) {
  const r = await client.query("select value from system_config where key=$1", [key]);
  return r.rows.length ? r.rows[0].value : fallback;
}

async function rateConfirm(client, args, normValue, bandKey) {
  if (normValue == null || args.confirm) return;
  const band = await config(client, bandKey, { min: 5, max: 120 });
  if (normValue < band.min || normValue > band.max)
    throw new ToolError({ error: "needs_confirm",
      reason: `normalized rate ${normValue} $/SF/yr is outside ${band.min}-${band.max}`,
      hint: "a 12x miss usually means $/SF/mo vs $/SF/yr; resubmit with confirm:true if intended" });
}

function normRate(amount, basis) {
  if (amount == null) return null;
  if (basis === "usd_sf_yr") return amount;
  if (basis === "usd_sf_mo") return amount * 12;
  return null; // gross bases: tool computes from area when space known, else norm_owed
}

// The deal paper writes a phone as (850) 361-2208. The actor row stores what the
// human typed (joe: 850.361.2208), so the plan formats it here — one convention
// in one place, rather than every field map carrying a format. An unrecognized
// shape passes through verbatim: a phone is never invented or truncated to fit.
function fmtPhoneUS(v) {
  if (v === null || v === undefined) return null;
  const digits = String(v).replace(/\D/g, "");
  const t = digits.length === 11 && digits[0] === "1" ? digits.slice(1) : digits;
  if (t.length !== 10) return String(v).trim() || null;
  return `(${t.slice(0, 3)}) ${t.slice(3, 6)}-${t.slice(6)}`;
}

async function resolveSubject(client, ref) {
  // Accepts 'L-204', 'C-127', 'V-CPA-006', a deal name, or a party/practice name.
  //
  // [amendment 11] Every lookup goes through v_ref_index. This used to query the
  // base tables, which carr_reader cannot see — views-only is deliberate — so the
  // read verbs returned permission-denied in production from build day until this
  // was found by ORDER 6's done-test. The security model wins; the verb adapts.
  // A raw UUID resolves exactly (the Deal Room board addresses deals by id;
  // v_ref_index carries the id, so views-only holds).
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(ref)) {
    const r = await client.query(
      "select subject_type, subject_id from v_ref_index where subject_id=$1 limit 2", [ref]);
    if (r.rows.length === 1) return { type: r.rows[0].subject_type, id: r.rows[0].subject_id };
    if (r.rows.length > 1) {
      const deal = r.rows.find(x => x.subject_type === "deal");
      if (deal) return { type: "deal", id: deal.subject_id };
    }
  }
  if (/^L-\d+/i.test(ref)) {
    const r = await client.query(
      "select subject_id from v_ref_index where subject_type='lead' and ref ilike $1", [ref]);
    if (r.rows.length) return { type: "lead", id: r.rows[0].subject_id };
  }
  if (/^C-\d+/i.test(ref)) {
    const r = await client.query(
      "select subject_id from v_ref_index where subject_type='client' and ref ilike $1", [ref]);
    if (r.rows.length) return { type: "client", id: r.rows[0].subject_id };
  }
  if (/^[VT]-/i.test(ref)) {
    const r = await client.query(
      "select subject_id from v_ref_index where subject_type='vendor' and ref ilike $1", [ref]);
    if (r.rows.length) return { type: "vendor", id: r.rows[0].subject_id };
  }
  // P- party refs. Added 2026-08-09 after update-decision refused about:'P-0948' during
  // the loop #278 backfill: party was the one record class whose OWN printed ref form
  // this resolver could not take back, so every verb built on it — catch-me-up, find,
  // update-decision, record-finding, all of them — pushed party work onto name matching
  // instead. record-finding's description had been advertising 'P-0301' as a valid
  // subject the whole time, so the documented contract and the resolver disagreed.
  if (/^P-\d+/i.test(ref)) {
    const r = await client.query(
      "select subject_id from v_ref_index where subject_type='party' and ref ilike $1", [ref]);
    if (r.rows.length) return { type: "party", id: r.rows[0].subject_id };
  }
  // [amendment 7] Both name fallbacks used to take the single newest/closest match.
  // On an ambiguous name that silently wrote to the WRONG record, with no signal —
  // exactly the failure tool-contracts §5 says a verb must never produce. Fetch up
  // to 5 and refuse to guess when more than one matches.
  // [amendment 7] Name paths fetch up to 5 and refuse to guess past one match.
  let r = await client.query(
    `select subject_id, display_name, status, client_ref from v_ref_index
      where subject_type='deal' and display_name ilike $1 limit 5`, [`%${ref}%`]);
  if (r.rows.length === 1) return { type: "deal", id: r.rows[0].subject_id };
  if (r.rows.length > 1) {
    throw new ToolError({ error: "needs_disambiguation", ref,
      candidates: r.rows.map(x => ({ name: x.display_name, phase: x.status, client_ref: x.client_ref })),
      hint: "pass the exact ref or full name" });
  }
  // Merge tombstones are excluded: a merged record is not a resolution target, and
  // leaving them in would make every merged pair permanently ambiguous. That is what
  // the view's `merged` flag is for — no column added to satisfy this.
  r = await client.query(
    `select subject_type, subject_id, display_name, ref, city from v_ref_index
      where subject_type in ('lead','client','vendor') and not merged and display_name ilike $1
      order by similarity(display_name, $2) desc limit 5`, [`%${ref}%`, ref]);
  if (r.rows.length === 1) return { type: r.rows[0].subject_type, id: r.rows[0].subject_id };
  if (r.rows.length > 1) {
    throw new ToolError({ error: "needs_disambiguation", ref,
      candidates: r.rows.map(x => ({ name: x.display_name, ref: x.ref, kind: x.subject_type, city: x.city })),
      hint: "pass the exact ref or full name" });
  }
  // BARE PARTIES ARE A FALLBACK, NEVER A COMPETITOR (0056, 2026-08-02). Migration
  // 0056 put every party in v_ref_index, which is what finally makes an org like
  // Henry Schein — 17 rows, no lead/client/vendor among them — resolvable at all.
  // But this is the WRITE path: folding parties into the query above would let a
  // bare party outrank the client or vendor a name resolves to today and quietly
  // move where writes land. So the role query runs first and unchanged, and this
  // runs ONLY when it found nothing. Purely additive: anything that resolves today
  // resolves to exactly the same record.
  r = await client.query(
    `select subject_type, subject_id, display_name, ref, city from v_ref_index
      where subject_type='party' and not merged and display_name ilike $1
      order by similarity(display_name, $2) desc limit 5`, [`%${ref}%`, ref]);
  if (r.rows.length === 1) return { type: "party", id: r.rows[0].subject_id };
  if (r.rows.length > 1) {
    throw new ToolError({ error: "needs_disambiguation", ref,
      candidates: r.rows.map(x => ({ name: x.display_name, ref: x.ref, kind: x.subject_type, city: x.city })),
      hint: "more than one party carries this name — often duplicate org rows for one company; pass the exact P-ref" });
  }
  throw new ToolError({ error: "subject_not_found", ref,
    hint: "use find first; refs look like L-204 / C-127 / V-CPA-006 / P-0948 or a deal name" });
}

// ---------- loop helpers (one-writer Phase A, ORDER 31) ----------

// A LOOP NUMBER IS NOT AN IDENTIFIER, and pretending otherwise is how a verb
// writes to the wrong row. Measured in the source files 2026-07-31: '111' names
// two different items inside open-loops.md, '103'/'95'/'88'/'108' each name one
// hot item and a different backlog item, 'T34' names one row in team-loops' Open
// table and another in its Done table. So `number` narrows and `loop_id`
// identifies, and an ambiguous number REFUSES with the candidates listed —
// ORDER 1's needs_disambiguation behaviour, applied to a surface that is
// genuinely ambiguous rather than occasionally so.
// opts.anyStatus (default false, unchanged behaviour for every existing
// caller) lets amend-closed-loop resolve a CLOSED row by number too: the
// default number-lookup is scoped to status='open' because 0112 only
// guarantees uniqueness there, and every caller before amend-closed-loop only
// ever needed an open row anyway. amend-closed-loop needs the opposite row —
// closed — so it opts in rather than the default widening for everyone.
async function resolveLoop(client, args, opts = {}) {
  const anyStatus = opts.anyStatus === true;
  if (args.loop_id) {
    const r = await client.query(
      `select li.id, li.kind, li.number, li.status, li.marker, li.due_on,
              li.close_outcome, lb.block_key as section
         from loop_item li join loop_block lb on lb.id = li.block_id
        where li.id = $1`, [args.loop_id]);
    if (!r.rows.length) throw new ToolError({ error: "not_found", loop_id: args.loop_id });
    return r.rows[0];
  }
  if (!args.number)
    throw new ToolError({ error: "missing_loop_ref",
      hint: "pass loop_id, or number (plus kind when the number is shared across kinds)" });
  const r = await client.query(
    `select li.id, li.kind, li.number, li.status, li.marker, li.due_on,
            li.close_outcome, lb.block_key as section, lb.rel_path
       from loop_item li join loop_block lb on lb.id = li.block_id
      where li.number = $1 and (${anyStatus ? "true" : "li.status = 'open'"})
        and ($2::text is null or li.kind = $2)`, [args.number, args.kind || null]);
  if (!r.rows.length)
    throw new ToolError({ error: "loop_not_found", number: args.number, kind: args.kind || null,
      hint: anyStatus ? "no row, open or closed, carries this number — pass loop_id"
                       : "only OPEN loops resolve by number; a closed one needs its loop_id" });
  if (r.rows.length > 1)
    throw new ToolError({ error: "needs_disambiguation", number: args.number,
      candidates: r.rows.map(x => ({ loop_id: x.id, kind: x.kind, section: x.section,
                                     renders_into: x.rel_path })),
      hint: "this number names more than one live row — pass loop_id to act on one now, and " +
            "fix the collision itself with update-loop's `number` (plus renumber_reason). " +
            "Migration 0112 makes a new one impossible; anything left is pre-0112 history." });
  return r.rows[0];
}

// read-loop's amendment attachment. loop_amendment_history() (migration 0702)
// is a SECURITY DEFINER function so carr_reader can call it without a
// base-table grant on loop_amendment — read-loop runs on the reader
// connection like every other write:false verb, and the append-only table's
// grant stays exactly select+insert to carr_writer, nothing wider.
async function withAmendmentHistory(client, loop) {
  const r = await client.query(
    `select id, prior_outcome, new_outcome, prior_resolution, new_resolution,
            reason, actor, to_jsonb(created_at)#>>'{}' as created_at
       from loop_amendment_history($1)`, [loop.loop_id]);
  return { loop, amended: r.rows.length > 0, amendments: r.rows };
}

// Next visible ref for a kind. Numeric part only, because that is the part the
// files increment; the prefix is the kind's own. Reads the MAX across every row
// including closed ones, so a number is never reused after a close.
async function nextLoopNumber(client, kind) {
  const prefix = kind === "team_loop" ? "T" : kind === "action_required" ? "A" : "";
  const r = await client.query(
    `select coalesce(max(nullif(regexp_replace(number, '\\D', '', 'g'), '')::int), 0) as m
       from loop_item where kind = $1`, [kind]);
  return `${prefix}${r.rows[0].m + 1}`;
}

async function nextRenderSeq(client, blockId) {
  const r = await client.query(
    "select coalesce(max(render_seq), 0) + 1 as n from loop_item where block_id = $1", [blockId]);
  return r.rows[0].n;
}

const FK = { deal: "deal_id", client: "client_id", lead: "lead_id", vendor: "vendor_id" };

// [ORDER 18] How many intro-graph edges `find` returns per query. A cap, not a
// page: find answers "who is this and who do we know through them", and the whole
// subgraph belongs to a graph verb nobody has ordered yet.
const CONNECTIONS_CAP = 12;

// [ORDER 32] who-do-we-know: the multi-hop half of the intro graph.
//
// DEPTH IS CAPPED AT 3 AND THE CAP IS NOT A PREFERENCE. A referral path four
// people long is not an asset — nobody makes that ask — and an uncapped
// recursive walk over a graph that will keep growing is a Worker timeout waiting
// for a busy night. The order says depth <= 3; this is where it is enforced, and
// a caller asking for more gets 3 rather than an error, because the answer at 3
// is still the right answer.
const WHO_MAX_DEPTH = 3;
const WHO_PATH_CAP = 25;

// [loop #132] How many RETIRED refs `find` lists per organisation group.
//
// A tombstone list is navigation, not an answer. 0059 consolidated 415 org rows
// into 306 survivors plus 109 tombstones and one name alone (Henry Schein) carries
// sixteen of them; the useful facts are "sixteen exist" and "here is where to look
// them up", not sixteen refs spending the whole payload. The COUNT is always exact
// and never truncated — only the ref list is capped, and the row says so.
const RETIRED_REF_CAP = 10;

// [loop #127] How many lead↔client links, and how many deals reached through them,
// `find` returns per search. Production carries 30 linked leads in total, so this is
// headroom rather than a real cut today; it exists so a future search on a common
// org name cannot spend the whole payload on traversal rows. A search that hits it
// says so in the note rather than truncating silently.
const LINK_CAP = 20;

// [loop #279] How many rulings find-precedent returns at once. A ruling's reasoning
// runs to paragraphs, so this is bounded by what a caller can actually read before
// deciding, not by what the query could return. Eight is the default; this is the
// ceiling a caller may raise it to.
const PRECEDENT_CAP = 25;

// THE NODE KEY IS THE REF, NOT THE NAME, and that choice is load-bearing.
// v_party_graph carries exactly one ref per party (0020's `distinct on`), so a
// ref identifies a party. A name does not: production holds one real lead
// twice — L-208 and C-155, the same human as two un-merged records — and
// joining paths on the name string would silently weld those two records into
// one node and invent hops that do not exist. Refs keep them separate, which is
// the truth of the book today, duplicate and all.
// (Example sanitized 2026-08-06, ORDER 42b — the original named the real lead.)
//
// The cost, stated rather than hidden: an edge whose endpoint carries NO ref
// cannot be walked. Today that is zero edges of 31. The verb counts them and
// returns the count as `edges_unwalkable_total` rather than dropping them
// quietly, so the day it stops being zero the answer says so instead of just
// getting smaller. That field is graph-wide; the per-target list beside it in
// the response is `unwalkable_edges`, and the two carry different scopes.
const WHO_EDGES = `
  select from_ref, from_name, kind, to_ref, to_name, note
    from v_party_graph
   where from_ref is not null and to_ref is not null`;

// [ORDER 18] The kind vocabulary lives in party_link_kind (0020) — the verb has no
// enum of its own any more, so widening it is a row a human adds, not a deploy.
// Validated against the table so an unknown kind is refused with the legal list
// rather than landing as a new de-facto vocabulary the way intro_sent did.
async function validateLinkKind(client, kind) {
  const r = await client.query(
    "select slug from party_link_kind where slug=$1", [kind]);
  if (r.rows.length) return kind;
  const all = await client.query("select slug from party_link_kind order by sort");
  throw new ToolError({ error: "unknown_kind", kind,
    valid: all.rows.map(x => x.slug),
    hint: "party_link_kind is the vocabulary; add a row there if a genuinely new kind is needed" });
}

// ---------- [0063] the counterparty-observation vocabularies ----------
//
// Same posture as validateLinkKind above and for the same reason: 0063 put
// submarket_condition and negotiation_claim_type in ref TABLES, with
// `falsifiable` and `derived` as columns rather than as lists hardcoded in a
// view, so widening either vocabulary is a row a human adds and not a deploy.
// A verb that carried its own enum would put a second copy of that list in a
// file only a deploy can change — the exact drift 0052/0053 had to unpick.

async function validateSubmarket(c, slug) {
  const r = await c.query("select slug from submarket_condition where slug=$1", [slug]);
  if (r.rows.length) return slug;
  const all = await c.query("select slug, label, tightness from submarket_condition order by sort");
  throw new ToolError({ error: "unknown_submarket_condition", got: slug, valid: all.rows,
    hint: "submarket_condition is a ref table (0063) — add a row there if a genuinely new " +
          "value is needed. Omitting it means NOT RECORDED, which is never a synonym for " +
          "'balanced'; do not pick one to fill the field." });
}

async function validateClaimType(c, slug) {
  const r = await c.query(
    "select slug, label, falsifiable, derived, reversal_test from negotiation_claim_type where slug=$1",
    [slug]);
  if (!r.rows.length) {
    const all = await c.query(
      "select slug, label, falsifiable, derived from negotiation_claim_type order by sort");
    throw new ToolError({ error: "unknown_claim_type", got: slug, valid: all.rows,
      hint: "negotiation_claim_type is the vocabulary (0063); widening it is a row a human adds" });
  }
  // The composite FK in 0063 makes a derived class physically unloggable. Catching
  // it here turns a foreign_key_violation into the sentence that says what to do.
  if (r.rows[0].derived)
    throw new ToolError({ error: "derived_claim_type", got: slug,
      reversal_test: r.rows[0].reversal_test,
      hint: "this claim class is already recorded elsewhere on the round, and two homes for " +
            "one fact is the 0045 fault. 'deadline' IS negotiation_round.expires_on — pass " +
            "expires_on on this same call instead." });
  return r.rows[0];
}

// vendor.stage is a FOREIGN KEY into vendor_stage(slug), and until now nothing
// checked it before the insert — so a plausible label (`prospect`, `Prospect`,
// `building`) came back as a bare "internal error" naming neither the field nor
// the options. Measured live 2026-08-10 re-creating Carla Adair: four calls
// died that way before the pattern was readable. Same failure class as
// new-lead's stage/lane and update-vendor's category_slug branch.
//
// Pre-validating rather than catching the FK matters: once the violation fires,
// the transaction is poisoned and cannot even run the query that would list the
// valid slugs, so the caller gets nothing to correct with.
//
// The slug is the FULL label, lowercased, every run of non-alphanumeric
// characters collapsed to one underscore — which is why `building_working_on_it`
// works and `building` does not. That is the rule the original import used to
// seed the table, so it holds for any stage added later. Both are returned so a
// caller who has the human label can map it without a second round trip.
async function validateVendorStage(c, slug) {
  const r = await c.query("select slug from vendor_stage where slug=$1", [slug]);
  if (r.rows.length) return slug;
  const all = await c.query("select slug, label from vendor_stage order by slug");
  throw new ToolError({ error: "unknown_vendor_stage", got: slug, valid: all.rows,
    hint: "stage is a foreign key into vendor_stage; pass one of the listed slugs, never " +
          "the label. The slug is the label lowercased with each run of non-alphanumeric " +
          "characters collapsed to a single underscore. A genuinely new stage is an INSERT " +
          "into vendor_stage by a human, never a guess." });
}

// 0063 lands as a migration Joe applies by hand, and this Worker deploys
// separately. Either order is possible, so the new arguments check for their own
// schema and say which half is missing instead of surfacing an undefined_column
// from inside a rolled-back transaction. Only runs when a new argument is used —
// an old-shaped record-counter call never pays for it.
async function require0063(c) {
  const r = await c.query(
    `select to_regclass('public.negotiation_claim') is not null as claims,
            exists (select 1 from information_schema.columns
                     where table_schema='public' and table_name='negotiation_round'
                       and column_name='submarket_condition') as submarket`);
  if (r.rows[0].claims && r.rows[0].submarket) return;
  throw new ToolError({ error: "migration_not_applied", migration: "0063_counterparty_observation",
    present: r.rows[0],
    hint: "submarket_condition and claims[] need migration 0063. Apply it " +
          "(`~/carr-system/run.sh migrate --apply --yes`) and retry; every other argument on " +
          "this verb works without it." });
}

// ---------- [0066] the marketing lane's resolvers and gates ----------
// Same split-deploy discipline require0063 established, and it matters more here:
// all four marketing verbs are brand new, so a Worker deployed ahead of the
// migration would fail on undefined_table from inside a rolled-back transaction
// and the caller would read it as "the verb is broken" rather than "the schema
// has not landed". Every marketing verb calls this FIRST, before it touches
// anything.
async function require0066(c) {
  const r = await c.query(
    `select to_regclass('public.marketing_subject')       is not null as subjects,
            to_regclass('public.placement_measurement')   is not null as attempts,
            to_regclass('public.v_campaign_scorecard')    is not null as scorecard,
            to_regclass('public.v_placement_measurement') is not null as coverage,
            exists (select 1 from information_schema.columns
                     where table_schema='public' and table_name='campaign'
                       and column_name='success_criterion') as campaign_shape`);
  const s = r.rows[0];
  if (s.subjects && s.attempts && s.scorecard && s.coverage && s.campaign_shape) return;
  throw new ToolError({ error: "migration_not_applied",
    migration: "0066_marketing_campaign_and_measurement", present: s,
    hint: "the marketing verbs need 0066 (campaign window/criterion/verdict columns, " +
          "marketing_subject, placement_measurement, and the measurement views). Apply it " +
          "(`~/carr-system/run.sh migrate --apply --yes`) and retry. NOTHING was written." });
}

// ---------- code subjects (0101, loop #211) ----------

// THE ONE REPO. CARR CLAUDE.md rev 10: "the code lives in ONE repo:
// jbookout/carr-system". A caller who writes `commit:<sha>` and names no repo means
// this one, because there is no other. The schema deliberately does NOT hard-code
// it — a second repo is a real possibility and a schema forbidding it would be a
// lie — so the default lives here, at the caller's edge, where it is a convenience
// rather than a constraint.
const DEFAULT_REPO = "jbookout/carr-system";
const SHA_RE = /^[0-9a-f]{7,40}$/i;
const REPO_RE = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/;

// Parse the forms a reviewing seat actually writes, in the order it writes them:
//   commit:f7abde7               -> the one repo at that commit
//   jbookout/carr-system@f7abde7 -> any repo at that commit
//   repo:jbookout/carr-system    -> the repo itself (a finding about the codebase,
//   jbookout/carr-system            not about one change)
// Returns { repo, sha } with sha null for a repo-level subject.
function parseCodeRef(raw) {
  const s = String(raw || "").trim();
  if (!s) return null;
  let body = s;
  let forcedKind = null;
  const m = /^(commit|repo)\s*:\s*(.*)$/i.exec(s);
  if (m) { forcedKind = m[1].toLowerCase(); body = m[2].trim(); }
  let repo = null, sha = null;
  const at = body.lastIndexOf("@");
  if (at > 0) {
    repo = body.slice(0, at).trim();
    sha = body.slice(at + 1).trim();
  } else if (SHA_RE.test(body)) {
    // A bare sha only reads as a sha when the caller said `commit:`. Otherwise a
    // seven-character word like 'deadbee' would silently become a commit.
    if (forcedKind !== "commit") return null;
    repo = DEFAULT_REPO; sha = body;
  } else {
    repo = body;
  }
  if (!repo) return null;
  repo = repo.toLowerCase();
  if (!REPO_RE.test(repo)) return null;
  if (sha !== null) {
    if (!SHA_RE.test(sha)) return null;
    sha = sha.toLowerCase();
  }
  if (forcedKind === "repo") sha = null;
  if (forcedKind === "commit" && sha === null) return null;
  return { repo, sha };
}

async function require0101(c) {
  const r = await c.query(
    `select to_regclass('public.code_subject')  is not null as registry,
            to_regclass('public.v_code_finding') is not null as read_side`);
  const s = r.rows[0];
  if (s.registry && s.read_side) return;
  throw new ToolError({ error: "migration_not_applied",
    migration: "0101_code_review_subject", present: s,
    hint: "filing a finding against code needs 0101 (code_subject plus the repo/commit " +
          "branches on record_flag and v_record_flag_subject). Apply it " +
          "(`~/carr-system/run.sh migrate --apply --yes`) and retry. NOTHING was written." });
}

// MINTED ON DEMAND, and that is the deliberate difference from marketing_subject.
// 0066 refuses to mint because a typo'd slug would invent a pillar and pollute a
// taxonomy. A commit sha invents nothing: it either names an object in the repo or
// it does not, and the CHECK constraints in 0101 are what stop a sentence becoming
// a subject. Requiring a human to pre-register every reviewed commit would put a
// gate in front of the one thing this fix exists to make automatic.
async function resolveCodeSubject(c, ref, actorId) {
  const parsed = parseCodeRef(ref);
  if (!parsed) throw new ToolError({ error: "code_subject_unparseable", got: String(ref || "").slice(0, 80),
    hint: "write it the way it is written everywhere else: 'commit:<sha>' for the one repo, " +
          "'owner/name@<sha>' for another repo, or 'repo:owner/name' for the codebase itself. " +
          "A sha is 7-40 hex characters." });
  await require0101(c);
  const { repo, sha } = parsed;
  const found = await c.query(
    "select id from code_subject where repo=$1 and coalesce(commit_sha,'')=coalesce($2,'')",
    [repo, sha]);
  if (found.rows.length) return { type: sha ? "commit" : "repo", id: found.rows[0].id, repo, sha };
  const ins = await c.query(
    `insert into code_subject (repo, commit_sha, created_by) values ($1,$2,$3)
     on conflict (repo, coalesce(commit_sha, '')) do nothing returning id`,
    [repo, sha, actorId || null]);
  if (ins.rows.length) return { type: sha ? "commit" : "repo", id: ins.rows[0].id, repo, sha };
  // Lost the race to a concurrent write — read the winner rather than failing.
  const again = await c.query(
    "select id from code_subject where repo=$1 and coalesce(commit_sha,'')=coalesce($2,'')",
    [repo, sha]);
  if (again.rows.length) return { type: sha ? "commit" : "repo", id: again.rows[0].id, repo, sha };
  throw new ToolError({ error: "code_subject_not_minted", repo, commit_sha: sha,
    hint: "the registry accepted neither an insert nor a read for this repo/sha — nothing was written" });
}

// A campaign by uuid or by name. Name matching is normalised the same way
// campaign_name_uniq normalises it, so the verb and the index agree about what
// "the same campaign" means — 0059's whole lesson was two layers disagreeing
// about identity and minting 415 rows for 306 organisations.
async function resolveCampaign(c, ref) {
  const raw = String(ref || "").trim();
  if (!raw) throw new ToolError({ error: "campaign_required" });
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(raw)) {
    const r = await c.query("select * from campaign where id=$1", [raw]);
    if (r.rows.length) return r.rows[0];
    throw new ToolError({ error: "campaign_not_found", campaign: raw });
  }
  const r = await c.query(
    "select * from campaign where lower(btrim(name)) = lower(btrim($1))", [raw]);
  if (r.rows.length) return r.rows[0];
  const near = await c.query(
    "select name, status from campaign order by created_at desc limit 5");
  throw new ToolError({ error: "campaign_not_found", campaign: raw,
    recent_campaigns: near.rows,
    hint: near.rows.length
      ? "match the name exactly, or pass the campaign uuid"
      : "no campaign exists yet — open-campaign is what creates one. Do NOT attach content " +
        "to a campaign that was never stated; the whole point of the object is that the " +
        "objective was written down BEFORE the results came in." });
}

// A placement by uuid, live URL, or Blotato external_id. The URL and the id are
// what a caller actually holds: the marketing seat's own campaign-proposal block
// names content "by placement URL or Blotato id" because those are the only
// handles that appear in the published log. Measured 2026-08-02: all 89
// placements carry a non-null external_id and url, and all 89 of each are
// distinct, so both are usable as keys and neither can silently collide.
// LinkedIn hands a human a DIFFERENT id than the one we store, and there is no
// way to convert between them. Blotato reports `postUrl` in the share/ugcPost
// form (urn:li:share:… / urn:li:ugcPost:…) and that is what `placement.url`
// holds. Every LinkedIn surface a person can reach — the recent-activity feed,
// the per-post analytics link, the whole rendered DOM — shows only the ACTIVITY
// urn. The two ids are minted for the same post milliseconds apart and are not
// derivable from each other:
//     2026-08-05  stored ugcPost 7490800344260841472  activity 7490800345598779392
//     2026-08-03  stored share   7490064185725280256  activity 7490064188413870080
// Measured 2026-08-19: this stranded four readings in one week, including the
// best-performing post on any platform, and it would have stranded four or five
// more every week for as long as it stood.
//
// Both ids are Snowflake-shaped, so the high bits ARE the publish time
// (ms = id >> 22). That gives an exact, checkable bridge rather than a guess.
// Measured against all four stranded posts on 2026-08-19: each activity urn
// decoded to within 0.1 SECONDS of a real linkedin placement's live_at, while
// the next-nearest linkedin placement sat ~170,000 seconds (about two days)
// away. A ±90s window therefore separates the true match from its nearest rival
// by more than three orders of magnitude. If that ever stops holding — two
// LinkedIn posts inside 90 seconds — this REFUSES as ambiguous rather than
// guessing, which is the same posture as every other handle here.
const LINKEDIN_ACTIVITY_URN = /urn:li:activity:(\d{6,25})\b/i;
const LINKEDIN_SNOWFLAKE_EPOCH_SHIFT = 22n;
const LINKEDIN_MATCH_WINDOW_SECONDS = 90;

export function linkedInActivityPublishedAt(ref) {
  const m = LINKEDIN_ACTIVITY_URN.exec(ref);
  if (!m) return null;
  const ms = BigInt(m[1]) >> LINKEDIN_SNOWFLAKE_EPOCH_SHIFT;
  const at = new Date(Number(ms));
  // A decode that lands outside plausible range means the id is not what we
  // think it is; say nothing rather than match on nonsense.
  if (!Number.isFinite(at.getTime())) return null;
  if (at.getUTCFullYear() < 2010 || at.getUTCFullYear() > 2100) return null;
  return at;
}

async function resolvePlacement(c, ref) {
  const raw = String(ref || "").trim();
  if (!raw) throw new ToolError({ error: "placement_required" });
  const by = async (sql, v) => (await c.query(sql, [v])).rows;
  let rows = [];
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(raw))
    rows = await by("select * from placement where id=$1", raw);
  if (!rows.length && /^https?:\/\//i.test(raw))
    rows = await by("select * from placement where url = $1", raw);
  if (!rows.length) rows = await by("select * from placement where external_id = $1", raw);
  if (!rows.length) {
    const publishedAt = linkedInActivityPublishedAt(raw);
    if (publishedAt) {
      rows = (await c.query(
        "select * from placement where platform = 'linkedin' and live_at is not null " +
        "and live_at between $1::timestamptz - make_interval(secs => $2) " +
        "                and $1::timestamptz + make_interval(secs => $2)",
        [publishedAt.toISOString(), LINKEDIN_MATCH_WINDOW_SECONDS])).rows;
      if (rows.length === 1) {
        // Say so out loud. A handle that matched on a derived timestamp rather
        // than on a stored key is a different kind of certainty, and the caller
        // recording a number against it should see which one they got.
        return Object.assign({}, rows[0], {
          _resolved_by: "linkedin_activity_urn_publish_time",
          _resolved_note:
            `matched the linkedin activity urn to placement.live_at within ` +
            `${LINKEDIN_MATCH_WINDOW_SECONDS}s (LinkedIn never exposes the stored ` +
            `share/ugcPost urn, so the publish time encoded in the activity id is ` +
            `the only bridge)`,
        });
      }
      if (rows.length > 1) throw new ToolError({ error: "ambiguous_placement", ref: raw,
        candidates: rows.map(r => ({ placement_id: r.id, platform: r.platform, url: r.url,
                                     live_at: r.live_at })),
        hint: "two or more linkedin placements published within " +
              `${LINKEDIN_MATCH_WINDOW_SECONDS}s of this activity urn, so the publish ` +
              "time cannot identify one. Pass the stored post URL or the Blotato id." });
    }
  }
  if (rows.length === 1) return rows[0];
  if (rows.length > 1) throw new ToolError({ error: "ambiguous_placement", ref: raw,
    candidates: rows.map(r => ({ placement_id: r.id, platform: r.platform, url: r.url })),
    hint: "this handle resolves to more than one placement — a data fault; surface it" });
  throw new ToolError({ error: "placement_not_found", ref: raw,
    hint: "pass the live post URL, the Blotato post id, or the placement uuid — and for " +
          "LinkedIn, the activity urn or any URL containing it works too, matched on the " +
          "publish time encoded in the id. Placements are created by " +
          "pipelines/pull_placement_metrics.py when a post publishes — if the post is live " +
          "and this fails, the pull has not run since it published. Do NOT invent a " +
          "placement to hang a number on." });
}

async function livePlatformSlugs(c) {
  const r = await c.query(
    "select slug from marketing_subject where subject_type='platform' and retired_at is null order by slug");
  return r.rows.map(x => x.slug);
}

// [ORDER 34] ref -> party.id, REFS ONLY. A name here is an error by design:
// production holds the same human twice un-merged (L-208 / C-155), and a name
// match would weld them. Same rule that makes who-do-we-know's node key the ref.
async function resolvePartyByRef(c, ref) {
  if (!/^[LCVT]-/i.test(ref || ""))
    throw new ToolError({ error: "ref_required", got: ref || null,
      hint: "links take refs only (L-### / C-### / V-XXX-### / T-###), never names; use find first" });
  // [ORDER 34 review, blocker 2] Follows party.merged_into to the SURVIVOR
  // (A3: reads follow merge pointers) and excludes client tombstones — an edge
  // written to a merged-away party would defeat the merge silently. And more
  // than one live match for a ref is a data fault to surface, never rows[0].
  const r = await c.query(
    `select distinct coalesce(p.merged_into, p.id) as party_id
       from (
         select c2.party_id, c2.roster_ref  as ref from client c2 where c2.merged_into is null
         union all select v.party_id, v.vendor_ref   from vendor v
         union all select l.party_id, l.registry_ref from lead l
       ) x
       join party p on p.id = x.party_id
      where x.ref ilike $1`, [ref]);
  if (!r.rows.length) throw new ToolError({ error: "ref_not_found", ref,
    hint: "no live record carries this ref, or it has no party row; use find" });
  if (r.rows.length > 1) throw new ToolError({ error: "ambiguous_ref", ref,
    candidates: r.rows.map(x => x.party_id),
    hint: "this ref string resolves to more than one live party — a data fault; surface to the human" });
  return r.rows[0].party_id;
}

// [ORDER 34] shared edge-writer for log-activity links[] — link-parties' exact
// upsert semantics (conflict returns the existing edge, no event row) so touch
// and edge are one atomic capture inside one envelope.
async function writeLinks(c, actor, links, idempotencyKey) {
  if (links.length > 10)
    throw new ToolError({ error: "too_many_links", count: links.length, hint: "max 10 per call" });
  const out = [];
  for (const ln of links) {
    const kind = await validateLinkKind(c, ln.kind);
    const fromId = await resolvePartyByRef(c, ln.from_ref);
    const toId = await resolvePartyByRef(c, ln.to_ref);
    if (fromId === toId)
      throw new ToolError({ error: "self_link", ref: ln.from_ref,
        hint: "both refs resolve to the same party" });
    const ins = await c.query(
      `insert into party_link (from_party, to_party, kind, note, source, created_by)
       values ($1,$2,$3,$4,'stated',$5)
       on conflict (from_party, to_party, kind) do nothing returning id`,
      [fromId, toId, kind, ln.note || null, actor.id]);
    if (ins.rows.length) {
      await writeEvent(c, actor, "log-activity:link", "party", fromId,
        { new: { kind, to_ref: ln.to_ref }, idempotency_key: idempotencyKey });
      out.push({ from_ref: ln.from_ref, to_ref: ln.to_ref, kind, link_id: ins.rows[0].id, existing: false });
    } else {
      const cur = await c.query(
        "select id from party_link where from_party=$1 and to_party=$2 and kind=$3",
        [fromId, toId, kind]);
      out.push({ from_ref: ln.from_ref, to_ref: ln.to_ref, kind, link_id: cur.rows[0].id, existing: true });
    }
  }
  return out;
}

// ---------- [ORDER 13] the document factory's resolver ----------
// A doc_template's field_map is DATA, not code: every slot names the template
// address it writes and the record path it reads. This resolves that map against
// one deal's records and returns the edits the local fill engine applies, plus
// the OWED list. The two properties that matter, both structural rather than
// remembered: a slot whose record field is missing is written as an explicit
// OWED marker (never left showing the template's own placeholder number, which
// is how a $20.00 asking rate would otherwise walk into a client's inbox), and
// no value is ever derived from prose.

function fmtValue(v, fmt) {
  if (v === null || v === undefined || v === "") return null;
  const num = typeof v === "number" ? v : Number(v);
  switch (fmt) {
    case "sf":   return isNaN(num) ? String(v) : Math.round(num).toLocaleString("en-US");
    case "usd":  return isNaN(num) ? String(v) : "$" + Math.round(num).toLocaleString("en-US");
    case "usd2": return isNaN(num) ? String(v) : num.toFixed(2);
    case "months": {
      if (isNaN(num)) return String(v);
      const n = num % 1 === 0 ? num.toFixed(0) : String(num);
      return `${n} month${num === 1 ? "" : "s"}`;
    }
    case "term_years":
      if (isNaN(num)) return String(v);
      return num % 12 === 0 ? `${num / 12} year${num === 12 ? "" : "s"}` : `${num} months`;
    case "term_years_num": return isNaN(num) ? String(v) : String(num / 12);
    case "date_long": {
      const d = new Date(String(v) + (String(v).length === 10 ? "T00:00:00Z" : ""));
      if (isNaN(d)) return String(v);
      return d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" });
    }
    case "date_short": {
      const d = new Date(String(v) + (String(v).length === 10 ? "T00:00:00Z" : ""));
      if (isNaN(d)) return String(v);
      const p = n => String(n).padStart(2, "0");
      return `${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}-${d.getUTCFullYear()}`;
    }
    case "rate": {                                   // {amount, basis} — never a bare number
      if (!v.amount || !v.basis) return null;
      const a = "$" + Number(v.amount).toFixed(2);
      if (v.basis === "usd_sf_yr") return `${a} per RSF`;
      if (v.basis === "usd_sf_mo") return `${a} per RSF per month`;
      if (v.basis === "usd_mo_gross") return `${a} per month, gross`;
      if (v.basis === "usd_yr_gross") return `${a} per year, gross`;
      return `${a} (${v.basis})`;
    }
    case "ti": {
      if (!v.amount || !v.basis) return null;
      const a = "$" + Number(v.amount).toLocaleString("en-US", { maximumFractionDigits: 2 });
      return v.basis === "usd_sf" ? `${a} per RSF` : `${a} total`;
    }
    default: return String(v);
  }
}

function bagGet(bag, path) {
  return path.split(".").reduce((o, k) => (o === null || o === undefined ? o : o[k]), bag);
}

// Segment rendering. A required segment that resolves to nothing owes the WHOLE
// slot. An `optional` one drops itself, and drops the preceding literal when
// that literal carries `with_next` — so "Practice, DDS" degrades to "Practice",
// not to "Practice, ". A missing credential must not cost the tenant its name.
function renderSegments(segments, bag) {
  const out = [];
  const missing = [];
  const soft = [];
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    if (seg.literal !== undefined) {
      if (seg.with_next) {
        const nxt = segments[i + 1];
        const nv = nxt && nxt.from ? fmtValue(bagGet(bag, nxt.from), nxt.format) : null;
        if (nxt && nxt.optional && (nv === null || nv === undefined)) continue;
      }
      out.push(seg.literal);
      continue;
    }
    const raw = bagGet(bag, seg.from);
    const val = fmtValue(raw, seg.format);
    if (val === null || val === undefined) {
      if (seg.optional) { soft.push(seg.from); continue; }
      missing.push(seg.from);
      continue;
    }
    out.push(val);
  }
  return { text: out.join(""), missing, soft };
}

function resolveSlot(name, slot, bag, options) {
  // returns { edit?, owed?, carried?, partial? }
  if (slot.kind === "carry")
    return { carried: { slot: name, label: slot.label, where: slot.where, note: slot.owed_note } };

  if (slot.kind === "option") {
    const chosen = options[name];
    let text = null;
    if (chosen !== undefined && chosen !== null) {
      text = typeof chosen === "number" ? slot.choices[chosen] : String(chosen);
      if (text === undefined)
        throw new ToolError({ error: "bad_option", slot: name, given: chosen, choices: slot.choices });
    } else if (slot.from && slot.value_map) {
      const src = bagGet(bag, slot.from);
      if (src !== null && src !== undefined) text = slot.value_map[String(src)] || null;
    }
    if (text === null && slot.default !== null && slot.default !== undefined)
      text = slot.choices[slot.default];
    if (text === null || text === undefined)
      return { owed: { slot: name, label: slot.label, where: slot.where, kind: "option",
                       wanted: slot.from ? `option, or ${slot.from}` : "an explicit option",
                       choices: slot.choices, why: slot.owed_note } };
    return { edit: { where: slot.where, text, slot: name } };
  }

  const segs = slot.segments || (slot.from ? [{ from: slot.from, format: slot.format }] : []);
  const r = renderSegments(segs, bag);
  if (r.missing.length)
    return { owed: { slot: name, label: slot.label, where: slot.where, kind: "record",
                     wanted: r.missing.join(", "), why: slot.owed_note } };
  const res = { edit: { where: slot.where, text: r.text, slot: name } };
  if (r.soft.length)
    res.partial = { slot: name, label: slot.label, where: slot.where, kind: "record_optional",
                    wanted: r.soft.join(", "), why: slot.owed_note };
  return res;
}

// The OWED marker written into the document itself. Visible, unmistakable, and
// impossible to confuse with a value: the point is that Joe opening the draft
// sees exactly what the record layer could not answer.
//
// NO EM-DASH, and that is not a style preference. The first version of this
// marker read `[OWED — label: field]`, and the writing lint returned 18 HARD
// findings on the very first C-112 draft, every one of them the factory's own
// marker rather than anything in CARR's template. A gate that the producer
// itself trips is a gate that gets switched off. Found by running the lint, not
// by reasoning about it.
function owedMarker(o) {
  return `[OWED: ${o.label} (needs ${o.wanted})]`;
}

async function buildRecordBag(c, dealId, clientId) {
  const bag = { today: new Date().toISOString().slice(0, 10) };
  const d = (await c.query(
    `select d.id, d.name, d.deal_type, d.phase, d.segment,
            c.roster_ref, c.vertical, c.subtype, c.contact_label,
            p.name as party_name, p.city as party_city, p.state as party_state,
            o.name as org_name
       from deal d join client c on c.id=d.client_id
       join party p on p.id=c.party_id
       left join party o on o.id=p.org_id
      where d.id=$1`, [dealId])).rows[0];
  if (!d) throw new ToolError({ error: "deal_not_found", deal_id: dealId });
  bag.deal = { name: d.name, type: d.deal_type, phase: d.phase, segment: d.segment };
  bag.client = { ref: d.roster_ref, display_name: d.party_name, org_name: d.org_name,
                 city: d.party_city, state: d.party_state, vertical: d.vertical,
                 subtype: d.subtype, contact_label: d.contact_label };

  // The signing agent is the deal's CURRENT lead participant, not the session's
  // actor: a document Dell prepares on Joe's deal still signs Joe.
  //
  // [ORDER 24] phone and email come off the actor row. They used to be hardcoded
  // null here, so the signature slots owed on every draft with a "no phone column"
  // reason that stopped being true the moment the column landed. Empty string is
  // normalized to null deliberately: a blank is missing, and a slot that owes is
  // worth more than a signature line rendered with nothing in it.
  const ag = (await c.query(
    `select a.slug, a.display_name, a.email, a.phone from deal_participant dp
       join actor a on a.id=dp.actor_id
      where dp.deal_id=$1 and dp.role='lead' and dp.to_at is null limit 1`, [dealId])).rows[0];
  bag.agent = ag ? { slug: ag.slug, display_name: ag.display_name,
                     email: (ag.email && String(ag.email).trim()) || null,
                     phone: fmtPhoneUS(ag.phone) }
                 : { slug: null, display_name: null, email: null, phone: null };

  const ct = (await c.query(
    `select p.name from deal_participant dp join party p on p.id=dp.party_id
      where dp.deal_id=$1 and dp.role='client_contact' and dp.to_at is null limit 1`, [dealId])).rows[0];
  if (ct) {
    const parts = String(ct.name).trim().split(/\s+/);
    bag.contact = { name: ct.name, first_name: parts[0] || null,
                    last_name: parts.length > 1 ? parts[parts.length - 1] : null };
  } else bag.contact = { name: null, first_name: null, last_name: null };

  const pr = (await c.query(
    `select pr.id, pr.label, b.address, b.city, b.state, s.suite,
            s.area_amount, s.area_basis
       from premises pr
       left join premises_space ps on ps.premises_id=pr.id
       left join space s on s.id=ps.space_id
       left join building b on b.id=s.building_id
      where pr.deal_id=$1 order by pr.created_at limit 1`, [dealId])).rows[0];
  bag.premises = pr
    ? { label: pr.label, address: pr.address, suite: pr.suite, city: pr.city,
        state: pr.state, area_sf: pr.area_basis === "sf" ? pr.area_amount : null }
    : { label: null, address: null, suite: null, city: null, state: null, area_sf: null };
  bag.premises_list = (await c.query(
    "select id from premises where deal_id=$1 order by created_at", [dealId])).rows;

  // Newest OUR-side round. tenant/buyer is our paper; the landlord's counter is
  // not what our LOI says, so side is not a caller choice here.
  const rnd = (await c.query(
    `select * from negotiation_round
      where deal_id=$1 and side in ('tenant','buyer')
      order by round_no desc, proposed_on desc limit 1`, [dealId])).rows[0];
  bag.round = rnd
    ? { round_no: rnd.round_no, side: rnd.side, proposed_on: rnd.proposed_on,
        rate: { amount: rnd.rate_amount, basis: rnd.rate_basis },
        rate_norm_sf_yr: rnd.rate_norm_sf_yr,
        ti: { amount: rnd.ti_amount, basis: rnd.ti_basis },
        free_rent_months: rnd.free_rent_months, term_months: rnd.term_months,
        options_note: rnd.options_note, escalator: rnd.escalator,
        opex_note: rnd.opex_note, expires_on: rnd.expires_on, note: rnd.note,
        purchase_price: null }
    : { rate: {}, ti: {} };

  const ls = (await c.query(
    "select * from lease where deal_id=$1 order by created_at desc limit 1", [dealId])).rows[0];
  bag.lease = ls
    ? { executed_on: ls.executed_on, commencement_on: ls.commencement_on,
        expiration_on: ls.expiration_on, term_months: ls.term_months,
        rate: { amount: ls.rate_amount, basis: ls.rate_basis },
        escalator: ls.escalator, ti_amount: ls.ti_amount,
        free_rent_months: ls.free_rent_months, options_note: ls.options_note,
        opex_structure: ls.opex_structure }
    : { rate: {} };

  // Structurally empty today and named so the map can point at it honestly:
  // nothing in the schema links a lender to a deal.
  bag.financing = { lender: null };
  bag.tenant = { credential: null };
  return bag;
}

// ---------- rule id resolution (loop #261) ----------
//
// THE DEFECT THIS CLOSES. Every rule verb took `rule_id` and passed it straight
// into SQL as a uuid. But the ONLY rule id a session can see is the 8-character
// short form: that is what the gist index prints, what standing-context returns,
// and what every rule cross-reference in the doctrine is written in. Passing the
// form the system itself publishes made Postgres reject it as a malformed uuid,
// which surfaced as a bare "internal error" — a validation failure wearing the
// costume of an outage. Measured 2026-08-09 by hitting it live: `teach` with
// supersedes:'179be4b8' died that way, and the fix cost a database tap to find
// the full uuid by hand.
//
// WHY IT MATTERS MORE FOR DELL THAN FOR JOE. Joe has a db-tap habit and can
// resolve a short id himself. Dell does not, so following a rule pointer — the
// thing the whole gist index exists to enable — is not awkward for him, it is
// impossible. A pointer nobody can follow is not a pointer.
//
// AMBIGUITY IS REPORTED, NEVER GUESSED. A prefix that matches two rules returns
// the candidates rather than picking one, because silently activating or
// retiring the wrong binding rule is worse than any error message.
// V5-A02: the named refusals ops.record_rule_enforcement_fallback raises
// (migration 0721). Each is mapped to a ToolError of the same name.
const RULE_ENFORCEMENT_FALLBACK_REFUSALS = Object.freeze(new Set([
  "rule_enforcement_fallback_requires_joe_authority",
  "rule_enforcement_fallback_kind_unknown",
  "rule_enforcement_fallback_fields_required",
  "rule_enforcement_fallback_rule_not_found",
  "rule_enforcement_fallback_actor_unregistered",
  "rule_enforcement_fallback_idempotency_conflict",
  "rule_enforcement_fallback_already_recorded",
  "rule_enforcement_fallback_receipts_append_only",
]));

async function resolveRuleId(c, value, field = "rule_id") {
  const raw = String(value || "").trim();
  if (!raw) throw new ToolError({ error: "rule_id_required", field });

  // A full uuid is used as-is: the fast path stays exactly what it was.
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(raw)) return raw;

  // Anything else must look like a hex prefix before it reaches SQL. This is the
  // check whose absence turned a typo into "internal error".
  if (!/^[0-9a-f]{4,}$/i.test(raw))
    throw new ToolError({ error: "rule_id_malformed", field, got: raw,
      hint: "a rule id is either the full 36-character uuid or the 8-character short form the gist index prints, e.g. '179be4b8'" });

  const m = await c.query(
    "select id, status, left(statement, 70) as gist from rule where id::text like $1 || '%' order by id",
    [raw.toLowerCase()]);
  if (!m.rows.length)
    throw new ToolError({ error: "rule_not_found", field, got: raw,
      hint: "no rule id begins with that prefix — check the gist index, and note a RETIRED rule still resolves" });
  if (m.rows.length > 1)
    throw new ToolError({ error: "ambiguous_rule_id", field, got: raw,
      candidates: m.rows.map(r => ({ rule_id: r.id, status: r.status, gist: r.gist })),
      hint: "that prefix matches more than one rule; pass more characters or the full uuid" });
  return m.rows[0].id;
}

// ---------- following a decision pointer the system itself printed ----------
//
// SAME DEFECT CLASS AS resolveRuleId ABOVE, and the same day (defect 69fb49b1,
// 2026-08-22). `update-decision` took `decision_id` straight into `where
// subject_id = $1`, a uuid column, so the EIGHT-CHARACTER form this system
// prints everywhere — loop source_notes cite "decisions f58ffba8, 91020f79",
// log-decision's own envelope, decision-history's rendered index — could not be
// passed back into the verb that corrects them. It died as 22P02, which until
// pull request 465 surfaced as a bare "internal error" naming nothing.
//
// A pointer nobody can follow is not a pointer. That reasoning is written out at
// length above resolveRuleId and applies here unchanged; the only reason this is
// a second function rather than one shared one is that rules and decisions live
// in different tables and a prefix must be matched against the right one.
//
// AMBIGUITY IS REPORTED, NEVER GUESSED, for a sharper reason than the rules
// case: amending the wrong decision rewrites a settled ruling's stated
// rationale, and the entry it overwrote gives no sign it was ever different.
async function resolveDecisionId(c, value, field = "decision_id") {
  const raw = String(value || "").trim();
  if (!raw) throw new ToolError({ error: "decision_id_required", field });

  if (UUID_RE.test(raw)) return raw;

  if (!/^[0-9a-f]{4,}$/i.test(raw))
    throw new ToolError({ error: "decision_id_malformed", field, got: raw,
      hint: "a decision id is either the full 36-character uuid or the 8-character short " +
            "form this system prints, e.g. 'f58ffba8'" });

  const m = await c.query(
    `select distinct on (subject_id) subject_id::text as id,
            left(coalesce(new_value->>'title', '(untitled)'), 70) as title,
            recorded_at
       from event
      where verb = 'log-decision' and subject_id::text like $1 || '%'
      order by subject_id, recorded_at`, [raw.toLowerCase()]);
  if (!m.rows.length)
    throw new ToolError({ error: "decision_not_found", field, got: raw,
      hint: "no decision id begins with that prefix — check decision-history, and note " +
            "that the id log-decision returns is the decision_id, not the event_id" });
  if (m.rows.length > 1)
    throw new ToolError({ error: "ambiguous_decision_id", field, got: raw,
      candidates: m.rows.map(r => ({ decision_id: r.id, title: r.title })),
      hint: "that prefix matches more than one decision; pass more characters or the full uuid" });
  return m.rows[0].id;
}


// ---------- the deferral gate (add-loop; migration 0081, Joe 2026-08-09) ----------
//
// Every class below is a state of the world OUTSIDE the session. That is the
// whole design: there is no cell to write "later" into, so a session either
// names something real or discovers it can do the work. Kept in one place
// because the migration's check constraint and this list are the same contract
// (rule a8c55a47 — a manual path and an automated path that do the same job must
// be the same code); the DB is the backstop, this is the surface that explains.
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const BLOCKER_CLASSES = Object.freeze([
  "human_only",     // needs Joe or Dell in person: a call, a signature, a site visit, a login only he holds
  "counterparty",   // waiting on someone outside: landlord, broker, client, vendor — named
  "ruling",         // needs Joe's decision, and the question is stated
  "external_event", // a dated event must arrive first, and the date is named
  "other_lane",     // depends on another lane's in-flight deliverable, named
  "capability",     // a credential, gate or verb this session cannot hold, named (rule 1b8e7f43)
]);

// loop_item.marker's own contract (migration 0024): 'check (marker in (...))'.
// THE BUG THIS LIST FIXES (found 2026-08-13, decision 7026246b): add-loop's
// inputSchema had always documented this enum, but inputSchema is advisory
// only — the MCP transport never validates a call's arguments against it
// (mcp.js's callTool passes `rpc.params?.arguments` straight to the handler),
// so an illegal value like 'wrench' sailed past the JS layer entirely and hit
// the DB's CHECK constraint raw, which the generic top-level catch then
// flattened into a bare {"error":"internal error"} naming neither the field
// nor the allowed values. Same pattern BLOCKER_CLASSES fixed for `blocker`
// above; marker gets the identical up-front-validation treatment in add-loop.
const LOOP_MARKERS = Object.freeze(["bell", "dated", "decision", "none"]);

// THE THIRD LEG OF THAT SAME DEFECT (found 2026-08-14): kind is documented in
// add-loop's inputSchema as a REQUIRED enum, but inputSchema is advisory (see
// LOOP_MARKERS above) and nothing in the handler ever checked it. A call that
// simply OMITTED kind sailed through: the placement ternary fell through to
// section "open" (undefined is not "idea" and not "open_loop"), the loop_block
// lookup ran with kind=NULL and matched nothing, and the caller got
// {"error":"no_block","section":"open"} whose hint blamed "the loop importer"
// — which had in fact run, for every kind, two weeks earlier. The thrown
// payload even carried `kind: args.kind`, but undefined never survives JSON
// serialization, so the one field that would have named the real mistake was
// invisible. Validated up front like marker/domain/blocker, so a missing or
// misspelled kind fails as itself instead of as a phantom importer failure.
const LOOP_KINDS = Object.freeze(["open_loop", "team_loop", "action_required", "idea"]);

// The detail field is where a determined session would smuggle the deferral back
// in, so the phrases that mean "not now, no reason" are refused by name. This is
// not a quality bar on writing; it is a check that the sentence names a WHO or a
// WHAT rather than a mood about time. Anchored loosely because the failure mode
// is a whole detail that reads "revisit later", not a passing mention of a word.
const VAGUE_BLOCKER_RE =
  /\b(?:later|someday|some day|eventually|when (?:there(?:'s| is) )?(?:more )?time|when time (?:permits|allows)|time permitting|revisit|circle back|down the (?:road|line)|at some point|in (?:the )?future|future session|next session|tbd|to be determined|n\/?a|low priority|nice to have|opportunistically|as time allows|no rush|whenever)\b/i;

// ── THE OWNERSHIP GATE (Joe 2026-08-10) ────────────────────────────────────
// Joe asked why the backlog never falls. The measured answer: intake is
// autonomous and the drain is not. Audits, IT sweeps, council reviews and
// research waves all OPEN loops on their own initiative — 34 of the 108 August
// loops still open came straight out of one — while nothing CLOSES one unless a
// human orders it. Stripping the purge Joe ordered on 2026-08-09 (68 closures
// in a day, 48 inside one hour), the baseline was about 4 closures a day
// against 21 opened.
//
// THE FIELD THAT ENFORCES THAT ASYMMETRY IS THIS ONE. 110 of the 150 open work
// loops were owned jointly — "Joe/Claude", "Joe + Dell", "Joe→Dell" — and only
// FOUR were owned by the system outright. Joint ownership reads as
// collaboration and functions as ambiguity: a row owned by everyone is picked
// up by no one, and the system is never licensed to close it alone. So the
// backlog can only fall on a day Joe says so.
//
// A single owner is not bureaucracy, it is the precondition for an autonomous
// drain. Of those 110 rows, 15 say in their own text that a human must act and
// 25 name Dell — the other 75 carry no human signal at all and were only ever
// waiting because nobody was unambiguously holding them.
const LOOP_OWNERS = Object.freeze(["joe", "dell", "claude"]);

// Any separator between two names is the ambiguity: slash, plus, arrow,
// ampersand, comma, or the word "and". Matched on the raw string because that
// is exactly how these rows were written by hand.
const JOINT_OWNER_RE = /[\/+&,]|→|->|\band\b/i;

function assertSingleOwner(owner) {
  const raw = (owner || "").trim();
  if (!raw) return null;                 // absent is allowed; ambiguous is not
  if (JOINT_OWNER_RE.test(raw))
    throw new ToolError({
      error: "joint_ownership_refused",
      got: raw,
      owners: LOOP_OWNERS,
      hint: "a loop owned by two people is owned by neither, and a jointly-owned loop can never be closed by the system on its own — which is why this backlog only falls when Joe orders a purge. Pick ONE: 'claude' if the system can finish it without a human, otherwise 'joe' or 'dell' — and then the row must name a blocker saying what it waits on.",
    });
  if (!LOOP_OWNERS.includes(raw.toLowerCase()))
    throw new ToolError({
      error: "unknown_owner",
      got: raw,
      owners: LOOP_OWNERS,
      hint: "owner is a single actor, lowercase — the field decides who may act, so a free-text value means nobody can be selected for by a query",
    });
  return raw.toLowerCase();
}

// ---------- Deal Room helpers (field-base concurrency, not record version) ----------


async function lockDealField(c, dealId, field) {
  // Same-field writers serialize; different fields deliberately use different
  // advisory keys and can commit independently on the same deal.
  await c.query(
    "select pg_advisory_xact_lock(hashtextextended($1 || ':' || $2, 0)) /* dealroom:field-lock */",
    [dealId, field],
  );
}

async function latestFieldConflict(c, dealId, field, baseEventId) {
  let base = null;
  if (baseEventId !== null && baseEventId !== undefined) {
    const found = await c.query(
      `select recorded_at, id from event
        where id=$1 and subject_type='deal' and subject_id=$2 and field=$3
        /* dealroom:base-event */`,
      [baseEventId, dealId, field],
    );
    if (!found.rows.length)
      throw new ToolError({ error: "invalid_base_event", base_event_id: baseEventId, deal_id: dealId, field });
    base = found.rows[0];
  }

  const newer = await c.query(
    `select e.id as event_id, e.actor_id, a.slug as actor, e.new_value -> $2 as value
       from event e join actor a on a.id=e.actor_id
      where e.subject_type='deal' and e.subject_id=$1 and e.field=$2
        and ($3::timestamptz is null or (e.recorded_at, e.id) > ($3::timestamptz, $4::uuid))
      order by e.recorded_at desc, e.id desc limit 1
      /* dealroom:latest-field-event */`,
    [dealId, field, base?.recorded_at || null, base?.id || null],
  );
  return newer.rows[0] || null;
}

// `provenance` is optional and defaults to empty: revert-deal-field and
// resolve-conflict pass nothing and keep automation semantics, which is what
// they are. patch-deal-field passes the partner's words through when the caller
// carried them (WR-000109), and never synthesises a cause — writeEvent derives
// it from the presence of a verbatim quote, and that derivation is the point.
async function applyDealRoomField(c, actor, dealId, field, value, idempotencyKey, verb, provenance = {}) {
  assertDealRoomField(field, value, ToolError);
  if (field === "operating_state") value = {
    state: value.state,
    reason: value.state === "parked" ? value.reason : null,
    note: value.state === "parked" ? value.note?.trim() || null : null,
  };
  const oldRow = field === "operating_state"
    ? await c.query(
      `select jsonb_build_object('state',operating_state,'reason',parking_reason,'note',parking_note) as value
         from deal where id=$1`, [dealId])
    : await c.query(field === "next_date"
      ? "select next_date::text as value from deal where id=$1"
      : `select ${field} as value from deal where id=$1`, [dealId]);
  if (!oldRow.rows.length) throw new ToolError({ error: "not_found", table: "deal", id: dealId });
  if (field === "owner") {
    // deal.owner is the board cache; deal_participant(role=lead) remains the
    // canonical operating assignment used by documents and the rest of the
    // record layer. A Deal Room change moves both in the same transaction.
    await c.query(
      `update deal_participant set to_at=now()
        where deal_id=$1 and role='lead' and to_at is null
        /* dealroom:close-lead */`, [dealId]);
    if (value) {
      const next = (await c.query("select id from actor where slug=$1 and active", [value])).rows[0];
      if (!next) throw new ToolError({ error: "unknown_owner", owner: value });
      await c.query(
        `insert into deal_participant (deal_id,actor_id,role,set_by)
         values ($1,$2,'lead',$3) /* dealroom:open-lead */`, [dealId, next.id, actor.id]);
    }
  }
  if (field === "operating_state") {
    await c.query(
      `update deal
          set operating_state=$2, parking_reason=$3, parking_note=$4,
              parked_at=case when $2='parked' then now() else null end,
              parked_by=case when $2='parked' then $5::uuid else null end,
              updated_by=$5::uuid
        where id=$1 /* dealroom:apply-operating-state */`,
      [dealId, value.state, value.state === "parked" ? value.reason : null,
       value.state === "parked" ? value.note : null, actor.id],
    );
  } else {
    await c.query(
      `update deal set ${field}=$2, updated_by=$3 where id=$1 /* dealroom:apply-field */`,
      [dealId, value, actor.id],
    );
  }
  await writeEvent(c, actor, verb, "deal", dealId, {
    field,
    // The field lock orders writes by commit. Transaction-start `now()` would
    // let a writer that waited for the lock sort behind the write it replaced.
    recorded_at_after_lock: true,
    old: { [field]: oldRow.rows[0].value },
    new: { [field]: value },
    human_quote: provenance.human_quote || null,
    agent_rationale: provenance.change_reason || null,
    idempotency_key: idempotencyKey,
  });
  // The committed identity of the event this write just made, READ BACK from the
  // record. Nothing here constructs an id: writeEvent's insert carries no
  // `returning` and is shared by every verb in this file, so it is not this
  // verb's to change, and the row is found the way any other row is found.
  //
  // What makes the read exact is the event's own idempotency_key — one intended
  // action, one key, one event row — so it names the row written immediately
  // above and can never name a partner's, which ordering by recorded_at alone
  // could under a concurrent writer. Same table and same ordering the Deal Room
  // already uses to find a cell's newest event (revert-deal-field).
  //
  // Why the answer needs it: the board's ONLY source of a field base was the
  // changes feed, which lags a write by up to a poll. A second, different edit to
  // the same cell inside that window therefore sent a base its own first edit had
  // already superseded, and the server correctly recorded a conflict — between a
  // partner and themselves. With the committed id in the answer the client can
  // advance that cell's base immediately, from the record's own word for it.
  //
  // recorded_at is serialized exactly as the changes feed serializes it
  // (dealroom.js), so the two are directly comparable on the client: a base is
  // only ever moved forward, never back onto an older event.
  const committed = (await c.query(
    `select id, to_jsonb(recorded_at)#>>'{}' as recorded_at from event
      where subject_type='deal' and subject_id=$1 and field=$2 and idempotency_key=$3
      order by recorded_at desc, id desc limit 1 /* dealroom:written-event */`,
    [dealId, field, idempotencyKey]))?.rows?.[0] || null;
  return {
    old_value: oldRow.rows[0].value,
    new_value: value,
    // Null when this write carried no key to find the row by. A client that gets
    // no id simply does not advance its base and waits for the feed, which is
    // exactly the behaviour it had before this existed.
    event_id: committed?.id ?? null,
    event_recorded_at: committed?.recorded_at ?? null,
  };
}

const FIND_CATCH_UP_QUERY_MAX = 200;
const FIND_CATCH_UP_LIMIT_MAX = 50;
const FIND_CATCH_UP_CANDIDATE_CAP = 25;
const CONVERSATION_TIMELINE_MAX = 20;
const CONVERSATION_PATH_MAX = 10;

function kindFromRef(ref) {
  if (/^L-/i.test(ref)) return "lead";
  if (/^C-/i.test(ref)) return "client";
  if (/^[VT]-/i.test(ref)) return "vendor";
  if (/^P-/i.test(ref)) return "party";
  return "record";
}

// Turn find's deliberately rich result into the one thing a composition may
// act on: an explicit LIVE target. Related lead/client links and deals_via_link
// are context, not matches, so they never enter this list. Ref-bearing rows are
// deduplicated because find can surface the same survivor in both parties and
// organizations; deal-name rows are not deduplicated because two deals with one
// name are still two records and therefore must stop for disambiguation.
function findCatchUpCandidates(found) {
  if (!found || typeof found !== "object" || Array.isArray(found) ||
      !Array.isArray(found.parties) || !Array.isArray(found.organizations) ||
      !Array.isArray(found.deals))
    throw new ToolError({ error: "find_result_invalid" });

  const refs = new Map();
  const addRef = (target, name, kind) => {
    if (typeof target !== "string" || !target.trim()) return;
    const clean = target.trim();
    if (!refs.has(clean)) refs.set(clean, {
      kind: typeof kind === "string" && kind ? kind : kindFromRef(clean),
      name: typeof name === "string" && name ? name : clean,
      target: clean,
    });
  };

  for (const row of found.parties) {
    if (row && row.merged === false) addRef(row.ref, row.name, row.kind);
  }
  for (const row of found.organizations) {
    if (!row || typeof row !== "object") continue;
    for (const ref of [...(Array.isArray(row.refs) ? row.refs : []),
                       ...(Array.isArray(row.role_refs) ? row.role_refs : [])])
      addRef(ref, row.name, kindFromRef(ref));
  }

  const deals = found.deals.flatMap((row) =>
    row && typeof row.name === "string" && row.name.trim()
      ? [{ kind: "deal", name: row.name.trim(), target: row.name.trim() }]
      : []);
  return [...refs.values(), ...deals].sort((a, b) =>
    a.target.localeCompare(b.target) || a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name));
}

// ---------- the registry ----------
// Each: { description, inputSchema, write: bool, humanOnly?: bool, handler(client, actor, args) }

// Project only the sanitized billing contract, never the board's raw JSON.
// This consumer validates evidence; the collector owns every calculation.
function morningCostSnapshot(value) {
  const unavailable = { state: "unavailable", reason: "source_unavailable", items: [],
    providers: [], months: [], alerts: [],
    action: "owner orchestrator: run the cost collector, restore missing billing reads, and verify a fresh complete-day snapshot; auto-clear after a valid snapshot younger than 36 hours." };
  const money = (n) => typeof n === "number" && Number.isFinite(n) && n >= 0;
  const text = (s) => typeof s === "string" && s.length > 0 && s.length <= 500;
  const date = (s) => typeof s === "string" && /^\d{4}-\d{2}-\d{2}$/.test(s) && isCalendarDate(s);
  const month = (s) => typeof s === "string" && /^\d{4}-(0[1-9]|1[0-2])$/.test(s);
  const amounts = (o) => {
    if (!o || typeof o !== "object" || Array.isArray(o) || Object.keys(o).length > 500) throw Error("invalid cost map");
    if (!Object.entries(o).every(([k, v]) => text(k) && money(v))) throw Error("invalid cost amount");
    return Object.fromEntries(Object.entries(o));
  };
  try {
    const age = Date.now() - Date.parse(value?.observed_at);
    if (value?.schema !== "carr-system-costs.v1" || !["ready", "partial"].includes(value.state)
        || !Number.isFinite(age) || age < -300000 || age > 36 * 3600000
        || !month(value.month) || !date(value.through) || !text(value.action)
        || !Array.isArray(value.providers) || value.providers.length > 30
        || !Array.isArray(value.months) || value.months.length > 24
        || !Array.isArray(value.alerts) || value.alerts.length > 100) return unavailable;
    const providers = value.providers.map((row) => {
      if (!text(row.provider) || !text(row.label) || !text(row.plan)
          || !["ready", "partial", "unavailable"].includes(row.state)
          || !(row.mtd_usd === null || money(row.mtd_usd))
          || !(row.projection_usd === null || money(row.projection_usd))
          || !(row.budget_usd === null || money(row.budget_usd))
          || !(row.reason === null || text(row.reason))
          || !Array.isArray(row.daily) || row.daily.length > 62) throw Error("invalid cost provider");
      return { provider: row.provider, label: row.label, plan: row.plan, state: row.state,
        reason: row.reason, mtd_usd: row.mtd_usd, projection_usd: row.projection_usd,
        budget_usd: row.budget_usd, ...(typeof row.estimated === "boolean" ? { estimated: row.estimated } : {}),
        call_sites: amounts(row.call_sites),
        daily: row.daily.map((day) => {
          if (!date(day.day) || !money(day.usd)) throw Error("invalid cost day");
          return { day: day.day, usd: day.usd, drivers: amounts(day.drivers) };
        }) };
    });
    const months = value.months.map((row) => {
      if (!month(row.month) || !money(row.usd)) throw Error("invalid cost month");
      return { month: row.month, usd: row.usd, providers: amounts(row.providers) };
    });
    const alerts = value.alerts.map((row) => {
      if (!text(row.provider) || !text(row.driver) || !text(row.kind)
          || !money(row.amount_usd) || !money(row.threshold_usd)) throw Error("invalid cost alert");
      return { provider: row.provider, driver: row.driver, kind: row.kind,
        amount_usd: row.amount_usd, threshold_usd: row.threshold_usd };
    });
    return { schema: value.schema, observed_at: value.observed_at, month: value.month,
      through: value.through, state: value.state, providers, months, alerts, action: value.action, items: providers,
      ...(money(value.budget_usd) ? { budget_usd: value.budget_usd } : {}),
      ...(money(value.projection_usd) ? { projection_usd: value.projection_usd } : {}) };
  } catch { return unavailable; }
}

export const TOOLS = {

  // ===== reads (carr_reader connection) =====

  "read-v5-a02-rule-enforcement-coverage": {
    write: false,
    description: "Read DoctorCRE V5-A02's server-derived coverage of every active rule. The record layer enumerates active rules and checks that every control its current exact approval names is installed and bound, with test verification that is not future-dated and still equals the value the approval captured, and an immutable fallback receipt written on Joe's authority database connection (the receipt proves which authority connection recorded it, not that a human chose it). Empty input only: caller evidence cannot make coverage green. Each uncovered rule is a named gap (amended after approval, control unmapped, an approved control not installed, tests missing, test evidence future-dated or changed since approval, fallback absent); zero active rules reads coverage_state `empty`, never complete; an unreadable or inconsistent record is a fail-closed unavailable result.",
    inputSchema: { type: "object", additionalProperties: false, properties: {} },
    handler: async (c) => readRuleEnforcementCoverage(c),
  },

  "find": {
    write: false,
    description: "Search people, practices, buildings, deals, leads, vendors by name (fuzzy). Use FIRST when you only have a name; returns refs (L-/C-/V-) the write verbs take. Matches party.name / deal.name / client.roster_ref. Survivors come first and are counted separately from retired aliases: `refs`/`live_rows` are what you may write to, `retired_refs`/`retired_aliases` are tombstones of completed merges, kept navigable but never a target. Also returns the intro-graph edges touching the match (who can introduce whom), newest first. FOLLOWS THE LEAD ↔ CLIENT LINK SINCE 0102: `lead_client_links` pairs a matched lead with the client it became (or sits under), by exact key and never by name, and `deals_via_link` carries the deals filed under that client — which is how a search for a doctor's name finally surfaces the deal filed under their practice's name. `deals` remains the name-match list and the two are never blended. NOT the verb for a ref you already hold (catch-me-up takes that), and NOT the referral-path verb (who-do-we-know walks the graph). Read-only.",
    inputSchema: { type: "object", properties: { query: { type: "string" } }, required: ["query"] },
    handler: async (c, _a, args) => {
      const q = args.query;
      // [amendment 11] Through v_ref_index, not the base tables. Merged records are
      // KEPT here (unlike resolveSubject) and carry the flag: someone searching a
      // merged name should learn the record exists and where it went, rather than
      // be told nothing matched.
      // SURVIVORS FIRST (loop #132). The flag was already on every row, but the
      // ordering was pure similarity, so a name carrying tombstones could spend the
      // ten-row budget on retired refs and push its own survivor out of the answer.
      // Sorting on `merged` first costs nothing — the tombstones still come back,
      // they just stop outranking the record that is actually live.
      const parties = await c.query(
        `select display_name as name, city, specialty, org_name, ref, subject_type as kind, merged
         from v_ref_index
         where subject_type in ('lead','client','vendor')
           and (display_name % $1 or display_name ilike $2)
         order by merged, similarity(display_name,$1) desc limit 10`, [q, `%${q}%`]);
      // ORGS AND UNLINKED PEOPLE, GROUPED (0056, 2026-08-02). Until migration 0056
      // v_ref_index held only role records, so 415 org parties were invisible here:
      // `find "Henry Schein"` returned "Henry Prescott" — a trigram hit on one word —
      // and none of the 17 rows literally named Henry Schein.
      // GROUPED BY NAME ON PURPOSE. Those 17 rows are one company minted 17 times,
      // once per rep, and listing them raw would spend the whole 10-row budget on
      // copies of one answer and push every other match out. One row per name, with
      // the count and the refs, answers "who do we know at X" AND surfaces the
      // duplication instead of hiding it. Kept separate from the role query above so
      // a bare party never outranks a real client or vendor.
      //
      // LIVE AND RETIRED ARE COUNTED SEPARATELY, AND THE BLEND WAS THE BUG (loop
      // #132, 2026-08-02). This grouping shipped in b0fda91, BEFORE 0059 consolidated
      // the orgs, and it was never taught about merged_into. Afterwards it kept
      // reporting `duplicate_rows: 17` for Henry Schein and `13` for Cadence Studio —
      // both of which are ONE live row plus sixteen and twelve tombstones. That
      // number then read as "the book is still full of duplicates", which is the
      // opposite of what 0059 did, and every ref in the list read as a live target.
      // There is no single honest count here, so there is no single count: the
      // survivors and the tombstones are two facts and they travel as two fields.
      // party_org_identity_uniq makes live_rows=1 the invariant for any consolidated
      // org, so a live_rows above 1 is now a real signal rather than noise.
      const orgs = await c.query(
        // 2026-08-02: the subject_type='party' restriction moved OFF the WHERE and
        // ONTO each aggregate. It has to, because v_ref_index indexes SUBJECTS rather
        // than roles (0056): the moment an org party gains a client, lead or vendor
        // record it stops appearing as a party row and starts appearing under that
        // role's ref. 0061 did exactly that to Cadence Studio — P-0111 is live and
        // unmerged, but it now indexes as client C-161, so a party-only query saw its
        // twelve tombstones, reported live_rows:0, and fired the all_retired note
        // claiming the survivor "carries a DIFFERENT name and is not in this result"
        // while the survivor sat in the SAME payload under the SAME name. Counting
        // live rows of any subject_type is what makes all_retired mean what it says.
        `select display_name as name,
                count(*) filter (where not merged and subject_type = 'party')::int
                  as live_rows,
                count(*) filter (where merged and subject_type = 'party')::int
                  as retired_aliases,
                coalesce(array_agg(ref order by ref)
                           filter (where not merged and subject_type = 'party'),
                         '{}'::text[]) as refs,
                coalesce((array_agg(ref order by ref)
                            filter (where merged and subject_type = 'party'))
                           [1:${RETIRED_REF_CAP}],
                         '{}'::text[]) as retired_refs,
                count(*) filter (where not merged and subject_type <> 'party')::int
                  as live_as_role,
                coalesce(array_agg(ref order by ref)
                           filter (where not merged and subject_type <> 'party'),
                         '{}'::text[]) as role_refs
         from v_ref_index
         where (display_name % $1 or display_name ilike $2)
         group by display_name
        having count(*) filter (where subject_type='party') > 0
         order by similarity(display_name,$1) desc limit 5`, [q, `%${q}%`]);
      const deals = await c.query(
        // The column is lead_owner; `owner` never existed on this view, so this
        // query has always thrown. It stayed invisible because the query above it
        // threw first (amendment 11) — one bug hiding another.
        `select name, phase, lead_owner as owner, client_ref,
                to_jsonb(invoiced_on)#>>'{}' as invoiced_on,
                to_jsonb(closed_on)#>>'{}' as closed_on, lane, outcome
           from v_deal_board where name ilike $1 limit 5`,
        [`%${q}%`]);
      // [ORDER 18] The intro graph, through v_party_graph — SAFE COLUMNS ONLY, the
      // same views-only posture as v_ref_index. Capped deliberately: a hub like
      // V-CPA-006 carries 16 edges on its own and the whole graph is not an answer
      // to a name search. Newest first, because a fresh intro is the useful one;
      // names break the tie so the 28 backfilled edges (one timestamp between them)
      // still come back in a stable order.
      const connections = await c.query(
        `select from_ref, from_name, kind, to_ref, to_name, note
         from v_party_graph
         where from_name ilike $1 or to_name ilike $1
            or from_ref  ilike $2 or to_ref  ilike $2
         order by linked_at desc, from_name, to_name limit $3`,
        [`%${q}%`, q, CONNECTIONS_CAP]);

      // The org rows carry their own truncation flag rather than a silent slice:
      // retired_aliases is the exact count, retired_refs may be the first
      // RETIRED_REF_CAP of them, and the reader is told which it is looking at.
      const organizations = orgs.rows.map(r => ({
        name: r.name,
        live_rows: r.live_rows,
        refs: r.refs,
        retired_aliases: r.retired_aliases,
        retired_refs: r.retired_refs,
        retired_refs_truncated: r.retired_aliases > r.retired_refs.length,
        // live_as_role / role_refs: the survivor is live but now carries a client,
        // lead or vendor ref instead of its bare party ref. all_retired means NOBODY
        // under this name is live ANYWHERE, which is the only case where telling the
        // reader to go search another name is true.
        live_as_role: r.live_as_role,
        role_refs: r.role_refs,
        all_retired: r.live_rows === 0 && r.live_as_role === 0,
      }));
      const retiredSeen = parties.rows.filter(r => r.merged).length
        + organizations.reduce((n, r) => n + r.retired_aliases, 0);
      const orphanNames = organizations.filter(r => r.all_retired).map(r => r.name);
      const promoted = organizations.filter(r => r.live_rows === 0 && r.live_as_role > 0);

      // ── THE LEAD ↔ CLIENT LINK, FOLLOWED (0102, loop #127) ────────────────────
      // Until now this verb matched deals BY NAME ONLY, so a search that landed on a
      // lead returned deals:[] even when that lead's own client carried a live deal —
      // the deal is filed under the practice's name and the search was for the
      // doctor's. The link was in the data the whole time and no read verb followed it.
      //
      // EXACT KEYS ONLY, NEVER A NAME. v_lead_client_best ranks three uuid equalities
      // (conversion pointer, shared party, shared org) and hands back one row per lead;
      // this verb does no matching of its own. That constraint is in the loop's own
      // body for a reason: this system once welded Jenna Castillo to Jeff Castillo, DMD —
      // two different people — through an import that matched on a surname.
      const matchedRefs = [
        ...parties.rows.map(r => r.ref).filter(Boolean),
        ...organizations.flatMap(r => [...(r.refs || []), ...(r.role_refs || [])]),
      ];
      let linked = [], linkedDeals = [];
      if (matchedRefs.length) {
        const lr = await c.query(
          `select lead_ref, lead_name, client_ref, client_name, link_basis, either_merged
             from v_lead_client_best
            where lead_ref = any($1) or client_ref = any($1)
            order by link_basis, lead_ref limit $2`, [matchedRefs, LINK_CAP]);
        linked = lr.rows;
        // The deals reachable THROUGH that link — the answer the caller wanted and did
        // not get. Kept in their own field rather than folded into `deals` so a reader
        // can always tell which of the two paths produced a row.
        const clientRefs = [...new Set(linked.map(r => r.client_ref).filter(Boolean))];
        const named = new Set(deals.rows.map(d => d.name));
        if (clientRefs.length) {
          const dr = await c.query(
            `select name, phase, lead_owner as owner, client_ref,
                    to_jsonb(invoiced_on)#>>'{}' as invoiced_on,
                    to_jsonb(closed_on)#>>'{}' as closed_on, lane, outcome
               from v_deal_board where client_ref = any($1)
              order by client_ref, name limit $2`, [clientRefs, LINK_CAP]);
          linkedDeals = dr.rows.filter(d => !named.has(d.name));
        }
      }

      const notes = [];
      if (retiredSeen)
        notes.push("LIVE and RETIRED are counted separately here and are never blended. " +
          "`refs` / `live_rows` (and any row with merged:false) are the survivors — those are " +
          "the refs write verbs take. `retired_refs` / `retired_aliases` and any row with " +
          "merged:true are tombstones of completed merges: they stay listed so a note, email or " +
          "document citing an old ref is still navigable, but they are not duplicates and are " +
          "never a write target.");
      else
        notes.push("No retired aliases among these matches — every ref listed is live.");
      if (promoted.length)
        notes.push(promoted.map(r =>
          `"${r.name}" has no live PARTY row, but it is not gone: the survivor is live in this ` +
          `same result under ${r.role_refs.join(", ")}. It stopped indexing as a bare party ` +
          `when it gained that record, which is how this index works — it indexes subjects, ` +
          `not roles. Write to ${r.role_refs.join(", ")}, not to a retired P- ref.`).join(" "));
      if (orphanNames.length)
        notes.push("Every row under " + orphanNames.map(n => `"${n}"`).join(", ") +
          " is retired, and nothing under that name is live anywhere: that merge's survivor " +
          "carries a DIFFERENT name and is not in this result. Search the survivor's name, or " +
          "catch-me-up one of the retired refs to see where it went. This is not a claim that " +
          "we do not know them.");

      if (linked.length) {
        const conv = linked.filter(r => r.link_basis === "conversion").length;
        const org = linked.filter(r => r.link_basis === "same_org").length;
        notes.push(
          `lead_client_links carries ${linked.length} lead/client pair(s) touching this ` +
          "search, resolved by exact key and never by name. link_basis says which: " +
          "`conversion` is lead.client_id, the pointer set when the lead became that " +
          "client; `same_party` is one person holding both records; `same_org` is the " +
          "lead sitting under the client's practice, which answers \"is this practice " +
          "already a client\" and is NOT a conversion." +
          (conv ? "" : " None of these is a conversion pointer.") +
          (org ? " Read the same_org rows as neighbours, not as the same record." : ""));
        if (linkedDeals.length)
          notes.push(`deals_via_link carries ${linkedDeals.length} deal(s) that this ` +
            "search would otherwise have missed entirely: they are filed under the linked " +
            "client, whose name does not contain the search term. `deals` is still the " +
            "name-match list and the two are never blended.");
      }

      return { parties: parties.rows, deals: deals.rows, connections: connections.rows,
               organizations,
               lead_client_links: linked, deals_via_link: linkedDeals,
               note: notes.join(" ") };
    },
  },

  "who-do-we-know": {
    write: false,
    description: "\"Who gets me to X?\" — walks the intro graph BACKWARD from a target (a ref like C-155 / V-CPA-006, or a name) and returns every referral path up to 3 hops (walks the party_link table), shortest first, each rendered as a readable chain (\"A. Vendor -knows-> B. Referrer -intro-> Dr. Example Target\"). The first name in a chain is who Joe asks. Use it before asking for an introduction; NOT for looking a record up (that is `find`) and NOT for what happened with a record (that is `catch-me-up`). Read-only, and it never guesses: it resolves to the SURVIVOR of a merge and never offers a tombstone as a target, an ambiguous LIVE name returns needs_disambiguation with the candidates, and a target that exists but carries no walkable edges says which of those two it is rather than returning an empty list that reads like 'no such person'.",
    inputSchema: { type: "object", properties: {
      target: { type: "string", description: "who you want to reach — C-155, V-CPA-006, L-208, or a full name" },
      max_depth: { type: "integer", description: `hops to walk, 1-${WHO_MAX_DEPTH} (default ${WHO_MAX_DEPTH})` },
      limit: { type: "integer", description: `paths returned, capped at ${WHO_PATH_CAP}` } },
      required: ["target"] },
    handler: async (c, _a, args) => {
      const q = String(args.target || "").trim();
      if (!q) throw new ToolError({ error: "missing_target", hint: "pass a ref (C-155) or a name" });
      const depth = Math.max(1, Math.min(WHO_MAX_DEPTH, args.max_depth || WHO_MAX_DEPTH));
      const cap = Math.max(1, Math.min(WHO_PATH_CAP, args.limit || WHO_PATH_CAP));

      // ── resolve the target to ONE node of the graph ──────────────────────
      // Ref first and exactly, name second and only on a distinct-ref basis: two
      // rows for one ref is the same party appearing on both ends of edges, not
      // an ambiguity, so the distinct is what makes the count mean something.
      //
      // MERGE-AWARE SINCE loop #132 (2026-08-02). Two changes, both about not
      // handing back a retired record. The null-ref filter: a NULL endpoint used to
      // resolve to a node with ref=null, which then walked nothing and reported
      // in_graph:true with zero paths — a live-looking answer built on a node the
      // walker cannot address. Those edges are now reported as unwalkable below,
      // by name, which is the truth. And the merged flag: a graph node can be a
      // tombstone (C-050 is one today, a real client name), so the survivor is
      // preferred whenever both are matched, and a tombstone that resolves anyway — because
      // the caller named it, or because it is the only match and its edges are
      // real — comes back FLAGGED rather than silently standing in for the survivor.
      const nodes = await c.query(
        `with n as (
           select from_ref as ref, from_name as name from v_party_graph
            where from_ref is not null
           union
           select to_ref, to_name from v_party_graph
            where to_ref is not null)
         select n.ref, n.name, coalesce(bool_or(ri.merged), false) as merged
           from n left join v_ref_index ri on ri.ref = n.ref
          where n.ref ilike $1 or n.name ilike $2
          group by n.ref, n.name
          order by merged, n.ref`, [q, `%${q}%`]);
      let node = null;
      if (nodes.rows.length) {
        // An exact ref among several name-ish matches is not ambiguous.
        const exact = nodes.rows.filter(r => (r.ref || "").toLowerCase() === q.toLowerCase());
        const live = nodes.rows.filter(r => !r.merged);
        const retired = nodes.rows.filter(r => r.merged);
        if (exact.length === 1) node = exact[0];
        else if (live.length === 1) node = live[0];
        else if (live.length > 1) throw new ToolError({ error: "needs_disambiguation", target: q,
          candidates: live.map(r => ({ ref: r.ref, name: r.name })),
          retired_aliases: retired.map(r => ({ ref: r.ref, name: r.name })),
          hint: "more than one LIVE party in the intro graph matches — pass the exact ref. " +
                "retired_aliases are tombstones of completed merges, listed so you can see them; " +
                "never pass one back as the target" });
        else if (retired.length === 1) node = retired[0];
        else throw new ToolError({ error: "needs_disambiguation", target: q,
          candidates: [],
          retired_aliases: retired.map(r => ({ ref: r.ref, name: r.name })),
          hint: "every graph node matching this name is a RETIRED alias of a completed merge, " +
                "and more than one matched. Run `find` on the name to see which survivor each " +
                "one points at, then target the survivor" });
      }

      // EDGES THIS NAME OWNS BUT THE WALKER CANNOT FOLLOW (loop #133). An edge whose
      // endpoint carries no business ref is invisible to WHO_EDGES, and staying
      // silent about it produced a flat lie: Joe Bookout is party P-1084 with no
      // client/lead/vendor row, so all six of his `can_introduce` edges — the single
      // most valuable edge class in the book — have a NULL from_ref, and asking for
      // him answered "this record exists but carries no intro-graph edges yet". It
      // carries six. Scoped to the query so the answer names the actual edges rather
      // than a global count nobody can act on.
      // Matched on REF AS WELL AS NAME. Half of every unwalkable edge has a ref on
      // the other end, and asking by that ref — who gets me to V-CPA-036 — is the
      // normal way this verb is called. Name-only matching would have answered
      // "nobody reaches her" while the Joe Bookout -> V-CPA-036 edge sat right there.
      const blocked = await c.query(
        `select from_ref, from_name, kind, to_ref, to_name, note
           from v_party_graph
          where (from_ref is null or to_ref is null)
            and (from_name ilike $1 or to_name ilike $1
                 or from_ref ilike $2 or to_ref ilike $2)
          order by from_name, to_name limit $3`, [`%${q}%`, q, CONNECTIONS_CAP]);
      const unwalkableHere = blocked.rows.map(r => ({
        from: r.from_name, from_ref: r.from_ref, kind: r.kind,
        to: r.to_name, to_ref: r.to_ref, evidence: r.note,
        blocked_end: r.from_ref === null ? "from" : "to" }));
      const blockedNote = unwalkableHere.length
        ? `${unwalkableHere.length} intro-graph edge(s) touching this name CANNOT be walked: ` +
          "one endpoint carries no business ref (a bare party — a CARR agent with no " +
          "client/lead/vendor row, or a party a link still points at after a merge). They are " +
          "listed in unwalkable_edges and they are real; the path walker simply has no node to " +
          "address. Do not read their absence from `paths` as an absence of the relationship."
        : "";

      if (!node) {
        // NOT the same answer as "no path". A record that exists and simply has
        // no edges is a gap in the Links data; a name nobody has ever recorded is
        // a different problem, and collapsing the two would hide both.
        // 'party' INCLUDED (0056, 2026-08-02). This block is the verb's honesty
        // guarantee — "exists but has no edges" must never collapse into "no such
        // person". Restricted to role records it was breaking exactly that promise:
        // asked for Henry Schein, which is 17 party rows, it answered "No record and
        // no graph node matches that name", which was simply false. A read-only
        // existence check has no reason to be narrower than the record.
        // MATCHING_RECORDS IS LIVE-ONLY, AND THE COUNT OF TOMBSTONES TRAVELS BESIDE
        // IT (loop #132). Unordered and capped at five, this block handed back five
        // tombstones for "Cadence Studio" — P-0840, P-1044, P-0909, P-0796 — as
        // selectable records while the survivor P-0111 never appeared. A caller that
        // links or writes to one of those defeats the merge. So: survivors in
        // matching_records, tombstones as a COUNT only (find lists them with their
        // refs; that is find's job, and it is where they stay navigable), and the
        // window counts are computed before the limit so the numbers are exact even
        // when the list is truncated.
        const known = await c.query(
          `select display_name as name, ref, subject_type as kind, merged,
                  (count(*) filter (where not merged) over ())::int as live_total,
                  (count(*) filter (where merged)     over ())::int as retired_total
             from v_ref_index
            where subject_type in ('lead','client','vendor','party')
              and (ref ilike $1 or display_name ilike $2)
            order by merged, ref limit 10`, [q, `%${q}%`]);
        const liveTotal = known.rows.length ? known.rows[0].live_total : 0;
        const retiredTotal = known.rows.length ? known.rows[0].retired_total : 0;
        const liveRows = known.rows.filter(r => !r.merged)
          .map(r => ({ name: r.name, ref: r.ref, kind: r.kind }));

        let note;
        if (liveTotal) {
          note = unwalkableHere.length
            // "log the connection" would be wrong advice here: the connection IS
            // logged, it just has no walkable node. Telling Joe to record it again
            // would mint a duplicate edge and hide the actual defect.
            ? "This record exists and its intro-graph edges ARE logged — they are the " +
              "unwalkable ones above, not missing. Nothing to re-record with link-parties."
            : "This record exists but carries no WALKABLE intro-graph edges — the " +
              "connection may simply not be logged. Record it with link-parties.";
          if (retiredTotal)
            note += ` Plus ${retiredTotal} retired alias(es) under this name from completed ` +
                    "merges; they are history, never link targets — run `find` to see them.";
        } else if (retiredTotal) {
          note = `Every record under this name is a RETIRED alias (${retiredTotal}) of a ` +
                 "completed merge. The survivor carries a different name and is not in this " +
                 "result — run `find` on it, or catch-me-up one of the retired refs to see " +
                 "where it went. This is NOT a claim that we do not know them.";
        } else {
          note = "No record and no graph node matches that name. Try `find` first.";
        }
        if (blockedNote) note = blockedNote + " " + note;

        return { target: q, resolved: null, paths: [],
                 in_graph: false,
                 matching_records: liveRows,
                 live_record_count: liveTotal,
                 retired_alias_count: retiredTotal,
                 unwalkable_edges: unwalkableHere,
                 note };
      }

      // ── walk BACKWARD from the target, following edge direction ──────────
      // Direction is the semantics: an edge A -> B means A can reach B, so the
      // people who get Joe to the target are the ones upstream of it. The
      // visited-array guard is what keeps the Colby <-> Nordin pair (a real
      // two-cycle in the book) from generating paths for ever.
      const paths = await c.query(
        `with recursive e as (${WHO_EDGES}),
         back as (
           select e.from_ref as head_ref, e.from_name as head_name, 1 as hops,
                  array[e.from_ref, e.to_ref] as ref_path,
                  e.from_name || ' -' || e.kind || '-> ' || e.to_name as chain,
                  e.note as first_note
             from e where e.to_ref = $1
           union all
           select e.from_ref, e.from_name, b.hops + 1,
                  array_prepend(e.from_ref, b.ref_path),
                  e.from_name || ' -' || e.kind || '-> ' || b.chain,
                  e.note
             from e join back b on e.to_ref = b.head_ref
            where b.hops < $2 and not (e.from_ref = any(b.ref_path)))
         select hops, head_ref as ask_ref, head_name as ask_name,
                ref_path, chain, first_note
           from back order by hops, head_name, chain limit $3`,
        [node.ref, depth, cap]);

      const unwalkable = await c.query(
        `select count(*)::int as n from v_party_graph
          where from_ref is null or to_ref is null`);

      const notes = [];
      if (node.merged)
        notes.push("WARNING: " + node.ref + " is a RETIRED alias of a completed merge, not the " +
          "survivor — it resolved because you named it, or because it is the only node matching. " +
          "Its edges below are real, but run `find` on the name and re-target the survivor before " +
          "you write anything.");
      if (paths.rows.length)
        notes.push("The FIRST name in each chain is who to ask. Run the pairing through DNA/Network/introduction-rules.md before making the ask — a path existing does not make it a clean ask.");
      else if (unwalkableHere.length)
        // "may not be logged" would be the wrong diagnosis when the edges are
        // sitting right there. Zero walkable paths and a non-empty unwalkable list
        // is a REF problem, not a capture problem, and it needs the opposite action.
        notes.push("Zero WALKABLE paths, but that is not the same as no relationship — see " +
          "unwalkable_edges. The gap is a missing ref on an endpoint, not a missing capture; " +
          "logging the connection again would only duplicate it.");
      else
        notes.push("Nobody in the intro graph reaches this record within " + depth + " hops. That may mean the connection is not logged rather than not real.");
      if (blockedNote) notes.push(blockedNote);

      return {
        target: q,
        resolved: { ref: node.ref, name: node.name, merged: node.merged },
        in_graph: true,
        max_depth: depth,
        path_count: paths.rows.length,
        capped: paths.rows.length === cap,
        // SYSTEM-WIDE total, and the name now says so. Renamed 2026-08-03: it
        // sat directly beside the per-target list reading `6` next to `[]`, and
        // a session read that pair as data corruption and started diagnosing an
        // integrity failure that did not exist. Both values were always correct
        // — the scalar counts every unwalkable edge in the graph (its purpose,
        // per opus-work-orders-2026-07-31: "if a ref-less party ever joins the
        // graph, this goes non-zero and THAT is the trigger"), while the list
        // below carries only the edges touching THIS target. Two scopes, two
        // names that looked like one. Nothing consumed the old name.
        edges_unwalkable_total: unwalkable.rows[0].n,
        unwalkable_edges: unwalkableHere,
        paths: paths.rows.map(r => ({
          hops: r.hops, ask_ref: r.ask_ref, ask_name: r.ask_name,
          path: r.chain, ref_path: r.ref_path, evidence: r.first_note })),
        note: notes.join(" "),
      };
    },
  },

  // [ORDER 27 EXT (d)] "We're negotiating against X — where have we faced X,
  // what happened?" Reads v_counterparty_history ONLY (safe columns; [D5]:
  // internal-seat, never client-facing). Counterparties mostly carry no ref,
  // so resolution is name-based with party_id disambiguation — ORDER 32's
  // three-answer convention: disambiguate / exists-but-empty / no match.
  "counterparty-history": {
    write: false,
    description: "Counterparty intelligence: every deal where we have faced this listing agent, landlord, owner, or property manager, and what happened. Ask before any negotiation. Name or ref; ambiguous names return candidates with party_id — retry with party_id.",
    inputSchema: { type: "object", properties: {
      target: { type: "string", description: "counterparty name, or a ref if they are also in the book" },
      party_id: { type: "string", description: "exact party_id from a disambiguation retry" } },
      required: ["target"] },
    handler: async (c, _a, args) => {
      if (args.party_id) {
        const rows = await c.query(
          `select * from v_counterparty_history where party_id = $1
           order by closed_on desc nulls first`, [args.party_id]);
        return { target: args.target, party_id: args.party_id, deals: rows.rows,
                 note: rows.rows.length ? noteFor(rows.rows) : "This party carries no counterparty history rows." };
      }
      let name = args.target;
      if (/^[LCVT]-/i.test(args.target)) {
        // [ORDER 34 review, fix 3] Ref -> party_id via 0027's v_ref_index
        // column, then filter the view by party_id — never by name, which
        // silently welds duplicate-name humans (the Okafor condition).
        const r = await c.query(
          "select party_id, display_name from v_ref_index where ref ilike $1", [args.target]);
        if (!r.rows.length)
          return { target: args.target, deals: [], note: "No record matches this ref." };
        if (r.rows[0].party_id) {
          const rows = await c.query(
            `select * from v_counterparty_history where party_id = $1
             order by closed_on desc nulls first`, [r.rows[0].party_id]);
          return { target: args.target, resolved_name: r.rows[0].display_name, deals: rows.rows,
                   note: rows.rows.length
                     ? `${rows.rows.filter(x => x.deal_id).length} deal(s) against this counterparty.`
                     : "This record exists but carries no counterparty history rows." };
        }
        name = r.rows[0].display_name;
      }
      const parties = await c.query(
        `select distinct party_id, party_name, party_city, party_state
           from v_counterparty_history where party_name ilike $1`, [`%${name}%`]);
      if (!parties.rows.length)
        return { target: args.target, deals: [],
                 note: "No counterparty history under this name. That may mean we have not captured the relationship (add-premises / ownership rows), not that we have never faced them." };
      if (parties.rows.length > 1)
        throw new ToolError({ error: "needs_disambiguation", target: args.target,
          candidates: parties.rows,
          hint: "more than one counterparty matches; retry with party_id" });
      const rows = await c.query(
        `select * from v_counterparty_history where party_id = $1
         order by closed_on desc nulls first`, [parties.rows[0].party_id]);
      function noteFor(rr) {
        const won = rr.filter(x => x.outcome === "won").length;
        const withDeal = rr.filter(x => x.deal_id).length;
        return `${withDeal} deal(s) against this counterparty (${won} won). Rounds and counters live on each deal — catch-me-up the deal for the blow-by-blow.`;
      }
      return { target: args.target, party: parties.rows[0], deals: rows.rows,
               note: rows.rows.length && rows.rows.some(x => x.deal_id)
                 ? noteFor(rows.rows)
                 : "Known counterparty, no deal linkage captured yet." };
    },
  },

  // [ORDER 33 (b)] Which lane has ever produced a commission. Reads
  // v_source_attribution; the honest-limits note travels with every answer.
  "source-attribution": {
    write: false,
    description: "Prospecting ROI: per source lane, the funnel pool -> promoted -> leads -> clients -> deals -> commissions. Answers 'which radar lane has ever produced a commission'. Reads acquisition_source per lane. Optional lane filter.",
    inputSchema: { type: "object", properties: {
      lane: { type: "string", description: "one lane slug to filter (e.g. lead-router, renewal-radar, direct:renewal, __unattributed__)" } },
      required: [] },
    handler: async (c, _a, args) => {
      const rows = args.lane
        ? await c.query("select * from v_source_attribution where lane = $1", [args.lane])
        : await c.query("select * from v_source_attribution order by lane");
      return { lanes: rows.rows,
        note: "Attribution walks pool.promoted_lead_id -> lead.client_id -> deal -> commission. " +
          "Limits, honestly: conversion links were only restored at the 7/30 cutover; leads with no lane " +
          "read direct:unknown; deals with no lead linkage sit in '__unattributed__' so totals reconcile " +
          "against the whole book; the commission table is the ONLY money source here (placeholders never sum)." };
    },
  },

  "catch-me-up": {
    write: false,
    // The canonical replay ledger is not granted to the views-only reader.
    // This route supplies actor context inside a read-only transaction.
    writerConnection: true,
    description: "The merged timeline (event + activity rows) for one deal, client, lead, or vendor, newest first, plus its narrative-file pointer (notes_path). Use before any conversation about a record.",
    inputSchema: { type: "object", properties: { ref: { type: "string", description: "L-204 / C-127 / V-CPA-006 / deal or party name" }, limit: { type: "integer", default: 20 } }, required: ["ref"] },
    handler: async (c, _a, args) => {
      const s = await resolveSubject(c, args.ref);
      const rows = await c.query(
        `select entry_kind, occurred_at, actor, verb, summary, detail, owed
         from v_subject_timeline where subject_type=$1 and subject_id=$2
         order by occurred_at desc limit $3`, [s.type, s.id, args.limit || 20]);
      const captured = await c.query(
        `select t.idempotency_key as key, a.id::text as activity_id, a.summary
           from tool_call t join activity a on a.id::text=t.response->>'activity_id'
          where t.verb='log-activity' and t.actor_id=$1
            and a.${FK[s.type]}=$2 and t.idempotency_key like 'calcap-%'
          order by t.idempotency_key limit 5001`, [_a.id, s.id]);
      if (captured.rows.length > 5000)
        throw new ToolError({ error: "calendar_history_too_large" });
      return { subject: s, timeline: rows.rows, calendar_history: captured.rows };
    },
  },

  "find-and-catch-up": {
    write: false,
    writerConnection: true, // catch-me-up reads the actor-bound replay ledger.
    description: "Find one live person, practice, vendor, or deal by name and immediately return that record's catch-me-up timeline. This is the bounded read-only composition of find then catch-me-up: exactly one live match proceeds; zero returns not_found; multiple matches return needs_disambiguation and no timeline. Retired aliases, linked neighbours, and related deals are never selected as the target. It performs no model call, retry, write, send, or arbitrary tool dispatch.",
    inputSchema: { type: "object", additionalProperties: false, properties: {
      query: { type: "string", description: `name to find, at most ${FIND_CATCH_UP_QUERY_MAX} characters` },
      limit: { type: "integer", minimum: 1, maximum: FIND_CATCH_UP_LIMIT_MAX, default: 20,
        description: `timeline rows returned, 1-${FIND_CATCH_UP_LIMIT_MAX}` },
    }, required: ["query"] },
    handler: async (c, actor, args) => {
      const allowed = new Set(["query", "limit"]);
      if (!args || typeof args !== "object" || Array.isArray(args) ||
          Object.keys(args).some((key) => !allowed.has(key)))
        throw new ToolError({ error: "unexpected_arguments" });

      if (typeof args.query !== "string" || !args.query.trim() ||
          args.query.trim().length > FIND_CATCH_UP_QUERY_MAX)
        throw new ToolError({ error: "invalid_query",
          hint: `query must be a nonempty string of at most ${FIND_CATCH_UP_QUERY_MAX} characters` });
      const query = args.query.trim();
      const limit = args.limit === undefined ? 20 : args.limit;
      if (!Number.isInteger(limit) || limit < 1 || limit > FIND_CATCH_UP_LIMIT_MAX)
        throw new ToolError({ error: "invalid_limit",
          hint: `limit must be an integer from 1 to ${FIND_CATCH_UP_LIMIT_MAX}` });

      // Reuse the registered read handlers on the same read-only client.
      // This is not a generic composite dispatcher: the two names are fixed in
      // code, no callback/tool name/provider is accepted, and the second handler
      // is unreachable until the first yields exactly one live target.
      const found = await executeRegisteredTool(c, actor, "find", { query });
      const candidates = findCatchUpCandidates(found);
      if (candidates.length === 0) {
        const retiredMatches = found.parties.filter((row) => row?.merged === true).length +
          found.organizations.reduce((total, row) => total +
            (Number.isInteger(row?.retired_aliases) ? row.retired_aliases : 0), 0);
        return { state: "not_found", query, candidates: [], retired_matches: retiredMatches,
          hint: retiredMatches
            ? "Only retired aliases matched; search the survivor name or call catch-me-up with a known retired ref."
            : "No live record matched this name." };
      }
      if (candidates.length !== 1) {
        return { state: "needs_disambiguation", query,
          candidate_count: candidates.length,
          candidates: candidates.slice(0, FIND_CATCH_UP_CANDIDATE_CAP),
          candidates_truncated: candidates.length > FIND_CATCH_UP_CANDIDATE_CAP,
          hint: "Choose one exact target and call catch-me-up; this verb never guesses." };
      }

      const match = candidates[0];
      const catchUp = await executeRegisteredTool(c, actor, "catch-me-up", { ref: match.target, limit });
      return { state: "completed", query, match: { ...match }, catch_up: catchUp };
    },
  },

  "prepare-conversation": {
    write: false,
    writerConnection: true, // fixed composition includes catch-me-up.
    description: "Prepare for one conversation by resolving a name to exactly one live record, returning its recent catch-up timeline, and—when the target is a person or organization—showing the existing introduction paths to that exact ref. This is a fixed bounded read composition: ambiguous or missing identity stops before timeline/graph reads; deals receive timeline context but are never pretended to be intro-graph people. It performs no model call, retry, write, send, or arbitrary tool dispatch.",
    inputSchema: { type: "object", additionalProperties: false, properties: {
      query: { type: "string", description: `person, organization, vendor, or deal name; at most ${FIND_CATCH_UP_QUERY_MAX} characters` },
      timeline_limit: { type: "integer", minimum: 1, maximum: CONVERSATION_TIMELINE_MAX, default: 10,
        description: `recent timeline rows, 1-${CONVERSATION_TIMELINE_MAX}` },
      path_limit: { type: "integer", minimum: 1, maximum: CONVERSATION_PATH_MAX, default: 10,
        description: `introduction paths, 1-${CONVERSATION_PATH_MAX}` },
      max_depth: { type: "integer", minimum: 1, maximum: WHO_MAX_DEPTH, default: WHO_MAX_DEPTH,
        description: `introduction hops, 1-${WHO_MAX_DEPTH}` },
    }, required: ["query"] },
    handler: async (c, actor, args) => {
      const allowed = new Set(["query", "timeline_limit", "path_limit", "max_depth"]);
      if (!args || typeof args !== "object" || Array.isArray(args) ||
          Object.keys(args).some((key) => !allowed.has(key)))
        throw new ToolError({ error: "unexpected_arguments" });
      if (typeof args.query !== "string" || !args.query.trim() ||
          args.query.trim().length > FIND_CATCH_UP_QUERY_MAX)
        throw new ToolError({ error: "invalid_query",
          hint: `query must be a nonempty string of at most ${FIND_CATCH_UP_QUERY_MAX} characters` });

      const timelineLimit = args.timeline_limit === undefined ? 10 : args.timeline_limit;
      const pathLimit = args.path_limit === undefined ? 10 : args.path_limit;
      const maxDepth = args.max_depth === undefined ? WHO_MAX_DEPTH : args.max_depth;
      if (!Number.isInteger(timelineLimit) || timelineLimit < 1 ||
          timelineLimit > CONVERSATION_TIMELINE_MAX)
        throw new ToolError({ error: "invalid_timeline_limit" });
      if (!Number.isInteger(pathLimit) || pathLimit < 1 || pathLimit > CONVERSATION_PATH_MAX)
        throw new ToolError({ error: "invalid_path_limit" });
      if (!Number.isInteger(maxDepth) || maxDepth < 1 || maxDepth > WHO_MAX_DEPTH)
        throw new ToolError({ error: "invalid_max_depth" });

      // Three fixed read stages, no caller-selected route: find, catch-up, then
      // (only for a live non-deal target) the introduction graph. The first two
      // already live behind find-and-catch-up's exact one-candidate gate.
      const located = await executeRegisteredTool(c, actor, "find-and-catch-up", {
        query: args.query.trim(),
        limit: timelineLimit,
      });
      if (located.state !== "completed")
        return { workflow: "prepare-conversation", ...located };

      if (located.match.kind === "deal") {
        return { workflow: "prepare-conversation", ...located,
          introduction: {
            status: "not_applicable",
            reason: "Deals do not represent people or organizations in the introduction graph.",
          } };
      }

      const graph = await executeRegisteredTool(c, actor, "who-do-we-know", {
        target: located.match.target,
        max_depth: maxDepth,
        limit: pathLimit,
      });
      return { workflow: "prepare-conversation", ...located,
        introduction: { status: "evaluated", ...graph } };
    },
  },

  "today-triage": {
    write: false,
    description: "What needs attention now: due next-actions (next_action.due_on, suppressed by hold_until), critical_date rows inside 14 days, untriaged ingest items. The morning-brief substrate.",
    inputSchema: { type: "object", properties: {} },
    handler: async (c) => ({ items: (await c.query("select * from v_today_triage order by due_on nulls last limit 50")).rows }),
  },

  "morning-brief": {
    write: false,
    description: "The record-native morning brief for the authenticated Joe or Dell context. It composes live triage, claim-card, deal-room, loop-board, the redacted renewal decision queue, and published system costs. Costs reports ready, partial, or unavailable; other sections report ready, empty, or unavailable. Unavailable is never rewritten as empty. Takes no audience, sponsor, or partner argument.",
    inputSchema: { type: "object", properties: {} },
    handler: async (c, actor, args) => {
      // This is an audience boundary, not a convenience filter.  A shared-only
      // runtime cannot choose Joe or Dell by argument, model name, or cwd.
      const scope = personalScopeForActor(actor);
      if (scope.status !== "personal")
        throw new ToolError({ error: "morning_brief_requires_partner_scope",
          hint: "Reconnect through a verified Joe- or Dell-sponsored context; this brief never accepts a caller-selected audience." });
      if (Object.keys(args || {}).length)
        throw new ToolError({ error: "morning_brief_context_not_selectable" });

      // A section returns a deliberately tiny error shape.  Driver errors can
      // include connection or relation details, so returning them would turn a
      // freshness signal into a disclosure.  Empty is reserved for a completed
      // read with zero rows; a missing/stale source is unavailable.
      const section = async (load) => {
        try {
          const value = await load();
          const items = Array.isArray(value.items) ? value.items : [];
          return { state: items.length ? "ready" : "empty", ...value, items };
        } catch {
          return { state: "unavailable", reason: "source_unavailable", items: [] };
        }
      };
      const ownOrShared = (row) => !row?.owner || String(row.owner).toLowerCase() === scope.sponsor;

      const today = await section(async () => {
        const value = await executeRegisteredTool(c, actor, "today-triage", {});
        return { items: value.items.filter(ownOrShared) };
      });
      const claimCard = await section(async () => {
        const value = await executeRegisteredTool(c, actor, "claim-card", { limit: 5, include_needs_contact: false });
        return { items: value.candidates, claimable: value.claimable,
          needs_contact_count: value.needs_contact_count };
      });
      const deals = await section(async () => {
        const value = await executeRegisteredTool(c, actor, "deal-room-board", { workspace: "all" });
        // deal-room-board is shared by design; the personal morning composite is
        // not.  Account ownership is therefore filtered from authenticated
        // sponsor scope exactly as deal ownership is, never from caller input.
        return { items: value.deals.filter(ownOrShared),
          accounts: value.accounts.filter((row) => String(row?.account_owner || "").toLowerCase() === scope.sponsor) };
      });
      const loops = await section(async () => {
        const value = await executeRegisteredTool(c, actor, "loop-board",
          { kind: "open_loop", status: "open", owner: scope.sponsor, limit: 60 });
        return { items: value.loops };
      });
      const renewals = await section(async () => {
        const status = await c.query(
          "select t1_candidate_count, source_observed_at, freshness_state from v_renewal_decision_queue_status where owner_slug=$1",
          [scope.sponsor]);
        if (status.rows.length !== 1)
          return { state: "unavailable", reason: "source_unavailable", items: [] };
        if (status.rows[0].freshness_state === "empty")
          return { state: "empty", items: [], t1_candidate_count: 0,
            source_observed_at: status.rows[0].source_observed_at, freshness_state: "empty" };
        if (status.rows[0].freshness_state !== "ready")
          return { state: "unavailable", reason: "source_unavailable", items: [] };
        const rows = await c.query(
          `select display_name, org_name, vertical, city, county, state, est_lease_event,
                  tier_status, flag_status, has_channel, decision_count, source_observed_at,
                  freshness_state
             from v_renewal_decision_queue
            where owner_slug=$1
            order by est_lease_event nulls last, display_name
            limit 20`, [scope.sponsor]);
        return { items: rows.rows, t1_candidate_count: status.rows[0].t1_candidate_count,
          source_observed_at: status.rows[0].source_observed_at,
          freshness_state: status.rows[0].freshness_state };
      });
      // Only the immutable source-run states may reach section().  Do not let
      // section() derive ready/empty from a stale or altered source.
      if (renewals.state !== "unavailable" && !["ready", "empty"].includes(renewals.freshness_state)) {
        renewals.state = "unavailable";
        renewals.reason = "source_unavailable";
        renewals.items = [];
      }
      // V5-A05's morning approval batch: unread notifications minted by
      // raise-delivery-cadence-alert (producer 'v5-a05-delivery-cadence'),
      // for the authenticated sponsor's own actor. This is the "morning
      // approval batch goes through the existing morning brief and
      // notification feed" wiring -- no separate queue is built; a batched
      // item is simply an unread notification from this producer, surfaced
      // here AND reachable through notification-feed like any other.
      const assuranceCadence = await section(async () => {
        // Reads through ops.v5_a05_assurance_cadence_batch (migration 0617,
        // sealed as SCAC v75 by 0618), a narrow SECURITY DEFINER function
        // granted to carr_reader that refuses any non-partner recipient and
        // hides items still held for the morning window -- morning-brief
        // runs on the reader connection, and ops.notification/
        // ops.notification_read themselves carry no carr_reader grant
        // (tools/test-handler-reads-are-granted.py). Never read those tables
        // directly from a handler.
        const result = await c.query(
          "select ops.v5_a05_assurance_cadence_batch($1) as batch", [scope.sponsor]);
        const batch = result.rows[0]?.batch;
        return { items: Array.isArray(batch) ? batch : [] };
      });
      const costs = { ...morningCostSnapshot(undefined), ...await section(async () => {
        // Existing carr_reader grant and authenticated scope, like read-progress-board.
        // Ordinary brief reads never call billing providers.
        const result = await c.query(
          `select snapshot_json from board_snapshot
            where organization_tenant_id=$1 and sponsoring_human_slug=$2 and board_id=$3`,
          [organizationTenantForActor(actor), scope.sponsor, "system-costs"]);
        return morningCostSnapshot(result.rows[0]?.snapshot_json?.costs);
      }) };
      const sections = { today, claim_card: claimCard, deals, loops, renewals, assurance_cadence: assuranceCadence, costs };
      return {
        state: Object.values(sections).some((value) => value.state === "unavailable")
          ? "unavailable"
          : Object.values(sections).some((value) => value.state === "partial") ? "partial" : "ready",
        sponsor: scope.sponsor,
        sections,
      };
    },
  },

  "deal-board": {
    write: false,
    description: "Open pipeline grouped by phase. Never exposes Salesforce commission/close-date placeholders (they are placeholders, not data).",
    inputSchema: { type: "object", properties: {} },
    handler: async (c) => ({ deals: (await c.query(`select b.id, b.name, b.client_ref, b.client_name, b.deal_type,
      b.phase, b.phase_sort, b.segment, b.outcome, b.lead_owner, b.last_touch, b.notes_path,
      d.operating_state, d.parking_note, to_jsonb(d.invoiced_on)#>>'{}' as invoiced_on,
      to_jsonb(d.closed_on)#>>'{}' as closed_on, d.lane
      from v_deal_board b join v_deal_room_board d on d.id=b.id
      order by b.phase_sort, b.name /* dealboard:operating-state */`)).rows }),
  },

  "deal-room-board": {
    write: false,
    description: "The Deal Room home read: Salesforce-linked work records plus their active/parked operating state, national-account portfolio summaries, current partner identity, review clocks, market-agent assignments, and one open review session. Each record also carries field_base — the latest committed event id and time for every editable cell, read in the same statement as the values, which is what patch-deal-field takes as base_event_id. A cell with no history has no entry. workspace may be team, national_account, or all; no row is duplicated between workspaces.",
    inputSchema: { type: "object", properties: {
      workspace: { type: "string", enum: ["team","national_account","all"], default: "all" },
      account_client_id: { type: "string", description: "optional national-account client uuid" },
    } },
    handler: async (c, actor, args) => {
      const workspace = args.workspace || "all";
      // field_base: the latest committed event for each EDITABLE cell, read in
      // the SAME statement as the values it belongs to.
      //
      // Why it is here and not a second read: patch-deal-field bases on an event
      // id, and until now the board had no way to learn one except the changes
      // feed, which starts at the beginning of the log. So the first edit of a
      // session to a cell with any history sent no base at all, which this verb's
      // own concurrency rule treats as "any prior event conflicts" — a refusal
      // naming a months-old change as concurrent. This closes that.
      //
      // ONE STATEMENT is the point, not an economy. A value and its base read in
      // two statements can straddle a commit, and the dangerous half of that is a
      // base NEWER than the value shown: the next write would then be accepted
      // over a value the person never saw. Inside one statement both come from
      // one snapshot, so a concurrent write is either wholly visible here or
      // wholly invisible, and a partner's later edit still conflicts.
      //
      // One correlated subquery, not one query per deal: the ordering is the
      // record layer's own (recorded_at desc, id desc), the same pair
      // latestFieldConflict and revert-deal-field sort by, and a cell that has
      // never been edited simply has no key — which the client must read as "no
      // base", never as a base of null-meaning-anything.
      const deals = await c.query(
        `select b.id, b.name, b.type, b.phase, b.owner, b.attention,
                to_jsonb(b.next_date)#>>'{}' as next_date, b.next_step, b.market, b.segment,
                b.client_id, b.client_ref, b.client_name, b.account_client_id, b.account_client_ref,
                b.account_name, b.account_owner, b.market_agent,
                to_jsonb(b.last_touch)#>>'{}' as last_touch,
                to_jsonb(b.last_review_at)#>>'{}' as last_review_at, b.workspace_kind,
                b.operating_state, b.parking_reason, b.parking_note,
                to_jsonb(b.invoiced_on)#>>'{}' as invoiced_on,
                to_jsonb(b.closed_on)#>>'{}' as closed_on, b.lane, b.outcome,
                (select to_jsonb(pc) from v_deal_room_phase_change pc where pc.deal_id=b.id) as phase_change,
                to_jsonb(b.parked_at)#>>'{}' as parked_at, b.parked_by,
                coalesce((
                  select jsonb_object_agg(latest.field,
                           jsonb_build_object('id', latest.id,
                             'recorded_at', to_jsonb(latest.recorded_at)#>>'{}'))
                    from (select distinct on (e.field) e.field, e.id, e.recorded_at
                            from v_deal_room_event e
                           where e.subject_type='deal' and e.subject_id=b.id
                             and e.field = any($3::text[])
                           order by e.field, e.recorded_at desc, e.id desc) latest
                ), '{}'::jsonb) as field_base
           from v_deal_room_board b
          where ($1 = 'all' or b.workspace_kind = $1)
            and ($2::uuid is null or b.account_client_id = $2::uuid)
          order by b.attention desc, b.next_date nulls last, b.name
          /* dealroom:board-field-base */`,
        [workspace, args.account_client_id || null, [...DEAL_ROOM_FIELDS]]);
      const accounts = await c.query(
        `select account_client_id, account_client_ref, account_name, account_owner,
                open_deals, attention_deals, overdue_deals, stale_deals,
                to_jsonb(last_review_at)#>>'{}' as last_review_at, parked_deals
           from v_deal_room_account order by account_name`);
      const session = await c.query(
        `select session_id, workspace_kind, account_client_id,
                to_jsonb(started_at)#>>'{}' as started_at
           from v_deal_room_session
          where started_by=$1 and status='open'
            and ($2 = 'all' or workspace_kind=$2)
            and ($3::uuid is null or account_client_id=$3::uuid)
          order by started_at desc limit 1`,
        [actor.slug, workspace, args.account_client_id || null]);
      return { schema_version: 'local-deals-board.v1', actor: actor.slug, deals: deals.rows, accounts: accounts.rows,
        open_session: session.rows[0] || null };
    },
  },

  "capture-queue": {
    write: false,
    description: "Pending capture proposals for active sessions. These are untrusted suggestions only; no confidence score confirms one. A partner must call resolve-candidate for every disposition.",
    inputSchema: { type: "object", properties: {} },
    handler: async (c) => ({ candidates: (await c.query(
      `select id, session_id, kind, payload, evidence_quote, confidence::float8 as confidence, deal_name,
              to_jsonb(created_at)#>>'{}' as created_at
         from v_capture_candidate_queue
        order by confidence desc, created_at, id`)).rows }),
  },

  "get-call-context": {
    write: false,
    description: "Read the exact active Call Mode context for an explicit list of deal UUIDs. It never searches by name: an unknown, closed, or parked UUID refuses the whole request. Current participant party refs, names, emails, and roles are returned only for the requested deals.",
    inputSchema: { type: "object", properties: {
      deal_ids: { type: "array", minItems: 1, maxItems: 50, items: { type: "string" } },
    }, required: ["deal_ids"] },
    handler: async (c, _actor, args) => {
      const ids = args.deal_ids;
      const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu;
      if (!Array.isArray(ids) || !ids.length || ids.length > 50 || ids.some(id => !uuid.test(id)) ||
          new Set(ids).size !== ids.length)
        throw new ToolError({ error: "invalid_call_context_deals" });
      const rows = (await c.query(
        `select deal_id,deal_name,owner,operating_state,participant_party_id,participant_party_ref,
                participant_name,participant_email,participant_role
           from capture_call_context($1::uuid[])
          order by deal_name,deal_id,participant_role,participant_name
          /* capture:tool-call-context */`, [ids])).rows;
      if (new Set(rows.map(row => String(row.deal_id))).size !== ids.length)
        throw new ToolError({ error: "invalid_call_context_deals" });
      const byDeal = new Map();
      for (const row of rows) {
        const deal = byDeal.get(row.deal_id) || { id: row.deal_id, name: row.deal_name,
          owner: row.owner, operating_state: row.operating_state, participants: [] };
        if (row.participant_role) deal.participants.push({ party_id: row.participant_party_id || null,
          ref: row.participant_party_ref || null, name: row.participant_name || null,
          email: row.participant_email || null, role: row.participant_role });
        byDeal.set(row.deal_id, deal);
      }
      return { deals: [...byDeal.values()] };
    },
  },

  "lead-hot": {
    write: false,
    description: "Scored, unsuppressed leads (score, lane, est_lease_event, next_action_date). ALL of them surface — qualification is the human's job, never pre-filtered.",
    inputSchema: { type: "object", properties: { limit: { type: "integer", default: 30 } } },
    handler: async (c, _a, args) => ({ leads: (await c.query("select * from v_lead_hot order by score desc nulls last limit $1", [args.limit || 30])).rows }),
  },

  "lead-board": {
    write: false,
    description: "The complete, safe worked-lead board. All leads surface, including weak, suppressed, and terminal rows: qualification is the human's job and this read is never pre-qualified or silently truncated. Returns the authoritative base_version needed by update-lead, ordered stage vocabulary including empty stages, score/confidence/freshness signals, and no phone, email, address, notes, or raw source detail.",
    inputSchema: LEAD_WORKSPACE_SCHEMA,
    handler: async (c, _actor, args = {}) => {
      if (args.workspace === "leads") return readLeadWorkspace(c, args);
      const stages = (await c.query(
        `select slug,label,sort
           from v_lead_board_stage
          order by sort,slug`)).rows;
      const leads = (await c.query(
        `select id,registry_ref,name,specialty,city,county,state,lane,stage,
                party_id,
                stage_label,stage_sort,score,segment,suppressed,est_lease_event,
                event_confidence,last_touch,next_action_date,owner,owner_label,
                base_version,created_at,updated_at,
                (stage <> 'archived') as conversion_eligible,
                converted,stage_moves
           from v_lead_board
          order by stage_sort,suppressed,score desc nulls last,name,registry_ref`)).rows;
      const eligible=leads.filter(l=>l.stage!=="archived");
      return { generated_at: new Date().toISOString(), stages, leads,
        metrics:{nurture_count:eligible.filter(l=>l.stage==="nurture_drip" && !l.suppressed).length,
          conversion_denominator:eligible.length,converted_count:eligible.filter(l=>l.converted).length} };
    },
  },

  "claim-card": {
    write: false,
    description: "The claimable candidate reservoir: who Joe or Dell could turn into a lead today, nearest lease window first. THE GAP THIS CLOSES, and it is the same one read-loop closed for loops: promote-pool and decline-candidate both refuse without base_version and both tell the caller to 'read the row from v_pool / v_claim_card first' — and nothing in the verb layer could perform that read. The only reader was a generated markdown card in the vault, which the doctrine cutoff retired on 2026-08-19; without this verb the two claim verbs would name a surface that no longer exists. Returns pool_id and base_version on every row, so a promote or decline follows directly with no guess and no version_conflict. SAFE COLUMNS ONLY — the view carries no email, phone or address by construction (has_channel says a channel exists; the human reads the number off the lead record after claiming). Ranked, never filtered: rows whose window has already PASSED are shown with a negative days_to_window rather than dropped, because a passed window is still a live conversation and three of them expired unread the last time this list had no reader. `needs_contact_count` is the tail with no channel at all — research, not calls, counted rather than hidden.",
    inputSchema: { type: "object", properties: {
      limit: { type: "integer", default: 5, description: "how many claimable rows to return, nearest window first" },
      include_needs_contact: { type: "boolean", default: false, description: "also return the rows with no phone or email on file — a research queue, not a call list" },
    } },
    handler: async (c, _a, args) => {
      // SAME ORDERING AS pipelines/brief_pack.py's claim card, deliberately:
      // dated rows before undated, future windows before passed ones, then
      // nearest window, then score. Rule a8c55a47 — a manual path and an
      // automated path that do the same job must be the same code; this is the
      // closest that gets across two languages, so the clause is copied
      // verbatim rather than reinvented, and any change belongs in both.
      const order = `order by (est_lease_event is null),
                              (days_to_window < 0),
                              abs(days_to_window) nulls last,
                              score desc nulls last`;
      const channel = args.include_needs_contact ? "" : "where has_channel";
      const rows = (await c.query(
        `select pool_id, base_version, lane, display_name, org_name, vertical,
                city, county, state, segment, segment_play, score, score_basis,
                est_lease_event, est_basis, days_to_window, has_channel,
                needs_contact, dup_tier, dup_ref, dup_basis
           from v_claim_card ${channel} ${order} limit $1`,
        [args.limit || 5])).rows;
      const totals = (await c.query(
        `select count(*)::int as claimable,
                count(*) filter (where not has_channel)::int as needs_contact_count
           from v_claim_card`)).rows[0];
      return {
        showing: rows.length,
        claimable: totals.claimable,
        needs_contact_count: totals.needs_contact_count,
        candidates: rows,
        hint: "promote-pool or decline-candidate with the row's own pool_id and base_version. Every decline shortens this list permanently, which is the only thing that makes it shorter.",
      };
    },
  },

  "stale-records": {
    write: false,
    description: "Active deals gone quiet 14+ days, measured on last_touch (see v_last_touch; a deal inherits its client's touch since 0033). Replaces the hand-run staleness sweep. Empty can mean 'nothing stale' OR 'nothing captured' — check v_capture_coverage before trusting a clean result.",
    inputSchema: { type: "object", properties: {} },
    handler: async (c) => ({ stale: (await c.query("select * from v_stale_records order by days_quiet desc nulls first")).rows }),
  },

  "integrity-digest": {
    write: false,
    description: "The heartbeat's lines: row counts, export freshness (dead-man; stale/last_ok per target), writes_by_dell_24h, norm_owed_open, merge_queue, triage queue.",
    inputSchema: { type: "object", properties: {} },
    handler: async (c) => ({ digest: (await c.query("select * from v_integrity_digest")).rows }),
  },

  "read-loop": {
    write: false,
    description: "Read ONE loop and its current version. THE GAP THIS CLOSES: update-loop and close-loop both refuse without base_version and tell the caller to 'read the record first' — and until this verb existed, nothing could perform that read. The only way to learn a loop's version was to guess, take a version_conflict, and lift the number out of the error message. Pass `number` (the '#142' a human says, with or without the hash) or `loop_id`. A number can repeat across kinds, so an ambiguous number returns the candidates rather than picking one for you. Also returns `amended` (true once amend-closed-loop has ever corrected this loop's outcome) and `amendments`, the full correction trail oldest first — each with prior_outcome, new_outcome, prior_resolution, new_resolution, reason, actor and created_at.",
    inputSchema: { type: "object", properties: {
      number: { type: "string", description: "the loop number as a human writes it, with or without the leading #" },
      loop_id: { type: "string", description: "exact uuid; wins over number" },
      kind: { type: "string", enum: LOOP_KINDS, description: "narrows an ambiguous number" },
    } },
    handler: async (c, _a, args) => {
      const cols = `id as loop_id, kind, number, domain, blocker_class, blocker_detail, status,
                    title, body, owner, marker, since_text, unblocks, source_note, tier, personal_to,
                    to_jsonb(due_on)#>>'{}' as due_on, close_outcome,
                    to_jsonb(closed_at)#>>'{}' as closed_at, version,
                    to_jsonb(created_at)#>>'{}' as created_at,
                    to_jsonb(updated_at)#>>'{}' as updated_at`;
      if (args.loop_id) {
        const r = await c.query(`select ${cols} from loop_item where id=$1`, [args.loop_id]);
        if (!r.rows.length) return { error: "not_found", hint: "no loop carries that id" };
        return await withAmendmentHistory(c, r.rows[0]);
      }
      const num = String(args.number || "").replace(/^#/, "").trim();
      if (!num) return { error: "need_number_or_id", hint: "pass number (e.g. '142') or loop_id" };
      const params = [num];
      let sql = `select ${cols} from loop_item where number=$1`;
      if (args.kind) { params.push(args.kind); sql += ` and kind=$${params.length}`; }
      const r = await c.query(sql, params);
      if (!r.rows.length) return { error: "not_found", number: num };
      if (r.rows.length > 1) {
        return {
          error: "ambiguous_number",
          candidates: r.rows.map((x) => ({ loop_id: x.loop_id, kind: x.kind, status: x.status, title: x.title })),
          hint: "same number in more than one kind — pass kind to narrow",
        };
      }
      return await withAmendmentHistory(c, r.rows[0]);
    },
  },

  "loop-board": {
    write: false,
    description: "Every open loop with its domain, what it is blocked on, and its version — the live answer to 'what is still open and what is it waiting on'. THE GAP THIS CLOSES: that question used to be answered by reading a generated markdown render, which splits loops across four files by kind and is only as fresh as the last export; a session counting from those files gets a number that is both stale and partial. Defaults to open work loops. Pass blocker:'none' for the rows that predate the blocker requirement — that is the do-it-or-close-it pile, and the standing rule is never to re-file them. Every row carries its version, so a close needs no second read.",
    inputSchema: { type: "object", properties: {
      kind: { type: "string", enum: LOOP_KINDS, default: "open_loop" },
      status: { type: "string", enum: ["open", "done", "dropped", "any"], default: "open" },
      domain: { type: "string", description: "deals | prospecting | networking | marketing | business | system" },
      blocker: { type: "string", description: "a blocker class to filter to, or 'none' for rows naming no blocker, or 'any' for rows that name one" },
      owner: { type: "string", description: "'claude' for the autonomous drain queue — rows the system may finish and close on its own evidence. 'joe' or 'dell' for a person's pile. 'joint' for the legacy rows owned by two people at once, which no query can select for and nobody picks up." },
      search: { type: "string", description: "case-insensitive match against the title" },
      limit: { type: "integer", default: 60 },
      summary: { type: "boolean", description: "Payload budget: counts by status, blocker class and owner, plus per-loop only {number, kind, label (title or first 80 chars of body's first line), blocker_class, owner, since_text, due_on, version}. No bodies, no blocker_detail. Absent/false returns today's full rows." },
    } },
    handler: async (c, _a, args) => {
      const where = ["kind = $1"];
      const params = [args.kind || "open_loop"];
      const st = args.status || "open";
      if (st !== "any") { params.push(st); where.push(`status = $${params.length}`); }
      if (args.domain) { params.push(args.domain); where.push(`domain = $${params.length}`); }
      if (args.blocker === "none") where.push("blocker_class is null");
      else if (args.blocker === "any") where.push("blocker_class is not null");
      else if (args.blocker) { params.push(args.blocker); where.push(`blocker_class = $${params.length}`); }
      if (args.owner === "joint") where.push("owner ~ '[/+&,]|→|->'");
      else if (args.owner) { params.push(args.owner); where.push(`lower(owner) = lower($${params.length})`); }
      if (args.search) {
        params.push(`%${args.search}%`);
        // Search BOTH columns. 148 of the 150 open work loops carry a null
        // title and hold their text in body, so a title-only search matches
        // almost nothing — which looks identical to "no such loop".
        where.push(`(coalesce(title,'') || ' ' || coalesce(body,'')) ilike $${params.length}`);
      }
      params.push(Math.min(Number(args.limit) || 60, 300));
      if (args.summary) {
        // PAYLOAD BUDGET, not a new view. The board's full rows carry body-sized
        // labels and blocker_detail prose; at 100+ loops one board read can
        // outweigh every other verb call in a session. Summary mode answers the
        // three questions the board actually exists for — what is open, what is
        // it blocked on, who owns it — from the SAME filtered query, so both
        // modes always agree on which rows exist.
        const s = await c.query(
          `select number, kind, status, owner,
                  -- LABEL: title or first 80 chars of body's first line, bold
                  -- markers stripped — the same fallback the full row uses.
                  left(coalesce(
                    nullif(title, ''),
                    nullif(regexp_replace(split_part(body, E'\\n', 1), '\\*\\*', '', 'g'), '')
                  ), 80) as label,
                  blocker_class, since_text,
                  to_jsonb(due_on)#>>'{}' as due_on, version
             from loop_item
            where ${where.join(" and ")}
            order by domain nulls last,
                     coalesce(nullif(regexp_replace(number, '[^0-9]', '', 'g'), '')::int, 999999)
            limit $${params.length}`, params);
        const tally = (key) => {
          const out = {};
          for (const row of s.rows) {
            const k = row[key] == null ? "none" : String(row[key]);
            out[k] = (out[k] || 0) + 1;
          }
          return out;
        };
        return {
          summary: true, count: s.rows.length,
          by_status: tally("status"),
          by_blocker_class: tally("blocker_class"),
          by_owner: tally("owner"),
          loops: s.rows.map(({ status, ...loop }) => loop),
        };
      }
      const r = await c.query(
        `select number, kind, domain, status, owner, marker, title,
                -- LABEL, not title. Almost every loop predates the title column
                -- and keeps its text in body, so a board keyed on title alone
                -- returns a column of nulls and cannot identify anything. Fall
                -- back to the first line of body with the bold markers stripped.
                coalesce(
                  nullif(title, ''),
                  nullif(regexp_replace(split_part(body, E'\\n', 1), '\\*\\*', '', 'g'), '')
                ) as label,
                -- Surfaced rather than silently tolerated: a row owned by two
                -- people is owned by neither, and 110 of the 150 open work
                -- loops were written that way. New writes are refused; these
                -- are the legacy rows waiting to be split.
                (owner ~ '[/+&,]|→|->') as joint_owner,
                blocker_class, blocker_detail, since_text,
                to_jsonb(due_on)#>>'{}' as due_on, version
           from loop_item
          where ${where.join(" and ")}
          order by domain nulls last,
                   coalesce(nullif(regexp_replace(number, '[^0-9]', '', 'g'), '')::int, 999999)
          limit $${params.length}`, params);
      return { count: r.rows.length, loops: r.rows };
    },
  },

  // ===== writes (carr_writer connection, envelope enforced) =====

  "record-rule-enforcement-fallback": {
    write: true,
    authorityOnly: true,
    description: "Joe-authority-only V5-A02 fallback receipt for one rule. Records what the system must do when that rule's installed control is unavailable; it never guesses from enforcement class and never rewrites an earlier receipt. The current rule version and statement hash, the authority connection's actor, procedure reference, reason and idempotency key are bound by the database. Refusals are named: rule_enforcement_fallback_requires_joe_authority (Dell's authority connection), rule_enforcement_fallback_already_recorded (this rule version already has a receipt), rule_enforcement_fallback_idempotency_conflict (key reused for a different request). A later rule version needs a new receipt.",
    inputSchema: { type: "object", additionalProperties: false, properties: {
      idempotency_key: { type: "string" },
      rule_id: { type: "string", description: "Full UUID or current short rule id." },
      fallback_kind: { type: "string", enum: [
        "degraded_read_only", "documented_manual_procedure",
        "escalate_to_verified_partner", "refuse_closed",
      ] },
      procedure_ref: { type: "string" },
      reason: { type: "string" },
    }, required: ["idempotency_key", "rule_id", "fallback_kind", "procedure_ref", "reason"] },
    handler: async (c, actor, args) => withEnvelope(
      c, actor, "record-rule-enforcement-fallback", args, async () => {
        const ruleId = await resolveRuleId(c, args.rule_id);
        let recorded;
        try {
          recorded = await c.query(
            "select ops.record_rule_enforcement_fallback($1,$2,$3,$4,$5) as result",
            [ruleId, args.fallback_kind, args.procedure_ref,
             args.idempotency_key, args.reason]);
        } catch (e) {
          // The database names every refusal; surface that name, never a
          // raw driver error.
          if (RULE_ENFORCEMENT_FALLBACK_REFUSALS.has(e?.message))
            throw new ToolError({ error: e.message, rule_id: ruleId,
              ...(typeof e.detail === "string" ? { detail: e.detail } : {}) });
          throw e;
        }
        const result = recorded.rows[0]?.result;
        if (!result?.receipt_id)
          throw new ToolError({ error: "rule_enforcement_fallback_not_recorded", rule_id: ruleId });
        await writeEvent(c, actor, "record-rule-enforcement-fallback", "rule", ruleId, {
          new: { fallback_kind: result.fallback_kind,
            fallback_receipt_id: result.receipt_id,
            rule_version: result.rule_version },
          agent_rationale: args.reason,
          idempotency_key: args.idempotency_key,
        });
        return result;
      }),
  },

  "log-activity": {
    write: true,
    description: "Log a business touch (call, email, meeting, tour, text, note, LOI...) against a deal/client/lead/vendor. THE default verb after any real-world contact. occurred_at = when it happened (defaults now); anything missing goes in 'owed', never invented. Writes an activity row; contact-kind rows are what move last_touch and lift capture coverage.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      ref: { type: "string", description: "L-/C-/V- ref or deal name" },
      // 'analysis' added by ORDER 36 (one-writer Phase B). It is the LAST slug on
      // purpose: it is not a touch (is_contact=false in activity_kind, so it can
      // never move Last Touch) and it is the write path for dossier analysis
      // prose — summary is the title, detail is the long text, and the newest
      // one renders in full into DNA/Clients/prospects/<name>.md. Requires
      // migration 0028; without it the FK to activity_kind rejects the row.
      kind: { type: "string", enum: ["call","email_out","email_in","meeting","tour","text","note","counter_sent","counter_received","loi","lease_signed","task","analysis"] },
      summary: { type: "string" }, detail: { type: "string" },
      occurred_at: { type: "string", description: "ISO timestamp; omit for now" },
      owed: { type: "string", description: "what is missing (a figure, a name) — recorded as owed" },
      human_quote: { type: "string", description: "the human's literal words, if dictated" },
      links: { type: "array", maxItems: 10, description:
        "[ORDER 34] introductions carried by this touch become intro-graph edges NOW, atomically. REFS ONLY (L-/C-/V-/T-), never names — ambiguity is find's job, before this call.",
        items: { type: "object", properties: {
          from_ref: { type: "string" }, to_ref: { type: "string" },
          kind: { type: "string", description: "party_link_kind slug: knows, intro, intro_received, can_introduce, works_with, referral" },
          note: { type: "string" } }, required: ["from_ref","to_ref","kind"] } },
    }, required: ["idempotency_key","ref","kind","summary"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "log-activity", args, async () => {
      // A TOUCH CANNOT HAVE HAPPENED TOMORROW. Found 2026-08-15: of 59 vendors
      // carrying a last-touch date, four were dated after today — the nearest
      // three days out, which reads exactly like a booked meeting logged as
      // though it had already happened.
      //
      // This is not tidiness. last_touch is what staleness is measured FROM, so
      // a vendor whose last touch is in the future can never read as stale no
      // matter how long it has actually been. The row goes quiet and no surface
      // can tell.
      //
      // The field already meant this: occurred_at is documented above as "when
      // it happened", and the activity table draws the same boundary from the
      // other side — migration 0017 stopped a `note` moving last_touch because a
      // note is annotation rather than contact. The future has its own verbs.
      //
      // The window is for CLOCK SKEW between a caller and the server, not for
      // scheduling: minutes, deliberately not hours.
      const SKEW_MS = 5 * 60 * 1000;
      if (args.occurred_at) {
        const when = Date.parse(args.occurred_at);
        // An unparseable date is a different problem; this guard judges future
        // ones and does not invent a verdict on malformed input.
        if (!Number.isNaN(when) && when > Date.now() + SKEW_MS)
          throw new ToolError({ error: "occurred_at_in_future",
            occurred_at: args.occurred_at,
            why: "occurred_at records when a touch HAPPENED. A future date makes the subject " +
                 "permanently un-stale — every staleness measure counts from last_touch, so a " +
                 "row dated ahead of today can never surface as gone quiet.",
            hint: "If this already happened, use its real date. If it is SCHEDULED, it is not a " +
                  "touch yet: set-next-action carries the ball you owe, and add-critical-date " +
                  "carries a dated obligation on a deal." });
      }
      const s = await resolveSubject(c, args.ref);
      const r = await c.query(
        `insert into activity (occurred_at, actor_id, kind, summary, detail, owed, ${FK[s.type]}, source)
         values (coalesce($1::timestamptz, now()), $2, $3, $4, $5, $6, $7, 'stated') returning id, occurred_at`,
        [args.occurred_at || null, actor.id, args.kind, args.summary, args.detail || null, args.owed || null, s.id]);
      await writeEvent(c, actor, "log-activity", s.type, s.id,
        { new: { activity: r.rows[0].id, kind: args.kind }, human_quote: args.human_quote, idempotency_key: args.idempotency_key });
      const links = args.links && args.links.length
        ? await writeLinks(c, actor, args.links, args.idempotency_key) : [];
      return { ok: true, activity_id: r.rows[0].id, subject: s,
               ...(links.length ? { links } : {}) };
    }),
  },

  "stamp-touch": {
    write: true,
    description: "Truck shorthand for log-activity: one-line call/text stamp. 'Called Ferris, going well' and done. Sets last_touch. Contact kinds only — a note is annotation, not a touch (it would not move Last Touch since 0017); use log-activity kind:note or an event for annotation.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, ref: { type: "string" },
      kind: { type: "string", enum: ["call","text"], default: "call" },
      summary: { type: "string" } }, required: ["idempotency_key","ref","summary"] },
    handler: async (c, actor, args) =>
      executeRegisteredTool(c, actor, "log-activity", { ...args, kind: args.kind || "call" }),
  },

  // Added 2026-08-06 (loop #216): the missing half of the capture pipeline.
  // The ingest socket only ever INSERTS (status 'new'), so until this verb
  // existed nothing could LEAVE the queue — the digest's count could only
  // rise, and the Aug 6 triage found 40 rows with no way to clear one.
  // Deliberately NOT in any narrow profile (capture/away/probe/reviewer):
  // deciding what an inbox item became is an interactive judgment call.
  "triage-item": {
    write: true,
    description: "Close the loop on ONE inbox item a human has looked at: say what it became. status 'filed' = it became records (name them in filed_refs: activity ids or L-/C-/V- refs); 'rejected' = not ours to record (personal calendar noise, spam — say why in note); 'duplicate' = another row or an existing record already carries it. Only moves rows out of 'new'; an item already dispositioned reports its state and changes nothing. Payloads stay stored and UNTRUSTED — this records a disposition, it never acts on what the payload says. The review queue (run.sh review-queue) and today-triage both surface the item ids this takes.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      item_id: { type: "string", description: "ingest_inbox uuid, from the review queue or today-triage" },
      status: { type: "string", enum: ["filed","rejected","duplicate"] },
      filed_refs: { type: "array", items: { type: "string" }, description: "what it became — required when status is 'filed'" },
      note: { type: "string", description: "one line on why — stored as triage_note" },
    }, required: ["idempotency_key","item_id","status"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "triage-item", args, async () => {
      if (args.status === "filed" && !(Array.isArray(args.filed_refs) && args.filed_refs.length))
        throw new ToolError({ error: "filed_needs_refs",
          hint: "status 'filed' must name what the item became — pass filed_refs" });
      const r = await c.query(
        `update ingest_inbox set status=$2, triage_note=$3, filed_refs=$4
          where id=$1 and status='new'
          returning id, source, status`,
        [args.item_id, args.status, args.note || null,
         args.filed_refs ? JSON.stringify(args.filed_refs) : null]);
      if (!r.rows.length) {
        const cur = await c.query("select status from ingest_inbox where id=$1", [args.item_id]);
        if (!cur.rows.length) throw new ToolError({ error: "not_found", item_id: args.item_id });
        return { ok: true, item_id: args.item_id, already: cur.rows[0].status,
                 note: "already dispositioned; nothing changed" };
      }
      await writeEvent(c, actor, "triage-item", "inbox", args.item_id,
        { new: { status: args.status, filed_refs: args.filed_refs || null },
          idempotency_key: args.idempotency_key });
      return { ok: true, item_id: r.rows[0].id, source: r.rows[0].source, status: r.rows[0].status };
    }),
  },

  "set-next-action": {
    write: true,
    description: "Set ONE open ball on a subject (replaces that owner's previous open one; the other partner's stays untouched). Defaults to your ball. Only the server-issued Hermes CoS door may name its Joe sponsor as owner; created_by remains the runtime.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, ref: { type: "string" },
      description: { type: "string" }, due_on: { type: "string", description: "YYYY-MM-DD, optional" },
      owner: { type: "string", description: "optional server-derived owner; only the caller or a Hermes CoS runtime's Joe sponsor" } },
      required: ["idempotency_key","ref","description"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "set-next-action", args, async () => {
      const s = await resolveSubject(c, args.ref);
      const permitted = permittedActionOwnerSlugs(actor);
      const ownerSlug = args.owner === undefined || args.owner === null || args.owner === ""
        ? actor.slug : String(args.owner).trim().toLowerCase();
      if (!permitted.includes(ownerSlug))
        throw new ToolError({ error: "owner_not_permitted", owner: ownerSlug, permitted,
          hint: "a ball may be set for yourself; only the Hermes CoS door may hand one to its verified Joe sponsor, never Dell" });
      let ownerId = actor.id;
      if (ownerSlug !== actor.slug) {
        const owner = await c.query("select id from actor where slug=$1", [ownerSlug]);
        if (!owner.rows.length) throw new ToolError({ error: "actor_not_provisioned", slug: ownerSlug,
          hint: "the selected owner has no actor row" });
        ownerId = owner.rows[0].id;
      }
      // [amendment 8] Replacing an unfinished ball used to record the old one as
      // 'done'. It wasn't done — it was superseded. No-fabrication applies to
      // metadata too, and 'done' would inflate any completion measure built on this.
      await c.query(
        `update next_action set status='dropped', updated_by=$4 where subject_type=$2 and subject_id=$3
         and owner_id=$1 and status='open'`, [ownerId, s.type, s.id, actor.id]);
      const droppedPostCall = s.type === "deal" ? (await c.query(
        `update capture_post_call_action
            set status='dropped',updated_at=now(),completed_at=null
          where deal_id=$1 and owner_id=$2 and status='open'
          returning id,description /* capture:replace-post-call-actions */`,
        [s.id, ownerId])).rows : [];
      const r = await c.query(
        `insert into next_action (subject_type, subject_id, owner_id, due_on, description, created_by)
         values ($1,$2,$3,$4,$5,$6) returning id`,
        [s.type, s.id, ownerId, args.due_on || null, args.description, actor.id]);
      await writeEvent(c, actor, "set-next-action", s.type, s.id,
        { old: droppedPostCall.length ? { post_call_actions: droppedPostCall.map(x =>
            ({ id: x.id, description: x.description, status: "open" })) } : null,
          new: { summary: `ball → ${ownerSlug}: ${args.description}`,
            next_action: args.description, due: args.due_on, owner: ownerSlug,
            set_by: actor.slug,
            dropped_post_call_action_ids: droppedPostCall.map(x => x.id) },
          idempotency_key: args.idempotency_key });
      return { ok: true, next_action_id: r.rows[0].id, subject: s, owner: ownerSlug,
        dropped_post_call_action_ids: droppedPostCall.map(x => x.id) };
    }),
  },

  "complete-action": {
    write: true,
    description: "Mark YOUR open ball on a subject DONE — the thing actually happened. Say what came of it in `outcome` if there is anything to say. DONE MEANS DONE: if you are abandoning the ball or replacing it with something else, use set-next-action instead (that records the old one as 'dropped', which is what it was). Completing is what feeds the follow-up cadence, so a false 'done' schedules a real touch on a fiction.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, ref: { type: "string" },
      outcome: { type: "string", description: "optional: what came of it, in your words" } },
      required: ["idempotency_key","ref"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "complete-action", args, async () => {
      const s = await resolveSubject(c, args.ref);
      // Only the caller's own ball, exactly like set-next-action: Dell's stays
      // untouched. The next_action_touch trigger stamps updated_at, and the
      // cadence engine reads THAT as the completion date, so nothing here sets
      // it by hand.
      const r = await c.query(
        `update next_action set status='done', updated_by=$1
          where subject_type=$2 and subject_id=$3 and owner_id=$1 and status='open'
          returning id, description, due_on`, [actor.id, s.type, s.id]);
      const postCall = s.type === "deal" ? (await c.query(
        `update capture_post_call_action
            set status='done',updated_at=now(),completed_at=now()
          where deal_id=$1 and owner_id=$2 and status='open'
          returning id,description,due_on /* capture:complete-post-call-actions */`,
        [s.id, actor.id])).rows : [];
      if (!r.rows.length && !postCall.length) {
        const others = (await c.query(
          `select a.slug as owner, n.description, n.due_on from next_action n
             join actor a on a.id = n.owner_id
            where n.subject_type=$1 and n.subject_id=$2 and n.status='open'`, [s.type, s.id])).rows;
        const otherPostCall = s.type === "deal" ? (await c.query(
          `select a.slug as owner,pca.description,pca.due_on
             from capture_post_call_action pca join actor a on a.id=pca.owner_id
            where pca.deal_id=$1 and pca.status='open'
            /* capture:other-post-call-actions */`, [s.id])).rows : [];
        const openForOthers = [...others, ...otherPostCall];
        throw new ToolError({ error: "no_open_action", subject: s,
          open_for_others: openForOthers,
          hint: openForOthers.length
            ? "the open ball on this subject belongs to someone else — only its holder can complete it"
            : "nobody holds an open action here; log-activity records what happened, set-next-action sets the next one" });
      }
      for (const row of r.rows)
        await writeEvent(c, actor, "complete-action", s.type, s.id,
          { field: "status", old: { status: "open" },
            new: { next_action: row.description, next_action_id: row.id, status: "done",
                   outcome: args.outcome || null },
            human_quote: args.outcome || null, idempotency_key: args.idempotency_key });
      for (const row of postCall)
        await writeEvent(c, actor, "complete-action", "deal", s.id,
          { field: "status", old: { status: "open" },
            new: { post_call_action: row.description, post_call_action_id: row.id, status: "done",
                   outcome: args.outcome || null },
            human_quote: args.outcome || null, idempotency_key: args.idempotency_key });
      const completed = [
        ...r.rows.map(x => ({ next_action_id: x.id, description: x.description, source: "next_action" })),
        ...postCall.map(x => ({ next_action_id: x.id, description: x.description, source: "post_call_action" })),
      ];
      return { ok: true, completed, count: completed.length, subject: s };
    }),
  },

  "add-critical-date": {
    write: true,
    description: "A critical_date — a date with consequences (LOI expiry, lease expiration, option window, earnout). Surfaces in today-triage inside 14 days. source is REQUIRED — where did this date come from?",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" },
      kind: { type: "string" }, due_on: { type: "string" }, note: { type: "string" },
      source: { type: "string" } }, required: ["idempotency_key","deal","kind","due_on","source"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "add-critical-date", args, async () => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      const r = await c.query(
        `insert into critical_date (deal_id, kind, due_on, note, source, created_by)
         values ($1,$2,$3,$4,$5,$6) returning id`,
        [s.id, args.kind, args.due_on, args.note || null, args.source, actor.id]);
      await writeEvent(c, actor, "add-critical-date", "deal", s.id,
        { new: { kind: args.kind, due_on: args.due_on }, idempotency_key: args.idempotency_key });
      return { ok: true, critical_date_id: r.rows[0].id };
    }),
  },

  "record-executed-lease": {
    write: true,
    authorityOnly: true,
    description: "Record the current executed lease/abstract that CARR actually holds for a deal. This is the authenticated first-party renewal authority: expiration_on must be an exact sourced date, evidence_kind/evidence_ref are mandatory, and a replacement needs the current lease version. It never infers a date from a term, listing, comp, NPPES, or web research. Only the deal's current owning partner may write it.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" },
      base_version: { type: "integer" },
      executed_on: { type: "string" }, commencement_on: { type: "string" },
      expiration_on: { type: "string" }, term_months: { type: "integer" },
      evidence_kind: { type: "string", enum: ["executed_lease", "lease_amendment", "lease_abstract"] },
      evidence_ref: { type: "string" }, source: { type: "string" },
    }, required: ["idempotency_key", "deal", "executed_on", "expiration_on", "evidence_kind", "evidence_ref", "source"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "record-executed-lease", args, async () => {
      const isoDate = (value, field, required = false) => {
        if ((value === null || value === undefined || value === "") && !required) return null;
        const text = String(value || "");
        if (!/^\d{4}-\d{2}-\d{2}$/.test(text) || Number.isNaN(Date.parse(`${text}T00:00:00Z`)))
          throw new ToolError({ error: "invalid_lease_date", field, expected: "YYYY-MM-DD" });
        return text;
      };
      const executedOn = isoDate(args.executed_on, "executed_on", true);
      const commencementOn = isoDate(args.commencement_on, "commencement_on");
      const expirationOn = isoDate(args.expiration_on, "expiration_on", true);
      if (commencementOn && expirationOn <= commencementOn)
        throw new ToolError({ error: "invalid_lease_dates", hint: "expiration_on must be after commencement_on" });
      if (args.term_months !== undefined &&
          (!Number.isInteger(args.term_months) || args.term_months < 1 || args.term_months > 480))
        throw new ToolError({ error: "invalid_term_months", allowed: "1..480" });
      const evidenceKinds = new Set(["executed_lease", "lease_amendment", "lease_abstract"]);
      if (!evidenceKinds.has(args.evidence_kind))
        throw new ToolError({ error: "invalid_lease_evidence_kind", valid: [...evidenceKinds] });
      const evidenceRef = String(args.evidence_ref || "").trim();
      const source = String(args.source || "").trim();
      if (!evidenceRef || evidenceRef.length > 1000)
        throw new ToolError({ error: "lease_evidence_ref_required" });
      if (!source || source.length > 500)
        throw new ToolError({ error: "lease_source_required" });

      let inserted;
      try {
        inserted = (await c.query(
          `select lease_id,version,superseded_lease_id,deal_id,client_id
             from ops.record_executed_lease($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
          [args.deal, args.base_version ?? null, executedOn, commencementOn, expirationOn,
           args.term_months ?? null, args.evidence_kind, evidenceRef, source])).rows[0];
      } catch (error) {
        const message = String(error?.message || "");
        if (message.includes("does not own the current deal"))
          throw new ToolError({ error: "not_deal_owner" });
        if (message.includes("version conflict"))
          throw new ToolError({ error: "version_conflict" });
        if (message.includes("deal was not found"))
          throw new ToolError({ error: "deal_not_found" });
        if (message.includes("needs exact disambiguation"))
          throw new ToolError({ error: "needs_disambiguation", ref: args.deal });
        if (message.includes("no current lease exists"))
          throw new ToolError({ error: "no_current_lease" });
        throw error;
      }
      if (!inserted) throw new ToolError({ error: "lease_write_no_readback" });
      await writeEvent(c, actor, "record-executed-lease", "deal", inserted.deal_id,
        { new: { lease_id: inserted.lease_id, expiration_on: expirationOn, evidence_kind: args.evidence_kind,
            evidence_ref: evidenceRef, source }, idempotency_key: args.idempotency_key });
      return { ok: true, lease_id: inserted.lease_id, version: inserted.version,
        superseded_lease_id: inserted.superseded_lease_id || null };
    }),
  },

  "update-deal": {
    write: true,
    description: "Field-level change to a deal (deal_type, phase, segment, outcome, notes_path, salesforce_id, city, lane, invoiced_on). invoiced_on (YYYY-MM-DD) marks the deal invoiced, which takes it out of the Salesforce reconciliation absence scope (V5-RW02). deal_type uses the closed deal_type_ref vocabulary. Requires base_version from a fresh read; a same-field conflict means someone else wrote the same column — ask the human, never retry blind. A version bump from a DIFFERENT field (someone else's disjoint edit) is rebased automatically and reported back as `rebased`/`rebase_receipt`; nothing about this call's own fields is ever silently changed. To move a deal to a DIFFERENT CLIENT, use reassign-deal: client_id is deliberately not settable here.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" },
      base_version: { type: "integer" },
      fields: { type: "object", description: "subset of: deal_type, phase, segment, outcome, closed_on, won_value, notes_path, salesforce_id, city, lane, invoiced_on" } },
      required: ["idempotency_key","deal","base_version","fields"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "update-deal", args, async () => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      // city and lane joined the list in 0074, when they stopped being source_row
      // passthrough and became real columns. Before that they were unsettable,
      // which is why salesforce-diff could only ever REPORT a city move.
      // invoiced_on joined in 0733 (V5-RW02): a won deal stays in the Salesforce
      // reconciliation absence scope until it is marked invoiced.
      const allowed = ["deal_type","phase","segment","outcome","closed_on","won_value","notes_path",
                       "salesforce_id","city","lane","invoiced_on"];
      if ("client_id" in args.fields) throw new ToolError({ error: "use_reassign_deal",
        hint: "moving a deal between clients is structural, not a field edit — use reassign-deal" });
      const keys = Object.keys(args.fields).filter(k => allowed.includes(k));
      if (!keys.length) throw new ToolError({ error: "no_updatable_fields", allowed });
      // A real calendar date: the pattern, then a round trip, so 2026-13-45 or
      // 2026-02-30 is refused here instead of failing later as a raw cast error.
      if (keys.includes("invoiced_on") && args.fields.invoiced_on !== null &&
          !isCalendarDate(args.fields.invoiced_on))
        throw new ToolError({ error: "invalid_invoiced_on",
          hint: "invoiced_on is a calendar date YYYY-MM-DD, or null to clear it" });
      // Legacy phase edits share the Deal Room event stream. Acquire its lock
      // before versionGuard can lock the deal row, matching Deal Room lock order.
      if (keys.includes("phase")) await lockDealField(c, s.id, "phase");
      // touchedFields = keys: computed BEFORE the guard so a version bump from
      // some OTHER field can be told apart from a bump on one of THESE fields.
      // See versionGuard's own comment (CONFLICT TIERING, slice S6).
      const guard = await versionGuard(c, "deal", s.id, args.base_version, keys);
      const old = (await c.query(`select ${keys.join(",")} from deal where id=$1`, [s.id])).rows[0];
      const sets = keys.map((k, i) => `${k}=$${i + 2}`).join(", ");
      await c.query(`update deal set ${sets}, updated_by=$1 where id=$${keys.length + 2}`,
        [actor.id, ...keys.map(k => args.fields[k]), s.id]);
      for (const k of keys)
        await writeEvent(c, actor, "update-deal", "deal", s.id,
          { field: k, old: { [k]: old[k] }, new: { [k]: args.fields[k] },
            recorded_at_after_lock: k === "phase" || k === "invoiced_on", idempotency_key: args.idempotency_key });
      return { ok: true, updated: keys,
               ...(guard.rebased ? { rebased: true, rebase_receipt: guard.rebase_receipt } : {}) };
    }),
  },

  "new-deal": {
    write: true,
    description: "Create a deal on an existing client. THE GAP THIS CLOSES: until 2026-08-07 nothing in the record layer could create a deal — new-client makes only a client row, reassign-deal and set-lead both need a deal that already exists, and the ONLY insert into `deal` in the whole repo was pipelines/import_wave1.py, the one-time bulk import. So every deal in the book traced back to that import, and the six deals the 2026-08-07 Salesforce read found had nowhere to land. The client must exist first (new-client over a party): a deal hangs off a client, never free-floating, and this verb will not invent one. humanOnly on purpose — a new deal is a real commitment in a partner's book, and salesforce-diff deliberately never auto-adds one. deal_type and phase are validated by the database against deal_type_ref and deal_phase, so a bad slug is refused with the live list rather than guessed at. Refuses a duplicate name and a salesforce_id already in use, naming the deal that holds it. Commission and close date from Salesforce are PLACEHOLDERS and land in the two labelled placeholder columns, never in won_value.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      client: { type: "string", description: "C-ref or exact name of the client this deal belongs to" },
      name: { type: "string", description: "deal name; keep it the Salesforce Deal Name where one exists" },
      deal_type: { type: "string", description: "slug from deal_type_ref, e.g. startup / relocation / additional_office / renewal / expansion / other" },
      phase: { type: "string", description: "slug from deal_phase, e.g. pending / research / negotiation / legal / due_diligence / closing / closed" },
      segment: { type: "string" },
      city: { type: "string", description: "city of transaction" },
      lane: { type: "string", description: "slug from deal_lane: territory (CARR represents) or national (out-of-market referral). Salesforce's Out of Market Deal checkbox is the truth here — never infer it from the city." },
      salesforce_id: { type: "string", description: "Opportunity id (006...), the reconciliation key back to the system of record" },
      notes_path: { type: "string" },
      sf_commission_placeholder: { type: "number", description: "Salesforce commission figure — a PLACEHOLDER, never summed and never shown as pipeline value" },
      sf_close_date_placeholder: { type: "string", description: "Salesforce close date (YYYY-MM-DD) — a PLACEHOLDER, never a forecast" },
      reason: { type: "string", description: "why this deal is being opened; lands on the event as agent_rationale" },
      human_quote: { type: "string", description: "the partner's verbatim words, when they directed it" } },
      required: ["idempotency_key","client","name","deal_type","phase"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "new-deal", args, async () => {
      const s = await resolveSubject(c, args.client);
      if (s.type !== "client") throw new ToolError({ error: "not_a_client", resolved: s,
        hint: "a deal hangs off a client. Create the client first with new-client over a party." });

      // A second deal with the same name is nearly always a double-add, and the damage
      // (two records drifting apart, each half-updated) is worse than the inconvenience.
      const dupe = await c.query(
        "select subject_id, display_name from v_ref_index where subject_type='deal' and lower(display_name)=lower($1)",
        [args.name]);
      if (dupe.rows.length) throw new ToolError({ error: "deal_name_exists",
        existing: dupe.rows.map(r => ({ id: r.subject_id, name: r.display_name })),
        hint: "if this is genuinely a second deal for the same client, give it a distinguishing name" });

      let r;
      // SAVEPOINT, for the same reason insertOrgPartyGuarded takes one (defect
      // 18b12fda's review, 2026-08-14): after the insert fails, the enclosing
      // transaction is aborted (25P02) until rolled back, so the diagnostic
      // queries below used to die on the poisoned transaction and replace both
      // friendly answers with an opaque error. The mapping was dead code.
      await c.query("savepoint new_deal_insert");
      try {
        r = await c.query(
          `insert into deal (client_id, name, deal_type, phase, segment, city, lane, salesforce_id,
             notes_path, sf_commission_placeholder, sf_close_date_placeholder, created_by, updated_by)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$12) returning id`,
          [s.id, args.name, args.deal_type, args.phase, args.segment || null,
           args.city || null, args.lane || null,
           args.salesforce_id || null, args.notes_path || null,
           args.sf_commission_placeholder ?? null, args.sf_close_date_placeholder || null, actor.id]);
      } catch (e) {
        // Map the database's own guards to answers a caller can act on, rather than
        // leaking a raw driver error. The DB stays the authority on both vocabularies.
        if (e.code === "23505") {
          await c.query("rollback to savepoint new_deal_insert");
          const held = await c.query("select name from deal where salesforce_id=$1", [args.salesforce_id]);
          throw new ToolError({ error: "salesforce_id_in_use", salesforce_id: args.salesforce_id,
            held_by: held.rows[0]?.name ?? null,
            hint: "one Opportunity maps to exactly one deal; check whether this deal already exists under another name" });
        }
        if (e.code === "23503") {
          await c.query("rollback to savepoint new_deal_insert");
          // deal has three closed vocabularies behind FKs: deal_type_ref,
          // deal_phase and (since 0074) deal_lane. Name the right one.
          const con = e.constraint || "";
          const which = /deal_type/.test(con) ? "deal_type" : /lane/.test(con) ? "lane" : "phase";
          const table = { deal_type: "deal_type_ref", lane: "deal_lane", phase: "deal_phase" }[which];
          let valid = [];
          try { valid = (await c.query(`select slug from ${table} order by slug`)).rows.map(x => x.slug); }
          catch { /* the role may not read the ref table; the error below still names the field */ }
          throw new ToolError({ error: `unknown_${which}`, given: args[which], valid });
        }
        throw e;
      }

      await writeEvent(c, actor, "new-deal", "deal", r.rows[0].id,
        { new: { name: args.name, client: args.client, deal_type: args.deal_type, phase: args.phase,
                 salesforce_id: args.salesforce_id || null },
          human_quote: args.human_quote, agent_rationale: args.reason,
          idempotency_key: args.idempotency_key });
      return { ok: true, deal_id: r.rows[0].id, name: args.name, client_ref: args.client };
    }),
  },

  "reassign-deal": {
    write: true,
    description: "Move a deal onto the client it actually belongs to. THIS IS THE ONLY VERB THAT CHANGES deal.client_id — update-deal refuses that field on purpose, because re-pointing a deal changes whose book it sits in and is structural, not a field edit (the same reason set-lead owns the owner). Built 2026-08-02 for the Cadence Studio finding: an import filed THIRTEEN deals under C-131, twelve of them belonging to other franchisees who each had their own client record, so nine clients rendered as 'Active deal – no deal on file' while their deals sat under someone else's name. Requires base_version from a fresh read. Refuses a no-op, refuses a merged-away target, and records the old and new client on the event so the move is auditable. It does NOT touch the client rows themselves: a parent/sub-client structure (a national account over its franchisees) is expressed by party.org_id, not by moving deals up to the parent.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" },
      base_version: { type: "integer" },
      new_client: { type: "string", description: "C-ref or exact name of the client the deal really belongs to" },
      reason: { type: "string", description: "why it moved; lands on the event as agent_rationale" },
      human_quote: { type: "string", description: "the partner's verbatim words, when they directed the move" } },
      required: ["idempotency_key","deal","base_version","new_client"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "reassign-deal", args, async () => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      await versionGuard(c, "deal", s.id, args.base_version);

      const t = await resolveSubject(c, args.new_client);
      if (t.type !== "client") throw new ToolError({ error: "not_a_client", resolved: t,
        hint: "new_client must resolve to a client (C-ref or exact name), not a lead, vendor or deal" });

      // A merged-away client is a tombstone, not a destination. Moving a deal onto
      // one would hide it behind a pointer — the exact shape this verb exists to undo.
      const tgt = (await c.query("select merged_into from client where id=$1", [t.id])).rows[0];
      if (!tgt) throw new ToolError({ error: "not_found", table: "client", id: t.id });
      if (tgt.merged_into) throw new ToolError({ error: "client_merged_away",
        merged_into: tgt.merged_into, hint: "re-point to the surviving client instead" });

      const cur = (await c.query("select client_id from deal where id=$1", [s.id])).rows[0];
      if (cur.client_id === t.id) throw new ToolError({ error: "no_op",
        hint: "the deal already belongs to that client; nothing was written" });

      const label = async (id) => (await c.query(
        `select ref, display_name from v_ref_index where subject_type='client' and subject_id=$1`, [id]
      )).rows[0] || { ref: null, display_name: null };
      const from = await label(cur.client_id);
      const to = await label(t.id);

      await c.query("update deal set client_id=$1, updated_by=$2 where id=$3", [t.id, actor.id, s.id]);
      await writeEvent(c, actor, "reassign-deal", "deal", s.id, {
        field: "client_id",
        old: { client_id: cur.client_id, ref: from.ref, name: from.display_name },
        new: { client_id: t.id, ref: to.ref, name: to.display_name },
        agent_rationale: args.reason || null,
        human_quote: args.human_quote || null,
        idempotency_key: args.idempotency_key });
      return { ok: true, deal: s.id, from: { ref: from.ref, name: from.display_name },
               to: { ref: to.ref, name: to.display_name } };
    }),
  },

  "set-lead": {
    write: true,
    description: "THE human ownership handoff: make joe or dell the current lead on a deal. THIS IS THE ONLY VERB THAT SETS A DEAL'S OWNER — it writes the deal_participant row (role='lead') that v_deal_board exposes as lead_owner, so a null lead_owner is fixed here and NOT through update-deal. Ownership is a matter between the two humans, never a machine's call. Requires base_version from a fresh read; the locked deal version makes simultaneous handoffs conflict instead of silently replacing one another. Closes the old lead row, opens the new one, one event. The database enforces exactly one current lead.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" },
      new_lead: { type: "string", enum: ["joe","dell"] },
      base_version: { type: "integer" } },
      required: ["idempotency_key","deal","new_lead","base_version"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "set-lead", args, async () => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      await lockDealField(c, s.id, "owner");
      await versionGuard(c, "deal", s.id, args.base_version);
      const previousOwner = (await c.query("select owner from deal where id=$1", [s.id])).rows[0]?.owner ?? null;
      const na = await c.query("select id from actor where slug=$1", [args.new_lead]);
      const prev = await c.query(
        `update deal_participant set to_at=now() where deal_id=$1 and role='lead' and to_at is null
         returning actor_id`, [s.id]);
      await c.query(
        "insert into deal_participant (deal_id, actor_id, role, set_by) values ($1,$2,'lead',$3)",
        [s.id, na.rows[0].id, actor.id]);
      await c.query("update deal set owner=$1, updated_by=$2 where id=$3", [args.new_lead, actor.id, s.id]);
      await writeEvent(c, actor, "set-lead", "deal", s.id,
        { field: "owner", old: { owner: previousOwner, lead: prev.rows[0]?.actor_id || null },
          new: { owner: args.new_lead, lead: args.new_lead }, recorded_at_after_lock: true,
          idempotency_key: args.idempotency_key });
      return { ok: true, new_lead: args.new_lead };
    }),
  },

  "add-party": {
    write: true,
    description: "Create a party (person or org). CHECKS for existing matches first (email, similar name) and returns candidates INSTEAD of inserting when found — pass force_new:true only after the human confirms it is genuinely a different person. Never store 205-643-6555 (it is Dell's placeholder, not a contact).",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, name: { type: "string" },
      kind: { type: "string", enum: ["person","org"], default: "person" },
      org_name: { type: "string" }, phone: { type: "string" }, email: { type: "string" },
      city: { type: "string" }, state: { type: "string" }, county: { type: "string" },
      specialty: { type: "string" }, force_new: { type: "boolean" },
      research_evidence: RESEARCH_EVIDENCE_SCHEMA },
      required: ["idempotency_key","name"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "add-party", args, async () => {
      if (args.phone && args.phone.replace(/\D/g, "").endsWith("2056436555"))
        throw new ToolError({ error: "placeholder_phone", hint: "205-643-6555 is never stored as a contact" });
      if (!args.force_new) {
        const cand = await c.query(
          `select id, name, email, city,
                  ($1::text is not null and lower(email)=lower($1)) as exact_email_match
             from party where merged_into is null and
               (($1::text is not null and lower(email)=lower($1)) or name % $2)
           order by exact_email_match desc, similarity(name,$2) desc limit 5`, [args.email || null, args.name]);
        if (cand.rows.length) {
          // DECISIVE AUTO-RESOLUTION ONLY (WR-000019 slice S6, CONFLICT TIERING).
          // The single signal in this query strong enough to resolve without a
          // human is an EXACT match on a real identifier (email) — never
          // trigram name similarity alone, which routinely scores two
          // different real people or orgs as "similar" (that is the whole
          // reason this query exists). Auto-resolve only when there is
          // EXACTLY ONE candidate and it is that exact-identifier match;
          // anything else — multiple candidates, or a candidate that matched
          // on name similarity only — stays needs_confirm exactly as before.
          if (cand.rows.length === 1 && cand.rows[0].exact_email_match) {
            const existing = cand.rows[0];
            return { ok: true, party_id: existing.id, auto_resolved: true,
                     reason: "exact email match to an existing party; no duplicate created",
                     hint: "pass force_new:true to create a separate party anyway (e.g. a shared team inbox)" };
          }
          return { needs_confirm: true, candidates: cand.rows,
                   hint: "existing similar parties; reuse one, or resubmit force_new:true" };
        }
      }
      // THE GENERATOR, CLOSED (0059, 2026-08-02). This line used to INSERT an org
      // unconditionally with no lookup, so every contact minted a private copy of
      // their own employer: Henry Schein existed as 17 org rows, one per rep,
      // Patterson Dental as 10, and all 415 org rows had exactly one inbound person
      // — a distribution with a single bucket, which is the signature. That is why
      // "who do we know at X" could not be answered: there was no X, only copies.
      // 0059 consolidated the 115 surplus rows AND added a unique index, so this
      // insert would now raise unique_violation on any org that already exists.
      // org_party_id() normalises the name, returns the existing survivor, and
      // mints only when genuinely new — so the duplicate count stops being a
      // running total. Placeholders like '(TBD — enrich)' deliberately still mint
      // separately: collapsing those would assert six unrelated people share an
      // employer, which is a fabricated fact, not a merge.
      const kind = args.kind || "person";
      const evidence = kind === "person"
        ? researchEvidence(args.research_evidence,
          ["name", "company", "phone", "specialty", "market"], "add-party")
        : null;
      const emp = await employerOrgId(c, actor.id, kind, args.name, args.org_name);
      const insertSql =
        `insert into party (kind,name,org_id,phone,email,city,state,county,specialty,created_by,updated_by)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10) returning id`;
      const insertParams =
        [kind, args.name, emp.orgId, args.phone || null, args.email || null,
         args.city || null, args.state || null, args.county || null, args.specialty || null, actor.id];
      let row;
      if (kind === "org") {
        const g = await insertOrgPartyGuarded(c, "add_party_org", insertSql, insertParams, args.name);
        if (g.conflict)
          return { needs_confirm: true, candidates: g.conflict,
                   hint: "a live organisation with this exact normalised identity already exists — " +
                         "reuse it, or disambiguate the NAME (the way 'Carr Riggs Ingram (advisory)' " +
                         "does); the identity key is never weakened, even under force_new" };
        row = g.row;
      } else {
        row = (await c.query(insertSql, insertParams)).rows[0];
      }
      if (evidence) await stampResearch(c, actor, row.id, evidence);
      await writeEvent(c, actor, "add-party", "party", row.id,
        { new: { name: args.name }, idempotency_key: args.idempotency_key });
      return { ok: true, party_id: row.id,
               ...(emp.selfNamed ? { note: "org_name ignored: it names this organisation itself " +
                 "(an org is not its own employer; pass org_name on an org only for a PARENT org)" } : {}) };
    }),
  },

  // [ORDER 27 (a) + EXT (c)] One atomic call gives a deal its physical +
  // counterparty spine: building (exact-address match or create), space rows,
  // premises + premises_space, building_ownership rows, optional listing_side
  // participant. Vocabularies are the EXISTING CHECKs — nothing reopens here.
  "add-premises": {
    write: true,
    description: "Capture a deal's premises: the building (matched by exact address or created), its space rows (suite, SF, basis), the premises linkage, and the counterparty spine — building_ownership rows (owner / landlord_rep / property_manager / listing_agent) and optionally the deal's listing_side participant. Feeds the counterparty graph, the LOI Premises/Size slots, and the grid. Ownership parties resolve by REF, or are CREATED via new_party — an existing party is never name-matched.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      deal_ref: { type: "string", description: "deal name, or a C- ref when the client has exactly one deal" },
      label: { type: "string", description: "premises label, e.g. '4301 Spanish Trail, ~3,424 SF'" },
      building: { type: "object", properties: {
        address: { type: "string" }, city: { type: "string" }, state: { type: "string" },
        zip: { type: "string" }, name: { type: "string" } }, required: ["address"] },
      spaces: { type: "array", minItems: 1, items: { type: "object", properties: {
        suite: { type: "string" }, floor: { type: "number" },
        area_amount: { type: "number" },
        area_basis: { type: "string", enum: ["rentable","usable","county_heated","listed_unverified"],
          description: "what the PAPER says; omit for listed_unverified — never guessed upward" },
        condition: { type: "string" } }, required: [] } },
      ownership: { type: "array", maxItems: 10, items: { type: "object", properties: {
        party_ref: { type: "string", description: "ref of an EXISTING party (V-/C-/L-/T-)" },
        new_party: { type: "object", properties: {
          name: { type: "string" }, kind: { type: "string", enum: ["person","org"] },
          org_name: { type: "string" }, force_new: { type: "boolean" },
          research_evidence: RESEARCH_EVIDENCE_SCHEMA }, required: ["name"] },
        kind: { type: "string", enum: ["owner","landlord_rep","property_manager","listing_agent"] },
        also_listing_side: { type: "boolean", description: "also record this party as the deal's listing_side participant" },
        source: { type: "string" } }, required: ["kind"] } },
    }, required: ["idempotency_key","deal_ref","label","building","spaces"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "add-premises", args, async () => {
      // deal resolution: a C- ref resolves through the client's deals — exactly
      // one or refuse (amendment 7's rule, applied to the deal hop).
      let dealId;
      const s = await resolveSubject(c, args.deal_ref);
      if (s.type === "deal") dealId = s.id;
      else if (s.type === "client") {
        const d = await c.query("select id, name from deal where client_id = $1", [s.id]);
        if (!d.rows.length) throw new ToolError({ error: "no_deal_for_client", deal_ref: args.deal_ref });
        if (d.rows.length > 1)
          throw new ToolError({ error: "needs_disambiguation", deal_ref: args.deal_ref,
            candidates: d.rows.map(x => ({ name: x.name })), hint: "pass the deal name" });
        dealId = d.rows[0].id;
      } else throw new ToolError({ error: "not_a_deal", deal_ref: args.deal_ref, resolved: s.type });

      // building: exact-address match (case-insensitive), else create. More than
      // one match is a data problem to surface, never a coin flip.
      const b = args.building;
      const match = await c.query(
        `select id, address, city from building
          where lower(address) = lower($1) and merged_into is null`, [b.address]);
      let buildingId, buildingCreated = false;
      if (match.rows.length > 1)
        throw new ToolError({ error: "needs_disambiguation", address: b.address,
          candidates: match.rows, hint: "two building rows share this address — merge or pass city" });
      if (match.rows.length === 1) buildingId = match.rows[0].id;
      else {
        const ins = await c.query(
          `insert into building (address, city, state, zip, name, created_by, updated_by)
           values ($1,$2,$3,$4,$5,$6,$6) returning id`,
          [b.address, b.city || null, b.state || null, b.zip || null, b.name || null, actor.id]);
        buildingId = ins.rows[0].id; buildingCreated = true;
      }

      const spaceIds = [];
      for (const sp of args.spaces) {
        // [ORDER 34 review, fix 6] surface the schema's band as a ToolError,
        // not a truncated raw SQL check_violation.
        if (sp.area_amount != null && (sp.area_amount < 50 || sp.area_amount > 500000))
          throw new ToolError({ error: "area_out_of_band", area_amount: sp.area_amount,
            hint: "space.area_amount accepts 50-500000 SF; a 3,424 SF suite is 3424, not 3.424" });
        const basis = sp.area_amount != null ? (sp.area_basis || "listed_unverified") : sp.area_basis || null;
        const ins = await c.query(
          `insert into space (building_id, suite, floor, area_amount, area_basis, condition, created_by, updated_by)
           values ($1,$2,$3,$4,$5,$6,$7,$7) returning id`,
          [buildingId, sp.suite || null, sp.floor ?? null, sp.area_amount ?? null, basis, sp.condition || null, actor.id]);
        spaceIds.push(ins.rows[0].id);
      }

      const pr = await c.query(
        `insert into premises (deal_id, label, created_by) values ($1,$2,$3) returning id`,
        [dealId, args.label, actor.id]);
      const premisesId = pr.rows[0].id;
      for (const sid of spaceIds)
        await c.query("insert into premises_space (premises_id, space_id) values ($1,$2)", [premisesId, sid]);

      const ownershipOut = [];
      for (const o of args.ownership || []) {
        let partyId;
        if (o.party_ref && o.new_party)
          throw new ToolError({ error: "conflicting_party_inputs",
            hint: "an ownership row carries party_ref OR new_party, never both" });
        if (o.party_ref) partyId = await resolvePartyByRef(c, o.party_ref);
        else if (o.new_party) {
          // Same dedup intent as add-party's guard, expressed as a THROW so a
          // refused attempt never lands a tool_call row (safer for key hygiene;
          // add-party returns needs_confirm inline instead — deliberate divergence).
          if (!o.new_party.force_new) {
            const cand = await c.query(
              `select id, name, city from party where merged_into is null and name % $1
               order by similarity(name, $1) desc limit 5`, [o.new_party.name]);
            if (cand.rows.length)
              throw new ToolError({ error: "needs_confirm", name: o.new_party.name,
                candidates: cand.rows,
                hint: "similar parties exist; pass party_ref if it is one of them (when it has a ref), or new_party.force_new:true after the human confirms it is a different person" });
          }
          // Same generator, second site — see the note in add-party. 0059's unique
          // index makes the old blind insert a unique_violation waiting to happen,
          // and defect 18b12fda proved the self-collision variant (org restating
          // itself in org_name) fires even against clean data. Same helpers as
          // add-party; the conflict surfaces as a THROW here, per this site's
          // deliberate divergence noted above.
          const npKind = o.new_party.kind || "person";
          // A counterparty created while capturing premises is still a new
          // contact record.  Do not let the convenience path bypass the same
          // sourced-research gate as add-party.
          const npEvidence = researchEvidence(o.new_party.research_evidence,
            ["name", "company", "phone", "specialty", "market"], "add-premises.new_party");
          const npEmp = await employerOrgId(c, actor.id, npKind, o.new_party.name, o.new_party.org_name);
          const npSql = `insert into party (kind, name, org_id, created_by, updated_by)
             values ($1,$2,$3,$4,$4) returning id`;
          const npParams = [npKind, o.new_party.name, npEmp.orgId, actor.id];
          if (npKind === "org") {
            const g = await insertOrgPartyGuarded(c, "add_premises_org", npSql, npParams, o.new_party.name);
            if (g.conflict)
              throw new ToolError({ error: "needs_confirm", name: o.new_party.name,
                candidates: g.conflict,
                hint: "a live organisation with this exact normalised identity already exists — " +
                      "pass its ref as party_ref, or disambiguate the NAME; the identity key is " +
                      "never weakened, even under force_new" });
            partyId = g.row.id;
          } else {
            partyId = (await c.query(npSql, npParams)).rows[0].id;
          }
          await stampResearch(c, actor, partyId, npEvidence);
        } else throw new ToolError({ error: "ownership_needs_party",
          hint: "each ownership row carries party_ref or new_party" });
        await c.query(
          `insert into building_ownership (building_id, party_id, kind, source, created_by)
           values ($1,$2,$3,$4,$5)`,
          [buildingId, partyId, o.kind, o.source || "stated", actor.id]);
        if (o.also_listing_side) {
          // [ORDER 34 review, fix 5] check-before-insert: a second capture pass
          // on the same deal must not duplicate the participant row (which would
          // double-count the deal in counterparty history).
          const dup = await c.query(
            `select 1 from deal_participant
              where deal_id=$1 and party_id=$2 and role='listing_side' and to_at is null`,
            [dealId, partyId]);
          if (!dup.rows.length)
            await c.query(
              `insert into deal_participant (deal_id, party_id, role, set_by)
               values ($1,$2,'listing_side',$3)`, [dealId, partyId, actor.id]);
        }
        ownershipOut.push({ party_id: partyId, kind: o.kind,
          listing_side: !!o.also_listing_side });
      }

      await writeEvent(c, actor, "add-premises", "deal", dealId,
        { new: { premises: premisesId, building: buildingId, building_created: buildingCreated,
                 spaces: spaceIds.length, ownership: ownershipOut.length },
          idempotency_key: args.idempotency_key });
      return { ok: true, deal_id: dealId, premises_id: premisesId, building_id: buildingId,
               building_created: buildingCreated, space_ids: spaceIds, ownership: ownershipOut };
    }),
  },

  "new-lead": {
    write: true,
    description: "Create a lead over a new or existing party; mints the next L-ref atomically. Accepts score (integer 0-100 or null), score_reason (string or null), and owner (joe|dell). Alabama parties default to Dell when owner is omitted; other parties default to the caller. Stage must be an existing lead_stage slug (they were imported from the live registry).",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      party_id: { type: "string", description: "from add-party or find" },
      stage: { type: "string" }, lane: { type: "string" }, segment: { type: "string" },
      ...LEAD_SCORE_FIELDS,
      source_type: { type: "string" }, source_detail: { type: "string" } },
      required: ["idempotency_key","party_id","stage"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "new-lead", args, async () => {
      validateLeadScoreFields(args);
      // stage and lane are FOREIGN KEYS (lead_stage.slug, lead_lane.slug). They used
      // to go straight into the insert, so a plausible-but-wrong value — `lane:
      // "referral"`, which reads like an obvious lane and is not one — came back as
      // a bare "internal error" with nothing naming the field or the options.
      // Measured live 2026-08-10 creating Dr. Harlan's lead: three attempts failed
      // opaquely and the bare call succeeded, which tells the caller nothing about
      // WHICH field was wrong. Same failure class as loop #261.
      for (const [field, table] of [["stage", "lead_stage"], ["lane", "lead_lane"]]) {
        const v = args[field];
        if (!v) continue;
        const hit = await c.query(`select 1 from ${table} where slug=$1`, [v]);
        if (!hit.rows.length) {
          const all = await c.query(`select slug from ${table} order by slug`);
          throw new ToolError({ error: `unknown_${field}`, got: v,
            valid: all.rows.map(x => x.slug),
            hint: `${field} is a foreign key into ${table}; pass one of the listed slugs. Inventing a plausible one fails at the database, not here.` });
        }
      }
      const party = (await c.query("select state from party where id=$1", [args.party_id])).rows[0];
      if (!party) throw new ToolError({ error: "party_not_found" });
      const ownerSlug = args.owner ?? (party.state === "AL" ? "dell" : null);
      const owner = ownerSlug ? await resolveLeadOwner(c, ownerSlug)
        : { owner: actor.slug, owner_id: actor.id, owner_label: actor.display };
      const ref = (await c.query("select 'L-' || lpad(nextval('ref_lead_seq')::text, 3, '0') as r")).rows[0].r;
      const r = await c.query(
        `insert into lead (registry_ref, party_id, stage, lane, segment, source_type, source_detail,
           owner_id, owner_label, created_by, updated_by, score, score_reason)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10,$11,$12) returning id`,
        [ref, args.party_id, args.stage, args.lane || null, args.segment || null,
         args.source_type || null, args.source_detail || null, owner.owner_id, owner.owner_label,
         actor.id, args.score ?? null, args.score_reason ?? null]);
      await writeEvent(c, actor, "new-lead", "lead", r.rows[0].id,
        { new: { ref, score: args.score ?? null, score_reason: args.score_reason ?? null, ...owner }, idempotency_key: args.idempotency_key });
      return { ok: true, lead_id: r.rows[0].id, ref };
    }),
  },

  "promote-pool": {
    write: true,
    description: "Promote a candidate_pool row into a real lead: mints the party, mints the next L-ref, copies the identity, contact and est-lease-event stamps across, points the pool row at the new lead and flips it to 'promoted'. ONE-WAY BY DESIGN — there is no demote verb; a lead created in error is worked through the lead's own lifecycle. Only a row whose status is still 'pool' can promote: a 'promoted' row would duplicate, and a 'suppressed_dup' row already points at the record it duplicates. A dup_tier 'review' row IS promotable — that tier exists precisely so a weak match never silently blocks Joe. Read the row from v_pool first and pass its version as base_version.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      pool_id: { type: "string", description: "candidate_pool.id, from v_pool" },
      base_version: { type: "integer", description: "the pool row's version, from a fresh read" },
      stage: { type: "string", description: "lead_stage slug — a promoted lead is one Joe is working, so it needs a real stage" },
      lane: { type: "string", description: "lead_lane slug (optional)" },
      source_detail: { type: "string", description: "why this one, now — free text provenance" },
      research_evidence: RESEARCH_EVIDENCE_SCHEMA },
      required: ["idempotency_key","pool_id","base_version","stage","research_evidence"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "promote-pool", args, async () => {
      await versionGuard(c, "candidate_pool", args.pool_id, args.base_version);
      const p = (await c.query(
        `select id, source, source_key, status, dup_tier, dup_ref, name, org_name, vertical,
                city, county, state, email, phone, segment, est_lease_event, est_basis
           from candidate_pool where id = $1`, [args.pool_id])).rows[0];
      if (p.status !== "pool")
        throw new ToolError({ error: "not_promotable", status: p.status, dup_ref: p.dup_ref,
          hint: p.status === "promoted"
            ? "this row already became a lead; read v_pool for its promoted_ref"
            : "this row is marked as a duplicate of an existing record — work that record instead" });
      const evidence = researchEvidence(args.research_evidence,
        ["name", "company", "phone", "specialty", "market"], "promote-pool");

      // The org, when the source named one, becomes its own party so the lead
      // hangs off a person who belongs to a practice — the shape add-party and
      // every export view already assume.
      let orgId = null;
      if (p.org_name) {
        orgId = (await c.query(
          "insert into party (kind,name,created_by,updated_by) values ('org',$1,$2,$2) returning id",
          [p.org_name, actor.id])).rows[0].id;
        await stampResearch(c, actor, orgId, evidence);
      }
      const partyId = (await c.query(
        `insert into party (kind,name,org_id,phone,email,city,state,county,specialty,
                            created_by,updated_by)
         values ('person',$1,$2,$3,$4,$5,$6,$7,$8,$9,$9) returning id`,
        [p.name, orgId, p.phone || null, p.email || null, p.city || null,
         p.state || null, p.county || null, p.vertical || null, actor.id])).rows[0].id;
      await stampResearch(c, actor, partyId, evidence);

      const ref = (await c.query(
        "select 'L-' || lpad(nextval('ref_lead_seq')::text, 3, '0') as r")).rows[0].r;
      // est_lease_event rides along per Joe's ruling 3, and it keeps its est-
      // naming on the far side: it lands in lead.est_lease_event with its basis
      // in event_source, never in a field that reads as a confirmed date.
      const lead = (await c.query(
        `insert into lead (registry_ref, party_id, stage, lane, segment, source_type,
           source_detail, est_lease_event, event_source, owner_id, owner_label,
           created_by, updated_by)
         values ($1,$2,$3,$4,$5,'prospect-pool',$6,$7,$8,$9,$10,$9,$9) returning id`,
        [ref, partyId, args.stage, args.lane || null, p.segment || null,
         args.source_detail || `promoted from ${p.source} ${p.source_key}`,
         p.est_lease_event || null, p.est_basis || null, actor.id, actor.display])).rows[0].id;

      await c.query(
        `update candidate_pool set status='promoted', promoted_lead_id=$1, updated_by=$2
          where id=$3 and status='pool'`, [lead, actor.id, args.pool_id]);

      await writeEvent(c, actor, "promote-pool", "lead", lead,
        { new: { ref, from_pool: p.source_key, est_lease_event: p.est_lease_event },
          idempotency_key: args.idempotency_key });
      await writeEvent(c, actor, "promote-pool", "candidate_pool", args.pool_id,
        { field: "status", old: { status: "pool" }, new: { status: "promoted", lead: ref },
          idempotency_key: args.idempotency_key });
      return { ok: true, lead_id: lead, ref, party_id: partyId,
               est_lease_event: p.est_lease_event, est_basis: p.est_basis };
    }),
  },

  "decline-candidate": {
    write: true,
    description: "Record that a HUMAN looked at a candidate and said no. This is promote-pool's missing counterpart, and it is the only thing that makes the claim card shorter. Measured 2026-08-09: six lanes had accumulated 9,870 candidates and promoted zero, ever, because a candidate rejected at the board stayed exactly as claimable as before and came back on every future card forever. A decline is NOT a suppression: suppression is a machine's assertion about identity and can be wrong, a decline is a human's judgment about fit and no sweep re-litigates it. The reason is REQUIRED and is the input to the lane-retirement decision, since 'no contact channel' is a fixable lane defect, 'out of territory' is a mis-scoped lane, and 'not a fit' is a lane working correctly with a low hit rate. Nothing is deleted: the row keeps its research and its provenance, it just stops being presented. Read the row from v_claim_card or v_pool first and pass its version as base_version.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      pool_id: { type: "string", description: "candidate_pool.id, from v_claim_card" },
      base_version: { type: "integer", description: "the pool row's version, from a fresh read" },
      reason: { type: "string", description: "why, in the human's own words. Required. One line is enough, but it must say something a lane owner could act on." } },
      required: ["idempotency_key","pool_id","base_version","reason"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "decline-candidate", args, async () => {
      const reason = (args.reason || "").trim();
      // Refused rather than defaulted. A blank reason would satisfy the database
      // constraint's letter if it were only NOT NULL, and would tell the lane
      // owner nothing — which is the whole point of collecting it.
      if (!reason)
        throw new ToolError({ error: "reason_required",
          hint: "say why in your own words: no contact channel, out of territory, "
              + "corporate-owned, already represented, not a fit. The reason is what "
              + "makes a lane's decline pattern readable." });

      await versionGuard(c, "candidate_pool", args.pool_id, args.base_version);
      const p = (await c.query(
        `select id, source, source_key, name, status, promoted_lead_id
           from candidate_pool where id = $1`, [args.pool_id])).rows[0];
      if (!p)
        throw new ToolError({ error: "not_found", table: "candidate_pool", id: args.pool_id });

      // Idempotent on an already-declined row, and REFUSING on a promoted one.
      // Declining something that already became a lead would silently strand the
      // lead: promote-pool is one-way by design and there is no demote, so the
      // honest answer is to work the lead's own lifecycle instead.
      if (p.status === "declined")
        return { ok: true, pool_id: p.id, already: "declined",
                 note: "already declined; nothing changed" };
      if (p.status !== "pool")
        throw new ToolError({ error: "not_declinable", status: p.status,
          lead_id: p.promoted_lead_id || null,
          hint: p.status === "promoted"
            ? "this candidate already became a lead. Declining here would strand it; "
              + "work the lead's own lifecycle instead."
            : "this row is already marked as a duplicate of a record we hold" });

      await c.query(
        `update candidate_pool
            set status='declined', declined_at=now(), declined_by=$1,
                decline_reason=$2, updated_by=$1
          where id=$3 and status='pool'`, [actor.id, reason, args.pool_id]);

      await writeEvent(c, actor, "decline-candidate", "candidate_pool", args.pool_id,
        { field: "status", old: { status: "pool" },
          new: { status: "declined", reason, lane: p.source },
          idempotency_key: args.idempotency_key });

      return { ok: true, pool_id: p.id, name: p.name, lane: p.source,
               status: "declined", reason,
               note: "off the claim card permanently; the research and provenance are kept" };
    }),
  },

  "log-outreach": {
    write: true,
    description: "THE DISPOSITION STEP: say what happened after you actually tried to reach someone, in ONE action. Completes your open ball on that subject, logs the touch at its real time, and either sets the next ball or closes the lead out. Use this instead of calling log-activity and set-next-action separately, because separately is how a touch gets logged with no next step or a next step gets set with no touch, and both halves are needed for the follow-up cadence to run. Outcomes: 'connected' you spoke with them · 'left_message' you tried and did not reach them · 'sent' you sent an email or text · 'no_channel' the number or address does not work · 'not_interested' they said no · 'do_not_contact' they asked you to stop. The first four REQUIRE a next date, because a touch with no next step is how a lead dies quietly. The last two close the lead and refuse a next date.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      ref: { type: "string", description: "L-/C-/V- ref or deal name" },
      channel: { type: "string", enum: ["call","email","text","meeting","tour"],
                 description: "how you reached out. Ignored when outcome is no_channel, since nothing was reached." },
      outcome: { type: "string",
                 enum: ["connected","left_message","sent","no_channel","not_interested","do_not_contact"] },
      summary: { type: "string", description: "what happened, in your words. Required: this is the line a future session reads instead of guessing." },
      occurred_at: { type: "string", description: "ISO timestamp of the ACTUAL contact. Omit only when it just happened. Never backfill a past touch with the current time: a false recent date suppresses the staleness alarm the field exists to raise." },
      next_on: { type: "string", description: "YYYY-MM-DD, the next step's date. Required for connected, left_message, sent and no_channel." },
      next_step: { type: "string", description: "what you will do next. Required with next_on." },
      detail: { type: "string" },
      human_quote: { type: "string", description: "their literal words, if worth keeping" } },
      required: ["idempotency_key","ref","outcome","summary"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "log-outreach", args, async () => {
      const OPEN = ["connected","left_message","sent","no_channel"];
      const CLOSING = { not_interested: "closed_lost", do_not_contact: "do_not_contact" };
      const isOpen = OPEN.includes(args.outcome);

      // A touch with no next step is how a lead dies quietly, so the open
      // outcomes refuse to be recorded without one. This is the same posture
      // decline-candidate takes on its reason: the field that makes the record
      // useful is required at the door rather than nagged about afterwards.
      if (isOpen && !(args.next_on && args.next_step))
        throw new ToolError({ error: "next_step_required", outcome: args.outcome,
          hint: "pass next_on (YYYY-MM-DD) and next_step. If there genuinely is no "
              + "next step because they said no, the outcome is 'not_interested', "
              + "not a touch with an empty future." });
      if (!isOpen && (args.next_on || args.next_step))
        throw new ToolError({ error: "closing_outcome_takes_no_next", outcome: args.outcome,
          hint: "this outcome closes the lead; a next step would contradict it" });

      const s = await resolveSubject(c, args.ref);

      // no_channel is NOT a contact and must never move last_touch: nothing was
      // reached. 'note' is is_contact=false in activity_kind, which is exactly
      // the honest record — the attempt happened, the touch did not.
      const KIND = { call: "call", email: "email_out", text: "text",
                     meeting: "meeting", tour: "tour" };
      const kind = args.outcome === "no_channel" ? "note"
                 : (KIND[args.channel] || (args.outcome === "sent" ? "email_out" : "call"));

      const act = await c.query(
        `insert into activity (occurred_at, actor_id, kind, summary, detail, ${FK[s.type]}, source)
         values (coalesce($1::timestamptz, now()), $2, $3, $4, $5, $6, 'stated')
         returning id, occurred_at`,
        [args.occurred_at || null, actor.id, kind,
         `[${args.outcome}] ${args.summary}`, args.detail || null, s.id]);

      // COMPLETING THE OPEN BALL IS THE POINT, not a side effect. The cadence
      // engine fires on next_action.status='done' and has spawned 0 actions ever
      // because complete-action has been called exactly ONCE in the system's
      // history. Wiring completion into the verb a human actually reaches for
      // after a call is what starts that engine, with no change to the engine.
      // Only the caller's own ball, exactly like complete-action: the partner's
      // stays untouched.
      const done = await c.query(
        `update next_action set status='done', updated_by=$1
          where subject_type=$2 and subject_id=$3 and owner_id=$1 and status='open'
          returning id, description`, [actor.id, s.type, s.id]);
      const postCallDone = s.type === "deal" ? (await c.query(
        `update capture_post_call_action
            set status='done',updated_at=now(),completed_at=now()
          where deal_id=$1 and owner_id=$2 and status='open'
          returning id,description,due_on /* capture:outreach-complete-post-call-actions */`,
        [s.id, actor.id])).rows : [];

      let nextId = null, closed = null;
      if (isOpen) {
        const n = await c.query(
          `insert into next_action (subject_type, subject_id, owner_id, description,
                                    due_on, created_by)
           values ($1,$2,$3,$4,$5::date,$3) returning id`,
          [s.type, s.id, actor.id, args.next_step, args.next_on]);
        nextId = n.rows[0].id;
      } else if (s.type === "lead") {
        // do_not_contact sets the EXISTING suppressed flag as well as the stage.
        // The flag is the mechanical gate every surface already honours; the
        // stage is the human-readable reason. Setting only the stage would leave
        // a future sweep free to pick the person back up, which is the one
        // mistake here that costs more than a lost deal.
        const stage = CLOSING[args.outcome];
        await c.query(
          `update lead set stage=$1, suppressed=$2, updated_by=$3 where id=$4`,
          [stage, args.outcome === "do_not_contact", actor.id, s.id]);
        closed = stage;
      } else {
        throw new ToolError({ error: "closing_outcome_needs_a_lead", subject: s,
          hint: "not_interested and do_not_contact set a LEAD's terminal stage. For a "
              + "client or a deal, the outcome belongs on the deal itself via update-deal." });
      }

      await writeEvent(c, actor, "log-outreach", s.type, s.id,
        { new: { activity: act.rows[0].id, outcome: args.outcome, kind,
                 completed: done.rows[0]?.id || postCallDone[0]?.id || null,
                 completed_post_call_action_ids: postCallDone.map(row => row.id),
                 next_action: nextId, stage: closed },
          human_quote: args.human_quote, idempotency_key: args.idempotency_key });

      const completedActions = [
        ...done.rows.map(row => ({ id: row.id, description: row.description, source: "next_action" })),
        ...postCallDone.map(row => ({ id: row.id, description: row.description, source: "post_call_action" })),
      ];

      return { ok: true, subject: s, activity_id: act.rows[0].id,
               occurred_at: act.rows[0].occurred_at, outcome: args.outcome,
               completed_action: done.rows[0]?.description || postCallDone[0]?.description || null,
               completed_actions: completedActions,
               completed_post_call_action_ids: postCallDone.map(row => row.id),
               next_action_id: nextId, next_on: args.next_on || null,
               stage: closed,
               note: completedActions.length
                 ? "your open ball on this subject was completed, which is what feeds the follow-up cadence"
                 : "no open ball of yours existed on this subject; nothing to complete" };
    }),
  },

  "new-client": {
    write: true,
    description: "Create a client over a party; mints the next C-ref (roster_ref). Sets client_status and acquisition_source. ALWAYS ask how they found us (acquisition_source) at intake — consult attribution starts day one.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, party_id: { type: "string" },
      status: { type: "string" }, vertical: { type: "string" }, subtype: { type: "string" },
      acquisition_source: { type: "string" }, acquisition_detail: { type: "string" },
      research_evidence: RESEARCH_EVIDENCE_SCHEMA },
      required: ["idempotency_key","party_id","status","acquisition_source","research_evidence"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "new-client", args, async () => {
      const evidence = researchEvidence(args.research_evidence,
        ["practice_name", "address", "phone", "specialty", "practitioners", "hours"], "new-client");
      await stampResearch(c, actor, args.party_id, evidence);
      const ref = (await c.query("select 'C-' || lpad(nextval('ref_client_seq')::text, 3, '0') as r")).rows[0].r;
      const r = await c.query(
        `insert into client (roster_ref, party_id, status, vertical, subtype, acquisition_source,
           acquisition_detail, owner_id, owner_label, created_by, updated_by)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$8,$8) returning id`,
        [ref, args.party_id, args.status, args.vertical || null, args.subtype || null,
         args.acquisition_source, args.acquisition_detail || null, actor.id, actor.display]);
      await writeEvent(c, actor, "new-client", "client", r.rows[0].id,
        { new: { ref }, idempotency_key: args.idempotency_key });
      return { ok: true, client_id: r.rows[0].id, ref };
    }),
  },

  "new-vendor": {
    write: true,
    description: "Create a vendor over a party; sets vendor stage; mints V-<CODE>-### (pass the category code explicitly: CPA, LEN, GC...). A Claude-found vendor enters at the prospect stage until a real call happens — that is a standing rule, not a suggestion.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, party_id: { type: "string" },
      category: { type: "string" }, ref_code: { type: "string", description: "CPA / LEN / GC / ..." },
      stage: { type: "string", description: "a vendor_stage SLUG, not the label — e.g. prospect_uncontacted, building_working_on_it. A wrong value comes back with the full valid list." },
      research_evidence: RESEARCH_EVIDENCE_SCHEMA },
      required: ["idempotency_key","party_id","category","ref_code","stage","research_evidence"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "new-vendor", args, async () => {
      const evidence = researchEvidence(args.research_evidence,
        ["company", "title", "category", "market", "phone", "website", "deal_side"], "new-vendor");
      // Before the ref is minted: a rejected call must not burn a V-### number.
      await validateVendorStage(c, args.stage);
      await stampResearch(c, actor, args.party_id, evidence);
      const ref = (await c.query(
        "select 'V-' || $1 || '-' || lpad(nextval('ref_vendor_seq')::text, 3, '0') as r",
        [args.ref_code.toUpperCase()])).rows[0].r;
      const r = await c.query(
        `insert into vendor (vendor_ref, party_id, category, stage, owner_id, owner_label,
           created_by, updated_by) values ($1,$2,$3,$4,$5,$6,$5,$5) returning id`,
        [ref, args.party_id, args.category, args.stage, actor.id, actor.display]);
      await writeEvent(c, actor, "new-vendor", "vendor", r.rows[0].id,
        { new: { ref }, idempotency_key: args.idempotency_key });
      return { ok: true, vendor_id: r.rows[0].id, ref };
    }),
  },

  "update-vendor": {
    write: true,
    description: "Field-level change to a vendor (stage, seeking, offers, referral_active, territory, out_of_market). stage takes a vendor_stage SLUG, not the label; a wrong one comes back with the full valid list. base_version required.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, vendor: { type: "string" },
      base_version: { type: "integer" }, fields: { type: "object" } },
      required: ["idempotency_key","vendor","base_version","fields"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "update-vendor", args, async () => {
      const s = await resolveSubject(c, args.vendor);
      if (s.type !== "vendor") throw new ToolError({ error: "not_a_vendor", resolved: s });
      await versionGuard(c, "vendor", s.id, args.base_version);
      // [0069] category_slug + verticals joined the list — the columns existed since
      // 0001/0050 but no verb could reach them, which left the 63 null-category
      // vendors unfixable (loop #199). category_slug, not free-text category: 0050
      // deprecated the free-text field after a stage value got stored as a
      // profession, and reopening it here would reopen that defect.
      const allowed = ["stage","seeking","offers","referral_active","territory","rivalry_group","out_of_market","intro_notes","category_slug","verticals","loan_programs","trust_override","deal_evidence"];
      const keys = Object.keys(args.fields).filter(k => allowed.includes(k));
      if (!keys.length && !Object.hasOwn(args.fields,"deal_evidence") && !Object.hasOwn(args.fields,"verify_deal_history")) throw new ToolError({ error: "no_updatable_fields", allowed });
      // Pre-validate rather than letting the FK abort the transaction: a poisoned
      // transaction cannot even fetch the slug list to explain itself.
      if (keys.includes("category_slug") && args.fields.category_slug !== null) {
        const slugs = (await c.query("select slug from vendor_category order by sort")).rows.map(r => r.slug);
        if (!slugs.includes(args.fields.category_slug))
          throw new ToolError({ error: "unknown_category_slug", got: args.fields.category_slug, allowed: slugs,
            hint: "a rare type is an INSERT into vendor_category by a human, never a guess" });
      }
      if (keys.includes("stage") && args.fields.stage !== null)
        await validateVendorStage(c, args.fields.stage);
      if (keys.includes("verticals") && args.fields.verticals !== null &&
          !(Array.isArray(args.fields.verticals) && args.fields.verticals.every(v => typeof v === "string")))
        throw new ToolError({ error: "verticals_not_array", hint: 'pass an array of strings, e.g. ["dental","vet"]' });
      if (keys.includes("trust_override")) {
        try { args.fields.trust_override = trustedOverride(args.fields.trust_override, actor); }
        catch (error) { throw new ToolError({ error: error.code }); }
      }
      if (keys.includes("loan_programs") && args.fields.loan_programs !== null &&
          !(Array.isArray(args.fields.loan_programs) && args.fields.loan_programs.every(v => typeof v === "string" && v.length <= 200)))
        throw new ToolError({ error: "loan_programs_invalid" });
      let entries = [];
      if (Object.hasOwn(args.fields, "deal_evidence")) {
        try { entries = dealEvidenceEntries(args.fields.deal_evidence); }
        catch (error) { throw new ToolError({ error: error.code }); }
        for (const entry of entries) {
          const live = await c.query("select d.id from public.deal d join public.client dc on dc.id=d.client_id join public.party dp on dp.id=dc.party_id where d.id=$1 and dc.merged_into is null and dp.merged_into is null and dp.deleted_at is null", [entry.deal_id]);
          if (!live.rows.length) throw new ToolError({ error: "deal_evidence_deal_not_found" });
        }
        args.fields.deal_evidence = entries;
      }
      if (Object.hasOwn(args.fields, "verify_deal_history")) {
        try { requireRelationshipPartner(actor, "deal_history_verification_refused"); }
        catch (error) { throw new ToolError({ error: error.code }); }
        if (args.fields.verify_deal_history !== true) throw new ToolError({ error: "deal_history_verification_refused" });
        args.fields.deal_history_verified_at = new Date().toISOString();
        keys.push("deal_history_verified_at");
      } else if (Object.hasOwn(args.fields, "deal_evidence")) {
        args.fields.deal_history_verified_at = null; keys.push("deal_history_verified_at");
      }
      const old = keys.length ? (await c.query(`select ${keys.join(",")} from vendor where id=$1`, [s.id])).rows[0] : {};
      const sets = keys.map((k, i) => `${k}=$${i + 2}`).join(", ");
      await c.query(`update vendor set ${sets ? sets + ", " : ""}updated_by=$1 where id=$${keys.length + 2}`,
        [actor.id, ...keys.map(k => k === "deal_evidence" ? JSON.stringify(args.fields[k]) : args.fields[k]), s.id]);
      for (const k of keys)
        await writeEvent(c, actor, "update-vendor", "vendor", s.id,
          { field: k, old: { [k]: old[k] }, new: { [k]: args.fields[k] }, idempotency_key: args.idempotency_key });
      return { ok: true, updated: keys };
    }),
  },

  // [loop #383] Found by the 2026-08-14 health audit: L-118 and L-135 are live
  // clients stuck on lead_stage 'nurture_drip' mid-deal, still on the receiving
  // end of the prospecting newsletter, because nothing in the 89-verb registry
  // could move an EXISTING lead's stage. new-lead and promote-pool are
  // creation-only. log-outreach's disposition step touches lead.stage too, but
  // only on the two CLOSING outcomes (not_interested -> closed_lost,
  // do_not_contact -> do_not_contact) — there was no way to move a lead FORWARD
  // through its own funnel, or to correct one an import or a stuck drip left
  // behind. update-lead is that writer.
  "claim-lead": {
    write: true, humanOnly: true,
    description: "Claim an unowned New lead for the authenticated human. Preserves stage, checks the current version and exact lifecycle links, and records the ownership change.",
    inputSchema: { type: "object", additionalProperties: false, properties: {
      idempotency_key: { type: "string" }, expected_actor: { type: "string", minLength: 1 }, lead: { type: "string" }, base_version: { type: "integer" }
    }, required: ["idempotency_key","lead","base_version","expected_actor"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "claim-lead", args, async () => {
      if (args.expected_actor && args.expected_actor !== actor.slug) throw new ToolError({ error: "account_changed" });
      if (!canExercisePartnerAuthority(actor)) throw new ToolError({ error: "human_confirmation_required" });
      const subject = await resolveSubject(c, args.lead);
      if (subject.type !== "lead") throw new ToolError({ error: "not_a_lead" });
      await versionGuard(c, "lead", subject.id, args.base_version);
      const { current: row } = await lockLeadLifecycle(c, subject.id);
      if (!row || !row.live_party || row.stage !== "new" || !row.contact_eligible || row.owner_id || row.client_id || row.is_client || row.linked_client)
        throw new ToolError({ error: "lead_not_claimable" });
      const owner = (await c.query("select id,slug,display_name from actor where slug=$1 and active", [partnerAuthoritySlugForActor(actor)])).rows[0];
      if (!owner) throw new ToolError({ error: "human_owner_unavailable" });
      await c.query("update lead set owner_id=$1,owner_label=$2,updated_by=$3 where id=$4", [owner.id, owner.display_name, actor.id, subject.id]);
      await writeEvent(c, actor, "claim-lead", "lead", subject.id, { field: "owner_id", old: { owner_id: null },
        new: { owner_id: owner.id }, idempotency_key: args.idempotency_key });
      return { ok: true, lead_id: subject.id, owner: owner.slug };
    }),
  },

  "link-lead-client": {
    write: true, humanOnly: true,
    description: "Confirm one lead belongs to an existing client, by exact IDs and an explicit human choice. Records the client pointer without merging parties or creating a client/deal. Refuses suppression, stale versions and an already linked lead.",
    inputSchema: { type: "object", additionalProperties: false, properties: {
      idempotency_key: { type: "string" }, expected_actor: { type: "string", minLength: 1 }, lead: { type: "string" }, base_version: { type: "integer" },
      client_id: { type: "string" }, confirmed: { type: "boolean", const: true }
    }, required: ["idempotency_key","lead","base_version","client_id","confirmed","expected_actor"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "link-lead-client", args, async () => {
      if (args.expected_actor && args.expected_actor !== actor.slug) throw new ToolError({ error: "account_changed" });
      if (!canExercisePartnerAuthority(actor) || args.confirmed !== true) throw new ToolError({ error: "human_confirmation_required" });
      const subject = await resolveSubject(c, args.lead);
      if (subject.type !== "lead") throw new ToolError({ error: "not_a_lead" });
      await versionGuard(c, "lead", subject.id, args.base_version);
      const { current, target } = await lockLeadLifecycle(c, subject.id, args.client_id);
      if (!current || !current.live_party || current.is_client || !current.contact_eligible || current.client_id || current.linked_client)
        throw new ToolError({ error: "lead_not_linkable" });
      if (!target) throw new ToolError({ error: "client_not_found" });
      await c.query("update lead set client_id=$1,updated_by=$2 where id=$3", [target.id, actor.id, subject.id]);
      await writeEvent(c, actor, "link-lead-client", "lead", subject.id, { field: "client_id",
        old: { client_id: null }, new: { client_id: target.id }, idempotency_key: args.idempotency_key });
      return { ok: true, lead_id: subject.id, client_id: target.id };
    }),
  },

  "update-lead": {
    write: true,
    description: "Field-level change to a lead (score, score_reason, owner, stage, lane, segment, source_type, source_detail, suppressed, est_lease_event, next_action_date, notes_path, notes, event_source, event_confidence, report_back_due, drip_campaign, drip_added, sf_deal). stage and lane are FOREIGN KEYS into lead_stage/lead_lane; a wrong slug comes back with the full valid list rather than a bare internal error. do_not_contact is inseparable from suppressed=true, and only a human may clear an existing suppression instruction. base_version required from a fresh read; a conflict means someone else wrote — surface it to the human, never auto-retry. party_id (identity) and client_id (the lead-to-client conversion pointer) are deliberately absent from fields: neither is a field edit through this verb, the same posture update-deal takes on client_id and update-party-contact takes on identity fields generally (rule 5d44d3f3) — a discrepancy there is a different kind of correction, not a value to overwrite in place.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, expected_actor: { type: "string", minLength: 1 }, lead: { type: "string" },
      base_version: { type: "integer" },
      stage_review: { type: "object", additionalProperties: false, properties: { reason: { type: "string", minLength: 1, maxLength: 1000 }, evidence_ids: { type: "array", items: { type: "string" }, maxItems: 20 }, undo_event_id: { type: "string" }, human_quote: { type: "string", maxLength: 1000 } }, required: ["reason", "evidence_ids"] },
      fields: { type: "object", properties: LEAD_SCORE_FIELDS, description: "subset of: score, score_reason, owner, stage, lane, segment, source_type, source_detail, suppressed, est_lease_event, next_action_date, notes_path, notes, event_source, event_confidence, report_back_due, drip_campaign, drip_added, sf_deal" } },
      required: ["idempotency_key","lead","base_version","fields"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "update-lead", args, async () => {
      validateLeadScoreFields(args.fields);
      const reviewed = Object.hasOwn(args, "stage_review") ? validateStageReview(args.stage_review, args.fields, ToolError) : null;
      if (reviewed?.undo_event_id) {
        if (!canExercisePartnerAuthority(actor)) throw new ToolError({ error: "human_confirmation_required" });
        if (!reviewed.human_quote?.trim()) throw new ToolError({ error: "undo_human_quote_required" });
      }
      if (args.expected_actor && args.expected_actor !== actor.slug) throw new ToolError({ error: "account_changed" });
      const s = await resolveSubject(c, args.lead);
      if (s.type !== "lead") throw new ToolError({ error: "not_a_lead", resolved: s });
      await versionGuard(c, "lead", s.id, args.base_version);
      const allowed = ["stage","lane","segment","source_type","source_detail","suppressed",
                       "est_lease_event","next_action_date","notes_path","notes","event_source",
                       "event_confidence","report_back_due","drip_campaign","drip_added","sf_deal","score","score_reason","owner"];
      const keys = Object.keys(args.fields).filter(k => allowed.includes(k));
      if (!keys.length) throw new ToolError({ error: "no_updatable_fields", allowed });
      // Pre-validate rather than letting the FK abort the transaction, same reason
      // new-lead checks stage/lane up front: once the violation fires the
      // transaction is poisoned and cannot even run the query that would list the
      // valid slugs, so the caller gets nothing to correct with.
      for (const [field, table] of [["stage", "lead_stage"], ["lane", "lead_lane"]]) {
        if (!keys.includes(field) || args.fields[field] === null) continue;
        const hit = await c.query(`select 1 from ${table} where slug=$1`, [args.fields[field]]);
        if (!hit.rows.length) {
          const all = await c.query(`select slug from ${table} order by slug`);
          throw new ToolError({ error: `unknown_${field}`, got: args.fields[field],
            valid: all.rows.map(x => x.slug),
            hint: `${field} is a foreign key into ${table}; pass one of the listed slugs, never the label.` });
        }
      }
      const current = (await c.query("select stage,suppressed from lead where id=$1", [s.id])).rows[0];
      const nextStage = keys.includes("stage") ? args.fields.stage : current.stage;
      const nextSuppressed = keys.includes("suppressed") ? args.fields.suppressed : current.suppressed;
      if (keys.includes("stage") && (current.stage === "archived" || nextStage === "archived") && !canExercisePartnerAuthority(actor))
        throw new ToolError({error:"archive_requires_partner"});
      if (nextStage === "do_not_contact" && nextSuppressed !== true) {
        throw new ToolError({ error: "do_not_contact_requires_suppression",
          hint: "do_not_contact is a standing instruction, not only a funnel label; pass stage='do_not_contact' and suppressed=true together" });
      }
      if (current.stage === "do_not_contact" && nextStage !== "do_not_contact" && nextSuppressed !== false) {
        throw new ToolError({ error: "suppression_clear_required",
          hint: "moving a do_not_contact lead requires the same explicit human correction to set suppressed=false" });
      }
      if (current.suppressed && nextSuppressed === false && !canExercisePartnerAuthority(actor)) {
        throw new ToolError({ error: "suppression_clear_requires_human",
          hint: "a standing suppression instruction may be cleared only by an authenticated human" });
      }
      let stageReview = null;
      if (reviewed) {
        const ids = reviewed.evidence_ids;
        const evidence = ids.length ? (await c.query(
          "select id,occurred_at,kind,connected from activity where lead_id=$1 and id=any($2::uuid[]) and occurred_at<=now()", [s.id, ids])).rows : [];
        if (evidence.length !== ids.length) throw new ToolError({ error: "stage_evidence_mismatch" });
        if (args.fields.stage === "engaged" && evidence.some(row => ["call","text"].includes(row.kind) && row.connected !== true))
          throw new ToolError({ error: "stage_evidence_not_contact" });
        if (reviewed.undo_event_id) {
          const last = (await c.query(`select * from v_lead_stage_transition
            where lead_id=$1 order by mutation_order desc limit 1`, [s.id])).rows[0];
          if (!last || !last.automatic || last.event_id !== reviewed.undo_event_id.toLowerCase() || last.prior_stage !== args.fields.stage || last.stage !== current.stage)
            throw new ToolError({ error: "undo_changed" });
        }
        stageReview = { ...reviewed, evidence_ids: ids,
          evidence_date: evidence.map(row => new Date(row.occurred_at).toISOString()).sort().at(-1) || null };
      }
      const owner = keys.includes("owner") ? await resolveLeadOwner(c, args.fields.owner) : null;
      const storage = Object.fromEntries(keys.filter(k => k !== "owner").map(k => [k, args.fields[k]]));
      if (owner) Object.assign(storage, { owner_id: owner.owner_id, owner_label: owner.owner_label });
      const columns = Object.keys(storage);
      const old = (await c.query(`select ${columns.join(",")}${owner ? ",(select slug from actor where id=lead.owner_id) as owner" : ""} from lead where id=$1`, [s.id])).rows[0];
      const sets = columns.map((k, i) => `${k}=$${i + 2}`).join(", ");
      await c.query(`update lead set ${sets}, updated_by=$1 where id=$${columns.length + 2}`,
        [actor.id, ...Object.values(storage), s.id]);
      for (const k of keys)
        await writeEvent(c, actor, "update-lead", "lead", s.id,
          { recorded_at_after_lock: k === "stage", field: k, old: { [k]: old[k], ...(k === "owner" ? { owner_id: old.owner_id, owner_label: old.owner_label } : {}) }, new: { [k]: args.fields[k], ...(k === "owner" ? owner : {}), ...(k === "stage" && stageReview ? { stage_review: stageReview } : {}) },
            ...(k === "stage" && stageReview ? { cause: stageReview.undo_event_id ? "human_correction" : undefined,
              human_quote: stageReview.human_quote, agent_rationale: stageReview.reason } : {}), idempotency_key: args.idempotency_key });
      return { ok: true, updated: keys };
    }),
  },

  // [0069, loop #199] The promotion path from evidence to live contact data. Before
  // this verb, record-finding could store a verified cell/email/title beside the
  // record but NOTHING could write it onto the party — contact-enrichment-weekly
  // hit the same wall every Thursday, and the 2026-08-06 Outlook mining run left
  // 8 verified facts stranded in record_flag.
  "update-party-contact": {
    write: true,
    description: "Promote a VERIFIED contact fact onto a party: phone (office), cell (mobile), email, title, city, county — CONTACT FACTS ONLY. Identity fields (name, org, npi, specialty) are deliberately out of reach: a discrepancy there goes through record-finding's proposes_correction and is applied by the owning partner, never by this verb (rule 5d44d3f3). source is REQUIRED on every call — provenance is binding, and the usual value is the record-finding row or thread being promoted. Accepts any ref (P-####, V-/C-/L-/T-, or a name); a role ref resolves to the PERSON under it, and a merged party hops to its survivor (reported in the result). base_version is the PARTY's version, from a fresh read. Placeholder guard: a CARR agent's own number or any carr.us address in a client/vendor contact field is a placeholder, never data — refused, not stored.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      party: { type: "string", description: "P-#### ref, a role ref (V-/C-/L-/T-), or a name" },
      base_version: { type: "integer" },
      fields: { type: "object", properties: {
        phone: { type: ["string","null"] }, cell: { type: ["string","null"] },
        email: { type: ["string","null"] }, title: { type: ["string","null"] },
        city: { type: ["string","null"] }, county: { type: ["string","null"] } },
        additionalProperties: false },
      source: { type: "string", description: "where the fact came from: 'record-finding <kind> observed <date>', 'outlook thread <subject> <date>', 'practice website', ..." } },
      required: ["idempotency_key","party","base_version","fields","source"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "update-party-contact", args, async () => {
      if (!args.source || !args.source.trim())
        throw new ToolError({ error: "missing_source", hint: "a contact fact without provenance is a rumour; say where it came from" });
      const s = await resolveSubject(c, args.party);
      if (s.type === "deal")
        throw new ToolError({ error: "not_a_party", hint: "a deal has no contact fields; pass the person or their role ref" });
      let partyId;
      if (s.type === "party") partyId = s.id;
      else {
        const r = await c.query(
          "select party_id from v_ref_index where subject_type=$1 and subject_id=$2", [s.type, s.id]);
        if (!r.rows.length || !r.rows[0].party_id)
          throw new ToolError({ error: "no_party_under_ref", resolved: s });
        partyId = r.rows[0].party_id;
      }
      // A merged party is a pointer; writing to a tombstone strands the fact.
      const hop = await c.query("select merged_into from party where id=$1", [partyId]);
      if (!hop.rows.length) throw new ToolError({ error: "not_found", table: "party", id: partyId });
      const hopped = hop.rows[0].merged_into !== null;
      if (hopped) partyId = hop.rows[0].merged_into;

      const allowed = ["phone","cell","email","title","city","county"];
      const keys = Object.keys(args.fields).filter(k => allowed.includes(k));
      if (!keys.length) throw new ToolError({ error: "no_updatable_fields", allowed,
        hint: "contact facts only; identity corrections go through record-finding proposes_correction" });

      // Placeholder rule 54e2bcb9: an agent's own details standing in for a contact
      // nobody had. Stored, they read as enriched while being emptier than a blank.
      for (const k of ["phone","cell"]) {
        if (args.fields[k] && String(args.fields[k]).replace(/\D/g, "").endsWith("2056436555"))
          throw new ToolError({ error: "placeholder_phone", field: k,
            hint: "205-643-6555 is a CARR agent's own line, never a contact — record the field as unknown instead" });
      }
      if (args.fields.email && /@carr\.us\s*$/i.test(String(args.fields.email).trim()))
        throw new ToolError({ error: "placeholder_email",
          hint: "a carr.us address in a contact field is a placeholder, never data — record the field as unknown instead" });

      await versionGuard(c, "party", partyId, args.base_version);
      const clean = {};
      for (const k of keys)
        clean[k] = (k === "phone" || k === "cell") ? fmtPhoneUS(args.fields[k]) : args.fields[k];
      const old = (await c.query(`select ${keys.join(",")} from party where id=$1`, [partyId])).rows[0];
      const sets = keys.map((k, i) => `${k}=$${i + 2}`).join(", ");
      await c.query(`update party set ${sets}, updated_by=$1 where id=$${keys.length + 2}`,
        [actor.id, ...keys.map(k => clean[k]), partyId]);
      for (const k of keys)
        await writeEvent(c, actor, "update-party-contact", "party", partyId,
          { field: k, old: { [k]: old[k] }, new: { [k]: clean[k] },
            agent_rationale: `source: ${args.source}`, idempotency_key: args.idempotency_key });
      return { ok: true, party_id: partyId, updated: keys, hopped_to_survivor: hopped || undefined };
    }),
  },

  "record-counter": {
    write: true,
    description: "Log a negotiation round: whose paper (side), the economics (rate REQUIRES its basis — never a bare number), TI, free rent, term, PLUS what that side CLAIMED about its own position (\"best and final\", \"the owner won't go below 18\", \"we walk Friday\") and how the submarket stood when they said it. Use it after every counter, and log the claims at the same time — a claim is only ever falsifiable against the rounds that come after it, so a claim not captured now is uncomputable for ever. Round number auto-increments per deal+side if omitted. Out-of-band rates ask for confirm. NOT a place for a characterisation of a human being: 'aggressive', 'bluffs', 'reasonable' have no field here and never will — claims[] records what was SAID, and whether it was later contradicted is computed at read time.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" },
      side: { type: "string", enum: ["tenant","landlord","buyer","seller"] },
      proposed_on: { type: "string", description: "YYYY-MM-DD; defaults today" },
      rate_amount: { type: "number" },
      rate_basis: { type: "string", enum: ["usd_sf_yr","usd_sf_mo","usd_mo_gross","usd_yr_gross"] },
      ti_amount: { type: "number" }, ti_basis: { type: "string", enum: ["usd_total","usd_sf"] },
      free_rent_months: { type: "number" }, term_months: { type: "integer" },
      options_note: { type: "string" }, escalator: { type: "string" },
      expires_on: { type: "string", description: "YYYY-MM-DD. THE DEADLINE LIVES HERE — 'this offer dies Friday' is this field, never a claims[] row; a deadline claim is refused on purpose (0063), because a later round from the same side dated after this date already falsifies it." },
      submarket_condition: { type: "string", description: "soft | balanced | tight — how the submarket stood WHEN THIS ROUND was proposed (0063; validated against the submarket_condition ref table, so widening it is a row a human adds). It separates leverage from skill: a landlord in a tight market concedes nothing because he need not, and scoring that as toughness credits the market to the man. Record it once per deal — the scorecard reads the latest non-null. OMIT IT when you do not know; blank means not recorded and is never a synonym for 'balanced'." },
      claims: { type: "array", maxItems: 6, description:
        "[0063] What this side CLAIMED about its own position ON THIS ROUND. A list, not a field, because a side routinely makes three at once (\"best and final, the owner won't go below eighteen, and we have another tenant looking\") and one slot would keep one and discard two. Observations only — what was said, on this round. 'deadline' is not loggable here; that is expires_on.",
        items: { type: "object", properties: {
          type: { type: "string", description: "negotiation_claim_type slug: finality (best and final), authority (\"the owner won't go below X\"), walk_away (\"we're done\"), competing_interest (\"another tenant is looking\" — logged for the history, permanently excluded from every score because nothing could ever falsify it)" },
          stated_floor: { type: "number", description: "the number named in an AUTHORITY claim when it differs from this round's own rate — \"won't go below 18\" while offering 19. Omit when the claim was about the round's own position; never a guess." },
          stated_floor_basis: { type: "string", enum: ["usd_sf_yr","usd_sf_mo","usd_mo_gross","usd_yr_gross","usd_total","usd_sf_total"], description: "REQUIRED whenever stated_floor is given — the same no-bare-numbers rule the rate follows" },
          quote: { type: "string", description: "their words, as close to verbatim as was heard. Evidence for a human reader; no score ever reads this text." },
          note: { type: "string" } }, required: ["type"] } },
      note: { type: "string" }, confirm: { type: "boolean" } },
      required: ["idempotency_key","deal","side"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "record-counter", args, async () => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      if (args.rate_amount != null && !args.rate_basis)
        throw new ToolError({ error: "missing_basis", hint: "a rate is meaningless without its basis" });
      await rateConfirm(c, args, normRate(args.rate_amount, args.rate_basis), "rate.asking_confirm_band_sf_yr");

      // [0063] EVERYTHING NEW IS VALIDATED BEFORE THE ROUND IS INSERTED. The
      // envelope would roll a late failure back cleanly, but the caller would get
      // a foreign_key_violation where it deserves the legal vocabulary — and the
      // 'deadline' refusal in particular is a sentence, not a constraint name.
      let submarket = null;
      const claims = Array.isArray(args.claims) ? args.claims : [];
      if (args.submarket_condition || claims.length) {
        await require0063(c);
        if (args.submarket_condition)
          submarket = await validateSubmarket(c, args.submarket_condition);
      }
      const claimTypes = [];
      for (const cl of claims) {
        const t = await validateClaimType(c, cl.type);
        if (claimTypes.some(x => x.slug === t.slug))
          throw new ToolError({ error: "duplicate_claim", claim_type: t.slug,
            hint: "one row per (round, claim class) — a class said twice in one breath is " +
                  "still one claim. Put the second wording in `quote` or `note`." });
        if (cl.stated_floor != null && !cl.stated_floor_basis)
          throw new ToolError({ error: "missing_basis", claim_type: t.slug,
            hint: "a stated floor is meaningless without its basis, exactly as a rate is" });
        claimTypes.push(t);
      }

      const round = (await c.query(
        "select coalesce(max(round_no),0)+1 as n from negotiation_round where deal_id=$1 and side=$2",
        [s.id, args.side])).rows[0].n;
      // submarket_condition joins the column list ONLY when it was given, so this
      // verb keeps working byte-for-byte on a database where 0063 has not been
      // applied yet. The Worker deploy and the migration are two separate human
      // taps and either can come first.
      const params = [s.id, round, args.side, args.proposed_on || null, args.rate_amount || null,
        args.rate_basis || null, args.ti_amount || null, args.ti_basis || null,
        args.free_rent_months || null, args.term_months || null, args.options_note || null,
        args.escalator || null, args.expires_on || null, args.note || null, actor.id];
      if (submarket) params.push(submarket);
      const r = await c.query(
        `insert into negotiation_round (deal_id, round_no, side, proposed_on, rate_amount, rate_basis,
           ti_amount, ti_basis, free_rent_months, term_months, options_note, escalator, expires_on,
           note, created_by, updated_by${submarket ? ", submarket_condition" : ""})
         values ($1,$2,$3,coalesce($4::date,current_date),$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$15${submarket ? ", $16" : ""})
         returning id, round_no`, params);

      const claimsOut = [];
      for (let i = 0; i < claims.length; i++) {
        const cl = claims[i], t = claimTypes[i];
        const ins = await c.query(
          `insert into negotiation_claim (round_id, claim_type, stated_floor, stated_floor_basis,
             quote, note, source, created_by)
           values ($1,$2,$3,$4,$5,$6,'stated',$7) returning id`,
          [r.rows[0].id, t.slug, cl.stated_floor ?? null, cl.stated_floor_basis || null,
           cl.quote || null, cl.note || null, actor.id]);
        claimsOut.push({ claim_id: ins.rows[0].id, type: t.slug, label: t.label,
                         falsifiable: t.falsifiable, reversal_test: t.reversal_test });
      }

      await writeEvent(c, actor, "record-counter", "deal", s.id,
        { new: { round, side: args.side, rate: args.rate_amount, basis: args.rate_basis,
                 submarket_condition: submarket, claims: claimsOut.map(x => x.type) },
          idempotency_key: args.idempotency_key });

      const notes = [];
      if (claimsOut.some(x => !x.falsifiable))
        notes.push("One or more of these claims is NOT falsifiable (" +
          claimsOut.filter(x => !x.falsifiable).map(x => x.type).join(", ") +
          ") — logged so the tactic is visible in the history, and permanently excluded from " +
          "every score. Nothing we could ever observe would contradict it.");
      if (!submarket)
        notes.push("No submarket_condition on this round. That is fine — the scorecard reads " +
          "the latest non-null value on the deal — but if nobody has ever recorded one, " +
          "leverage and skill stay welded together in the numbers.");
      if (claimsOut.length)
        notes.push("Each claim is checked against the rounds that come AFTER it; " +
          "reversal_test says how. Nothing is scored now.");

      return { ok: true, round_id: r.rows[0].id, round_no: r.rows[0].round_no,
               submarket_condition: submarket, claims: claimsOut,
               note: notes.join(" ") || undefined };
    }),
  },

  // ===== [ORDER 13] the document factory =====

  "register-template": {
    write: true,
    description: "Register (or re-version) a CARR template so prepare-document can fill it. Writes the template row; field_map is the reviewable contract between the template's slots and the record layer — a map carrying unreviewed:true is REFUSED by prepare-document until a human has read it. Registering never touches the template file; source_path points at the real file in Templates/ and nothing writes there, ever.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      slug: { type: "string", description: "stable id, e.g. loi-lease / loi-purchase / loi-grid" },
      name: { type: "string" },
      source_path: { type: "string", description: "vault-relative path to the REAL template file" },
      template_version: { type: "string" },
      field_map: { type: "object", description: "{unreviewed, template_kind, slots{...}} — see fill-engine/field-maps/" },
      output_kinds: { type: "array", items: { type: "string" }, description: "defaults to {working,pdf}" },
      replace: { type: "boolean", description: "true to re-version an existing slug; without it an existing slug is refused so a map is never silently overwritten" } },
      required: ["idempotency_key","slug","name","source_path","template_version","field_map"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "register-template", args, async () => {
      const map = args.field_map;
      if (!map || typeof map !== "object" || !map.slots)
        throw new ToolError({ error: "bad_field_map", hint: "field_map needs a slots object; see fill-engine/field-maps/" });
      const kinds = args.output_kinds && args.output_kinds.length ? args.output_kinds : ["working","pdf"];
      const prior = await c.query("select id, template_version, field_map from doc_template where slug=$1", [args.slug]);
      if (prior.rows.length && !args.replace)
        throw new ToolError({ error: "template_exists", slug: args.slug,
          current_version: prior.rows[0].template_version,
          hint: "pass replace:true to re-version; a field map is a reviewed artifact and is never overwritten by accident" });
      let id;
      if (prior.rows.length) {
        id = prior.rows[0].id;
        await c.query(
          `update doc_template set name=$1, source_path=$2, template_version=$3,
             field_map=$4, output_kinds=$5, active=true where id=$6`,
          [args.name, args.source_path, args.template_version, JSON.stringify(map), kinds, id]);
        await writeEvent(c, actor, "register-template", "doc_template", id,
          { field: "field_map", old: { template_version: prior.rows[0].template_version },
            new: { template_version: args.template_version, unreviewed: !!map.unreviewed },
            idempotency_key: args.idempotency_key });
      } else {
        const r = await c.query(
          `insert into doc_template (slug, name, source_path, template_version, field_map, output_kinds, created_by)
           values ($1,$2,$3,$4,$5,$6,$7) returning id`,
          [args.slug, args.name, args.source_path, args.template_version,
           JSON.stringify(map), kinds, actor.id]);
        id = r.rows[0].id;
        await writeEvent(c, actor, "register-template", "doc_template", id,
          { new: { slug: args.slug, template_version: args.template_version, unreviewed: !!map.unreviewed },
            idempotency_key: args.idempotency_key });
      }
      const slots = Object.keys(map.slots).length;
      return { ok: true, template_id: id, slug: args.slug, slots,
               unreviewed: !!map.unreviewed,
               note: map.unreviewed
                 ? "map is UNREVIEWED: prepare-document refuses it unless allow_unreviewed:true"
                 : "map is reviewed" };
    }),
  },

  "prepare-document": {
    write: true,
    description: "Produce a document RECORD and its fill plan for one deal from a registered template: pulls the deal, client, premises, newest our-side negotiation round and lease, resolves every mapped slot, and returns the exact edits the local fill engine applies plus the OWED list. A field the records cannot answer is written into the document as a visible OWED marker — never invented, and never left showing the template's own placeholder number. Produces a draft; NOTHING is ever sent.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      deal: { type: "string", description: "deal name or a C-### client ref" },
      template: { type: "string", description: "doc_template slug" },
      options: { type: "object", description: "choices for the template's option slots, by slot name: an index or the literal text" },
      allow_unreviewed: { type: "boolean", description: "required to run against a field map still marked unreviewed" },
      note: { type: "string" } },
      required: ["idempotency_key","deal","template"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "prepare-document", args, async () => {
      const t = (await c.query(
        "select * from doc_template where slug=$1 and active", [args.template])).rows[0];
      if (!t) throw new ToolError({ error: "template_not_found", template: args.template,
        hint: "register-template first; slugs look like loi-lease / loi-purchase / loi-grid" });
      const map = t.field_map;
      if (map.unreviewed && !args.allow_unreviewed)
        throw new ToolError({ error: "template_unreviewed", template: args.template,
          hint: "the field map has not been reviewed by a human. Review it, re-register with unreviewed removed, or pass allow_unreviewed:true for a deliberate draft run." });

      const s = await resolveSubject(c, args.deal);
      let dealId = null, clientId = null;
      if (s.type === "deal") {
        dealId = s.id;
        clientId = (await c.query("select client_id from deal where id=$1", [dealId])).rows[0].client_id;
      } else if (s.type === "client") {
        clientId = s.id;
        const open = await c.query(
          "select id, name from deal where client_id=$1 and outcome is null order by created_at desc", [clientId]);
        if (open.rows.length !== 1)
          throw new ToolError({ error: open.rows.length ? "needs_disambiguation" : "no_open_deal",
            candidates: open.rows.map(x => ({ name: x.name })),
            hint: "a document belongs to a deal; name the deal" });
        dealId = open.rows[0].id;
      } else throw new ToolError({ error: "not_a_deal_or_client", resolved: s });

      const bag = await buildRecordBag(c, dealId, clientId);
      const options = args.options || {};
      if (options.tenant_credential) bag.tenant.credential = options.tenant_credential;

      const edits = [], owed = [], carried = [], partial = [];
      for (const [name, slot] of Object.entries(map.slots)) {
        const r = resolveSlot(name, slot, bag, options);
        if (r.carried) { carried.push(r.carried); continue; }
        if (r.owed) { owed.push(r.owed); edits.push({ where: r.owed.where, text: owedMarker(r.owed), slot: name, owed: true }); continue; }
        edits.push(r.edit);
        if (r.partial) partial.push(r.partial);
      }
      // A repeat block (the LOI grid's four property column-pairs) resolves as a
      // unit: with no premises rows there is nothing to iterate, and 160 owed
      // entries would bury the one fact that matters.
      if (map.repeat) {
        const list = bagGet(bag, map.repeat.over) || [];
        if (!list.length)
          owed.push({ slot: map.repeat.name, label: map.repeat.label, where: "repeat",
                      kind: "repeat", wanted: map.repeat.over, why: map.repeat.owed_note });
        else
          owed.push({ slot: map.repeat.name, label: map.repeat.label, where: "repeat",
                      kind: "repeat_unimplemented", wanted: `${list.length} rows present`,
                      why: "premises rows exist but the repeat filler is not built; ORDER 13 built the single-subject path." });
      }

      const doc = await c.query(
        `insert into document (template_id, deal_id, client_id, prepared_by, sent_status, note)
         values ($1,$2,$3,$4,'draft',$5) returning id, prepared_at`,
        [t.id, dealId, clientId, actor.id, args.note || null]);
      await writeEvent(c, actor, "prepare-document", "deal", dealId,
        { field: "document", new: { template: t.slug, document_id: doc.rows[0].id,
            filled: edits.length - owed.filter(o => o.where !== "repeat").length, owed: owed.length },
          agent_rationale: `prepare-document ${t.slug}: ${owed.length} owed field(s)`,
          idempotency_key: args.idempotency_key });

      const safe = v => String(v || "document").replace(/[^A-Za-z0-9]+/g, "_").replace(/^_|_$/g, "");
      const d = new Date();
      const p = n => String(n).padStart(2, "0");
      const stamp = `${p(d.getMonth() + 1)}-${p(d.getDate())}-${d.getFullYear()}`;
      return {
        ok: true,
        document_id: doc.rows[0].id,
        template: { slug: t.slug, name: t.name, source_path: t.source_path,
                    template_version: t.template_version, kind: map.template_kind,
                    unreviewed: !!map.unreviewed, output_kinds: t.output_kinds },
        deal: { id: dealId, name: bag.deal.name, client_ref: bag.client.ref,
                client_name: bag.client.display_name, org_name: bag.client.org_name },
        // Deal name, not the client party name: it is what Joe's own OneDrive
        // deal folders are named, and the org row can carry an fka suffix.
        basename: `${safe(bag.deal.name || bag.client.display_name)}-${safe(t.name)}-DRAFT-${stamp}`,
        edits, owed, partial, carried,
        status: "draft",
        human_gate: "This is a DRAFT for Joe to review. No send verb exists; update-document-status records his word, it does not send.",
      };
    }),
  },

  "update-document-status": {
    write: true,
    description: "Record what a HUMAN says happened to a prepared document — sets document status: draft -> handed_to_joe -> sent. It records a statement; it does not send anything and no verb anywhere does. Also the place to record the lint and leak-guard results and the filed attachments once the local fill run has produced them.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      document_id: { type: "string" },
      status: { type: "string", enum: ["draft","handed_to_joe","sent"] },
      human_quote: { type: "string", description: "the partner's own words, when he said it" },
      working_attachment: { type: "string" }, pdf_attachment: { type: "string" },
      lint_passed: { type: "boolean" }, leak_check_passed: { type: "boolean" },
      format_exception: { type: "string", description:
        "Required ONLY to record a send that goes against Joe's format split — an LOI or letter leaving as PDF, or a spreadsheet leaving as a live file. State why (the listing agent's system blocks .docx, the client asked for the live sheet). Joe's rule ends with 'unless the partner says otherwise'; this is where the partner says otherwise." },
      note: { type: "string" } },
      required: ["idempotency_key","document_id"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "update-document-status", args, async () => {
      const cur = (await c.query("select * from document where id=$1", [args.document_id])).rows[0];
      if (!cur) throw new ToolError({ error: "document_not_found", document_id: args.document_id });
      // A 'sent' claim is a human statement about the world. Automation saying it
      // would be the system asserting a send that no code in it can perform.
      if (args.status === "sent" && actor.kind !== "human")
        throw new ToolError({ error: "human_only",
          hint: "'sent' records that a partner sent it; only a human can state that" });

      // JOE'S SPLIT ON OUTBOUND FORMAT, in his words: "the LOI is sent over to the
      // listing agent in word format so they can easily edit or revise. i know i
      // said everything goes out pdf but thats really just spreadsheets that go
      // out pdf so noone can see the formulas"
      //
      // TWO RULES, and the correction is the important half. An LOI or letter
      // goes out in WORD, because the listing agent editing it IS the
      // negotiation; a PDF makes them retype it. A SPREADSHEET goes out as PDF so
      // nobody reads our formulas. The older blanket "everything goes out PDF" is
      // superseded and would break the negotiation it exists to start.
      //
      // THIS IS NOT ABOUT WHETHER THE FILE WAS MADE. output_kinds {working,pdf}
      // names ROLES, not formats, and both files exist by now. The question here
      // is which one we handed over — and this verb is the only place a human
      // states that a document went out at all.
      if (args.status === "sent") {
        const tk = await c.query(
          `/* outbound_template_kind */
           select coalesce(t.field_map->>'template_kind',
                           lower(regexp_replace(t.source_path, '^.*\\.', ''))) as template_kind
             from document d join doc_template t on t.id = d.template_id
            where d.id = $1`, [args.document_id]);
        const kind = (tk.rows[0]?.template_kind || "").toLowerCase();
        // Attachments and the status change often land in one call, so the gate
        // reads the MERGED state rather than the stored row alone.
        const working = args.working_attachment ?? cur.working_attachment;
        const pdf = args.pdf_attachment ?? cur.pdf_attachment;
        // A throwaway word is not a decision. Length rather than a banned-word
        // list, for the same reason as confirm-merge's basis: any such list is one
        // synonym from useless.
        const excused = String(args.format_exception ?? "").trim().length >= 20;

        const wordKinds = ["docx", "doc", "dotx", "rtf"];
        const sheetKinds = ["xlsx", "xls", "xlsm", "csv"];
        if (!excused) {
          if (wordKinds.includes(kind) && !working)
            throw new ToolError({ error: "outbound_format", template_kind: kind,
              ruling: "Joe: \"the LOI is sent over to the listing agent in word format so they can " +
                      "easily edit or revise.\"",
              why: "An LOI or letter goes out in WORD. The listing agent being able to edit or revise " +
                   "it IS the negotiation workflow; a PDF makes them retype it. No working file is " +
                   "recorded on this document, so what is on file to have been sent is the PDF.",
              hint: "Record the Word file as working_attachment. If the send genuinely went out as PDF " +
                    "— their system blocks .docx, say — pass format_exception with the reason." });
          if (sheetKinds.includes(kind) && !pdf)
            throw new ToolError({ error: "outbound_format", template_kind: kind,
              ruling: "Joe: \"thats really just spreadsheets that go out pdf so noone can see the formulas\"",
              why: "A spreadsheet goes out as PDF. A live workbook carries our formulas, and the " +
                   "recipient can read every one of them.",
              hint: "Record the PDF as pdf_attachment. If the client genuinely needed the live sheet, " +
                    "pass format_exception with the reason." });
        }
      }

      const sets = [], vals = [];
      const put = (col, v) => { if (v !== undefined && v !== null) { vals.push(v); sets.push(`${col}=$${vals.length}`); } };
      put("sent_status", args.status);
      put("working_attachment", args.working_attachment);
      put("pdf_attachment", args.pdf_attachment);
      put("lint_passed", args.lint_passed);
      put("leak_check_passed", args.leak_check_passed);
      put("note", args.note);
      if (!sets.length) throw new ToolError({ error: "nothing_to_update" });
      vals.push(args.document_id);
      await c.query(`update document set ${sets.join(", ")} where id=$${vals.length}`, vals);
      await writeEvent(c, actor, "update-document-status", "deal", cur.deal_id,
        { field: "document.sent_status", old: { sent_status: cur.sent_status },
          new: { document_id: args.document_id, sent_status: args.status || cur.sent_status,
                 // An exception nobody can find later is indistinguishable from an
                 // inconsistency, so it rides the event.
                 ...(args.format_exception ? { format_exception: args.format_exception } : {}) },
          human_quote: args.human_quote || null, idempotency_key: args.idempotency_key });
      return { ok: true, document_id: args.document_id, sent_status: args.status || cur.sent_status };
    }),
  },

  "link-parties": {
    write: true,
    description: "Record an intro-graph edge (a party_link row): who knows whom, who can introduce whom, who REFERRED whom. Feeds who-do-we-know (find returns these) and the reciprocity ledger. kind comes from the party_link_kind table — knows, works_with, can_introduce, intro_requested, introduced, referred (plus the legacy intro / intro_received) — and the same edge recorded twice returns the first one, never a duplicate. AN INTRODUCTION IS TERNARY: A introduced B to C, so pass via_party for the BROKER whenever one exists. from/to are the two people connected; via_party is who connected them. That is the whole basis of the reciprocity ledger — 'count where via = us' against 'count where via = them' — so an edge recorded with the broker left out is counted as nobody's referral and earns that vendor nothing.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, from_party: { type: "string" }, to_party: { type: "string" },
      kind: { type: "string", description: "a slug from party_link_kind: knows, works_with, can_introduce, intro_requested, introduced, referred" },
      via_party: { type: "string", description: "WHO made the connection — the broker in the middle. A ref (V-/C-/L-/T-/P-) or a party uuid. Omit ONLY for a genuinely direct edge with no third party; for 'a vendor sent us this client' the vendor goes HERE, not on an end. Refused if it resolves to either end, because a broker cannot be one of the two people being connected." },
      deal_id: { type: "string", description: "Exact referred deal UUID; referral/referred only, destination must be its live client party, note required. Several deals may attach to one relationship." },
      occurred_on: { type: "string", description: "YYYY-MM-DD — when it happened. An offer and a completed introduction are different events and the gap between them is the follow-up." },
      note: { type: "string" } }, required: ["idempotency_key","from_party","to_party","kind"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "link-parties", args, async () => {
      // [ORDER 18] The old hard-coded enum (can_introduce/intro_sent/intro_received/
      // works_with/referred) is retired. It was one of the two vocabularies ORDER 17
      // found: this verb could not write a single kind the backfill used, and the
      // backfill could not write one this verb offered. One table, one vocabulary.
      const kind = await validateLinkKind(c, args.kind);

      // REFS RESOLVE HERE (2026-08-10). Both ends used to go straight into the
      // insert as uuids, so passing V-DEV-007 or L-214 — the refs this verb's own
      // schema tells callers to use — hit a uuid cast error and surfaced as a bare
      // "internal error". Measured live while recording a real referral edge: the
      // call failed twice on refs and succeeded immediately on party uuids. Same
      // failure class as loop #261 — a validation failure wearing the costume of
      // an outage.
      const ends = {};
      for (const side of ["from_party", "to_party", "via_party"]) {
        const raw = String(args[side] || "").trim();
        // via_party is optional — a direct edge has no broker. from/to are required
        // by the schema, so an empty string there still falls through to the
        // resolver and surfaces as a named subject_not_found rather than a null.
        if (!raw && side === "via_party") { ends[side] = null; continue; }
        // PostgreSQL returns uuids lowercase; compare stored and resolved ends in one spelling.
        if (UUID_RE.test(raw)) { ends[side] = raw.toLowerCase(); continue; }
        const s = await resolveSubject(c, raw);          // throws subject_not_found, named
        let pid = s.type === "party" ? s.id : null;
        if (!pid) {
          const r = await c.query(
            "select party_id from v_ref_index where subject_type=$1 and subject_id=$2", [s.type, s.id]);
          pid = r.rows[0]?.party_id || null;
        }
        if (!pid) throw new ToolError({ error: "no_party_under_ref", side, got: raw, resolved: s,
          hint: "that ref resolves to a record with no person behind it — the intro graph links PEOPLE" });
        // A merged party is a tombstone; an edge must attach to the survivor or it
        // is invisible to who-do-we-know (the same rule find applies on read).
        const hop = await c.query("select merged_into from party where id=$1", [pid]);
        ends[side] = hop.rows[0]?.merged_into || pid;
      }
      if (ends.from_party === ends.to_party)
        throw new ToolError({ error: "self_link", got: args.from_party,
          hint: "both ends resolve to the same person — an intro graph edge needs two parties" });
      // A broker sits BETWEEN the two ends. If via resolves to one of them the edge
      // is malformed, and it fails silently rather than loudly: the reciprocity
      // ledger counts "via = them", so a vendor recorded as both the broker and an
      // end double-counts on one side of the exact comparison this shape exists for.
      if (ends.via_party && (ends.via_party === ends.from_party || ends.via_party === ends.to_party))
        throw new ToolError({ error: "via_is_an_end", got: args.via_party,
          hint: "via_party is who CONNECTED the two ends, so it cannot also be one of them — " +
                "for 'this vendor sent us this client', from = us, to = the client, via = the vendor" });

      let occurredOn = null;
      if (args.occurred_on != null && String(args.occurred_on).trim() !== "") {
        occurredOn = String(args.occurred_on).trim();
        if (!/^\d{4}-\d{2}-\d{2}$/.test(occurredOn))
          throw new ToolError({ error: "bad_occurred_on", got: args.occurred_on,
            hint: "occurred_on is a calendar date, YYYY-MM-DD" });
      }

      // The referrer is the relationship's broker as stored after this call (a
      // direct relationship credits its from end); the audit names the same party.
      const attachDeal = async (linkId, broker) => {
        let bound;
        try { bound = await bindReferralDeal(c, actor, args, ends, kind,
          { id: linkId, referred_by: broker || ends.from_party, occurred_on: occurredOn }); }
        catch (error) { throw new ToolError({error:error.code || 'referral_deal_invalid'}); }
        if (bound) await writeEvent(c,actor,'link-parties','party',bound.referred_by,{new:{link_id:linkId,deal_id:bound.deal_id,referred_by:bound.referred_by,kind:'referred'},idempotency_key:args.idempotency_key});
      };
      // Upsert against 0020's unique index. Before it, two taps wrote two identical
      // edges and nothing complained. `do nothing` returns no row on conflict, so
      // the existing edge is read back and returned — the caller gets the edge it
      // asked for either way, and learns which case it was.
      const ins = await c.query(
        `insert into party_link (from_party, to_party, kind, note, via_party, occurred_on, source, created_by)
         values ($1,$2,$3,$4,$5,$6,'stated',$7)
         on conflict (from_party, to_party, kind) do nothing
         returning id`,
        [ends.from_party, ends.to_party, kind, args.note || null,
         ends.via_party, occurredOn, actor.id]);
      if (!ins.rows.length) {
        // Locked: a concurrent caller naming another broker waits here and then
        // sees this call's backfill, instead of validating against a stale row.
        const cur = await c.query(
          "select id, via_party, occurred_on from party_link where from_party=$1 and to_party=$2 and kind=$3 for update",
          [ends.from_party, ends.to_party, kind]);
        const row = cur.rows[0];
        if (args.deal_id && row.via_party && row.via_party !== ends.via_party) throw new ToolError({error:"referral_broker_mismatch"});
        await attachDeal(row.id, row.via_party || ends.via_party);
        // BACKFILL, not overwrite. Every edge written between 0051 and 2026-08-10
        // carries a null broker, because this verb had no via_party to pass — the
        // schema was ternary and the only writer was binary. Those edges are the
        // reciprocity ledger's missing half, so a later call that DOES name the
        // broker must be able to fill the hole. It fills nulls only: a stored
        // broker or date is evidence already recorded and is never silently
        // replaced by a second caller's guess.
        const fills = {};
        if (ends.via_party && !row.via_party) fills.via_party = ends.via_party;
        if (occurredOn && !row.occurred_on) fills.occurred_on = occurredOn;
        if (Object.keys(fills).length) {
          await c.query(
            `update party_link set via_party = coalesce(via_party,$2),
                                   occurred_on = coalesce(occurred_on,$3)
              where id = $1`,
            [row.id, ends.via_party, occurredOn]);
          await writeEvent(c, actor, "link-parties", "party", ends.from_party,
            { old: { via_party: row.via_party, occurred_on: row.occurred_on },
              new: { kind, to: ends.to_party, ...fills, backfilled: true },
              idempotency_key: args.idempotency_key });
          return { ok: true, link_id: row.id, existing: true, backfilled: Object.keys(fills) };
        }
        // No event row: nothing changed in the record, and an event that says a
        // link was made when none was is the kind of fiction the ledger exists to
        // prevent. The tool_call row (envelope) still records that it was asked.
        return { ok: true, link_id: row.id, existing: true };
      }
      await writeEvent(c, actor, "link-parties", "party", ends.from_party,
        { new: { kind, to: ends.to_party, via: ends.via_party, occurred_on: occurredOn,
                 from_input: args.from_party, to_input: args.to_party },
          idempotency_key: args.idempotency_key });
      await attachDeal(ins.rows[0].id, ends.via_party);
      return { ok: true, link_id: ins.rows[0].id, existing: false };
    }),
  },

  "confirm-merge": {
    write: true,
    humanOnly: true,
    description: "HUMAN-confirmed merge of two duplicate parties: sets merged_into on the loser so it becomes a pointer to the survivor. Only after a human has looked at both records — the Hovanian rule means nothing auto-merges, ever.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, survivor_party: { type: "string" }, merged_party: { type: "string" },
      match_basis: { type: "string", description: "The corroborating signal: exact domain, normalized org name, phone, address, or corroborated name plus city. Recorded permanently with the merge." },
      same_person_because: { type: "string", description:
        "Required ONLY when one side holds just a lead row and the other just a client row. Joe's ruling: everyone starts as a lead, so an L- and a C- ref for one person is the system working, not a duplicate. State what makes these TWO party rows for ONE human — matching NPI, address, the intake record — not that the names match." } },
      required: ["idempotency_key","survivor_party","merged_party","match_basis"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "confirm-merge", args, async () => {
      // [0069] Inputs used to be assumed party uuids; a V- ref passed here died in
      // the update below as "invalid input syntax for type uuid" — an internal
      // error where a routing answer belonged (loop #199). Resolve refs properly,
      // and name the one case this verb structurally cannot do.
      const toParty = async (input) => {
        if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(input))
          return { partyId: input, via: "uuid" };
        const s = await resolveSubject(c, input);
        if (s.type === "party") return { partyId: s.id, via: "party" };
        if (s.type === "deal")
          throw new ToolError({ error: "not_a_party", ref: input, hint: "a deal cannot be merged; pass a party or role ref" });
        const r = await c.query(
          "select party_id from v_ref_index where subject_type=$1 and subject_id=$2", [s.type, s.id]);
        if (!r.rows.length || !r.rows[0].party_id)
          throw new ToolError({ error: "no_party_under_ref", ref: input, resolved: s });
        return { partyId: r.rows[0].party_id, via: s.type, roleId: s.id };
      };
      const surv = await toParty(args.survivor_party);
      const merg = await toParty(args.merged_party);
      if (surv.partyId === merg.partyId) {
        if (surv.via === "vendor" && merg.via === "vendor" && surv.roleId !== merg.roleId)
          throw new ToolError({ error: "one_party_two_vendor_rows",
            hint: "these two vendor refs ride ONE party — that is a vendor-row duplicate, not a party duplicate. Use merge-vendor-rows." });
        throw new ToolError({ error: "same_party", hint: "a party cannot be merged into itself" });
      }
      args = { ...args, survivor_party: surv.partyId, merged_party: merg.partyId };
      const basis = String(args.match_basis || "").trim();
      if (basis.length < 8)
        throw new ToolError({ error: "match_basis_required",
          hint: "state the corroborating signal that established this duplicate; a name alone is never a merge basis" });

      // Serialize overlapping merges before reading scores or moving roles.
      // A stable UUID order also keeps reverse calls from deadlocking. The
      // caller's transaction holds these locks through the mutation and event.
      const endpoints = await c.query(
        `/* merge_live_endpoints */
         select id, merged_into from party where id = any($1::uuid[])
          order by id for update`, [[surv.partyId, merg.partyId]]);
      if (endpoints.rows.length !== 2)
        throw new ToolError({ error: "merge_survivorship_unavailable",
          hint: "both party rows must be readable before a merge can run" });
      for (const endpoint of endpoints.rows) {
        if (endpoint.merged_into)
          throw new ToolError({ error: "party_already_merged", party_id: endpoint.id,
            merged_into: endpoint.merged_into,
            hint: "read the live party and confirm the duplicate pair again before merging" });
      }

      // The human confirms THAT this pair is a duplicate. Code decides WHICH
      // row survives, with the rule's exact precedence, so a human cannot
      // accidentally retire the more-cited or better-evidenced record.
      const metrics = await c.query(
        `/* merge_survivorship */
         select p.id,p.created_at,
           exists(select 1 from lead l where l.party_id=p.id and l.registry_ref is not null)
            or exists(select 1 from client cl where cl.party_id=p.id and cl.roster_ref is not null and cl.merged_into is null)
            or exists(select 1 from vendor v where v.party_id=p.id and v.vendor_ref is not null) as has_business_ref,
           (select count(distinct rf.kind) from record_flag rf where rf.subject_type='party' and rf.subject_id=p.id
             and rf.kind in ('verified','address','phone','email','npi','specialty') and coalesce(rf.value->>'found','true') <> 'false') as verified_identity_fields,
           -- Activities attach to role rows, which move with the party below.
           -- EXISTS counts a multi-role activity once, without multiplying it.
           ((select count(*) from activity a
              where exists(select 1 from client cl where cl.id=a.client_id and cl.party_id=p.id)
                 or exists(select 1 from lead l where l.id=a.lead_id and l.party_id=p.id)
                 or exists(select 1 from vendor v where v.id=a.vendor_id and v.party_id=p.id))
             + (select count(*) from deal_participant dp where dp.party_id=p.id)
             + (select count(*) from party_link pl where pl.from_party=p.id or pl.to_party=p.id or pl.via_party=p.id)) as linked_records
          from party p where p.id = any($1::uuid[])`, [[surv.partyId, merg.partyId]]);
      const preferred = preferredMergeSurvivor(metrics.rows);
      if (!preferred)
        throw new ToolError({ error: "merge_survivorship_unavailable", hint: "both party rows must be readable before a merge can run" });
      if (preferred.id !== surv.partyId)
        throw new ToolError({ error: "wrong_merge_survivor", required_survivor: preferred.id,
          selected_survivor: surv.partyId, precedence: "business ref, verified identity, linked records, oldest",
          hint: "the human gate confirmed the pair; use the deterministic survivor chosen from the record" });
      const sweep = await c.query(
        `/* merge_orphan_sweep */
         select 'party_link' as attachment, count(*)::int as count from party_link where from_party=$1 or to_party=$1 or via_party=$1
         union all select 'activity', count(*)::int from activity a
           where exists(select 1 from client cl where cl.id=a.client_id and cl.party_id=$1)
              or exists(select 1 from lead l where l.id=a.lead_id and l.party_id=$1)
              or exists(select 1 from vendor v where v.id=a.vendor_id and v.party_id=$1)
         union all select 'deal_participant', count(*)::int from deal_participant where party_id=$1
         union all select 'record_flag', count(*)::int from record_flag where subject_type='party' and subject_id=$1
         union all select 'child_party', count(*)::int from party where org_id=$1`, [merg.partyId]);

      // Moving role rows cannot preserve graph endpoints or the broker. Until
      // an attachment-preserving merge handles duplicate and self edges, refuse
      // this pair before any write instead of retiring a still-cited party.
      const graphCount = sweep.rows.find(row => row.attachment === "party_link")?.count;
      if (Number(graphCount) > 0)
        throw new ToolError({ error: "merge_graph_attachments_require_resolution",
          party_id: merg.partyId, count: Number(graphCount),
          hint: "the losing party has introduction links; preserve their endpoints and broker before confirming this merge" });

      // JOE'S RULING, in his words: "Okafor is a client now duh. everyone starts
      // as a lead." A lead record and a client record for the same person are
      // NOT a duplicate — every party enters as a lead and converts, and both
      // refs coexist by design.
      //
      // WHY THIS REFUSES RATHER THAN WARNS. Merging is destructive in a way that
      // does not undo: it retires the loser's ref permanently, and a lost ref is
      // never reissued, so every piece of doctrine quoting it goes dead and has
      // to be repointed by hand.
      //
      // WHY IT IS NOT A FLAT NO. The opposite case is real and this verb's own
      // history records it: Whitfield was two party rows for one human, one
      // carrying the lead and one the client, and merging them was correct. So
      // the gate refuses the merge whose only basis is that the names match, and
      // takes `same_person_because` as the evidence that it is that shape.
      const roleKinds = async (partyId) => {
        const r = await c.query(
          `/* role_kinds_for_party */
           select 'lead' as kind from lead where party_id=$1
           union all select 'client' from client where party_id=$1 and merged_into is null
           union all select 'vendor' from vendor where party_id=$1`, [partyId]);
        return new Set(r.rows.map(x => x.kind));
      };
      const [survRoles, mergRoles] = [await roleKinds(surv.partyId), await roleKinds(merg.partyId)];
      const only = (set, kind) => set.size === 1 && set.has(kind);
      // Symmetric on purpose: swapping the arguments must not slip past it.
      const isLeadClientPair =
        (only(survRoles, "client") && only(mergRoles, "lead")) ||
        (only(survRoles, "lead") && only(mergRoles, "client"));
      if (isLeadClientPair) {
        // A throwaway word is not a basis. The bar is length rather than a
        // vocabulary list because the failure being prevented is a session
        // typing "yes" to clear a gate, and any list of banned words is one
        // synonym from useless.
        const stated = String(args.same_person_because ?? "").trim();
        if (stated.length < 20)
          throw new ToolError({ error: "lead_client_pair",
            ruling: "Joe, on the Okafor record: \"Okafor is a client now duh. everyone starts as a lead.\"",
            why: "A lead record and a client record for the same person are not a duplicate. Every party " +
                 "enters as a lead and converts to a client; both refs coexist by design. Merging them " +
                 "retires one ref permanently, and a lost ref is never reissued.",
            hint: "If these really are TWO party rows for ONE human — the Whitfield shape — pass " +
                  "same_person_because with what establishes it (matching NPI, address, the intake " +
                  "record). Not that the names match." });
      }

      // THE ROLE ROWS MOVE WITH THE PERSON. Until 2026-08-02 this verb set merged_into and
      // nothing else, so the loser's lead/client/vendor rows were left pointing at a party
      // that no longer resolves — they vanished from every party-based view while still
      // existing. Three such orphans predated the fix, and merging Whitfield produced a
      // fourth: his lead L-201 disappeared and he read as "Client" only, when the entire
      // point of the merge was one person holding BOTH roles.
      const moved = {};
      for (const t of ["lead", "client", "vendor"]) {
        const r = await c.query(
          `update ${t} set party_id=$1, updated_by=$2 where party_id=$3 returning id`,
          [args.survivor_party, actor.id, args.merged_party]);
        if (r.rows.length) moved[t] = r.rows.length;
      }

      await c.query("update party set merged_into=$1, updated_by=$2 where id=$3",
        [args.survivor_party, actor.id, args.merged_party]);

      // A survivor holding two rows of the SAME role is a second duplicate hiding behind
      // the first. Reported, never auto-resolved: which of two lead records is authoritative
      // is a human call, and guessing is how the wrong Castillo got merged.
      const dup = await c.query(
        `select 'lead' k, count(*) n from lead where party_id=$1 having count(*)>1
         union all select 'client', count(*) from client where party_id=$1 and merged_into is null having count(*)>1
         union all select 'vendor', count(*) from vendor where party_id=$1 having count(*)>1`,
        [args.survivor_party]);

      // The stated basis rides the event: a merge is permanent, so the reason it
      // was allowed has to outlive the session that gave it.
      await writeEvent(c, actor, "confirm-merge", "party", args.merged_party,
        { new: { merged_into: args.survivor_party, roles_moved: moved, match_basis: basis,
                 survivorship: { business_ref: !!preferred.has_business_ref,
                   verified_identity_fields: Number(preferred.verified_identity_fields || 0),
                   linked_records: Number(preferred.linked_records || 0), created_at: preferred.created_at },
                 orphan_sweep: sweep.rows,
                 ...(args.same_person_because ? { same_person_because: args.same_person_because } : {}) },
          idempotency_key: args.idempotency_key });
      return { ok: true, roles_moved: moved, match_basis: basis, orphan_sweep: sweep.rows,
               duplicate_roles_on_survivor: dup.rows.length ? dup.rows : undefined };
    }),
  },

  // [0069, loop #199] The case confirm-merge structurally cannot do: two VENDOR
  // rows riding ONE party. The 8/1-ruled Cromwell and Wexler merges executed at
  // party level and left exactly this behind (V-GC-001+V-GC-013, V-MKT-001+
  // V-MSC-024), and the build sweep found a third pair the loop never named
  // (T-004+T-040). Backlog #119/#120's "executed" claims were true-but-incomplete.
  "merge-vendor-rows": {
    write: true,
    description: "HUMAN-confirmed merge of two vendor rows that ride the SAME party — a duplicate role, not a duplicate person. Survivorship is deterministic (rule 4c21d86b applied at role level): the survivor keeps every value it has, its NULLs fill from the loser, and a field where both rows disagree is REPORTED untouched for a human to settle — never coin-flipped. Activities, findings and next actions move to the survivor; the loser becomes a tombstone (merged_into) that v_ref_index still resolves with merged=true, and renders exclude. Different-party duplicates are confirm-merge's lane, and this verb refuses them. Nothing auto-merges, ever: a human picks the pair and the survivor. Survivor choice per rule 4c21d86b: more corroborated identity, then more linked records, then oldest.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      survivor_vendor: { type: "string", description: "V-/T- ref of the row that keeps the ref cited elsewhere" },
      merged_vendor: { type: "string", description: "V-/T- ref of the row that becomes the tombstone" } },
      required: ["idempotency_key","survivor_vendor","merged_vendor"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "merge-vendor-rows", args, async () => {
      const resolveVendor = async (ref) => {
        const s = await resolveSubject(c, ref);
        if (s.type !== "vendor") throw new ToolError({ error: "not_a_vendor", ref, resolved: s.type });
        return s.id;
      };
      const survId = await resolveVendor(args.survivor_vendor);
      const mergId = await resolveVendor(args.merged_vendor);
      if (survId === mergId)
        throw new ToolError({ error: "same_vendor_row", hint: "both refs resolve to one row; nothing to merge" });

      const FIELDS = ["category","category_slug","verticals","stage","owner_id","owner_label",
        "referral_active","territory","offers","seeking","rivalry_group","originated",
        "intro_notes","links_label","last_touch","relationship_level"];
      const rows = (await c.query(
        `select id, vendor_ref, party_id, merged_into, loan_programs, deal_evidence, deal_history_verified_at, trust_override, ${FIELDS.join(",")} from vendor where id = any($1) order by id for update`,
        [[survId, mergId]])).rows;
      const surv = rows.find(r => r.id === survId), merg = rows.find(r => r.id === mergId);
      if (surv.merged_into || merg.merged_into)
        throw new ToolError({ error: "already_merged",
          which: [surv, merg].filter(r => r.merged_into).map(r => r.vendor_ref),
          hint: "a tombstone cannot merge again; resolve to the live survivor first" });
      if (surv.party_id !== merg.party_id)
        throw new ToolError({ error: "different_parties",
          hint: "these vendor rows sit on two different people — that is a PARTY duplicate. confirm-merge is the verb, and it moves the vendor rows with the person." });

      // Survivorship: fill the survivor's NULLs, report disagreements, change nothing else.
      const filled = {}, conflicts = [];
      for (const f of FIELDS) {
        const a = surv[f], b = merg[f];
        const empty = (v) => v === null || v === undefined || (Array.isArray(v) && v.length === 0);
        if (empty(a) && !empty(b)) filled[f] = b;
        else if (!empty(a) && !empty(b) && JSON.stringify(a) !== JSON.stringify(b))
          conflicts.push({ field: f, survivor: a, merged: b });
      }
      const relationship = mergeRelationshipFields(surv, merg);
      Object.assign(filled, relationship.filled);
      conflicts.push(...relationship.conflicts);
      const fk = Object.keys(filled);
      if (fk.length) {
        const sets = fk.map((k, i) => `${k}=$${i + 2}`).join(", ");
        await c.query(`update vendor set ${sets}, updated_by=$1 where id=$${fk.length + 2}`,
          [actor.id, ...fk.map(k => k === "deal_evidence" ? JSON.stringify(filled[k]) : filled[k]), survId]);
      }

      // Dependents move; event rows stay where they happened (history is immutable).
      const moved = {};
      const act = await c.query(
        "update activity set vendor_id=$1 where vendor_id=$2 returning id", [survId, mergId]);
      if (act.rows.length) moved.activities = act.rows.length;
      const rf = await c.query(
        "update record_flag set subject_id=$1 where subject_type='vendor' and subject_id=$2 returning id",
        [survId, mergId]);
      if (rf.rows.length) moved.findings = rf.rows.length;
      // next_action: a unique index guards one OPEN action per (subject, owner).
      // A colliding open action on the loser is dropped and reported, never lost silently.
      const droppedActions = [];
      const na = await c.query(
        "select id, owner_id, description, status from next_action where subject_type='vendor' and subject_id=$1", [mergId]);
      for (const row of na.rows) {
        const clash = row.status === "open" && (await c.query(
          `select 1 from next_action where subject_type='vendor' and subject_id=$1
            and owner_id=$2 and status='open'`, [survId, row.owner_id])).rows.length;
        if (clash) {
          await c.query("update next_action set status='dropped', updated_by=$1 where id=$2", [actor.id, row.id]);
          droppedActions.push(row.description);
        } else {
          await c.query("update next_action set subject_id=$1, updated_by=$2 where id=$3", [survId, actor.id, row.id]);
          moved.next_actions = (moved.next_actions || 0) + 1;
        }
      }

      await c.query("update vendor set merged_into=$1, updated_by=$2 where id=$3",
        [survId, actor.id, mergId]);
      await writeEvent(c, actor, "merge-vendor-rows", "vendor", mergId,
        { new: { merged_into: args.survivor_vendor, filled, conflicts, moved },
          idempotency_key: args.idempotency_key });
      return { ok: true, survivor: surv.vendor_ref, tombstone: merg.vendor_ref,
               fields_filled: fk.length ? filled : undefined,
               conflicts_left_for_human: conflicts.length ? conflicts : undefined,
               moved, dropped_duplicate_open_actions: droppedActions.length ? droppedActions : undefined };
    }),
  },

  "record-finding": {
    write: true,
    description: "Land ONE open-source research or enrichment finding as a record_flag row. This is the only path a verification result becomes part of the record — findings do not go into a markdown report (Joe, 2026-08-02: 'we dont write to markdown in the new system only the database'). IT NEVER EDITS AN IDENTITY FIELD. A finding is stored BESIDE the record with its source; a disagreement with name/phone/email/title/specialty is passed as proposes_correction, which is recorded as a proposal for the owning partner and applied by them, never by this verb. STORE NOTHING-FOUND TOO: pass found:false and the empty result becomes a real row, so a record nobody searched is distinguishable from one that was searched and came up dry — that difference is the whole meaning of a verified stamp. source is REQUIRED on every row; provenance is binding, and a finding without it is a rumour. Pass expires_on for anything volatile: title and company change with promotions and job moves, so an expired verification reads as unverified rather than as fact. Common kinds: verified (an identity pass, value lists what was checked), email, cell, office_phone, social, website, npi, license_status, title, entity_filing, address, discrepancy. A near-match on a similar name is contamination, not confirmation — record both candidates and pick neither. Also writes an event, so the finding shows up in catch-me-up without a second read surface. NOT ONLY PEOPLE SINCE 0066: subject_kind campaign / platform / pillar / format files a finding against a THING — a platform, a content pillar, a format, a campaign — which is how the marketing seat's measured conclusions finally get a home. Read them back through v_record_flag_subject, which resolves every branch to a name. AND NOT ONLY BUSINESS RECORDS SINCE 0101: a finding can be filed against CODE — pass 'commit:<sha>' (the one repo at that commit), 'owner/name@<sha>', or 'repo:owner/name' (the codebase itself) and the subject is minted on first use. That is how a code review's result — INCLUDING its failure finding, which is the one a reader most needs — becomes part of the record instead of surviving only in a local sidecar. Read code findings back through v_code_finding, which carries repo and commit_sha as their own columns.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      subject: { type: "string", description: "C-127 / L-204 / V-CPA-006 / P-0301, an exact deal name, or — when subject_kind is campaign/platform/pillar/format — a campaign name or a marketing_subject slug ('twitter', 'reel'). CODE (0101): 'commit:<sha>' files against the one repo at that commit, 'owner/name@<sha>' against another repo, 'repo:owner/name' against the codebase itself." },
      subject_kind: { type: "string", enum: ["auto","party","campaign","platform","pillar","format","repo","commit"], default: "auto",
        description: "'party' pins the flag to the person/org behind a ref instead of the client/lead/vendor record. THE FOUR MARKETING KINDS (0066) are how a finding about a THING rather than a PERSON gets recorded: 'X has returned no analytics for any of 42 placements' is a platform finding, 'reels outperform statics on reach' is a format finding. Before 0066 those had no subject at all and the marketing seat's core output went nowhere. A platform/pillar/format subject must already exist in marketing_subject — this verb registers nothing, because a typo'd slug minting a new pillar is how a taxonomy becomes noise. THE TWO CODE KINDS (0101) are 'commit' (subject is a sha, or 'owner/name@<sha>') and 'repo' (subject is 'owner/name'). Unlike the marketing kinds these ARE minted on demand: a sha is self-evidencing rather than a taxonomy, so there is no vocabulary a typo can pollute. You rarely need to pass these — a subject written as 'commit:<sha>' or 'owner/name@<sha>' is recognised under 'auto'." },
      kind: { type: "string", description: "what was looked for: verified, email, cell, social, npi, title, discrepancy..." },
      value: { type: "object", description: "the finding, structured. Omit when found:false." },
      found: { type: "boolean", default: true, description: "false records a searched-and-empty result" },
      epistemic_status: { type: "string",
        enum: ["proposed","observed","reproduced","accepted","disputed","superseded","inferred","source_backed","speculative"],
        description: "what CLASS of knowledge this row is, so a reader can tell a verified invariant from a provisional interpretation from a hypothesis (idea #57, from the repo-centric agent-stack study 2026-08-07). Defaults: source_backed when found with an external source; inferred when internal. Set disputed/superseded when filing against an earlier finding. Stored in value; query as value->>'epistemic_status'." },
      source: { type: "string", description: "REQUIRED. Where it came from: a URL, 'NPPES', 'Sunbiz', 'practice website'. For EXTERNAL findings this must be a re-verifiable LOCATOR (a URL, a registry name + identifier, a file+section, a thread + date) — wave 1 C5, decision a317439f: the re-verify queue is only as good as the pointer it re-checks. A bare label like 'research' is refused." },
      internal: { type: "boolean", description: "true = an internally observed finding (derived from the record layer itself, a session's own computation, or partner testimony) — exempt from the external-locator requirement, and stored flagged so readers know no outside source backs it." },
      observed_at: { type: "string", description: "when the source was read (ISO); defaults to now" },
      expires_on: { type: "string", description: "date after which this reads as unverified again (volatile fields)" },
      proposes_correction: { type: "object",
        description: "{field, current, proposed} — RECORDED ONLY. The owning partner applies it." } },
      required: ["idempotency_key","subject","kind","source"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "record-finding", args, async () => {
      // [wave 1 C5, decision a317439f — scoped per the Codex judge: external and
      // decision-bearing findings need a re-verifiable locator; internal
      // observations are exempt but flagged. A universal requirement would
      // manufacture placeholder citations, which is worse than none.]
      if (!args.internal) {
        const src = String(args.source || "");
        const locator = /https?:\/\//i.test(src)
          || /\b(nppes|sunbiz|npi|bbb|linkedin|zoominfo|rocketreach|facebook|instagram|chamber|arxiv|github)\b/i.test(src)
          || /[\/#§@]|\bp\.?\s?\d|\bevent\b|\bthread\b|\bv_[a-z_]+/i.test(src);
        if (!locator || src.trim().length < 12)
          throw new ToolError({ error: "source_not_a_locator",
            got: src.slice(0, 80),
            hint: "an external finding's source must be re-verifiable: a URL, a registry name + identifier, a file+section, or a thread+date. If this finding was derived internally (from the record itself or partner testimony), resubmit with internal:true and it will be stored flagged as such." });
      }
      const src = String(args.source || "").trim();
      if (!src) throw new ToolError({ error: "source_required",
        hint: "every finding carries its provenance; a finding without a source is a rumour" });

      const found = args.found !== false;
      if (found && (!args.value || typeof args.value !== "object" || !Object.keys(args.value).length))
        throw new ToolError({ error: "value_required",
          hint: "pass the finding as value{}, or pass found:false to record a searched-and-empty result" });

      let subjectType, subjectId;
      const MARKETING_KINDS = ["campaign", "platform", "pillar", "format"];
      const CODE_KINDS = ["repo", "commit"];
      // [0101, loop #211] THE CODE BRANCH. Placed FIRST among the special kinds and
      // sniffed even under subject_kind:'auto', because the whole defect this closes is
      // that a reviewing seat writes `commit:<sha>` and gets subject_not_found. Requiring
      // it to also know about a subject_kind flag would leave the reported failure in
      // place for every caller who writes what they already write. The sniff is narrow —
      // parseCodeRef returns null for anything that is not unmistakably a repo or a
      // commit ref, and a bare hex word is only a sha when the caller said `commit:`.
      const codeRef = CODE_KINDS.includes(args.subject_kind)
        ? parseCodeRef(args.subject_kind === "repo"
            ? `repo:${args.subject}` : `commit:${args.subject}`)
        : (args.subject_kind && args.subject_kind !== "auto" ? null : parseCodeRef(args.subject));
      if (CODE_KINDS.includes(args.subject_kind) && !codeRef)
        throw new ToolError({ error: "code_subject_unparseable", got: String(args.subject || "").slice(0, 80),
          subject_kind: args.subject_kind,
          hint: args.subject_kind === "commit"
            ? "with subject_kind:'commit', subject is a sha ('f7abde7') or 'owner/name@<sha>'"
            : "with subject_kind:'repo', subject is 'owner/name'" });
      if (codeRef) {
        const cs = await resolveCodeSubject(c, args.subject_kind === "repo"
          ? `repo:${codeRef.repo}`
          : (codeRef.sha ? `${codeRef.repo}@${codeRef.sha}` : `repo:${codeRef.repo}`), actor.id);
        subjectType = cs.type; subjectId = cs.id;
      } else if (args.subject_kind === "party") {
        subjectType = "party";
        subjectId = await resolvePartyByRef(c, args.subject);
      } else if (MARKETING_KINDS.includes(args.subject_kind)) {
        // [0066] The non-party branch. It resolves through the SAME
        // (subject_type, subject_id) pointer every other branch uses — a campaign
        // already has a uuid, and marketing_subject exists to give platforms,
        // pillars and formats one, rather than bolting a second pointer column
        // onto record_flag.
        await require0066(c);
        if (args.subject_kind === "campaign") {
          subjectType = "campaign";
          subjectId = (await resolveCampaign(c, args.subject)).id;
        } else {
          const slug = String(args.subject || "").trim().toLowerCase();
          const r = await c.query(
            "select id, retired_at from marketing_subject where subject_type=$1 and slug=$2",
            [args.subject_kind, slug]);
          if (!r.rows.length) {
            const known = await c.query(
              "select slug from marketing_subject where subject_type=$1 and retired_at is null order by slug",
              [args.subject_kind]);
            throw new ToolError({ error: "marketing_subject_not_found",
              subject_kind: args.subject_kind, slug,
              known_slugs: known.rows.map(x => x.slug),
              hint: known.rows.length
                ? "use one of the registered slugs, or register a new one deliberately — this verb never mints one"
                : `no ${args.subject_kind} is registered yet. 0066 deliberately seeds ZERO pillars ` +
                  "because none is evidenced anywhere in the record; naming the first one is a " +
                  "human modelling act, not a side effect of filing a finding." });
          }
          subjectType = args.subject_kind;
          subjectId = r.rows[0].id;
          if (r.rows[0].retired_at)
            throw new ToolError({ error: "marketing_subject_retired", slug,
              retired_at: r.rows[0].retired_at,
              hint: "findings stay readable against a retired subject, but new ones do not attach to it" });
        }
      } else {
        const s = await resolveSubject(c, args.subject);
        subjectType = s.type; subjectId = s.id;
      }

      // The correction is DATA, not an instruction. It rides inside value so it is
      // impossible to store one without its provenance, and no code path applies it.
      const value = {
        found,
        ...(found ? args.value : { searched_for: args.kind }),
        ...(args.proposes_correction
            ? { proposes_correction: { ...args.proposes_correction, applied: false,
                                       note: "proposal only — the owning partner applies identity changes" } }
            : {}),
        // [wave 1 C5] an internally observed finding is stored FLAGGED, so a
        // reader knows no outside source backs it — the exemption is visible,
        // never silent.
        ...(args.internal ? { internal: true } : {}),
        // Epistemic status (idea #57): explicit wins; otherwise internal
        // observations are inferences and externally-sourced rows are
        // source-backed. Never silently "known".
        epistemic_status: args.epistemic_status
          || (args.internal ? "inferred" : "source_backed"),
      };

      const r = await c.query(
        `insert into record_flag (subject_type, subject_id, kind, value, source, observed_at, expires_on, created_by)
         values ($1,$2,$3,$4,$5, coalesce($6::timestamptz, now()), $7::date, $8) returning id, observed_at`,
        [subjectType, subjectId, args.kind, JSON.stringify(value), src,
         args.observed_at || null, args.expires_on || null, actor.id]);

      await writeEvent(c, actor, "record-finding", subjectType, subjectId, {
        occurred_at: args.observed_at || null,
        field: args.kind,
        new: { found, source: src, expires_on: args.expires_on || null,
               proposes_correction: args.proposes_correction ? args.proposes_correction.field : null },
        agent_rationale: found ? null : "searched, nothing found",
        idempotency_key: args.idempotency_key });

      return { ok: true, flag_id: r.rows[0].id, subject_type: subjectType, subject_id: subjectId,
               kind: args.kind, found, observed_at: r.rows[0].observed_at,
               correction_proposed: !!args.proposes_correction };
    }),
  },

  "find-rule": {
    description: "Find proposed, active, or retired rules by their words when the id is unknown. Matches a literal substring or all whitespace-separated words, case-insensitively. Returns bounded statement previews and full ids for amend-rule or retire-rule. Read-only; status defaults to any.",
    inputSchema: { type: "object", properties: {
      text: { type: "string", minLength: 1 },
      status: { type: "string", enum: ["proposed", "active", "retired", "any"], default: "any" },
      limit: { type: "integer", minimum: 1, maximum: 100, default: 20 },
    }, required: ["text"] },
    handler: async (c, actor, args) => {
      const text = String(args.text || "").trim();
      if (!text) throw new ToolError({ error: "rule_search_text_required" });
      const status = args.status ?? "any";
      if (!["proposed", "active", "retired", "any"].includes(status))
        throw new ToolError({ error: "invalid_rule_status" });
      const limit = args.limit ?? 20;
      if (!Number.isInteger(limit) || limit < 1 || limit > 100)
        throw new ToolError({ error: "invalid_rule_search_limit" });
      const personalScope = personalScopeForActor(actor);
      if (personalScope.status === "error")
        throw new ToolError({ error: personalScope.error });
      const matches = await c.query(
        `select id, left(id::text,8) as short_id, status, version, created_at,
                scope, left(statement,200) as statement
           from v_rule_lookup
          where (personal_to is null or personal_to=$5::text)
            and ($2='any' or status=$2)
            and (strpos(lower(statement),lower($1))>0 or not exists (
              select 1 from unnest($3::text[]) as terms(word)
               where strpos(lower(statement),lower(word))=0))
          order by created_at desc, id
          limit $4`, [text, status, text.split(/\s+/u), limit, personalScope.sponsor]);
      return { ok: true, rules: matches.rows };
    },
  },

  "teach": {
    write: true,
    description: "Write a rule from the human's own words (status: proposed — after exact enforcement is built and verified, one explicit human approve-rule act atomically activates the enforced policy). Capture the verbatim quote. Personal-scope rules (voice, format) set personal_to. WHEN TO CALL IT — the test is 'would the system have to ask this again?', NOT whether the partner phrased it as 'always X' or 'never Y'. Standing lessons arrive as ordinary sentences: a modeling ruling ('cadence studio is one national account'), a correction to a fact in the record, a choice between options you offered with the reasoning attached, a rejection of a draft. Capture on the spot, never at 'session close' — the same event-not-session-close rule protocol 27b already settles. Pass supersedes when this rule replaces an earlier one; the old rule is retired with an immutable receipt in the same transaction. Superseding an ACTIVE rule requires a human caller and the same authority connection as retire-rule. ENFORCEMENT-FIRST BIRTH (WR-000019 slice S10): every teach REQUIRES enforcement_home, one of 'gate' (a deny/stop control will carry it — name carrying_control), 'jit' (delivered just-in-time by pack/moment), 'core' (always-loaded), or 'judgment_advisory' (no mechanical control ever will — say why_no_machine in one line). This is a refusal, not a default: a rule captured with nobody having said where it will live is exactly how guidance debt piled up before this slice, and a silent default would be indistinguishable from a considered choice. THIS IS CLERICAL WORK, NOT SELF-MODIFICATION, AND IT IS NEVER REFUSED ON THAT GROUND. Joe's ruling 2026-08-10, verbatim: 'You didn't make your own rule. You applied my rule to the system.' A session INVENTING a standing rule for itself would be self-modification and would be gated. A session TRANSCRIBING what a partner just said is the entire purpose of this verb, and the gate is already built into it: the rule lands as PROPOSED, binds nobody, and takes effect through one human approve-rule act only when enforcement is ready. A session that declines to record a partner's instruction because writing rules 'feels like' changing itself has not been careful, it has lost the instruction — which is the one outcome this verb exists to prevent. Recorded because a session hit exactly this on the day the ruling was made and stopped three routes early.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, statement: { type: "string" },
      human_quote: { type: "string" }, scope: { type: "object" },
      personal: { type: "boolean", description: "true = applies to this partner only" },
      supersedes: { type: "string", description: "rule_id this one replaces and retires atomically; ACTIVE rules require a human caller" },
      enforcement_home: { type: "string", enum: ["gate","jit","core","judgment_advisory"],
        description: "REQUIRED. Where this rule will be enforced: 'gate' (a deny/stop control — pass carrying_control), 'jit' (delivered just-in-time by pack/moment), 'core' (always-loaded), or 'judgment_advisory' (no mechanical control — pass why_no_machine)." },
      carrying_control: { type: "string", description: "REQUIRED when enforcement_home is 'gate'. The control this rule's enforcement will carry — an existing control_key, or the one about to be built." },
      why_no_machine: { type: "string", description: "REQUIRED when enforcement_home is 'judgment_advisory'. One line: why no mechanical control can carry this rule." } },
      required: ["idempotency_key","statement","human_quote","enforcement_home"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "teach", args, async () => {
      // ENFORCEMENT-FIRST BIRTH (WR-000019 slice S10). See the description
      // above: a clear, named refusal rather than a silent default, so an
      // existing caller that has not been told about this yet gets an error
      // that IS the migration path, not a rule quietly filed with no home.
      const ENFORCEMENT_HOMES = ["gate", "jit", "core", "judgment_advisory"];
      const enforcementHome = args.enforcement_home;
      if (!ENFORCEMENT_HOMES.includes(enforcementHome))
        throw new ToolError({ error: "enforcement_home_required",
          hint: "pass enforcement_home: one of 'gate' (a deny/stop control will carry it — also pass carrying_control), 'jit' (delivered just-in-time by pack/moment), 'core' (always-loaded), or 'judgment_advisory' (no mechanical control — also pass why_no_machine)" });
      const carryingControl = String(args.carrying_control || "").trim();
      if (enforcementHome === "gate" && !carryingControl)
        throw new ToolError({ error: "carrying_control_required",
          hint: "enforcement_home 'gate' means a deny/stop control carries this rule; name it in carrying_control — an existing control_key, or the one about to be built" });
      const whyNoMachine = String(args.why_no_machine || "").trim();
      if (enforcementHome === "judgment_advisory" && !whyNoMachine)
        throw new ToolError({ error: "why_no_machine_required",
          hint: "enforcement_home 'judgment_advisory' means no mechanical control will ever back this rule; say why not in why_no_machine, one line" });

      let supersedes = args.supersedes || null;
      // A supersedes pointer at a rule that does not exist is a silent lie in the
      // audit trail, so it is checked rather than trusted.
      if (supersedes) {
        // Short form accepted (loop #261): the gist index prints 8 characters and
        // that is the only id a session can quote back.
        supersedes = await resolveRuleId(c, supersedes, "supersedes");
        const prior = await c.query("select id, status from rule where id=$1 for update", [supersedes]);
        if (!prior.rows.length) throw new ToolError({ error: "supersedes_not_found",
          rule_id: supersedes, hint: "pass the id of a real rule, or omit supersedes" });
        if (prior.rows[0].status === "retired")
          throw new ToolError({ error: "already_retired", rule_id: supersedes });
        if (prior.rows[0].status === "active" && actor.human !== true)
          throw new ToolError({ error: "human_only", verb: "teach", rule_id: supersedes,
            hint: "superseding an ACTIVE rule requires a human caller" });
      }
      const r = await c.query(
        `insert into rule (statement, human_quote, taught_by, scope, personal_to, supersedes)
         values ($1,$2,$3,$4,$5,$6) returning id, personal_to`,
        [args.statement, args.human_quote, actor.id, JSON.stringify(args.scope || {}),
         // STRICT, matching the two response lines below (loop 353). The
         // boundary coercer already guarantees a real boolean here, so this is
         // belt-and-braces: it makes the storage line and the echo lines
         // structurally incapable of disagreeing, which is the disagreement that
         // stored a shared rule as personal while reporting otherwise.
         args.personal === true ? actor.id : null, supersedes || null]);
      // This definer door preserves retirement receipts and the active-rule
      // authority guard. Failure rolls back the new rule and its envelope too.
      let retirement = null;
      if (supersedes) {
        const retired = await c.query("select ops.retire_superseded_rule($1,$2) as result",
          [r.rows[0].id, args.idempotency_key]);
        retirement = retired.rows[0].result;
      }
      // Capture precedes authority. Every proposed rule enters the same intake
      // state machine immediately, but this row is deliberately only CAPTURED:
      // neither a model nor the transcription verb can make it binding.
      await c.query(
        `insert into ops.guidance_intake
           (lane,source_kind,source_ref,statement,state,captured_by)
         values ('rule','human',$1,$2,'captured',$3)`,
        [`rule:${r.rows[0].id}`, args.statement, actor.id]);
      await writeEvent(c, actor, "teach", "rule", r.rows[0].id,
        { new: { statement: args.statement, supersedes: supersedes || null,
                 enforcement_home: enforcementHome,
                 carrying_control: enforcementHome === "gate" ? carryingControl : null,
                 why_no_machine: enforcementHome === "judgment_advisory" ? whyNoMachine : null },
          human_quote: args.human_quote, idempotency_key: args.idempotency_key });
      // SCOPE IS ECHOED BACK, added 2026-08-03, because it defaulted silently
      // once and nothing in the response could show it. A rule taught with
      // personal:true landed SHARED, and the only thing that caught it was a
      // row-count comparison between two exported files minutes after it was
      // already ACTIVE and binding both partners. `personal_to` is derived from
      // args.personal AND a resolved actor, so a caller genuinely cannot know
      // which scope it got from an envelope that says only {ok, rule_id,
      // status}. The failure direction is always toward binding MORE people
      // than intended, which is the direction that matters least to the caller
      // and most to the other partner. So the verb now states what it did.
      const scopeApplied = r.rows[0].personal_to ? `personal:${actor.slug}` : "shared";
      const scopeMismatch = args.personal === true && !r.rows[0].personal_to;
      return { ok: true, rule_id: r.rows[0].id, status: "proposed",
               next_authority_action: "approve-rule",
               scope_applied: scopeApplied,
               personal_requested: args.personal === true,
               supersedes: supersedes || null,
               retirement,
               enforcement_home: enforcementHome,
               carrying_control: enforcementHome === "gate" ? carryingControl : null,
               why_no_machine: enforcementHome === "judgment_advisory" ? whyNoMachine : null,
               ...(scopeMismatch ? { warning:
                 "personal:true was requested but this rule was stored SHARED — activating it " +
                 "will bind BOTH partners, including any wording specific to one of them or to " +
                 "one machine. Retire it and re-teach before activating if that is wrong." } : {}) };
    }),
  },

  "admit-rule": {
    write: true,
    description: "Normalize and admit one PROPOSED rule into executable authority. Capture remains free; this is the separate human gate. Applicability, projection, reachability, input contract, binding moment, fixtures, and enforcement points are all explicit. A machine-enforceable rule is refused unless at least one installed enforcement point and fixture are named. Admission writes an immutable authority receipt but does not activate the rule; activate-rule remains a second explicit human act.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      rule_id: { type: "string" },
      enforcement_class: { type: "string", enum: ["machine_enforceable","judgment_advisory","human_only"] },
      binding_moment: { type: "string" },
      applicability: { type: "object" },
      projection: { type: "object" },
      reachability: { type: "object" },
      input_contract: { type: "object" },
      fixture_refs: { type: "array", items: { type: "string" } },
      enforcement_points: { type: "array", items: { type: "object", properties: {
        control_key: { type: "string" }, implementation_ref: { type: "string" },
        test_ref: { type: "string" },
        enforcement_class: { type: "string", enum: ["deny_gate","stop_gate","schema","surfacing","transactional_schema","judgment_ambient"] },
        installed: { type: "boolean" },
      }, required: ["control_key","implementation_ref","test_ref","enforcement_class","installed"] } },
      reason: { type: "string" },
    }, required: ["idempotency_key","rule_id","enforcement_class","binding_moment",
                  "applicability","projection","reachability","input_contract",
                  "fixture_refs","enforcement_points","reason"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "admit-rule", args, async () => {
      args.rule_id = await resolveRuleId(c, args.rule_id);
      const rule = await c.query("select status,statement from rule where id=$1", [args.rule_id]);
      if (!rule.rows.length) throw new ToolError({ error: "rule_not_found", rule_id: args.rule_id });
      if (rule.rows[0].status !== "proposed") throw new ToolError({
        error: "rule_not_proposed", rule_id: args.rule_id,
        current_status: rule.rows[0].status,
        hint: "admission is a pre-activation contract; active or retired history is not rewritten",
      });
      const reason = String(args.reason || "").trim();
      const binding = String(args.binding_moment || "").trim();
      if (!reason || !binding) throw new ToolError({ error: "admission_explanation_required" });
      if (args.enforcement_class === "machine_enforceable") {
        if (!args.fixture_refs.length) throw new ToolError({ error: "fixture_required" });
        if (!args.enforcement_points.some(p => p.installed === true))
          throw new ToolError({ error: "installed_enforcement_point_required" });
      }

      let intake = await c.query(
        "select id from ops.guidance_intake where lane='rule' and source_ref=$1 order by captured_at limit 1",
        [`rule:${args.rule_id}`]);
      if (!intake.rows.length) intake = await c.query(
        `insert into ops.guidance_intake
           (lane,source_kind,source_ref,statement,state,captured_by)
         values ('rule','system',$1,$2,'captured',$3) returning id`,
        [`rule:${args.rule_id}`, rule.rows[0].statement, actor.id]);

      const normalized = {
        enforcement_class: args.enforcement_class, binding_moment: binding,
        applicability: args.applicability, projection: args.projection,
        reachability: args.reachability, input_contract: args.input_contract,
        fixture_refs: args.fixture_refs, enforcement_points: args.enforcement_points,
      };
      await c.query(
        `update ops.guidance_intake
            set state='admitted',normalized_contract=$1,updated_at=now(),version=version+1
          where id=$2`, [JSON.stringify(normalized), intake.rows[0].id]);
      await c.query(
        `insert into ops.rule_admission
           (rule_id,guidance_intake_id,enforcement_class,binding_moment,applicability,
            projection,reachability,input_contract,fixture_refs,state,admitted_by,
            admitted_at,reason)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9,'admitted',$10,now(),$11)
         on conflict (rule_id) do update set
           guidance_intake_id=excluded.guidance_intake_id,
           enforcement_class=excluded.enforcement_class,binding_moment=excluded.binding_moment,
           applicability=excluded.applicability,projection=excluded.projection,
           reachability=excluded.reachability,input_contract=excluded.input_contract,
           fixture_refs=excluded.fixture_refs,state='admitted',admitted_by=excluded.admitted_by,
           admitted_at=excluded.admitted_at,reason=excluded.reason,
           version=ops.rule_admission.version+1,updated_at=now()`,
        [args.rule_id,intake.rows[0].id,args.enforcement_class,binding,
         JSON.stringify(args.applicability),JSON.stringify(args.projection),
         JSON.stringify(args.reachability),JSON.stringify(args.input_contract),
         args.fixture_refs,actor.id,reason]);
      // A revision is the complete current contract. Controls omitted from the
      // new contract must stop counting as installed evidence; retaining their
      // rows preserves audit history without silently retaining authority.
      await c.query(
        `update ops.rule_enforcement_point
            set installed=false,verified_at=null
          where rule_id=$1 and not (control_key = any($2::text[]))`,
        [args.rule_id,args.enforcement_points.map(point => point.control_key)]);
      for (const point of args.enforcement_points) await c.query(
        `insert into ops.rule_enforcement_point
           (rule_id,control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
         values ($1,$2,$3,$4,$5,$6,case when $6 then now() else null end)
         on conflict (rule_id,control_key) do update set
           implementation_ref=excluded.implementation_ref,test_ref=excluded.test_ref,
           enforcement_class=excluded.enforcement_class,installed=excluded.installed,
           verified_at=excluded.verified_at`,
        [args.rule_id,point.control_key,point.implementation_ref,point.test_ref,
         point.enforcement_class,point.installed]);
      await c.query(
        `insert into ops.authority_receipt
           (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
         values ($1,'admission','rule',$2,$3,$4,
                 encode(digest($5::text,'sha256'),'hex'),$6)`,
        [`admission:${args.idempotency_key}`,args.rule_id,actor.id,reason,
         JSON.stringify(normalized),args.fixture_refs]);
      await writeEvent(c, actor, "admit-rule", "rule", args.rule_id, {
        new: { admission_state: "admitted", enforcement_class: args.enforcement_class },
        agent_rationale: reason, idempotency_key: args.idempotency_key,
      });
      return { ok: true, rule_id: args.rule_id, admission_state: "admitted",
               enforcement_class: args.enforcement_class,
               installed_controls: args.enforcement_points.filter(p => p.installed).length };
    }),
  },

  "approve-rule": {
    write: true, authorityOnly: true,
    description: "Approve one captured system rule in a single Joe-authority act. Approval means the server atomically verifies exact registered enforcement, records the immutable authority receipt, and activates the rule in the same transaction. There is no approved-but-inactive or active-but-pending state. If enforcement is missing, approval refuses so the system must build and verify the control before carrying Joe's already-recorded approval. Dell retains teaching, review and optional participation capability but cannot replace Joe as the required system authority. Advisory guidance is not mislabeled as an unbreakable rule.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      rule_id: { type: "string", description: "Full UUID or the short id printed by standing-context." },
      policy_kind: { type: "string", enum: ["machine_enforceable","human_only"] },
      control_keys: { type: "array", items: { type: "string" }, description: "Compiler-selected registered controls. Unknown or unverified controls refuse approval; callers cannot supply implementation or test evidence." },
      reason: { type: "string" },
    }, required: ["idempotency_key","rule_id","policy_kind","control_keys","reason"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "approve-rule", args, async () => {
      args.rule_id = await resolveRuleId(c, args.rule_id);
      const approved = await c.query(
        "select ops.approve_rule($1,$2,$3,$4,$5) as result",
        [args.rule_id,args.policy_kind,args.control_keys,args.idempotency_key,args.reason]);
      const result = approved.rows[0]?.result;
      if (!result || result.policy_status !== "active")
        throw new ToolError({ error: "rule_approval_failed", rule_id: args.rule_id });
      await writeEvent(c, actor, "approve-rule", "rule", args.rule_id, {
        new: { status: "active", enforcement_status: result.enforcement_status,
          installed_controls: result.installed_controls,
          pending_controls: result.pending_controls },
        agent_rationale: args.reason, idempotency_key: args.idempotency_key,
      });
      return result;
    }),
  },

  "activate-rule": {
    write: true,
    description: "Retired compatibility verb. Direct activation is forbidden because it could separate human approval from verified enforcement. Use approve-rule, which succeeds only when it can enforce and activate the rule atomically.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      rule_id: { type: "string", description: "Accepts either the full 36-character uuid or the 8-character SHORT FORM the gist index and standing-context print (e.g. '179be4b8'); an ambiguous prefix returns the candidates rather than guessing." } },
      required: ["idempotency_key","rule_id"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "activate-rule", args, async () => {
      throw new ToolError({ error: "direct_rule_activation_retired", rule_id: args.rule_id,
        hint: "use approve-rule; approval succeeds only with exact installed enforcement and activates atomically" });
    }),
  },

  "applicable-rules": {
    write: false,
    description: "Compile the active admitted rule set for a finite workflow, surface and tier. This is deterministic applicability selection from stored tags; no model performs routing or decides which authority applies.",
    inputSchema: { type: "object", properties: {
      workflow: { type: "string" }, surface: { type: "string" }, tier: { type: "string" },
    } },
    handler: async (c, _actor, args) => {
      const r = await c.query(
        "select * from ops.applicable_rules($1,$2,$3)",
        [args.workflow || null,args.surface || null,args.tier || null]);
      return { ok: true, count: r.rows.length, rules: r.rows };
    },
  },

  // BATCH REVIEW QUEUE (WR-000019 slice S6). Every pending governance decision
  // in one payload, so Joe reviews on his own schedule rather than one verb at
  // a time: rules admitted and waiting on approve-rule, guidance import
  // batches staged and waiting on decide-guidance-import-batch, retrieval
  // proposals waiting on approve-retrieval-proposals. Read-only projection —
  // it opens no new write path and changes nothing. carr_reader (the read-verb
  // credential) has no grant on `rule` or `retrieval_proposal`, so this reads
  // through a SECURITY DEFINER function (migration 0345), the same reason
  // read-execution-environment-providers is a function rather than a direct
  // multi-table read.
  "governance-queue": {
    write: false,
    description: "Read every pending governance decision in one payload: rules admitted and awaiting approve-rule, guidance import batches staged and awaiting decide-guidance-import-batch, and retrieval proposals awaiting approve-retrieval-proposals — each with enough context to decide. Read-only; grants no authority and performs no decision itself.",
    inputSchema: { type: "object", additionalProperties: false, properties: {} },
    handler: async (c) => {
      const row = (await c.query("select ops.read_governance_queue() as queue /* governance-queue */")).rows[0];
      const queue = row?.queue || {};
      const rules = queue.pending_rule_approvals || [];
      const batches = queue.pending_guidance_import_batches || [];
      const proposals = queue.pending_retrieval_proposals || [];
      return {
        ok: true,
        pending_rule_approvals: rules,
        pending_guidance_import_batches: batches,
        pending_retrieval_proposals: proposals,
        counts: {
          pending_rule_approvals: rules.length,
          pending_guidance_import_batches: batches.length,
          pending_retrieval_proposals: proposals.length,
          total: rules.length + batches.length + proposals.length,
        },
      };
    },
  },

  "accept-workflow": {
    write: true, authorityOnly: true,
    description: "Authority acceptance of a completed workflow run. Shadow acceptance remains available to either admitted human partner; canary acceptance is Joe-only and is enforced by the authenticated authority database session, never a caller field. Uses the authority connection, derives the partner from that connection's authenticated session, and refuses an arbitrary receipt reference.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, workflow_key: { type: "string" },
      mode: { type: "string", enum: ["shadow", "canary"] }, receipt_ref: { type: "string" },
    }, required: ["idempotency_key", "workflow_key", "mode", "receipt_ref"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "accept-workflow", args, async () => {
      const accepted = await c.query(
        "select ops.record_workflow_acceptance($1,$2,'accepted',$3) as id",
        [args.workflow_key, args.mode, args.receipt_ref]);
      await writeEvent(c, actor, "accept-workflow", "system", accepted.rows[0].id,
        { new: { workflow_key: args.workflow_key, mode: args.mode, receipt_ref: args.receipt_ref },
          idempotency_key: args.idempotency_key });
      return { ok: true, acceptance_id: accepted.rows[0].id };
    }),
  },

  "disable-legacy-schedule": {
    write: true, authorityOnly: true,
    description: "Joe-only authority readback after native legacy schedules are disabled. Requires accepted shadow/canary evidence plus immutable enabled and disabled observations for the exact registered surface; a duplicate group additionally requires all four observations for both surfaces. It never performs a native disable.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, workflow_key: { type: "string" }, reason: { type: "string" },
      surface_id: { type: "string" }, locator: { type: "string" },
      pre_observation_ref: { type: "string" }, post_observation_ref: { type: "string" },
      sibling_surface_id: { type: "string" }, sibling_locator: { type: "string" },
      sibling_pre_observation_ref: { type: "string" }, sibling_post_observation_ref: { type: "string" },
    }, required: ["idempotency_key", "workflow_key", "surface_id", "locator", "reason", "pre_observation_ref", "post_observation_ref"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "disable-legacy-schedule", args, async () => {
      const sibling = [args.sibling_surface_id, args.sibling_locator,
        args.sibling_pre_observation_ref, args.sibling_post_observation_ref];
      if (sibling.some(value => value != null) && !sibling.every(value => typeof value === "string" && value.length > 0))
        throw new ToolError({ error: "duplicate_scheduler_evidence_incomplete", workflow_key: args.workflow_key });
      const retired = await c.query("select ops.disable_legacy_schedule($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) as receipt_ref",
        [args.workflow_key, args.surface_id, args.locator, args.reason,
         args.pre_observation_ref, args.post_observation_ref,
         args.sibling_surface_id || null, args.sibling_locator || null,
         args.sibling_pre_observation_ref || null, args.sibling_post_observation_ref || null,
         args.idempotency_key]);
      if (!retired.rows[0].receipt_ref)
        throw new ToolError({ error: "legacy_schedule_not_disabled", workflow_key: args.workflow_key });
      // THE EVENT SUBJECT IS A UUID, AND THIS VERB PASSED A NAME. Until 2026-08-28
      // the audit write below received args.workflow_key, so every call — however
      // good its evidence — died on `invalid input syntax for type uuid` AFTER the
      // receipt row was written, and the rollback took the receipt with it. The
      // verb had therefore never once succeeded, and no legacy schedule could be
      // retired through the only sanctioned path. Its sibling accept-workflow got
      // this right four lines earlier by passing the returned row's id.
      const disabled = await c.query(
        "select id from ops.legacy_schedule_disable_receipt where receipt_ref=$1",
        [retired.rows[0].receipt_ref]);
      if (!disabled.rows[0])
        throw new ToolError({ error: "legacy_schedule_receipt_not_readable", workflow_key: args.workflow_key });
      await writeEvent(c, actor, "disable-legacy-schedule", "system", disabled.rows[0].id,
        { new: { workflow_key: args.workflow_key, surface_id: args.surface_id, locator: args.locator,
                 reason: args.reason, pre_observation_ref: args.pre_observation_ref,
                 post_observation_ref: args.post_observation_ref,
                 sibling_surface_id: args.sibling_surface_id || null,
                 sibling_locator: args.sibling_locator || null,
                 sibling_pre_observation_ref: args.sibling_pre_observation_ref || null,
                 sibling_post_observation_ref: args.sibling_post_observation_ref || null,
                 receipt_ref: retired.rows[0].receipt_ref }, idempotency_key: args.idempotency_key });
      return { ok: true, workflow_key: args.workflow_key, disabled: true, receipt_ref: retired.rows[0].receipt_ref };
    }),
  },

  "activate-guidance-registry": {
    write: true, authorityOnly: true,
    description: "Activate the typed Guidance Registry after its 5–10-item constitution and complete coverage pass. Uses the human authority connection, derives the approving partner from its authenticated database session, and atomically records the registry-bound manifest-digest receipt and activation event.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, registry_id: { type: "string" },
      manifest_digest: { type: "string", description: "Exact lowercase SHA-256 digest of the reviewed activation manifest." },
      reason: { type: "string" },
    }, required: ["idempotency_key", "registry_id", "manifest_digest", "reason"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "activate-guidance-registry", args, async () => {
      const activated = await c.query(
        "select ops.activate_guidance_registry($1,$2,$3,$4) as id",
        [args.registry_id, args.manifest_digest, args.idempotency_key, args.reason]);
      await writeEvent(c, actor, "activate-guidance-registry", "guidance_registry", args.registry_id,
        { new: { manifest_digest: args.manifest_digest, state: "active" },
          agent_rationale: args.reason, idempotency_key: args.idempotency_key });
      return { ok: true, registry_id: args.registry_id, activation_event_id: activated.rows[0].id,
               manifest_digest: args.manifest_digest };
    }),
  },

  "decide-guidance-import-batch": {
    write: true, authorityOnly: true,
    description: "Joe-only authority decision for one staged typed-guidance import batch. The human authority database session derives Joe; the caller supplies only the exact reviewed batch id, manifest digest, idempotency key, and recorded reason. It activates the batch's immutable decisions but does not activate the registry itself.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, batch_id: { type: "string" },
      manifest_digest: { type: "string", description: "Exact lowercase SHA-256 digest of the reviewed activation manifest." },
      reason: { type: "string" },
    }, required: ["idempotency_key", "batch_id", "manifest_digest", "reason"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "decide-guidance-import-batch", args, async () => {
      const decided = await c.query(
        "select ops.decide_guidance_import_batch($1,$2,$3,$4,$5) as id",
        [args.batch_id, args.manifest_digest, "active", args.idempotency_key, args.reason]);
      await writeEvent(c, actor, "decide-guidance-import-batch", "guidance_import_batch", args.batch_id,
        { new: { manifest_digest: args.manifest_digest, state: "active" },
          agent_rationale: args.reason, idempotency_key: args.idempotency_key });
      return { ok: true, batch_id: args.batch_id, decision_event_id: decided.rows[0].id,
               manifest_digest: args.manifest_digest, state: "active" };
    }),
  },

  "deactivate-guidance-registry": {
    write: true, authorityOnly: true,
    description: "Joe-only authority operation to deactivate the active typed Guidance Registry. The authority database session derives Joe and the supplied digest must exactly bind the registry activation being withdrawn. This is append-only history; it never edits a guidance revision.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, registry_id: { type: "string" },
      manifest_digest: { type: "string", description: "Exact lowercase SHA-256 digest of the activation being withdrawn." },
      reason: { type: "string" },
    }, required: ["idempotency_key", "registry_id", "manifest_digest", "reason"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "deactivate-guidance-registry", args, async () => {
      const deactivated = await c.query(
        "select ops.deactivate_guidance_registry($1,$2,$3,$4) as id",
        [args.registry_id, args.manifest_digest, args.idempotency_key, args.reason]);
      await writeEvent(c, actor, "deactivate-guidance-registry", "guidance_registry", args.registry_id,
        { new: { manifest_digest: args.manifest_digest, state: "inactive" },
          agent_rationale: args.reason, idempotency_key: args.idempotency_key });
      return { ok: true, registry_id: args.registry_id, registry_event_id: deactivated.rows[0].id,
               manifest_digest: args.manifest_digest, state: "inactive" };
    }),
  },

  "retire-rule": {
    write: true, authorityOnly: true,
    description: "Joe-authority retirement of a proposed or active rule through one database transaction. It writes an immutable retirement receipt bound to the exact rule version, statement hash, prior approval, reason and replacement before changing status. Direct writer updates cannot retire a rule. Retirement preserves the frozen rule text and history; a changed rule must be taught and approved separately.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      rule_id: { type: "string", description: "Accepts either the full 36-character uuid or the 8-character SHORT FORM the gist index and standing-context print (e.g. '179be4b8'); an ambiguous prefix returns the candidates rather than guessing." },
      reason: { type: "string", description: "REQUIRED. Why it is being withdrawn — wrong scope, duplicate, superseded, never wanted." },
      superseded_by: { type: "string", description: "rule_id of the replacement, when there is one" } },
      required: ["idempotency_key","rule_id","reason"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "retire-rule", args, async () => {
      const reason = String(args.reason || "").trim();
      if (!reason) throw new ToolError({ error: "reason_required",
        hint: "an unexplained retirement reads as a mistake later; say why in one line" });

      args.rule_id = await resolveRuleId(c, args.rule_id);          // loop #261
      const cur = await c.query("select status, statement, personal_to from rule where id=$1", [args.rule_id]);
      if (!cur.rows.length) throw new ToolError({ error: "rule_not_found", rule_id: args.rule_id });
      if (cur.rows[0].status === "retired") throw new ToolError({ error: "already_retired",
        rule_id: args.rule_id, hint: "nothing was written; the rule is already withdrawn" });

      if (args.superseded_by) {
        args.superseded_by = await resolveRuleId(c, args.superseded_by, "superseded_by"); // loop #261
        const rep = await c.query("select id from rule where id=$1", [args.superseded_by]);
        if (!rep.rows.length) throw new ToolError({ error: "superseded_by_not_found",
          rule_id: args.superseded_by, hint: "pass the id of a real replacement rule, or omit it" });
        if (args.superseded_by === args.rule_id) throw new ToolError({ error: "self_supersede",
          hint: "a rule cannot replace itself" });
      }

      const was = cur.rows[0].status;
      const retired = await c.query(
        "select ops.retire_rule($1,$2,$3,$4) as result",
        [args.rule_id, reason, args.superseded_by || null, args.idempotency_key]);
      const result = retired.rows[0].result;
      await writeEvent(c, actor, "retire-rule", "rule", args.rule_id, {
        field: "status", old: { status: was }, new: { status: "retired" },
        agent_rationale: reason,
        idempotency_key: args.idempotency_key });
      return { ...result, rule_id: args.rule_id, was, now: "retired", reason,
               superseded_by: args.superseded_by || null,
               note: was === "active"
                 ? "this rule was BINDING — re-export compiled-rules so sessions stop loading it"
                 : "it bound nobody; no re-export needed" };
    }),
  },

  // ---------- amend-rule (2026-08-02) ----------
  // The store shipped one-way: teach -> activate -> retire. There was no way to
  // fix the WORDS of a rule that was otherwise right, so every wording fix meant
  // retire + re-teach: a new id (breaking every citation), a lost created_at and
  // activation event, and a REQUIRED fresh human_quote — forcing the partner to
  // re-say something he already said, to fix prose he never wrote.
  //
  // 53 of the 54 proposed rules carry no quote at all; they were imported from
  // ai-operating-notes.md by a pipeline that correctly refused to fabricate one.
  // Their statement is our articulation, not his testimony. `update-decision`
  // already established that a durable record can be corrected rather than
  // re-litigated; rules simply never got the same affordance.
  "amend-rule": {
    write: true, authorityOnly: true,
    description: "Correct the WORDS of a rule in place, keeping its id, created_at, taught_by and quote. THE LINE: amend = same rule, better words; teach + retire = a different rule. A PROPOSED rule's statement, human_quote (fill-only) and scope may all be corrected directly, no authority principal required beyond the write itself. An ACTIVE rule's quote, scope, audience and enforcement preimage stay frozen — changing any of them would make the old approval receipt appear to bless new substance — but its STATEMENT ALONE may now be amended (WR-000019 slice S10) through a Joe-authority-guarded, receipted path (ops.amend_rule_statement): an append-only ops.rule_amendment_receipt hashes the prior words, and the enforced rule keeps reciting under its old approval. To change an active rule's scope, quote or audience, still teach and approve a corrected replacement, then retire this one. Requires base_version from a fresh read; a conflict is never retried blind.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      rule_id: { type: "string" },
      base_version: { type: "integer", description: "the rule's version, from a fresh read" },
      statement: { type: "string", description: "the corrected rule text. Omit to leave it as-is." },
      human_quote: { type: "string", description: "ONLY to fill a quote that is currently absent — the partner's literal words. Refused if the rule already carries one. Never paraphrase into this field." },
      scope: { type: "object", description: "replacement scope object, e.g. {\"section\":\"...\"}. Omit to leave it as-is." },
      reason: { type: "string", description: "REQUIRED. Why the wording is being corrected — an unexplained edit to a binding rule is indistinguishable from drift." } },
      required: ["idempotency_key","rule_id","base_version","reason"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "amend-rule", args, async () => {
      const reason = String(args.reason || "").trim();
      if (!reason) throw new ToolError({ error: "reason_required",
        hint: "say in one line why the wording is wrong; a silent edit to a binding rule reads as drift later" });

      args.rule_id = await resolveRuleId(c, args.rule_id);          // loop #261
      const cur = await c.query(
        "select status, statement, human_quote, scope, version from rule where id=$1", [args.rule_id]);
      if (!cur.rows.length) throw new ToolError({ error: "rule_not_found", rule_id: args.rule_id });
      const row = cur.rows[0];

      // A retired rule is history. Editing a tombstone rewrites the past instead
      // of correcting the present.
      if (row.status === "retired") throw new ToolError({ error: "rule_retired",
        rule_id: args.rule_id, current_status: row.status,
        hint: "a withdrawn rule stays as written; teach a new one rather than editing the tombstone" });

      await versionGuard(c, "rule", args.rule_id, args.base_version);

      const hasQuote = !!String(row.human_quote || "").trim();
      const quoteIn  = args.human_quote === undefined ? undefined : String(args.human_quote).trim();
      if (quoteIn !== undefined && hasQuote && quoteIn !== String(row.human_quote).trim())
        throw new ToolError({ error: "human_quote_immutable",
          current_quote: row.human_quote,
          hint: "the partner's words are testimony, not prose. Amend the statement instead; to record something DIFFERENT he said, teach a new rule and retire this one." });

      const nextStatement = args.statement === undefined ? row.statement : String(args.statement).trim();
      if (!nextStatement) throw new ToolError({ error: "empty_statement",
        hint: "a rule with no words binds nothing — pass the corrected text, or omit the field to leave it alone" });

      const nextQuote = (!hasQuote && quoteIn) ? quoteIn : row.human_quote;
      const nextScope = args.scope === undefined ? row.scope : args.scope;

      const changed = [];
      if (nextStatement !== row.statement) changed.push("statement");
      if (nextQuote !== row.human_quote) changed.push("human_quote");
      if (JSON.stringify(nextScope) !== JSON.stringify(row.scope)) changed.push("scope");
      if (!changed.length) throw new ToolError({ error: "no_change",
        hint: "nothing was written; the rule already reads exactly this way" });

      if (row.status === "active") {
        // The old approval's preimage — quote, scope, audience, enforcement —
        // stays frozen exactly as before. Only the ONE axis the versioned
        // amendment path exists to correct (statement) may move; a request
        // that also touches scope or quote still refuses, same as always.
        if (changed.includes("scope"))
          throw new ToolError({ error: "active_rule_scope_frozen", rule_id: args.rule_id,
            hint: "scope is part of the approval preimage and stays frozen on an active rule; teach the corrected rule, approve it, and retire this one to change scope" });
        if (changed.includes("human_quote"))
          throw new ToolError({ error: "active_rule_quote_frozen", rule_id: args.rule_id,
            hint: "the partner's words are testimony, not prose, and stay fixed once a rule is active" });

        // changed is now exactly ["statement"] — ops.amend_rule_statement,
        // guarded like ops.approve_rule (Joe authority, refuses a retired
        // rule), writes the receipt and updates rule.statement atomically.
        const amended = await c.query(
          "select ops.amend_rule_statement($1,$2,$3,$4) as result",
          [args.rule_id, nextStatement, args.idempotency_key, reason]);
        const result = amended.rows[0]?.result;
        if (!result || !result.ok)
          throw new ToolError({ error: "rule_amendment_failed", rule_id: args.rule_id });

        await writeEvent(c, actor, "amend-rule", "rule", args.rule_id, {
          field: "statement",
          old: { statement: row.statement },
          new: { statement: nextStatement },
          agent_rationale: reason,
          idempotency_key: args.idempotency_key });

        return { ok: true, rule_id: args.rule_id, status: "active",
                 changed: ["statement"], version: result.rule_version_after, reason,
                 replayed: !!result.replayed,
                 amendment_receipt_id: result.amendment_receipt_id,
                 note: "amended under Joe authority and receipted; the rule stays active and keeps reciting under its old approval" };
      }

      await c.query("update rule set statement=$1, human_quote=$2, scope=$3 where id=$4",
        [nextStatement, nextQuote, JSON.stringify(nextScope), args.rule_id]);

      await writeEvent(c, actor, "amend-rule", "rule", args.rule_id, {
        field: changed.join(","),
        old: { statement: row.statement, human_quote: row.human_quote, scope: row.scope },
        new: { statement: nextStatement, human_quote: nextQuote, scope: nextScope },
        human_quote: nextQuote || null,
        agent_rationale: reason,
        idempotency_key: args.idempotency_key });

      const after = await c.query("select version from rule where id=$1", [args.rule_id]);
      return { ok: true, rule_id: args.rule_id, status: row.status,
               changed, version: after.rows[0].version, reason,
               note: "it binds nobody yet; approve-rule is the atomic enforcement and activation gate" };
    }),
  },

  // ---------- decisions (the verb 0031 named and nobody built) ----------
  // 0031 built v_decision_entry as "the read side of decision-history-as-events"
  // and said verb='log-decision' is what "a future present-tense verb would
  // write". That verb was never written, so decision-history.md could only ever
  // render the one-time import — which is why its export has last_ok = null to
  // this day, and why a 2026-08-02 session with a settled decision to record had
  // nowhere to put it and hand-wrote a DECISIONS.md instead. The generated header
  // on decision-history.md has been telling readers "to record one, use the verb"
  // the whole time. This is that verb.
  //
  // Shape is dictated by v_decision_entry, not invented: an event row carrying
  // title/quote_absent/provenance in new_value plus human_quote and
  // agent_rationale, and a record_source row keyed '<source_file>#<session_key>'
  // under source_system='decision-history'. Grouping stays the render's job
  // (rule 29, one entry per session) — this verb exposes session_key and groups
  // nothing, exactly as the view does.
  // [0070] The Source Material capture log as a verb. The markdown INDEX was a
  // table wearing prose: append-only rows plus a check-before-capture dedup step
  // that two concurrent sessions could race. The check now runs inside the write
  // transaction and cannot.
  "log-capture": {
    write: true,
    description: "Log a learning-source capture into the Source Material capture log — one row per source (podcast, article, video, portal session, thread). Its ONE job is the dedup guard: it CHECKS for an existing capture first (exact URL, then session-name similarity) and returns candidates INSTEAD of inserting when found — if it's already here, it's already absorbed; pass force_new:true only after a human confirms it is genuinely a different source. The knowledge itself NEVER lives here: it merges into the domain playbooks per the knowledge policy, and merge_note records where it merged and what was declined, honestly. status: merged (absorbed), declined (evaluated, not adopted — say why), queued (spotted, capture later; merge_note may be empty only here). Renders to DNA/Marketing/Source Material/INDEX.md — never hand-edit that file.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      session: { type: "string", description: "what the source is, in the words a future dedup check would search — include the author/platform and [public source] / [colleague source] style markers as before" },
      merge_note: { type: "string", description: "where it merged and what was declined, with reasons. Required unless status is queued." },
      captured_on: { type: "string", description: "YYYY-MM-DD; defaults today" },
      source_url: { type: "string", description: "primary link when one exists — it becomes the exact-match dedup key" },
      visibility: { type: "string", enum: ["public","member_gated","colleague","internal"], default: "public" },
      status: { type: "string", enum: ["merged","declined","queued"], default: "merged" },
      force_new: { type: "boolean", description: "insert despite dedup candidates — only after a human confirmed it is a different source" } },
      required: ["idempotency_key","session"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "log-capture", args, async () => {
      const status = args.status || "merged";
      if (status !== "queued" && !(args.merge_note && args.merge_note.trim()))
        throw new ToolError({ error: "missing_merge_note",
          hint: "a merged or declined capture must say where it went or why it was declined; only a queued row may be empty" });
      if (!args.force_new) {
        const cand = await c.query(
          `select captured_on, session, status, left(merge_note, 160) as merge_note
             from source_capture
            where ($1::text is not null and source_url is not null
                   and lower(source_url) = lower($1))
               or session % $2
            order by similarity(session, $2) desc limit 5`,
          [args.source_url || null, args.session]);
        if (cand.rows.length)
          return { needs_confirm: true, candidates: cand.rows,
                   hint: "similar captures exist — if it's here, it's already absorbed; resubmit force_new:true only for a genuinely different source" };
      }
      const r = await c.query(
        `insert into source_capture
           (captured_on, session, source_url, visibility, status, merge_note,
            created_by, updated_by)
         values (coalesce($1::date, current_date),$2,$3,$4,$5,$6,$7,$7)
         returning id, captured_on`,
        [args.captured_on || null, args.session, args.source_url || null,
         args.visibility || "public", status, args.merge_note || "", actor.id]);
      await writeEvent(c, actor, "log-capture", "source_capture", r.rows[0].id,
        { new: { session: args.session, status }, idempotency_key: args.idempotency_key });
      return { ok: true, capture_id: r.rows[0].id, captured_on: r.rows[0].captured_on,
               status, renders_into: "DNA/Marketing/Source Material/INDEX.md" };
    }),
  },

  "log-decision": {
    write: true,
    description: "Record a SETTLED decision and its rationale — the thing that stops it being relitigated next session. Writes a decision event (subject_type='decision', verb='log-decision') that v_decision_entry reads and decision-history.md renders; never hand-edit that file. NOT the same as add-loop marker:'decision', which is an OPEN question awaiting a ruling, and not the same as teach, which stores a standing rule that binds future sessions. Use this when a fork has been closed: what was decided, why, what lost. human_quote is Joe's or Dell's literal words when he said them — omit it and the entry is flagged quote_absent rather than paraphrase being passed off as a quote. PASS `about` WHENEVER THE RULING CONCERNS ONE RECORD: a decision without it is filed in decision-history and reachable from nothing, which is how 363 rulings ended up invisible to catch-me-up on the very deals they governed. PRICE IT WHEN THE RULING CHANGES HOW THE SYSTEM WORKS: cost_delta and quality_delta record what a build cost and what it bought, together or not at all, because a build with no before-and-after number can never be shown to have worked, only asserted to have. If the after-measure does not exist yet, log it unpriced and add both halves later with update-decision.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      title: { type: "string", description: "the decision itself, in one line, stated as settled" },
      rationale: { type: "string", description: "why — including alternatives considered and why they lost, and any condition that would reopen it" },
      about: { description: "the record(s) this ruling is ABOUT — one ref or several: \"C-127\" or [\"V-GC-001\",\"V-MSC-024\",\"T-004\"]. Mirrors the decision onto each record's timeline so catch-me-up on it shows what was decided. Omit only for a genuinely system-wide ruling that belongs to no one record, which most build and doctrine rulings are; a bad ref is refused and NOTHING is written, so a mistyped ref never leaves a decision behind. Forgot it? update-decision takes `about` too.",
        oneOf: [{ type: "string" }, { type: "array", items: { type: "string" } }] },
      human_quote: { type: "string", description: "the partner's literal words, when he said them. Never paraphrase into this field." },
      session_key: { type: "string", description: "groups entries per session (rule 29). Defaults to <date>-<actor>." },
      provenance: { type: "string", description: "where this came from — a session, a call, a document" },
      cost_delta: { type: "string", description: "WHAT IT COST, in the unit that actually matters for this build — model calls, dollars, minutes of a partner's attention, added latency. Free text on purpose, because the unit changes per build and forcing a number would force a fake one. Must be passed together with quality_delta: half a price is not a price. Example: \"+60% inference cost per finished draft, 3 model calls where there was 1\"." },
      quality_delta: { type: "string", description: "WHAT IT BOUGHT, stated as before and after against a named baseline, never as an after-value alone. A delta with no baseline is the same unfalsifiable claim the skeptic chair already refuses on client work. Must be passed together with cost_delta. Example: \"approval 45% -> 82.5%, measured on the same 40 drafts\"." },
      occurred_at: { type: "string", description: "when it was decided; defaults now" } },
      required: ["idempotency_key","title","rationale"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "log-decision", args, async () => {
      // [loop #278] Resolve `about` BEFORE the decision is inserted. resolveSubject
      // throws not_found / needs_disambiguation, and a throw here must leave no
      // decision behind — an orphaned ruling written by a mistyped ref is exactly the
      // record this loop exists to stop creating. Resolution happens twice on the happy
      // path (here to validate, again inside mirrorDecision to write); that is a couple
      // of indexed reads against correctness, which is not a trade worth making.
      const aboutRefs = args.about
        ? (Array.isArray(args.about) ? args.about : [args.about]).map(r => String(r || "").trim()).filter(Boolean)
        : [];
      for (const ref of aboutRefs) await resolveSubject(c, ref);

      // [idea 68, 0085] BOTH HALVES OF A PRICE OR NEITHER. A cost with no
      // quality number is a complaint and a quality number with no cost is a
      // boast; either alone is the selective reporting the discipline exists to
      // stop, so one without the other is refused rather than stored. This is a
      // doctrine rule about honest reporting, which is why it lives here and not
      // in a CHECK constraint — the same reasoning R-40a applies to grouping.
      const costDelta = (args.cost_delta || "").trim() || null;
      const qualityDelta = (args.quality_delta || "").trim() || null;
      if (Boolean(costDelta) !== Boolean(qualityDelta))
        throw new ToolError({ error: "half_a_price",
          got: costDelta ? "cost_delta only" : "quality_delta only",
          hint: "pass cost_delta AND quality_delta together, or neither. What a build cost is only meaningful beside what it bought, and a quality claim with no cost beside it is unfalsifiable. If the other half genuinely is not known yet, log the decision unpriced and add both later with update-decision." });
      const decisionId = (await c.query("select gen_random_uuid() as id")).rows[0].id;
      const identity = auditIdentity(actor);
      const r = await c.query(
        `insert into event (occurred_at, actor_id, verb, subject_type, subject_id,
           new_value, cause, human_quote, agent_rationale, idempotency_key, via, client_id,
           organization_tenant_id, sponsoring_human_slug, personal_scope, authorization_class, correlation_id)
         values (coalesce($1::timestamptz, now()), $2, 'log-decision', 'decision', $3,
                 $4, 'human_stated', $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
         -- to_char, not ::date: node-postgres hands a ::date back as a JS Date, and
         -- interpolating that into session_key produced
         -- "Sun Aug 02 2026 00:00:00 GMT+0000 (Coordinated Universal Time)-joe"
         -- on the first live decision. A text date interpolates as a text date.
         returning id, to_char(coalesce($1::timestamptz, now()), 'YYYY-MM-DD') as entry_date`,
        [args.occurred_at || null, actor.id, decisionId,
         JSON.stringify({ title: args.title,
                          quote_absent: !args.human_quote,
                          provenance: args.provenance || null,
                          // Keys are OMITTED when unpriced rather than written as
                          // null, because v_decision_entry.priced tests key
                          // PRESENCE (new_value ? 'cost_delta'). A null-valued key
                          // would read as priced-with-no-price.
                          ...(costDelta ? { cost_delta: costDelta,
                                            quality_delta: qualityDelta } : {}) }),
         args.human_quote || null, args.rationale, args.idempotency_key,
         actor.via || null, actor.client_id || null, identity.organization_tenant_id,
         identity.sponsoring_human_slug, identity.personal_scope, identity.authorization_class,
         identity.correlation_id]);

      const ev = r.rows[0];
      const sessionKey = args.session_key || `${ev.entry_date}-${actor.slug}`;
      // source_file 'live' distinguishes verb-written entries from the imported
      // ones, whose key carries the markdown file they came out of.
      //
      // The event id is the THIRD segment and it is load-bearing: record_source is
      // unique on (source_system, external_key) and v_decision_entry JOINS through
      // it, so a key of just 'live#<session>' would make the second decision of any
      // session collide and vanish from the render entirely. The view reads
      // source_file from segment 1 and session_key from segment 2, so a third
      // segment costs nothing and buys per-decision uniqueness.
      await c.query(
        `insert into record_source (entity_type, entity_id, source_system, external_key)
         values ('event', $1, 'decision-history', $2)
         on conflict (source_system, external_key) do nothing`,
        [ev.id, `live#${sessionKey}#${ev.id}`]);

      // [loop #278] Mirror the ruling onto the record it governs. A SECOND event row,
      // not a rewrite of the first: v_decision_entry keys off subject_type='decision'
      // and decision-history.md must keep rendering exactly as it did, so the decision
      // row is left untouched and the record gets its own pointer at it.
      //
      // event.idempotency_key is NON-unique by design (0001, [A1]: "one tool call may
      // write several event rows; replay is tool_call's job"), so both rows carry the
      // same key and the tool_call replay table still guards the call as one unit.
      //
      // new_value.summary is the 0082 hook: v_subject_timeline reads it as the row's
      // summary, so catch-me-up on the record shows WHAT was decided instead of the
      // bare verb. decision_id and event_id ride along so the full entry is one hop away.
      const attached = await mirrorDecision(c, actor, {
        about: aboutRefs, decision_id: decisionId, decision_event_id: ev.id,
        title: args.title, human_quote: args.human_quote, rationale: args.rationale,
        occurred_at: args.occurred_at, idempotency_key: args.idempotency_key });

      return { ok: true, decision_id: decisionId, event_id: ev.id,
               session_key: sessionKey, quote_absent: !args.human_quote,
               about: attached.map(a => ({ type: a.type, id: a.id, ref: a.ref })),
               renders_into: "00_Context/decision-history.md",
               ...(attached.length ? {} : { hint: "no `about` ref given — this ruling is reachable from decision-history only, not from any record's timeline. That is correct for a build or doctrine ruling and wrong for anything about a client, lead, vendor or deal. update-decision takes `about` if you want to attach it later." }) };
    }),
  },

  // ---------- correcting a decision already recorded ----------
  // Added 2026-08-02 because two decision entries landed with quote_absent:true when the
  // session malformed the human_quote parameter twice. The flag then read as "no quote
  // existed" when the truth was that one existed and was fumbled — a defective record, not
  // history worth preserving. Joe: "create an update-decision verb and then update them".
  //
  // MUTATES THE ENTRY, APPENDS THE AMENDMENT. The decision event itself is corrected in
  // place so v_decision_entry and decision-history.md read the truth, AND a separate
  // amend-decision event records what changed and why. Correcting the record without a
  // trace would be the worse half of both options.
  "update-decision": {
    write: true,
    description: "Correct a decision entry already recorded by log-decision — a wrong or missing title, rationale, human_quote or provenance — or ATTACH it to the record(s) it governs after the fact with `about`. Use for a DEFECTIVE record (a quote that was lost, a rationale that stated something untrue), never to rewrite what was actually decided: a decision that CHANGED is a new log-decision, because the old one really was the call at the time. Attaching is different from correcting and is always safe: it adds a pointer on the record's timeline, changes nothing about the entry itself, and re-attaching the same record is a no-op rather than a second pointer. Pass only the fields you are correcting. Re-derives quote_absent from whether a quote is present afterwards, and appends an amend-decision event recording the change.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      decision_id: { type: "string", description: "the decision this corrects — the full uuid log-decision returned, OR the 8-character short form this system prints in loop source_notes and decision-history, e.g. 'f58ffba8'. A prefix matching more than one decision is refused with the candidates listed rather than guessed at." },
      title: { type: "string" }, rationale: { type: "string" },
      about: { description: "attach this ruling to the record(s) it is ABOUT, now — one ref or several: \"C-063\" or [\"V-GC-001\",\"V-MSC-024\",\"T-004\"]. This is how a decision logged without `about` gets connected later. Only pass records the ruling is genuinely ABOUT: a session-level build decision that merely MENTIONS a ref in its rationale is not about that record, and attaching it there puts noise on a timeline a human reads before a client conversation.",
        oneOf: [{ type: "string" }, { type: "array", items: { type: "string" } }] },
      human_quote: { type: "string", description: "the partner's literal words. Never paraphrase into this field." },
      provenance: { type: "string" },
      cost_delta: { type: "string", description: "WHAT IT COST — see log-decision. This is the path for pricing a build whose numbers were not known at the moment it shipped, which is the common case: you rarely have the after-measure on the day. Must be passed together with quality_delta." },
      quality_delta: { type: "string", description: "WHAT IT BOUGHT, before and after against a named baseline — see log-decision. Must be passed together with cost_delta." },
      reason: { type: "string", description: "why the entry needed correcting — recorded on the amendment" } },
      required: ["idempotency_key","decision_id"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "update-decision", args, async () => {
      // Resolved BEFORE the read, so the short form the system prints is a first-class
      // argument rather than a 22P02 on a uuid column (defect 69fb49b1).
      const decisionId = await resolveDecisionId(c, args.decision_id);
      const cur = (await c.query(
        `select id, new_value, human_quote, agent_rationale from event
          where subject_id = $1 and verb = 'log-decision' limit 1`, [decisionId])).rows[0];
      if (!cur) throw new ToolError({ error: "decision_not_found", decision_id: args.decision_id,
        hint: "pass the decision_id log-decision returned, not the event_id" });

      const nv = cur.new_value || {};
      const quote = args.human_quote !== undefined ? args.human_quote : cur.human_quote;

      // [idea 68, 0085] Same both-or-neither rule log-decision enforces, applied to
      // the after-the-fact path — which is the COMMON path for pricing, because the
      // after-measure rarely exists on the day a build ships. Judged against the
      // MERGED state, not the arguments alone, so passing one half to a decision
      // that already carries the other is a completion rather than a violation.
      const mergedCost = args.cost_delta !== undefined
        ? ((args.cost_delta || "").trim() || null) : (nv.cost_delta || null);
      const mergedQuality = args.quality_delta !== undefined
        ? ((args.quality_delta || "").trim() || null) : (nv.quality_delta || null);
      if (Boolean(mergedCost) !== Boolean(mergedQuality))
        throw new ToolError({ error: "half_a_price",
          got: mergedCost ? "cost_delta only" : "quality_delta only",
          hint: "a decision carries both halves of a price or neither. Pass whichever half is missing in the same call." });

      const next = { ...nv,
        title: args.title !== undefined ? args.title : nv.title,
        provenance: args.provenance !== undefined ? args.provenance : nv.provenance,
        quote_absent: !quote };
      // Presence, not null — v_decision_entry.priced tests for the key itself.
      if (mergedCost) { next.cost_delta = mergedCost; next.quality_delta = mergedQuality; }
      else { delete next.cost_delta; delete next.quality_delta; }

      await c.query(
        `update event set new_value = $1, human_quote = $2, agent_rationale = $3 where id = $4`,
        [JSON.stringify(next), quote || null,
         args.rationale !== undefined ? args.rationale : cur.agent_rationale, cur.id]);

      // [loop #278] Attach after the fact, through the SAME helper log-decision uses, so
      // a late attachment is indistinguishable from one made at the time. The mirror
      // carries the CURRENT title, which is right: an entry corrected here should not
      // leave the old wording sitting on a record's timeline.
      const attached = await mirrorDecision(c, actor, {
        about: args.about, decision_id: decisionId, decision_event_id: cur.id,
        title: next.title, human_quote: quote,
        rationale: args.rationale !== undefined ? args.rationale : cur.agent_rationale,
        idempotency_key: args.idempotency_key });

      const changed = ["title","rationale","human_quote","provenance","cost_delta","quality_delta"]
        .filter(f => args[f] !== undefined);
      await writeEvent(c, actor, "amend-decision", "decision", decisionId,
        { old: { quote_absent: nv.quote_absent },
          new: { fields: changed, quote_absent: !quote,
                 ...(attached.length ? { attached_to: attached.map(a => a.ref) } : {}) },
          agent_rationale: args.reason || null, idempotency_key: args.idempotency_key });

      return { ok: true, decision_id: decisionId, amended: changed, quote_absent: !quote,
               about: attached.map(a => ({ type: a.type, id: a.id, ref: a.ref, already_attached: a.already })) };
    }),
  },

  // ---------- taking a decision back off a record ----------
  // Joe, 2026-08-09, on whether a wrong pointer should be deletable: "okay then keep it
  // for the audit log but just make sure its clear".
  //
  // So this does NOT delete. The pointer row stays exactly where it is, because someone
  // attaching a ruling to the wrong client is a thing that happened and the event log is
  // where things that happened live. What changes is that the row now SAYS SO, in the one
  // field a human reads: its timeline summary is struck through with RETRACTED and the
  // reason, so the ruling can never be mistaken for a live one at a glance.
  //
  // Same mutate-in-place-plus-append-the-amendment shape update-decision already uses:
  // the pointer is corrected where it is read, and a separate detach-decision event
  // records who retracted it and why.
  "detach-decision": {
    write: true,
    description: "Take a decision back off a record it was wrongly attached to. NOTHING IS DELETED: the pointer stays on the timeline as a permanent audit row, restated so a reader sees at a glance that it was retracted and why — a wrong attachment is a thing that happened, and hiding it would be the worse record. Use when a ruling was attached to a client, lead, vendor or party it is not actually about. NOT for a decision that has CHANGED (that is a new log-decision) and NOT for fixing the entry's own wording (that is update-decision). Re-attaching afterwards is allowed and writes a fresh live pointer beside the retracted one, so the timeline shows attached → retracted → attached rather than a row quietly coming back to life.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      decision_id: { type: "string", description: "the decision the pointer carries — the full uuid, or the 8-character short form this system prints. Resolved the same way update-decision resolves it, so a ref read off a timeline can be passed straight back." },
      from: { type: "string", description: "the record to take it off — C-127 / L-204 / V-CPA-006 / P-0948 / a deal name" },
      reason: { type: "string", description: "why it does not belong there. REQUIRED — this is what the retracted row shows a future reader, and 'wrong' with no cause is how the same mistake gets made again." } },
      required: ["idempotency_key","decision_id","from","reason"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "detach-decision", args, async () => {
      if (!String(args.reason || "").trim())
        throw new ToolError({ error: "missing_reason",
          hint: "say why the ruling does not belong on this record; the retracted row carries it forward" });

      // The pointer stores the decision_id as TEXT, so a short form here never
      // raised 22P02 the way update-decision did — it simply matched nothing and
      // reported not_attached, which reads as "no such pointer" rather than "wrong
      // form of id". Same defect, quieter symptom, same one-line resolution.
      const decisionId = await resolveDecisionId(c, args.decision_id);
      const s = await resolveSubject(c, args.from);
      const ptr = (await c.query(
        `select id, new_value from event
          where subject_type = $1 and subject_id = $2 and field = 'decision'
            and new_value->>'decision_id' = $3
          order by coalesce((new_value->>'retracted')::boolean, false), recorded_at desc
          limit 1`,
        [s.type, s.id, decisionId])).rows[0];
      if (!ptr) throw new ToolError({ error: "not_attached", decision_id: decisionId, from: args.from,
        hint: "this decision has no pointer on that record — check catch-me-up on it first" });

      const nv = ptr.new_value || {};
      if (nv.retracted)
        return { ok: true, decision_id: decisionId, from: args.from,
                 already_retracted: true, reason: nv.retracted_reason || null,
                 hint: "this pointer was already retracted; nothing changed" };

      // The summary is the ONLY field v_subject_timeline surfaces, so the retraction has
      // to live there or a reader never sees it. The original text is kept verbatim
      // behind the marker and preserved whole in summary_before_retraction.
      const original = nv.summary || "";
      const next = { ...nv,
        retracted: true,
        retracted_reason: args.reason,
        retracted_by: actor.slug,
        summary_before_retraction: original,
        summary: `RETRACTED — not about this record (${args.reason}) — was: ${original}` };

      await c.query("update event set new_value = $1 where id = $2",
        [JSON.stringify(next), ptr.id]);

      await writeEvent(c, actor, "detach-decision", s.type, s.id, {
        field: "decision_retracted",
        old: { summary: original, decision_id: decisionId },
        new: { summary: `retracted a decision pointer: ${args.reason}`, decision_id: decisionId },
        agent_rationale: args.reason, idempotency_key: args.idempotency_key });

      return { ok: true, decision_id: decisionId,
               from: { type: s.type, id: s.id, ref: args.from },
               retracted: true, retained_as_audit_row: true, pointer_event_id: ptr.id };
    }),
  },

  // ---------- the loop accumulators (one-writer Phase A, ORDER 31) ----------
  // open-loops.md, open-loops-backlog.md, action-required.md and team-loops.md
  // are generated renders of loop_item after this lands. Sessions stop editing
  // those four files and use these three verbs instead — which is the whole
  // point of Joe's ruling: "if i do something in my session, i want dell to be
  // able to instantly recall it in his session."

  "add-loop": {
    write: true,
    description: "Open a new loop — a Joe/Dell task (kind open_loop), a partner handoff (team_loop), a cross-brain interrupt (action_required), or a parked idea (kind idea, which renders into 00_Context/idea-bank.md and is personal, never shared). Do NOT hand-edit open-loops.md, open-loops-backlog.md, action-required.md or team-loops.md; they are rendered from this. Markers carry meaning the heartbeat obeys: `bell` = actionable THIS WEEK (hard cap 3 PER DOMAIN — more than 3 means re-tier, not stack; read v_loop_bell_cap for breaches. The old cap was 5 across the whole hot list, written before domains existed: with six lanes that was under one bell each, so everything drifted to 'none' until the hot list held 21 items against a cap of 5), `dated` + due_on = silent until its day, `decision` = a ❓ the Monday brief surfaces, `none` = backlog. An open_loop with bell, or a dated one already due, lands hot; everything else lands in the backlog, which is the file's own rule. The action_required bar is deliberately high: only a new shared mechanism, a build the other side must replicate, or a protocol change — if everything is urgent, nothing is. THE DEFERRAL GATE: an open_loop is REFUSED unless it names `blocker` (a closed list of states of the world outside this session) and `blocker_detail` (the specific person, ruling, date or credential). There is no value meaning 'later'. Before filing one, ask whether this session could just do the work — if it could, do it and file nothing, because a session with the context already loaded is the cheapest builder this item will ever get.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      kind: { type: "string", enum: LOOP_KINDS },
      domain: { type: "string", enum: ["deals","prospecting","networking","marketing","business","system"],
        description: "deals | prospecting | networking | marketing | business | system. Classify by WHAT THE WORK IS, not who appears in it: a vendor introducing a PROSPECT normally means real intent and is DEALS (prospecting only while no deal has formed); a vendor introducing a VENDOR is networking; connecting a prospect to a vendor is networking; connecting a client to a vendor on a LIVE deal is deals. Omit only when genuinely unclear — an unclassified loop renders in its own unsorted section, which is honest, but a loop nobody can find is a loop nobody does." },
      title: { type: "string", description: "team_loop 'Ask' / action_required 'Action needed'. Not used by open_loop, whose text is `body`." },
      body: { type: "string", description: "open_loop 'Item' / team_loop 'Notes / links'" },
      owner: { type: "string", description: "the label the file uses: 'Joe', 'Joe/Claude', 'Dell', 'Joe→Dell'" },
      unblocks: { type: "string", description: "what it unblocks / why it matters" },
      source_note: { type: "string", description: "source / detail / links" },
      marker: { type: "string", enum: LOOP_MARKERS },
      due_on: { type: "string", description: "YYYY-MM-DD; required when marker is 'dated'" },
      drift_critical: { type: "boolean", description: "the ⚡ — leaving it undone causes system drift; BOTH brains' heartbeats surface it daily" },
      number: { type: "string", description: "override the auto-assigned ref. Only pass this to reproduce a number that already exists somewhere; the files already contain collisions." },
      since: { type: "string", description: "YYYY-MM-DD; defaults to today" },
      blocker: { type: "string", enum: BLOCKER_CLASSES,
        description: "REQUIRED on kind open_loop: why THIS session cannot do the work now. Every value is a state of the world outside the session, and the list is closed on purpose — there is no value meaning 'later'. human_only = needs Joe or Dell in person (a call, a signature, a site visit, a login only he holds) · counterparty = waiting on someone outside (name the landlord, broker, client or vendor) · ruling = needs Joe's decision (state the question) · external_event = a dated event must arrive first (name the date) · other_lane = depends on another lane's in-flight deliverable (name it) · capability = a credential, gate or verb this session cannot hold (name it). IF NONE OF THESE FIT, DO NOT FILE THE LOOP — do the work. Not asked for team_loop or action_required (the blocker is the other partner by construction) or for idea (parked by design)." },
      blocker_detail: { type: "string",
        description: "REQUIRED whenever `blocker` is set: the SPECIFIC thing, named. 'the landlord' is not a counterparty; 'Sanders, the listing broker on C-112' is. 'a ruling' is not a ruling; 'whether the 3% escalation cap applies to renewal years' is." } },
      required: ["idempotency_key", "kind", "owner"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "add-loop", args, async () => {
      // kind decides EVERYTHING downstream — the deferral gate, the tier, and
      // which block the row renders into — so it is checked before anything
      // else. See LOOP_KINDS for the live defect this closes (2026-08-14: an
      // omitted kind surfaced as a no_block error blaming the loop importer).
      if (args.kind === undefined || args.kind === null)
        throw new ToolError({ error: "missing_kind", allowed: LOOP_KINDS,
          hint: "kind is required and decides where the loop lives: open_loop (a Joe/Dell task), team_loop (partner handoff), action_required (cross-brain interrupt), or idea (parked, personal)" });
      if (!LOOP_KINDS.includes(args.kind))
        throw new ToolError({ error: "unknown_kind", got: args.kind, allowed: LOOP_KINDS,
          hint: "kind must be one of open_loop/team_loop/action_required/idea — see this verb's description for what each means" });
      if (!args.title && !args.body)
        throw new ToolError({ error: "empty_loop",
          hint: "a loop needs text: `body` for an open_loop, `title` for a team_loop or action_required" });
      // THE OTHER HALF OF DEFECT 2 (found 2026-08-13, decision 7026246b): marker
      // is documented in inputSchema as an enum but was never checked before
      // hitting loop_item's CHECK constraint. An illegal value ('wrench' — not
      // in bell/dated/decision/none) reached the DB raw and came back as a bare
      // {"error":"internal error"}, reproduced twice live. Validate up front,
      // exactly like BLOCKER_CLASSES does for `blocker` a few lines below.
      if (args.marker !== undefined && !LOOP_MARKERS.includes(args.marker))
        throw new ToolError({ error: "unknown_marker", got: args.marker, allowed: LOOP_MARKERS,
          hint: "marker must be one of bell/dated/decision/none — the file's own convention " +
                "(see this verb's description for what each means)" });
      if (args.marker === "dated" && !args.due_on)
        throw new ToolError({ error: "dated_marker_needs_date",
          hint: "a 🗓 row is silent until its day — without a date it would be silent forever" });
      // Same class of bug, same fix: domain is a reference-table taxonomy
      // (loop_domain, 'open taxonomy, insert a row not a migration' per this
      // system's own convention — rule 0001), enforced by a FOREIGN KEY rather
      // than a CHECK, but an unrecognized slug fails exactly the same way: a
      // raw constraint violation with no field named. Checked against the live
      // table rather than a hardcoded list, because the taxonomy is deliberately
      // open to a new row without a code change.
      if (args.domain !== undefined && args.domain !== null) {
        const dom = await c.query("select slug from loop_domain where slug=$1", [args.domain]);
        if (!dom.rows.length) {
          const all = await c.query("select slug from loop_domain order by sort asc");
          throw new ToolError({ error: "unknown_domain", got: args.domain,
            allowed: all.rows.map(r => r.slug),
            hint: "classify by what the WORK is, not who appears in it — see this verb's description" });
        }
      }

      // ── THE DEFERRAL GATE (migration 0081, Joe 2026-08-09) ──────────────────
      // Joe taught rule 179be4b8 on 2026-08-08 — "why would you put them off?
      // thats the exact reason we have a giant growing list of loops" — and one
      // day later the list stood at 189 open rows. That rule binds at BUILD
      // WRAP-UP; nothing ever bound at the moment a session decides to file
      // instead of finish. This is that moment, and it is the only place the
      // question can be asked while the session still has the context to answer
      // it. A session that cannot name a blocker has just demonstrated it could
      // have done the work.
      const blockerGated = args.kind === "open_loop";
      // A BLOCKER ON A KIND THAT DOES NOT CARRY ONE USED TO BE DROPPED IN
      // SILENCE. Only open_loop rows are blocker-gated, and the insert below
      // writes `blockerGated ? args.blocker : null` — so a blocker passed on a
      // team_loop, action_required or idea row was accepted, reported back as a
      // successful create, and stored as NULL. Same defect class as e34d2b88
      // (a write verb accepted a field and silently discarded it), found by the
      // sweep loop #476 asked for. Refuse instead: the caller either meant a
      // different kind or did not need the field.
      if (!blockerGated && (args.blocker !== undefined || args.blocker_detail !== undefined))
        throw new ToolError({ error: "blocker_not_carried_by_kind",
          kind: args.kind, blocker: args.blocker || null,
          hint: "only an open_loop carries a blocker — on every other kind the field would be " +
                "stored as null, so this refuses rather than reporting a write that did not " +
                "happen. Drop the blocker, or file this as kind:'open_loop'." });
      if (blockerGated) {
        if (!args.blocker)
          throw new ToolError({ error: "blocker_required",
            classes: BLOCKER_CLASSES,
            hint: "an open_loop must name why THIS session cannot do the work now, from the closed list in `blocker`. There is no value meaning 'later' on purpose. If none of them fits, the answer is not a better loop — it is to do the work now and file nothing." });
        if (!BLOCKER_CLASSES.includes(args.blocker))
          throw new ToolError({ error: "unknown_blocker_class",
            got: args.blocker, classes: BLOCKER_CLASSES,
            hint: "the list is closed — every entry is a state of the world outside this session" });
        const detail = (args.blocker_detail || "").trim();
        if (detail.length < 12)
          throw new ToolError({ error: "blocker_detail_required", got: detail || null,
            hint: "name the SPECIFIC thing: which person, which ruling, which date, which credential. A class with no specific thing is the vague deferral wearing a label." });
        const vague = VAGUE_BLOCKER_RE.exec(detail);
        if (vague)
          throw new ToolError({ error: "blocker_detail_vague", matched: vague[0],
            hint: `"${vague[0]}" names a feeling about time, not a blocker. Say who or what has to happen first — and if nothing has to, do the work now instead of filing this.` });
      }
      // capability_no_decider and internal_decision_parked (bypass audit
      // C33/C34, ported from hooks/blocker-decider-gate.py and
      // hooks/escalation-gate.py) are enforced in this module's
      // executeRegisteredTool(), before this handler ever runs — see the
      // comment there for why that placement (the CANONICAL one, since
      // break-glass bypasses mcp.js's callTool() entirely) is what makes
      // every door, including break-glass, hit the same check. callTool()
      // also runs the same imported check earlier, purely as a fail-fast
      // ahead of its writer-pool connect — see its comment.

      // ── THE OWNERSHIP GATE ──────────────────────────────────────────────
      // Refuses a jointly-owned row at the moment it is filed. See LOOP_OWNERS
      // above for why: joint ownership is what stops the system ever draining
      // the backlog on its own initiative.
      args.owner = assertSingleOwner(args.owner);

      // THE CREATION-SIDE MIRROR of update-loop's due_date_needs_dated_marker.
      // Omitting the marker and passing a date INFERS 'dated', which is the
      // convenient path and stays. Passing an explicit non-dated marker
      // alongside a date was the bad one: the date was stored on a row whose
      // marker sends it elsewhere, and nothing reads due_on except on a dated
      // row — so the value sat in the column, honoured by nobody. Stored-and-
      // inert is the same lie as dropped, told in a way that is harder to spot.
      if (args.due_on && args.marker !== undefined && args.marker !== "dated")
        throw new ToolError({ error: "due_date_needs_dated_marker",
          marker: args.marker, due_on: args.due_on,
          hint: "a due date is only acted on when the marker is 'dated' — on any other marker " +
                "it would sit in the column unread. Pass marker:'dated', or omit the marker " +
                "entirely and it is inferred, or drop the date." });
      const marker = args.marker || (args.due_on ? "dated" : "none");
      const literal = marker === "bell" ? "🔔"
        : marker === "decision" ? "❓"
        : marker === "dated" ? `🗓${args.due_on}` : null;

      // Placement follows the files' own rule. It is STORED, not derived, so a
      // later promotion is a recorded act (see v_loop_promotion_due).
      const nowDue = marker === "dated" && args.due_on <= new Date().toISOString().slice(0, 10);
      // 'idea' has no "open" block — its live section is 'parked' (44 rows) and
      // 'retired' is its closed state. Asking for "open" would throw no_block.
      const wantKey = args.kind === "idea" ? "parked"
        : args.kind !== "open_loop" ? "open"
        : (marker === "bell" || nowDue) ? "hot" : "backlog";
      const b = await c.query(
        "select id, rel_path, col_order from loop_block where kind=$1 and block_key=$2",
        [args.kind, wantKey]);
      if (!b.rows.length)
        throw new ToolError({ error: "no_block", kind: args.kind, section: wantKey,
          hint: "the loop importer has not run for this kind — nothing to render into" });
      const block = b.rows[0];

      const num = args.number || await nextLoopNumber(c, args.kind);
      const seq = await nextRenderSeq(c, block.id);
      const tier = (args.kind === "open_loop" || args.kind === "idea") ? "personal" : "shared";
      const personal = tier === "personal" ? actor.id : null;

      const r = await c.query(
        `insert into loop_item (kind, number, block_id, render_seq, title, body, owner,
           since_text, unblocks, source_note, marker, marker_literal, due_on,
           drift_critical, status, tier, personal_to, created_by, updated_by, domain,
           blocker_class, blocker_detail)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,'open',$15,$16,$17,$17,$18,$19,$20)
         returning id`,
        [args.kind, num, block.id, seq, args.title || null, args.body || null, args.owner,
         args.since || new Date().toISOString().slice(0, 10), args.unblocks || null,
         args.source_note || null, marker, literal, args.due_on || null,
         args.drift_critical === true, tier, personal, actor.id, args.domain || null,
         blockerGated ? args.blocker : null,
         blockerGated ? args.blocker_detail.trim() : null]);

      // The blocker goes on the EVENT as well as the row: an open_loop that was
      // filed under a blocker which later turns out to be false is a thing Joe
      // should be able to find, and events are the only surface that keeps the
      // claim as it was made on the day it was made.
      await writeEvent(c, actor, "add-loop", "loop", r.rows[0].id,
        { new: { number: num, kind: args.kind, section: wantKey, marker,
                 due_on: args.due_on || null, owner: args.owner, domain: args.domain || null,
                 blocker_class: blockerGated ? args.blocker : null,
                 blocker_detail: blockerGated ? args.blocker_detail.trim() : null },
          idempotency_key: args.idempotency_key });
      return { ok: true, loop_id: r.rows[0].id, number: num, kind: args.kind,
               section: wantKey, renders_into: block.rel_path,
               blocker: blockerGated ? args.blocker : null };
    }),
  },

  "update-loop": {
    write: true,
    description: "Change an open loop — its text, its owner, its marker, or which section it sits in. This is also how a due backlog row gets PROMOTED to the hot list: pass section 'hot'. Promotion is a recorded act by an actor, never something a view does to Joe's file behind his back — read v_loop_promotion_due for what has come due. Pass only the fields you are changing; anything omitted is left alone. Closing is a different act: use close-loop, which requires an outcome.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      loop_id: { type: "string" },
      number: { type: "string", description: "alternative to loop_id; refuses when the number is ambiguous, and several are. To RENUMBER the row, pass a different number and renumber_reason: two open rows of the same kind can carry the same number, and every verb that resolves by number then refuses. Refused if the target number is already taken by another OPEN row of this kind." },
      kind: { type: "string", enum: LOOP_KINDS, description: "narrows an ambiguous number" },
      base_version: { type: "integer" },
      title: { type: "string" }, body: { type: "string" }, owner: { type: "string" },
      unblocks: { type: "string" }, source_note: { type: "string" },
      domain: { type: "string", enum: ["deals","prospecting","networking","marketing","business","system"],
        description: "reclassify the loop. Same rule as add-loop: classify by what the WORK is, not who appears in it." },
      marker: { type: "string", enum: LOOP_MARKERS },
      due_on: { type: "string", description: "YYYY-MM-DD. ONLY STORED ON A 'dated' ROW. On any " +
        "other marker this is refused rather than silently dropped — pass marker:'dated' in the " +
        "same call to promote the row, or omit the date. A bare due_on on a row that is already " +
        "dated is a reschedule and is fine. add-loop documents this dependency the other way " +
        "round (the date is required WHEN the marker is dated), which reads as a constraint on " +
        "the marker and is why a careful caller still walked into it — defect e34d2b88, 46 " +
        "writes lost on 2026-08-20." },
      drift_critical: { type: "boolean" },
      blocker: { type: "string", enum: ["human_only","counterparty","ruling","external_event","other_lane","capability"],
        description: "REVISE what the loop is waiting on. add-loop refuses a loop without a blocker, but until 2026-08-09 nothing could change one, so a blocker named on day one was permanent even after the real obstacle turned out to be different — found on loop #295, whose blocker read human_only until building it revealed the actual block was a missing corpus (other_lane). Changing this requires blocker_detail too: a reclassification with the old specifics attached is worse than the original, because it reads as current and is not." },
      blocker_detail: { type: "string", description: "the SPECIFIC thing, restated for the new class: which person, which ruling, which date, which prerequisite. Required whenever blocker changes; may also be passed alone to sharpen the detail without reclassifying." },
      renumber_reason: { type: "string", description: "REQUIRED whenever number changes: why, and where the old number still appears. Rule 7105955b binds here — a renumbered row is not an abandoned one, and the note recording the change has to say so in its first words, because the old number lives on in other rows' prose and in every generated render." },
      last_surfaced: { type: "string", description: "IDEA ROWS ONLY: stamp the idea bank's 'Last surfaced' column, YYYY-MM-DD. This is what the monthly resurface writes when a parked idea is presented and KEPT — the column its own rotation reads to pick the oldest ideas next month. Blank or '—' means never surfaced, so leaving it unwritten is not neutral: it keeps re-presenting the same rows." },
      section: { type: "string", enum: ["hot", "backlog", "open"], description: "move the row to this section of its file" } },
      required: ["idempotency_key"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "update-loop", args, async () => {
      const cur = await resolveLoop(c, args);
      await versionGuard(c, "loop_item", cur.id, args.base_version);
      if (cur.status !== "open")
        throw new ToolError({ error: "loop_not_open", loop_id: cur.id, status: cur.status,
          hint: "a closed loop is history; open a new one rather than editing the record of what happened" });

      // Ownership gate on the edit path too — otherwise the rule holds only for
      // rows filed after it shipped, and a session that wants a joint owner
      // just files clean and edits it back. See LOOP_OWNERS above.
      if (args.owner !== undefined) args.owner = assertSingleOwner(args.owner);

      const sets = [], vals = [];
      const set = (col, v) => { vals.push(v); sets.push(`${col}=$${vals.length}`); };
      for (const f of ["title", "body", "owner", "unblocks", "source_note", "domain"])
        if (args[f] !== undefined) set(f, args[f]);
      if (args.drift_critical !== undefined) set("drift_critical", args.drift_critical === true);

      // A blocker may be REVISED, because what a loop is waiting on is a finding
      // and findings change. Reclassifying without restating the specifics is
      // refused: a row reading `other_lane` above a detail that explains a
      // `human_only` block is worse than the stale original, because it reads as
      // current. Detail alone is allowed — sharpening the specifics under an
      // unchanged class is exactly what a working loop should do.
      if (args.blocker !== undefined) {
        if (args.blocker_detail === undefined)
          throw new ToolError({ error: "blocker_detail_required",
            hint: "changing the blocker class means restating the specific thing it is now waiting on. Pass blocker_detail in the same call." });
        set("blocker_class", args.blocker);
      }
      if (args.blocker_detail !== undefined) set("blocker_detail", args.blocker_detail);

      // RENUMBER (loop #306). Two open rows of one kind sharing a number is a
      // data-integrity defect, not a cosmetic one: update-loop, close-loop and
      // read-loop all resolve by number and all refuse on an ambiguous one — which
      // is the right behaviour and still leaves the human unable to act, because
      // the failure reads like a broken verb rather than like broken data. Until
      // now nothing could set this column, so the only workaround was to pass
      // loop_id, which silently picks whichever row the caller happened to look up.
      if (args.number !== undefined) {
        const next = String(args.number).replace(/^#/, "").trim();
        if (!/^\d+$/.test(next))
          throw new ToolError({ error: "bad_number", got: args.number,
            hint: "digits only — the renders sort on this and a free-form ref sorts wrong forever" });
        // THE REASON IS REQUIRED FOR A RENUMBER, NOT FOR SAYING WHICH ROW YOU
        // MEAN. This check used to sit above the comparison, so it fired on the
        // mere PRESENCE of `number` — and `number` is also this verb's
        // documented way to identify a row ("alternative to loop_id" in its own
        // input schema). The result: editing a loop by the number a human
        // actually says was refused with an error about renumbering, and the
        // only way through was to fetch the row with read-loop and pass
        // loop_id, one extra round trip to work around a guard that was not
        // guarding anything. Hit twice on 2026-08-14 clearing stale blockers.
        // The guard itself is right (rule 7105955b); it was reading the wrong
        // condition. Now it asks only when the number is genuinely CHANGING.
        if (next !== cur.number) {
          if (args.renumber_reason === undefined)
            throw new ToolError({ error: "renumber_reason_required",
              from: cur.number, to: next,
              hint: "rule 7105955b: a renumbered row is not an abandoned one, and the old number " +
                    "survives in other rows' prose and in every render. Say why, in the same call." });
          // The uniqueness index added alongside this enforces it in the database;
          // this check exists so the caller gets the two colliding ids back instead
          // of a constraint name.
          const clash = await c.query(
            "select id from loop_item where kind=$1 and number=$2 and status='open' and id <> $3",
            [cur.kind, next, cur.id]);
          if (clash.rows.length)
            throw new ToolError({ error: "number_taken", kind: cur.kind, number: next,
              held_by: clash.rows.map((x) => x.id),
              hint: "another OPEN row of this kind already carries that number — pick one nothing holds" });
          set("number", next);
        }
      }

      // 'Last surfaced' is an extra_cells key, not a column — the idea bank's two
      // bank-specific columns (Status, Last surfaced) ride in that jsonb because
      // loop_item is generic over four kinds. Until 2026-08-09 no verb could write
      // it, so the monthly resurface stamped source_note instead and the column it
      // rotates on stayed "—" forever (loop #273, the half that outlived the
      // close-loop fix above). MERGE rather than replace: #42 and #44-#47 carry a
      // `domain` and a `status` key in the same object, and a bare assignment would
      // silently drop both.
      if (args.last_surfaced !== undefined) {
        if (cur.kind !== "idea")
          throw new ToolError({ error: "last_surfaced_is_idea_only", kind: cur.kind,
            hint: "only the idea bank renders a 'Last surfaced' column; other kinds have no such cell" });
        if (!/^\d{4}-\d{2}-\d{2}$/.test(args.last_surfaced))
          throw new ToolError({ error: "bad_last_surfaced", got: args.last_surfaced,
            hint: "YYYY-MM-DD. The rotation sorts on this text, so a free-form date " +
                  "silently sorts wrong and the same ideas keep coming back." });
        vals.push(args.last_surfaced);
        sets.push("extra_cells=coalesce(extra_cells,'{}'::jsonb) || " +
                  `jsonb_build_object('last_surfaced', $${vals.length}::text)`);
      }

      if (args.marker !== undefined || args.due_on !== undefined) {
        const marker = args.marker !== undefined ? args.marker : cur.marker;
        const due = args.due_on !== undefined ? args.due_on : cur.due_on;
        if (marker === "dated" && !due)
          throw new ToolError({ error: "dated_marker_needs_date",
            hint: "a 🗓 row without a date is silent forever" });
        // A DATE ON A ROW THAT IS NOT DATED USED TO BE ACCEPTED AND THROWN
        // AWAY. The line below this block writes `marker === "dated" ? due
        // : null`, so a due_on arriving on a row whose marker is none/bell/
        // decision was written straight to NULL — while the call returned
        // success, bumped the version and moved updated_at. On 2026-08-20
        // that swallowed 46 consecutive writes during the idea-bank
        // conversion to due-date selection, caught only because the whole
        // board happened to be read back before reporting (defect e34d2b88,
        // the first of its class: a write verb accepted a field and silently
        // discarded it).
        //
        // REFUSING IS THE FIX, not implicitly promoting the row to "dated".
        // The marker decides which render a row lands in — a dated row that
        // has come due goes hot, everything else goes to the backlog — so
        // inferring the marker from the date would silently move rows on a
        // file Joe reads. Making the caller say both is one extra argument
        // and no surprises.
        //
        // Note what is deliberately still allowed: a bare due_on on a row
        // ALREADY marked dated, which is how a row is rescheduled and has
        // never been ambiguous.
        if (args.due_on !== undefined && args.due_on !== null && marker !== "dated")
          throw new ToolError({ error: "due_date_needs_dated_marker",
            marker, due_on: args.due_on,
            hint: "a due date is only stored on a 'dated' row — on any other marker it " +
                  "would be dropped, so this refuses rather than reporting a write that " +
                  "did not happen. Pass marker:'dated' in the same call, or drop the date." });
        set("marker", marker);
        set("due_on", marker === "dated" ? due : null);
        set("marker_literal", marker === "bell" ? "🔔"
          : marker === "decision" ? "❓"
          : marker === "dated" ? `🗓${due}` : null);
      }

      let moved = null;
      if (args.section && args.section !== cur.section) {
        const b = await c.query(
          "select id, rel_path from loop_block where kind=$1 and block_key=$2",
          [cur.kind, args.section]);
        if (!b.rows.length)
          throw new ToolError({ error: "no_such_section", kind: cur.kind, section: args.section,
            hint: `open_loop has hot and backlog; team_loop and action_required have open` });
        set("block_id", b.rows[0].id);
        set("render_seq", await nextRenderSeq(c, b.rows[0].id));
        moved = { from: cur.section, to: args.section };
      }

      if (!sets.length)
        throw new ToolError({ error: "nothing_to_update",
          hint: "pass at least one field; base_version alone changes nothing" });

      vals.push(actor.id); sets.push(`updated_by=$${vals.length}`);
      vals.push(cur.id);
      await c.query(`update loop_item set ${sets.join(", ")} where id=$${vals.length}`, vals);
      const renumbered = sets.some(s => s.startsWith("number="))
        ? { from: cur.number, to: String(args.number).replace(/^#/, "").trim(),
            reason: args.renumber_reason }
        : null;
      await writeEvent(c, actor, "update-loop", "loop", cur.id,
        { old: renumbered ? { number: cur.number } : undefined,
          new: { changed: sets.map(s => s.split("=")[0]), moved, renumbered },
          idempotency_key: args.idempotency_key });
      return { ok: true, loop_id: cur.id, number: renumbered ? renumbered.to : cur.number,
        moved, renumbered };
    }),
  },

  "loop-headers": {
    write: false,
    description: "Read the standing paragraph that sits at the top of each loops section — the header prose of open-loops.md, its backlog file, team-loops.md, action-required.md and the idea bank. THE GAP THIS CLOSES: that prose is DATA (loop_block.prose_md), held there deliberately so a partner's own words stay his to change instead of being a code edit, but nothing could read it back except a raw table query, so nobody could see that a header had gone stale. Returns each block's file, section, version and prose, so a caller has the base_version edit-loop-header needs without probing a write verb for it.",
    inputSchema: { type: "object", properties: {
      file: { type: "string", description: "narrow to one render, e.g. '00_Context/open-loops.md'; substring match" },
      section: { type: "string", description: "narrow to one section key: hot, backlog, open, done, parked, retired" },
    } },
    handler: async (c, _a, args) => {
      const where = [], params = [];
      if (args.file) { params.push(`%${args.file}%`); where.push(`rel_path ilike $${params.length}`); }
      if (args.section) { params.push(args.section); where.push(`block_key = $${params.length}`); }
      const r = await c.query(
        `select id as block_id, rel_path as file, kind, block_key as section, seq,
                version, coalesce(prose_md,'') as prose_md
           from loop_block ${where.length ? "where " + where.join(" and ") : ""}
          order by rel_path, seq`, params);
      return { count: r.rows.length, blocks: r.rows };
    },
  },

  "edit-loop-header": {
    write: true,
    description: "Rewrite the standing paragraph at the top of one loops section. THE GAP THIS CLOSES (loop #294): this prose is stored data rather than code — migration 0024 put it in loop_block.prose_md on purpose, so Joe's doctrine paragraph stays his to change — but no verb wrote that column, so a header that went stale could only be corrected by a raw table write, which is exactly the kind of write the record layer exists to prevent. It cost something real: open-loops.md's header pointed closed rows at a file that had been a frozen archive since 2026-07-31, a session read the stale pointer instead of the archive's own header, and reported to Joe that the closed-loop history was broken when 152 outcomes were sitting exactly where they belonged. Read the current text with loop-headers first and pass its version back as base_version. Pass the WHOLE replacement paragraph, not a patch — this verb sets the column, it does not merge.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      block_id: { type: "string", description: "exact uuid from loop-headers; wins over file+section" },
      file: { type: "string", description: "the render, e.g. '00_Context/open-loops.md'; use with section" },
      section: { type: "string", description: "the section key within that file: hot, backlog, open, done, parked, retired" },
      base_version: { type: "integer", description: "the version loop-headers returned for this block" },
      prose_md: { type: "string", description: "REQUIRED: the complete replacement paragraph, markdown, exactly as it should render" },
    }, required: ["idempotency_key", "prose_md"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "edit-loop-header", args, async () => {
      const cols = "id, rel_path, kind, block_key, version, coalesce(prose_md,'') as prose_md";
      let r;
      if (args.block_id) {
        r = await c.query(`select ${cols} from loop_block where id=$1`, [args.block_id]);
        if (!r.rows.length) throw new ToolError({ error: "not_found", block_id: args.block_id,
          hint: "no loops section carries that id — read loop-headers for the list" });
      } else {
        if (!args.file || !args.section)
          throw new ToolError({ error: "need_block_id_or_file_and_section",
            hint: "pass block_id, or both file and section — a file alone is ambiguous because most render two sections" });
        r = await c.query(
          `select ${cols} from loop_block where rel_path ilike $1 and block_key = $2`,
          [`%${args.file}%`, args.section]);
        if (!r.rows.length) throw new ToolError({ error: "no_such_section", file: args.file, section: args.section,
          hint: "read loop-headers for the real file/section pairs" });
        if (r.rows.length > 1) throw new ToolError({ error: "ambiguous_file",
          candidates: r.rows.map((x) => ({ block_id: x.id, file: x.rel_path, section: x.block_key })),
          hint: "the file substring matched more than one render — pass block_id" });
      }
      const cur = r.rows[0];
      await versionGuard(c, "loop_block", cur.id, args.base_version);

      // An EMPTY header is not an edit, it is a deletion of the only explanation a
      // reader of that render ever gets. Two of these blocks are Done tables whose
      // prose is a single short line; blanking one silently is indistinguishable
      // from the column never having been populated.
      if (!String(args.prose_md).trim())
        throw new ToolError({ error: "empty_prose",
          hint: "pass the replacement paragraph; to say nothing, say it in words rather than by blanking the header" });
      if (args.prose_md === cur.prose_md)
        throw new ToolError({ error: "nothing_to_update", block_id: cur.id,
          hint: "the text passed is byte-identical to what is stored" });

      await c.query("update loop_block set prose_md=$1, updated_by=$2 where id=$3",
        [args.prose_md, actor.id, cur.id]);
      // The OLD text is kept in the event, in full. This paragraph is doctrine a
      // partner wrote; an edit that leaves no way back is not a correction.
      await writeEvent(c, actor, "edit-loop-header", "loop_block", cur.id,
        { old: { prose_md: cur.prose_md }, new: { prose_md: args.prose_md },
          idempotency_key: args.idempotency_key });
      return { ok: true, block_id: cur.id, file: cur.rel_path, section: cur.block_key,
        was_length: cur.prose_md.length, now_length: args.prose_md.length };
    }),
  },

  "close-loop": {
    write: true,
    description: "Close a loop — done, or deliberately dropped. AN OUTCOME IS REQUIRED and the verb refuses without one: team-loops states the reason in its own words, 'outcomes are how the asker finds out without asking twice.' Say what actually came of it, not that it is closed. Where the row goes depends on whether its file keeps closed rows visible: a team_loop or action_required row moves to its Done table carrying the outcome, and an idea moves to the idea bank's Retired table the same way (its rule 4 is 'move, don't delete — the reasoning stays visible so we don't re-litigate it later'); an open_loop simply leaves the hot/backlog render, the same thing closing a row has always done. Use resolution 'dropped' when it is being abandoned rather than finished — recording an abandonment as done inflates every completion measure built on this.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      loop_id: { type: "string" },
      number: { type: "string", description: "alternative to loop_id; refuses when ambiguous" },
      kind: { type: "string", enum: LOOP_KINDS },
      base_version: { type: "integer" },
      outcome: { type: "string", description: "REQUIRED: what came of it, in your words" },
      resolution: { type: "string", enum: ["done", "dropped"] },
      successor_loop: { type: "string", description: "Required when the row is renumbered, superseded, merged, or split: the open row that carries the work forward. Say it the way you would out loud — the number, with or without the leading hash ('#213' or '213') — or pass its loop_id if you have one." } },
      required: ["idempotency_key", "outcome"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "close-loop", args, async () => {
      // The refusal is first and unconditional. A whitespace-only outcome is no
      // outcome: the record-level CHECK would accept ' ' and the asker would still
      // never find out, which is the failure this rule exists to prevent.
      const outcome = (args.outcome || "").trim();
      if (!outcome)
        throw new ToolError({ error: "outcome_required",
          hint: "close-loop will not close a loop silently — say what came of it. " +
                "If nothing came of it and you are abandoning it, say that and pass resolution 'dropped'." });

      const cur = await resolveLoop(c, args);
      await versionGuard(c, "loop_item", cur.id, args.base_version);
      if (cur.status !== "open")
        throw new ToolError({ error: "loop_not_open", loop_id: cur.id, status: cur.status,
          closed_outcome: cur.close_outcome,
          hint: "already closed — this is what came of it" });

      const resolution = args.resolution || "done";
      // A BOOKKEEPING CLOSE DECLARES ITSELF; it is not sniffed out of free text.
      // This used to be /\b(renumbered|superseded|merged|split)\b/i tested
      // ANYWHERE in the outcome, which conflated two unrelated senses of one
      // word: a LOOP merged into another loop (bookkeeping) versus a PULL
      // REQUEST landing on main (completion). Since main is PR-only with
      // automerge, every honest close of a code loop names a landed pull
      // request — so the guard fired hardest against the most common true
      // completion it would ever see. It refused loop #500 on 2026-08-22, whose
      // work was finished, landed and verified twice (defect 3fe38a2f). "split"
      // and "superseded" carry the same double meaning: a split file, a
      // superseded API endpoint.
      //
      // The anywhere-match bought no real protection either. It filters WORDING
      // rather than substance, and is evaded by writing "landed" instead, which
      // is how that close eventually went through — a guard a caller escapes by
      // reaching for a synonym is not enforcing anything. What actually signals
      // continuing work is the outcome OPENING with a bookkeeping declaration
      // (which the prefix check just below already demands) or a successor loop
      // being named. Both are deliberate acts; neither is an accident of prose.
      //
      // A real bookkeeping close is identifiable three ways, and all three are
      // things this verb already demands of one: the outcome OPENS with the
      // declaration (the prefix check immediately below requires exactly that),
      // or it NAMES the loop the work moved to, or it passes successor_loop.
      // Prose about a branch reaching main does none of those.
      //
      // The narrowing is real and worth naming: an outcome saying "merged with
      // another effort", naming no loop and passing no successor, is no longer
      // flagged. That phrasing already failed the two checks below, so it could
      // never have completed a bookkeeping close — it would only have been
      // refused later and less clearly.
      const bookkeeping =
        /^\s*(renumbered|superseded)\b/i.test(outcome) ||
        /\b(?:superseded\s+by|renumbered\s+(?:to|as)|merged\s+(?:into|with)|split\s+into)\s+(?:open\s+)?(?:loop\s*)?#?\d+/i.test(outcome) ||
        Boolean(args.successor_loop);
      let successor = null;
      if (bookkeeping) {
        if (resolution !== "dropped")
          throw new ToolError({ error: "bookkeeping_close_is_dropped", hint: "continuing work is not done; close it as dropped with the successor named" });
        if (!/^(renumbered|superseded)/i.test(outcome))
          throw new ToolError({ error: "bookkeeping_outcome_prefix", hint: "open a bookkeeping close with RENNUMBERED or SUPERSEDED, not an abandonment claim" });
        if (!args.successor_loop)
          throw new ToolError({ error: "successor_loop_required", hint: "name the open loop that now carries this work; a bookkeeping close cannot read as abandonment" });
        // Accept the form a partner actually says. This field is described as
        // "the open row that carries the work forward" and its own missing-field
        // hint says "name the open loop", but it used to be handed straight to
        // resolveLoop as loop_id — the uuid-only branch. Passing "#213", which is
        // exactly what the hint asks for, sent a non-uuid string into a where
        // clause on a uuid column: Postgres raised invalid input syntax and the
        // caller got a bare `internal error` naming nothing. Four loop closes
        // failed that way during the 2026-08-21 markdown-endgame consolidation
        // before the uuid was substituted by hand (defect 3eb1ad6d, rule
        // 3a9dbafd — never make a partner decode an id).
        const successorRef = String(args.successor_loop).trim();
        successor = UUID_RE.test(successorRef)
          ? await resolveLoop(c, { loop_id: successorRef })
          : await resolveLoop(c, { number: successorRef.replace(/^#/, "") });
        if (successor.id === cur.id || successor.status !== "open")
          throw new ToolError({ error: "successor_loop_not_open", successor_loop: args.successor_loop,
            hint: "the successor must be a different open loop" });
      }

      // A file with a Done table keeps its closed rows visible in the render; that
      // is the file's own convention, not a new one. open_loop has no Done table
      // in either of its two files, so a closed one simply leaves the list.
      //
      // FOUND BY block_key='done' UNTIL 2026-08-09, WHICH SILENTLY BROKE THE IDEA
      // BANK (loop #273). Ideas call their Done table "Retired", so the lookup
      // matched nothing, the row kept its `parked` block_id, and because `parked`
      // has renders_closed=false the join in v_export_loops dropped it from the
      // file entirely — neither Parked nor Retired. The outcome was never lost
      // (close_outcome is required) but it was UNRENDERED, which broke the bank's
      // founding rule 4, "move, don't delete — the reasoning stays visible so we
      // don't re-litigate it later", and blinded the monthly resurface gate that
      // reads the file to decide whether the round already ran.
      //
      // Match on renders_closed instead of on a hardcoded name. That column IS the
      // property being asked about — "the block that keeps closed rows visible" —
      // so the lookup can no longer be defeated by a file calling its Done table
      // something else, and a future kind gets the behaviour by setting one flag.
      // Exactly one block per kind carries it today (team_loop/done,
      // action_required/done, idea/retired; open_loop has none, so those rows keep
      // leaving the render as they always have). `order by seq` makes the pick
      // deterministic rather than dependent on row order if that ever stops being true.
      const done = await c.query(
        "select id, rel_path, block_key from loop_block " +
        " where kind=$1 and renders_closed order by seq limit 1", [cur.kind]);
      const sets = ["status=$1", "close_outcome=$2", "closed_by=$3", "closed_at=now()",
                    "outcome=$2", "closed_text=to_char(now(),'YYYY-MM-DD')", "updated_by=$3"];
      const vals = [resolution, outcome, actor.id];
      let movedTo = null, movedToBlock = null;
      if (done.rows.length) {
        vals.push(done.rows[0].id); sets.push(`block_id=$${vals.length}`);
        vals.push(await nextRenderSeq(c, done.rows[0].id)); sets.push(`render_seq=$${vals.length}`);
        movedTo = done.rows[0].rel_path;
        movedToBlock = done.rows[0].block_key;
      }
      vals.push(cur.id);
      await c.query(`update loop_item set ${sets.join(", ")} where id=$${vals.length}`, vals);

      await writeEvent(c, actor, "close-loop", "loop", cur.id,
        { field: "status", old: { status: "open" },
          new: { status: resolution, outcome,
                 ...(successor ? { successor_loop: { id: successor.id, number: successor.number } } : {}) }, human_quote: outcome,
          idempotency_key: args.idempotency_key });
      // Name the destination BLOCK, not just the file. `ok:true` proves the call
      // parsed, never that the row landed where a reader will find it (rule
      // c53beeaa) — and a null here is now the caller's signal that this kind
      // keeps no closed table, rather than something having gone wrong.
      return { ok: true, loop_id: cur.id, number: cur.number, status: resolution,
               ...(successor ? { successor_loop: { id: successor.id, number: successor.number } } : {}),
               moved_to_done_table_in: movedTo, closed_rows_render_in: movedToBlock };
    }),
  },

  "amend-closed-loop": {
    write: true,
    description: "Correct a CLOSED loop's outcome, append-only — never rewrite history. THE GAP THIS CLOSES: close-loop refuses with loop_not_open on anything already closed, by design (a closed loop is history; open a new one rather than editing the record of what happened) — but that rule has no answer for the outcome text itself being WRONG. Loop c7265238-effe-4166-bc9a-eccc5f389763 was closed with outcome \"x\" by mistake and nothing could fix it (defect a2c04ffa-92d0-4428-b175-32fa3cfb0802): the only paths were a raw table UPDATE, exactly what the record layer exists to prevent, or living with a nonsense outcome forever. This verb is the third path. It NEVER reopens the loop and never rewrites the prior outcome in place — it appends a loop_amendment row (prior outcome, new outcome, reason, server-derived actor) and only then updates loop_item's current projection to match, so the loop's current outcome reads the latest amendment while every prior one stays on the record. Refused on a still-OPEN loop (use update-loop to change it, or close-loop to close it) and on a stale base_version (version_conflict — re-read and re-decide, never auto-retried).",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      loop_id: { type: "string" },
      number: { type: "string", description: "alternative to loop_id; refuses when ambiguous. Unlike every other loop verb's `number`, this resolves CLOSED rows too, because that is the only kind this verb ever acts on." },
      kind: { type: "string", enum: LOOP_KINDS, description: "narrows an ambiguous number" },
      base_version: { type: "integer" },
      outcome: { type: "string", description: "REQUIRED: the CORRECTED outcome, in your words. Refused under ~10 characters — a placeholder correction ('x', 'n/a', 'fixed') is not a correction, and is exactly the failure mode this verb exists to repair." },
      reason: { type: "string", description: "REQUIRED: why the recorded outcome is being corrected. Never inferred, never defaulted — the reason is as much a part of the record as the new text." },
      resolution: { type: "string", enum: ["done", "dropped"], description: "Corrects the loop's resolution alongside its outcome. Omit to leave the resolution exactly as it was closed. Passing 'dropped' with a bookkeeping outcome (opens with RENUMBERED/SUPERSEDED, or names a successor) is held to the same successor rules close-loop enforces: successor_loop is required and must name a different, currently OPEN loop." },
      successor_loop: { type: "string", description: "Same field, same rule as close-loop's: the open row that now carries the work forward, required whenever the corrected outcome reads as a bookkeeping close. Pass the number as a human would say it ('#213' or '213') or a loop_id." } },
      required: ["idempotency_key", "outcome", "reason"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "amend-closed-loop", args, async () => {
      // Refusals are unconditional and first, exactly like close-loop's own
      // outcome_required check: a caller who cannot yet say the corrected text
      // and why should not be able to half-amend the record.
      const newOutcome = (args.outcome || "").trim();
      if (!newOutcome)
        throw new ToolError({ error: "outcome_required",
          hint: "say what actually came of it — the corrected text, not that it needs correcting" });
      // "x" is the literal outcome that caused defect a2c04ffa-92d0-4428-b175-32fa3cfb0802.
      // A length floor cannot judge PROSE, but it can catch a placeholder, and a
      // placeholder is exactly what got this verb built.
      if (newOutcome.length < 10)
        throw new ToolError({ error: "outcome_too_short", got: newOutcome.length, min: 10,
          hint: "a meaningful correction reads as one — say what actually came of it, at least 10 characters" });
      const reason = (args.reason || "").trim();
      if (!reason)
        throw new ToolError({ error: "reason_required",
          hint: "say why the recorded outcome is being corrected — required, never inferred" });

      // anyStatus:true is the whole reason this verb needed to extend the seam
      // rather than call resolveLoop as every other loop verb does: it is the
      // one verb whose entire purpose is acting on a row every other number
      // lookup would refuse to find.
      const cur = await resolveLoop(c, args, { anyStatus: true });
      await versionGuard(c, "loop_item", cur.id, args.base_version);
      if (cur.status === "open")
        throw new ToolError({ error: "loop_open", loop_id: cur.id, status: cur.status,
          hint: "amend-closed-loop only corrects a CLOSED loop's outcome — this one is still open. " +
                "Use update-loop to change it, or close-loop to close it." });

      const resolution = args.resolution !== undefined ? args.resolution : cur.status;

      // Identical bookkeeping-close detection to close-loop's own, applied to
      // the CORRECTED outcome: a correction that turns an outcome into a
      // renumber/supersede/merge/split declaration is making the same kind of
      // claim close-loop guards, and deserves the same guard. See close-loop's
      // own comment for the full history of why this is exact-prefix-or-named-
      // successor rather than an anywhere-in-prose match.
      const bookkeeping =
        /^\s*(renumbered|superseded)\b/i.test(newOutcome) ||
        /\b(?:superseded\s+by|renumbered\s+(?:to|as)|merged\s+(?:into|with)|split\s+into)\s+(?:open\s+)?(?:loop\s*)?#?\d+/i.test(newOutcome) ||
        Boolean(args.successor_loop);
      let successor = null;
      if (bookkeeping) {
        if (resolution !== "dropped")
          throw new ToolError({ error: "bookkeeping_close_is_dropped",
            hint: "continuing work is not done; correct the resolution to 'dropped' with the successor named" });
        if (!/^(renumbered|superseded)/i.test(newOutcome))
          throw new ToolError({ error: "bookkeeping_outcome_prefix",
            hint: "open a bookkeeping close with RENUMBERED or SUPERSEDED, not an abandonment claim" });
        if (!args.successor_loop)
          throw new ToolError({ error: "successor_loop_required",
            hint: "name the open loop that now carries this work; a bookkeeping close cannot read as abandonment" });
        const successorRef = String(args.successor_loop).trim();
        successor = UUID_RE.test(successorRef)
          ? await resolveLoop(c, { loop_id: successorRef })
          : await resolveLoop(c, { number: successorRef.replace(/^#/, "") });
        if (successor.id === cur.id || successor.status !== "open")
          throw new ToolError({ error: "successor_loop_not_open", successor_loop: args.successor_loop,
            hint: "the successor must be a different open loop" });
      }

      // The prior outcome, read from resolveLoop's row — BEFORE versionGuard's
      // `for update` lock, not from it. That read is still safe: it is the
      // version check right above, not the lock itself, that makes it so — a
      // concurrent amend that changed the row between this read and the lock
      // moves the version, versionGuard throws version_conflict on the stale
      // base_version, and this priorOutcome is never written. Not from the
      // args, not reconstructed either way — the actual current text this
      // amendment is correcting.
      const priorOutcome = cur.close_outcome || "";
      const priorResolution = cur.status;

      // APPEND FIRST. The amendment row is the history; the loop_item update
      // right after it is only the current projection catching up to what the
      // history now says. Reversing this order would let a crash between the
      // two leave a projection change with no corresponding record of why.
      await c.query(
        `insert into loop_amendment (loop_id, prior_outcome, new_outcome, prior_resolution,
           new_resolution, reason, actor_id, idempotency_key)
         values ($1,$2,$3,$4,$5,$6,$7,$8)`,
        [cur.id, priorOutcome, newOutcome, priorResolution, resolution, reason, actor.id,
         args.idempotency_key]);

      // The loop's current outcome reads the latest amendment: close_outcome
      // and outcome (the pair close-loop itself always keeps in sync) are set
      // to the corrected text, and the row's own version increments via
      // trg_touch_row exactly as any other loop_item update does. closed_at
      // and closed_by are deliberately UNTOUCHED — this is a correction to
      // what was said, not a re-closing of the loop, and the original closer
      // and closing time stay accurate.
      await c.query(
        `update loop_item set close_outcome=$1, outcome=$1, status=$2, updated_by=$3 where id=$4`,
        [newOutcome, resolution, actor.id, cur.id]);

      await writeEvent(c, actor, "amend-closed-loop", "loop", cur.id,
        { field: "close_outcome",
          old: { outcome: priorOutcome, resolution: priorResolution },
          new: { outcome: newOutcome, resolution, reason,
                 ...(successor ? { successor_loop: { id: successor.id, number: successor.number } } : {}) },
          idempotency_key: args.idempotency_key });

      return { ok: true, loop_id: cur.id, number: cur.number, status: resolution,
               prior_outcome: priorOutcome, outcome: newOutcome,
               ...(successor ? { successor_loop: { id: successor.id, number: successor.number } } : {}) };
    }),
  },

  // ═══════════════════════════════════════════════════════════════════════════
  // MARKETING (0066) — the four verbs that give the lane an intent and an answer
  //
  // WHAT WAS BROKEN, measured on 2026-08-02 and not inferred. `campaign` held 0
  // rows. `content_piece` held 89 and every single campaign_id was null. 259
  // placement_metric rows existed and could not answer whether anything worked,
  // because nothing in the database ever said what any of it was FOR. The only
  // writer of any of these tables was pipelines/pull_placement_metrics.py, a
  // scheduled ingest that creates pieces and placements from Blotato and sets
  // campaign_id to nothing. The marketing COO seat could SPECIFY a campaign in
  // prose and could not RECORD one.
  //
  // WHY FOUR VERBS AND NOT THREE — the close/score question, answered.
  // open-campaign and score-campaign are separate on this system's own
  // precedent: activate-rule and retire-rule are separate, and update-loop and
  // close-loop are separate, because a state transition that carries a JUDGMENT
  // needs arguments the opening act must never accept. Folding them together
  // would mean a verb whose required fields depend on a mode flag, and a mode
  // flag is how a campaign gets closed by accident. More concretely: scoring
  // requires a verdict, evidence, and a measurement-coverage check that
  // open-campaign has no business running, and it must REFUSE a "worked" verdict
  // formed over unmeasured placements — a refusal that only makes sense at the
  // closing end. The cost is one more verb; the benefit is that "we decided this
  // worked" can never be a side effect of editing a start date.
  //
  // WHY NO VERB CREATES A content_piece. Checked before deciding, which is the
  // only reason the answer is trustworthy: all 89 existing pieces were born in
  // pull_placement_metrics.py, keyed on placement.external_id, at publish time.
  // A second birth path would mint duplicates the moment the ingest next runs,
  // because the ingest matches on external_id and a hand-made piece has none.
  // So pieces arrive by publishing, and attach-to-campaign BINDS them. The real
  // consequence, stated rather than hidden: content that is PLANNED but not yet
  // published has no record-layer home at all, and that is a genuine gap for
  // Joe to rule on, not something to paper over by minting orphan rows here.
  //
  // NOTHING HERE IS OUTBOUND AND NOTHING HERE SPENDS. These four verbs write
  // records about content that already exists. No verb publishes, schedules,
  // boosts, funds or touches a platform credential — the one human gate is
  // unchanged.

  "open-campaign": {
    write: true,
    description: "Open a campaign: the object that says what a run of content is FOR, so its results can later be judged instead of admired. THIS IS THE MISSING MIDDLE OF THE WHOLE MARKETING LANE — as of 2026-08-02 the campaign table held 0 rows while 89 content pieces and 259 metrics existed, so nothing published in the system's entire recorded history had a stated objective. Requires the objective (goal), the WINDOW (starts_on, optionally ends_on), the CHANNELS, and a success_criterion written so it can be CHECKED: 'X drives three practice-owner replies by Sept 30' is a criterion; 'grow awareness on X' is a restated goal and this verb refuses it. The criterion is required at OPEN because a criterion invented after the numbers arrive is not a criterion, and score-campaign quotes this one back before it accepts any verdict. ONE CAMPAIGN PER NAME, enforced by a unique index — reopening the same name is refused with the existing campaign's id, because the way this system produced 415 organisation rows for 306 real organisations was writers that never looked first. Backdating over content that already published is legitimate and normal (all 89 existing pieces are historical), so a start far in the past asks for confirm rather than refusing. NOT a publishing verb: it schedules nothing, spends nothing, and posts nothing. NOT the place for a piece of content — attach-to-campaign binds those, and it can only bind pieces that already published.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      name: { type: "string", description: "short, human, no ids. Unique — one campaign per name." },
      goal: { type: "string", description: "the objective in one sentence: what this run of content is FOR" },
      success_criterion: { type: "string",
        description: "REQUIRED. What would have to be observably TRUE for this to have worked, stated so it can be checked against the record. Name the observable and, where you can, the number and the date." },
      starts_on: { type: "string", description: "YYYY-MM-DD. Required — a campaign is a window." },
      ends_on: { type: "string", description: "YYYY-MM-DD. Omit for an open-ended run; scoring works either way." },
      channels: { type: "array", items: { type: "string" },
        description: "platform slugs this runs on: facebook, instagram, linkedin, twitter. Validated against the registered platforms; never empty." },
      note: { type: "string", description: "anything a later reader needs in order to judge the verdict" },
      human_quote: { type: "string", description: "the partner's literal words when he set this campaign" },
      confirm: { type: "boolean", description: "acknowledge an implausible window (deep backdate, or over a year long)" } },
      required: ["idempotency_key","name","goal","success_criterion","starts_on","channels"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "open-campaign", args, async () => {
      await require0066(c);

      const name = String(args.name || "").trim();
      const goal = String(args.goal || "").trim();
      const crit = String(args.success_criterion || "").trim();
      if (!name) throw new ToolError({ error: "name_required" });
      if (!goal) throw new ToolError({ error: "goal_required",
        hint: "one sentence: what is this run of content FOR?" });
      if (!crit) throw new ToolError({ error: "success_criterion_required",
        hint: "what would have to be observably true for this to have worked?" });
      // A criterion that merely restates the goal is the shape that lets a
      // campaign be declared a success on vibes. The check is deliberately crude
      // — it catches the copy-paste, not the merely vague — because a verb
      // cannot judge prose and pretending otherwise would be worse.
      if (crit.toLowerCase() === goal.toLowerCase())
        throw new ToolError({ error: "criterion_restates_goal",
          hint: "the success criterion must be CHECKABLE, and different from the objective: " +
                "name the observable, and where you can the number and the date" });

      const existing = await c.query(
        "select id, status, starts_on, ends_on from campaign where lower(btrim(name))=lower($1)",
        [name]);
      if (existing.rows.length)
        throw new ToolError({ error: "campaign_exists", campaign: existing.rows[0],
          hint: "one campaign per name. Attach to the existing one, or pick a name that says " +
                "what makes this run different. Nothing was written." });

      const channels = Array.isArray(args.channels)
        ? [...new Set(args.channels.map(x => String(x || "").trim().toLowerCase()).filter(Boolean))]
        : [];
      if (!channels.length) throw new ToolError({ error: "channels_required",
        hint: "a campaign with no channel cannot be measured; name at least one platform" });
      const live = await livePlatformSlugs(c);
      const unknown = channels.filter(ch => !live.includes(ch));
      if (unknown.length) throw new ToolError({ error: "unknown_channel", unknown,
        known_platforms: live,
        hint: "register a platform in marketing_subject before running a campaign on it — a " +
              "channel nobody registered is a channel no view will ever roll up" });

      const start = String(args.starts_on || "").trim();
      const end = args.ends_on ? String(args.ends_on).trim() : null;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(start))
        throw new ToolError({ error: "bad_date", field: "starts_on", got: args.starts_on });
      if (end && !/^\d{4}-\d{2}-\d{2}$/.test(end))
        throw new ToolError({ error: "bad_date", field: "ends_on", got: args.ends_on });
      if (end && end < start) throw new ToolError({ error: "window_inverted", starts_on: start, ends_on: end,
        hint: "an end before its start would exclude every piece from every date filter" });

      // PLAUSIBILITY, NOT PROHIBITION. Backdating a campaign over content that
      // already published is exactly what this lane needs first — all 89 pieces
      // are historical — so a deep backdate ASKS rather than refuses.
      const today = new Date().toISOString().slice(0, 10);
      const days = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 86400000);
      if (!args.confirm) {
        if (days(start, today) > 90)
          throw new ToolError({ error: "needs_confirm",
            reason: `starts_on is ${days(start, today)} days in the past`,
            hint: "backdating over already-published content is legitimate — resubmit with " +
                  "confirm:true if that is what you mean, and say so in note" });
        if (end && days(start, end) > 365)
          throw new ToolError({ error: "needs_confirm",
            reason: `the window is ${days(start, end)} days long`,
            hint: "a campaign longer than a year is usually a pillar wearing a campaign's " +
                  "clothes, and it will never be scorable. Resubmit with confirm:true if intended" });
      }

      const r = await c.query(
        `insert into campaign (name, goal, success_criterion, starts_on, ends_on, channels,
                               status, created_by, updated_by)
         values ($1,$2,$3,$4::date,$5::date,$6,'active',$7,$7)
         returning id, version, starts_on, ends_on`,
        [name, goal, crit, start, end, channels, actor.id]);
      const row = r.rows[0];

      // WHAT EVIDENCE THIS CAMPAIGN CAN EVEN HOPE FOR, returned at open time
      // rather than discovered at scoring time. On these channels, in this
      // window: how many placements exist and how many of them are measured. If
      // the answer is "42 placements, 0 measured", the caller learns NOW that
      // this campaign is unscorable, instead of six weeks from now.
      const ev = await c.query(
        `select count(*)::int as placements,
                count(*) filter (where measured)::int as measured,
                count(*) filter (where campaign_id is null)::int as unattached
           from v_placement_measurement
          where platform = any($1)
            and (live_at is null or live_at::date >= $2::date)
            and ($3::date is null or live_at is null or live_at::date <= $3::date)`,
        [channels, start, end]);

      await writeEvent(c, actor, "open-campaign", "campaign", row.id,
        { new: { name, goal, success_criterion: crit, starts_on: start, ends_on: end,
                 channels, status: "active" },
          human_quote: args.human_quote || null,
          agent_rationale: args.note || null,
          idempotency_key: args.idempotency_key });

      return { ok: true, campaign_id: row.id, name, version: row.version,
               starts_on: row.starts_on, ends_on: row.ends_on, channels, status: "active",
               success_criterion: crit,
               evidence_available_in_window: {
                 placements: ev.rows[0].placements,
                 measured: ev.rows[0].measured,
                 unmeasured: ev.rows[0].placements - ev.rows[0].measured,
                 unattached_to_any_campaign: ev.rows[0].unattached },
               note: ev.rows[0].measured === 0
                 ? "NOTHING in this window on these channels is measured today. This campaign " +
                   "is not scorable until measure-placement or the Blotato pull lands metrics — " +
                   "say that to the human rather than letting the campaign imply evidence it has not got."
                 : null };
    }),
  },

  "score-campaign": {
    write: true,
    description: "Close a campaign with a VERDICT against the criterion it was opened with — the act that turns a pile of metrics into an answer. Separate from open-campaign on purpose (the same reason retire-rule is separate from activate-rule): a verdict is a judgment, it needs arguments opening must never accept, and it must be impossible to reach by accident while editing a date. THE REFUSAL THAT MATTERS: it will not accept 'worked' or 'did_not_work' over ZERO measured placements, and it asks for confirm below the coverage floor in system_config (marketing.scoring_min_coverage_pct). 73 of 89 placements in this system have never been measured, including all 42 on X, so a verdict formed over them would be a guess wearing a number. 'inconclusive' is always available and is the HONEST answer when the measurement never happened — use it rather than reaching for confirm. Snapshots the coverage into coverage_at_scoring so nobody can later re-read a thin verdict as a thick one. Requires base_version from a fresh read. Refuses a campaign that is already scored: changing a recorded verdict is a new fact, not an edit, and Joe rules on it.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      campaign: { type: "string", description: "campaign name (exact) or uuid" },
      base_version: { type: "integer", description: "from a fresh read; a conflict is a question for the human, never a retry" },
      verdict: { type: "string", enum: ["worked","did_not_work","inconclusive"],
        description: "measured against the success_criterion this campaign was OPENED with — the verb quotes it back to you in the response" },
      evidence: { type: "string",
        description: "REQUIRED. What in the record supports this verdict, in one or two lines. Name the numbers you read." },
      close: { type: "boolean", default: true,
        description: "false scores it but leaves status active — for a mid-flight read-out. The verdict is still recorded and still requires evidence." },
      human_quote: { type: "string", description: "the partner's literal words when he called it" },
      confirm: { type: "boolean", description: "acknowledge scoring below the coverage floor" } },
      required: ["idempotency_key","campaign","base_version","verdict","evidence"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "score-campaign", args, async () => {
      await require0066(c);

      const evidence = String(args.evidence || "").trim();
      if (!evidence) throw new ToolError({ error: "evidence_required",
        hint: "a verdict with no evidence is an opinion the record will later quote as a fact" });

      const cam = await resolveCampaign(c, args.campaign);
      await versionGuard(c, "campaign", cam.id, args.base_version);
      if (cam.scored_at) throw new ToolError({ error: "already_scored",
        campaign_id: cam.id, verdict: cam.outcome_verdict, scored_at: cam.scored_at,
        outcome_note: cam.outcome_note,
        hint: "this campaign already carries a verdict. Changing it is a new fact, not an " +
              "edit — surface the existing verdict to the human and let him rule. Nothing was written." });

      const sc = (await c.query(
        `select placements, pieces, measured_placements, unmeasured_placements,
                coverage_pct, views_total, interactions_total
           from v_campaign_scorecard where campaign_id=$1`, [cam.id])).rows[0]
        || { placements: 0, pieces: 0, measured_placements: 0, unmeasured_placements: 0,
             coverage_pct: null, views_total: null, interactions_total: null };

      const measured = Number(sc.measured_placements || 0);
      const coverage = sc.coverage_pct === null ? null : Number(sc.coverage_pct);

      // THE HARD FLOOR, and confirm cannot cross it. A campaign over which
      // nothing at all was measured has no evidence of any kind, so 'worked' and
      // 'did_not_work' are both unsupportable — not merely thin. 'inconclusive'
      // is the true answer and is always allowed, which is why this refuses
      // instead of asking: an unmeasured campaign is exactly the case where a
      // confirm prompt would be clicked through.
      if (measured === 0 && args.verdict !== "inconclusive")
        throw new ToolError({ error: "no_measured_evidence",
          campaign_id: cam.id, placements: Number(sc.placements || 0),
          measured_placements: 0, unmeasured_placements: Number(sc.unmeasured_placements || 0),
          hint: "not one placement on this campaign carries a metric, so '" + args.verdict +
                "' cannot be supported by anything. Record 'inconclusive' with the reason, or " +
                "measure the placements first. An unmeasured placement is NOT a zero result." });

      // THE SOFT FLOOR: thin but real evidence asks rather than refuses.
      const floor = Number(await config(c, "marketing.scoring_min_coverage_pct", 50));
      if (!args.confirm && coverage !== null && coverage < floor && args.verdict !== "inconclusive")
        throw new ToolError({ error: "needs_confirm",
          reason: `only ${coverage}% of this campaign's placements are measured ` +
                  `(${measured} of ${sc.placements}); the floor is ${floor}%`,
          measured_placements: measured, unmeasured_placements: Number(sc.unmeasured_placements || 0),
          hint: "the unmeasured placements are not zeros, they are unknowns. Either measure " +
                "more, record 'inconclusive', or resubmit with confirm:true and say in evidence " +
                "why the measured subset is representative." });

      const snapshot = { placements: Number(sc.placements || 0), pieces: Number(sc.pieces || 0),
                         measured_placements: measured,
                         unmeasured_placements: Number(sc.unmeasured_placements || 0),
                         coverage_pct: coverage,
                         views_total: sc.views_total === null ? null : Number(sc.views_total),
                         interactions_total: sc.interactions_total === null ? null : Number(sc.interactions_total),
                         floor_pct: floor, confirmed_below_floor: !!args.confirm,
                         snapshot_at: new Date().toISOString() };

      const close = args.close !== false;
      await c.query(
        `update campaign set outcome_verdict=$1, outcome_note=$2, coverage_at_scoring=$3,
                             scored_at=now(), scored_by=$4, updated_by=$4,
                             status = case when $5 then 'closed' else status end
          where id=$6`,
        [args.verdict, evidence, JSON.stringify(snapshot), actor.id, close, cam.id]);

      await writeEvent(c, actor, "score-campaign", "campaign", cam.id, {
        field: "outcome_verdict",
        old: { status: cam.status, outcome_verdict: null },
        new: { status: close ? "closed" : cam.status, outcome_verdict: args.verdict,
               coverage: snapshot },
        agent_rationale: evidence,
        human_quote: args.human_quote || null,
        idempotency_key: args.idempotency_key });

      return { ok: true, campaign_id: cam.id, name: cam.name,
               verdict: args.verdict, status: close ? "closed" : cam.status,
               scored_against_criterion: cam.success_criterion,
               coverage: snapshot,
               note: coverage !== null && coverage < 100
                 ? `${snapshot.unmeasured_placements} of ${snapshot.placements} placements on this ` +
                   "campaign were never measured. Say that beside the verdict — the totals above " +
                   "cover the measured subset only and are not the campaign's whole result."
                 : null };
    }),
  },

  "attach-to-campaign": {
    write: true,
    description: "Bind published content to the campaign it belongs to — the link that makes 259 metrics answerable. Takes content by the handles a caller actually holds: the live post URL, the Blotato post id, or a placement/piece uuid. IT DOES NOT CREATE CONTENT, and that is a decision from evidence rather than a limitation: all 89 existing pieces were created by pipelines/pull_placement_metrics.py at publish time, keyed on placement.external_id, so a second birth path here would mint a duplicate the moment the ingest next ran. Content that is PLANNED but unpublished therefore has no record-layer home yet — say so plainly rather than inventing a row. ATOMIC: if any item cannot be resolved the WHOLE call refuses and nothing is written, because a partial attach that silently skips two items is a campaign that quietly under-reports its own content. A piece already attached to a DIFFERENT campaign is refused; moving one is `reattach` with base_version and a reason, one piece at a time, because re-pointing content rewrites what a past verdict was based on. Attaching content that published outside the campaign's window asks for confirm. The response always reports how many of the attached placements are actually MEASURED — usually the answer is few, and the caller needs to know that before quoting any total.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      campaign: { type: "string", description: "campaign name (exact) or uuid" },
      items: { type: "array", items: { type: "string" },
        description: "post URLs, Blotato post ids, placement uuids or content_piece uuids. Max 100." },
      reattach: { type: "boolean",
        description: "move a piece off another campaign. Requires exactly ONE item, a reason, and piece_base_version." },
      piece_base_version: { type: "integer", description: "content_piece.version, from a fresh read. reattach only." },
      reason: { type: "string", description: "REQUIRED for reattach: why this content belongs to a different campaign than the one it was filed under" },
      confirm: { type: "boolean", description: "acknowledge attaching content published outside the campaign window" } },
      required: ["idempotency_key","campaign","items"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "attach-to-campaign", args, async () => {
      await require0066(c);

      const items = Array.isArray(args.items) ? args.items.filter(x => String(x || "").trim()) : [];
      if (!items.length) throw new ToolError({ error: "items_required" });
      if (items.length > 100) throw new ToolError({ error: "too_many_items", count: items.length,
        hint: "max 100 per call — split the batch" });

      const cam = await resolveCampaign(c, args.campaign);
      if (cam.scored_at && !args.confirm)
        throw new ToolError({ error: "needs_confirm",
          reason: "this campaign is already scored; adding content changes what the recorded verdict covers",
          verdict: cam.outcome_verdict, scored_at: cam.scored_at,
          hint: "resubmit with confirm:true only if the verdict is still honest with this " +
                "content in it, and expect to re-state the coverage" });

      // RESOLVE EVERYTHING FIRST, WRITE NOTHING UNTIL IT ALL RESOLVES. A partial
      // attach is the false-completeness failure in this domain: the campaign
      // would look complete while quietly missing whatever did not resolve.
      const resolved = [];
      const failed = [];
      for (const raw of items) {
        const ref = String(raw).trim();
        try {
          let piece = null, placement = null;
          if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(ref)) {
            const p = await c.query("select id, campaign_id, version, status from content_piece where id=$1", [ref]);
            if (p.rows.length) piece = p.rows[0];
          }
          if (!piece) {
            placement = await resolvePlacement(c, ref);
            const p = await c.query(
              "select id, campaign_id, version, status from content_piece where id=$1", [placement.piece_id]);
            piece = p.rows[0];
          }
          resolved.push({ ref, piece, placement });
        } catch (e) {
          failed.push({ ref, error: e.payload ? e.payload.error : "unresolved",
                        detail: e.payload ? e.payload.hint : String(e) });
        }
      }
      if (failed.length)
        throw new ToolError({ error: "unresolved_items", unresolved: failed,
          resolved_count: resolved.length,
          hint: "NOTHING was written. Every item must resolve, because a campaign that " +
                "silently dropped two of its twelve posts under-reports its own content and " +
                "nothing downstream can tell." });

      // Reattach is a different act with a different blast radius, so it has
      // different rules: one piece, a version guard, and a stated reason. The
      // plain attach path needs no version guard because it only ever writes
      // null -> value and the update is conditional on the null, so it cannot
      // clobber a concurrent writer — it loses the race visibly instead.
      const reattach = args.reattach === true;
      if (reattach) {
        if (resolved.length !== 1) throw new ToolError({ error: "reattach_is_one_at_a_time",
          count: resolved.length,
          hint: "moving content between campaigns rewrites what a past verdict was based on; " +
                "do it deliberately, one piece at a time" });
        if (!String(args.reason || "").trim()) throw new ToolError({ error: "reason_required",
          hint: "say why this content belongs to a different campaign than the one it was filed under" });
        await versionGuard(c, "content_piece", resolved[0].piece.id, args.piece_base_version);
      }

      // Window plausibility, across the batch, once.
      if (!args.confirm) {
        const ids = resolved.map(r => r.piece.id);
        const out = await c.query(
          `select count(*)::int as n from placement p
            where p.piece_id = any($1) and p.live_at is not null
              and (p.live_at::date < $2::date
                   or ($3::date is not null and p.live_at::date > $3::date))`,
          [ids, cam.starts_on, cam.ends_on]);
        if (out.rows[0].n > 0)
          throw new ToolError({ error: "needs_confirm",
            reason: `${out.rows[0].n} of these placements published outside the campaign window ` +
                    `(${cam.starts_on} to ${cam.ends_on || "open"})`,
            hint: "either the window is wrong or this content is not part of this campaign. " +
                  "Resubmit with confirm:true if you mean it." });
      }

      const attached = [], already = [], conflicts = [];
      for (const r of resolved) {
        if (r.piece.campaign_id === cam.id) { already.push(r.ref); continue; }
        if (r.piece.campaign_id && !reattach) {
          const other = await c.query("select name from campaign where id=$1", [r.piece.campaign_id]);
          conflicts.push({ ref: r.ref, piece_id: r.piece.id,
                           currently_on: other.rows[0] ? other.rows[0].name : r.piece.campaign_id });
          continue;
        }
        const upd = reattach
          ? await c.query(
              "update content_piece set campaign_id=$1, updated_by=$2 where id=$3 returning id",
              [cam.id, actor.id, r.piece.id])
          : await c.query(
              "update content_piece set campaign_id=$1, updated_by=$2 where id=$3 and campaign_id is null returning id",
              [cam.id, actor.id, r.piece.id]);
        if (!upd.rows.length) { // lost a race with a concurrent attach
          const now = await c.query("select campaign_id from content_piece where id=$1", [r.piece.id]);
          conflicts.push({ ref: r.ref, piece_id: r.piece.id,
                           currently_on: now.rows[0] ? now.rows[0].campaign_id : null,
                           note: "another writer attached this piece first — nothing was overwritten" });
          continue;
        }
        attached.push({ ref: r.ref, piece_id: r.piece.id,
                        moved_from: reattach ? r.piece.campaign_id : null });
        await writeEvent(c, actor, reattach ? "attach-to-campaign:reattach" : "attach-to-campaign",
          "content_piece", r.piece.id,
          { field: "campaign_id",
            old: { campaign_id: r.piece.campaign_id },
            new: { campaign_id: cam.id, campaign: cam.name },
            agent_rationale: args.reason || null,
            idempotency_key: args.idempotency_key });
      }

      if (conflicts.length)
        throw new ToolError({ error: "already_on_another_campaign", conflicts,
          hint: "a piece belongs to one campaign. Use reattach (one item, a reason and " +
                "piece_base_version) if it really moved. This call is rolled back whole." });

      // THE COVERAGE LINE. Attaching content does not measure it, and a caller
      // who reads only "12 attached" will quote totals that cover almost none of
      // them. As of 2026-08-02 that is 73 placements out of 89.
      const cov = (await c.query(
        `select count(*)::int as placements,
                count(*) filter (where measured)::int as measured
           from v_placement_measurement where campaign_id=$1`, [cam.id])).rows[0];

      return { ok: true, campaign_id: cam.id, campaign: cam.name,
               attached: attached.length, attached_items: attached,
               already_attached: already,
               campaign_now_covers: {
                 placements: cov.placements, measured: cov.measured,
                 unmeasured: cov.placements - cov.measured },
               note: cov.measured < cov.placements
                 ? `${cov.placements - cov.measured} of this campaign's ${cov.placements} ` +
                   "placements carry NO metrics. They are unmeasured, not zero — do not " +
                   "average or total over them as if they scored nothing."
                 : null };
    }),
  },

  "measure-placement": {
    write: true,
    description: "Record what one placement actually did — including, and especially, that it could not be measured. THE RULE THIS VERB EXISTS FOR: an unmeasured placement must stay VISIBLY unmeasured and must never read as a zero. 73 of 89 placements have never been measured, among them all 42 on X, and a reader who totals metrics by platform is currently handed 0 for X, which is a lie about performance rather than a fact about it. So `unavailable:true` with a reason is a first-class outcome here, exactly the way record-finding's found:false is: it lands a real placement_measurement row saying we looked and there was nothing, which is a different fact from nobody having looked. USE THIS FOR MEASUREMENTS THE SCHEDULED PULL CANNOT SEE — a figure read off a platform's own dashboard, an off-platform outcome (a DM, a reply, a consult that traced back to a post), or a confirmed 'this platform returns no analytics for us'. It REFUSES source 'blotato_api': that provenance belongs to pipelines/pull_placement_metrics.py, and a hand-written row claiming it would make the pull's output untrustworthy — use 'blotato_ui_manual', 'platform_native', 'joe_observed' or similar. A genuine measured zero IS allowed and is common (173 of 259 existing metric values are 0), but a payload where EVERY value is zero asks for confirm, because that is the shape an empty API response takes and it is precisely how an unmeasured post becomes a measured zero. Metrics are snapshots keyed (placement, kind, observed_at), so re-recording the same instant is a no-op rather than a duplicate.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      placement: { type: "string", description: "the live post URL, the Blotato post id, or the placement uuid" },
      source: { type: "string",
        description: "REQUIRED. Where the number came from: 'blotato_ui_manual', 'platform_native', 'joe_observed', a URL. 'blotato_api' is REFUSED — that source string belongs to the scheduled pull." },
      metrics: { type: "object",
        description: "{kind: number}. Kinds are stored verbatim as the source names them, snake_cased — views_count, reach_count, likes_count, comments_count, shares_count, saves_count, follows_count, interactions_sum, profile_visits_count, profile_activity_count. Do NOT map a platform's word onto a different platform's word; an equivalence nobody ruled is a wrong number later." },
      unavailable: { type: "boolean",
        description: "true records that measurement was ATTEMPTED and returned nothing. Requires reason. This is the honest alternative to silence, and to a zero." },
      reason: { type: "string", description: "REQUIRED with unavailable: why there is no number — 'the platform exposes no analytics for this account', 'post deleted', 'API returns 404'." },
      observed_at: { type: "string", description: "when the number was read (ISO); defaults to now. The snapshot key — pass the real read time, not the time you typed it." },
      note: { type: "string", description: "anything a later reader needs" },
      confirm: { type: "boolean", description: "acknowledge an all-zero payload or an out-of-band value" } },
      required: ["idempotency_key","placement","source"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "measure-placement", args, async () => {
      await require0066(c);

      const source = String(args.source || "").trim();
      if (!source) throw new ToolError({ error: "source_required",
        hint: "a number with no provenance is a rumour; say where you read it" });
      if (source.toLowerCase() === "blotato_api")
        throw new ToolError({ error: "reserved_source", source,
          hint: "'blotato_api' is the scheduled pull's provenance " +
                "(pipelines/pull_placement_metrics.py). A hand-written row wearing it would " +
                "make every API row unverifiable. Use 'blotato_ui_manual' for a figure read " +
                "off Blotato's own screen, 'platform_native' for the platform's dashboard, or " +
                "'joe_observed' for something Joe saw happen." });

      const unavailable = args.unavailable === true;
      const metrics = (args.metrics && typeof args.metrics === "object") ? args.metrics : null;
      const kinds = metrics ? Object.keys(metrics).filter(k => metrics[k] !== undefined && metrics[k] !== null) : [];

      // THE TWO REFUSALS THAT KEEP SILENCE OUT OF THE RECORD.
      if (unavailable && kinds.length)
        throw new ToolError({ error: "ambiguous_measurement",
          hint: "unavailable:true says there was nothing to record. Send the metrics, or send " +
                "the unavailability — never both in one act." });
      if (!unavailable && !kinds.length)
        throw new ToolError({ error: "nothing_to_record",
          hint: "pass metrics{}, or pass unavailable:true with a reason. An empty call would " +
                "leave this placement looking exactly like the 73 nobody has ever measured, " +
                "which is the one outcome this verb exists to prevent." });
      if (unavailable && !String(args.reason || "").trim())
        throw new ToolError({ error: "reason_required",
          hint: "'no data' with no reason cannot be acted on. Say whether the platform gives " +
                "us nothing, the post is gone, or the pull has simply never run — those lead " +
                "to three different next moves." });

      const pl = await resolvePlacement(c, args.placement);
      const observedAt = args.observed_at || null;

      if (unavailable) {
        const att = await c.query(
          `insert into placement_measurement (placement_id, attempted_at, source, outcome, reason,
                                              metric_kinds, note, recorded_by)
           values ($1, coalesce($2::timestamptz, now()), $3, 'unavailable', $4, '{}', $5, $6)
           on conflict (placement_id, source, attempted_at) do nothing
           returning id, attempted_at`,
          [pl.id, observedAt, source, String(args.reason).trim(), args.note || null, actor.id]);
        await writeEvent(c, actor, "measure-placement", "placement", pl.id, {
          occurred_at: observedAt,
          field: "measurement",
          new: { outcome: "unavailable", source, reason: String(args.reason).trim() },
          agent_rationale: "attempted and returned nothing — recorded so it is not mistaken for zero",
          idempotency_key: args.idempotency_key });
        return { ok: true, placement_id: pl.id, platform: pl.platform,
                 outcome: "unavailable", source, reason: String(args.reason).trim(),
                 recorded: !!att.rows.length,
                 measured: false,
                 note: "This placement is now recorded as ATTEMPTED AND UNMEASURED. It still " +
                       "reports measured:false in v_placement_measurement and it still has no " +
                       "number — that is the point. Do not read it as a zero." };
      }

      // ── values: validated one at a time, and the refusals are specific ──────
      const band = await config(c, "marketing.metric_value_band", { max: 1000000 });
      const clean = {};
      let allZero = true;
      for (const k of kinds) {
        const key = String(k).trim();
        if (!/^[a-z][a-z0-9_]*$/.test(key))
          throw new ToolError({ error: "bad_metric_kind", kind: k,
            hint: "kinds are the source's own names, snake_cased: views_count, reach_count, " +
                  "interactions_sum. Never invent an equivalence between two platforms' words." });
        const v = Number(metrics[k]);
        if (!Number.isFinite(v))
          throw new ToolError({ error: "bad_metric_value", kind: key, got: metrics[k],
            hint: "values are numbers. A missing number is not 0 — omit the kind entirely, or " +
                  "record the whole placement as unavailable." });
        if (v < 0)
          throw new ToolError({ error: "negative_metric", kind: key, got: v,
            hint: "no engagement count is negative; this is a sign error or a delta pasted as a total" });
        if (!args.confirm && Number(band.max) && v > Number(band.max))
          throw new ToolError({ error: "needs_confirm",
            reason: `${key} = ${v} exceeds the plausibility band (${band.max})`,
            hint: "the largest real value in placement_metric on 2026-08-02 was 845,877 " +
                  "(view_time_ms_sum). Check for a units error, then resubmit with confirm:true if real." });
        if (v !== 0) allZero = false;
        clean[key] = v;
      }

      // THE ALL-ZERO GATE, and the number behind it. Real zeros are ordinary: 173
      // of 259 existing metric values are 0. But across 26 real analytics
      // snapshots, ZERO of them were all-zero — an entirely zero payload is not
      // what real data looks like, it is what an empty API response looks like.
      // Writing one turns an unmeasured placement into a measured zero, which is
      // exactly the false completeness this whole verb guards against.
      if (allZero && !args.confirm)
        throw new ToolError({ error: "needs_confirm",
          reason: `every one of the ${Object.keys(clean).length} values is 0`,
          hint: "0 of 26 real snapshots in this system were all-zero, so this is far more " +
                "likely an empty response than a measured nothing. If the platform genuinely " +
                "returned no data, use unavailable:true with a reason — that keeps the " +
                "placement UNMEASURED instead of recording it as a zero result. Resubmit with " +
                "confirm:true only if the post truly earned zero of everything." });

      let landed = 0, unchanged = 0;
      for (const [kind, value] of Object.entries(clean)) {
        const r = await c.query(
          `insert into placement_metric (placement_id, observed_at, kind, value, source)
           values ($1, coalesce($2::timestamptz, now()), $3, $4, $5)
           on conflict (placement_id, kind, observed_at) do nothing returning kind`,
          [pl.id, observedAt, kind, value, source]);
        if (r.rows.length) landed++; else unchanged++;
      }

      await c.query(
        `insert into placement_measurement (placement_id, attempted_at, source, outcome, reason,
                                            metric_kinds, note, recorded_by)
         values ($1, coalesce($2::timestamptz, now()), $3, 'recorded', null, $4, $5, $6)
         on conflict (placement_id, source, attempted_at) do nothing`,
        [pl.id, observedAt, source, Object.keys(clean), args.note || null, actor.id]);

      // The same status catch-up the scheduled pull performs, for the same
      // reason: a piece whose placements gained metrics is 'measured'. Guarded on
      // the current status so it can never walk a retired or rejected piece back
      // into the live funnel.
      const promoted = await c.query(
        `update content_piece set status='measured', updated_by=$1
          where id=$2 and status in ('live','scheduled') returning id`,
        [actor.id, pl.piece_id]);

      await writeEvent(c, actor, "measure-placement", "placement", pl.id, {
        occurred_at: observedAt,
        field: "metrics",
        new: { outcome: "recorded", source, kinds: Object.keys(clean), values: clean },
        agent_rationale: args.note || null,
        idempotency_key: args.idempotency_key });

      return { ok: true, placement_id: pl.id, platform: pl.platform, piece_id: pl.piece_id,
               outcome: "recorded", source, measured: true,
               metrics_written: landed, metrics_already_present: unchanged,
               piece_marked_measured: !!promoted.rows.length,
               // Present ONLY when the handle did not match a stored key. The
               // caller is recording a number against a row identified by a
               // derived publish time, and that is worth seeing.
               ...(pl._resolved_by ? { resolved_by: pl._resolved_by,
                                       resolved_note: pl._resolved_note,
                                       resolved_url: pl.url } : {}),
               note: unchanged && !landed
                 ? "every kind already had a row at this exact observed_at — nothing changed. " +
                   "Pass the real read time if this was a new pull."
                 : null };
    }),
  },

  // ===== the Gate Zero read-only outcome (DoctorCRE v5 slice V5-A02, Step B) =====
  //
  // ONE VERB, AND IT CARRIES THE FIRST NON-HUMAN, NON-SPONSORED AUTHORITY CLASS
  // IN THIS REGISTRY. Every write verb before it either gated on a verified
  // human partner (`humanOnly: true`) or admitted any sponsored agent. This one
  // is neither: it refuses every actor except the single review-token seat that
  // holds oracle:gate-producer:gate-zero-read-only, under Joe's 2026-09-13
  // ruling d4e5f6a7-b8c9-4d0e-9f1a-2b3c4d5e6f70. The gate is enforced in
  // executeRegisteredTool through the `oracleSeatOnly` flag AND again inside the
  // handler through deriveGateZeroProducerSeat AND a third time in SQL by
  // ops.gate_zero_producer_actor_id() -- three independent derivations of one
  // fact, because a flag that nothing enforces is a label, which is exactly the
  // defect WR-000021 found on humanOnly.
  //
  // WHY IT IS DEFINED HERE rather than in a store module of its own: the
  // registry's source locator is part of the runtime mutation contract, and the
  // contract for this verb should read from the same file every other inline
  // verb's does. The receipt contract and the seat derivation live in
  // gate-zero-outcome-store.v5.js, which registers no verb of its own.
  "record-gate-zero-read-only-outcome": {
    write: true, humanOnly: false, oracleSeatOnly: true,
    description: "ORACLE-SEAT-ONLY, AND NOT A HUMAN ACT: record the DoctorCRE v5 Gate Zero read-only outcome as one consumer-gate-receipt.v1, its recomputed digest, the instant it was observed and the seat that produced it. It refuses every actor except the one review-token seat holding oracle:gate-producer:gate-zero-read-only — a partner is refused, a sponsored agent is refused, and a review-token seat on a DIFFERENT lane is refused by name rather than admitted by authority class. That shape exists because r7 registers this producer's role as independent_control_plane_oracle and Joe ruled on 2026-09-13 (decision d4e5f6a7-b8c9-4d0e-9f1a-2b3c4d5e6f70) that the seat records the row on its own authority with no partner countersign. THE HUMAN ACT IN THIS CHAIN IS UNCHANGED AND IS DOWNSTREAM: accept-benchmark-manifest-draft is still humanOnly and still derives its acceptor from a live verified partner. THE RECEIPT IS PRODUCED IN THIS CALL, NOT SUPPLIED: the verb takes ONE argument, idempotency_key, and invokes the bound producer seam — Step A's zero-argument Gate Zero producer — which derives its own run binding, aims the three ruled evidence readers at it and assembles one consumer-gate-receipt.v1 signed with the identity the server derived for this call. There is no receipt argument and no receipt-shaped argument: any other top-level field is refused as unregistered_operation_fields before the handler runs. NOTHING HERE IS A CALLER'S WORD FOR ANYTHING — the producing seat comes from the frozen registration, the actor from the authenticated bearer match, the three receipt identities from the authenticated call, and the outcome digest is RECOMPUTED by the record layer from the stored receipt, so no caller-supplied digest is accepted and none is sent. What the producer emits is still checked against the closed twenty-one-field r7 schema, against the twelve constants the producer registry fixes, against the closed three-field authenticated-receipt-identity.v1 shape, and against the identity rule that the producer and evaluator are this call while the subject maker is not. A producer refusal records nothing and is reported as gate_zero_outcome_not_produced with the reason the producer gave. RETRYABLE, IMMUTABLE FIRST OUTCOME: any later call for the same candidate returns the row already recorded rather than writing a second one. If the offered receipt differs because its session, instant, evidence or verdict moved, it converges onto that immutable row so the caller can heal a missing audit event; the result explicitly separates recorded and offered digests, statuses and producer reasons. candidate_scoped_digest is informational only; candidate_digest is the row arbiter. It grants no dispatch, activation or execution authority, accepts no benchmark and starts no clock.",
    // ONE ARGUMENT, AND IT NAMES AN INTENDED ACT RATHER THAN A SUBJECT. The
    // caller says "record the outcome, once, under this key"; WHAT gets recorded
    // is produced here. A `receipt` property was in the first draft of this verb
    // and is deliberately gone: the closed top-level check in mutation-registry.js
    // refuses any key that is not in this list as `unregistered_operation_fields`,
    // so `receipt`, `outcome`, `consumer_gate_receipt` and every other
    // receipt-shaped spelling is refused BEFORE the handler runs.
    inputSchema: {
      type: "object", additionalProperties: false,
      properties: {
        idempotency_key: { type: "string" },
      },
      required: ["idempotency_key"],
    },
    handler: async (c, actor, args) => withEnvelope(c, actor, "record-gate-zero-read-only-outcome", args, async () => {
      // ORDER IS DELIBERATE, and it is the same order the record layer uses.
      //   1. The seat is derived from the LIVE actor and the frozen
      //      registration. It runs FIRST, so an actor who may not write here
      //      never reaches a contract check that could tell them about the
      //      receipt shape.
      //   2. The receipt is checked against the derived seat.
      //   3. Only then does anything reach the database, where all three
      //      derivations happen again as the function owner.
      const seat = deriveGateZeroProducerSeat(actor);

      // THE RECEIPT IS PRODUCED, NOT RECEIVED (2026-09-13, PR 1014 correction).
      // `emitGateZeroOutcome` is the gate's bound producer seam: it calls Step
      // A's zero-argument producer, which derives its own run binding, aims the
      // three ruled readers at it, applies the three clauses over what came back
      // and assembles one consumer-gate-receipt.v1 signed with the identity
      // identity.js derived for THIS call. Nothing is handed in and nothing can
      // be: the producer's arity is zero and this verb has no receipt argument.
      //
      // A REFUSAL IS NOT A ROW. The producer refuses when it cannot authenticate,
      // when a row its binding stands on is absent, when a ruled seam returned no
      // row for the address it derived, or when its own falsifiers did not fire.
      // Each of those is a run that established nothing, so nothing is written
      // and the reason is reported by name. A clause that was READ and did not
      // hold is the opposite case: that is a real outcome with `status: "fail"`,
      // and it is recorded.
      const emitted = await emitGateZeroOutcome();
      if (emitted?.decision !== "report" || emitted?.status !== "outcome_produced"
          || !emitted?.receipt) {
        throw new ToolError({ error: "gate_zero_outcome_not_produced",
          reason_id: emitted?.reason_id ?? null,
          unavailable_because: emitted?.unavailable_because ?? null,
          decided_by: emitted?.decided_by ?? null,
          owed_seams: emitted?.owed_seams ?? null,
          producer_bound: emitted?.producer_bound ?? null,
          hint: "the Gate Zero producer did not emit an outcome in this call, so there is nothing to record. " +
                "This is a refusal by the producer over the rows it derived, not a caller error: no receipt " +
                "argument exists and none would have changed it." });
      }
      // CHECKED ANYWAY, against the derived seat and this call's own identity.
      // The producer already assembled the twenty-one fields, so this cannot be
      // a caller's malformed object -- which is exactly why it is checked: the
      // contract is the record layer's, not the producer's, and a producer that
      // drifted from r7's shape must be refused at the write path rather than
      // trusted because it is ours.
      const receipt = assertGateZeroReceipt(emitted.receipt, seat);

      // THE WRITE RUNS ON THE SEAT'S OWN CONNECTION, NOT ON `c` (2026-09-14,
      // PR 1014 third correction, standing-rule amendment 9).
      //
      // `c` is the ordinary writer connection every other verb uses, and that is
      // exactly why this call may not travel on it. Sol's finding 2: the record
      // layer's seat test read carr.acting_actor_slug, a GUC any session can set
      // on itself, so any carr_writer connection could have named the staffed
      // lane and been believed. Migration 0502 now revokes EXECUTE from
      // carr_writer, grants it to one capability bundle reachable by one login
      // role, and derives the seat from session_user. This opens a connection
      // that AUTHENTICATES as that role, with a secret used for nothing else.
      //
      // NO FALLBACK, BY DESIGN. A Worker that carries no such secret refuses here
      // by name. Falling back to `c` would be a deployment in which the whole
      // amendment is off and nothing said so — which is the failure this correction
      // exists to close, not a degradation worth tolerating.
      if (typeof c.seatConnection !== "function") {
        throw new ToolError({ error: "gate_zero_seat_connection_unavailable",
          required_secret: GATE_ZERO_WRITER_SECRET_NAME,
          hint: "recording a Gate Zero outcome requires the dedicated producer connection. The ordinary " +
                "writer connection is refused by the record layer and is not used as a fallback: provision " +
                "the login role and its secret before this verb can record anything." });
      }
      // ONE TRANSACTION ON THAT CONNECTION, carrying the write and the readback
      // together. The readback has to be inside it: on a FIRST call the row is
      // this transaction's own and is not visible to any other connection until
      // it commits.
      const row = await c.seatConnection(async seat => {
        const recordedId = (await seat.query(
          `select ops.gate_zero_record_read_only_outcome($1::uuid,$2::jsonb) as id`,
          [args.idempotency_key, JSON.stringify(receipt)])).rows[0].id;

        // READ THE DIGEST BACK OUT OF THE ROW rather than reporting the one this
        // process computed. The database recomputes it from the persisted receipt
        // with ops.gate_zero_outcome_digest(); reporting our own value would let a
        // caller believe a number the record layer never agreed to. The locally
        // computed digest is compared, not returned, so a divergence between the
        // two canonicalizations is a refusal here instead of a silent mismatch
        // that only surfaces when a benchmark acceptance binds the wrong value.
        //
        // THE STORED RECEIPT COMES BACK TOO, AND THE CHECK IS AGAINST IT rather
        // than against this call's object (PR 1014, second correction). On a
        // RETRY the row is the earlier run's -- same candidate, different
        // session_ref, different instants -- so comparing the row's digest with a
        // digest of THIS call's receipt refused every genuine second call, which
        // was the gateway half of Sol's finding 3. Recomputing from the persisted
        // receipt asks the question the check was always meant to ask: do the
        // record layer's canonical JSON and artifact-trust.js's agree about the
        // bytes that are actually stored? That holds on a first call and a retry
        // alike, and it is the stronger of the two readings.
        return (await seat.query(
          `select id, outcome_digest, candidate_scoped_digest, candidate_digest, status,
                  producing_seat_ref, receipt,
                  to_char(observed_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"') as observed_at,
                  to_char(recorded_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"') as recorded_at
             from ops.gate_zero_read_only_outcome where id = $1::uuid`,
          [recordedId])).rows[0];
      });
      const localDigest = gateZeroOutcomeDigest(row.receipt);
      if (row.outcome_digest !== localDigest) {
        throw new ToolError({ error: "gate_zero_outcome_digest_divergence",
          recorded: row.outcome_digest, recomputed_here: localDigest,
          hint: "the record layer's canonical JSON and artifact-trust.js's disagreed about the stored receipt. " +
                "The recorded value is the database's; this is a contract defect, not a caller error." });
      }
      // AND THE TWO CANONICALIZATIONS AGREE ABOUT THE PROJECTION TOO, asked over
      // the STORED receipt exactly as the full digest above is. This is the same
      // contract question in the narrower place: does the record layer's
      // projection-then-canonicalize produce the bytes artifact-trust.js's does?
      // It is a property of the row alone, so it holds on a first write and on
      // every retry that converges onto the immutable row.
      const storedCandidateDigest = gateZeroOutcomeCandidateDigest(row.receipt);
      if (row.candidate_scoped_digest !== storedCandidateDigest) {
        throw new ToolError({ error: "gate_zero_outcome_candidate_digest_divergence",
          recorded: row.candidate_scoped_digest, recomputed_here: storedCandidateDigest,
          hint: "the record layer's candidate projection and artifact-trust.js's disagreed about the stored " +
                "receipt. The recorded value is the database's; this is a contract defect, not a caller error." });
      }
      // KEEP THE OFFERED RECEIPT SEPARATE FROM THE RECORDED ONE. A normal retry
      // necessarily carries fresh session and time bytes, and its evidence may
      // have moved. The record layer returns the immutable row for the candidate
      // so this outer transaction can heal a missing audit event; these digests
      // make that convergence visible rather than describing the offered run as
      // if it had been stored.
      const offeredDigest = gateZeroOutcomeDigest(receipt);
      const offeredCandidateDigest = gateZeroOutcomeCandidateDigest(receipt);
      const convergedOntoRecorded = row.outcome_digest !== offeredDigest;

      // ONE EVENT PER OUTCOME ROW, SERIALIZED ON THE OUTCOME. The retry is
      // exactly what makes this necessary: it exists to heal a missing event, and
      // an unguarded insert would instead write a second one every time a call
      // got past the seat commit.
      //
      // THE LOCK BLOCKS, DELIBERATELY. A try-lock let a concurrent retry return
      // `ok: true` without an event; if the holder then rolled back, the row was
      // still eventless and only a THIRD call could repair it. That is eventually
      // healable, not the unconditional second-call healing the refusal requires.
      // In production the dependency graph has no cycle: each seat transaction
      // commits before its caller reaches this lock, so a waiter holds no row the
      // lock owner needs. The former test deadlock came from its harness awaiting
      // the waiter before committing the owner transaction; the proof now stages
      // the production order instead of weakening the production invariant.
      await c.query(
        "select pg_advisory_xact_lock(hashtextextended($1, 0))",
        [`gate-zero-outcome-event:${row.id}`]);
      const eventAlready = await c.query(
        `select 1 from event
          where verb = 'record-gate-zero-read-only-outcome'
            and subject_type = 'gate_zero_outcome' and subject_id = $1::uuid
          limit 1`, [row.id]);
      if (!eventAlready.rows.length) {
        await writeEvent(c, actor, "record-gate-zero-read-only-outcome", "gate_zero_outcome", row.id,
          { field: "outcome_recorded",
            new: { outcome_digest: row.outcome_digest,
                   candidate_scoped_digest: row.candidate_scoped_digest,
                   candidate_digest: row.candidate_digest,
                   status: row.status, observed_at: row.observed_at,
                   producing_seat_ref: row.producing_seat_ref },
            idempotency_key: args.idempotency_key });
      }

      return {
        ok: true, outcome_id: row.id,
        step_ref: "step:gate-zero-read-only-outcome",
        gate_id: "gate-zero-read-only-accepted",
        receipt_schema: GATE_ZERO_RECEIPT_SCHEMA,
        outcome_digest: row.outcome_digest,
        // INFORMATIONAL PROJECTION OF THE RECORDED RECEIPT. candidate_digest,
        // not this value, is the one-row-per-candidate arbiter.
        candidate_scoped_digest: row.candidate_scoped_digest,
        candidate_digest: row.candidate_digest,
        converged_onto_recorded_outcome: convergedOntoRecorded,
        offered_outcome_digest: offeredDigest,
        offered_candidate_scoped_digest: offeredCandidateDigest,
        status: row.status,
        observed_at: row.observed_at,
        recorded_at: row.recorded_at,
        producing_seat_ref: row.producing_seat_ref,
        producing_seat_charter_decision_ref: seat.charter_decision_ref,
        producing_seat_staffing_decision_ref: seat.staffing_decision_ref,
        producer_reason_id: convergedOntoRecorded ? null : emitted.reason_id,
        receipt_status: row.receipt.status,
        offered_producer_reason_id: emitted.reason_id,
        offered_receipt_status: receipt.status,
        // WHAT THIS ROW IS WORTH, said on the result so a consumer does not read
        // more into it than it carries.
        digest_recipe: "canonical-JSON sha256 over the TAGGED two-element array " +
          "[\"consumer-gate-receipt.v1\", <receipt>], recomputed by the record layer. r7's " +
          "receipt_payload_digest_rule declares that preimage for this schema (amended 2026-09-13) and states that " +
          "a plain digest over the receipt alone does not satisfy it. candidate_scoped_digest is the UNTAGGED " +
          "canonical-JSON sha256 over a projection of the receipt — without observed_at, ttl_expires_at and the " +
          "per-call session_ref of each of its three identities — because that projection is not a " +
          "consumer-gate-receipt.v1 and r7's rule does not speak about it. It is an informational comparison aid, " +
          "not evidence or an admission key; candidate_digest is the one-row-per-candidate arbiter.",
        effects: Object.freeze({
          creates_effect: false, clock_started: false, benchmark_accepted: false,
          note: "recording an outcome binds nothing on its own. Benchmark acceptance reads the current passing " +
                "outcome and is still humanOnly; the Journey 1 clock still starts at the first passing " +
                "foundation-assurance-minimum receipt, which this verb neither issues nor reaches.",
        }),
      };
    }),
  },

  "add-industry-event": {
    write: true,
    description: "Record a healthcare CRE industry event such as a conference, association meeting, trade show, or networking event. Timestamps must include their timezone; source and the owner partner are required. Tenant is server-derived.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, title: { type: "string" }, organizer: { type: "string" },
      kind: { type: "string", enum: INDUSTRY_EVENT_KINDS }, starts_at: { type: "string" },
      ends_at: { type: "string" }, location: { type: ["string", "null"] }, is_virtual: { type: "boolean" },
      url: { type: ["string", "null"] }, relevance_note: { type: ["string", "null"] },
      attendance_intent: { type: "string", enum: INDUSTRY_EVENT_ATTENDANCE_INTENTS },
      owner_partner: { type: "string", enum: PARTNER_SLUGS },
      status: { type: "string", enum: INDUSTRY_EVENT_STATUSES }, source: { type: "string" },
    }, required: ["idempotency_key", "title", "organizer", "kind", "starts_at", "ends_at", "owner_partner", "source"] },
    handler: async (c, actor, args) => {
      const clean = validateIndustryEventFields(args);
      return withEnvelope(c, actor, "add-industry-event", args, async () => {
        const tenant = organizationTenantForActor(actor);
        const inserted = await c.query(
          `insert into industry_event
             (organization_tenant_id,title,organizer,kind,starts_at,ends_at,location,is_virtual,url,
              relevance_note,attendance_intent,owner_partner,status,source,created_by_actor_id,updated_by_actor_id)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$15)
           returning id, organization_tenant_id, title, organizer, kind, starts_at, ends_at, location,
                     is_virtual, url, relevance_note, attendance_intent, owner_partner, status, source,
                     version, created_at, updated_at`,
          [tenant, clean.title, clean.organizer, clean.kind, clean.starts_at, clean.ends_at,
           clean.location, clean.is_virtual, clean.url, clean.relevance_note, clean.attendance_intent,
           clean.owner_partner, clean.status, clean.source, actor.id]);
        const event = inserted.rows[0];
        await writeEvent(c, actor, "add-industry-event", "industry_event", event.id,
          { new: event, idempotency_key: args.idempotency_key });
        return { ok: true, event };
      });
    },
  },

  "list-industry-events": {
    write: false,
    description: "List tenant-scoped healthcare CRE industry events for the Events tab, ordered by start time. Returns the current version needed for a later update.",
    inputSchema: { type: "object", properties: { limit: { type: "integer" } } },
    handler: async (c, actor, args) => {
      const limit = args.limit === undefined ? 50 : args.limit;
      if (!Number.isInteger(limit) || limit < 1 || limit > 100)
        throw new ToolError({ error: "industry_event_limit_invalid", hint: "limit must be an integer from 1 through 100" });
      const result = await c.query(
        `select id, organization_tenant_id, title, organizer, kind, starts_at, ends_at, location,
                is_virtual, url, relevance_note, attendance_intent, owner_partner, status, source,
                version, created_at, updated_at
           from industry_event
          where organization_tenant_id=$1
          order by starts_at, id limit $2`, [organizationTenantForActor(actor), limit]);
      return { ok: true, events: result.rows, count: result.rows.length };
    },
  },

  "update-industry-event": {
    write: true,
    description: "Update a tenant-scoped healthcare CRE industry event with optimistic concurrency. Send the version returned by list-industry-events as base_version; stale updates refuse without changing the row.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, event_id: { type: "string" }, base_version: { type: "integer" },
      title: { type: "string" }, organizer: { type: "string" }, kind: { type: "string", enum: INDUSTRY_EVENT_KINDS },
      starts_at: { type: "string" }, ends_at: { type: "string" }, location: { type: ["string", "null"] },
      is_virtual: { type: "boolean" }, url: { type: ["string", "null"] }, relevance_note: { type: ["string", "null"] },
      attendance_intent: { type: "string", enum: INDUSTRY_EVENT_ATTENDANCE_INTENTS },
      owner_partner: { type: "string", enum: PARTNER_SLUGS }, status: { type: "string", enum: INDUSTRY_EVENT_STATUSES },
      source: { type: "string" },
    }, required: ["idempotency_key", "event_id", "base_version"] },
    handler: async (c, actor, args) => {
      const eventId = industryEventId(args.event_id);
      if (!Number.isInteger(args.base_version) || args.base_version < 1)
        throw new ToolError({ error: "industry_event_base_version_invalid" });
      const clean = validateIndustryEventFields(args, { partial: true });
      const keys = ["title", "organizer", "kind", "starts_at", "ends_at", "location", "is_virtual",
        "url", "relevance_note", "attendance_intent", "owner_partner", "status", "source"]
        .filter(key => Object.hasOwn(clean, key));
      if (!keys.length)
        throw new ToolError({ error: "industry_event_update_empty" });
      return withEnvelope(c, actor, "update-industry-event", args, async () => {
        const tenant = organizationTenantForActor(actor);
        const values = keys.map(key => clean[key]);
        const actorParam = values.length + 1;
        const idParam = values.length + 2;
        const tenantParam = values.length + 3;
        const versionParam = values.length + 4;
        const set = keys.map((key, index) => `${key}=$${index + 1}`);
        set.push(`updated_by_actor_id=$${actorParam}`, "updated_at=now()", "version=version+1");
        const result = await c.query(
          `update industry_event set ${set.join(",")}
             where id=$${idParam} and organization_tenant_id=$${tenantParam} and version=$${versionParam}
           returning id, organization_tenant_id, title, organizer, kind, starts_at, ends_at, location,
                     is_virtual, url, relevance_note, attendance_intent, owner_partner, status, source,
                     version, created_at, updated_at`,
          [...values, actor.id, eventId, tenant, args.base_version]);
        if (!result.rows.length)
          throw new ToolError({ error: "industry_event_version_conflict",
            hint: "read the event again and retry with its current version" });
        const event = result.rows[0];
        await writeEvent(c, actor, "update-industry-event", "industry_event", event.id,
          { new: event, idempotency_key: args.idempotency_key });
        return { ok: true, event };
      });
    },
  },
};

// These names describe server-owned authority, never data a tool invocation may
// claim. This is deliberately TOP-LEVEL only: record findings, template maps,
// metrics, and correction payloads legitimately carry free-form business keys
// such as `action` or `profile`, and those nested keys cannot widen authority.
// call-verb recursion and composite dispatch each hand their inner arguments
// back to this boundary as a new top-level invocation.
const RESERVED_AUTHORITY_ARGUMENT_FIELDS = new Set([
  "tenant", "tenant_id", "organization_tenant_id", "sponsor", "sponsoring_human_id",
  "sponsoring_human_slug", "human_slug", "identity", "actor", "runtime_principal", "audience",
  "authorization", "authorization_class", "profile", "capability", "capabilities",
  "action", "actions", "action_authority", "action_authorities", "allowed_actions",
  "write", "writes_records", "calls_models", "call_models",
]);

// Authority and registry checks remain distinct. The server resolves and audits
// the sponsor separately from the runtime actor; caller fields can select
// neither. Joe's 2026-08-26 ruling retired the humanOnly refusal and admitted
// the two server-bound local partner doors, but reviewers, probes, Hermes, Grok,
// and unsponsored runtimes remain outside partner authority.
export function assertNoCallerAuthorityFields(args) {
  if (args && typeof args === "object" && !Array.isArray(args) &&
      Object.keys(args).some((key) => RESERVED_AUTHORITY_ARGUMENT_FIELDS.has(key)))
    throw new ToolError({ error: "caller_authority_field_forbidden" });
  return args;
}

// One registered-handler path for direct MCP calls and composite verbs. The
// MCP layer applies its profile gate first; this helper owns caller-authority,
// registry lookup, human-only, coercion, and handler/envelope gates. Keeping
// the first gate here makes direct MCP, call-verb recursion, and composites
// fail closed before a handler or database client can be used.
// EVERY DECLARED CLOSED VOCABULARY IS ENFORCED HERE, AND THIS IS THE ONLY
// PLACE THAT ENFORCES IT GENERICALLY.
//
// WHY IT EXISTS. There is no JSON-schema validator anywhere in this server --
// no ajv, no jsonschema -- so an `enum` in an inputSchema was documentation
// that nothing read. 73 of 89 enum fields were guarded BY HAND in their own
// handler with one(); the other 16, across 15 verbs, passed whatever they were
// given straight through to Postgres. Thirteen of the columns behind them
// carry no check constraint either, so for those the declared vocabulary was
// enforced at NO layer.
//
// THE COST, MEASURED 2026-09-18: v_code_finding.epistemic_status holds
// 'human_stated', which is not one of the nine values record-finding declares.
// It is a legitimate value of a DIFFERENT closed vocabulary, event.cause, and
// nothing anywhere noticed the two being confused. Three of the nine declared
// values have never been used at all.
//
// A HAND-WRITTEN GUARD PER FIELD WOULD CLOSE THE 16 AND NOT THE SEVENTEENTH.
// The gap reappears the next time someone declares an enum and forgets the
// one() call, which is precisely how these 16 arose. Reading the schema the
// verb already publishes closes the class, including for verbs not yet
// written.
//
// THE REFUSAL NAMES THE FIELD AND THE ALLOWED VALUES, which the database
// cannot do: 219 check constraints span more than one column, so Postgres
// reports `column: null` and the caller is told a rule broke without being
// told which of their inputs broke it. This refusal happens BEFORE the
// database and can say exactly what to send instead.
function assertDeclaredVocabularies(name, tool, args) {
  const properties = tool?.inputSchema?.properties;
  if (!properties || !args || typeof args !== "object" || Array.isArray(args)) return;
  for (const [field, spec] of Object.entries(properties)) {
    const allowed = spec?.enum || spec?.items?.enum;
    if (!Array.isArray(allowed) || allowed.length === 0) continue;
    const value = args[field];
    // Absent and null are the field not being sent. Optionality is the
    // schema's business, and required-field checks already run elsewhere;
    // this one rules on VALUE only.
    if (value === undefined || value === null) continue;
    const sent = spec?.items?.enum && Array.isArray(value) ? value : [value];
    for (const one of sent) {
      if (allowed.includes(one)) continue;
      throw new ToolError({
        error: "value_not_in_declared_vocabulary",
        verb: name, field,
        received: typeof one === "string" ? one : typeof one,
        allowed,
        hint: `\`${field}\` is a closed vocabulary on ${name}. Send one of the ` +
              `values in \`allowed\`. This refusal comes from the verb's own ` +
              `published schema, before any database write, so nothing was ` +
              `recorded and nothing needs undoing.`,
      });
    }
  }
}

export async function assertRegisteredToolInput(name, tool, args = {}) {
  assertDeclaredVocabularies(name, tool, args);
  try {
    await assertRegisteredOperation(name, tool, args);
  } catch (error) {
    if (error instanceof MutationRegistryRefusal)
      throw new ToolError({ error: error.error, operation: error.operation,
        ...(error.fields ? { fields: error.fields } : {}) });
    throw error;
  }
}

export async function executeRegisteredTool(client, actor, name, args = {}) {
  const tool = TOOLS[name];
  if (!tool) throw new ToolError({ error: "unknown_tool", name });
  assertNoCallerAuthorityFields(args);
  // ── PORTED VERB GATES (bypass audit C33/C34, 2026-09-24), MOVED HERE
  // (Opus re-review, 2026-09-24) from mcp.js's callTool(). callTool() is NOT
  // the one choke point: mcp-server/local-verb.mjs's BREAK-GLASS mode
  // (a direct DATABASE_URL connection, used for local/rehearsal calls) calls
  // executeRegisteredTool() DIRECTLY and never goes through callTool() at
  // all, so the check placed there missed that door entirely. THIS function
  // is the actual chokepoint: local-verb.mjs's own comment at its call site
  // says so ("the one choke point that also applies argument type coercion
  // and raw-DB-error translation"), and callTool()'s read AND write branches
  // both call it too. One placement, three doors: callTool() read, callTool()
  // write, and local-verb.mjs break-glass (both its read and write shapes).
  // Still purely in memory, before any client.query call in this function —
  // same testability as the callTool()-level placement had.
  if (name === "add-loop") {
    if (needsDecider(args))
      throw new ToolError({ error: "capability_no_decider", hint: BLOCKER_DECIDER_REASON });
    if (parksADecision(args)) {
      const { allow, why } = classifyLoopText(loopRowText(args));
      if (!allow)
        throw new ToolError({ error: "internal_decision_parked", why, hint: ESCALATION_REASON });
    }
  }
  // HUMAN-ONLY MEANS PARTNER AUTHORITY, NOT A SECOND CHAT WINDOW. A verified
  // partner passes directly. A native Codex/Claude grant or local machine door
  // passes only when partner-authority.js can derive its sponsor from
  // server-held identity state and bind it to that sponsor's authority DB
  // credential. Probe, review, unknown, unverified, and merely caller-claimed
  // sponsors still refuse before schema validation or database access.
  //
  // This restores the useful part of Joe's 2026-08-26 ruling and removes the
  // WR-000021 overcorrection: the ledger still keeps acting_actor_slug as the
  // actual machine actor while mcp.js separately records the verified sponsor.
  // The registry-wide test covers every present and future humanOnly verb.
  if (tool.humanOnly === true) {
    const actorClass = authorizationClassForActor(actor);
    // Identity merges require the human caller even when a machine holds
    // sponsor-scoped partner authority. Other partner-authority verbs retain
    // their existing native/local agent route.
    if (actorClass !== "verified_partner" &&
        (name === "confirm-merge" || !canExercisePartnerAuthority(actor)))
      throw new ToolError({ error: "human_only_verb_requires_verified_partner",
        verb: name, actor_class: actorClass,
        hint: name === "confirm-merge" ? "confirm-merge requires a verified human partner; machine identities cannot confirm identity merges." :
              "this verb records a partner-authority act and requires either the verified partner " +
              "or a server-verified native/local agent bound to that partner's sponsor-scoped " +
              "authority connection." });
  }
  // ORACLE-SEAT-ONLY IS ENFORCED HERE, AND THIS IS THE ONLY PLACE THAT
  // ENFORCES IT (2026-09-13, DoctorCRE v5 slice V5-A02 Step B). It is a THIRD
  // authority shape beside humanOnly and the ordinary sponsored-agent surface,
  // and it exists because r7 registers a producer whose role is
  // `independent_control_plane_oracle` and whose seat is a machine: the
  // independent Codex reviewer lane, whose derived class is `review_agent`.
  // Joe ruled on 2026-09-13 (decision d4e5f6a7-b8c9-4d0e-9f1a-2b3c4d5e6f70)
  // that this seat records the Gate Zero outcome on its own authority, with no
  // partner countersign.
  //
  // IT IS THE MIRROR IMAGE OF humanOnly, not a relaxation of it. humanOnly
  // refuses every machine; this refuses every human AND every machine but one.
  // A verified partner is refused here on purpose: a partner signing an
  // independent oracle's receipt is the exact thing the oracle exists to
  // prevent, and the partner's own act in this chain sits downstream on the
  // benchmark manifest, which is still humanOnly.
  //
  // THE CLASS IS NOT THE TEST. `grok-reviewer` authenticates through the same
  // review-token door and derives the same `review_agent` class, so admitting
  // the class would hand the Gate Zero signature to a lane nobody ruled on.
  // deriveGateZeroProducerSeat checks the class AND the seat, reading the lane
  // off the frozen registration's DERIVED holder ref -- so putting that seat
  // back to unstaffed closes this door too, with nothing else touched.
  //
  // ENFORCED IN THREE INDEPENDENT PLACES, deliberately: here, again inside the
  // handler, and a third time in SQL by ops.gate_zero_producer_actor_id(). The
  // flag alone would be a label, which is the defect WR-000021 found on
  // humanOnly between 2026-08-26 and 2026-09-11.
  if (tool.oracleSeatOnly === true) {
    const foundation = tool.oracleFamily === "foundation-assurance";
    const lane = foundation ? foundationAssuranceOracleLane(name) : gateZeroOracleSeatLane();
    const actorClass = authorizationClassForActor(actor);
    if (lane === null || actorClass !== "review_agent" || actor?.human === true || actor?.slug !== lane)
      throw new ToolError({ error: "oracle_seat_verb_requires_the_staffed_seat",
        verb: name, actor_class: actorClass,
        seat_staffed: lane !== null,
        hint: "this verb records an independent control-plane oracle's receipt and refuses every actor except " +
              "the one review-token seat that holds it — including verified partners, sponsored agents, and a " +
              "second review-token lane deriving the same authority class. Report what you would have recorded " +
              "and let the seat run it." });
    if (foundation) {
      try { assertFoundationAssuranceOracleSeat(actor, name); }
      catch (error) {
        throw new ToolError({ error: error.code || "foundation_assurance_oracle_seat_required",
          ...(error.detail || {}) });
      }
    }
  }
  await assertRegisteredToolInput(name, tool, args);
  // TYPE COERCION AT THE CHOKE POINT (loop 353, 2026-08-13). See
  // coerceArgsToSchema above for what this fixes and why it is here rather than
  // in the seventeen handlers that would otherwise each need to remember. It
  // runs before the humanOnly-passed handler sees anything, so no verb can read
  // a declared boolean or number in the wrong JS type.
  coerceArgsToSchema(tool.inputSchema, args);
  // REQUIRED-ARGUMENT CHECK, immediately after coercion so a value that only
  // becomes present once coerced is judged in its final form. See
  // assertRequiredArgs above: a missing required field used to reach the
  // handler as undefined and come back as a confident empty answer.
  assertRequiredArgs(tool.inputSchema, args);
  // V5-S01 GLOBAL BOUNDARIES, AT THE ONE SEAM EVERY DOOR PASSES (2026-09-25).
  // Evaluated here, after coercion and the required-argument check so the
  // verdict reads the arguments in their final form, and before the handler
  // so nothing a boundary refuses can reach the database. The door only ever
  // ADDS a refusal; every gate above and every check inside the handler still
  // runs. SHADOW until Joe approves enforcement: V5_BOUNDARY_DOOR_MODE in
  // global-boundaries-door.v5.js is the constant "shadow", passBoundaryDoor
  // records and logs a refusal verdict and never throws in that mode, and the
  // catch below is reachable only once that constant is flipped to "enforce".
  // The door context (connectivity "online") is the server's, never a field a
  // caller sent.
  try {
    passBoundaryDoor({ verb: name, write: tool.write === true, actor, args, now: new Date().toISOString() });
  } catch (error) {
    if (error instanceof V5BoundaryDoorRefusal) throw new ToolError(error.payload);
    throw error;
  }
  // DEFECT 2, HALF (b): every verb funnels through here — the one choke point
  // where a raw DB error can be translated into a clean ToolError before it
  // ever reaches the transport (mcp.js's callTool/dispatch, or local-verb.mjs),
  // so the fix lands once instead of being re-implemented per caller. A
  // ToolError a handler threw on purpose passes straight through unchanged;
  // only an UNTRANSLATED error gets a look from pgConstraintError, and only a
  // recognized class-23 violation gets rewritten — anything else (a real
  // connection or driver fault) still surfaces as-is for the transport's own
  // generic handling.
  try {
    // THE AUTHENTICATED CALL IS NOT ESTABLISHED HERE ANY MORE (amendment 9,
    // 2026-09-14). It used to be: this line asked identity.js for THIS actor's
    // dispatcher and ran the handler inside it. The fifth review round found
    // what that required identity.js to publish — `dispatchFor(actor)`, a
    // callable that enters a context — and that a probe composing it with the
    // equally public review door ran its own code as `review_agent`.
    //
    // SO THE ENTRY MOVED INTO identity.js, where the bearer is matched and the
    // context entered in one module-internal act — and since the sixth
    // correction round (2026-09-15) what runs inside it is named rather than
    // handed over: `serveAuthenticatedCall(bearer, correlationId, entryName)`
    // resolves the name in a frozen map of the server's own entries. No verb
    // dispatched through this function runs inside that context, including this
    // one; nothing is threaded through here, so there is nothing here for a
    // caller to aim.
    //
    // WHAT THAT NARROWS, stated rather than discovered later: a verb reached
    // through any OTHER door — the OAuth grant path, the agent, Hermes,
    // continuity and local bearers, `./run.sh call`, a direct import — runs with
    // NO authenticated call at all, so r7's receipt surfaces refuse. That is
    // correct for the Gate Zero producer, whose candidate is the deployed
    // Worker's own build stamp (build-stamp.js) and which has nothing to say
    // about a local checkout.
    return await tool.handler(client, actor, args);
  } catch (e) {
    if (e instanceof ToolError) throw e;
    throw pgConstraintError(e) || e;
  }
}


const TOOL_REGISTRATION_SOURCE = Object.freeze({
  "inline": "mcp-server/src/tools.js",
  "deal-room-inline": "mcp-server/src/tools.js",
  "deploy-gap-inline": "mcp-server/src/tools.js",
  "doctrine": "mcp-server/src/doctrine.js",
  "situation-retrieval": "mcp-server/src/situation-retrieval.js",
  "investigation": "mcp-server/src/investigation.js",
  "doc-conversation": "mcp-server/src/doc-conversation.js",
  "doc-activity": "mcp-server/src/doc-activity.js",
  "meeting-mode": "mcp-server/src/meeting-mode.js",
  "notifications": "mcp-server/src/notifications.js",
  "delivery-cadence-a05": "mcp-server/src/delivery-cadence-a05-tools.js",
  "session-identity": "mcp-server/src/session-identity.js",
  "dispatch-spine": "mcp-server/src/dispatch-spine.js",
  "doc-outcome-cards": "mcp-server/src/tools.js",
  "capability-program": "mcp-server/src/capability-program.js",
  "work-shape": "mcp-server/src/work-shape.js",
  "work-request-intake": "mcp-server/src/work-request-intake.js",
  "lease-term-comparison": "mcp-server/src/lease-term-comparison.js",
  "partner-room": "mcp-server/src/partner-room.js",
  "agent-profile": "mcp-server/src/agent-profiles.js",
  "bot-brief": "mcp-server/src/bot-brief.js",
  "evidence-activation": "mcp-server/src/evidence-activation.js",
  "resource-observation": "mcp-server/src/resource-observation.v5.js",
  "jev-call-receipt": "mcp-server/src/jev-call-receipt.js",
  "lead-automation": "mcp-server/src/lead-automation.js",
  "invoice-automation": "mcp-server/src/invoice-automation.js",
  "workflow-cutover": "mcp-server/src/workflow-cutover.v5.js",
  "memory": "mcp-server/src/memory.js",
  "codex-continuity": "mcp-server/src/codex-continuity.js",
  "claude-continuity": "mcp-server/src/claude-continuity.js",
  "incident": "mcp-server/src/incident.js",
  "engineering-runtime": "mcp-server/src/engineering-runtime.js",
  "work-portfolio": "mcp-server/src/work-portfolio.js",
  "tour-rights-projection": "mcp-server/src/tour-rights-projection.js",
  "tour-property-jurisdiction": "mcp-server/src/tour-property-jurisdiction.js",
  "tour-domain": "mcp-server/src/tour-domain.js",
  "tour-property-search": "mcp-server/src/tour-property-search.js",
  "tour-map-promotion": "mcp-server/src/tour-map-promotion.js",
  "tour-sharing": "mcp-server/src/tour-sharing.js",
  "tour-artifacts": "mcp-server/src/tour-artifacts.js",
  "benchmark-acceptance": "mcp-server/src/benchmark-acceptance-store.v5.js",
  "model-role-store": "mcp-server/src/model-role-store.v5.js",
  "record-source-authority": "mcp-server/src/record-source-authority-store.v5.js",
  "cre-lifecycle": "mcp-server/src/cre-lifecycle-store.v5.js",
  "salesforce-reconciliation-rw02": "mcp-server/src/salesforce-reconciliation-store-rw02.v5.js",
  "salesforce-read-run-rw02": "mcp-server/src/salesforce-read-run-store-rw02.v5.js",
  "foundation-assurance": "mcp-server/src/foundation-assurance-minimum-producer.v5.js",
  "global-boundaries-door": "mcp-server/src/global-boundaries-door.v5.js",
  "journey-one-clock-door": "mcp-server/src/journey-one-clock-door.v5.js",
  "governed-correspondence-store": "mcp-server/src/governed-correspondence-store.v5.js",
  "assurance-health-store": "mcp-server/src/assurance-health-store.v5.js",
  "action-class-successor-registry": "mcp-server/src/action-class-successor-registry.v5.js",
  "complete-set-review-a03-store": "mcp-server/src/independent-review-cycle-store.v5.js",
  "rule-context-runtime": "mcp-server/src/rule-context-runtime.v5.js",
  "system-work-census": "mcp-server/src/system-work-census.v5.js",
  "board-answers": "mcp-server/src/board-answers.js",
  "schedule-board": "mcp-server/src/schedule-board.js",
  "doc-suggestions": "mcp-server/src/doc-suggestions.js",
  "whats-new": "mcp-server/src/whats-new.js",
  "invoice-tracker": "mcp-server/src/invoice-tracker.js",
});

function bindToolSource(tool, source) {
  if (!tool || typeof tool !== "object" || !TOOL_REGISTRATION_SOURCE[source])
    throw new Error(`missing reviewed tool source for ${source}`);
  Object.defineProperty(tool, "registrySource", {
    value: TOOL_REGISTRATION_SOURCE[source], enumerable: false, writable: false, configurable: false,
  });
  deepFreezeToolContract(tool);
}

function deepFreezeToolContract(value, seen = new Set()) {
  if ((!value || (typeof value !== "object" && typeof value !== "function")) || seen.has(value)) return value;
  seen.add(value);
  for (const key of Reflect.ownKeys(value)) deepFreezeToolContract(value[key], seen);
  return Object.freeze(value);
}

for (const tool of Object.values(TOOLS)) bindToolSource(tool, "inline");

function registerTools(additions, source) {
  const duplicates = Object.keys(additions).filter(name => Object.hasOwn(TOOLS, name)).sort();
  if (duplicates.length)
    throw new Error(`duplicate tool registration from ${source}: ${duplicates.join(",")}`);
  for (const tool of Object.values(additions)) bindToolSource(tool, source);
  Object.assign(TOOLS, additions);
}

registerTools(invoiceTrackerTools({ ToolError, withEnvelope, writeEvent }), "invoice-tracker");

// Deal Room contract. Durable writes use the same envelope and event helper as
// the rest of this registry; the one explicit exception is the ephemeral lease.
registerTools({
  "get-deal-room": {
    description: "Read one complete open Deal Room work record: board/workspace and active/parked fields, next actions, notes, critical dates, activity, parties, premises, current economics, documents, and attributed history. Includes salesforce_id and base_version only so a reconciler can make one guarded follow-on write. Placeholder Salesforce fields are structurally excluded.",
    inputSchema: { type: "object", properties: { deal: { type: "string" } }, required: ["deal"] },
    handler: async (c, _actor, args) => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      const deal = await c.query(
        `select b.id, b.name, b.phase, b.owner, b.type, b.market as city, b.segment, b.attention,
                to_jsonb(b.next_date)#>>'{}' as next_date, b.next_step, b.client_ref, b.client_name,
                b.account_client_id, b.account_client_ref, b.account_name, b.account_owner,
                b.market_agent, to_jsonb(b.last_touch)#>>'{}' as last_touch,
                to_jsonb(b.last_review_at)#>>'{}' as last_review_at, b.workspace_kind,
                b.operating_state, b.parking_reason, b.parking_note,
                to_jsonb(b.invoiced_on)#>>'{}' as invoiced_on,
                to_jsonb(b.closed_on)#>>'{}' as closed_on, b.lane, b.outcome,
                (select to_jsonb(pc) from v_deal_room_phase_change pc where pc.deal_id=b.id) as phase_change,
                to_jsonb(b.parked_at)#>>'{}' as parked_at, b.parked_by,
                r.salesforce_id, r.base_version
           from v_deal_room_board b
           join v_deal_reconciliation_read r on r.id=b.id
          where b.id=$1`, [s.id]);
      if (!deal.rows.length) throw new ToolError({ error: "not_found", table: "deal", id: s.id });
      const thread = await c.query(
        "select id, kind, text, actor, to_jsonb(created_at)#>>'{}' as created_at from v_deal_room_note where deal_id=$1 order by created_at desc, id desc",
        [s.id],
      );
      const criticalDates = await c.query(
        "select id, kind, to_jsonb(due_on)#>>'{}' as due_on, note, source, status from v_deal_room_critical_date where deal_id=$1 order by due_on, id",
        [s.id],
      );
      const history = await c.query(
        `select id, to_jsonb(recorded_at)#>>'{}' as recorded_at, actor, verb, field, old_value, new_value
           from v_deal_room_event where subject_id=$1
          order by recorded_at desc, id desc`,
        [s.id],
      );
      const actions = await c.query(
        `select n.id, n.owner, n.description,
                to_jsonb(n.due_on)#>>'{}' as due_on, n.status,
                to_jsonb(n.updated_at)#>>'{}' as updated_at
           from v_deal_room_action n
          where n.deal_id=$1
          order by (n.status='open') desc, n.updated_at desc, n.id desc`, [s.id]);
      const activities = await c.query(
        `select a.id, to_jsonb(a.occurred_at)#>>'{}' as occurred_at, a.actor,
                a.kind, a.summary, a.detail, a.source
           from v_deal_room_activity a
          where a.deal_id=$1 order by a.occurred_at desc, a.id desc limit 50`, [s.id]);
      const participants = await c.query(
        `select dp.role, dp.name, dp.actor, dp.party_id
           from v_deal_room_participant dp
          where dp.deal_id=$1 order by dp.role, name`, [s.id]);
      const premises = await c.query(
        `select pr.id, pr.label, pr.building_name, pr.address, pr.city, pr.state,
                pr.suite, pr.area_amount, pr.area_basis
           from v_deal_room_premises pr
          where pr.deal_id=$1 order by pr.created_at, pr.suite`, [s.id]);
      const negotiation = await c.query(
        `select round_no, side, to_jsonb(proposed_on)#>>'{}' as proposed_on,
                rate_amount, rate_basis, rate_norm_sf_yr, ti_amount, ti_basis,
                free_rent_months, term_months, escalator, opex_note,
                to_jsonb(expires_on)#>>'{}' as expires_on, note, source
           from v_deal_room_negotiation where deal_id=$1
          order by round_no desc, proposed_on desc limit 6`, [s.id]);
      const documents = await c.query(
        `select d.id, d.sent_status, d.lint_passed, d.leak_check_passed,
                to_jsonb(d.prepared_at)#>>'{}' as prepared_at, d.note
           from v_deal_room_document d where d.deal_id=$1 order by d.prepared_at desc limit 20`, [s.id]);
      // Only the current sourced lease belongs on an obligation timeline.
      // Never derive rent commencement or an option date from a term or note.
      const lease = await c.query(
        `select id, version, status, to_jsonb(executed_on)#>>'{}' as executed_on,
                to_jsonb(commencement_on)#>>'{}' as commencement_on,
                to_jsonb(expiration_on)#>>'{}' as expiration_on,
                options_note, evidence_kind, evidence_ref, source
           from v_deal_room_current_lease where deal_id=$1`, [s.id]);
      return stripDealPlaceholders({ schema_version: "deal-timeline.v1", lease: lease.rows[0] || null,
        deal_id: s.id, ...deal.rows[0], thread: thread.rows,
        critical_dates: criticalDates.rows, next_actions: actions.rows,
        activities: activities.rows, participants: participants.rows,
        premises: premises.rows, negotiation_rounds: negotiation.rows,
        documents: documents.rows, events: history.rows });
    },
  },

  "read-deal-reconciliation": {
    description: "Read the minimal all-deal reconciliation record. Use after update-deal closes a deal, because the Deal Room board intentionally contains only open deals. Returns the Salesforce reconciliation key, current base_version, and close-state fields; it never exposes Salesforce placeholders or source_row.",
    inputSchema: { type: "object", properties: { deal: { type: "string" } }, required: ["deal"] },
    handler: async (c, _actor, args) => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      const r = await c.query(
        `select id, name, salesforce_id, base_version, phase, outcome,
                to_jsonb(closed_on)#>>'{}' as closed_on,
                to_jsonb(invoiced_on)#>>'{}' as invoiced_on, lane
           from v_deal_reconciliation_read where id=$1`, [s.id]);
      if (!r.rows.length) throw new ToolError({ error: "not_found", table: "deal", id: s.id });
      return r.rows[0];
    },
  },

  "presence-lease": {
    write: true,
    description: "Acquire or refresh this actor's field-level Deal Room presence for about three seconds. Expiry is read-time only and presence never enters event history.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" }, field: { type: "string" },
    }, required: ["idempotency_key", "deal", "field"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "presence-lease", args, async () => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      if (typeof args.field !== "string" || !args.field.trim())
        throw new ToolError({ error: "field_required" });
      const lease = await c.query(
        `insert into deal_presence_lease (actor_id, deal_id, field, expires_at)
         values ($1,$2,$3,now() + interval '3 seconds')
         on conflict (actor_id, deal_id, field)
         do update set expires_at=excluded.expires_at
         returning expires_at /* dealroom:presence-upsert */`,
        [actor.id, s.id, args.field],
      );
      return { ok: true, deal_id: s.id, field: args.field, expires_at: lease.rows[0].expires_at };
    }),
  },

  "patch-deal-field": {
    write: true,
    description: "Patch one Deal Room cell using its last-seen event as the field base. Only a newer event for this exact deal+field conflicts; other fields never block it.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" },
      field: { type: "string", enum: DEAL_ROOM_FIELDS }, value: {},
      base_event_id: { anyOf: [{ type: "string" }, { type: "null" }] },
      change_reason: { type: "string", description: "why this cell changed; lands on the event as agent_rationale" },
      human_quote: { type: "string", description: "the partner's verbatim words, when they directed the change" },
    }, required: ["idempotency_key", "deal", "field", "value", "base_event_id"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "patch-deal-field", args, async () => {
      assertDealRoomField(args.field, args.value, ToolError);
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      await lockDealField(c, s.id, args.field);
      const intervening = await latestFieldConflict(c, s.id, args.field, args.base_event_id);
      if (intervening) {
        const made = await c.query(
          `insert into deal_conflict
             (deal_id, field, value_a, actor_a, event_a, value_b, actor_b)
           values ($1,$2,$3::jsonb,$4,$5,$6::jsonb,$7)
           returning id, status /* dealroom:create-conflict */`,
          [s.id, args.field, JSON.stringify(intervening.value), intervening.actor_id,
           intervening.event_id, JSON.stringify(args.value), actor.id],
        );
        return { ok: false, conflict: { id: made.rows[0].id, status: made.rows[0].status,
          deal_id: s.id, field: args.field,
          value_a: intervening.value, actor_a: intervening.actor, event_a: intervening.event_id,
          value_b: args.value, actor_b: actor.slug } };
      }
      const applied = await applyDealRoomField(c, actor, s.id, args.field, args.value,
        args.idempotency_key, "patch-deal-field",
        { change_reason: args.change_reason, human_quote: args.human_quote });
      return { ok: true, deal_id: s.id, field: args.field, ...applied };
    }),
  },

  "add-deal-note": {
    write: true,
    description: "Append context or an answer to a deal's immutable thread. Existing rows are never edited or deleted.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" }, text: { type: "string" },
    }, required: ["idempotency_key", "deal", "text"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "add-deal-note", args, async () => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      if (typeof args.text !== "string" || !args.text.trim()) throw new ToolError({ error: "text_required" });
      const note = await c.query(
        "insert into deal_note (deal_id, kind, text, actor_id) values ($1,'note',$2,$3) returning id, created_at /* dealroom:add-note */",
        [s.id, args.text.trim(), actor.id],
      );
      await writeEvent(c, actor, "add-deal-note", "deal", s.id, {
        field: "note", new: { note: args.text.trim() }, idempotency_key: args.idempotency_key,
      });
      return { ok: true, deal_id: s.id, note_id: note.rows[0].id, created_at: note.rows[0].created_at };
    }),
  },

  "set-next-step": {
    write: true,
    description: "Append a new current next step. The prior step remains unchanged as attributed history; newest next_step wins.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" }, text: { type: "string" },
      next_date: { anyOf: [{ type: "string" }, { type: "null" }] },
    }, required: ["idempotency_key", "deal", "text"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "set-next-step", args, async () => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      if (typeof args.text !== "string" || !args.text.trim()) throw new ToolError({ error: "text_required" });
      assertDealRoomField("next_date", args.next_date ?? null, ToolError);
      // The step also changes next_date. Take that cell's lock first so its
      // field history cannot race a direct date edit or undo.
      await lockDealField(c, s.id, "next_date");
      await lockDealField(c, s.id, "next_step");
      const oldDate = (await c.query("select next_date::text as next_date from deal where id=$1", [s.id])).rows[0]?.next_date ?? null;
      const prior = await c.query(
        "select id, text from deal_note where deal_id=$1 and kind='next_step' order by created_at desc, id desc limit 1 /* dealroom:current-step */",
        [s.id],
      );
      const note = await c.query(
        "insert into deal_note (deal_id, kind, text, actor_id, created_at) values ($1,'next_step',$2,$3,clock_timestamp()) returning id, to_jsonb(created_at)#>>'{}' as created_at /* dealroom:add-next-step */",
        [s.id, args.text.trim(), actor.id],
      );
      await c.query(
        "update deal set next_date=$2, updated_by=$3 where id=$1 /* dealroom:set-next-date */",
        [s.id, args.next_date ?? null, actor.id],
      );
      // The Deal Room's next step and the operating system's next action are
      // one fact, not parallel lists. Each partner still keeps one ball of
      // their own on the deal; replacing yours drops (does not complete) it.
      await c.query(
        `update next_action set status='dropped', updated_by=$1
          where subject_type='deal' and subject_id=$2 and owner_id=$1 and status='open'
          /* dealroom:drop-prior-action */`, [actor.id, s.id]);
      const action = await c.query(
        `insert into next_action (subject_type, subject_id, owner_id, due_on,
                                  description, created_by, updated_by)
         values ('deal',$1,$2,$3,$4,$2,$2) returning id
         /* dealroom:add-next-action */`,
        [s.id, actor.id, args.next_date ?? null, args.text.trim()]);
      await writeEvent(c, actor, "set-next-step", "deal", s.id, {
        field: "next_step",
        old: { next_step: prior.rows[0]?.text ?? null },
        new: { next_step: args.text.trim(), next_date: args.next_date ?? null },
        recorded_at_after_lock: true,
        idempotency_key: args.idempotency_key,
      });
      await writeEvent(c, actor, "set-next-step", "deal", s.id, {
        field: "next_date",
        old: { next_date: oldDate },
        new: { next_date: args.next_date ?? null },
        recorded_at_after_lock: true,
        idempotency_key: args.idempotency_key,
      });
      return { ok: true, deal_id: s.id, next_step_id: note.rows[0].id,
        next_action_id: action.rows[0].id,
        supersedes: prior.rows[0]?.id ?? null, created_at: note.rows[0].created_at };
    }),
  },

  "start-deal-review": {
    write: true,
    description: "Start a Team Book or national-account agenda. One partner may have one open session per workspace/account; the other partner can run their own independently.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      workspace_kind: { type: "string", enum: ["team","national_account"] },
      account_client_id: { type: "string" },
    }, required: ["idempotency_key","workspace_kind"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "start-deal-review", args, async () => {
      const accountId = args.account_client_id || null;
      if (args.workspace_kind === "national_account" && !accountId)
        throw new ToolError({ error: "account_required" });
      if (args.workspace_kind === "team" && accountId)
        throw new ToolError({ error: "team_review_has_no_account" });
      if (accountId) {
        const valid = await c.query("select 1 from client where id=$1 and client_type='national_account'", [accountId]);
        if (!valid.rows.length) throw new ToolError({ error: "not_a_national_account", account_client_id: accountId });
      }
      const existing = await c.query(
        `select id from deal_review_session where started_by=$1 and workspace_kind=$2
          and account_client_id is not distinct from $3::uuid and status='open'`,
        [actor.id, args.workspace_kind, accountId]);
      if (existing.rows.length) return { ok: true, session_id: existing.rows[0].id, already_open: true };
      const made = await c.query(
        `insert into deal_review_session (workspace_kind,account_client_id,started_by)
         values ($1,$2,$3) returning id,to_jsonb(started_at)#>>'{}' as started_at`,
        [args.workspace_kind, accountId, actor.id]);
      await writeEvent(c, actor, "start-deal-review", "actor", actor.id, {
        new: { session_id: made.rows[0].id, workspace_kind: args.workspace_kind,
          account_client_id: accountId }, idempotency_key: args.idempotency_key });
      return { ok: true, session_id: made.rows[0].id, started_at: made.rows[0].started_at };
    }),
  },

  "review-deal": {
    write: true,
    description: "Mark one deal reviewed or skipped in an open agenda. Repeating the action updates the disposition instead of double-counting it.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, session_id: { type: "string" },
      deal: { type: "string" }, disposition: { type: "string", enum: ["reviewed","skipped"] },
      note: { type: "string" },
    }, required: ["idempotency_key","session_id","deal","disposition"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "review-deal", args, async () => {
      const session = (await c.query(
        "select * from deal_review_session where id=$1 and started_by=$2 and status='open' for update",
        [args.session_id, actor.id])).rows[0];
      if (!session) throw new ToolError({ error: "review_session_not_open" });
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      const membership = await c.query(
        `select workspace_kind,account_client_id from v_deal_room_board where id=$1`, [s.id]);
      const row = membership.rows[0];
      if (!row || row.workspace_kind !== session.workspace_kind ||
          String(row.account_client_id || '') !== String(session.account_client_id || ''))
        throw new ToolError({ error: "deal_outside_review_workspace", deal_id: s.id });
      await c.query(
        `insert into deal_review_item (session_id,deal_id,disposition,note)
         values ($1,$2,$3,$4)
         on conflict (session_id,deal_id) do update
         set disposition=excluded.disposition,note=excluded.note,reviewed_at=now()`,
        [session.id, s.id, args.disposition, args.note || null]);
      return { ok: true, session_id: session.id, deal_id: s.id, disposition: args.disposition };
    }),
  },

  "end-deal-review": {
    write: true,
    description: "Complete or abandon an open agenda and return its reviewed/skipped counts. Completed sessions become the workspace's last-review clock.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, session_id: { type: "string" },
      status: { type: "string", enum: ["completed","abandoned"], default: "completed" },
      summary: { type: "string" },
    }, required: ["idempotency_key","session_id"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "end-deal-review", args, async () => {
      const status = args.status || "completed";
      const closed = await c.query(
        `update deal_review_session set status=$3,summary=$4,ended_at=now()
          where id=$1 and started_by=$2 and status='open'
          returning id,workspace_kind,account_client_id,to_jsonb(ended_at)#>>'{}' as ended_at`,
        [args.session_id, actor.id, status, args.summary || null]);
      if (!closed.rows.length) throw new ToolError({ error: "review_session_not_open" });
      const counts = (await c.query(
        `select count(*) filter (where disposition='reviewed')::int as reviewed,
                count(*) filter (where disposition='skipped')::int as skipped
           from deal_review_item where session_id=$1`, [args.session_id])).rows[0];
      await writeEvent(c, actor, "end-deal-review", "actor", actor.id, {
        new: { session_id: args.session_id, status, ...counts },
        idempotency_key: args.idempotency_key });
      return { ok: true, ...closed.rows[0], ...counts, status };
    }),
  },

  "set-market-agent": {
    write: true,
    description: "Set the stated local-market agent on a national-account deal. The readable name is stored as stated; an optional party id is linked only when already verified.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" },
      agent_name: { type: "string" }, agent_party_id: { type: "string" },
      market: { type: "string" }, source: { type: "string" },
    }, required: ["idempotency_key","deal","agent_name","source"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "set-market-agent", args, async () => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      const account = await c.query("select account_client_id from v_deal_room_board where id=$1", [s.id]);
      if (!account.rows[0]?.account_client_id)
        throw new ToolError({ error: "not_a_national_account_deal" });
      const old = (await c.query("select agent_name,market from deal_market_assignment where deal_id=$1", [s.id])).rows[0] || null;
      await c.query(
        `insert into deal_market_assignment (deal_id,agent_name,agent_party_id,market,source,set_by)
         values ($1,$2,$3,$4,$5,$6)
         on conflict (deal_id) do update set agent_name=excluded.agent_name,
           agent_party_id=excluded.agent_party_id,market=excluded.market,
           source=excluded.source,set_by=excluded.set_by,set_at=now()`,
        [s.id, args.agent_name.trim(), args.agent_party_id || null,
         args.market || null, args.source, actor.id]);
      await writeEvent(c, actor, "set-market-agent", "deal", s.id, {
        field: "market_agent", old, new: { market_agent: args.agent_name.trim(), market: args.market || null },
        idempotency_key: args.idempotency_key });
      return { ok: true, deal_id: s.id, market_agent: args.agent_name.trim() };
    }),
  },

  "set-national-account-owner": {
    write: true,
    description: "Assign Joe or Dell as the accountable partner for a national-account portfolio without changing individual deal owners.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, account_client_id: { type: "string" },
      owner: { type: "string", enum: ["joe","dell"] },
    }, required: ["idempotency_key","account_client_id","owner"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "set-national-account-owner", args, async () => {
      const account = await c.query("select id from client where id=$1 and client_type='national_account'", [args.account_client_id]);
      if (!account.rows.length) throw new ToolError({ error: "not_a_national_account" });
      const owner = (await c.query("select id from actor where slug=$1 and active", [args.owner])).rows[0];
      if (!owner) throw new ToolError({ error: "unknown_owner" });
      await c.query(
        `insert into national_account_owner (account_client_id,owner_actor_id,set_by)
         values ($1,$2,$3) on conflict (account_client_id) do update
         set owner_actor_id=excluded.owner_actor_id,set_by=excluded.set_by,set_at=now()`,
        [args.account_client_id, owner.id, actor.id]);
      return { ok: true, account_client_id: args.account_client_id, owner: args.owner };
    }),
  },

  "create-national-account": {
    write: true,
    description: "Create one national-account parent org/client and assign its accountable partner. It does not create market deals or duplicate a brand that already exists.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, name: { type: "string" },
      owner: { type: "string", enum: ["joe","dell"] }, vertical: { type: "string" },
      force_new: { type: "boolean" },
      research_evidence: RESEARCH_EVIDENCE_SCHEMA,
    }, required: ["idempotency_key","name","owner","research_evidence"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "create-national-account", args, async () => {
      const matches = await c.query(
        "select id,name from party where kind='org' and merged_into is null and lower(name)=lower($1)", [args.name.trim()]);
      if (matches.rows.length && !args.force_new)
        return { needs_confirm: true, candidates: matches.rows,
          hint: "An org with this exact name exists. Use its client or explicitly confirm a genuinely separate brand." };
      const evidence = researchEvidence(args.research_evidence,
        ["name", "company", "phone", "specialty", "market"], "create-national-account");
      const org = await c.query(
        `insert into party (kind,name,created_by,updated_by) values ('org',$1,$2,$2) returning id`,
        [args.name.trim(), actor.id]);
      await stampResearch(c, actor, org.rows[0].id, evidence);
      const ref = (await c.query("select 'C-' || lpad(nextval('ref_client_seq')::text,3,'0') as ref")).rows[0].ref;
      const client = await c.query(
        `insert into client (roster_ref,party_id,client_type,vertical,status,
                             acquisition_source,owner_id,owner_label,created_by,updated_by)
         values ($1,$2,'national_account',$3,'engaged','national_account',$4,$5,$4,$4) returning id`,
        [ref, org.rows[0].id, args.vertical || null, actor.id, actor.display]);
      const owner = (await c.query("select id from actor where slug=$1", [args.owner])).rows[0];
      await c.query(
        "insert into national_account_owner (account_client_id,owner_actor_id,set_by) values ($1,$2,$3)",
        [client.rows[0].id, owner.id, actor.id]);
      await writeEvent(c, actor, "create-national-account", "client", client.rows[0].id, {
        new: { ref, name: args.name.trim(), owner: args.owner, client_type: "national_account" },
        idempotency_key: args.idempotency_key });
      return { ok: true, account_client_id: client.rows[0].id, account_client_ref: ref,
        name: args.name.trim(), owner: args.owner };
    }),
  },

  "create-national-market-deal": {
    write: true,
    description: "Create one market transaction under a national account: reuse or create the named franchisee sub-client under the parent org, then create exactly one deal and optional stated market-agent assignment.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, account_client_id: { type: "string" },
      client_name: { type: "string" }, deal_name: { type: "string" }, market: { type: "string" },
      state: { type: "string" }, segment: { type: "string" }, agent_name: { type: "string" },
      deal_type: { type: "string", default: "startup" },
      phase: { type: "string", default: "pending" },
      research_evidence: RESEARCH_EVIDENCE_SCHEMA,
    }, required: ["idempotency_key","account_client_id","client_name","deal_name","market"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "create-national-market-deal", args, async () => {
      const account = (await c.query(
        `select c.id,c.party_id,p.name from client c join party p on p.id=c.party_id
          where c.id=$1 and c.client_type='national_account' and c.merged_into is null`,
        [args.account_client_id])).rows[0];
      if (!account) throw new ToolError({ error: "not_a_national_account" });
      const duplicate = await c.query(
        "select id,name from deal where outcome is null and lower(name)=lower($1)", [args.deal_name.trim()]);
      if (duplicate.rows.length) throw new ToolError({ error: "deal_name_exists", existing: duplicate.rows });
      let sub = (await c.query(
        `select c.id,c.roster_ref from client c join party p on p.id=c.party_id
          where p.org_id=$1 and p.merged_into is null and c.merged_into is null
            and lower(p.name)=lower($2) limit 1`, [account.party_id, args.client_name.trim()])).rows[0];
      if (!sub) {
        const evidence = researchEvidence(args.research_evidence,
          ["name", "company", "phone", "specialty", "market"], "create-national-market-deal");
        const person = await c.query(
          `insert into party (kind,name,org_id,city,state,created_by,updated_by)
           values ('person',$1,$2,$3,$4,$5,$5) returning id`,
          [args.client_name.trim(), account.party_id, args.market.trim(), args.state || null, actor.id]);
        await stampResearch(c, actor, person.rows[0].id, evidence);
        const ref = (await c.query("select 'C-' || lpad(nextval('ref_client_seq')::text,3,'0') as ref")).rows[0].ref;
        const made = await c.query(
          `insert into client (roster_ref,party_id,client_type,vertical,status,
                               acquisition_source,owner_id,owner_label,created_by,updated_by)
           values ($1,$2,'franchise',$3,'active_deal','national_account',$4,$5,$4,$4) returning id`,
          [ref, person.rows[0].id, args.segment || null, actor.id, actor.display]);
        sub = { id: made.rows[0].id, roster_ref: ref };
      }
      const deal = await c.query(
        `insert into deal (client_id,name,deal_type,phase,segment,city,lane,owner,created_by,updated_by)
         values ($1,$2,$3,$4,$5,$6,'national',$7,$8,$8) returning id`,
        [sub.id, args.deal_name.trim(), args.deal_type || 'startup', args.phase || 'pending',
         args.segment || null, args.market.trim(), actor.slug, actor.id]);
      await c.query(
        `insert into deal_participant (deal_id,actor_id,role,set_by)
         values ($1,$2,'lead',$2)`, [deal.rows[0].id, actor.id]);
      if (args.agent_name?.trim()) await c.query(
        `insert into deal_market_assignment (deal_id,agent_name,market,source,set_by)
         values ($1,$2,$3,'partner stated in Deal Room',$4)`,
        [deal.rows[0].id, args.agent_name.trim(), args.market.trim(), actor.id]);
      await writeEvent(c, actor, "create-national-market-deal", "deal", deal.rows[0].id, {
        new: { name: args.deal_name.trim(), client_ref: sub.roster_ref,
          account_client_id: account.id, market: args.market.trim(), agent_name: args.agent_name || null },
        idempotency_key: args.idempotency_key });
      return { ok: true, deal_id: deal.rows[0].id, client_ref: sub.roster_ref,
        account_client_id: account.id };
    }),
  },

  "revert-deal-field": {
    write: true,
    description: "Undo one Deal Room field change only when it is still the latest change to that exact field. Newer partner work is never overwritten by undo.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, event_id: { type: "string" },
    }, required: ["idempotency_key","event_id"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "revert-deal-field", args, async () => {
      const scope = personalScopeForActor(actor);
      if (scope.status === "error") throw new ToolError({ error: scope.error });
      const row = (await c.query(
        `select id,subject_id,field,old_value,new_value from event
          where id=$1 and subject_type='deal' and organization_tenant_id=$2
            and (personal_scope='none' or personal_scope=$3)`,
        [args.event_id, organizationTenantForActor(actor),
          scope.status === "personal" ? `${scope.sponsor}-personal` : "none"])).rows[0];
      if (!row || !DEAL_ROOM_FIELDS.includes(row.field) ||
          !Object.prototype.hasOwnProperty.call(row.old_value || {}, row.field))
        throw new ToolError({ error: "event_not_revertible" });
      await lockDealField(c, row.subject_id, row.field);
      const latest = (await c.query(
        `select id from event where subject_type='deal' and subject_id=$1 and field=$2
          order by recorded_at desc,id desc limit 1`, [row.subject_id,row.field])).rows[0];
      if (latest?.id !== row.id)
        throw new ToolError({ error: "newer_change_exists", hint: "Open the deal and review the newer value before changing it." });
      const oldValue = row.old_value?.[row.field] ?? null;
      const applied = await applyDealRoomField(c, actor, row.subject_id, row.field, oldValue,
        args.idempotency_key, "revert-deal-field");
      return { ok: true, deal_id: row.subject_id, field: row.field,
        reverted_event_id: row.id, ...applied };
    }),
  },

  "resolve-conflict": {
    write: true,
    description: "Resolve an open Deal Room cell conflict only while its recorded field value is still current, by applying value a or b through the normal field patch/event path.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, conflict_id: { type: "string" },
      winner: { type: "string", enum: ["a", "b"] },
    }, required: ["idempotency_key", "conflict_id", "winner"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "resolve-conflict", args, async () => {
      if (!['a', 'b'].includes(args.winner)) throw new ToolError({ error: "invalid_winner", allowed: ["a", "b"] });
      const found = await c.query(
        `select id, deal_id, field, value_a, value_b, event_a, status
           from deal_conflict where id=$1 for update /* dealroom:get-conflict */`,
        [args.conflict_id],
      );
      if (!found.rows.length) throw new ToolError({ error: "not_found", table: "deal_conflict", id: args.conflict_id });
      const conflict = found.rows[0];
      if (conflict.status !== "open") throw new ToolError({ error: "conflict_already_resolved", conflict_id: conflict.id });
      await lockDealField(c, conflict.deal_id, conflict.field);
      if (await latestFieldConflict(c, conflict.deal_id, conflict.field, conflict.event_a))
        throw new ToolError({ error: "newer_change_exists", conflict_id: conflict.id,
          hint: "This field changed again after the conflict appeared. Open the deal and review its current value before deciding." });
      const value = args.winner === "a" ? conflict.value_a : conflict.value_b;
      const applied = await applyDealRoomField(c, actor, conflict.deal_id, conflict.field,
        value, args.idempotency_key, "resolve-conflict");
      await c.query(
        `update deal_conflict set status='resolved', resolved_by=$2, winner=$3,
             resolved_at=now() where id=$1 /* dealroom:resolve-conflict */`,
        [conflict.id, actor.id, args.winner],
      );
      return { ok: true, conflict_id: conflict.id, deal_id: conflict.deal_id,
        field: conflict.field, winner: args.winner, ...applied };
    }),
  },

  "resolve-candidate": {
    write: true,
    description: "Human gate for one capture proposal. Rejecting only skips it. Accepting invokes its mapped live verb as the confirming partner, then confirms the candidate only after that write returns a real record reference.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, candidate_id: { type: "string" },
      accept: { type: "boolean" }, note: { type: "string" },
    }, required: ["idempotency_key", "candidate_id", "accept"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "resolve-candidate", args, async () => {
      if (typeof args.accept !== "boolean") throw new ToolError({ error: "accept_required" });
      const found = await c.query(
        `select id, kind, payload, status, resulting_ref
           from capture_candidate where id=$1 for update /* capture:resolve-read */`,
        [args.candidate_id]);
      if (!found.rows.length)
        throw new ToolError({ error: "not_found", table: "capture_candidate", id: args.candidate_id });
      const candidate = found.rows[0];
      if (candidate.status !== "pending") return { ok: true, candidate_id: candidate.id,
        already: candidate.status, ref: candidate.resulting_ref || null,
        note: "already dispositioned; nothing changed" };

      if (!args.accept) {
        await c.query(
          `update capture_candidate
              set status='skipped', resolved_by=$2, resolution_note=$3, resolved_at=now()
            where id=$1 /* capture:resolve-skip */`,
          [candidate.id, actor.id, args.note || null]);
        return { ok: true, candidate_id: candidate.id, status: "skipped", ref: null };
      }

      const verbByKind = {
        phase_move: "patch-deal-field",
        next_step: "set-next-step",
        new_deal: "new-deal",
        activity: "log-activity",
        meeting_record: "log-activity",
      };
      const verb = verbByKind[candidate.kind];
      if (!verb) throw new ToolError({ error: "unknown_candidate_kind", kind: candidate.kind });
      const innerArgs = { ...candidate.payload, idempotency_key: `capture:${candidate.id}` };
      if (candidate.kind === "meeting_record") innerArgs.kind = "meeting";
      const result = await executeRegisteredTool(c, actor, verb, innerArgs);
      if (!result || result.ok === false) throw new ToolError(result || { error: "inner_write_failed" });
      const ref = candidate.kind === "phase_move" ? result.deal_id
        : candidate.kind === "next_step" ? result.next_step_id
        : candidate.kind === "new_deal" ? result.deal_id
        : result.activity_id;
      if (!ref) throw new ToolError({ error: "inner_write_missing_ref", verb });
      await c.query(
        `update capture_candidate
            set status='confirmed', resolved_by=$2, resolution_note=$3,
                resulting_ref=$4, resolved_at=now()
          where id=$1 /* capture:resolve-confirm */`,
        [candidate.id, actor.id, args.note || null, String(ref)]);
      return { ok: true, candidate_id: candidate.id, status: "confirmed", ref: String(ref),
        verb, result };
    }),
  },

  "resolve-post-call-candidate": {
    write: true,
    description: "Human-only resolution for one Call Mode proposal. assigned_action creates a real next action for the explicit Joe or Dell assignee. email_draft only confirms metadata and its local body hash: it never creates or sends an email or Outlook draft.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, candidate_id: { type: "string" },
      accept: { type: "boolean" }, note: { type: "string" },
    }, required: ["idempotency_key", "candidate_id", "accept"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "resolve-post-call-candidate", args, async () => {
      if (!canExercisePartnerAuthority(actor)) throw new ToolError({ error: "human_only",
        hint: "this verb requires a partner or a server-verified Codex/Claude session sponsored by one" });
      if (typeof args.accept !== "boolean") throw new ToolError({ error: "accept_required" });
      const found = await c.query(
        `select id,kind,deal_id,assignee_slug,action_description,due_on,recipient_party_id,
                recipient_ref,email_subject,body_sha256,status,resulting_ref
           from capture_post_call_candidate where id=$1 for update
           /* capture:resolve-post-call-read */`, [args.candidate_id]);
      if (!found.rows.length)
        throw new ToolError({ error: "not_found", table: "capture_post_call_candidate", id: args.candidate_id });
      const candidate = found.rows[0];
      if (candidate.status !== "pending") return { ok: true, candidate_id: candidate.id,
        already: candidate.status, ref: candidate.resulting_ref || null,
        note: "already dispositioned; nothing changed" };
      if (!args.accept) {
        await c.query(
          `update capture_post_call_candidate
              set status='skipped',resolved_by=$2,resolution_note=$3,resolved_at=now()
            where id=$1 /* capture:resolve-post-call-skip */`,
          [candidate.id, actor.id, args.note || null]);
        return { ok: true, candidate_id: candidate.id, status: "skipped", ref: null };
      }
      if (candidate.kind === "email_draft") {
        await c.query(
          `update capture_post_call_candidate
              set status='confirmed',resolved_by=$2,resolution_note=$3,resolved_at=now()
            where id=$1 /* capture:resolve-post-call-email */`,
          [candidate.id, actor.id, args.note || null]);
        await writeEvent(c, actor, "resolve-post-call-candidate", "deal", candidate.deal_id, {
          field: "email_draft_metadata",
          new: { candidate_id: candidate.id, recipient_ref: candidate.recipient_ref,
            subject: candidate.email_subject, body_sha256: candidate.body_sha256, approved: true },
          idempotency_key: args.idempotency_key,
        });
        return { ok: true, candidate_id: candidate.id, status: "confirmed", ref: null,
          local_only: true, send: false };
      }
      if (candidate.kind !== "assigned_action")
        throw new ToolError({ error: "unknown_candidate_kind", kind: candidate.kind });
      const assignee = await c.query(
        "select id from actor where slug=$1 and active /* capture:resolve-post-call-assignee */",
        [candidate.assignee_slug]);
      if (!assignee.rows.length)
        throw new ToolError({ error: "assignee_not_provisioned", assignee: candidate.assignee_slug });
      const action = await c.query(
        `insert into capture_post_call_action (candidate_id,deal_id,owner_id,due_on,description,accepted_by)
         values ($1,$2,$3,$4,$5,$6) returning id
         /* capture:resolve-post-call-action */`,
        [candidate.id, candidate.deal_id, assignee.rows[0].id, candidate.due_on || null,
         candidate.action_description, actor.id]);
      await c.query(
        `update capture_post_call_candidate
            set status='confirmed',resolved_by=$2,resolution_note=$3,resulting_ref=$4,resolved_at=now()
          where id=$1 /* capture:resolve-post-call-confirm */`,
        [candidate.id, actor.id, args.note || null, String(action.rows[0].id)]);
      await writeEvent(c, actor, "resolve-post-call-candidate", "deal", candidate.deal_id, {
        field: "next_action", new: { next_action_id: action.rows[0].id,
          assignee: candidate.assignee_slug, description: candidate.action_description,
          due_on: candidate.due_on || null }, idempotency_key: args.idempotency_key,
      });
      return { ok: true, candidate_id: candidate.id, status: "confirmed",
        ref: String(action.rows[0].id), assignee: candidate.assignee_slug };
    }),
  },
}, "deal-room-inline");

// The deploy-gap pair (2026-08-08, Joe's reconnect complaint). call-verb's
// dispatch lives in mcp.js callTool (interception, so profile checks apply to
// the INNER verb by recursion); these registry entries exist so both tools are
// listed with schemas. list-verbs is the discovery half: a session whose
// cached tool list predates a deploy asks the live registry what exists now.
registerTools({
  "list-verbs": {
    description: "The LIVE verb registry — names, descriptions, write flags, input schemas — straight from the deployed Worker, bypassing the connector's cached tool list. Use when a verb you expect is missing from your tool list (a deploy since this session connected): find it here, then invoke it through call-verb without any reconnect.",
    inputSchema: { type: "object", properties: {
      filter: { type: "string", description: "case-insensitive substring match over verb name AND description" },
      names_only: { type: "boolean", description: "names plus first-sentence descriptions, no schemas. Composes with filter." } } },
    handler: async (c, actor, args) => {
      const needle = args.filter ? String(args.filter).toLowerCase() : null;
      // MATCH ON BEHAVIOR, not just the label (rule 49c627cc): a session hunting
      // for what serves a job often does not know the verb's NAME yet, so the
      // filter reads descriptions too.
      const names = Object.keys(TOOLS).sort()
        .filter(n => !needle
          || n.toLowerCase().includes(needle)
          || String(TOOLS[n].description || "").toLowerCase().includes(needle));
      if (args.names_only) {
        return { ok: true, count: names.length, names_only: true,
                 verbs: names.map(n => ({ name: n, write: !!TOOLS[n].write,
                   description: String(TOOLS[n].description || "").split(/(?<=\.)\s/)[0].slice(0, 200) })) };
      }
      return { ok: true, count: names.length,
               verbs: names.map(n => ({ name: n, write: !!TOOLS[n].write,
                 description: (TOOLS[n].description || "").slice(0, 200),
                 inputSchema: TOOLS[n].inputSchema || null })) };
    },
  },
  "export-email-domains": {
    description: "The email DOMAINS on record for clients and leads — never the addresses. Aggregated in SQL, so an address cannot leave through this verb even by accident. WHY IT EXISTS (decision 2026-08-19): ops/fetch-allowlist.py builds the egress guard's allowlist from these two views, and on a second machine it was the ONLY thing that needed a direct database connection — the one credential standing between Dell's Mac and needing none at all. This returns strictly less than that connection does (two columns, aggregated, no write, no other view) while keeping every read attributable through the machine door. The guard's POLICY — freemail suffixes, institutional TLDs, hostname shape — deliberately stays in the caller: this verb is a data read and must never become the place that decides what the guard trusts.",
    inputSchema: { type: "object", properties: {} },
    handler: async (c) => {
      // Constant identifiers, never caller input — these two names are the
      // whole surface of this verb and are not parameterisable on purpose.
      const SOURCES = [["v_export_clients", '"Email"'], ["v_export_leads", '"Email"']];
      const domains = new Set();
      // PER-SOURCE, not just the union. The caller reports "N seen, M kept"
      // for each view, and folding the two together here would cost it that
      // line — a report that cannot say WHICH book a domain came from is the
      // kind of small loss that gets noticed only when something is wrong.
      const by_source = {}, counts = {}, notes = [];
      for (const [view, col] of SOURCES) {
        try {
          const r = await c.query(
            `select distinct lower(split_part(${col}, '@', 2)) as domain
               from ${view} where ${col} like '%@%'`);
          const seen = [];
          for (const row of r.rows) {
            const d = String(row.domain || "").trim().replace(/^\.+|\.+$/g, "").toLowerCase();
            if (!d) continue;
            seen.push(d);
            domains.add(d);
          }
          by_source[view] = seen.sort();
          counts[view] = seen.length;
        } catch (exc) {
          // One unreadable view must not cost the caller the other one — the
          // same tolerance ops/fetch-allowlist.py has always had. A skipped
          // source is REPORTED rather than folded silently into a smaller
          // answer, because a quietly short allowlist looks exactly like a
          // correct one and the guard would refuse real client domains.
          notes.push(`${view}: skipped (${exc && exc.name ? exc.name : "error"})`);
        }
      }
      if (!Object.keys(counts).length)
        throw new ToolError({ error: "no_export_view_readable", notes,
          hint: "Neither export view could be read. Treat this as NO ANSWER and keep the previous allowlist — an empty result here is indistinguishable from 'this book has no clients', and writing it out would silently strip every client domain the guard trusts." });
      return { ok: true, domains: [...domains].sort(), by_source, counts, notes };
    },
  },
  "record-defect": {
    write: true,
    description: "File ONE defect: a claim the system made that was not true, with what WAS true beside it. This is the record layer's only RETROSPECTIVE mechanism — every other safeguard here (a hook, a gate, a registry) is prospective and guesses in advance at what will go wrong; this one gets better as failures accumulate. NOT record-finding: a finding is something learned about a client, a commit or a platform, while a defect is something the system itself got wrong. NOT a loop either, and that distinction is the reason this verb exists — a loop is a TO-DO, so it gets closed and disappears, while a defect must ACCUMULATE to be worth anything. The four load-bearing fields are claimed / actual / source_unread / rule_violated, and claimed and actual are both required and must actually differ: a row that does not state a contradiction is a note, not a defect. detected_by is required and closed-vocabulary because it is the most diagnostic field in the table — a log where every row reads 'human' is a log saying the self-checks do not work, and that is only visible if it is counted. FILE ONE THE MOMENT IT IS FOUND, including when the session filing it is the one that erred; a defect caught and not recorded is the failure this whole mechanism exists to stop. Read them back through v_defect and v_defect_class; standing-context surfaces the class counts at session start.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      defect_class: { type: "string", description: "the KIND of failure, lowercase, kebab-ish — e.g. 'dated-artifact-read-as-present-state', 'success-signal-from-the-wrong-function'. Free text on purpose: the classes are not known in advance and a fixed vocabulary would force every new failure into an old bucket. Reuse an existing class where one fits — call this verb's read side (v_defect_class) or catch-me-up first — because the count per class is the entire point." },
      claimed: { type: "string", description: "REQUIRED. What the system asserted, in the words it asserted it." },
      actual: { type: "string", description: "REQUIRED. What was true. Must differ from claimed — the pair is what makes the row reviewable later." },
      source_unread: { type: "string", description: "the artifact that would have shown it and was not opened, or was opened partially. This is the field that turns a defect log into a reading list." },
      rule_violated: { type: "string", description: "the rule this broke — the 8-character short id is fine and is what a session can actually quote; the read view resolves it to the rule's statement." },
      detected_by: { type: "string", enum: ["human","self","gate","check","peer_review","downstream"],
        description: "who caught it. 'human' means a partner had to find it, which is the most expensive kind and the one worth counting." },
      occurred_on: { type: "string", description: "date it happened (ISO); defaults today. Pass it when filing an OLD defect — a backfilled row dated today would make the trend line lie." },
      session_key: { type: "string" },
      cost_note: { type: "string", description: "what it cost, in whatever unit is true: tokens, a wrong deliverable, a partner's evening." } },
      required: ["idempotency_key","defect_class","claimed","actual","detected_by"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "record-defect", args, async () => {
      const present = await c.query("select to_regclass('public.defect') is not null as t");
      if (!present.rows[0].t)
        throw new ToolError({ error: "migration_not_applied", migration: "0103_defect_log",
          hint: "the defect log needs 0103. Apply it (`~/carr-system/run.sh migrate --apply --yes`) and retry. NOTHING was written." });
      const cls = String(args.defect_class || "").trim().toLowerCase().replace(/\s+/g, " ");
      const claimed = String(args.claimed || "").trim();
      const actualTxt = String(args.actual || "").trim();
      if (!cls) throw new ToolError({ error: "defect_class_required",
        hint: "name the KIND of failure, not this one instance — the count per class is what makes the log useful" });
      if (!claimed || !actualTxt || claimed.toLowerCase() === actualTxt.toLowerCase())
        throw new ToolError({ error: "no_contradiction_stated", claimed, actual: actualTxt,
          hint: "a defect states what was CLAIMED and what was TRUE, and they must differ. If they do not, this is a note — log-decision or add-loop is its home, not the defect log." });
      const r = await c.query(
        `insert into defect (occurred_on, defect_class, claimed, actual, source_unread,
                             rule_violated, detected_by, session_key, cost_note, created_by)
         values (coalesce($1::date, current_date), $2,$3,$4,$5,$6,$7,$8,$9,$10)
         returning id, occurred_on`,
        [args.occurred_on || null, cls, claimed, actualTxt,
         args.source_unread || null, args.rule_violated || null, args.detected_by,
         args.session_key || null, args.cost_note || null, actor.id]);
      // The event is what makes a defect show up in catch-me-up without a second read
      // surface — the same reason record-finding writes one.
      await writeEvent(c, actor, "record-defect", "defect", r.rows[0].id, {
        field: cls,
        new: { detected_by: args.detected_by, rule_violated: args.rule_violated || null,
               source_unread: args.source_unread || null },
        agent_rationale: claimed.slice(0, 300),
        idempotency_key: args.idempotency_key });
      const cnt = await c.query(
        "select occurrences, caught_by_human, first_seen from v_defect_class where defect_class=$1", [cls]);
      const row = cnt.rows[0] || {};
      // A date rendered through JS's default toString comes out as
      // "Tue Aug 04 2026 00:00:00 GMT-0500 (Central Daylight Time)", which is noise in
      // a sentence a session is meant to read at a glance (rule 80def9d2).
      const asDate = v => (v instanceof Date ? v.toISOString().slice(0, 10) : String(v).slice(0, 10));
      return { ok: true, defect_id: r.rows[0].id, defect_class: cls,
               occurred_on: asDate(r.rows[0].occurred_on),
               class_occurrences: row.occurrences, class_caught_by_human: row.caught_by_human,
               class_first_seen: row.first_seen ? asDate(row.first_seen) : null,
               note: row.occurrences > 1
                 ? `this class has now failed ${row.occurrences} times since ${asDate(row.first_seen)} — ` +
                   `${row.caught_by_human} of them caught by a human. A repeat class is a design ` +
                   "problem, not a lapse: say so rather than filing the next one quietly."
                 : "first of its class." };
    }),
  },
  "find-precedent": {
    write: false,
    description: "\"What precedent exists for a fork shaped like this?\" — searches recorded RULING HISTORY plus activated typed precedents. Results carry record_kind: settled_ruling is a recorded decision; typed_precedent is governed guidance and must not be presented as a settled decision. Search before re-arguing a settled point or declaring a fork open. Matches titles, partner wording, and reasoning with trigram similarity; two or three concrete nouns beat a sentence. NOT for doctrine (search-doctrine), records (find), or open work (loop-board). Read-only.",
    inputSchema: { type: "object", properties: {
      query: { type: "string", description: "the fork in a few concrete words — 'party merge survivor', 'markdown vs database', 'national account modelling'. Two or three specific nouns beat a full sentence: this is trigram matching, not a question answerer." },
      limit: { type: "integer", description: `rulings returned, capped at ${PRECEDENT_CAP} (default 8)` },
      since: { type: "string", description: "ISO date; only rulings on or after it. Use when you want the CURRENT position rather than the whole history — an older ruling may have been superseded." } },
      required: ["query"] },
    handler: async (c, _a, args) => {
      const q = String(args.query || "").trim();
      if (!q) throw new ToolError({ error: "query_required",
        hint: "name the fork in a few concrete words" });
      const cap = Math.max(1, Math.min(PRECEDENT_CAP, args.limit || 8));
      const present = await c.query("select to_regclass('public.v_precedent') is not null as t");
      if (!present.rows[0].t)
        throw new ToolError({ error: "migration_not_applied", migration: "0106_precedent_and_point_in_time",
          hint: "precedent search needs 0106. Apply it (`~/carr-system/run.sh migrate --apply --yes`) and retry." });
      // Typed guidance is deliberately additive to ruling history, not a replacement for it.
      // 0168 may not yet exist on a local or older environment, so its presence and active
      // lifecycle state are both required before it can contribute searchable precedents.
      const registryPresent = await c.query(
        "select to_regclass('ops.v_guidance_registry_state') is not null as t");
      let guidanceRegistryActive = false;
      if (registryPresent.rows[0].t) {
        const registryState = await c.query(
          "select state from ops.v_guidance_registry_state limit 1");
        guidanceRegistryActive = registryState.rows[0]?.state === "active";
      }
      // WORD SIMILARITY, NOT similarity(). This was built with similarity() first and it
      // returned ZERO for every realistic query, because similarity() compares two whole
      // trigram sets: a three-word query against a thousand-character ruling scores near
      // zero no matter how exactly those words appear in it. word_similarity() scores the
      // query against the best-matching WINDOW of the document, which is the actual
      // question. Measured on the live corpus: similarity() found 0 rulings for "markdown
      // database" and word_similarity() found the database-first ruling at 0.58.
      //
      // The second branch is the one that catches an exact multi-word phrase whose words
      // are far apart in the text — every word present, order and distance irrelevant.
      const words = q.split(/\s+/).filter(w => w.length > 2);
      const precedentSource = guidanceRegistryActive
        ? `(select decision_id, entry_date, title, human_quote, agent_rationale, author,
                   provenance::text as provenance, haystack,
                   'settled_ruling'::text as record_kind from v_precedent
            union all
            select decision_id, entry_date, title, human_quote, agent_rationale, author,
                   provenance::text as provenance, haystack,
                   'typed_precedent'::text as record_kind from ops.v_guidance_precedent) as precedent_history`
        : `(select decision_id, entry_date, title, human_quote, agent_rationale, author,
                   provenance, haystack, 'settled_ruling'::text as record_kind
              from v_precedent) as precedent_history`;
      const r = await c.query(
        `select decision_id, entry_date, title, human_quote, agent_rationale, author,
                provenance, record_kind, word_similarity($1, haystack) as score
           from ${precedentSource}
          where (word_similarity($1, haystack) >= 0.3
                 or ($2::text[] <> '{}' and haystack ilike all(
                       select '%' || w || '%' from unnest($2::text[]) w)))
            and ($3::date is null or entry_date >= $3::date)
          order by word_similarity($1, haystack) desc, entry_date desc
          limit $4`,
        [q, words, args.since || null, cap]);
      // THE RATIONALE IS CUT, NOT DROPPED. A ruling's reasoning runs to paragraphs and eight
      // of them would swamp the caller; the id is here so the full text is one read away.
      const rulings = r.rows.map(x => ({
        decision_id: x.decision_id,
        date: x.entry_date,
        title: x.title,
        // The partner's own words first: that is the binding half of a ruling, and the
        // summary is the session's paraphrase of it.
        human_said: x.human_quote || null,
        reasoning_excerpt: (x.agent_rationale || "").slice(0, 400)
          + ((x.agent_rationale || "").length > 400 ? " …" : ""),
        author: x.author,
        provenance: x.provenance || null,
        record_kind: x.record_kind || "settled_ruling",
        match_score: Number(x.score?.toFixed?.(3) ?? x.score),
      }));
      return { ok: true, query: q, count: rulings.length, rulings,
        note: rulings.length
          ? "Only settled_ruling results are settled decisions. typed_precedent results are " +
            "governed guidance patterns, not proof that Joe or Dell ruled on this fork. Read " +
            "the kind, date, provenance, and full reasoning before relying on any result."
          : "No precedent matches. That is NOT proof none exists — this is trigram matching over " +
            "the words actually used, so try the partner's likely phrasing and a couple of " +
            "different concrete nouns before concluding the fork is unsettled." };
    },
  },
  "state-as-of": {
    write: false,
    description: "\"What did the record SAY about this on date X?\" — replays the field-level change log for one record up to an instant. The material has been there since the first migration (every write stores the old and new value of the field it touched) and nothing exposed it. USE IT INSTEAD OF ASKING A PARTNER WHETHER SOMETHING IS STILL TRUE: the standing rule that a session must stop and ask before drafting off notes older than about sixty days exists because nothing could answer that mechanically, and this can. Also the way to settle which of two disagreeing surfaces went stale — knowing what we believed WHEN is the whole of that question. Every field comes back with how many times it changed AFTER the cutoff and what it says now, so a caller can always tell \"true then and still true\" from \"true then, moved since\". Read-only, and it reconstructs rather than restores: nothing is written back.",
    inputSchema: { type: "object", properties: {
      ref: { type: "string", description: "the record — C-127 / L-204 / V-CPA-006 / P-0948 or an exact deal name" },
      as_of: { type: "string", description: "the instant to reconstruct (ISO date or timestamp). Defaults to now, which returns the current state with its change counts — useful on its own for seeing what has moved recently." } },
      required: ["ref"] },
    handler: async (c, _a, args) => {
      const present = await c.query("select to_regclass('public.v_field_history') is not null as t");
      if (!present.rows[0].t)
        throw new ToolError({ error: "migration_not_applied", migration: "0106_precedent_and_point_in_time",
          hint: "point-in-time reconstruction needs 0106. Apply it and retry." });
      const s = await resolveSubject(c, args.ref);
      const at = args.as_of || null;
      const r = await c.query(
        `select field, value_at, changed_at, changed_by, verb, later_changes, current_value
           from state_as_of($1, $2, coalesce($3::timestamptz, now()))`,
        [s.type, s.id, at]);
      if (!r.rows.length)
        return { ok: true, ref: args.ref, subject_type: s.type, as_of: at || "now",
                 fields: [], count: 0,
                 note: "This record has NO field-level history at or before that instant. That " +
                       "means nothing was recorded about it then, not that it did not exist — a " +
                       "record created by an import carries its creation but no field changes." };
      const fields = r.rows.map(x => ({
        field: x.field,
        value_then: x.value_at,
        set_at: x.changed_at,
        set_by: x.changed_by,
        by_verb: x.verb,
        changes_since: x.later_changes,
        value_now: x.later_changes > 0 ? x.current_value : undefined,
        still_current: x.later_changes === 0,
      }));
      const moved = fields.filter(f => !f.still_current);
      return { ok: true, ref: args.ref, subject_type: s.type, as_of: at || "now",
               count: fields.length, fields_changed_since: moved.length, fields,
               note: moved.length
                 ? `${moved.length} of ${fields.length} field(s) have moved since that instant — ` +
                   "each of those carries value_now beside value_then. Anything drafted off a " +
                   "note from that date is out of step on exactly those fields and no others, " +
                   "which is a narrower and more useful answer than 'the notes are old'."
                 : "Nothing about this record has changed since that instant, so a note from " +
                   "that date is still accurate on every field the record tracks." };
    },
  },
  "call-verb": {
    // write:true here decides PROFILE and PERMISSION treatment, NOT the database
    // connection — and the old comment claiming it "rides the writer path" was
    // wrong in a way that cost a real investigation on 2026-08-21. mcp.js
    // intercepts this verb by name and re-enters the dispatcher as the INNER
    // verb, so the inner verb's own write flag picks reader or writer. A
    // call-verb wrapping a read verb lands on the READER connection and fails
    // exactly as the direct call would; it did, on the doctrine-search outage.
    write: true,
    description: "Invoke ANY live verb by name — the deploy-gap passthrough. A freshly deployed verb is callable here the moment the Worker ships, no connector reconnect needed; its first-class tool appears at your next session start. Takes {verb, args} where args is the inner verb's own argument object (including its idempotency_key for writes). All profile and permission checks apply to the inner verb exactly as a direct call.",
    inputSchema: { type: "object", properties: {
      verb: { type: "string" },
      args: { type: "object" } },
      required: ["verb"] },
    handler: async () => {
      // Reachable only if a caller bypasses mcp.js dispatch (local-verb.mjs).
      throw new ToolError({ error: "dispatcher_only",
        hint: "call-verb is intercepted in mcp.js callTool; invoke the inner verb directly here" });
    },
  },
}, "deploy-gap-inline");

// Doctrine store verbs (P2, decision 82a2fb62) — same envelope, same contracts.
registerTools(doctrineTools({ withEnvelope, writeEvent, ToolError }), "doctrine");
registerTools(systemWorkTools(), "system-work-census");
registerTools(boardAnswerTools({ withEnvelope, writeEvent }), "board-answers");
registerTools(scheduleBoardTools(), "schedule-board");

// WR-AI-006: curation proposals are machine-callable; approval and retirement
// remain human-only inside their handlers and the dispatcher boundary.
registerTools(situationRetrievalTools({ withEnvelope, writeEvent, ToolError }), "situation-retrieval");

// Bounded investigation control plane (0098): deterministic signals, one
// reasoning owner, evidence-only worker packets, explicit branch termination.
registerTools(investigationTools({ withEnvelope, writeEvent, ToolError }), "investigation");

// WR-000112: the Doc conversation store. The append is authority-only because
// ops.append_doc_conversation_turn is granted to carr_authority alone; the read
// runs on the writer connection because that is the only one that installs the
// acting-actor context ops.doc_conversation_facts is handed.
registerTools(docConversationTools({ withEnvelope, writeEvent, ToolError }), "doc-conversation");
registerTools(docSuggestionTools({ withEnvelope, writeEvent, ToolError }), "doc-suggestions");
registerTools(docActivityTools({ ToolError }), "doc-activity");
registerTools(whatsNewTools({ withEnvelope, executeRegisteredTool, ToolError }), "whats-new");

// V5-UX-B11: non-recording shared Meeting Mode. The store's definer functions
// own every transition; an accepted action points at an existing write verb in
// this registry, looked up at call time, and never runs it in-process.
registerTools(meetingModeTools({ withEnvelope, writeEvent, ToolError,
  lookupTool: name => (Object.hasOwn(TOOLS, name) ? TOOLS[name] : null) }), "meeting-mode");

// WR-000113: the R03 notification feed and its acknowledgement receipt.
// acknowledge-notification writes ops.notification_read and nothing else, which
// is why a session may never report it as having moved a task.
registerTools(notificationTools({ withEnvelope, writeEvent, ToolError }), "notifications");
registerTools(deliveryCadenceA05Tools({ withEnvelope, writeEvent, ToolError }), "delivery-cadence-a05");

// WR-000117: the session-identity read pair. Both verbs are READS on the writer
// connection -- ops.session_identity_facts and ops.session_dispatch_history
// derive the acting actor from a context only the writer path installs -- and
// neither writes a row anywhere, so neither takes the envelope or the event
// helper.
registerTools(sessionIdentityTools({ ToolError }), "session-identity");

// WR-000119: the dispatch spine write pair. The OPPOSITE declaration to the
// read pair above -- both of these carry write: true as well as the writer
// connection, because ops.record_dispatch_link and ops.acknowledge_dispatch
// insert and 0531 makes them volatile, so a read-only transaction would fail
// them. Both take the envelope and the event helper for that reason.
registerTools(dispatchSpineTools({ withEnvelope, writeEvent, ToolError }), "dispatch-spine");

export function docOutcomeCardsProjection(facts, ErrorType = ToolError) {
  const states = new Set(["queued", "active", "waiting", "failed", "unknown", "verified"]);
  if (!facts || facts.ok !== true || facts.schema_version !== "doc-outcome-cards.v2" || !Array.isArray(facts.cards))
    throw new ErrorType({ error: facts?.reason_id || "doc_outcome_cards_unavailable" });
  for (const card of facts.cards) {
    if (!card || !states.has(card.routing_state) || card.session_entry?.auto_launch !== false || !Object.hasOwn(card, "native_task_id"))
      throw new ErrorType({ error: "doc_outcome_cards_invalid_projection" });
  }
  return { ok: true, schema_version: facts.schema_version, as_of: facts.as_of, correlation_version: facts.correlation_version,
    more: facts.more === true, next_cursor: facts.next_cursor ?? null, cards: facts.cards };
}

registerTools({
  "read-doc-outcome-cards": {
    writerConnection: true,
    description: "Read actor- and tenant-scoped, page-atomic DoctorCRE outcome cards. The producer derives joins and availability; it never launches a native task.",
    inputSchema: { type: "object", additionalProperties: false, properties: { cursor: { type: "string", minLength: 1, maxLength: 1000 }, limit: { type: "integer", minimum: 1, maximum: 50 } }, required: [] },
    handler: async (c, _actor, args) => docOutcomeCardsProjection((await c.query("select ops.read_doc_outcome_cards_successor($1::text,$2::integer) as facts", [args.cursor ?? null, args.limit ?? null])).rows[0]?.facts, ToolError),
  },
}, "doc-outcome-cards");

// One fixed ordered AI-capability portfolio over canonical Work Requests.
registerTools(capabilityProgramTools({ withEnvelope, writeEvent, ToolError }), "capability-program");

// Evidence-backed implementation form, linked to canonical Work Requests.
registerTools(workShapeTools({ withEnvelope, writeEvent, ToolError }), "work-shape");

// Program 6: sourced additive capture and a safe card only. No lifecycle verbs.
registerTools(workRequestIntakeTools({ withEnvelope, writeEvent, ToolError }), "work-request-intake");
registerTools(workPortfolioTools({ withEnvelope, writeEvent, ToolError }), "work-portfolio");

// Pure workbook-derived lease economics. No database, model, or write path.
registerTools(leaseTermComparisonTools({ ToolError }), "lease-term-comparison");

// The partner room (Idea 78): shared AI-to-AI transcript both Macs poll; raw
// turns, server-derived attribution, human-watchable. See src/partner-room.js.
registerTools(partnerRoomTools({ withEnvelope, ToolError }), "partner-room");
registerTools(agentProfileTools({ withEnvelope, writeEvent, ToolError }), "agent-profile");
registerTools(botBriefTools({ ToolError, assertNoCallerAuthorityFields }), "bot-brief");
registerTools(evidenceActivationTools({ withEnvelope, ToolError }), "evidence-activation");
// DoctorCRE v5 V5-UX-C02/C06: the resource-metering read contract and the
// one write door the local, credential-less collector uses. See
// src/resource-observation.v5.js.
registerTools(resourceObservationTools({ withEnvelope, ToolError }), "resource-observation");
// Server-side Jev call log: the Worker calls TypeSafe itself and appends a
// server-timestamped receipt (migration 0587) before returning the answers, so
// Jev gates credit only rows the gated model could not forge locally. See
// src/jev-call-receipt.js.
const invoices=invoiceAutomation({withEnvelope,writeEvent,ToolError,lockDealField,
  updateDeal:(c,actor,args)=>TOOLS["update-deal"].handler(c,actor,args),
  invoicingMailbox:process.env.CARR_INVOICING_MAILBOX});
registerTools(invoices.tools,"invoice-automation");
registerTools(leadAutomationTools({ withEnvelope, writeEvent, ToolError, invoices }), "lead-automation");
registerTools(jevCallReceiptTools({ withEnvelope, ToolError }), "jev-call-receipt");
// DoctorCRE V5-R02: workflow cutover, caller migration and retirement
// readiness. Composes accept-workflow / disable-legacy-schedule rather than
// duplicating their evidence; retire-workflow-cutover-plan is authority-only.
// See src/workflow-cutover.v5.js.
registerTools(workflowCutoverTools({ withEnvelope, ToolError }), "workflow-cutover");
// Phase 1 CARR-native learning memory: evidence-backed context with explicit
// candidate/promotion/correction/forgetting lifecycle. Memory never grants
// authority; actor and sponsor scope are resolved by the server.
registerTools(memoryTools({ withEnvelope, writeEvent, ToolError, assertNoCallerAuthorityFields }), "memory");
// Native Codex continuity is a separate, bounded surface.  It stores semantic
// checkpoint revisions and lifecycle receipts; transcript bodies stay local to
// the Codex adapter and Claude never reaches these verbs through its config.
registerTools(codexContinuityTools({ withEnvelope, writeEvent, ToolError, assertNoCallerAuthorityFields }), "codex-continuity");
registerTools(claudeContinuityTools({ withEnvelope, writeEvent, ToolError, assertNoCallerAuthorityFields }), "claude-continuity");

// The operational incident ledger gets a front door (2026-08-23 rules-and-verbs
// council, item 1 from both chairs). ops.incident has been written by two
// collectors since 0115 and read by none: seeing it meant tools/ops-record.py on
// a partner's Mac, and closing one meant the break-glass database tap. Five
// verbs — board, card, open, close, adjudicate — with the close and the
// adjudication carrying partner authority, because 0117 already wrote that
// boundary into the grants and the verb surface should not be laxer than the
// grants are. See migrations/0286 for the permission half.
registerTools(incidentTools({ withEnvelope, writeEvent, ToolError, authorizationClassForActor }), "incident");

// Engineering Passport runtime: typed plan registration, server-derived
// admission, and read-only closure projection over the canonical job ledger.
registerTools(engineeringRuntimeTools({ withEnvelope, writeEvent, ToolError }), "engineering-runtime");

// Tour Operations Slice 2: bounded rights, evidence, assertion, and immutable
// public-projection seams. Sealing is authority-only; publication is absent.
registerTools(tourRightsProjectionTools({ withEnvelope, writeEvent, ToolError }), "tour-rights-projection");

// Tour Operations Slice 3: narrow, rights-bound identity assertions,
// coordinate candidates, and human entrance-verification receipts. No map,
// route, publication, or promotion seam is exposed here.
registerTools(tourPropertyJurisdictionTools({ withEnvelope, writeEvent, ToolError }), "tour-property-jurisdiction");

// Tour Operations Slice 4: immutable Tour route versions and internal-only
// cheat-sheet revisions. Route acceptance is authority-only; publication is absent.
registerTools(tourDomainTools({ withEnvelope, writeEvent, ToolError }), "tour-domain");

// Tour Operations delivery surfaces: governed property search and cart,
// confidential sharing, and deterministic PDF request/review records.
registerTools(tourPropertySearchTools({ withEnvelope, writeEvent, ToolError }), "tour-property-search");
registerTools(tourMapPromotionTools({ withEnvelope, writeEvent, ToolError }), "tour-map-promotion");
registerTools(tourSharingTools({ withEnvelope, writeEvent, ToolError }), "tour-sharing");
registerTools(tourArtifactTools({ withEnvelope, writeEvent, ToolError }), "tour-artifacts");
registerTools(benchmarkAcceptanceStoreTools({
  withEnvelope, writeEvent, ToolError, authenticatedIdentity,
}),
  "benchmark-acceptance");
registerTools(modelRoleStoreTools({ withEnvelope, writeEvent, ToolError }),
  "model-role-store");
registerTools(creLifecycleStoreTools({ withEnvelope, ToolError }), "cre-lifecycle");
registerTools(salesforceReconciliationStoreTools({ withEnvelope, ToolError }),
  "salesforce-reconciliation-rw02");
registerTools(salesforceReadRunStoreTools({ withEnvelope, ToolError }), "salesforce-read-run-rw02");
registerTools(recordSourceAuthorityStoreTools({ withEnvelope, ToolError }),
  "record-source-authority");
registerTools(foundationAssuranceMinimumTools({
  withEnvelope, ToolError, authenticatedIdentity,
}), "foundation-assurance");
// V5-S01: read-only projection of the settled global boundaries and the
// dispatch door's mode and shadow counters. No database access.
registerTools(globalBoundariesDoorTools({ ToolError }), "global-boundaries-door");
// DoctorCRE V5-M01: the live door to the Journey 1 clock runtime. The read verb
// derives clock_started from the record; the advance verb takes only an
// idempotency key and is registered WITHOUT an installation resolver, so it
// refuses journey_one_clock_installation_unavailable before any query here. No
// Worker can start, advance or pause the Journey 1 clock through it until a
// verifier for the composed projection is installed by trusted server code.
registerTools(journeyOneClockDoorTools({ withEnvelope, ToolError }), "journey-one-clock-door");
// DoctorCRE V5-J103: the governed correspondence store. Two reads
// (correspondence-readiness, read-correspondence-thread) and the humanOnly
// consent pair, each partner only for their own carr.us mailbox. There is no
// send verb and no draft verb: a draft can never dispatch, and Joe sends. Reads
// answer unavailable until the F10 local-store adapter lands with a reviewed
// grant for the read-receipt writer.
registerTools(governedCorrespondenceStoreTools({ withEnvelope, writeEvent, ToolError }),
  "governed-correspondence-store");
registerTools(assuranceHealthStoreTools({ withEnvelope, ToolError }), "assurance-health-store");
// DoctorCRE V5-D01: inactive action-specific autonomy successors. Three verbs
// over migration 0708's append-only registry -- register-, read- and the
// deterministic read-action-class-gate, which as shipped always denies (no
// activation door exists). Registration grants no authority and no verb here
// can ever produce a row this gate reads as allowed.
registerTools(actionClassSuccessorRegistryTools({ withEnvelope, writeEvent, ToolError }),
  "action-class-successor-registry");
// DoctorCRE V5-A03: append-only independent complete-set review. Every
// participant registers only its authenticated actor/session duty; all eleven
// dimensions precede one batch repair; regression checks cannot shrink; and a
// stronger adjudicator is the only transition after two unresolved rounds.
registerTools(completeSetReviewA03StoreTools({ withEnvelope, writeEvent, ToolError }),
  "complete-set-review-a03-store");
// DoctorCRE V5-F05: authenticated, actor-scoped rule-universe read plus the
// Joe-authority typed-contract binder. The read uses the writer connection only
// to receive server-established actor/sponsor transaction settings; its tool
// contract remains read-only and the SQL function is stable.
registerTools(ruleContextRuntimeTools({ withEnvelope, ToolError }), "rule-context-runtime");

Object.freeze(TOOLS);
