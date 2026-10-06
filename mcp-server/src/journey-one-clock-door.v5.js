// DoctorCRE v5 slice V5-M01: THE LIVE DOOR TO THE JOURNEY ONE CLOCK.
//
// journey-one-clock-runtime.v5.js is the one seat where the admitted-minimum
// inventory, the kernel and the clock history meet, and until this file NOTHING
// OUTSIDE A TEST CALLED IT. This file is the door: two registered verbs and
// nothing else.
//
//   read-journey-one-clock     READ ONLY. One authoritative clock scope: the
//                              admitted-minimum inventory the origin would be
//                              selected from, the clock bound to that scope (or
//                              none), and — only when one is bound — its head
//                              revision exactly as the kernel recorded it.
//                              `clock_started` is DERIVED from the record, never
//                              a constant.
//   advance-journey-one-clock  WRITE, FAIL-CLOSED, SEAT-RESTRICTED. Takes ONE
//                              argument, an idempotency key, and runs exactly one
//                              runtime advance through a TRUSTED INSTALLATION the
//                              server supplies. No Worker supplies one, so in
//                              every deployed environment this verb refuses by
//                              name before it issues a single query.
//
// ---------------------------------------------------------------------------
// WHY THE ADVANCE DOOR REFUSES EVERYWHERE TODAY, stated as the fact it is.
//
// The runtime needs an already-constructed kernel — createJourneyOneClock({
// verifySnapshot }) — and a verifier that authenticates the projection the record
// layer composed. No such verifier exists in this repository or in any Worker.
// That absence is now the ONLY thing standing between an advance and a started
// production clock: A00's oracle seat has admitted a passing foundation-assurance-
// minimum receipt into the production inventory, so a first advance with a
// working verifier WOULD select that receipt as the origin and file the clock
// (`created_clock: true`). The clock origin belongs to A00's minimum receipt and
// the decision to start it is not this slice's. So the door is wired, reachable,
// schema-registered and exercised end to end on fixtures, and in production it
// answers `journey_one_clock_installation_unavailable` ahead of any query.
//
// Landing the verifier is a change to ONE seam — the installation resolver
// handed to journeyOneClockDoorTools() — and not a fresh set of decisions.
//
// ---------------------------------------------------------------------------
// WHY THE CALLER SUPPLIES ONLY AN IDEMPOTENCY KEY.
//
// The runtime takes `as_of`, `pauses`, `amendments`, `completion` and
// `completion_expectation` as trusted inputs. At a public door every one of them
// is a way to lie to the kernel:
//   * a caller-chosen `as_of` forty days ahead records a MISS that never
//     happened, and a miss is sticky by decision Q008.D1 — it can never be
//     withdrawn;
//   * a caller-chosen `completion` stops the clock on a terminus nobody produced;
//   * a caller-chosen `pauses` spends the accepted 120-hour budget.
// So all five come from the installation's own trusted reader
// (`readAdvanceInputs`), the instant included, and the request carries none of
// them. `history` and `expected_prior_history_digest` were already derived by the
// runtime and are refused there by name; `clock_ref` is not accepted either.
//
// ---------------------------------------------------------------------------
// WHAT THIS FILE IS NOT.
//
//   * NOT A SECOND LOOP. The read-compose-evaluate-bind-append order lives in the
//     runtime and in createJourneyOneClockRecorder; this file calls advance() once
//     and forwards its closed receipt unchanged, kernel verdict included.
//   * NOT A DECIDER. It computes no deadline, judges no receipt, holds no pause
//     budget and reads no live clock. `clock_started` on a read is whether the
//     record holds a bound clock whose rebuilt head carries the kernel's own
//     clock_started event — a fact read back, not a status re-decided.
//   * NOT A PRODUCER. It admits nothing, issues nothing and accepts nothing.
//   * NOT A JOB. Nothing here runs unattended; an advance is one call.

