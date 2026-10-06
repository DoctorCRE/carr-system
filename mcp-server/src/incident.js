// incident.js — the guards behind the five incident verbs (2026-08-23,
// rules-and-verbs council item 1).
//
// WHY A SEPARATE FILE, AND WHY IT TOUCHES NO DATABASE. tools/ops-record.py
// already made this call and stated the reason: resolve_preconditions and
// sweep_decision are kept "PURE and separate from the write so the guards can
// be tested without a database, the same reason mcp-server/src/trace.js exports
// its classifiers. The guards are the substance: a close path that rubber-stamps
// anything is worse than none, because then the pile LOOKS handled."
//
// This file is the JavaScript half of that same sentence. Every function here
// takes plain values and returns plain values; the SQL lives in tools.js.
//
// ── THE SAME GUARDS, NOT SIMILAR ONES ───────────────────────────────────────
// closePreconditions below is a port of resolve_preconditions in
// tools/ops-record.py, check for check and message for message. That is
// deliberate and it is the hard requirement of this build: the council asked
// for a verb that replaces the break-glass route for an ordinary close, and a
// verb that closes rows the CLI would have refused is not a replacement — it is
// a second, laxer door with a nicer name. Rule a8c55a47's principle (the same
// job done from two runtimes stays one piece of logic) cannot be met literally
// here, because a Cloudflare Worker cannot invoke Python; it is met by keeping
// the two implementations line-comparable and by testing this one against the
// same cases. If you change a guard, change it in both files.
//
// THE ONE ADDITION the Python side does not have is the duplicate arm, because
// the Python side has no duplicate column to read — see migration 0286.

import { incidentSignature } from "./trace.js";
import { canExercisePartnerAuthority } from "./partner-authority.js";
import { organizationTenantForActor } from "./identity.js";

/** The watch a recovered service gets before a human may call it resolved.
 * Same 24 hours tools/ops-record.py and trace.js both use: a reader's sense of
 * "how stale can this get before it is ignored" must not depend on which
 * writer opened the row. */
export const MONITORING_HOURS = 24;

/** ops.incident's own check constraint is `severity ~ '^SEV-[0-4]$'`. This
 * list is that constraint, not a narrower opinion about which severities are
 * in fashion — a verb that refuses a value the ledger accepts teaches people
 * the ledger is smaller than it is. */
export const SEVERITIES = Object.freeze(["SEV-0", "SEV-1", "SEV-2", "SEV-3", "SEV-4"]);

/** Owners are people. ops.incident.owner_actor is free text and the collectors
 * all write 'joe', but a verb that accepts any string produces an owner column
 * nobody can filter on — the exact failure loop_item's owner column already
 * has, where 110 of 150 open rows name two people at once and no query can
 * select for them. */
export const OWNERS = Object.freeze(["joe", "dell"]);

/** The states ops.incident treats as still live. 'monitoring' counts as open
 * on purpose — 0116's index comment says why: "a service that fails again
 * while we are watching it recover is the same incident continuing, not a
 * second one starting." */
export const OPEN_STATES = Object.freeze([
  "detected", "triaged", "investigating", "mitigating", "monitoring",
]);

/**
 * The de-dupe identity, in the council's words a fingerprint: service,
 * operation, failure class, environment.
 *
 * IT IS NOT A NEW SHAPE. It is 0116's `signature` column, in the exact field
 * order tools/ops-record.py's assess() and mcp-server/src/trace.js already
 * write — service|environment|operation|failure_class — so an incident a
 * partner opens by hand and an incident a collector opens from an exit code
 * collapse under ONE partial unique index rather than sitting beside each
 * other as two pages for one event. `operation` plays run_key's role: the job
 * key, the route, the check name — whatever names the thing that broke.
 *
 * Re-exported through trace.js's own function rather than reimplemented: two
 * string templates that must agree forever is how the second one drifts.
 */
export function incidentFingerprint({ service, environment, operation, failureClass }) {
  const parts = { service, environment, operation, failureClass };
  for (const [key, value] of Object.entries(parts))
    if (!value || !String(value).trim())
      return { ok: false, error: `fingerprint_incomplete`, missing: key };
  return {
    ok: true,
    signature: incidentSignature({
      serviceKey: String(service).trim(),
      environment: String(environment).trim(),
      routeKey: String(operation).trim(),
      failureClass: String(failureClass).trim(),
    }),
  };
}

/**
 * The source_ref an occurrence fact carries.
 *
 * ONE SHAPE, SHARED WITH THE RECORDER ALREADY RUNNING. trace.js writes
 * `correlation:<uuid>` and refuses to write a second fact for a correlation it
 * has already recorded ("never grow the list on a retry"). open-incident
 * writes the same shape for the same reason, which is also what makes the
 * board's occurrence count mean one thing rather than two.
 */
export function occurrenceSourceRef(correlationId) {
  return `correlation:${correlationId}`;
}

/**
 * (ok, error) — may this actor perform a partner-authority incident act?
 *
 * THE COUNCIL'S LINE, VERBATIM: close-incident is "partner (Joe or Dell, never
 * unsponsored)". Two things have to be true and they are different things:
 *
 *   1. The server resolves either a verified partner or a sponsored native
 *      Codex/Claude actor. Probe, reviewer, local-token, and unsponsored doors
 *      remain refused; nothing on the wire can select a sponsor.
 *   2. The resolved authority belongs to Joe or Dell specifically. The runtime
 *      agent remains separately attributable, while the authorization class
 *      and sponsor come only from the server's identity resolution.
 *
 * This function is the SECOND check and is written to stand alone: passed an
 * unsponsored runtime it refuses even if the humanOnly gate were somehow
 * removed from the registry entry, which is what makes it testable as a
 * boundary rather than as a comment about one.
 */
export function partnerAuthority(actor, authorizationClass) {
  if (!canExercisePartnerAuthority(actor))
    return { ok: false, error: "partner_authority_required", authorization_class: authorizationClass || null,
      hint: "closing or reclassifying an operational incident requires Joe or Dell, or a server-verified " +
            "Codex/Claude session sponsored by one of them" };
  if (authorizationClass !== "verified_partner" && authorizationClass !== "sponsored_agent")
    return { ok: false, error: "partner_authority_required", authorization_class: authorizationClass || null,
      hint: "this session carries no server-derived partner authority over the operational ledger" };
  return { ok: true, error: null };
}

/**
 * (ok, error, fields, facts) — may this ONE incident close?
 *
 * A port of resolve_preconditions in tools/ops-record.py. `incident` is a
 * mapping with ref / state / recovery_evidence_ref / monitoring_until, plus
 * duplicate_of_ref for the arm the Python side does not have.
 *
 * `now` IS THE DATABASE'S CLOCK, PASSED IN. The caller reads `now()` in the
 * same statement that reads the incident and hands it here, rather than
 * letting this function reach for the runtime's own clock. ops-record.py's
 * _next_incident_ref carries the full story of why: it once read the day from
 * the server for one half of a decision and from the client for the other,
 * "and the two agree only when the server runs UTC, so production was correct
 * by luck rather than by design." A monitoring window is exactly that kind of
 * decision — the value being compared was written by the database, so the
 * instant it is compared against comes from there too.
 */
