// DoctorCRE v5 slice V5-A02, the seam half: THE THREE STORES, AND NOTHING ELSE.
//
// This file fetches rows. It decides nothing. Every function here either returns
// the rows a store actually holds for one addressed query, or throws
// SeamStoreUnreachable naming what it could not reach. It contains no
// classification, no comparison, no threshold and no vocabulary a consumer could
// act on — those live as MODULE-PRIVATE functions inside
// gate-zero-seam-readers.v5.js, which is the only module that imports this one.
//
// WHY THE SPLIT IS THE POINT. The defect this slice exists to prevent is a
// function that turns a description of evidence into an outcome. A fetcher that
// also judges can be handed a fabricated connection and will judge it. So the
// I/O is here, exported and judgment-free; the judgment is private to the reader
// that fetched the rows, and is exported by nothing.
//
// NO CALLER EVER SUPPLIES A CONNECTION. There is no handle parameter, no client
// parameter, no `env` parameter and no injectable opener. The query arguments
// below ADDRESS a row — which work request, which service, which commit — and
// addressing is not asserting. A caller can say which row to look at; it can
// never say what is in it.
//
// WHERE THE CONNECTION COMES FROM, and why that is not the env override the
// ruling table forbids. The RULING is the switch: while a seam's decision id is
// null its reader never calls into this file at all. What the environment
// supplies is only WHERE the ruled store lives — the same read-only DSN and the
// same GitHub credentials every other ops-side reader in this repository uses.
// An absent DSN is not a fallback and not a degraded mode: it throws, the reader
// reports unreachable, and nothing optimistic is returned. WHICH REPOSITORY the
// checks store serves is NOT part of that: see AUTHORITATIVE_REPOSITORY below.
//
// THESE RUN OPS-SIDE, NOT IN THE WORKER. Gate Zero is a control-plane gate; its
// readers run in the ops/CI process, where `process.env` and a Postgres socket
// exist. `pg` is imported DYNAMICALLY inside the two database functions so the
// Worker bundle never pulls it in through this module's import graph.
//
// ---------------------------------------------------------------------------
// THE TWO INVARIANTS THIS FILE OWES ITS CALLERS, both of them whole-file rules
// rather than a habit applied where somebody remembered.
//
// (1) NO STORE TEXT REACHES A CALLER. Every value in every row this file returns
//     is one of exactly three things: a CONSTANT written in this file, a value
//     that MATCHED A TOTAL PATTERN owned by this file, or a DIGEST of store
//     text. Nothing else travels. A row read out of Postgres or off the GitHub
//     wire is text nobody here controls — a service key, a check name, an
//     evidence ref, a status word — and a reader that put it in an answer would
//     be handing a consumer a word the store chose. Equality still works, which
//     is all any derivation asks of these fields: two digests are equal exactly
//     when the two strings were.
//
// (2) NOTHING BUT A REGISTERED REFUSAL LEAVES AN EXPORT. Every exported callable
//     here is wrapped by `guarded`, one boundary, applied in one place. Whatever
//     is thrown underneath it — a native TypeError with the CALLER'S frames in
//     its stack, `throw "allow"`, `throw true`, a Proxy whose traps throw — is
//     caught and replaced by a SeamStoreUnreachable carrying a registered store
//     token and a registered reason, whose own fields are non-writable data
//     properties and whose `stack` is a fixed string this file wrote. A caller
//     function named `green` therefore never appears anywhere in what comes
//     back out, because no engine-produced stack ever does.

/**
 * THE TWO CLOSED REGISTRIES, AND WHY NEITHER IS EXPORTED.
 *
 * A reader puts this error's `because` straight into its answer, so every byte
 * of it has to be a byte this file wrote. An upstream driver message is text
 * nobody here controls — it can carry a fragment of a DSN, a host name, or a
 * word the privileged-word sweep closes over. A CALLER'S argument is worse:
 * this class is exported, so anything can construct it with anything.
 *
 * So the constructor is a FILTER, not a formatter. `storeRef` and `because` are
 * looked up in the frozen registries below BY IDENTITY, and the error carries
 * the registered value that matched — never the argument. An unregistered
 * argument is not quoted back in a complaint about itself, which is the escape
 * the ninth review round found: it is replaced by the registered unknown-store
 * token and the registered not-a-registered-reason phrase, so the refusal is
 * still a refusal and still says nothing the caller wrote.
 *
 * Neither registry is exported. A consumer that wants to know what this file
 * can say reads this file; an importer that could enumerate the set could
 * assemble a message out of it and hand it back in.
 */
import { digest } from "./artifact-trust.js";
import { closedCallable } from "./closed-callable.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";

const STORE_TOKENS = Object.freeze({
  predecessorOutcome: "record-layer:work-request-outcome-feedback",
  schedulerLedger: "control-plane:ops.service+ops.run",
  checkConclusion: "github:checks",
  candidateBuildRecord: "control-plane:ops.candidate-build-record",
  unregistered: "a-store-this-file-does-not-serve",
});

/**
 * THE MARKER OF AN AUTHENTICATED MAKER, in the one spelling tools/ops-record.py
 * writes. It is a literal here rather than a parameter for the same reason the
 * store tokens are: a reader whose admission predicate a caller could choose
 * admits whatever that caller chose.
 */
const MAKER_AUTHORITY_PREFIX = "ops.authority-principal:";

const UNREACHABLE_REASONS = Object.freeze({
  answerDidNotParse: "the checks source answer did not parse",
  credentialsNotConfigured: "the checks source credentials are not configured in this process",
  sourceRefused: "the checks source refused the request",
  sourceNotReachable: "the checks source was not reachable",
  clientNotAvailable: "the database client is not available in this process",
  targetNotConfigured: "the connection target for this store is not configured in this process",
  queryDidNotFinish: "the query did not finish",
  queryNotAddressed: "the query did not address a row",
  foreignRepository: "the configured checks repository is not the one this file serves",
  callDidNotFinish: "the call did not finish",
  shaNotAddressed: "the addressed head sha is not the shape this file serves",
  candidateRecordAmbiguous:
    "more than one authenticated candidate build row answers this head sha",
  notRegistered: "the reason this store was unreachable is not a registered one",
});