import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { assertNoSelfAssertedAuthority } from "./benchmark-acceptance-store.v5.js";
import { JOURNEY_ONE_CLOCK_SCHEMA } from "./journey-one-clock.v5.js";
import {
  JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE, createJourneyOneClockStore,
  createPostgresJourneyOneClockJournal, deriveJourneyOneClockWriter,
  journeyOneClockScopeBinding,
} from "./journey-one-clock-store.v5.js";
import {
  JOURNEY_ONE_MINIMUM_INPUT_STORE_CANNOT_PROVE,
  createJourneyOneClockMinimumInputStore, createPostgresJourneyOneMinimumAdmissionJournal,
} from "./journey-one-clock-input-store.v5.js";
import {
  JOURNEY_ONE_CLOCK_ADVANCE_FIELDS, JOURNEY_ONE_CLOCK_RUNTIME_CANNOT_PROVE,
  createJourneyOneClockRuntime,
} from "./journey-one-clock-runtime.v5.js";

export const JOURNEY_ONE_CLOCK_DOOR_READ_SCHEMA = "doctorcre-v5-journey-one-clock-door-read.v1";
export const JOURNEY_ONE_CLOCK_DOOR_ADVANCE_SCHEMA =
  "doctorcre-v5-journey-one-clock-door-advance.v1";
export const JOURNEY_ONE_CLOCK_DOOR_INTEGRATION_SCHEMA =
  "doctorcre-v5-journey-one-clock-door-integration.v1";

export const JOURNEY_ONE_CLOCK_READ_VERB = "read-journey-one-clock";
export const JOURNEY_ONE_CLOCK_ADVANCE_VERB = "advance-journey-one-clock";

/** The two installation kinds a resolver may hand back. Anything else refuses. */
export const JOURNEY_ONE_CLOCK_INSTALLATION_KINDS = Object.freeze([
  "fixture", "trusted_server_verifier",
]);

/** The advance fields that are the request's own. Everything else is installed. */
const REQUEST_OWNED_ADVANCE_FIELDS = Object.freeze(["clock_ref", "idempotency_key"]);

/**
 * The five advance inputs the INSTALLATION reads, C-sorted, DERIVED from the
 * runtime's own advance field set rather than restated. A field the runtime gains
 * is then either installed or request-owned, and never silently neither.
 */
export const JOURNEY_ONE_CLOCK_INSTALLED_ADVANCE_FIELDS = Object.freeze(
  JOURNEY_ONE_CLOCK_ADVANCE_FIELDS.filter(field => !REQUEST_OWNED_ADVANCE_FIELDS.includes(field)));
{
  const covered = [...JOURNEY_ONE_CLOCK_INSTALLED_ADVANCE_FIELDS, ...REQUEST_OWNED_ADVANCE_FIELDS].sort();
  if (covered.join("|") !== [...JOURNEY_ONE_CLOCK_ADVANCE_FIELDS].sort().join("|") ||
      !JOURNEY_ONE_CLOCK_INSTALLED_ADVANCE_FIELDS.includes("as_of")) {
    throw new Error(
      "journey-one-clock-door: the installed and request-owned advance fields no longer partition the runtime's advance field set");
  }
}

/** The closed field set of a door read, C-sorted, asserted on the way out. */
export const JOURNEY_ONE_CLOCK_DOOR_READ_FIELDS = Object.freeze([
  "clock", "clock_key", "clock_scope_key", "clock_started", "clock_started_basis",
  "deadline_accepted_by_record_layer", "effects", "input_store_cannot_prove", "inventory",
  "ok", "record_layer_cannot_prove", "schema_version",
]);

export const JOURNEY_ONE_CLOCK_DOOR_CANNOT_PROVE = Object.freeze([
  "that a clock SHOULD have started. `clock_started: false` says this record layer holds no clock bound to the scope; it is not a finding that the origin receipt is unusable, and `true` is not a finding that the start was authorized -- the kernel selects the origin, and the decision to run the first advance belongs to whoever installs the verifier",
  "that the projection an advance was computed from was authentic. The advance door refuses unless trusted server code installed a verifier; it holds no verifier of its own and accepts none from a request",
  "anything the runtime, the clock store or the input store cannot prove. Their own lists travel on every result unchanged",
]);