export function closePreconditions(incident, { rootCause, evidence, allowEarly, now } = {}) {
  const facts = [];
  const state = String(incident?.state || "").trim();
  if (state === "resolved" || state === "reviewed")
    return { ok: false, error: `${incident?.ref} is already ${state}`, fields: {}, facts };

  if (!String(rootCause || "").trim())
    return { ok: false, fields: {}, facts,
      error: "a root cause is required — 'close with an outcome' means the outcome is recorded, " +
             "not that the row is cleared" };

  // EVIDENCE, in the order the ledger prefers it. What assess() already
  // recorded off a real green run beats what a caller types; a duplicate's
  // canonical row is evidence in its own right, because the thing that shows
  // this row is safe to close is that another row carries the same event, its
  // investigation and its watch.
  const duplicateOf = incident?.duplicate_of_ref || null;
  const ref = incident?.recovery_evidence_ref
    || String(evidence || "").trim()
    || (duplicateOf ? `ops.incident:${duplicateOf}` : null)
    || null;
  if (!ref)
    return { ok: false, fields: {}, facts,
      error: "no recovery evidence on the incident and none supplied — pass evidence naming what " +
             "shows it is safe to close, or adjudicate it as a duplicate of the incident that " +
             "carries the investigation" };

  // THE WINDOW. A green run says the symptom stopped, not that the cause is
  // understood, so an incident inside its watch does not close on the clock
  // alone. Two things excuse it and both are RECORDED rather than merely
  // permitted — ops-record.py's rule, kept: "the reason an early close was
  // allowed belongs ON the incident, not in a shell history nobody reads back."
  const until = incident?.monitoring_until ? new Date(incident.monitoring_until) : null;
  const inWindow = Boolean(until && now && until.getTime() > new Date(now).getTime());
  const reason = String(allowEarly || "").trim();
  if (inWindow && !duplicateOf && !reason)
    return { ok: false, fields: {}, facts,
      error: `${incident?.ref} is still inside its monitoring window until ` +
             `${formatWindow(until)} — a green run says the symptom stopped, not that the cause ` +
             `is understood. Pass allow_early with a reason if the window cannot apply.` };

  // NOTHING A PARTNER SAID IS DROPPED, AND NOTHING WRITTEN IS FALSE. The first
  // build recorded a fact only when the window happened to be open, which meant
  // a partner who typed a reason for closing early could get silence back if
  // the row carried no window at all — the reason existed in their head and the
  // request and nowhere in the record. It also meant a duplicate closed outside
  // a window left no trace of WHY its evidence was another incident.
  //
  // So both are always recorded, and the sentence says which case it was. The
  // Python side records "closed before its monitoring window elapsed" whenever
  // --allow-early is passed, which on a row whose window already ran out is a
  // false sentence in the ledger; this is the one place the two implementations
  // deliberately differ, and it differs by being truthful rather than by being
  // laxer — the REFUSAL logic above is identical.
  if (duplicateOf && !incident?.recovery_evidence_ref && !String(evidence || "").trim())
    facts.push(inWindow
      ? `closed inside its monitoring window as a duplicate of ${duplicateOf}, which carries the ` +
        `investigation and the watch for this event`
      : `closed as a duplicate of ${duplicateOf}, which carries the investigation for this event`);
  if (reason)
    facts.push(inWindow
      ? `closed before its monitoring window elapsed: ${reason}`
      : `closed with a stated exception, though no monitoring window was open at the time: ${reason}`);

  // monitoring_until is NOT NULL under 0115's resolved constraint. An incident
  // closed early, or one that never had a window, still needs a value: stamp
  // the close instant, so the row says the watching ended here rather than
  // implying a wait that never happened.
  return {
    ok: true, error: null, facts,
    fields: {
      recovery_evidence_ref: ref,
      stamp_monitoring_until_now: !until,
      root_cause: String(rootCause).trim(),
    },
  };
}

function formatWindow(date) {
  return `${date.toISOString().slice(0, 16).replace("T", " ")}Z`;
}

/**
 * (ok, error, fields, facts) — the partner's reclassification of one incident.
 *
 * Severity, owner and duplicate-of, which is exactly the set both council
 * chairs named. Nothing here touches state, resolved_at or root_cause: an
 * adjudication records a JUDGMENT ABOUT WHAT THIS IS, and close-incident
 * remains the only path to 'resolved'. Two verbs that can both close a row is
 * how a guard gets bypassed by the one that forgot it.
 *
 * `canonical` is the resolved duplicate target (null when none was asked for):
 * { id, ref, state, duplicate_of_id }.
 */
export function adjudicationChanges(incident, { severity, owner, canonical, note } = {}) {
  const fields = {};
  const facts = [];

  if (severity !== undefined && severity !== null) {
    if (!SEVERITIES.includes(severity))
      return { ok: false, fields: {}, facts,
        error: `severity must be one of ${SEVERITIES.join(", ")} — ops.incident's own check ` +
               `constraint accepts nothing else` };
    if (severity !== incident?.severity) {
      fields.severity = severity;
      facts.push(`severity changed from ${incident?.severity} to ${severity}` +
                 (note ? ` — ${note}` : ""));
    }
  }

  if (owner !== undefined && owner !== null) {
    if (!OWNERS.includes(owner))
      return { ok: false, fields: {}, facts,
        error: `owner must be ${OWNERS.join(" or ")} — an incident owned by nobody in particular ` +
               `is an incident nobody picks up` };
    if (owner !== incident?.owner_actor) {
      fields.owner_actor = owner;
      facts.push(`owner changed from ${incident?.owner_actor || "nobody"} to ${owner}` +
                 (note ? ` — ${note}` : ""));
    }
  }

  if (canonical !== undefined && canonical !== null) {
    if (canonical.id === incident?.id)
      return { ok: false, fields: {}, facts,
        error: "an incident cannot be a duplicate of itself" };
    // ONE HOP, NEVER A CHAIN. B duplicates A and C duplicates B leaves a reader
    // walking pointers to find the row that actually carries the investigation,
    // and the walk has no guaranteed end. Point at the canonical row directly.
    if (canonical.duplicate_of_id)
      return { ok: false, fields: {}, facts,
        error: `${canonical.ref} is itself recorded as a duplicate — point at the incident that ` +
               `carries the investigation, not at another duplicate` };
    fields.duplicate_of_id = canonical.id;
    fields.next_action = `close as a duplicate of ${canonical.ref}, which carries the investigation`;
    facts.push(`adjudicated as the same operational event as ${canonical.ref}` +
               (note ? ` — ${note}` : ""));
  }

  if (!Object.keys(fields).length)
    return { ok: false, fields: {}, facts,
      error: "nothing to adjudicate — pass a severity, an owner, or a duplicate_of that differs " +
             "from what the incident already says" };

  return { ok: true, error: null, fields, facts };
}


