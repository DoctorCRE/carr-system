/**
 * One board cell, one intended change, one idempotency key.
 *
 * The record layer's contract is explicit about what a key means: "generate a
 * UUID per intended action; retries reuse the SAME key" (mcp-server/src/tools.js).
 * A second call under the same key returns the STORED answer instead of writing
 * again, and a call whose arguments differ under a key that was already spent is
 * refused as `key_reuse`. Undo already honours that (change-receipts.mjs); the
 * board's cells did not, and minted a fresh key on every attempt.
 *
 * What that cost, exactly: a flag click commits event e1 and the answer is lost
 * on the way back. Nothing on the page moved, so the person clicks again before
 * the feed has delivered e1. The second click computes the same value from the
 * same still-stale base — but under a NEW key it is a new operation, so the
 * server sees an intervening event, records a durable conflict row, and the
 * board opens "Two edits crossed" with both sides attributed to the same person.
 * Under the ORIGINAL key the retry never reaches that handler: it replays.
 *
 * So this module holds, per `deal|field`, the one operation that has not been
 * answered yet, frozen:
 *
 *   - the request is built ONCE — deal, field, value, base_event_id, key — and
 *     every later attempt sends that same object. A base the changes feed has
 *     moved on since does not rewrite it: the request is a statement about what
 *     was on screen when the person acted, and replay depends on it byte for byte.
 *   - while a request is open, a second one for that cell is not sent at all.
 *     That is the double-click guard, and it is a guard against duplicate SENDS,
 *     not a guess about the outcome.
 *   - an unresolved operation is not silently replaced. A different intent for
 *     the same cell is refused with an explanation while the first one is still
 *     unknown, because minting a second key over the top of it is the defect,
 *     not the fix. Retrying the first one — same key — is offered instead.
 *   - only the server's own answer settles anything. There is no timer, no blind
 *     re-send, no optimistic value, and no second copy of the board: the entry is
 *     bookkeeping about one request, exactly as `state.undo` and `state.fieldBase`
 *     already are.
 *   - an accepted answer is reported as SUPERSEDED when this cell's base moved
 *     while the request was out AND the event that moved it is not this
 *     operation's own. A replay is truthful about an older operation, and a
 *     truthful old answer is still an old answer: if something else landed on
 *     that cell since, the caller is told to leave the newer state alone and
 *     re-read rather than paint the request's value over it. The answer names
 *     the event it committed, so this board's own write arriving on the feed is
 *     recognised as itself instead of being reported as a partner's change.
 *
 * What it deliberately does NOT do: infer that a write landed because the feed
 * later showed a matching value. A partner can set the same value; equality is
 * not evidence of authorship. An unresolved operation stays unresolved until the
 * server answers a re-send of it.
 */

/** Codes that mean the server BROKE, not that it declined. */
const SERVER_FAULT_CODES = new Set(['unhandled_verb_failure', 'internal_error']);

/**
 * Statuses that are a DECISION about this request rather than a failure around
 * it. 401 and 403 are taken at the door, before the verb runs: the change was
 * not saved, nothing is pending, and inviting a retry of it would be a lie.
 * Everything else non-2xx is treated as uncertain, because a proxy can produce
 * almost any status and this client cannot tell one from the record layer.
 */
const DEFINITIVE_HTTP = new Set([401, 403]);

// Sentence tails. The caller supplies the subject — "Attention flag on
// Riverbank Dental", or the default "This change" — so one wording serves a
// toast that must name the cell and a dialog that already has.
const OUTCOME_SENTENCE = Object.freeze({
  // An accepted answer that is no longer the newest word on its cell. It is not
  // a failure and it is not a conflict: the operation landed, and something the
  // board has since seen came after it. Saying which value is on screen is the
  // whole content of it.
  superseded: 'was recorded. The board has since seen a newer change to this cell, so it is showing the current value rather than applying this one.',
  // Three different things, said as three different sentences. Nothing came
  // back at all; something came back and it was the server failing; the server
  // decided, at the door, that this session may not make this change.
  // These two name WHERE the control is. A sentence that tells a person to retry
  // something has to point at something they can see: the row's own Retry button
  // is hidden by a filter, a search or a workspace switch, so the one place that
  // is always on screen — the Unconfirmed changes bar — is what they are sent to.
  no_answer: 'could not be confirmed — nothing came back from the server. It may already be saved: send it again from the Unconfirmed changes bar at the top of the page, or open the deal to check, before changing this cell again.',
  server_error: 'could not be confirmed — the server reported an error instead of confirming it. It may already be saved: send it again from the Unconfirmed changes bar at the top of the page, or open the deal to check, before changing this cell again.',
  unauthorized: 'was not saved — this session is not signed in, or is not allowed to change it. Sign in again, re-open the deal, and make the change from what it holds now.',
  key_reuse: 'was already sent under the same safety key for a different request, so the server refused it. Open the deal and check what it holds now.',
  unresolved: 'has an earlier change that was never confirmed. Send that one again from the Unconfirmed changes bar at the top of the page, or open the deal to check, before making a different change here.',
  in_flight: 'is still being sent. Wait for the server to answer before changing it again.',
});