const SHA256_REF = /^sha256:[0-9a-f]{64}$/;

export class JourneyOneClockDoorError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "JourneyOneClockDoorError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function refuse(code, message, detail) {
  throw new JourneyOneClockDoorError(code, message, detail);
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

const copy = value => JSON.parse(JSON.stringify(value));

function closedShape(value, fields, path) {
  if (!isPlainObject(value)) refuse("invalid_shape", `${path} must be an object`, { path });
  const keys = Object.keys(value);
  if (keys.length !== fields.length || fields.some(field => !Object.hasOwn(value, field))) {
    refuse("closed_shape", `${path} must carry exactly its declared fields`,
      { path, expected: [...fields], actual: [...keys].sort() });
  }
  return value;
}

// ---------------------------------------------------------------------------
// THE READ DOOR.
// ---------------------------------------------------------------------------

/**
 * Read one authoritative clock scope, deriving whether its clock has started.
 *
 * `input_store` reads the admitted-minimum inventory; `clock_store` reads clock
 * histories; `clock_journal` answers which clock, if any, the scope is bound to.
 * All three are READ here and none is written: the stores are constructed without
 * a scope binding, which is exactly the construction that cannot record.
 *
 * THE SCOPE IS CHECKED AGAINST ITSELF. An inventory's stored scope must derive the
 * key it is filed under, and a bound clock's readback must name the scope it was
 * looked up by. Either disagreement is a record that no longer says what it is,
 * and it refuses rather than reporting a clock state for the wrong program.
 */