const REGISTERED_STORE_TOKENS = Object.freeze(Object.values(STORE_TOKENS));
const REGISTERED_REASONS = Object.freeze(Object.values(UNREACHABLE_REASONS));

/**
 * THE UNDERLYING CAUSE IS DROPPED, NOT CARRIED. An earlier draft kept it on
 * `error.cause` "for a human reading a log", and that is precisely how a
 * caller-supplied object reached the public surface: `new SeamStoreUnreachable(
 * anything, anything, { ok: true })` handed its own third argument straight
 * back. What survives instead is one of the four codes below, decided by
 * `instanceof` against this module's own type and the intrinsic Error — a
 * boolean question, so nothing the cause holds can answer it with text.
 *
 * The check is wrapped because `instanceof` is not a safe read: a Proxy's
 * getPrototypeOf trap runs during the prototype walk and can throw whatever the
 * caller wrote. A throw there is answered by the unknown code, in this file.
 */
const CAUSE_KINDS = Object.freeze({
  none: "none",
  seamStore: "a-seam-store-that-was-unreachable",
  error: "an-error",
  other: "not-an-error",
  unknown: "undetermined",
});

function causeKind(cause) {
  try {
    if (cause === undefined || cause === null) return CAUSE_KINDS.none;
    if (cause instanceof SeamStoreUnreachableType) return CAUSE_KINDS.seamStore;
    if (cause instanceof Error) return CAUSE_KINDS.error;
    return CAUSE_KINDS.other;
  } catch {
    return CAUSE_KINDS.unknown;
  }
}

/**
 * Every own property is installed as a NON-WRITABLE, NON-CONFIGURABLE DATA
 * property, and the finished error is frozen. A plain assignment would run a
 * setter if one were ever planted on the prototype chain, and a configurable
 * property can be redefined as an accessor afterwards; neither is available
 * here. `enumerable` says only whether the field belongs in a serialization —
 * the three facts do, `name`/`message`/`stack` keep Error's convention.
 */
function own(target, key, value, enumerable) {
  Object.defineProperty(target, key, { value, writable: false, enumerable, configurable: false });
}

// AMENDMENT 2'S CLOSED SHAPE COMES FROM ./closed-callable.js (amendment 9, fifth
// correction round, 2026-09-14). This file used to define its own copy, on the
// argument that a self-contained module is worth a duplicated primitive. The
// review measured that argument against the copies and it failed: the local
// copies had already DIVERGED from the shared one — they never froze the
// callable, which is clause (c), the clause the first shape enumeration added
// after finding it missing — so the file whose whole job is to close a probe was
// running the unhardened version of the shape. A security primitive that exists
// five times is hardened in one of five places. There is one definition now, and
// the enumeration control walks every export against it.

class SeamStoreUnreachableType extends Error {
  constructor(storeRef, because, cause) {
    // SUBCLASSING IS REFUSED BY REACH, NOT BY A RUNTIME CHECK, and the fifth
    // review round is what retired the check. A `new.target` guard here could
    // only ever fire for a caller HOLDING THIS CLASS, and no caller holds it:
    // the class is module-private, nothing exports it, `prototype.constructor`
    // is the factory below and not the class, and an instance therefore reaches
    // the factory too. What a consumer has is an arrow function, which has no
    // [[Construct]] and no `prototype` for a foreign new.target to be read
    // against. A clause no input can reach is a clause no mutation can kill, and
    // this file does not ship one in place of the reach that actually closes it.
    const store = REGISTERED_STORE_TOKENS.includes(storeRef) ? storeRef : STORE_TOKENS.unregistered;
    const reason = REGISTERED_REASONS.includes(because) ? because : UNREACHABLE_REASONS.notRegistered;
    const message = `${store}: ${reason}`;
    super(message);
    own(this, "name", "SeamStoreUnreachable", false);
    own(this, "message", message, false);
    // THE STACK IS A FIXED STRING, and that is deliberate rather than lazy. A
    // real stack is a list of file paths and function names from the CALLER'S
    // frames — text this file did not write, which is exactly what may not
    // travel on this surface. Where the store was and why is already here.
    own(this, "stack", `SeamStoreUnreachable: ${message}`, false);
    own(this, "store_ref", store, true);
    own(this, "because", reason, true);
    own(this, "cause_kind", causeKind(cause), true);
    Object.freeze(this);
  }
}

/**
 * THE PUBLIC SURFACE OF THIS TYPE, AND IT IS NOT THE TYPE.
 *
 * The fifth review round found the last two routes, and the orchestrator's
 * amendment of 2026-09-12 rules the shape that closes them for good: the class
 * above stays MODULE-PRIVATE and nothing exports it, so there is no binding to
 * call, no binding to subclass, and no `newTarget.prototype` for the engine to
 * read on the way in. What leaves this file is an ARROW FUNCTION that builds one
 * internally.
 *
 * An arrow has no [[Construct]] and no `prototype` property at all, so
 * `new seamStoreUnreachable(...)` and `Reflect.construct(seamStoreUnreachable,
 * args, somebodyElse)` are refused by the ENGINE before a line of this file
 * runs — and the amendment puts that refusal out of scope precisely because no
 * module code is reached: nothing here can have read the caller's object, and
 * nothing here wrote the sentence that comes back.
 *
 * THE PROXY THAT USED TO BE HERE IS DELETED, not tightened. A proxy forwards
 * `get`, so `SeamStoreUnreachable.prototype.constructor` handed the raw class
 * straight back out of the exported binding, and the raw class constructed with
 * a foreign `new.target` read THAT target's own `prototype` — a caller object,
 * read on the way to building a caller error. There is no wrapper left to
 * forward anything.
 */
export const seamStoreUnreachable = closedCallable(
  (storeRef, because, cause) => new SeamStoreUnreachableType(storeRef, because, cause));