const SLUG = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/;

function humanize(code) {
  if (code === null || code === undefined) return '';
  const value = String(code);
  if (!SLUG.test(value)) return value;
  return value.replace(/_/g, ' ');
}

/** Stable text for any value a cell can hold, so equality survives a re-render. */
function stableText(value) {
  if (value === null || value === undefined) return 'null';
  if (typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableText).join(',')}]`;
  const keys = Object.keys(value).sort();
  return `{${keys.map((key) => `${JSON.stringify(key)}:${stableText(value[key])}`).join(',')}}`;
}

/**
 * Is this the SAME intended change? Cells hold booleans, strings, nulls and the
 * operating-state object, and a re-render hands back a structurally equal object
 * rather than the identical one, so identity would call every retry a new intent.
 */
export function sameFieldValue(a, b) {
  return stableText(a) === stableText(b);
}

/** The one cell an operation belongs to. app.js uses this for `state.fieldBase` too. */
export function cellKey(deal, field) {
  return `${deal}|${field}`;
}

/** Per-cell write bookkeeping. Plain data so it stays comparable. */
export function createFieldWriteState() {
  return {};
}

function freezeRequest(request) {
  if (request.value && typeof request.value === 'object') Object.freeze(request.value);
  return Object.freeze(request);
}

/**
 * Claim the single in-flight write for one cell, or take back the request that
 * was never answered.
 *
 * Three answers:
 *   started — send `request`. It is either brand new or, for a retry of an
 *             unresolved operation, the ORIGINAL frozen one: same key, same
 *             base, same value, whatever the feed has done since.
 *   in_flight — a request for this cell is open. Nothing is sent.
 *   blocked — an earlier operation on this cell is unresolved and this is a
 *             DIFFERENT intent. Nothing is sent; `pending` names what has to be
 *             reconciled first.
 */
export function beginFieldWrite(state, { deal, field, value, base = null, newKey }) {
  const writes = state || {};
  const cell = cellKey(deal, field);
  const current = writes[cell] || null;
  if (current && current.status === 'pending') {
    return { state: writes, started: false, status: 'in_flight', reason: 'in_flight', request: null, pending: current };
  }
  if (current && !sameFieldValue(current.request.value, value)) {
    return { state: writes, started: false, status: 'blocked', reason: 'unresolved', request: null, pending: current };
  }
  // The retained request is reused WHOLE. Rebuilding it from today's base would
  // change the operation manifest the server hashed, which is precisely how a
  // retry stops being a retry.
  const request = current ? current.request : freezeRequest({
    deal, field, value, base_event_id: base ?? null, idempotency_key: newKey(),
  });
  const entry = {
    cell, status: 'pending', request, attempts: (current?.attempts || 0) + 1,
    reason: null, code: null, hint: null, message: null,
  };
  return {
    state: { ...writes, [cell]: entry },
    started: true, status: 'sending', reason: null, request,
    pending: null, retry: Boolean(current),
  };
}

/**
 * Record what the server actually said. An unknown answer KEEPS the entry — that
 * is the whole point of it — and every settled answer, including a conflict and
 * including a refusal, drops it so the next intent starts clean.
 */
export function settleFieldWrite(state, cell, outcome) {
  const writes = state || {};
  const current = writes[cell];
  if (!current) return writes;
  if (outcome?.status === 'unknown') {
    return { ...writes, [cell]: {
      ...current,
      status: 'unknown',
      reason: outcome.reason || null,
      code: outcome.code || null,
      hint: outcome.hint || null,
      message: fieldWriteMessage(outcome),
    } };
  }
  const next = { ...writes };
  delete next[cell];
  return next;
}

/**
 * Classify one patch-deal-field answer. Five outcomes, and the differences
 * between them are the whole reason this exists:
 *
 *   ok       — the server said so. A replayed answer is the recorded result of
 *              the SAME operation, so it counts exactly as much as the first one.
 *              An accepted answer carries the committed event's own identity
 *              (`event_id`, `event_recorded_at`) when the record layer names it,
 *              which is what lets a caller advance that cell's base.
 *   conflict — a real partner conflict: an event this request did not see landed
 *              on this cell first. Settled and server-authoritative; the board
 *              shows it as it always has.
 *   refused  — a DECISION about this request: an invalid base, a missing parking
 *              reason, a key already spent on different arguments, or a 401/403
 *              taken at the door before the verb ran. Terminal, because
 *              re-sending it unchanged cannot change the answer — and for the
 *              401/403 case the change definitively did not land, so nothing is
 *              kept pending and the person is told to sign in, not to retry.
 *   unknown  — we do not know whether it landed, and the evidence is kept apart
 *              because it is different evidence: `no_answer` (nothing came back
 *              at all — the connection, the tab, a request that never returned)
 *              and `server_error` (something came back and it was the server
 *              failing: its own exception on the tool channel, or any other
 *              non-2xx status). A fault must never be badged as a refusal,
 *              because the write may well have committed.
 *
 * `key_reuse` is called out on its own: it means this key already carries a
 * DIFFERENT request, so neither retrying nor assuming success is honest.
 */
export function classifyFieldWriteOutcome({ response = null, error = null } = {}) {
  if (error) {
    const code = error?.payload?.error || null;
    const hint = error?.payload?.hint || null;
    const httpStatus = Number(error?.status ?? error?.http_status ?? 0) || null;
    if (code === 'key_reuse') return { status: 'refused', reason: 'key_reuse', code, hint, http_status: httpStatus };
    if (code && SERVER_FAULT_CODES.has(code)) return { status: 'unknown', reason: 'server_error', code, hint: null, http_status: httpStatus };
    if (code) return { status: 'refused', reason: 'declined', code, hint, http_status: httpStatus };
    // No tool payload, so this stopped before or outside the verb. The status —
    // which the live client now carries on the error — is the only evidence of
    // which kind of failure it was, and the two must not be told alike:
    //   401/403 the request was DECIDED and did not land. Nothing is retained,
    //           because retrying it is not the thing to do; signing in is.
    //   any other non-2xx  an error came BACK. It may still have landed.
    //   no status at all   nothing came back: the wire, the tab, the timeout.
    if (DEFINITIVE_HTTP.has(httpStatus)) {
      return { status: 'refused', reason: 'unauthorized', code: `http_${httpStatus}`, hint: null, http_status: httpStatus };
    }
    if (httpStatus) return { status: 'unknown', reason: 'server_error', code: null, hint: null, http_status: httpStatus };
    return { status: 'unknown', reason: 'no_answer', code: null, hint: null, http_status: null };
  }
  if (response && response.status === 'conflict' && response.conflict) {
    return { status: 'conflict', reason: null, code: null, hint: null, conflict: response.conflict };
  }
  if (response && response.status === 'ok' && response.ok !== false) {
    return {
      status: 'ok', reason: null, code: null, hint: null,
      replayed: response.replayed === true,
      // Taken from the answer verbatim, or null. Nothing here constructs one.
      event_id: response.event_id ?? null,
      event_recorded_at: response.event_recorded_at ?? null,
    };
  }
  return { status: 'unknown', reason: 'no_answer', code: null, hint: null, http_status: null };
}

/**
 * Has the cell moved on since this request was built — and if it has, was it
 * moved by someone else?
 *
 * Two pieces of evidence, and the second one only exists because the answer now
 * carries it:
 *
 *   baseNow      the newest event the caller knows for this cell. When it is not
 *                the base this request was built on, SOMETHING landed on that
 *                cell while the request was out.
 *   committedId  the event this very operation committed, named by the answer
 *                (`applyDealRoomField` reads its own row back). When the cell's
 *                newest event IS that event, the thing that moved the base was
 *                this operation's own write arriving on the feed — which is not
 *                a newer change to reconcile with, it is this change.
 *
 * Getting that second test wrong is a false sentence on the very path this
 * module exists for: a lost answer, retried, replays, the feed has meanwhile
 * delivered our own event, and the person would be told "the board has since
 * seen a newer change to this cell" about their own edit — while the value on
 * screen is exactly theirs, and the local apply is withheld for no reason.
 *
 * What it still does NOT claim: whose event a DIFFERENT newer id belongs to, or
 * what the cell now holds. An unrecognised newer event is treated as a partner's
 * and reconciled through an authoritative read, which is the safe reading of an
 * id we cannot account for — including when the answer names no event at all,
 * where this falls back to comparing the two bases exactly as it did before.
 */
export function answerSupersededByFeed(request, baseNow, committedId = null) {
  if (!request) return false;
  const now = baseNow ?? null;
  if (committedId && committedId === now) return false;
  return now !== (request.base_event_id ?? null);
}

/**
 * Which of two observations of one cell's base is the newer one — the base its
 * NEXT write will send.
 *
 * A cell's base now has two sources: the changes feed, and the answer to a write
 * this board made, which names the event it committed. One rule keeps them
 * straight, and it is the record layer's own ordering — `(recorded_at, id)`, the
 * pair `latestFieldConflict` and `revert-deal-field` sort by — so the base only
 * ever moves FORWARD:
 *
 *   - a page of catch-up history for a cell whose newer event is already known
 *     cannot drag the base backwards;
 *   - a replayed answer, which is the truthful recorded result of an OLDER
 *     operation, cannot reset the base over a partner's newer event.
 *
 * `recorded_at` is compared as an instant, not as text: the feed serializes it
 * one way (`to_jsonb(recorded_at)#>>'{}'`) and a fake or a fixture may render it
 * another, and two spellings of the same moment must not order differently. When
 * either side has no readable time the candidate wins, which is the behaviour the
 * board had when the feed was its only source — never a silent refusal of news.
 */