export async function readJourneyOneClockDoor({
  input_store, clock_store, clock_journal, clock_scope_key } = {}) {
  if (typeof clock_scope_key !== "string" || !SHA256_REF.test(clock_scope_key)) {
    refuse("invalid_reference", "clock_scope_key must be a sha256: reference",
      { path: "clock_scope_key", actual: clock_scope_key ?? null });
  }
  if (!input_store || typeof input_store.read !== "function" ||
      !clock_store || typeof clock_store.read !== "function" ||
      !clock_journal || typeof clock_journal.readScopeBindings !== "function") {
    refuse("invalid_shape",
      "the read door needs a minimum input store, a clock store and the clock journal's scope-binding read",
      { path: "read_door" });
  }

  // 1. THE INVENTORY THE ORIGIN WOULD BE SELECTED FROM. Rebuilt and re-hashed by
  //    the input store's own readback; a tampered row refuses there.
  const inventory = await input_store.read(clock_scope_key);
  if (inventory.exists === true) {
    const stored = journeyOneClockScopeBinding(inventory.clock_scope);
    if (stored.clock_scope_key !== clock_scope_key) {
      refuse("clock_door_inventory_scope_mismatch",
        "the inventory filed under this scope key carries a scope that derives a different key, so it is not this program's inventory and no clock state is reported for it",
        { invariant: "j1_clock_scope_binds_one_clock",
          clock_scope_key, stored_scope_derives: stored.clock_scope_key });
    }
  }

  // 2. WHICH CLOCK, IF ANY, THIS SCOPE IS BOUND TO. The same definer read the
  //    runtime's first attempt makes; it takes no lock and binds nothing.
  const bindings = await clock_journal.readScopeBindings({ clockScopeKey: clock_scope_key });
  const clockKey = bindings?.by_scope?.clock_key ?? null;

  // 3. THE HEAD, ONLY WHEN ONE IS BOUND, and exactly as the store rebuilt it.
  let clock = null;
  if (clockKey !== null) {
    const readback = await clock_store.read(clockKey);
    if (readback?.exists !== true) {
      refuse("clock_door_bound_clock_unreadable",
        "this scope names a clock the record layer cannot produce a history for. Reporting it as not started would turn a lost clock into a fresh one, which is a reset wearing a new address",
        { invariant: "j1_clock_scope_binds_one_clock", clock_scope_key, clock_key: clockKey });
    }
    if (readback.clock_scope_key !== clock_scope_key) {
      refuse("clock_door_bound_clock_scope_mismatch",
        "the clock this scope is bound to reads back as bound to another scope",
        { invariant: "j1_clock_scope_binds_one_clock", clock_scope_key, clock_key: clockKey,
          readback_clock_scope_key: readback.clock_scope_key ?? null });
    }
    clock = {
      clock_key: readback.clock_key,
      state_schema_version: readback.state_schema_version,
      revision_count: readback.revision_count,
      head_revision_ordinal: readback.head_revision_ordinal,
      history_digest: readback.history_digest,
      recorded_at: readback.recorded_at,
      evaluated_at: readback.evaluated_at,
      // THE KERNEL'S RECORD, UNCHANGED. Every decision (status, due_at, miss_at,
      // paused_ms, the completion instants) and every event's `at` is the value
      // the kernel wrote; this door re-decides none of it.
      history: copy(readback.history),
      // The whole revision chain, oldest first, each with the digest it was
      // written under. Append-only is what makes a miss survive a late
      // completion, and this is where a reader sees that it did.
      revisions: copy(readback.revisions),
      provenance: copy(readback.provenance ?? null),
    };
  }

  // 4. STARTED IS READ, NOT DECIDED. A bound clock whose rebuilt head carries the
  //    kernel's own clock_started event, or no clock at all.
  const startedEvent = clock === null ? null
    : (clock.history.events ?? []).find(event => event?.type === "clock_started") ?? null;
  if (clock !== null && startedEvent === null) {
    refuse("clock_door_head_without_start",
      "a bound clock's head carries no clock_started event, which the kernel never writes; nothing is reported for it",
      { invariant: "j1_clock_events_are_append_only", clock_key: clockKey });
  }

  const result = {
    ok: true,
    schema_version: JOURNEY_ONE_CLOCK_DOOR_READ_SCHEMA,
    clock_scope_key,
    inventory: inventory.exists === true ? {
      exists: true,
      clock_scope_ref: inventory.clock_scope_ref,
      admission_count: inventory.admission_count,
      head_admission_digest: inventory.head_admission_digest,
      minimum_receipt_ttl_policy_ms: inventory.minimum_receipt_ttl_policy_ms,
      admissions: inventory.admissions.map(admission => ({
        admission_ordinal: admission.admission_ordinal,
        admitted_at: admission.admitted_at,
        receipt_digest: admission.receipt_digest,
        status: admission.status,
        observed_at: admission.observed_at,
        ttl_expires_at: admission.ttl_expires_at,
        admission_digest: admission.admission_digest,
      })),
    } : { exists: false },
    clock_key: clockKey,
    clock_started: startedEvent !== null,
    clock_started_basis: startedEvent === null
      ? "no_clock_bound_to_this_scope"
      : "bound_clock_head_records_the_kernel_clock_started_event",
    clock,
    deadline_accepted_by_record_layer: false,
    record_layer_cannot_prove: [...JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE,
      ...JOURNEY_ONE_CLOCK_DOOR_CANNOT_PROVE],
    input_store_cannot_prove: [...JOURNEY_ONE_MINIMUM_INPUT_STORE_CANNOT_PROVE],
    effects: V5_NO_EFFECTS,
  };
  closedShape(result, JOURNEY_ONE_CLOCK_DOOR_READ_FIELDS, "door_read");
  return deepFreeze(result);
}

// ---------------------------------------------------------------------------
// THE ADVANCE DOOR.
// ---------------------------------------------------------------------------

/** The resolver every deployed Worker gets: there is no installation. */
export const JOURNEY_ONE_CLOCK_NO_INSTALLATION = Object.freeze(() => null);

/**
 * ADMIT ONE ADVANCE REQUEST, touching nothing: no connection is taken and no
 * query can be issued, because none is in reach. The verb runs this BEFORE its
 * idempotency envelope, so in a Worker with no installation the refusal happens
 * before even the envelope's own replay lookup.
 *
 * ORDER IS DELIBERATE AND EACH STEP IS A REFUSAL, NOT A WARNING:
 *   1. the request's keys meet A00's self-asserted-authority guard, then the
 *      closed request shape — one idempotency key and nothing else;
 *   2. the writer is derived from the LIVE actor;
 *   3. the installation is resolved WITHOUT the connection. None exists in any
 *      deployed Worker, so this is where production stops;
 *   4. the installation's kind and seat are checked, and the live actor must BE
 *      that seat — a second authenticated writer is refused by name.
 */