/**
 * `instanceof` IS NOT THE QUESTION A CONSUMER SHOULD ASK OF THIS TYPE, so this
 * is the question it asks instead, and the reason is the third finding of the
 * fifth round: the intrinsic `instanceof` runs OrdinaryHasInstance, which WALKS
 * THE LEFT OPERAND'S PROTOTYPE CHAIN — and a Proxy whose getPrototypeOf trap
 * throws carried the caller's own text out of an exported callable, a bare
 * `"allow"` in the probe that found it. Every exported callable of this module
 * therefore answers `instanceof` with a flat false, without looking at the
 * operand at all (see `closedCallable`), and the honest question is this
 * predicate, which is guarded and answers rather than throwing.
 *
 * AND ITS WALK IS BOUNDED, which the intrinsic one is not. A Proxy over an
 * extensible target may answer its own getPrototypeOf trap with ITSELF, and an
 * unbounded walk over that never returns — a hang is a refusal the caller chose,
 * and this file does not hand one out. A chain longer than this is not a chain
 * that reaches this type.
 */
const PROTOTYPE_WALK_LIMIT = 100;

function seamStoreInstance(value) {
  try {
    if (value === null || (typeof value !== "object" && typeof value !== "function")) return false;
    let walked = Reflect.getPrototypeOf(value);
    for (let step = 0; step < PROTOTYPE_WALK_LIMIT; step += 1) {
      if (walked === null || walked === undefined) return false;
      if (walked === SeamStoreUnreachableType.prototype) return true;
      walked = Reflect.getPrototypeOf(walked);
    }
    return false;
  } catch {
    return false;
  }
}

export const isSeamStoreUnreachable = closedCallable(value => seamStoreInstance(value));

/**
 * AND AN INSTANCE IS NOT A ROUTE BACK TO THE CLASS EITHER. Every class installs
 * its own unwrapped self on `prototype.constructor`, so `error.constructor` WAS
 * the class — callable, constructable, and reachable from any refusal this file
 * ever returned. It is redefined as the factory, non-writable and
 * non-configurable, and the prototype is frozen afterwards so it cannot be
 * redefined back. The private `Symbol.hasInstance` beside it keeps this module's
 * OWN `instanceof` uses — `causeKind`, `isOwnRefusal` — from walking a hostile
 * operand's chain with the intrinsic.
 */
Object.defineProperty(SeamStoreUnreachableType.prototype, "constructor", {
  value: seamStoreUnreachable, writable: false, enumerable: false, configurable: false,
});
Object.defineProperty(SeamStoreUnreachableType, Symbol.hasInstance, {
  value: seamStoreInstance, writable: false, enumerable: false, configurable: false,
});
Object.freeze(SeamStoreUnreachableType.prototype);


/**
 * THE ONE GUARDED BOUNDARY, applied to every export of this module in one place
 * at the bottom of the file. Invariant (2) of the header lives here.
 *
 * `await call(query)` is inside the try, so a rejected promise is caught by the
 * same clause as a synchronous throw. What is re-thrown is this module's own
 * refusal unless the thrown value ALREADY is one — in which case the specific
 * registered reason it carries is worth more to a reader than a generic one,
 * and it is already a conforming value, so it passes through unchanged.
 *
 * `instanceof` is not a safe read on a value a caller may have shaped, so the
 * test is wrapped: a Proxy whose getPrototypeOf trap throws answers "no", and
 * the generic refusal is what leaves.
 */
function isOwnRefusal(value) {
  try {
    return value instanceof SeamStoreUnreachableType;
  } catch {
    return false;
  }
}

/**
 * AND THE BOUNDARY COVERS `new`, NOT ONLY THE CALL — by there being nothing to
 * construct. The fourth review round found that an async function has no
 * [[Construct]] at all, so `Reflect.construct` on one is refused by the ENGINE
 * before the function starts; the third correction answered that with a Proxy
 * whose construct trap threw this module's own refusal, and the fifth round
 * found what the proxy still forwarded: `get`, and with it the raw target under
 * `prototype.constructor`.
 *
 * So the wrapper is gone and the exported value is an ARROW FUNCTION. An arrow
 * is not a constructor and has no `prototype` of its own, so there is no target
 * to reach and no `newTarget.prototype` read on the way in — construction is
 * refused by the engine, in the caller's own frame, with nothing of this
 * module's in it and nothing of this module run. That is the shape amendment 2
 * of 2026-09-12 rules for every exported callable here, and it is out of scope
 * as a finding for exactly the reason it is safe: no module code executes.
 *
 * The arrow returns the async work as a promise, so every caller sees what it
 * saw before, and the throwing door is unchanged.
 */
function guarded(storeRef, call) {
  const guardedStoreCall = query => (async () => {
    try {
      return await call(query);
    } catch (thrown) {
      if (isOwnRefusal(thrown)) throw thrown;
      throw new SeamStoreUnreachableType(storeRef, UNREACHABLE_REASONS.callDidNotFinish);
    }
  })();
  return closedCallable(guardedStoreCall);
}

/**
 * One addressed field out of a query, or a refusal with a REGISTERED reason.
 *
 * The three fetchers below are exported, so they can be called by anything with
 * anything — including nothing. Two ways a caller's object writes its own text
 * into an error are closed here:
 *
 *   * DESTRUCTURING IN THE SIGNATURE answered a missing argument with the
 *     engine's own TypeError, whose text names the parameter.
 *   * A REVOKED PROXY, a throwing getter or a throwing trap answers a plain
 *     property read with the ENGINE's message or the CALLER's. Node's own text
 *     for a revoked proxy carries a privileged substring, which is how this was
 *     found, and a throwing getter would carry whatever the caller wrote.
 */
function cell(holder, key) {
  try {
    return holder === null || holder === undefined ? undefined : Reflect.get(Object(holder), key);
  } catch {
    return undefined;
  }
}

function addressed(storeRef, query, key) {
  const value = cell(query, key);
  if (typeof value !== "string" || value.length === 0)
    throw new SeamStoreUnreachableType(storeRef, UNREACHABLE_REASONS.queryNotAddressed);
  return value;
}

/**
 * An addressed value that also has to be the SHAPE this file will serve, refused
 * with its own registered reason when it is not.
 *
 * THIS IS NOT THE READER'S CHECK REPEATED FOR TIDINESS. The reader validates
 * `headSha` against the same pattern, and the reader is not the only caller:
 * this module is exported, so the fetcher can be called by anything with
 * anything. The fourth review round supplied
 * `../../../foreign-owner/foreign-repo/commits/<40 hex>` — a nonempty string,
 * which is all `addressed` ever asked — and the path it was interpolated into
 * NORMALIZED to a foreign repository's check runs. The repository binding above
 * was bypassed without ever naming a repository. A value that goes into a URL
 * PATH is validated by the file that builds the URL.
 */