export function nextCellBase(current, candidate) {
  if (!candidate?.id) return current || null;
  if (!current?.id) return candidate;
  if (candidate.id === current.id) return current;
  const next = instantOf(candidate.recorded_at);
  const held = instantOf(current.recorded_at);
  if (!Number.isFinite(next) || !Number.isFinite(held)) return candidate;
  if (next !== held) return next > held ? candidate : current;
  // The same millisecond. Postgres keeps microseconds, and in production both
  // sides are rendered by the same expression, so their text still separates
  // them; same-length is the cheapest honest test that they are comparable.
  const a = String(candidate.recorded_at ?? '');
  const b = String(current.recorded_at ?? '');
  if (a !== b && a.length === b.length) return a > b ? candidate : current;
  // Last tie-break, and the record layer's own: `(recorded_at, id)`.
  return candidate.id > current.id ? candidate : current;
}

/**
 * One recorded_at as an instant. Postgres renders more fractional digits than
 * the ECMAScript date-time grammar names and a parser is entitled to refuse
 * them, so an unreadable value is retried at millisecond precision before it is
 * given up on. Nothing here decides ordering by itself.
 */
function instantOf(value) {
  if (typeof value !== 'string' || !value) return NaN;
  const direct = Date.parse(value);
  if (Number.isFinite(direct)) return direct;
  return Date.parse(value.replace(/(\.\d{3})\d+/, '$1'));
}