export function admitJourneyOneClockAdvance({
  actor, args, resolve_installation = JOURNEY_ONE_CLOCK_NO_INSTALLATION } = {}) {
  // 1. THE REQUEST.
  if (!isPlainObject(args)) refuse("invalid_shape", "advance takes one idempotency key", { path: "args" });
  assertNoSelfAssertedAuthority(Object.fromEntries(Object.keys(args).map(key => [key, null])),
    JOURNEY_ONE_CLOCK_ADVANCE_VERB);
  closedShape(args, ["idempotency_key"], "args");

  // 2. THE WRITER, from the live actor and nothing else.
  const writer = deriveJourneyOneClockWriter(actor);

  // 3. THE INSTALLATION, resolved without the connection.
  if (typeof resolve_installation !== "function") {
    refuse("journey_one_clock_installation_unavailable",
      "no trusted Journey 1 clock installation resolver is bound to this door", { path: "resolve_installation" });
  }
  const installation = resolve_installation();
  if (installation === null || installation === undefined) {
    refuse("journey_one_clock_installation_unavailable",
      "no trusted Journey 1 clock installation is bound in this environment: no verifier authenticates the projection the record layer composes, so no kernel can judge it and no clock can be advanced here. This refusal happens before any query, so nothing is half-started",
      { resolved: false, clock_started: false,
        blocked_by: "binding:journey-one-clock-authenticated-projection" });
  }
  if (!isPlainObject(installation) ||
      !JOURNEY_ONE_CLOCK_INSTALLATION_KINDS.includes(installation.kind) ||
      typeof installation.advancing_seat !== "string" || installation.advancing_seat.length === 0 ||
      typeof installation.open !== "function") {
    refuse("journey_one_clock_installation_invalid",
      "a Journey 1 clock installation names its kind, the one seat that may advance through it, and how to open it",
      { kinds: [...JOURNEY_ONE_CLOCK_INSTALLATION_KINDS] });
  }

  // 4. THE SEAT.
  if (writer.actor_id !== installation.advancing_seat) {
    refuse("journey_one_clock_advance_seat_required",
      "this installation advances the clock for one seat, and the live actor is not it",
      { advancing_seat: installation.advancing_seat, actor_id: writer.actor_id });
  }
  return Object.freeze({ writer, installation });
}

/**
 * Run one ADMITTED advance through its installation:
 *   5. the installation is opened, and what it opened is checked: a store built
 *      over this same actor, and a fixture never over a durable journal;
 *   6. the five trusted inputs are read from the installation, closed-shape;
 *   7. the runtime advances once, and its closed receipt is returned unchanged.
 */