// ═══════════════════════════════════════════════════════════════════════════
// THE VERBS
// ═══════════════════════════════════════════════════════════════════════════
//
// FIVE, AND THE NAMES ARE THE COUNCIL'S. incident-board is deliberately shaped
// like loop-board and deal-board — grok's chair named that analogy explicitly —
// because a partner already knows what a board is for, and rule 3578d799 asks
// for human words. get / open / close / adjudicate are what a person does to an
// incident, said out loud.
//
// ── THE THREE ADJACENT OBJECTS, SETTLED HERE ────────────────────────────────
// Both chairs flagged the same risk: with incidents on the verb surface there
// are now three ways to file something that went wrong, and codex's chair asked
// for the boundary in words before, not after — "do not leave three ways to
// mint parallel unresolved records." The line, and it is in each description:
//
//   record-defect  — a CLAIM THE SYSTEM MADE that collided with the truth.
//                    public.defect. No severity, no service, no lifecycle.
//   report-problem — a PARTNER-FACING REPORT: something a person hit and wants
//                    looked at. It is intake, and a human triages it.
//   open-incident  — an OPERATIONAL EVENT: a named service, in a named
//                    environment, failed in a named way, and the ledger watches
//                    it until a partner closes it with an outcome.
//
// ── WHAT IS AND IS NOT A PARTNER'S ACT ──────────────────────────────────────
// READ and OPEN are session-authority: any authenticated session may see what
// is broken and may say that something broke. CLOSE and ADJUDICATE are partner
// authority — Joe or Dell, never an unsponsored runtime — because they are the
// two acts that decide what is TRUE about an incident rather than what was
// observed about it. That is not a new rule invented for this build; it is the
// rule 0117 already wrote into the grants (carr_jobs may report a recovery and
// may never mark one resolved), lifted to the verb surface so a partner has a
// door that obeys it instead of a break-glass credential that bypasses it.

const VALID_ENVIRONMENTS = ["local", "rehearsal", "staging", "production"];
const LINK_KINDS = ["run", "deployment", "work_request", "defect", "decision"];
const OBSERVATION_MAX = 500;
const BOARD_LIMIT_DEFAULT = 60;
const BOARD_LIMIT_MAX = 200;
const FACT_LIMIT_DEFAULT = 50;
const TRACE_CORRELATION_MAX = 25;
// The allocator uses padStart(2), so its sequence is 01..99, 100, 101...
// (not a fixed two-digit suffix). Share this with every schema and handler
// that validates an incident ref so the hundredth row remains addressable.
const INCIDENT_REF_PATTERN = "^INC-[0-9]{8}-(?:0[1-9]|[1-9][0-9]+)$";
const INCIDENT_REF_RE = new RegExp(INCIDENT_REF_PATTERN);

// OCCURRENCES: ONE NUMBER, TWO WRITERS, NEITHER OF THEM WRONG.
//
// "How many times has this happened" has no single column, and it cannot have
// one, because the two collectors that fill this ledger record a recurrence
// differently and both are correct for what they see:
//
//   tools/ops-record.py links the failed ops.run or ops.deployment row
//     (ops.incident_link) and writes a fact beside it.
//   mcp-server/src/trace.js has no ledger row to link — a failed request is
//     not a run — so it records one fact per distinct correlation id and
//     refuses to write a second for a correlation it has already seen.
//
// Counting facts alone double-counts the first writer (a link and its
// correlation fact are ONE event). Counting links alone reads ZERO for every
// incident the Worker opened — eleven of the twenty-two open rows on
// 2026-08-23, including the one carrying twenty-eight recurrences. So the
// Old rows can carry both shapes for one occurrence. Resolve run/deployment
// targets before combining them so a shared correlation counts once. Rows
// whose legacy target cannot be resolved retain the conservative greater-of
// count and say that the overlap is unknown.
//
// Derived, never stored. A counter column would read 1 for every one of the
// existing rows until someone backfilled it, and a count that is wrong about
// history is worse than one computed on read.
export const INCIDENT_OCCURRENCE_JOIN_SQL = `
  left join lateral (
    with occurrence_links as (
      select l.kind, l.ref,
             case when l.ref ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
                  then l.ref::uuid else null::uuid end as target_uuid
        from ops.incident_link l
       where l.incident_id = i.id and l.kind in ('run','deployment')
    ), resolved_links as (
      select x.kind, x.ref,
             case when x.kind = 'run' then
                    (select r.correlation_id::text from ops.run r where r.id = x.target_uuid)
                  when x.kind = 'deployment' then
                    (select d.correlation_id::text from ops.deployment d where d.id = x.target_uuid)
             end as resolved_correlation
        from occurrence_links x
    ), correlation_suffixes as (
      select distinct substring(f.source_ref from 13) as correlation_suffix
        from ops.incident_fact f
       where f.incident_id = i.id and f.source_ref like 'correlation:%'
    ), occurrence_counts as (
      select (select count(*) from resolved_links)::int as link_count,
             (select count(*) from correlation_suffixes)::int as correlation_count,
             (select count(*) from resolved_links where resolved_correlation is null)::int as unresolved_count,
             (select count(*) from correlation_suffixes c
                where not exists (
                  select 1 from resolved_links r
                   where r.resolved_correlation = c.correlation_suffix
                ))::int as unpaired_correlation_count
    )
    select case when unresolved_count = 0
                then greatest(1, link_count + unpaired_correlation_count)
                else greatest(1, link_count, correlation_count) end::int as occurrences,
           (unresolved_count > 0) as legacy_overlap_unknown,
           unresolved_count as unresolved_occurrence_edge_count,
           case when unresolved_count > 0 then 'legacy_overlap_unknown' else 'complete' end
             as occurrence_evidence_status
      from occurrence_counts
  ) occurrence on true`;

export const INCIDENT_OCCURRENCE_COLUMNS = `
  occurrence.occurrences as occurrences,
  occurrence.occurrence_evidence_status,
  occurrence.legacy_overlap_unknown,
  occurrence.unresolved_occurrence_edge_count`;

/** The row shape both reads return, so the board and the card never disagree
 * about what an incident says. */
const INCIDENT_COLUMNS = `
  i.id, i.ref, i.title, i.severity, i.state, i.environment,
  i.owner_actor, i.next_action, i.business_impact,
  i.signature as fingerprint,
  i.correlation_id,
  to_jsonb(i.detected_at)#>>'{}'      as detected_at,
  to_jsonb(i.observed_at)#>>'{}'      as observed_at,
  to_jsonb(i.monitoring_until)#>>'{}' as monitoring_until,
  to_jsonb(i.resolved_at)#>>'{}'      as resolved_at,
  i.recovery_evidence_ref, i.root_cause, i.followup_disposition,
  i.detected_source, i.source_kind, i.source_ref,
  dup.ref as duplicate_of,
  (i.monitoring_until is not null and i.monitoring_until > now()) as monitoring_window_open,
  floor(extract(epoch from (now() - i.detected_at)) / 86400)::int as age_days,
  ${INCIDENT_OCCURRENCE_COLUMNS}`;