function addressedShape(storeRef, query, key, pattern, because) {
  const value = addressed(storeRef, query, key);
  if (!pattern.test(value)) throw new SeamStoreUnreachableType(storeRef, because);
  return value;
}

function configured(name, storeRef, because) {
  const env = globalThis.process?.env;
  const value = env && typeof env[name] === "string" ? env[name].trim() : "";
  if (!value) throw new SeamStoreUnreachableType(storeRef, because);
  return value;
}

// ---------------------------------------------------------------------------
// THE THREE WAYS A VALUE IS ALLOWED TO LEAVE THIS FILE. Header invariant (1).
// ---------------------------------------------------------------------------

/** A hash the record layer writes, in the one shape it writes them. */
const OUTCOME_HASH = /^sha256:[0-9a-f]{64}$/;
/** A commit sha, in the one shape GitHub writes them. */
const HEAD_SHA = /^[0-9a-f]{40}$/;
/** An actor slug, in the one shape this system registers them. */
const ACTOR_SLUG = /^[a-z0-9][a-z0-9-]{0,63}$/;
/** A correlation id, in the one shape the ops recorder writes them. */
const CORRELATION_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** A value that matched a total pattern owned here, or nothing. */
function matchedText(value, pattern) {
  return typeof value === "string" && pattern.test(value) ? value : null;
}

/**
 * Store text, reduced to a value only equality can be asked of. Two digests are
 * equal exactly when the two strings were, which is everything the derivations
 * ask of a service key, a run key, an evidence ref or a status word — and it
 * carries no byte the store chose. An empty or non-string cell is absence, and
 * absence stays distinguishable from any value.
 */
function opaque(value) {
  return typeof value === "string" && value.length > 0 ? digest(value) : null;
}

/** An instant, re-serialized by this file, or nothing. */
function instantText(value) {
  try {
    const parsed = value instanceof Date ? value.getTime()
      : typeof value === "string" ? Date.parse(value) : Number.NaN;
    return Number.isFinite(parsed) ? new Date(parsed).toISOString() : null;
  } catch {
    return null;
  }
}

/**
 * Several statements, ONE read-only transaction AT ONE SNAPSHOT, one pool,
 * closed before returning. One transaction because the clauses compare facts to
 * each other: a receipt read at one instant and a card read at another could
 * disagree, and a reader that joined two instants would be reporting a state
 * that never existed.
 *
 * AND A TRANSACTION IS NOT A SNAPSHOT, which is the fourth review round's fourth
 * finding. `begin read only` keeps PostgreSQL's DEFAULT isolation, READ
 * COMMITTED, and read committed takes a NEW snapshot for every statement — so
 * an acceptance committed by another session between card 11's first statement
 * and its second was visible to the second and not the first, and the join of
 * the two described a state the database never held. Read-only forbids this
 * transaction from writing; it says nothing about what it sees.
 *
 * REPEATABLE READ is the weakest level that fixes one snapshot for the whole
 * transaction — taken at the first statement and held to the commit — which is
 * exactly the property the derivations assume. SERIALIZABLE would do as well and
 * costs more: with no writes here there is no serialization anomaly to prevent,
 * so nothing is bought by the stronger level. A read-only repeatable-read
 * transaction cannot raise a serialization failure either, so there is no retry
 * path to write and none is pretended.
 */