export async function advanceAdmittedJourneyOneClock({ c, actor, args, admitted } = {}) {
  const { writer, installation } = admitted ?? {};
  if (!isPlainObject(writer) || !isPlainObject(installation)) {
    refuse("journey_one_clock_advance_not_admitted",
      "an advance runs only after admitJourneyOneClockAdvance has admitted its request", { path: "admitted" });
  }

  // 5. WHAT THE INSTALLATION OPENED.
  const parts = await installation.open({ c, actor });
  if (!isPlainObject(parts) || typeof parts.readAdvanceInputs !== "function") {
    refuse("journey_one_clock_installation_invalid",
      "an opened installation supplies the runtime's parts and its own trusted advance-input reader",
      { path: "installation.open" });
  }
  const { composer, clock, clock_store, present_projection, verifier_ref } = parts;
  if (clock_store?.writer?.actor_id !== writer.actor_id) {
    refuse("journey_one_clock_store_actor_mismatch",
      "the installation's clock store writes as another actor than the one advancing",
      { advancing_as_actor_id: writer.actor_id,
        store_writer_actor_id: clock_store?.writer?.actor_id ?? null });
  }
  if (installation.kind === "fixture" && clock_store.journal_is_durable !== false) {
    refuse("journey_one_clock_fixture_installation_on_durable_journal",
      "a fixture installation authenticates with a test verifier and may never write to a durable journal",
      { installation_kind: installation.kind });
  }
  const runtime = createJourneyOneClockRuntime({
    composer, clock, clock_store, present_projection, verifier_ref });

  // 6. THE TRUSTED INPUTS, the instant included.
  const installed = await parts.readAdvanceInputs();
  closedShape(installed, JOURNEY_ONE_CLOCK_INSTALLED_ADVANCE_FIELDS, "installation.readAdvanceInputs");

  // 7. ONE ADVANCE.
  const receipt = await runtime.advance({
    ...copy(installed), clock_ref: null, idempotency_key: args.idempotency_key });

  return deepFreeze({
    ok: true,
    schema_version: JOURNEY_ONE_CLOCK_DOOR_ADVANCE_SCHEMA,
    installation_kind: installation.kind,
    advancing_seat: installation.advancing_seat,
    // DERIVED FROM THE RECEIPT, per call: this call wrote the revision that
    // created the clock. A replay of that creation did not.
    clock_started_by_this_call: receipt.created_clock === true && receipt.appended === true,
    advance: receipt,
    door_cannot_prove: [...JOURNEY_ONE_CLOCK_DOOR_CANNOT_PROVE],
  });
}

/** Admission then advance, in one call: the path the verb takes, minus its envelope. */
export async function advanceJourneyOneClockDoor({
  c, actor, args, resolve_installation = JOURNEY_ONE_CLOCK_NO_INSTALLATION } = {}) {
  const admitted = admitJourneyOneClockAdvance({ actor, args, resolve_installation });
  return advanceAdmittedJourneyOneClock({ c, actor, args, admitted });
}