/**
 * What a partner needs to know before reaching for close-incident: can this row
 * close right now, and if not, what is missing?
 *
 * IT ASKS closePreconditions ITSELF rather than restating its rules. A board
 * that computes readiness from its own copy of the guards is a board that
 * eventually disagrees with the verb, and the disagreement always surfaces as
 * "it said I could close it and then refused."
 */
export function readinessFor(row, now) {
  if (row.state === "resolved" || row.state === "reviewed")
    return { ready_to_close: false, blocked_by: `already ${row.state}` };
  const probe = closePreconditions(
    { ...row, duplicate_of_ref: row.duplicate_of },
    { rootCause: "probe", now });
  if (probe.ok)
    return { ready_to_close: true, blocked_by: null };
  // The one blocker a partner can clear by typing, versus the one they cannot.
  const needsEvidence = /no recovery evidence/.test(probe.error || "");
  return {
    ready_to_close: false,
    blocked_by: needsEvidence
      ? "no recovery evidence — supply one, or adjudicate it as a duplicate"
      : `monitoring window open until ${row.monitoring_until}`,
  };
}

export function incidentTools({ withEnvelope, writeEvent, ToolError, authorizationClassForActor }) {

  /** Every partner-authority handler opens with this. It is a function rather
   * than four copies for the reason rule a8c55a47 gives: a boundary written
   * twice is a boundary enforced once. */
  const requirePartner = (actor) => {
    const verdict = partnerAuthority(actor, authorizationClassForActor(actor));
    if (!verdict.ok) throw new ToolError(verdict);
  };

  /** Read one incident by ref, with the database's own clock, or refuse. */
  const loadIncident = async (c, ref) => {
    const r = await c.query(
      `select ${INCIDENT_COLUMNS}, i.duplicate_of_id, to_jsonb(now())#>>'{}' as db_now
         from ops.incident i
         left join ops.incident dup on dup.id = i.duplicate_of_id
         ${INCIDENT_OCCURRENCE_JOIN_SQL}
        where i.ref = $1`, [String(ref || "").trim()]);
    if (!r.rows.length)
      throw new ToolError({ error: "no_such_incident", ref,
        hint: "incident refs look like INC-20260823-01; list them with incident-board" });
    return r.rows[0];
  };

  return {
    "incident-board": {
      write: false,
      description:
        "Every open operational incident with its severity, state, age, owner, how many times it has " +
        "recurred, and the ledger's own next step — the live answer to 'what is broken and what is it " +
        "waiting on'. THE GAP THIS CLOSES: until now that question could only be answered by running " +
        "tools/ops-record.py on a partner's own Mac, so a session could see nothing at all. Each row " +
        "also says whether it is READY TO CLOSE — evidence present and the monitoring window elapsed — " +
        "so a partner can tell the rows waiting on a human from the rows waiting on the clock. This is " +
        "the OPERATIONAL ledger: a named service failing in a named environment. A claim the system " +
        "made that collided with the truth is record-defect; something a person hit and wants looked " +
        "at is report-problem. Read-only.",
      inputSchema: { type: "object", properties: {
        state: { type: "string", description: "'open' (default) for everything still live including monitoring, 'any' for the whole history, or one exact state: detected, triaged, investigating, mitigating, monitoring, resolved, reviewed" },
        severity: { type: "string", description: "SEV-1, SEV-2, ... — one severity to filter to" },
        environment: { type: "string", enum: VALID_ENVIRONMENTS },
        owner: { type: "string", description: "joe | dell" },
        service: { type: "string", description: "a service key — matched against the fingerprint, which starts with it" },
        search: { type: "string", description: "case-insensitive match against the title" },
        ready_to_close: { type: "boolean", description: "true for only the rows whose evidence and monitoring window are already satisfied — the pile a partner can actually clear today" },
        limit: { type: "integer", default: BOARD_LIMIT_DEFAULT },
      } },
      handler: async (c, _actor, args) => {
        const where = [];
        const params = [];
        const state = String(args.state || "open");
        if (state === "open") where.push(`i.state not in ('resolved','reviewed')`);
        else if (state !== "any") { params.push(state); where.push(`i.state = $${params.length}`); }
        if (args.severity) { params.push(args.severity); where.push(`i.severity = $${params.length}`); }
        if (args.environment) { params.push(args.environment); where.push(`i.environment = $${params.length}`); }
        if (args.owner) { params.push(String(args.owner).toLowerCase()); where.push(`lower(i.owner_actor) = $${params.length}`); }
        // The fingerprint's first field IS the service key (see
        // incidentFingerprint), so a prefix match is exact rather than a
        // substring guess that could match an environment or a failure class.
        if (args.service) { params.push(`${args.service}|%`); where.push(`i.signature like $${params.length}`); }
        if (args.search) { params.push(`%${args.search}%`); where.push(`i.title ilike $${params.length}`); }
        params.push(Math.min(Number(args.limit) || BOARD_LIMIT_DEFAULT, BOARD_LIMIT_MAX));

        const r = await c.query(
          `select ${INCIDENT_COLUMNS}, to_jsonb(now())#>>'{}' as db_now
             from ops.incident i
             left join ops.incident dup on dup.id = i.duplicate_of_id
             ${INCIDENT_OCCURRENCE_JOIN_SQL}
            ${where.length ? `where ${where.join(" and ")}` : ""}
            -- SEVERITY FIRST, THEN OLDEST FIRST. Severity is a text sort and
            -- 'SEV-1' < 'SEV-2' lexically, which is the order a person means.
            -- Within one severity the oldest row is the one that has been
            -- ignored longest, and that is the whole point of a board.
            order by i.severity, i.detected_at
            limit $${params.length}`, params);

        const dbNow = r.rows[0]?.db_now || null;
        const incidents = r.rows.map(({ db_now, id, ...row }) => ({ ...row, ...readinessFor(row, dbNow || db_now) }));
        const shown = args.ready_to_close === true
          ? incidents.filter((row) => row.ready_to_close)
          : incidents;

        const tally = (key) => shown.reduce((out, row) => {
          const k = row[key] == null ? "none" : String(row[key]);
          out[k] = (out[k] || 0) + 1;
          return out;
        }, {});
        return {
          count: shown.length,
          by_severity: tally("severity"),
          by_state: tally("state"),
          ready_to_close: shown.filter((row) => row.ready_to_close).length,
          incidents: shown,
        };
      },
    },

    "get-incident": {
      write: false,
      description:
        "One incident's whole evidence trail: the row itself, every recorded fact with its source, every " +
        "hypothesis kept separately from the facts (a guess must never sit where a reader looks for " +
        "evidence), the ledger rows it is linked to, any incidents adjudicated as duplicates of it, and " +
        "the CORRELATED JOURNEY — every deploy, job run, check and work request sharing a correlation id " +
        "with it, in time order. Read this before closing anything: close-incident requires a root cause, " +
        "and the root cause is in here. Read-only.",
      inputSchema: { type: "object", properties: {
        ref: { type: "string", description: "the incident ref, e.g. INC-20260823-01" },
        fact_limit: { type: "integer", default: FACT_LIMIT_DEFAULT, description: "newest facts returned; a long-running recurrence can carry hundreds" },
      }, required: ["ref"] },
      handler: async (c, _actor, args) => {
        const inc = await loadIncident(c, args.ref);
        const factLimit = Math.min(Math.max(Number(args.fact_limit) || FACT_LIMIT_DEFAULT, 1), 500);

        const [facts, hypotheses, links, services, duplicates, factTotal] = await Promise.all([
          c.query(`select text, source_ref, to_jsonb(recorded_at)#>>'{}' as recorded_at
                     from ops.incident_fact where incident_id = $1
                    order by recorded_at desc limit $2`, [inc.id, factLimit]),
          c.query(`select text, confidence, settled_as, to_jsonb(recorded_at)#>>'{}' as recorded_at
                     from ops.incident_hypothesis where incident_id = $1
                    order by recorded_at`, [inc.id]),
          c.query(`select kind, ref, note from ops.incident_link where incident_id = $1
                    order by kind, ref`, [inc.id]),
          c.query(`select s.key, s.name, s.criticality from ops.incident_service x
                     join ops.service s on s.id = x.service_id where x.incident_id = $1
                    order by s.key`, [inc.id]),
          c.query(`select ref, severity, state, title from ops.incident
                    where duplicate_of_id = $1 order by ref`, [inc.id]),
          c.query(`select count(*)::int as n from ops.incident_fact where incident_id = $1`, [inc.id]),
        ]);

        // THE CORRELATED JOURNEY. An incident's own correlation_id is one
        // thread; a recurrence arrives under a NEW correlation and leaves a
        // `correlation:<uuid>` fact, which is exactly the shape 0123's trace
        // recurrence arm consumes. Both are followed, newest recurrences
        // first, and the list is capped: a row carrying eighty recurrences
        // would otherwise return eighty journeys nobody asked for. The cap
        // is REPORTED rather than silently applied.
        //
        // ASKED OF THE WHOLE FACT TABLE, NOT OF THE PAGE ABOVE. The first build
        // scanned `facts.rows`, which is the newest fact_limit rows — so
        // get-incident with fact_limit:3 on a row carrying eighty-seven
        // recurrences reported "correlations 2/2", a cap notice that was
        // truthfully computed and completely wrong. A count derived from a page
        // is a count of the page.
        const known = await c.query(
          // 13 is one past 'correlation:' — the same twelve characters
          // occurrenceSourceRef writes and trace.js has written since 0122.
          `select substring(source_ref from 13) as correlation_id, max(recorded_at) as newest
             from ops.incident_fact
            where incident_id = $1
              and source_ref ~ '^correlation:[0-9a-fA-F-]{36}$'
            group by 1 order by newest desc`, [inc.id]);
        const correlations = [inc.correlation_id];
        for (const row of known.rows)
          if (!correlations.includes(row.correlation_id)) correlations.push(row.correlation_id);
        const followed = correlations.slice(0, TRACE_CORRELATION_MAX);
        const trace = await c.query(
          `select correlation_id, kind, ref, state, environment, service_key, failure_class,
                  detail, source_kind, source_ref, freshness_state,
                  to_jsonb(occurred_at)#>>'{}' as occurred_at
             from ops.v_trace where correlation_id = any($1::uuid[])
            order by occurred_at`, [followed]);

        const { id, db_now, duplicate_of_id, ...incident } = inc;
        return {
          incident: { ...incident, ...readinessFor(inc, db_now) },
          facts: facts.rows,
          facts_total: factTotal.rows[0].n,
          facts_truncated: factTotal.rows[0].n > facts.rows.length,
          hypotheses: hypotheses.rows,
          links: links.rows,
          services: services.rows,
          duplicates: duplicates.rows,
          trace: trace.rows,
          correlations_followed: followed.length,
          correlations_total: correlations.length,
        };
      },
    },

    "open-incident": {
      write: true,
      description:
        "Open an operational incident, or attach this occurrence to the one already open for the same " +
        "failure. THE FINGERPRINT IS THE DE-DUPE and it is four things: service, environment, operation " +
        "(the job key, route or check name), and failure class — 'the same thing broke the same way in " +
        "the same place'. A repeat of an OPEN fingerprint never mints a second row; it appends an " +
        "occurrence to the existing one and returns opened:false with the running count. The service " +
        "must already be registered in the ledger — an unregistered one is refused, never invented. " +
        "This verb records what was OBSERVED; it can never mark anything resolved, which is a partner's " +
        "act through close-incident. Use record-defect instead for a claim the system made that " +
        "collided with the truth, and report-problem for something a person hit and wants looked at.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        service: { type: "string", description: "a registered service key, e.g. carr-mcp or nightly-record-layer" },
        environment: { type: "string", enum: VALID_ENVIRONMENTS },
        operation: { type: "string", description: "what ran: the job key, route, or check name. This is the third field of the fingerprint, so it must name the same thing every time the failure recurs." },
        failure_class: { type: "string", description: "how it broke, as a short stable slug — timeout, http_5xx, permission_denied. The fourth fingerprint field." },
        observed: { type: "string", description: "what you actually saw, in your words. Recorded as a FACT with its source, so keep it to what is true rather than what you suspect — a theory belongs in a hypothesis, not here." },
        title: { type: "string", description: "one line naming the failure; a plain one is composed from the fingerprint when absent" },
        severity: { type: "string", enum: SEVERITIES, description: "defaults to SEV-3 (contained, with a workaround). Raising it later is adjudicate-incident, a partner's act." },
        owner: { type: "string", enum: OWNERS, description: "who is holding it; defaults to joe, matching the collectors" },
        next_action: { type: "string", description: "the ledger's own next step, shown on the board" },
        related_kind: { type: "string", enum: LINK_KINDS, description: "link this incident to a ledger row you already have: a run, deployment, work_request, defect or decision" },
        related_ref: { type: "string", description: "that row's id or ref" },
      }, required: ["idempotency_key", "service", "environment", "operation", "failure_class"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "open-incident", args, async () => {
        const environment = String(args.environment || "").trim();
        if (!VALID_ENVIRONMENTS.includes(environment))
          throw new ToolError({ error: "invalid_environment", got: args.environment,
            allowed: VALID_ENVIRONMENTS,
            hint: "an unlabelled deploy is never guessed at — name the environment the failure happened in" });

        const fingerprint = incidentFingerprint({
          service: args.service, environment,
          operation: args.operation, failureClass: args.failure_class,
        });
        if (!fingerprint.ok)
          throw new ToolError({ error: "fingerprint_incomplete", missing: fingerprint.missing,
            hint: "the de-dupe needs all four: service, environment, operation, failure class. " +
                  "Without one of them a repeat of this failure opens a second incident." });

        // THE SERVICE IS NEVER INVENTED. tools/ops-record.py and trace.js both
        // take this posture — "an unregistered service is never invented" — and
        // it is load-bearing for the board: a typo'd key produces a fingerprint
        // that can never match the collector's, so the same failure would sit
        // on the board twice under two spellings.
        const svc = await c.query(
          "select id, key from ops.service where key = $1 and retired_at is null", [String(args.service).trim()]);
        if (!svc.rows.length)
          throw new ToolError({ error: "no_such_service", service: args.service,
            hint: "register the service in ops/config/services.json and sync it before filing incidents " +
                  "against it — a service this ledger has never heard of cannot be watched" });
        const serviceId = svc.rows[0].id;

        // DOUBLE-CHECKED, AND THE LOCK IS ONLY ON THE MINT PATH. Two writers
        // that both observe "no open incident for this fingerprint" both mint,
        // and the second dies on either incident_ref_key or 0116's partial
        // unique index — so the mint has to be serialized. The lock that does
        // it is the SAME one tools/ops-record.py takes for ref allocation, and
        // that writer holds it to transaction end: `assess` opening one
        // incident holds it for its whole run over every job.
        //
        // So an unconditional lock here would put the COMMON path — an
        // unattended run attaching an occurrence to an incident that is already
        // open — behind the nightly assessment's transaction, for a row it was
        // never going to insert. The lookup runs first and unlocked, and only a
        // miss pays for the lock and re-asks under it. That is ops-record.py's
        // own pattern, and it takes the correlation lock BEFORE this one while
        // this path takes only this one, so the two orderings cannot form a
        // cycle.
        const findOpen = () => c.query(
          `select id, ref, severity, state, owner_actor from ops.incident
            where signature = $1 and state not in ('resolved','reviewed') limit 1`,
          [fingerprint.signature]);
        let open = await findOpen();
        if (!open.rows.length) {
          await c.query("select pg_advisory_xact_lock(hashtextextended('ops.incident.ref-allocation', 0))");
          open = await findOpen();   // another writer may have opened it while we waited
        }

        // The occurrence's source. actor.correlation_id is the x-correlation-id
        // of the request that produced this write, set server-side by mcp.js's
        // dispatch() — the same thread trace.js keys its own occurrence facts
        // on, so both writers' recurrences count as one kind of thing.
        const correlationId = actor.correlation_id || null;
        const sourceRef = correlationId
          ? occurrenceSourceRef(correlationId)
          : `verb:open-incident:${args.idempotency_key}`;
        const observed = String(args.observed || "").trim().slice(0, OBSERVATION_MAX);
        const factText = observed
          || `${args.operation} failed on ${svc.rows[0].key} (${environment}), failure class ${args.failure_class}`;

        let incidentId = open.rows[0]?.id || null;
        let ref = open.rows[0]?.ref || null;
        const opened = !incidentId;

        if (opened) {
          const n = await c.query(
            `select to_char(now() at time zone 'UTC', 'YYYYMMDD') as day,
                    coalesce(max(substring(ref from '[0-9]+$')::int), 0) + 1 as seq
               from ops.incident
              where ref like 'INC-' || to_char(now() at time zone 'UTC', 'YYYYMMDD') || '-%'`);
          // BOTH HALVES FROM THE QUERY, no date formatted here. ops-record.py's
          // _next_incident_ref carries the incident this prevents: a ref
          // counted against the server's day and stamped from the client's is
          // correct only when both run UTC, and every incident opened in the
          // gap is numbered 01.
          ref = `INC-${n.rows[0].day}-${String(n.rows[0].seq || 1).padStart(2, "0")}`;
          const ins = await c.query(
            `insert into ops.incident
                 (ref, correlation_id, title, severity, state, environment, owner_actor,
                  next_action, detected_source, detected_at, source_kind, source_ref,
                  signature, observed_at, expires_at)
               values ($1, coalesce($2::uuid, gen_random_uuid()), $3, $4, 'detected', $5, $6,
                       $7, 'verb:open-incident', now(), 'operator', $8,
                       $9, now(), now() + make_interval(hours => ${MONITORING_HOURS}))
             returning id`,
            [ref, correlationId,
             String(args.title || "").trim() || `${args.operation} failed on ${svc.rows[0].key} (${environment})`,
             args.severity || "SEV-3", environment, args.owner || "joe",
             String(args.next_action || "").trim()
               || `read the trace: get-incident ${ref}`,
             `actor:${actor.slug}`, fingerprint.signature]);
          incidentId = ins.rows[0].id;
        }

        // The service attachment, and the optional ledger link. Both are
        // on-conflict-do-nothing because a repeat occurrence re-asserts the
        // same edges and re-asserting is not an error.
        await c.query(
          `insert into ops.incident_service (incident_id, service_id) values ($1,$2)
             on conflict do nothing`, [incidentId, serviceId]);
        let linked = false;
        if (args.related_kind || args.related_ref) {
          if (!args.related_kind || !String(args.related_ref || "").trim())
            throw new ToolError({ error: "incomplete_link",
              hint: "a link needs both related_kind and related_ref, or neither" });
          const l = await c.query(
            `insert into ops.incident_link (incident_id, kind, ref, note) values ($1,$2,$3,$4)
               on conflict do nothing returning incident_id`,
            [incidentId, args.related_kind, String(args.related_ref).trim(), observed || null]);
          linked = l.rows.length > 0;
        }

        // NEVER GROW THE LIST ON A RETRY. trace.js's own rule, same reason: an
        // occurrence is keyed on its source, and the same source observed twice
        // is one occurrence. withEnvelope already replays an identical
        // idempotency key, but a genuine second call under the SAME correlation
        // is a different path to the same duplicate.
        const dup = await c.query(
          "select 1 from ops.incident_fact where incident_id=$1 and source_ref=$2 limit 1",
          [incidentId, sourceRef]);
        const recorded = dup.rows.length === 0;
        if (recorded)
          await c.query(
            "insert into ops.incident_fact (incident_id, text, source_ref) values ($1,$2,$3)",
            [incidentId, factText, sourceRef]);

        // A NEW failure during a recovery watch invalidates that recovery.
        // The fact insert above is the idempotency boundary: a replay with the
        // same correlation/source_ref records nothing and therefore changes
        // nothing here.  This may move monitoring -> detected and clear the
        // evidence/window that made the board ready; it can never resolve an
        // incident, stamp resolved_at, or write a root cause.  Those remain
        // partner-only through close-incident.
        if (!opened && recorded)
          await c.query(
            `update ops.incident set observed_at = now(),
                    expires_at = now() + make_interval(hours => ${MONITORING_HOURS}),
                    state = case when state = 'monitoring' then 'detected' else state end,
                    recovery_evidence_ref = case when state = 'monitoring'
                                               then null else recovery_evidence_ref end,
                    monitoring_until = case when state = 'monitoring'
                                            then null else monitoring_until end,
                    next_action = case when state = 'monitoring'
                      then 'failed again during its watch — read get-incident ' || ref
                      else next_action end
              where id = $1`, [incidentId]);

        const count = await c.query(
          `select occurrence.occurrences as occurrences from ops.incident i
             ${INCIDENT_OCCURRENCE_JOIN_SQL}
            where i.id = $1`, [incidentId]);

        await writeEvent(c, actor, "open-incident", "incident", incidentId, {
          field: opened ? "state" : "observed_at",
          new: { ref, fingerprint: fingerprint.signature, opened },
          idempotency_key: args.idempotency_key,
          agent_rationale: observed || null,
        });

        return {
          ref, opened, occurrence_recorded: recorded, linked,
          occurrences: count.rows[0].occurrences,
          fingerprint: fingerprint.signature,
          severity: opened ? (args.severity || "SEV-3") : open.rows[0].severity,
          state: opened || (recorded && open.rows[0].state === "monitoring")
            ? "detected" : open.rows[0].state,
          note: opened
            ? null
            : "this fingerprint was already open — the occurrence was attached to the existing " +
              "incident rather than opening a second one",
        };
      }),
    },

    "triage-incident": {
      write: true,
      description:
        "Move one detected operational incident into triaged with a concrete next action and " +
        "a provisional business impact assessment. Use 'unknown' when impact has not been " +
        "established. This records a workflow disposition, not a root cause, severity judgment, " +
        "recovery, or closure. Read get-incident first to keep observed facts separate from " +
        "hypotheses. Only detected -> triaged is allowed; a second transition needs a new action.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        ref: { type: "string", pattern: INCIDENT_REF_PATTERN },
        next_action: { type: "string", description: "the concrete next investigation or mitigation step" },
        impact_assessment: { type: "string", description: "provisional business impact; 'unknown' is valid when evidence is insufficient" },
      }, required: ["idempotency_key", "ref", "next_action", "impact_assessment"] },
      handler: async (c, actor, args) => {
        // Serialize a same-key first attempt before withEnvelope's replay read.
        // Different keys still meet at the detected-state conditional update.
        if (args.idempotency_key)
          await c.query("select pg_advisory_xact_lock(hashtextextended($1,0))",
            [String(args.idempotency_key)]);
        return withEnvelope(c, actor, "triage-incident", args, async () => {
        const ref = String(args.ref || "").trim();
        const nextAction = String(args.next_action || "").trim();
        const impact = String(args.impact_assessment || "").trim();
        if (!INCIDENT_REF_RE.test(ref))
          throw new ToolError({ error: "invalid_incident_ref", ref });
        if (!nextAction || !impact)
          throw new ToolError({ error: "triage_details_required", ref,
            hint: "supply a next action and an impact assessment; use 'unknown' for impact when evidence is insufficient" });

        // The conditional update is the state guard, including against a
        // concurrent triage. No earlier read can safely substitute for it.
        const changed = await c.query(
          `update ops.incident
              set state = 'triaged', next_action = $2, business_impact = $3
            where ref = $1 and state = 'detected'
          returning id, ref, state, next_action, business_impact`,
          [ref, nextAction, impact]);
        if (!changed.rows.length) {
          const current = await c.query("select state from ops.incident where ref=$1", [ref]);
          throw new ToolError(current.rows.length
            ? { error: "incident_state_conflict", ref, expected: "detected", actual: current.rows[0].state }
            : { error: "no_such_incident", ref });
        }
        const row = changed.rows[0];
        await writeEvent(c, actor, "triage-incident", "incident", row.id, {
          field: "state", old: { state: "detected" },
          new: { state: "triaged", next_action: nextAction, business_impact: impact },
          idempotency_key: args.idempotency_key,
        });
        return { ref: row.ref, state: row.state, next_action: row.next_action,
          business_impact: row.business_impact };
        });
      },
    },

    "link-incident-work-request": {
      write: true,
      description:
        "Attach one same-tenant public Work Request to an incident as contextual evidence. The link " +
        "does not count as an occurrence and changes neither lifecycle. Same-key concurrent calls " +
        "serialize before the existing envelope replay boundary.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string", pattern: "^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$" },
        incident_ref: { type: "string", pattern: INCIDENT_REF_PATTERN },
        work_request: { type: "string", pattern: "^WR-[0-9]{1,12}$" },
      }, required: ["idempotency_key", "incident_ref", "work_request"] },
      handler: async (c, actor, args) => {
        const idempotencyKey = String(args.idempotency_key || "").trim().toLowerCase();
        const incidentRef = String(args.incident_ref || "").trim();
        const workRequest = String(args.work_request || "").trim();
        if (!/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(idempotencyKey))
          throw new ToolError({ error: "invalid_idempotency_key" });

        // withEnvelope reads before it writes. Serialize equal keys first so
        // two first calls cannot both miss the receipt and perform the body.
        await c.query("select pg_advisory_xact_lock(hashtextextended($1,0))", [idempotencyKey]);
        return withEnvelope(c, actor, "link-incident-work-request", {
          idempotency_key: idempotencyKey, incident_ref: incidentRef, work_request: workRequest,
        }, async () => {
          const incident = await c.query(
            "select id, ref from ops.incident where ref=$1", [incidentRef]);
          if (!incident.rows.length)
            throw new ToolError({ error: "incident_not_found", incident_ref: incidentRef });

          const target = await c.query(
            `select ref, state from ops.work_request_card($1::text,$2::text)
               /* incident:link-work-request-target */`,
            [workRequest, organizationTenantForActor(actor)]);
          if (!target.rows.length || !["captured", "triaged", "ready", "declined", "superseded"].includes(target.rows[0].state))
            throw new ToolError({ error: "work_request_not_found", work_request: workRequest });

          await c.query(
            "select pg_advisory_xact_lock(hashtextextended($1,0))",
            [`incident-work-request:${incidentRef}`]);
          const conflict = await c.query(
            `select ref from ops.incident_link
              where incident_id=$1 and kind='work_request' and ref<>$2
              order by ref limit 1`, [incident.rows[0].id, workRequest]);
          if (conflict.rows.length)
            throw new ToolError({ error: "incident_work_request_conflict", incident_ref: incidentRef,
              existing_work_request: conflict.rows[0].ref, requested_work_request: workRequest });

          const inserted = await c.query(
            `insert into ops.incident_link (incident_id,kind,ref,note)
             values ($1,'work_request',$2,'contextual evidence')
             on conflict do nothing returning incident_id`, [incident.rows[0].id, workRequest]);
          const linked = inserted.rows.length > 0;
          if (linked)
            await writeEvent(c, actor, "link-incident-work-request", "incident", incident.rows[0].id, {
              field: "contextual_work_request",
              new: { incident_ref: incidentRef, work_request: workRequest },
              idempotency_key: idempotencyKey,
            });
          return { ok: true, incident_ref: incidentRef, work_request: workRequest,
            linked, already_linked: !linked, link_kind: "work_request",
            relationship: "contextual_evidence", occurrence_effect: "none", lifecycle_effect: "none" };
        });
      },
    },

    "close-incident": {
      write: true, humanOnly: true,
      description:
        "Close one operational incident with an outcome. PARTNER AUTHORITY: Joe or Dell, in their own " +
        "interactive session — never an unsponsored runtime, never a machine credential. This is the " +
        "normal route, replacing the receipted break-glass database tap (CARR_BREAK_GLASS=1 " +
        "tools/db-tap.py run tools/ops-record.py resolve) that used to be the only way; break-glass " +
        "stays for emergencies. THE GUARDS ARE THE CLI'S, unchanged: a root cause is required, because " +
        "'close with an outcome' means the outcome is recorded and not that the row was cleared; " +
        "evidence is required and comes from the incident's own recovery evidence when a green run " +
        "already produced one, otherwise from you; and an incident still inside its monitoring window " +
        "does not close, because a green run says the symptom stopped rather than that the cause is " +
        "understood — pass allow_early with a reason, which is RECORDED ON THE INCIDENT as a fact. Read " +
        "get-incident first: the root cause is in the evidence trail.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        ref: { type: "string", description: "the incident ref, e.g. INC-20260823-01" },
        root_cause: { type: "string", description: "REQUIRED: what actually caused it, in your words. Not 'fixed' — what was wrong." },
        evidence: { type: "string", description: "what shows it is safe to close, when the incident carries none of its own: a run ref, a commit, a check name" },
        allow_early: { type: "string", description: "a reason the monitoring window cannot apply. Recorded as a fact on the incident, not merely accepted." },
      }, required: ["idempotency_key", "ref", "root_cause"] },
      handler: async (c, actor, args) => {
        requirePartner(actor);
        return withEnvelope(c, actor, "close-incident", args, async () => {
          const inc = await loadIncident(c, args.ref);
          const verdict = closePreconditions(
            { ...inc, duplicate_of_ref: inc.duplicate_of },
            { rootCause: args.root_cause, evidence: args.evidence,
              allowEarly: args.allow_early, now: inc.db_now });
          if (!verdict.ok)
            throw new ToolError({ error: "close_refused", ref: inc.ref, reason: verdict.error,
              state: inc.state,
              hint: "this is the same refusal tools/ops-record.py resolve gives — the verb did not " +
                    "loosen it, and neither should a break-glass run" });

          await c.query(
            `update ops.incident
                set state = 'resolved', resolved_at = now(),
                    monitoring_until = ${verdict.fields.stamp_monitoring_until_now ? "now()" : "monitoring_until"},
                    recovery_evidence_ref = $1, root_cause = $2,
                    next_action = 'review and record a followup disposition'
              where ref = $3 and state not in ('resolved','reviewed')`,
            [verdict.fields.recovery_evidence_ref, verdict.fields.root_cause, inc.ref]);

          // THE REASON AN EXCEPTION WAS ALLOWED BELONGS ON THE INCIDENT, not in
          // a shell history nobody reads back. ops-record.py's rule, kept
          // verbatim in behaviour.
          for (const fact of verdict.facts)
            await c.query(
              "insert into ops.incident_fact (incident_id, text, source_ref) values ($1,$2,$3)",
              [inc.id, fact, `verb:close-incident:${actor.slug}`]);

          await writeEvent(c, actor, "close-incident", "incident", inc.id, {
            field: "state", old: { state: inc.state }, new: { state: "resolved" },
            idempotency_key: args.idempotency_key,
            agent_rationale: verdict.fields.root_cause,
          });

          return {
            ref: inc.ref, state: "resolved",
            root_cause: verdict.fields.root_cause,
            recovery_evidence_ref: verdict.fields.recovery_evidence_ref,
            recorded_facts: verdict.facts,
            next_action: "review and record a followup disposition",
          };
        });
      },
    },

    "adjudicate-incident": {
      write: true, humanOnly: true,
      description:
        "A partner's judgment about what an incident IS: its severity, who holds it, and whether it is " +
        "the same operational event as another incident. PARTNER AUTHORITY: Joe or Dell only — the " +
        "collectors are granted exactly six columns on this table and severity is deliberately not one " +
        "of them, so a machine reading an exit code can report a recovery and can never reclassify. " +
        "IT DOES NOT CLOSE ANYTHING: recording that this is a duplicate of INC-X sets the pointer and " +
        "the next step, and close-incident is still the only path to resolved — where the canonical " +
        "incident then counts as the evidence, because the thing that shows this row is safe to close " +
        "is that another row carries the same event and its watch. Every change is recorded as a fact " +
        "on the incident, so the board's history says who decided what.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        ref: { type: "string", description: "the incident being adjudicated" },
        severity: { type: "string", enum: SEVERITIES },
        owner: { type: "string", enum: OWNERS, description: "joe or dell — an incident owned by nobody in particular is one nobody picks up" },
        duplicate_of: { type: "string", description: "the ref of the incident that carries this same operational event, its investigation and its monitoring window" },
        note: { type: "string", description: "why — appended to each fact this writes" },
      }, required: ["idempotency_key", "ref"] },
      handler: async (c, actor, args) => {
        requirePartner(actor);
        return withEnvelope(c, actor, "adjudicate-incident", args, async () => {
          const inc = await loadIncident(c, args.ref);
          if (inc.state === "resolved" || inc.state === "reviewed")
            throw new ToolError({ error: "already_closed", ref: inc.ref, state: inc.state,
              hint: "adjudication is a judgment about a live incident; a closed one keeps the reading " +
                    "it was closed under" });

          let canonical = null;
          if (args.duplicate_of !== undefined && args.duplicate_of !== null) {
            const target = await c.query(
              "select id, ref, state, duplicate_of_id from ops.incident where ref = $1",
              [String(args.duplicate_of).trim()]);
            if (!target.rows.length)
              throw new ToolError({ error: "no_such_incident", ref: args.duplicate_of,
                hint: "duplicate_of names the incident that CARRIES the event — find it with incident-board" });
            canonical = target.rows[0];
          }

          const change = adjudicationChanges(inc, {
            severity: args.severity, owner: args.owner, canonical,
            note: String(args.note || "").trim() || null,
          });
          if (!change.ok)
            throw new ToolError({ error: "adjudication_refused", ref: inc.ref, reason: change.error });

          const sets = [];
          const params = [];
          for (const [column, value] of Object.entries(change.fields)) {
            params.push(value);
            sets.push(`${column} = $${params.length}`);
          }
          params.push(inc.ref);
          await c.query(
            `update ops.incident set ${sets.join(", ")} where ref = $${params.length}`, params);

          for (const fact of change.facts)
            await c.query(
              "insert into ops.incident_fact (incident_id, text, source_ref) values ($1,$2,$3)",
              [inc.id, fact, `verb:adjudicate-incident:${actor.slug}`]);

          await writeEvent(c, actor, "adjudicate-incident", "incident", inc.id, {
            field: Object.keys(change.fields).join(","),
            old: { severity: inc.severity, owner_actor: inc.owner_actor, duplicate_of: inc.duplicate_of },
            new: { ...change.fields, duplicate_of: canonical?.ref || inc.duplicate_of },
            idempotency_key: args.idempotency_key,
            agent_rationale: String(args.note || "").trim() || null,
          });

          return {
            ref: inc.ref,
            severity: change.fields.severity || inc.severity,
            owner: change.fields.owner_actor || inc.owner_actor,
            duplicate_of: canonical?.ref || inc.duplicate_of,
            state: inc.state,
            recorded_facts: change.facts,
            note: canonical
              ? `recorded as a duplicate — close it with close-incident, which will take ${canonical.ref} as its evidence`
              : null,
          };
        });
      },
    },
  };
}