/**
 * The sentence a person sees. The server's own hint comes FIRST and verbatim for
 * a decline — it is the only text that knows why this particular request was
 * declined — and the table above is the fallback for codes that travel bare. An
 * accepted answer says nothing at all unless the cell moved under it.
 */
export function fieldWriteMessage(outcome, subject = 'This change') {
  if (!outcome) return null;
  if (outcome.status === 'ok') {
    return outcome.superseded === true ? `${subject} ${OUTCOME_SENTENCE.superseded}` : null;
  }
  if (outcome.status === 'conflict') return null;
  if (outcome.reason === 'declined') {
    return `${subject} was refused by the server: ${outcome.hint || `${humanize(outcome.code) || 'no reason given'}.`}`;
  }
  const tail = OUTCOME_SENTENCE[outcome.reason] || OUTCOME_SENTENCE.no_answer;
  // The status, when there is one, and never the body: a person reporting this
  // can name what came back, and a server stack is not something to show them.
  const status = outcome.http_status ? ` (HTTP ${outcome.http_status})` : '';
  return `${subject} ${tail}${status}`;
}

/**
 * Every cell whose operation is still unresolved — what a board row needs in
 * order to offer a retry of THAT operation rather than a fresh guess at it.
 */
export function unresolvedFieldWrites(state, deal = null) {
  return Object.values(state || {})
    .filter((entry) => entry.status === 'unknown' && (!deal || entry.request.deal === deal))
    .map((entry) => ({
      cell: entry.cell, deal: entry.request.deal, field: entry.request.field,
      value: entry.request.value, attempts: entry.attempts,
      reason: entry.reason, code: entry.code, message: entry.message,
    }))
    .sort((a, b) => a.cell.localeCompare(b.cell));
}