/** What the door joins, what it refuses, and why it cannot advance anywhere yet. */
export function journeyOneClockDoorIntegrationRequirements() {
  return deepFreeze({
    schema_version: JOURNEY_ONE_CLOCK_DOOR_INTEGRATION_SCHEMA,
    kernel_state_schema_version: JOURNEY_ONE_CLOCK_SCHEMA,
    verbs: [JOURNEY_ONE_CLOCK_READ_VERB, JOURNEY_ONE_CLOCK_ADVANCE_VERB],
    installed_advance_fields: [...JOURNEY_ONE_CLOCK_INSTALLED_ADVANCE_FIELDS],
    request_fields: ["idempotency_key"],
    installation_bound_in_deployed_workers: false,
    blocked_by: [
      "binding:journey-one-clock-authenticated-projection — no verifier authenticates the composed projection, so no kernel can be constructed for a real one. The advance door refuses journey_one_clock_installation_unavailable before any query in every deployed Worker.",
      "the terminus producer for step:j1-kernel-production-outcome, so an installation has no authenticated completion to read.",
    ],
    explicitly_refused: [
      "a caller-supplied as_of, pause, amendment, completion, completion expectation, history, prior digest or clock_ref",
      "a caller-supplied verifier, installation or seat",
      "a fixture installation over a durable journal",
      "a scheduled job: nothing here advances unattended",
    ],
    cannot_prove: [...JOURNEY_ONE_CLOCK_DOOR_CANNOT_PROVE, ...JOURNEY_ONE_CLOCK_RUNTIME_CANNOT_PROVE],
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// THE TWO VERBS.
// ---------------------------------------------------------------------------

const TRANSLATED_ERROR_NAMES = Object.freeze([
  "JourneyOneClockDoorError", "JourneyOneClockRuntimeError", "JourneyOneClockStoreError",
  "JourneyOneMinimumInputStoreError", "JourneyOneClockError", "BenchmarkAcceptanceStoreError",
  "BenchmarkMinimumError",
]);

/**
 * `resolve_installation` is SERVER CODE'S ARGUMENT, never a request's. tools.js
 * registers this door without one, so every deployed Worker gets
 * JOURNEY_ONE_CLOCK_NO_INSTALLATION and the advance verb refuses before any query.
 */
export function journeyOneClockDoorTools({
  withEnvelope, ToolError, resolve_installation = JOURNEY_ONE_CLOCK_NO_INSTALLATION }) {
  const asToolError = (error) => {
    if (TRANSLATED_ERROR_NAMES.includes(error?.name)) {
      throw new ToolError({ error: error.code, message: error.message,
        ...(error.detail !== undefined ? { detail: error.detail } : {}) });
    }
    throw error;
  };

  return {
    [JOURNEY_ONE_CLOCK_READ_VERB]: {
      write: false,
      description: "Read the DoctorCRE v5 Journey 1 clock for one authoritative clock scope, with no effect. Returns the admitted foundation-assurance-minimum inventory the kernel would select an origin from (each admission rebuilt and re-hashed), which clock the scope is bound to, and whether that clock has started -- derived from the record, not asserted: clock_started is true only when a bound clock's rebuilt head carries the kernel's own clock_started event, and false with basis no_clock_bound_to_this_scope otherwise. When a clock exists it returns the head history exactly as the kernel recorded it (status, due_at, miss_at, paused_ms, completion instants and every event's observed instant) and the full append-only revision chain with each revision's digest. It starts, advances and pauses nothing, and says in its own fields what this record layer cannot prove.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { clock_scope_key: { type: "string", pattern: "^sha256:[0-9a-f]{64}$" } },
        required: ["clock_scope_key"],
      },
      handler: async (c, actor, args) => {
        try {
          const query = (sql, params) => c.query(sql, params);
          const clockJournal = createPostgresJourneyOneClockJournal({ query });
          return await readJourneyOneClockDoor({
            input_store: createJourneyOneClockMinimumInputStore({
              journal: createPostgresJourneyOneMinimumAdmissionJournal({ query }), actor }),
            clock_store: createJourneyOneClockStore({ journal: clockJournal, actor }),
            clock_journal: clockJournal,
            clock_scope_key: args.clock_scope_key,
          });
        } catch (error) { return asToolError(error); }
      },
    },

    [JOURNEY_ONE_CLOCK_ADVANCE_VERB]: {
      write: true,
      description: "REFUSES IN EVERY DEPLOYED WORKER, BY DESIGN: advance the DoctorCRE v5 Journey 1 clock by one kernel revision through a trusted installation the server binds. No Worker binds one -- no verifier authenticates the projection the record layer composes -- so this verb refuses journey_one_clock_installation_unavailable before it issues any query, and it cannot start, advance or pause the production clock. The caller supplies ONLY a fresh idempotency_key: the evaluation instant, pauses, amendments, completion and completion expectation come from the installation's own trusted reader, because a caller-chosen instant could record a sticky miss that never happened. Where an installation is bound, only its one named seat may advance, the clock store must write as that same live actor, and a fixture installation may never touch a durable journal. The advance itself is journey-one-clock-runtime.v5.js's: head read once, projection composed from the admitted inventory, kernel evaluated, identity proven, revision appended by compare-and-swap, and its receipt returned unchanged with the kernel's own verdict. It grants no dispatch, activation or execution authority and accepts no deadline.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { idempotency_key: { type: "string" } },
        required: ["idempotency_key"],
      },
      handler: async (c, actor, args) => {
        // ADMISSION FIRST, OUTSIDE THE ENVELOPE. It holds no connection, so in a
        // Worker with no installation the refusal precedes even the envelope's
        // replay lookup: this verb issues no query at all there.
        let admitted;
        try {
          admitted = admitJourneyOneClockAdvance({ actor, args, resolve_installation });
        } catch (error) { return asToolError(error); }
        return withEnvelope(c, actor, JOURNEY_ONE_CLOCK_ADVANCE_VERB, args, async () => {
          try {
            return await advanceAdmittedJourneyOneClock({ c, actor, args, admitted });
          } catch (error) { return asToolError(error); }
        });
      },
    },
  };
}