async function readOnlyStatements(storeRef, statements) {
  const connectionString = configured("DATABASE_URL_READER", storeRef,
    UNREACHABLE_REASONS.targetNotConfigured);
  let pg;
  try {
    ({ default: pg } = await import("pg"));
  } catch (cause) {
    throw new SeamStoreUnreachableType(storeRef, UNREACHABLE_REASONS.clientNotAvailable, cause);
  }
  const pool = new pg.Pool({ connectionString, max: 1, statement_timeout: 15000 });
  try {
    const client = await pool.connect();
    try {
      await client.query("begin isolation level repeatable read, read only");
      const results = [];
      for (const { text, params } of statements) results.push((await client.query(text, params)).rows);
      await client.query("commit");
      return results;
    } finally {
      client.release();
    }
  } catch (cause) {
    if (isOwnRefusal(cause)) throw cause;
    throw new SeamStoreUnreachableType(storeRef, UNREACHABLE_REASONS.queryDidNotFinish, cause);
  } finally {
    await pool.end().catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// Card 11's store — the record layer's own outcome feedback.
// ---------------------------------------------------------------------------

/**
 * The two statuses a predecessor row can carry, and BOTH ARE CONSTANTS OUT OF
 * THIS FILE rather than the status column's text. Which one a row gets is
 * decided by WHICH QUERY FOUND IT — the acceptance-receipt table holds accepted
 * rows and the pending view holds unsigned ones — not by a word the database
 * chose. That is a fact about the query, not a judgment about the row.
 */
const OUTCOME_STATUS = Object.freeze({
  accepted: "accepted",
  pending: "pending_human_acceptance",
});

/**
 * A Work Request's outcome feedback, through the three doors the record layer
 * already opens — and NOT through ops.sourced_work_request_outcome_feedback,
 * which is granted to nobody.
 *
 * THAT IS A BOUNDARY, NOT A MISSING GRANT. The proposal table is reached only by
 * security-definer functions; a handler that selected from it directly would
 * fail with sqlstate 42501, and tools/test-handler-reads-are-granted.py refuses
 * the attempt at push time. It refused this reader's first draft, which is how
 * the boundary was found. The fix is to read what the record layer exposes, not
 * to widen a role.
 *
 *   ops.sourced_work_request_outcome_feedback_acceptance_receipt — THE
 *     AUTHORITY. One row per acceptance, carrying the hash a human signed. An
 *     outcome is accepted exactly when this table holds a row for it.
 *   ops.work_request_card — the readable detail: feedback_ref, the proposal's
 *     own hash and the outcome it concluded, for the latest accepted feedback
 *     and up to twenty of its history.
 *   ops.pending_sourced_work_request_outcome_feedback — the still-unsigned
 *     proposal, so "proposed and never accepted" stays distinguishable from
 *     "nothing was ever proposed". Without it both would look like absence, and
 *     those are different facts about a predecessor.
 *
 * `accepted_feedback_hash` IS SET ONLY FROM THE RECEIPT. The card's own
 * `feedback_hash` is the proposal's, and it is carried separately under that
 * name. They are equal in production, which is exactly why they must not be the
 * same field here: a reader that matched on the proposal's hash would look
 * correct for as long as nothing ever went wrong.
 *
 * AND A RECEIPT WITHOUT ITS CARD DETAIL IS A MISSING ROW, NOT AN ACCEPTED ONE.
 * An earlier draft synthesized the absent detail as nulls, so a receipt the card
 * did not carry came back looking accepted with a null outcome and could still be
 * admitted. It is now counted by `detail_row_count` and the derivation refuses
 * unless that count is one. Nothing is invented to fill a row the store did not
 * have.
 *
 * IT IS A COUNT AND NOT A BOOLEAN, and that is the second review round's finding
 * rather than a style choice: the field shipped as `detail_present: entry !==
 * undefined`, so an exported function's successful path returned a bare `true` —
 * the exact shape the standing rule closes over, under a key that carries a
 * privileged word besides.
 */
async function predecessorOutcomeRows(query) {
  const storeRef = STORE_TOKENS.predecessorOutcome;
  const workRequestRef = addressed(storeRef, query, "workRequestRef");
  const [receipts, cards, pending] = await readOnlyStatements(storeRef, [
    { text: `select r.feedback_hash as accepted_feedback_hash
               from ops.sourced_work_request_outcome_feedback_acceptance_receipt r
               join ops.work_request w on w.id = r.work_request_id
              where w.ref = $1
              order by r.accepted_at desc, r.id desc`, params: [workRequestRef] },
    { text: `select outcome_feedback, outcome_feedback_history
               from ops.work_request_card($1::text, $2::text)`,
      params: [workRequestRef, ORGANIZATION_TENANT_ID] },
    { text: `select feedback_hash
               from ops.pending_sourced_work_request_outcome_feedback($1::text, $2::text)`,
      params: [workRequestRef, ORGANIZATION_TENANT_ID] },
  ]);

  const card = cell(cards, 0);
  const history = cell(card, "outcome_feedback_history");
  const detail = new Map();
  for (const entry of [cell(card, "outcome_feedback"), ...(Array.isArray(history) ? history : [])]) {
    const hash = matchedText(cell(entry, "feedback_hash"), OUTCOME_HASH);
    if (hash !== null) detail.set(hash, hash);
  }

  // NOTHING FREE-FORM TRAVELS. A row carries a status this file wrote, the two
  // hashes that are compared — each of which had to match this file's own
  // pattern to survive — and a count of the card rows found for the receipt.
  // The feedback ref, the stored outcome and the acceptance timestamp are not
  // even selected: no store text reaches an answer, so no store text can carry a
  // word into one.
  const rows = (Array.isArray(receipts) ? receipts : []).map(receipt => {
    const acceptedHash = matchedText(cell(receipt, "accepted_feedback_hash"), OUTCOME_HASH);
    const matchingDetail = acceptedHash === null ? undefined : detail.get(acceptedHash);
    return {
      status: OUTCOME_STATUS.accepted,
      detail_row_count: matchingDetail === undefined ? 0 : 1,
      accepted_feedback_hash: acceptedHash,
      feedback_hash: matchingDetail ?? null,
    };
  });
  for (const proposal of Array.isArray(pending) ? pending : [])
    rows.push({
      status: OUTCOME_STATUS.pending,
      detail_row_count: 1,
      accepted_feedback_hash: null,
      feedback_hash: matchedText(cell(proposal, "feedback_hash"), OUTCOME_HASH),
    });
  return { store_ref: storeRef, rows };
}

// ---------------------------------------------------------------------------
// Card 12's store — the Control Plane ledger and bin/run-scheduled.sh's runs.
// ---------------------------------------------------------------------------

/**
 * The service row for a scheduled canary and every run row the scheduler wrote
 * for it, newest observation first.
 *
 * TIMESTAMPS ARE THE WHOLE POINT of this query, so the three the clauses need
 * are selected and none is computed here: `started_at` is dispatch, `ended_at`
 * and `observed_at` are readback. Ordering by `observed_at desc` is a
 * presentation choice; the reader compares instants and never trusts position.
 *
 * THE IDENTIFIERS COME BACK AS DIGESTS. A service key, a run key, an evidence
 * ref, a source kind and a source ref are all free-form ledger text,
 * and the derivation asks nothing of them but equality — is this observation of
 * the same run as that dispatch, was this row written by the wrapper. Digesting
 * answers exactly those questions and answers nothing else, so a service someone
 * names `release-canary` cannot put a privileged word into an answer. The two
 * receipt fields are digested for the same reason and answer the same kind of
 * question: `receipt_run_key_digest` is what the receipt says its run is,
 * `run_key_receipt_digest` is what this row's own run key hashes to, and
 * `receipt_minted_at` is the instant the wrapper stamped after its child exited.
 */
/**
 * THE ONLY RECEIPT SHAPE THIS STORE WILL PARSE, and it is bin/run-scheduled.sh's
 * own mint:
 *
 *     carr-run-receipt:v1:<YYYYMMDDTHHMMSS.mmmZ>:<16 hex>:<32 hex run-key hash>
 *
 * Nothing else in ops.run.evidence_ref parses, and a ref that does not parse
 * reaches the derivation as an absence rather than as text. That is the point:
 * evidence_ref is free-form ledger text that ANY writer can put a value into,
 * and before this shape existed the only question asked of it was "not null" —
 * which a hand-written row, a stale file or a fabricated string answered as
 * readily as a real dispatch.
 */
const SCHEDULED_RECEIPT =
  /^carr-run-receipt:v1:(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})\.(\d{3})Z:[0-9a-f]{16}:([0-9a-f]{32})$/;

/** The two facts a parsed receipt carries, or two absences. */
function receiptFields(value) {
  const parsed = typeof value === "string" ? SCHEDULED_RECEIPT.exec(value) : null;
  if (parsed === null) return { runKeyHash: null, mintedAt: null };
  const [, year, month, day, hour, minute, second, millis, runKeyHash] = parsed;
  return {
    runKeyHash,
    mintedAt: instantText(`${year}-${month}-${day}T${hour}:${minute}:${second}.${millis}Z`),
  };
}

/**
 * The same truncated digest of a run key that a minted receipt carries, computed
 * HERE from the row's own run_key. The derivation's question — is this receipt
 * this run's receipt — is then an equality between two values this file
 * produced, and a receipt minted for some other job answers it with a mismatch
 * instead of with a shrug.
 */
const RECEIPT_HASH_PREFIX = "sha256:".length;
function runKeyReceiptHash(value) {
  return typeof value === "string" && value.length > 0
    ? digest(value).slice(RECEIPT_HASH_PREFIX, RECEIPT_HASH_PREFIX + 32)
    : null;
}

/**
 * THE LATEST SCHEDULER-MINTED RUN FOR A SERVICE, as a SQL address rather than a
 * value a caller supplies (2026-09-12, PR 1013 correction round).
 *
 * An absent `canaryRunKey` no longer refuses: it means "the run this service's
 * scheduler wrapper most recently minted a receipt for", which the ledger can
 * answer on its own and no human has to look up and paste. The predicate is the
 * receipt shape SCHEDULED_RECEIPT parses plus the wrapper's own source kind, so
 * an operator row, a collector row or a pre-receipt row is not a candidate; the
 * run key selected is still never returned as text, only as a digest, and the
 * derivation over the rows is byte-for-byte the one it always was.
 */
const LATEST_MINTED_RUN_KEY = `
      and r.run_key = (
        select r2.run_key from ops.run r2
         where r2.service_id = s.id
           and r2.source_kind = 'wrapper'
           and r2.evidence_ref like 'carr-run-receipt:v1:%'
         order by r2.started_at desc nulls last, r2.run_key desc
         limit 1)`;

async function schedulerLedgerRows(query) {
  const storeRef = STORE_TOKENS.schedulerLedger;
  const serviceKey = addressed(storeRef, query, "serviceKey");
  // OPTIONAL, and the only optional address in this file. Absent is a REQUEST
  // for the derivation above, not a malformed query; a present-but-unusable
  // value is still refused by `addressed`.
  const named = cell(query, "canaryRunKey") !== undefined;
  const canaryRunKey = named ? addressed(storeRef, query, "canaryRunKey") : null;
  const [raw] = await readOnlyStatements(storeRef, [{ text: `
    select s.key            as service_key,
           r.run_key,
           r.started_at,
           r.ended_at,
           r.observed_at,
           r.evidence_ref,
           r.source_kind,
           r.source_ref
      from ops.service s
      left join ops.run r on r.service_id = s.id
       and ${named ? "r.run_key = $2" : "$2::text is null" + LATEST_MINTED_RUN_KEY}
     where s.key = $1
     order by r.observed_at desc nulls last`, params: [serviceKey, canaryRunKey] }]);
  const rows = (Array.isArray(raw) ? raw : []).map(row => {
    const receipt = receiptFields(cell(row, "evidence_ref"));
    return {
      service_key_digest: opaque(cell(row, "service_key")),
      run_key_digest: opaque(cell(row, "run_key")),
      evidence_ref_digest: opaque(cell(row, "evidence_ref")),
      source_kind_digest: opaque(cell(row, "source_kind")),
      source_ref_digest: opaque(cell(row, "source_ref")),
      // The receipt's own claim about which run it belongs to, and this file's
      // answer to the same question from the row itself. Digested like every
      // other identifier here, so the derivation can ask equality and nothing
      // else, and no hex the ledger happened to hold reaches an answer.
      receipt_run_key_digest: opaque(receipt.runKeyHash),
      run_key_receipt_digest: opaque(runKeyReceiptHash(cell(row, "run_key"))),
      receipt_minted_at: receipt.mintedAt,
      started_at: instantText(cell(row, "started_at")),
      ended_at: instantText(cell(row, "ended_at")),
      observed_at: instantText(cell(row, "observed_at")),
    };
  });
  return { store_ref: storeRef, rows };
}

// ---------------------------------------------------------------------------
// Card 13's store — hosted CI, through the GitHub checks API.
// ---------------------------------------------------------------------------

/**
 * THE ONE REPOSITORY THIS FILE SERVES, and it is a constant rather than a
 * setting.
 *
 * The earlier draft read `GITHUB_REPOSITORY` and built the URL out of it, so
 * whoever set that variable chose which repository's check runs would be
 * reported under the label `github:checks` — a foreign repository whose checks
 * the caller controls answers just as readily as this one, and Gate Zero cannot
 * tell the difference. Anything that disagrees with this constant is refused
 * with a registered reason, whether it arrived in the environment or in the
 * query: an unset variable is the supported state, a matching one is harmless,
 * and a different one is a misconfiguration loud enough to stop on.
 */
const AUTHORITATIVE_REPOSITORY = "jbookout/carr-system";

/**
 * THE SAME CONSTANT, IN THE FORM A URL PATH TAKES IT: each segment encoded on
 * its own, so the only unencoded `/` in the path is one this file wrote. The
 * constant cannot carry anything that needs escaping today — it is two literal
 * words — and that is exactly why the encoding is applied to the whole path
 * rather than to the sha alone: `${repository}/${headSha}` was a template that
 * TRUSTED its parts, and the next part added to it would have been trusted too.
 */
const AUTHORITATIVE_REPOSITORY_PATH =
  AUTHORITATIVE_REPOSITORY.split("/").map(segment => encodeURIComponent(segment)).join("/");

/** Every place a repository could be named from outside this file. */
const REPOSITORY_FIELDS = Object.freeze(["repository", "repo", "owner", "GITHUB_REPOSITORY"]);

function refuseAForeignRepository(query) {
  const env = globalThis.process?.env;
  const named = REPOSITORY_FIELDS.map(field =>
    field === "GITHUB_REPOSITORY" ? cell(env, field) : cell(query, field));
  for (const candidate of named) {
    if (typeof candidate !== "string") continue;
    const trimmed = candidate.trim();
    if (trimmed.length > 0 && trimmed !== AUTHORITATIVE_REPOSITORY)
      throw new SeamStoreUnreachableType(STORE_TOKENS.checkConclusion,
        UNREACHABLE_REASONS.foreignRepository);
  }
}

/**
 * Every check run GitHub holds for one commit under one check name.
 *
 * The head sha and the check name ADDRESS the query and are validated by the
 * reader before they reach here. The repository is NOT addressable at all.
 *
 * WHAT COMES BACK IS FOUR FIELDS AND NOT SEVEN. The check run's `name` and
 * `html_url` are wire text no derivation reads, so they are dropped rather than
 * carried; `status` and `conclusion` are wire text two derivations compare, so
 * they are digested; `completed_at` is re-serialized by this file under the same
 * name the ledger store uses for the same instant. A conclusion word GitHub has
 * not documented therefore cannot reach a consumer even as a substring — the
 * reader recognizes the digest of each documented word and reports its OWN copy
 * of that word, or reports the conclusion as unrecognized.
 */
async function checkConclusionRows(query) {
  const storeRef = STORE_TOKENS.checkConclusion;
  // VALIDATED BEFORE ANYTHING IS BUILT OUT OF IT, and refused with its own
  // registered reason rather than with the generic not-addressed one, so a
  // reader can tell a query that named no commit from one that named something
  // that is not a commit id at all.
  const headSha = addressedShape(storeRef, query, "headSha", HEAD_SHA,
    UNREACHABLE_REASONS.shaNotAddressed);
  const checkName = addressed(storeRef, query, "checkName");
  refuseAForeignRepository(query);
  const token = configured("GITHUB_TOKEN", storeRef, UNREACHABLE_REASONS.credentialsNotConfigured);
  // EVERY SEGMENT ENCODED, INCLUDING THE ONES THAT CANNOT NEED IT. `headSha` has
  // already matched forty hex characters, and the repository is this file's own
  // constant — so both encodings are no-ops today, and they are what keeps the
  // path's shape a property of this line rather than of a validation somewhere
  // above it.
  const url = `https://api.github.com/repos/${AUTHORITATIVE_REPOSITORY_PATH}`
    + `/commits/${encodeURIComponent(headSha)}/check-runs`
    + `?check_name=${encodeURIComponent(checkName)}&per_page=100`;
  let response;
  try {
    response = await fetch(url, {
      headers: {
        accept: "application/vnd.github+json",
        authorization: `Bearer ${token}`,
        "user-agent": "carr-gate-zero-seam-reader",
        "x-github-api-version": "2022-11-28",
      },
    });
  } catch (cause) {
    throw new SeamStoreUnreachableType(storeRef, UNREACHABLE_REASONS.sourceNotReachable, cause);
  }
  if (cell(response, "ok") !== true)
    throw new SeamStoreUnreachableType(storeRef, UNREACHABLE_REASONS.sourceRefused);
  let body;
  try {
    body = await response.json();
  } catch (cause) {
    throw new SeamStoreUnreachableType(storeRef, UNREACHABLE_REASONS.answerDidNotParse, cause);
  }
  const runs = cell(body, "check_runs");
  return {
    store_ref: storeRef,
    rows: (Array.isArray(runs) ? runs : []).map(run => ({
      head_sha: matchedText(cell(run, "head_sha"), HEAD_SHA),
      status_digest: opaque(cell(run, "status")),
      conclusion_digest: opaque(cell(run, "conclusion")),
      ended_at: instantText(cell(run, "completed_at")),
    })),
  };
}

// ---------------------------------------------------------------------------
// The candidate-build record store — `ops.release`, the row the deploy wrapper
// filed for the build this Worker is running (standing-rule amendment 9,
// 2026-09-14).
//
// THE TOKEN IS SPELLED `control-plane:ops.candidate-build-record` RATHER THAN
// NAMING THE TABLE, and that is deliberate rather than vague. Store refs travel:
// they come back on every answer and into the receipt's own reasoning, and the
// v5 privileged-word sweep closes over its union AS SUBSTRINGS — `release`
// among them. A token carrying that word would put a privileged outcome word
// into every answer this store touches, for no reason but naming a table in a
// place the table's name is not the question. It is the same reason the
// producer's absence fields are `head_revision_object` rather than the obvious
// spelling. The table is `ops.release`, state `candidate`, written by
// `tools/ops-record.py release candidate`; the query below is the exact address.
// ---------------------------------------------------------------------------

/**
 * THE CANDIDATE-BUILD ROWS FOR ONE REVISION: who made the candidate, and the
 * correlation the recorder stamped on the row that says so.
 *
 * WHY THIS STORE EXISTS. The receipt's subject maker is the person who built
 * what is being judged. Three shapes of that have now been tried and two were
 * wrong: a static seat declaration (the first round — a constant, authenticating
 * nobody), the committer line of HEAD's commit object (the third — a git
 * attribution, which authenticates nobody either, and which the deployed Worker
 * cannot read at all). Amendment 9 names the third: the RELEASE-CANDIDATE RECORD
 * `tools/ops-record.py release candidate` filed for that exact sha, which is an
 * authenticated authority write into the Control Plane. This reads it back.
 *
 * TWO COLUMNS LEAVE THIS FILE AS TEXT, WHICH IS TWO MORE THAN ANY OTHER READER
 * HERE GETS, and both are total patterns owned by this module. `maker_actor` is
 * matched against the actor-slug shape and `correlation_id` against the uuid the
 * recorder writes; anything else is absence. The consumer then has to find that
 * slug in its OWN registry of partners before it can name one, so the widest
 * value this path can carry into a receipt is a registered partner's slug. A
 * digest would not do here — a receipt has to NAME its subject maker, and a hash
 * names nobody.
 *
 * EVERYTHING ELSE IS DIGESTED or reduced, exactly as the other stores do it: the
 * state and environment words come back as digests, because the derivation asks
 * equality of them and nothing else.
 *
 * TWO PREDICATES DECIDE WHETHER A ROW IS THE AUTHENTICATED RECORD AT ALL, and
 * they are in the WHERE clause rather than in the consumer, because a row this
 * reader cannot vouch for is not a row it should hand anybody.
 *
 *   `maker_authority_verified` — THE ONE PREDICATE NO ROLE CAN WRITE, added by
 *   migration 0504 after the seventh review round found the other two forgeable.
 *   It is a STORED GENERATED column over `maker_session_user`, which a BEFORE
 *   trigger writes from `session_user` on every insert and which no update may
 *   move. PostgreSQL refuses any statement that so much as NAMES a generated
 *   column, so this is not a guarded field but an unwritable one: it is true
 *   exactly when the login role that filed the row is carr_authority_joe or
 *   carr_authority_dell. The two text columns below are what the row SAYS; this
 *   column is why a reader may believe it.
 *
 *   `source_kind = 'wrapper'` — the one writer of a release candidate,
 *   tools/ops-record.py, writes that literal and nothing else ever does.
 *
 *   `maker_verification_ref = 'ops.authority-principal:' || maker_actor` — the
 *   MARKER OF THE DERIVATION. Neither column is reachable from a caller flag:
 *   `release candidate` refuses --maker and --maker-verification, and 0504's
 *   trigger writes both halves itself from the recorded login for an authority
 *   session and REFUSES the marker (42501) to every session that is not one. So a
 *   row satisfying this pair carries a maker the DATABASE named from a human
 *   authority credential, and a row whose maker somebody typed does not satisfy
 *   it and is not returned.
 *
 * WHAT FILLS THIS READER, AND WHEN IT STARTED TO. Filing a row on a human
 * authority credential needs that credential to hold INSERT on ops.release, and
 * for a long time it did not: migration 0161 built carr_authority as a privilege
 * bundle carrying no business-record table grant, 0273 made the two partner
 * logins members of exactly that bundle, and adding the grant was a new DB
 * mutation capability that SIEP-11 admits only through a SCAC mutation-registry
 * successor. Open loop #594 carried that admission and migration 0503 landed it,
 * with the two column-scoped selects the table's invoker-rights trigger reads;
 * migration 0505 then granted the two reads the FILING PATH needs — ops.service
 * by key, and the ops.release columns its insert returns — and moved
 * tools/ops-record.py's `release candidate` onto the authority connection. From
 * that point the deploy wrapper's row is authority-verified and this store
 * answers from it, with no change to this file.
 *
 * A ROW FILED ON THE LEDGER WRITER IS STILL IGNORED HERE, and that is the whole
 * predicate rather than a leftover: 0504 marks such a row unauthenticated, and
 * an unauthenticated maker is exactly what amendment 9(c) refuses to read.
 *
 * WHAT THIS DOES NOT CLAIM, said plainly: every row written before migration 0504
 * — including the ones an earlier round's recorder derived honestly and then
 * inserted over the generic ledger writer — has no recorded filing login at all,
 * carries `maker_authority_verified` false, and is NOT read here, because at the
 * moment it was written any role with INSERT on the table could have written the
 * same two columns. The predicate is
 * therefore exact about the rows it admits rather than trusting the table's
 * history, and it is state-INDEPENDENT on purpose: the release this
 * producer judges is usually approved or complete by the time it is deployed, so
 * filtering on `state = 'candidate'` would hide the record of every build that
 * actually shipped.
 */
async function candidateBuildRecordRows(query) {
  const storeRef = STORE_TOKENS.candidateBuildRecord;
  const gitSha = addressedShape(storeRef, query, "gitSha", HEAD_SHA,
    UNREACHABLE_REASONS.shaNotAddressed);
  const [raw] = await readOnlyStatements(storeRef, [{ text: `
    select r.git_sha,
           r.state,
           r.environment,
           r.maker_actor,
           r.correlation_id::text as correlation_id,
           r.observed_at
     from ops.release r
     where r.git_sha = $1
       and r.environment = 'production'
       and r.maker_authority_verified
       and r.source_kind = 'wrapper'
       and r.maker_verification_ref = '${MAKER_AUTHORITY_PREFIX}' || r.maker_actor
     order by r.observed_at desc nulls last`, params: [gitSha] }]);
  const answered = Array.isArray(raw) ? raw : [];
  // EXACTLY ONE PRODUCTION ROW, OR NOBODY. Migration 0513's partial unique index
  // is what makes this the normal case rather than a hope while still permitting
  // an authority-filed staging record for the same revision. This is the reader's
  // own half of the same rule: a second Production row for one revision would leave the producer
  // CHOOSING which authenticated maker to name, and a store that hands its
  // consumer an ambiguity has already made that choice for it. So the ambiguity
  // is the refusal, and it says which store it is about.
  if (answered.length > 1)
    throw new SeamStoreUnreachableType(storeRef, UNREACHABLE_REASONS.candidateRecordAmbiguous);
  const rows = answered.map(row => ({
    git_sha_digest: opaque(cell(row, "git_sha")),
    state_digest: opaque(cell(row, "state")),
    environment_digest: opaque(cell(row, "environment")),
    maker_actor: matchedText(cell(row, "maker_actor"), ACTOR_SLUG),
    correlation_id: matchedText(cell(row, "correlation_id"), CORRELATION_UUID),
    observed_at: instantText(cell(row, "observed_at")),
  }));
  return { store_ref: storeRef, rows };
}

// ---------------------------------------------------------------------------
// THE PUBLIC SURFACE. Four fetchers and one error type, and every fetcher passes
// through the same boundary — there is no second copy of it to forget to apply,
// and no export that bypasses it.
// ---------------------------------------------------------------------------

export const fetchPredecessorOutcomeRows =
  guarded(STORE_TOKENS.predecessorOutcome, predecessorOutcomeRows);
export const fetchSchedulerLedgerRows =
  guarded(STORE_TOKENS.schedulerLedger, schedulerLedgerRows);
export const fetchCheckConclusionRows =
  guarded(STORE_TOKENS.checkConclusion, checkConclusionRows);
export const fetchCandidateBuildRecordRows =
  guarded(STORE_TOKENS.candidateBuildRecord, candidateBuildRecordRows);