/** The retained request for one cell, or null. Nothing here mutates state. */
export function pendingFieldWrite(state, cell) {
  return (state || {})[cell] || null;
}

/**
 * Send one cell change end to end against an injected `patch` — in the app that
 * is client.patchDealField, the verb the Deal Room already uses.
 *
 * The claim is published through `setState` BEFORE the request is awaited, so a
 * second click during the round trip sees the pending entry and sends nothing.
 * The request itself is never rejected out of this function: a transport that
 * throws becomes an `unknown` outcome with a sentence attached, which is what
 * the click listeners used to lose on the floor.
 *
 * @param {Object} args
 * @param {string} args.deal
 * @param {string} args.field
 * @param {*} args.value
 * An accepted answer is reported as `superseded` when this cell's base moved
 * while the request was out. That is the one thing a replay cannot tell you: the
 * server truthfully returns the recorded result of the original operation, which
 * may be older than what the caller's feed has since delivered. The caller is
 * told so it can leave the newer state alone and re-read, rather than paint a
 * stale value over it.
 *
 * @param {Object} args
 * @param {string} args.deal
 * @param {string} args.field
 * @param {*} args.value
 * @param {string|null} [args.base] the cell's last-seen event id, used only when
 *   this is a NEW operation; a retained request keeps the base it was built with
 * @param {() => (string|null)} [args.baseNow] the cell's last-seen event id AS OF
 *   the answer; defaults to `base`, which is what the caller believed on the way
 *   out — so a retry whose base has moved is superseded even without a feed
 * @param {() => Object} args.getState
 * @param {(state:Object) => void} args.setState
 * @param {() => string} args.newKey
 * @param {(request:Object) => Promise<any>} args.patch
 */
export async function performFieldWrite({ deal, field, value, base = null, baseNow = null, getState, setState, newKey, patch }) {
  const claim = beginFieldWrite(getState(), { deal, field, value, base, newKey });
  setState(claim.state);
  if (!claim.started) {
    return {
      status: claim.status, sent: false, retry: false, replayed: false, superseded: false,
      reason: claim.reason, code: null, hint: null, http_status: null,
      event_id: null, event_recorded_at: null, conflict: null,
      request: claim.pending?.request || null, pending: claim.pending,
      message: fieldWriteMessage({ status: claim.status, reason: claim.reason }),
      response: null,
    };
  }
  let response = null;
  let error = null;
  try {
    // A copy, so a client that rewrites an argument on its way out — the live
    // client translates a phase name into its slug — cannot touch the request
    // this operation is defined by.
    response = await patch({ ...claim.request });
  } catch (caught) {
    error = caught;
  }
  const classified = classifyFieldWriteOutcome({ response, error });
  // Read the cell's base only NOW, and only for an accepted answer: an operation
  // that was refused or never answered has no value to withhold in the first
  // place, and an open conflict is already the server saying the cell moved.
  // The answer's own committed event id is the third piece of evidence: without
  // it, this operation's own event arriving on the feed reads as a partner's.
  const outcome = classified.status === 'ok'
    ? { ...classified, superseded: answerSupersededByFeed(
      claim.request, baseNow ? baseNow() : (base ?? null), classified.event_id ?? null) }
    : classified;
  const cell = cellKey(deal, field);
  setState(settleFieldWrite(getState(), cell, outcome));
  return {
    status: outcome.status, sent: true, retry: claim.retry === true,
    replayed: outcome.replayed === true, superseded: outcome.superseded === true,
    reason: outcome.reason || null, code: outcome.code || null, hint: outcome.hint || null,
    http_status: outcome.http_status || null,
    // The committed event this write made, as the record layer named it — and
    // null on every answer that did not name one, which the caller must read as
    // "no base to advance to" rather than as any kind of identity.
    event_id: outcome.event_id ?? null,
    event_recorded_at: outcome.event_recorded_at ?? null,
    conflict: outcome.conflict || null,
    request: claim.request,
    pending: pendingFieldWrite(getState(), cell),
    message: fieldWriteMessage(outcome),
    response,
  };
}
